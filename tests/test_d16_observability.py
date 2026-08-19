from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import d16_observability as d16
from database import _ConnectionWrapper

CN_TZ = timezone(timedelta(hours=8))
NOW = datetime.now(CN_TZ)

SCHEMA = """
CREATE TABLE d13_agent_runs (
    run_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    current_user_id TEXT NOT NULL,
    current_role TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    skill_version TEXT NOT NULL,
    tool_contract_version TEXT NOT NULL,
    transcription_version TEXT NOT NULL,
    system_current_datetime TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE d13_agent_trace_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE d15_outbox_execution_state (
    event_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    business_action_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL,
    retry_budget INTEGER NOT NULL DEFAULT 3,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    dispatch_started INTEGER NOT NULL DEFAULT 0,
    result_known INTEGER NOT NULL DEFAULT 0,
    external_effect_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    user_message_code TEXT NOT NULL DEFAULT 'ACTION_PENDING',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE erp_sync_state (
    organization_id TEXT NOT NULL,
    doctype TEXT NOT NULL,
    sync_status TEXT NOT NULL,
    last_success_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(organization_id, doctype)
);
"""


def _script(conn, sql: str) -> None:
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(text(stmt))


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'd16.db'}")
    with engine.begin() as conn:
        _script(conn, SCHEMA)
    raw = engine.connect()
    wrapper = _ConnectionWrapper(raw, is_sqlite=True)
    yield wrapper
    raw.close()
    engine.dispose()


def test_flag_defaults_have_explicit_safe_off_behavior(db):
    flags = d16.resolve_all_flags(db, organization_id="ORG-A", user_id="USER-1")
    by_key = {x["flag_key"]: x for x in flags["items"]}
    assert by_key[d16.FLAG_AGENT_ASSIST]["effective_enabled"] is True
    assert by_key[d16.FLAG_ERP_SYNC]["effective_enabled"] is True
    assert by_key[d16.FLAG_EXTERNAL_DISPATCH]["effective_enabled"] is False
    assert all(x["safe_off_behavior"] for x in flags["items"])


def test_user_override_precedes_org_override_and_rollout_is_deterministic(db):
    d16.set_feature_flag_override(
        db, flag_key=d16.FLAG_AGENT_ASSIST, organization_id="ORG-A",
        scope_type="ORG", scope_id="ORG-A", enabled=False, rollout_percent=100,
        reason="pilot off", actor="MANAGER-A",
    )
    d16.set_feature_flag_override(
        db, flag_key=d16.FLAG_AGENT_ASSIST, organization_id="ORG-A",
        scope_type="USER", scope_id="USER-1", enabled=True, rollout_percent=100,
        reason="pilot user", actor="MANAGER-A",
    )
    db.commit()
    first = d16.resolve_feature_flag(db, flag_key=d16.FLAG_AGENT_ASSIST, organization_id="ORG-A", user_id="USER-1")
    second = d16.resolve_feature_flag(db, flag_key=d16.FLAG_AGENT_ASSIST, organization_id="ORG-A", user_id="USER-1")
    other = d16.resolve_feature_flag(db, flag_key=d16.FLAG_AGENT_ASSIST, organization_id="ORG-A", user_id="USER-2")
    assert first["source"] == "USER" and first["effective_enabled"] is True
    assert first["rollout_bucket"] == second["rollout_bucket"]
    assert other["source"] == "ORG" and other["effective_enabled"] is False


def test_unsafe_external_dispatch_configuration_is_rejected_without_adapter(db, monkeypatch):
    monkeypatch.delenv("FLOWORDER_EXTERNAL_WRITE_ADAPTER_PRESENT", raising=False)
    with pytest.raises(d16.D16ForbiddenConfiguration):
        d16.set_feature_flag_override(
            db, flag_key=d16.FLAG_EXTERNAL_DISPATCH, organization_id="ORG-A",
            scope_type="ORG", scope_id="ORG-A", enabled=True, rollout_percent=100,
            reason="unsafe", actor="MANAGER-A",
        )


def test_external_dispatch_can_only_resolve_enabled_when_real_adapter_is_declared(db, monkeypatch):
    monkeypatch.setenv("FLOWORDER_EXTERNAL_WRITE_ADAPTER_PRESENT", "1")
    d16.set_feature_flag_override(
        db, flag_key=d16.FLAG_EXTERNAL_DISPATCH, organization_id="ORG-A",
        scope_type="ORG", scope_id="ORG-A", enabled=True, rollout_percent=100,
        reason="accepted adapter", actor="MANAGER-A",
    )
    db.commit()
    result = d16.resolve_feature_flag(db, flag_key=d16.FLAG_EXTERNAL_DISPATCH, organization_id="ORG-A", user_id="USER-1")
    assert result["effective_enabled"] is True


def test_flag_changes_are_audited_and_delete_restores_default(db):
    d16.set_feature_flag_override(
        db, flag_key=d16.FLAG_ATTENTION_DASHBOARD, organization_id="ORG-A",
        scope_type="ORG", scope_id="ORG-A", enabled=False, rollout_percent=100,
        reason="kill switch rehearsal", actor="MANAGER-A",
    )
    db.commit()
    assert d16.resolve_feature_flag(db, flag_key=d16.FLAG_ATTENTION_DASHBOARD, organization_id="ORG-A", user_id="USER-1")["effective_enabled"] is False
    assert d16.delete_feature_flag_override(
        db, flag_key=d16.FLAG_ATTENTION_DASHBOARD, organization_id="ORG-A",
        scope_type="ORG", scope_id="ORG-A", actor="MANAGER-A",
    ) is True
    db.commit()
    assert d16.resolve_feature_flag(db, flag_key=d16.FLAG_ATTENTION_DASHBOARD, organization_id="ORG-A", user_id="USER-1")["effective_enabled"] is True
    events = d16.list_flag_events(db, organization_id="ORG-A")
    assert {e["action"] for e in events} == {"CREATE", "DELETE"}


def test_observability_summary_correlates_agent_tool_d15_and_erp_health(db):
    recent = NOW.isoformat(timespec="seconds")
    stale = (NOW - timedelta(hours=3)).isoformat(timespec="seconds")
    # 5 model attempts: 2 errors, 2 fallback selections; 10 tool calls: 2 errors.
    db.execute(
        "INSERT INTO d13_agent_runs(run_id,organization_id,current_user_id,current_role,trigger_type,goal,status,skill_version,tool_contract_version,transcription_version,system_current_datetime,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("RUN-1", "ORG-A", "USER-1", "OPERATOR", "USER", "test", "FAILED", "s", "t", "x", recent, recent, recent),
    )
    seq = 1
    for i in range(5):
        db.execute("INSERT INTO d13_agent_trace_events(event_id,run_id,sequence_no,event_type,status,created_at) VALUES(?,?,?,?,?,?)",
                   (f"M{i}", "RUN-1", seq, "MODEL_ATTEMPT", "ERROR" if i < 2 else "OK", recent)); seq += 1
    for i in range(2):
        db.execute("INSERT INTO d13_agent_trace_events(event_id,run_id,sequence_no,event_type,status,created_at) VALUES(?,?,?,?,?,?)",
                   (f"F{i}", "RUN-1", seq, "MODEL_FALLBACK_SELECTED", "OK", recent)); seq += 1
    for i in range(10):
        db.execute("INSERT INTO d13_agent_trace_events(event_id,run_id,sequence_no,event_type,status,created_at) VALUES(?,?,?,?,?,?)",
                   (f"T{i}", "RUN-1", seq, "TOOL_CALL", "ERROR" if i < 2 else "OK", recent)); seq += 1
    for state in ("RESULT_UNCERTAIN", "HUMAN_REQUIRED", "RETRYABLE"):
        db.execute(
            "INSERT INTO d15_outbox_execution_state(event_id,organization_id,business_action_id,request_id,idempotency_key,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (f"EV-{state}", "ORG-A", f"BA-{state}", f"REQ-{state}", f"IDEM-{state}", state, recent, recent),
        )
    db.execute(
        "INSERT INTO erp_sync_state(organization_id,doctype,sync_status,last_success_at,updated_at) VALUES(?,?,?,?,?)",
        ("ORG-A", "Sales Order", "FRESH", stale, recent),
    )
    db.commit()
    summary = d16.observability_summary(db, organization_id="ORG-A", window_minutes=60)
    assert summary["d13_agent"]["model_error_rate"] == 0.4
    assert summary["d13_agent"]["fallback_selection_rate"] == 0.4
    assert summary["d13_agent"]["tool_error_rate"] == 0.2
    assert summary["d15_execution"]["result_uncertain"] == 1
    assert summary["erp_readonly"]["freshness"] == "STALE"
    assert summary["hidden_chain_of_thought_recorded"] is False


def test_alerts_are_product_actions_not_raw_metrics(db):
    summary = {
        "d13_agent": {"model_attempt_count": 10, "model_error_rate": 0.3, "fallback_selection_rate": 0.4, "tool_call_count": 10, "tool_error_rate": 0.2},
        "d15_execution": {"result_uncertain": 1, "human_required": 1},
        "erp_readonly": {"freshness": "UNAVAILABLE"},
    }
    alerts = d16.evaluate_alerts(summary)
    codes = {a["code"] for a in alerts}
    assert {"D15_RESULT_UNCERTAIN", "D15_HUMAN_REQUIRED", "D13_MODEL_FAILURE_RATE", "D13_TOOL_ERROR_RATE", "D13_FALLBACK_RATE", "ERP_FRESHNESS"}.issubset(codes)
    assert all(a["owner_role"] == "manager" for a in alerts)
    assert all(a["recommended_action"] for a in alerts)
    assert all(a["external_notification_sent"] is False for a in alerts)


def test_version_registry_pins_frozen_runtime_versions():
    versions = d16.version_registry()
    assert versions["d14_attention_ranking"] == "D14_2_ATTENTION_V1"
    assert versions["d15_durable_execution"]
    assert versions["d16_observability"] == d16.D16_POLICY_VERSION


def test_product_contract_never_makes_human_review_a_feature_flag():
    contract = d16.product_contract()
    assert "Human Review" in " ".join(contract["product_principles"])
    assert all("human_review" not in key for key in contract["feature_flags"])
    assert contract["notification_scope"]["implemented"].startswith("In-product")
    assert "No email/WeCom/PagerDuty" in contract["notification_scope"]["not_claimed"]
