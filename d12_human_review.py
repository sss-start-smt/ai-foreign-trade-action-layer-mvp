"""
FlowOrder D12: Human Review / Approval Gate
==========================================

D12 sits strictly BEFORE the frozen D10 BusinessAction submission boundary:

    Task -> D12 policy/review -> D10 BusinessAction -> PENDING Outbox

D12 does not execute ERP/CRM/email side effects. It decides whether a requested
BusinessAction may enter D10, binds the human decision to an immutable action
shape + current business state, and prevents stale/replayed approvals.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import d10_business_action as d10
from auth import CurrentIdentity
from d8_action_case import _conn_exec, _row_to_dict
from business_value_validation import BusinessValueValidationError, validate_action_dates

CN_TZ = timezone(timedelta(hours=8))
D12_POLICY_VERSION = "D12_HUMAN_REVIEW_V1"
DEFAULT_REVIEW_TTL_HOURS = 24

REQUIREMENT_OPERATOR = "OPERATOR_CONFIRM"
REQUIREMENT_MANAGER = "MANAGER_APPROVAL"
REQUIREMENT_FORBIDDEN = "FORBIDDEN"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_STALE = "STALE"
STATUS_CONSUMED = "CONSUMED"

# V1 deliberately starts narrow. Unknown effectful action types are denied.
ACTION_POLICY: dict[str, str] = {
    # Low-risk facts / internal follow-up actions.
    "RECORD_CONTACT": REQUIREMENT_OPERATOR,
    "SET_WAITING": REQUIREMENT_OPERATOR,
    "UPDATE_INTERNAL_PLAN": REQUIREMENT_OPERATOR,
    "RECORD_SUPPLIER_COMMITMENT": REQUIREMENT_OPERATOR,
    "LINK_MESSAGE_ORDER": REQUIREMENT_OPERATOR,
    # Changes to formal customer/company commitments.
    "UPDATE_EXPECTED_DELIVERY_DATE": REQUIREMENT_MANAGER,
    "UPDATE_CUSTOMER_COMMITMENT": REQUIREMENT_MANAGER,
    "ACCEPT_DELAY": REQUIREMENT_MANAGER,
    "HIGH_RISK_OVERRIDE": REQUIREMENT_MANAGER,
    # Not implemented in D12 V1.
    "SEND_MESSAGE": REQUIREMENT_FORBIDDEN,
    "ERP_WRITE_GENERIC": REQUIREMENT_FORBIDDEN,
}


class D12Error(Exception):
    pass


class D12NotFoundError(D12Error):
    pass


class D12ForbiddenError(D12Error):
    pass


class D12StateError(D12Error):
    pass


class D12ConflictError(D12Error):
    pass


class D12StaleReview(D12Error):
    pass


@dataclass(frozen=True)
class ReviewRequest:
    submission: d10.BusinessActionSubmission
    expires_in_hours: int = DEFAULT_REVIEW_TTL_HOURS


def _now() -> datetime:
    return datetime.now(CN_TZ)


def _now_iso() -> str:
    return _now().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _canonical_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise D12StateError(f"payload must be JSON-serializable: {exc}") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def classify_action(action_type: str) -> str:
    normalized = str(action_type or "").strip().upper()
    return ACTION_POLICY.get(normalized, REQUIREMENT_FORBIDDEN)


def _effect_binding(submission: d10.BusinessActionSubmission) -> dict[str, Any]:
    return {
        "organization_id": submission.organization_id,
        "task_id": submission.task_id,
        "action_type": submission.action_type.upper(),
        "target_type": submission.target_type.upper(),
        "target_id": submission.target_id,
        "payload": submission.payload,
    }


def payload_hash(submission: d10.BusinessActionSubmission) -> str:
    """Hash the full intended effect, not only the nested payload object."""
    return _sha256(_effect_binding(submission))


def _load_bound_state(conn: Any, *, organization_id: str, task_id: str) -> dict[str, Any]:
    task_row = _conn_exec(
        conn,
        "SELECT * FROM d9_action_case_tasks WHERE task_id=? AND organization_id=?",
        (task_id, organization_id),
    ).fetchone()
    if not task_row:
        raise D12NotFoundError("Task not found")
    task = _row_to_dict(task_row)

    case_row = _conn_exec(
        conn,
        "SELECT * FROM action_cases WHERE action_case_id=? AND organization_id=?",
        (task["action_case_id"], organization_id),
    ).fetchone()
    if not case_row:
        raise D12NotFoundError("Action Case not found")
    case = _row_to_dict(case_row)

    order_row = _conn_exec(
        conn,
        "SELECT * FROM orders WHERE order_id=? AND organization_id=?",
        (case["order_id"], organization_id),
    ).fetchone()
    if not order_row:
        raise D12NotFoundError("Order not found")
    order = _row_to_dict(order_row)

    # Relevant business version. Orders do not yet own an integer version, so D12
    # binds to the order's last update plus the frozen D8/D9 integer versions and
    # the commitment fields that matter to this gate.
    snapshot = {
        "order_id": order.get("order_id"),
        "order_updated_at": order.get("updated_at"),
        "requested_delivery_date": order.get("requested_delivery_date"),
        "latest_supplier_commitment": order.get("latest_supplier_commitment"),
        "action_case_id": case.get("action_case_id"),
        "action_case_version": case.get("version"),
        "action_case_stage": case.get("stage"),
        "action_case_lifecycle_status": case.get("lifecycle_status"),
        "task_id": task.get("task_id"),
        "task_version": task.get("version"),
        "task_status": task.get("status"),
    }
    return {
        "order_id": order["order_id"],
        "action_case_id": case["action_case_id"],
        "snapshot": snapshot,
        "state_version": _sha256(snapshot),
    }


def _review_row(conn: Any, review_id: str) -> dict[str, Any] | None:
    row = _conn_exec(conn, "SELECT * FROM d12_human_reviews WHERE review_id=?", (review_id,)).fetchone()
    return _row_to_dict(row) if row else None


def _review_by_idempotency(conn: Any, organization_id: str, idempotency_key: str) -> dict[str, Any] | None:
    row = _conn_exec(
        conn,
        "SELECT * FROM d12_human_reviews WHERE organization_id=? AND idempotency_key=?",
        (organization_id, idempotency_key),
    ).fetchone()
    return _row_to_dict(row) if row else None


def request_review(conn: Any, request: ReviewRequest, *, identity: CurrentIdentity) -> dict[str, Any]:
    submission = request.submission
    requirement = classify_action(submission.action_type)
    if requirement == REQUIREMENT_FORBIDDEN:
        raise D12ForbiddenError(f"action_type {submission.action_type!r} is not requestable in D12 V1")

    if identity.organization_id != submission.organization_id:
        raise D12ForbiddenError("submission organization must match authenticated organization")
    if identity.user_id != submission.actor:
        raise D12ForbiddenError("submission actor must match authenticated user")
    if identity.role not in {"operator", "manager"}:
        raise D12ForbiddenError("unsupported requester role")
    if request.expires_in_hours <= 0 or request.expires_in_hours > 168:
        raise D12StateError("expires_in_hours must be between 1 and 168")

    try:
        validate_action_dates(submission.action_type, submission.payload)
    except BusinessValueValidationError as exc:
        raise D12StateError(str(exc)) from exc

    # Reuse D10's frozen validation as a read-only preflight: actionable task,
    # matching org, open case, JSON shape, and deterministic request plan.
    plan = d10.build_business_action_plan(conn, submission)
    effect_hash = payload_hash(submission)
    state = _load_bound_state(conn, organization_id=plan.organization_id, task_id=plan.task_id)

    existing = _review_by_idempotency(conn, plan.organization_id, plan.idempotency_key)
    if existing:
        if existing.get("payload_hash") != effect_hash:
            raise D12ConflictError("idempotency_key was reused for a different review payload")
        return {**existing, "replayed": True}

    review_id = _new_id("HR")
    created_at = _now()
    expires_at = created_at + timedelta(hours=request.expires_in_hours)
    _conn_exec(
        conn,
        """INSERT INTO d12_human_reviews
           (review_id,organization_id,order_id,action_case_id,task_id,action_type,target_type,target_id,
            payload_json,payload_hash,state_version,state_snapshot_json,requested_by,requester_role,
            required_review,idempotency_key,d10_request_id,reason,status,decision,reviewed_by,reviewer_role,
            created_at,reviewed_at,expires_at,consumed_at,business_action_id,result_json,policy_version,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            review_id,
            plan.organization_id,
            plan.order_id,
            plan.action_case_id,
            plan.task_id,
            plan.action_type,
            plan.target_type,
            plan.target_id,
            _canonical_json(plan.payload),
            effect_hash,
            state["state_version"],
            _canonical_json(state["snapshot"]),
            identity.user_id,
            identity.role,
            requirement,
            plan.idempotency_key,
            plan.request_id,
            plan.reason,
            STATUS_PENDING,
            None,
            None,
            None,
            created_at.isoformat(timespec="seconds"),
            None,
            expires_at.isoformat(timespec="seconds"),
            None,
            None,
            None,
            D12_POLICY_VERSION,
            created_at.isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    row = _review_row(conn, review_id)
    return {**(row or {}), "replayed": False}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed


def _mark_stale(conn: Any, review_id: str, reason: str) -> None:
    now = _now_iso()
    _conn_exec(
        conn,
        "UPDATE d12_human_reviews SET status=?,decision=?,updated_at=? WHERE review_id=?",
        (STATUS_STALE, reason, now, review_id),
    )
    conn.commit()


def _assert_review_fresh(conn: Any, review: dict[str, Any]) -> None:
    expires_at = _parse_iso(review.get("expires_at"))
    if expires_at and _now() >= expires_at:
        _mark_stale(conn, review["review_id"], "EXPIRED")
        raise D12StaleReview("review expired")

    state = _load_bound_state(
        conn,
        organization_id=review["organization_id"],
        task_id=review["task_id"],
    )
    if state["state_version"] != review.get("state_version"):
        _mark_stale(conn, review["review_id"], "STATE_CHANGED")
        raise D12StaleReview("business state changed after review request")


def decide_review(
    conn: Any,
    *,
    review_id: str,
    identity: CurrentIdentity,
    decision: str,
    note: str | None = None,
) -> dict[str, Any]:
    review = _review_row(conn, review_id)
    if not review or review.get("organization_id") != identity.organization_id:
        # Deliberately indistinguishable from not-found for cross-org probing.
        raise D12NotFoundError("review not found")

    normalized = str(decision or "").strip().upper()
    if normalized not in {"APPROVE", "REJECT"}:
        raise D12StateError("decision must be APPROVE or REJECT")

    if review["status"] != STATUS_PENDING:
        return {**review, "duplicate_skipped": True}

    requirement = review["required_review"]
    if requirement == REQUIREMENT_MANAGER and not identity.is_manager():
        raise D12ForbiddenError("manager approval required")
    if requirement == REQUIREMENT_OPERATOR:
        if not identity.is_manager() and identity.user_id != review["requested_by"]:
            raise D12ForbiddenError("operator may only confirm their own request")

    _assert_review_fresh(conn, review)

    now = _now_iso()
    new_status = STATUS_APPROVED if normalized == "APPROVE" else STATUS_REJECTED
    stored_decision = normalized if not note else f"{normalized}:{note}"
    _conn_exec(
        conn,
        """UPDATE d12_human_reviews
           SET status=?,decision=?,reviewed_by=?,reviewer_role=?,reviewed_at=?,updated_at=?
           WHERE review_id=?""",
        (new_status, stored_decision, identity.user_id, identity.role, now, now, review_id),
    )
    conn.commit()
    return _review_row(conn, review_id) or {}


def _submission_from_review(review: dict[str, Any]) -> d10.BusinessActionSubmission:
    return d10.BusinessActionSubmission(
        organization_id=review["organization_id"],
        task_id=review["task_id"],
        action_type=review["action_type"],
        target_type=review["target_type"],
        target_id=review["target_id"],
        payload=json.loads(review["payload_json"] or "{}"),
        idempotency_key=review["idempotency_key"],
        actor=review["requested_by"],
        request_id=review["d10_request_id"],
        source="D12_HUMAN_REVIEW",
        reason=review.get("reason"),
    )


def submit_after_review(
    conn: Any,
    *,
    review_id: str,
    identity: CurrentIdentity,
    submission_override: d10.BusinessActionSubmission | None = None,
) -> dict[str, Any]:
    review = _review_row(conn, review_id)
    if not review or review.get("organization_id") != identity.organization_id:
        raise D12NotFoundError("review not found")

    if review["status"] == STATUS_CONSUMED:
        stored = json.loads(review.get("result_json") or "{}")
        return {**stored, "review_id": review_id, "review_replayed": True}
    if review["status"] == STATUS_REJECTED:
        raise D12ForbiddenError("rejected review cannot be submitted; create a new request")
    if review["status"] == STATUS_STALE:
        raise D12StaleReview("stale review cannot be submitted")
    if review["status"] != STATUS_APPROVED:
        raise D12StateError("review must be approved before BusinessAction submission")

    _assert_review_fresh(conn, review)

    submission = submission_override or _submission_from_review(review)
    if submission.organization_id != review["organization_id"]:
        raise D12ForbiddenError("submission organization does not match approved review")
    if payload_hash(submission) != review["payload_hash"]:
        raise D12ConflictError("approved payload does not match submission payload")
    if submission.actor != review["requested_by"]:
        raise D12ForbiddenError("submission actor does not match review requester")

    result = d10.submit_business_action(conn, submission)

    now = _now_iso()
    _conn_exec(
        conn,
        """UPDATE d12_human_reviews SET status=?,consumed_at=?,business_action_id=?,result_json=?,updated_at=?
           WHERE review_id=?""",
        (
            STATUS_CONSUMED,
            now,
            result["business_action_id"],
            _canonical_json(result),
            now,
            review_id,
        ),
    )
    conn.commit()
    return {**result, "review_id": review_id, "review_replayed": False}


def get_review(conn: Any, *, review_id: str, identity: CurrentIdentity) -> dict[str, Any]:
    review = _review_row(conn, review_id)
    if not review or review.get("organization_id") != identity.organization_id:
        raise D12NotFoundError("review not found")
    return review


def list_reviews(
    conn: Any,
    *,
    identity: CurrentIdentity,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    params: list[Any] = [identity.organization_id]
    where = "r.organization_id=?"
    if status:
        where += " AND r.status=?"
        params.append(status.upper())
    # Operators see their own requests; managers see the organization's queue.
    if not identity.is_manager():
        where += " AND r.requested_by=?"
        params.append(identity.user_id)
    params.append(max(1, min(int(limit), 200)))
    rows = _conn_exec(
        conn,
        f"""SELECT r.*,o.order_no,o.customer_name
            FROM d12_human_reviews r
            LEFT JOIN orders o ON o.order_id=r.order_id AND o.organization_id=r.organization_id
            WHERE {where} ORDER BY r.created_at DESC LIMIT ?""",
        tuple(params),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]
