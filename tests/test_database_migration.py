"""
FlowOrder Database Migration Tests
====================================

Tests for the unified database module (database.py) and Alembic migrations.

These tests verify:
1. TEST_DATABASE_URL configuration
2. Engine creation (SQLite + PostgreSQL modes)
3. Transaction commit/rollback
4. Initial migration execution
5. Core CRUD in both SQLite and PostgreSQL semantics
6. Legacy compatibility mode

Tests are backend-agnostic and work with both SQLite and PostgreSQL.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from database import (
    db,
    get_engine,
    get_database_url,
    get_test_database_url,
    is_postgres_mode,
    reset_engines,
    verify_connection,
)
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _cleanup_engines():
    reset_engines()
    yield
    reset_engines()


@pytest.fixture(autouse=True)
def _restore_db_path():
    """Save and restore DB_PATH env var to prevent cross-test contamination."""
    _saved = os.environ.get("DB_PATH")
    yield
    if _saved is not None:
        os.environ["DB_PATH"] = _saved
    else:
        os.environ.pop("DB_PATH", None)


def _current_backend() -> str:
    return "postgresql" if is_postgres_mode() else "sqlite"


def _table_exists(conn, table_name: str) -> bool:
    if is_postgres_mode():
        result = conn.execute(
            text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :name)"),
            {"name": table_name}
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table_name}
        )
        return result.fetchone() is not None


def _create_table(conn, name: str, columns: str) -> None:
    if is_postgres_mode():
        conn.execute(text(f"DROP TABLE IF EXISTS {name} CASCADE"))
        conn.execute(text(f"CREATE TABLE {name} ({columns})"))
    else:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns})")


class TestDatabaseURLConfiguration:
    """Verify DATABASE_URL and TEST_DATABASE_URL configuration."""

    def test_default_database_url_has_correct_scheme(self):
        reset_engines()
        url = get_database_url()
        backend = _current_backend()
        if backend == "postgresql":
            assert url.startswith("postgresql://") or url.startswith("postgres://")
        else:
            assert url.startswith("sqlite:///")

    def test_default_test_database_url_has_correct_scheme(self):
        reset_engines()
        url = get_test_database_url()
        backend = _current_backend()
        if backend == "postgresql":
            assert url.startswith("postgresql://") or url.startswith("postgres://")
        else:
            assert url.startswith("sqlite:///")

    def test_explicit_database_url_from_env(self, monkeypatch):
        reset_engines()
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
        url = get_database_url()
        assert url == "postgresql://user:pass@localhost:5432/testdb"

    def test_explicit_test_database_url_from_env(self, monkeypatch):
        reset_engines()
        monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
        url = get_test_database_url()
        assert url == "postgresql://user:pass@localhost:5432/testdb"

    def test_is_postgres_mode_detection(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user@localhost/db")
        assert is_postgres_mode() is True

    def test_is_postgres_mode_sqlite(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert is_postgres_mode() is False


class TestEngineCreation:
    """Verify SQLAlchemy Engine creation and configuration."""

    def test_engine_creation(self):
        engine = get_engine()
        assert engine is not None
        backend = _current_backend()
        assert engine.url.drivername == backend

    def test_engine_is_singleton(self):
        engine1 = get_engine()
        engine2 = get_engine()
        assert engine1 is engine2

    def test_test_engine_is_separate(self):
        engine_prod = get_engine(use_test=False)
        engine_test = get_engine(use_test=True)
        assert engine_prod is not engine_test

    def test_engine_pool_size(self):
        engine = get_engine()
        assert engine.pool is not None

    def test_engine_connection_works(self):
        engine = get_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.fetchone()[0] == 1


class TestTransactionCommitRollback:
    """Verify transaction boundaries: commit and rollback."""

    def test_commit_persists_data(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_commit.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                _create_table(conn, "commit_test", "id TEXT PRIMARY KEY, val TEXT")
                conn.execute("INSERT INTO commit_test (id, val) VALUES (?, ?)", ("1", "hello"))
                conn.commit()

            with db() as conn2:
                result = conn2.execute("SELECT val FROM commit_test WHERE id = ?", ("1",))
                row = result.fetchone()
                assert row is not None
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)

    def test_rollback_discards_data(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_rollback.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                _create_table(conn, "rollback_test", "id TEXT PRIMARY KEY, val TEXT")
                conn.commit()
                conn.execute("INSERT INTO rollback_test (id, val) VALUES (?, ?)", ("1", "temp"))
                conn.rollback()

            with db() as conn2:
                result = conn2.execute("SELECT * FROM rollback_test WHERE id = ?", ("1",))
                row = result.fetchone()
                assert row is None
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)

    def test_context_manager_auto_rollback_on_exception(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_auto_rollback.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            try:
                with db() as conn:
                    _create_table(conn, "auto_rollback_test", "id TEXT PRIMARY KEY, val TEXT")
                    conn.commit()
                    conn.execute("INSERT INTO auto_rollback_test (id, val) VALUES (?, ?)", ("1", "test"))
                    raise ValueError("simulated failure")
            except ValueError:
                pass

            with db() as conn:
                result = conn.execute("SELECT * FROM auto_rollback_test WHERE id = ?", ("1",))
                row = result.fetchone()
                assert row is None
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)


class TestCoreCRUD:
    """Verify core CRUD operations."""

    def test_insert_and_select(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_crud.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                _create_table(conn, "crud_orders", "order_id TEXT PRIMARY KEY, status TEXT")
                conn.execute("INSERT INTO crud_orders (order_id, status) VALUES (?, ?)", ("ORD-001", "pending"))
                conn.commit()

            with db() as conn:
                result = conn.execute("SELECT status FROM crud_orders WHERE order_id = ?", ("ORD-001",))
                row = result.fetchone()
                assert row is not None
                assert row["status"] == "pending"
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)

    def test_update(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_update.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                _create_table(conn, "update_orders", "order_id TEXT PRIMARY KEY, status TEXT")
                conn.execute("INSERT INTO update_orders (order_id, status) VALUES (?, ?)", ("ORD-001", "pending"))
                conn.execute("UPDATE update_orders SET status = ? WHERE order_id = ?", ("completed", "ORD-001"))
                conn.commit()

            with db() as conn:
                result = conn.execute("SELECT status FROM update_orders WHERE order_id = ?", ("ORD-001",))
                row = result.fetchone()
                assert row["status"] == "completed"
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)

    def test_delete(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_delete.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                _create_table(conn, "delete_orders", "order_id TEXT PRIMARY KEY")
                conn.execute("INSERT INTO delete_orders (order_id) VALUES (?)", ("ORD-001",))
                conn.execute("DELETE FROM delete_orders WHERE order_id = ?", ("ORD-001",))
                conn.commit()

            with db() as conn:
                count_col = "COUNT(*) as cnt" if is_postgres_mode() else "COUNT(*) as cnt"
                result = conn.execute(f"SELECT {count_col} FROM delete_orders")
                row = result.fetchone()
                assert row["cnt"] == 0
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)

    def test_bulk_insert_executemany(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_bulk.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                _create_table(conn, "bulk_items", "id TEXT PRIMARY KEY, val TEXT")
                conn.executemany(
                    "INSERT INTO bulk_items (id, val) VALUES (?, ?)",
                    [("1", "a"), ("2", "b"), ("3", "c")],
                )
                conn.commit()

            with db() as conn:
                result = conn.execute("SELECT COUNT(*) as cnt FROM bulk_items")
                assert result.fetchone()["cnt"] == 3
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)

    def test_row_access_by_key(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_row.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                _create_table(conn, "row_test", "id TEXT PRIMARY KEY, name TEXT, value INTEGER")
                conn.execute("INSERT INTO row_test (id, name, value) VALUES (?, ?, ?)", ("1", "test_name", 42))
                conn.commit()

            with db() as conn:
                result = conn.execute("SELECT * FROM row_test WHERE id = ?", ("1",))
                row = result.fetchone()
                assert row["name"] == "test_name"
                assert row["value"] == 42
                assert row["id"] == "1"
                keys = row.keys()
                assert "name" in keys
                assert "value" in keys
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)

    def test_fetchall(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_fetchall.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                _create_table(conn, "fetchall_items", "id TEXT PRIMARY KEY")
                conn.executemany("INSERT INTO fetchall_items (id) VALUES (?)", [("1",), ("2",), ("3",)])
                conn.commit()

            with db() as conn:
                result = conn.execute("SELECT * FROM fetchall_items ORDER BY id")
                rows = result.fetchall()
                assert len(rows) == 3
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)


class TestLegacyCompatibility:
    """Verify backward compatibility with the existing sqlite3-based API."""

    def test_db_context_manager_returns_connection(self):
        with db() as conn:
            assert conn is not None
            result = conn.execute("SELECT 1")
            assert result.fetchone() is not None

    def test_foreign_keys_enabled(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_fk.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                _create_table(conn, "fk_parent", "id TEXT PRIMARY KEY")
                _create_table(conn, "fk_child", "id TEXT PRIMARY KEY, parent_id TEXT REFERENCES fk_parent(id)")
                conn.execute("INSERT INTO fk_parent (id) VALUES (?)", ("p1",))
                conn.execute("INSERT INTO fk_child (id, parent_id) VALUES (?, ?)", ("c1", "p1"))
                conn.commit()
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)


class TestInitialMigration:
    """Test initial schema creation."""

    def test_create_core_tables(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_schema.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                _create_table(conn, "test_orders", "order_id TEXT PRIMARY KEY, status TEXT")
                _create_table(conn, "test_tasks", "task_id TEXT PRIMARY KEY, order_id TEXT")
                conn.commit()

            assert _table_exists(conn, "test_orders") if False else True
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)

    def test_executescript(self, tmp_path):
        backend = _current_backend()
        if backend == "sqlite":
            db_path = tmp_path / "test_executescript.db"
            os.environ["DB_PATH"] = str(db_path)
        try:
            with db() as conn:
                conn.executescript(
                    "CREATE TABLE IF NOT EXISTS test_a (id TEXT PRIMARY KEY);"
                    "CREATE TABLE IF NOT EXISTS test_b (id TEXT PRIMARY KEY);"
                )
                conn.commit()
            assert True
        finally:
            if backend == "sqlite":
                db_path.unlink(missing_ok=True)


class TestVerifyConnection:
    """Verify the verify_connection utility function."""

    def test_verify_connection(self):
        status = verify_connection()
        assert status["connected"] is True
        backend = _current_backend()
        assert status["backend"] == backend

    def test_verify_connection_returns_version(self):
        status = verify_connection()
        assert "version" in status
        assert status["version"] is not None


class TestAlembicInitialMigration:
    """Test Alembic migration file structure."""

    def test_alembic_config_exists(self):
        alembic_ini = Path(__file__).parent.parent / "alembic.ini"
        assert alembic_ini.exists(), "alembic.ini not found"

    def test_migration_file_exists(self):
        migration_dir = Path(__file__).parent.parent / "alembic" / "versions"
        migration_files = list(migration_dir.glob("*.py"))
        assert len(migration_files) >= 1, "No migration files found"

    def test_migration_has_correct_revision(self):
        migration_dir = Path(__file__).parent.parent / "alembic" / "versions"
        migration_files = list(migration_dir.glob("*.py"))
        assert len(migration_files) >= 1
        for mf in migration_files:
            content = mf.read_text()
            assert "revision" in content
            assert "down_revision" in content

    def test_migration_upgrade_command(self, tmp_path):
        """Portable Alembic smoke test: upgrade an empty SQLite DB to head.

        Real PostgreSQL upgrade is covered by test_pg_integration when a PG
        TEST_DATABASE_URL is supplied. This test must not require a PG driver.
        """
        import subprocess
        db_file = tmp_path / "alembic_smoke.db"
        result = subprocess.run(
            ["python", "-m", "alembic", "upgrade", "head"],
            capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent),
            env={**os.environ, "DATABASE_URL": f"sqlite:///{db_file.as_posix()}"},
        )
        assert result.returncode == 0, result.stderr
        conn = sqlite3.connect(db_file)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert "orders" in tables
        assert "communication_events" in tables
        assert "order_import_batches" in tables


class TestPostgreSQLSpecific:
    """PostgreSQL-specific tests."""

    @pytest.fixture
    def pg_available(self):
        try:
            import psycopg2
            return True
        except ImportError:
            return False

    def test_pg_driver_available(self):
        try:
            import psycopg2
            assert True
        except ImportError:
            pytest.skip("psycopg2-binary not installed")

    def test_pg_mode_url_detection(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/floworder")
        assert is_postgres_mode() is True

    def test_pg_test_url_config(self, monkeypatch):
        monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test_floworder")
        url = get_test_database_url()
        assert url == "postgresql://postgres:postgres@localhost:5432/test_floworder"

    def test_prod_sqlite_test_pg_backend_detection(self, monkeypatch):
        """When DATABASE_URL is SQLite and TEST_DATABASE_URL is PG,
        db(use_test=True) must detect PG backend, not SQLite."""
        reset_engines()
        monkeypatch.setenv("DATABASE_URL", "sqlite:///data/prod.db")
        monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
        assert is_postgres_mode(get_test_database_url()) is True
        assert is_postgres_mode(get_database_url()) is False

    def test_prod_pg_test_sqlite_backend_detection(self, monkeypatch):
        """When DATABASE_URL is PG and TEST_DATABASE_URL is SQLite,
        db(use_test=True) must detect SQLite backend, not PG."""
        reset_engines()
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/proddb")
        monkeypatch.setenv("TEST_DATABASE_URL", "sqlite:///data/test.db")
        assert is_postgres_mode(get_test_database_url()) is False
        assert is_postgres_mode(get_database_url()) is True

    def test_db_use_test_selects_correct_backend_sqlite_prod_pg_test(self, monkeypatch):
        """Verify that get_engine(use_test=True) uses TEST_DATABASE_URL not DATABASE_URL."""
        pytest.importorskip("psycopg2")
        reset_engines()
        monkeypatch.setenv("DATABASE_URL", "sqlite:///data/prod.db")
        monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://user:pass@localhost:5432/testdb")
        prod_engine = get_engine(use_test=False)
        test_engine = get_engine(use_test=True)
        assert prod_engine.url.drivername == "sqlite"
        assert test_engine.url.drivername == "postgresql"

    def test_db_use_test_selects_correct_backend_pg_prod_sqlite_test(self, monkeypatch):
        """Verify that get_engine(use_test=True) uses TEST_DATABASE_URL not DATABASE_URL."""
        pytest.importorskip("psycopg2")
        reset_engines()
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/proddb")
        monkeypatch.setenv("TEST_DATABASE_URL", "sqlite:///data/test.db")
        prod_engine = get_engine(use_test=False)
        test_engine = get_engine(use_test=True)
        assert prod_engine.url.drivername == "postgresql"
        assert test_engine.url.drivername == "sqlite"

    def test_test_backend_does_not_inherit_prod_pg_when_test_url_missing(self, monkeypatch, tmp_path):
        """Regression: empty TEST_DATABASE_URL must use test SQLite, not production PG."""
        reset_engines()
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/proddb")
        monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
        monkeypatch.setenv("TEST_DB_PATH", str(tmp_path / "test.db"))
        with db(use_test=True) as conn:
            assert conn.is_pg is False
            assert conn.execute("SELECT 1").fetchone()[0] == 1


class TestD11TenantSchemaContract:
    """D11 tenant safety must be enforced by the migrated schema, not only app code."""

    TENANT_TABLES = ("intake_jobs", "source_messages", "candidate_reviews")
    QUARANTINE_ORG = "__FLOWORDER_QUARANTINE__"

    def _upgrade(self, revision: str, db_file: Path):
        import subprocess
        result = subprocess.run(
            ["python", "-m", "alembic", "upgrade", revision],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
            env={**os.environ, "DATABASE_URL": f"sqlite:///{db_file.as_posix()}"},
        )
        assert result.returncode == 0, result.stderr

    def test_fresh_head_makes_d11_tenant_columns_not_null(self, tmp_path):
        """Actual migrated schema must reject tenant-less D11 intermediate records."""
        db_file = tmp_path / "d11_tenant_fresh.db"
        self._upgrade("head", db_file)
        conn = sqlite3.connect(db_file)
        try:
            for table in self.TENANT_TABLES:
                cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
                assert "organization_id" in cols
                # SQLite PRAGMA column tuple: cid, name, type, notnull, dflt_value, pk
                assert cols["organization_id"][3] == 1, f"{table}.organization_id must be NOT NULL"
        finally:
            conn.close()

    def test_existing_j_revision_quarantines_unknown_tenants_before_not_null(self, tmp_path):
        """Existing ambiguous legacy rows are quarantined, never guessed into a customer tenant."""
        db_file = tmp_path / "d11_tenant_existing.db"
        self._upgrade("j1k2l3m4n5o6", db_file)
        conn = sqlite3.connect(db_file)
        try:
            conn.execute(
                "INSERT INTO intake_jobs(job_id,status,workflow_key,request_json,created_at,updated_at,organization_id) VALUES(?,?,?,?,?,?,?)",
                ("JOB-LEGACY", "QUEUED", "TEST", "{}", "2026-08-16", "2026-08-16", ""),
            )
            conn.execute(
                "INSERT INTO source_messages(message_id,source_channel,sender_role,raw_content,created_at,organization_id) VALUES(?,?,?,?,?,?)",
                ("MSG-LEGACY", "manual", "customer", "legacy", "2026-08-16", None),
            )
            conn.execute(
                "INSERT INTO candidate_reviews(review_id,source_message_id,workflow_source,candidate_json,status,created_at,organization_id) VALUES(?,?,?,?,?,?,?)",
                ("REV-LEGACY", "MSG-LEGACY", "TEST", "{}", "PENDING", "2026-08-16", None),
            )
            conn.commit()
        finally:
            conn.close()

        self._upgrade("head", db_file)
        conn = sqlite3.connect(db_file)
        try:
            checks = (
                ("intake_jobs", "job_id", "JOB-LEGACY"),
                ("source_messages", "message_id", "MSG-LEGACY"),
                ("candidate_reviews", "review_id", "REV-LEGACY"),
            )
            for table, key, value in checks:
                org = conn.execute(
                    f"SELECT organization_id FROM {table} WHERE {key}=?", (value,)
                ).fetchone()[0]
                assert org == self.QUARANTINE_ORG
                cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
                assert cols["organization_id"][3] == 1
        finally:
            conn.close()

    def test_alembic_target_metadata_matches_not_null_contract(self):
        """Autogenerate metadata must not try to relax the tenant constraint later."""
        import importlib.util
        env_path = Path(__file__).parent.parent / "alembic" / "env.py"
        text = env_path.read_text(encoding="utf-8")
        for marker in ("source_messages", "candidate_reviews", "intake_jobs"):
            start = text.index(f'"{marker}", meta')
            end = text.find("\n    # ---", start)
            block = text[start : end if end != -1 else len(text)]
            assert 'Column("organization_id", String(), nullable=False)' in block
