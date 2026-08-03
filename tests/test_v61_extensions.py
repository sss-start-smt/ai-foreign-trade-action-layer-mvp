import os
from pathlib import Path

os.environ.setdefault("DB_PATH", str(Path(__file__).parent / "test_action_layer.db"))
os.environ.setdefault("APP_API_KEY", "test-key")
os.environ.setdefault("SEED_DEMO_DATA", "true")

from fastapi.testclient import TestClient

import agent_api
from main import app, db, init_db

client = TestClient(app)
HEADERS = {"X-FlowOrder-Agent-Key": "agent-test-key"}


def setup_function():
    agent_api.AGENT_API_KEY = "agent-test-key"
    init_db()
    assert client.post("/api/reset").status_code == 200
    assert client.post("/api/demo/seed").status_code == 200


def test_parse_bulk_order_updates_returns_confirmation_candidates():
    response = client.post(
        "/api/agent/tools/bulk-updates/parse",
        headers=HEADERS,
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "organization_id": "ORG-DEMO",
            "text": "PO-1002工厂说现在做到82%，最新承诺8月6日完工。",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["confirmation_required"] is True
    assert data["summary"]["matched_order_count"] == 1
    updates = data["orders"][0]["updates"]
    assert any(x["field_name"] == "current_progress" and x["new_value"] == 0.82 for x in updates)
    assert any(x["field_name"] == "latest_supplier_commitment" for x in updates)


def test_confirm_bulk_update_writes_safe_field_and_tracks_event():
    parsed = client.post(
        "/api/agent/bulk-updates/parse",
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "organization_id": "ORG-DEMO",
            "text": "PO-1002目前已经完成88%，正在包装。",
        },
    ).json()
    updates = [u for u in parsed["orders"][0]["updates"] if u["field_name"] in {"current_progress", "current_node"}]
    confirmed = client.post(
        "/api/agent/bulk-updates/confirm",
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "organization_id": "ORG-DEMO",
            "batch_id": parsed["batch_id"],
            "decisions": [{"update_id": u["update_id"], "decision": "ACCEPT"} for u in updates],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["updated_order_count"] == 1
    with db() as conn:
        row = conn.execute("SELECT current_progress,current_node FROM orders WHERE order_no='PO-1002'").fetchone()
        events = conn.execute("SELECT event_name FROM analytics_events WHERE event_name='bulk_update_confirmed'").fetchall()
    assert row[0] == 0.88
    assert row[1] == "包装中"
    assert events


def test_high_risk_delivery_date_update_creates_approval_not_direct_write():
    with db() as conn:
        before = conn.execute("SELECT requested_delivery_date FROM orders WHERE order_no='PO-1002'").fetchone()[0]
    parsed = client.post(
        "/api/agent/bulk-updates/parse",
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "organization_id": "ORG-DEMO",
            "text": "PO-1002客户同意把正式交期改到8月25日。",
        },
    ).json()
    high = next(u for u in parsed["orders"][0]["updates"] if u["field_name"] == "requested_delivery_date")
    confirmed = client.post(
        "/api/agent/bulk-updates/confirm",
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "organization_id": "ORG-DEMO",
            "batch_id": parsed["batch_id"],
            "decisions": [{"update_id": high["update_id"], "decision": "ACCEPT"}],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["approval_created_count"] == 1
    with db() as conn:
        after = conn.execute("SELECT requested_delivery_date FROM orders WHERE order_no='PO-1002'").fetchone()[0]
        approval = conn.execute("SELECT status,action_type FROM approval_requests ORDER BY created_at DESC LIMIT 1").fetchone()
    assert after == before
    assert approval[0] == "PENDING"
    assert approval[1] == "UPDATE_ORDER"


def test_diagnose_priority_orders_is_one_composite_tool_call():
    started = client.post(
        "/api/agent/tools/runs/start",
        headers=HEADERS,
        json={"current_user_id": "USER-1", "current_role": "operator", "goal": "组合诊断"},
    )
    run_id = started.json()["run_id"]
    response = client.post(
        "/api/agent/tools/priority-orders/diagnose",
        headers=HEADERS,
        json={
            "run_id": run_id,
            "current_user_id": "USER-1",
            "current_role": "operator",
            "organization_id": "ORG-DEMO",
            "due_within_days": 60,
            "top_n": 7,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["count"] <= 7
    assert data["selection_strategy"]["not_padded"] is True
    with db() as conn:
        calls = conn.execute("SELECT tool_name FROM agent_tool_calls WHERE run_id=?", (run_id,)).fetchall()
    assert [x[0] for x in calls] == ["diagnose_priority_orders"]


def test_analytics_summary_exposes_activation_ai_and_quality():
    client.post(
        "/api/analytics/events",
        json={
            "event_name": "anomaly_result_viewed",
            "organization_id": "ORG-DEMO",
            "user_id": "USER-1",
            "properties": {"candidate_count": 1},
        },
    )
    response = client.get("/api/analytics/summary?days=30&organization_id=ORG-DEMO")
    assert response.status_code == 200
    data = response.json()
    assert "activation_funnel" in data
    assert "ai_value" in data
    assert "system_quality" in data
    assert data["event_counts"]["anomaly_result_viewed"] >= 1
