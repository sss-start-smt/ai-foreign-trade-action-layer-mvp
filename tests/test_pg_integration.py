"""
FlowOrder PostgreSQL Integration Tests
======================================

These tests verify that:
1. Alembic upgrade head creates all runtime tables
2. Core CRUD operations work in PostgreSQL
3. Transaction rollback works correctly
4. Business entry points use the same unified engine
5. The 6 missing runtime tables exist after migration

These tests require a running PostgreSQL instance.
If TEST_DATABASE_URL is not set to PostgreSQL, tests are skipped.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
import pytest
from sqlalchemy import text

from database import (
    db,
    get_engine,
    is_postgres_mode,
    table_exists,
    insert_or_ignore,
    insert_or_replace,
    get_table_columns,
    get_table_column_names,
    begin_transaction,
    reset_engines,
)


PG_SKIP_REASON = "TEST_DATABASE_URL is not set to PostgreSQL"


@pytest.fixture(autouse=True)
def _check_pg():
    reset_engines()
    yield
    reset_engines()


def _pg_available() -> bool:
    test_url = os.getenv("TEST_DATABASE_URL", "")
    return test_url.startswith("postgresql://") or test_url.startswith("postgres://")


@pytest.fixture(scope="module")
def pg_migrated():
    """Apply Alembic head to the configured disposable PostgreSQL test DB."""
    if not _pg_available():
        pytest.skip(PG_SKIP_REASON)
    pytest.importorskip("psycopg2")
    test_url = os.environ["TEST_DATABASE_URL"]
    project_root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        ["python", "-m", "alembic", "upgrade", "head"],
        cwd=str(project_root),
        env={**os.environ, "DATABASE_URL": test_url},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return test_url


@pytest.fixture
def pg_conn(pg_migrated):
    """Get a PG connection after Alembic has been applied."""
    with db(use_test=True) as conn:
        yield conn


NOW = "2026-01-01T00:00:00+08:00"

EXPECTED_RUNTIME_TABLES = [
    "orders",
    "tasks",
    "communication_events",
    "communication_workflow_runs",
    "communication_drafts",
    "communication_task_candidates",
    "order_import_rows",
    "order_import_batches",
    "agent_runs",
    "agent_tool_calls",
    "agent_chat_jobs",
    "anomaly_candidates",
    "approval_requests",
    "bulk_update_batches",
    "bulk_update_candidates",
    "candidate_reviews",
    "commitment_history",
    "confirmation_snapshots",
    "daily_inspection_reports",
    "event_logs",
    "idempotency_records",
    "intake_jobs",
    "logistics_events",
    "order_dependencies",
    "risk_signals",
    "source_messages",
    "task_rankings",
    "user_settings",
    "workflow_runs",
    "analytics_events",
]


class TestAlembicSchema:
    """Verify alembic upgrade head creates all expected tables."""

    def test_alembic_head_applied(self, pg_conn):
        row = pg_conn.execute(text("SELECT version_num FROM alembic_version")).fetchone()
        assert row is not None
        assert row[0] == "e5a1b7c2d8f4"

    def test_all_runtime_tables_exist(self, pg_conn):
        for table in EXPECTED_RUNTIME_TABLES:
            assert table_exists(pg_conn, table), f"Table '{table}' missing after alembic upgrade head"

    def test_orders_table_has_correct_columns(self, pg_conn):
        columns = get_table_column_names(pg_conn, "orders")
        assert "order_id" in columns
        assert "order_no" in columns
        assert "customer_name" in columns
        assert "status" in columns

    def test_tasks_table_has_correct_columns(self, pg_conn):
        columns = get_table_column_names(pg_conn, "tasks")
        assert "task_id" in columns
        assert "related_order_id" in columns
        assert "title" in columns

    def test_communication_events_table_exists(self, pg_conn):
        assert table_exists(pg_conn, "communication_events")
        columns = get_table_column_names(pg_conn, "communication_events")
        assert "event_id" in columns
        assert "entity_id" in columns
        assert "entity_type" in columns

    def test_order_import_batches_table_exists(self, pg_conn):
        assert table_exists(pg_conn, "order_import_batches")
        columns = get_table_column_names(pg_conn, "order_import_batches")
        assert "batch_id" in columns
        assert "source_filename" in columns

    def test_order_import_rows_table_exists(self, pg_conn):
        assert table_exists(pg_conn, "order_import_rows")
        columns = get_table_column_names(pg_conn, "order_import_rows")
        assert "row_id" in columns
        assert "batch_id" in columns


class TestCoreCRUD:
    """Verify core CRUD operations in PostgreSQL."""

    def _insert_order(self, pg_conn, order_id, order_no, customer_name, status):
        pg_conn.execute(
            text("""
                INSERT INTO orders (order_id, order_no, customer_name, status, created_at, updated_at)
                VALUES (:oid, :ono, :cn, :st, :ca, :ua)
            """),
            {"oid": order_id, "ono": order_no, "cn": customer_name, "st": status, "ca": NOW, "ua": NOW},
        )

    def test_insert_and_select_order(self, pg_conn):
        self._insert_order(pg_conn, "ORD-PG-001", "PO-PG-001", "PG Customer", "ACTIVE")
        pg_conn.commit()

        result = pg_conn.execute(
            text("SELECT order_no, customer_name FROM orders WHERE order_id = :oid"),
            {"oid": "ORD-PG-001"},
        )
        row = result.fetchone()
        assert row is not None
        assert row[0] == "PO-PG-001"
        assert row[1] == "PG Customer"

    def test_update_order(self, pg_conn):
        self._insert_order(pg_conn, "ORD-PG-002", "PO-PG-002", "PG Customer 2", "PENDING")
        pg_conn.commit()

        pg_conn.execute(
            text("UPDATE orders SET status = :status, updated_at = :ua WHERE order_id = :oid"),
            {"status": "ACTIVE", "ua": NOW, "oid": "ORD-PG-002"},
        )
        pg_conn.commit()

        result = pg_conn.execute(
            text("SELECT status FROM orders WHERE order_id = :oid"),
            {"oid": "ORD-PG-002"},
        )
        row = result.fetchone()
        assert row[0] == "ACTIVE"

    def test_delete_order(self, pg_conn):
        self._insert_order(pg_conn, "ORD-PG-003", "PO-PG-003", "PG Customer 3", "ACTIVE")
        pg_conn.commit()

        pg_conn.execute(
            text("DELETE FROM orders WHERE order_id = :oid"),
            {"oid": "ORD-PG-003"},
        )
        pg_conn.commit()

        result = pg_conn.execute(
            text("SELECT COUNT(*) FROM orders WHERE order_id = :oid"),
            {"oid": "ORD-PG-003"},
        )
        assert result.fetchone()[0] == 0

    def test_insert_or_ignore_agent_run(self, pg_conn):
        insert_or_ignore(
            pg_conn, "agent_runs",
            ["run_id", "current_user_id", "current_role", "goal", "status", "created_at"],
            ("RUN-PG-001", "USER-1", "MANAGER", "test goal", "RUNNING", NOW),
            conflict_key="run_id",
        )
        pg_conn.commit()

        insert_or_ignore(
            pg_conn, "agent_runs",
            ["run_id", "current_user_id", "current_role", "goal", "status", "created_at"],
            ("RUN-PG-001", "USER-2", "WORKER", "overwritten", "COMPLETED", NOW),
            conflict_key="run_id",
        )
        pg_conn.commit()

        result = pg_conn.execute(
            text("SELECT status FROM agent_runs WHERE run_id = :rid"),
            {"rid": "RUN-PG-001"},
        )
        row = result.fetchone()
        assert row[0] == "RUNNING"

    def test_insert_or_replace_order(self, pg_conn):
        insert_or_replace(
            pg_conn, "orders",
            ["order_id", "order_no", "customer_name", "status", "created_at", "updated_at"],
            ("ORD-PG-004", "PO-PG-004", "Original", "PENDING", NOW, NOW),
            conflict_key="order_id",
        )
        pg_conn.commit()

        insert_or_replace(
            pg_conn, "orders",
            ["order_id", "order_no", "customer_name", "status", "created_at", "updated_at"],
            ("ORD-PG-004", "PO-PG-004", "Replaced", "ACTIVE", NOW, NOW),
            conflict_key="order_id",
        )
        pg_conn.commit()

        result = pg_conn.execute(
            text("SELECT customer_name, status FROM orders WHERE order_id = :oid"),
            {"oid": "ORD-PG-004"},
        )
        row = result.fetchone()
        assert row[0] == "Replaced"
        assert row[1] == "ACTIVE"

    def test_task_crud(self, pg_conn):
        self._insert_order(pg_conn, "ORD-PG-005", "PO-PG-005", "Task Test Order", "ACTIVE")
        pg_conn.commit()

        pg_conn.execute(
            text("""
                INSERT INTO tasks (task_id, related_order_id, title, status, created_at, updated_at, evidence_json)
                VALUES (:tid, :roid, :title, :st, :ca, :ua, :ej)
            """),
            {"tid": "TASK-PG-001", "roid": "ORD-PG-005", "title": "PG Task", "st": "OPEN", "ca": NOW, "ua": NOW, "ej": "[]"},
        )
        pg_conn.commit()

        result = pg_conn.execute(
            text("SELECT title FROM tasks WHERE task_id = :tid"),
            {"tid": "TASK-PG-001"},
        )
        assert result.fetchone()[0] == "PG Task"

    def test_communication_event_crud(self, pg_conn):
        pg_conn.execute(
            text("""
                INSERT INTO communication_events (event_id, entity_type, entity_id, event_type, payload_json, created_at)
                VALUES (:eid, :et, :eid_val, :evt, :pj, :ca)
            """),
            {"eid": "EVT-PG-001", "et": "ORDER", "eid_val": "ORD-PG-005", "evt": "SENT", "pj": "{}", "ca": NOW},
        )
        pg_conn.commit()

        result = pg_conn.execute(
            text("SELECT event_type FROM communication_events WHERE event_id = :eid"),
            {"eid": "EVT-PG-001"},
        )
        assert result.fetchone()[0] == "SENT"

    def test_import_batch_crud(self, pg_conn):
        pg_conn.execute(
            text("""
                INSERT INTO order_import_batches
                (batch_id, source_filename, source_sha256, status, total_rows, importable_rows, error_rows,
                 mapping_json, summary_json, created_at)
                VALUES (:bid, :sfn, :ss, :st, :tr, :ir, :er, :mj, :sj, :ca)
            """),
            {"bid": "BATCH-PG-001", "sfn": "test.xlsx", "ss": "abc123", "st": "COMPLETED",
             "tr": 10, "ir": 8, "er": 0, "mj": "{}", "sj": "{}", "ca": NOW},
        )
        pg_conn.commit()

        result = pg_conn.execute(
            text("SELECT status FROM order_import_batches WHERE batch_id = :bid"),
            {"bid": "BATCH-PG-001"},
        )
        assert result.fetchone()[0] == "COMPLETED"


class TestTransactionRollback:
    """Verify transaction rollback in PostgreSQL."""

    def test_rollback_discards_insert(self, pg_conn):
        try:
            pg_conn.execute(
                text("""
                    INSERT INTO orders (order_id, order_no, customer_name, status, created_at, updated_at)
                    VALUES (:oid, :ono, :cn, :st, :ca, :ua)
                """),
                {"oid": "ORD-PG-ROLLBACK", "ono": "PO-PG-RB", "cn": "RB Test", "st": "ACTIVE", "ca": NOW, "ua": NOW},
            )
            pg_conn.rollback()
        except Exception:
            pg_conn.rollback()
            raise

        result = pg_conn.execute(
            text("SELECT COUNT(*) FROM orders WHERE order_id = :oid"),
            {"oid": "ORD-PG-ROLLBACK"},
        )
        assert result.fetchone()[0] == 0

    def test_partial_rollback(self, pg_conn):
        pg_conn.execute(
            text("""
                INSERT INTO orders (order_id, order_no, customer_name, status, created_at, updated_at)
                VALUES (:oid, :ono, :cn, :st, :ca, :ua)
            """),
            {"oid": "ORD-PG-PARTIAL", "ono": "PO-PG-PART", "cn": "Partial Test", "st": "PENDING", "ca": NOW, "ua": NOW},
        )
        pg_conn.commit()

        try:
            pg_conn.execute(
                text("""
                    INSERT INTO tasks (task_id, related_order_id, title, status, created_at, updated_at, evidence_json)
                    VALUES (:tid, :roid, :title, :st, :ca, :ua, :ej)
                """),
                {"tid": "TASK-PG-PART", "roid": "ORD-PG-PARTIAL", "title": "Partial Task", "st": "OPEN", "ca": NOW, "ua": NOW, "ej": "[]"},
            )
            pg_conn.execute(
                text("INSERT INTO nonexistent_table VALUES (1)"),
            )
            pg_conn.commit()
        except Exception:
            pg_conn.rollback()

        result = pg_conn.execute(
            text("SELECT COUNT(*) FROM tasks WHERE task_id = :tid"),
            {"tid": "TASK-PG-PART"},
        )
        assert result.fetchone()[0] == 0

        result = pg_conn.execute(
            text("SELECT COUNT(*) FROM orders WHERE order_id = :oid"),
            {"oid": "ORD-PG-PARTIAL"},
        )
        assert result.fetchone()[0] == 1


class TestUnifiedEngine:
    """Verify that all entry points use the same engine."""

    def test_db_context_manager_uses_same_engine(self):
        reset_engines()
        engine1 = get_engine(use_test=True)
        engine2 = get_engine(use_test=True)
        assert engine1 is engine2

    def test_prod_and_test_engines_are_separate(self):
        reset_engines()
        if not _pg_available():
            pytest.skip(PG_SKIP_REASON)
        prod_engine = get_engine(use_test=False)
        test_engine = get_engine(use_test=True)
        assert prod_engine is not test_engine

    def test_db_context_manager_commits(self):
        if not _pg_available():
            pytest.skip(PG_SKIP_REASON)
        with db(use_test=True) as conn:
            conn.execute(
                text("""
                    INSERT INTO orders (order_id, order_no, customer_name, status, created_at, updated_at)
                    VALUES (:oid, :ono, :cn, :st, :ca, :ua)
                """),
                {"oid": "ORD-PG-CTX", "ono": "PO-PG-CTX", "cn": "Context Test", "st": "ACTIVE", "ca": NOW, "ua": NOW},
            )
            conn.commit()

        with db(use_test=True) as conn2:
            result = conn2.execute(
                text("SELECT status FROM orders WHERE order_id = :oid"),
                {"oid": "ORD-PG-CTX"},
            )
            assert result.fetchone()[0] == "ACTIVE"


class TestPGSQLHelperFunctions:
    """Verify PG-compatible helper functions."""

    def test_table_exists(self, pg_conn):
        assert table_exists(pg_conn, "orders")
        assert not table_exists(pg_conn, "nonexistent_table")

    def test_get_table_columns(self, pg_conn):
        columns = get_table_columns(pg_conn, "orders")
        names = {c["name"] for c in columns}
        assert "order_id" in names
        assert "order_no" in names

    def test_get_table_column_names(self, pg_conn):
        names = get_table_column_names(pg_conn, "orders")
        assert isinstance(names, set)
        assert "order_id" in names

    def test_begin_transaction(self, pg_conn):
        begin_transaction(pg_conn)
        pg_conn.execute(
            text("""
                INSERT INTO orders (order_id, order_no, customer_name, status, created_at, updated_at)
                VALUES (:oid, :ono, :cn, :st, :ca, :ua)
            """),
            {"oid": "ORD-PG-TXN", "ono": "PO-PG-TXN", "cn": "Txn Test", "st": "PENDING", "ca": NOW, "ua": NOW},
        )
        pg_conn.commit()

        result = pg_conn.execute(
            text("SELECT COUNT(*) FROM orders WHERE order_id = :oid"),
            {"oid": "ORD-PG-TXN"},
        )
        assert result.fetchone()[0] == 1
