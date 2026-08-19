"""D12 P0/P1 tests: Human Review / Approval / Permission Gate."""
from __future__ import annotations

import os
import sys

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import d10_business_action as d10
import d12_human_review as d12
import agent_api
from auth import resolve_identity_for_testing
from database import _ConnectionWrapper

NOW = "2026-08-16T20:00:00+08:00"

SCHEMA_SQL = """
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    order_no TEXT UNIQUE NOT NULL,
    customer_name TEXT,
    requested_delivery_date TEXT,
    latest_supplier_commitment TEXT,
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
    close_reason TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE d9_action_case_tasks (
    task_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    title TEXT NOT NULL,
    recommended_action TEXT,
    status TEXT NOT NULL DEFAULT 'TODO',
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
CREATE TABLE d12_human_reviews (
    review_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    state_version TEXT NOT NULL,
    state_snapshot_json TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    requester_role TEXT NOT NULL,
    required_review TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    d10_request_id TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    decision TEXT,
    reviewed_by TEXT,
    reviewer_role TEXT,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    business_action_id TEXT,
    result_json TEXT,
    policy_version TEXT NOT NULL,
    updated_at TEXT NOT NULL,
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
    path = tmp_path / "d12.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as raw:
        _exec_script(raw, SCHEMA_SQL)
    raw = engine.connect()
    wrapper = _ConnectionWrapper(raw, is_sqlite=True)
    yield wrapper
    raw.close()
    engine.dispose()


def seed(conn, *, org="ORG-A", order_id="ORD-A", case_id="AC-A", task_id="TK-A", owner="OPERATOR-A1"):
    conn.execute(
        "INSERT INTO orders(order_id,order_no,customer_name,requested_delivery_date,latest_supplier_commitment,status,owner,organization_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (order_id, order_id, "ACME", "2026-08-20", "2026-08-18", "ACTIVE", owner, org, NOW, NOW),
    )
    conn.execute(
        """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,stage,lifecycle_status,observation_status,first_seen_at,last_seen_at,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (case_id, org, order_id, "v1:DELIVERY_RECOVERY", "DELIVERY_RECOVERY", "IN_PROGRESS", "ACTIVE", "OBSERVED", NOW, NOW, 1, NOW, NOW),
    )
    conn.execute(
        "INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (task_id, org, case_id, "处理交期变化", "确认处理方案", "IN_PROGRESS", 1, NOW, NOW),
    )
    conn.commit()


def submission(*, action_type="UPDATE_EXPECTED_DELIVERY_DATE", payload=None, idem="D12-IDEM-1", actor="OPERATOR-A1", org="ORG-A", task_id="TK-A"):
    return d10.BusinessActionSubmission(
        organization_id=org,
        task_id=task_id,
        action_type=action_type,
        target_type="ERP_SALES_ORDER",
        target_id="SO-A",
        payload=payload or {"expected_delivery_date": "2026-08-23"},
        idempotency_key=idem,
        actor=actor,
        request_id=f"REQ-{idem}",
        source="D12_ACTION_WORKSPACE",
        reason="交期变化需要确认",
    )


def request(conn, sub, user="OPERATOR-A1"):
    return d12.request_review(
        conn,
        d12.ReviewRequest(submission=sub),
        identity=resolve_identity_for_testing(user),
    )


def test_manager_approval_is_required_for_formal_delivery_commitment(conn):
    seed(conn)
    review = request(conn, submission())
    assert review["required_review"] == d12.REQUIREMENT_MANAGER

    with pytest.raises(d12.D12ForbiddenError):
        d12.decide_review(
            conn,
            review_id=review["review_id"],
            identity=resolve_identity_for_testing("OPERATOR-A1"),
            decision="APPROVE",
        )

    approved = d12.decide_review(
        conn,
        review_id=review["review_id"],
        identity=resolve_identity_for_testing("MANAGER-A"),
        decision="APPROVE",
    )
    assert approved["status"] == "APPROVED"

    result = d12.submit_after_review(
        conn,
        review_id=review["review_id"],
        identity=resolve_identity_for_testing("OPERATOR-A1"),
    )
    assert result["status"] == "ACCEPTED"
    assert result["external_effect_executed"] is False
    assert conn.execute("SELECT COUNT(*) FROM d10_business_actions").fetchone()[0] == 1
    assert conn.execute("SELECT status FROM d10_outbox_events").fetchone()[0] == "PENDING"
    # D12/D10 still do not pretend that ERP/order write already happened.
    assert conn.execute("SELECT requested_delivery_date FROM orders WHERE order_id='ORD-A'").fetchone()[0] == "2026-08-20"


def test_low_risk_action_is_operator_confirm_not_manager_bottleneck(conn):
    seed(conn)
    low = submission(
        action_type="RECORD_CONTACT",
        payload={"channel": "email", "contacted_party": "supplier"},
        idem="LOW-1",
    )
    review = request(conn, low)
    assert review["required_review"] == d12.REQUIREMENT_OPERATOR
    approved = d12.decide_review(
        conn,
        review_id=review["review_id"],
        identity=resolve_identity_for_testing("OPERATOR-A1"),
        decision="APPROVE",
    )
    assert approved["status"] == "APPROVED"


def test_role_spoof_cannot_upgrade_authenticated_operator(conn):
    seed(conn)
    review = request(conn, submission())
    # There is no caller-controlled reviewer role in the contract; authenticated
    # OPERATOR-A1 remains an operator regardless of any UI/body claim.
    with pytest.raises(d12.D12ForbiddenError):
        d12.decide_review(
            conn,
            review_id=review["review_id"],
            identity=resolve_identity_for_testing("OPERATOR-A1"),
            decision="APPROVE",
            note="I claim manager",
        )


def test_cross_org_manager_sees_not_found_and_cannot_decide(conn):
    seed(conn)
    review = request(conn, submission())
    with pytest.raises(d12.D12NotFoundError):
        d12.decide_review(
            conn,
            review_id=review["review_id"],
            identity=resolve_identity_for_testing("MANAGER-B"),
            decision="APPROVE",
        )
    assert conn.execute("SELECT status FROM d12_human_reviews WHERE review_id=?", (review["review_id"],)).fetchone()[0] == "PENDING"


def test_payload_mutation_after_approval_is_blocked(conn):
    seed(conn)
    original = submission()
    review = request(conn, original)
    d12.decide_review(
        conn,
        review_id=review["review_id"],
        identity=resolve_identity_for_testing("MANAGER-A"),
        decision="APPROVE",
    )
    mutated = submission(payload={"expected_delivery_date": "2026-09-30"})
    with pytest.raises(d12.D12ConflictError):
        d12.submit_after_review(
            conn,
            review_id=review["review_id"],
            identity=resolve_identity_for_testing("OPERATOR-A1"),
            submission_override=mutated,
        )
    assert conn.execute("SELECT COUNT(*) FROM d10_business_actions").fetchone()[0] == 0


def test_state_change_makes_old_review_stale(conn):
    seed(conn)
    review = request(conn, submission())
    conn.execute(
        "UPDATE orders SET latest_supplier_commitment=?,updated_at=? WHERE order_id='ORD-A'",
        ("2026-08-25", "2026-08-16T21:00:00+08:00"),
    )
    conn.commit()
    with pytest.raises(d12.D12StaleReview):
        d12.decide_review(
            conn,
            review_id=review["review_id"],
            identity=resolve_identity_for_testing("MANAGER-A"),
            decision="APPROVE",
        )
    assert conn.execute("SELECT status FROM d12_human_reviews WHERE review_id=?", (review["review_id"],)).fetchone()[0] == "STALE"


def test_duplicate_approval_and_submit_are_idempotent(conn):
    seed(conn)
    review = request(conn, submission())
    manager = resolve_identity_for_testing("MANAGER-A")
    first = d12.decide_review(conn, review_id=review["review_id"], identity=manager, decision="APPROVE")
    second = d12.decide_review(conn, review_id=review["review_id"], identity=manager, decision="APPROVE")
    assert first["status"] == "APPROVED"
    assert second["duplicate_skipped"] is True

    operator = resolve_identity_for_testing("OPERATOR-A1")
    result1 = d12.submit_after_review(conn, review_id=review["review_id"], identity=operator)
    result2 = d12.submit_after_review(conn, review_id=review["review_id"], identity=operator)
    assert result2["business_action_id"] == result1["business_action_id"]
    assert result2["review_replayed"] is True
    assert conn.execute("SELECT COUNT(*) FROM d10_business_actions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM d10_outbox_events").fetchone()[0] == 1


def test_rejected_review_cannot_be_bypassed(conn):
    seed(conn)
    review = request(conn, submission())
    d12.decide_review(
        conn,
        review_id=review["review_id"],
        identity=resolve_identity_for_testing("MANAGER-A"),
        decision="REJECT",
    )
    with pytest.raises(d12.D12ForbiddenError):
        d12.submit_after_review(
            conn,
            review_id=review["review_id"],
            identity=resolve_identity_for_testing("OPERATOR-A1"),
        )
    assert conn.execute("SELECT COUNT(*) FROM d10_business_actions").fetchone()[0] == 0


def test_unknown_or_external_send_action_is_forbidden(conn):
    seed(conn)
    for action_type in ("DROP_DATABASE", "SEND_MESSAGE"):
        with pytest.raises(d12.D12ForbiddenError):
            request(conn, submission(action_type=action_type, idem=f"FORBID-{action_type}"))


def test_legacy_agent_approval_no_longer_directly_writes_formal_delivery_date(conn):
    seed(conn)
    approval = {
        "approval_id": "APR-LEGACY",
        "order_id": "ORD-A",
        "action_type": "UPDATE_ORDER",
        "payload_json": '{"updates":{"requested_delivery_date":"2026-09-01"}}',
    }
    result = agent_api._commit_approved_action(conn, approval, "MANAGER-A")
    assert result["d12_review_required"] is True
    assert result["blocked_fields"] == ["requested_delivery_date"]
    assert result["updated_fields"] == []
    assert conn.execute("SELECT requested_delivery_date FROM orders WHERE order_id='ORD-A'").fetchone()[0] == "2026-08-20"


def test_d12_static_ui_exposes_business_language_and_removes_direct_date_edit():
    from pathlib import Path
    app_js = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")
    assert 'data-d12-delivery' in app_js
    assert '申请修改客户交期' in app_js
    assert '需要主管审批' in app_js
    assert '我可以确认' in app_js
    assert '审批通过 ≠ ERP 已修改' in app_js
    # Order creation may record an already-agreed date, but the generic edit modal
    # must not be able to mutate the formal customer commitment.
    edit_block = app_js.split('function openEditOrderModal', 1)[1].split('async function openTaskModal', 1)[0]
    assert 'name="customer_delivery_date"' not in edit_block
    assert "input[name],select[name]" in edit_block


def test_generic_order_patch_cannot_bypass_d12_formal_delivery_gate():
    from fastapi import HTTPException
    import main
    from auth import CurrentIdentity

    identity = CurrentIdentity(user_id="OPERATOR-1", organization_id="ORG-A", role="operator")
    for body in (
        {"requested_delivery_date": "2026-08-31"},
        {"customer_delivery_date": "2026-08-31"},
    ):
        with pytest.raises(HTTPException) as exc:
            main.update_order("ORD-ANY", main.AnyPayload(**body), identity)
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "D12_MANAGER_APPROVAL_REQUIRED"
        assert exc.value.detail["required_review"] == "MANAGER_APPROVAL"


def test_d12_defense_in_depth_rejects_invalid_delivery_date_before_review_storage(conn):
    seed(conn)
    bad = submission(
        action_type="UPDATE_EXPECTED_DELIVERY_DATE",
        payload={"expected_delivery_date": "2026-13-99"},
        idem="BAD-D12-DATE",
    )
    with pytest.raises(d12.D12StateError):
        request(conn, bad)
    assert conn.execute("SELECT COUNT(*) FROM d12_human_reviews").fetchone()[0] == 0
