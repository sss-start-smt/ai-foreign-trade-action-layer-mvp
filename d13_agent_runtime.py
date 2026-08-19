"""FlowOrder D13 controlled Agent Runtime and business trace.

The runtime records only auditable business execution facts (trigger, model
identity, validated plan shape, tool requests/results, human-gate creation and
stop reason). It never stores or exposes hidden chain-of-thought.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import d13_agent_skill as d13
import d13_skill_runtime as skill_runtime
import d13_model_provider as model_provider
from auth import CurrentIdentity
from d8_action_case import _conn_exec, _row_to_dict

CN_TZ = timezone(timedelta(hours=8))
D13_TRACE_VERSION = "D13_BUSINESS_TRACE_V1_1"

# Safety fuse, not a business capability limit. It is calibrated against the
# D13 V1 worst supported Top-7 chain (1 ranking read + 7 context reads + 7 distinct-task requests = 15 calls) and must be re-baselined when the Skill or
# Tool Catalog expands. Effect counts are governed structurally: one effect per
# Task, not by an arbitrary global "3 effects" rule.
D13_EMERGENCY_TOOL_CALL_CAP = 20

STATUS_RUNNING = "RUNNING"
STATUS_WAITING_HUMAN = "WAITING_HUMAN"
STATUS_COMPLETED = "COMPLETED"
STATUS_CLARIFICATION = "CLARIFICATION_REQUIRED"
STATUS_REFUSED = "REFUSED"
STATUS_NO_ACTION = "NO_ACTION"
STATUS_BUDGET_REACHED = "BUDGET_REACHED"
STATUS_FAILED = "FAILED"

STOP_READ_RESULTS_READY = "READ_RESULTS_READY"
STOP_WAITING_HUMAN = "WAITING_HUMAN"
STOP_GOAL_SATISFIED = "GOAL_SATISFIED"
STOP_CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
STOP_NO_ACTION = "NO_ACTION"
STOP_REFUSED = "REFUSED"
STOP_NO_PROGRESS = "NO_PROGRESS_LOOP_GUARD"
STOP_BUDGET = "SAFETY_BUDGET_EXHAUSTED"


class D13RuntimeError(RuntimeError):
    pass


class D13RunNotFound(D13RuntimeError):
    pass


class D13RunForbidden(D13RuntimeError):
    pass


class D13RunStateError(D13RuntimeError):
    pass


class D13ModelExecutionError(D13RuntimeError):
    """Public-safe model execution failure.

    Provider exception text is deliberately not propagated because upstream
    SDK/HTTP errors may embed API keys, Authorization headers, request bodies
    or other credentials. Diagnostics are carried by the structured
    ``error_kind`` field and MODEL_ATTEMPT telemetry instead.
    """

    def __init__(self, message: str = "D13 model execution failed", *, error_kind: str | None = None) -> None:
        super().__init__(message)
        self.error_kind = error_kind



@dataclass(frozen=True)
class StartRunRequest:
    goal: str
    trigger_type: str = skill_runtime.TRIGGER_USER_REQUEST
    trigger_ref: str | None = None
    current_datetime: str | None = None
    timezone: str = "Asia/Shanghai"
    context_refs: tuple[str, ...] = ()
    active_order_id: str | None = None
    active_order_no: str | None = None
    model_provider: str | None = None
    model_name: str | None = None


def _now() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _safe_json(value: str | None, default: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return default


def _load_run(conn: Any, run_id: str) -> dict[str, Any]:
    row = _conn_exec(conn, "SELECT * FROM d13_agent_runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise D13RunNotFound("D13 Agent Run not found")
    return _row_to_dict(row)


def _assert_run_access(run: dict[str, Any], identity: CurrentIdentity) -> None:
    if run.get("organization_id") != identity.organization_id:
        raise D13RunNotFound("D13 Agent Run not found")
    if not identity.is_manager() and run.get("current_user_id") != identity.user_id:
        raise D13RunForbidden("Operator may only access own D13 Agent Runs")


def _next_seq(conn: Any, run_id: str) -> int:
    row = _conn_exec(
        conn,
        "SELECT COALESCE(MAX(sequence_no),0)+1 FROM d13_agent_trace_events WHERE run_id=?",
        (run_id,),
    ).fetchone()
    return int(row[0])


def _record_event(
    conn: Any,
    *,
    run_id: str,
    event_type: str,
    status: str,
    tool_name: str | None = None,
    task_id: str | None = None,
    mode: str | None = None,
    request: dict[str, Any] | None = None,
    response: dict[str, Any] | None = None,
) -> str:
    event_id = _new_id("D13EVT")
    _conn_exec(
        conn,
        """INSERT INTO d13_agent_trace_events
           (event_id,run_id,sequence_no,event_type,tool_name,task_id,mode,request_json,response_json,status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            event_id, run_id, _next_seq(conn, run_id), event_type, tool_name, task_id, mode,
            _json(request or {}), _json(response or {}), status, _now(),
        ),
    )
    return event_id


def start_run(conn: Any, *, identity: CurrentIdentity, request: StartRunRequest) -> dict[str, Any]:
    goal = str(request.goal or "").strip()
    if not goal:
        raise D13RunStateError("goal is required")
    ctx = skill_runtime.RuntimeContext(
        trigger_type=request.trigger_type,
        current_datetime=request.current_datetime,
        timezone=request.timezone,
        context_refs=request.context_refs,
    ).normalized()
    active_context: dict[str, Any] = {}
    active_order_id = str(request.active_order_id or "").strip()
    active_order_no = str(request.active_order_no or "").strip()
    if active_order_id or active_order_no:
        order_ctx = d13.get_order_context(
            conn,
            identity=identity,
            payload={
                **({"order_id": active_order_id} if active_order_id else {}),
                **({"order_no": active_order_no} if active_order_no and not active_order_id else {}),
            },
        )
        order = order_ctx["order"]
        active_context = {"order_id": order.get("order_id"), "order_no": order.get("order_no")}

    default_primary, _ = model_provider.default_model_chain()
    selected_provider = str(request.model_provider or "").strip() or default_primary.provider
    selected_model = str(request.model_name or "").strip() or default_primary.model
    run_id = _new_id("D13RUN")
    now = _now()
    _conn_exec(
        conn,
        """INSERT INTO d13_agent_runs
           (run_id,organization_id,current_user_id,current_role,trigger_type,trigger_ref,goal,status,stop_reason,
            skill_version,tool_contract_version,transcription_version,model_provider,model_name,
            system_current_datetime,timezone,context_refs_json,tool_call_count,distinct_task_count,
            final_response,external_effect_executed,created_at,updated_at,completed_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id, identity.organization_id, identity.user_id, identity.role, ctx.trigger_type,
            str(request.trigger_ref or "").strip() or None, goal, STATUS_RUNNING, None,
            d13.D13_SKILL_VERSION, d13.D13_TOOL_CONTRACT_VERSION, skill_runtime.D13_TRANSCRIPTION_VERSION,
            selected_provider, selected_model,
            ctx.current_datetime, ctx.timezone, _json(list(ctx.context_refs)), 0, 0, None, 0,
            now, now, None,
        ),
    )
    _record_event(
        conn,
        run_id=run_id,
        event_type="RUN_STARTED",
        status="SUCCESS",
        request={
            "trigger_type": ctx.trigger_type,
            "trigger_ref": request.trigger_ref,
            "goal": goal,
            "system_current_datetime": ctx.current_datetime,
            "timezone": ctx.timezone,
            "context_refs": list(ctx.context_refs),
            "active_context": active_context,
            "model_provider": selected_provider,
            "model_name": selected_model,
        },
        response={
            "skill_version": d13.D13_SKILL_VERSION,
            "tool_contract_version": d13.D13_TOOL_CONTRACT_VERSION,
            "transcription_version": skill_runtime.D13_TRANSCRIPTION_VERSION,
            "semantic_guard_version": d13.D13_SEMANTIC_GUARD_VERSION,
            "trace_version": D13_TRACE_VERSION,
            "model_routing": {
                "version": model_provider.D13_MODEL_ROUTING_VERSION,
                "primary": model_provider.default_model_chain()[0].model,
                "fallback": model_provider.default_model_chain()[1].model,
                "retry_quick_transient": True,
                "retry_full_timeout": False,
                "json_format_retry_max": model_provider.DEFAULT_JSON_FORMAT_RETRIES,
                "preferred_route_follows_last_success": True,
        "preferred_route_cross_model_rescue": True,
                "fallback_on_semantic_error": False,
            },
        },
    )
    return serialize_run(_load_run(conn, run_id))


def _call_signature(call: dict[str, Any]) -> str:
    raw = _json({
        "tool_name": call["tool_name"],
        "task_id": call.get("task_id"),
        "payload": call.get("payload") or {},
    })
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _existing_success_signature(conn: Any, run_id: str, signature: str) -> bool:
    rows = _conn_exec(
        conn,
        "SELECT request_json FROM d13_agent_trace_events WHERE run_id=? AND event_type='TOOL_CALL' AND status='SUCCESS'",
        (run_id,),
    ).fetchall()
    for row in rows:
        payload = _safe_json(row[0], {})
        if payload.get("call_signature") == signature:
            return True
    return False


def _run_effect_tasks(conn: Any, run_id: str) -> set[str]:
    rows = _conn_exec(
        conn,
        "SELECT task_id,mode,status FROM d13_agent_trace_events WHERE run_id=? AND event_type='TOOL_CALL'",
        (run_id,),
    ).fetchall()
    return {
        str(row[0]) for row in rows
        if row[0] and row[1] == d13.MODE_REQUEST_D12 and row[2] == "SUCCESS"
    }


def _set_run_state(
    conn: Any,
    *,
    run_id: str,
    status: str,
    stop_reason: str | None,
    final_response: str | None = None,
    completed: bool = False,
) -> None:
    now = _now()
    _conn_exec(
        conn,
        """UPDATE d13_agent_runs
           SET status=?,stop_reason=?,final_response=COALESCE(?,final_response),updated_at=?,completed_at=?
           WHERE run_id=?""",
        (status, stop_reason, final_response, now, now if completed else None, run_id),
    )


def _effect_task_binding_guard(conn: Any, *, identity: CurrentIdentity, plan: dict[str, Any]) -> dict[str, Any] | None:
    """Fail closed before D12 if a model invents or cross-binds an effect Task id."""
    for call in plan.get("tool_calls") or []:
        if call.get("mode") != d13.MODE_REQUEST_D12:
            continue
        task_id = str(call.get("task_id") or "").strip()
        row = _conn_exec(
            conn,
            "SELECT t.task_id,a.order_id FROM d9_action_case_tasks t "
            "JOIN action_cases a ON a.action_case_id=t.action_case_id "
            "WHERE t.task_id=? AND t.organization_id=? AND a.organization_id=?",
            (task_id, identity.organization_id, identity.organization_id),
        ).fetchone()
        if not row:
            return {
                "guard_version": d13.D13_SEMANTIC_GUARD_VERSION,
                "code": "INVALID_EFFECT_TASK_BINDING",
                "blocked_tool": str(call.get("tool_name") or ""),
                "invalid_task_id": task_id or None,
                "clarification_question": (
                    "我还没有可靠绑定到可执行的跟单任务，不能创建正式业务请求。"
                    "请先读取订单上下文或选择正确的任务后再继续。"
                ),
            }
    return None


def apply_model_plan(
    conn: Any,
    *,
    run_id: str,
    identity: CurrentIdentity,
    raw_plan: dict[str, Any],
) -> dict[str, Any]:
    """Validate and execute one model planning turn within a D13 Run.

    READ-only turns return ``continue_model=True`` so the provider can receive
    observations and plan again. Human-gated effect requests are a natural stop
    condition: the run becomes WAITING_HUMAN rather than polling approvals.
    """
    run = _load_run(conn, run_id)
    _assert_run_access(run, identity)
    if run.get("status") != STATUS_RUNNING:
        raise D13RunStateError(f"run status {run.get('status')} does not accept a new model plan")

    plan = skill_runtime.validate_model_plan(raw_plan)
    semantic_guard = d13.business_semantic_guard(
        str(run.get("goal") or ""),
        plan,
        trusted_context=_trusted_model_context(conn, run=run, identity=identity),
    )
    if semantic_guard:
        _record_event(
            conn, run_id=run_id, event_type="MODEL_PLAN_SEMANTIC_GUARD", status="BLOCKED",
            request={
                "decision": plan["decision"],
                "tool_names": [c["tool_name"] for c in plan["tool_calls"]],
                "guard_version": semantic_guard.get("guard_version"),
            },
            response={
                "code": semantic_guard.get("code"),
                "blocked_tool": semantic_guard.get("blocked_tool"),
                "external_effect_executed": False,
            },
        )
        _set_run_state(
            conn, run_id=run_id, status=STATUS_CLARIFICATION,
            stop_reason=STOP_CLARIFICATION_REQUIRED,
            final_response=str(semantic_guard.get("clarification_question") or "").strip() or None,
            completed=True,
        )
        return {
            "run": serialize_run(_load_run(conn, run_id)),
            "observations": [],
            "continue_model": False,
            "semantic_guard": semantic_guard,
        }

    task_binding_guard = _effect_task_binding_guard(conn, identity=identity, plan=plan)
    if task_binding_guard:
        _record_event(
            conn, run_id=run_id, event_type="MODEL_PLAN_SEMANTIC_GUARD", status="BLOCKED",
            request={
                "decision": plan["decision"],
                "tool_names": [c["tool_name"] for c in plan["tool_calls"]],
                "guard_version": task_binding_guard.get("guard_version"),
            },
            response={
                "code": task_binding_guard.get("code"),
                "blocked_tool": task_binding_guard.get("blocked_tool"),
                "invalid_task_id": task_binding_guard.get("invalid_task_id"),
                "external_effect_executed": False,
            },
        )
        _set_run_state(
            conn, run_id=run_id, status=STATUS_CLARIFICATION,
            stop_reason=STOP_CLARIFICATION_REQUIRED,
            final_response=str(task_binding_guard.get("clarification_question") or "").strip() or None,
            completed=True,
        )
        return {
            "run": serialize_run(_load_run(conn, run_id)),
            "observations": [],
            "continue_model": False,
            "semantic_guard": task_binding_guard,
        }

    _record_event(
        conn,
        run_id=run_id,
        event_type="MODEL_PLAN_VALIDATED",
        status="SUCCESS",
        request={
            "decision": plan["decision"],
            "tool_names": [c["tool_name"] for c in plan["tool_calls"]],
            "evidence_refs": plan.get("evidence_refs") or [],
        },
        response={
            "effect_task_count": plan.get("effect_task_count", 0),
            "clarification_question": plan.get("clarification_question"),
            # final response is safe business output, not hidden reasoning.
            "response_draft": plan.get("response_draft"),
        },
    )

    decision = plan["decision"]
    if decision == "RESPOND_ONLY":
        _set_run_state(
            conn, run_id=run_id, status=STATUS_COMPLETED, stop_reason=STOP_GOAL_SATISFIED,
            final_response=plan.get("response_draft"), completed=True,
        )
        return {"run": serialize_run(_load_run(conn, run_id)), "observations": [], "continue_model": False}
    if decision == "CLARIFY":
        _set_run_state(
            conn, run_id=run_id, status=STATUS_CLARIFICATION,
            stop_reason=STOP_CLARIFICATION_REQUIRED,
            final_response=plan.get("clarification_question"), completed=True,
        )
        return {"run": serialize_run(_load_run(conn, run_id)), "observations": [], "continue_model": False}
    if decision == "NO_ACTION":
        _set_run_state(
            conn, run_id=run_id, status=STATUS_NO_ACTION, stop_reason=STOP_NO_ACTION,
            final_response=plan.get("response_draft"), completed=True,
        )
        return {"run": serialize_run(_load_run(conn, run_id)), "observations": [], "continue_model": False}
    if decision == "REFUSE":
        _set_run_state(
            conn, run_id=run_id, status=STATUS_REFUSED, stop_reason=STOP_REFUSED,
            final_response=plan.get("response_draft"), completed=True,
        )
        return {"run": serialize_run(_load_run(conn, run_id)), "observations": [], "continue_model": False}

    previous_effect_tasks = _run_effect_tasks(conn, run_id)
    observations: list[dict[str, Any]] = []
    effect_created = False

    for index, call in enumerate(plan["tool_calls"]):
        current_run = _load_run(conn, run_id)
        used = int(current_run.get("tool_call_count") or 0)
        if used >= D13_EMERGENCY_TOOL_CALL_CAP:
            _set_run_state(
                conn, run_id=run_id, status=STATUS_BUDGET_REACHED,
                stop_reason=STOP_BUDGET, completed=True,
            )
            _record_event(
                conn, run_id=run_id, event_type="SAFETY_BUDGET_STOP", status="BLOCKED",
                request={"used": used, "hard_cap": D13_EMERGENCY_TOOL_CALL_CAP},
                response={"external_effect_executed": False},
            )
            raise D13RunStateError("D13 emergency tool-call safety cap reached")

        signature = _call_signature(call)
        if _existing_success_signature(conn, run_id, signature):
            _set_run_state(
                conn, run_id=run_id, status=STATUS_FAILED,
                stop_reason=STOP_NO_PROGRESS, completed=True,
            )
            _record_event(
                conn, run_id=run_id, event_type="NO_PROGRESS_LOOP_GUARD", status="BLOCKED",
                tool_name=call["tool_name"], task_id=call.get("task_id"), mode=call["mode"],
                request={"call_signature": signature}, response={"external_effect_executed": False},
            )
            raise D13RunStateError("same tool with same arguments repeated without state progress")

        if call["mode"] == d13.MODE_REQUEST_D12 and call.get("task_id") in previous_effect_tasks:
            _set_run_state(
                conn, run_id=run_id, status=STATUS_FAILED,
                stop_reason="ONE_EFFECT_PER_TASK_GUARD", completed=True,
            )
            raise D13RunStateError(
                f"Task {call.get('task_id')} already produced an effect request in this run"
            )

        request_record = {
            "tool_name": call["tool_name"],
            "task_id": call.get("task_id"),
            "payload": call.get("payload") or {},
            "evidence_refs": call.get("evidence_refs") or [],
            "call_signature": signature,
        }
        try:
            if call["mode"] == d13.MODE_REQUEST_D12:
                idem_seed = f"{run_id}|{signature}"
                idem = "D13RUN-" + hashlib.sha256(idem_seed.encode("utf-8")).hexdigest()[:24].upper()
                result = d13.request_controlled_action(
                    conn,
                    tool_name=call["tool_name"],
                    task_id=str(call.get("task_id") or ""),
                    payload=call.get("payload") or {},
                    identity=identity,
                    idempotency_key=idem,
                    reason="D13 Agent Runtime controlled request",
                    request_id=f"{run_id}-P{index+1}",
                )
                effect_created = True
                previous_effect_tasks.add(str(call.get("task_id")))
            else:
                result = d13.execute_non_effect_tool(
                    conn,
                    tool_name=call["tool_name"],
                    identity=identity,
                    task_id=call.get("task_id"),
                    payload=call.get("payload") or {},
                    response_draft=plan.get("response_draft"),
                )
            _record_event(
                conn, run_id=run_id, event_type="TOOL_CALL", status="SUCCESS",
                tool_name=call["tool_name"], task_id=call.get("task_id"), mode=call["mode"],
                request=request_record, response=result,
            )
            observations.append({
                "tool_name": call["tool_name"],
                "task_id": call.get("task_id"),
                "mode": call["mode"],
                "result": result,
            })
        except Exception as exc:
            _record_event(
                conn, run_id=run_id, event_type="TOOL_CALL", status="ERROR",
                tool_name=call["tool_name"], task_id=call.get("task_id"), mode=call["mode"],
                request=request_record,
                response={"error_type": type(exc).__name__, "message": str(exc), "external_effect_executed": False},
            )
            _set_run_state(conn, run_id=run_id, status=STATUS_FAILED, stop_reason="TOOL_ERROR", completed=True)
            raise

        task_ids = {
            str(x[0])
            for x in _conn_exec(
                conn,
                "SELECT DISTINCT task_id FROM d13_agent_trace_events WHERE run_id=? AND task_id IS NOT NULL AND status='SUCCESS'",
                (run_id,),
            ).fetchall()
            if x[0]
        }
        _conn_exec(
            conn,
            "UPDATE d13_agent_runs SET tool_call_count=tool_call_count+1,distinct_task_count=?,updated_at=? WHERE run_id=?",
            (len(task_ids), _now(), run_id),
        )

    if effect_created:
        _set_run_state(
            conn, run_id=run_id, status=STATUS_WAITING_HUMAN,
            stop_reason=STOP_WAITING_HUMAN,
            final_response=plan.get("response_draft"), completed=True,
        )
        return {"run": serialize_run(_load_run(conn, run_id)), "observations": observations, "continue_model": False}

    if decision == "DRAFT_ONLY" or (plan.get("response_draft") and any(c["mode"] == d13.MODE_SUGGEST_ONLY for c in plan["tool_calls"])):
        _set_run_state(
            conn, run_id=run_id, status=STATUS_COMPLETED,
            stop_reason=STOP_GOAL_SATISFIED,
            final_response=plan.get("response_draft"), completed=True,
        )
        return {"run": serialize_run(_load_run(conn, run_id)), "observations": observations, "continue_model": False}

    # Pure reads are not the end of the run. Feed observations back to the model.
    _set_run_state(conn, run_id=run_id, status=STATUS_RUNNING, stop_reason=STOP_READ_RESULTS_READY, completed=False)
    return {"run": serialize_run(_load_run(conn, run_id)), "observations": observations, "continue_model": True}


def complete_with_response(
    conn: Any,
    *,
    run_id: str,
    identity: CurrentIdentity,
    response: str,
) -> dict[str, Any]:
    run = _load_run(conn, run_id)
    _assert_run_access(run, identity)
    if run.get("status") != STATUS_RUNNING:
        raise D13RunStateError("only RUNNING read-only runs may be completed with a response")
    _record_event(
        conn, run_id=run_id, event_type="FINAL_RESPONSE", status="SUCCESS",
        request={}, response={"text": str(response or "").strip()},
    )
    _set_run_state(
        conn, run_id=run_id, status=STATUS_COMPLETED,
        stop_reason=STOP_GOAL_SATISFIED,
        final_response=str(response or "").strip() or None, completed=True,
    )
    return serialize_run(_load_run(conn, run_id))


def serialize_run(run: dict[str, Any]) -> dict[str, Any]:
    result = dict(run)
    result["context_refs"] = _safe_json(result.pop("context_refs_json", "[]"), [])
    result["external_effect_executed"] = bool(result.get("external_effect_executed"))
    result["hard_tool_call_cap"] = D13_EMERGENCY_TOOL_CALL_CAP
    result["trace_version"] = D13_TRACE_VERSION
    result["semantic_guard_version"] = d13.D13_SEMANTIC_GUARD_VERSION
    primary, fallback = model_provider.default_model_chain()
    result["model_routing"] = {
        "version": model_provider.D13_MODEL_ROUTING_VERSION,
        "primary": primary.model,
        "fallback": fallback.model,
        "retry_quick_transient": True,
        "retry_full_timeout": False,
        "json_format_retry_max": model_provider.DEFAULT_JSON_FORMAT_RETRIES,
        "cross_model_fallback": True,
        "preferred_route_follows_last_success": True,
        "preferred_route_cross_model_rescue": True,
        "fallback_on_semantic_error": False,
    }
    return result



def _run_started_request(conn: Any, run_id: str) -> dict[str, Any]:
    row = _conn_exec(
        conn,
        "SELECT request_json FROM d13_agent_trace_events WHERE run_id=? AND event_type='RUN_STARTED' ORDER BY sequence_no LIMIT 1",
        (run_id,),
    ).fetchone()
    return _safe_json(row[0], {}) if row else {}


def _trusted_model_context(conn: Any, *, run: dict[str, Any], identity: CurrentIdentity) -> dict[str, Any]:
    started = _run_started_request(conn, run["run_id"])
    context: dict[str, Any] = {
        "active_context": started.get("active_context") or {},
        "context_refs": _safe_json(run.get("context_refs_json"), []),
        "resolved_messages": [],
        # Minimal authoritative identifiers needed for effect binding. This does
        # not replace get_order_context: details/risk/waiting/recent messages are
        # still read through the READ_ONLY tool. The model must copy a task_id
        # from here or from a later get_order_context observation, never invent
        # one from order_id.
        "effect_task_candidates": [],
    }
    active = context["active_context"]
    active_order_id = str(active.get("order_id") or "").strip()
    if active_order_id:
        task_rows = _conn_exec(
            conn,
            "SELECT t.task_id,t.action_case_id,t.title,t.recommended_action,t.status "
            "FROM d9_action_case_tasks t JOIN action_cases a ON a.action_case_id=t.action_case_id "
            "WHERE t.organization_id=? AND a.organization_id=? AND a.order_id=? "
            "AND a.lifecycle_status!='CLOSED' AND t.status NOT IN ('DONE','CANCELLED') "
            "ORDER BY t.updated_at DESC LIMIT 10",
            (identity.organization_id, identity.organization_id, active_order_id),
        ).fetchall()
        context["effect_task_candidates"] = [_row_to_dict(r) for r in task_rows]
    refs = [str(x) for x in context["context_refs"] if str(x).strip()]
    trigger_ref = str(run.get("trigger_ref") or "").strip()
    if trigger_ref and trigger_ref not in refs:
        refs.append(trigger_ref)
    if refs:
        placeholders = ",".join("?" for _ in refs)
        rows = _conn_exec(
            conn,
            f"SELECT message_id,order_id,source_channel,sender_role,message_type,raw_content,source_time FROM source_messages "
            f"WHERE organization_id=? AND message_id IN ({placeholders}) ORDER BY source_time,created_at",
            tuple([identity.organization_id] + refs),
        ).fetchall()
        context["resolved_messages"] = [_row_to_dict(r) for r in rows]
    return context


def _successful_tool_observations(conn: Any, run_id: str) -> list[dict[str, Any]]:
    rows = _conn_exec(
        conn,
        "SELECT tool_name,task_id,mode,response_json FROM d13_agent_trace_events "
        "WHERE run_id=? AND event_type='TOOL_CALL' AND status='SUCCESS' ORDER BY sequence_no",
        (run_id,),
    ).fetchall()
    return [
        {
            "tool_name": row[0],
            "task_id": row[1],
            "mode": row[2],
            "result": _safe_json(row[3], {}),
        }
        for row in rows
    ]


def _record_model_attempts(conn: Any, *, run_id: str, attempts: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> None:
    for item in attempts:
        usage = dict(item.get("usage") or {})
        _record_event(
            conn,
            run_id=run_id,
            event_type="MODEL_ATTEMPT",
            status="SUCCESS" if item.get("success") else "ERROR",
            request={
                "route": item.get("route"),
                "provider": item.get("provider"),
                "model": item.get("model"),
                "attempt": item.get("attempt"),
            },
            response={
                "error_kind": item.get("error_kind"),
                "latency_ms": item.get("latency_ms"),
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "estimated_cost_cny": item.get("estimated_cost_cny"),
                "raw_model_content_stored": False,
            },
        )


def model_telemetry(conn: Any, *, run_id: str) -> dict[str, Any]:
    rows = _conn_exec(
        conn,
        "SELECT request_json,response_json,status FROM d13_agent_trace_events "
        "WHERE run_id=? AND event_type='MODEL_ATTEMPT' ORDER BY sequence_no",
        (run_id,),
    ).fetchall()
    attempts = []
    for row in rows:
        req = _safe_json(row[0], {})
        resp = _safe_json(row[1], {})
        attempts.append({**req, **resp, "success": row[2] == "SUCCESS"})
    successful_routes = [str(x.get("route") or "") for x in attempts if x.get("success")]
    model_switch_count = sum(
        1 for left, right in zip(successful_routes, successful_routes[1:]) if left != right
    )
    unknown_cost_attempts = [
        x for x in attempts
        if not x.get("success") and x.get("estimated_cost_cny") is None
    ]
    known_cost = round(
        sum(float(x.get("estimated_cost_cny") or 0.0) for x in attempts),
        8,
    )
    return {
        "attempt_count": len(attempts),
        "primary_attempt_count": sum(1 for x in attempts if x.get("route") == "PRIMARY"),
        "fallback_attempt_count": sum(1 for x in attempts if x.get("route") == "FALLBACK"),
        "successful_attempt_count": sum(1 for x in attempts if x.get("success")),
        "failed_attempt_count": sum(1 for x in attempts if not x.get("success")),
        "retry_attempt_count": sum(1 for x in attempts if int(x.get("attempt") or 1) > 1),
        "fallback_used": any(x.get("route") == "FALLBACK" for x in attempts),
        "model_switch_count": model_switch_count,
        "timeout_attempt_count": sum(1 for x in attempts if x.get("error_kind") == "PROVIDER_TIMEOUT"),
        "format_failure_attempt_count": sum(1 for x in attempts if x.get("error_kind") == "MODEL_FORMAT_FAILURE"),
        "latency_ms_total": sum(int(x.get("latency_ms") or 0) for x in attempts),
        "prompt_tokens_total": sum(int(x.get("prompt_tokens") or 0) for x in attempts),
        "completion_tokens_total": sum(int(x.get("completion_tokens") or 0) for x in attempts),
        # Backward-compatible field: this is a known-cost lower bound whenever
        # failed requests did not return usage metadata.
        "estimated_cost_cny_total": known_cost,
        "estimated_cost_cny_known_total": known_cost,
        "cost_estimate_complete": len(unknown_cost_attempts) == 0,
        "unmetered_failed_attempt_count": len(unknown_cost_attempts),
        "attempts": attempts,
    }


def run_with_selected_model(
    conn: Any,
    *,
    run_id: str,
    identity: CurrentIdentity,
    planner: Any | None = None,
) -> dict[str, Any]:
    """Drive a RUNNING D13 run through provider planning + controlled tools.

    The normal path is GLM-5.2 -> same-model retry -> Qwen3.8-Max fallback.
    Fallback is availability recovery only. Semantic/tool-policy plan failures
    fail closed and never trigger another model.
    """
    planner = planner or model_provider.plan_next_turn

    initial_run = _load_run(conn, run_id)
    _assert_run_access(initial_run, identity)
    pre_guard = d13.pre_model_policy_guard(str(initial_run.get("goal") or ""))
    if pre_guard:
        _record_event(
            conn, run_id=run_id, event_type="PRE_MODEL_POLICY_GUARD", status="BLOCKED",
            request={"guard_version": pre_guard.get("guard_version")},
            response={
                "code": pre_guard.get("code"),
                "external_effect_executed": False,
                "model_call_avoided": True,
            },
        )
        _set_run_state(
            conn, run_id=run_id, status=STATUS_REFUSED, stop_reason=STOP_REFUSED,
            final_response=str(pre_guard.get("response") or "").strip() or None, completed=True,
        )
        return {
            "run": serialize_run(_load_run(conn, run_id)),
            "observations": [],
            "continue_model": False,
            "model_telemetry": model_telemetry(conn, run_id=run_id),
            "policy_guard": pre_guard,
        }

    while True:
        run = _load_run(conn, run_id)
        _assert_run_access(run, identity)
        if run.get("status") != STATUS_RUNNING:
            return {
                "run": serialize_run(run),
                "observations": _successful_tool_observations(conn, run_id),
                "continue_model": False,
                "model_telemetry": model_telemetry(conn, run_id=run_id),
            }
        trusted_context = _trusted_model_context(conn, run=run, identity=identity)
        observations = _successful_tool_observations(conn, run_id)
        # Route preference follows the model that most recently produced a
        # valid plan, not "fallback was ever used".  If that preferred model has
        # a later availability/format failure, provider V3 may use the alternate
        # model once as rescue.  This avoids both repeated primary probing and a
        # single-point sticky fallback.
        _, fallback_endpoint = model_provider.default_model_chain()
        prefer_fallback = str(run.get("model_name") or "") == fallback_endpoint.model
        try:
            planned = planner(
                trigger_type=run["trigger_type"],
                current_datetime=run["system_current_datetime"],
                timezone=run["timezone"],
                authenticated_scope={
                    "organization_id": identity.organization_id,
                    "user_id": identity.user_id,
                    "role": identity.role,
                },
                trusted_context=trusted_context,
                goal=run["goal"],
                observations=observations,
                prefer_fallback=prefer_fallback,
            )
        except model_provider.D13ModelUnavailable as exc:
            _record_model_attempts(conn, run_id=run_id, attempts=list(exc.attempts))
            semantic_plan_failure = exc.error_kind == "MODEL_PLAN_INVALID"
            event_type = "MODEL_PLAN_REJECTED" if semantic_plan_failure else "MODEL_ROUTING_FAILED"
            stop_reason = "MODEL_PLAN_INVALID" if semantic_plan_failure else "MODEL_PROVIDER_FAILED"
            # Never persist or re-expose raw provider exception text. Provider
            # errors can contain API keys / Authorization values / request
            # fragments. The trace keeps only structured, allow-listed failure
            # metadata; detailed model-attempt telemetry is recorded separately.
            _record_event(
                conn, run_id=run_id, event_type=event_type, status="ERROR",
                request={"fallback_on_semantic_error": False},
                response={
                    "error_kind": exc.error_kind,
                    "external_effect_executed": False,
                    "provider_error_text_stored": False,
                },
            )
            _set_run_state(conn, run_id=run_id, status=STATUS_FAILED, stop_reason=stop_reason, completed=True)
            safe_message = (
                "Model plan rejected by D13 contract"
                if semantic_plan_failure
                else "Model provider unavailable"
            )
            raise D13ModelExecutionError(safe_message, error_kind=exc.error_kind) from None

        _record_model_attempts(conn, run_id=run_id, attempts=list(planned.attempts))
        _conn_exec(
            conn,
            "UPDATE d13_agent_runs SET model_provider=?,model_name=?,updated_at=? WHERE run_id=?",
            (planned.provider, planned.model, _now(), run_id),
        )
        if planned.route == "FALLBACK":
            _record_event(
                conn,
                run_id=run_id,
                event_type="MODEL_FALLBACK_PREFERRED" if prefer_fallback else "MODEL_FALLBACK_SELECTED",
                status="SUCCESS",
                request={"route": "FALLBACK", "preferred": prefer_fallback},
                response={"provider": planned.provider, "model": planned.model},
            )
        elif prefer_fallback and planned.route == "PRIMARY":
            _record_event(
                conn,
                run_id=run_id,
                event_type="MODEL_PRIMARY_RESCUE_SELECTED",
                status="SUCCESS",
                request={"route": "PRIMARY", "preferred_route": "FALLBACK"},
                response={"provider": planned.provider, "model": planned.model},
            )
        result = apply_model_plan(
            conn,
            run_id=run_id,
            identity=identity,
            raw_plan=planned.plan,
        )
        if not result.get("continue_model"):
            result["model_telemetry"] = model_telemetry(conn, run_id=run_id)
            return result


def get_run_trace(conn: Any, *, run_id: str, identity: CurrentIdentity) -> dict[str, Any]:
    run = _load_run(conn, run_id)
    _assert_run_access(run, identity)
    rows = _conn_exec(
        conn,
        "SELECT * FROM d13_agent_trace_events WHERE run_id=? ORDER BY sequence_no",
        (run_id,),
    ).fetchall()
    events = []
    for row in rows:
        item = _row_to_dict(row)
        item["request"] = _safe_json(item.pop("request_json"), {})
        item["response"] = _safe_json(item.pop("response_json"), {})
        events.append(item)
    return {
        "run": serialize_run(run),
        "events": events,
        "trace_contains_hidden_chain_of_thought": False,
        "trace_scope": "BUSINESS_EXECUTION_FACTS_ONLY",
    }
