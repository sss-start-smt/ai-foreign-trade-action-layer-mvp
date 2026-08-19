"""
FlowOrder D7: Risk Signals + Action Buckets + Explainable Priority
======================================================================

Policy Version: D7_RISK_POLICY_V1

This module implements the D7 decision chain:
    Order Facts → Risk Signals → Action Bucket → Within-Bucket Ranking → Evidence/Reasons

Core Principles:
    1. Risk != Action — a risk signal is not an action bucket
    2. Risk Attention ranking is independent from action bucket / governance escalation
    3. priority_score is a backward-compatible alias of risk_attention_score, NOT a probability
    4. INFORMATION_GAP is independent and never pads the risk Top-N
    5. One order appears only once in the ranking; multiple risks are aggregated
    6. Waiting suppression only controls repeated external contact; it does NOT erase risk attention
    7. Freshness (FRESH/STALE/UNAVAILABLE/NEVER_SYNCED) enters risk boundaries
    8. ERPNext document 'owner' is NEVER used as FlowOrder business owner
    9. ERPNext snapshot data is bridged as facts, not automatically treated as risk
    10. Deterministic: same facts + time + policy version → same output
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

CN_TZ = timezone(timedelta(hours=8))

D7_POLICY_VERSION = "D7_RISK_POLICY_V1"
ATTENTION_RANKING_VERSION = "D14_2_ATTENTION_V1"

# ---------------------------------------------------------------------------
# 1. Risk Signal Contract
# ---------------------------------------------------------------------------

RISK_TYPES = frozenset({
    "DELIVERY_RISK",
    "SUPPLIER_COMMITMENT_OVERDUE",
    "CUSTOMER_CONFIRMATION_BLOCKING",
    "LOGISTICS_EXCEPTION",
    "QUALITY_BLOCKING",
    "OWNER_MISSING",
    "SOURCE_CONFLICT",
})

ALL_RISK_TYPES = frozenset(RISK_TYPES | {"INFORMATION_GAP"})

SEVERITY_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})

FRESHNESS_STATES = frozenset({"FRESH", "STALE", "UNAVAILABLE", "NEVER_SYNCED"})


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def _date_only(value: Any) -> datetime.date | None:
    parsed = _parse_dt(value)
    return parsed.date() if parsed else None


def _extract_identity_fields(identity: Any) -> tuple[str | None, str | None, str | None]:
    """Extract user_id, organization_id, role from any identity form.

    Supports:
      - CurrentIdentity dataclass (preferred, production path)
      - Plain dict (test helper only)

    Returns (user_id, organization_id, role) or (None, None, None).
    """
    if hasattr(identity, "user_id") and hasattr(identity, "organization_id") and hasattr(identity, "role"):
        user_id = getattr(identity, "user_id", None)
        org_id = getattr(identity, "organization_id", None)
        user_role = getattr(identity, "role", None)
        return (str(user_id) if user_id else None, str(org_id) if org_id else None, str(user_role) if user_role else None)

    if isinstance(identity, dict):
        return (
            identity.get("user_id"),
            identity.get("organization_id"),
            identity.get("role"),
        )

    return (None, None, None)


def _compute_effective_delivery_date(
    order: dict[str, Any],
    erp_facts: dict[str, Any] | None,
) -> dict[str, Any]:
    """Determine the single effective delivery date with full provenance.

    Priority:
      1. FRESH ERP customer_due_date → ERP_NEXT provenance
      2. Local requested_delivery_date → ORDER_FACTS provenance

    When ERP is STALE/UNAVAILABLE, we still use local date but note
    the freshness boundary in the provenance.

    Returns:
        {
            "effective_delivery_date": str | None,
            "delivery_source": str,  # ERP_NEXT or ORDER_FACTS
            "delivery_source_id": str | None,
            "delivery_source_modified_at": str | None,
            "delivery_fetched_at": str | None,
            "delivery_freshness": str,
        }
    """
    local_due = order.get("requested_delivery_date")
    if local_due:
        local_due = str(local_due)

    if erp_facts and erp_facts.get("freshness") == "FRESH" and erp_facts.get("customer_due_date"):
        return {
            "effective_delivery_date": str(erp_facts["customer_due_date"]),
            "delivery_source": "ERP_NEXT",
            "delivery_source_id": erp_facts.get("snapshot_id") or erp_facts.get("source_id") or order.get("order_id"),
            "delivery_source_modified_at": erp_facts.get("source_modified_at"),
            "delivery_fetched_at": erp_facts.get("fetched_at"),
            "delivery_freshness": "FRESH",
        }

    if erp_facts and erp_facts.get("freshness") in ("STALE", "UNAVAILABLE") and erp_facts.get("customer_due_date"):
        return {
            "effective_delivery_date": local_due,
            "delivery_source": "ORDER_FACTS",
            "delivery_source_id": order.get("order_id"),
            "delivery_source_modified_at": order.get("updated_at"),
            "delivery_fetched_at": None,
            "delivery_freshness": erp_facts.get("freshness", "STALE"),
        }

    return {
        "effective_delivery_date": local_due,
        "delivery_source": "ORDER_FACTS",
        "delivery_source_id": order.get("order_id"),
        "delivery_source_modified_at": order.get("updated_at"),
        "delivery_fetched_at": None,
        "delivery_freshness": "FRESH",
    }


def _deadline_overdue(value: Any, current: datetime) -> bool:
    parsed = _parse_dt(value)
    if not parsed:
        return False
    return parsed < current


def _business_date_overdue(value: Any, current: datetime) -> bool:
    parsed = _date_only(value)
    if not parsed:
        return False
    return parsed < current.date()


# ---------------------------------------------------------------------------
# 2. Action Bucket Policy
# ---------------------------------------------------------------------------

ACTION_BUCKETS = frozenset({
    "ESCALATE",
    "DO_NOW",
    "NEEDS_CONFIRMATION",
    "DO_TODAY",
    "SCHEDULED",
    "WAITING_EXTERNAL",
    "NOT_MY_RESPONSIBILITY",
    "DONE",
})

_BUCKET_PRIORITY: dict[str, int] = {
    "ESCALATE": 800,
    "DO_NOW": 700,
    "NEEDS_CONFIRMATION": 600,
    "DO_TODAY": 500,
    "SCHEDULED": 300,
    "WAITING_EXTERNAL": 200,
    "NOT_MY_RESPONSIBILITY": 100,
    "DONE": 0,
}


def bucket_priority(bucket: str) -> int:
    return _BUCKET_PRIORITY.get(bucket, 0)


# ---------------------------------------------------------------------------
# 3. Risk Signal Builder
# ---------------------------------------------------------------------------

def build_risk_signal(
    *,
    order_id: str,
    order_no: str | None = None,
    risk_type: str,
    severity: str = "MEDIUM",
    status: str = "OPEN",
    evidence: list[str] | None = None,
    missing_information: list[str] | None = None,
    source_type: str = "LOCAL_RULE",
    source_id: str | None = None,
    source_modified_at: str | None = None,
    fetched_at: str | None = None,
    freshness: str = "FRESH",
    rule_id: str | None = None,
    explanation: str | None = None,
    detected_at: str | None = None,
    organization_id: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Build a RiskSignal conforming to the D7 contract."""
    if risk_type not in ALL_RISK_TYPES:
        raise ValueError(f"Unknown risk_type: {risk_type}")
    if severity not in SEVERITY_LEVELS:
        raise ValueError(f"Unknown severity: {severity}")
    if freshness not in FRESHNESS_STATES:
        raise ValueError(f"Unknown freshness: {freshness}")

    now = detected_at or _now_iso()
    signal: dict[str, Any] = {
        "risk_signal_id": _new_id("RS"),
        "order_id": order_id,
        "order_no": order_no,
        "risk_type": risk_type,
        "severity": severity,
        "status": status,
        "detected_at": now,
        "evidence": list(evidence or []),
        "missing_information": list(missing_information or []),
        "source_type": source_type,
        "source_id": source_id,
        "source_modified_at": source_modified_at,
        "fetched_at": fetched_at or now,
        "freshness": freshness,
        "rule_id": rule_id or f"RULE_{risk_type}_{D7_POLICY_VERSION}",
        "policy_version": D7_POLICY_VERSION,
        "explanation": explanation or "",
    }
    if organization_id is not None:
        signal["organization_id"] = organization_id
    return signal


# ---------------------------------------------------------------------------
# 4. Freshness → Risk Boundary
# ---------------------------------------------------------------------------

def _freshness_boundary(freshness: str) -> dict[str, Any]:
    """Translate freshness state into risk boundary flags.

    Returns a dict with:
      - data_reliable: whether the data can be used for normal risk判断
      - data_stale_warning: whether data may be outdated
      - data_availability_warning: whether data may be unavailable
      - can_assess_real_risk: whether real-risk assessment can proceed
    """
    if freshness == "FRESH":
        return {
            "data_reliable": True,
            "data_stale_warning": False,
            "data_availability_warning": False,
            "can_assess_real_risk": True,
        }
    if freshness == "STALE":
        return {
            "data_reliable": False,
            "data_stale_warning": True,
            "data_availability_warning": False,
            "can_assess_real_risk": True,
        }
    if freshness == "UNAVAILABLE":
        return {
            "data_reliable": False,
            "data_stale_warning": True,
            "data_availability_warning": True,
            "can_assess_real_risk": True,
        }
    # NEVER_SYNCED
    return {
        "data_reliable": False,
        "data_stale_warning": True,
        "data_availability_warning": True,
        "can_assess_real_risk": False,
    }


def _is_information_gap_only(risk_signal: dict[str, Any]) -> bool:
    """Return True if this risk signal is purely an INFORMATION_GAP."""
    return risk_signal.get("risk_type") == "INFORMATION_GAP"


# ---------------------------------------------------------------------------
# 5. ERP Snapshot Bridge
# ---------------------------------------------------------------------------

def bridge_erp_snapshot_to_facts(
    snapshot: dict[str, Any],
    sync_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bridge an ERPNext snapshot into D7-ordered facts.

    The snapshot is NOT automatically a risk. It becomes facts that feed
    the risk assessment engine.
    """
    normalized = snapshot.get("normalized") or {}
    freshness = "FRESH"
    if sync_state:
        from erpnext_readonly import compute_freshness
        freshness = compute_freshness(sync_state)
    elif snapshot.get("freshness"):
        freshness = snapshot["freshness"]

    order_facts = {
        "external_id": snapshot.get("external_id"),
        "customer_external_id": normalized.get("customer_external_id"),
        "customer_due_date": normalized.get("customer_due_date"),
        "order_status": normalized.get("order_status"),
        "transaction_date": normalized.get("transaction_date"),
        "source_modified_at": snapshot.get("source_modified_at") or normalized.get("source_modified_at"),
        "items": normalized.get("items") or [],
        "erp_owner": normalized.get("erp_owner"),
        "freshness": freshness,
        "snapshot_id": snapshot.get("snapshot_id"),
        "fetched_at": snapshot.get("fetched_at"),
        "source": "ERP_NEXT",
    }
    return order_facts


# ---------------------------------------------------------------------------
# 6. Risk Assessment from Facts
# ---------------------------------------------------------------------------

def assess_risks_from_facts(
    order: dict[str, Any],
    current: datetime | None = None,
    erp_facts: dict[str, Any] | None = None,
    sync_state: dict[str, Any] | None = None,
    tasks: list[dict[str, Any]] | None = None,
    logistics: list[dict[str, Any]] | None = None,
    quality_events: list[dict[str, Any]] | None = None,
    fact_conflicts: list[dict[str, Any]] | None = None,
    commitments: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Assess risk signals from combined order + ERP + local facts.

    This is the D7 bridge: it takes local FlowOrder order data + D6 ERP
    snapshot facts + local task/logistics/commitment data and produces
    RiskSignal objects.

    ERP owner is NEVER used as FlowOrder business owner.
    """
    now = current or datetime.now(CN_TZ)
    order_id = order.get("order_id", "")
    order_no = order.get("order_no")

    # Determine data freshness boundary
    freshness = "FRESH"
    if erp_facts:
        freshness = erp_facts.get("freshness", "FRESH")
    elif sync_state:
        from erpnext_readonly import compute_freshness
        freshness = compute_freshness(sync_state)

    boundary = _freshness_boundary(freshness)
    risk_signals: list[dict[str, Any]] = []

    # Compute single effective delivery date with provenance
    delivery_info = _compute_effective_delivery_date(order, erp_facts)
    delivery_date = delivery_info["effective_delivery_date"]

    # === DELIVERY_RISK ===
    delivery_signal = _assess_delivery_risk(
        order, now, delivery_date, erp_facts, boundary,
        delivery_source=delivery_info["delivery_source"],
        delivery_source_id=delivery_info["delivery_source_id"],
        delivery_source_modified_at=delivery_info["delivery_source_modified_at"],
        delivery_fetched_at=delivery_info["delivery_fetched_at"],
        delivery_freshness=delivery_info["delivery_freshness"],
    )
    if delivery_signal:
        risk_signals.append(delivery_signal)

    # === SUPPLIER_COMMITMENT_OVERDUE ===
    commitment_signal = _assess_commitment_overdue(order, now, commitments, tasks, boundary)
    if commitment_signal:
        risk_signals.append(commitment_signal)

    # === CUSTOMER_CONFIRMATION_BLOCKING ===
    confirmation_signal = _assess_customer_confirmation_blocking(
        order, tasks, boundary, current=now, delivery_date=delivery_date
    )
    if confirmation_signal:
        risk_signals.append(confirmation_signal)

    # === LOGISTICS_EXCEPTION ===
    logistics_signal = _assess_logistics_exception(
        order, logistics, boundary, current=now, delivery_date=delivery_date
    )
    if logistics_signal:
        risk_signals.append(logistics_signal)

    # === QUALITY_BLOCKING ===
    quality_signal = _assess_quality_blocking(
        order, quality_events, boundary, current=now, delivery_date=delivery_date
    )
    if quality_signal:
        risk_signals.append(quality_signal)

    # === OWNER_MISSING ===
    owner_signal = _assess_owner_missing(order, erp_facts, boundary)
    if owner_signal:
        risk_signals.append(owner_signal)

    # === SOURCE_CONFLICT ===
    conflict_signal = _assess_source_conflict(order, erp_facts, boundary, tasks, fact_conflicts=fact_conflicts)
    if conflict_signal:
        risk_signals.append(conflict_signal)

    # === INFORMATION_GAP (only if no real risks found) ===
    if not risk_signals:
        gap_signal = _assess_information_gap(order, boundary, tasks)
        if gap_signal:
            risk_signals.append(gap_signal)

    return risk_signals


def _assess_delivery_risk(
    order: dict[str, Any],
    current: datetime,
    delivery_date: Any,
    erp_facts: dict[str, Any] | None,
    boundary: dict[str, Any],
    *,
    delivery_source: str = "ORDER_FACTS",
    delivery_source_id: str | None = None,
    delivery_source_modified_at: str | None = None,
    delivery_fetched_at: str | None = None,
    delivery_freshness: str = "FRESH",
) -> dict[str, Any] | None:
    """Assess DELIVERY_RISK based on trusted delivery date.

    Provenance is explicitly passed in, not re-derived, to ensure
    that the risk signal accurately reflects the actual data source used.
    """
    delivery = _parse_dt(delivery_date)
    if not delivery:
        return None

    days_remaining = (delivery.date() - current.date()).days
    progress = float(order.get("current_progress") or 0)
    evidence: list[str] = []
    severity = "MEDIUM"

    if days_remaining < 0:
        evidence.append(f"客户正式交期已超期{abs(days_remaining)}天")
        severity = "CRITICAL" if abs(days_remaining) >= 7 else "HIGH"
    elif days_remaining <= 3:
        evidence.append(f"距离客户正式交期仅{days_remaining}天")
        severity = "HIGH" if days_remaining <= 1 else "MEDIUM"
    elif days_remaining <= 7:
        evidence.append(f"距离客户正式交期{days_remaining}天")
        severity = "MEDIUM"
    else:
        return None

    if progress < 0.5 and days_remaining <= 7:
        evidence.append(f"当前进度仅{round(progress * 100)}%，与临近交期不匹配")
        if severity == "MEDIUM":
            severity = "HIGH"
    elif progress < 0.8 and 0 <= days_remaining <= 3:
        evidence.append(f"当前进度{round(progress * 100)}%与临近交期不匹配")
        if severity in ("MEDIUM", "HIGH"):
            severity = "CRITICAL" if days_remaining <= 1 else "HIGH"

    missing: list[str] = []
    if order.get("current_node") is None:
        missing.append("当前节点")
    if order.get("current_progress") is None:
        missing.append("当前生产进度")

    if boundary.get("data_stale_warning"):
        evidence.append("注意：当前数据可能已陈旧")

    source_type = delivery_source
    source_id = delivery_source_id or order.get("order_id")
    source_modified_at = delivery_source_modified_at or order.get("updated_at")
    fetched_at = delivery_fetched_at

    return build_risk_signal(
        order_id=order.get("order_id", ""),
        order_no=order.get("order_no"),
        risk_type="DELIVERY_RISK",
        severity=severity,
        evidence=evidence,
        missing_information=missing,
        source_type=source_type,
        source_id=source_id,
        source_modified_at=source_modified_at,
        fetched_at=fetched_at,
        freshness=delivery_freshness,
        rule_id="RULE_DELIVERY_RISK_V1",
        explanation=f"交期{delivery.date()}，剩余{days_remaining}天",
        organization_id=order.get("organization_id"),
    )


def _assess_commitment_overdue(
    order: dict[str, Any],
    current: datetime,
    commitments: list[dict[str, Any]] | None,
    tasks: list[dict[str, Any]] | None,
    boundary: dict[str, Any],
) -> dict[str, Any] | None:
    """Assess SUPPLIER_COMMITMENT_OVERDUE.

    Priority:
      1. Confirmed commitment from history (confirmed_by is not empty)
      2. Local latest_supplier_commitment field
      3. No commitment → no risk

    Provenance reflects which source was actually used.
    """
    commitment_date = None
    source_type = "ORDER_FACTS"
    source_id = order.get("order_id")
    source_modified_at = order.get("updated_at")
    fetched_at = None

    confirmed = None
    if commitments:
        for c in commitments:
            if c.get("commitment_type") == "SUPPLIER_COMMITMENT" and c.get("confirmed_by"):
                confirmed = c
                break

    if confirmed:
        commitment_date = confirmed.get("commitment_value")
        source_type = "COMMITMENT_HISTORY"
        source_id = confirmed.get("commitment_id") or source_id
        source_modified_at = confirmed.get("created_at") or source_modified_at
    else:
        commitment_date = order.get("latest_supplier_commitment")

    commitment = _parse_dt(commitment_date)
    if not commitment:
        return None

    if not _business_date_overdue(commitment, current):
        return None

    progress = float(order.get("current_progress") or 0)
    if progress >= 1:
        return None

    evidence = [f"供应商完工承诺{commitment.date()}已过期"]
    severity = "HIGH"

    overdue_tasks = [
        t for t in (tasks or [])
        if t.get("waiting_on") and t.get("promised_reply_at")
        and _deadline_overdue(t.get("promised_reply_at"), current)
        and str(t.get("status") or "").upper() not in {"DONE", "WAITING_EXTERNAL"}
    ]
    for task in overdue_tasks[:3]:
        evidence.append(f"等待事项「{task.get('title')}」的承诺回复时间已过")
        severity = "CRITICAL" if len(overdue_tasks) >= 2 else severity

    if boundary.get("data_stale_warning"):
        evidence.append("注意：部分数据可能已陈旧")

    return build_risk_signal(
        order_id=order.get("order_id", ""),
        order_no=order.get("order_no"),
        risk_type="SUPPLIER_COMMITMENT_OVERDUE",
        severity=severity,
        evidence=evidence,
        source_type=source_type,
        source_id=source_id,
        source_modified_at=source_modified_at,
        fetched_at=fetched_at,
        freshness=boundary_freshness_label(boundary),
        rule_id="RULE_SUPPLIER_COMMITMENT_OVERDUE_V1",
        explanation=f"供应商承诺{commitment.date()}已过期，进度{round(progress * 100)}%",
        organization_id=order.get("organization_id"),
    )


CUSTOMER_CONFIRMATION_RESPONSE_WINDOW_HOURS = 4


def _pending_customer_confirmation_tasks(tasks: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [
        t for t in (tasks or [])
        if int(t.get("pending_confirmation") or 0) == 1
        or (t.get("target") == "customer" and "确认" in str(t.get("title") or ""))
    ]


def _recent_confirmation_contact_age_hours(
    tasks: list[dict[str, Any]] | None,
    current: datetime,
    *,
    max_hours: int = CUSTOMER_CONFIRMATION_RESPONSE_WINDOW_HOURS,
) -> float | None:
    """Return the oldest contact age when *all* pending confirmation tasks were contacted recently.

    This deliberately requires a concrete last_contact_at on every pending customer
    confirmation item. Missing timestamps never create an implicit waiting window.
    Future timestamps and contacts older than the bounded response window also fail
    closed, so the normal NEEDS_CONFIRMATION behavior remains in force.
    """
    pending = _pending_customer_confirmation_tasks(tasks)
    if not pending:
        return None

    ages: list[float] = []
    for task in pending:
        contacted = _parse_dt(task.get("last_contact_at"))
        if not contacted:
            return None
        age_hours = (current - contacted).total_seconds() / 3600.0
        if age_hours < 0 or age_hours > max_hours:
            return None
        ages.append(age_hours)
    return max(ages) if ages else None


def _assess_customer_confirmation_blocking(
    order: dict[str, Any],
    tasks: list[dict[str, Any]] | None,
    boundary: dict[str, Any],
    *,
    current: datetime | None = None,
    delivery_date: Any = None,
) -> dict[str, Any] | None:
    """Assess CUSTOMER_CONFIRMATION_BLOCKING with delivery-buffer calibration.

    D14 R3: two pending customer confirmations should not become HIGH solely
    because there are two of them when the customer-delivery date still has
    more than seven days of buffer. Four same-case users consistently placed
    FO-D14-015 below the frozen system Top-7 while still wanting the items
    tracked and followed up.

    Unknown due dates stay conservative.
    """
    pending = _pending_customer_confirmation_tasks(tasks)
    if not pending:
        return None

    evidence = [f"待确认任务：{t.get('title')}" for t in pending[:3]]
    now = current or datetime.now(CN_TZ)
    delivery = _parse_dt(
        delivery_date
        or order.get("effective_delivery_date")
        or order.get("requested_delivery_date")
        or order.get("customer_due_date")
    )
    days_to_due = (delivery.date() - now.date()).days if delivery else None

    if len(pending) >= 2 and (days_to_due is None or days_to_due <= 7):
        severity = "HIGH"
    else:
        severity = "MEDIUM"

    if days_to_due is not None:
        evidence.append(f"距离客户正式交期{days_to_due}天")

    recent_age = _recent_confirmation_contact_age_hours(pending, now)
    if recent_age is not None:
        evidence.append(f"客户确认请求刚发出约{recent_age:.1f}小时，仍在合理回复窗口内")

    if boundary.get("data_stale_warning"):
        evidence.append("注意：部分数据可能已陈旧")

    return build_risk_signal(
        order_id=order.get("order_id", ""),
        order_no=order.get("order_no"),
        risk_type="CUSTOMER_CONFIRMATION_BLOCKING",
        severity=severity,
        evidence=evidence,
        source_type="ORDER_FACTS",
        source_id=order.get("order_id"),
        source_modified_at=order.get("updated_at"),
        freshness=boundary_freshness_label(boundary),
        rule_id="RULE_CUSTOMER_CONFIRMATION_BLOCKING_V3",
        explanation=(
            f"{len(pending)}项客户确认阻塞"
            + (f"，距客户交期{days_to_due}天" if days_to_due is not None else "，客户交期缓冲未知")
        ),
        organization_id=order.get("organization_id"),
    )


def _assess_logistics_exception(
    order: dict[str, Any],
    logistics: list[dict[str, Any]] | None,
    boundary: dict[str, Any],
    *,
    current: datetime | None = None,
    delivery_date: Any = None,
) -> dict[str, Any] | None:
    """Assess LOGISTICS_EXCEPTION with delivery-buffer calibration.

    D14 R2 calibration: unresolved logistics count alone must not imply the
    same urgency regardless of customer-delivery buffer. Four independent
    target-user rankings agreed that a single exception with ~10 days of
    buffer is worth same-day follow-up but not immediate Top-N rescue, while
    two unresolved exceptions with ~12 days of buffer require active recovery
    but do not automatically justify manager escalation.

    Conservative behavior is retained when the delivery date is unavailable.
    """
    bad = [
        e for e in (logistics or [])
        if e.get("status") in {"DELAYED", "EXCEPTION", "CUSTOMS_HOLD"}
        and not e.get("resolved_at")
    ]
    if not bad:
        return None

    evidence = [
        f"{e.get('event_type') or '物流事件'}：{e.get('description') or e.get('status')}"
        for e in bad[:4]
    ]

    now = current or datetime.now(CN_TZ)
    delivery = _parse_dt(
        delivery_date
        or order.get("effective_delivery_date")
        or order.get("requested_delivery_date")
        or order.get("customer_due_date")
    )
    days_to_due = (delivery.date() - now.date()).days if delivery else None

    # D14 R2: combine exception multiplicity with remaining delivery buffer.
    # D14.1: when every unresolved logistics event has a structured ETA and
    # the latest ETA still leaves >=3 full days before customer delivery, a
    # multi-exception order in the 8-14 day window is no longer treated like
    # an immediate rescue.  This is deliberately ETA-aware rather than
    # keyword-parsing free-text descriptions. Unknown/missing ETA remains
    # conservative.
    eta_values = [_parse_dt(e.get("estimated_arrival_at")) for e in bad]
    all_eta_known = bool(eta_values) and all(v is not None for v in eta_values)
    latest_eta = max((v for v in eta_values if v is not None), default=None)
    eta_buffer_days = (delivery.date() - latest_eta.date()).days if delivery and latest_eta else None

    if days_to_due is None or days_to_due <= 7:
        severity = "CRITICAL" if len(bad) >= 2 else "HIGH"
    elif days_to_due <= 14:
        if len(bad) >= 2:
            severity = "MEDIUM" if all_eta_known and eta_buffer_days is not None and eta_buffer_days >= 3 else "HIGH"
        else:
            severity = "MEDIUM"
    else:
        severity = "MEDIUM"

    if days_to_due is not None:
        evidence.append(f"距离客户正式交期{days_to_due}天")
    if all_eta_known and latest_eta is not None and eta_buffer_days is not None:
        evidence.append(f"当前最晚结构化ETA为{latest_eta.date().isoformat()}，较客户交期提前{eta_buffer_days}天")

    if boundary.get("data_stale_warning"):
        evidence.append("注意：部分数据可能已陈旧")

    missing: list[str] = []
    if not any(e.get("estimated_arrival_at") for e in bad):
        missing.append("最新预计到达时间")

    return build_risk_signal(
        order_id=order.get("order_id", ""),
        order_no=order.get("order_no"),
        risk_type="LOGISTICS_EXCEPTION",
        severity=severity,
        evidence=evidence,
        missing_information=missing,
        source_type="ORDER_FACTS",
        source_id=order.get("order_id"),
        source_modified_at=order.get("updated_at"),
        freshness=boundary_freshness_label(boundary),
        rule_id="RULE_LOGISTICS_EXCEPTION_V3",
        explanation=(
            f"{len(bad)}项未解决物流异常"
            + (f"，距客户交期{days_to_due}天" if days_to_due is not None else "，客户交期缓冲未知")
            + (f"；最晚ETA仍提前{eta_buffer_days}天" if all_eta_known and eta_buffer_days is not None else "")
        ),
        organization_id=order.get("organization_id"),
    )


def _assess_quality_blocking(
    order: dict[str, Any],
    quality_events: list[dict[str, Any]] | None,
    boundary: dict[str, Any],
    *,
    current: datetime | None = None,
    delivery_date: Any = None,
) -> dict[str, Any] | None:
    """Assess a structured quality event that currently blocks delivery.

    D14 Quality P1 contract:
    - Only structured quality_events with is_delivery_blocking=1 can trigger it.
    - Resolved/closed events never trigger it.
    - Free-text source messages are deliberately not keyword-parsed here.
    - A blocking issue near due date is HIGH/DO_NOW, but not automatically
      manager escalation. CRITICAL is reserved for already-overdue orders or a
      known resolution date later than the customer due date.
    """
    active = []
    for event in quality_events or []:
        status = str(event.get("status") or "OPEN").upper()
        if event.get("resolved_at") or status in {"RESOLVED", "CLOSED", "CANCELLED"}:
            continue
        if int(event.get("is_delivery_blocking") or 0) != 1:
            continue
        active.append(event)

    if not active:
        return None

    now = current or datetime.now(CN_TZ)
    delivery = _parse_dt(
        delivery_date
        or order.get("effective_delivery_date")
        or order.get("requested_delivery_date")
        or order.get("customer_due_date")
    )
    days_to_due = (delivery.date() - now.date()).days if delivery else None

    # Latest blocking event is used as provenance; all active blockers remain evidence.
    active = sorted(
        active,
        key=lambda e: str(e.get("event_time") or e.get("created_at") or ""),
        reverse=True,
    )
    primary = active[0]
    evidence = []
    for event in active[:3]:
        label = event.get("event_type") or "质量事件"
        desc = event.get("description") or event.get("status") or "阻塞交付"
        evidence.append(f"质量阻塞：{label} - {desc}")

    if days_to_due is not None:
        evidence.append(f"距离客户正式交期{days_to_due}天")

    resolution = _parse_dt(primary.get("expected_resolution_at"))
    severity = "HIGH" if days_to_due is None or days_to_due <= 7 else "MEDIUM"
    if days_to_due is not None and days_to_due < 0:
        severity = "CRITICAL"
    elif delivery and resolution and resolution.date() > delivery.date():
        severity = "CRITICAL"
        evidence.append("当前预计质量问题解决时间晚于客户交期")

    missing = []
    if not primary.get("expected_resolution_at"):
        missing.append("质量问题预计解决时间")

    if boundary.get("data_stale_warning"):
        evidence.append("注意：部分数据可能已陈旧")

    return build_risk_signal(
        order_id=order.get("order_id", ""),
        order_no=order.get("order_no"),
        risk_type="QUALITY_BLOCKING",
        severity=severity,
        evidence=evidence,
        missing_information=missing,
        source_type="QUALITY_EVENT",
        source_id=primary.get("quality_event_id") or order.get("order_id"),
        source_modified_at=primary.get("updated_at") or primary.get("event_time"),
        freshness=boundary_freshness_label(boundary),
        rule_id="RULE_QUALITY_BLOCKING_V1",
        explanation=(
            f"{len(active)}项未解决质量问题正在阻塞交付"
            + (f"，距客户交期{days_to_due}天" if days_to_due is not None else "，客户交期未知")
        ),
        organization_id=order.get("organization_id"),
    )


def _assess_owner_missing(
    order: dict[str, Any],
    erp_facts: dict[str, Any] | None,
    boundary: dict[str, Any],
) -> dict[str, Any] | None:
    """Assess OWNER_MISSING.

    CRITICAL: ERPNext document 'owner' (erp_owner) MUST NOT be used as
    FlowOrder business owner. ERP owner is the document creator, not the
    order follow-up person.
    """
    business_owner = order.get("owner")
    if business_owner and str(business_owner).strip() not in ("", "待分配", "未分配", "-", "—"):
        return None

    evidence = ["FlowOrder业务负责人缺失"]
    if erp_facts and erp_facts.get("erp_owner"):
        evidence.append(
            f"注意：ERP文档创建人'{erp_facts['erp_owner']}'不是FlowOrder业务负责人"
        )

    if boundary.get("data_stale_warning"):
        evidence.append("注意：部分数据可能已陈旧")

    return build_risk_signal(
        order_id=order.get("order_id", ""),
        order_no=order.get("order_no"),
        risk_type="OWNER_MISSING",
        severity="HIGH",
        evidence=evidence,
        source_type="ORDER_FACTS",
        source_id=order.get("order_id"),
        source_modified_at=order.get("updated_at"),
        freshness=boundary_freshness_label(boundary),
        rule_id="RULE_OWNER_MISSING_V1",
        explanation="业务负责人缺失，需指派跟单人员",
        organization_id=order.get("organization_id"),
    )


# Field Authority / Status Mapping for Source Conflict detection.
# Only fields with the same business semantic dimension can trigger conflict.
ERP_STATUS_TO_FLOWORDER_SEMANTIC: dict[str, str] = {
    "TO DELIVER AND BILL": "ACTIVE",
    "SUBMITTED": "ACTIVE",
    "COMPLETED": "DONE",
    "CANCELLED": "CANCELLED",
    "DRAFT": "DRAFT",
    "ON HOLD": "PAUSED",
}

# Fields that are directly comparable (same semantic dimension)
_COMPARABLE_FIELDS = frozenset({"order_status", "requested_delivery_date", "customer_due_date"})


def _normalize_erp_status(erp_status: str) -> str | None:
    """Map ERPNext status to FlowOrder semantic category."""
    return ERP_STATUS_TO_FLOWORDER_SEMANTIC.get(erp_status.strip().upper())


def _assess_source_conflict(
    order: dict[str, Any],
    erp_facts: dict[str, Any] | None,
    boundary: dict[str, Any],
    tasks: list[dict[str, Any]] | None,
    *,
    fact_conflicts: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Assess SOURCE_CONFLICT.

    Detects when different trusted sources have conflicting facts about
    the same order. Does NOT auto-resolve the conflict.

    Field Authority Rules:
    - ERPNext 'order_status' and FlowOrder 'status' are compared via
      semantic mapping, NOT raw string equality.
    - ERPNext 'customer_due_date' and FlowOrder 'requested_delivery_date'
      are directly comparable (same semantic dimension).
    - Only fields with same business meaning can trigger conflict.
    """
    conflicts: list[str] = []

    # Check: ERP status vs local status (via semantic mapping)
    if erp_facts and erp_facts.get("order_status"):
        erp_status = str(erp_facts["order_status"]).strip().upper()
        local_status = str(order.get("status") or "").strip().upper()
        if local_status:
            erp_semantic = _normalize_erp_status(erp_status)
            # Only flag conflict if both map to the same semantic category
            # but have different raw values AND aren't in the compatible set
            if erp_semantic and erp_semantic != local_status:
                # Check if they are actually compatible (both in-progress states)
                compatible_semantics = {"ACTIVE", "DRAFT", "PAUSED"}
                if not (erp_semantic in compatible_semantics and local_status in compatible_semantics):
                    conflicts.append(
                        f"ERP状态'{erp_status}'(语义:{erp_semantic})与本地状态'{local_status}'不一致"
                    )
            elif not erp_semantic and erp_status != local_status:
                # Unknown ERP status - only flag if clearly different
                pass  # Unknown ERP statuses are not auto-conflicted

    # Check: ERP customer_due_date vs local requested_delivery_date
    if erp_facts and erp_facts.get("customer_due_date") and order.get("requested_delivery_date"):
        erp_due = str(erp_facts["customer_due_date"])[:10]
        local_due = str(order["requested_delivery_date"])[:10]
        if erp_due != local_due:
            conflicts.append(f"ERP交期'{erp_due}'与本地交期'{local_due}'不一致")

    # D14 R6: explicit structured conflict facts from trusted internal sources.
    # Free-text messages are deliberately not parsed into conflicts.
    for fc in fact_conflicts or []:
        status = str(fc.get("status") or "OPEN").upper()
        if fc.get("resolved_at") or status in {"RESOLVED", "CLOSED", "CANCELLED"}:
            continue
        field_name = fc.get("field_name") or "业务事实"
        source_a = fc.get("source_a") or "来源A"
        source_b = fc.get("source_b") or "来源B"
        value_a = fc.get("value_a")
        value_b = fc.get("value_b")
        if value_a is not None and value_b is not None and str(value_a) != str(value_b):
            conflicts.append(f"{field_name}：{source_a}='{value_a}' 与 {source_b}='{value_b}'不一致")

    if not conflicts:
        return None

    evidence = [
        f"不同可信来源对同一事实出现冲突：{'; '.join(conflicts)}",
        "系统不会自动选择某个来源覆盖另一个，需人工确认",
    ]
    severity = "MEDIUM"

    if boundary.get("data_stale_warning"):
        evidence.append("注意：部分数据可能已陈旧")

    return build_risk_signal(
        order_id=order.get("order_id", ""),
        order_no=order.get("order_no"),
        risk_type="SOURCE_CONFLICT",
        severity=severity,
        evidence=evidence,
        source_type="ORDER_FACTS",
        source_id=order.get("order_id"),
        source_modified_at=order.get("updated_at"),
        freshness=boundary_freshness_label(boundary),
        rule_id="RULE_SOURCE_CONFLICT_V1",
        explanation=f"{len(conflicts)}项来源冲突",
        organization_id=order.get("organization_id"),
    )


def _assess_information_gap(
    order: dict[str, Any],
    boundary: dict[str, Any],
    tasks: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    """Assess INFORMATION_GAP — only when no real risk exists.

    INFORMATION_GAP is NOT a business risk. It's a data quality signal.
    It must never enter the risk Top-N ranking.
    """
    missing: list[str] = []
    if not order.get("current_node"):
        missing.append("当前节点")
    if order.get("current_progress") is None:
        missing.append("当前生产进度")
    if not order.get("latest_supplier_commitment"):
        missing.append("最新工厂承诺")

    # Check for tasks that lack key information
    for task in (tasks or []):
        if not task.get("next_action_at") and not task.get("promised_reply_at"):
            if task.get("title"):
                missing.append(f"任务「{task['title']}」缺少下一步时间")

    if not missing:
        return None

    evidence = [f"关键信息缺失：{'; '.join(missing)}", "现有数据不足以形成可靠异常结论"]

    if boundary.get("data_availability_warning"):
        evidence.append("ERP数据不可用，当前仅基于本地数据判断")

    return build_risk_signal(
        order_id=order.get("order_id", ""),
        order_no=order.get("order_no"),
        risk_type="INFORMATION_GAP",
        severity="LOW",
        evidence=evidence,
        missing_information=missing,
        source_type="ORDER_FACTS",
        source_id=order.get("order_id"),
        source_modified_at=order.get("updated_at"),
        freshness=boundary_freshness_label(boundary),
        rule_id="RULE_INFORMATION_GAP_V1",
        explanation=f"{len(missing)}项信息缺失",
        organization_id=order.get("organization_id"),
    )


def boundary_freshness_label(boundary: dict[str, Any]) -> str:
    """Convert boundary dict back to a freshness label."""
    if boundary.get("data_availability_warning") and boundary.get("data_stale_warning"):
        if boundary.get("can_assess_real_risk", True):
            return "UNAVAILABLE"
        return "NEVER_SYNCED"
    if boundary.get("data_availability_warning"):
        return "NEVER_SYNCED"
    if boundary.get("data_stale_warning"):
        return "STALE"
    return "FRESH"


# ---------------------------------------------------------------------------
# 7. Action Bucket Assignment
# ---------------------------------------------------------------------------

def assign_action_bucket(
    risk_signals: list[dict[str, Any]],
    order: dict[str, Any],
    tasks: list[dict[str, Any]] | None = None,
    current: datetime | None = None,
    user_id: str | None = None,
    user_role: str | None = None,
) -> dict[str, Any]:
    """Assign an Action Bucket based on risk signals + order context.

    Key rule: Risk != Action. A risk signal does not automatically determine
    an action bucket. Context matters.

    Returns:
        {
            "action_bucket": str,
            "bucket_reasons": list[str],
            "ranking_suppressed": bool,
            "suppression_reason": str | None,
        }
    """
    now = current or datetime.now(CN_TZ)
    tasks = tasks or []

    # If user is not the owner and not a manager → NOT_MY_RESPONSIBILITY
    owner = str(order.get("owner") or "")
    if user_id and owner:
        if str(user_id).strip() != owner.strip():
            if str(user_role or "").lower() not in {"manager", "admin", "supervisor"}:
                return {
                    "action_bucket": "NOT_MY_RESPONSIBILITY",
                    "bucket_reasons": [f"订单负责人为{owner}，不属于当前用户"],
                    "ranking_suppressed": True,
                    "suppression_reason": "NOT_MY_RESPONSIBILITY",
                }

    risk_types = {s["risk_type"] for s in risk_signals}
    is_info_gap_only = risk_types == {"INFORMATION_GAP"}

    # === DONE ===
    if str(order.get("status") or "").upper() in {"DONE", "CLOSED", "CANCELLED", "COMPLETED"}:
        return {
            "action_bucket": "DONE",
            "bucket_reasons": ["订单已完成/关闭"],
            "ranking_suppressed": True,
            "suppression_reason": "ORDER_DONE",
        }

    # === ESCALATE ===
    escalate_reasons: list[str] = []
    if "OWNER_MISSING" in risk_types:
        escalate_reasons.append("业务负责人缺失需主管介入")
    for sig in risk_signals:
        if sig.get("severity") == "CRITICAL":
            escalate_reasons.append(f"存在{sig['risk_type']}严重风险")
    if escalate_reasons:
        return {
            "action_bucket": "ESCALATE",
            "bucket_reasons": escalate_reasons,
            "ranking_suppressed": False,
            "suppression_reason": None,
        }

    # === WAITING_EXTERNAL with suppression ===
    # Check if there's an active waiting task (contact done, promised reply not yet due)
    waiting_tasks = [
        t for t in tasks
        if t.get("waiting_on") and t.get("promised_reply_at")
        and str(t.get("status") or "").upper() == "WAITING_EXTERNAL"
    ]
    active_waiting = [
        t for t in waiting_tasks
        if not _deadline_overdue(t.get("promised_reply_at"), now)
    ]
    overdue_waiting = [
        t for t in waiting_tasks
        if _deadline_overdue(t.get("promised_reply_at"), now)
    ]

    if active_waiting and not overdue_waiting and "SOURCE_CONFLICT" not in risk_types:
        wait_reasons = [f"已联系{active_waiting[0].get('waiting_on')}，等待回复中"]
        for sig in risk_signals:
            if sig.get("risk_type") == "SUPPLIER_COMMITMENT_OVERDUE":
                wait_reasons.append("虽然存在风险，但仍在有效等待窗口内")
        return {
            "action_bucket": "WAITING_EXTERNAL",
            "bucket_reasons": wait_reasons,
            "ranking_suppressed": True,
            "suppression_reason": "WAITING_SUPPRESSION",
        }

    # === DO_NOW (overdue waiting, critical delivery, logistics, etc.) ===
    donow_reasons: list[str] = []
    if overdue_waiting:
        donow_reasons.append(f"等待回复时间已过，需再次跟进{overdue_waiting[0].get('waiting_on')}")
    effective_delivery = (
        order.get("effective_delivery_date")
        or order.get("requested_delivery_date")
        or order.get("customer_due_date")
    )
    for sig in risk_signals:
        if sig.get("risk_type") == "DELIVERY_RISK" and sig.get("severity") in ("CRITICAL", "HIGH"):
            delivery = _parse_dt(effective_delivery)
            if delivery and delivery.date() < now.date():
                donow_reasons.append("客户交期已过")
    for sig in risk_signals:
        if sig.get("risk_type") == "SUPPLIER_COMMITMENT_OVERDUE":
            donow_reasons.append("供应商承诺已过期")
    for sig in risk_signals:
        if sig.get("risk_type") == "LOGISTICS_EXCEPTION" and sig.get("severity") == "HIGH":
            donow_reasons.append("物流异常需立即跟进")
    for sig in risk_signals:
        if sig.get("risk_type") == "QUALITY_BLOCKING" and sig.get("severity") == "HIGH":
            donow_reasons.append("质量问题正在阻塞交付，需立即确认返工/替代方案")
    # Overdue waiting is an independent action trigger — never blocked by INFORMATION_GAP
    if overdue_waiting and donow_reasons:
        return {
            "action_bucket": "DO_NOW",
            "bucket_reasons": donow_reasons,
            "ranking_suppressed": False,
            "suppression_reason": None,
        }
    if donow_reasons and not is_info_gap_only:
        return {
            "action_bucket": "DO_NOW",
            "bucket_reasons": donow_reasons,
            "ranking_suppressed": False,
            "suppression_reason": None,
        }

    # === D14 R5: recent customer-confirmation response window ===
    # A newly sent confirmation request should not immediately create another
    # chase action when there is still meaningful delivery buffer.  This is
    # an action-timing rule, not a risk deletion: CUSTOMER_CONFIRMATION_BLOCKING
    # remains visible, but the current action is scheduled until the bounded
    # response window has had time to elapse.
    if "CUSTOMER_CONFIRMATION_BLOCKING" in risk_types and "SOURCE_CONFLICT" not in risk_types:
        delivery = _parse_dt(effective_delivery)
        days_to_due = (delivery.date() - now.date()).days if delivery else None
        recent_age = _recent_confirmation_contact_age_hours(tasks, now)
        if recent_age is not None and days_to_due is not None and days_to_due >= 8:
            return {
                "action_bucket": "SCHEDULED",
                "bucket_reasons": [
                    f"客户确认请求刚发出约{recent_age:.1f}小时，交期仍有{days_to_due}天；先留出正常回复时间再复核"
                ],
                "ranking_suppressed": False,
                "suppression_reason": None,
            }

    # === NEEDS_CONFIRMATION ===
    if "SOURCE_CONFLICT" in risk_types or "CUSTOMER_CONFIRMATION_BLOCKING" in risk_types:
        confirm_reasons: list[str] = []
        if "SOURCE_CONFLICT" in risk_types:
            confirm_reasons.append("存在来源冲突，需人工确认事实")
        if "CUSTOMER_CONFIRMATION_BLOCKING" in risk_types:
            confirm_reasons.append("存在客户确认阻塞事项")
        return {
            "action_bucket": "NEEDS_CONFIRMATION",
            "bucket_reasons": confirm_reasons,
            "ranking_suppressed": False,
            "suppression_reason": None,
        }

    # === DO_TODAY ===
    # D14 R2: a single unresolved logistics exception with a meaningful
    # 8-14 day delivery buffer still deserves same-day ETA follow-up, but not
    # the immediate-action semantics used for HIGH logistics risk.
    for sig in risk_signals:
        if sig.get("risk_type") == "LOGISTICS_EXCEPTION" and sig.get("severity") == "MEDIUM":
            delivery = _parse_dt(effective_delivery)
            if delivery:
                days = (delivery.date() - now.date()).days
                if 8 <= days <= 14:
                    return {
                        "action_bucket": "DO_TODAY",
                        "bucket_reasons": [f"物流异常距客户交期仍有{days}天缓冲，今日确认最新ETA"],
                        "ranking_suppressed": False,
                        "suppression_reason": None,
                    }

    # === D14 R4: high progress + credible next-day commitment protection ===
    # User validation repeatedly showed that a near-due order should not be
    # promoted to same-day chasing solely because the due date is 1-3 days
    # away when all of the following are true:
    #   - DELIVERY_RISK is the only real risk type
    #   - current progress is explicitly known and >= 90%
    #   - supplier has a concrete commitment for the next calendar day
    #   - that commitment is on/before the customer due date
    # This is deliberately narrow. Any quality/logistics/confirmation/source
    # conflict/overdue-commitment risk disables the protection. Due-today is
    # also never suppressed.
    if risk_types == {"DELIVERY_RISK"}:
        progress_value = order.get("current_progress")
        delivery = _parse_dt(effective_delivery)
        commitment = _parse_dt(order.get("latest_supplier_commitment"))
        try:
            progress = float(progress_value) if progress_value is not None else None
        except (TypeError, ValueError):
            progress = None
        if progress is not None and progress >= 0.90 and delivery and commitment:
            days_to_due = (delivery.date() - now.date()).days
            commitment_days = (commitment.date() - now.date()).days
            if 1 <= days_to_due <= 3 and commitment_days == 1 and commitment.date() < delivery.date():
                return {
                    "action_bucket": "SCHEDULED",
                    "bucket_reasons": [
                        f"当前进度{round(progress * 100)}%，供应商明确承诺明日完成且不晚于客户交期；按承诺节点复核"
                    ],
                    "ranking_suppressed": False,
                    "suppression_reason": None,
                }

    # Imminent but not overdue delivery should be handled within the current
    # business day.  The risk assessor intentionally classifies 0-1 days as
    # HIGH and 2-3 days as MEDIUM, so the bucket rule must accept both
    # severities; otherwise DO_TODAY is unreachable on the normal pipeline.
    for sig in risk_signals:
        if (
            sig.get("risk_type") == "DELIVERY_RISK"
            and sig.get("severity") in ("MEDIUM", "HIGH")
        ):
            delivery = _parse_dt(effective_delivery)
            if delivery:
                days = (delivery.date() - now.date()).days
                if 0 <= days <= 3:
                    if days == 0:
                        reason = "客户交期为今天"
                    elif days == 1:
                        reason = "客户交期在1天内"
                    else:
                        reason = f"客户交期在{days}天内，今日需推进"
                    return {
                        "action_bucket": "DO_TODAY",
                        "bucket_reasons": [reason],
                        "ranking_suppressed": False,
                        "suppression_reason": None,
                    }

    # === D14 R7: commitment-aware protection for moderate progress mismatch ===
    # Two independent new-case users agreed that 6-7 days remaining with
    # roughly half the work complete can stay on-plan when the supplier has a
    # concrete completion commitment at least one day before customer delivery
    # and DELIVERY_RISK is the only risk. This protection is intentionally
    # narrower than the R1 mismatch rule and never applies at 5 days or less.
    if risk_types == {"DELIVERY_RISK"}:
        progress_value = order.get("current_progress")
        delivery = _parse_dt(effective_delivery)
        commitment = _parse_dt(order.get("latest_supplier_commitment"))
        try:
            progress = float(progress_value) if progress_value is not None else None
        except (TypeError, ValueError):
            progress = None
        if progress is not None and 0.45 <= progress < 0.50 and delivery and commitment:
            days_to_due = (delivery.date() - now.date()).days
            commitment_buffer = (delivery.date() - commitment.date()).days
            commitment_days = (commitment.date() - now.date()).days
            if 6 <= days_to_due <= 7 and commitment_days > 0 and commitment_buffer >= 1:
                return {
                    "action_bucket": "SCHEDULED",
                    "bucket_reasons": [
                        f"当前进度{round(progress * 100)}%，距交期{days_to_due}天；供应商明确承诺在客户交期前{commitment_buffer}天完成，按承诺节点复核"
                    ],
                    "ranking_suppressed": False,
                    "suppression_reason": None,
                }

    # === D14 CALIBRATION: progress-to-deadline mismatch → DO_TODAY ===
    # Round-1 target-user blind tests consistently surfaced a gap in the
    # D7 bucket policy: a delivery risk can already be HIGH because progress
    # is far behind the remaining time (e.g. 30% complete with 5 days left),
    # yet the old bucket policy falls through to SCHEDULED because the due
    # date is outside the 0-3 day DO_TODAY window.
    #
    # Narrow fix: only promote when all of the following are true:
    #   - DELIVERY_RISK is HIGH
    #   - customer due date is 4-7 days away
    #   - current_progress is explicitly known and < 50%
    # This intentionally does NOT treat missing progress as 0%, and it does
    # not change high-progress near-due orders or 8+ day orders.
    progress_value = order.get("current_progress")
    if progress_value is not None:
        try:
            progress = float(progress_value)
        except (TypeError, ValueError):
            progress = None
        if progress is not None and progress < 0.5:
            delivery = _parse_dt(effective_delivery)
            if delivery:
                days = (delivery.date() - now.date()).days
                has_high_delivery_mismatch = any(
                    sig.get("risk_type") == "DELIVERY_RISK"
                    and sig.get("severity") == "HIGH"
                    for sig in risk_signals
                )
                if has_high_delivery_mismatch and 4 <= days <= 7:
                    # Stronger action only in the user-validated severe window:
                    # 4-5 days left with <50% progress. 6-7 days stays DO_TODAY
                    # so this calibration does not over-promote the wider band.
                    bucket = "DO_NOW" if days <= 5 else "DO_TODAY"
                    timing = "立即" if bucket == "DO_NOW" else "今日"
                    return {
                        "action_bucket": bucket,
                        "bucket_reasons": [
                            f"客户交期剩余{days}天，但当前进度仅{round(progress * 100)}%，{timing}需确认能否追回进度"
                        ],
                        "ranking_suppressed": False,
                        "suppression_reason": None,
                    }

    # === INFORMATION_GAP only → SCHEDULED (not a real risk) ===
    if is_info_gap_only:
        return {
            "action_bucket": "SCHEDULED",
            "bucket_reasons": ["当前无真实业务风险，仅信息不足"],
            "ranking_suppressed": False,
            "suppression_reason": None,
        }

    # === Default: SCHEDULED ===
    return {
        "action_bucket": "SCHEDULED",
        "bucket_reasons": ["尚未进入立即处理窗口"],
        "ranking_suppressed": False,
        "suppression_reason": None,
    }


# ---------------------------------------------------------------------------
# 8. Within-Bucket Ranking
# ---------------------------------------------------------------------------

def _deadline_attention(days_to_due: int | None) -> tuple[float, str | None]:
    """Continuous-ish deadline attention independent from action timing.

    This intentionally has more resolution than action buckets.  A due date can
    deserve high attention even when the correct current action is WAITING or
    SCHEDULED.
    """
    if days_to_due is None:
        return 8.0, "客户交期未知，保守保留基础关注度"
    if days_to_due < 0:
        overdue_days = abs(days_to_due)
        return 50.0 + min(20.0, overdue_days * 2.5), f"客户交期已过{overdue_days}天"
    if days_to_due <= 1:
        return 44.0, f"客户交期仅剩{days_to_due}天"
    if days_to_due <= 3:
        return 36.0, f"客户交期仅剩{days_to_due}天"
    if days_to_due <= 5:
        return 28.0, f"客户交期仅剩{days_to_due}天"
    if days_to_due <= 7:
        return 21.0, f"客户交期剩余{days_to_due}天"
    if days_to_due <= 10:
        return 13.0, f"客户交期剩余{days_to_due}天"
    if days_to_due <= 14:
        return 7.0, f"客户交期剩余{days_to_due}天"
    if days_to_due <= 21:
        return 3.0, None
    return 1.0, None


def _build_attention_context(
    order: dict[str, Any],
    risk_signals: list[dict[str, Any]],
    *,
    tasks: list[dict[str, Any]] | None,
    logistics: list[dict[str, Any]] | None,
    quality_events: list[dict[str, Any]] | None,
    fact_conflicts: list[dict[str, Any]] | None,
    current: datetime,
    effective_delivery_date: Any,
) -> dict[str, Any]:
    """Build structured inputs for D14.2 Risk Attention ranking.

    No free-text keyword extraction is used.  The score only consumes the
    deterministic facts already accepted by the D7 contracts.
    """
    delivery = _parse_dt(effective_delivery_date)
    days_to_due = (delivery.date() - current.date()).days if delivery else None

    commitment = _parse_dt(order.get("latest_supplier_commitment"))
    commitment_buffer_days = (
        (delivery.date() - commitment.date()).days if delivery and commitment else None
    )
    commitment_days_from_now = (
        (commitment.date() - current.date()).days if commitment else None
    )

    active_logistics = [
        e for e in (logistics or [])
        if str(e.get("status") or "").upper() in {"DELAYED", "EXCEPTION", "CUSTOMS_HOLD"}
        and not e.get("resolved_at")
    ]
    eta_values = [_parse_dt(e.get("estimated_arrival_at")) for e in active_logistics]
    logistics_all_eta_known = bool(eta_values) and all(v is not None for v in eta_values)
    latest_eta = max((v for v in eta_values if v is not None), default=None)
    logistics_eta_buffer_days = (
        (delivery.date() - latest_eta.date()).days if delivery and latest_eta else None
    )

    active_quality = []
    for event in quality_events or []:
        status = str(event.get("status") or "OPEN").upper()
        if event.get("resolved_at") or status in {"RESOLVED", "CLOSED", "CANCELLED"}:
            continue
        if int(event.get("is_delivery_blocking") or 0) == 1:
            active_quality.append(event)
    resolution_values = [_parse_dt(e.get("expected_resolution_at")) for e in active_quality]
    latest_quality_resolution = max((v for v in resolution_values if v is not None), default=None)
    quality_resolution_buffer_days = (
        (delivery.date() - latest_quality_resolution.date()).days
        if delivery and latest_quality_resolution else None
    )

    pending_confirmation = _pending_customer_confirmation_tasks(tasks)
    contact_ages: list[float] = []
    for task in pending_confirmation:
        contacted = _parse_dt(task.get("last_contact_at"))
        if contacted:
            age = (current - contacted).total_seconds() / 3600.0
            if age >= 0:
                contact_ages.append(age)
    max_confirmation_age_hours = max(contact_ages, default=None)

    waiting_tasks = [
        t for t in (tasks or [])
        if str(t.get("status") or "").upper() == "WAITING_EXTERNAL"
    ]
    active_waiting = any(
        (_parse_dt(t.get("promised_reply_at")) or _parse_dt(t.get("next_action_at")))
        and (_parse_dt(t.get("promised_reply_at")) or _parse_dt(t.get("next_action_at"))) > current
        for t in waiting_tasks
    )
    waiting_overdue = any(
        (_parse_dt(t.get("promised_reply_at")) or _parse_dt(t.get("next_action_at")))
        and (_parse_dt(t.get("promised_reply_at")) or _parse_dt(t.get("next_action_at"))) <= current
        for t in waiting_tasks
    )

    open_fact_conflicts = [
        c for c in (fact_conflicts or [])
        if not c.get("resolved_at") and str(c.get("status") or "OPEN").upper() not in {"RESOLVED", "CLOSED", "CANCELLED"}
    ]
    risk_types = {str(s.get("risk_type") or "") for s in risk_signals}

    return {
        "days_to_due": days_to_due,
        "supplier_commitment_buffer_days": commitment_buffer_days,
        "supplier_commitment_days_from_now": commitment_days_from_now,
        "unresolved_logistics_count": len(active_logistics),
        "logistics_all_eta_known": logistics_all_eta_known,
        "logistics_eta_buffer_days": logistics_eta_buffer_days,
        "quality_blocking_count": len(active_quality),
        "quality_resolution_buffer_days": quality_resolution_buffer_days,
        "pending_confirmation_count": len(pending_confirmation),
        "max_confirmation_age_hours": max_confirmation_age_hours,
        "active_external_waiting": bool(active_waiting),
        "waiting_overdue": bool(waiting_overdue),
        "open_fact_conflict_count": len(open_fact_conflicts),
        "risk_types": sorted(risk_types),
    }


def _actionability_from_bucket(bucket: str) -> str:
    return {
        "ESCALATE": "GOVERNANCE_OR_ESCALATION",
        "DO_NOW": "READY_NOW",
        "NEEDS_CONFIRMATION": "READY_CONFIRMATION",
        "DO_TODAY": "READY_TODAY",
        "SCHEDULED": "SCHEDULED",
        "WAITING_EXTERNAL": "WAITING_EXTERNAL",
        "NOT_MY_RESPONSIBILITY": "NOT_MY_RESPONSIBILITY",
        "DONE": "DONE",
    }.get(str(bucket or "SCHEDULED"), "SCHEDULED")


def compute_priority_score(
    item: dict[str, Any],
    current: datetime,
    policy_version: str = D7_POLICY_VERSION,
) -> dict[str, Any]:
    """Compute D14.2 Risk Attention Score.

    The old D7 score mixed *what action state an order is in* with *how much
    business attention it deserves*.  That caused governance items to jump to
    the top and valid WAITING items to disappear.  D14.2 deliberately separates:

      - risk_attention_score: how much attention the order deserves today;
      - current_actionability: what can be done right now;
      - governance_escalation_required: whether management/assignment is needed.

    `priority_score` remains as a backward-compatible alias for
    `risk_attention_score`.  It is a deterministic heuristic, never a
    probability.
    """
    ctx = dict(item.get("attention_context") or {})
    risk_types = set(ctx.get("risk_types") or item.get("anomaly_types") or [])
    days_to_due = ctx.get("days_to_due")
    if days_to_due is None:
        delivery_date = (
            item.get("effective_delivery_date")
            or item.get("requested_delivery_date")
            or item.get("customer_due_date")
        )
        delivery = _parse_dt(delivery_date)
        days_to_due = (delivery.date() - current.date()).days if delivery else None

    score, deadline_reason = _deadline_attention(days_to_due)
    reasons: list[str] = []
    if deadline_reason:
        reasons.append(deadline_reason)

    progress_value = item.get("current_progress")
    try:
        progress = float(progress_value) if progress_value is not None else None
    except (TypeError, ValueError):
        progress = None

    commitment_buffer = ctx.get("supplier_commitment_buffer_days")
    commitment_days_from_now = ctx.get("supplier_commitment_days_from_now")

    # ---- Hard delivery blockers / broken commitments ----
    if "QUALITY_BLOCKING" in risk_types:
        score += 24
        reasons.append("存在已确认阻塞交付的质量问题")
        q_buffer = ctx.get("quality_resolution_buffer_days")
        if q_buffer is None:
            score += 4
            reasons.append("质量问题解决时间仍不确定")
        elif q_buffer <= 0:
            score += 16
            reasons.append("质量问题预计解决时间已触及/晚于客户交期")
        elif q_buffer <= 2:
            score += 8
        elif q_buffer >= 7:
            score -= 5
            reasons.append("质量返工预计完成后仍有较大交期缓冲")

    if "SUPPLIER_COMMITMENT_OVERDUE" in risk_types:
        score += 24
        reasons.append("供应商正式完工承诺已逾期")

    # ---- Delivery progress / supplier commitment buffer ----
    if "DELIVERY_RISK" in risk_types:
        score += 4
        if commitment_buffer is not None and commitment_buffer <= 0 and (days_to_due is None or days_to_due >= 0):
            score += 24
            reasons.append("供应商完成日与客户交期无正向缓冲")
        elif commitment_buffer is not None and commitment_buffer >= 2:
            # A healthy explicit commitment reduces uncertainty only when the
            # order is not currently sitting on an unresolved external waiting
            # checkpoint.  Waiting changes contact timing, not the need to watch
            # the checkpoint.
            if risk_types == {"DELIVERY_RISK"} and not ctx.get("active_external_waiting"):
                score -= 8
                reasons.append(f"供应商明确承诺较客户交期提前{commitment_buffer}天")

        if commitment_days_from_now is None and progress is not None and progress < 0.50:
            if days_to_due is not None and days_to_due <= 5:
                score += 22
                reasons.append("近交期且进度不足一半，同时缺少明确供应商承诺")
            elif days_to_due is not None and days_to_due <= 7:
                score += 15
                reasons.append("7天内进度不足一半且缺少明确供应商承诺")
            elif days_to_due is not None and days_to_due <= 10:
                score += 8
        if progress is not None and progress < 0.35 and days_to_due is not None and days_to_due <= 5:
            score += 6
            reasons.append("当前进度严重落后于剩余交期")
        if days_to_due is not None and days_to_due < 0 and progress is not None:
            if progress < 0.50:
                score += 10
                reasons.append("订单已逾期且当前进度仍不足一半")
            elif progress < 0.70:
                score += 5
                reasons.append("订单已逾期且仍有较多生产未完成")
        if (
            progress is not None and progress >= 0.90
            and commitment_buffer is not None and commitment_buffer >= 1
            and risk_types == {"DELIVERY_RISK"}
        ):
            score -= 7
            reasons.append("高进度且存在正向供应商交付缓冲")
        elif (
            progress is not None and progress >= 0.80
            and commitment_buffer is not None and commitment_buffer >= 2
            and risk_types == {"DELIVERY_RISK"}
            and not ctx.get("active_external_waiting")
        ):
            score -= 5
            reasons.append("较高进度且供应商承诺留有至少2天缓冲")

    # ---- Customer confirmation: attention depends on time, not just count ----
    if "CUSTOMER_CONFIRMATION_BLOCKING" in risk_types:
        score += 6
        if days_to_due is not None and days_to_due <= 7:
            score += 5
        age = ctx.get("max_confirmation_age_hours")
        if age is not None and age <= 4:
            score -= 5 if days_to_due is not None and days_to_due >= 8 else 2
            reasons.append("客户确认刚发出，仍在合理回复窗口")
        elif age is not None and age >= 18:
            score += 5
            reasons.append("客户确认已等待较长时间")
        if ctx.get("waiting_overdue"):
            score += 10
            reasons.append("对方承诺的回复时间已经到期")

    # ---- Source conflict: near-due conflicts matter much more than distant ones ----
    if "SOURCE_CONFLICT" in risk_types:
        score += 9
        if days_to_due is not None and days_to_due <= 3:
            score += 5
            reasons.append("交期临近且关键事实来源冲突")

    # ---- Logistics: structured ETA buffer controls attention ----
    if "LOGISTICS_EXCEPTION" in risk_types:
        score += 4
        eta_buffer = ctx.get("logistics_eta_buffer_days")
        all_eta_known = bool(ctx.get("logistics_all_eta_known"))
        if not all_eta_known or eta_buffer is None:
            score += 7
            reasons.append("物流异常仍缺少可靠ETA")
        elif eta_buffer <= 0:
            score += 22
            reasons.append("当前ETA已触及/晚于客户交期")
        elif eta_buffer <= 2:
            score += 8
            reasons.append(f"当前ETA仅比客户交期早{eta_buffer}天")
        elif eta_buffer <= 6:
            score += 2
            reasons.append(f"当前ETA仍比客户交期早{eta_buffer}天")
        else:
            score -= 2
        if int(ctx.get("unresolved_logistics_count") or 0) > 1:
            score += 2

    # ---- Governance: visible, but deliberately not equal to business urgency ----
    if "OWNER_MISSING" in risk_types:
        score += 7
        reasons.append("业务负责人缺失，需要治理动作但不等同于最高业务风险")

    # Waiting controls external contact timing only.  It gets a small checkpoint
    # attention floor rather than the old -1000 suppression penalty.
    if ctx.get("active_external_waiting"):
        score += 2
        reasons.append("当前处于有效外部等待窗口；不重复催办，但保留复核关注度")

    score = max(0.0, min(100.0, round(float(score), 2)))
    bucket = str(item.get("action_bucket") or "SCHEDULED")
    governance_required = bool("OWNER_MISSING" in risk_types or bucket == "ESCALATE")
    attention_band = (
        "CRITICAL_ATTENTION" if score >= 60 else
        "HIGH_ATTENTION" if score >= 45 else
        "MEDIUM_ATTENTION" if score >= 28 else
        "LOW_ATTENTION"
    )

    return {
        "priority_score": score,  # backward-compatible alias
        "risk_attention_score": score,
        "risk_attention_band": attention_band,
        "risk_attention_reasons": reasons or ["基于确定性风险关注度规则排序"],
        "priority_reasons": reasons or ["基于确定性风险关注度规则排序"],
        "current_actionability": _actionability_from_bucket(bucket),
        "governance_escalation_required": governance_required,
        "ranking_rule_version": ATTENTION_RANKING_VERSION,
        "is_heuristic": True,
        "score_description": (
            "Deterministic Risk Attention heuristic. It ranks business attention independently "
            "from current action timing and governance escalation. NOT a probability. "
            f"Policy version: {policy_version}; ranking version: {ATTENTION_RANKING_VERSION}."
        ),
    }


# ---------------------------------------------------------------------------
# 9. Order Ranking (Risk-Attention First)
# ---------------------------------------------------------------------------

def rank_orders(
    order_results: list[dict[str, Any]],
    top_n: int = 7,
    current: datetime | None = None,
    policy_version: str = D7_POLICY_VERSION,
) -> dict[str, Any]:
    """Rank unique risk orders by business Risk Attention.

    D14.2 explicitly rejects the old `bucket-first` ordering.  Action bucket is
    still returned and still controls *what to do*, but it no longer decides
    *how much attention the order deserves*.

    INFORMATION_GAP-only items stay outside the risk Top-N.  DONE and
    NOT_MY_RESPONSIBILITY are not surfaced in the current user's attention list.
    """
    _ = current or datetime.now(CN_TZ)
    top_n = max(1, min(int(top_n or 7), 200))

    _ACTIONABLE_BUCKETS = frozenset({"DO_NOW", "ESCALATE", "NEEDS_CONFIRMATION"})
    real_risk_items: list[dict[str, Any]] = []
    info_gap_items: list[dict[str, Any]] = []

    for item in order_results:
        risk_signals = item.get("risk_signals") or []
        risk_types = {s["risk_type"] for s in risk_signals}
        action_bucket = item.get("action_bucket", "")
        is_pure_info_gap = risk_types == {"INFORMATION_GAP"} or not risk_signals
        if is_pure_info_gap and action_bucket not in _ACTIONABLE_BUCKETS:
            info_gap_items.append(item)
            continue
        if action_bucket in {"DONE", "NOT_MY_RESPONSIBILITY"}:
            continue
        real_risk_items.append(item)

    def _sort_key(item: dict[str, Any]) -> tuple:
        attention = float(item.get("risk_attention_score") or item.get("priority_score") or 0)
        no = str(item.get("order_no") or item.get("order_id") or "")
        oid = str(item.get("order_id") or "")
        return (-attention, no, oid)

    real_risk_items.sort(key=_sort_key)
    selected = real_risk_items[:top_n]
    for i, item in enumerate(selected, 1):
        item["rank"] = i

    gap_by_order: dict[str, dict[str, Any]] = {}
    for gap in info_gap_items:
        key = str(gap.get("order_id") or "")
        if key and key not in gap_by_order:
            gap_by_order[key] = gap

    return {
        "risk_items": selected,
        "risk_order_count": len(real_risk_items),
        "information_gaps": list(gap_by_order.values()),
        "information_gap_order_count": len(gap_by_order),
        "selection_strategy": {
            "not_padded": True,
            "unit": "unique_order",
            "max_items": top_n,
            "bucket_first": False,
            "attention_first": True,
            "attention_score_field": "risk_attention_score",
            "action_bucket_independent": True,
            "governance_independent": True,
            "policy_version": policy_version,
            "ranking_rule_version": ATTENTION_RANKING_VERSION,
        },
        "policy_version": policy_version,
    }


# ---------------------------------------------------------------------------
# 10. Full D7 Pipeline
# ---------------------------------------------------------------------------

def _conn_exec(conn: Any, sql: str, params: dict[str, Any] | tuple | list | None = None) -> Any:
    """Execute SQL against either a raw Connection or DatabaseConnWrapper.

    SQLAlchemy 2.0 raw Connection requires TextClause + dict params.
    DatabaseConnWrapper accepts str SQL + either tuples or dicts.
    """
    is_wrapper = hasattr(conn, "_conn")
    if is_wrapper:
        return conn.execute(sql, params)
    if params is None:
        return conn.execute(text(sql))
    if isinstance(params, dict):
        return conn.execute(text(sql), params)
    pos_dict = {f"p{i}": v for i, v in enumerate(params)}
    counter = [0]
    def _replace(_: re.Match) -> str:
        counter[0] += 1
        return f":p{counter[0] - 1}"
    converted_sql = re.sub(r"\?", _replace, sql)
    return conn.execute(text(converted_sql), pos_dict)


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Convert a SQLAlchemy Row or wrapper row to dict."""
    if hasattr(row, "_asdict"):
        return row._asdict()
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    if hasattr(row, "keys"):
        return {k: row[k] for k in row.keys()}
    return dict(row)


def run_d7_pipeline(
    conn: Any,
    identity: Any,
    *,
    top_n: int = 7,
    due_within_days: int = 14,
    current_time: str | None = None,
    policy_version: str = D7_POLICY_VERSION,
    include_erp_snapshot: bool = True,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the full D7 pipeline: facts → risk signals → action buckets → ranking.

    This is the main entry point for D7 risk assessment.

    Identity Resolution:
      Supports CurrentIdentity dataclass (production) and dict (test helper).
      Organization hard filter is ALWAYS enforced (D3 frozen rule).
    """
    now = _parse_dt(current_time) or datetime.now(CN_TZ)
    user_id, org_id, user_role = _extract_identity_fields(identity)

    # Import table_exists locally to avoid circular imports
    from database import table_exists

    # Fail-closed: if identity cannot be resolved, return empty result
    if not user_id or not org_id or not user_role:
        return {
            "policy_version": policy_version,
            "generated_at": _now_iso(),
            "current_time": now.isoformat(timespec="seconds"),
            "scope": {
                "organization_id": org_id,
                "user_id": user_id,
                "user_role": user_role,
            },
            "screened_order_count": 0,
            "risk_signal_count": 0,
            "risk_order_count": 0,
            "information_gap_order_count": 0,
            "items": [],
            "information_gaps": [],
            "count": 0,
            "action_case_observations": [],
            "error": "IDENTITY_NOT_RESOLVED",
            "message": "Identity could not be resolved. Ensure a valid CurrentIdentity is passed.",
        }

    # 1. Get orders in scope — D3 organization hard filter ALWAYS enforced
    is_mgr = str(user_role or "").lower() in {"manager", "admin", "supervisor"}
    if is_mgr:
        # Manager: see all orders within their organization ONLY
        rows = [_row_to_dict(r) for r in _conn_exec(
            conn,
            "SELECT * FROM orders WHERE organization_id=? AND UPPER(COALESCE(status,'ACTIVE')) NOT IN ('DONE','CLOSED','CANCELLED','COMPLETED')",
            (org_id,)
        )]
    else:
        # Operator: see only their own orders within their organization
        rows = [_row_to_dict(r) for r in _conn_exec(
            conn,
            "SELECT * FROM orders WHERE organization_id=? AND owner=? AND UPPER(COALESCE(status,'ACTIVE')) NOT IN ('DONE','CLOSED','CANCELLED','COMPLETED')",
            (org_id, user_id)
        )]

    # 2. Bridge ERP snapshot facts for each order (if available)
    erp_facts_map: dict[str, dict[str, Any]] = {}
    if include_erp_snapshot and table_exists(conn, "erp_read_snapshots"):
        for row in rows:
            order_id = row["order_id"]
            # Find matching ERP snapshot by external_id
            ext_id = None
            if row.get("order_no"):
                snap_row = _conn_exec(
                    conn,
                    "SELECT snapshot_id, external_id, source_modified_at, normalized_json, fetched_at FROM erp_read_snapshots WHERE organization_id=? AND doctype='Sales Order' AND external_id=? ORDER BY fetched_at DESC LIMIT 1",
                    (org_id or "ORG-DEMO", row["order_no"]),
                ).fetchone()
                if snap_row:
                    snap_dict = _row_to_dict(snap_row)
                    normalized = json.loads(snap_dict["normalized_json"]) if isinstance(snap_dict["normalized_json"], str) else snap_dict["normalized_json"]
                    sync_state = None
                    if table_exists(conn, "erp_sync_state"):
                        sync_row = _conn_exec(
                            conn,
                            "SELECT * FROM erp_sync_state WHERE organization_id=? AND doctype='Sales Order'",
                            (org_id or "ORG-DEMO",),
                        ).fetchone()
                        sync_state = _row_to_dict(sync_row) if sync_row else None
                    erp_facts_map[order_id] = bridge_erp_snapshot_to_facts(
                        {
                            "snapshot_id": snap_dict["snapshot_id"],
                            "external_id": snap_dict["external_id"],
                            "source_modified_at": snap_dict["source_modified_at"],
                            "normalized": normalized,
                            "fetched_at": snap_dict["fetched_at"],
                        },
                        sync_state,
                    )

    # 3. Process each order
    order_results: list[dict[str, Any]] = []
    action_case_observations: list[dict[str, Any]] = []
    for row in rows:
        order_id = row["order_id"]
        erp_facts = erp_facts_map.get(order_id)

        # Get related tasks
        tasks = [_row_to_dict(r) for r in _conn_exec(
            conn,
            "SELECT * FROM tasks WHERE related_order_id=? AND status!='DONE'",
            (order_id,),
        )]

        # Get logistics events
        logistics = [_row_to_dict(r) for r in _conn_exec(
            conn,
            "SELECT * FROM logistics_events WHERE order_id=? ORDER BY COALESCE(event_time,created_at) DESC",
            (order_id,),
        )]

        # Get structured quality events when the D14 Quality contract exists.
        # Older test/legacy databases may not have the table yet; fail safely to [].
        quality_events = []
        if table_exists(conn, "quality_events"):
            quality_events = [_row_to_dict(r) for r in _conn_exec(
                conn,
                "SELECT * FROM quality_events WHERE order_id=? ORDER BY COALESCE(event_time,created_at) DESC",
                (order_id,),
            )]

        # Get structured fact conflicts when the D14 R6 contract exists.
        fact_conflicts = []
        if table_exists(conn, "fact_conflicts"):
            fact_conflicts = [_row_to_dict(r) for r in _conn_exec(
                conn,
                "SELECT * FROM fact_conflicts WHERE order_id=? AND resolved_at IS NULL ORDER BY detected_at DESC",
                (order_id,),
            )]

        # Get commitments
        commitments = [_row_to_dict(r) for r in _conn_exec(
            conn,
            "SELECT * FROM commitment_history WHERE order_id=? ORDER BY created_at DESC LIMIT 30",
            (order_id,),
        )]

        # Assess risks
        risk_signals = assess_risks_from_facts(
            row,
            current=now,
            erp_facts=erp_facts,
            tasks=tasks,
            logistics=logistics,
            quality_events=quality_events,
            fact_conflicts=fact_conflicts,
            commitments=commitments,
        )

        if not risk_signals:
            # Order was fully screened but has zero risk signals.
            # It MUST be included in action_case_observations so D8
            # can properly mark old cases as NOT_OBSERVED when risks
            # truly disappear.
            action_case_observations.append({
                **row,
                "risk_signals": [],
                "evidence": [],
                "missing_information": [],
                "severity": None,
                "action_bucket": None,
                "recommended_action": None,
                "is_screened": True,
            })
            continue

        # Compute effective delivery date for this order (single source of truth)
        delivery_info = _compute_effective_delivery_date(row, erp_facts)
        effective_delivery_date = delivery_info["effective_delivery_date"]

        # Assign action bucket
        bucket_result = assign_action_bucket(
            risk_signals,
            row,
            tasks=tasks,
            current=now,
            user_id=user_id,
            user_role=user_role,
        )

        # Compute priority score
        # Aggregate severity: highest severity from all risk signals wins
        _severity_order = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}
        max_severity = "LOW"
        for sig in risk_signals:
            sig_sev = sig.get("severity", "LOW")
            if _severity_order.get(sig_sev, 0) > _severity_order.get(max_severity, 0):
                max_severity = sig_sev

        attention_context = _build_attention_context(
            row,
            risk_signals,
            tasks=tasks,
            logistics=logistics,
            quality_events=quality_events,
            fact_conflicts=fact_conflicts,
            current=now,
            effective_delivery_date=effective_delivery_date,
        )

        item_base = {
            **row,
            "effective_delivery_date": effective_delivery_date,
            "delivery_source": delivery_info["delivery_source"],
            "delivery_source_id": delivery_info["delivery_source_id"],
            "delivery_source_modified_at": delivery_info["delivery_source_modified_at"],
            "delivery_fetched_at": delivery_info["delivery_fetched_at"],
            "delivery_freshness": delivery_info["delivery_freshness"],
            "risk_signals": risk_signals,
            "action_bucket": bucket_result["action_bucket"],
            "bucket_reasons": bucket_result["bucket_reasons"],
            "ranking_suppressed": bucket_result["ranking_suppressed"],
            "suppression_reason": bucket_result.get("suppression_reason"),
            "order_anomaly_count": len(risk_signals),
            "anomaly_types": [s["risk_type"] for s in risk_signals],
            "primary_anomaly_type": risk_signals[0]["risk_type"] if risk_signals else None,
            "secondary_anomaly_types": [s["risk_type"] for s in risk_signals[1:]],
            "evidence": [e for s in risk_signals for e in (s.get("evidence") or [])],
            "missing_information": [m for s in risk_signals for m in (s.get("missing_information") or [])],
            "recommended_action": _merge_recommended_actions(risk_signals, bucket_result),
            "freshness": _determine_overall_freshness(risk_signals, erp_facts),
            "data_quality_flag": _determine_data_quality_flag(risk_signals),
            "severity": max_severity,
            "attention_context": attention_context,
        }

        score_result = compute_priority_score(item_base, now, policy_version)
        item_base["priority_score"] = score_result["priority_score"]
        item_base["risk_attention_score"] = score_result["risk_attention_score"]
        item_base["risk_attention_band"] = score_result["risk_attention_band"]
        item_base["risk_attention_reasons"] = score_result["risk_attention_reasons"]
        item_base["priority_reasons"] = score_result["priority_reasons"]
        item_base["current_actionability"] = score_result["current_actionability"]
        item_base["governance_escalation_required"] = score_result["governance_escalation_required"]
        item_base["ranking_rule_version"] = score_result["ranking_rule_version"]
        item_base["is_heuristic"] = score_result["is_heuristic"]
        item_base["score_description"] = score_result["score_description"]

        order_results.append(item_base)
        action_case_observations.append(item_base)

    # 4. Role-aware queue separation and ranking
    if is_mgr:
        # D14.2: one cross-team Risk Attention list is the manager's focus window.
        # Operational assigned/unassigned sections remain separate for execution.
        ranked_attention = rank_orders(order_results, top_n=top_n, current=now, policy_version=policy_version)

        # Manager: separate assigned vs unassigned orders
        assigned_results: list[dict[str, Any]] = []
        unassigned_results: list[dict[str, Any]] = []
        for item in order_results:
            owner_val = str(item.get("owner") or "").strip()
            if owner_val and owner_val not in ("", "待分配", "未分配", "-", "—"):
                assigned_results.append(item)
            else:
                unassigned_results.append(item)

        # Rank assigned items (same ranking engine, same top_n)
        ranked_assigned = rank_orders(assigned_results, top_n=top_n, current=now, policy_version=policy_version)

        # Rank unassigned items separately — no Top N truncation for manager visibility
        ranked_unassigned = rank_orders(
            unassigned_results, top_n=max(200, len(unassigned_results) + 1),
            current=now, policy_version=policy_version,
        )

        team_items = ranked_assigned["risk_items"]
        unassigned_items = ranked_unassigned["risk_items"]
        team_info_gaps = ranked_assigned["information_gaps"]
        unassigned_info_gaps = ranked_unassigned["information_gaps"]

        # Merge unassigned info gaps into unassigned_orders for full visibility
        all_unassigned = unassigned_items + unassigned_info_gaps

        return {
            "policy_version": policy_version,
            "generated_at": _now_iso(),
            "current_time": now.isoformat(timespec="seconds"),
            "scope": {
                "organization_id": org_id,
                "user_id": user_id,
                "user_role": user_role,
            },
            "screened_order_count": len(rows),
            "risk_signal_count": sum(len(r.get("risk_signals") or []) for r in order_results),
            "risk_order_count": ranked_assigned["risk_order_count"],
            "information_gap_order_count": ranked_assigned["information_gap_order_count"],
            "risk_attention_items": ranked_attention["risk_items"],
            "team_action_items": team_items,
            "unassigned_orders": all_unassigned,
            "information_gaps": team_info_gaps,
            "items": team_items,  # backward compat → team_action_items
            "count": len(team_items),
            "unassigned_order_count": len(all_unassigned),
            "selection_strategy": ranked_assigned["selection_strategy"],
            "human_confirmation_required": True,
            "action_case_observations": action_case_observations,
            "d7_features": {
                "risk_signal_contract": True,
                "bucket_first_ranking": False,
                "risk_attention_ranking": True,
                "actionability_separated": True,
                "governance_separated": True,
                "action_bucket_policy": True,
                "freshness_boundary": True,
                "owner_missing_detection": True,
                "source_conflict_detection": True,
                "erp_snapshot_bridge": include_erp_snapshot,
                "no_erp_owner_mapping": True,
                "information_gap_independent": True,
                "not_padded": True,
                "one_order_one_rank": True,
                "waiting_suppression": True,
                "deterministic_scoring": True,
                "role_aware_queue": True,
            },
        }
    else:
        # Operator: keep existing behavior, exposed as my_action_items
        ranked = rank_orders(order_results, top_n=top_n, current=now, policy_version=policy_version)

        return {
            "policy_version": policy_version,
            "generated_at": _now_iso(),
            "current_time": now.isoformat(timespec="seconds"),
            "scope": {
                "organization_id": org_id,
                "user_id": user_id,
                "user_role": user_role,
            },
            "screened_order_count": len(rows),
            "risk_signal_count": sum(len(r.get("risk_signals") or []) for r in order_results),
            "risk_order_count": ranked["risk_order_count"],
            "information_gap_order_count": ranked["information_gap_order_count"],
            "risk_attention_items": ranked["risk_items"],
            "my_action_items": ranked["risk_items"],
            "information_gaps": ranked["information_gaps"],
            "items": ranked["risk_items"],  # backward compat → my_action_items
            "count": len(ranked["risk_items"]),
            "selection_strategy": ranked["selection_strategy"],
            "human_confirmation_required": True,
            "action_case_observations": action_case_observations,
            "d7_features": {
                "risk_signal_contract": True,
                "bucket_first_ranking": False,
                "risk_attention_ranking": True,
                "actionability_separated": True,
                "governance_separated": True,
                "action_bucket_policy": True,
                "freshness_boundary": True,
                "owner_missing_detection": True,
                "source_conflict_detection": True,
                "erp_snapshot_bridge": include_erp_snapshot,
                "no_erp_owner_mapping": True,
                "information_gap_independent": True,
                "not_padded": True,
                "one_order_one_rank": True,
                "waiting_suppression": True,
                "deterministic_scoring": True,
                "role_aware_queue": True,
            },
        }


def _merge_recommended_actions(risk_signals: list[dict[str, Any]], bucket_result: dict[str, Any]) -> str:
    """Merge recommended actions from risk signals and bucket decision."""
    actions: list[str] = []
    for sig in risk_signals:
        action_map = {
            "DELIVERY_RISK": "核对生产、验货和出运节点",
            "SUPPLIER_COMMITMENT_OVERDUE": "确认工厂实际进度、明确完工时间",
            "CUSTOMER_CONFIRMATION_BLOCKING": "联系客户确认阻塞事项",
            "LOGISTICS_EXCEPTION": "向货代确认最新节点与补救方案",
            "QUALITY_BLOCKING": "确认返工/重检完成时间与可行替代方案",
            "OWNER_MISSING": "指派业务负责人",
            "SOURCE_CONFLICT": "人工确认冲突事实",
            "INFORMATION_GAP": "补充缺失信息后再诊断",
        }
        action = action_map.get(sig.get("risk_type", ""))
        if action and action not in actions:
            actions.append(action)

    bucket_action_map = {
        "ESCALATE": "请求主管介入",
        "DO_NOW": "立即处理",
        "NEEDS_CONFIRMATION": "人工确认",
        "DO_TODAY": "今日处理",
        "SCHEDULED": "按计划处理",
        "WAITING_EXTERNAL": "等待外部回复",
        "NOT_MY_RESPONSIBILITY": "转交给正确负责人",
        "DONE": "无需处理",
    }
    bucket = bucket_result.get("action_bucket", "")
    bucket_action = bucket_action_map.get(bucket)
    if bucket_action:
        actions.insert(0, bucket_action)

    return "；".join(actions) if actions else "按规则处理"


def _determine_overall_freshness(risk_signals: list[dict[str, Any]], erp_facts: dict[str, Any] | None) -> str:
    """Determine overall data freshness for an order."""
    freshnesses = {s.get("freshness", "FRESH") for s in risk_signals}
    if erp_facts:
        freshnesses.add(erp_facts.get("freshness", "FRESH"))
    if "UNAVAILABLE" in freshnesses:
        return "UNAVAILABLE"
    if "NEVER_SYNCED" in freshnesses:
        return "NEVER_SYNCED"
    if "STALE" in freshnesses:
        return "STALE"
    return "FRESH"


def _determine_data_quality_flag(risk_signals: list[dict[str, Any]]) -> list[str]:
    """Determine data quality flags from risk signals."""
    flags: list[str] = []
    for sig in risk_signals:
        f = sig.get("freshness", "FRESH")
        if f == "STALE" and "STALE_DATA" not in flags:
            flags.append("STALE_DATA")
        if f == "UNAVAILABLE" and "UNAVAILABLE_DATA" not in flags:
            flags.append("UNAVAILABLE_DATA")
        if f == "NEVER_SYNCED" and "NO_ERP_DATA" not in flags:
            flags.append("NO_ERP_DATA")
    return flags


# ---------------------------------------------------------------------------
# 11. Exports
# ---------------------------------------------------------------------------

__all__ = [
    "D7_POLICY_VERSION",
    "ATTENTION_RANKING_VERSION",
    "RISK_TYPES",
    "ALL_RISK_TYPES",
    "SEVERITY_LEVELS",
    "ACTION_BUCKETS",
    "build_risk_signal",
    "bridge_erp_snapshot_to_facts",
    "assess_risks_from_facts",
    "assign_action_bucket",
    "compute_priority_score",
    "rank_orders",
    "run_d7_pipeline",
    "bucket_priority",
    "boundary_freshness_label",
]