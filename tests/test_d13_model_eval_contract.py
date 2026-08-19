from __future__ import annotations

import json
from pathlib import Path

import d13_agent_skill as d13

BASE = Path(__file__).resolve().parents[1]
CASES = BASE / "evaluation" / "d13_model_selection_cases_v0_2.json"


def load_cases():
    return json.loads(CASES.read_text(encoding="utf-8"))


def test_formal_model_eval_is_rich_context_not_prompt_only():
    cases = load_cases()
    assert len(cases) >= 30
    assert len({x["case_id"] for x in cases}) == len(cases)
    for case in cases:
        assert case.get("current_datetime")
        assert case.get("timezone") == "Asia/Shanghai"
        assert case.get("trigger_type") in {"USER_REQUEST", "NORMALIZED_MESSAGE_EVENT"}
        assert isinstance(case.get("context"), dict)
        assert isinstance(case.get("authenticated_scope"), dict)


def test_formal_model_eval_only_expects_current_agent_tools():
    current = set(d13.CONTROLLED_TOOL_CATALOG)
    removed = {
        "diagnose_priority_orders",
        "request_update_expected_delivery_date",
        "request_update_customer_commitment",
        "request_accept_delay",
        "request_high_risk_override",
    }
    for case in load_cases():
        for tool in case.get("expected_tools") or []:
            assert tool in current, (case["case_id"], tool)
        assert not (set(case.get("expected_tools") or []) & removed)


def test_eval_contains_required_d13_risk_categories():
    categories = {x["category"] for x in load_cases()}
    required = {
        "READ_BEFORE_ASK", "EXTRACT_FACT", "UNCERTAIN_FACT", "FORMAL_COMMITMENT",
        "ROLE_SPOOF", "FORBIDDEN_ERP", "MULTI_INTENT_DISTINCT_TASKS",
        "MULTI_EFFECT_SAME_TASK", "MESSAGE_EVENT_FACT", "CROSS_ORG",
        "EXTERNAL_EFFECT_BOUNDARY", "SOURCE_CONFLICT", "AMBIGUOUS_ORDER_MATCH",
    }
    assert required <= categories
