"""Shared deterministic validation for business values that must be machine-safe.

This module is deliberately model-independent so D13, D12 and D10 can enforce
exactly the same date / datetime contract before a value is persisted or queued
for an external adapter.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Iterable


class BusinessValueValidationError(ValueError):
    pass


DATE_ONLY_FIELDS = {
    "supplier_commitment_date",
    "customer_delivery_date",
    # Legacy / non-D13 D12 callers may still use this name for the same action.
    "expected_delivery_date",
}

DATETIME_FIELDS = {
    "promised_reply_at",
    "due_at",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_iso_date(field: str, value: Any) -> None:
    raw = str(value or "").strip()
    if not _DATE_RE.fullmatch(raw):
        raise BusinessValueValidationError(f"{field} must be ISO date YYYY-MM-DD")
    try:
        date.fromisoformat(raw)
    except ValueError as exc:
        raise BusinessValueValidationError(f"{field} is not a valid calendar date") from exc


def _validate_iso_datetime(field: str, value: Any) -> None:
    raw = str(value or "").strip()
    # Datetime fields are execution / waiting boundaries. Require an explicit
    # time component and timezone offset so "tomorrow 3pm" cannot be persisted
    # as a locale-dependent string.
    if "T" not in raw:
        raise BusinessValueValidationError(
            f"{field} must be ISO datetime with timezone, e.g. 2026-08-18T15:00:00+08:00"
        )
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise BusinessValueValidationError(f"{field} is not a valid ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BusinessValueValidationError(f"{field} must include a timezone offset")


def validate_business_dates(payload: dict[str, Any], *, fields: Iterable[str] | None = None) -> None:
    """Validate known date-like fields if they are present.

    ``fields`` can narrow validation to the subset meaningful for a particular
    action/tool. Unknown payload keys remain the caller's responsibility.
    """
    if not isinstance(payload, dict):
        raise BusinessValueValidationError("payload must be an object")
    selected = set(fields) if fields is not None else (DATE_ONLY_FIELDS | DATETIME_FIELDS)
    for field in selected:
        if field not in payload or payload.get(field) in (None, ""):
            continue
        if field in DATE_ONLY_FIELDS:
            _validate_iso_date(field, payload[field])
        elif field in DATETIME_FIELDS:
            _validate_iso_datetime(field, payload[field])


ACTION_DATE_FIELDS: dict[str, tuple[str, ...]] = {
    "RECORD_CONTACT": ("promised_reply_at",),
    "SET_WAITING": ("promised_reply_at",),
    "UPDATE_INTERNAL_PLAN": ("due_at",),
    "RECORD_SUPPLIER_COMMITMENT": ("supplier_commitment_date",),
    "UPDATE_EXPECTED_DELIVERY_DATE": ("customer_delivery_date", "expected_delivery_date"),
    "UPDATE_CUSTOMER_COMMITMENT": ("customer_delivery_date", "expected_delivery_date"),
}


def validate_action_dates(action_type: str, payload: dict[str, Any]) -> None:
    fields = ACTION_DATE_FIELDS.get(str(action_type or "").strip().upper(), ())
    validate_business_dates(payload, fields=fields)
