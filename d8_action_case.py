"""
FlowOrder D8: Action Case — Risk/Action Judgment → Deterministic Action Intent
============================================================================

This module implements:
  1. Deterministic action_intent_key derivation from D7 pipeline output
  2. Action case reconciliation (create vs reuse)
  3. Finite State Machine for action case lifecycle
  4. Organization/user-scoped CRUD operations
  5. Root-cause suppression for DELIVERY_RISK
  6. Authorization boundary for transitions and reconciliation

Core Principles:
  - action_case != task: case is a business goal/question; task is an execution object
  - Same business intent repeated will NOT create duplicate ACTIVE action_cases
  - DELIVERY_RISK is suppressed when a more specific root cause already exists
  - FSM transitions are strictly validated; illegal transitions are rejected
  - Risk disappearance does NOT automatically close the case
  - organization_id is enforced on all read/write operations
  - transition_action_case is itself the authorization boundary
  - Reconcile uses identity.org_id as the ONLY write scope
  - Observation is at (order_id, action_intent_key) granularity
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

CN_TZ = timezone(timedelta(hours=8))

D8_POLICY_VERSION = "D8_ACTION_CASE_V1"


# ---------------------------------------------------------------------------
# Custom Errors
# ---------------------------------------------------------------------------


class ActionCaseAuthError(PermissionError):
    """Raised when authorization check fails for action_case operations."""

    def __init__(self, message: str, user_id: str | None = None, org_id: str | None = None) -> None:
        super().__init__(message)
        self.user_id = user_id
        self.org_id = org_id


class ActionCaseVersionConflict(Exception):
    """Raised when optimistic concurrency check fails (CAS miss)."""

    def __init__(self, action_case_id: str, expected_version: int) -> None:
        super().__init__(
            f"Version conflict on action_case {action_case_id}: "
            f"expected version {expected_version}, row already modified"
        )
        self.action_case_id = action_case_id
        self.expected_version = expected_version


class ReconcileAuthError(Exception):
    """Raised when reconciliation payload fails organization validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class OrderNotAuthorizedError(Exception):
    """Raised when an order fails non-critical authorization (skip, don't reject).

    This is used when an operator tries to reconcile an order they don't own,
    or when the order has no owner assigned. The order should be skipped
    rather than aborting the entire reconciliation.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)

# ---------------------------------------------------------------------------
# 1. Intent Mapping (Risk Type → Intent Type)
# ---------------------------------------------------------------------------

_RISK_TO_INTENT: dict[str, str] = {
    "LOGISTICS_EXCEPTION": "LOGISTICS_RECOVERY",
    "QUALITY_BLOCKING": "QUALITY_RECOVERY",
    "SUPPLIER_COMMITMENT_OVERDUE": "SUPPLIER_FOLLOWUP",
    "CUSTOMER_CONFIRMATION_BLOCKING": "CUSTOMER_CONFIRMATION",
    "SOURCE_CONFLICT": "FACT_CONFLICT_RESOLUTION",
    "INFORMATION_GAP": "INFORMATION_COMPLETION",
    "OWNER_MISSING": "OWNER_ASSIGNMENT",
    "DELIVERY_RISK": "DELIVERY_RECOVERY",
}

# Intents that start in NEEDS_JUDGMENT stage
_NEEDS_JUDGMENT_INTENTS = frozenset({
    "FACT_CONFLICT_RESOLUTION",
    "INFORMATION_COMPLETION",
})

# Valid FSM stages
FSM_STAGES = frozenset({
    "NEEDS_JUDGMENT",
    "READY_FOR_ACTION",
    "IN_PROGRESS",
    "WAITING_RESULT",
    "RESUMED_OR_ESCALATED",
    "CLOSED",
})

# Valid close reasons
CLOSE_REASONS = frozenset({
    "RESOLVED",
    "NO_LONGER_NEEDED",
    "DISMISSED",
    "DUPLICATE",
    "MERGED",
    "SUPERSEDED",
    "CANCELLED",
    "INVALIDATED",
})

# Valid FSM transitions: from_stage → set of allowed next stages
_FSM_TRANSITIONS: dict[str, set[str]] = {
    "NEEDS_JUDGMENT": {"READY_FOR_ACTION", "CLOSED"},
    "READY_FOR_ACTION": {"IN_PROGRESS", "CLOSED"},
    "IN_PROGRESS": {"WAITING_RESULT", "RESUMED_OR_ESCALATED", "CLOSED"},
    "WAITING_RESULT": {"RESUMED_OR_ESCALATED", "CLOSED"},
    "RESUMED_OR_ESCALATED": {"READY_FOR_ACTION", "IN_PROGRESS", "WAITING_RESULT", "CLOSED"},
    "CLOSED": set(),
}

# Root cause suppression: these risk types suppress DELIVERY_RISK
_DELIVERY_SUPPRESSION_ROOT_CAUSES = frozenset({
    "LOGISTICS_EXCEPTION",
    "QUALITY_BLOCKING",
    "SUPPLIER_COMMITMENT_OVERDUE",
    "CUSTOMER_CONFIRMATION_BLOCKING",
})

# ---------------------------------------------------------------------------
# 2. Helpers
# ---------------------------------------------------------------------------


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _row_to_dict(row: Any) -> dict[str, Any]:
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    if isinstance(row, dict):
        return row
    return dict(row)


def _conn_exec(conn: Any, sql: str, params: dict[str, Any] | tuple | list | None = None) -> Any:
    """Execute SQL and return a result object with rowcount support.

    For UPDATE/DELETE statements, the returned object must expose .rowcount
    to enable optimistic concurrency verification.
    """
    if hasattr(conn, "_conn"):
        result = conn.execute(sql, params)
        # Ensure rowcount is accessible
        if not hasattr(result, "rowcount") and hasattr(result, "_rowcount"):
            result.rowcount = result._rowcount
        return result
    if params is None:
        result = conn.execute(text(sql))
        if not hasattr(result, "rowcount") and hasattr(result, "_rowcount"):
            result.rowcount = result._rowcount
        return result
    if isinstance(params, dict):
        result = conn.execute(text(sql), params)
        if not hasattr(result, "rowcount") and hasattr(result, "_rowcount"):
            result.rowcount = result._rowcount
        return result
    pos_dict = {f"p{i}": v for i, v in enumerate(params)}
    counter = [0]

    def _replace(_: Any) -> str:
        counter[0] += 1
        return f":p{counter[0] - 1}"

    import re
    converted_sql = re.sub(r"\?", _replace, sql)
    result = conn.execute(text(converted_sql), pos_dict)
    if not hasattr(result, "rowcount") and hasattr(result, "_rowcount"):
        result.rowcount = result._rowcount
    return result


# ---------------------------------------------------------------------------
# 3. Intent Derivation
# ---------------------------------------------------------------------------


def derive_action_intents(
    d7_item: dict[str, Any],
    *,
    organization_id: str,
) -> list[dict[str, Any]]:
    """Derive deterministic action intents from a D7 pipeline output item.

    Implements root-cause suppression: if the order already has a more
    specific root cause (LOGISTICS_EXCEPTION, SUPPLIER_COMMITMENT_OVERDUE,
    CUSTOMER_CONFIRMATION_BLOCKING), generic DELIVERY_RISK is suppressed.

    Returns a list of intent dicts, each containing:
        - action_intent_key: deterministic key like "v1:LOGISTICS_RECOVERY"
        - intent_type: the intent type string
        - intent_stage: initial FSM stage
        - risk_type: original D7 risk type
        - evidence: evidence from the risk signal
        - severity: highest severity across signals of this type
        - action_bucket: latest action bucket
        - recommended_action: merged recommended action
    """
    order_id = d7_item.get("order_id", "")
    risk_signals = d7_item.get("risk_signals") or []
    action_bucket = d7_item.get("action_bucket", "")
    recommended_action = d7_item.get("recommended_action", "")
    evidence = d7_item.get("evidence") or []
    severity = d7_item.get("severity", "MEDIUM")

    if not order_id or not risk_signals:
        return []

    risk_types = {s.get("risk_type", "") for s in risk_signals if s.get("risk_type")}

    # Root-cause suppression: check if DELIVERY_RISK should be suppressed
    delivery_suppressed = False
    suppressed_evidence: list[str] = []
    if "DELIVERY_RISK" in risk_types:
        has_root_cause = risk_types & _DELIVERY_SUPPRESSION_ROOT_CAUSES
        if has_root_cause:
            delivery_suppressed = True
            # Collect evidence from suppressed DELIVERY_RISK signals
            for sig in risk_signals:
                if sig.get("risk_type") == "DELIVERY_RISK":
                    for e in (sig.get("evidence") or []):
                        if e not in suppressed_evidence:
                            suppressed_evidence.append(e)

    # Group signals by risk type
    signals_by_type: dict[str, list[dict[str, Any]]] = {}
    for sig in risk_signals:
        rt = sig.get("risk_type", "")
        if rt:
            signals_by_type.setdefault(rt, []).append(sig)

    intents: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for rt, sigs in signals_by_type.items():
        if rt == "DELIVERY_RISK" and delivery_suppressed:
            # Keep the risk in evidence but don't create a separate DELIVERY_RECOVERY intent
            continue

        intent_type = _RISK_TO_INTENT.get(rt)
        if not intent_type:
            continue

        intent_key = f"v1:{intent_type}"
        if intent_key in seen_keys:
            continue
        seen_keys.add(intent_key)

        # Determine initial stage
        intent_stage = "NEEDS_JUDGMENT" if intent_type in _NEEDS_JUDGMENT_INTENTS else "READY_FOR_ACTION"

        # Gather evidence from all signals of this type
        intent_evidence: list[str] = []
        for sig in sigs:
            sig_ev = sig.get("evidence") or []
            for e in sig_ev:
                if e not in intent_evidence:
                    intent_evidence.append(e)

        # Include suppressed evidence (e.g., DELIVERY_RISK evidence when suppressed)
        for e in suppressed_evidence:
            if e not in intent_evidence:
                intent_evidence.append(e)

        # Determine highest severity for this intent
        sev_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        intent_severity = "LOW"
        for sig in sigs:
            sig_sev = sig.get("severity", "LOW")
            if sev_order.get(sig_sev, 0) > sev_order.get(intent_severity, 0):
                intent_severity = sig_sev

        intents.append({
            "action_intent_key": intent_key,
            "intent_type": intent_type,
            "intent_stage": intent_stage,
            "risk_type": rt,
            "evidence": intent_evidence or evidence,
            "severity": intent_severity,
            "action_bucket": action_bucket,
            "recommended_action": recommended_action,
            "order_id": order_id,
            "organization_id": organization_id,
        })

    return intents


def derive_intents_from_pipeline(
    d7_result: dict[str, Any],
    *,
    organization_id: str | None = None,
) -> list[dict[str, Any]]:
    """Derive action intents from a full D7 pipeline result.

    Processes all action items (role-aware) and returns a flat list of intents.
    """
    if not organization_id:
        scope = d7_result.get("scope") or {}
        organization_id = scope.get("organization_id", "")

    if not organization_id:
        return []

    all_items: list[dict[str, Any]] = []
    seen_order_ids: set[str] = set()

    # Collect items from all queues
    for key in ("my_action_items", "team_action_items", "unassigned_orders"):
        items = d7_result.get(key) or []
        for item in items:
            oid = item.get("order_id", "")
            if oid and oid not in seen_order_ids:
                seen_order_ids.add(oid)
                all_items.append(item)

    # Also check "items" for backward compatibility
    if not all_items:
        items = d7_result.get("items") or []
        for item in items:
            oid = item.get("order_id", "")
            if oid and oid not in seen_order_ids:
                seen_order_ids.add(oid)
                all_items.append(item)

    all_intents: list[dict[str, Any]] = []
    for item in all_items:
        item_org = str(item.get("organization_id") or organization_id)
        intents = derive_action_intents(item, organization_id=item_org)
        all_intents.extend(intents)

    return all_intents


# ---------------------------------------------------------------------------
# 4. Action Case CRUD (Database Layer)
# ---------------------------------------------------------------------------


def _ensure_table(conn: Any) -> None:
    from database import table_exists
    if not table_exists(conn, "action_cases"):
        raise RuntimeError(
            "action_cases table does not exist. "
            "Run alembic upgrade head or create_tables_from_schema first."
        )


def get_active_case(
    conn: Any,
    *,
    organization_id: str,
    order_id: str,
    action_intent_key: str,
) -> dict[str, Any] | None:
    """Get the ACTIVE action_case for a given (org, order, intent_key)."""
    _ensure_table(conn)
    row = _conn_exec(
        conn,
        "SELECT * FROM action_cases "
        "WHERE organization_id=? AND order_id=? AND action_intent_key=? AND lifecycle_status='ACTIVE' "
        "LIMIT 1",
        (organization_id, order_id, action_intent_key),
    ).fetchone()
    return _row_to_dict(row) if row else None


def get_case_by_id(
    conn: Any,
    action_case_id: str,
) -> dict[str, Any] | None:
    """Get an action_case by its ID."""
    _ensure_table(conn)
    row = _conn_exec(
        conn,
        "SELECT * FROM action_cases WHERE action_case_id=?",
        (action_case_id,),
    ).fetchone()
    return _row_to_dict(row) if row else None


def list_cases(
    conn: Any,
    *,
    organization_id: str,
    order_id: str | None = None,
    lifecycle_status: str | None = None,
    stage: str | None = None,
) -> list[dict[str, Any]]:
    """List action cases scoped to an organization.

    Supports optional filtering by order_id, lifecycle_status, and stage.
    Organization scope is ALWAYS enforced.
    """
    _ensure_table(conn)

    sql = "SELECT * FROM action_cases WHERE organization_id=?"
    params: list[Any] = [organization_id]

    if order_id:
        sql += " AND order_id=?"
        params.append(order_id)
    if lifecycle_status:
        sql += " AND lifecycle_status=?"
        params.append(lifecycle_status)
    if stage:
        sql += " AND stage=?"
        params.append(stage)

    sql += " ORDER BY last_seen_at DESC"

    rows = _conn_exec(conn, sql, tuple(params)).fetchall()
    return [_row_to_dict(r) for r in rows]


def create_action_case(
    conn: Any,
    *,
    organization_id: str,
    order_id: str,
    action_intent_key: str,
    intent_type: str,
    stage: str,
    title: str | None = None,
    latest_action_bucket: str | None = None,
    latest_severity: str | None = None,
    latest_recommended_action: str | None = None,
    latest_evidence: list[str] | None = None,
    source_policy_version: str | None = None,
) -> dict[str, Any]:
    """Create a new ACTIVE action_case.

    Relies on the partial unique index to prevent duplicate ACTIVE cases
    for the same (org, order, intent_key). If a duplicate is attempted,
    the database will raise an IntegrityError.

    Returns the newly created case dict.
    """
    _ensure_table(conn)

    now = _now_iso()
    case_id = _new_id("AC")

    evidence_json = json.dumps(latest_evidence or [], ensure_ascii=False)

    _conn_exec(
        conn,
        """INSERT INTO action_cases
           (action_case_id, organization_id, order_id, action_intent_key,
            intent_type, stage, lifecycle_status, title,
            latest_action_bucket, latest_severity, latest_recommended_action,
            latest_evidence_json, observation_status,
            first_seen_at, last_seen_at, last_reconciled_at,
            source_policy_version, version,
            created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            case_id,
            organization_id,
            order_id,
            action_intent_key,
            intent_type,
            stage,
            "ACTIVE",
            title,
            latest_action_bucket,
            latest_severity,
            latest_recommended_action,
            evidence_json,
            "OBSERVED",
            now,
            now,
            now,
            source_policy_version or D8_POLICY_VERSION,
            1,
            now,
            now,
        ),
    )

    created = get_case_by_id(conn, case_id)
    if not created:
        raise RuntimeError(f"Failed to create action_case {case_id}")
    return created


def update_action_case_reconcile(
    conn: Any,
    action_case_id: str,
    *,
    latest_action_bucket: str | None = None,
    latest_severity: str | None = None,
    latest_recommended_action: str | None = None,
    latest_evidence: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing case during reconciliation.

    Updates:
      - latest_action_bucket, latest_severity, latest_recommended_action
      - latest_evidence_json (merged)
      - last_seen_at, last_reconciled_at, updated_at
      - observation_status → OBSERVED

    Does NOT change: stage, lifecycle_status
    """
    now = _now_iso()

    # Get current case to merge evidence
    current = get_case_by_id(conn, action_case_id)
    if not current:
        raise ValueError(f"action_case {action_case_id} not found")

    # Merge evidence (deduplicate, preserve order)
    existing_evidence: list[str] = []
    try:
        raw = current.get("latest_evidence_json", "[]")
        existing_evidence = json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        existing_evidence = []

    new_evidence = latest_evidence or []
    merged = list(existing_evidence)
    for e in new_evidence:
        if e not in merged:
            merged.append(e)

    evidence_json = json.dumps(merged, ensure_ascii=False)

    _conn_exec(
        conn,
        """UPDATE action_cases
           SET latest_action_bucket=?,
               latest_severity=?,
               latest_recommended_action=?,
               latest_evidence_json=?,
               observation_status='OBSERVED',
               last_seen_at=?,
               last_reconciled_at=?,
               updated_at=?
           WHERE action_case_id=?""",
        (
            latest_action_bucket,
            latest_severity,
            latest_recommended_action,
            evidence_json,
            now,
            now,
            now,
            action_case_id,
        ),
    )

    updated = get_case_by_id(conn, action_case_id)
    if not updated:
        raise RuntimeError(f"Failed to update action_case {action_case_id}")
    return updated


def mark_cases_not_observed(
    conn: Any,
    *,
    organization_id: str,
    observed_case_keys: set[tuple[str, str]],
    scope_order_ids: set[str] | None = None,
) -> int:
    """Mark ACTIVE cases as NOT_OBSERVED if their (order_id, action_intent_key)
    was not observed in the current reconciliation cycle.

    Observation granularity is (order_id, action_intent_key), not just order_id.
    This means if ORD-1 had [CUSTOMER_CONFIRMATION, LOGISTICS_RECOVERY] in round 1,
    and only [CUSTOMER_CONFIRMATION] in round 2, then:
      - CUSTOMER_CONFIRMATION → OBSERVED
      - LOGISTICS_RECOVERY → NOT_OBSERVED

    SCOPE IS CRITICAL:
      - scope_order_ids limits which orders can be marked NOT_OBSERVED.
      - Only orders that were part of the current reconciliation's authoritative
        observation scope can be marked NOT_OBSERVED.
      - This prevents operator USER-2 from accidentally marking USER-1's cases
        as NOT_OBSERVED when USER-2 only reconciles their own orders.

    Does NOT automatically close them. Risk disappearance != business resolution.

    Returns the number of cases updated.
    """
    _ensure_table(conn)
    now = _now_iso()

    # Build the WHERE clause for NOT_OBSERVED marking
    # Only consider orders within scope_order_ids (if provided)
    not_observed_sql = """UPDATE action_cases
        SET observation_status='NOT_OBSERVED',
            updated_at=?
        WHERE organization_id=?
          AND lifecycle_status='ACTIVE'"""
    params: list[Any] = [now, organization_id]

    # Add scope_order_ids restriction
    if scope_order_ids is not None:
        if not scope_order_ids:
            # Empty scope: nothing to mark
            return 0
        scope_placeholders = ",".join(["?"] * len(scope_order_ids))
        not_observed_sql += f" AND order_id IN ({scope_placeholders})"
        params.extend(sorted(scope_order_ids))

    # Exclude observed case keys
    if observed_case_keys:
        observed_order_intents = list(observed_case_keys)
        or_clauses: list[str] = []
        for order_id, intent_key in observed_order_intents:
            or_clauses.append("(order_id=? AND action_intent_key=?)")
            params.extend([order_id, intent_key])
        not_observed_sql += f" AND NOT ({' OR '.join(or_clauses)})"

    rows = _conn_exec(conn, not_observed_sql, tuple(params))
    return rows.rowcount if hasattr(rows, "rowcount") else 0


# ---------------------------------------------------------------------------
# 5. Authorization Boundary + FSM Transition Service
# ---------------------------------------------------------------------------


def authorize_case_transition(
    conn: Any,
    action_case_id: str,
    identity: Any,
) -> dict[str, Any]:
    """Authorize a transition on an action_case.

    This is the authorization boundary. Every transition MUST pass through
    this check. Returns the case dict on success; raises on authorization
    failure.

    Rules:
      - Manager/Admin/Supervisor: can transition any case in their organization
      - Operator: can only transition cases for orders they own (order.owner == user_id)
      - owner=NULL: operator cannot transition
      - Cross-organization: always rejected

    Raises ActionCaseAuthError on any authorization failure.
    Returns the current case dict for use by the caller.
    """
    from d7_risk_engine import _extract_identity_fields

    user_id, identity_org_id, user_role = _extract_identity_fields(identity)

    if not identity_org_id:
        raise ActionCaseAuthError(
            "Cannot authorize transition: identity has no organization",
            user_id=user_id,
        )

    case = get_case_by_id(conn, action_case_id)
    if not case:
        raise ActionCaseAuthError(
            f"action_case {action_case_id} not found or not visible",
            user_id=user_id,
            org_id=identity_org_id,
        )

    case_org_id = case["organization_id"]

    # Cross-organization check: identity org_id must match case org_id
    if case_org_id != identity_org_id:
        raise ActionCaseAuthError(
            f"Cross-organization transition denied: "
            f"identity org={identity_org_id} cannot transition case org={case_org_id}",
            user_id=user_id,
            org_id=identity_org_id,
        )

    is_mgr = str(user_role or "").lower() in {"manager", "admin", "supervisor"}

    if is_mgr:
        # Manager can transition any case in their organization
        return case

    # Operator: must own the order
    order_row = _conn_exec(
        conn,
        "SELECT owner FROM orders WHERE order_id=?",
        (case["order_id"],),
    ).fetchone()

    if not order_row:
        raise ActionCaseAuthError(
            f"Order {case['order_id']} not found for permission check",
            user_id=user_id,
            org_id=identity_org_id,
        )

    owner = str(order_row["owner"] or "")
    if not owner:
        raise ActionCaseAuthError(
            f"Operator {user_id} cannot transition case: order {case['order_id']} has no owner",
            user_id=user_id,
            org_id=identity_org_id,
        )

    if owner != user_id:
        raise ActionCaseAuthError(
            f"Operator {user_id} cannot transition case for order owned by {owner}",
            user_id=user_id,
            org_id=identity_org_id,
        )

    return case


class ActionCaseFSMError(Exception):
    """Raised when an illegal FSM transition is attempted."""

    def __init__(self, message: str, current_stage: str, target_stage: str) -> None:
        super().__init__(message)
        self.current_stage = current_stage
        self.target_stage = target_stage


def validate_transition(from_stage: str, to_stage: str) -> bool:
    """Check if a transition from from_stage to to_stage is legal."""
    if from_stage == "CLOSED":
        return False
    allowed = _FSM_TRANSITIONS.get(from_stage, set())
    return to_stage in allowed


def transition_action_case(
    conn: Any,
    action_case_id: str,
    target_stage: str,
    *,
    close_reason: str | None = None,
    identity: Any | None = None,
    _expected_version: int | None = None,
) -> dict[str, Any]:
    """Transition an action_case to a new stage.

    THIS IS THE AUTHORIZATION BOUNDARY. Permission is enforced here,
    not delegated to the caller.

    Validates:
      1. Identity authorization (organization isolation + role-based rules)
      2. FSM transition legality
      3. Close reason validity
      4. Optimistic concurrency (version check)
      5. Row count verification (CAS miss detection)

    Args:
        _expected_version: Internal/test-only parameter. When provided,
            overrides the version read from DB for the CAS check. This
            allows simulation of stale-read race conditions.

    Raises:
      ActionCaseAuthError: If authorization fails
      ActionCaseFSMError: If transition is illegal
      ActionCaseVersionConflict: If optimistic concurrency check fails
      ValueError: If close_reason is required but missing/invalid
    """
    _ensure_table(conn)

    if not identity:
        raise ActionCaseAuthError(
            "Identity is required for transition",
        )

    # Step 1: Authorization boundary — must pass before any DB mutation
    case = authorize_case_transition(conn, action_case_id, identity)

    if target_stage not in FSM_STAGES:
        raise ActionCaseFSMError(
            f"Unknown target stage: {target_stage}",
            current_stage=case["stage"],
            target_stage=target_stage,
        )

    current_stage = case["stage"]
    current_version = _expected_version if _expected_version is not None else int(case.get("version", 1))

    # Step 2: FSM validation
    if not validate_transition(current_stage, target_stage):
        raise ActionCaseFSMError(
            f"Illegal FSM transition: {current_stage} → {target_stage}",
            current_stage=current_stage,
            target_stage=target_stage,
        )

    # Step 3: Close reason validation
    if target_stage == "CLOSED":
        if not close_reason:
            raise ValueError(
                "close_reason is required when transitioning to CLOSED. "
                f"Valid reasons: {sorted(CLOSE_REASONS)}"
            )
        if close_reason not in CLOSE_REASONS:
            raise ValueError(
                f"Invalid close_reason: {close_reason}. "
                f"Valid reasons: {sorted(CLOSE_REASONS)}"
            )

    # Step 4: Optimistic concurrency update with rowcount check
    now = _now_iso()
    new_version = current_version + 1

    if target_stage == "CLOSED":
        rows = _conn_exec(
            conn,
            """UPDATE action_cases
               SET stage=?,
                   lifecycle_status='CLOSED',
                   close_reason=?,
                   closed_at=?,
                   version=?,
                   updated_at=?
               WHERE action_case_id=? AND version=?""",
            (
                target_stage,
                close_reason,
                now,
                new_version,
                now,
                action_case_id,
                current_version,
            ),
        )
    else:
        rows = _conn_exec(
            conn,
            """UPDATE action_cases
               SET stage=?,
                   version=?,
                   updated_at=?
               WHERE action_case_id=? AND version=?""",
            (
                target_stage,
                new_version,
                now,
                action_case_id,
                current_version,
            ),
        )

    # Step 5: Row count verification (CAS miss detection)
    rowcount = rows.rowcount if hasattr(rows, "rowcount") else rows
    if rowcount == 0:
        raise ActionCaseVersionConflict(
            action_case_id=action_case_id,
            expected_version=current_version,
        )

    updated = get_case_by_id(conn, action_case_id)
    if not updated:
        raise RuntimeError(f"Failed to transition action_case {action_case_id}")

    # Verify the update actually happened
    if target_stage == "CLOSED":
        if updated["lifecycle_status"] != "CLOSED":
            raise RuntimeError(
                f"Failed to close action_case {action_case_id}: lifecycle_status not updated"
            )
        if updated["close_reason"] != close_reason:
            raise RuntimeError(
                f"Failed to set close_reason on action_case {action_case_id}"
            )

    return updated


# ---------------------------------------------------------------------------
# 6. Reconciliation Service
# ---------------------------------------------------------------------------


def _validate_order_authority(
    conn: Any,
    order_id: str,
    identity: Any,
    claimed_org_id: str | None = None,
) -> dict[str, Any] | None:
    """Validate an order's authority against the database.

    The database IS the authority source for organization_id and owner.
    Payload fields are only used as consistency validation, NOT as the
    primary source of truth.

    Returns the order row dict on success.

    Raises ReconcileAuthError (critical — abort reconciliation):
      - Order not found in database
      - DB organization_id doesn't match identity organization_id
      - claimed_org_id (payload) doesn't match DB org (tampering)

    Raises OrderNotAuthorizedError (non-critical — skip this order):
      - Operator doesn't own the order
      - Operator tries to access order with no owner
    """
    from d7_risk_engine import _extract_identity_fields

    user_id, identity_org_id, user_role = _extract_identity_fields(identity)

    order_row = _conn_exec(
        conn,
        "SELECT order_id, organization_id, owner, status FROM orders WHERE order_id=?",
        (order_id,),
    ).fetchone()

    if not order_row:
        raise ReconcileAuthError(
            f"Order {order_id} not found in database. "
            f"Cannot reconcile without a valid order record."
        )

    order_dict = _row_to_dict(order_row)
    db_org_id = str(order_dict.get("organization_id") or "")
    db_owner = str(order_dict.get("owner") or "")

    # CRITICAL: Organization must match between DB and identity
    if db_org_id != identity_org_id:
        raise ReconcileAuthError(
            f"Database organization_id={db_org_id} for order {order_id} "
            f"does not match identity organization_id={identity_org_id}. "
            f"Cross-organization data injection detected."
        )

    # CRITICAL: claimed_org_id tampering check
    if claimed_org_id and claimed_org_id != db_org_id:
        raise ReconcileAuthError(
            f"Payload claimed organization_id={claimed_org_id} "
            f"does not match database organization_id={db_org_id} for order {order_id}. "
            f"Payload tampering detected."
        )

    is_mgr = str(user_role or "").lower() in {"manager", "admin", "supervisor"}

    if is_mgr:
        # Manager/Admin/Supervisor can access any order in their org
        return order_dict

    # NON-CRITICAL: Operator must own the order — skip, don't abort
    if not db_owner:
        raise OrderNotAuthorizedError(
            f"Operator {user_id} cannot reconcile order {order_id}: "
            f"order has no owner assigned."
        )

    if db_owner != user_id:
        raise OrderNotAuthorizedError(
            f"Operator {user_id} cannot reconcile order {order_id} "
            f"owned by {db_owner}. Permission denied."
        )

    return order_dict


def reconcile_action_cases(
    conn: Any,
    d7_result: dict[str, Any],
    *,
    identity: Any,
    policy_version: str = D8_POLICY_VERSION,
) -> dict[str, Any]:
    """Reconcile D7 pipeline output with action_cases.

    THIS IS THE AUTHORIZATION BOUNDARY FOR RECONCILIATION.

    CRITICAL DESIGN:
      Uses D7's `action_case_observations` as the authoritative observation
      source — NOT the ranked/display queues (my_action_items, etc.).

      Why? Because ranked queues are affected by Top-N truncation and UI
      filtering. "Not in Top-N" ≠ "Risk disappeared".

      The full observation feed contains ALL evaluated orders, including:
        - Orders with real risk signals
        - INFORMATION_GAP-only orders
        - Screened orders with no active risk

      DB is the authority for organization_id and owner — payload fields
      are only used as consistency validation.

    Returns a reconciliation summary.

    Raises ReconcileAuthError if payload validation fails.
    """
    from d7_risk_engine import _extract_identity_fields

    user_id, identity_org_id, user_role = _extract_identity_fields(identity)

    if not identity_org_id:
        return {
            "status": "ERROR",
            "error": "NO_ORGANIZATION",
            "message": "Organization ID is required for reconciliation",
            "intents_count": 0,
            "created_count": 0,
            "reused_count": 0,
        }

    # ── Validate scope.organization_id if present ──
    scope = d7_result.get("scope") or {}
    scope_org_id = scope.get("organization_id", "")
    if scope_org_id and scope_org_id != identity_org_id:
        raise ReconcileAuthError(
            f"Payload scope organization_id={scope_org_id} "
            f"does not match identity organization_id={identity_org_id}"
        )

    # ── Use action_case_observations as authoritative source ──
    # This is the FULL observation feed from D7, before ranking/Top-N truncation.
    # It includes ALL evaluated orders with their risk_signals.
    observations = d7_result.get("action_case_observations")
    using_legacy_fallback = False
    if observations is None:
        # Tightened legacy fallback: ranked queues are NOT a complete
        # observation snapshot. They are affected by Top-N truncation.
        # We allow fallback ONLY for create/reuse (lineage compatibility)
        # but PROHIBIT using ranked queues for NOT_OBSERVED marking.
        observations = []
        for key in ("my_action_items", "team_action_items", "unassigned_orders", "items"):
            items = d7_result.get(key) or []
            for item in items:
                oid = item.get("order_id", "")
                if oid and oid not in {o.get("order_id", "") for o in observations}:
                    observations.append(item)
        using_legacy_fallback = True

    if not observations:
        return {
            "status": "OK",
            "policy_version": policy_version,
            "organization_id": identity_org_id,
            "intents_count": 0,
            "created_count": 0,
            "reused_count": 0,
            "results": [],
            "observed_case_count": 0,
            "timestamp": _now_iso(),
        }

    # ── Validate ALL orders against DB authority before any write ──
    validated_items: list[dict[str, Any]] = []
    scope_order_ids: set[str] = set()
    seen_order_ids: set[str] = set()

    for item in observations:
        order_id = item.get("order_id", "")
        if not order_id or order_id in seen_order_ids:
            continue

        # DB authority validation — this is the hard boundary
        try:
            db_order = _validate_order_authority(
                conn,
                order_id,
                identity,
                claimed_org_id=str(item.get("organization_id") or ""),
            )
        except OrderNotAuthorizedError:
            # Non-critical: operator doesn't own this order → skip it
            continue
        # ReconcileAuthError (critical) propagates up automatically

        seen_order_ids.add(order_id)
        scope_order_ids.add(order_id)
        validated_items.append(item)

    if not validated_items:
        return {
            "status": "OK",
            "policy_version": policy_version,
            "organization_id": identity_org_id,
            "intents_count": 0,
            "created_count": 0,
            "reused_count": 0,
            "results": [],
            "observed_case_count": 0,
            "timestamp": _now_iso(),
        }

    # ── Derive intents from validated observations ──
    all_intents: list[dict[str, Any]] = []
    for item in validated_items:
        intents = derive_action_intents(item, organization_id=identity_org_id)
        all_intents.extend(intents)

    created_count = 0
    reused_count = 0
    observed_case_keys: set[tuple[str, str]] = set()
    results: list[dict[str, Any]] = []

    for intent in all_intents:
        order_id = intent["order_id"]
        intent_key = intent["action_intent_key"]
        observed_case_keys.add((order_id, intent_key))

        # Check for existing ACTIVE case
        existing = get_active_case(
            conn,
            organization_id=identity_org_id,
            order_id=order_id,
            action_intent_key=intent_key,
        )

        if existing:
            # Reuse: update the existing case
            updated = update_action_case_reconcile(
                conn,
                existing["action_case_id"],
                latest_action_bucket=intent.get("action_bucket"),
                latest_severity=intent.get("severity"),
                latest_recommended_action=intent.get("recommended_action"),
                latest_evidence=intent.get("evidence"),
            )
            reused_count += 1
            results.append({
                "action_case_id": updated["action_case_id"],
                "action_intent_key": updated["action_intent_key"],
                "status": "REUSED",
            })
        else:
            # Create new case
            try:
                created = create_action_case(
                    conn,
                    organization_id=identity_org_id,
                    order_id=order_id,
                    action_intent_key=intent_key,
                    intent_type=intent["intent_type"],
                    stage=intent["intent_stage"],
                    title=f"{intent['intent_type']} - {order_id}",
                    latest_action_bucket=intent.get("action_bucket"),
                    latest_severity=intent.get("severity"),
                    latest_recommended_action=intent.get("recommended_action"),
                    latest_evidence=intent.get("evidence"),
                    source_policy_version=policy_version,
                )
                created_count += 1
                results.append({
                    "action_case_id": created["action_case_id"],
                    "action_intent_key": created["action_intent_key"],
                    "status": "CREATED",
                })
            except IntegrityError:
                # Race condition on partial unique index
                existing_after = get_active_case(
                    conn,
                    organization_id=identity_org_id,
                    order_id=order_id,
                    action_intent_key=intent_key,
                )
                if existing_after:
                    updated = update_action_case_reconcile(
                        conn,
                        existing_after["action_case_id"],
                        latest_action_bucket=intent.get("action_bucket"),
                        latest_severity=intent.get("severity"),
                        latest_recommended_action=intent.get("recommended_action"),
                        latest_evidence=intent.get("evidence"),
                    )
                    reused_count += 1
                    results.append({
                        "action_case_id": updated["action_case_id"],
                        "action_intent_key": updated["action_intent_key"],
                        "status": "REUSED",
                    })
                else:
                    raise IntegrityError(
                        f"IntegrityError during create of {order_id}/{intent_key} "
                        f"but no existing ACTIVE case found"
                    )

    # ── Mark non-observed cases (SCOPED to current reconciliation) ──
    # ONLY allowed when using the authoritative observation feed.
    # Legacy fallback (ranked queues) is explicitly prohibited from
    # driving NOT_OBSERVED because Top-N absence ≠ risk disappearance.
    if not using_legacy_fallback:
        mark_cases_not_observed(
            conn,
            organization_id=identity_org_id,
            observed_case_keys=observed_case_keys,
            scope_order_ids=scope_order_ids,
        )

    return {
        "status": "OK",
        "policy_version": policy_version,
        "organization_id": identity_org_id,
        "intents_count": len(all_intents),
        "created_count": created_count,
        "reused_count": reused_count,
        "results": results,
        "observed_case_count": len(observed_case_keys),
        "scope_order_count": len(scope_order_ids),
        "timestamp": _now_iso(),
    }


# ---------------------------------------------------------------------------
# 7. Permission-Scoped Helpers
# ---------------------------------------------------------------------------


def list_my_cases(
    conn: Any,
    identity: Any,
    *,
    lifecycle_status: str | None = None,
) -> list[dict[str, Any]]:
    """List action cases visible to the current user.

    Operator: only sees cases for orders they own
    Manager/Admin/Supervisor: sees all cases in the organization
    """
    from d7_risk_engine import _extract_identity_fields

    user_id, org_id, user_role = _extract_identity_fields(identity)
    if not org_id:
        return []

    is_mgr = str(user_role or "").lower() in {"manager", "admin", "supervisor"}

    if is_mgr:
        # Manager sees all cases in the organization
        return list_cases(
            conn,
            organization_id=org_id,
            lifecycle_status=lifecycle_status,
        )
    else:
        # Operator sees only cases for orders they own
        _ensure_table(conn)
        sql = """SELECT ac.* FROM action_cases ac
                 JOIN orders o ON ac.order_id = o.order_id
                 WHERE ac.organization_id=? AND o.owner=?"""
        params: list[Any] = [org_id, user_id]

        if lifecycle_status:
            sql += " AND ac.lifecycle_status=?"
            params.append(lifecycle_status)

        sql += " ORDER BY ac.last_seen_at DESC"

        rows = _conn_exec(conn, sql, tuple(params)).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_my_case(
    conn: Any,
    identity: Any,
    action_case_id: str,
) -> dict[str, Any] | None:
    """Get a specific action case with permission check."""
    from d7_risk_engine import _extract_identity_fields

    user_id, org_id, user_role = _extract_identity_fields(identity)
    if not org_id:
        return None

    case = get_case_by_id(conn, action_case_id)
    if not case:
        return None

    # Organization check
    if case["organization_id"] != org_id:
        return None

    is_mgr = str(user_role or "").lower() in {"manager", "admin", "supervisor"}

    if is_mgr:
        return case

    # Operator: must own the order
    order_row = _conn_exec(
        conn,
        "SELECT owner FROM orders WHERE order_id=?",
        (case["order_id"],),
    ).fetchone()
    if not order_row:
        return None

    owner = str(order_row["owner"] or "")
    if owner == user_id:
        return case

    return None


# ---------------------------------------------------------------------------
# 8. Convenience: Run D7 + D8 Pipeline
# ---------------------------------------------------------------------------


def run_d8_pipeline(
    conn: Any,
    identity: Any,
    *,
    top_n: int = 7,
    due_within_days: int = 14,
    current_time: str | None = None,
    policy_version: str = D8_POLICY_VERSION,
) -> dict[str, Any]:
    """Run the combined D7 (risk assessment) + D8 (action case reconciliation) pipeline.

    This is the full D8 entry point that runs D7 first, then reconciles action cases.
    """
    from d7_risk_engine import run_d7_pipeline as _run_d7

    d7_result = _run_d7(
        conn,
        identity,
        top_n=top_n,
        due_within_days=due_within_days,
        current_time=current_time,
    )

    if d7_result.get("error"):
        return {
            "d7_result": d7_result,
            "d8_result": {
                "status": "ERROR",
                "error": "D7_PIPELINE_FAILED",
                "message": d7_result.get("message", "D7 pipeline failed"),
            },
            "policy_version": policy_version,
        }

    d8_result = reconcile_action_cases(
        conn,
        d7_result,
        identity=identity,
        policy_version=policy_version,
    )

    return {
        "d7_result": d7_result,
        "d8_result": d8_result,
        "policy_version": policy_version,
    }


# ---------------------------------------------------------------------------
# 9. Exports
# ---------------------------------------------------------------------------

__all__ = [
    "D8_POLICY_VERSION",
    "FSM_STAGES",
    "CLOSE_REASONS",
    "derive_action_intents",
    "derive_intents_from_pipeline",
    "get_active_case",
    "get_case_by_id",
    "list_cases",
    "create_action_case",
    "update_action_case_reconcile",
    "mark_cases_not_observed",
    "validate_transition",
    "authorize_case_transition",
    "transition_action_case",
    "ActionCaseFSMError",
    "ActionCaseAuthError",
    "ActionCaseVersionConflict",
    "ReconcileAuthError",
    "reconcile_action_cases",
    "list_my_cases",
    "get_my_case",
    "run_d8_pipeline",
]