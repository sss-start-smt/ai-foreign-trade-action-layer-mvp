from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _create_legacy_orders_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                order_no TEXT UNIQUE NOT NULL,
                customer_name TEXT,
                product_name TEXT,
                packaging_method TEXT,
                requested_delivery_date TEXT,
                latest_supplier_commitment TEXT,
                current_progress REAL,
                current_node TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                owner TEXT,
                action_readiness TEXT NOT NULL DEFAULT 'BASE_ONLY',
                contact_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                issue_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                initialization_waiting_on TEXT,
                initialization_promised_reply_at TEXT,
                initialization_note TEXT,
                initialization_source TEXT,
                initialized_at TEXT,
                last_dynamic_update_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """INSERT INTO orders(
                order_id, order_no, customer_name, product_name, requested_delivery_date,
                status, owner, action_readiness, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                "ORD-LEGACY-001",
                "PO-LEGACY-001",
                "Legacy Customer",
                "Legacy Product",
                "2026-08-30",
                "ACTIVE",
                "USER-1",
                "READY_FOR_RANKING",
                "2026-08-03T00:00:00+00:00",
                "2026-08-03T00:00:00+00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_init_db_repairs_legacy_sqlite_before_schema_indexes(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.db"
    _create_legacy_orders_db(db_path)

    env = os.environ.copy()
    env.pop("DATABASE_URL", None)
    env.pop("TEST_DATABASE_URL", None)
    env["DB_PATH"] = str(db_path)
    env["SEED_DEMO_DATA"] = "false"
    env["SEED_D19_DEMO_DATA"] = "false"
    env["PYTHONPATH"] = str(ROOT)

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import main; "
                "main.init_db(); "
                "print(main.storage_status()['backend'])"
            ),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "sqlite" in proc.stdout

    conn = sqlite3.connect(db_path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(orders)")}
        assert "organization_id" in cols
        assert conn.execute(
            "SELECT organization_id FROM orders WHERE order_id='ORD-LEGACY-001'"
        ).fetchone()[0] == "ORG-A"
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(orders)")}
        assert "idx_orders_org" in indexes
        assert "idx_orders_action_readiness" in indexes
    finally:
        conn.close()
