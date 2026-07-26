import os
from pathlib import Path

os.environ["DB_PATH"] = str(Path(__file__).parent / "test_action_layer.db")
os.environ["APP_API_KEY"] = "test-key"

from fastapi.testclient import TestClient
from main import app, init_db

client = TestClient(app)


def setup_function():
    # Windows cannot delete an SQLite file while a previous connection is
    # still being released. Reset rows through the API instead of unlinking.
    init_db()
    response = client.post("/api/reset")
    assert response.status_code == 200


def test_health_and_dashboard():
    assert client.get("/health").status_code == 200
    data = client.get("/api/dashboard").json()
    assert data["summary"]["total"] >= 4
    states = {x["action_state"] for x in data["items"]}
    assert "WAITING_EXTERNAL" in states
    assert "NEEDS_CONFIRMATION" in states
    assert "ESCALATE" in states


def test_demo_full_loop_and_rerank():
    r1 = client.post("/api/demo/apply-ft01")
    assert r1.status_code == 200
    assert r1.json()["status"] == "COMMITTED"
    board = client.get("/api/dashboard").json()
    task = next(x for x in board["items"] if x["task_id"] == "TASK-PO1001-CONFIRM")
    assert task["action_state"] in {"DO_NOW", "DO_TODAY"}

    contact = client.post(
        "/api/tasks/TASK-PO1001-CONFIRM/contacted",
        json={"waiting_on": "factory", "promised_reply_at": "2030-07-26T15:00:00+08:00"},
    )
    assert contact.status_code == 200
    board = client.get("/api/dashboard").json()
    task = next(x for x in board["items"] if x["task_id"] == "TASK-PO1001-CONFIRM")
    assert task["action_state"] == "WAITING_EXTERNAL"
    assert task["ranking_suppressed"] is True

    r2 = client.post("/api/demo/apply-ft02")
    assert r2.status_code == 200
    order = client.get("/api/orders/ORD-1001").json()["order"]
    assert order["current_progress"] == 0.7
    assert order["latest_supplier_commitment"] == "2026-07-29"


def test_writeback_idempotency():
    payload = {
        "transaction_json": "{\"idempotency_key\":\"TEST|ONE\",\"change_set\":[]}",
        "existing_business_state_json": "{\"order_id\":\"ORD-1001\"}",
    }
    first = client.post("/api/writeback", headers={"X-API-Key": "test-key"}, json=payload)
    second = client.post("/api/writeback", headers={"X-API-Key": "test-key"}, json=payload)
    assert first.status_code == 200
    assert first.json()["status"] == "COMMITTED"
    assert second.json()["status"] == "DUPLICATE_SKIPPED"


def test_writeback_rejects_bad_key():
    r = client.post("/api/writeback", headers={"X-API-Key": "wrong"}, json={})
    assert r.status_code == 401
