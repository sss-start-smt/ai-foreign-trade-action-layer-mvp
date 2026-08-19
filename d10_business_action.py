"""
FlowOrder D10: BusinessAction Submission + Transactional Outbox
===============================================================

D10 sits strictly downstream of the frozen D8/D9 object model:

    Risk Signal -> Action Case -> Task -> BusinessAction -> Outbox

This module does NOT execute ERP/CRM/email side effects. It only makes a
business action request durable and records an Outbox event in the same short
transaction. D11+ may add approval/execution policy; D12+ may dispatch Outbox
records to workers.

D10 V1 invariants:
- Action Case and Task are read-only inputs here; D10 never mutates D8/D9 state.
- A Task may have zero or one primary BusinessAction in V1. Independent effects
  should be modeled as separate Tasks so they can succeed/fail/retry separately.
- Same (organization_id, idempotency_key) + same canonical request returns the
  original result; it never creates a second BusinessAction/Outbox event.
- Reusing the same idempotency key for a different request is a hard conflict.
- BusinessAction + Outbox + Idempotency + Audit are committed together or all
  rolled back together.
- ACCEPTED means "FlowOrder durably accepted the requested action", NOT "the
  external system has already executed it".
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy.exc import IntegrityError

from d8_action_case import _conn_exec, _row_to_dict
from business_value_validation import BusinessValueValidationError, validate_action_dates

CN_TZ = timezone(timedelta(hours=8))
D10_POLICY_VERSION = "D10_BUSINESS_ACTION_V1"

ACTION_STATUS_ACCEPTED = "ACCEPTED"
OUTBOX_STATUS_PENDING = "PENDING"


class D10Error(Exception):
    """Base error for D10 contract violations."""


class D10NotFoundError(D10Error):
    pass


class D10StateError(D10Error):
    pass


class D10IdempotencyConflict(D10Error):
    pass


class D10TaskActionConflict(D10Error):
    pass


class D10SubmissionError(D10Error):
    """Submission failed and the owned Unit of Work was rolled back."""

    def __init__(self, message: str, *, stage: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.cause = cause


@dataclass(frozen=True)
class BusinessActionSubmission:
    organization_id: str
    task_id: str
    action_type: str
    target_type: str
    target_id: str
    payload: dict[str, Any]
    idempotency_key: str
    actor: str
    request_id: str
    source: str = "ACTION_WORKSPACE"
    reason: str | None = None


@dataclass(frozen=True)
class BusinessActionPlan:
    organization_id: str
    action_case_id: str
    task_id: str
    order_id: str
    action_type: str
    target_type: str
    target_id: str
    payload: dict[str, Any]
    idempotency_key: str
    actor: str
    request_id: str
    source: str
    reason: str | None
    request_hash: str
    effect_hash: str
    policy_version: str = D10_POLICY_VERSION


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise D10StateError(f"payload must be JSON-serializable: {exc}") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _require_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise D10StateError(f"{name} is required")
    return value.strip()


def _ensure_tables(conn: Any) -> None:
    from database import table_exists

    for table in (
        "d10_business_actions",
        "d10_outbox_events",
        "d10_idempotency_records",
        "d10_audit_events",
    ):
        if not table_exists(conn, table):
            raise RuntimeError(
                f"D10 table '{table}' does not exist. Run alembic upgrade head "
                "or create_tables_from_schema first."
            )


def get_business_action_by_id(conn: Any, business_action_id: str) -> dict[str, Any] | None:
    _ensure_tables(conn)
    row = _conn_exec(
        conn,
        "SELECT * FROM d10_business_actions WHERE business_action_id=?",
        (business_action_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_business_action_for_task(
    conn: Any, *, organization_id: str, task_id: str
) -> dict[str, Any] | None:
    _ensure_tables(conn)
    row = _conn_exec(
        conn,
        "SELECT * FROM d10_business_actions WHERE organization_id=? AND task_id=?",
        (organization_id, task_id),
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_outbox_for_action(conn: Any, business_action_id: str) -> dict[str, Any] | None:
    _ensure_tables(conn)
    row = _conn_exec(
        conn,
        "SELECT * FROM d10_outbox_events WHERE business_action_id=?",
        (business_action_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_audit_for_action(conn: Any, business_action_id: str) -> list[dict[str, Any]]:
    _ensure_tables(conn)
    rows = _conn_exec(
        conn,
        "SELECT * FROM d10_audit_events WHERE entity_type='business_action' "
        "AND entity_id=? ORDER BY created_at ASC, audit_id ASC",
        (business_action_id,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _load_task_case(conn: Any, *, organization_id: str, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    task_row = _conn_exec(
        conn,
        "SELECT * FROM d9_action_case_tasks WHERE task_id=?",
        (task_id,),
    ).fetchone()
    if not task_row:
        raise D10NotFoundError(f"Task {task_id} not found")
    task = _row_to_dict(task_row)
    if task.get("organization_id") != organization_id:
        raise D10StateError("Task organization_id does not match submission organization_id")

    if task.get("status") in {"WAITING", "DONE", "CANCELLED"}:
        raise D10StateError(
            f"Task {task_id} is in status {task.get('status')}; BusinessAction submission "
            "requires an actionable Task (TODO or IN_PROGRESS)."
        )

    case_row = _conn_exec(
        conn,
        "SELECT * FROM action_cases WHERE action_case_id=?",
        (task["action_case_id"],),
    ).fetchone()
    if not case_row:
        raise D10NotFoundError(f"Action Case {task['action_case_id']} not found")
    case = _row_to_dict(case_row)
    if case.get("organization_id") != organization_id:
        raise D10StateError("Action Case organization_id does not match submission organization_id")
    if case.get("lifecycle_status") == "CLOSED" or case.get("stage") == "CLOSED":
        raise D10StateError(
            f"Action Case {case['action_case_id']} is CLOSED; no new BusinessAction is allowed."
        )
    return task, case


def _request_fingerprint(submission: BusinessActionSubmission) -> dict[str, Any]:
    return {
        "organization_id": submission.organization_id,
        "task_id": submission.task_id,
        "action_type": submission.action_type,
        "target_type": submission.target_type,
        "target_id": submission.target_id,
        "payload": submission.payload,
        "actor": submission.actor,
        # request_id is transport/audit metadata and may legitimately change on
        # a network retry using the same idempotency key. It is intentionally
        # excluded from semantic request equality.
        "source": submission.source,
        "reason": submission.reason,
    }


def _effect_fingerprint(
    *, organization_id: str, task_id: str, action_type: str, target_type: str,
    target_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    # Task is included intentionally: identical payloads from two distinct Tasks
    # are two distinct business effects in D10 V1.
    return {
        "organization_id": organization_id,
        "task_id": task_id,
        "action_type": action_type,
        "target_type": target_type,
        "target_id": target_id,
        "payload": payload,
    }


def build_business_action_plan(
    conn: Any, submission: BusinessActionSubmission
) -> BusinessActionPlan:
    """Validate a submission and build its deterministic, side-effect-free plan."""
    _ensure_tables(conn)

    org = _require_text("organization_id", submission.organization_id)
    task_id = _require_text("task_id", submission.task_id)
    action_type = _require_text("action_type", submission.action_type).upper()
    target_type = _require_text("target_type", submission.target_type).upper()
    target_id = _require_text("target_id", submission.target_id)
    idem = _require_text("idempotency_key", submission.idempotency_key)
    actor = _require_text("actor", submission.actor)
    request_id = _require_text("request_id", submission.request_id)
    source = _require_text("source", submission.source)

    if not isinstance(submission.payload, dict):
        raise D10StateError("payload must be a JSON object")
    _canonical_json(submission.payload)  # serialization validation
    try:
        validate_action_dates(action_type, submission.payload)
    except BusinessValueValidationError as exc:
        raise D10StateError(str(exc)) from exc

    task, case = _load_task_case(conn, organization_id=org, task_id=task_id)

    normalized = BusinessActionSubmission(
        organization_id=org,
        task_id=task_id,
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        payload=submission.payload,
        idempotency_key=idem,
        actor=actor,
        request_id=request_id,
        source=source,
        reason=submission.reason,
    )
    request_hash = _sha256(_request_fingerprint(normalized))
    effect_hash = _sha256(
        _effect_fingerprint(
            organization_id=org,
            task_id=task_id,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            payload=submission.payload,
        )
    )

    return BusinessActionPlan(
        organization_id=org,
        action_case_id=case["action_case_id"],
        task_id=task["task_id"],
        order_id=case["order_id"],
        action_type=action_type,
        target_type=target_type,
        target_id=target_id,
        payload=submission.payload,
        idempotency_key=idem,
        actor=actor,
        request_id=request_id,
        source=source,
        reason=submission.reason,
        request_hash=request_hash,
        effect_hash=effect_hash,
    )


def _get_idempotency_record(conn: Any, *, organization_id: str, idempotency_key: str) -> dict[str, Any] | None:
    row = _conn_exec(
        conn,
        "SELECT * FROM d10_idempotency_records WHERE organization_id=? AND idempotency_key=?",
        (organization_id, idempotency_key),
    ).fetchone()
    return _row_to_dict(row) if row else None


def _result_for_existing(conn: Any, record: dict[str, Any], *, request_hash: str) -> dict[str, Any]:
    if record["request_hash"] != request_hash:
        raise D10IdempotencyConflict(
            "The same idempotency_key was reused for a different BusinessAction request."
        )
    action = get_business_action_by_id(conn, record["business_action_id"])
    if not action:
        raise D10StateError(
            "Idempotency record exists but its BusinessAction is missing; database invariant violated."
        )
    outbox = get_outbox_for_action(conn, action["business_action_id"])
    if not outbox:
        raise D10StateError(
            "BusinessAction exists but its Outbox event is missing; database invariant violated."
        )
    return {
        "status": action["status"],
        "business_action_id": action["business_action_id"],
        "outbox_event_id": outbox["event_id"],
        "idempotency_key": action["idempotency_key"],
        "request_hash": action["request_hash"],
        "effect_hash": action["effect_hash"],
        "replayed": True,
        "external_effect_executed": False,
    }


def submit_business_action(
    conn: Any,
    submission: BusinessActionSubmission,
    *,
    failure_injector: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Own one short Unit of Work: idempotency + action + outbox + audit.

    `failure_injector(stage)` exists only for deterministic rollback tests. It
    may raise at `after_idempotency_reservation`, `after_action_insert`,
    `after_outbox_insert`, or `after_audit_insert`.

    This function commits on success and rolls back on failure. It is intended
    as the top-level D10 command boundary, not a nested helper inside another
    long-running transaction.
    """
    plan = build_business_action_plan(conn, submission)

    existing = _get_idempotency_record(
        conn,
        organization_id=plan.organization_id,
        idempotency_key=plan.idempotency_key,
    )
    if existing:
        return _result_for_existing(conn, existing, request_hash=plan.request_hash)

    # V1 product boundary: one Task -> zero/one primary BusinessAction.
    existing_task_action = get_business_action_for_task(
        conn, organization_id=plan.organization_id, task_id=plan.task_id
    )
    if existing_task_action:
        # Concurrent identical requests can observe the task-unique action after
        # the first request commits even if an earlier idempotency lookup was
        # made before that commit. Treat the action row itself as a second
        # convergence boundary for the SAME logical request.
        if existing_task_action.get("idempotency_key") == plan.idempotency_key:
            if existing_task_action.get("request_hash") != plan.request_hash:
                raise D10IdempotencyConflict(
                    "The same idempotency_key was reused for a different BusinessAction request."
                )
            outbox = get_outbox_for_action(conn, existing_task_action["business_action_id"])
            if not outbox:
                raise D10StateError(
                    "BusinessAction exists but its Outbox event is missing; database invariant violated."
                )
            return {
                "status": existing_task_action["status"],
                "business_action_id": existing_task_action["business_action_id"],
                "outbox_event_id": outbox["event_id"],
                "idempotency_key": existing_task_action["idempotency_key"],
                "request_hash": existing_task_action["request_hash"],
                "effect_hash": existing_task_action["effect_hash"],
                "replayed": True,
                "external_effect_executed": False,
            }
        raise D10TaskActionConflict(
            f"Task {plan.task_id} already has BusinessAction "
            f"{existing_task_action['business_action_id']}. Independent effects must use separate Tasks."
        )

    action_id = _new_id("BA")
    outbox_id = _new_id("OB")
    audit_id = _new_id("AUD")
    now = _now_iso()
    stage = "start"

    result = {
        "status": ACTION_STATUS_ACCEPTED,
        "business_action_id": action_id,
        "outbox_event_id": outbox_id,
        "idempotency_key": plan.idempotency_key,
        "request_hash": plan.request_hash,
        "effect_hash": plan.effect_hash,
        "replayed": False,
        # This is deliberately false in D10. PENDING Outbox is evidence of a
        # durable request, not evidence that ERP/CRM/email already succeeded.
        "external_effect_executed": False,
    }

    try:
        stage = "reserve_idempotency"
        _conn_exec(
            conn,
            """INSERT INTO d10_idempotency_records
               (organization_id,idempotency_key,request_hash,business_action_id,result_json,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                plan.organization_id,
                plan.idempotency_key,
                plan.request_hash,
                action_id,
                _canonical_json(result),
                now,
            ),
        )
        if failure_injector:
            failure_injector("after_idempotency_reservation")

        stage = "insert_business_action"
        _conn_exec(
            conn,
            """INSERT INTO d10_business_actions
               (business_action_id,organization_id,action_case_id,task_id,order_id,
                action_type,target_type,target_id,payload_json,request_id,idempotency_key,
                request_hash,effect_hash,status,actor,source,reason,policy_version,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                action_id,
                plan.organization_id,
                plan.action_case_id,
                plan.task_id,
                plan.order_id,
                plan.action_type,
                plan.target_type,
                plan.target_id,
                _canonical_json(plan.payload),
                plan.request_id,
                plan.idempotency_key,
                plan.request_hash,
                plan.effect_hash,
                ACTION_STATUS_ACCEPTED,
                plan.actor,
                plan.source,
                plan.reason,
                plan.policy_version,
                now,
                now,
            ),
        )
        if failure_injector:
            failure_injector("after_action_insert")

        stage = "insert_outbox"
        outbox_payload = {
            "schema_version": "1",
            "producer": "floworder.d10_business_action",
            "policy_version": plan.policy_version,
            "business_action_id": action_id,
            "organization_id": plan.organization_id,
            "action_case_id": plan.action_case_id,
            "task_id": plan.task_id,
            "order_id": plan.order_id,
            "action_type": plan.action_type,
            "target_type": plan.target_type,
            "target_id": plan.target_id,
            "payload": plan.payload,
            "effect_hash": plan.effect_hash,
        }
        _conn_exec(
            conn,
            """INSERT INTO d10_outbox_events
               (event_id,organization_id,business_action_id,event_type,payload_json,dedupe_key,
                status,attempt_count,next_attempt_at,lease_owner,lease_until,published_at,last_error,
                created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                outbox_id,
                plan.organization_id,
                action_id,
                "BUSINESS_ACTION_REQUESTED",
                _canonical_json(outbox_payload),
                f"business-action:{action_id}",
                OUTBOX_STATUS_PENDING,
                0,
                None,
                None,
                None,
                None,
                None,
                now,
                now,
            ),
        )
        if failure_injector:
            failure_injector("after_outbox_insert")

        stage = "insert_audit"
        _conn_exec(
            conn,
            """INSERT INTO d10_audit_events
               (audit_id,organization_id,actor,request_id,entity_type,entity_id,event_type,
                before_json,after_json,reason,source,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                audit_id,
                plan.organization_id,
                plan.actor,
                plan.request_id,
                "business_action",
                action_id,
                "BUSINESS_ACTION_ACCEPTED",
                _canonical_json({}),
                _canonical_json(
                    {
                        "status": ACTION_STATUS_ACCEPTED,
                        "action_case_id": plan.action_case_id,
                        "task_id": plan.task_id,
                        "order_id": plan.order_id,
                        "action_type": plan.action_type,
                        "target_type": plan.target_type,
                        "target_id": plan.target_id,
                        "effect_hash": plan.effect_hash,
                        "outbox_event_id": outbox_id,
                    }
                ),
                plan.reason,
                plan.source,
                now,
            ),
        )
        if failure_injector:
            failure_injector("after_audit_insert")

        stage = "commit"
        conn.commit()
        return result

    except (D10IdempotencyConflict, D10TaskActionConflict):
        conn.rollback()
        raise
    except (IntegrityError, sqlite3.IntegrityError) as exc:
        # A concurrent request can win the unique idempotency reservation or
        # one-Task/one-action constraint after our pre-check. Roll back first,
        # then reconcile with the committed winner.
        conn.rollback()
        winner = _get_idempotency_record(
            conn,
            organization_id=plan.organization_id,
            idempotency_key=plan.idempotency_key,
        )
        if winner:
            return _result_for_existing(conn, winner, request_hash=plan.request_hash)
        task_winner = get_business_action_for_task(
            conn, organization_id=plan.organization_id, task_id=plan.task_id
        )
        if task_winner:
            if task_winner.get("idempotency_key") == plan.idempotency_key:
                if task_winner.get("request_hash") != plan.request_hash:
                    raise D10IdempotencyConflict(
                        "The same idempotency_key was reused for a different BusinessAction request."
                    ) from exc
                outbox = get_outbox_for_action(conn, task_winner["business_action_id"])
                if not outbox:
                    raise D10StateError(
                        "BusinessAction exists but its Outbox event is missing; database invariant violated."
                    ) from exc
                return {
                    "status": task_winner["status"],
                    "business_action_id": task_winner["business_action_id"],
                    "outbox_event_id": outbox["event_id"],
                    "idempotency_key": task_winner["idempotency_key"],
                    "request_hash": task_winner["request_hash"],
                    "effect_hash": task_winner["effect_hash"],
                    "replayed": True,
                    "external_effect_executed": False,
                }
            raise D10TaskActionConflict(
                f"Task {plan.task_id} already has BusinessAction "
                f"{task_winner['business_action_id']}."
            ) from exc
        raise D10SubmissionError(
            f"BusinessAction submission failed at {stage}; transaction rolled back.",
            stage=stage,
            cause=exc,
        ) from exc
    except Exception as exc:
        conn.rollback()
        raise D10SubmissionError(
            f"BusinessAction submission failed at {stage}; transaction rolled back.",
            stage=stage,
            cause=exc,
        ) from exc


def serialize_plan(plan: BusinessActionPlan) -> dict[str, Any]:
    """Convenience helper for review evidence / contract snapshots."""
    return asdict(plan)
