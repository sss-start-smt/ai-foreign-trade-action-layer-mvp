from agent_router import route_agent_request


def intents(plan):
    return [step.intent for step in plan.intents]


def test_messy_language_routes_to_risk_diagnosis():
    plan = route_agent_request(
        "我今天一来事情特别多，客户也在催，工厂也有几笔不太对，你先别发消息，帮我看看接下来两周到底哪些订单最危险，我应该先处理哪个。",
        {"default_top_n": 7},
    )
    assert plan.route_mode == "DETERMINISTIC_PLAN"
    assert intents(plan) == ["RISK_DIAGNOSIS"]
    assert plan.extracted["due_within_days"] == 14
    assert plan.constraints["allow_external_send"] is False


def test_multi_intent_builds_dependency_plan():
    plan = route_agent_request("检查未来两周最危险的订单，解释第一笔为什么优先，再给它建一个任务，但不要发消息。")
    assert intents(plan) == ["RISK_DIAGNOSIS", "EXPLAIN_PRIORITY", "CREATE_TASK_DRAFT"]
    assert plan.intents[1].depends_on == ["step_1"]
    assert plan.intents[2].depends_on == ["step_1"]
    assert plan.constraints["allow_external_send"] is False


def test_followup_explanation_uses_previous_run():
    plan = route_agent_request("为什么第一笔排在最前？", {"previous_run_id": "AGR-1"})
    assert plan.route_mode == "DETERMINISTIC_PLAN"
    assert intents(plan) == ["EXPLAIN_PRIORITY"]
    assert plan.extracted["target_rank"] == 1


def test_ambiguous_action_requires_clarification():
    plan = route_agent_request("订单好像有点问题，你处理一下。")
    assert plan.route_mode == "CLARIFICATION"
    assert plan.needs_clarification is True


def test_bulk_update_routes_to_composite_parser():
    plan = route_agent_request("PO-001现在完成60%，预计8月8日完工。\nPO-002客户明天下午回复包装确认。")
    assert plan.route_mode == "DETERMINISTIC_PLAN"
    assert intents(plan)[0] == "BATCH_UPDATE_PARSE"
    assert len(plan.extracted["order_refs"]) == 2


def test_negative_task_constraint_wins():
    plan = route_agent_request("检查风险并解释第一笔，但不要创建任务。")
    assert "CREATE_TASK_DRAFT" not in intents(plan)
    assert plan.constraints["allow_task_draft"] is False
