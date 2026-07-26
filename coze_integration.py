from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

CN_TZ = timezone(timedelta(hours=8))
DEFAULT_API_BASE = "https://api.coze.cn"
DEFAULT_WORKFLOW_IDS = {
    "ft01": "7666718083223076906",
    "ft02": "7666718126143373352",
    "ft03": "7666767992962154522",
    "ft04": "7666718175644041266",
}
WORKFLOW_ENV_KEYS = {
    "ft01": "COZE_FT01_WORKFLOW_ID",
    "ft02": "COZE_FT02_WORKFLOW_ID",
    "ft03": "COZE_FT03_WORKFLOW_ID",
    "ft04": "COZE_FT04_WORKFLOW_ID",
}

ACTION_COPY = {
    "reply_customer": ("回复客户并同步处理进展", "立即回复客户并说明处理计划", "customer"),
    "confirm_with_factory": ("确认变更是否影响交期", "联系工厂确认变更是否影响交期", "factory"),
    "check_order": ("核对订单并确定下一步", "核对订单状态并明确后续动作", "internal"),
    "handle_exception": ("处理订单异常", "确认异常原因、负责人和补救时间", "factory"),
    "forward_to_owner": ("转交正确负责人", "转交并保留原始证据", "internal"),
    "wait_for_reply": ("等待对方回复", "等待承诺回复时间后再处理", "unknown"),
    "confirm_commitment": ("确认明确承诺日期", "向工厂确认具体完工日期和承诺确定性", "factory"),
    "ask_for_remedy": ("补问补救方案", "补问补救措施、负责人和预计完成时间", "factory"),
}

RISK_LEVELS = {
    "customer_complaint": "critical",
    "customer_cancellation": "critical",
    "delivery_impact_unknown": "high",
    "delivery_risk": "high",
    "material_shortage": "high",
    "document_conflict": "medium",
    "order_match_ambiguous": "medium",
    "commitment_uncertain": "medium",
    "missing_answer": "medium",
    "missing_information": "medium",
    "other": "medium",
}


class CozeWorkflowError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: int | None = None,
                 debug_url: str | None = None, workflow_key: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.debug_url = debug_url
        self.workflow_key = workflow_key


@dataclass
class WorkflowRun:
    run_id: str
    workflow_key: str
    workflow_id: str
    parameters: dict[str, Any]
    result: dict[str, Any]
    raw_data: Any
    debug_url: str | None
    duration_ms: int
    envelope: dict[str, Any]


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def workflow_id(workflow_key: str) -> str:
    key = workflow_key.lower()
    return os.getenv(WORKFLOW_ENV_KEYS[key], DEFAULT_WORKFLOW_IDS[key]).strip()


def coze_status() -> dict[str, Any]:
    token = bool(os.getenv("COZE_API_TOKEN", "").strip())
    ids = {key: workflow_id(key) for key in DEFAULT_WORKFLOW_IDS}
    return {
        "platform": "coze.cn",
        "space_id": os.getenv("COZE_SPACE_ID", "7660328260442046474"),
        "api_base": os.getenv("COZE_API_BASE", DEFAULT_API_BASE).rstrip("/"),
        "token_configured": token,
        "workflows": {
            key: {"configured": bool(value), "workflow_id": value}
            for key, value in ids.items()
        },
        "ready": token and all(ids.values()),
    }


def _parse_json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _parse_result_data(data: Any) -> tuple[dict[str, Any], Any]:
    parsed_data = _parse_json(data, {})
    if isinstance(parsed_data, dict):
        result_raw = parsed_data.get("result_json", parsed_data)
    else:
        result_raw = parsed_data
    result = _parse_json(result_raw, {})
    if not isinstance(result, dict):
        result = {"value": result}
    return result, parsed_data



def _clean_workflow_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    """Remove empty optional values before sending them to Coze."""
    cleaned: dict[str, Any] = {}
    for key, value in parameters.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[key] = value
    return cleaned


def _parameter_modes() -> list[str]:
    mode = os.getenv("COZE_PARAMETERS_MODE", "auto").strip().lower()
    if mode == "object":
        return ["object"]
    if mode == "string":
        return ["string"]
    return ["object", "string"]


def _coze_payload(workflow_id_value: str, parameters: dict[str, Any], mode: str) -> dict[str, Any]:
    return {
        "workflow_id": workflow_id_value,
        "parameters": (
            parameters
            if mode == "object"
            else json.dumps(parameters, ensure_ascii=False, separators=(",", ":"))
        ),
    }


def _invalid_parameter_response(status_code: int, envelope: dict[str, Any]) -> bool:
    try:
        code = int(envelope.get("code", -1))
    except (TypeError, ValueError):
        code = -1
    msg = str(envelope.get("msg") or envelope.get("message") or "").lower()
    return (
        status_code in {400, 422}
        or code == 4000
        or "invalid request parameter" in msg
        or "invalid parameters" in msg
        or "请求参数" in msg
    )


def run_workflow(
    workflow_key: str,
    parameters: dict[str, Any],
    *,
    timeout_seconds: float | None = None,
    record: Callable[[dict[str, Any]], None] | None = None,
) -> WorkflowRun:
    """Run one published Coze workflow.

    Auto mode first sends start-node parameters as a JSON object. When Coze
    explicitly rejects that input shape, it retries once using the legacy
    serialized JSON-string format.
    """
    key = workflow_key.lower()
    if key not in DEFAULT_WORKFLOW_IDS:
        raise CozeWorkflowError(f"未知工作流：{workflow_key}", workflow_key=key)

    token = os.getenv("COZE_API_TOKEN", "").strip()
    if not token:
        raise CozeWorkflowError("Render尚未配置COZE_API_TOKEN", workflow_key=key)

    wid = workflow_id(key)
    if not wid:
        raise CozeWorkflowError(f"未配置{WORKFLOW_ENV_KEYS[key]}", workflow_key=key)

    cleaned_parameters = _clean_workflow_parameters(parameters)
    api_base = os.getenv("COZE_API_BASE", DEFAULT_API_BASE).rstrip("/")
    timeout = timeout_seconds or float(os.getenv("COZE_WORKFLOW_TIMEOUT_SECONDS", "180"))

    bot_id = os.getenv(f"COZE_{key.upper()}_BOT_ID", os.getenv("COZE_BOT_ID", "")).strip()
    app_id = os.getenv(f"COZE_{key.upper()}_APP_ID", os.getenv("COZE_APP_ID", "")).strip()
    if bot_id and app_id:
        raise CozeWorkflowError("bot_id与app_id不能同时配置", workflow_key=key)

    run_id = f"CZR-{uuid.uuid4().hex[:12].upper()}"
    started = time.perf_counter()
    modes = _parameter_modes()
    attempted_modes: list[str] = []
    last_status: int | None = None
    last_envelope: dict[str, Any] = {}
    last_debug_url: str | None = None

    try:
        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0)) as client:
            for index, mode in enumerate(modes):
                attempted_modes.append(mode)
                payload = _coze_payload(wid, cleaned_parameters, mode)
                if bot_id:
                    payload["bot_id"] = bot_id
                if app_id:
                    payload["app_id"] = app_id

                response = client.post(
                    f"{api_base}/v1/workflow/run",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                last_status = response.status_code
                try:
                    envelope = response.json()
                except ValueError as exc:
                    raise CozeWorkflowError(
                        f"Coze返回非JSON内容：HTTP {response.status_code}",
                        status_code=response.status_code,
                        workflow_key=key,
                    ) from exc

                last_envelope = envelope
                last_debug_url = envelope.get("debug_url")

                if (
                    index + 1 < len(modes)
                    and _invalid_parameter_response(response.status_code, envelope)
                ):
                    continue

                if response.status_code >= 400:
                    raise CozeWorkflowError(
                        f"Coze HTTP调用失败：{response.status_code} {envelope}",
                        status_code=response.status_code,
                        debug_url=last_debug_url,
                        workflow_key=key,
                    )

                try:
                    code = int(envelope.get("code", -1))
                except (TypeError, ValueError):
                    code = -1

                if code != 0:
                    keys = ",".join(cleaned_parameters.keys())
                    raise CozeWorkflowError(
                        (
                            f"Coze工作流执行失败：{envelope.get('msg') or 'unknown error'}；"
                            f"parameters模式={mode}；参数键={keys}"
                        ),
                        code=code,
                        debug_url=last_debug_url,
                        workflow_key=key,
                    )

                duration_ms = round((time.perf_counter() - started) * 1000)
                result, raw_data = _parse_result_data(envelope.get("data"))
                if not result:
                    raise CozeWorkflowError(
                        "Coze工作流未返回result_json",
                        code=code,
                        debug_url=last_debug_url,
                        workflow_key=key,
                    )

                if record:
                    record({
                        "run_id": run_id,
                        "workflow_key": key,
                        "workflow_id": wid,
                        "status": "SUCCESS",
                        "input_json": json.dumps(
                            {"parameters_mode": mode, "parameters": cleaned_parameters},
                            ensure_ascii=False,
                        ),
                        "output_json": json.dumps(result, ensure_ascii=False),
                        "coze_code": code,
                        "coze_msg": envelope.get("msg"),
                        "debug_url": last_debug_url,
                        "duration_ms": duration_ms,
                        "created_at": now_iso(),
                    })

                return WorkflowRun(
                    run_id=run_id,
                    workflow_key=key,
                    workflow_id=wid,
                    parameters=cleaned_parameters,
                    result=result,
                    raw_data=raw_data,
                    debug_url=last_debug_url,
                    duration_ms=duration_ms,
                    envelope=envelope,
                )

        raise CozeWorkflowError(
            f"Coze调用未返回结果；已尝试：{','.join(attempted_modes)}",
            status_code=last_status,
            debug_url=last_debug_url,
            workflow_key=key,
        )

    except CozeWorkflowError as exc:
        duration_ms = round((time.perf_counter() - started) * 1000)
        if record:
            record({
                "run_id": run_id,
                "workflow_key": key,
                "workflow_id": wid,
                "status": "FAILED",
                "input_json": json.dumps(
                    {
                        "attempted_parameter_modes": attempted_modes,
                        "parameters": cleaned_parameters,
                    },
                    ensure_ascii=False,
                ),
                "output_json": json.dumps(
                    {
                        "error": str(exc),
                        "last_status": last_status,
                        "last_envelope": last_envelope,
                    },
                    ensure_ascii=False,
                ),
                "coze_code": exc.code,
                "coze_msg": str(exc),
                "debug_url": exc.debug_url or last_debug_url,
                "duration_ms": duration_ms,
                "created_at": now_iso(),
            })
        raise

    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        duration_ms = round((time.perf_counter() - started) * 1000)
        error = CozeWorkflowError(f"连接Coze失败或超时：{exc}", workflow_key=key)
        if record:
            record({
                "run_id": run_id,
                "workflow_key": key,
                "workflow_id": wid,
                "status": "FAILED",
                "input_json": json.dumps(
                    {
                        "attempted_parameter_modes": attempted_modes,
                        "parameters": cleaned_parameters,
                    },
                    ensure_ascii=False,
                ),
                "output_json": json.dumps({"error": str(error)}, ensure_ascii=False),
                "coze_code": None,
                "coze_msg": str(error),
                "debug_url": None,
                "duration_ms": duration_ms,
                "created_at": now_iso(),
            })
        raise error from exc

def action_candidate(action: dict[str, Any]) -> dict[str, Any]:
    action_type = str(action.get("action_type") or "check_order")
    default_title, default_action, default_target = ACTION_COPY.get(
        action_type, ("处理AI识别行动", "核对候选并确定下一步", "internal")
    )
    return {
        **action,
        "action_type": action_type,
        "title": action.get("title") or default_title,
        "recommended_action": action.get("recommended_action") or default_action,
        "target": action.get("target") or action.get("target_role") or default_target,
        "requires_confirmation": action.get("requires_confirmation", True),
    }


def risk_signal(risk: dict[str, Any]) -> dict[str, Any]:
    risk_type = str(risk.get("type") or risk.get("risk_type") or "other")
    return {
        **risk,
        "type": risk_type,
        "risk_level": risk.get("risk_level") or RISK_LEVELS.get(risk_type, "medium"),
        "evidence": risk.get("evidence") or risk.get("source_quote"),
    }


def normalize_ft01(result: dict[str, Any], *, order: dict[str, Any] | None, run: WorkflowRun) -> dict[str, Any]:
    candidate = dict(result)
    candidate["fields"] = [dict(x) for x in result.get("fields") or []]
    candidate["risk_signals"] = [risk_signal(x) for x in result.get("risk_signals") or result.get("risk_cues") or []]
    candidate["action_candidates"] = [action_candidate(x) for x in result.get("action_candidates") or []]
    candidate.setdefault("message_type", "customer_request")
    match = dict(candidate.get("order_match") or {})
    if order and match.get("status") in {None, "no_match", "new"}:
        match.update({
            "status": "unique_match",
            "selected_order_id": order.get("order_id"),
            "matched_order_no": order.get("order_no"),
        })
    candidate["order_match"] = match
    candidate["_integration"] = {
        "workflow_key": "ft01",
        "workflow_run_id": run.run_id,
        "debug_url": run.debug_url,
        "duration_ms": run.duration_ms,
        "raw_result": result,
    }
    return candidate


def normalize_ft02(
    result: dict[str, Any], *, order: dict[str, Any] | None, task: dict[str, Any] | None, run: WorkflowRun
) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    progress = result.get("progress") or {}
    if progress.get("percentage") is not None:
        fields.append({
            "field_name": "current_progress",
            "old_value": order.get("current_progress") if order else None,
            "normalized_value": progress.get("percentage"),
            "source_quote": progress.get("raw_expression"),
            "confidence": 0.9,
            "review_status": "needs_review" if progress.get("raw_expression") and "差不多" in str(progress.get("raw_expression")) else "confirmed",
        })
    commitment = result.get("supplier_commitment") or {}
    if commitment.get("latest_date"):
        fields.append({
            "field_name": "latest_supplier_commitment",
            "old_value": order.get("latest_supplier_commitment") if order else None,
            "normalized_value": commitment.get("latest_date"),
            "source_quote": commitment.get("raw_expression"),
            "confidence": 0.9,
            "review_status": "needs_review" if any(x in str(commitment.get("raw_expression") or "") for x in ["应该", "大概", "预计", "可能"]) else "confirmed",
        })
    for exception in result.get("exceptions") or []:
        fields.append({
            "field_name": "exception_description",
            "old_value": None,
            "normalized_value": exception.get("reason") or exception.get("type"),
            "source_quote": exception.get("source_quote"),
            "confidence": 0.9,
            "review_status": "confirmed",
        })
    risks = [risk_signal(x) for x in result.get("risk_signals") or []]
    actions = [action_candidate(x) for x in result.get("action_candidates") or []]
    candidate = {
        "schema_version": result.get("schema_version"),
        "workflow_id": result.get("workflow_id"),
        "run_status": result.get("run_status"),
        "message_type": result.get("message_type") or "factory_update",
        "order_match": {
            "status": "unique_match" if order else "no_match",
            "selected_order_id": order.get("order_id") if order else None,
            "matched_order_no": order.get("order_no") if order else result.get("order_no"),
        },
        "fields": fields,
        "risk_signals": risks,
        "action_candidates": actions,
        "answer_coverage": result.get("answer_coverage") or [],
        "followup_messages": result.get("followup_messages") or [],
        "task_transition_candidate": result.get("task_transition_candidate") or {},
        "manual_review_required": result.get("manual_review_required", False),
        "writeback_confirmation_required": True,
        "_integration": {
            "workflow_key": "ft02",
            "workflow_run_id": run.run_id,
            "debug_url": run.debug_url,
            "duration_ms": run.duration_ms,
            "task_id": task.get("task_id") if task else None,
            "raw_result": result,
        },
    }
    return candidate


def confirmed_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    order_changes = []
    for field in candidate.get("fields") or []:
        value = field.get("normalized_value", field.get("final_value"))
        if value is None or field.get("field_name") == "order_no":
            continue
        order_changes.append({
            "field_name": field.get("field_name"),
            "final_value": value,
            "confirmation_status": field.get("confirmation_status") or "confirmed",
            "source_quote": field.get("source_quote"),
        })
    risk_changes = []
    for risk in candidate.get("risk_signals") or []:
        item = risk_signal(risk)
        risk_changes.append({
            "field_name": "risk_type",
            "final_value": item.get("type"),
            "risk_level": item.get("risk_level"),
            "confirmation_status": risk.get("confirmation_status") or "confirmed",
            "source_quote": item.get("evidence"),
        })
    actions = [action_candidate(x) for x in candidate.get("action_candidates") or []]
    first = actions[0] if actions else None
    task_changes = []
    task_id = (candidate.get("_integration") or {}).get("task_id")
    if task_id and first:
        task_changes.append({
            "field_name": "recommended_action",
            "final_value": first.get("recommended_action"),
            "confirmation_status": "confirmed",
            "source_quote": first.get("source_quote"),
        })
    action_decision = None
    if first:
        transition = candidate.get("task_transition_candidate") or {}
        action_decision = {
            "final_action_state": transition.get("candidate_state") or ("DO_NOW" if any(x.get("risk_level") == "critical" for x in risk_changes) else "NEEDS_CONFIRMATION"),
            "recommended_action": first.get("recommended_action"),
            "title": first.get("title"),
            "target": first.get("target"),
            "next_action_at": first.get("next_action_at"),
            "waiting_on": first.get("waiting_on"),
            "confirmation_status": "confirmed",
        }
    return {
        "order_match": candidate.get("order_match") or {},
        "order_changes": order_changes,
        "task_changes": task_changes,
        "risk_changes": risk_changes,
        "action_decision": action_decision,
    }
