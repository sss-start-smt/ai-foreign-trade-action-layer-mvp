from datetime import datetime, timedelta

from d7_risk_engine import CN_TZ, _assess_logistics_exception, assign_action_bucket


def _order(days_to_due: int | None):
    now = datetime(2026, 8, 19, 10, 0, tzinfo=CN_TZ)
    due = (now + timedelta(days=days_to_due)).date().isoformat() if days_to_due is not None else None
    return now, {
        "order_id": f"ORD-D14-1-{days_to_due}",
        "order_no": f"D14-1-{days_to_due}",
        "requested_delivery_date": due,
        "current_progress": 1.0,
        "current_node": "运输中",
        "owner": "USER-1",
        "organization_id": "ORG-1",
        "updated_at": now.isoformat(),
    }, due


def _events(now, eta_days, count=2, *, one_missing=False):
    rows=[]
    for i in range(count):
        eta = None if (one_missing and i == count-1) else (now + timedelta(days=eta_days)).isoformat()
        rows.append({
            "event_type": "CUSTOMS" if i == 0 else "TRANSSHIPMENT",
            "status": "CUSTOMS_HOLD" if i == 0 else "DELAYED",
            "description": "unresolved logistics event",
            "estimated_arrival_at": eta,
            "resolved_at": None,
        })
    return rows


def _assess(days_to_due, eta_days, *, count=2, one_missing=False):
    now, order, due = _order(days_to_due)
    sig = _assess_logistics_exception(
        order,
        _events(now, eta_days, count, one_missing=one_missing),
        {"data_stale_warning": False},
        current=now,
        delivery_date=due,
    )
    bucket = assign_action_bucket([sig], order, tasks=[], current=now, user_id="USER-1", user_role="operator")
    return sig, bucket


def test_two_exceptions_14_days_with_eta_6_days_before_due_is_medium_do_today():
    sig, bucket = _assess(14, 8)  # due day14, ETA day8 => 6-day arrival buffer
    assert sig["severity"] == "MEDIUM"
    assert bucket["action_bucket"] == "DO_TODAY"
    assert any("提前6天" in e for e in sig["evidence"])


def test_two_exceptions_12_days_with_eta_only_2_days_before_due_stays_high_do_now():
    sig, bucket = _assess(12, 10)
    assert sig["severity"] == "HIGH"
    assert bucket["action_bucket"] == "DO_NOW"


def test_two_exceptions_10_days_with_eta_exactly_3_days_before_due_is_medium():
    sig, bucket = _assess(10, 7)
    assert sig["severity"] == "MEDIUM"
    assert bucket["action_bucket"] == "DO_TODAY"


def test_two_exceptions_with_one_missing_eta_stays_conservative_high():
    sig, bucket = _assess(14, 8, one_missing=True)
    assert sig["severity"] == "HIGH"
    assert bucket["action_bucket"] == "DO_NOW"


def test_two_exceptions_near_due_remain_critical_even_with_early_eta():
    sig, bucket = _assess(5, 2)
    assert sig["severity"] == "CRITICAL"
    assert bucket["action_bucket"] == "ESCALATE"


def test_single_exception_10_days_remains_medium_do_today():
    sig, bucket = _assess(10, 6, count=1)
    assert sig["severity"] == "MEDIUM"
    assert bucket["action_bucket"] == "DO_TODAY"


def test_unknown_due_remains_conservative_for_multiple_exceptions():
    sig, bucket = _assess(None, 8)
    assert sig["severity"] == "CRITICAL"
    assert bucket["action_bucket"] == "ESCALATE"
