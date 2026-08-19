"""
FlowOrder D9-P0: Action Case → Task → Waiting — Minimum Execution Closed Loop
=============================================================================

This module implements the D9-P0 minimum closed loop. It is built strictly
ON TOP OF the frozen D8 Action Case contract and NEVER mutates action_cases.

Design boundaries (enforced, not suggested):
  - Action Case (D8, frozen): a business problem that must be managed.
  - Task (this module): ONE concrete executable action under an Action Case.
  - Waiting (this module): a Task is paused waiting for an external reply /
    event / condition.

Hard rules from the D9-P0 product contract:
  1. The three layers are SEPARATE state machines. Task completion, Waiting
     resolution, and Waiting expiry NEVER auto-change the Action Case
     lifecycle_status or close_reason. Action Case keeps obeying D8 frozen rules.
  2. One Action Case may have MANY Tasks. A Task entering WAITING only
     suppresses THAT task — it does NOT freeze the Action Case and does NOT
     suppress other Tasks in the same Case.
  3. Task lifecycle (minimum): TODO (待执行) / IN_PROGRESS (执行中或可执行) /
     WAITING (等待中) / DONE (已完成) / CANCELLED (已取消).
  4. Waiting lifecycle (minimum): ACTIVE / RESOLVED / EXPIRED / CANCELLED.
  5. Due Recovery (P0): ACTIVE waiting with due_at <= now → EXPIRED and the
     linked Task is resumed (back to IN_PROGRESS). This is idempotent by
     construction (conditional UPDATE on status + event emission gated on
     rowcount). It never reopens a closed Case, never creates a ghost Task,
     and never produces a duplicate recovery.
  6. Partial Reply: a reply that does NOT satisfy the Waiting's completion
     condition is recorded as evidence/trace but the Waiting stays ACTIVE and
     the Task keeps WAITING. Mere receipt of "a message" never ends a Waiting.
  7. Duplicate Reply / Duplicate Cancel / Repeated Due scan: all idempotent
     via conditional updates + reply_id dedupe + persisted state.

Reused from D8 (no reinvention):
  - _new_id, _now_iso, _row_to_dict, _conn_exec, CN_TZ
  - get_case_by_id (read-only) — used ONLY to (a) verify org scoping at
    Task creation and (b) detect a CLOSED parent Case during Due Recovery.
  - The optimistic-concurrency `version` CAS pattern and partial unique
    index pattern.

Persistence: dedicated `d9_*` tables (NOT the legacy `tasks` table) so the
three layers are never collapsed into one state machine.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

# Reuse D8 helpers and read-only case accessor. D8 never imports D9, so there
# is no import cycle.
from d8_action_case import (
    CN_TZ,
    _conn_exec,
    _new_id,
    _now_iso,
    _row_to_dict,
    get_case_by_id,
)

D9_POLICY_VERSION = "D9_TASK_WAITING_V1"

# ── Task lifecycle (minimum required semantics) ──────────────────────────
TASK_STATUSES = frozenset({
    "TODO",          # 待执行
    "IN_PROGRESS",   # 执行中 / 可执行
    "WAITING",       # 等待中
    "DONE",          # 已完成
    "CANCELLED",     # 已取消
})

# ── Waiting lifecycle (minimum required semantics) ───────────────────────
WAITING_STATUSES = frozenset({
    "ACTIVE",        # 等待中
    "RESOLVED",      # 等待事项已得到有效回复/事件
    "EXPIRED",       # 等待到期
    "CANCELLED",     # 等待被明确取消
})


class D9Error(Exception):
    """Base error for D9 Task/Waiting operations."""


class D9NotFoundError(D9Error):
    """Raised when a Task or Waiting is not found."""


class D9StateError(D9Error):
    """Raised on an illegal state transition or contract violation."""


# ---------------------------------------------------------------------------
# Timezone helpers (D: canonical UTC for all due-time writes & comparisons)
# ---------------------------------------------------------------------------


def _normalize_iso_to_utc(iso_str: str) -> str:
    """Parse an ISO8601 timestamp, REJECT naive (timezone-less) values, and
    return a canonical UTC ISO8601 string.

    Naive times are rejected rather than guessed: mixing offsets in a raw
    string comparison (`due_at <= now`) silently misjudges expiry across
    timezones, so we normalize everything to UTC before it is ever persisted
    or compared."""
    if not isinstance(iso_str, str):
        raise D9StateError(f"Timestamp must be a string, got {type(iso_str).__name__}: {iso_str!r}")
    try:
        dt = datetime.fromisoformat(iso_str)
    except ValueError:
        raise D9StateError(f"Invalid ISO8601 timestamp: {iso_str!r}")
    if dt.tzinfo is None:
        raise D9StateError(
            f"Timezone-aware timestamp required (naive time rejected): {iso_str!r}"
        )
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Table guards
# ---------------------------------------------------------------------------


def _ensure_tables(conn: Any) -> None:
    from database import table_exists

    for t in ("d9_action_case_tasks", "d9_action_case_waitings", "d9_trace_events"):
        if not table_exists(conn, t):
            raise RuntimeError(
                f"D9 table '{t}' does not exist. "
                "Run alembic upgrade head or create_tables_from_schema first."
            )


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------


def _trace(
    conn: Any,
    *,
    organization_id: str,
    trace_kind: str,
    entity_type: str,
    entity_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    actor: str | None = None,
) -> str:
    """Append a D9 trace event. Used to answer 'why is this task back in my
    todo?' by reconstructing the Action Case → Task → Waiting → Reply →
    Resolved/Expired → Task resumed chain."""
    trace_id = _new_id("TR")
    now = _now_iso()
    _conn_exec(
        conn,
        """INSERT INTO d9_trace_events
           (trace_id, organization_id, trace_kind, entity_type, entity_id,
            event_type, payload_json, actor, created_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            trace_id,
            organization_id,
            trace_kind,
            entity_type,
            entity_id,
            event_type,
            json.dumps(payload or {}, ensure_ascii=False),
            actor,
            now,
        ),
    )
    return trace_id


def get_trace_for_entity(
    conn: Any,
    *,
    entity_type: str,
    entity_id: str,
    organization_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return ordered trace events for an entity (task / waiting / case)."""
    sql = "SELECT * FROM d9_trace_events WHERE entity_type=? AND entity_id=?"
    params: list[Any] = [entity_type, entity_id]
    if organization_id:
        sql += " AND organization_id=?"
        params.append(organization_id)
    sql += " ORDER BY created_at ASC, trace_id ASC"
    rows = _conn_exec(conn, sql, tuple(params)).fetchall()
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Task CRUD + lifecycle
# ---------------------------------------------------------------------------


def get_task_by_id(conn: Any, task_id: str) -> dict[str, Any] | None:
    _ensure_tables(conn)
    row = _conn_exec(
        conn, "SELECT * FROM d9_action_case_tasks WHERE task_id=?", (task_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_tasks_for_case(
    conn: Any, *, organization_id: str, action_case_id: str
) -> list[dict[str, Any]]:
    _ensure_tables(conn)
    rows = _conn_exec(
        conn,
        "SELECT * FROM d9_action_case_tasks "
        "WHERE organization_id=? AND action_case_id=? ORDER BY created_at ASC",
        (organization_id, action_case_id),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def create_task(
    conn: Any,
    *,
    organization_id: str,
    action_case_id: str,
    title: str,
    recommended_action: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Create a Task under an Action Case.

    New Tasks ALWAYS start at TODO — there is deliberately no backdoor to
    create a Task already in WAITING / DONE / CANCELLED (Task FSM, section A).

    Read-only check against D8 action_cases: the case must exist, its
    organization_id must match, and it must NOT be CLOSED (B3). This enforces
    tenant isolation and prevents orphan / post-close Tasks. This function
    NEVER writes to action_cases.
    """
    _ensure_tables(conn)
    case = get_case_by_id(conn, action_case_id)
    if case is None:
        raise D9NotFoundError(
            f"Action Case {action_case_id} not found; cannot create Task under it."
        )
    if case.get("organization_id") != organization_id:
        raise D9StateError(
            "Task organization_id does not match its Action Case organization_id."
        )
    if case.get("lifecycle_status") == "CLOSED":
        raise D9StateError(
            f"Cannot create a Task under a CLOSED Action Case {action_case_id}."
        )

    status = "TODO"
    now = _now_iso()
    task_id = _new_id("TK")
    _conn_exec(
        conn,
        """INSERT INTO d9_action_case_tasks
           (task_id, organization_id, action_case_id, title, recommended_action,
            status, version, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (
            task_id,
            organization_id,
            action_case_id,
            title,
            recommended_action,
            status,
            1,
            now,
            now,
        ),
    )
    _trace(
        conn,
        organization_id=organization_id,
        trace_kind="TASK",
        entity_type="task",
        entity_id=task_id,
        event_type="TASK_CREATED",
        payload={"action_case_id": action_case_id, "title": title, "status": status},
        actor=actor,
    )
    created = get_task_by_id(conn, task_id)
    if not created:
        raise RuntimeError(f"Failed to create task {task_id}")
    return created


def _update_task_status_internal(
    conn: Any,
    task_id: str,
    new_status: str,
    *,
    expected_status: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """INTERNAL state-transition funnel. NOT part of the D9 public API.

    Task status must change ONLY through a business action — create_task /
    start_task / complete_task / cancel_task / put_task_on_waiting / Waiting
    resolve / Waiting cancel / Due Recovery. This private helper is the single
    place those actions use to mutate the Task row, and it enforces:

      * terminal-state guard: DONE / CANCELLED are sink states;
      * optional CAS guard via expected_status (idempotent resume uses
        expected_status='WAITING');
      * Invariant A: a Task may only become WAITING when an ACTIVE Waiting
        exists — no "Task=WAITING with no Active Waiting" zombie;
      * Invariant B/C: a Task may only leave WAITING (to any non-WAITING
        status) once the linked ACTIVE Waiting has been RESOLVED/EXPIRED/
        CANCELLED — the caller must close the Waiting first.

    Calling this helper directly (it is name-mangled private) bypasses the
    business contract and is unsupported; the guards below still apply so it
    can never silently produce a Task/Waiting desync."""
    _ensure_tables(conn)
    if new_status not in TASK_STATUSES:
        raise D9StateError(f"Invalid task status: {new_status}")
    task = get_task_by_id(conn, task_id)
    if not task:
        raise D9NotFoundError(f"Task {task_id} not found")

    cur = task["status"]
    # A-ed: terminal-state guard. DONE and CANCELLED are sink states; no further
    # transition (including re-entry via WAITING/IN_PROGRESS) is permitted.
    if cur in ("DONE", "CANCELLED"):
        raise D9StateError(
            f"Task {task_id} is in terminal state '{cur}'; "
            f"no further transitions allowed."
        )

    # Optional CAS guard (idempotent resume uses expected_status='WAITING').
    if expected_status is not None and cur != expected_status:
        raise D9StateError(
            f"Task {task_id} expected status '{expected_status}' but was '{cur}'."
        )

    # R2 invariant guards (defense-in-depth against Task/Waiting desync).
    active = get_active_waiting_for_task(conn, task_id)
    if new_status == "WAITING" and not active:
        # Invariant A: Task=WAITING must have exactly one ACTIVE Waiting.
        raise D9StateError(
            f"Cannot set Task {task_id} to WAITING: no ACTIVE Waiting exists "
            f"(would violate Invariant A). Use put_task_on_waiting()."
        )
    if new_status != "WAITING" and active:
        # Invariant B/C: leaving WAITING while an ACTIVE Waiting still exists
        # would create a conflicting state ("Task not waiting, but still
        # waiting"). The Waiting must be RESOLVED/EXPIRED/CANCELLED first.
        raise D9StateError(
            f"Cannot set Task {task_id} to {new_status}: ACTIVE Waiting "
            f"{active['waiting_id']} still exists (would violate Invariant B/C). "
            f"Resolve/expire/cancel the Waiting before changing Task status."
        )

    now = _now_iso()
    if expected_status is not None:
        rows = _conn_exec(
            conn,
            """UPDATE d9_action_case_tasks
               SET status=?, version=version+1, updated_at=?
               WHERE task_id=? AND status=?""",
            (new_status, now, task_id, expected_status),
        )
    else:
        rows = _conn_exec(
            conn,
            """UPDATE d9_action_case_tasks
               SET status=?, version=version+1, updated_at=?
               WHERE task_id=?""",
            (new_status, now, task_id),
        )

    changed = (rows.rowcount if hasattr(rows, "rowcount") else 0) > 0
    if not changed:
        # CAS miss (or already not in expected_status) — no-op, return current.
        return get_task_by_id(conn, task_id)

    updated = get_task_by_id(conn, task_id)
    _trace(
        conn,
        organization_id=updated["organization_id"],
        trace_kind="TASK",
        entity_type="task",
        entity_id=task_id,
        event_type="TASK_STATUS_CHANGED",
        payload={"from": cur, "to": new_status},
        actor=actor,
    )
    return updated


def start_task(conn: Any, task_id: str, *, actor: str | None = None) -> dict[str, Any]:
    """TODO → IN_PROGRESS (begin / make executable)."""
    return _update_task_status_internal(conn, task_id, "IN_PROGRESS", expected_status="TODO", actor=actor)


def complete_task(conn: Any, task_id: str, *, actor: str | None = None) -> dict[str, Any]:
    """Mark a Task DONE. Does NOT touch the Action Case. Any ACTIVE Waiting on
    this Task is cancelled (TASK_DONE) to avoid an orphan waiting the Due
    Worker would later act on."""
    task = get_task_by_id(conn, task_id)
    if not task:
        raise D9NotFoundError(f"Task {task_id} not found")
    _cancel_active_waiting_if_any(conn, task_id, cancel_reason="TASK_DONE", actor=actor)
    updated = _update_task_status_internal(conn, task_id, "DONE", actor=actor)
    return updated


def cancel_task(conn: Any, task_id: str, *, actor: str | None = None) -> dict[str, Any]:
    """Mark a Task CANCELLED. Does NOT touch the Action Case. Any ACTIVE Waiting
    on this Task is cancelled (TASK_CANCELLED)."""
    task = get_task_by_id(conn, task_id)
    if not task:
        raise D9NotFoundError(f"Task {task_id} not found")
    _cancel_active_waiting_if_any(conn, task_id, cancel_reason="TASK_CANCELLED", actor=actor)
    updated = _update_task_status_internal(conn, task_id, "CANCELLED", actor=actor)
    return updated


def _cancel_task_internal(
    conn: Any, task_id: str, *, cancel_reason: str, actor: str | None = None
) -> dict[str, Any] | None:
    """Safely close a Task to CANCELLED with an explicit reason-trace, WITHOUT
    performing any Waiting cleanup (the caller is already managing the linked
    Waiting). Used by the closed-parent-case收口 path so we never resurrect an
    executable Task under a closed/missing Action Case."""
    task = get_task_by_id(conn, task_id)
    if not task:
        return None
    if task["status"] in ("DONE", "CANCELLED"):
        return task  # already terminal — no-op
    from_status = task["status"]
    updated = _update_task_status_internal(conn, task_id, "CANCELLED", actor=actor)
    _trace(
        conn,
        organization_id=updated["organization_id"],
        trace_kind="TASK",
        entity_type="task",
        entity_id=task_id,
        event_type="TASK_CANCELLED",
        payload={"from": from_status, "to": "CANCELLED", "reason": cancel_reason},
        actor=actor,
    )
    return updated


# ---------------------------------------------------------------------------
# Waiting CRUD + lifecycle
# ---------------------------------------------------------------------------


def get_waiting_by_id(conn: Any, waiting_id: str) -> dict[str, Any] | None:
    _ensure_tables(conn)
    row = _conn_exec(
        conn, "SELECT * FROM d9_action_case_waitings WHERE waiting_id=?", (waiting_id,)
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_active_waiting_for_task(conn: Any, task_id: str) -> dict[str, Any] | None:
    _ensure_tables(conn)
    row = _conn_exec(
        conn,
        "SELECT * FROM d9_action_case_waitings WHERE task_id=? AND status='ACTIVE' LIMIT 1",
        (task_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_waitings_for_case(
    conn: Any, *, organization_id: str, action_case_id: str
) -> list[dict[str, Any]]:
    _ensure_tables(conn)
    rows = _conn_exec(
        conn,
        "SELECT * FROM d9_action_case_waitings "
        "WHERE organization_id=? AND action_case_id=? ORDER BY created_at ASC",
        (organization_id, action_case_id),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def list_overdue_active_waitings(
    conn: Any, *, organization_id: str, now: str
) -> list[dict[str, Any]]:
    """Due-scan query. Only ACTIVE waitings whose due_at <= now are returned."""
    _ensure_tables(conn)
    rows = _conn_exec(
        conn,
        """SELECT * FROM d9_action_case_waitings
           WHERE organization_id=? AND status='ACTIVE' AND due_at <= ?
           ORDER BY due_at ASC, waiting_id ASC""",
        (organization_id, now),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def put_task_on_waiting(
    conn: Any,
    *,
    task_id: str,
    waiting_type: str,
    due_at: str,
    reason: str | None = None,
    source_trace_id: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Create a Waiting for a Task and set the Task to WAITING.

    Only THAT task is suppressed. The Action Case is NOT frozen and other
    Tasks in the same Case are NOT affected.

    Idempotency: a Task may have at most one ACTIVE Waiting (partial unique
    index uq_d9_waitings_active). If the Task already has an ACTIVE Waiting,
    the existing one is returned instead of creating a duplicate.
    """
    _ensure_tables(conn)
    task = get_task_by_id(conn, task_id)
    if not task:
        raise D9NotFoundError(f"Task {task_id} not found")

    # Idempotent: if an ACTIVE Waiting already exists, return it.
    existing = get_active_waiting_for_task(conn, task_id)
    if existing:
        return existing

    # B4 / A: a CLOSED (or missing) parent Action Case rejects new Waiting
    # creation. We must not create an ACTIVE Waiting under a case that can no
    # longer legally carry executable work.
    case = get_case_by_id(conn, task["action_case_id"])
    if case is None or case.get("lifecycle_status") == "CLOSED":
        raise D9StateError(
            f"Cannot create a Waiting under a CLOSED/missing Action Case "
            f"{task['action_case_id']}."
        )

    # A / Task FSM: only an executable (IN_PROGRESS) Task may enter WAITING.
    # DONE / CANCELLED are terminal and must never be resurrected into WAITING;
    # TODO must be started first (start_task). A WAITING Task without an active
    # Waiting is an anomaly and is rejected — no backdoor to create one.
    if task["status"] != "IN_PROGRESS":
        raise D9StateError(
            f"Task {task_id} is '{task['status']}'; only IN_PROGRESS Tasks may "
            f"enter WAITING."
        )

    # D: normalize due_at to canonical UTC before persisting. Naive (timezone-
    # less) timestamps are rejected outright.
    due_at_utc = _normalize_iso_to_utc(due_at)

    now = _now_iso()
    waiting_id = _new_id("WT")
    try:
        _conn_exec(
            conn,
            """INSERT INTO d9_action_case_waitings
               (waiting_id, organization_id, task_id, action_case_id,
                waiting_type, reason, due_at, status, source_trace_id,
                reply_count, latest_reply_json, version, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                waiting_id,
                task["organization_id"],
                task_id,
                task["action_case_id"],
                waiting_type,
                reason,
                due_at_utc,
                "ACTIVE",
                source_trace_id,
                0,
                "[]",
                1,
                now,
                now,
            ),
        )
    except IntegrityError:
        # Race: another writer created the ACTIVE waiting first.
        already = get_active_waiting_for_task(conn, task_id)
        if already:
            return already
        raise

    # Suppress ONLY this task.
    _update_task_status_internal(conn, task_id, "WAITING", expected_status=None, actor=actor)

    _trace(
        conn,
        organization_id=task["organization_id"],
        trace_kind="WAITING",
        entity_type="waiting",
        entity_id=waiting_id,
        event_type="WAITING_CREATED",
        payload={
            "task_id": task_id,
            "action_case_id": task["action_case_id"],
            "waiting_type": waiting_type,
            "due_at": due_at_utc,
            "source_trace_id": source_trace_id,
        },
        actor=actor,
    )
    created = get_waiting_by_id(conn, waiting_id)
    if not created:
        raise RuntimeError(f"Failed to create waiting {waiting_id}")
    return created


def _recorded_reply_ids(waiting: dict[str, Any]) -> set[str]:
    try:
        data = json.loads(waiting.get("latest_reply_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        data = []
    return {r.get("reply_id") for r in data if isinstance(r, dict) and r.get("reply_id")}


def _append_reply(
    conn: Any,
    waiting_id: str,
    reply_id: str | None,
    reply_payload: Any,
    received_at: str,
) -> None:
    waiting = get_waiting_by_id(conn, waiting_id)
    if not waiting:
        raise D9NotFoundError(f"Waiting {waiting_id} not found")
    try:
        data = json.loads(waiting.get("latest_reply_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        data = []
    if not isinstance(data, list):
        data = []
    data.append({
        "reply_id": reply_id,
        "payload": reply_payload,
        "received_at": received_at,
    })
    _conn_exec(
        conn,
        """UPDATE d9_action_case_waitings
           SET latest_reply_json=?, reply_count=?, updated_at=?
           WHERE waiting_id=?""",
        (json.dumps(data, ensure_ascii=False), len(data), received_at, waiting_id),
    )


def record_waiting_reply(
    conn: Any,
    *,
    waiting_id: str,
    reply_id: str | None = None,
    reply_payload: Any = None,
    satisfies_completion: bool = False,
    actor: str | None = None,
) -> dict[str, Any]:
    """Record an external reply against a Waiting.

    Idempotent / safe against the contract's required attacks:
      - If the Waiting is not ACTIVE (already RESOLVED/EXPIRED/CANCELLED), a
        late/duplicate reply is a no-op (no re-resolve, no new recovery event).
      - If the SAME reply_id was already recorded, it is a no-op (duplicate
        consumption of the same external message).

    Partial Reply (satisfies_completion=False):
      The reply is recorded as evidence/trace but the Waiting stays ACTIVE and
      the Task keeps WAITING. Mere receipt of a message never ends a Waiting.

    Full Reply (satisfies_completion=True):
      ACTIVE → RESOLVED and the linked Task is resumed to IN_PROGRESS.
    """
    _ensure_tables(conn)
    waiting = get_waiting_by_id(conn, waiting_id)
    if not waiting:
        raise D9NotFoundError(f"Waiting {waiting_id} not found")

    if waiting["status"] != "ACTIVE":
        # Already resolved/expired/cancelled → duplicate/late reply is a no-op.
        return waiting

    # Duplicate consumption guard: same external reply_id already processed.
    if reply_id and reply_id in _recorded_reply_ids(waiting):
        return waiting

    received_at = _now_iso()
    _append_reply(conn, waiting_id, reply_id, reply_payload, received_at)
    _trace(
        conn,
        organization_id=waiting["organization_id"],
        trace_kind="REPLY",
        entity_type="waiting",
        entity_id=waiting_id,
        event_type="REPLY_RECEIVED",
        payload={
            "reply_id": reply_id,
            "satisfies_completion": satisfies_completion,
        },
        actor=actor,
    )

    if satisfies_completion:
        return _resolve_waiting_internal(conn, waiting_id, actor=actor, resolved_by_reply=reply_id)
    # Partial reply: stay ACTIVE, Task stays WAITING.
    return get_waiting_by_id(conn, waiting_id)


def _resolve_waiting_internal(
    conn: Any,
    waiting_id: str,
    *,
    actor: str | None = None,
    resolved_by_reply: str | None = None,
) -> dict[str, Any]:
    """ACTIVE → RESOLVED and resume the linked Task (if still WAITING).

    Idempotent: conditional UPDATE on status='ACTIVE'. A second call (duplicate
    resolve) changes 0 rows → no-op, no duplicate Task resume, no duplicate
    trace event."""
    now = _now_iso()
    rows = _conn_exec(
        conn,
        """UPDATE d9_action_case_waitings
           SET status='RESOLVED', resolved_at=?, version=version+1, updated_at=?
           WHERE waiting_id=? AND status='ACTIVE'""",
        (now, now, waiting_id),
    )
    if (rows.rowcount if hasattr(rows, "rowcount") else 0) == 0:
        return get_waiting_by_id(conn, waiting_id)

    waiting = get_waiting_by_id(conn, waiting_id)
    task = get_task_by_id(conn, waiting["task_id"])
    resumed = False
    if task and task["status"] == "WAITING":
        case = get_case_by_id(conn, waiting["action_case_id"])
        parent_active = case is not None and case.get("lifecycle_status") == "ACTIVE"
        if parent_active:
            # Normal path: resume the (now unblocked) Task to IN_PROGRESS.
            _update_task_status_internal(
                conn, task["task_id"], "IN_PROGRESS",
                expected_status="WAITING", actor=actor,
            )
            resumed = True
        else:
            # B2: closed / missing parent Case. A late completing reply must
            # NEVER resurrect an executable Task (that is a ghost action). The
            # (now meaningless) Task is safely closed instead.
            _cancel_task_internal(
                conn, task["task_id"], cancel_reason="PARENT_CASE_CLOSED", actor=actor
            )

    _trace(
        conn,
        organization_id=waiting["organization_id"],
        trace_kind="RECOVERY",
        entity_type="waiting",
        entity_id=waiting_id,
        event_type="WAITING_RESOLVED",
        payload={
            "task_id": waiting["task_id"],
            "action_case_id": waiting["action_case_id"],
            "resolved_by_reply": resolved_by_reply,
            "task_resumed": resumed,
        },
        actor=actor,
    )
    return waiting


def resolve_waiting(
    conn: Any, *, waiting_id: str, actor: str | None = None
) -> dict[str, Any]:
    """Explicitly resolve a Waiting (not via an external reply)."""
    _ensure_tables(conn)
    waiting = get_waiting_by_id(conn, waiting_id)
    if not waiting:
        raise D9NotFoundError(f"Waiting {waiting_id} not found")
    return _resolve_waiting_internal(conn, waiting_id, actor=actor)


def _cancel_waiting_internal(
    conn: Any,
    waiting_id: str,
    *,
    cancel_reason: str,
    actor: str | None = None,
) -> dict[str, Any]:
    """ACTIVE → CANCELLED. Idempotent (conditional on status='ACTIVE')."""
    now = _now_iso()
    rows = _conn_exec(
        conn,
        """UPDATE d9_action_case_waitings
           SET status='CANCELLED', cancelled_at=?, cancel_reason=?,
               version=version+1, updated_at=?
           WHERE waiting_id=? AND status='ACTIVE'""",
        (now, cancel_reason, now, waiting_id),
    )
    if (rows.rowcount if hasattr(rows, "rowcount") else 0) == 0:
        return get_waiting_by_id(conn, waiting_id)

    waiting = get_waiting_by_id(conn, waiting_id)
    task = get_task_by_id(conn, waiting["task_id"])
    # C: resolve the linked Task consistently with WHY the Waiting was cancelled.
    # We must never leave a "Task=WAITING with no Active Waiting" zombie, nor
    # resurrect an executable Task under a closed Case.
    task_resumed = False
    if task and task["status"] == "WAITING":
        if cancel_reason == "PARENT_CASE_CLOSED":
            # Closed / missing parent Case: never resume; safely close the Task.
            _cancel_task_internal(
                conn, task["task_id"], cancel_reason="PARENT_CASE_CLOSED", actor=actor
            )
        elif cancel_reason in ("TASK_DONE", "TASK_CANCELLED"):
            # The Task is already terminal (complete_task / cancel_task triggered
            # this Waiting cancellation). Leave it as-is.
            pass
        else:
            # Manual / business-active cancel under an ACTIVE parent Case: the
            # external condition is no longer blocking, so the action becomes
            # executable again.
            case = get_case_by_id(conn, waiting["action_case_id"])
            parent_active = case is not None and case.get("lifecycle_status") == "ACTIVE"
            if parent_active:
                _update_task_status_internal(
                    conn, task["task_id"], "IN_PROGRESS",
                    expected_status="WAITING", actor=actor,
                )
                task_resumed = True
            else:
                # Defensive: closed parent Case + manual cancel → close the Task,
                # never resurrect.
                _cancel_task_internal(
                    conn, task["task_id"], cancel_reason="PARENT_CASE_CLOSED", actor=actor
                )

    _trace(
        conn,
        organization_id=waiting["organization_id"],
        trace_kind="WAITING",
        entity_type="waiting",
        entity_id=waiting_id,
        event_type="WAITING_CANCELLED",
        payload={
            "task_id": waiting["task_id"],
            "action_case_id": waiting["action_case_id"],
            "cancel_reason": cancel_reason,
            "task_resumed": task_resumed,
        },
        actor=actor,
    )
    return waiting


def cancel_waiting(
    conn: Any, *, waiting_id: str, cancel_reason: str = "MANUAL", actor: str | None = None
) -> dict[str, Any]:
    """Explicitly cancel a Waiting. Idempotent.

    Minimal conservative contract decision (documented in D9 decisions):
    cancelling a Waiting does NOT change the linked Task status and does NOT
    infer anything about the Action Case. The Task owner decides next steps.
    """
    _ensure_tables(conn)
    waiting = get_waiting_by_id(conn, waiting_id)
    if not waiting:
        raise D9NotFoundError(f"Waiting {waiting_id} not found")
    return _cancel_waiting_internal(conn, waiting_id, cancel_reason=cancel_reason, actor=actor)


def _cancel_active_waiting_if_any(
    conn: Any, task_id: str, *, cancel_reason: str, actor: str | None = None
) -> None:
    waiting = get_active_waiting_for_task(conn, task_id)
    if waiting:
        _cancel_waiting_internal(conn, waiting["waiting_id"], cancel_reason=cancel_reason, actor=actor)


# ---------------------------------------------------------------------------
# Due Recovery (P0) — idempotent expiry scan
# ---------------------------------------------------------------------------


def run_due_recovery(
    conn: Any,
    *,
    organization_id: str,
    current_time: str | None = None,
    scan_id: str | None = None,
    actor: str = "DUE_WORKER",
) -> dict[str, Any]:
    """Minimum due-recovery scan.

    For every ACTIVE Waiting with due_at <= now:
      - If its parent Action Case is CLOSED (or missing): the Waiting is
        CANCELLED (reason PARENT_CASE_CLOSED). The Task is NOT resumed and the
        Action Case is NOT reopened — no ghost task, no case mutation.
      - Otherwise: the Waiting is EXPIRED and the linked Task is resumed to
        IN_PROGRESS.

    Idempotency (P0 acceptance): the expiry/cancel UPDATE is conditional on
    status='ACTIVE', and the Task resume is conditional on status='WAITING'.
    Event emission is gated on actual row changes. Therefore running the scan
    any number of times (same worker twice, two scans, after restart, after
    already-EXPIRED) yields exactly ONE effective recovery and ONE set of trace
    events — never a duplicate Task or duplicate recovery side-effect.

    This function NEVER writes to action_cases.
    """
    _ensure_tables(conn)
    # D: normalize the scan time to canonical UTC before comparing against the
    # (also UTC-normalized) due_at column. Naive timestamps are rejected.
    now = _normalize_iso_to_utc(current_time) if current_time else _normalize_iso_to_utc(_now_iso())
    due = list_overdue_active_waitings(conn, organization_id=organization_id, now=now)

    scanned = len(due)
    expired = 0
    cancelled_orphan = 0
    skipped = 0
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()  # defensive in-scan dedupe

    for w in due:
        if w["waiting_id"] in seen_ids:
            continue
        seen_ids.add(w["waiting_id"])

        case = get_case_by_id(conn, w["action_case_id"])
        if case is None or case.get("lifecycle_status") == "CLOSED":
            # Closed / missing parent Case → orphan waiting.
            res = _cancel_waiting_internal(
                conn, w["waiting_id"], cancel_reason="PARENT_CASE_CLOSED", actor=actor
            )
            cancelled_orphan += 1
            results.append({
                "waiting_id": w["waiting_id"],
                "task_id": w["task_id"],
                "outcome": "CANCELLED_ORPHAN",
                "parent_case_status": (case.get("lifecycle_status") if case else "MISSING"),
            })
            continue

        exp = _expire_waiting_internal(conn, w["waiting_id"], now=now, actor=actor)
        if exp["changed"]:
            expired += 1
            results.append({
                "waiting_id": w["waiting_id"],
                "task_id": w["task_id"],
                "outcome": "EXPIRED",
                "task_resumed": exp["task_resumed"],
            })
        else:
            skipped += 1  # already not ACTIVE (idempotent no-op)

    return {
        "status": "OK",
        "policy_version": D9_POLICY_VERSION,
        "organization_id": organization_id,
        "scan_id": scan_id,
        "scanned_at": now,
        "scanned": scanned,
        "expired": expired,
        "cancelled_orphan": cancelled_orphan,
        "skipped": skipped,
        "results": results,
    }


def _expire_waiting_internal(
    conn: Any, waiting_id: str, *, now: str, actor: str = "DUE_WORKER"
) -> dict[str, Any]:
    """ACTIVE → EXPIRED and resume linked Task (if still WAITING).

    Idempotent via conditional UPDATE. Returns {'changed': bool, 'task_resumed': bool}."""
    rows = _conn_exec(
        conn,
        """UPDATE d9_action_case_waitings
           SET status='EXPIRED', expired_at=?, version=version+1, updated_at=?
           WHERE waiting_id=? AND status='ACTIVE'""",
        (now, now, waiting_id),
    )
    if (rows.rowcount if hasattr(rows, "rowcount") else 0) == 0:
        return {"changed": False, "task_resumed": False}

    waiting = get_waiting_by_id(conn, waiting_id)
    task = get_task_by_id(conn, waiting["task_id"])
    task_resumed = False
    if task and task["status"] == "WAITING":
        _update_task_status_internal(
            conn, task["task_id"], "IN_PROGRESS",
            expected_status="WAITING", actor=actor,
        )
        task_resumed = True

    _trace(
        conn,
        organization_id=waiting["organization_id"],
        trace_kind="RECOVERY",
        entity_type="waiting",
        entity_id=waiting_id,
        event_type="WAITING_EXPIRED",
        payload={
            "task_id": waiting["task_id"],
            "action_case_id": waiting["action_case_id"],
            "task_resumed": task_resumed,
        },
        actor=actor,
    )
    return {"changed": True, "task_resumed": task_resumed}


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "D9_POLICY_VERSION",
    "TASK_STATUSES",
    "WAITING_STATUSES",
    "D9Error",
    "D9NotFoundError",
    "D9StateError",
    "get_task_by_id",
    "list_tasks_for_case",
    "create_task",
    "start_task",
    "complete_task",
    "cancel_task",
    "get_waiting_by_id",
    "get_active_waiting_for_task",
    "list_waitings_for_case",
    "list_overdue_active_waitings",
    "put_task_on_waiting",
    "record_waiting_reply",
    "resolve_waiting",
    "cancel_waiting",
    "run_due_recovery",
    "get_trace_for_entity",
]
