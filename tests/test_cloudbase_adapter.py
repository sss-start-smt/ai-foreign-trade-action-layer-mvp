from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_cloudbase_bootstrap_uses_supported_fastapi_http_port():
    text = (ROOT / "scf_bootstrap").read_text(encoding="utf-8")
    assert "/var/lang/python311/bin/python3.11" in text
    assert "main:app" in text
    assert "--host 0.0.0.0" in text
    assert "--port 9000" in text


def test_cloudbase_baseline_covers_runtime_required_tables(monkeypatch, tmp_path):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from main import PG_REQUIRED_TABLES

    sql = (ROOT / "cloudbase" / "migrations" / "20260820000100_floworder_baseline.sql").read_text(encoding="utf-8")
    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+([A-Za-z_][A-Za-z0-9_]*)", sql, flags=re.I))
    assert not (set(PG_REQUIRED_TABLES) - tables)
    assert "PRAGMA" not in sql.upper()
    assert "CREATE SCHEMA IF NOT EXISTS floworder" in sql


def test_database_schema_is_validated(monkeypatch):
    import database

    monkeypatch.setenv("DB_SCHEMA", "floworder")
    assert database.get_database_schema() == "floworder"

    monkeypatch.setenv("DB_SCHEMA", "floworder;drop schema public")
    with pytest.raises(ValueError):
        database.get_database_schema()


def test_serverless_mode_removes_after_response_worker_dependency():
    main_text = (ROOT / "main.py").read_text(encoding="utf-8")
    agent_text = (ROOT / "agent_api.py").read_text(encoding="utf-8")
    assert "if FLOWORDER_SERVERLESS_MODE:" in main_text
    assert "process_intake_job(job_id)" in main_text
    assert "_initialize_database_worker()" in main_text
    assert "if FLOWORDER_SERVERLESS_MODE:" in agent_text
    assert "_execute_agent_chat_job(**worker_kwargs)" in agent_text
