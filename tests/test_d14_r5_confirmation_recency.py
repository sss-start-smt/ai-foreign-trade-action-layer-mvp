from __future__ import annotations

from datetime import datetime, timedelta, timezone

from d7_risk_engine import assess_risks_from_facts, assign_action_bucket, build_risk_signal

CN_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=CN_TZ)


def _order(**overrides):
    base = {
        "order_id": "ORD-R5",
        "order_no": "SO-R5",
        "organization_id": "ORG-A",
        "owner": "USER-A",
        "requested_delivery_date": "2026-08-30",
        "latest_supplier_commitment": "2026-08-26",
        "current_progress": 0.70,
        "current_node": "生产中/待客户确认",
        "status": "ACTIVE",
        "updated_at": "2026-08-19T09:30:00+08:00",
    }
    base.update(overrides)
    return base


def _tasks(last_contact="2026-08-19T09:00:00+08:00"):
    return [
        {
            "task_id": f"T-R5-{idx}",
            "title": title,
            "target": "customer",
            "status": "OPEN",
            "pending_confirmation": 1,
            "last_contact_at": last_contact,
        }
        for idx, title in enumerate(
            ["确认颜色样", "确认包装方式", "确认最终收货窗口"], start=1
        )
    ]


def _evaluate(order=None, tasks=None, extra_signals=None):
    order = order or _order()
    tasks = tasks if tasks is not None else _tasks()
    signals = assess_risks_from_facts(order, current=NOW, tasks=tasks)
    signals.extend(extra_signals or [])
    bucket = assign_action_bucket(
        signals, order, tasks=tasks, current=NOW, user_id="USER-A", user_role="operator"
    )
    return signals, bucket


def test_recent_confirmation_request_with_long_buffer_is_scheduled():
    signals, bucket = _evaluate()
    assert "CUSTOMER_CONFIRMATION_BLOCKING" in {s["risk_type"] for s in signals}
    assert bucket["action_bucket"] == "SCHEDULED"
    assert "正常回复时间" in bucket["bucket_reasons"][0]
    confirmation = next(s for s in signals if s["risk_type"] == "CUSTOMER_CONFIRMATION_BLOCKING")
    assert any("合理回复窗口" in x for x in confirmation["evidence"])


def test_old_confirmation_contact_keeps_needs_confirmation():
    _, bucket = _evaluate(tasks=_tasks("2026-08-18T15:00:00+08:00"))
    assert bucket["action_bucket"] == "NEEDS_CONFIRMATION"


def test_recent_contact_does_not_suppress_near_due_confirmation():
    _, bucket = _evaluate(order=_order(requested_delivery_date="2026-08-25"))
    assert bucket["action_bucket"] == "NEEDS_CONFIRMATION"


def test_missing_contact_timestamp_fails_closed():
    tasks = _tasks()
    tasks[1]["last_contact_at"] = None
    _, bucket = _evaluate(tasks=tasks)
    assert bucket["action_bucket"] == "NEEDS_CONFIRMATION"


def test_four_hour_boundary_is_protected_but_older_is_not():
    _, at_boundary = _evaluate(tasks=_tasks("2026-08-19T06:00:00+08:00"))
    assert at_boundary["action_bucket"] == "SCHEDULED"

    _, older = _evaluate(tasks=_tasks("2026-08-19T05:59:00+08:00"))
    assert older["action_bucket"] == "NEEDS_CONFIRMATION"


def test_source_conflict_disables_recency_protection():
    conflict = build_risk_signal(
        order_id="ORD-R5",
        order_no="SO-R5",
        risk_type="SOURCE_CONFLICT",
        severity="MEDIUM",
        evidence=["两个可信来源交期不一致"],
        organization_id="ORG-A",
    )
    _, bucket = _evaluate(extra_signals=[conflict])
    assert bucket["action_bucket"] == "NEEDS_CONFIRMATION"


def test_future_contact_timestamp_never_creates_waiting_window():
    _, bucket = _evaluate(tasks=_tasks("2026-08-19T10:30:00+08:00"))
    assert bucket["action_bucket"] == "NEEDS_CONFIRMATION"
