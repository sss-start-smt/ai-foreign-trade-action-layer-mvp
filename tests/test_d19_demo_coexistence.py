from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from database import db, reset_engines


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("DB_PATH", str(tmp_path / "coexist.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SEED_D19_DEMO_DATA", "true")
    reset_engines()
    from main import app, init_db
    init_db()
    yield TestClient(app)
    reset_engines()


def test_demo_seed_coexists_with_preexisting_orders(client):
    # Reproduces Railway: the DB is NOT empty, but it has zero D19-DEMO orders.
    with db() as conn:
        conn.execute(
            """INSERT INTO orders(order_id,order_no,customer_name,status,owner,organization_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            ("ORD-OLD-1", "PO-OLD-1", "Existing Import", "ACTIVE", "USER-1", "ORG-A", "2026-08-03T00:00:00+00:00", "2026-08-03T00:00:00+00:00"),
        )
        conn.commit()

    headers = {"X-Auth-Token": "tok-user-1"}
    orders_before = client.get("/api/orders", headers=headers).json()
    assert orders_before["total"] == 1

    status = client.get("/api/d19/demo/status", headers=headers)
    assert status.status_code == 200
    assert status.json()["enabled"] is True
    assert status.json()["order_count"] == 0
    assert status.json()["expected_order_count"] == 17

    ensured = client.post("/api/d19/demo/ensure", headers=headers, json={})
    assert ensured.status_code == 200
    assert ensured.json()["order_count"] == 17

    orders_after = client.get("/api/orders", headers=headers).json()
    assert orders_after["total"] == 18
    assert any(x["order_id"] == "ORD-OLD-1" for x in orders_after["items"])
    assert len([x for x in orders_after["items"] if x["order_id"].startswith("ORD-D19-DEMO-")]) == 17

    dashboard = client.get("/api/dashboard", headers=headers)
    assert dashboard.status_code == 200
    open_demo = [x for x in dashboard.json()["items"] if str(x.get("task_id", "")).startswith("TASK-D19-DEMO-") and x.get("action_state") != "DONE"]
    assert len(open_demo) == 14

    summary = client.get("/api/d19/review-summary", headers=headers)
    assert summary.status_code == 200
