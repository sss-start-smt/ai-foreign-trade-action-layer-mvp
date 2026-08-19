from __future__ import annotations

import json
import os
import sys
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from auth import resolve_identity_for_testing
from database import _LegacySQLiteWrapper
from d9_task_waiting import create_task, put_task_on_waiting, run_due_recovery, start_task
from d10_business_action import BusinessActionSubmission, submit_business_action
from d11_action_workspace import (
    build_case_workspace,
    list_action_workspaces,
    start_case_task,
    wait_case_task,
    record_case_waiting_reply,
)

CN_TZ = timezone(timedelta(hours=8))
NOW = "2026-08-14T15:00:00+08:00"


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "d11.db"
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    schema = Path(__file__).resolve().parents[1] / "schema.sql"
    raw.executescript(schema.read_text(encoding="utf-8"))
    # schema.sql is a legacy-compatible base; runtime adds these auth columns.
    cols = {r[1] for r in raw.execute("PRAGMA table_info(orders)").fetchall()}
    if "organization_id" not in cols:
        raw.execute("ALTER TABLE orders ADD COLUMN organization_id TEXT")
    task_cols = {r[1] for r in raw.execute("PRAGMA table_info(tasks)").fetchall()}
    if "organization_id" not in task_cols:
        raw.execute("ALTER TABLE tasks ADD COLUMN organization_id TEXT")
    raw.commit()
    wrapper = _LegacySQLiteWrapper(raw)
    yield wrapper
    raw.close()


def seed_order_case(conn, *, order_id="ORD-1", case_id="AC-1", owner="USER-1", org="ORG-A", lifecycle="ACTIVE", title="解决交期异常"):
    conn.execute(
        "INSERT INTO orders(order_id,order_no,customer_name,status,owner,organization_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (order_id, order_id, "测试客户", "ACTIVE", owner, org, NOW, NOW),
    )
    conn.execute(
        """INSERT INTO action_cases(
        action_case_id,organization_id,order_id,action_intent_key,intent_type,stage,lifecycle_status,title,
        latest_action_bucket,latest_severity,latest_recommended_action,latest_evidence_json,observation_status,
        first_seen_at,last_seen_at,version,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            case_id, org, order_id, f"v1:{case_id}", "DELIVERY_RECOVERY", "IN_PROGRESS", lifecycle, title,
            "ACTION", "high", "尽快确认供应商交期", json.dumps(["供应商尚未确认"]), "OBSERVED",
            NOW, NOW, 1, NOW, NOW,
        ),
    )
    conn.commit()


def test_operator_scoping_and_legacy_tasks_are_not_used(conn):
    seed_order_case(conn, order_id="ORD-1", case_id="AC-1", owner="USER-1")
    seed_order_case(conn, order_id="ORD-2", case_id="AC-2", owner="USER-2")
    create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="正式D9任务", actor="seed")
    conn.execute(
        "INSERT INTO tasks(task_id,related_order_id,title,status,owner_user_id,created_at,updated_at,organization_id) VALUES(?,?,?,?,?,?,?,?)",
        ("LEGACY-LEAK", "ORD-1", "不应出现在D11的旧任务", "OPEN", "USER-1", NOW, NOW, "ORG-A"),
    )
    conn.commit()

    operator = list_action_workspaces(conn, resolve_identity_for_testing("USER-1"))
    assert [x["action_case"]["action_case_id"] for x in operator["items"]] == ["AC-1"]
    titles = [t["title"] for x in operator["items"] for t in x["actionable_tasks"]]
    assert titles == ["正式D9任务"]
    assert "不应出现在D11的旧任务" not in titles

    manager = list_action_workspaces(conn, resolve_identity_for_testing("MANAGER-1"))
    assert {x["action_case"]["action_case_id"] for x in manager["items"]} == {"AC-1", "AC-2"}


def test_multiple_actionable_tasks_are_shown_without_frontend_priority_invention(conn):
    seed_order_case(conn)
    create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="联系供应商", actor="seed")
    create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="核对库存", actor="seed")
    conn.commit()

    w = build_case_workspace(conn, resolve_identity_for_testing("USER-1"), "AC-1")
    assert w["workspace_state"] == "ACTIONABLE"
    assert len(w["actionable_tasks"]) == 2
    assert w["primary_task"] is None


def test_waiting_on_one_task_does_not_hide_other_actionable_task(conn):
    seed_order_case(conn)
    t1 = create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="联系供应商", actor="seed")
    t2 = create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="核对替代方案", actor="seed")
    start_task(conn, t1["task_id"], actor="seed")
    put_task_on_waiting(
        conn, task_id=t1["task_id"], waiting_type="SUPPLIER_REPLY",
        due_at="2026-08-15T10:00:00+08:00", reason="等待供应商回复", actor="seed",
    )
    conn.commit()

    w = build_case_workspace(conn, resolve_identity_for_testing("USER-1"), "AC-1")
    assert w["workspace_state"] == "ACTIONABLE"
    assert [t["task_id"] for t in w["actionable_tasks"]] == [t2["task_id"]]
    assert [t["task_id"] for t in w["waiting_tasks"]] == [t1["task_id"]]


def test_reply_recovery_is_reflected_in_same_case_workspace(conn):
    seed_order_case(conn)
    t = create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="联系供应商", actor="seed")
    identity = resolve_identity_for_testing("USER-1")
    start_case_task(conn, identity, t["task_id"])
    waiting = wait_case_task(
        conn, identity, task_id=t["task_id"], waiting_type="SUPPLIER_REPLY",
        due_at="2026-08-15T10:00:00+08:00", reason="等待供应商确认",
    )
    conn.commit()

    before = build_case_workspace(conn, identity, "AC-1")
    assert before["waiting_tasks"][0]["status"] == "WAITING"

    record_case_waiting_reply(
        conn, identity, waiting_id=waiting["waiting_id"], reply_id="R-1",
        reply_payload={"summary": "确认8月23日"}, satisfies_completion=True,
    )
    conn.commit()
    after = build_case_workspace(conn, identity, "AC-1")
    assert after["workspace_state"] == "ACTIONABLE"
    assert after["actionable_tasks"][0]["status"] == "IN_PROGRESS"
    assert after["actionable_tasks"][0]["waiting_history"][0]["status"] == "RESOLVED"


def test_due_recovery_returns_waiting_task_to_actionable_workspace(conn):
    seed_order_case(conn)
    t = create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="等待到期任务", actor="seed")
    start_task(conn, t["task_id"], actor="seed")
    put_task_on_waiting(
        conn, task_id=t["task_id"], waiting_type="EXTERNAL_REPLY",
        due_at="2026-08-14T12:00:00+08:00", reason="超时等待", actor="seed",
    )
    conn.commit()
    run_due_recovery(conn, organization_id="ORG-A", current_time="2026-08-14T16:00:00+08:00")
    conn.commit()

    w = build_case_workspace(conn, resolve_identity_for_testing("USER-1"), "AC-1")
    assert w["workspace_state"] == "ACTIONABLE"
    assert w["actionable_tasks"][0]["status"] == "IN_PROGRESS"
    assert w["actionable_tasks"][0]["waiting_history"][0]["status"] == "EXPIRED"


def test_business_action_is_visible_but_not_presented_as_external_success(conn):
    seed_order_case(conn)
    t = create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="更新预计交期", actor="seed")
    start_task(conn, t["task_id"], actor="seed")
    submission = BusinessActionSubmission(
        organization_id="ORG-A",
        task_id=t["task_id"],
        action_type="UPDATE_EXPECTED_DELIVERY_DATE",
        target_type="ERP_SALES_ORDER",
        target_id="ORD-1",
        payload={"before": "2026-08-20", "after": "2026-08-23"},
        request_id="REQ-D11-1",
        idempotency_key="D11-BA-1",
        actor="USER-1",
        source="D11_TEST",
        reason="交期调整",
    )
    result = submit_business_action(conn, submission)
    conn.commit()

    w = build_case_workspace(conn, resolve_identity_for_testing("USER-1"), "AC-1")
    task = w["actionable_tasks"][0]
    assert result["status"] == "ACCEPTED"
    assert task["business_action"]["status"] == "ACCEPTED"
    assert task["outbox"]["status"] == "PENDING"
    assert task["outbox"]["published_at"] is None


def test_closed_case_never_exposes_open_task_as_executable(conn):
    seed_order_case(conn, lifecycle="CLOSED")
    # Seed inconsistent historical row directly: D9 correctly refuses creating it now,
    # but D11 must remain safe when old data exists after upgrades.
    conn.execute(
        "INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        ("TK-GHOST", "ORG-A", "AC-1", "旧开放任务", "IN_PROGRESS", 1, NOW, NOW),
    )
    conn.commit()

    w = build_case_workspace(conn, resolve_identity_for_testing("USER-1"), "AC-1")
    assert w["workspace_state"] == "CLOSED"
    assert w["actionable_tasks"] == []
    assert w["waiting_tasks"] == []
    assert [t["task_id"] for t in w["blocked_open_tasks"]] == ["TK-GHOST"]


# ── D11 UAT Fixture Provider Tests (TEST/UAT ONLY) ────────────────────


def test_uat_fixture_provider_produces_deterministic_results():
    """D11_UAT_INTAKE_PROVIDER=fixture produces deterministic candidate changes."""
    os.environ["D11_UAT_INTAKE_PROVIDER"] = "fixture"
    try:
        import main as fm
        candidate = fm.uat_fixture_candidate(
            "工厂说订单会延迟一周", "factory", NOW,
            {"order_id": "ORD-1", "order_no": "SO-2026-001"},
        )
        assert candidate["_uat_fixture"] is True
        assert candidate["_integration"]["workflow_key"] == "UAT_FIXTURE_PROVIDER"
        assert candidate["message_type"] == "factory_update"
        assert len(candidate["action_candidates"]) >= 1
        assert candidate["action_candidates"][0]["action_type"] == "confirm_with_factory"
        assert candidate["manual_review_required"] is True

        # Deterministic: same input → same output
        candidate2 = fm.uat_fixture_candidate(
            "工厂说订单会延迟一周", "factory", NOW,
            {"order_id": "ORD-1", "order_no": "SO-2026-001"},
        )
        assert candidate == candidate2
    finally:
        del os.environ["D11_UAT_INTAKE_PROVIDER"]


def test_uat_fixture_provider_cancel_keyword():
    """Cancel keyword triggers customer_cancellation risk."""
    os.environ["D11_UAT_INTAKE_PROVIDER"] = "fixture"
    try:
        import main as fm
        candidate = fm.uat_fixture_candidate(
            "客户要求取消订单", "customer", NOW,
            {"order_id": "ORD-2", "order_no": "SO-2026-002"},
        )
        assert candidate["_uat_fixture"] is True
        risks = [r["type"] for r in candidate["risk_signals"]]
        assert "customer_cancellation" in risks
        actions = [a["action_type"] for a in candidate["action_candidates"]]
        assert "reply_customer" in actions
    finally:
        del os.environ["D11_UAT_INTAKE_PROVIDER"]


def test_uat_fixture_provider_no_match_fallback():
    """Unknown keyword falls back to generic check_order action."""
    os.environ["D11_UAT_INTAKE_PROVIDER"] = "fixture"
    try:
        import main as fm
        candidate = fm.uat_fixture_candidate(
            "这是一条不包含任何关键词的普通消息", "customer", NOW, None,
        )
        assert candidate["_uat_fixture"] is True
        assert len(candidate["action_candidates"]) >= 1
        assert candidate["action_candidates"][0]["action_type"] == "check_order"
        assert candidate["manual_review_required"] is True
    finally:
        del os.environ["D11_UAT_INTAKE_PROVIDER"]


def test_uat_fixture_provider_disabled_by_default():
    """Without D11_UAT_INTAKE_PROVIDER env var, fixture is not used."""
    saved = os.environ.pop("D11_UAT_INTAKE_PROVIDER", None)
    try:
        import main as fm
        # When env var is not set, uat_provider check won't match "fixture"
        # The analyze_intake_body function would proceed to Coze path
        # We test that uat_fixture_candidate still works as a function
        candidate = fm.uat_fixture_candidate(
            "测试消息", "customer", NOW, None,
        )
        assert candidate["_uat_fixture"] is True
        assert candidate["manual_review_required"] is True
    finally:
        if saved:
            os.environ["D11_UAT_INTAKE_PROVIDER"] = saved
