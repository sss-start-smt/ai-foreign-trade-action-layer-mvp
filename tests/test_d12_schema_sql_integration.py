"""D12 release regression: bundled schema.sql must support the real D12 chain.

This closes the R4 independent-acceptance P2 where the Alembic path had
orders.organization_id but the SQLite schema.sql bootstrap path did not.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import d10_business_action as d10
import d12_human_review as d12
from auth import resolve_identity_for_testing
from database import _LegacySQLiteWrapper

NOW = "2026-08-16T23:30:00+08:00"
BASE_DIR = Path(__file__).resolve().parents[1]


def test_real_schema_sql_supports_d12_manager_approval_chain(tmp_path):
    db_path = tmp_path / "d12-real-schema.db"
    raw = sqlite3.connect(db_path)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    raw.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))
    conn = _LegacySQLiteWrapper(raw)

    try:
        order_columns = {row["name"] for row in conn.execute("PRAGMA table_info(orders)").fetchall()}
        assert "organization_id" in order_columns

        order_indexes = {row["name"] for row in conn.execute("PRAGMA index_list(orders)").fetchall()}
        assert "idx_orders_org" in order_indexes

        conn.execute(
            """INSERT INTO orders
               (order_id,order_no,customer_name,requested_delivery_date,latest_supplier_commitment,
                status,owner,organization_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("ORD-SCHEMA", "SO-SCHEMA", "Schema ACME", "2026-08-20", "2026-08-18",
             "ACTIVE", "OPERATOR-A1", "ORG-A", NOW, NOW),
        )
        conn.execute(
            """INSERT INTO action_cases
               (action_case_id,organization_id,order_id,action_intent_key,intent_type,stage,
                lifecycle_status,observation_status,first_seen_at,last_seen_at,version,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("AC-SCHEMA", "ORG-A", "ORD-SCHEMA", "v1:DELIVERY_RECOVERY", "DELIVERY_RECOVERY",
             "IN_PROGRESS", "ACTIVE", "OBSERVED", NOW, NOW, 1, NOW, NOW),
        )
        conn.execute(
            """INSERT INTO d9_action_case_tasks
               (task_id,organization_id,action_case_id,title,recommended_action,status,version,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            ("TK-SCHEMA", "ORG-A", "AC-SCHEMA", "处理客户交期变化", "申请修改客户交期",
             "IN_PROGRESS", 1, NOW, NOW),
        )
        conn.commit()

        submission = d10.BusinessActionSubmission(
            organization_id="ORG-A",
            task_id="TK-SCHEMA",
            action_type="UPDATE_EXPECTED_DELIVERY_DATE",
            target_type="ERP_SALES_ORDER",
            target_id="SO-SCHEMA",
            payload={"expected_delivery_date": "2026-08-23"},
            idempotency_key="D12-SCHEMA-IDEM-1",
            actor="OPERATOR-A1",
            request_id="REQ-D12-SCHEMA-1",
            source="D12_ACTION_WORKSPACE",
            reason="真实 schema.sql 引导路径回归",
        )

        requested = d12.request_review(
            conn,
            d12.ReviewRequest(submission=submission),
            identity=resolve_identity_for_testing("OPERATOR-A1"),
        )
        assert requested["required_review"] == d12.REQUIREMENT_MANAGER
        assert requested["status"] == d12.STATUS_PENDING

        approved = d12.decide_review(
            conn,
            review_id=requested["review_id"],
            identity=resolve_identity_for_testing("MANAGER-A"),
            decision="APPROVE",
        )
        assert approved["status"] == d12.STATUS_APPROVED

        result = d12.submit_after_review(
            conn,
            review_id=requested["review_id"],
            identity=resolve_identity_for_testing("MANAGER-A"),
        )
        assert result["status"] == d10.ACTION_STATUS_ACCEPTED
        assert result["external_effect_executed"] is False

        outbox = conn.execute(
            "SELECT status FROM d10_outbox_events WHERE event_id=?",
            (result["outbox_event_id"],),
        ).fetchone()
        assert outbox["status"] == "PENDING"

        order = conn.execute(
            "SELECT requested_delivery_date FROM orders WHERE order_id='ORD-SCHEMA'"
        ).fetchone()
        assert order["requested_delivery_date"] == "2026-08-20"
    finally:
        raw.close()
