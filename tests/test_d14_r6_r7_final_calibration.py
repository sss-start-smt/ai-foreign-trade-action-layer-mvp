from datetime import datetime, timezone, timedelta
from floworder.d7_risk_engine import assess_risks_from_facts, assign_action_bucket
CN=timezone(timedelta(hours=8)); NOW=datetime(2026,8,19,10,0,tzinfo=CN)

def order(days, progress, commit=None):
    from datetime import timedelta
    due=(NOW+timedelta(days=days)).date().isoformat()
    return {"order_id":"O","order_no":"O","requested_delivery_date":due,"latest_supplier_commitment":commit,"current_progress":progress,"current_node":"生产中","status":"ACTIVE","owner":"U","organization_id":"ORG","updated_at":NOW.isoformat()}

def test_active_waiting_is_suppressed_without_other_structured_conflict():
    o=order(5,.85,"2026-08-20")
    tasks=[{"status":"WAITING_EXTERNAL","waiting_on":"supplier","promised_reply_at":"2026-08-19T14:00:00+08:00"}]
    risks=assess_risks_from_facts(o,current=NOW,tasks=tasks)
    b=assign_action_bucket(risks,o,tasks=tasks,current=NOW,user_id="U")
    assert b["action_bucket"]=="WAITING_EXTERNAL" and b["ranking_suppressed"] is True

def test_structured_internal_conflict_breaks_wait_suppression_and_needs_confirmation():
    o=order(5,.85,"2026-08-20")
    tasks=[{"status":"WAITING_EXTERNAL","waiting_on":"supplier","promised_reply_at":"2026-08-19T14:00:00+08:00"}]
    conflicts=[{"conflict_id":"C1","field_name":"当前生产状态","source_a":"ERP","value_a":"包装完成","source_b":"仓库跟单表","value_b":"待验货","status":"OPEN","detected_at":NOW.isoformat()}]
    risks=assess_risks_from_facts(o,current=NOW,tasks=tasks,fact_conflicts=conflicts)
    assert "SOURCE_CONFLICT" in {r["risk_type"] for r in risks}
    b=assign_action_bucket(risks,o,tasks=tasks,current=NOW,user_id="U")
    assert b["action_bucket"]=="NEEDS_CONFIRMATION" and b["ranking_suppressed"] is False

def test_resolved_internal_conflict_does_not_break_waiting():
    o=order(5,.85,"2026-08-20")
    tasks=[{"status":"WAITING_EXTERNAL","waiting_on":"supplier","promised_reply_at":"2026-08-19T14:00:00+08:00"}]
    conflicts=[{"conflict_id":"C1","field_name":"状态","source_a":"ERP","value_a":"A","source_b":"仓库","value_b":"B","status":"RESOLVED","resolved_at":NOW.isoformat()}]
    risks=assess_risks_from_facts(o,current=NOW,tasks=tasks,fact_conflicts=conflicts)
    b=assign_action_bucket(risks,o,tasks=tasks,current=NOW,user_id="U")
    assert b["action_bucket"]=="WAITING_EXTERNAL"

def test_r7_six_day_48pct_with_one_day_commitment_buffer_is_scheduled():
    o=order(6,.48,"2026-08-24")
    risks=assess_risks_from_facts(o,current=NOW)
    b=assign_action_bucket(risks,o,tasks=[],current=NOW,user_id="U")
    assert b["action_bucket"]=="SCHEDULED"

def test_r7_five_day_30pct_still_do_now_even_with_commitment():
    o=order(5,.30,"2026-08-23")
    risks=assess_risks_from_facts(o,current=NOW)
    b=assign_action_bucket(risks,o,tasks=[],current=NOW,user_id="U")
    assert b["action_bucket"]=="DO_NOW"

def test_r7_six_day_30pct_not_protected():
    o=order(6,.30,"2026-08-24")
    risks=assess_risks_from_facts(o,current=NOW)
    b=assign_action_bucket(risks,o,tasks=[],current=NOW,user_id="U")
    assert b["action_bucket"]=="DO_TODAY"

def test_r7_zero_commitment_buffer_not_protected():
    o=order(6,.48,"2026-08-25")
    risks=assess_risks_from_facts(o,current=NOW)
    b=assign_action_bucket(risks,o,tasks=[],current=NOW,user_id="U")
    assert b["action_bucket"]=="DO_TODAY"

def test_r7_other_risk_disables_protection():
    o=order(6,.48,"2026-08-24")
    tasks=[{"title":"确认包装","target":"customer","pending_confirmation":1,"status":"OPEN"}]
    risks=assess_risks_from_facts(o,current=NOW,tasks=tasks)
    b=assign_action_bucket(risks,o,tasks=tasks,current=NOW,user_id="U")
    assert b["action_bucket"]=="NEEDS_CONFIRMATION"
