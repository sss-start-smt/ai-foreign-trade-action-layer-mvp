"""
FlowOrder Unified Database Configuration
==========================================

Provides SQLAlchemy Core-based database engine and connection management.

Environment Variables:
    DATABASE_URL      - Production database connection string
                        PostgreSQL: postgresql://user:pass@host:port/dbname
                        SQLite (legacy): sqlite:///data/action_layer.db
    TEST_DATABASE_URL - Test database connection string
                        PostgreSQL: postgresql://user:pass@host:port/test_dbname
                        SQLite (legacy): sqlite:///data/test_action_layer.db

The module supports two modes:
1. PostgreSQL mode (when DATABASE_URL starts with 'postgresql://')
2. SQLite legacy mode (when DATABASE_URL starts with 'sqlite:///' or not set)

In both modes, the `db()` context manager provides a Connection-compatible object
that mirrors the sqlite3.Connection API (execute, executemany, fetchone, fetchall,
commit, rollback, close).

Usage:
    from database import db, get_engine

    with db() as conn:
        result = conn.execute(text("SELECT * FROM orders WHERE order_id=:id"), {"id": "ORD-1001"})
        row = result.fetchone()
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import create_engine, text, Engine, Connection
from sqlalchemy.pool import QueuePool
from sqlalchemy.sql.elements import TextClause

BASE_DIR = Path(__file__).resolve().parent


def _build_sqlite_url() -> str:
    db_path = os.getenv("DB_PATH", str(BASE_DIR / "data" / "action_layer.db"))
    return f"sqlite:///{Path(db_path).resolve().as_posix()}"


def _build_test_sqlite_url() -> str:
    test_db = os.getenv("TEST_DB_PATH", str(BASE_DIR / "data" / "test_action_layer.db"))
    return f"sqlite:///{Path(test_db).resolve().as_posix()}"


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        return url
    return _build_sqlite_url()


def get_test_database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL", "").strip()
    if url:
        return url
    return _build_test_sqlite_url()


def is_postgres_mode(url: str | None = None) -> bool:
    target = url or get_database_url()
    return target.startswith("postgresql://") or target.startswith("postgres://")


def get_database_schema() -> str:
    """Return the PostgreSQL schema used by this application.

    CloudBase can host multiple portfolio projects in one PostgreSQL environment.
    DB_SCHEMA keeps FlowOrder isolated without changing every SQL statement.
    SQLite ignores this setting.
    """
    schema = os.getenv("DB_SCHEMA", "public").strip() or "public"
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("DB_SCHEMA must be a simple PostgreSQL identifier")
    return schema


def is_serverless_mode() -> bool:
    return os.getenv("FLOWORDER_SERVERLESS_MODE", "false").lower() == "true"


def _make_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            echo=False,
        )
    else:
        # Keep serverless pools deliberately small. Each warm function instance has
        # its own process-global SQLAlchemy pool, so large defaults multiply quickly.
        default_pool = 2 if is_serverless_mode() else 5
        default_overflow = 1 if is_serverless_mode() else 10
        pool_size = max(1, int(os.getenv("DB_POOL_SIZE", str(default_pool))))
        max_overflow = max(0, int(os.getenv("DB_MAX_OVERFLOW", str(default_overflow))))
        return create_engine(
            database_url,
            poolclass=QueuePool,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_pre_ping=True,
            pool_recycle=max(60, int(os.getenv("DB_POOL_RECYCLE_SECONDS", "300"))),
            echo=False,
        )


_engine: Engine | None = None
_test_engine: Engine | None = None


def get_engine(use_test: bool = False) -> Engine:
    global _engine, _test_engine
    if use_test:
        if _test_engine is None:
            _test_engine = _make_engine(get_test_database_url())
        return _test_engine
    if _engine is None:
        _engine = _make_engine(get_database_url())
    return _engine


def reset_engines() -> None:
    global _engine, _test_engine
    if _engine is not None:
        _engine.dispose()
    if _test_engine is not None:
        _test_engine.dispose()
    _engine = None
    _test_engine = None


class _RowWrapper:
    """Wraps SQLAlchemy Row to mimic sqlite3.Row interface."""

    def __init__(self, row: Any) -> None:
        self._row = row
        self._keys = list(row._fields) if hasattr(row, "_fields") else list(row.keys())

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._row[key]
        return getattr(self._row, key)

    def keys(self) -> list[str]:
        return self._keys

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __contains__(self, key: str) -> bool:
        return key in self._keys

    def __repr__(self) -> str:
        return f"Row({dict(self)})"

    def __dict__(self) -> dict:
        return {k: self[k] for k in self._keys}


# PG reserved word handling is done via _quote_pg_reserved_sql for
# specific known reserved words used as column names (e.g., current_role).
# This avoids blanket regex matching that could corrupt SQL keywords.


def _quote_pg_reserved_sql(sql: str) -> str:
    """Double-quote specific PG reserved words used as column identifiers.

    Only quotes the word 'current_role' which is a PG reserved word
    used as a column name. Does NOT quote other words that might appear
    as table names or SQL keywords.
    Skips already-quoted identifiers to avoid double-quoting.
    """
    result = sql
    # Only handle current_role - the confirmed PG reserved word used as column name.
    # Use negative look-around to skip already double-quoted identifiers like "current_role".
    # Pattern: match 'current_role' NOT preceded by '"' and NOT followed by '"'
    result = re.sub(
        r'(?<!")\bcurrent_role\b(?!")',
        '"current_role"',
        result,
        flags=re.IGNORECASE,
    )
    return result


def _pg_compat_sql(sql: str) -> str:
    """Apply PG compatibility transformations to raw SQL strings."""
    result = _quote_pg_reserved_sql(sql)
    result = re.sub(
        r"PRAGMA\s+foreign_keys\s*=\s*OFF\b",
        "SET session_replication_role = 'replica'",
        result,
        flags=re.IGNORECASE,
    )
    result = re.sub(
        r"PRAGMA\s+foreign_keys\s*=\s*ON\b",
        "SET session_replication_role = 'origin'",
        result,
        flags=re.IGNORECASE,
    )
    return result


class _ConnectionWrapper:
    """Wraps SQLAlchemy Connection to mimic sqlite3.Connection interface.

    Supports:
      - execute(sql, params=None) where sql can be:
          * SQLAlchemy TextClause (used as-is)
          * str with :param style placeholders (converted to TextClause)
          * str with ? placeholders (converted to :param style automatically)
      - executemany(sql, params_list)
      - fetchone(), fetchall() on result sets
      - commit(), rollback(), close()
      - row_factory attribute (ignored, rows always behave as dict-like)
    """

    def __init__(self, connection: Connection, is_sqlite: bool) -> None:
        self._conn = connection
        self._is_sqlite = is_sqlite
        self._row_factory: Any = None

    @property
    def is_pg(self) -> bool:
        return not self._is_sqlite

    @property
    def row_factory(self) -> Any:
        return self._row_factory

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        self._row_factory = value

    def _convert_params(self, params: Any) -> dict[str, Any] | list[Any] | None:
        if params is None:
            return None
        if isinstance(params, (list, tuple)):
            return {f"p{i}": v for i, v in enumerate(params)}
        if isinstance(params, dict):
            return params
        return params

    def _normalize_sql(self, sql: Any) -> TextClause:
        if isinstance(sql, TextClause):
            return sql

        if isinstance(sql, str):
            counter = [0]

            def _replace_placeholder(_: re.Match) -> str:
                counter[0] += 1
                return f":p{counter[0] - 1}"

            converted = re.sub(r"\?", _replace_placeholder, sql)
            return text(converted)

        if hasattr(sql, "compile"):
            return text(str(sql))

        return text(str(sql))

    def execute(self, sql: Any, params: Any = None) -> "_ResultWrapper":
        if isinstance(sql, TextClause) and params is not None:
            result = self._conn.execute(sql, params)
        elif isinstance(sql, TextClause):
            result = self._conn.execute(sql)
        elif isinstance(sql, str):
            if self.is_pg:
                sql = _pg_compat_sql(sql)
            normalized = self._normalize_sql(sql)
            converted_params = self._convert_params(params)
            if converted_params is not None:
                result = self._conn.execute(normalized, converted_params)
            else:
                result = self._conn.execute(normalized)
        else:
            result = self._conn.execute(sql, params)

        return _ResultWrapper(result)

    def executemany(self, sql: Any, params_list: list[Any]) -> "_ResultWrapper":
        if isinstance(sql, str) and self.is_pg:
            sql = _pg_compat_sql(sql)
        normalized = self._normalize_sql(sql)
        converted = [self._convert_params(p) for p in params_list]
        result = self._conn.execute(normalized, converted)
        return _ResultWrapper(result)

    def executescript(self, script: str) -> None:
        statements = re.split(r";\s*(?:\n|$)", script.strip())
        for stmt in statements:
            stmt = stmt.strip()
            if stmt:
                if self.is_pg:
                    stmt = _pg_compat_sql(stmt)
                self._conn.execute(text(stmt))

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "_ConnectionWrapper":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class _ResultWrapper:
    """Wraps SQLAlchemy Result to support sqlite3-style fetchone/fetchall."""

    def __init__(self, result: Any) -> None:
        self._result = result

    def fetchone(self) -> _RowWrapper | None:
        row = self._result.fetchone()
        if row is None:
            return None
        return _RowWrapper(row)

    def fetchall(self) -> list[_RowWrapper]:
        return [_RowWrapper(r) for r in self._result.fetchall()]

    def fetchmany(self, size: int | None = None) -> list[_RowWrapper]:
        if size is None:
            return self.fetchall()
        return [_RowWrapper(r) for r in self._result.fetchmany(size)]

    def __iter__(self) -> Iterator[_RowWrapper]:
        for row in self._result:
            yield _RowWrapper(row)

    @property
    def rowcount(self) -> int:
        return self._result.rowcount if hasattr(self._result, "rowcount") else -1

    @property
    def lastrowid(self) -> Any:
        return None

    def keys(self) -> list[str]:
        if hasattr(self._result, "keys"):
            return list(self._result.keys())
        return []


_legacy_sqlite_dbs: dict[str, sqlite3.Connection] = {}


@contextmanager
def db(use_test: bool = False) -> Iterator[_ConnectionWrapper]:
    """Unified database context manager.

    In PostgreSQL mode: creates a real SQLAlchemy connection.
    In SQLite legacy mode: wraps the existing sqlite3 connection for backward
    compatibility during the migration transition period.

    This allows gradual migration: existing code continues to work unchanged
    while new code can use SQLAlchemy-native patterns.
    """
    # Resolve the effective URL through the same helpers used by get_engine().
    # An empty TEST_DATABASE_URL must fall back to TEST_DB_PATH/SQLite,
    # not inherit the production DATABASE_URL backend.
    db_url = get_test_database_url() if use_test else get_database_url()
    pg_mode = is_postgres_mode(db_url)

    if pg_mode:
        engine = get_engine(use_test=use_test)
        with engine.connect() as raw_conn:
            schema = get_database_schema()
            if schema != "public":
                # Identifier is validated by get_database_schema(); SET search_path
                # lets existing unqualified SQL remain unchanged.
                raw_conn.execute(text(f'SET search_path TO "{schema}", public'))
            wrapper = _ConnectionWrapper(raw_conn, is_sqlite=False)
            try:
                yield wrapper
            except Exception:
                raw_conn.rollback()
                raise
    else:
        db_path_str = os.getenv("DB_PATH", str(BASE_DIR / "data" / "action_layer.db"))
        if use_test:
            db_path_str = os.getenv("TEST_DB_PATH", str(BASE_DIR / "data" / "test_action_layer.db"))

        db_path = Path(db_path_str)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")

        wrapper = _LegacySQLiteWrapper(conn)
        try:
            yield wrapper
        finally:
            conn.close()


class _LegacySQLiteWrapper:
    """Thin compatibility wrapper around raw sqlite3.Connection.

    Used only during migration transition when DATABASE_URL is SQLite.
    Provides the same interface as _ConnectionWrapper but delegates to
    the actual sqlite3 connection.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def is_pg(self) -> bool:
        return False

    @property
    def row_factory(self) -> Any:
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, value: Any) -> None:
        self._conn.row_factory = value

    def execute(self, sql: Any, params: Any = None) -> "_LegacyResultWrapper":
        if isinstance(sql, TextClause):
            if params is not None:
                compiled = sql.compile(compile_kwargs={"literal_binds": False})
                result = self._conn.execute(str(compiled), params)
            else:
                compiled = sql.compile(compile_kwargs={"literal_binds": True})
                result = self._conn.execute(str(compiled))
            return _LegacyResultWrapper(result)
        if isinstance(sql, str) and params is not None:
            result = self._conn.execute(sql, params)
        elif isinstance(sql, str):
            result = self._conn.execute(sql)
        else:
            result = self._conn.execute(sql, params if params else [])
        return _LegacyResultWrapper(result)

    def executemany(self, sql: Any, params_list: list[Any]) -> "_LegacyResultWrapper":
        if isinstance(sql, TextClause):
            compiled = sql.compile(compile_kwargs={"literal_binds": False})
            result = self._conn.executemany(str(compiled), params_list)
        else:
            result = self._conn.executemany(sql, params_list)
        return _LegacyResultWrapper(result)

    def executescript(self, script: str) -> None:
        self._conn.executescript(script)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "_LegacySQLiteWrapper":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class _LegacyResultWrapper:
    """Wraps sqlite3.Cursor with a compatible interface."""

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    def fetchone(self) -> sqlite3.Row | None:
        return self._cursor.fetchone()

    def fetchall(self) -> list[sqlite3.Row]:
        return self._cursor.fetchall()

    def fetchmany(self, size: int | None = None) -> list[sqlite3.Row]:
        if size is None:
            return self._cursor.fetchall()
        return self._cursor.fetchmany(size)

    def __iter__(self) -> Iterator[sqlite3.Row]:
        return iter(self._cursor)

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> Any:
        return self._cursor.lastrowid


def create_tables_from_schema(engine: Engine, schema_sql_path: str | None = None) -> None:
    """Execute schema SQL file against the engine (PostgreSQL or SQLite).

    This is the Alembic-free initial setup path. For new deployments, prefer
    `alembic upgrade head` instead.
    """
    if schema_sql_path is None:
        schema_sql_path = str(BASE_DIR / "schema.sql")

    schema = Path(schema_sql_path).read_text(encoding="utf-8")
    engine = engine or get_engine()
    pg_mode = is_postgres_mode(str(engine.url))

    if pg_mode:
        with engine.connect() as conn:
            statements = [s.strip() for s in re.split(r";\s*(?:\n|$)", schema) if s.strip()]
            for stmt in statements:
                if "PRAGMA" in stmt.upper():
                    continue
                conn.execute(text(stmt))
            conn.commit()
    else:
        db_path = os.getenv("DB_PATH", str(BASE_DIR / "data" / "action_layer.db"))
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(schema)
        conn.commit()
        conn.close()


def get_connection_string_display() -> str:
    """Return a safe display version of the current connection string (no passwords)."""
    url = get_database_url()
    if "@" in url:
        prefix, suffix = url.rsplit("@", 1)
        if ":" in prefix:
            protocol_user, password = prefix.rsplit(":", 1)
            password_chars = len(password)
            return f"{protocol_user}:{'*' * password_chars}@{suffix}"
    return url


def verify_connection(use_test: bool = False) -> dict[str, Any]:
    """Verify database connectivity and return status info."""
    url = get_test_database_url() if use_test else get_database_url()
    pg = is_postgres_mode(url)
    try:
        engine = get_engine(use_test=use_test)
        with engine.connect() as conn:
            if pg:
                result = conn.execute(text("SELECT 1")).fetchone()
                version = conn.execute(text("SELECT version()")).fetchone()
            else:
                result = conn.execute(text("SELECT 1")).fetchone()
                version = ("SQLite " + sqlite3.sqlite_version,)
        return {
            "connected": True,
            "backend": "postgresql" if pg else "sqlite",
            "url": get_connection_string_display(),
            "version": str(version[0]) if version else "unknown",
            "result": str(result[0]) if result else None,
        }
    except Exception as exc:
        return {
            "connected": False,
            "backend": "postgresql" if pg else "sqlite",
            "url": get_connection_string_display(),
            "error": str(exc),
        }


def table_exists(conn: Any, table_name: str) -> bool:
    """Check if a table exists in the current database (PG/SQLite compatible)."""
    if getattr(conn, 'is_pg', False):
        result = conn.execute(
            text("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables
                    WHERE table_schema = current_schema() AND table_name = :name
                )
            """),
            {"name": table_name},
        )
        return result.fetchone()[0]
    else:
        result = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table_name},
        )
        return result.fetchone() is not None


def _quote_ident_pg(ident: str) -> str:
    """Double-quote an identifier for PostgreSQL reserved word safety."""
    return f'"{ident}"'


def insert_or_ignore(conn: Any, table: str, columns: list[str], values: tuple[Any, ...],
                      conflict_key: str = "id") -> None:
    """INSERT OR IGNORE, PG-compatible (uses ON CONFLICT DO NOTHING)."""
    is_pg = getattr(conn, 'is_pg', False)
    if is_pg:
        quoted_cols = [_quote_ident_pg(c) for c in columns]
        col_list = ", ".join(quoted_cols)
        quoted_ckey = _quote_ident_pg(conflict_key)
    else:
        col_list = ", ".join(columns)
        quoted_ckey = conflict_key
    placeholders = ", ".join(["?"] * len(columns))
    if is_pg:
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT ({quoted_ckey}) DO NOTHING"
    else:
        sql = f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})"
    conn.execute(sql, values)


def insert_or_replace(conn: Any, table: str, columns: list[str], values: tuple[Any, ...],
                      conflict_key: str = "id") -> None:
    """INSERT OR REPLACE, PG-compatible (uses ON CONFLICT DO UPDATE)."""
    is_pg = getattr(conn, 'is_pg', False)
    if is_pg:
        quoted_cols = [_quote_ident_pg(c) for c in columns]
        col_list = ", ".join(quoted_cols)
        quoted_ckey = _quote_ident_pg(conflict_key)
        update_set = ", ".join(
            f'{_quote_ident_pg(c)} = EXCLUDED.{_quote_ident_pg(c)}'
            for c in columns if c != conflict_key
        )
    else:
        col_list = ", ".join(columns)
        quoted_ckey = conflict_key
        update_set = ", ".join([f"{c} = EXCLUDED.{c}" for c in columns if c != conflict_key])
    placeholders = ", ".join(["?"] * len(columns))
    if is_pg:
        sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) ON CONFLICT ({quoted_ckey}) DO UPDATE SET {update_set}"
    else:
        sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
    conn.execute(sql, values)


def get_table_columns(conn: Any, table: str) -> list[dict[str, Any]]:
    """Get column metadata for a table (PG/SQLite compatible).

    Returns list of dicts with keys: name, type, notnull, dflt_value, pk.
    """
    if getattr(conn, 'is_pg', False):
        result = conn.execute(
            text("""
                SELECT
                    column_name AS name,
                    data_type AS type,
                    is_nullable = 'NO' AS notnull,
                    column_default AS dflt_value,
                    is_identity = 'YES' AS pk
                FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = :table
                ORDER BY ordinal_position
            """),
            {"table": table},
        )
        return [dict(row) for row in result.fetchall()]
    else:
        if not table_exists(conn, table):
            return []
        rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        return [{"name": row["name"], "type": row["type"], "notnull": row["notnull"],
                 "dflt_value": row["dflt_value"], "pk": row["pk"]} for row in rows]


def begin_transaction(conn: Any) -> None:
    """Begin an explicit transaction (PG/SQLite compatible).

    Replaces SQLite-specific BEGIN IMMEDIATE / BEGIN EXCLUSIVE.
    For PostgreSQL, explicit BEGIN is optional.
    """
    if getattr(conn, 'is_pg', False):
        pass
    else:
        conn.execute("BEGIN")


def get_table_column_names(conn: Any, table: str) -> set[str]:
    """Get set of column names for a table (PG/SQLite compatible)."""
    return {col["name"] for col in get_table_columns(conn, table)}
