"""D13 V2 contract tests: language model can request semantics, never authority."""
from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import d12_human_review as d12
import d13_agent_skill as d13
from auth import resolve_identity_for_testing
from database import _ConnectionWrapper

NOW = "2026-08-17T09:00:00+08:00"

SCHEMA_SQL = """
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY, order_no TEXT UNIQUE NOT NULL, customer_name TEXT,
    requested_delivery_date TEXT, latest_supplier_commitment TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE', owner TEXT, organization_id TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE source_messages (
    message_id TEXT PRIMARY KEY, order_id TEXT, organization_id TEXT NOT NULL,
    source_channel TEXT, sender_role TEXT, message_type TEXT, raw_content TEXT,
    source_time TEXT, created_at TEXT NOT NULL
);
CREATE TABLE action_cases (
    action_case_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, order_id TEXT NOT NULL,
    action_intent_key TEXT NOT NULL, intent_type TEXT NOT NULL, stage TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE', observation_status TEXT NOT NULL DEFAULT 'OBSERVED',
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    close_reason TEXT, closed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE d9_action_case_tasks (
    task_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, action_case_id TEXT NOT NULL,
    title TEXT NOT NULL, recommended_action TEXT, status TEXT NOT NULL DEFAULT 'TODO',
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE d9_action_case_waitings (
    waiting_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, task_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL, waiting_type TEXT NOT NULL, reason TEXT, due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE', source_trace_id TEXT, reply_count INTEGER NOT NULL DEFAULT 0,
    latest_reply_json TEXT NOT NULL DEFAULT '[]', version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, resolved_at TEXT, expired_at TEXT,
    cancelled_at TEXT, cancel_reason TEXT
);
CREATE TABLE d10_business_actions (
    business_action_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, action_case_id TEXT NOT NULL,
    task_id TEXT NOT NULL, order_id TEXT NOT NULL, action_type TEXT NOT NULL, target_type TEXT NOT NULL,
    target_id TEXT NOT NULL, payload_json TEXT NOT NULL, request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL, effect_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACCEPTED', actor TEXT NOT NULL, source TEXT NOT NULL,
    reason TEXT, policy_version TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(organization_id, task_id), UNIQUE(organization_id, idempotency_key)
);
CREATE TABLE d10_outbox_events (
    event_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, business_action_id TEXT NOT NULL,
    event_type TEXT NOT NULL, payload_json TEXT NOT NULL, dedupe_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING', attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT, lease_owner TEXT, lease_until TEXT, published_at TEXT, last_error TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(organization_id, business_action_id), UNIQUE(organization_id, dedupe_key)
);
CREATE TABLE d10_idempotency_records (
    organization_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, request_hash TEXT NOT NULL,
    business_action_id TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(organization_id, idempotency_key)
);
CREATE TABLE d10_audit_events (
    audit_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, actor TEXT NOT NULL,
    request_id TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
    reason TEXT, source TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE d12_human_reviews (
    review_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL, order_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL, task_id TEXT NOT NULL, action_type TEXT NOT NULL,
    target_type TEXT NOT NULL, target_id TEXT NOT NULL, payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL, state_version TEXT NOT NULL, state_snapshot_json TEXT NOT NULL,
    requested_by TEXT NOT NULL, requester_role TEXT NOT NULL, required_review TEXT NOT NULL,
    idempotency_key TEXT NOT NULL, d10_request_id TEXT NOT NULL, reason TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING', decision TEXT, reviewed_by TEXT, reviewer_role TEXT,
    created_at TEXT NOT NULL, reviewed_at TEXT, expires_at TEXT NOT NULL, consumed_at TEXT,
    business_action_id TEXT, result_json TEXT, policy_version TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(organization_id, idempotency_key)
);
"""


def _exec_script(conn, script: str) -> None:
    for stmt in script.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(text(stmt))


@pytest.fixture
def conn(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'd13.db'}")
    with engine.begin() as raw:
        _exec_script(raw, SCHEMA_SQL)
    raw = engine.connect()
    wrapper = _ConnectionWrapper(raw, is_sqlite=True)
    yield wrapper
    raw.close()
    engine.dispose()


def seed(conn, *, org="ORG-A", order_id="ORD-A", order_no="PO-A", case_id="AC-A", task_id="TK-A", owner="OPERATOR-A1"):
    conn.execute(
        "INSERT INTO orders VALUES(?,?,?,?,?,?,?,?,?,?)",
        (order_id, order_no, "ACME", "2026-08-20", "2026-08-18", "ACTIVE", owner, org, NOW, NOW),
    )
    conn.execute(
        """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,stage,lifecycle_status,observation_status,first_seen_at,last_seen_at,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (case_id, org, order_id, "v1:DELIVERY_RECOVERY", "DELIVERY_RECOVERY", "IN_PROGRESS", "ACTIVE", "OBSERVED", NOW, NOW, 1, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO d9_action_case_tasks VALUES(?,?,?,?,?,?,?,?,?)",
        (task_id, org, case_id, "处理交期变化", "确认处理方案", "IN_PROGRESS", 1, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO source_messages VALUES(?,?,?,?,?,?,?,?,?)",
        (f"MSG-{order_id}", order_id, org, "email", "supplier", "delivery_update", "工厂确认25号完工", NOW, NOW),
    )
    conn.commit()


def request(conn, *, tool_name, payload, idem="D13-1", user="OPERATOR-A1", task_id="TK-A"):
    return d13.request_controlled_action(
        conn,
        tool_name=tool_name,
        task_id=task_id,
        payload=payload,
        identity=resolve_identity_for_testing(user),
        idempotency_key=idem,
        reason="Agent根据当前任务提出受控请求",
    )


def test_manifest_is_small_semantic_surface_and_does_not_grant_authority():
    manifest = d13.tool_manifest()
    tool_names = {x["tool_name"] for x in manifest["tools"]}
    assert "get_actionable_orders" in tool_names
    assert "request_change_customer_delivery_date" in tool_names
    assert "request_update_expected_delivery_date" not in tool_names
    assert "request_update_customer_commitment" not in tool_names
    assert "request_accept_delay" not in tool_names
    assert "request_high_risk_override" not in tool_names
    assert manifest["authority"]["model_can_grant_permission"] is False
    assert manifest["authority"]["agent_can_approve_review"] is False
    assert manifest["authority"]["agent_can_submit_review"] is False
    assert manifest["authority"]["agent_can_execute_external_effect"] is False
    assert "erp_write_generic" in manifest["forbidden_tools"]


def test_formal_customer_delivery_tool_only_creates_manager_review(conn):
    seed(conn)
    result = request(
        conn,
        tool_name="request_change_customer_delivery_date",
        payload={"customer_delivery_date": "2026-08-23", "reason": "工厂延期"},
    )
    assert result["required_review"] == d12.REQUIREMENT_MANAGER
    assert result["review_status"] == d12.STATUS_PENDING
    assert result["agent_executed_effect"] is False
    assert result["target_id"] == "ORD-A"
    assert conn.execute("SELECT COUNT(*) FROM d10_business_actions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM d10_outbox_events").fetchone()[0] == 0
    assert conn.execute("SELECT requested_delivery_date FROM orders").fetchone()[0] == "2026-08-20"


def test_supplier_commitment_is_operator_confirm_not_manager(conn):
    seed(conn)
    result = request(
        conn,
        tool_name="request_record_supplier_commitment",
        payload={"supplier_commitment_date": "2026-08-25", "evidence": "工厂邮件"},
    )
    assert result["required_review"] == d12.REQUIREMENT_OPERATOR
    assert result["review_status"] == d12.STATUS_PENDING


def test_agent_payload_cannot_spoof_identity_or_manager(conn):
    seed(conn)
    with pytest.raises(d13.D13ForbiddenError):
        request(
            conn,
            tool_name="request_change_customer_delivery_date",
            payload={"customer_delivery_date": "2026-08-23", "manager_id": "MANAGER-A"},
        )


def test_unknown_removed_and_forbidden_tools_fail_closed(conn):
    seed(conn)
    for name in ["magic_write_order", "send_message", "request_high_risk_override", "request_accept_delay", "request_update_expected_delivery_date"]:
        with pytest.raises(d13.D13ForbiddenError):
            request(conn, tool_name=name, payload={})


def test_payload_shape_is_bounded_for_read_and_effect_tools(conn):
    seed(conn)
    with pytest.raises(d13.D13ValidationError):
        request(
            conn,
            tool_name="request_record_supplier_commitment",
            payload={"supplier_commitment_date": "2026-08-25", "sql": "UPDATE orders"},
        )
    with pytest.raises(d13.D13ForbiddenError):
        d13.validate_tool_payload("get_actionable_orders", {"organization_id": "ORG-B"})
    with pytest.raises(d13.D13ValidationError):
        request(conn, tool_name="request_record_supplier_commitment", payload={})


def test_idempotent_replay_and_payload_mutation_conflict(conn):
    seed(conn)
    first = request(
        conn,
        tool_name="request_change_customer_delivery_date",
        payload={"customer_delivery_date": "2026-08-23"},
        idem="D13-IDEM",
    )
    second = request(
        conn,
        tool_name="request_change_customer_delivery_date",
        payload={"customer_delivery_date": "2026-08-23"},
        idem="D13-IDEM",
    )
    assert second["review_id"] == first["review_id"]
    assert second["replayed"] is True
    with pytest.raises(d12.D12ConflictError):
        request(
            conn,
            tool_name="request_change_customer_delivery_date",
            payload={"customer_delivery_date": "2026-09-01"},
            idem="D13-IDEM",
        )


def test_cross_org_task_is_not_visible_to_agent_request(conn):
    seed(conn, org="ORG-A", task_id="TK-A")
    with pytest.raises(d13.D13NotFoundError):
        request(
            conn,
            tool_name="request_change_customer_delivery_date",
            payload={"customer_delivery_date": "2026-08-23"},
            user="OPERATOR-B1",
            task_id="TK-A",
        )


def test_get_order_context_supports_read_before_ask_and_is_org_bound(conn):
    seed(conn)
    result = d13.get_order_context(
        conn,
        identity=resolve_identity_for_testing("OPERATOR-A1"),
        payload={"order_no": "PO-A"},
    )
    assert result["order"]["order_id"] == "ORD-A"
    assert result["tasks"][0]["task_id"] == "TK-A"
    assert result["recent_messages"][0]["message_id"] == "MSG-ORD-A"
    assert result["business_state_changed"] is False
    with pytest.raises(d13.D13NotFoundError):
        d13.get_order_context(
            conn,
            identity=resolve_identity_for_testing("OPERATOR-B1"),
            payload={"order_no": "PO-A"},
        )


def test_waiting_on_aliases_are_backend_normalized():
    assert d13.validate_tool_payload("request_set_waiting", {"waiting_on": "客户"})["waiting_on"] == "customer"
    assert d13.validate_tool_payload("request_set_waiting", {"waiting_on": "工厂"})["waiting_on"] == "supplier"
    assert d13.validate_tool_payload("request_set_waiting", {"waiting_on": "内部"})["waiting_on"] == "internal"
    with pytest.raises(d13.D13ValidationError):
        d13.validate_tool_payload("request_set_waiting", {"waiting_on": "随便"})


def test_d13_rejects_semantically_invalid_business_dates_before_d12(conn):
    seed(conn)
    invalid_date_values = ["2026-13-99", "asap", "2026/08/25 99:99", "下周", "0000-00-00"]
    for idx, value in enumerate(invalid_date_values):
        with pytest.raises(d13.D13ValidationError):
            request(
                conn,
                tool_name="request_change_customer_delivery_date",
                payload={"customer_delivery_date": value},
                idem=f"BAD-DATE-{idx}",
            )
    assert conn.execute("SELECT COUNT(*) FROM d12_human_reviews").fetchone()[0] == 0


def test_d13_rejects_invalid_datetime_fields_and_accepts_timezone_aware_iso(conn):
    seed(conn)
    for value in ["下周", "2026-08-18 15:00:00", "2026-08-18T15:00:00", "2026-08-18T99:00:00+08:00"]:
        with pytest.raises(d13.D13ValidationError):
            d13.validate_tool_payload(
                "request_set_waiting",
                {"waiting_on": "customer", "promised_reply_at": value},
            )
    clean = d13.validate_tool_payload(
        "request_set_waiting",
        {"waiting_on": "客户", "promised_reply_at": "2026-08-18T15:00:00+08:00"},
    )
    assert clean["waiting_on"] == "customer"
    assert clean["promised_reply_at"] == "2026-08-18T15:00:00+08:00"
