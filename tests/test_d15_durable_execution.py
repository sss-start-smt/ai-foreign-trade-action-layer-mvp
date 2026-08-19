"""D15 durable execution / RESULT_UNCERTAIN fault-injection tests."""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import d10_business_action as d10
import d15_api
import d15_durable_execution as d15
from database import _ConnectionWrapper
from d11_action_workspace import _business_effect

CN_TZ = timezone(timedelta(hours=8))
NOW = "2026-08-19T10:00:00+08:00"

SCHEMA_SQL = """
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    order_no TEXT UNIQUE NOT NULL,
    customer_name TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    owner TEXT,
    organization_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE action_cases (
    action_case_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    action_intent_key TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE',
    observation_status TEXT NOT NULL DEFAULT 'OBSERVED',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE d9_action_case_tasks (
    task_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    title TEXT NOT NULL,
    recommended_action TEXT,
    status TEXT NOT NULL DEFAULT 'IN_PROGRESS',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE d10_business_actions (
    business_action_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    effect_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACCEPTED',
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organization_id, task_id),
    UNIQUE(organization_id, idempotency_key)
);
CREATE TABLE d10_outbox_events (
    event_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    business_action_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    published_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organization_id, business_action_id),
    UNIQUE(organization_id, dedupe_key)
);
CREATE TABLE d10_idempotency_records (
    organization_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    business_action_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(organization_id, idempotency_key)
);
CREATE TABLE d10_audit_events (
    audit_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    request_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    reason TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE d15_outbox_execution_state (
    event_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    business_action_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'PENDING',
    retry_budget INTEGER NOT NULL DEFAULT 3,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    dispatch_started INTEGER NOT NULL DEFAULT 0,
    result_known INTEGER NOT NULL DEFAULT 0,
    external_effect_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    error_kind TEXT,
    user_message_code TEXT NOT NULL DEFAULT 'ACTION_PENDING',
    reconciliation_status TEXT,
    next_attempt_at TEXT,
    last_attempt_at TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organization_id, idempotency_key)
);
CREATE TABLE d15_execution_trace_events (
    trace_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    sequence_no INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    state TEXT NOT NULL,
    error_kind TEXT,
    request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    dispatch_started INTEGER NOT NULL DEFAULT 0,
    result_known INTEGER NOT NULL DEFAULT 0,
    external_effect_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    response_meta_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(event_id, sequence_no)
);
"""


def _exec_script(conn, script: str) -> None:
    for stmt in script.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(text(stmt))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "d15.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        _exec_script(conn, SCHEMA_SQL)
    raw = engine.connect()
    wrapper = _ConnectionWrapper(raw, is_sqlite=True)
    yield wrapper
    try:
        raw.close()
    except Exception:
        pass
    engine.dispose()


def _seed_outbox(conn, *, org="ORG-A", task_id="TK-1", idem="idem-d15", request_id="REQ-D15") -> str:
    conn.execute(
        "INSERT INTO orders(order_id,order_no,status,owner,organization_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("ORD-1", "SO-1", "ACTIVE", "USER-1", org, NOW, NOW),
    )
    conn.execute(
        """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,stage,
           lifecycle_status,observation_status,first_seen_at,last_seen_at,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("AC-1", org, "ORD-1", "v1:DELIVERY", "DELIVERY", "IN_PROGRESS", "ACTIVE", "OBSERVED", NOW, NOW, 1, NOW, NOW),
    )
    conn.execute(
        """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,status,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (task_id, org, "AC-1", "更新交期", "更新交期", "IN_PROGRESS", 1, NOW, NOW),
    )
    conn.commit()
    result = d10.submit_business_action(
        conn,
        d10.BusinessActionSubmission(
            organization_id=org,
            task_id=task_id,
            action_type="UPDATE_EXPECTED_DELIVERY_DATE",
            target_type="ERP_SALES_ORDER",
            target_id="SO-1",
            payload={"expected_delivery_date": "2026-08-25"},
            idempotency_key=idem,
            actor="USER-1",
            request_id=request_id,
            source="D15_TEST",
            reason="test",
        ),
    )
    return result["outbox_event_id"]


class SuccessAdapter:
    def __init__(self):
        self.calls = []

    def dispatch(self, payload, *, idempotency_key, request_id):
        self.calls.append((payload, idempotency_key, request_id))
        return d15.DispatchReceipt(external_reference="ERP-OK-1", metadata={"status_code": 200, "adapter": "FAKE"})


class RetryableAdapter:
    def __init__(self):
        self.calls = 0

    def dispatch(self, payload, *, idempotency_key, request_id):
        self.calls += 1
        raise d15.D15RetryableNoEffect(error_kind="HTTP_503_NO_EFFECT")


class RetryThenSuccessAdapter:
    def __init__(self):
        self.calls = 0

    def dispatch(self, payload, *, idempotency_key, request_id):
        self.calls += 1
        if self.calls == 1:
            raise d15.D15RetryableNoEffect(error_kind="CONNECTION_BEFORE_DISPATCH")
        return d15.DispatchReceipt(external_reference="OK-2")


class UncertainAdapter:
    def __init__(self):
        self.calls = 0

    def dispatch(self, payload, *, idempotency_key, request_id):
        self.calls += 1
        raise d15.D15ResultUncertain(error_kind="ACK_LOST_AFTER_DISPATCH")


class SecretGenericCrashAdapter:
    def dispatch(self, payload, *, idempotency_key, request_id):
        raise RuntimeError("api_key=SUPERSECRET Authorization=Bearer SUPERSECRET")


class FailedSafeAdapter:
    def dispatch(self, payload, *, idempotency_key, request_id):
        raise d15.D15FailedSafe(error_kind="POLICY_PRECONDITION_FAILED")


class HumanRequiredAdapter:
    def dispatch(self, payload, *, idempotency_key, request_id):
        raise d15.D15HumanRequired(error_kind="AUTH_REPAIR_REQUIRED")


def _future(seconds=60):
    return datetime(2026, 8, 19, 10, 0, tzinfo=CN_TZ) + timedelta(seconds=seconds)


def test_d15_initial_state_preserves_d10_boundary(db):
    event_id = _seed_outbox(db)
    status = d15.ensure_execution_state(db, event_id)
    outbox = db.execute("SELECT * FROM d10_outbox_events WHERE event_id=?", (event_id,)).fetchone()
    assert status["state"] == d15.STATE_PENDING
    assert outbox["status"] == "PENDING"
    action = db.execute("SELECT * FROM d10_business_actions WHERE business_action_id=?", (outbox["business_action_id"],)).fetchone()
    assert action["status"] == "ACCEPTED"


def test_confirmed_success_dispatches_once_and_replay_never_duplicates(db):
    event_id = _seed_outbox(db)
    adapter = SuccessAdapter()
    first = d15.process_outbox_event(db, event_id=event_id, adapter=adapter)
    second = d15.process_outbox_event(db, event_id=event_id, adapter=adapter)
    assert first["state"] == "SUCCESS"
    assert second["state"] == "SUCCESS"
    assert len(adapter.calls) == 1
    assert first["external_effect_status"] == "TRUE"
    outbox = db.execute("SELECT * FROM d10_outbox_events WHERE event_id=?", (event_id,)).fetchone()
    assert outbox["status"] == "PUBLISHED"
    assert outbox["attempt_count"] == 1


def test_dispatch_receives_d10_idempotency_and_request_id(db):
    event_id = _seed_outbox(db, idem="idem-safe-123", request_id="REQ-safe-123")
    adapter = SuccessAdapter()
    d15.process_outbox_event(db, event_id=event_id, adapter=adapter)
    assert adapter.calls[0][1] == "idem-safe-123"
    assert adapter.calls[0][2] == "REQ-safe-123"


def test_retryable_no_effect_has_finite_budget_and_due_time(db):
    event_id = _seed_outbox(db)
    adapter = RetryableAdapter()
    base = datetime(2026, 8, 19, 10, 0, tzinfo=CN_TZ)
    r1 = d15.process_outbox_event(db, event_id=event_id, adapter=adapter, retry_budget=2, now_fn=lambda: base)
    assert r1["state"] == "RETRYABLE"
    assert r1["attempt_count"] == 1
    assert r1["auto_retry_allowed"] is True
    # Before next_attempt_at: no hot-loop dispatch.
    early = d15.process_outbox_event(db, event_id=event_id, adapter=adapter, now_fn=lambda: base + timedelta(seconds=1))
    assert early["state"] == "RETRYABLE"
    assert adapter.calls == 1
    # After due: second attempt exhausts budget -> HUMAN_REQUIRED.
    r2 = d15.process_outbox_event(db, event_id=event_id, adapter=adapter, now_fn=lambda: base + timedelta(seconds=30))
    assert r2["state"] == "HUMAN_REQUIRED"
    assert r2["attempt_count"] == 2
    assert adapter.calls == 2


def test_retryable_then_success_is_safe_when_no_effect_was_confirmed(db):
    event_id = _seed_outbox(db)
    adapter = RetryThenSuccessAdapter()
    base = datetime(2026, 8, 19, 10, 0, tzinfo=CN_TZ)
    first = d15.process_outbox_event(db, event_id=event_id, adapter=adapter, now_fn=lambda: base)
    assert first["state"] == "RETRYABLE"
    second = d15.process_outbox_event(db, event_id=event_id, adapter=adapter, now_fn=lambda: base + timedelta(seconds=30))
    assert second["state"] == "SUCCESS"
    assert adapter.calls == 2


def test_result_uncertain_never_auto_retries(db):
    event_id = _seed_outbox(db)
    adapter = UncertainAdapter()
    first = d15.process_outbox_event(db, event_id=event_id, adapter=adapter)
    second = d15.process_outbox_event(db, event_id=event_id, adapter=adapter)
    assert first["state"] == "RESULT_UNCERTAIN"
    assert first["result_known"] is False
    assert first["external_effect_status"] == "UNKNOWN"
    assert first["auto_retry_allowed"] is False
    assert second["state"] == "RESULT_UNCERTAIN"
    assert adapter.calls == 1


def test_unknown_generic_adapter_exception_fails_closed_to_uncertain_and_secret_not_stored(db):
    event_id = _seed_outbox(db)
    result = d15.process_outbox_event(db, event_id=event_id, adapter=SecretGenericCrashAdapter())
    assert result["state"] == "RESULT_UNCERTAIN"
    trace_blob = json.dumps(d15.list_execution_trace(db, event_id), ensure_ascii=False)
    assert "SUPERSECRET" not in trace_blob
    assert "Authorization" not in trace_blob
    assert "api_key" not in trace_blob
    assert "ADAPTER_EXCEPTION_UNCERTAIN" in trace_blob


def test_failed_safe_never_changes_external_effect_to_success(db):
    event_id = _seed_outbox(db)
    result = d15.process_outbox_event(db, event_id=event_id, adapter=FailedSafeAdapter())
    assert result["state"] == "FAILED_SAFE"
    assert result["result_known"] is True
    assert result["external_effect_status"] == "FALSE"
    assert result["auto_retry_allowed"] is False


def test_human_required_stops_automation(db):
    event_id = _seed_outbox(db)
    result = d15.process_outbox_event(db, event_id=event_id, adapter=HumanRequiredAdapter())
    assert result["state"] == "HUMAN_REQUIRED"
    assert result["auto_retry_allowed"] is False


def test_orphaned_inflight_is_recovered_as_uncertain_without_redispatch(db):
    event_id = _seed_outbox(db)
    d15.ensure_execution_state(db, event_id)
    db.execute(
        "UPDATE d15_outbox_execution_state SET state='IN_FLIGHT',attempt_count=1,dispatch_started=1,result_known=0 WHERE event_id=?",
        (event_id,),
    )
    db.execute("UPDATE d10_outbox_events SET status='IN_FLIGHT',attempt_count=1 WHERE event_id=?", (event_id,))
    db.commit()
    adapter = SuccessAdapter()
    result = d15.process_outbox_event(db, event_id=event_id, adapter=adapter)
    assert result["state"] == "RESULT_UNCERTAIN"
    assert adapter.calls == []


def test_reconcile_uncertain_success_closes_to_success(db):
    event_id = _seed_outbox(db)
    d15.process_outbox_event(db, event_id=event_id, adapter=UncertainAdapter())
    result = d15.reconcile_outbox_event(db, event_id=event_id, result="SUCCESS", actor="MANAGER-1", evidence_ref="ERP-AUDIT-77")
    assert result["state"] == "SUCCESS"
    assert result["reconciliation_status"] == "CONFIRMED_SUCCESS"
    assert result["external_effect_status"] == "TRUE"


def test_reconcile_uncertain_not_executed_allows_explicit_requeue_only(db):
    event_id = _seed_outbox(db)
    d15.process_outbox_event(db, event_id=event_id, adapter=UncertainAdapter())
    result = d15.reconcile_outbox_event(db, event_id=event_id, result="NOT_EXECUTED", actor="MANAGER-1")
    assert result["state"] == "FAILED_SAFE"
    assert result["reconciliation_status"] == "CONFIRMED_NOT_EXECUTED"
    requeued = d15.requeue_after_confirmed_no_effect(db, event_id=event_id, actor="MANAGER-1")
    assert requeued["state"] == "PENDING"


def test_unreconciled_failed_safe_cannot_be_manually_requeued(db):
    event_id = _seed_outbox(db)
    d15.process_outbox_event(db, event_id=event_id, adapter=FailedSafeAdapter())
    with pytest.raises(d15.D15StateError):
        d15.requeue_after_confirmed_no_effect(db, event_id=event_id, actor="MANAGER-1")


def test_reconcile_still_unknown_escalates_to_human_required(db):
    event_id = _seed_outbox(db)
    d15.process_outbox_event(db, event_id=event_id, adapter=UncertainAdapter())
    result = d15.reconcile_outbox_event(db, event_id=event_id, result="UNKNOWN", actor="MANAGER-1")
    assert result["state"] == "HUMAN_REQUIRED"
    assert result["result_known"] is False


def test_invalid_reconciliation_decision_is_rejected(db):
    event_id = _seed_outbox(db)
    d15.process_outbox_event(db, event_id=event_id, adapter=UncertainAdapter())
    with pytest.raises(d15.D15StateError):
        d15.reconcile_outbox_event(db, event_id=event_id, result="MAYBE", actor="MANAGER-1")


def test_d11_read_model_exposes_d15_state_without_changing_d10_action(db):
    event_id = _seed_outbox(db)
    d15.process_outbox_event(db, event_id=event_id, adapter=UncertainAdapter())
    action, outbox = _business_effect({"organization_id": "ORG-A", "task_id": "TK-1"}, db)
    assert action["status"] == "ACCEPTED"
    assert outbox["durable_execution"]["state"] == "RESULT_UNCERTAIN"
    assert "自动重试已暂停" in outbox["durable_execution"]["ui"]["message"]


def test_contract_explicitly_declares_no_mcp_redis_or_live_write_adapter():
    scope = d15.failure_contract()["scope"]
    assert scope == {
        "mcp_required": False,
        "redis_required": False,
        "external_write_adapter_present": False,
        "note": "D15 defines the worker contract; no live ERP/email write adapter is claimed in the D14.2 baseline.",
    }


def test_api_operator_can_read_but_cannot_reconcile(db, monkeypatch):
    event_id = _seed_outbox(db)
    d15.process_outbox_event(db, event_id=event_id, adapter=UncertainAdapter())

    @contextmanager
    def fake_db():
        yield db

    monkeypatch.setattr(d15_api, "db", fake_db)
    app = FastAPI()
    d15_api.register_d15_api(app)
    client = TestClient(app)
    read = client.get(f"/api/d15/outbox/{event_id}", headers={"X-Auth-Token": "tok-user-1"})
    assert read.status_code == 200
    assert read.json()["state"] == "RESULT_UNCERTAIN"
    denied = client.post(
        f"/api/d15/outbox/{event_id}/reconcile",
        headers={"X-Auth-Token": "tok-user-1"},
        json={"result": "SUCCESS"},
    )
    assert denied.status_code == 403


def test_api_manager_can_reconcile_and_trace_is_safe(db, monkeypatch):
    event_id = _seed_outbox(db)
    d15.process_outbox_event(db, event_id=event_id, adapter=UncertainAdapter())

    @contextmanager
    def fake_db():
        yield db

    monkeypatch.setattr(d15_api, "db", fake_db)
    app = FastAPI()
    d15_api.register_d15_api(app)
    client = TestClient(app)
    r = client.post(
        f"/api/d15/outbox/{event_id}/reconcile",
        headers={"X-Auth-Token": "tok-manager-1"},
        json={"result": "SUCCESS", "evidence_ref": "external-audit-1"},
    )
    assert r.status_code == 200
    assert r.json()["state"] == "SUCCESS"
    trace = client.get(f"/api/d15/outbox/{event_id}/trace", headers={"X-Auth-Token": "tok-manager-1"})
    assert trace.status_code == 200
    assert trace.json()["count"] >= 4


def test_cross_org_read_is_forbidden(db, monkeypatch):
    event_id = _seed_outbox(db, org="ORG-B")

    @contextmanager
    def fake_db():
        yield db

    monkeypatch.setattr(d15_api, "db", fake_db)
    app = FastAPI()
    d15_api.register_d15_api(app)
    client = TestClient(app)
    # MANAGER-1 belongs ORG-A in the frozen auth map.
    r = client.get(f"/api/d15/outbox/{event_id}", headers={"X-Auth-Token": "tok-manager-1"})
    assert r.status_code == 403


def test_ui_contract_distinguishes_all_five_public_states():
    contract = d15.failure_contract()["states"]
    assert set(contract) == {"SUCCESS", "FAILED_SAFE", "RETRYABLE", "RESULT_UNCERTAIN", "HUMAN_REQUIRED"}
    assert contract["RESULT_UNCERTAIN"]["auto_retry_allowed"] is False
    assert contract["RETRYABLE"]["auto_retry_allowed"] is True
    assert contract["SUCCESS"]["title"] != contract["RESULT_UNCERTAIN"]["title"]


def test_trace_contains_required_d15_fields(db):
    event_id = _seed_outbox(db)
    d15.process_outbox_event(db, event_id=event_id, adapter=UncertainAdapter())
    trace = d15.list_execution_trace(db, event_id)
    last = trace[-1]
    for key in (
        "event_id", "state", "error_kind", "request_id", "idempotency_key",
        "attempt", "dispatch_started", "result_known", "external_effect_status",
    ):
        assert key in last
    assert last["state"] == "RESULT_UNCERTAIN"


def test_real_schema_and_alembic_include_d15_tables():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    schema = (root / "schema.sql").read_text(encoding="utf-8")
    migration = (root / "alembic" / "versions" / "o6p7q8r9s0t1_add_d15_durable_execution.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS d15_outbox_execution_state" in schema
    assert "CREATE TABLE IF NOT EXISTS d15_execution_trace_events" in schema
    assert 'down_revision: Union[str, None] = "n5o6p7q8r9s0"' in migration


def test_static_action_workspace_exposes_d15_uncertain_copy():
    from pathlib import Path
    app_js = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    assert "结果未知·已暂停自动重试" in app_js
    assert "外部操作结果暂无法确认，系统已停止自动重试，请先核对" in app_js
    assert "需要人工处理" in app_js
