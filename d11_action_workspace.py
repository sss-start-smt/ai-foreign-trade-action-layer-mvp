"""FlowOrder D11: canonical Action Workspace read model + thin D9 action routes.

D11 does not create a second workflow. It composes the already-frozen D8/D9/D10
objects into a user-facing read model and delegates state changes to the D9
service functions.

Canonical chain:
    Action Case -> Task -> Waiting/Recovery -> BusinessAction -> Outbox

Important boundary:
    BusinessAction.ACCEPTED / Outbox.PENDING never means an external ERP write
    succeeded. D11 has no outbox worker and no production ERP write path.
"""
from __future__ import annotations

import json
from typing import Any

from d8_action_case import get_my_case, list_my_cases
from d9_task_waiting import (
    D9NotFoundError,
    D9StateError,
    complete_task,
    create_task,
    get_active_waiting_for_task,
    get_task_by_id,
    get_waiting_by_id,
    list_tasks_for_case,
    list_waitings_for_case,
    put_task_on_waiting,
    record_waiting_reply,
    start_task,
)
from d10_business_action import get_business_action_for_task, get_outbox_for_action
import d15_durable_execution as d15
from database import table_exists

D11_POLICY_VERSION = "D11_ACTION_WORKSPACE_V1"


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        keys = row.keys() if hasattr(row, "keys") else []
        return {k: row[k] for k in keys}


def _safe_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _load_order(conn: Any, order_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    return _row_to_dict(row) if row else None


def _business_effect(task: dict[str, Any], conn: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    action = get_business_action_for_task(
        conn,
        organization_id=task["organization_id"],
        task_id=task["task_id"],
    )
    if not action:
        return None, None
    action = dict(action)
    action["payload"] = _safe_json(action.get("payload_json"), {})
    outbox = get_outbox_for_action(conn, action["business_action_id"])
    if outbox:
        outbox = dict(outbox)
        outbox["payload"] = _safe_json(outbox.get("payload_json"), {})
        # D15 is an additive execution overlay. Older/custom test schemas may not
        # have D15 tables, so the D11 frozen read model remains backward compatible.
        if table_exists(conn, "d15_outbox_execution_state") and table_exists(conn, "d15_execution_trace_events"):
            try:
                outbox["durable_execution"] = d15.get_execution_status(conn, outbox["event_id"], create=False)
            except Exception:
                outbox["durable_execution"] = None
    return action, outbox


def _task_view(conn: Any, task: dict[str, Any], waiting_by_task: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    task = dict(task)
    waitings = waiting_by_task.get(task["task_id"], [])
    active_waiting = next((w for w in waitings if w.get("status") == "ACTIVE"), None)
    action, outbox = _business_effect(task, conn)
    return {
        **task,
        "active_waiting": active_waiting,
        "waiting_history": waitings,
        "business_action": action,
        "outbox": outbox,
    }


def build_case_workspace(conn: Any, identity: Any, action_case_id: str) -> dict[str, Any] | None:
    """Build one permission-scoped D11 Action Case workspace.

    No legacy `tasks` table is read here. Every task comes from
    d9_action_case_tasks and every waiting comes from d9_action_case_waitings.
    """
    case = get_my_case(conn, identity, action_case_id)
    if not case:
        return None

    org_id = case["organization_id"]
    tasks = list_tasks_for_case(conn, organization_id=org_id, action_case_id=action_case_id)
    waitings = list_waitings_for_case(conn, organization_id=org_id, action_case_id=action_case_id)
    waiting_by_task: dict[str, list[dict[str, Any]]] = {}
    for waiting in waitings:
        item = dict(waiting)
        item["latest_replies"] = _safe_json(item.get("latest_reply_json"), [])
        waiting_by_task.setdefault(item["task_id"], []).append(item)

    task_views = [_task_view(conn, t, waiting_by_task) for t in tasks]
    raw_actionable = [t for t in task_views if t.get("status") in {"TODO", "IN_PROGRESS"}]
    raw_waiting = [t for t in task_views if t.get("status") == "WAITING"]
    history = [t for t in task_views if t.get("status") in {"DONE", "CANCELLED"}]

    lifecycle = case.get("lifecycle_status")
    # A CLOSED Case must never present executable work in D11, even if legacy or
    # inconsistent rows remain underneath. Preserve them as blocked evidence.
    blocked_open_tasks = []
    if lifecycle == "CLOSED":
        blocked_open_tasks = raw_actionable + raw_waiting
        actionable = []
        waiting_tasks = []
    else:
        actionable = raw_actionable
        waiting_tasks = raw_waiting

    # D11 deliberately does not invent priority when multiple same-level tasks exist.
    if len(actionable) == 1:
        primary = actionable[0]
    elif not actionable and len(waiting_tasks) == 1:
        primary = waiting_tasks[0]
    else:
        primary = None

    if lifecycle == "CLOSED":
        workspace_state = "CLOSED"
    elif actionable:
        workspace_state = "ACTIONABLE"
    elif waiting_tasks:
        workspace_state = "WAITING_ONLY"
    else:
        workspace_state = "NO_OPEN_TASK"

    order = _load_order(conn, case["order_id"]) or {"order_id": case["order_id"]}
    evidence = _safe_json(case.get("latest_evidence_json"), [])
    return {
        "policy_version": D11_POLICY_VERSION,
        "action_case": dict(case),
        "order": order,
        "risk_context": {
            "severity": case.get("latest_severity"),
            "action_bucket": case.get("latest_action_bucket"),
            "recommended_action": case.get("latest_recommended_action"),
            "evidence": evidence,
            "observation_status": case.get("observation_status"),
        },
        "workspace_state": workspace_state,
        "primary_task": primary,
        "actionable_tasks": actionable,
        "waiting_tasks": waiting_tasks,
        "history_tasks": history,
        "blocked_open_tasks": blocked_open_tasks,
        "task_count": len(task_views),
        "open_task_count": len(actionable) + len(waiting_tasks),
    }


def list_action_workspaces(
    conn: Any,
    identity: Any,
    *,
    include_closed: bool = False,
) -> dict[str, Any]:
    cases = list_my_cases(conn, identity, lifecycle_status=None if include_closed else "ACTIVE")
    items = []
    for case in cases:
        workspace = build_case_workspace(conn, identity, case["action_case_id"])
        if workspace:
            items.append(workspace)

    summary = {
        "total_cases": len(items),
        "actionable_cases": sum(x["workspace_state"] == "ACTIONABLE" for x in items),
        "waiting_only_cases": sum(x["workspace_state"] == "WAITING_ONLY" for x in items),
        "closed_cases": sum(x["workspace_state"] == "CLOSED" for x in items),
        "actionable_tasks": sum(len(x["actionable_tasks"]) for x in items),
        "active_waitings": sum(
            1
            for x in items
            for task in x["waiting_tasks"]
            if task.get("active_waiting") and task["active_waiting"].get("status") == "ACTIVE"
        ),
    }
    return {"policy_version": D11_POLICY_VERSION, "items": items, "summary": summary}


def _authorized_task(conn: Any, identity: Any, task_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    task = get_task_by_id(conn, task_id)
    if not task:
        raise D9NotFoundError(f"Task {task_id} not found")
    case = get_my_case(conn, identity, task["action_case_id"])
    if not case:
        # Deliberately indistinguishable from not-found to avoid cross-tenant leakage.
        raise D9NotFoundError(f"Task {task_id} not found")
    return task, case


def _authorized_waiting(conn: Any, identity: Any, waiting_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    waiting = get_waiting_by_id(conn, waiting_id)
    if not waiting:
        raise D9NotFoundError(f"Waiting {waiting_id} not found")
    case = get_my_case(conn, identity, waiting["action_case_id"])
    if not case:
        raise D9NotFoundError(f"Waiting {waiting_id} not found")
    return waiting, case


def create_case_task(conn: Any, identity: Any, *, action_case_id: str, title: str, recommended_action: str | None = None) -> dict[str, Any]:
    case = get_my_case(conn, identity, action_case_id)
    if not case:
        raise D9NotFoundError(f"Action Case {action_case_id} not found")
    return create_task(
        conn,
        organization_id=case["organization_id"],
        action_case_id=action_case_id,
        title=title,
        recommended_action=recommended_action,
        actor=identity.user_id,
    )


def start_case_task(conn: Any, identity: Any, task_id: str) -> dict[str, Any]:
    _authorized_task(conn, identity, task_id)
    return start_task(conn, task_id, actor=identity.user_id)


def complete_case_task(conn: Any, identity: Any, task_id: str) -> dict[str, Any]:
    _authorized_task(conn, identity, task_id)
    return complete_task(conn, task_id, actor=identity.user_id)


def wait_case_task(
    conn: Any,
    identity: Any,
    *,
    task_id: str,
    waiting_type: str,
    due_at: str,
    reason: str | None = None,
) -> dict[str, Any]:
    _authorized_task(conn, identity, task_id)
    return put_task_on_waiting(
        conn,
        task_id=task_id,
        waiting_type=waiting_type,
        due_at=due_at,
        reason=reason,
        actor=identity.user_id,
    )


def record_case_waiting_reply(
    conn: Any,
    identity: Any,
    *,
    waiting_id: str,
    reply_id: str | None = None,
    reply_payload: Any = None,
    satisfies_completion: bool = False,
) -> dict[str, Any]:
    _authorized_waiting(conn, identity, waiting_id)
    return record_waiting_reply(
        conn,
        waiting_id=waiting_id,
        reply_id=reply_id,
        reply_payload=reply_payload,
        satisfies_completion=satisfies_completion,
        actor=identity.user_id,
    )


__all__ = [
    "D11_POLICY_VERSION",
    "build_case_workspace",
    "list_action_workspaces",
    "create_case_task",
    "start_case_task",
    "complete_case_task",
    "wait_case_task",
    "record_case_waiting_reply",
]
