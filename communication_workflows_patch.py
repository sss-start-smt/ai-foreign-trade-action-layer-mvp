from __future__ import annotations

import hmac
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from database import db, table_exists, get_table_column_names, get_table_columns
from auth import CurrentIdentity, get_current_identity, require_manager, require_order_access

PATCH_VERSION = "2.0.0-ft05-ft06-site-uiux"
DEFAULT_API_BASE = "https://api.coze.cn"
DEFAULT_TIMEOUT_SECONDS = 90
DEFAULT_ORGANIZATION_ID = "ORG-DEFAULT"
DEFAULT_OPERATOR_ID = "USER-1"

ORDER_FIELD_ALIASES: dict[str, list[str]] = {
    "order_id": ["order_id", "id"],
    "order_no": ["order_no", "po_no", "po_number"],
    "customer_name": ["customer_name", "customer"],
    "sku": ["sku", "product_sku", "item_no"],
    "product_name": ["product_name", "product"],
    "quantity": ["quantity", "qty"],
    "unit": ["unit", "uom"],
    "customer_delivery_date": ["customer_delivery_date", "requested_delivery_date", "delivery_date"],
    "current_node": ["current_node", "current_stage", "stage"],
    "factory_name": ["factory_name", "factory", "supplier_name"],
    "supplier_completion_commitment_date": [
        "supplier_completion_commitment_date",
        "latest_supplier_commitment",
        "supplier_commitment_date",
        "factory_commitment_date",
    ],
    "current_progress": ["current_progress", "progress"],
    "owner": ["owner", "assignee", "owner_user_id"],
    "packaging_method": ["packaging_method", "packaging"],
    "specification": ["specification", "spec"],
    "material": ["material"],
    "color": ["color"],
    "logo_process": ["logo_process", "process"],
    "risk_level": ["risk_level"],
    "status": ["status"],
    "updated_at": ["updated_at"],
    "organization_id": ["organization_id"],
}

TASK_FIELD_ALIASES: dict[str, list[str]] = {
    "task_id": ["task_id", "id"],
    "order_id": ["order_id"],
    "order_no": ["order_no", "po_no"],
    "task_type": ["task_type", "action_type", "type"],
    "task_title": ["task_title", "title", "action_title"],
    "task_description": ["task_description", "description"],
    "action_state": ["action_state", "status"],
    "owner": ["owner", "owner_user_id", "assignee"],
    "waiting_on": ["waiting_on"],
    "next_action_at": ["next_action_at", "due_at", "business_deadline"],
    "promised_reply_at": ["promised_reply_at"],
    "risk_level": ["risk_level", "priority"],
    "source_quote": ["source_quote", "evidence"],
    "updated_at": ["updated_at"],
    "organization_id": ["organization_id"],
}

MESSAGE_FIELD_ALIASES: dict[str, list[str]] = {
    "message_id": ["message_id", "id"],
    "order_id": ["related_order_id", "order_id"],
    "order_no": ["order_no"],
    "channel": ["channel", "source_channel"],
    "sender_role": ["sender_role", "sender_type"],
    "raw_content": ["raw_content", "content", "message_content"],
    "source_time": ["source_time", "received_at", "created_at"],
}


class FT05RunRequest(BaseModel):
    communication_text: str
    sender_role: str
    channel: str = "email"
    received_at: str | None = None
    timezone: str = "Asia/Shanghai"
    order_id: str | None = None
    order_no: str | None = None
    order_context: dict[str, Any] | list[dict[str, Any]] | None = None
    existing_open_tasks: list[dict[str, Any]] | None = None
    organization_id: str | None = None
    request_id: str | None = None
    source_message_id: str | None = None


class FT06RunRequest(BaseModel):
    draft_type: str
    recipient_role: str
    channel: str = "email"
    language: str = "zh-CN"
    tone: str = "professional"
    order_id: str | None = None
    order_no: str | None = None
    fact_catalog: list[dict[str, Any]] | None = None
    order_context: dict[str, Any] | None = None
    task_context: dict[str, Any] | list[dict[str, Any]] | None = None
    communication_history: list[dict[str, Any]] | None = None
    user_instruction: str = ""
    organization_id: str | None = None
    request_id: str | None = None


class CandidateCommitRequest(BaseModel):
    operator_id: str = DEFAULT_OPERATOR_ID
    edited_candidate: dict[str, Any] | None = None
    confirmation_version: str = "1"
    note: str = ""


class CandidateRejectRequest(BaseModel):
    operator_id: str = DEFAULT_OPERATOR_ID
    note: str = ""


class DraftReviewRequest(BaseModel):
    action: str
    operator_id: str = DEFAULT_OPERATOR_ID
    edited_subject: str | None = None
    edited_draft: str | None = None
    note: str = ""
    task_id: str | None = None
    waiting_on: str | None = None
    promised_reply_at: str | None = None
    next_action_at: str | None = None
    risk_override_confirmed: bool = False


class RankingRefreshRequest(BaseModel):
    current_user_id: str = DEFAULT_OPERATOR_ID
    timezone: str = "Asia/Shanghai"
    current_time: str | None = None



def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8].upper()}"


def _resolve_column(existing: Iterable[str], aliases: Mapping[str, list[str]], canonical: str) -> str | None:
    names = set(existing)
    for candidate in aliases.get(canonical, [canonical]):
        if candidate in names:
            return candidate
    return None


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_loads(value: Any, fallback: Any = None) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None or value == "":
        return fallback
    current = value
    for _ in range(5):
        if not isinstance(current, str):
            return current
        text = current.strip()
        if not text:
            return fallback
        try:
            current = json.loads(text)
        except json.JSONDecodeError:
            return current
    return current


def _ensure_patch_schema(conn: Any) -> None:
    if getattr(conn, "is_pg", False):
        required = ("communication_task_candidates", "communication_drafts", "communication_workflow_runs", "communication_events")
        missing = [name for name in required if not table_exists(conn, name)]
        if missing:
            raise RuntimeError(f"PostgreSQL communication schema missing {missing}; run `alembic upgrade head`.")
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS communication_task_candidates (
            candidate_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            source_message_id TEXT,
            order_id TEXT,
            order_no TEXT,
            communication_text TEXT NOT NULL,
            sender_role TEXT,
            channel TEXT,
            result_json TEXT NOT NULL,
            task_candidate_json TEXT NOT NULL,
            run_status TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'PENDING',
            reviewer_id TEXT,
            review_note TEXT,
            ft03_result_json TEXT,
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_comm_candidates_order ON communication_task_candidates(order_id, order_no);
        CREATE INDEX IF NOT EXISTS idx_comm_candidates_status ON communication_task_candidates(review_status, run_status);

        CREATE TABLE IF NOT EXISTS communication_drafts (
            draft_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL UNIQUE,
            order_id TEXT,
            order_no TEXT,
            draft_type TEXT NOT NULL,
            recipient_role TEXT NOT NULL,
            channel TEXT,
            result_json TEXT NOT NULL,
            ai_subject TEXT,
            ai_draft TEXT,
            edited_subject TEXT,
            edited_draft TEXT,
            final_text TEXT,
            facts_used_json TEXT NOT NULL DEFAULT '[]',
            missing_facts_json TEXT NOT NULL DEFAULT '[]',
            questions_to_ask_json TEXT NOT NULL DEFAULT '[]',
            risk_flags_json TEXT NOT NULL DEFAULT '[]',
            run_status TEXT NOT NULL,
            approval_status TEXT,
            human_status TEXT NOT NULL DEFAULT 'PENDING',
            reviewer_id TEXT,
            review_note TEXT,
            approved_at TEXT,
            copied_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_comm_drafts_order ON communication_drafts(order_id, order_no);
        CREATE INDEX IF NOT EXISTS idx_comm_drafts_status ON communication_drafts(human_status, run_status);

        CREATE TABLE IF NOT EXISTS communication_workflow_runs (
            run_id TEXT PRIMARY KEY,
            workflow_code TEXT NOT NULL,
            workflow_id TEXT,
            request_id TEXT,
            status TEXT NOT NULL,
            input_json TEXT NOT NULL,
            output_json TEXT,
            error_code TEXT,
            error_message TEXT,
            debug_url TEXT,
            duration_ms INTEGER,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_comm_runs_workflow ON communication_workflow_runs(workflow_code, created_at);

        CREATE TABLE IF NOT EXISTS communication_events (
            event_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            operator_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_comm_events_entity ON communication_events(entity_type, entity_id, created_at);
        """
    )
    conn.commit()


def _safe_row(row: Any | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _canonicalize_row(row: Mapping[str, Any], aliases: Mapping[str, list[str]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    keys = set(row.keys())
    for canonical in aliases:
        column = _resolve_column(keys, aliases, canonical)
        if column:
            output[canonical] = row.get(column)
    return output


def _find_order(conn: Any, order_id: str | None, order_no: str | None) -> dict[str, Any] | None:
    if not table_exists(conn, "orders"):
        return None
    columns = get_table_column_names(conn, "orders")
    id_col = _resolve_column(columns, ORDER_FIELD_ALIASES, "order_id")
    no_col = _resolve_column(columns, ORDER_FIELD_ALIASES, "order_no")
    row = None
    if order_id and id_col:
        row = conn.execute(f'SELECT * FROM orders WHERE "{id_col}"=? LIMIT 1', (order_id,)).fetchone()
    if row is None and order_no and no_col:
        row = conn.execute(f'SELECT * FROM orders WHERE "{no_col}"=? LIMIT 1', (order_no,)).fetchone()
    return _canonicalize_row(dict(row), ORDER_FIELD_ALIASES) if row else None


def _list_orders(conn: Any, limit: int = 200) -> list[dict[str, Any]]:
    if not table_exists(conn, "orders"):
        return []
    columns = get_table_column_names(conn, "orders")
    updated = _resolve_column(columns, ORDER_FIELD_ALIASES, "updated_at")
    no_col = _resolve_column(columns, ORDER_FIELD_ALIASES, "order_no")
    order_by = f'"{updated}" DESC' if updated else (f'"{no_col}" ASC' if no_col else "rowid DESC")
    rows = conn.execute(f"SELECT * FROM orders ORDER BY {order_by} LIMIT ?", (limit,)).fetchall()
    return [_canonicalize_row(dict(row), ORDER_FIELD_ALIASES) for row in rows]


def _list_tasks(conn: Any, order: Mapping[str, Any] | None = None, open_only: bool = False) -> list[dict[str, Any]]:
    if not table_exists(conn, "tasks"):
        return []
    columns = get_table_column_names(conn, "tasks")
    where: list[str] = []
    params: list[Any] = []
    if order:
        order_id_col = _resolve_column(columns, TASK_FIELD_ALIASES, "order_id")
        order_no_col = _resolve_column(columns, TASK_FIELD_ALIASES, "order_no")
        clauses: list[str] = []
        if order.get("order_id") and order_id_col:
            clauses.append(f'"{order_id_col}"=?')
            params.append(order["order_id"])
        if order.get("order_no") and order_no_col:
            clauses.append(f'"{order_no_col}"=?')
            params.append(order["order_no"])
        if clauses:
            where.append("(" + " OR ".join(clauses) + ")")
    if open_only:
        state_col = _resolve_column(columns, TASK_FIELD_ALIASES, "action_state")
        if state_col:
            where.append(
                f'COALESCE("{state_col}",\'\') NOT IN (\'DONE\',\'CLOSED\',\'CANCELLED\',\'REJECTED\')'
            )
    sql = "SELECT * FROM tasks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rowid DESC LIMIT 200"
    rows = conn.execute(sql, params).fetchall()
    return [_canonicalize_row(dict(row), TASK_FIELD_ALIASES) for row in rows]


def _list_messages(conn: Any, order: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not table_exists(conn, "source_messages"):
        return []
    columns = get_table_column_names(conn, "source_messages")
    where: list[str] = []
    params: list[Any] = []
    if order:
        id_col = _resolve_column(columns, MESSAGE_FIELD_ALIASES, "order_id")
        no_col = _resolve_column(columns, MESSAGE_FIELD_ALIASES, "order_no")
        clauses: list[str] = []
        if order.get("order_id") and id_col:
            clauses.append(f'"{id_col}"=?')
            params.append(order["order_id"])
        if order.get("order_no") and no_col:
            clauses.append(f'"{no_col}"=?')
            params.append(order["order_no"])
        if clauses:
            where.append("(" + " OR ".join(clauses) + ")")
    time_col = _resolve_column(columns, MESSAGE_FIELD_ALIASES, "source_time")
    sql = "SELECT * FROM source_messages"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f' ORDER BY "{time_col}" DESC' if time_col else " ORDER BY rowid DESC"
    sql += " LIMIT 30"
    rows = conn.execute(sql, params).fetchall()
    canonical = [_canonicalize_row(dict(row), MESSAGE_FIELD_ALIASES) for row in rows]
    canonical.reverse()
    return canonical


def _fact_catalog_from_order(order: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not order:
        return []
    labels = {
        "order_no": "订单号",
        "customer_name": "客户名称",
        "sku": "SKU/货号",
        "product_name": "产品名称",
        "quantity": "订单数量",
        "unit": "单位",
        "customer_delivery_date": "客户正式交期",
        "current_node": "当前节点",
        "factory_name": "工厂名称",
        "supplier_completion_commitment_date": "供应商生产完成承诺日期",
        "current_progress": "当前生产进度",
        "owner": "负责人",
        "packaging_method": "包装方式",
        "specification": "规格",
        "material": "材质",
        "color": "颜色",
        "logo_process": "Logo工艺",
        "risk_level": "风险等级",
        "status": "订单状态",
    }
    result: list[dict[str, Any]] = []
    for index, (field, label) in enumerate(labels.items(), start=1):
        value = order.get(field)
        if value is None or value == "":
            continue
        result.append(
            {
                "fact_id": f"DB-{index:03d}-{field}",
                "fact_type": field,
                "label": label,
                "value": value,
                "confirmed": True,
                "source": "ORDER_DATABASE",
            }
        )
    return result


def _task_context_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "open_tasks": tasks,
        "unanswered_items": [
            item
            for task in tasks
            for item in (
                _json_loads(task.get("unanswered_items"), [])
                if task.get("unanswered_items") is not None
                else []
            )
            if isinstance(item, str)
        ],
    }


def _configuration() -> dict[str, Any]:
    return {
        "api_base": os.environ.get("COZE_API_BASE", DEFAULT_API_BASE).rstrip("/"),
        "token": os.environ.get("COZE_API_TOKEN", "").strip(),
        "ft03_id": os.environ.get("COZE_FT03_WORKFLOW_ID", "").strip(),
        "ft04_id": os.environ.get("COZE_FT04_WORKFLOW_ID", "").strip(),
        "ft05_id": os.environ.get("COZE_FT05_WORKFLOW_ID", "").strip(),
        "ft06_id": os.environ.get("COZE_FT06_WORKFLOW_ID", "").strip(),
        "bot_id": os.environ.get("COZE_BOT_ID", "").strip(),
        "app_id": os.environ.get("COZE_APP_ID", "").strip(),
        "timeout": max(5, int(os.environ.get("COZE_WORKFLOW_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS))),
        "parameters_mode": os.environ.get("COZE_PARAMETERS_MODE", "string").strip().lower(),
        "organization_id": os.environ.get("COMMUNICATION_ORGANIZATION_ID", DEFAULT_ORGANIZATION_ID).strip()
        or DEFAULT_ORGANIZATION_ID,
        "admin_key": os.environ.get("COMMUNICATION_ADMIN_KEY", "").strip(),
    }



def _require_admin_key(x_communication_key: str | None, x_api_key: str | None = None) -> None:
    configured = _configuration()["admin_key"]
    if not configured:
        return
    supplied = (x_communication_key or x_api_key or "").strip()
    if not supplied or not hmac.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="沟通助手操作密钥错误或缺失")

def _normalize_workflow_result(value: Any) -> dict[str, Any]:
    current = _json_loads(value, value)
    for _ in range(6):
        if isinstance(current, dict):
            if "result" in current:
                nested = _json_loads(current["result"], current["result"])
                if isinstance(nested, dict):
                    current = nested
                    continue
            if "result_json" in current:
                nested = _json_loads(current["result_json"], current["result_json"])
                if isinstance(nested, dict):
                    current = nested
                    continue
            if "data" in current and len(current) <= 8:
                nested = _json_loads(current["data"], current["data"])
                if isinstance(nested, dict):
                    current = nested
                    continue
            return current
        if isinstance(current, str):
            parsed = _json_loads(current, current)
            if parsed == current:
                return {"raw_result": current}
            current = parsed
            continue
        return {"raw_result": current}
    return current if isinstance(current, dict) else {"raw_result": current}


def _workflow_http_request(workflow_id: str, parameters: dict[str, Any], mode: str) -> dict[str, Any]:
    config = _configuration()
    if not config["token"]:
        raise RuntimeError("COZE_API_TOKEN未配置")
    if not workflow_id:
        raise RuntimeError("工作流ID未配置")
    if config["bot_id"] and config["app_id"]:
        raise RuntimeError("COZE_BOT_ID与COZE_APP_ID不能同时配置")

    body: dict[str, Any] = {"workflow_id": workflow_id}
    body["parameters"] = parameters if mode == "object" else _json_dumps(parameters)
    if config["bot_id"]:
        body["bot_id"] = config["bot_id"]
    if config["app_id"]:
        body["app_id"] = config["app_id"]

    request = urllib.request.Request(
        config["api_base"] + "/v1/workflow/run",
        data=_json_dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {config['token']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=config["timeout"]) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status_code = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Coze HTTP {exc.code}: {raw[:800]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Coze网络调用失败: {exc.reason}") from exc

    parsed = _json_loads(raw, {})
    if not isinstance(parsed, dict):
        raise RuntimeError(f"Coze返回不是JSON对象: {raw[:500]}")
    if status_code >= 400:
        raise RuntimeError(f"Coze HTTP {status_code}: {parsed}")
    code = parsed.get("code", 0)
    if code not in (0, "0", None):
        raise RuntimeError(f"Coze业务错误 {code}: {parsed.get('msg') or parsed.get('message') or parsed}")
    return {
        "result": _normalize_workflow_result(parsed.get("data", parsed)),
        "debug_url": parsed.get("debug_url"),
        "execute_id": parsed.get("execute_id"),
        "raw_response": parsed,
        "parameters_mode": mode,
    }


def _run_coze_workflow(workflow_code: str, workflow_id: str, parameters: dict[str, Any], request_id: str) -> dict[str, Any]:
    config = _configuration()
    modes = [config["parameters_mode"]]
    if config["parameters_mode"] == "auto":
        modes = ["string", "object"]
    elif config["parameters_mode"] not in ("string", "object"):
        modes = ["string"]

    started = time.perf_counter()
    last_error: Exception | None = None
    attempted: list[str] = []
    for mode in modes:
        attempted.append(mode)
        try:
            output = _workflow_http_request(workflow_id, parameters, mode)
            duration_ms = int((time.perf_counter() - started) * 1000)
            with db() as conn:
                _ensure_patch_schema(conn)
                conn.execute(
                    "INSERT INTO communication_workflow_runs(run_id,workflow_code,workflow_id,request_id,status,input_json,output_json,debug_url,duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        _new_id("CWR"),
                        workflow_code,
                        workflow_id,
                        request_id,
                        "SUCCESS",
                        _json_dumps({"parameters": parameters, "attempted_modes": attempted}),
                        _json_dumps(output),
                        output.get("debug_url"),
                        duration_ms,
                        _now_iso(),
                    ),
                )
                conn.commit()
            return output
        except Exception as exc:
            last_error = exc
            if mode != modes[-1]:
                continue
    duration_ms = int((time.perf_counter() - started) * 1000)
    with db() as conn:
        _ensure_patch_schema(conn)
        conn.execute(
            "INSERT INTO communication_workflow_runs(run_id,workflow_code,workflow_id,request_id,status,input_json,error_code,error_message,duration_ms,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                _new_id("CWR"),
                workflow_code,
                workflow_id,
                request_id,
                "FAILED",
                _json_dumps({"parameters": parameters, "attempted_modes": attempted}),
                "COZE_CALL_FAILED",
                str(last_error),
                duration_ms,
                _now_iso(),
            ),
        )
        conn.commit()
    raise RuntimeError(str(last_error) if last_error else "Coze调用失败")


def _normalize_ft06_task_context(value: Any) -> list[dict[str, Any]]:
    """Normalize FT06 task context without assuming dict/list shape.

    D11 V0.4 sends one current task as a dict, while older communication code can
    supply {"open_tasks": [...]} or a plain list.  Treat all three forms as the
    same bounded list before building provider parameters.
    """
    if isinstance(value, dict):
        open_tasks = value.get("open_tasks")
        items = open_tasks if isinstance(open_tasks, list) else [value]
    elif isinstance(value, list):
        items = value
    else:
        items = []
    allowed = (
        "task_id", "title", "status", "task_type", "action_target", "target",
        "recommended_action", "evidence", "due_at", "waiting_for", "waiting_on",
        "promised_reply_at", "risk_level",
    )
    return [
        {key: task.get(key) for key in allowed if task.get(key) not in (None, "")}
        for task in items[:10]
        if isinstance(task, dict)
    ]


def _d11_uat_ft06_fixture_enabled() -> bool:
    explicit = os.environ.get("D11_UAT_COMMUNICATION_PROVIDER", "").strip().lower()
    intake = os.environ.get("D11_UAT_INTAKE_PROVIDER", "").strip().lower()
    return explicit == "fixture" or (not explicit and intake == "fixture")


def _fact_value(facts: list[dict[str, Any]], *names: str) -> Any:
    for fact in facts:
        if not isinstance(fact, dict):
            continue
        fact_type = str(fact.get("fact_type") or fact.get("type") or fact.get("name") or "")
        if fact_type in names:
            return fact.get("value")
    return None


def _uat_ft06_fixture_result(
    payload: FT06RunRequest,
    order: dict[str, Any],
    fact_catalog: list[dict[str, Any]],
    task_context: list[dict[str, Any]],
) -> dict[str, Any]:
    """Deterministic D11 UAT-only draft generator.

    This validates the product interaction (facts -> draft -> human review ->
    waiting) without claiming model quality.  It never sends anything outside
    FlowOrder and never mutates order facts.
    """
    order_no = str(order.get("order_no") or payload.order_no or "当前订单")
    customer = str(order.get("customer_name") or "客户")
    due = (
        order.get("requested_delivery_date")
        or order.get("customer_delivery_date")
        or _fact_value(fact_catalog, "customer_delivery_date")
    )
    commitment = (
        order.get("latest_supplier_commitment")
        or order.get("supplier_completion_commitment_date")
        or _fact_value(fact_catalog, "supplier_completion_commitment_date")
    )
    task = task_context[0] if task_context else {}
    task_title = str(task.get("title") or task.get("recommended_action") or "")
    instruction = payload.user_instruction.strip()

    facts_used = [f"订单 {order_no}", f"客户：{customer}"]
    if due:
        facts_used.append(f"客户交期：{due}")
    if commitment:
        facts_used.append(f"供应商承诺：{commitment}")
    if task_title:
        facts_used.append(f"当前任务：{task_title}")

    if payload.recipient_role == "supplier" or payload.draft_type == "SUPPLIER_PROGRESS_FOLLOWUP":
        subject = f"请确认订单 {order_no} 当前进度"
        due_text = f"客户交期为 {due}。" if due else ""
        body = (
            f"您好，关于订单 {order_no}，想请您协助确认当前准确进度。{due_text}"
            "请同步关键物料到货情况、当前补救方案，以及可以明确承诺的完成时间。"
            "如现计划存在风险，也请直接说明影响和预计恢复时间，便于我们及时协调。"
        )
        questions = ["当前准确进度是什么？", "关键物料预计何时到货？", "可以明确承诺的完成时间是什么？"]
    elif payload.recipient_role == "internal" or payload.draft_type == "CHANGE_HISTORY_SUMMARY":
        subject = f"订单 {order_no} 当前跟进摘要"
        body = f"订单 {order_no} 当前跟进摘要：" + "；".join(facts_used) + "。"
        questions = []
    else:
        subject = f"关于订单 {order_no} 的进度更新"
        if commitment:
            progress_text = f"目前供应商记录的完成承诺为 {commitment}。"
        else:
            progress_text = "目前供应商的明确完成时间仍在核实中。"
        due_text = f"我们记录的客户交期为 {due}。" if due else ""
        body = (
            f"您好，关于订单 {order_no}，向您同步当前进展。{due_text}{progress_text}"
            "我们正在继续确认生产与出货安排，确认后会及时更新；在正式确认前不会做未经核实的交期承诺。"
        )
        questions = ["是否还有需要我们同步确认的事项？"]

    if instruction:
        body += f"\n\n本次沟通重点：{instruction}"

    return {
        "run_status": "draft_ready",
        "approval_status": "NEEDS_CONFIRMATION",
        "draft_result": {
            "subject": subject,
            "draft": body,
            "facts_used": facts_used,
            "missing_facts_required_for_generation": [],
            "questions_to_ask": questions,
            "blocking_risk_flags": [],
        },
        "_uat_fixture": True,
        "_integration": {
            "workflow_key": "D11_UAT_FT06_FIXTURE",
            "provider": "fixture",
            "evidence_level": "UAT_ONLY_NOT_MODEL_QUALITY",
        },
    }


def _extract_ft06_fields(result: dict[str, Any]) -> dict[str, Any]:
    draft_result = result.get("draft_result") or {}
    if not isinstance(draft_result, dict):
        draft_result = {}
    subject = draft_result.get("subject") or draft_result.get("email_subject") or ""
    draft = draft_result.get("draft") or draft_result.get("body") or draft_result.get("content") or ""
    facts_used = draft_result.get("facts_used") or []
    missing = draft_result.get("missing_facts_required_for_generation")
    if missing is None:
        missing = draft_result.get("missing_facts") or []
    questions = draft_result.get("questions_to_ask") or []
    risk_flags = draft_result.get("blocking_risk_flags") or draft_result.get("risk_flags") or []
    return {
        "draft_result": draft_result,
        "subject": str(subject or ""),
        "draft": str(draft or ""),
        "facts_used": facts_used if isinstance(facts_used, list) else [],
        "missing_facts": missing if isinstance(missing, list) else [],
        "questions_to_ask": questions if isinstance(questions, list) else [],
        "risk_flags": risk_flags if isinstance(risk_flags, list) else [],
    }


def _append_event(conn: Any, entity_type: str, entity_id: str, event_type: str, payload: Any, operator_id: str | None) -> None:
    _ensure_patch_schema(conn)
    conn.execute(
        "INSERT INTO communication_events(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
        (_new_id("CEV"), entity_type, entity_id, event_type, _json_dumps(payload), operator_id, _now_iso()),
    )


def _candidate_confirmed_payload(candidate: dict[str, Any], order: dict[str, Any], operator_id: str) -> dict[str, Any]:
    priority = str(candidate.get("priority_hint") or "normal")
    action_state = "DO_NOW" if priority == "high" else "DO_TODAY"
    if candidate.get("due_at_candidate") is None and candidate.get("due_expression"):
        action_state = "NEEDS_CONFIRMATION"
    return {
        "order_match": {
            "status": "unique_match",
            "selected_order_id": order.get("order_id"),
            "selected_order_no": order.get("order_no"),
        },
        "order_changes": [],
        "task_changes": [
            {
                "action": "create",
                "task_type": candidate.get("task_type"),
                "task_title": candidate.get("task_title"),
                "task_description": candidate.get("task_description"),
                "due_at": candidate.get("due_at_candidate"),
                "due_expression": candidate.get("due_expression"),
                "responsible_role": candidate.get("responsible_role") or "order_owner",
                "priority_hint": priority,
                "source_quote": candidate.get("source_quote"),
                "confirmation_status": "confirmed",
                "confirmed_by": operator_id,
            }
        ],
        "risk_changes": [],
        "action_decision": {
            "final_action_state": action_state,
            "next_action_at": candidate.get("due_at_candidate"),
            "confirmation_status": "confirmed",
        },
    }


def _existing_idempotency_keys(conn: Any) -> list[str]:
    if not table_exists(conn, "idempotency_records"):
        return []
    columns = get_table_column_names(conn, "idempotency_records")
    key_col = "idempotency_key" if "idempotency_key" in columns else None
    if not key_col:
        return []
    return [str(row[0]) for row in conn.execute(f'SELECT "{key_col}" FROM idempotency_records ORDER BY rowid DESC LIMIT 500')]


def _parse_persistence_status(result: dict[str, Any]) -> str:
    candidates = [
        result.get("persistence_status"),
        result.get("status"),
        (result.get("adapter_result") or {}).get("status") if isinstance(result.get("adapter_result"), dict) else None,
    ]
    for candidate in candidates:
        value = str(candidate or "").strip().lower()
        if value:
            return value
    return ""


def _update_task_waiting_state(conn: Any, task_id: str, waiting_on: str | None, promised_reply_at: str | None, next_action_at: str | None) -> dict[str, Any]:
    if not table_exists(conn, "tasks"):
        return {"updated": False, "reason": "tasks_table_missing"}
    columns = get_table_column_names(conn, "tasks")
    id_col = _resolve_column(columns, TASK_FIELD_ALIASES, "task_id")
    if not id_col:
        return {"updated": False, "reason": "task_id_column_missing"}
    values: dict[str, Any] = {}
    state_col = _resolve_column(columns, TASK_FIELD_ALIASES, "action_state")
    if state_col:
        values[state_col] = "WAITING_EXTERNAL"
    waiting_col = _resolve_column(columns, TASK_FIELD_ALIASES, "waiting_on")
    if waiting_col and waiting_on:
        values[waiting_col] = waiting_on
    promised_col = _resolve_column(columns, TASK_FIELD_ALIASES, "promised_reply_at")
    if promised_col and promised_reply_at:
        values[promised_col] = promised_reply_at
    next_col = _resolve_column(columns, TASK_FIELD_ALIASES, "next_action_at")
    if next_col and next_action_at:
        values[next_col] = next_action_at
    updated_col = _resolve_column(columns, TASK_FIELD_ALIASES, "updated_at")
    if updated_col:
        values[updated_col] = _now_iso()
    if not values:
        return {"updated": False, "reason": "no_compatible_columns"}
    assignments = ",".join(f'"{column}"=?' for column in values)
    cursor = conn.execute(
        f'UPDATE tasks SET {assignments} WHERE "{id_col}"=?',
        [*values.values(), task_id],
    )
    return {"updated": cursor.rowcount > 0, "fields": values}


COMMUNICATION_HTML = r'''<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>沟通与行动助手</title>
  <link rel="stylesheet" href="/api/communication/assets/style.css">
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar" id="sidebar">
      <a class="brand" href="/" aria-label="返回AI外贸跟单行动助手首页">
        <span class="brand-mark" aria-hidden="true">A</span>
        <span><strong>Action Layer</strong><small>外贸跟单行动助手</small></span>
      </a>

      <div class="nav-label">工作空间</div>
      <nav class="side-nav" aria-label="沟通助手导航">
        <button class="nav-item active" data-view="overview" type="button">
          <span class="nav-icon">⌂</span><span>沟通工作台</span>
        </button>
        <button class="nav-item" data-view="task" type="button">
          <span class="nav-icon">↳</span><span>沟通转任务</span><span class="nav-code">FT05</span>
        </button>
        <button class="nav-item" data-view="draft" type="button">
          <span class="nav-icon">✦</span><span>受控草稿</span><span class="nav-code">FT06</span>
        </button>
        <button class="nav-item" data-view="history" type="button">
          <span class="nav-icon">◷</span><span>确认与历史</span>
        </button>
      </nav>

      <div class="guardrail-card">
        <span class="guardrail-dot"></span>
        <strong>人工确认始终开启</strong>
        <p>系统只生成任务候选和沟通草稿，不自动向客户或工厂发送。</p>
      </div>

      <a class="back-link" href="/">← 返回主工作台</a>
    </aside>

    <div class="app-frame">
      <header class="topbar">
        <button id="mobile-menu" class="mobile-menu" type="button" aria-label="打开导航">☰</button>
        <div class="page-heading">
          <span id="page-eyebrow">AI行动层</span>
          <h1 id="page-title">沟通工作台</h1>
          <p id="page-subtitle">把外部沟通转成下一步行动，并用可信订单事实生成可审核草稿。</p>
        </div>
        <div class="top-actions">
          <span id="system-status" class="system-status"><i></i><span>正在检查连接</span></span>
          <button id="key-open" class="ghost-button" type="button">操作密钥</button>
          <span class="version">UI/UX V2.0</span>
        </div>
      </header>

      <main class="workspace">
        <section id="config-alert" class="system-alert hidden"></section>

        <section id="view-overview" class="view active">
          <div class="hero-card">
            <div class="hero-copy">
              <span class="eyebrow">Communication Operations</span>
              <h2>从一条消息，到一个清晰的下一步</h2>
              <p>关联订单、识别行动、生成受控草稿，再由跟单员确认。ERP保留订单事实，本系统负责行动闭环。</p>
              <div class="hero-actions">
                <button class="primary" data-jump="task" type="button">把沟通转成任务</button>
                <button class="soft-button" data-jump="draft" type="button">生成沟通草稿</button>
              </div>
            </div>
            <div class="hero-flow" aria-label="工作流概览">
              <div class="flow-node active"><span>01</span><strong>识别消息</strong><small>订单、意图、时间</small></div>
              <div class="flow-line"></div>
              <div class="flow-node"><span>02</span><strong>生成候选</strong><small>任务或沟通草稿</small></div>
              <div class="flow-line"></div>
              <div class="flow-node"><span>03</span><strong>人工确认</strong><small>写回、复制、记录</small></div>
            </div>
          </div>

          <div class="metric-grid">
            <article class="metric-card mint"><span class="metric-label">可用订单</span><strong id="metric-orders">—</strong><small>可关联到沟通上下文</small></article>
            <article class="metric-card yellow"><span class="metric-label">待人工处理</span><strong id="metric-pending">—</strong><small>候选与草稿确认队列</small></article>
            <article class="metric-card blue"><span class="metric-label">最近生成</span><strong id="metric-drafts">—</strong><small>近30条沟通记录</small></article>
            <article class="metric-card coral"><span class="metric-label">工作流健康度</span><strong id="metric-health">—</strong><small>FT05 / FT06 / Token</small></article>
          </div>

          <div class="overview-grid">
            <article class="surface process-card">
              <div class="surface-head">
                <div><span class="section-kicker">Action Flow</span><h3>行动闭环</h3></div>
                <span class="tag neutral">人工确认门控</span>
              </div>
              <div class="process-list">
                <div class="process-item"><span class="process-index">1</span><div><strong>订单事实进入</strong><p>从ERP、Excel或订单中心获取结构化事实。</p></div><span class="process-state good">已具备</span></div>
                <div class="process-item"><span class="process-index">2</span><div><strong>外部沟通理解</strong><p>FT05识别任务，FT06生成草稿和事实引用。</p></div><span id="process-ai-state" class="process-state">检查中</span></div>
                <div class="process-item"><span class="process-index">3</span><div><strong>人工审核与执行</strong><p>确认任务后走FT03；草稿复制后记录触达。</p></div><span class="process-state good">强制</span></div>
                <div class="process-item"><span class="process-index">4</span><div><strong>等待与重新排序</strong><p>更新等待对象、回复时间，并可触发FT04重排。</p></div><span class="process-state good">已接入</span></div>
              </div>
            </article>

            <article class="surface quick-card">
              <div class="surface-head"><div><span class="section-kicker">Quick Start</span><h3>常用动作</h3></div></div>
              <button class="quick-action" data-jump="task" type="button"><span class="quick-icon dark">↳</span><span><strong>客户消息转任务</strong><small>识别截止时间、负责人和订单</small></span><b>→</b></button>
              <button class="quick-action" data-preset="SUPPLIER_PROGRESS_FOLLOWUP" type="button"><span class="quick-icon lime">⌁</span><span><strong>催工厂进度</strong><small>询问完成比例、到料和补救方案</small></span><b>→</b></button>
              <button class="quick-action" data-preset="DELIVERY_STATUS_REPLY" type="button"><span class="quick-icon sky">◴</span><span><strong>回复客户交期</strong><small>区分客户交期与工厂完工承诺</small></span><b>→</b></button>
            </article>

            <article class="surface recent-card">
              <div class="surface-head">
                <div><span class="section-kicker">Recent Activity</span><h3>最近处理</h3></div>
                <button class="text-button" data-jump="history" type="button">查看全部</button>
              </div>
              <div id="recent-overview" class="recent-list"><div class="empty compact">正在加载最近记录…</div></div>
            </article>

            <article class="surface readiness-card">
              <div class="surface-head"><div><span class="section-kicker">Readiness</span><h3>接入状态</h3></div></div>
              <div id="readiness-list" class="readiness-list"><div class="empty compact">正在检查配置…</div></div>
            </article>
          </div>
        </section>

        <section id="view-task" class="view hidden">
          <div class="workflow-heading">
            <div><span class="section-kicker">FT05 · Communication to Task</span><h2>沟通转任务</h2><p>先识别，再确认。任何任务写回都必须经过人工。</p></div>
            <div class="workflow-steps" id="ft05-steps">
              <span class="current"><b>1</b>输入沟通</span><i></i><span><b>2</b>AI分析</span><i></i><span><b>3</b>人工确认</span>
            </div>
          </div>

          <div class="work-grid">
            <article class="surface form-surface">
              <div class="surface-head"><div><span class="step-label">步骤 1</span><h3>建立沟通上下文</h3></div><span class="tag neutral">不会自动写回</span></div>
              <label class="field"><span>关联订单 <em>必选</em></span><select id="ft05-order"></select></label>
              <div id="ft05-order-context" class="order-context empty compact">选择订单后展示客户、交期、节点和工厂信息。</div>
              <div class="field-grid">
                <label class="field"><span>发送方</span><select id="ft05-sender"><option value="customer">客户</option><option value="supplier">工厂/供应商</option><option value="forwarder">货代</option><option value="internal">内部同事</option><option value="finance">财务</option></select></label>
                <label class="field"><span>沟通渠道</span><select id="ft05-channel"><option value="email">邮箱</option><option value="wechat">工作微信</option><option value="whatsapp">WhatsApp</option><option value="other">其他</option></select></label>
              </div>
              <label class="field"><span>沟通原文 <em>必填</em></span><textarea id="ft05-text" rows="10" maxlength="8000" placeholder="粘贴客户、工厂或内部沟通原文。系统会保留原文证据，不会凭空补充业务事实。"></textarea><small class="field-meta"><button id="ft05-sample" class="inline-link" type="button">填入示例</button><span><b id="ft05-count">0</b>/8000</span></small></label>
              <div class="guard-note"><span>✓</span><p><strong>写回边界</strong> FT05只生成任务候选；确认后才调用FT03，重复任务会被抑制。</p></div>
              <button id="ft05-run" class="primary full" type="button"><span>运行FT05分析</span><small>识别任务、证据、截止时间和重复项</small></button>
            </article>

            <article class="surface result-surface sticky-card">
              <div class="surface-head"><div><span class="step-label">步骤 2—3</span><h3>候选审核</h3></div><span id="ft05-live" class="live-pill idle"><i></i>等待输入</span></div>
              <div id="ft05-empty" class="empty-state">
                <div class="empty-visual">↳</div><h4>等待生成任务候选</h4><p>运行后会展示任务类型、截止时间、负责人、原文证据和去重结果。</p>
              </div>
              <div id="ft05-result" class="hidden"></div>
            </article>
          </div>
        </section>

        <section id="view-draft" class="view hidden">
          <div class="workflow-heading">
            <div><span class="section-kicker">FT06 · Controlled Draft</span><h2>受控沟通草稿</h2><p>只使用已确认订单事实生成草稿，发送前必须人工审核。</p></div>
            <div class="workflow-steps" id="ft06-steps">
              <span class="current"><b>1</b>选择场景</span><i></i><span><b>2</b>生成草稿</span><i></i><span><b>3</b>人工确认</span>
            </div>
          </div>

          <div class="work-grid">
            <article class="surface form-surface">
              <div class="surface-head"><div><span class="step-label">步骤 1</span><h3>选择订单与沟通场景</h3></div><span class="tag good">事实约束生成</span></div>
              <label class="field"><span>关联订单 <em>必选</em></span><select id="ft06-order"></select></label>
              <div id="ft06-order-context" class="order-context empty compact">选择订单后展示客户交期、工厂承诺和当前节点。</div>

              <div class="scenario-grid" aria-label="草稿场景快捷选择">
                <button class="scenario active" data-draft-type="CUSTOMER_REPLY" type="button"><span>客户回复</span><small>一般订单沟通</small></button>
                <button class="scenario" data-draft-type="CUSTOMER_CONFIRMATION_REMINDER" type="button"><span>催客户确认</span><small>规格、包装、交期</small></button>
                <button class="scenario" data-draft-type="SUPPLIER_PROGRESS_FOLLOWUP" type="button"><span>催工厂进度</span><small>进度、到料、补救</small></button>
                <button class="scenario" data-draft-type="DELIVERY_STATUS_REPLY" type="button"><span>交期回复</span><small>基于可信事实</small></button>
                <button class="scenario" data-draft-type="CHANGE_HISTORY_SUMMARY" type="button"><span>变更汇总</span><small>客户历史变化</small></button>
              </div>
              <label class="field hidden"><span>草稿类型</span><select id="ft06-type"><option value="CUSTOMER_REPLY">客户一般回复</option><option value="CUSTOMER_CONFIRMATION_REMINDER">催客户确认邮件</option><option value="SUPPLIER_PROGRESS_FOLLOWUP">催工厂进度消息</option><option value="DELIVERY_STATUS_REPLY">客户交期状态回复</option><option value="CHANGE_HISTORY_SUMMARY">客户历史变更汇总</option></select></label>
              <div class="field-grid">
                <label class="field"><span>接收对象</span><select id="ft06-recipient"><option value="customer">客户</option><option value="supplier">工厂/供应商</option><option value="internal">内部同事</option></select></label>
                <label class="field"><span>渠道</span><select id="ft06-channel"><option value="email">邮箱</option><option value="wechat">工作微信</option><option value="whatsapp">WhatsApp</option></select></label>
              </div>
              <label class="field"><span>表达语气</span><select id="ft06-tone"><option value="professional">专业</option><option value="concise">简洁</option><option value="polite">礼貌</option><option value="firm">明确</option></select></label>
              <label class="field"><span>本次生成要求</span><textarea id="ft06-instruction" rows="6" maxlength="4000" placeholder="说明本次要询问、确认或回复的重点。不要在这里要求系统作出未经确认的交期、费用或赔偿承诺。"></textarea><small class="field-meta"><span>系统会自动加载订单、任务和历史沟通</span><span><b id="ft06-count">0</b>/4000</span></small></label>
              <div class="guard-note"><span>✓</span><p><strong>发送边界</strong> 所有草稿均返回 <code>send_allowed=false</code>，必须人工修改、确认或复制。</p></div>
              <button id="ft06-run" class="primary full" type="button"><span>运行FT06生成</span><small>生成草稿、引用事实并检查高风险承诺</small></button>
            </article>

            <article class="surface result-surface sticky-card">
              <div class="surface-head"><div><span class="step-label">步骤 2—3</span><h3>草稿审核</h3></div><span id="ft06-live" class="live-pill idle"><i></i>等待输入</span></div>
              <div id="ft06-empty" class="empty-state">
                <div class="empty-visual">✦</div><h4>等待生成受控草稿</h4><p>系统会展示正文、事实依据、待询问项和风险阻断原因，不会自动发送。</p>
              </div>
              <div id="ft06-result" class="hidden"></div>
            </article>
          </div>
        </section>

        <section id="view-history" class="view hidden">
          <div class="workflow-heading compact-heading">
            <div><span class="section-kicker">Audit Trail</span><h2>确认与历史</h2><p>保留AI生成、人工修改、写回结果和触达记录。</p></div>
            <button id="history-refresh" class="soft-button" type="button">刷新记录</button>
          </div>
          <article class="surface history-surface">
            <div class="history-toolbar">
              <div class="filter-chips" role="group" aria-label="历史记录筛选">
                <button class="active" data-history-filter="all" type="button">全部</button>
                <button data-history-filter="task" type="button">任务候选</button>
                <button data-history-filter="draft" type="button">沟通草稿</button>
                <button data-history-filter="pending" type="button">待确认</button>
                <button data-history-filter="done" type="button">已处理</button>
              </div>
              <label class="search-box"><span>⌕</span><input id="history-search" type="search" placeholder="搜索订单号、标题或正文"></label>
            </div>
            <div id="history" class="history-timeline"><div class="empty compact">正在加载记录…</div></div>
          </article>
        </section>
      </main>
    </div>
  </div>

  <dialog id="key-dialog" class="key-dialog">
    <form method="dialog">
      <div class="dialog-icon">⌁</div>
      <h2>设置沟通助手操作密钥</h2>
      <p>密钥只保存在当前浏览器会话中，不会写入页面源码或调用日志。</p>
      <label class="field"><span>COMMUNICATION_ADMIN_KEY</span><input id="key-input" type="password" autocomplete="off" placeholder="输入Render环境变量中的操作密钥"></label>
      <div class="dialog-actions"><button id="key-cancel" class="ghost-button" value="cancel" type="button">取消</button><button id="key-save" class="primary" value="default" type="button">保存到本次会话</button></div>
    </form>
  </dialog>

  <div id="toast" role="status" aria-live="polite"></div>
  <script src="/api/communication/assets/app.js"></script>
</body>
</html>
'''

COMMUNICATION_CSS = r''':root{
  --canvas:#f4f6f2;
  --surface:#ffffff;
  --surface-soft:#f8faf7;
  --ink:#131719;
  --muted:#64706b;
  --muted-2:#8b958f;
  --forest:#01472f;
  --forest-2:#0b5b43;
  --forest-3:#dcebe4;
  --line:#dfe5dc;
  --line-strong:#cbd5cc;
  --lime:#dff45b;
  --yellow:#f3d563;
  --mint:#d8f1e2;
  --sky:#d9ebf7;
  --coral:#f5c5bc;
  --danger:#b53838;
  --danger-soft:#fff0ee;
  --warning:#8a5d00;
  --warning-soft:#fff6d9;
  --success:#166743;
  --success-soft:#e8f5ee;
  --shadow:0 18px 50px rgba(18,42,32,.08);
  --shadow-soft:0 8px 26px rgba(18,42,32,.06);
  --radius-xl:28px;
  --radius-lg:22px;
  --radius-md:16px;
  --radius-sm:12px;
  font-family:Inter,"PingFang SC","Microsoft YaHei",system-ui,-apple-system,sans-serif;
  color:var(--ink);
  background:var(--canvas);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
body{margin:0;background:var(--canvas);color:var(--ink);min-width:320px}
button,input,select,textarea{font:inherit}
button{cursor:pointer}
button:disabled{cursor:not-allowed;opacity:.58}
a{color:inherit}
.hidden{display:none!important}
.app-shell{display:grid;grid-template-columns:264px minmax(0,1fr);min-height:100vh}
.sidebar{position:sticky;top:0;height:100vh;padding:24px 18px 20px;background:linear-gradient(180deg,#053f2d 0%,#092923 100%);color:#fff;display:flex;flex-direction:column;z-index:30}
.brand{display:flex;align-items:center;gap:12px;text-decoration:none;padding:0 8px 24px;border-bottom:1px solid rgba(255,255,255,.11)}
.brand-mark{width:42px;height:42px;border-radius:13px;background:linear-gradient(145deg,var(--lime),#7ed7a8);color:var(--forest);display:grid;place-items:center;font-weight:900;font-size:20px;box-shadow:inset 0 0 0 1px rgba(255,255,255,.38)}
.brand strong,.brand small{display:block}.brand strong{font-size:15px;letter-spacing:.01em}.brand small{font-size:11px;color:rgba(255,255,255,.62);margin-top:3px}
.nav-label{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:rgba(255,255,255,.42);padding:24px 12px 9px}
.side-nav{display:grid;gap:6px}
.nav-item{width:100%;border:0;background:transparent;color:rgba(255,255,255,.72);display:grid;grid-template-columns:28px 1fr auto;align-items:center;gap:8px;padding:12px 12px;border-radius:13px;text-align:left;font-weight:650;transition:.18s ease}
.nav-item:hover{background:rgba(255,255,255,.08);color:#fff}
.nav-item.active{background:#fff;color:var(--forest);box-shadow:0 10px 24px rgba(0,0,0,.14)}
.nav-icon{font-size:16px;text-align:center}.nav-code{font-size:10px;letter-spacing:.04em;padding:3px 6px;border-radius:999px;background:rgba(255,255,255,.1)}
.nav-item.active .nav-code{background:var(--forest-3)}
.guardrail-card{margin-top:auto;border:1px solid rgba(255,255,255,.12);background:rgba(255,255,255,.06);border-radius:18px;padding:16px}
.guardrail-card strong{font-size:13px}.guardrail-card p{margin:7px 0 0;color:rgba(255,255,255,.6);font-size:11px;line-height:1.65}.guardrail-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--lime);margin-right:7px;box-shadow:0 0 0 5px rgba(223,244,91,.12)}
.back-link{display:block;margin-top:14px;padding:10px 12px;text-decoration:none;color:rgba(255,255,255,.62);font-size:12px}.back-link:hover{color:#fff}
.app-frame{min-width:0}
.topbar{height:104px;padding:18px 32px;display:flex;align-items:center;gap:20px;border-bottom:1px solid var(--line);background:rgba(244,246,242,.9);backdrop-filter:blur(18px);position:sticky;top:0;z-index:20}
.mobile-menu{display:none;border:0;background:var(--surface);width:42px;height:42px;border-radius:12px}
.page-heading{min-width:0}.page-heading>span{font-size:10px;text-transform:uppercase;letter-spacing:.16em;color:var(--forest-2);font-weight:800}.page-heading h1{font-size:23px;margin:4px 0 3px;letter-spacing:-.025em}.page-heading p{margin:0;color:var(--muted);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:680px}
.top-actions{margin-left:auto;display:flex;align-items:center;gap:10px}
.system-status{display:inline-flex;align-items:center;gap:8px;padding:9px 12px;border:1px solid var(--line);border-radius:999px;background:rgba(255,255,255,.72);font-size:11px;font-weight:700;color:var(--muted)}
.system-status i{width:8px;height:8px;border-radius:50%;background:#aab4ae}.system-status.good i{background:#24a36a;box-shadow:0 0 0 5px rgba(36,163,106,.12)}.system-status.warn i{background:#d8a414;box-shadow:0 0 0 5px rgba(216,164,20,.12)}
.version{font-size:10px;font-weight:800;color:var(--forest);background:var(--lime);padding:9px 11px;border-radius:999px}
.workspace{max-width:1520px;margin:0 auto;padding:28px 32px 72px}
.view{animation:viewIn .22s ease}.view.active{display:block}@keyframes viewIn{from{opacity:.4;transform:translateY(5px)}to{opacity:1;transform:none}}
.system-alert{padding:14px 16px;margin-bottom:20px;border:1px solid #e8cf73;background:var(--warning-soft);border-radius:16px;color:#705000;font-size:13px;line-height:1.55}
.hero-card{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(360px,.75fr);gap:28px;align-items:center;min-height:310px;padding:42px;border-radius:var(--radius-xl);background:linear-gradient(135deg,#063f2f 0%,#0b5b43 58%,#5da881 100%);color:#fff;box-shadow:var(--shadow);overflow:hidden;position:relative}
.hero-card:after{content:"";position:absolute;width:360px;height:360px;border-radius:50%;background:radial-gradient(circle,rgba(223,244,91,.28),transparent 68%);right:-120px;top:-130px}
.hero-copy,.hero-flow{position:relative;z-index:1}.eyebrow,.section-kicker{display:block;font-size:10px;letter-spacing:.16em;text-transform:uppercase;font-weight:850}.eyebrow{color:var(--lime)}
.hero-copy h2{font-size:42px;line-height:1.05;letter-spacing:-.045em;max-width:680px;margin:14px 0 18px}.hero-copy p{font-size:14px;line-height:1.8;color:rgba(255,255,255,.72);max-width:640px}.hero-actions{display:flex;gap:10px;margin-top:24px}
.hero-flow{padding:20px;border:1px solid rgba(255,255,255,.18);background:rgba(255,255,255,.08);border-radius:22px;backdrop-filter:blur(12px)}
.flow-node{display:grid;grid-template-columns:38px 1fr;column-gap:12px;align-items:center}.flow-node span{grid-row:1/3;width:38px;height:38px;border-radius:12px;display:grid;place-items:center;background:rgba(255,255,255,.13);font-size:11px;font-weight:900}.flow-node.active span{background:var(--lime);color:var(--forest)}.flow-node strong{font-size:13px}.flow-node small{color:rgba(255,255,255,.55);font-size:11px;margin-top:3px}.flow-line{width:1px;height:24px;background:rgba(255,255,255,.18);margin:5px 0 5px 19px}
.primary,.soft-button,.ghost-button,.text-button{border:0;border-radius:12px;font-weight:800;transition:.18s ease}.primary{background:var(--lime);color:var(--forest);padding:13px 18px;box-shadow:0 8px 20px rgba(0,0,0,.1)}.primary:hover{transform:translateY(-1px);filter:brightness(.98)}.soft-button{background:var(--surface);color:var(--forest);padding:12px 16px;border:1px solid var(--line)}.ghost-button{padding:10px 13px;background:transparent;border:1px solid var(--line);color:var(--ink)}.text-button{background:transparent;color:var(--forest-2);padding:6px}
.primary.full{width:100%;display:flex;flex-direction:column;align-items:flex-start;padding:15px 18px}.primary.full span{font-size:14px}.primary.full small{font-size:10px;opacity:.68;margin-top:3px;font-weight:650}
.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;margin:18px 0}.metric-card{min-height:142px;padding:20px;border-radius:22px;border:1px solid rgba(19,23,25,.06);display:flex;flex-direction:column;justify-content:space-between;box-shadow:var(--shadow-soft)}.metric-card.mint{background:var(--mint)}.metric-card.yellow{background:var(--yellow)}.metric-card.blue{background:var(--sky)}.metric-card.coral{background:var(--coral)}.metric-label{font-size:11px;font-weight:800;color:rgba(19,23,25,.65)}.metric-card strong{font-size:38px;letter-spacing:-.04em}.metric-card small{font-size:10px;color:rgba(19,23,25,.58)}
.overview-grid{display:grid;grid-template-columns:1.18fr .82fr;gap:16px}.surface{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);box-shadow:var(--shadow-soft)}.process-card,.quick-card,.recent-card,.readiness-card{padding:22px}.surface-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:18px}.surface-head h3{font-size:17px;margin:5px 0 0;letter-spacing:-.02em}.section-kicker{color:var(--forest-2)}.tag{display:inline-flex;align-items:center;padding:7px 9px;border-radius:999px;font-size:10px;font-weight:800;white-space:nowrap}.tag.neutral{background:#eef2ed;color:var(--muted)}.tag.good{background:var(--success-soft);color:var(--success)}
.process-list{display:grid;gap:4px}.process-item{display:grid;grid-template-columns:34px 1fr auto;gap:12px;align-items:center;padding:14px 0;border-bottom:1px solid var(--line)}.process-item:last-child{border-bottom:0}.process-index{width:30px;height:30px;border-radius:10px;display:grid;place-items:center;background:var(--surface-soft);color:var(--forest);font-size:11px;font-weight:900}.process-item strong{font-size:13px}.process-item p{margin:4px 0 0;color:var(--muted);font-size:11px}.process-state{font-size:10px;font-weight:800;padding:6px 8px;border-radius:999px;background:#eef1ee;color:var(--muted)}.process-state.good{background:var(--success-soft);color:var(--success)}.process-state.warn{background:var(--warning-soft);color:var(--warning)}
.quick-card{display:grid;gap:10px;align-content:start}.quick-action{display:grid;grid-template-columns:42px 1fr auto;align-items:center;gap:12px;border:1px solid var(--line);background:var(--surface-soft);border-radius:16px;padding:13px;text-align:left;transition:.18s ease}.quick-action:hover{border-color:var(--line-strong);transform:translateY(-1px);background:#fff}.quick-action strong,.quick-action small{display:block}.quick-action strong{font-size:12px}.quick-action small{font-size:10px;color:var(--muted);margin-top:4px}.quick-action b{font-size:16px}.quick-icon{width:40px;height:40px;border-radius:12px;display:grid;place-items:center;font-size:17px}.quick-icon.dark{background:var(--forest);color:#fff}.quick-icon.lime{background:var(--lime);color:var(--forest)}.quick-icon.sky{background:var(--sky);color:#31566c}
.recent-list,.readiness-list{display:grid;gap:9px}.recent-row,.readiness-row{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;padding:11px 0;border-bottom:1px solid var(--line)}.recent-row:last-child,.readiness-row:last-child{border-bottom:0}.recent-dot{width:10px;height:10px;border-radius:50%;background:var(--forest-2);box-shadow:0 0 0 5px var(--forest-3)}.recent-row strong,.readiness-row strong{display:block;font-size:12px}.recent-row small,.readiness-row small{display:block;color:var(--muted);font-size:10px;margin-top:4px}.recent-row time{font-size:9px;color:var(--muted-2)}.readiness-state{font-size:10px;font-weight:850}.readiness-state.ok{color:var(--success)}.readiness-state.no{color:var(--danger)}
.workflow-heading{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;margin-bottom:18px}.workflow-heading h2{font-size:30px;margin:7px 0 5px;letter-spacing:-.035em}.workflow-heading p{margin:0;color:var(--muted);font-size:12px}.compact-heading{align-items:center}
.workflow-steps{display:flex;align-items:center;gap:8px;padding:8px;border-radius:16px;background:var(--surface);border:1px solid var(--line)}.workflow-steps span{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:10px;font-weight:800;white-space:nowrap}.workflow-steps b{width:24px;height:24px;border-radius:8px;display:grid;place-items:center;background:#edf1ed}.workflow-steps span.current{color:var(--forest)}.workflow-steps span.current b{background:var(--lime)}.workflow-steps span.done b{background:var(--forest);color:#fff}.workflow-steps i{width:24px;height:1px;background:var(--line)}
.work-grid{display:grid;grid-template-columns:minmax(420px,.82fr) minmax(520px,1.18fr);gap:16px;align-items:start}.form-surface,.result-surface{padding:24px}.sticky-card{position:sticky;top:126px;max-height:calc(100vh - 150px);overflow:auto}
.step-label{display:inline-flex;font-size:9px;letter-spacing:.12em;text-transform:uppercase;color:var(--forest-2);font-weight:900}.field{display:flex;flex-direction:column;gap:8px;margin:0 0 15px}.field>span{font-size:11px;font-weight:800;color:#44504b}.field em{font-style:normal;color:var(--danger);font-size:9px}.field-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}
select,input,textarea{width:100%;border:1px solid var(--line-strong);border-radius:13px;padding:12px 13px;background:#fff;color:var(--ink);outline:none;transition:.16s ease}select:focus,input:focus,textarea:focus{border-color:var(--forest-2);box-shadow:0 0 0 4px rgba(11,91,67,.09)}textarea{resize:vertical;line-height:1.65}.field-meta{display:flex;justify-content:space-between;color:var(--muted-2);font-size:9px}.inline-link{border:0;background:transparent;padding:0;color:var(--forest-2);font-weight:850}
.order-context{margin:-3px 0 15px;padding:13px;border:1px solid var(--line);border-radius:15px;background:var(--surface-soft)}.order-context:not(.empty){display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.context-item span,.context-item strong{display:block}.context-item span{font-size:9px;color:var(--muted);margin-bottom:4px}.context-item strong{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.guard-note{display:grid;grid-template-columns:28px 1fr;gap:10px;padding:13px;margin:4px 0 15px;border-radius:15px;background:var(--forest-3);color:var(--forest)}.guard-note>span{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;background:#fff;font-weight:900}.guard-note p{font-size:10px;line-height:1.6;margin:0}.guard-note code{font-size:9px;background:rgba(255,255,255,.6);padding:2px 4px;border-radius:4px}
.scenario-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:0 0 15px}.scenario{border:1px solid var(--line);background:var(--surface-soft);border-radius:14px;padding:11px;text-align:left;transition:.16s}.scenario:hover{border-color:var(--line-strong)}.scenario.active{border-color:var(--forest);background:var(--forest-3);box-shadow:inset 0 0 0 1px var(--forest)}.scenario span,.scenario small{display:block}.scenario span{font-size:11px;font-weight:850}.scenario small{font-size:9px;color:var(--muted);margin-top:4px}
.live-pill{display:inline-flex;align-items:center;gap:7px;padding:7px 9px;border-radius:999px;font-size:9px;font-weight:850;background:#eef1ee;color:var(--muted)}.live-pill i{width:7px;height:7px;border-radius:50%;background:#aab3ad}.live-pill.running i{background:#d8a414;animation:pulse 1s infinite}.live-pill.good i{background:#24a36a}.live-pill.bad i{background:#c44949}@keyframes pulse{50%{opacity:.35}}
.empty-state{min-height:380px;border:1px dashed var(--line-strong);border-radius:20px;background:linear-gradient(180deg,#fbfcfa,#f5f8f4);display:flex;align-items:center;justify-content:center;flex-direction:column;text-align:center;padding:40px}.empty-visual{width:68px;height:68px;border-radius:22px;background:var(--forest-3);color:var(--forest);display:grid;place-items:center;font-size:28px;margin-bottom:16px}.empty-state h4{font-size:15px;margin:0}.empty-state p{font-size:11px;line-height:1.65;color:var(--muted);max-width:320px}.empty{border:1px dashed var(--line-strong);border-radius:14px;padding:28px;text-align:center;color:var(--muted);font-size:11px}.empty.compact{padding:16px}
.decision-banner{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;border-radius:17px;padding:15px;margin-bottom:16px;background:var(--success-soft);border:1px solid #cce7d8}.decision-banner.warn{background:var(--warning-soft);border-color:#eddda4}.decision-banner.bad{background:var(--danger-soft);border-color:#efcfca}.decision-banner strong{display:block;font-size:13px}.decision-banner p{margin:4px 0 0;color:var(--muted);font-size:10px}.status-row{display:flex;gap:7px;flex-wrap:wrap}.badge{display:inline-flex;align-items:center;padding:6px 8px;border-radius:999px;font-size:9px;font-weight:850;background:#eef1ee;color:var(--muted)}.badge.good{background:var(--success-soft);color:var(--success)}.badge.warn{background:var(--warning-soft);color:var(--warning)}.badge.bad{background:var(--danger-soft);color:var(--danger)}
.summary-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin-bottom:16px}.summary-item{padding:12px;border-radius:14px;background:var(--surface-soft);border:1px solid var(--line)}.summary-item span,.summary-item strong{display:block}.summary-item span{font-size:9px;color:var(--muted)}.summary-item strong{font-size:12px;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.review-section{padding:15px 0;border-top:1px solid var(--line)}.review-section:first-of-type{border-top:0}.review-section h4{font-size:11px;margin:0 0 10px}.review-section p{font-size:11px;line-height:1.65;color:var(--muted);margin:0}.quote{border-left:3px solid var(--forest-2);background:var(--surface-soft);border-radius:0 13px 13px 0;padding:13px;font-size:11px;line-height:1.65}.list{margin:0;padding-left:18px;color:var(--muted);font-size:11px;line-height:1.7}.facts{display:flex;gap:6px;flex-wrap:wrap}.fact{background:var(--forest-3);color:var(--forest);border-radius:999px;padding:6px 8px;font-size:9px;font-weight:750}.risk-panel{padding:14px;border-radius:15px;background:var(--danger-soft);border:1px solid #efcfca;color:#792d2d}.risk-panel p{color:#792d2d}.check{display:flex;flex-direction:row;align-items:flex-start;gap:8px;font-size:10px;font-weight:700;margin-top:10px}.check input{width:auto;margin-top:2px}.actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px;position:sticky;bottom:-24px;background:linear-gradient(180deg,rgba(255,255,255,0),#fff 24%);padding:26px 0 3px}.action-button{border:0;border-radius:11px;padding:11px 13px;font-size:10px;font-weight:850}.action-button.primary-action{background:var(--forest);color:#fff}.action-button.success{background:var(--success-soft);color:var(--success)}.action-button.secondary{background:#eef2ed;color:var(--forest)}.action-button.danger{background:var(--danger-soft);color:var(--danger)}
.editor{min-height:210px}.editor-label{display:flex;justify-content:space-between;align-items:center}.editor-label small{font-size:9px;color:var(--muted)}
.history-surface{padding:20px}.history-toolbar{display:flex;justify-content:space-between;gap:14px;align-items:center;margin-bottom:18px}.filter-chips{display:flex;gap:7px;flex-wrap:wrap}.filter-chips button{border:1px solid var(--line);background:var(--surface-soft);color:var(--muted);border-radius:999px;padding:8px 11px;font-size:10px;font-weight:800}.filter-chips button.active{background:var(--forest);border-color:var(--forest);color:#fff}.search-box{display:flex;align-items:center;gap:8px;min-width:280px;border:1px solid var(--line);border-radius:12px;background:var(--surface-soft);padding:0 11px}.search-box input{border:0;background:transparent;box-shadow:none;padding:10px 0;font-size:11px}.search-box input:focus{box-shadow:none}
.history-timeline{position:relative}.history-row{display:grid;grid-template-columns:18px 1fr auto;gap:12px;padding:0 0 18px}.history-row:before{content:"";position:absolute;left:8px;width:1px;height:100%;background:var(--line)}.history-row:last-child:before{display:none}.history-node{width:17px;height:17px;border-radius:50%;background:#fff;border:4px solid var(--forest-2);z-index:1;margin-top:3px}.history-content{border:1px solid var(--line);background:var(--surface-soft);border-radius:15px;padding:13px}.history-content strong{font-size:12px}.history-content p{font-size:10px;color:var(--muted);line-height:1.6;margin:7px 0 0;white-space:pre-wrap}.history-meta{display:flex;gap:7px;flex-wrap:wrap;margin-top:9px}.history-row time{font-size:9px;color:var(--muted-2);padding-top:4px;white-space:nowrap}
.key-dialog{border:0;border-radius:22px;padding:0;box-shadow:0 30px 80px rgba(8,31,22,.26);max-width:440px;width:calc(100% - 32px)}.key-dialog::backdrop{background:rgba(1,27,19,.48);backdrop-filter:blur(6px)}.key-dialog form{padding:28px}.dialog-icon{width:48px;height:48px;border-radius:15px;background:var(--lime);color:var(--forest);display:grid;place-items:center;font-size:22px}.key-dialog h2{font-size:20px;margin:16px 0 7px}.key-dialog p{color:var(--muted);font-size:11px;line-height:1.65}.dialog-actions{display:flex;justify-content:flex-end;gap:9px;margin-top:18px}
#toast{position:fixed;right:26px;bottom:26px;max-width:430px;padding:13px 16px;border-radius:14px;background:var(--forest);color:#fff;font-size:11px;font-weight:700;box-shadow:0 18px 40px rgba(0,0,0,.18);opacity:0;transform:translateY(12px);pointer-events:none;transition:.2s;z-index:100}#toast.show{opacity:1;transform:none}#toast.error{background:#8f2e2e}
@media(max-width:1180px){.app-shell{grid-template-columns:224px minmax(0,1fr)}.sidebar{padding-left:13px;padding-right:13px}.hero-card{grid-template-columns:1fr}.hero-flow{display:none}.metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.work-grid{grid-template-columns:1fr}.sticky-card{position:static;max-height:none}.overview-grid{grid-template-columns:1fr}.workflow-heading{align-items:flex-start;flex-direction:column}.workflow-steps{width:100%;justify-content:center}}
@media(max-width:820px){.app-shell{display:block}.sidebar{position:fixed;left:0;right:0;top:auto;bottom:0;width:100%;height:72px;padding:8px 10px;background:rgba(5,63,45,.96);backdrop-filter:blur(16px);z-index:50}.brand,.nav-label,.guardrail-card,.back-link{display:none}.side-nav{display:grid;grid-template-columns:repeat(4,1fr);gap:4px}.nav-item{display:flex;flex-direction:column;justify-content:center;gap:2px;text-align:center;padding:7px 4px;font-size:9px}.nav-icon{font-size:14px}.nav-code{display:none}.topbar{height:auto;min-height:88px;padding:15px 18px}.mobile-menu{display:none}.page-heading p{max-width:48vw}.top-actions .version,.top-actions .ghost-button{display:none}.workspace{padding:20px 16px 104px}.hero-card{padding:28px 22px;min-height:300px}.hero-copy h2{font-size:32px}.hero-actions{flex-direction:column}.metric-grid{grid-template-columns:1fr 1fr}.metric-card{min-height:122px}.metric-card strong{font-size:30px}.field-grid,.summary-grid,.scenario-grid{grid-template-columns:1fr}.order-context:not(.empty){grid-template-columns:repeat(2,minmax(0,1fr))}.history-toolbar{align-items:stretch;flex-direction:column}.search-box{min-width:0}.workflow-steps{overflow:auto;justify-content:flex-start}.top-actions{gap:6px}.system-status span{display:none}.system-status{padding:10px}.work-grid{gap:12px}.form-surface,.result-surface{padding:18px}}
@media(max-width:520px){.page-heading p{display:none}.topbar{min-height:76px}.page-heading h1{font-size:19px}.metric-grid{grid-template-columns:1fr}.metric-card{min-height:112px}.hero-copy h2{font-size:28px}.workflow-heading h2{font-size:26px}.workflow-steps span{font-size:9px}.workflow-steps i{width:10px}.history-row{grid-template-columns:16px 1fr}.history-row time{display:none}.top-actions{margin-left:auto}.system-status{display:none}.order-context:not(.empty){grid-template-columns:1fr 1fr}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;animation:none!important;transition:none!important}}
'''

COMMUNICATION_JS = r'''const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? "").replace(/[&<>'"]/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
}[char]));

let orders = [];
let capabilities = null;
let historyData = { candidates: [], drafts: [] };
let currentCandidate = null;
let currentDraft = null;
let historyFilter = "all";

const VIEW_META = {
  overview: ["AI行动层", "沟通工作台", "把外部沟通转成下一步行动，并用可信订单事实生成可审核草稿。"],
  task: ["FT05 · Communication to Task", "沟通转任务", "识别任务意图、订单、截止时间和原文证据，再由人工确认写回。"],
  draft: ["FT06 · Controlled Draft", "受控沟通草稿", "基于订单事实生成回复、催办与变更汇总，发送前必须人工确认。"],
  history: ["Audit Trail", "确认与历史", "查看AI生成、人工修改、写回结果与触达记录。"]
};

const STATUS_LABELS = {
  task_candidate_ready: "任务候选可确认",
  no_task: "无需创建任务",
  duplicate_suppressed: "已抑制重复任务",
  needs_manual_review: "需要人工判断",
  blocked: "已阻断",
  draft_ready: "草稿可审核",
  PENDING_HUMAN_CONFIRMATION: "待人工确认",
  BLOCKED_FOR_EDIT: "需修改后确认",
  BLOCKED_FOR_INPUT: "需补充事实",
  PENDING: "待确认",
  COMMITTED: "已写回",
  REJECTED: "已驳回",
  APPROVED: "已确认",
  COPIED_AND_RECORDED: "已复制并记录"
};

const DRAFT_PRESETS = {
  CUSTOMER_REPLY: {
    recipient: "customer", channel: "email",
    instruction: "根据已确认的订单事实回复客户，语气专业，不作未经确认的交期、费用或赔偿承诺。"
  },
  CUSTOMER_CONFIRMATION_REMINDER: {
    recipient: "customer", channel: "email",
    instruction: "礼貌提醒客户确认仍待确认的订单事项，明确说明需要确认的内容和期望回复时间。"
  },
  SUPPLIER_PROGRESS_FOLLOWUP: {
    recipient: "supplier", channel: "wechat",
    instruction: "询问当前准确进度、关键物料到货时间、异常补救方案和明确完成时间，不替供应商作承诺。"
  },
  DELIVERY_STATUS_REPLY: {
    recipient: "customer", channel: "email",
    instruction: "根据客户正式交期、供应商生产完成承诺和当前节点回复交期状态；无法确认时明确说明仍在核实。"
  },
  CHANGE_HISTORY_SUMMARY: {
    recipient: "internal", channel: "email",
    instruction: "按时间顺序汇总客户已确认的历史变更，保留事实来源，不新增未发生的变化。"
  }
};

function toast(message, kind = "") {
  const element = $("#toast");
  element.textContent = message;
  element.className = kind === "error" ? "show error" : "show";
  window.clearTimeout(element._timer);
  element._timer = window.setTimeout(() => { element.className = ""; }, 3400);
}

function labelStatus(value) {
  return STATUS_LABELS[value] || value || "—";
}

function badge(text, kind = "") {
  return `<span class="badge ${kind}">${esc(text)}</span>`;
}

function list(items) {
  return Array.isArray(items) && items.length
    ? `<ul class="list">${items.map(item => `<li>${esc(typeof item === "string" ? item : JSON.stringify(item))}</li>`).join("")}</ul>`
    : "<span class=\"muted-text\">—</span>";
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(date);
}

function openKeyDialog() {
  const dialog = $("#key-dialog");
  $("#key-input").value = sessionStorage.getItem("communicationAdminKey") || "";
  if (dialog.showModal) dialog.showModal();
  else dialog.setAttribute("open", "");
  setTimeout(() => $("#key-input").focus(), 30);
}

async function api(url, options = {}) {
  const key = sessionStorage.getItem("communicationAdminKey") || "";
  const tokenMap = {"USER-1":"tok-user-1","USER-2":"tok-user-2","USER-3":"tok-user-3","MANAGER-1":"tok-manager-1","OPERATOR-A1":"tok-operator-a1","OPERATOR-A2":"tok-operator-a2","MANAGER-A":"tok-manager-a","OPERATOR-B1":"tok-operator-b1","OPERATOR-B2":"tok-operator-b2","MANAGER-B":"tok-manager-b"};
  const currentUserId = localStorage.getItem("currentUserId") || "USER-1";
  const headers = {
    "Content-Type": "application/json",
    "X-Auth-Token": tokenMap[currentUserId] || "tok-user-1",
    ...(key ? { "X-Communication-Key": key } : {}),
    ...(options.headers || {})
  };
  const response = await fetch(url, { ...options, headers });
  let data = {};
  try { data = await response.json(); }
  catch (_) { data = { detail: await response.text() }; }
  if (!response.ok) {
    if (response.status === 401) {
      sessionStorage.removeItem("communicationAdminKey");
      openKeyDialog();
    }
    const message = typeof data.detail === "string"
      ? data.detail
      : JSON.stringify(data.detail || data.error || `请求失败 ${response.status}`);
    throw new Error(message);
  }
  return data;
}

function switchView(view) {
  if (!VIEW_META[view]) return;
  $$(".nav-item").forEach(button => button.classList.toggle("active", button.dataset.view === view));
  $$(".view").forEach(section => {
    const active = section.id === `view-${view}`;
    section.classList.toggle("hidden", !active);
    section.classList.toggle("active", active);
  });
  const [eyebrow, title, subtitle] = VIEW_META[view];
  $("#page-eyebrow").textContent = eyebrow;
  $("#page-title").textContent = title;
  $("#page-subtitle").textContent = subtitle;
  if (view === "history") loadHistory();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setSteps(id, step) {
  const spans = $$(`#${id} span`);
  spans.forEach((item, index) => {
    item.classList.toggle("current", index === step - 1);
    item.classList.toggle("done", index < step - 1);
  });
}

function setLive(id, state, text) {
  const element = $(id);
  element.className = `live-pill ${state}`;
  element.innerHTML = `<i></i>${esc(text)}`;
}

function setBusy(button, busy, mainText, helper = "") {
  button.disabled = busy;
  if (busy) {
    button.dataset.original = button.innerHTML;
    button.innerHTML = `<span>${esc(mainText)}</span>${helper ? `<small>${esc(helper)}</small>` : ""}`;
  } else if (button.dataset.original) {
    button.innerHTML = button.dataset.original;
  }
}

function selectedOrder(selector) {
  const select = $(selector);
  const value = select.value;
  const option = select.selectedOptions[0];
  return { order_id: value || null, order_no: option?.dataset.no || null };
}

function getOrderBySelect(selector) {
  const selected = selectedOrder(selector);
  return orders.find(order => String(order.order_id || order.order_no) === String(selected.order_id || selected.order_no));
}

function orderOptions() {
  const options = [
    '<option value="">请选择订单</option>',
    ...orders.map(order => `<option value="${esc(order.order_id || order.order_no)}" data-no="${esc(order.order_no || "")}">${esc(order.order_no || order.order_id || "未编号")} · ${esc(order.customer_name || "未知客户")}</option>`)
  ].join("");
  $("#ft05-order").innerHTML = options;
  $("#ft06-order").innerHTML = options;
}

function renderOrderContext(selector, targetSelector, mode) {
  const order = getOrderBySelect(selector);
  const target = $(targetSelector);
  if (!order) {
    target.className = "order-context empty compact";
    target.textContent = mode === "draft"
      ? "选择订单后展示客户交期、工厂承诺和当前节点。"
      : "选择订单后展示客户、交期、节点和工厂信息。";
    return;
  }
  const items = [
    ["订单", order.order_no || order.order_id || "—"],
    ["客户", order.customer_name || "—"],
    ["客户交期", order.customer_delivery_date || "—"],
    ["当前节点", order.current_node || order.status || "—"],
    [mode === "draft" ? "工厂完工承诺" : "工厂", mode === "draft" ? (order.supplier_completion_commitment_date || order.latest_supplier_commitment || "—") : (order.factory_name || "—")],
    ["当前进度", order.current_progress == null ? "—" : `${Number(order.current_progress) <= 1 ? Math.round(Number(order.current_progress) * 100) : order.current_progress}%`]
  ];
  target.className = "order-context";
  target.innerHTML = items.map(([label, value]) => `<div class="context-item"><span>${esc(label)}</span><strong title="${esc(value)}">${esc(value)}</strong></div>`).join("");
}

function renderReadiness() {
  if (!capabilities) return;
  const workflows = capabilities.workflows || {};
  const rows = [
    ["Coze API Token", capabilities.token_configured, "调用已发布工作流"],
    ["FT05 沟通转任务", Boolean(workflows.ft05?.configured), workflows.ft05?.workflow_id || "未配置ID"],
    ["FT06 受控草稿", Boolean(workflows.ft06?.configured), workflows.ft06?.workflow_id || "未配置ID"],
    ["FT03 / FT04 闭环", Boolean(workflows.ft03?.configured && workflows.ft04?.configured), "确认写回与行动重排"]
  ];
  $("#readiness-list").innerHTML = rows.map(([name, ready, note]) => `
    <div class="readiness-row"><span class="recent-dot"></span><span><strong>${esc(name)}</strong><small>${esc(note)}</small></span><b class="readiness-state ${ready ? "ok" : "no"}">${ready ? "已就绪" : "待配置"}</b></div>
  `).join("");
  const allReady = capabilities.token_configured && workflows.ft05?.configured && workflows.ft06?.configured;
  const status = $("#system-status");
  status.className = `system-status ${allReady ? "good" : "warn"}`;
  status.querySelector("span").textContent = allReady ? "工作流已连接" : "存在待配置项";
  $("#metric-health").textContent = allReady ? "正常" : "待配置";
  const process = $("#process-ai-state");
  process.className = `process-state ${allReady ? "good" : "warn"}`;
  process.textContent = allReady ? "已连接" : "待配置";
}

function allHistoryItems() {
  return [
    ...(historyData.candidates || []).map(item => ({ ...item, _type: "task" })),
    ...(historyData.drafts || []).map(item => ({ ...item, _type: "draft" }))
  ].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));
}

function isPending(item) {
  const value = String(item.review_status || item.human_status || item.approval_status || item.run_status || "").toUpperCase();
  return ["PENDING", "PENDING_HUMAN_CONFIRMATION", "TASK_CANDIDATE_READY", "DRAFT_READY", "NEEDS_MANUAL_REVIEW"].some(key => value.includes(key));
}

function isDone(item) {
  return !isPending(item) && !["BLOCKED", "BLOCKED_FOR_EDIT", "BLOCKED_FOR_INPUT"].includes(String(item.run_status || item.approval_status || "").toUpperCase());
}

function renderRecentOverview() {
  const items = allHistoryItems().slice(0, 5);
  $("#recent-overview").innerHTML = items.length ? items.map(item => {
    const title = item.task_title || item.ai_subject || item.order_no || "沟通记录";
    const status = item.review_status || item.human_status || item.approval_status || item.run_status;
    return `<div class="recent-row"><span class="recent-dot"></span><span><strong>${esc(title)}</strong><small>${esc(item._type === "task" ? "任务候选" : "沟通草稿")} · ${esc(labelStatus(status))}</small></span><time>${esc(formatTime(item.created_at))}</time></div>`;
  }).join("") : '<div class="empty compact">暂无沟通记录。完成一次FT05或FT06后会显示在这里。</div>';
}

function updateOverview() {
  const items = allHistoryItems();
  $("#metric-orders").textContent = String(orders.length);
  $("#metric-pending").textContent = String(items.filter(isPending).length);
  $("#metric-drafts").textContent = String(items.length);
  renderRecentOverview();
  renderReadiness();
}

function renderHistory() {
  const query = $("#history-search").value.trim().toLowerCase();
  const items = allHistoryItems().filter(item => {
    if (historyFilter === "task" && item._type !== "task") return false;
    if (historyFilter === "draft" && item._type !== "draft") return false;
    if (historyFilter === "pending" && !isPending(item)) return false;
    if (historyFilter === "done" && !isDone(item)) return false;
    const haystack = [item.order_no, item.task_title, item.ai_subject, item.ai_draft, item.communication_text, item.final_text].join(" ").toLowerCase();
    return !query || haystack.includes(query);
  });
  $("#history").innerHTML = items.length ? items.map(item => {
    const title = item.task_title || item.ai_subject || (item._type === "task" ? "任务候选" : "沟通草稿");
    const body = item.final_text || item.edited_draft || item.ai_draft || item.communication_text || "";
    const status = item.review_status || item.human_status || item.approval_status || item.run_status || "—";
    const kind = isPending(item) ? "warn" : isDone(item) ? "good" : "bad";
    return `<div class="history-row"><span class="history-node"></span><div class="history-content"><strong>${esc(title)} · ${esc(item.order_no || "未关联订单")}</strong><p>${esc(body)}</p><div class="history-meta">${badge(item._type === "task" ? "任务候选" : "沟通草稿")}${badge(labelStatus(status), kind)}${item.channel ? badge(item.channel) : ""}</div></div><time>${esc(formatTime(item.created_at))}</time></div>`;
  }).join("") : '<div class="empty">没有符合当前筛选条件的记录。</div>';
}

async function loadHistory(silent = false) {
  try {
    historyData = await api("/api/communication/history?limit=30");
    renderHistory();
    updateOverview();
  } catch (error) {
    if (!silent) toast(error.message, "error");
  }
}

function applyDraftPreset(type, overwriteInstruction = true) {
  const preset = DRAFT_PRESETS[type];
  if (!preset) return;
  $("#ft06-type").value = type;
  $("#ft06-recipient").value = preset.recipient;
  $("#ft06-channel").value = preset.channel;
  if (overwriteInstruction || !$("#ft06-instruction").value.trim()) {
    $("#ft06-instruction").value = preset.instruction;
    updateCounters();
  }
  $$(".scenario").forEach(button => button.classList.toggle("active", button.dataset.draftType === type));
}

function updateCounters() {
  $("#ft05-count").textContent = String($("#ft05-text").value.length);
  $("#ft06-count").textContent = String($("#ft06-instruction").value.length);
}

function decisionKind(status) {
  if (["task_candidate_ready", "draft_ready"].includes(status)) return "";
  if (["no_task", "duplicate_suppressed", "needs_manual_review"].includes(status)) return "warn";
  return "bad";
}

async function runFT05() {
  const button = $("#ft05-run");
  const order = selectedOrder("#ft05-order");
  const text = $("#ft05-text").value.trim();
  if (!order.order_id && !order.order_no) return toast("请先选择关联订单", "error");
  if (!text) return toast("请输入沟通原文", "error");
  try {
    setSteps("ft05-steps", 2);
    setLive("#ft05-live", "running", "FT05分析中");
    setBusy(button, true, "正在分析沟通…", "识别任务、证据和截止时间");
    const data = await api("/api/workflows/ft05/run", {
      method: "POST",
      body: JSON.stringify({ communication_text: text, sender_role: $("#ft05-sender").value, channel: $("#ft05-channel").value, ...order })
    });
    currentCandidate = data;
    renderFT05(data);
    setSteps("ft05-steps", 3);
    setLive("#ft05-live", "good", "等待人工确认");
    toast("FT05分析完成");
  } catch (error) {
    setSteps("ft05-steps", 1);
    setLive("#ft05-live", "bad", "运行失败");
    toast(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

function renderFT05(data) {
  $("#ft05-empty").classList.add("hidden");
  const element = $("#ft05-result");
  element.classList.remove("hidden");
  const result = data.result || {};
  const candidate = result.task_candidate || {};
  const status = result.run_status || "unknown";
  const kind = decisionKind(status);
  const canCommit = Boolean(result.writeback_confirmation_required && data.candidate_id);
  const statusKind = status === "task_candidate_ready" ? "good" : ["no_task", "duplicate_suppressed"].includes(status) ? "warn" : "bad";
  const summary = candidate && Object.keys(candidate).length ? `
    <div class="summary-grid">
      <div class="summary-item"><span>任务类型</span><strong>${esc(candidate.task_type || "—")}</strong></div>
      <div class="summary-item"><span>截止时间</span><strong>${esc(candidate.due_at_candidate || candidate.due_expression || "待确认")}</strong></div>
      <div class="summary-item"><span>优先级</span><strong>${esc(candidate.priority_hint || "—")}</strong></div>
    </div>` : "";
  const editable = candidate && Object.keys(candidate).length ? `
    <div class="review-section">
      <h4>人工确认字段</h4>
      <label class="field"><span>任务标题</span><input id="candidate-title" value="${esc(candidate.task_title || "")}"></label>
      <div class="field-grid">
        <label class="field"><span>截止时间</span><input id="candidate-due" value="${esc(candidate.due_at_candidate || "")}" placeholder="ISO时间或留空待确认"></label>
        <label class="field"><span>优先级</span><select id="candidate-priority"><option value="high">high</option><option value="medium">medium</option><option value="normal">normal</option><option value="low">low</option></select></label>
      </div>
      <label class="field"><span>任务说明</span><textarea id="candidate-description" rows="3">${esc(candidate.task_description || "")}</textarea></label>
    </div>` : "";
  element.innerHTML = `
    <div class="decision-banner ${kind}"><div><strong>${esc(labelStatus(status))}</strong><p>${esc(candidate.reason || result.technical?.error_message || "系统已完成语义理解与规则校验。")}</p></div><div class="status-row">${badge(`风险 ${result.risk_level || "—"}`)}${badge(result.writeback_confirmation_required ? "需人工确认" : "不写回", result.writeback_confirmation_required ? "warn" : "good")}</div></div>
    ${summary}
    ${editable}
    <div class="review-section"><h4>原文证据</h4><div class="quote">${esc(candidate.source_quote || "—")}</div></div>
    <div class="review-section"><h4>未回答事项</h4>${list(candidate.unanswered_items)}</div>
    <div class="review-section"><h4>订单与置信度</h4><div class="status-row">${badge(candidate.related_order_no || "未唯一关联", candidate.related_order_no ? "good" : "warn")}${badge(`负责人 ${candidate.responsible_role || "—"}`)}${badge(`置信度 ${candidate.confidence ?? "—"}`)}</div></div>
    <div class="actions">${canCommit ? '<button id="candidate-commit" class="action-button primary-action" type="button">确认任务并调用FT03</button>' : ""}${data.candidate_id ? '<button id="candidate-reject" class="action-button danger" type="button">驳回候选</button>' : ""}</div>`;
  if ($("#candidate-priority")) $("#candidate-priority").value = candidate.priority_hint || "normal";
  $("#candidate-commit")?.addEventListener("click", commitCandidate);
  $("#candidate-reject")?.addEventListener("click", rejectCandidate);
}

async function commitCandidate() {
  if (!currentCandidate?.candidate_id) return;
  const button = $("#candidate-commit");
  const candidate = { ...(currentCandidate.result?.task_candidate || {}) };
  if ($("#candidate-title")) candidate.task_title = $("#candidate-title").value.trim();
  if ($("#candidate-description")) candidate.task_description = $("#candidate-description").value.trim();
  if ($("#candidate-due")) candidate.due_at_candidate = $("#candidate-due").value.trim() || null;
  if ($("#candidate-priority")) candidate.priority_hint = $("#candidate-priority").value;
  if (!candidate.task_title) return toast("任务标题不能为空", "error");
  try {
    button.disabled = true;
    button.textContent = "正在调用FT03…";
    const data = await api(`/api/communication/candidates/${currentCandidate.candidate_id}/commit`, {
      method: "POST",
      body: JSON.stringify({ operator_id: "USER-1", edited_candidate: candidate, confirmation_version: "2", note: "网站V2人工确认" })
    });
    button.textContent = labelStatus(data.persistence_status || "已处理");
    setLive("#ft05-live", "good", "已人工确认");
    toast(data.message || "任务确认并写回完成");
    await loadHistory(true);
  } catch (error) {
    button.disabled = false;
    button.textContent = "确认任务并调用FT03";
    toast(error.message, "error");
  }
}

async function rejectCandidate() {
  if (!currentCandidate?.candidate_id) return;
  try {
    await api(`/api/communication/candidates/${currentCandidate.candidate_id}/reject`, {
      method: "POST",
      body: JSON.stringify({ operator_id: "USER-1", note: "网站V2人工驳回" })
    });
    setLive("#ft05-live", "bad", "已人工驳回");
    toast("候选已驳回");
    await loadHistory(true);
  } catch (error) { toast(error.message, "error"); }
}

async function runFT06() {
  const button = $("#ft06-run");
  const order = selectedOrder("#ft06-order");
  if (!order.order_id && !order.order_no) return toast("请先选择关联订单", "error");
  try {
    setSteps("ft06-steps", 2);
    setLive("#ft06-live", "running", "FT06生成中");
    setBusy(button, true, "正在生成草稿…", "整理事实并检查风险边界");
    const data = await api("/api/workflows/ft06/run", {
      method: "POST",
      body: JSON.stringify({
        draft_type: $("#ft06-type").value,
        recipient_role: $("#ft06-recipient").value,
        channel: $("#ft06-channel").value,
        tone: $("#ft06-tone").value,
        user_instruction: $("#ft06-instruction").value,
        ...order
      })
    });
    currentDraft = data;
    renderFT06(data);
    setSteps("ft06-steps", 3);
    setLive("#ft06-live", "good", "等待人工审核");
    toast("FT06草稿生成完成");
  } catch (error) {
    setSteps("ft06-steps", 1);
    setLive("#ft06-live", "bad", "运行失败");
    toast(error.message, "error");
  } finally {
    setBusy(button, false);
  }
}

function renderFT06(data) {
  $("#ft06-empty").classList.add("hidden");
  const element = $("#ft06-result");
  element.classList.remove("hidden");
  const result = data.result || {};
  const draft = data.draft_result || result.draft_result || {};
  const status = result.run_status || "unknown";
  const blocked = String(result.approval_status || "").startsWith("BLOCKED");
  const kind = blocked ? "bad" : status === "draft_ready" ? "" : "warn";
  element.innerHTML = `
    <div class="decision-banner ${kind}"><div><strong>${esc(labelStatus(status))}</strong><p>${blocked ? "草稿存在事实或承诺风险，需补充或人工复核。" : "草稿已通过事实引用与高风险承诺检查，仍需人工确认。"}</p></div><div class="status-row">${badge(labelStatus(result.approval_status), blocked ? "bad" : "warn")}${badge(`风险 ${result.risk_level || "—"}`)}${badge("禁止自动发送", "warn")}</div></div>
    <label class="field"><span class="editor-label">主题 <small>可人工修改</small></span><input id="draft-subject" value="${esc(draft.subject || "")}"></label>
    <label class="field"><span class="editor-label">草稿正文 <small>所有修改都会进入审计记录</small></span><textarea id="draft-body" class="editor">${esc(draft.draft || "")}</textarea></label>
    <div class="review-section"><h4>引用事实</h4><div class="facts">${(draft.facts_used || []).map(fact => `<span class="fact">${esc(fact)}</span>`).join("") || "<span>—</span>"}</div></div>
    <div class="review-section"><h4>本次需要询问</h4>${list(draft.questions_to_ask)}</div>
    <div class="review-section"><h4>生成前仍需补充的事实</h4>${list(draft.missing_facts_required_for_generation || draft.missing_facts)}</div>
    <div class="review-section"><h4>阻断风险</h4>${list(draft.blocking_risk_flags || draft.risk_flags)}</div>
    ${blocked ? `<div class="risk-panel"><strong>FT06已阻断该草稿</strong><p>优先补充事实后重新生成。确需人工放行时，必须勾选复核并填写原因。</p><label class="check"><input type="checkbox" id="risk-override">我已核对交期、费用、赔偿与责任边界，并确认人工放行</label></div>` : ""}
    <label class="field"><span>人工确认备注</span><textarea id="draft-note" rows="3" placeholder="普通草稿可选；高风险人工放行时必填"></textarea></label>
    <div class="actions">${data.draft_id ? '<button id="draft-save" class="action-button secondary" type="button">保存修改</button><button id="draft-approve" class="action-button success" type="button">人工确认</button><button id="draft-copy" class="action-button primary-action" type="button">复制并记录触达</button><button id="draft-reject" class="action-button danger" type="button">驳回草稿</button>' : ""}</div>`;
  $("#draft-save")?.addEventListener("click", () => reviewDraft("save_edit"));
  $("#draft-approve")?.addEventListener("click", () => reviewDraft("approve"));
  $("#draft-copy")?.addEventListener("click", () => reviewDraft("copy_and_record"));
  $("#draft-reject")?.addEventListener("click", () => reviewDraft("reject"));
}

async function reviewDraft(action) {
  if (!currentDraft?.draft_id) return;
  const subject = $("#draft-subject").value;
  const body = $("#draft-body").value;
  const blocked = String(currentDraft.result?.approval_status || "").startsWith("BLOCKED");
  const override = Boolean($("#risk-override")?.checked);
  const note = $("#draft-note")?.value || "";
  if (["approve", "copy_and_record"].includes(action) && !body.trim()) return toast("草稿正文为空", "error");
  if (blocked && ["approve", "copy_and_record"].includes(action) && !override) return toast("该草稿已被FT06阻断，请补充事实重生成，或勾选高风险人工复核", "error");
  if (blocked && override && !note.trim()) return toast("高风险人工放行必须填写确认原因", "error");
  if (action === "copy_and_record" && !window.confirm("请确认你已人工审核正文。系统只复制文本并记录触达，不会自动发送。")) return;
  try {
    if (action === "copy_and_record") await navigator.clipboard.writeText([subject, body].filter(Boolean).join("\n\n"));
    const data = await api(`/api/communication/drafts/${currentDraft.draft_id}/review`, {
      method: "POST",
      body: JSON.stringify({ action, operator_id: "USER-1", edited_subject: subject, edited_draft: body, note, risk_override_confirmed: override })
    });
    const state = action === "reject" ? "bad" : "good";
    setLive("#ft06-live", state, action === "reject" ? "已人工驳回" : "已人工处理");
    toast(data.message || "草稿状态已更新");
    await loadHistory(true);
  } catch (error) { toast(error.message, "error"); }
}

async function init() {
  try {
    capabilities = await api("/api/communication/capabilities");
    if (capabilities.admin_key_required && !sessionStorage.getItem("communicationAdminKey")) openKeyDialog();
    const missing = [];
    if (!capabilities.workflows?.ft05?.configured) missing.push("COZE_FT05_WORKFLOW_ID");
    if (!capabilities.workflows?.ft06?.configured) missing.push("COZE_FT06_WORKFLOW_ID");
    if (!capabilities.token_configured) missing.push("COZE_API_TOKEN");
    if (missing.length) {
      const alert = $("#config-alert");
      alert.classList.remove("hidden");
      alert.innerHTML = `<strong>部署配置未完成：</strong>请在Render补充 ${missing.map(esc).join("、")}。页面可以预览，但对应工作流无法调用。`;
    }
    orders = (await api("/api/communication/orders")).orders || [];
    orderOptions();
    await loadHistory(true);
    updateOverview();
  } catch (error) {
    toast(error.message, "error");
    $("#system-status").className = "system-status warn";
    $("#system-status span").textContent = "连接检查失败";
  }
}

$$(".nav-item").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
$$('[data-jump]').forEach(button => button.addEventListener("click", () => switchView(button.dataset.jump)));
$$('[data-preset]').forEach(button => button.addEventListener("click", () => {
  switchView("draft");
  applyDraftPreset(button.dataset.preset, true);
}));
$$('[data-draft-type]').forEach(button => button.addEventListener("click", () => applyDraftPreset(button.dataset.draftType, true)));
$$('[data-history-filter]').forEach(button => button.addEventListener("click", () => {
  historyFilter = button.dataset.historyFilter;
  $$('[data-history-filter]').forEach(item => item.classList.toggle("active", item === button));
  renderHistory();
}));

$("#history-search").addEventListener("input", renderHistory);
$("#history-refresh").addEventListener("click", () => loadHistory());
$("#ft05-order").addEventListener("change", () => renderOrderContext("#ft05-order", "#ft05-order-context", "task"));
$("#ft06-order").addEventListener("change", () => renderOrderContext("#ft06-order", "#ft06-order-context", "draft"));
$("#ft05-text").addEventListener("input", updateCounters);
$("#ft06-instruction").addEventListener("input", updateCounters);
$("#ft05-sample").addEventListener("click", () => {
  $("#ft05-text").value = "PO-1001的彩盒版本V3请今天17点前确认，确认后才能安排后续生产。";
  updateCounters();
});
$("#ft05-run").addEventListener("click", runFT05);
$("#ft06-run").addEventListener("click", runFT06);
$("#key-open").addEventListener("click", openKeyDialog);
$("#key-save").addEventListener("click", () => {
  const value = $("#key-input").value.trim();
  if (!value) return toast("请输入操作密钥", "error");
  sessionStorage.setItem("communicationAdminKey", value);
  $("#key-dialog").close?.();
  toast("操作密钥已保存到本次浏览器会话");
});
$("#key-cancel").addEventListener("click", () => $("#key-dialog").close?.());

applyDraftPreset("CUSTOMER_REPLY", false);
updateCounters();
init();
'''

ENTRY_JS = r'''(()=>{
  const add=()=>{
    if(document.querySelector('[data-communication-entry]'))return;
    const link=document.createElement('a');
    link.href='/communication-assistant';
    link.dataset.communicationEntry='1';
    link.title='沟通转任务、受控草稿与人工确认';
    link.innerHTML='<span aria-hidden="true">✦</span><b>沟通助手</b><small>FT05 / FT06</small>';
    link.style.cssText='display:grid;grid-template-columns:28px 1fr auto;align-items:center;gap:8px;padding:11px 12px;border-radius:13px;text-decoration:none;color:inherit;font-weight:700;border:1px solid transparent;transition:.18s;';
    const icon=link.querySelector('span');
    icon.style.cssText='width:28px;height:28px;border-radius:9px;background:#dff45b;color:#01472f;display:grid;place-items:center;font-weight:900';
    const small=link.querySelector('small');
    small.style.cssText='font-size:9px;opacity:.55;font-weight:800';
    const targets=['aside nav','.sidebar nav','.sidebar-menu','.nav-list','[data-nav]'];
    let target=null;
    for(const selector of targets){target=document.querySelector(selector);if(target)break}
    if(target){target.appendChild(link)}else{
      link.style.cssText+='position:fixed;right:18px;bottom:76px;background:#01472f;color:#fff;border-color:rgba(255,255,255,.14);box-shadow:0 16px 36px rgba(1,71,47,.24);z-index:9999;';
      document.body.appendChild(link)
    }
  };
  document.readyState==='loading'?document.addEventListener('DOMContentLoaded',add):add();
  setTimeout(add,1200)
})();
'''


class CommunicationEntryMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path not in ("/", "/index.html"):
            return response
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type:
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        text = body.decode("utf-8", errors="replace")
        marker = "/api/communication/assets/entry.js"
        if marker not in text:
            script = f'<script src="{marker}"></script>'
            text = text.replace("</body>", script + "</body>") if "</body>" in text else text + script
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(content=text, status_code=response.status_code, headers=headers, media_type="text/html")


def register_communication_workflows_patch(app: FastAPI) -> None:
    if getattr(app.state, "communication_workflows_patch_registered", False):
        return
    app.state.communication_workflows_patch_registered = True

    @app.get("/communication-assistant")
    def communication_page():
        return RedirectResponse(url="/#today", status_code=307)

    @app.get("/api/communication/assets/style.css")
    def communication_style():
        return Response(COMMUNICATION_CSS, media_type="text/css")

    @app.get("/api/communication/assets/app.js")
    def communication_script():
        return Response(COMMUNICATION_JS, media_type="application/javascript")

    @app.get("/api/communication/assets/entry.js")
    def communication_entry_script():
        return Response(ENTRY_JS, media_type="application/javascript")

    @app.get("/api/communication/capabilities")
    def communication_capabilities(identity: CurrentIdentity = Depends(get_current_identity)):
        config = _configuration()
        with db() as conn:
            _ensure_patch_schema(conn)
            recent = [
                dict(row)
                for row in conn.execute(
                    "SELECT workflow_code,status,error_code,error_message,debug_url,duration_ms,created_at FROM communication_workflow_runs ORDER BY created_at DESC LIMIT 10"
                )
            ]
        return {
            "patch_version": PATCH_VERSION,
            "token_configured": bool(config["token"]),
            "admin_key_required": bool(config["admin_key"]),
            "api_base": config["api_base"],
            "parameters_mode": config["parameters_mode"],
            "workflows": {
                "ft03": {"configured": bool(config["ft03_id"]), "workflow_id": config["ft03_id"]},
                "ft04": {"configured": bool(config["ft04_id"]), "workflow_id": config["ft04_id"]},
                "ft05": {"configured": bool(config["ft05_id"]), "workflow_id": config["ft05_id"]},
                "ft06": {"configured": bool(config["ft06_id"]), "workflow_id": config["ft06_id"]},
            },
            "recent_runs": recent,
            "page": "/communication-assistant",
        }

    @app.get("/api/communication/orders")
    def communication_orders(limit: int = Query(default=200, ge=1, le=500), identity: CurrentIdentity = Depends(get_current_identity)):
        accessible = []
        with db() as conn:
            _ensure_patch_schema(conn)
            for order in _list_orders(conn, min(limit * 3, 500)):
                try:
                    require_order_access(identity, order, conn)
                except HTTPException:
                    continue
                accessible.append(order)
                if len(accessible) >= limit:
                    break
        return {"orders": accessible}

    @app.post("/api/workflows/ft05/run")
    def run_ft05(
        payload: FT05RunRequest,
        x_communication_key: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        identity: CurrentIdentity = Depends(get_current_identity),
    ):
        _require_admin_key(x_communication_key, x_api_key)
        config = _configuration()
        request_id = payload.request_id or _new_id("FT05RUN")
        source_message_id = payload.source_message_id or _new_id("MSG")
        received_at = payload.received_at or _now_iso()
        with db() as conn:
            _ensure_patch_schema(conn)
            supplied_context = payload.order_context
            context_probe = supplied_context[0] if isinstance(supplied_context, list) and supplied_context else (supplied_context if isinstance(supplied_context, dict) else {})
            order = _find_order(conn, payload.order_id or context_probe.get("order_id"), payload.order_no or context_probe.get("order_no"))
            if order is None:
                raise HTTPException(status_code=404, detail="未找到关联订单，请先导入订单或选择正确订单")
            require_order_access(identity, order, conn)
            # Security: workflow facts are rebuilt from the authorized DB order; client-supplied order_context cannot widen scope.
            order_context = order
            order_list = [order_context]
            selected_order = order_list[0] if len(order_list) == 1 else None
            open_tasks = payload.existing_open_tasks
            if open_tasks is None:
                open_tasks = _list_tasks(conn, selected_order, open_only=True) if selected_order else _list_tasks(conn, None, open_only=True)
            # FT05只需要用于去重和判断行动的最小任务上下文，避免把整张任务表送入模型。
            compact_tasks = []
            for task in (open_tasks or [])[:20]:
                compact_tasks.append({
                    key: task.get(key)
                    for key in (
                        "task_id", "title", "status", "task_type", "action_target",
                        "due_at", "next_action_at", "waiting_for", "promised_reply_at",
                        "related_order_no", "source_message_id"
                    )
                    if task.get(key) not in (None, "")
                })
            open_tasks = compact_tasks

        parameters = {
            "communication_text": payload.communication_text,
            "sender_role": payload.sender_role,
            "channel": payload.channel,
            "received_at": received_at,
            "timezone": payload.timezone,
            "order_context_json": _json_dumps(order_context),
            "existing_open_tasks_json": _json_dumps(open_tasks),
            "organization_id": payload.organization_id or config["organization_id"],
            "request_id": request_id,
            "source_message_id": source_message_id,
        }
        try:
            call = _run_coze_workflow("FT05", config["ft05_id"], parameters, request_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        result = call["result"]
        candidate = result.get("task_candidate") if isinstance(result.get("task_candidate"), dict) else {}
        candidate_id = None
        if candidate or result.get("run_status") in ("task_candidate_ready", "needs_manual_review", "duplicate_suppressed", "no_task"):
            candidate_id = _new_id("FTC")
            order_no = candidate.get("related_order_no") or (selected_order or {}).get("order_no")
            order_id = (selected_order or {}).get("order_id")
            with db() as conn:
                _ensure_patch_schema(conn)
                conn.execute(
                    "INSERT INTO communication_task_candidates(candidate_id,request_id,source_message_id,order_id,order_no,communication_text,sender_role,channel,result_json,task_candidate_json,run_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        candidate_id,
                        request_id,
                        source_message_id,
                        order_id,
                        order_no,
                        payload.communication_text,
                        payload.sender_role,
                        payload.channel,
                        _json_dumps(result),
                        _json_dumps(candidate),
                        str(result.get("run_status") or "unknown"),
                        _now_iso(),
                    ),
                )
                _append_event(conn, "task_candidate", candidate_id, "FT05_RESULT_SAVED", result, None)
                conn.commit()
        return {
            "ok": True,
            "workflow": "FT05",
            "candidate_id": candidate_id,
            "request_id": request_id,
            "source_message_id": source_message_id,
            "result": result,
            "debug_url": call.get("debug_url"),
        }

    @app.post("/api/workflows/ft06/run")
    def run_ft06(
        payload: FT06RunRequest,
        x_communication_key: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        identity: CurrentIdentity = Depends(get_current_identity),
    ):
        _require_admin_key(x_communication_key, x_api_key)
        config = _configuration()
        request_id = payload.request_id or _new_id("FT06RUN")
        with db() as conn:
            _ensure_patch_schema(conn)
            probe = payload.order_context if isinstance(payload.order_context, dict) else {}
            order = _find_order(conn, payload.order_id or probe.get("order_id"), payload.order_no or probe.get("order_no"))
            if not order:
                raise HTTPException(status_code=404, detail="未找到关联订单，请先导入订单或选择正确订单")
            require_order_access(identity, order, conn)
            tasks = _list_tasks(conn, order, open_only=True)
            fact_catalog = payload.fact_catalog if payload.fact_catalog is not None else _fact_catalog_from_order(order)
            raw_task_context = payload.task_context if payload.task_context is not None else _task_context_from_tasks(tasks)
            history = payload.communication_history if payload.communication_history is not None else _list_messages(conn, order)

            # FT06按草稿类型只保留最小必要上下文。事实目录保留已确认事实，
            # 开放任务与沟通历史限制条数，降低Token和等待时间。
            fact_catalog = [
                fact for fact in (fact_catalog or [])
                if not isinstance(fact, dict) or fact.get("confirmed", True) is not False
            ][:40]
            task_context = _normalize_ft06_task_context(raw_task_context)
            history = [
                {key: message.get(key) for key in (
                    "message_id", "sender_role", "channel", "raw_content",
                    "received_at", "created_at"
                ) if message.get(key) not in (None, "")}
                for message in (history or [])[-8:]
            ]

        parameters = {
            "draft_type": payload.draft_type,
            "recipient_role": payload.recipient_role,
            "channel": payload.channel,
            "language": payload.language,
            "tone": payload.tone,
            "fact_catalog_json": _json_dumps(fact_catalog),
            "order_context_json": _json_dumps(order),
            "task_context_json": _json_dumps(task_context),
            "communication_history_json": _json_dumps(history),
            "user_instruction": payload.user_instruction,
            # Tenant context comes from authenticated identity, never from client payload.
            "organization_id": identity.organization_id,
            "request_id": request_id,
            "order_id": str(order.get("order_id") or payload.order_id or ""),
        }
        if _d11_uat_ft06_fixture_enabled():
            result = _uat_ft06_fixture_result(payload, order, fact_catalog, task_context)
            call = {"result": result, "debug_url": None}
        else:
            try:
                call = _run_coze_workflow("FT06", config["ft06_id"], parameters, request_id)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            result = call["result"]
        fields = _extract_ft06_fields(result)
        draft_id = _new_id("FTD")
        now = _now_iso()
        with db() as conn:
            _ensure_patch_schema(conn)
            conn.execute(
                "INSERT INTO communication_drafts(draft_id,request_id,order_id,order_no,draft_type,recipient_role,channel,result_json,ai_subject,ai_draft,facts_used_json,missing_facts_json,questions_to_ask_json,risk_flags_json,run_status,approval_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    draft_id,
                    request_id,
                    order.get("order_id"),
                    order.get("order_no"),
                    payload.draft_type,
                    payload.recipient_role,
                    payload.channel,
                    _json_dumps(result),
                    fields["subject"],
                    fields["draft"],
                    _json_dumps(fields["facts_used"]),
                    _json_dumps(fields["missing_facts"]),
                    _json_dumps(fields["questions_to_ask"]),
                    _json_dumps(fields["risk_flags"]),
                    str(result.get("run_status") or "unknown"),
                    str(result.get("approval_status") or ""),
                    now,
                    now,
                ),
            )
            _append_event(conn, "communication_draft", draft_id, "FT06_RESULT_SAVED", result, None)
            conn.commit()
        return {
            "ok": True,
            "workflow": "FT06",
            "draft_id": draft_id,
            "request_id": request_id,
            "result": result,
            "draft_result": {
                **fields["draft_result"],
                "subject": fields["subject"],
                "draft": fields["draft"],
                "facts_used": fields["facts_used"],
                "missing_facts_required_for_generation": fields["missing_facts"],
                "questions_to_ask": fields["questions_to_ask"],
                "blocking_risk_flags": fields["risk_flags"],
            },
            "debug_url": call.get("debug_url"),
        }

    @app.post("/api/communication/candidates/{candidate_id}/commit")
    def commit_candidate(
        candidate_id: str,
        payload: CandidateCommitRequest,
        x_communication_key: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        identity: CurrentIdentity = Depends(get_current_identity),
    ):
        _require_admin_key(x_communication_key, x_api_key)
        config = _configuration()
        if not config["ft03_id"]:
            raise HTTPException(status_code=503, detail="COZE_FT03_WORKFLOW_ID未配置，无法正式写回任务")
        with db() as conn:
            _ensure_patch_schema(conn)
            row = conn.execute("SELECT * FROM communication_task_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="任务候选不存在")
            record = dict(row)
            if record["review_status"] == "COMMITTED":
                existing = _json_loads(record.get("ft03_result_json"), {})
                return {"ok": True, "message": "该候选已提交，未重复写回", "persistence_status": "duplicate_skipped", "ft03_result": existing}
            candidate = payload.edited_candidate or _json_loads(record["task_candidate_json"], {})
            if not candidate.get("task_required", True):
                raise HTTPException(status_code=409, detail="该候选不需要创建任务")
            if candidate.get("duplicate_suppressed"):
                raise HTTPException(status_code=409, detail="该候选已被重复任务规则抑制")
            order = _find_order(conn, record.get("order_id"), record.get("order_no"))
            if not order:
                raise HTTPException(status_code=404, detail="关联订单不存在")
            require_order_access(identity, order, conn)
            existing_tasks = _list_tasks(conn, order, open_only=False)
            idempotency_keys = _existing_idempotency_keys(conn)

        confirmed_payload = _candidate_confirmed_payload(candidate, order, identity.user_id)
        ft03_parameters = {
            "extraction_run_id": record["request_id"],
            "operation_time": _now_iso(),
            "adapter_configured": "YES",
            "confirmed_payload_json": _json_dumps(confirmed_payload),
            "existing_task_state_json": _json_dumps({"tasks": existing_tasks}),
            "operator_id": identity.user_id,
            "source_document_id": record["source_message_id"] or candidate_id,
            "existing_idempotency_keys_json": _json_dumps(idempotency_keys),
            "confirmation_version": payload.confirmation_version,
            "existing_business_state_json": _json_dumps(order),
        }
        try:
            call = _run_coze_workflow("FT03_FROM_FT05", config["ft03_id"], ft03_parameters, record["request_id"])
        except Exception as exc:
            with db() as conn:
                _ensure_patch_schema(conn)
                conn.execute(
                    "UPDATE communication_task_candidates SET review_status='FT03_FAILED',reviewer_id=?,review_note=?,reviewed_at=? WHERE candidate_id=?",
                    (identity.user_id, f"{payload.note} | {exc}".strip(" |"), _now_iso(), candidate_id),
                )
                _append_event(conn, "task_candidate", candidate_id, "FT03_CALL_FAILED", {"error": str(exc)}, identity.user_id)
                conn.commit()
            raise HTTPException(status_code=502, detail=f"FT03调用失败，候选未标记成功：{exc}") from exc

        ft03_result = call["result"]
        persistence_status = _parse_persistence_status(ft03_result)
        success = persistence_status in ("committed", "duplicate_skipped", "persistence_confirmed")
        with db() as conn:
            _ensure_patch_schema(conn)
            conn.execute(
                "UPDATE communication_task_candidates SET review_status=?,reviewer_id=?,review_note=?,ft03_result_json=?,reviewed_at=? WHERE candidate_id=?",
                (
                    "COMMITTED" if success else "FT03_UNVERIFIED",
                    identity.user_id,
                    payload.note,
                    _json_dumps(ft03_result),
                    _now_iso(),
                    candidate_id,
                ),
            )
            _append_event(
                conn,
                "task_candidate",
                candidate_id,
                "FT03_COMMITTED" if success else "FT03_RESULT_UNVERIFIED",
                ft03_result,
                identity.user_id,
            )
            conn.commit()
        if not success:
            raise HTTPException(status_code=502, detail={"message": "FT03未返回可确认的提交状态，系统没有标记成功", "ft03_result": ft03_result})
        return {
            "ok": True,
            "message": "任务候选已人工确认并交由FT03写回",
            "persistence_status": persistence_status,
            "ft03_result": ft03_result,
            "debug_url": call.get("debug_url"),
        }

    @app.post("/api/communication/candidates/{candidate_id}/reject")
    def reject_candidate(
        candidate_id: str,
        payload: CandidateRejectRequest,
        x_communication_key: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        identity: CurrentIdentity = Depends(get_current_identity),
    ):
        _require_admin_key(x_communication_key, x_api_key)
        with db() as conn:
            _ensure_patch_schema(conn)
            existing = conn.execute("SELECT order_id,order_no FROM communication_task_candidates WHERE candidate_id=?", (candidate_id,)).fetchone()
            if not existing:
                raise HTTPException(status_code=404, detail="任务候选不存在")
            order = _find_order(conn, existing["order_id"], existing["order_no"])
            if not order:
                raise HTTPException(status_code=404, detail="关联订单不存在")
            require_order_access(identity, order, conn)
            cursor = conn.execute(
                "UPDATE communication_task_candidates SET review_status='REJECTED',reviewer_id=?,review_note=?,reviewed_at=? WHERE candidate_id=?",
                (identity.user_id, payload.note, _now_iso(), candidate_id),
            )
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="任务候选不存在")
            _append_event(conn, "task_candidate", candidate_id, "HUMAN_REJECTED", {"note": payload.note}, identity.user_id)
            conn.commit()
        return {"ok": True, "message": "任务候选已驳回"}

    @app.post("/api/communication/drafts/{draft_id}/review")
    def review_draft(
        draft_id: str,
        payload: DraftReviewRequest,
        x_communication_key: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        identity: CurrentIdentity = Depends(get_current_identity),
    ):
        _require_admin_key(x_communication_key, x_api_key)
        action = payload.action.strip().lower()
        if action not in ("approve", "reject", "save_edit", "copy_and_record"):
            raise HTTPException(status_code=400, detail="action仅支持approve/reject/save_edit/copy_and_record")
        with db() as conn:
            _ensure_patch_schema(conn)
            row = conn.execute("SELECT * FROM communication_drafts WHERE draft_id=?", (draft_id,)).fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="草稿不存在")
            record = dict(row)
            order = _find_order(conn, record.get("order_id"), record.get("order_no"))
            if not order:
                raise HTTPException(status_code=404, detail="关联订单不存在")
            require_order_access(identity, order, conn)
            subject = payload.edited_subject if payload.edited_subject is not None else record.get("edited_subject") or record.get("ai_subject") or ""
            draft = payload.edited_draft if payload.edited_draft is not None else record.get("edited_draft") or record.get("ai_draft") or ""
            blocked_by_workflow = str(record.get("approval_status") or "").upper().startswith("BLOCKED")
            if action in ("approve", "copy_and_record") and blocked_by_workflow:
                if not payload.risk_override_confirmed:
                    raise HTTPException(status_code=409, detail="该草稿被FT06标记为阻断。请补充事实后重新生成，或勾选高风险人工复核并填写原因。")
                if not payload.note.strip():
                    raise HTTPException(status_code=400, detail="高风险人工复核必须填写确认原因")
            now = _now_iso()
            if action == "reject":
                human_status = "REJECTED"
                approved_at = record.get("approved_at")
                copied_at = record.get("copied_at")
                final_text = record.get("final_text")
                event_type = "HUMAN_REJECTED"
                message = "草稿已驳回"
            elif action == "save_edit":
                human_status = "EDITED"
                approved_at = record.get("approved_at")
                copied_at = record.get("copied_at")
                final_text = draft
                event_type = "HUMAN_EDIT_SAVED"
                message = "人工修改已保存"
            elif action == "approve":
                human_status = "APPROVED"
                approved_at = now
                copied_at = record.get("copied_at")
                final_text = draft
                event_type = "HUMAN_APPROVED"
                message = "草稿已人工确认，仍未自动发送"
            else:
                if not draft.strip():
                    raise HTTPException(status_code=400, detail="最终草稿不能为空")
                human_status = "COPIED_AND_RECORDED"
                approved_at = record.get("approved_at") or now
                copied_at = now
                final_text = draft
                event_type = "COPIED_AND_RECORDED"
                message = "已复制最终文本并记录人工触达；系统没有自动发送"
            conn.execute(
                "UPDATE communication_drafts SET edited_subject=?,edited_draft=?,final_text=?,human_status=?,reviewer_id=?,review_note=?,approved_at=?,copied_at=?,updated_at=? WHERE draft_id=?",
                (subject, draft, final_text, human_status, identity.user_id, payload.note, approved_at, copied_at, now, draft_id),
            )
            task_update = None
            if action == "copy_and_record" and payload.task_id:
                task_update = _update_task_waiting_state(
                    conn,
                    payload.task_id,
                    payload.waiting_on,
                    payload.promised_reply_at,
                    payload.next_action_at,
                )
            event_payload = {
                "action": action,
                "subject": subject,
                "final_text": final_text,
                "task_id": payload.task_id,
                "task_update": task_update,
                "waiting_on": payload.waiting_on,
                "promised_reply_at": payload.promised_reply_at,
                "next_action_at": payload.next_action_at,
                "workflow_blocked": blocked_by_workflow,
                "risk_override_confirmed": payload.risk_override_confirmed,
                "risk_override_note": payload.note if payload.risk_override_confirmed else "",
            }
            _append_event(conn, "communication_draft", draft_id, event_type, event_payload, identity.user_id)
            conn.commit()
        return {
            "ok": True,
            "message": message,
            "human_status": human_status,
            "send_allowed": False,
            "actual_send_performed": False,
            "task_update": task_update,
        }

    @app.post("/api/communication/ranking/refresh")
    def refresh_ranking(
        payload: RankingRefreshRequest,
        x_communication_key: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None),
        identity: CurrentIdentity = Depends(get_current_identity),
    ):
        _require_admin_key(x_communication_key, x_api_key)
        require_manager(identity)
        config = _configuration()
        if not config["ft04_id"]:
            raise HTTPException(status_code=503, detail="COZE_FT04_WORKFLOW_ID未配置")
        with db() as conn:
            _ensure_patch_schema(conn)
            orders = _list_orders(conn, 500)
            tasks = _list_tasks(conn, None, open_only=False)
            risks: list[dict[str, Any]] = []
            if table_exists(conn, "risk_signals"):
                risks = [dict(row) for row in conn.execute("SELECT * FROM risk_signals ORDER BY rowid DESC LIMIT 500")]
        parameters = {
            "timezone": payload.timezone,
            "current_time": payload.current_time or _now_iso(),
            "current_user_id": identity.user_id,
            "orders_json": _json_dumps(orders),
            "risk_signals_json": _json_dumps(risks),
            "workday_policy_json": "{}",
            "ranking_config_json": "{}",
            "tasks_json": _json_dumps(tasks),
        }
        request_id = _new_id("FT04RUN")
        try:
            call = _run_coze_workflow("FT04_FROM_COMMUNICATION", config["ft04_id"], parameters, request_id)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "result": call["result"], "debug_url": call.get("debug_url")}

    @app.get("/api/communication/history")
    def communication_history(limit: int = Query(default=30, ge=1, le=200), identity: CurrentIdentity = Depends(get_current_identity)):
        with db() as conn:
            _ensure_patch_schema(conn)
            candidates = [
                dict(row)
                for row in conn.execute(
                    "SELECT candidate_id,request_id,source_message_id,order_id,order_no,communication_text,sender_role,channel,run_status,review_status,reviewer_id,review_note,created_at,reviewed_at,task_candidate_json FROM communication_task_candidates ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            ]
            for item in candidates:
                candidate = _json_loads(item.pop("task_candidate_json"), {})
                item["task_title"] = candidate.get("task_title") if isinstance(candidate, dict) else None
                item["task_type"] = candidate.get("task_type") if isinstance(candidate, dict) else None
            drafts = [
                dict(row)
                for row in conn.execute(
                    "SELECT draft_id,request_id,order_id,order_no,draft_type,recipient_role,channel,ai_subject,ai_draft,edited_subject,edited_draft,final_text,run_status,approval_status,human_status,reviewer_id,review_note,approved_at,copied_at,created_at,updated_at FROM communication_drafts ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            ]
            def visible(item: dict[str, Any]) -> bool:
                order = _find_order(conn, item.get("order_id"), item.get("order_no"))
                if not order:
                    return False
                try:
                    require_order_access(identity, order, conn)
                    return True
                except HTTPException:
                    return False
            candidates = [item for item in candidates if visible(item)]
            drafts = [item for item in drafts if visible(item)]
        return {"candidates": candidates, "drafts": drafts}


__all__ = [
    "PATCH_VERSION",
    "register_communication_workflows_patch",
    "_normalize_workflow_result",
    "_fact_catalog_from_order",
    "_candidate_confirmed_payload",
]
