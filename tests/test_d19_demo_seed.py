from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from d19_demo_seed import SEED_VERSION, seed_d19_demo
from database import db, reset_engines

CN_TZ = timezone(timedelta(hours=8))


@pytest.fixture
def demo_client(tmp_path, monkeypatch) -> TestClient:
    db_path = tmp_path / "d19_seed_test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    reset_engines()
    from main import app, init_db

    init_db()
    yield TestClient(app)
    reset_engines()


def test_d19_demo_seed_is_idempotent_and_reset_is_scoped(demo_client):
    fixed = datetime(2026, 8, 19, 22, 20, tzinfo=CN_TZ)

    # Non-demo record must survive reset.
    with db() as conn:
        conn.execute(
            """INSERT INTO orders(order_id,order_no,customer_name,status,owner,organization_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            ("ORD-REAL-KEEP", "SO-REAL-KEEP", "Real Keep", "ACTIVE", "USER-1", "ORG-A", fixed.isoformat(), fixed.isoformat()),
        )
        conn.commit()

    first = seed_d19_demo(now=fixed)
    assert first.order_count == 17
    assert first.open_task_count == 14
    assert first.pending_review_count == 4

    second = seed_d19_demo(now=fixed)
    assert second.order_count == 17
    assert sum(second.inserted.values()) == 0

    reset = seed_d19_demo(reset=True, now=fixed)
    assert reset.order_count == 17
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM orders WHERE initialization_source=?", (SEED_VERSION,)).fetchone()[0] == 17
        assert conn.execute("SELECT COUNT(*) FROM orders WHERE order_id='ORD-REAL-KEEP'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM candidate_reviews WHERE workflow_source=?", (SEED_VERSION,)).fetchone()[0] == 4
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE task_id LIKE 'TASK-D19-DEMO-H4-%' AND status='DONE'"
        ).fetchone()[0] == 8


def test_seeded_orders_are_visible_to_operator_api(demo_client):
    fixed = datetime(2026, 8, 19, 22, 20, tzinfo=CN_TZ)
    seed_d19_demo(reset=True, now=fixed)
    headers = {"X-Auth-Token": "tok-user-1"}

    orders = demo_client.get("/api/orders", headers=headers)
    assert orders.status_code == 200
    items = orders.json()["items"]
    demo = [x for x in items if str(x.get("order_id", "")).startswith("ORD-D19-DEMO-")]
    assert len(demo) == 17
    assert any(x["order_no"] == "SO-1048" and x["max_risk"] == "high" for x in demo)
    assert any(x["order_no"] == "SO-1120" and x["current_node"] == "出货" for x in demo)

    dashboard = demo_client.get("/api/dashboard", headers=headers)
    assert dashboard.status_code == 200
    data = dashboard.json()
    open_demo = [x for x in data["items"] if str(x.get("task_id", "")).startswith("TASK-D19-DEMO-") and x.get("action_state") != "DONE"]
    assert len(open_demo) == 14
    assert any(x.get("action_state") == "WAITING_EXTERNAL" for x in open_demo)
    assert any(x.get("action_state") == "NEEDS_CONFIRMATION" for x in open_demo)

    reviews = demo_client.get("/api/reviews?status=ALL", headers=headers)
    assert reviews.status_code == 200
    demo_reviews = [x for x in reviews.json()["items"] if str(x.get("review_id", "")).startswith("REV-D19-DEMO-")]
    assert len(demo_reviews) == 4

    summary = demo_client.get("/api/d19/review-summary", headers=headers)
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["today_completed"] >= 8
    assert len(payload["daily_handled"]) == 5
