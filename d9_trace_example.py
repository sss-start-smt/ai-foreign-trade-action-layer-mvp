"""D9 Trace example — runnable demonstration of the Action Case -> Task ->
Waiting -> Reply/Expiry -> Task resumed trace chain.

Used to produce the concrete trace example in D9_TASK_WAITING_CONTRACT.md.
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.dirname(__file__))

from database import _ConnectionWrapper
import d9_task_waiting as d9

CN_TZ = timezone(timedelta(hours=8))


def _iso(h=0.0):
    return (datetime.now(CN_TZ) + timedelta(hours=h)).isoformat(timespec="seconds")


def _exec_script(conn, script: str) -> None:
    """Execute a multi-statement DDL script. SQLite's execute() accepts only
    one statement at a time, so we split on ';' (consistent with the test
    fixture)."""
    for stmt in script.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(text(stmt))


ORDERS_CASES_SQL = """
CREATE TABLE orders (order_id TEXT PRIMARY KEY, order_no TEXT UNIQUE NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE', owner TEXT, organization_id TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE action_cases (action_case_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
    order_id TEXT NOT NULL, action_intent_key TEXT NOT NULL, intent_type TEXT NOT NULL,
    stage TEXT NOT NULL, lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE',
    observation_status TEXT NOT NULL DEFAULT 'OBSERVED', first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""

D9_TABLES_SQL = """
CREATE TABLE d9_action_case_tasks (task_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL, title TEXT NOT NULL, recommended_action TEXT,
    status TEXT NOT NULL DEFAULT 'TODO', version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE d9_action_case_waitings (waiting_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
    task_id TEXT NOT NULL, action_case_id TEXT NOT NULL, waiting_type TEXT NOT NULL, reason TEXT,
    due_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE', source_trace_id TEXT,
    reply_count INTEGER NOT NULL DEFAULT 0, latest_reply_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    resolved_at TEXT, expired_at TEXT, cancelled_at TEXT, cancel_reason TEXT);
CREATE TABLE d9_trace_events (trace_id TEXT PRIMARY KEY, organization_id TEXT NOT NULL,
    trace_kind TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}', actor TEXT, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX uq_d9_waitings_active
    ON d9_action_case_waitings(task_id) WHERE status = 'ACTIVE';
CREATE INDEX idx_d9_waitings_due_scan
    ON d9_action_case_waitings(organization_id, status, due_at);
"""


def main():
    tmp = tempfile.mkdtemp(prefix="d9_trace_demo_")
    engine = create_engine(f"sqlite:///{tmp}/demo.db")
    with engine.begin() as conn:
        _exec_script(conn, ORDERS_CASES_SQL)
        _exec_script(conn, D9_TABLES_SQL)
    conn = _ConnectionWrapper(engine.connect(), is_sqlite=True)

    # Seed a D8 Action Case (frozen contract untouched; only read here)
    conn.execute(text(
        "INSERT INTO orders VALUES ('ORD-100','PO-100','ACTIVE','U1','ORG-A',:n,:n)"), {"n": _iso()})
    conn.execute(text(
        "INSERT INTO action_cases VALUES ('AC-100','ORG-A','ORD-100','v1:LOGISTICS_RECOVERY',"
        "'LOGISTICS_RECOVERY','READY_FOR_ACTION','ACTIVE','OBSERVED',:n,:n,1,:n,:n)"), {"n": _iso()})

    # D9 flow
    task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-100",
                           title="联系供应商确认船期", recommended_action="电话+邮件")
    d9.start_task(conn, task["task_id"])
    waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"],
                                     waiting_type="SUPPLIER_REPLY", due_at=_iso(-1),
                                     reason="等待供应商确认发货船期")
    # external reply arrives but is partial -> stays ACTIVE
    d9.record_waiting_reply(conn, waiting_id=waiting["waiting_id"], reply_id="MSG-77",
                            reply_payload={"text": "在确认中"}, satisfies_completion=False)
    # Due Worker runs: waiting overdue -> EXPIRED, task resumed
    res = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
    print("=== Due Recovery result ===")
    print(res)

    print("\n=== Trace for TASK (answers: why is this back in my todo?) ===")
    for e in d9.get_trace_for_entity(conn, entity_type="task", entity_id=task["task_id"]):
        print(f"  [{e['event_type']}] {e['payload_json']}")

    print("\n=== Trace for WAITING ===")
    for e in d9.get_trace_for_entity(conn, entity_type="waiting", entity_id=waiting["waiting_id"]):
        print(f"  [{e['event_type']}] {e['payload_json']}")

    print("\n=== Final state ===")
    print("  task.status      =", d9.get_task_by_id(conn, task["task_id"])["status"])
    print("  waiting.status   =", d9.get_waiting_by_id(conn, waiting["waiting_id"])["status"])
    print("  case.lifecycle   =", conn.execute(text(
        "SELECT lifecycle_status FROM action_cases WHERE action_case_id='AC-100'")).fetchone()[0])


if __name__ == "__main__":
    main()
