from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "RISK_DIAGNOSIS": {
        "tool": "diagnose_priority_orders",
        "mode": "DETERMINISTIC_BACKEND",
        "requires_confirmation": False,
    },
    "EXPLAIN_PRIORITY": {
        "tool": "explain_priority_result",
        "mode": "DETERMINISTIC_BACKEND",
        "requires_confirmation": False,
    },
    "BATCH_UPDATE_PARSE": {
        "tool": "parse_bulk_order_updates",
        "mode": "DETERMINISTIC_BACKEND",
        "requires_confirmation": True,
    },
    "CREATE_TASK_DRAFT": {
        "tool": "create_task_draft",
        "mode": "DETERMINISTIC_BACKEND",
        "requires_confirmation": True,
    },
    "CREATE_MESSAGE_DRAFT": {
        "tool": "draft_message",
        "mode": "COZE_AGENT",
        "requires_confirmation": True,
    },
    "ORDER_STATUS_QUERY": {
        "tool": "get_order_diagnostic_context",
        "mode": "COZE_AGENT",
        "requires_confirmation": False,
    },
    "INFORMATION_GAP_QUERY": {
        "tool": "get_order_diagnostic_context",
        "mode": "COZE_AGENT",
        "requires_confirmation": False,
    },
    "APPROVAL_STATUS_QUERY": {
        "tool": "get_approval_status",
        "mode": "COZE_AGENT",
        "requires_confirmation": False,
    },
}


@dataclass(slots=True)
class IntentStep:
    id: str
    intent: str
    tool: str | None
    parameters: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    requires_confirmation: bool = False


@dataclass(slots=True)
class RoutePlan:
    intents: list[IntentStep]
    constraints: dict[str, Any]
    confidence: float
    needs_clarification: bool
    clarification_question: str | None
    route_mode: str
    normalized_question: str
    extracted: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["intents"] = [asdict(item) for item in self.intents]
        return data


_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _parse_small_number(value: str | None) -> int | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    if value in _CN_NUMBERS:
        return _CN_NUMBERS[value]
    if value.startswith("十") and len(value) == 2 and value[1] in _CN_NUMBERS:
        return 10 + _CN_NUMBERS[value[1]]
    if len(value) == 2 and value[0] in _CN_NUMBERS and value[1] == "十":
        return _CN_NUMBERS[value[0]] * 10
    if len(value) == 3 and value[0] in _CN_NUMBERS and value[1] == "十" and value[2] in _CN_NUMBERS:
        return _CN_NUMBERS[value[0]] * 10 + _CN_NUMBERS[value[2]]
    return None


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _extract_due_days(text: str, default: int) -> int:
    fixed = [
        (("接下来两周", "未来两周", "最近两周", "这两周", "未来十四天", "未来14天"), 14),
        (("接下来一周", "未来一周", "最近一周", "这周", "本周"), 7),
        (("未来一个月", "接下来一个月", "未来一月", "接下来一月"), 30),
        (("月底前", "本月底前"), 30),
    ]
    for phrases, days in fixed:
        if _contains_any(text, phrases):
            return days
    match = re.search(r"(?:(?:未来|接下来|最近|往后|后面)\s*([0-9一二两三四五六七八九十]{1,3})\s*(天|日|周|星期|个月)|([0-9一二两三四五六七八九十]{1,3})\s*(天|日|周|星期)内)", text)
    if not match:
        return max(1, min(int(default or 14), 90))
    number = _parse_small_number(match.group(1) or match.group(3))
    if not number:
        return max(1, min(int(default or 14), 90))
    unit = match.group(2) or match.group(4)
    if unit in {"周", "星期"}:
        number *= 7
    elif unit in {"个月", "月"}:
        number *= 30
    return max(1, min(number, 90))


def _extract_top_n(text: str, default: int) -> int:
    patterns = [
        r"(?:前|top\s*)([0-9一二两三四五六七]{1,2})\s*(?:笔|个|条|单)?",
        r"(?:最危险|最紧急|最优先|最需要处理|最该处理)(?:的)?\s*([0-9一二两三四五六七]{1,2})\s*(?:笔|个|条|单)",
        r"挑(?:出)?\s*([0-9一二两三四五六七]{1,2})\s*(?:笔|个|条|单)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = _parse_small_number(match.group(1))
            if value:
                return max(1, min(value, 7))
    if _contains_any(text, ("几个", "几笔", "挑几个", "挑几笔")):
        return min(5, max(1, int(default or 5)))
    return max(1, min(int(default or 7), 7))


def _extract_rank(text: str) -> int | None:
    for pattern in (
        r"第\s*([0-9一二两三四五六七]{1,2})\s*(?:笔|个|条|名|单)?",
        r"排名\s*([0-9一二两三四五六七]{1,2})",
    ):
        match = re.search(pattern, text)
        if match:
            value = _parse_small_number(match.group(1))
            if value:
                return max(1, min(value, 7))
    if _contains_any(text, ("第一笔", "最前面那笔", "最高优先级", "最优先那笔", "最严重那笔")):
        return 1
    return None


def _extract_order_refs(text: str) -> list[str]:
    refs = re.findall(r"(?<![A-Z0-9])(?:PO|SO|ORD|ORDER|PI)[-_]?[A-Z0-9][A-Z0-9_-]{2,}(?=$|[^A-Z0-9_-])", text, flags=re.IGNORECASE)
    result: list[str] = []
    for ref in refs:
        normalized = ref.upper()
        if normalized not in result:
            result.append(normalized)
    return result


def _build_steps(intents: list[str], *, due_days: int, top_n: int, target_rank: int | None,
                 order_refs: list[str]) -> list[IntentStep]:
    steps: list[IntentStep] = []
    id_by_intent: dict[str, str] = {}
    for index, intent in enumerate(intents, 1):
        registry = TOOL_REGISTRY[intent]
        step_id = f"step_{index}"
        params: dict[str, Any] = {}
        dependencies: list[str] = []
        if intent == "RISK_DIAGNOSIS":
            params.update({"due_within_days": due_days, "top_n": top_n})
        elif intent == "EXPLAIN_PRIORITY":
            params["target_rank"] = target_rank or 1
            if "RISK_DIAGNOSIS" in id_by_intent:
                dependencies.append(id_by_intent["RISK_DIAGNOSIS"])
        elif intent == "CREATE_TASK_DRAFT":
            params["target_rank"] = target_rank or 1
            if order_refs:
                params["order_ref"] = order_refs[0]
            if "RISK_DIAGNOSIS" in id_by_intent:
                dependencies.append(id_by_intent["RISK_DIAGNOSIS"])
        elif intent == "BATCH_UPDATE_PARSE":
            params["order_refs"] = order_refs
        elif target_rank:
            params["target_rank"] = target_rank
        if order_refs and "order_refs" not in params:
            params["order_refs"] = order_refs
        step = IntentStep(
            id=step_id,
            intent=intent,
            tool=registry.get("tool"),
            parameters=params,
            depends_on=dependencies,
            requires_confirmation=bool(registry.get("requires_confirmation")),
        )
        steps.append(step)
        id_by_intent[intent] = step_id
    return steps


def route_agent_request(question: str, context: dict[str, Any] | None = None) -> RoutePlan:
    """Route natural language into a bounded FlowOrder execution plan.

    The router never grants permissions and never executes business actions. It only
    extracts intent, parameters, constraints and dependencies. FastAPI remains the
    authority for scope, idempotency and approval requirements.
    """
    context = context or {}
    text = _normalize(question)
    lowered = text.lower()
    due_days = _extract_due_days(text, int(context.get("default_due_within_days") or 14))
    top_n = _extract_top_n(text, int(context.get("default_top_n") or 7))
    target_rank = _extract_rank(text)
    order_refs = _extract_order_refs(text)

    constraints = {
        "allow_external_send": not _contains_any(text, ("不要发", "别发", "先别发", "不能发", "禁止发送", "不要发送", "不发送")),
        "allow_order_writeback": not _contains_any(text, ("不要修改订单", "别改订单", "先别写回", "不要写回", "不写回", "先别更新订单")),
        "allow_task_draft": not _contains_any(text, ("不要建任务", "别建任务", "不要创建任务", "不创建任务", "不要生成任务")),
        "draft_only": _contains_any(text, ("只生成草稿", "仅生成草稿", "先出草稿", "不要直接执行", "先别执行")),
        "require_human_approval": True,
    }

    risk = _contains_any(text, (
        "风险", "危险", "最急", "最紧急", "最优先", "优先处理", "最需要处理", "最该处理", "巡检", "排个优先级",
        "排优先级", "排序", "先处理哪个", "先做哪个", "最麻烦", "最严重",
    ))
    explain = _contains_any(text, ("为什么", "为何", "原因", "解释", "凭什么", "相比", "区别"))
    task = _contains_any(text, ("建任务", "创建任务", "生成任务", "安排任务", "加个任务", "建个任务", "建一个任务", "创建一个任务", "生成一个任务", "建个待办", "生成待办", "安排跟进")) or bool(re.search(r"(?:建|创建|生成|安排|加)(?:一个|个|条)?任务", text))
    message_draft = _contains_any(text, ("写邮件", "写封邮件", "生成邮件", "回复客户", "催客户", "催工厂", "沟通草稿", "消息草稿", "怎么说"))
    approval = _contains_any(text, ("审批状态", "审批怎么样", "审批通过", "审批了吗", "待审批"))
    info_gap = _contains_any(text, ("缺什么信息", "还缺什么", "信息缺口", "资料不全", "需要补充什么"))
    order_status = bool(order_refs) and _contains_any(text, ("什么情况", "现在怎样", "当前状态", "进展", "到哪一步", "查一下"))

    update_terms = _contains_any(text, ("更新", "进展", "完工", "完成", "延期", "承诺", "回复", "提柜", "到料", "到货"))
    bulk_parse = len(order_refs) >= 2 and update_terms
    if not bulk_parse:
        line_count = sum(1 for line in str(question or "").splitlines() if line.strip())
        bulk_parse = line_count >= 2 and len(order_refs) >= 1 and update_terms

    intents: list[str] = []
    if bulk_parse:
        intents.append("BATCH_UPDATE_PARSE")
    if risk:
        intents.append("RISK_DIAGNOSIS")
    if explain:
        intents.append("EXPLAIN_PRIORITY")
    if info_gap:
        intents.append("INFORMATION_GAP_QUERY")
    if order_status:
        intents.append("ORDER_STATUS_QUERY")
    if task and constraints["allow_task_draft"]:
        intents.append("CREATE_TASK_DRAFT")
    if message_draft:
        intents.append("CREATE_MESSAGE_DRAFT")
    if approval:
        intents.append("APPROVAL_STATUS_QUERY")

    # A follow-up such as “为什么第一笔” is valid when the website supplies a prior run.
    if explain and not risk and context.get("previous_run_id"):
        intents = [x for x in intents if x != "RISK_DIAGNOSIS"]

    vague_action = _contains_any(text, ("处理一下", "搞一下", "看着办", "有点问题", "帮我弄一下"))
    needs_clarification = False
    clarification: str | None = None
    if not intents:
        needs_clarification = True
        clarification = "你希望我先检查风险订单、查询某笔订单，还是生成任务/沟通草稿？"
    elif vague_action and not (risk or order_refs or bulk_parse or task or message_draft):
        needs_clarification = True
        clarification = "你希望我先检查所有有权限的订单风险，还是处理某一笔指定订单？"
    elif "EXPLAIN_PRIORITY" in intents and "RISK_DIAGNOSIS" not in intents and not context.get("previous_run_id") and not order_refs:
        needs_clarification = True
        clarification = "你要解释哪一次排序结果？请先运行风险诊断，或告诉我具体订单号。"
    elif "CREATE_TASK_DRAFT" in intents and "RISK_DIAGNOSIS" not in intents and not order_refs and not context.get("previous_run_id"):
        needs_clarification = True
        clarification = "要为哪一笔订单创建任务草稿？请提供订单号，或先运行风险诊断。"

    steps = _build_steps(
        intents,
        due_days=due_days,
        top_n=top_n,
        target_rank=target_rank,
        order_refs=order_refs,
    )

    deterministic_intents = {"RISK_DIAGNOSIS", "EXPLAIN_PRIORITY", "BATCH_UPDATE_PARSE", "CREATE_TASK_DRAFT"}
    if needs_clarification:
        route_mode = "CLARIFICATION"
        confidence = 0.45
    elif set(intents).issubset(deterministic_intents):
        route_mode = "DETERMINISTIC_PLAN"
        confidence = 0.92 if risk or bulk_parse else 0.82
    else:
        route_mode = "COZE_AGENT"
        confidence = 0.78

    return RoutePlan(
        intents=steps,
        constraints=constraints,
        confidence=confidence,
        needs_clarification=needs_clarification,
        clarification_question=clarification,
        route_mode=route_mode,
        normalized_question=text,
        extracted={
            "due_within_days": due_days,
            "top_n": top_n,
            "target_rank": target_rank,
            "order_refs": order_refs,
            "has_previous_run": bool(context.get("previous_run_id")),
            "original_length": len(str(question or "")),
        },
    )
