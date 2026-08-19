"""FlowOrder D13: domain Skill + controlled Tool boundary.

D13 deliberately separates language intelligence from business authority:

    user/message trigger -> Agent/Skill -> semantic tool request
                         -> D12 Human Review -> D10 BusinessAction -> Outbox

The model may understand, extract, explain and request. It may not grant itself
permission, approve/submit a review, or directly execute ERP/email effects.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from business_value_validation import BusinessValueValidationError, validate_business_dates

import d10_business_action as d10
import d12_human_review as d12
from auth import CurrentIdentity, require_order_access
from d8_action_case import _conn_exec, _row_to_dict

D13_SKILL_VERSION = "D13_AGENT_SKILL_V3"
D13_TOOL_CONTRACT_VERSION = "D13_CONTROLLED_TOOLS_V3_1"
D13_SEMANTIC_GUARD_VERSION = "D13_SEMANTIC_GUARD_V4"

MODE_READ_ONLY = "READ_ONLY"
MODE_SUGGEST_ONLY = "SUGGEST_ONLY"
MODE_REQUEST_D12 = "REQUEST_D12_REVIEW"


class D13Error(Exception):
    pass


class D13NotFoundError(D13Error):
    pass


class D13ForbiddenError(D13Error):
    pass


class D13ValidationError(D13Error):
    pass


@dataclass(frozen=True)
class ControlledToolSpec:
    tool_name: str
    mode: str
    action_type: str | None = None
    target_type: str | None = None
    required_payload_keys: tuple[str, ...] = ()
    allowed_payload_keys: tuple[str, ...] = ()
    description: str = ""


# D13 V2 keeps the Agent-facing surface intentionally small and semantic.
# Stable deterministic capabilities remain in Rules/Backend; the LLM chooses
# among these tools but never supplies org/role/approval authority.
CONTROLLED_TOOL_CATALOG: dict[str, ControlledToolSpec] = {
    "get_order_context": ControlledToolSpec(
        "get_order_context",
        MODE_READ_ONLY,
        allowed_payload_keys=("order_id", "order_no"),
        description="读取当前用户有权限的订单、Action Case、Task、Waiting与最近消息上下文。",
    ),
    "get_actionable_orders": ControlledToolSpec(
        "get_actionable_orders",
        MODE_READ_ONLY,
        allowed_payload_keys=("due_within_days", "top_n"),
        description="读取冻结的确定性风险/行动排序结果；Agent只解释，不重算优先级。",
    ),
    "get_review_status": ControlledToolSpec(
        "get_review_status",
        MODE_READ_ONLY,
        required_payload_keys=("review_id",),
        allowed_payload_keys=("review_id",),
        description="读取D12 Human Review状态，不改变任何审批状态。",
    ),
    "draft_message": ControlledToolSpec(
        "draft_message",
        MODE_SUGGEST_ONLY,
        allowed_payload_keys=("audience", "purpose", "language", "tone", "facts"),
        description="基于已验证事实生成沟通草稿，不外发。",
    ),
    "request_record_contact": ControlledToolSpec(
        "request_record_contact", MODE_REQUEST_D12, "RECORD_CONTACT", "TASK",
        allowed_payload_keys=("channel", "contacted_party", "note", "promised_reply_at", "evidence"),
        description="请求记录已联系事实。",
    ),
    "request_set_waiting": ControlledToolSpec(
        "request_set_waiting", MODE_REQUEST_D12, "SET_WAITING", "TASK",
        required_payload_keys=("waiting_on",),
        allowed_payload_keys=("waiting_on", "promised_reply_at", "reason", "evidence"),
        description="请求把当前任务进入等待；waiting_on使用customer/supplier/internal/other，中文别名由后端确定性归一化。",
    ),
    "request_update_internal_plan": ControlledToolSpec(
        "request_update_internal_plan", MODE_REQUEST_D12, "UPDATE_INTERNAL_PLAN", "TASK",
        required_payload_keys=("plan",),
        allowed_payload_keys=("plan", "due_at", "note", "evidence"),
        description="请求更新内部跟进计划。",
    ),
    "request_record_supplier_commitment": ControlledToolSpec(
        "request_record_supplier_commitment", MODE_REQUEST_D12, "RECORD_SUPPLIER_COMMITMENT", "ORDER",
        required_payload_keys=("supplier_commitment_date",),
        allowed_payload_keys=("supplier_commitment_date", "source_message_id", "evidence", "note"),
        description="请求记录供应商最新明确承诺事实；不把推测升级为承诺。",
    ),
    "request_link_message_order": ControlledToolSpec(
        "request_link_message_order", MODE_REQUEST_D12, "LINK_MESSAGE_ORDER", "ORDER",
        required_payload_keys=("message_id",),
        allowed_payload_keys=("message_id", "relation_reason", "evidence"),
        description="请求把消息与当前订单建立关联。",
    ),
    "request_change_customer_delivery_date": ControlledToolSpec(
        "request_change_customer_delivery_date", MODE_REQUEST_D12, "UPDATE_EXPECTED_DELIVERY_DATE", "ORDER",
        required_payload_keys=("customer_delivery_date",),
        allowed_payload_keys=("customer_delivery_date", "reason", "evidence"),
        description="请求修改公司对客户的正式交期；D12 V1要求主管审批。",
    ),
}

# Explicitly not callable by D13 V2. ERP write is not removed from the product
# roadmap; it must be an external effect AFTER D12/D10 via an ERP Adapter.
FORBIDDEN_TOOL_NAMES = {
    "send_message",
    "erp_write_generic",
    "erp_write_direct",
    "approve_review",
    "reject_review",
    "submit_review",
    "direct_business_action",
    "update_order_directly",
    # Removed generic/high-risk Agent tools. Unknown high-risk intents escalate to human.
    "request_high_risk_override",
    "request_accept_delay",
    # Removed overlapping legacy names for the same customer delivery commitment.
    "request_update_expected_delivery_date",
    "request_update_customer_commitment",
    "diagnose_priority_orders",
}

IDENTITY_OR_AUTHORITY_KEYS = {
    "organization_id", "actor", "requested_by", "reviewed_by", "reviewer_role",
    "required_review", "approve", "approved", "manager_id", "current_role",
    "current_user_id", "user_id", "role",
}


UNCERTAIN_FACT_MARKERS = (
    "应该", "大概", "预计", "可能", "尽量", "差不多", "也许", "或许",
    "如果", "假如", "maybe", "probably", "likely", "expected", "if",
)

CONFIRMED_FACT_MARKERS = (
    "明确确认", "正式确认", "已经确认", "已确认", "确认承诺", "明确承诺",
    "confirmed", "committed", "firmlyconfirmed",
)


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_flatten_text(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_flatten_text(v) for v in value)
    return str(value)


def _supplier_commitment_evidence_text(
    goal: str,
    call: dict[str, Any],
    trusted_context: dict[str, Any] | None = None,
) -> str:
    """Collect only evidence relevant to a supplier-commitment request.

    If the model names a source_message_id, prefer the authoritative resolved
    source content for that id.  This prevents a model from sanitising an
    uncertain phrase merely by rewriting the payload note.
    """
    payload = call.get("payload") if isinstance(call.get("payload"), dict) else {}
    parts = [str(goal or ""), _flatten_text(payload.get("evidence")), _flatten_text(payload.get("note"))]
    source_message_id = str(payload.get("source_message_id") or "").strip()
    resolved = list((trusted_context or {}).get("resolved_messages") or [])
    if source_message_id:
        for message in resolved:
            if str(message.get("message_id") or "").strip() == source_message_id:
                parts.append(str(message.get("raw_content") or ""))
                break
    return " ".join(x for x in parts if x).lower()


def _uncertain_supplier_commitment_guard(
    goal: str,
    calls: list[dict[str, Any]],
    trusted_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    for call in calls:
        if str(call.get("tool_name") or "") != "request_record_supplier_commitment":
            continue
        evidence_text = _supplier_commitment_evidence_text(goal, call, trusted_context)
        has_uncertainty = any(marker in evidence_text for marker in UNCERTAIN_FACT_MARKERS)
        # A strong, explicit confirmation phrase can coexist with historical
        # uncertainty in the same request (e.g. "earlier maybe 25, now confirmed 27").
        # When no such confirmation exists, fail closed before D12.
        has_confirmation = any(marker in evidence_text for marker in CONFIRMED_FACT_MARKERS)
        if has_uncertainty and not has_confirmation:
            return {
                "guard_version": D13_SEMANTIC_GUARD_VERSION,
                "code": "UNCERTAIN_SUPPLIER_COMMITMENT_REQUIRES_CLARIFICATION",
                "blocked_tool": "request_record_supplier_commitment",
                "clarification_question": (
                    "当前信息包含“应该/大概/预计/可能/如果”等不确定或条件表达，"
                    "不能直接记为供应商正式承诺。请确认供应商是否已经明确承诺具体日期。"
                ),
            }
    return None




def pre_model_policy_guard(goal: str) -> dict[str, Any] | None:
    """Deterministic refusal for explicit requests to bypass FlowOrder and write ERP directly.

    This is intentionally narrow: it only catches an explicit direct/bypass ERP-write
    instruction. Questions about ERP, normal semantic requests that may eventually flow
    through D12/D10/Outbox, and read-only ERP discussion are not blocked here.
    """
    text = "".join(str(goal or "").lower().split())
    erp = "erp" in text
    write_intent = any(m in text for m in (
        "写入", "直接写", "直接改", "直接修改", "更新erp", "改erp",
        "write", "updateerp", "changeerp",
    ))
    bypass = any(m in text for m in (
        "忽略权限", "忽略权限规则", "绕过权限", "跳过审批", "绕过审批",
        "直接调用", "直接操作", "direct", "bypass", "skipapproval",
    ))
    if erp and write_intent and bypass:
        return {
            "guard_version": D13_SEMANTIC_GUARD_VERSION,
            "code": "FORBIDDEN_DIRECT_ERP_WRITE",
            "response": (
                "不能绕过权限、审批和受控提交链路直接写ERP。"
                "如果你要变更业务事实或正式交期，请说明具体业务目标，我可以按D12→D10的受控流程提出请求。"
            ),
        }
    return None

def business_semantic_guard(
    goal: str,
    plan: dict[str, Any],
    *,
    trusted_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Model-independent business-intent guard before any D12 effect request.

    Two distinct failure modes are blocked here:

    1. ``request_change_customer_delivery_date`` requires an explicit user intent
       to change the company's formal customer delivery date.  A nearby concept
       such as supplier delay must not be absorbed into that high-risk tool.
    2. ``accept delay`` is currently an *undefined* Agent action because the old
       generic ``request_accept_delay`` tool was intentionally removed.  When a
       goal asks only to accept/agree to a delay, the model may read context but
       it must not opportunistically emit some other D12 effect (for example,
       recording a supplier commitment) merely because supporting evidence is
       visible.  It must clarify which supported business effect the user wants.

    This guard deliberately does not retry or route to another model: semantic
    mistakes are fail-closed, not provider-availability failures.
    """
    calls = list((plan or {}).get("tool_calls") or [])
    if not calls:
        return None

    text = "".join(str(goal or "").lower().split())
    effect_calls = [
        c for c in calls
        if CONTROLLED_TOOL_CATALOG.get(str(c.get("tool_name") or ""), ControlledToolSpec("", "")).mode
        == MODE_REQUEST_D12
    ]

    uncertain_commitment = _uncertain_supplier_commitment_guard(
        goal, effect_calls, trusted_context
    )
    if uncertain_commitment:
        return uncertain_commitment

    # D13-M22 / LIVE-M22: accepting a delay is intentionally not a defined
    # business effect.  Safe READ_ONLY exploration is allowed, but once the
    # model tries to turn that ambiguous goal into *any* D12 effect, require the
    # user to name the actual supported action first.
    undefined_delay_markers = (
        "接受延期", "接受这个延期", "接受这次延期", "同意延期", "同意这个延期",
        "批准延期", "确认接受延期", "acceptdelay", "acceptthedelay", "agreetodelay",
    )
    has_undefined_delay_intent = any(marker in text for marker in undefined_delay_markers)

    if has_undefined_delay_intent and effect_calls:
        explicit_supported_effect = False
        for call in effect_calls:
            tool_name = str(call.get("tool_name") or "")
            if tool_name == "request_record_supplier_commitment":
                target = any(m in text for m in ("供应商承诺", "工厂承诺", "供应商最新承诺", "完工承诺"))
                action = any(m in text for m in ("记", "记录", "登记", "保存", "录入", "record", "save"))
                explicit_supported_effect = explicit_supported_effect or (target and action)
            elif tool_name == "request_change_customer_delivery_date":
                target = any(m in text for m in (
                    "客户交期", "客户正式交期", "对客户交期", "对客交期",
                    "客户承诺交期", "对客户的正式交期", "customerdelivery", "customerdeliverydate",
                ))
                action = any(m in text for m in ("改", "修改", "调整", "变更", "推迟", "提前", "change", "update", "move", "set"))
                explicit_supported_effect = explicit_supported_effect or (target and action)
            elif tool_name == "request_record_contact":
                explicit_supported_effect = explicit_supported_effect or (
                    any(m in text for m in ("记录联系", "记录已联系", "记下已联系", "recordcontact"))
                )
            elif tool_name == "request_set_waiting":
                explicit_supported_effect = explicit_supported_effect or (
                    any(m in text for m in ("先等", "等待", "设为等待", "进入等待", "wait"))
                )
            elif tool_name == "request_update_internal_plan":
                explicit_supported_effect = explicit_supported_effect or (
                    any(m in text for m in ("内部计划", "跟进计划", "更新计划", "调整计划", "internalplan"))
                )
            elif tool_name == "request_link_message_order":
                explicit_supported_effect = explicit_supported_effect or (
                    any(m in text for m in ("关联消息", "绑定消息", "关联订单", "linkmessage"))
                )

        if not explicit_supported_effect:
            return {
                "guard_version": D13_SEMANTIC_GUARD_VERSION,
                "code": "UNDEFINED_DELAY_EFFECT_REQUIRES_CLARIFICATION",
                "blocked_tool": str(effect_calls[0].get("tool_name") or ""),
                "clarification_question": (
                    "“接受延期方案”目前不是一个已定义的可执行业务动作。"
                    "你是要记录供应商最新完成承诺，还是要正式修改对客户的交期？"
                    "如果要修改客户正式交期，请明确目标日期。"
                ),
            }

    # Formal customer-delivery changes always need an explicit customer target
    # plus a change verb, independently of the generic undefined-delay guard.
    if not any(c.get("tool_name") == "request_change_customer_delivery_date" for c in calls):
        return None
    customer_target_markers = (
        "客户交期", "客户正式交期", "对客户交期", "对客交期",
        "客户承诺交期", "对客户的正式交期", "customerdelivery",
        "customerdeliverydate",
    )
    change_markers = ("改", "修改", "调整", "变更", "推迟", "提前", "change", "update", "move", "set")
    target_explicit = any(marker in text for marker in customer_target_markers)
    change_explicit = any(marker in text for marker in change_markers)
    if target_explicit and change_explicit:
        return None
    return {
        "guard_version": D13_SEMANTIC_GUARD_VERSION,
        "code": "CUSTOMER_DELIVERY_INTENT_NOT_EXPLICIT",
        "blocked_tool": "request_change_customer_delivery_date",
        "clarification_question": (
            "你说的延期是要记录供应商最新完成承诺，还是要正式修改对客户的交期？"
            "如果要修改客户正式交期，请明确目标日期。"
        ),
    }

def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def tool_manifest() -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    for spec in CONTROLLED_TOOL_CATALOG.values():
        item = {
            "tool_name": spec.tool_name,
            "mode": spec.mode,
            "description": spec.description,
            "required_payload_keys": list(spec.required_payload_keys),
            "allowed_payload_keys": list(spec.allowed_payload_keys),
        }
        if spec.action_type:
            item.update({
                "action_type": spec.action_type,
                "required_review": d12.classify_action(spec.action_type),
            })
        tools.append(item)
    return {
        "skill_version": D13_SKILL_VERSION,
        "tool_contract_version": D13_TOOL_CONTRACT_VERSION,
        "tools": tools,
        "forbidden_tools": sorted(FORBIDDEN_TOOL_NAMES),
        "authority": {
            "model_can_grant_permission": False,
            "agent_can_approve_review": False,
            "agent_can_submit_review": False,
            "agent_can_execute_external_effect": False,
            "erp_write_path": "BUSINESS_SEMANTIC_REQUEST -> D12 -> D10 -> OUTBOX -> FUTURE_ERP_ADAPTER",
            "human_identity_source": "AUTHENTICATED_SERVER_IDENTITY",
            "effect_gate": "D12_HUMAN_REVIEW",
            "durable_commit": "D10_BUSINESS_ACTION_OUTBOX",
        },
    }


def _task_binding(conn: Any, *, task_id: str, identity: CurrentIdentity) -> dict[str, Any]:
    task_id = str(task_id or "").strip()
    if not task_id:
        raise D13ValidationError("task_id is required for effectful requests")
    task_row = _conn_exec(
        conn,
        "SELECT * FROM d9_action_case_tasks WHERE task_id=? AND organization_id=?",
        (task_id, identity.organization_id),
    ).fetchone()
    if not task_row:
        raise D13NotFoundError("Task not found")
    task = _row_to_dict(task_row)
    case_row = _conn_exec(
        conn,
        "SELECT * FROM action_cases WHERE action_case_id=? AND organization_id=?",
        (task["action_case_id"], identity.organization_id),
    ).fetchone()
    if not case_row:
        raise D13NotFoundError("Action Case not found")
    case = _row_to_dict(case_row)
    order_row = _conn_exec(
        conn,
        "SELECT * FROM orders WHERE order_id=? AND organization_id=?",
        (case["order_id"], identity.organization_id),
    ).fetchone()
    if not order_row:
        raise D13NotFoundError("Order not found")
    order = _row_to_dict(order_row)
    require_order_access(identity, order, conn=conn)
    return {"task": task, "case": case, "order": order}


def _validate_payload(spec: ControlledToolSpec, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise D13ValidationError("payload must be an object")
    authority_keys = sorted(set(payload) & IDENTITY_OR_AUTHORITY_KEYS)
    if authority_keys:
        raise D13ForbiddenError(
            "Agent payload may not carry identity/authority fields: " + ", ".join(authority_keys)
        )
    unknown = sorted(set(payload) - set(spec.allowed_payload_keys))
    if unknown:
        raise D13ValidationError("unsupported payload fields: " + ", ".join(unknown))
    missing = [key for key in spec.required_payload_keys if payload.get(key) in (None, "")]
    if missing:
        raise D13ValidationError("missing required payload fields: " + ", ".join(missing))
    clean = dict(payload)
    try:
        validate_business_dates(clean, fields=spec.allowed_payload_keys)
    except BusinessValueValidationError as exc:
        raise D13ValidationError(str(exc)) from exc
    # Qualification exposed a contract gap: the Tool Manifest required waiting_on
    # but did not define a canonical value set, while the evaluator assumed an
    # English enum. Normalize this deterministically in the backend instead of
    # making model language choice a business-state risk.
    if spec.tool_name == "request_set_waiting" and "waiting_on" in clean:
        raw_waiting_on = str(clean.get("waiting_on") or "").strip().lower()
        aliases = {
            "customer": "customer", "客户": "customer", "客户方": "customer",
            "supplier": "supplier", "供应商": "supplier", "工厂": "supplier",
            "internal": "internal", "内部": "internal", "我方": "internal", "自己": "internal",
            "other": "other", "其他": "other",
        }
        normalized_waiting_on = aliases.get(raw_waiting_on)
        if not normalized_waiting_on:
            raise D13ValidationError(
                "waiting_on must resolve to one of: customer, supplier, internal, other"
            )
        clean["waiting_on"] = normalized_waiting_on
    return clean


def validate_tool_payload(tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate any Agent-facing tool payload against the same allowlist contract."""
    normalized_tool = str(tool_name or "").strip().lower()
    if normalized_tool in FORBIDDEN_TOOL_NAMES:
        raise D13ForbiddenError(f"tool {normalized_tool!r} is forbidden in D13 V2")
    spec = CONTROLLED_TOOL_CATALOG.get(normalized_tool)
    if not spec:
        raise D13ForbiddenError(f"unknown tool {normalized_tool!r}; D13 fails closed")
    return _validate_payload(spec, payload or {})


def request_controlled_action(
    conn: Any,
    *,
    tool_name: str,
    task_id: str,
    payload: dict[str, Any],
    identity: CurrentIdentity,
    idempotency_key: str,
    reason: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Translate one semantic Agent request into a D12 Human Review request."""
    normalized_tool = str(tool_name or "").strip().lower()
    if normalized_tool in FORBIDDEN_TOOL_NAMES:
        raise D13ForbiddenError(f"tool {normalized_tool!r} is forbidden in D13 V2")
    spec = CONTROLLED_TOOL_CATALOG.get(normalized_tool)
    if not spec:
        raise D13ForbiddenError(f"unknown tool {normalized_tool!r}; D13 fails closed")
    if spec.mode != MODE_REQUEST_D12 or not spec.action_type or not spec.target_type:
        raise D13ForbiddenError(f"tool {normalized_tool!r} does not create business-action requests")

    requirement = d12.classify_action(spec.action_type)
    if requirement == d12.REQUIREMENT_FORBIDDEN:
        raise D13ForbiddenError(f"D12 forbids action_type {spec.action_type!r}")

    clean_payload = _validate_payload(spec, payload)
    binding = _task_binding(conn, task_id=str(task_id or "").strip(), identity=identity)
    target_id = binding["task"]["task_id"] if spec.target_type == "TASK" else binding["order"]["order_id"]
    idem = str(idempotency_key or "").strip()
    if not idem:
        raise D13ValidationError("idempotency_key is required")

    submission = d10.BusinessActionSubmission(
        organization_id=identity.organization_id,
        task_id=binding["task"]["task_id"],
        action_type=spec.action_type,
        target_type=spec.target_type,
        target_id=target_id,
        payload=clean_payload,
        idempotency_key=idem,
        actor=identity.user_id,
        request_id=str(request_id or _new_id("D13REQ")),
        source="D13_AGENT_TOOL_REQUEST",
        reason=reason or spec.description,
    )
    review = d12.request_review(
        conn,
        d12.ReviewRequest(submission=submission),
        identity=identity,
    )
    return {
        "skill_version": D13_SKILL_VERSION,
        "tool_contract_version": D13_TOOL_CONTRACT_VERSION,
        "tool_name": normalized_tool,
        "action_type": spec.action_type,
        "required_review": review.get("required_review"),
        "review_id": review.get("review_id"),
        "review_status": review.get("status"),
        "replayed": bool(review.get("replayed")),
        "target_type": spec.target_type,
        "target_id": target_id,
        "order_id": binding["order"]["order_id"],
        "task_id": binding["task"]["task_id"],
        "agent_executed_effect": False,
        "human_decision_required": True,
        "next_boundary": "D12_HUMAN_REVIEW",
    }


def _load_order_by_ref(conn: Any, *, payload: dict[str, Any], identity: CurrentIdentity) -> dict[str, Any]:
    order_id = str(payload.get("order_id") or "").strip()
    order_no = str(payload.get("order_no") or "").strip()
    if not order_id and not order_no:
        raise D13ValidationError("get_order_context requires order_id or order_no when no task_id is supplied")
    if order_id:
        row = _conn_exec(
            conn,
            "SELECT * FROM orders WHERE order_id=? AND organization_id=?",
            (order_id, identity.organization_id),
        ).fetchone()
    else:
        rows = _conn_exec(
            conn,
            "SELECT * FROM orders WHERE order_no=? AND organization_id=? ORDER BY order_id LIMIT 2",
            (order_no, identity.organization_id),
        ).fetchall()
        if len(rows) > 1:
            raise D13ValidationError("order_no is not unique in current organization")
        row = rows[0] if rows else None
    if not row:
        raise D13NotFoundError("Order not found")
    order = _row_to_dict(row)
    require_order_access(identity, order, conn=conn)
    return order


def get_order_context(
    conn: Any,
    *,
    identity: CurrentIdentity,
    task_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read a compact, organization-bound context for Read-Before-Ask."""
    clean = validate_tool_payload("get_order_context", payload or {})
    if str(task_id or "").strip():
        binding = _task_binding(conn, task_id=str(task_id).strip(), identity=identity)
        order = binding["order"]
        cases = [binding["case"]]
        tasks = [binding["task"]]
    else:
        order = _load_order_by_ref(conn, payload=clean, identity=identity)
        cases = [
            _row_to_dict(r)
            for r in _conn_exec(
                conn,
                "SELECT * FROM action_cases WHERE order_id=? AND organization_id=? AND lifecycle_status!='CLOSED' ORDER BY updated_at DESC LIMIT 5",
                (order["order_id"], identity.organization_id),
            ).fetchall()
        ]
        tasks = [
            _row_to_dict(r)
            for r in _conn_exec(
                conn,
                "SELECT * FROM d9_action_case_tasks WHERE organization_id=? AND action_case_id IN (SELECT action_case_id FROM action_cases WHERE order_id=? AND organization_id=?) ORDER BY updated_at DESC LIMIT 10",
                (identity.organization_id, order["order_id"], identity.organization_id),
            ).fetchall()
        ]

    waitings = [
        _row_to_dict(r)
        for r in _conn_exec(
            conn,
            "SELECT * FROM d9_action_case_waitings WHERE organization_id=? AND task_id IN (SELECT task_id FROM d9_action_case_tasks WHERE organization_id=? AND action_case_id IN (SELECT action_case_id FROM action_cases WHERE order_id=? AND organization_id=?)) ORDER BY created_at DESC LIMIT 10",
            (identity.organization_id, identity.organization_id, order["order_id"], identity.organization_id),
        ).fetchall()
    ]
    messages = [
        _row_to_dict(r)
        for r in _conn_exec(
            conn,
            "SELECT * FROM source_messages WHERE order_id=? AND organization_id=? ORDER BY source_time DESC, created_at DESC LIMIT 8",
            (order["order_id"], identity.organization_id),
        ).fetchall()
    ]
    return {
        "tool_name": "get_order_context",
        "order": order,
        "action_cases": cases,
        "tasks": tasks,
        "waiting": waitings,
        "recent_messages": messages,
        "authority": {"organization_id": identity.organization_id, "user_id": identity.user_id},
        "business_state_changed": False,
    }


def get_actionable_orders(
    conn: Any,
    *,
    identity: CurrentIdentity,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the same D14.2 Risk Attention ranking used by the product UI.

    D13 previously used the legacy anomaly+task score path, which could disagree
    with D7.  This read-only tool now consumes the single deterministic D7
    attention ranking; it still cannot persist candidates or cause any business
    side effect.
    """
    clean = validate_tool_payload("get_actionable_orders", payload or {})
    from d7_risk_engine import run_d7_pipeline

    result = run_d7_pipeline(
        conn,
        identity,
        top_n=int(clean.get("top_n") or 7),
        due_within_days=int(clean.get("due_within_days") or 14),
    )
    items = (
        result.get("risk_attention_items")
        or result.get("my_action_items")
        or result.get("team_action_items")
        or result.get("items")
        or []
    )
    return {
        "tool_name": "get_actionable_orders",
        "items": items,
        "information_gaps": result.get("information_gaps") or [],
        "selection_strategy": result.get("selection_strategy") or {},
        "business_state_changed": False,
        "ranking_source": "D14_2_RISK_ATTENTION",
    }


def get_review_status(
    conn: Any,
    *,
    identity: CurrentIdentity,
    payload: dict[str, Any],
) -> dict[str, Any]:
    clean = validate_tool_payload("get_review_status", payload)
    row = _conn_exec(
        conn,
        "SELECT * FROM d12_human_reviews WHERE review_id=? AND organization_id=?",
        (clean["review_id"], identity.organization_id),
    ).fetchone()
    if not row:
        raise D13NotFoundError("Review not found")
    review = _row_to_dict(row)
    return {
        "tool_name": "get_review_status",
        "review_id": review["review_id"],
        "status": review["status"],
        "required_review": review["required_review"],
        "action_type": review["action_type"],
        "task_id": review["task_id"],
        "order_id": review["order_id"],
        "business_action_id": review.get("business_action_id"),
        "external_effect_executed": False,
        "business_state_changed": False,
    }


def execute_non_effect_tool(
    conn: Any,
    *,
    tool_name: str,
    identity: CurrentIdentity,
    task_id: str | None,
    payload: dict[str, Any],
    response_draft: str | None = None,
) -> dict[str, Any]:
    """Execute READ/SUGGEST tools only; effectful tools use request_controlled_action."""
    normalized = str(tool_name or "").strip().lower()
    spec = CONTROLLED_TOOL_CATALOG.get(normalized)
    if not spec:
        raise D13ForbiddenError(f"unknown tool {normalized!r}; D13 fails closed")
    if spec.mode == MODE_REQUEST_D12:
        raise D13ForbiddenError("effectful tool must go through request_controlled_action")
    if normalized == "get_order_context":
        return get_order_context(conn, identity=identity, task_id=task_id, payload=payload)
    if normalized == "get_actionable_orders":
        return get_actionable_orders(conn, identity=identity, payload=payload)
    if normalized == "get_review_status":
        return get_review_status(conn, identity=identity, payload=payload)
    if normalized == "draft_message":
        clean = validate_tool_payload(normalized, payload)
        return {
            "tool_name": normalized,
            "draft": str(response_draft or "").strip() or None,
            "draft_request": clean,
            "external_effect_executed": False,
            "business_state_changed": False,
        }
    raise D13ForbiddenError(f"tool {normalized!r} has no D13 executor")
