from __future__ import annotations

import pytest

from d13_skill_runtime import (
    D13PlanError,
    RuntimeContext,
    TRIGGER_MESSAGE_EVENT,
    compile_skill_instruction,
    validate_model_plan,
)


def test_skill_instruction_is_provider_agnostic_injects_time_and_denies_authority():
    text = compile_skill_instruction(RuntimeContext(
        trigger_type=TRIGGER_MESSAGE_EVENT,
        current_datetime="2026-08-17T12:00:00+08:00",
        timezone="Asia/Shanghai",
    ))
    assert "NORMALIZED_MESSAGE_EVENT" in text
    assert "2026-08-17T12:00:00+08:00" in text
    assert "Read-Before-Ask" in text
    assert "不能批准/拒绝审批" in text
    assert "不能直接或通用写ERP" in text
    assert "Qwen" not in text and "Coze" not in text


def test_valid_multi_intent_requires_distinct_tasks_for_distinct_effects():
    plan = validate_model_plan({
        "decision": "TOOL_CALLS",
        "evidence_refs": ["MSG-1"],
        "tool_calls": [
            {
                "tool_name": "request_record_supplier_commitment",
                "task_id": "TK-SUPPLIER",
                "payload": {"supplier_commitment_date": "2026-08-25", "evidence": "工厂邮件"},
                "evidence_refs": ["MSG-1"],
            },
            {
                "tool_name": "request_change_customer_delivery_date",
                "task_id": "TK-CUSTOMER",
                "payload": {"customer_delivery_date": "2026-08-27", "reason": "供应商延期"},
            },
        ],
    })
    assert len(plan["tool_calls"]) == 2
    assert plan["effect_task_count"] == 2


def test_two_effects_on_same_task_are_rejected_by_d10_derived_guard():
    with pytest.raises(D13PlanError, match="one primary BusinessAction"):
        validate_model_plan({
            "decision": "TOOL_CALLS",
            "tool_calls": [
                {
                    "tool_name": "request_record_supplier_commitment",
                    "task_id": "TK-1",
                    "payload": {"supplier_commitment_date": "2026-08-25"},
                },
                {
                    "tool_name": "request_change_customer_delivery_date",
                    "task_id": "TK-1",
                    "payload": {"customer_delivery_date": "2026-08-27"},
                },
            ],
        })


def test_forbidden_removed_or_hallucinated_tool_is_rejected_before_execution():
    for tool_name in ["send_message", "super_magic_erp", "request_high_risk_override", "request_accept_delay", "diagnose_priority_orders"]:
        with pytest.raises(D13PlanError):
            validate_model_plan({"decision": "TOOL_CALLS", "tool_calls": [{"tool_name": tool_name, "payload": {}}]})


def test_model_cannot_smuggle_authority_or_raw_target_fields():
    with pytest.raises(D13PlanError):
        validate_model_plan({
            "decision": "TOOL_CALLS",
            "tool_calls": [{
                "tool_name": "request_change_customer_delivery_date",
                "task_id": "TK-1",
                "payload": {"customer_delivery_date": "2026-08-25", "manager_id": "MANAGER-A"},
            }],
        })
    with pytest.raises(D13PlanError):
        validate_model_plan({
            "decision": "TOOL_CALLS",
            "tool_calls": [{
                "tool_name": "request_change_customer_delivery_date",
                "task_id": "TK-1",
                "target_id": "OTHER-ORDER",
                "payload": {"customer_delivery_date": "2026-08-25"},
            }],
        })


def test_clarify_no_action_and_draft_semantics_are_strict():
    assert validate_model_plan({"decision": "NO_ACTION", "tool_calls": []})["decision"] == "NO_ACTION"
    assert validate_model_plan({"decision": "CLARIFY", "clarification_question": "你想处理交期还是催回复？", "tool_calls": []})["decision"] == "CLARIFY"
    draft = validate_model_plan({
        "decision": "DRAFT_ONLY",
        "response_draft": "邮件草稿",
        "tool_calls": [{"tool_name": "draft_message", "payload": {"audience": "customer"}}],
    })
    assert draft["tool_calls"][0]["mode"] == "SUGGEST_ONLY"
    with pytest.raises(D13PlanError):
        validate_model_plan({"decision": "CLARIFY", "tool_calls": []})
    with pytest.raises(D13PlanError):
        validate_model_plan({
            "decision": "DRAFT_ONLY",
            "tool_calls": [{"tool_name": "request_set_waiting", "task_id": "TK-1", "payload": {"waiting_on": "customer"}}],
        })


def test_read_before_ask_plan_is_allowed_without_effect():
    plan = validate_model_plan({
        "decision": "TOOL_CALLS",
        "tool_calls": [{"tool_name": "get_order_context", "payload": {"order_no": "PO-1001"}}],
    })
    assert plan["tool_calls"][0]["mode"] == "READ_ONLY"
    assert plan["effect_task_count"] == 0


def test_duplicate_same_tool_same_args_is_no_progress_error():
    with pytest.raises(D13PlanError, match="no progress"):
        validate_model_plan({
            "decision": "TOOL_CALLS",
            "tool_calls": [
                {"tool_name": "get_order_context", "payload": {"order_no": "PO-1001"}},
                {"tool_name": "get_order_context", "payload": {"order_no": "PO-1001"}},
            ],
        })


def test_respond_only_supports_grounded_answer_after_read_without_fake_no_action():
    plan = validate_model_plan({"decision": "RESPOND_ONLY", "response_draft": "当前优先级高，原因是交期临近且承诺回复已超时。", "tool_calls": []})
    assert plan["decision"] == "RESPOND_ONLY"
    with pytest.raises(D13PlanError):
        validate_model_plan({"decision": "RESPOND_ONLY", "tool_calls": []})
