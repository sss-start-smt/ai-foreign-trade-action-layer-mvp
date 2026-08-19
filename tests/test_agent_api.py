import os
from pathlib import Path

os.environ.setdefault("DB_PATH", str(Path(__file__).parent / "test_action_layer.db"))
os.environ.setdefault("APP_API_KEY", "test-key")
os.environ.setdefault("SEED_DEMO_DATA", "true")

from fastapi.testclient import TestClient
import agent_api
from main import app, db, init_db, iso, new_id
from conftest import auth_headers

client = TestClient(app)
HEADERS = {"X-FlowOrder-Agent-Key": "agent-test-key"}


def setup_function():
    agent_api.AGENT_API_KEY = "agent-test-key"
    init_db()
    assert client.post("/api/reset", headers={"X-FlowOrder-Agent-Key": "agent-test-key"}).status_code == 200
    assert client.post("/api/demo/seed", headers={"X-FlowOrder-Agent-Key": "agent-test-key"}).status_code == 200
    with db() as conn:
        conn.execute(
            """INSERT INTO order_dependencies(dependency_id,order_id,dependency_type,dependency_name,sequence_no,status,blocking_party,due_at,evidence_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ("DEP-TEST-1", "ORD-1003", "CUSTOMER_CONFIRMATION", "确认彩盒样品", 1,
             "WAITING_CONFIRMATION", "customer", "2026-08-01T10:00:00+08:00", "[]", iso(), iso()),
        )
        conn.execute(
            "INSERT INTO logistics_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("LOG-TEST-1", "ORD-1001", "VESSEL_DELAY", "DELAYED", "Ningbo",
             "船期延误2天", iso(), "2026-08-22T12:00:00+08:00", "SYNTHETIC", None, iso(), iso()),
        )
        # Create a test order owned by USER-2 with no tasks for USER-1
        now = iso()
        conn.execute(
            """INSERT INTO orders(order_id,order_no,customer_name,product_name,packaging_method,
               requested_delivery_date,latest_supplier_commitment,current_progress,current_node,
               status,owner,action_readiness,contact_status,issue_status,organization_id,
               initialization_source,initialization_note,initialization_waiting_on,
               created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("ORD-TEST-NOACCESS", "PO-TEST", "Test Customer", "Test Product", "Box",
             "2026-09-01", None, 0.0, "New", "ACTIVE", "USER-2",
             "ACTION_GENERATED", "UNKNOWN", "UNKNOWN", "ORG-A",
             "TEST", None, None, now, now),
        )
        conn.commit()


def test_agent_status_and_tool_auth():
    assert client.get("/api/agent/status", headers=auth_headers("USER-1")).status_code == 200
    denied = client.post(
        "/api/agent/tools/candidate-orders/list",
        headers=auth_headers("USER-1"),
        json={},
    )
    assert denied.status_code == 401
    allowed = client.post(
        "/api/agent/tools/candidate-orders/list",
        headers={**HEADERS, **auth_headers("USER-1")},
        json={"due_within_days": 30},
    )
    assert allowed.status_code == 200
    assert all(item["owner"] == "USER-1" for item in allowed.json()["items"])


def test_agent_order_context_respects_owner_scope():
    ok = client.post(
        "/api/agent/tools/orders/context",
        headers={**HEADERS, **auth_headers("USER-1")},
        json={"order_id": "ORD-1001"},
    )
    assert ok.status_code == 200
    # USER-1 has no task on ORD-TEST-NOACCESS (owned by USER-2) and is not the owner -> 403
    denied = client.post(
        "/api/agent/tools/orders/context",
        headers={**HEADERS, **auth_headers("USER-1")},
        json={"order_id": "ORD-TEST-NOACCESS"},
    )
    assert denied.status_code == 403
    manager = client.post(
        "/api/agent/tools/orders/context",
        headers={**HEADERS, **auth_headers("MANAGER-1")},
        json={"order_id": "ORD-TEST-NOACCESS"},
    )
    assert manager.status_code == 200


def test_local_inspection_generates_ranked_candidates_and_report():
    response = client.post(
        "/api/agent/inspection/run",
        headers=auth_headers("USER-1"),
        json={"due_within_days": 60, "top_n": 7},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["human_confirmation_required"] is True
    assert len(data["top_items"]) <= 7
    assert all("evidence" in item for item in data["top_items"])
    overview = client.get(
        "/api/agent/overview?current_user_id=USER-1&current_role=operator",
        headers=auth_headers("USER-1"),
    )
    assert overview.status_code == 200
    assert overview.json()["summary"]["report_count"] >= 1


def test_anomaly_confirmation_is_human_decision():
    result = client.post(
        "/api/agent/tools/anomalies/build",
        headers={**HEADERS, **auth_headers("USER-1")},
        json={"order_id": "ORD-1001", "anomaly_types": ["LOGISTICS_EXCEPTION"]},
    )
    assert result.status_code == 200
    candidate = result.json()["items"][0]
    assert candidate["status"] == "ANOMALY_CANDIDATE"
    decided = client.post(
        f"/api/agent/candidates/{candidate['candidate_id']}/decision",
        headers=auth_headers("USER-1"),
        json={"decision": "CONFIRM", "operator_id": "USER-1"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "CONFIRMED"


def test_approval_matrix_requires_manager_for_high_risk_order_change():
    request = client.post(
        "/api/agent/tools/approvals/create",
        headers={**HEADERS, **auth_headers("USER-1")},
        json={
            "order_id": "ORD-1001",
            "action_type": "UPDATE_ORDER",
            "idempotency_key": "TEST-HIGH-RISK-1",
            "action_payload": {"updates": {"requested_delivery_date": "2026-09-01"}},
        },
    )
    assert request.status_code == 200
    approval_id = request.json()["approval_id"]
    denied = client.post(
        f"/api/agent/approvals/{approval_id}/decision",
        headers=auth_headers("USER-1"),
        json={"decision": "APPROVE", "operator_id": "USER-1"},
    )
    assert denied.status_code == 403
    approved = client.post(
        f"/api/agent/approvals/{approval_id}/decision",
        headers=auth_headers("MANAGER-1"),
        json={"decision": "APPROVE", "note": "manager approval"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "APPROVED"


def test_create_task_draft_does_not_write_formal_task():
    before = client.get(
        "/api/dashboard?current_user_id=USER-1",
        headers=auth_headers("USER-1"),
    ).json()["summary"]["total"]
    draft = client.post(
        "/api/agent/tools/task-drafts/create",
        headers={**HEADERS, **auth_headers("USER-1")},
        json={"order_id": "ORD-1001", "title": "确认物流补救方案"},
    )
    assert draft.status_code == 200
    assert draft.json()["status"] == "DRAFT"
    after = client.get(
        "/api/dashboard?current_user_id=USER-1",
        headers=auth_headers("USER-1"),
    ).json()["summary"]["total"]
    assert before == after


def test_agent_run_enforces_eight_tool_call_budget():
    started = client.post(
        "/api/agent/tools/runs/start",
        headers={**HEADERS, **auth_headers("USER-1")},
        json={"goal": "预算测试"},
    )
    assert started.status_code == 200
    run_id = started.json()["run_id"]
    for _ in range(8):
        response = client.post(
            "/api/agent/tools/candidate-orders/list",
            headers={**HEADERS, **auth_headers("USER-1")},
            json={"run_id": run_id, "due_within_days": 30},
        )
        assert response.status_code == 200
    blocked = client.post(
        "/api/agent/tools/candidate-orders/list",
        headers={**HEADERS, **auth_headers("USER-1")},
        json={"run_id": run_id, "due_within_days": 30},
    )
    assert blocked.status_code == 429


def test_operator_cannot_approve_other_owners_normal_action():
    request = client.post(
        "/api/agent/tools/approvals/create",
        headers={**HEADERS, **auth_headers("MANAGER-1")},
        json={
            "order_id": "ORD-1004",
            "action_type": "CREATE_TASK",
            "idempotency_key": "OTHER-OWNER-TASK",
            "action_payload": {"title": "处理其他负责人订单", "owner_user_id": "USER-3"},
        },
    )
    assert request.status_code == 200
    denied = client.post(
        f"/api/agent/approvals/{request.json()['approval_id']}/decision",
        headers=auth_headers("USER-1"),
        json={"decision": "APPROVE", "operator_id": "USER-1"},
    )
    assert denied.status_code == 403


def test_record_contact_moves_task_to_waiting_external():
    task_id = client.get(
        "/api/tasks?current_user_id=USER-1",
        headers=auth_headers("USER-1"),
    ).json()["items"][0]["task_id"]
    with db() as conn:
        order_id = conn.execute("SELECT related_order_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()[0]
    request = client.post(
        "/api/agent/tools/approvals/create",
        headers={**HEADERS, **auth_headers("USER-1")},
        json={
            "order_id": order_id,
            "action_type": "RECORD_CONTACT",
            "idempotency_key": "RECORD-CONTACT-TEST",
            "action_payload": {
                "task_id": task_id,
                "waiting_on": "factory",
                "promised_reply_at": "2026-08-03T15:00:00+08:00",
            },
        },
    )
    assert request.status_code == 200
    approved = client.post(
        f"/api/agent/approvals/{request.json()['approval_id']}/decision",
        headers=auth_headers("USER-1"),
        json={"decision": "APPROVE", "operator_id": "USER-1"},
    )
    assert approved.status_code == 200
    with db() as conn:
        task = conn.execute(
            "SELECT status,waiting_on,promised_reply_at FROM tasks WHERE task_id=?",
            (task_id,),
        ).fetchone()
    assert task[0] == "WAITING_EXTERNAL"
    assert task[1] == "factory"