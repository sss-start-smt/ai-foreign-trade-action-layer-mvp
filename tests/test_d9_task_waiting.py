"""
D9-P0 Tests: Action Case → Task → Waiting — Minimum Execution Closed Loop
=========================================================================

Covers the full D9-P0 required attack surface:
  - Normal Task → Waiting → Reply(resolve) → Resume
  - Partial Reply must NOT end the Waiting
  - Duplicate Reply (idempotent)
  - Waiting expiry via Due Recovery (expire + resume Task)
  - Due Worker repeated execution (idempotent)
  - Scan after service restart (idempotent)
  - Waiting cancel + cancel-idempotent + excluded from due scan
  - One Case, multiple Tasks (waiting one does not affect the other)
  - One Case, multiple Waitings (A expiring must not affect B)
  - Closed Case + Active Waiting handled safely (no reopen / no ghost)
  - Task completion must NOT auto-close the Action Case
  - Waiting Resolve must NOT auto-close the Action Case
  - Trace chain answers "why is this task back in my todo?"
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import d9_task_waiting as d9
from d8_action_case import _conn_exec, _now_iso
from database import _ConnectionWrapper

CN_TZ = timezone(timedelta(hours=8))
NOW = "2026-08-13T09:00:00+08:00"


# ─── Schema ────────────────────────────────────────────────────────────

ORDERS_SQL = """
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    order_no TEXT UNIQUE NOT NULL,
    customer_name TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    owner TEXT,
    organization_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

ACTION_CASES_SQL = """
CREATE TABLE action_cases (
    action_case_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    action_intent_key TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE',
    observation_status TEXT NOT NULL DEFAULT 'OBSERVED',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_reconciled_at TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    close_reason TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
)
"""

D9_TABLES_SQL = """
CREATE TABLE d9_action_case_tasks (
    task_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    title TEXT NOT NULL,
    recommended_action TEXT,
    status TEXT NOT NULL DEFAULT 'TODO',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE d9_action_case_waitings (
    waiting_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    waiting_type TEXT NOT NULL,
    reason TEXT,
    due_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    source_trace_id TEXT,
    reply_count INTEGER NOT NULL DEFAULT 0,
    latest_reply_json TEXT NOT NULL DEFAULT '[]',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    expired_at TEXT,
    cancelled_at TEXT,
    cancel_reason TEXT
);
CREATE TABLE d9_trace_events (
    trace_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    trace_kind TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_d9_waitings_active
ON d9_action_case_waitings(task_id) WHERE status = 'ACTIVE';
CREATE INDEX idx_d9_waitings_due_scan
ON d9_action_case_waitings(organization_id, status, due_at);
"""


def _iso(offset_hours: float = 0.0) -> str:
    return (datetime.now(CN_TZ) + timedelta(hours=offset_hours)).isoformat(timespec="seconds")


# ─── Fixtures ──────────────────────────────────────────────────────────

def _exec_script(conn, script: str) -> None:
    for stmt in script.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(text(stmt))


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test_d9.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        _exec_script(conn, ORDERS_SQL)
        _exec_script(conn, ACTION_CASES_SQL)
        _exec_script(conn, D9_TABLES_SQL)
    raw = engine.connect()
    wrapper = _ConnectionWrapper(raw, is_sqlite=True)
    return {"wrapper": wrapper, "engine": engine, "db_path": str(db_path)}


def _reconnect(db_path: str) -> _ConnectionWrapper:
    engine = create_engine(f"sqlite:///{db_path}")
    raw = engine.connect()
    return _ConnectionWrapper(raw, is_sqlite=True)


def _seed_case(
    conn,
    *,
    organization_id: str = "ORG-A",
    order_id: str = "ORD-1",
    action_case_id: str = "AC-1",
    lifecycle_status: str = "ACTIVE",
    stage: str = "READY_FOR_ACTION",
    owner: str = "USER-1",
):
    _conn_exec(
        conn,
        "INSERT OR IGNORE INTO orders (order_id, order_no, status, owner, organization_id, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (order_id, order_id, "ACTIVE", owner, organization_id, NOW, NOW),
    )
    _conn_exec(
        conn,
        "INSERT OR IGNORE INTO action_cases "
        "(action_case_id, organization_id, order_id, action_intent_key, intent_type, stage, "
        "lifecycle_status, observation_status, first_seen_at, last_seen_at, version, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            action_case_id, organization_id, order_id, "v1:TEST", "TEST_INTENT", stage,
            lifecycle_status, "OBSERVED", NOW, NOW, 1, NOW, NOW,
        ),
    )


def _close_case(conn, action_case_id: str) -> None:
    """Faithfully simulate a D8-closed Action Case by setting the closed state
    directly (test seeding only — D8 code is NOT modified). This represents the
    post-close state the D9 real-race tests need, without invoking D8 transition
    authorization."""
    _conn_exec(
        conn,
        "UPDATE action_cases SET lifecycle_status='CLOSED', close_reason='MANUAL', "
        "closed_at=?, version=version+1, updated_at=? WHERE action_case_id=?",
        (NOW, NOW, action_case_id),
    )


# ─── 1. Normal Task → Waiting → Reply(resolve) → Resume ─────────────────

class TestNormalResolveFlow:
    def test_task_waiting_reply_resume(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="联系供应商")
        assert task["status"] == "TODO"

        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(
            conn, task_id=task["task_id"], waiting_type="SUPPLIER_REPLY", due_at=_iso(24),
        )
        assert waiting["status"] == "ACTIVE"
        t_after = d9.get_task_by_id(conn, task["task_id"])
        assert t_after["status"] == "WAITING"

        resolved = d9.record_waiting_reply(
            conn, waiting_id=waiting["waiting_id"], reply_id="MSG-1",
            reply_payload={"text": "已发货"}, satisfies_completion=True,
        )
        assert resolved["status"] == "RESOLVED"
        assert resolved["resolved_at"] is not None

        t_final = d9.get_task_by_id(conn, task["task_id"])
        assert t_final["status"] == "IN_PROGRESS"  # resumed, NOT done

        # Action Case untouched
        case = _conn_exec(conn, "SELECT lifecycle_status, stage FROM action_cases WHERE action_case_id='AC-1'").fetchone()
        assert case["lifecycle_status"] == "ACTIVE"
        assert case["stage"] == "READY_FOR_ACTION"


# ─── 2. Partial Reply must NOT end Waiting ─────────────────────────────

class TestPartialReply:
    def test_partial_reply_keeps_waiting_active(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="等客户确认")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="CUSTOMER_CONFIRM", due_at=_iso(24))

        # Partial reply (does NOT satisfy completion)
        r = d9.record_waiting_reply(
            conn, waiting_id=waiting["waiting_id"], reply_id="MSG-P1",
            reply_payload={"text": "在看了"}, satisfies_completion=False,
        )
        assert r["status"] == "ACTIVE"
        t = d9.get_task_by_id(conn, task["task_id"])
        assert t["status"] == "WAITING"  # still waiting

        # A second partial reply also keeps it active
        r2 = d9.record_waiting_reply(
            conn, waiting_id=waiting["waiting_id"], reply_id="MSG-P2",
            reply_payload={"text": "稍等"}, satisfies_completion=False,
        )
        assert r2["status"] == "ACTIVE"

        # Now a full reply ends it
        r3 = d9.record_waiting_reply(
            conn, waiting_id=waiting["waiting_id"], reply_id="MSG-FULL",
            reply_payload={"text": "确认无误"}, satisfies_completion=True,
        )
        assert r3["status"] == "RESOLVED"
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "IN_PROGRESS"

        # Evidence recorded: 3 replies tracked
        w = d9.get_waiting_by_id(conn, waiting["waiting_id"])
        assert w["reply_count"] == 3


# ─── 3. Duplicate Reply (idempotent) ───────────────────────────────────

class TestDuplicateReply:
    def test_duplicate_reply_resolves_once(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))

        first = d9.record_waiting_reply(
            conn, waiting_id=waiting["waiting_id"], reply_id="MSG-DUP",
            reply_payload={"text": "ok"}, satisfies_completion=True,
        )
        assert first["status"] == "RESOLVED"

        # Same external reply consumed again → no-op
        second = d9.record_waiting_reply(
            conn, waiting_id=waiting["waiting_id"], reply_id="MSG-DUP",
            reply_payload={"text": "ok"}, satisfies_completion=True,
        )
        assert second["status"] == "RESOLVED"

        # Exactly one RESOLVED event, one REPLY_RECEIVED with that reply_id
        resolved_events = d9.get_trace_for_entity(conn, entity_type="waiting", entity_id=waiting["waiting_id"])
        resolved = [e for e in resolved_events if e["event_type"] == "WAITING_RESOLVED"]
        replies = [e for e in resolved_events if e["event_type"] == "REPLY_RECEIVED"]
        assert len(resolved) == 1
        assert len(replies) == 1

        # Task resumed exactly once (only the WAITING → IN_PROGRESS resume)
        task_events = d9.get_trace_for_entity(conn, entity_type="task", entity_id=task["task_id"])
        status_changes = [e for e in task_events if e["event_type"] == "TASK_STATUS_CHANGED"]
        resume_count = 0
        for e in status_changes:
            p = json.loads(e["payload_json"]) if isinstance(e["payload_json"], str) else e["payload_json"]
            if p.get("from") == "WAITING" and p.get("to") == "IN_PROGRESS":
                resume_count += 1
        assert resume_count == 1


# ─── 4. Waiting expiry via Due Recovery ────────────────────────────────

class TestDueRecoveryExpiry:
    def test_overdue_waiting_expires_and_resumes(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(-1))  # overdue

        result = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        assert result["scanned"] == 1
        assert result["expired"] == 1
        assert result["cancelled_orphan"] == 0

        w = d9.get_waiting_by_id(conn, waiting["waiting_id"])
        assert w["status"] == "EXPIRED"
        assert w["expired_at"] is not None
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "IN_PROGRESS"


# ─── 5 & 6. Due Worker repeated / after restart (idempotent) ───────────

class TestDueRecoveryIdempotency:
    def test_repeated_scan_is_noop(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(-1))

        r1 = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        assert r1["expired"] == 1
        r2 = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        assert r2["expired"] == 0  # waiting already EXPIRED → idempotent no-op

        # Exactly one WAITING_EXPIRED trace
        traces = d9.get_trace_for_entity(conn, entity_type="waiting", entity_id=waiting["waiting_id"])
        assert len([e for e in traces if e["event_type"] == "WAITING_EXPIRED"]) == 1

    def test_scan_after_restart_is_noop(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(-1))

        r1 = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        assert r1["expired"] == 1

        # Simulate service restart: new connection to the SAME db file
        conn2 = _reconnect(db["db_path"])
        r2 = d9.run_due_recovery(conn2, organization_id="ORG-A", current_time=_iso(0))
        assert r2["expired"] == 0  # persisted EXPIRED → idempotent no-op after restart

        # Already-EXPIRED then scanned again (same worker) → no-op
        r3 = d9.run_due_recovery(conn2, organization_id="ORG-A", current_time=_iso(0))
        assert r3["expired"] == 0

    def test_two_scans_hit_same_waiting_once(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(-1))
        # Run recovery, then run it again twice in a row
        for _ in range(3):
            res = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        assert res["expired"] == 0  # third run: nothing new to expire
        w = d9.get_waiting_by_id(conn, waiting["waiting_id"])
        assert w["status"] == "EXPIRED"


# ─── 7 & 8. Waiting cancel + excluded from due scan ────────────────────

class TestWaitingCancel:
    def test_cancel_and_excluded_from_due(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(-1))

        cancelled = d9.cancel_waiting(conn, waiting_id=waiting["waiting_id"], cancel_reason="MANUAL")
        assert cancelled["status"] == "CANCELLED"
        assert cancelled["cancelled_at"] is not None

        # Due scan must NOT recover a cancelled waiting
        res = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        assert res["expired"] == 0
        assert res["cancelled_orphan"] == 0
        # C: manual cancel under an ACTIVE Case resumes the Task to IN_PROGRESS
        # (the action is no longer blocked) and leaves no Active Waiting. It must
        # NOT stay a zombie at WAITING.
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "IN_PROGRESS"
        assert d9.get_active_waiting_for_task(conn, task["task_id"]) is None

    def test_duplicate_cancel_idempotent(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        c1 = d9.cancel_waiting(conn, waiting_id=waiting["waiting_id"], cancel_reason="MANUAL")
        c2 = d9.cancel_waiting(conn, waiting_id=waiting["waiting_id"], cancel_reason="MANUAL")
        assert c1["status"] == "CANCELLED"
        assert c2["status"] == "CANCELLED"
        traces = d9.get_trace_for_entity(conn, entity_type="waiting", entity_id=waiting["waiting_id"])
        assert len([e for e in traces if e["event_type"] == "WAITING_CANCELLED"]) == 1


# ─── 9. One Case, multiple Tasks (waiting one does not affect other) ────

class TestMultipleTasks:
    def test_waiting_one_task_keeps_other_runnable(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        t_a = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="Task A")
        t_b = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="Task B")
        d9.start_task(conn, t_a["task_id"])
        d9.start_task(conn, t_b["task_id"])

        w_a = d9.put_task_on_waiting(conn, task_id=t_a["task_id"], waiting_type="X", due_at=_iso(24))
        assert d9.get_task_by_id(conn, t_a["task_id"])["status"] == "WAITING"
        # Task B unaffected
        assert d9.get_task_by_id(conn, t_b["task_id"])["status"] == "IN_PROGRESS"

        # Action Case NOT frozen
        case = _conn_exec(conn, "SELECT lifecycle_status, stage FROM action_cases WHERE action_case_id='AC-1'").fetchone()
        assert case["lifecycle_status"] == "ACTIVE"
        assert case["stage"] == "READY_FOR_ACTION"

        # Resolve A → only A resumes
        d9.record_waiting_reply(conn, waiting_id=w_a["waiting_id"], reply_id="M", satisfies_completion=True)
        assert d9.get_task_by_id(conn, t_a["task_id"])["status"] == "IN_PROGRESS"
        assert d9.get_task_by_id(conn, t_b["task_id"])["status"] == "IN_PROGRESS"


# ─── 10. One Case, multiple Waitings (A expiring must not affect B) ─────

class TestMultipleWaitings:
    def test_both_overdue_both_expire(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        t_a = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="A")
        t_b = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="B")
        d9.start_task(conn, t_a["task_id"])
        d9.start_task(conn, t_b["task_id"])
        w_a = d9.put_task_on_waiting(conn, task_id=t_a["task_id"], waiting_type="X", due_at=_iso(-2))
        w_b = d9.put_task_on_waiting(conn, task_id=t_b["task_id"], waiting_type="Y", due_at=_iso(-1))

        res = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        assert res["expired"] == 2
        assert d9.get_waiting_by_id(conn, w_a["waiting_id"])["status"] == "EXPIRED"
        assert d9.get_waiting_by_id(conn, w_b["waiting_id"])["status"] == "EXPIRED"
        assert d9.get_task_by_id(conn, t_a["task_id"])["status"] == "IN_PROGRESS"
        assert d9.get_task_by_id(conn, t_b["task_id"])["status"] == "IN_PROGRESS"

    def test_a_expiry_does_not_affect_b(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        t_a = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="A")
        t_b = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="B")
        d9.start_task(conn, t_a["task_id"])
        d9.start_task(conn, t_b["task_id"])
        w_a = d9.put_task_on_waiting(conn, task_id=t_a["task_id"], waiting_type="X", due_at=_iso(-2))  # overdue
        w_b = d9.put_task_on_waiting(conn, task_id=t_b["task_id"], waiting_type="Y", due_at=_iso(48))  # future

        res = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        assert res["expired"] == 1
        assert res["scanned"] == 1  # only A is overdue

        # A expired + resumed; B still waiting
        assert d9.get_waiting_by_id(conn, w_a["waiting_id"])["status"] == "EXPIRED"
        assert d9.get_task_by_id(conn, t_a["task_id"])["status"] == "IN_PROGRESS"
        assert d9.get_waiting_by_id(conn, w_b["waiting_id"])["status"] == "ACTIVE"
        assert d9.get_task_by_id(conn, t_b["task_id"])["status"] == "WAITING"

        # Case not auto-changed
        case = _conn_exec(conn, "SELECT lifecycle_status, stage FROM action_cases WHERE action_case_id='AC-1'").fetchone()
        assert case["lifecycle_status"] == "ACTIVE"
        assert case["stage"] == "READY_FOR_ACTION"


# ─── 11. Closed Case + Active Waiting handled safely ───────────────────

# ---------------------------------------------------------------------------
# A. Terminal Task FSM enforcement (section A)
# ---------------------------------------------------------------------------


class TestTerminalTaskFsm:
    def test_done_task_rejects_put_on_waiting(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.complete_task(conn, task["task_id"])
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "DONE"
        with pytest.raises(d9.D9StateError):
            d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))

    def test_cancelled_task_rejects_put_on_waiting(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.cancel_task(conn, task["task_id"])
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "CANCELLED"
        with pytest.raises(d9.D9StateError):
            d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))

    def test_todo_task_rejects_put_on_waiting(self, db):
        # FSM: TODO must be started (→ IN_PROGRESS) before it may enter WAITING.
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        assert task["status"] == "TODO"
        with pytest.raises(d9.D9StateError):
            d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))


# ---------------------------------------------------------------------------
# B. Closed Case race — the REAL race: close AFTER the Waiting exists
#    (supersedes the obsolete TestClosedCaseRace, whose "seed CLOSED then
#     create Task" premise is now explicitly forbidden by B3/B4)
# ---------------------------------------------------------------------------


class TestClosedCaseRaceReal:
    def test_b1_due_after_close_no_resume(self, db):
        # ACTIVE Case → Task → Waiting(overdue) → Case CLOSED → Due Worker
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(-1))
        _close_case(conn, "AC-1")

        res = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        assert res["expired"] == 0
        assert res["cancelled_orphan"] == 1

        # Case still CLOSED — never reopened, never mutated
        case = _conn_exec(conn, "SELECT lifecycle_status FROM action_cases WHERE action_case_id='AC-1'").fetchone()
        assert case["lifecycle_status"] == "CLOSED"
        all_cases = _conn_exec(conn, "SELECT COUNT(*) AS n FROM action_cases WHERE organization_id='ORG-A'").fetchone()
        assert all_cases["n"] == 1  # no new Case created

        # Waiting no longer ACTIVE
        w = d9.get_waiting_by_id(conn, waiting["waiting_id"])
        assert w["status"] == "CANCELLED"
        assert w["cancel_reason"] == "PARENT_CASE_CLOSED"

        # Task NOT resumed to IN_PROGRESS (no ghost); safely closed instead
        t = d9.get_task_by_id(conn, task["task_id"])
        assert t["status"] == "CANCELLED"
        # No Active Waiting remains for the (closed) Task — no zombie either
        assert d9.get_active_waiting_for_task(conn, task["task_id"]) is None

    def test_b2_late_full_reply_after_close_no_resume(self, db):
        # ACTIVE Case → Task → Waiting → Case CLOSED → full Reply
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        _close_case(conn, "AC-1")

        r = d9.record_waiting_reply(
            conn, waiting_id=waiting["waiting_id"], reply_id="MSG-LATE",
            reply_payload={"text": "done"}, satisfies_completion=True,
        )
        assert r["status"] == "RESOLVED"  # reply recorded/resolved
        # Task must NEVER be resurrected into an executable state
        t = d9.get_task_by_id(conn, task["task_id"])
        assert t["status"] == "CANCELLED"
        assert d9.get_active_waiting_for_task(conn, task["task_id"]) is None
        # Case unchanged
        case = _conn_exec(conn, "SELECT lifecycle_status FROM action_cases WHERE action_case_id='AC-1'").fetchone()
        assert case["lifecycle_status"] == "CLOSED"

    def test_b3_create_task_on_closed_case_rejected(self, db):
        conn = db["wrapper"]
        _seed_case(conn, lifecycle_status="CLOSED", stage="CLOSED")
        with pytest.raises(d9.D9StateError):
            d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        rows = _conn_exec(conn, "SELECT COUNT(*) AS n FROM d9_action_case_tasks WHERE action_case_id='AC-1'").fetchone()
        assert rows["n"] == 0  # no Task created

    def test_b4_put_on_waiting_after_close_rejected(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        _close_case(conn, "AC-1")
        with pytest.raises(d9.D9StateError):
            d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        # No Waiting created; Task stays IN_PROGRESS (NOT WAITING)
        assert d9.get_active_waiting_for_task(conn, task["task_id"]) is None
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "IN_PROGRESS"


# ---------------------------------------------------------------------------
# C. Waiting Cancel semantics — no zombie WAITING Task (section C)
# ---------------------------------------------------------------------------


class TestCancelSemantics:
    def test_manual_cancel_resumes_task(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "WAITING"
        cancelled = d9.cancel_waiting(conn, waiting_id=waiting["waiting_id"], cancel_reason="MANUAL")
        assert cancelled["status"] == "CANCELLED"
        # Task resumed to IN_PROGRESS; no Active Waiting remains
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "IN_PROGRESS"
        assert d9.get_active_waiting_for_task(conn, task["task_id"]) is None

    def test_task_done_keeps_done_on_waiting_cancel(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        d9.complete_task(conn, task["task_id"])  # cancels active waiting (TASK_DONE)
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "DONE"
        w = d9.get_waiting_by_id(conn, waiting["waiting_id"])
        assert w["status"] == "CANCELLED"
        assert w["cancel_reason"] == "TASK_DONE"

    def test_task_cancelled_keeps_cancelled_on_waiting_cancel(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        d9.cancel_task(conn, task["task_id"])  # cancels active waiting (TASK_CANCELLED)
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "CANCELLED"
        w = d9.get_waiting_by_id(conn, waiting["waiting_id"])
        assert w["status"] == "CANCELLED"
        assert w["cancel_reason"] == "TASK_CANCELLED"

    def test_parent_case_closed_cancel_no_resume(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        _close_case(conn, "AC-1")
        cancelled = d9.cancel_waiting(conn, waiting_id=waiting["waiting_id"], cancel_reason="MANUAL")
        assert cancelled["status"] == "CANCELLED"
        # Task must NEVER resume IN_PROGRESS; safely closed instead
        t = d9.get_task_by_id(conn, task["task_id"])
        assert t["status"] == "CANCELLED"
        assert d9.get_active_waiting_for_task(conn, task["task_id"]) is None


# ---------------------------------------------------------------------------
# D. due_at timezone correctness (section D)
# ---------------------------------------------------------------------------


class TestDueAtTimezone:
    def test_mixed_offset_future_not_early_expired(self, db):
        # due_at = 02:30Z (Beijing 10:30) is actually 30 min in the FUTURE
        # relative to current_time = 10:00+08:00 (Beijing 10:00 = 02:00Z).
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        due = "2026-08-13T02:30:00+00:00"
        now = "2026-08-13T10:00:00+08:00"
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=due)
        assert waiting["status"] == "ACTIVE"
        res = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=now)
        assert res["expired"] == 0  # NOT yet expired — no early EXPIRE
        assert d9.get_waiting_by_id(conn, waiting["waiting_id"])["status"] == "ACTIVE"
        # Task stays WAITING (not wrongly resumed)
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "WAITING"

    def test_mixed_offset_past_must_expire(self, db):
        # due_at = 01:00Z (Beijing 09:00) is actually in the PAST relative to
        # current_time = 10:00+08:00 (Beijing 10:00 = 02:00Z).
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        due = "2026-08-13T01:00:00+00:00"
        now = "2026-08-13T10:00:00+08:00"
        d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=due)
        res = d9.run_due_recovery(conn, organization_id="ORG-A", current_time=now)
        assert res["expired"] == 1  # must correctly EXPIRE
        assert d9.get_waiting_by_id(conn, d9.list_waitings_for_case(conn, organization_id="ORG-A", action_case_id="AC-1")[0]["waiting_id"])["status"] == "EXPIRED"
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "IN_PROGRESS"

    def test_naive_due_at_rejected(self, db):
        # A timezone-less timestamp must be rejected, not guessed.
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        with pytest.raises(d9.D9StateError):
            d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at="2026-08-13T10:00:00")


# ---------------------------------------------------------------------------
# E. State invariant: no Task=WAITING without an Active Waiting (section E)
# ---------------------------------------------------------------------------


class TestNoZombieWaiting:
    def test_no_zombie_waiting_task_after_normal_ops(self, db):
        conn = db["wrapper"]
        # Flow 1: manual cancel under ACTIVE Case → Task IN_PROGRESS (no active waiting)
        _seed_case(conn, action_case_id="AC-1")
        t1 = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t1")
        d9.start_task(conn, t1["task_id"])
        w1 = d9.put_task_on_waiting(conn, task_id=t1["task_id"], waiting_type="X", due_at=_iso(24))
        d9.cancel_waiting(conn, waiting_id=w1["waiting_id"], cancel_reason="MANUAL")

        # Flow 2: closed-case due → Task CANCELLED (no active waiting)
        _seed_case(conn, action_case_id="AC-2")
        t2 = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-2", title="t2")
        d9.start_task(conn, t2["task_id"])
        w2 = d9.put_task_on_waiting(conn, task_id=t2["task_id"], waiting_type="X", due_at=_iso(-1))
        _close_case(conn, "AC-2")
        d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))

        # Flow 3: normal expiry under ACTIVE Case → Task IN_PROGRESS
        _seed_case(conn, action_case_id="AC-3")
        t3 = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-3", title="t3")
        d9.start_task(conn, t3["task_id"])
        w3 = d9.put_task_on_waiting(conn, task_id=t3["task_id"], waiting_type="X", due_at=_iso(-1))
        d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))

        # Flow 4: a Task legitimately WAITING WITH an Active Waiting must survive
        _seed_case(conn, action_case_id="AC-4")
        t4 = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-4", title="t4")
        d9.start_task(conn, t4["task_id"])
        d9.put_task_on_waiting(conn, task_id=t4["task_id"], waiting_type="X", due_at=_iso(24))

        # Invariant: every WAITING Task must have an Active Waiting.
        tasks = _conn_exec(conn, "SELECT task_id, status FROM d9_action_case_tasks").fetchall()
        for tk in tasks:
            if tk["status"] == "WAITING":
                assert d9.get_active_waiting_for_task(conn, tk["task_id"]) is not None, \
                    f"Zombie: Task {tk['task_id']} is WAITING with no Active Waiting"


# ─── 12 & 13. Task completion / Waiting resolve must NOT auto-close Case ─

class TestNoAutoCloseCase:
    def test_complete_task_does_not_close_case(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.complete_task(conn, task["task_id"])
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "DONE"
        case = _conn_exec(conn, "SELECT lifecycle_status FROM action_cases WHERE action_case_id='AC-1'").fetchone()
        assert case["lifecycle_status"] == "ACTIVE"

    def test_resolve_waiting_does_not_close_case(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        d9.record_waiting_reply(conn, waiting_id=waiting["waiting_id"], reply_id="M", satisfies_completion=True)
        case = _conn_exec(conn, "SELECT lifecycle_status FROM action_cases WHERE action_case_id='AC-1'").fetchone()
        assert case["lifecycle_status"] == "ACTIVE"

    def test_expire_waiting_does_not_close_case(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(-1))
        d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        case = _conn_exec(conn, "SELECT lifecycle_status FROM action_cases WHERE action_case_id='AC-1'").fetchone()
        assert case["lifecycle_status"] == "ACTIVE"


# ─── 14. Invariants: one active waiting per task; org scoping ───────────

class TestInvariants:
    def test_one_active_waiting_per_task(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        w1 = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        w2 = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="Y", due_at=_iso(48))
        # Idempotent: returns the same ACTIVE waiting
        assert w1["waiting_id"] == w2["waiting_id"]
        assert w2["status"] == "ACTIVE"

    def test_create_task_org_mismatch_rejected(self, db):
        conn = db["wrapper"]
        _seed_case(conn, organization_id="ORG-A")
        with pytest.raises(d9.D9StateError):
            d9.create_task(conn, organization_id="ORG-B", action_case_id="AC-1", title="t")

    def test_create_task_requires_existing_case(self, db):
        conn = db["wrapper"]
        with pytest.raises(d9.D9NotFoundError):
            d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-MISSING", title="t")


# ─── 15. Trace chain answers "why is this task back in my todo?" ────────

class TestTraceChain:
    def test_trace_reconstructs_reappearance(self, db):
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="联系供应商")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="SUPPLIER_REPLY", due_at=_iso(-1))
        # Due recovery expires it → task reappears in todo
        d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))

        # Query the Task's trace to explain reappearance
        traces = d9.get_trace_for_entity(conn, entity_type="task", entity_id=task["task_id"])
        kinds = [t["event_type"] for t in traces]
        assert "TASK_CREATED" in kinds
        assert "TASK_STATUS_CHANGED" in kinds  # WAITING then IN_PROGRESS

        # Query the Waiting's trace to explain why it left waiting
        w_traces = d9.get_trace_for_entity(conn, entity_type="waiting", entity_id=waiting["waiting_id"])
        w_kinds = [t["event_type"] for t in w_traces]
        assert "WAITING_CREATED" in w_kinds
        assert "WAITING_EXPIRED" in w_kinds

        # The EXPIRED trace explains the task resume
        expired = [t for t in w_traces if t["event_type"] == "WAITING_EXPIRED"][0]
        assert expired["payload_json"] and "task_resumed" in expired["payload_json"]


# ─── R2. Public API must NOT expose an arbitrary Task-status setter ───────

class TestPublicApiNoStatusBackdoor:
    def test_update_task_status_not_in_public_api(self):
        # The generic status setter is NOT part of the D9 public contract.
        # There is no supported way for an external caller to set an arbitrary
        # Task status; every change must go through a business action.
        assert "update_task_status" not in d9.__all__
        assert not hasattr(d9, "update_task_status")

    def test_todo_cannot_become_waiting_via_internal_backdoor(self, db):
        # Even reaching into the private funnel must refuse to create a WAITING
        # Task with no ACTIVE Waiting (Invariant A). This is exactly the
        # independent-acceptance finding: TODO + no Active Waiting → WAITING.
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        assert task["status"] == "TODO"
        with pytest.raises(d9.D9StateError):
            d9._update_task_status_internal(conn, task["task_id"], "WAITING")
        # Task remains untouched.
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "TODO"

    def test_waiting_plus_active_cannot_become_in_progress_via_backdoor(self, db):
        # Independent-acceptance finding #2: Task=WAITING + Waiting=ACTIVE must
        # never be turned into Task=IN_PROGRESS + Waiting=ACTIVE (conflicting
        # "still waiting yet also resumed" state).
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "WAITING"
        assert d9.get_active_waiting_for_task(conn, task["task_id"]) is not None
        with pytest.raises(d9.D9StateError):
            d9._update_task_status_internal(conn, task["task_id"], "IN_PROGRESS")
        # State unchanged — still consistent.
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "WAITING"
        assert d9.get_waiting_by_id(conn, waiting["waiting_id"])["status"] == "ACTIVE"

    def test_waiting_plus_active_cannot_become_todo_via_backdoor(self, db):
        # Independent-acceptance finding #3: Task=WAITING + Waiting=ACTIVE must
        # never be turned into Task=TODO + Waiting=ACTIVE.
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "WAITING"
        with pytest.raises(d9.D9StateError):
            d9._update_task_status_internal(conn, task["task_id"], "TODO")
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "WAITING"
        assert d9.get_waiting_by_id(conn, waiting["waiting_id"])["status"] == "ACTIVE"


# ─── R2. Strict Task/Waiting invariants after every public business op ────

def _assert_global_invariants(conn):
    """Invariant A: Task=WAITING ⇒ exactly one ACTIVE Waiting.
    Invariant B: ACTIVE Waiting ⇒ Task=WAITING.
    Invariant C: Task in {DONE,CANCELLED} ⇒ no ACTIVE Waiting.
    (Invariant D — no executable Task re-resumed under a CLOSED parent Case —
    is covered by the closed-case race tests.)"""
    tasks = _conn_exec(conn, "SELECT task_id, status FROM d9_action_case_tasks").fetchall()
    for tk in tasks:
        active = _conn_exec(
            conn,
            "SELECT COUNT(*) AS n FROM d9_action_case_waitings "
            "WHERE task_id=? AND status='ACTIVE'",
            (tk["task_id"],),
        ).fetchone()["n"]
        if tk["status"] == "WAITING":
            assert active == 1, (
                f"Invariant A violated: Task {tk['task_id']} is WAITING with "
                f"{active} active waiting"
            )
        else:
            assert active == 0, (
                f"Invariant B/C violated: Task {tk['task_id']} is {tk['status']} "
                f"with {active} active waiting"
            )
        assert tk["status"] in TASK_STATUSES_LITERAL


TASK_STATUSES_LITERAL = ("TODO", "IN_PROGRESS", "WAITING", "DONE", "CANCELLED")


class TestStrictInvariantsAfterBusinessOps:
    def test_full_flows_preserve_invariants(self, db):
        conn = db["wrapper"]

        # Scenario 1: create → start → waiting → reply → resume
        _seed_case(conn, action_case_id="AC-1")
        t1 = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t1")
        d9.start_task(conn, t1["task_id"])
        w1 = d9.put_task_on_waiting(conn, task_id=t1["task_id"], waiting_type="X", due_at=_iso(24))
        _assert_global_invariants(conn)  # WAITING + exactly 1 active
        d9.record_waiting_reply(conn, waiting_id=w1["waiting_id"], reply_id="M", satisfies_completion=True)
        _assert_global_invariants(conn)  # IN_PROGRESS, no active waiting

        # Scenario 2: create → start → waiting → due → resume
        _seed_case(conn, action_case_id="AC-2")
        t2 = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-2", title="t2")
        d9.start_task(conn, t2["task_id"])
        d9.put_task_on_waiting(conn, task_id=t2["task_id"], waiting_type="X", due_at=_iso(-1))
        _assert_global_invariants(conn)
        d9.run_due_recovery(conn, organization_id="ORG-A", current_time=_iso(0))
        _assert_global_invariants(conn)  # IN_PROGRESS, no active waiting

        # Scenario 3: create → start → waiting → manual cancel → resume
        _seed_case(conn, action_case_id="AC-3")
        t3 = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-3", title="t3")
        d9.start_task(conn, t3["task_id"])
        w3 = d9.put_task_on_waiting(conn, task_id=t3["task_id"], waiting_type="X", due_at=_iso(24))
        _assert_global_invariants(conn)
        d9.cancel_waiting(conn, waiting_id=w3["waiting_id"], cancel_reason="MANUAL")
        _assert_global_invariants(conn)  # IN_PROGRESS, no active waiting

        # Scenario 4: waiting task → complete → DONE + waiting cancelled
        _seed_case(conn, action_case_id="AC-4")
        t4 = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-4", title="t4")
        d9.start_task(conn, t4["task_id"])
        d9.put_task_on_waiting(conn, task_id=t4["task_id"], waiting_type="X", due_at=_iso(24))
        _assert_global_invariants(conn)
        d9.complete_task(conn, t4["task_id"])
        _assert_global_invariants(conn)  # DONE, no active waiting

        # Scenario 5: waiting task → cancel → CANCELLED + waiting cancelled
        _seed_case(conn, action_case_id="AC-5")
        t5 = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-5", title="t5")
        d9.start_task(conn, t5["task_id"])
        d9.put_task_on_waiting(conn, task_id=t5["task_id"], waiting_type="X", due_at=_iso(24))
        _assert_global_invariants(conn)
        d9.cancel_task(conn, t5["task_id"])
        _assert_global_invariants(conn)  # CANCELLED, no active waiting

    def test_terminal_task_keeps_no_active_waiting(self, db):
        # A terminal Task must never leave an ACTIVE Waiting behind, and a
        # terminal Task must never be resurrected into WAITING.
        conn = db["wrapper"]
        _seed_case(conn)
        task = d9.create_task(conn, organization_id="ORG-A", action_case_id="AC-1", title="t")
        d9.start_task(conn, task["task_id"])
        waiting = d9.put_task_on_waiting(conn, task_id=task["task_id"], waiting_type="X", due_at=_iso(24))
        d9.complete_task(conn, task["task_id"])  # → DONE, waiting cancelled
        _assert_global_invariants(conn)
        w = d9.get_waiting_by_id(conn, waiting["waiting_id"])
        assert w["status"] == "CANCELLED"
        # Terminal Task cannot be pushed back to WAITING (no backdoor) — and the
        # public API offers no such path at all.
        assert d9.get_task_by_id(conn, task["task_id"])["status"] == "DONE"
        assert d9.get_active_waiting_for_task(conn, task["task_id"]) is None
