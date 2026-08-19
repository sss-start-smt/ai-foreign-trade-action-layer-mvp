from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("DB_PATH", str(Path(__file__).parent / "test_action_layer.db"))
os.environ.setdefault("APP_API_KEY", "test-key")
os.environ.setdefault("SEED_DEMO_DATA", "true")
os.environ.setdefault("ENABLE_DEMO_ADMIN_ACTIONS", "true")

from fastapi.testclient import TestClient

import agent_api
from conftest import auth_headers
from main import app, db, init_db, iso

client = TestClient(app)
AGENT_HEADERS = {"X-FlowOrder-Agent-Key": "agent-test-key"}


def setup_function():
    agent_api.AGENT_API_KEY = "agent-test-key"
    init_db()
    assert client.post("/api/reset", headers=AGENT_HEADERS).status_code == 200
    now = iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO orders(order_id,order_no,customer_name,requested_delivery_date,latest_supplier_commitment,status,owner,organization_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("D13-ORD-A", "PO-D13-A", "ACME", "2026-08-20", "2026-08-18", "ACTIVE", "OPERATOR-A1", "ORG-A", now, now),
        )
        conn.execute(
            """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,stage,lifecycle_status,observation_status,first_seen_at,last_seen_at,version,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("D13-AC-A", "ORG-A", "D13-ORD-A", "v1:DELIVERY_RECOVERY", "DELIVERY_RECOVERY", "IN_PROGRESS", "ACTIVE", "OBSERVED", now, now, 1, now, now),
        )
        conn.execute(
            "INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            ("D13-TK-A", "ORG-A", "D13-AC-A", "处理交期变化", "确认处理方案", "IN_PROGRESS", 1, now, now),
        )
        conn.commit()


def test_d13_request_action_requires_both_user_identity_and_agent_key():
    body = {
        "tool_name": "request_change_customer_delivery_date",
        "task_id": "D13-TK-A",
        "payload": {"customer_delivery_date": "2026-08-23"},
        "idempotency_key": "D13-HTTP-1",
    }
    missing_agent = client.post("/api/d13/tools/request-action", headers=auth_headers("OPERATOR-A1"), json=body)
    assert missing_agent.status_code == 401
    missing_user = client.post("/api/d13/tools/request-action", headers=AGENT_HEADERS, json=body)
    assert missing_user.status_code == 401
    ok = client.post(
        "/api/d13/tools/request-action",
        headers={**AGENT_HEADERS, **auth_headers("OPERATOR-A1")},
        json=body,
    )
    assert ok.status_code == 200
    assert ok.json()["required_review"] == "MANAGER_APPROVAL"
    assert ok.json()["agent_executed_effect"] is False


def test_d13_http_body_role_claim_never_auto_approves():
    response = client.post(
        "/api/d13/tools/request-action",
        headers={**AGENT_HEADERS, **auth_headers("OPERATOR-A1")},
        json={
            "tool_name": "request_change_customer_delivery_date",
            "task_id": "D13-TK-A",
            "payload": {"customer_delivery_date": "2026-08-24"},
            "idempotency_key": "D13-HTTP-2",
            "current_role": "manager",
            "manager_id": "MANAGER-A",
            "approve": True,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["review_status"] == "PENDING"
    with db() as conn:
        review = conn.execute(
            "SELECT requested_by,requester_role,reviewed_by,status FROM d12_human_reviews WHERE review_id=?",
            (data["review_id"],),
        ).fetchone()
        assert review[0] == "OPERATOR-A1"
        assert review[1] == "operator"
        assert review[2] is None
        assert review[3] == "PENDING"


def test_d13_http_cross_org_task_is_404_and_removed_tool_is_403():
    cross = client.post(
        "/api/d13/tools/request-action",
        headers={**AGENT_HEADERS, **auth_headers("OPERATOR-B1")},
        json={
            "tool_name": "request_change_customer_delivery_date",
            "task_id": "D13-TK-A",
            "payload": {"customer_delivery_date": "2026-08-25"},
            "idempotency_key": "D13-HTTP-B",
        },
    )
    assert cross.status_code == 404
    forbidden = client.post(
        "/api/d13/tools/request-action",
        headers={**AGENT_HEADERS, **auth_headers("OPERATOR-A1")},
        json={"tool_name": "request_high_risk_override", "task_id": "D13-TK-A", "payload": {}, "idempotency_key": "D13-OVERRIDE"},
    )
    assert forbidden.status_code == 403


def test_d13_runtime_http_start_read_before_ask_and_trace():
    headers = {**AGENT_HEADERS, **auth_headers("OPERATOR-A1")}
    start = client.post(
        "/api/d13/runs/start",
        headers=headers,
        json={
            "goal": "这个订单有点问题，你处理一下",
            "trigger_type": "USER_REQUEST",
            "current_datetime": "2026-08-17T12:00:00+08:00",
            "timezone": "Asia/Shanghai",
            "model_provider": "TEST",
            "model_name": "TEST_MODEL",
        },
    )
    assert start.status_code == 200
    run_id = start.json()["run_id"]
    read = client.post(
        f"/api/d13/runs/{run_id}/plan",
        headers=headers,
        json={
            "decision": "TOOL_CALLS",
            "tool_calls": [{"tool_name": "get_order_context", "payload": {"order_no": "PO-D13-A"}}],
        },
    )
    assert read.status_code == 200
    assert read.json()["continue_model"] is True
    trace = client.get(f"/api/d13/runs/{run_id}/trace", headers=auth_headers("OPERATOR-A1"))
    assert trace.status_code == 200
    data = trace.json()
    assert data["trace_contains_hidden_chain_of_thought"] is False
    assert any(x["event_type"] == "TOOL_CALL" for x in data["events"])


def test_d13_runtime_trace_is_cross_org_hidden():
    headers = {**AGENT_HEADERS, **auth_headers("OPERATOR-A1")}
    start = client.post(
        "/api/d13/runs/start",
        headers=headers,
        json={"goal": "查看订单", "trigger_type": "USER_REQUEST"},
    )
    assert start.status_code == 200
    run_id = start.json()["run_id"]
    cross = client.get(f"/api/d13/runs/{run_id}/trace", headers=auth_headers("OPERATOR-B1"))
    assert cross.status_code == 404


def test_d13_execute_endpoint_drives_selected_model_runtime(monkeypatch):
    import d13_agent_runtime as d13_runtime

    headers = {**AGENT_HEADERS, **auth_headers("OPERATOR-A1")}
    start = client.post(
        "/api/d13/runs/start",
        headers=headers,
        json={
            "goal": "查看这个订单",
            "trigger_type": "USER_REQUEST",
            "active_order_no": "PO-D13-A",
            "current_datetime": "2026-08-17T12:00:00+08:00",
        },
    )
    assert start.status_code == 200
    run_id = start.json()["run_id"]

    def fake_auto(conn, *, run_id, identity):
        return {
            "run": {"run_id": run_id, "status": "COMPLETED", "stop_reason": "GOAL_SATISFIED"},
            "observations": [],
            "continue_model": False,
            "model_telemetry": {"attempt_count": 1},
        }

    monkeypatch.setattr(d13_runtime, "run_with_selected_model", fake_auto)
    response = client.post(f"/api/d13/runs/{run_id}/execute", headers=headers)
    assert response.status_code == 200
    assert response.json()["model_telemetry"]["attempt_count"] == 1


def test_d13_model_execution_http_error_never_echoes_provider_secret():
    import d13_api
    import d13_agent_runtime as d13_runtime

    secret = "SK-FAKE-12345"
    exc = d13_runtime.D13ModelExecutionError(
        f"provider failure api_key={secret}",
        error_kind="PROVIDER_PERMANENT",
    )
    http_exc = d13_api._http_error(exc)
    assert http_exc.status_code == 503
    assert secret not in str(http_exc.detail)
    assert http_exc.detail["message"] == "Model provider unavailable"
    assert http_exc.detail["error_kind"] == "PROVIDER_PERMANENT"
