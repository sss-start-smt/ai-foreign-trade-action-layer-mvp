from datetime import datetime, timedelta, timezone

from d7_risk_engine import compute_priority_score, rank_orders

CN_TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=CN_TZ)


def item(order_id, *, days, bucket, risk_types, progress=None, commitment_buffer=None,
         waiting=False, waiting_overdue=False, eta_buffer=None, eta_known=False,
         logistics_count=0, quality_buffer=None):
    due = (NOW + timedelta(days=days)).date().isoformat()
    x = {
        "order_id": order_id,
        "order_no": order_id,
        "requested_delivery_date": due,
        "effective_delivery_date": due,
        "current_progress": progress,
        "action_bucket": bucket,
        "ranking_suppressed": waiting,
        "risk_signals": [{"risk_type": rt} for rt in risk_types],
        "anomaly_types": list(risk_types),
        "attention_context": {
            "days_to_due": days,
            "supplier_commitment_buffer_days": commitment_buffer,
            "supplier_commitment_days_from_now": None if commitment_buffer is None else max(1, days - commitment_buffer),
            "unresolved_logistics_count": logistics_count,
            "logistics_all_eta_known": eta_known,
            "logistics_eta_buffer_days": eta_buffer,
            "quality_blocking_count": 1 if "QUALITY_BLOCKING" in risk_types else 0,
            "quality_resolution_buffer_days": quality_buffer,
            "pending_confirmation_count": 0,
            "max_confirmation_age_hours": None,
            "active_external_waiting": waiting,
            "waiting_overdue": waiting_overdue,
            "open_fact_conflict_count": 1 if "SOURCE_CONFLICT" in risk_types else 0,
            "risk_types": list(risk_types),
        },
    }
    score = compute_priority_score(x, NOW)
    x.update(score)
    return x


def test_zero_buffer_near_due_outranks_owner_missing_governance():
    zero_buffer = item(
        "ZERO", days=2, bucket="DO_TODAY", risk_types=["DELIVERY_RISK"],
        progress=0.94, commitment_buffer=0,
    )
    owner = item(
        "OWNER", days=7, bucket="ESCALATE", risk_types=["DELIVERY_RISK", "OWNER_MISSING"],
        progress=0.70, commitment_buffer=2,
    )
    ranked = rank_orders([owner, zero_buffer], top_n=7, current=NOW)["risk_items"]
    assert ranked[0]["order_id"] == "ZERO"
    assert zero_buffer["governance_escalation_required"] is False
    assert owner["governance_escalation_required"] is True


def test_waiting_keeps_attention_but_actionability_stays_waiting():
    waiting = item(
        "WAIT", days=6, bucket="WAITING_EXTERNAL", risk_types=["DELIVERY_RISK"],
        progress=0.83, commitment_buffer=3, waiting=True,
    )
    assert waiting["risk_attention_score"] > 0
    assert waiting["current_actionability"] == "WAITING_EXTERNAL"
    assert any("不重复催办" in r for r in waiting["risk_attention_reasons"])


def test_near_source_conflict_outranks_distant_source_conflict():
    near = item("NEAR", days=3, bucket="NEEDS_CONFIRMATION", risk_types=["SOURCE_CONFLICT"])
    far = item("FAR", days=12, bucket="NEEDS_CONFIRMATION", risk_types=["SOURCE_CONFLICT"])
    assert near["risk_attention_score"] > far["risk_attention_score"]


def test_no_commitment_low_progress_outranks_healthy_commitment():
    no_commit = item("NO-COMMIT", days=7, bucket="DO_TODAY", risk_types=["DELIVERY_RISK"], progress=0.47)
    healthy = item("HEALTHY", days=7, bucket="SCHEDULED", risk_types=["DELIVERY_RISK"], progress=0.47, commitment_buffer=2)
    assert no_commit["risk_attention_score"] > healthy["risk_attention_score"]


def test_logistics_eta_buffer_controls_attention_without_bucket_dominance():
    tight = item(
        "ETA2", days=12, bucket="DO_NOW", risk_types=["LOGISTICS_EXCEPTION"],
        eta_buffer=2, eta_known=True, logistics_count=2,
    )
    safe = item(
        "ETA6", days=14, bucket="DO_TODAY", risk_types=["LOGISTICS_EXCEPTION"],
        eta_buffer=6, eta_known=True, logistics_count=2,
    )
    assert tight["risk_attention_score"] > safe["risk_attention_score"]
    ranked = rank_orders([safe, tight], top_n=7, current=NOW)["risk_items"]
    assert ranked[0]["order_id"] == "ETA2"


def test_quality_blocker_remains_visible_even_with_longer_buffer():
    quality = item(
        "QUALITY", days=10, bucket="SCHEDULED", risk_types=["QUALITY_BLOCKING"],
        progress=0.82, commitment_buffer=4, quality_buffer=8,
    )
    normal_delivery = item(
        "NORMAL", days=10, bucket="SCHEDULED", risk_types=["DELIVERY_RISK"],
        progress=0.82, commitment_buffer=4,
    )
    assert quality["risk_attention_score"] > normal_delivery["risk_attention_score"]


def test_attention_score_is_not_severity_or_action_probability():
    x = item("X", days=4, bucket="DO_NOW", risk_types=["DELIVERY_RISK"], progress=0.32)
    assert 0 <= x["risk_attention_score"] <= 100
    assert x["priority_score"] == x["risk_attention_score"]
    assert x["ranking_rule_version"] == "D14_2_ATTENTION_V1"
    assert "NOT a probability" in x["score_description"]
