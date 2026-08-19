from __future__ import annotations

from datetime import datetime, timedelta, timezone

from d7_risk_engine import assess_risks_from_facts, assign_action_bucket

CN_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=CN_TZ)


def _order(**overrides):
    base = {
        "order_id": "ORD-R4",
        "order_no": "SO-R4",
        "organization_id": "ORG-A",
        "owner": "USER-A",
        "requested_delivery_date": "2026-08-21",
        "latest_supplier_commitment": "2026-08-20",
        "current_progress": 0.94,
        "current_node": "包装/待出货",
        "status": "ACTIVE",
        "updated_at": "2026-08-19T09:30:00+08:00",
    }
    base.update(overrides)
    return base


def _bucket(order, *, quality_events=None, logistics=None, tasks=None):
    signals = assess_risks_from_facts(
        order,
        current=NOW,
        quality_events=quality_events or [],
        logistics=logistics or [],
        tasks=tasks or [],
    )
    return signals, assign_action_bucket(
        signals, order, current=NOW, user_id="USER-A", user_role="operator"
    )


def test_high_progress_next_day_commitment_is_scheduled_not_do_today():
    signals, bucket = _bucket(_order())
    assert {s["risk_type"] for s in signals} == {"DELIVERY_RISK"}
    assert bucket["action_bucket"] == "SCHEDULED"
    assert "按承诺节点复核" in bucket["bucket_reasons"][0]


def test_progress_below_90_percent_keeps_do_today():
    _, bucket = _bucket(_order(current_progress=0.89))
    assert bucket["action_bucket"] == "DO_TODAY"


def test_missing_or_late_commitment_keeps_do_today():
    for commitment in [None, "2026-08-22"]:
        _, bucket = _bucket(_order(latest_supplier_commitment=commitment))
        assert bucket["action_bucket"] == "DO_TODAY"


def test_due_today_is_never_suppressed():
    _, bucket = _bucket(
        _order(requested_delivery_date="2026-08-19", latest_supplier_commitment="2026-08-20")
    )
    assert bucket["action_bucket"] == "DO_TODAY"


def test_supplier_commitment_on_customer_due_date_keeps_do_today():
    _, bucket = _bucket(
        _order(requested_delivery_date="2026-08-20", latest_supplier_commitment="2026-08-20")
    )
    assert bucket["action_bucket"] == "DO_TODAY"


def test_quality_blocking_disables_r4_protection_and_stays_do_now():
    quality = [{
        "quality_event_id": "QE-R4",
        "order_id": "ORD-R4",
        "event_type": "PACKAGING_LABEL_ERROR",
        "status": "REWORKING",
        "description": "标签错误需要重印",
        "is_delivery_blocking": 1,
        "rework_required": 1,
        "event_time": "2026-08-19T09:20:00+08:00",
        "created_at": "2026-08-19T09:20:00+08:00",
        "updated_at": "2026-08-19T09:20:00+08:00",
    }]
    signals, bucket = _bucket(_order(), quality_events=quality)
    assert "QUALITY_BLOCKING" in {s["risk_type"] for s in signals}
    assert bucket["action_bucket"] == "DO_NOW"


def test_logistics_exception_disables_r4_protection():
    logistics = [{
        "logistics_event_id": "LE-R4",
        "order_id": "ORD-R4",
        "event_type": "ROLLOVER",
        "status": "DELAYED",
        "description": "甩柜，暂无新ETA",
        "event_time": "2026-08-19T08:00:00+08:00",
        "estimated_arrival_at": None,
        "resolved_at": None,
    }]
    signals, bucket = _bucket(_order(), logistics=logistics)
    assert "LOGISTICS_EXCEPTION" in {s["risk_type"] for s in signals}
    assert bucket["action_bucket"] != "SCHEDULED"
