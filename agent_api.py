from __future__ import annotations

import hmac
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from coze_agent_client import agent_status as coze_agent_status, run_agent_chat
from analytics import ensure_analytics_schema, track_event

CN_TZ = timezone(timedelta(hours=8))
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "action_layer.db")))
AGENT_API_KEY = os.getenv("FLOWORDER_AGENT_API_KEY", "").strip()
CRON_API_KEY = os.getenv("FLOWORDER_CRON_API_KEY", "").strip()
ALLOW_INSECURE_TOOLS = os.getenv("ALLOW_INSECURE_AGENT_TOOLS", "false").lower() == "true"
MANAGER_IDS = {"MANAGER-1"}
OWNER_NAME_TO_ID = {"李梅": "USER-1", "王晓": "USER-2", "陈琳": "USER-3", "周主管": "MANAGER-1"}
ACTIVE_ORDER_STATUSES = {"ACTIVE", "OPEN", "IN_PROGRESS", "进行中", "活跃"}
FINAL_ORDER_STATUSES = {"DONE", "CLOSED", "CANCELLED", "COMPLETED", "已完成", "已取消"}
ANOMALY_TYPES = {
    "SUPPLIER_COMMITMENT_OVERDUE",
    "CUSTOMER_CONFIRMATION_BLOCKING",
    "DELIVERY_RISK",
    "LOGISTICS_EXCEPTION",
    "INFORMATION_GAP",
}


class AnyPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def iso(dt: datetime | None = None) -> str:
    return (dt or now_cn()).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def safe_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(str(value))
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback


def parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(CN_TZ)


def normalize_owner(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text in {"待分配", "未分配", "-", "—"}:
        return None
    return OWNER_NAME_TO_ID.get(text, text)


def is_manager(user_id: str | None, role: str | None = None) -> bool:
    return normalize_owner(user_id) in MANAGER_IDS or str(role or "").upper() in {"MANAGER", "SUPERVISOR", "ADMIN"}


def owner_allowed(owner: Any, user_id: str, role: str | None = None, allowed_owner_ids: Iterable[str] | None = None) -> bool:
    if is_manager(user_id, role):
        return True
    normalized = normalize_owner(owner)
    allowed = {normalize_owner(x) for x in (allowed_owner_ids or []) if normalize_owner(x)}
    allowed.add(normalize_owner(user_id))
    return normalized in allowed


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


def rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _require_agent_key(x_floworder_agent_key: str | None) -> None:
    if not AGENT_API_KEY:
        if ALLOW_INSECURE_TOOLS:
            return
        raise HTTPException(503, "未配置FLOWORDER_AGENT_API_KEY，Agent工具默认拒绝访问")
    if not x_floworder_agent_key or not hmac.compare_digest(x_floworder_agent_key, AGENT_API_KEY):
        raise HTTPException(401, "Agent工具密钥无效")


def _require_cron_key(x_floworder_cron_key: str | None) -> None:
    expected = CRON_API_KEY or AGENT_API_KEY
    if not expected:
        raise HTTPException(503, "未配置FLOWORDER_CRON_API_KEY或FLOWORDER_AGENT_API_KEY")
    if not x_floworder_cron_key or not hmac.compare_digest(x_floworder_cron_key, expected):
        raise HTTPException(401, "定时巡检密钥无效")


def actor(payload: dict[str, Any]) -> dict[str, Any]:
    user_id = str(payload.get("current_user_id") or "USER-1").strip()
    role = str(payload.get("current_role") or ("manager" if user_id in MANAGER_IDS else "operator")).strip()
    allowed = payload.get("allowed_owner_ids") or []
    if isinstance(allowed, str):
        allowed = [x.strip() for x in allowed.split(",") if x.strip()]
    return {
        "organization_id": str(payload.get("organization_id") or "ORG-DEMO"),
        "current_user_id": user_id,
        "current_role": role,
        "allowed_owner_ids": allowed,
    }




def enforce_run_budget(conn: sqlite3.Connection, run_id: str | None) -> None:
    """Enforce the server-side tool and time budget for Coze-driven runs."""
    if not run_id:
        return
    row = conn.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Agent运行不存在，请先调用start_agent_run")
    if row["status"] not in {"RUNNING", "PARTIAL"}:
        raise HTTPException(409, f"Agent运行当前状态为{row['status']}，不能继续调用工具")
    used = conn.execute("SELECT COUNT(*) FROM agent_tool_calls WHERE run_id=?", (run_id,)).fetchone()[0]
    max_calls = int(row["max_tool_calls"] or 8)
    started_at = parse_dt(row["started_at"] or row["created_at"]) or now_cn()
    elapsed = max(0.0, (now_cn() - started_at).total_seconds())
    max_seconds = int(row["max_duration_seconds"] or 60)
    if used >= max_calls or elapsed >= max_seconds:
        conn.execute(
            "UPDATE agent_runs SET status='PARTIAL',stop_reason='BUDGET_REACHED',duration_ms=?,completed_at=? WHERE run_id=?",
            (int(elapsed * 1000), iso(), run_id),
        )
        reason = "工具调用次数已达到上限" if used >= max_calls else "分析时间已达到上限"
        track_event(
            conn,
            "agent_run_timeout",
            organization_id=row["organization_id"],
            user_id=row["current_user_id"],
            user_role=row["current_role"],
            run_id=run_id,
            source="agent_tool",
            properties={
                "reason": "TOOL_BUDGET" if used >= max_calls else "TIME_BUDGET",
                "tool_call_count": used,
                "max_tool_calls": max_calls,
                "elapsed_seconds": round(elapsed, 2),
                "max_duration_seconds": max_seconds,
                "approval_created": bool(conn.execute("SELECT 1 FROM approval_requests WHERE run_id=? LIMIT 1", (run_id,)).fetchone()),
                "final_response_generated": False,
            },
        )
        conn.commit()
        raise HTTPException(429, f"{reason}；请基于当前证据返回部分结果")

def log_tool_call(conn: sqlite3.Connection, *, run_id: str | None, tool_name: str, request: dict[str, Any], response: Any,
                  status: str, duration_ms: int, error_code: str | None = None, error_message: str | None = None) -> str:
    call_id = new_id("ATC")
    conn.execute(
        """INSERT INTO agent_tool_calls(call_id,run_id,tool_name,request_json,response_json,status,error_code,error_message,duration_ms,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (call_id, run_id, tool_name, json.dumps(request, ensure_ascii=False), json.dumps(response, ensure_ascii=False),
         status, error_code, error_message, duration_ms, iso()),
    )
    return call_id


def audit_event(conn: sqlite3.Connection, entity_type: str, entity_id: str | None, event_type: str,
                payload: dict[str, Any], operator_id: str) -> None:
    conn.execute(
        "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
        (new_id("EVT"), entity_type, entity_id, event_type, json.dumps(payload, ensure_ascii=False), operator_id, iso()),
    )


def _order_scope_sql(a: dict[str, Any]) -> tuple[str, list[Any]]:
    if is_manager(a["current_user_id"], a["current_role"]):
        return "1=1", []
    allowed = {normalize_owner(a["current_user_id"])}
    allowed.update(normalize_owner(x) for x in a["allowed_owner_ids"] if normalize_owner(x))
    placeholders = ",".join("?" for _ in allowed)
    return f"owner IN ({placeholders})", list(allowed)


def _assert_order_access(conn: sqlite3.Connection, order_id: str, a: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(404, "订单不存在")
    order = dict(row)
    if not owner_allowed(order.get("owner"), a["current_user_id"], a["current_role"], a["allowed_owner_ids"]):
        raise HTTPException(403, "无权访问该订单")
    return order


def list_candidate_orders_logic(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    a = actor(payload)
    due_days = max(1, min(int(payload.get("due_within_days") or 14), 90))
    limit = max(1, min(int(payload.get("limit") or 50), 200))
    current = parse_dt(payload.get("current_time")) or now_cn()
    deadline = current + timedelta(days=due_days)
    scope_sql, scope_params = _order_scope_sql(a)
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM orders WHERE {scope_sql} AND UPPER(COALESCE(status,'ACTIVE')) NOT IN ('DONE','CLOSED','CANCELLED','COMPLETED') ORDER BY requested_delivery_date,updated_at DESC",
        scope_params,
    )]
    candidates: list[dict[str, Any]] = []
    for order in rows:
        order_id = order["order_id"]
        delivery = parse_dt(order.get("requested_delivery_date"))
        supplier_commitment = parse_dt(order.get("latest_supplier_commitment"))
        overdue_waiting = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE related_order_id=? AND status!='DONE' AND promised_reply_at IS NOT NULL AND promised_reply_at<?",
            (order_id, iso(current)),
        ).fetchone()[0]
        high_tasks = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE related_order_id=? AND status!='DONE' AND (urgent=1 OR risk_level IN ('high','critical'))",
            (order_id,),
        ).fetchone()[0]
        pending_confirmation = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE related_order_id=? AND status!='DONE' AND pending_confirmation=1",
            (order_id,),
        ).fetchone()[0]
        recent_high_message = conn.execute(
            """SELECT COUNT(*) FROM source_messages WHERE order_id=? AND created_at>=? AND
               (raw_content LIKE '%取消%' OR raw_content LIKE '%投诉%' OR raw_content LIKE '%延误%' OR raw_content LIKE '%赶不上%' OR raw_content LIKE '%缺料%')""",
            (order_id, iso(current - timedelta(hours=24))),
        ).fetchone()[0]
        logistics_exception = conn.execute(
            "SELECT COUNT(*) FROM logistics_events WHERE order_id=? AND status IN ('DELAYED','EXCEPTION','CUSTOMS_HOLD') AND resolved_at IS NULL",
            (order_id,),
        ).fetchone()[0]
        in_window = delivery is not None and delivery <= deadline
        include = bool(in_window or overdue_waiting or high_tasks or pending_confirmation or recent_high_message or logistics_exception)
        if not include:
            continue
        reasons: list[str] = []
        if in_window:
            reasons.append(f"客户交期在未来{due_days}天内")
        if supplier_commitment and supplier_commitment < current and float(order.get("current_progress") or 0) < 1:
            reasons.append("供应商完工承诺已过期")
        if overdue_waiting:
            reasons.append(f"{overdue_waiting}项等待承诺已超时")
        if high_tasks:
            reasons.append(f"{high_tasks}项高风险开放任务")
        if pending_confirmation:
            reasons.append(f"{pending_confirmation}项待确认事项")
        if recent_high_message:
            reasons.append("过去24小时收到高风险消息")
        if logistics_exception:
            reasons.append(f"{logistics_exception}项物流异常")
        candidates.append({
            "order_id": order_id,
            "order_no": order.get("order_no"),
            "customer_name": order.get("customer_name"),
            "owner": order.get("owner"),
            "requested_delivery_date": order.get("requested_delivery_date"),
            "current_node": order.get("current_node"),
            "current_progress": order.get("current_progress"),
            "candidate_reasons": reasons,
            "screening_flags": {
                "in_delivery_window": in_window,
                "overdue_waiting_count": overdue_waiting,
                "high_open_task_count": high_tasks,
                "pending_confirmation_count": pending_confirmation,
                "recent_high_message_count": recent_high_message,
                "logistics_exception_count": logistics_exception,
            },
        })
    candidates.sort(key=lambda x: (
        -x["screening_flags"]["logistics_exception_count"],
        -x["screening_flags"]["overdue_waiting_count"],
        -x["screening_flags"]["high_open_task_count"],
        str(x.get("requested_delivery_date") or "9999-12-31"),
    ))
    return {"scope": a, "due_within_days": due_days, "count": len(candidates[:limit]), "items": candidates[:limit]}


def order_context_logic(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    a = actor(payload)
    order_id = str(payload.get("order_id") or "").strip()
    if not order_id:
        raise HTTPException(422, "缺少order_id")
    order = _assert_order_access(conn, order_id, a)
    event_limit = max(1, min(int(payload.get("event_limit") or 20), 100))
    messages = [dict(r) for r in conn.execute(
        "SELECT * FROM source_messages WHERE order_id=? ORDER BY COALESCE(source_time,created_at) DESC LIMIT ?", (order_id, event_limit)
    )]
    tasks = [dict(r) for r in conn.execute(
        "SELECT * FROM tasks WHERE related_order_id=? AND status!='DONE' ORDER BY updated_at DESC", (order_id,)
    )]
    commitments = [dict(r) for r in conn.execute(
        "SELECT * FROM commitment_history WHERE order_id=? ORDER BY created_at DESC LIMIT 30", (order_id,)
    )]
    dependencies = [dict(r) for r in conn.execute(
        "SELECT * FROM order_dependencies WHERE order_id=? ORDER BY sequence_no,updated_at DESC", (order_id,)
    )]
    logistics = [dict(r) for r in conn.execute(
        "SELECT * FROM logistics_events WHERE order_id=? ORDER BY COALESCE(event_time,created_at) DESC LIMIT 30", (order_id,)
    )]
    risks = [dict(r) for r in conn.execute(
        "SELECT * FROM risk_signals WHERE order_id=? AND status='OPEN' ORDER BY created_at DESC", (order_id,)
    )]
    for task in tasks:
        task["evidence"] = safe_json(task.pop("evidence_json", "[]"), [])
    return {
        "order": order,
        "recent_events": messages,
        "open_tasks": tasks,
        "commitments": commitments,
        "dependencies": dependencies,
        "logistics": logistics,
        "open_risks": risks,
        "evidence_summary": {
            "message_count": len(messages), "open_task_count": len(tasks), "commitment_count": len(commitments),
            "dependency_count": len(dependencies), "logistics_event_count": len(logistics), "risk_count": len(risks),
        },
    }


def _delivery_risk(order: dict[str, Any], current: datetime) -> tuple[bool, float, list[str], list[str]]:
    delivery = parse_dt(order.get("requested_delivery_date"))
    progress = float(order.get("current_progress") or 0)
    node = str(order.get("current_node") or "").strip()
    evidence: list[str] = []
    missing: list[str] = []
    if not delivery:
        return False, 0, evidence, ["客户正式交期"]
    days = (delivery.date() - current.date()).days
    score = 0.0
    if days < 0:
        score += 70
        evidence.append(f"客户正式交期已超期{abs(days)}天")
    elif days <= 3:
        score += 55
        evidence.append(f"距离客户正式交期仅{days}天")
    elif days <= 7:
        score += 38
        evidence.append(f"距离客户正式交期{days}天")
    elif days <= 14:
        score += 20
        evidence.append(f"客户正式交期在14天内（{days}天）")
    if order.get("current_progress") is None:
        missing.append("当前生产进度")
        score += 12
    elif progress < 0.5 and days <= 7:
        score += 28
        evidence.append(f"当前进度仅{round(progress*100)}%")
    elif progress < 0.8 and days <= 3:
        score += 22
        evidence.append(f"当前进度{round(progress*100)}%与临近交期不匹配")
    if not node:
        missing.append("当前节点")
        score += 8
    return score >= 40, score, evidence, missing


def build_anomaly_logic(conn: sqlite3.Connection, payload: dict[str, Any], persist: bool = True) -> dict[str, Any]:
    a = actor(payload)
    order_id = str(payload.get("order_id") or "").strip()
    order = _assert_order_access(conn, order_id, a)
    current = parse_dt(payload.get("current_time")) or now_cn()
    requested_types = payload.get("anomaly_types") or list(ANOMALY_TYPES)
    if isinstance(requested_types, str):
        requested_types = [requested_types]
    requested_types = {x for x in requested_types if x in ANOMALY_TYPES}
    ctx = order_context_logic(conn, {**payload, "order_id": order_id})
    candidates: list[dict[str, Any]] = []

    # 1) Supplier commitment overdue.
    if "SUPPLIER_COMMITMENT_OVERDUE" in requested_types:
        commitment = parse_dt(order.get("latest_supplier_commitment"))
        overdue_tasks = [t for t in ctx["open_tasks"] if parse_dt(t.get("promised_reply_at")) and parse_dt(t.get("promised_reply_at")) < current]
        if (commitment and commitment < current and float(order.get("current_progress") or 0) < 1) or overdue_tasks:
            evidence = []
            score = 60.0
            if commitment and commitment < current:
                evidence.append(f"供应商完工承诺{order.get('latest_supplier_commitment')}已过期")
                score += min(25, max(1, (current.date()-commitment.date()).days)*5)
            for task in overdue_tasks[:3]:
                evidence.append(f"等待事项“{task.get('title')}”的承诺回复时间已过")
                score += 8
            candidates.append({
                "anomaly_type": "SUPPLIER_COMMITMENT_OVERDUE", "severity": "HIGH" if score < 85 else "CRITICAL",
                "confidence": 0.92 if evidence else 0.75, "score": min(score, 100), "evidence": evidence,
                "missing_information": [] if order.get("current_progress") is not None else ["当前生产进度"],
                "recommended_action": "立即确认工厂实际进度、明确完工时间与补救方案",
                "approval_required": True,
            })

    # 2) Customer confirmation blocking.
    if "CUSTOMER_CONFIRMATION_BLOCKING" in requested_types:
        pending = [t for t in ctx["open_tasks"] if int(t.get("pending_confirmation") or 0) == 1 or str(t.get("target") or "") == "customer" and "确认" in str(t.get("title") or "")]
        blocked_deps = [d for d in ctx["dependencies"] if d.get("status") in {"BLOCKED", "WAITING_CONFIRMATION"} and d.get("blocking_party") == "customer"]
        if pending or blocked_deps:
            evidence = [f"待确认任务：{t.get('title')}" for t in pending[:3]] + [f"客户确认阻塞依赖：{d.get('dependency_name')}" for d in blocked_deps[:3]]
            score = 48 + len(pending)*8 + len(blocked_deps)*10
            candidates.append({
                "anomaly_type": "CUSTOMER_CONFIRMATION_BLOCKING", "severity": "HIGH" if score >= 65 else "MEDIUM",
                "confidence": 0.9, "score": min(score, 100), "evidence": evidence,
                "missing_information": [], "recommended_action": "联系客户确认阻塞事项，并同步其对生产或交期的影响",
                "approval_required": True,
            })

    # 3) Delivery risk.
    if "DELIVERY_RISK" in requested_types:
        found, score, evidence, missing = _delivery_risk(order, current)
        unresolved_dependencies = [d for d in ctx["dependencies"] if d.get("status") not in {"DONE", "COMPLETED", "NOT_REQUIRED"}]
        if unresolved_dependencies:
            score += min(25, len(unresolved_dependencies)*5)
            evidence.extend(f"未完成前置事项：{d.get('dependency_name')}" for d in unresolved_dependencies[:3])
        if found or score >= 40:
            candidates.append({
                "anomaly_type": "DELIVERY_RISK", "severity": "CRITICAL" if score >= 85 else "HIGH" if score >= 60 else "MEDIUM",
                "confidence": 0.86 if not missing else 0.68, "score": min(score, 100), "evidence": evidence,
                "missing_information": missing, "recommended_action": "核对生产、验货和出运节点，确认是否需要补救或升级",
                "approval_required": True,
            })

    # 4) Logistics exception.
    if "LOGISTICS_EXCEPTION" in requested_types:
        bad = [e for e in ctx["logistics"] if e.get("status") in {"DELAYED", "EXCEPTION", "CUSTOMS_HOLD"} and not e.get("resolved_at")]
        if bad:
            evidence = [f"{e.get('event_type') or '物流事件'}：{e.get('description') or e.get('status')}" for e in bad[:4]]
            score = min(100, 65 + len(bad)*8)
            candidates.append({
                "anomaly_type": "LOGISTICS_EXCEPTION", "severity": "CRITICAL" if score >= 85 else "HIGH",
                "confidence": 0.94, "score": score, "evidence": evidence,
                "missing_information": [] if any(e.get("estimated_arrival_at") for e in bad) else ["最新预计到达时间"],
                "recommended_action": "向货代确认最新节点、预计到达时间与可选补救方案",
                "approval_required": True,
            })

    # 5) Information gap only when no concrete anomaly and key facts are missing.
    if not candidates and "INFORMATION_GAP" in requested_types:
        missing = []
        if not order.get("current_node"): missing.append("当前节点")
        if order.get("current_progress") is None: missing.append("当前进度")
        if not ctx["recent_events"]: missing.append("最近客户或工厂沟通")
        if missing:
            candidates.append({
                "anomaly_type": "INFORMATION_GAP", "severity": "LOW", "confidence": 0.98, "score": 20,
                "evidence": ["现有数据不足以形成可靠异常结论"], "missing_information": missing,
                "recommended_action": "向跟单人员追问缺失信息后再诊断", "approval_required": False,
            })

    persisted: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_id = new_id("ANOM")
        record = {
            **candidate, "candidate_id": candidate_id, "order_id": order_id, "order_no": order.get("order_no"),
            "customer_name": order.get("customer_name"), "owner_user_id": order.get("owner"),
            "status": "ANOMALY_CANDIDATE", "created_at": iso(current),
        }
        if persist:
            conn.execute(
                """INSERT INTO anomaly_candidates(candidate_id,run_id,order_id,anomaly_type,severity,confidence,score,evidence_json,
                   missing_information_json,recommended_action,status,created_by,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (candidate_id, payload.get("run_id"), order_id, candidate["anomaly_type"], candidate["severity"],
                 candidate["confidence"], candidate["score"], json.dumps(candidate["evidence"], ensure_ascii=False),
                 json.dumps(candidate["missing_information"], ensure_ascii=False), candidate["recommended_action"],
                 "ANOMALY_CANDIDATE", a["current_user_id"], iso(current), iso(current)),
            )
        persisted.append(record)
    return {"order_id": order_id, "count": len(persisted), "items": persisted, "requires_human_confirmation": True}


def rank_candidates_logic(items: list[dict[str, Any]], top_n: int = 7) -> list[dict[str, Any]]:
    severity_weight = {"CRITICAL": 40, "HIGH": 25, "MEDIUM": 12, "LOW": 3}
    ranked = []
    for item in items:
        score = float(item.get("score") or 0)
        score += severity_weight.get(str(item.get("severity") or "").upper(), 0)
        missing_count = len(item.get("missing_information") or [])
        score -= min(15, missing_count * 4)
        ranked.append({**item, "priority_score": round(score, 2)})
    ranked.sort(key=lambda x: (-x["priority_score"], str(x.get("order_no") or ""), str(x.get("anomaly_type") or "")))
    for i, item in enumerate(ranked[:top_n], 1):
        item["rank"] = i
        item["priority_reasons"] = list(item.get("evidence") or [])[:3] or ["基于确定性异常规则排序"]
    return ranked[:top_n]


def create_approval_logic(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    a = actor(payload)
    action_type = str(payload.get("action_type") or "").strip().upper()
    allowed = {"CONFIRM_ANOMALY", "CREATE_TASK", "RECORD_CONTACT", "UPDATE_ORDER", "SEND_MESSAGE", "ACCEPT_DELAY", "HIGH_RISK_OVERRIDE"}
    if action_type not in allowed:
        raise HTTPException(422, f"不支持的审批动作：{action_type}")
    order_id = str(payload.get("order_id") or "").strip() or None
    if order_id:
        _assert_order_access(conn, order_id, a)
    high_risk = action_type in {"UPDATE_ORDER", "ACCEPT_DELAY", "HIGH_RISK_OVERRIDE"} or bool(payload.get("high_risk"))
    required_role = "manager" if high_risk else "operator_or_manager"
    approval_id = new_id("APR")
    idempotency_key = str(payload.get("idempotency_key") or f"{action_type}:{order_id}:{payload.get('candidate_id')}:{payload.get('task_draft_id')}")
    existing = conn.execute("SELECT * FROM approval_requests WHERE idempotency_key=?", (idempotency_key,)).fetchone()
    if existing:
        return {**dict(existing), "duplicate_skipped": True, "payload": safe_json(existing["payload_json"], {})}
    conn.execute(
        """INSERT INTO approval_requests(approval_id,run_id,candidate_id,order_id,action_type,payload_json,status,
           requested_by,required_role,idempotency_key,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (approval_id, payload.get("run_id"), payload.get("candidate_id"), order_id, action_type,
         json.dumps(payload.get("action_payload") or {}, ensure_ascii=False), "PENDING", a["current_user_id"],
         required_role, idempotency_key, iso(), iso()),
    )
    audit_event(conn, "approval", approval_id, "AGENT_APPROVAL_REQUESTED", payload, a["current_user_id"])
    return {"approval_id": approval_id, "status": "PENDING", "required_role": required_role,
            "message": "已创建人工审批请求，尚未执行正式写操作"}


def _commit_approved_action(conn: sqlite3.Connection, approval: dict[str, Any], operator_id: str) -> dict[str, Any]:
    payload = safe_json(approval.get("payload_json"), {})
    action_type = approval["action_type"]
    now = iso()
    if action_type == "CONFIRM_ANOMALY":
        candidate_id = approval.get("candidate_id") or payload.get("candidate_id")
        conn.execute("UPDATE anomaly_candidates SET status='CONFIRMED',confirmed_by=?,confirmed_at=?,updated_at=? WHERE candidate_id=?",
                     (operator_id, now, now, candidate_id))
        return {"candidate_id": candidate_id, "status": "CONFIRMED"}
    if action_type == "CREATE_TASK":
        order_id = approval.get("order_id") or payload.get("order_id")
        task_id = str(payload.get("task_id") or new_id("TASK"))
        requested_owner = normalize_owner(payload.get("owner_user_id") or operator_id)
        order_row = conn.execute("SELECT owner FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if order_row and requested_owner != normalize_owner(order_row["owner"]) and not is_manager(operator_id):
            raise HTTPException(403, "普通跟单人员不能跨负责人分派任务")
        conn.execute(
            """INSERT INTO tasks(task_id,related_order_id,title,recommended_action,target,status,owner_user_id,responsibility_status,
               waiting_on,promised_reply_at,next_action_at,business_deadline,last_contact_at,risk_level,urgent,pending_confirmation,
               source_message_id,evidence_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, order_id, payload.get("title") or "处理订单异常", payload.get("recommended_action") or payload.get("title") or "处理订单异常",
             payload.get("target") or "internal", "OPEN", requested_owner, "assigned", None, None,
             payload.get("next_action_at"), payload.get("business_deadline"), None, payload.get("risk_level") or "medium",
             int(bool(payload.get("urgent"))), 0, None, json.dumps(payload.get("evidence") or [], ensure_ascii=False), now, now),
        )
        return {"task_id": task_id, "status": "OPEN"}
    if action_type == "RECORD_CONTACT":
        task_id = payload.get("task_id")
        if not task_id:
            raise HTTPException(422, "记录联系需要task_id")
        conn.execute(
            "UPDATE tasks SET status='WAITING_EXTERNAL',last_contact_at=?,waiting_on=?,promised_reply_at=?,next_action_at=?,updated_at=? WHERE task_id=?",
            (now, payload.get("waiting_on"), payload.get("promised_reply_at"), payload.get("promised_reply_at"), now, task_id),
        )
        return {"task_id": task_id, "status": "WAITING_EXTERNAL"}
    if action_type == "UPDATE_ORDER":
        order_id = approval.get("order_id") or payload.get("order_id")
        allowed_fields = {"current_progress", "current_node", "latest_supplier_commitment", "requested_delivery_date", "packaging_method"}
        updates = {k: v for k, v in (payload.get("updates") or {}).items() if k in allowed_fields}
        if not updates:
            raise HTTPException(422, "没有可写入的订单字段")
        set_sql = ",".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE orders SET {set_sql},updated_at=? WHERE order_id=?", (*updates.values(), now, order_id))
        return {"order_id": order_id, "updated_fields": list(updates)}
    # Sending external messages is intentionally not implemented in V1.
    if action_type == "SEND_MESSAGE":
        return {"status": "APPROVED_NOT_SENT", "message": "首版不自动发送，仅记录人工审批结果"}
    if action_type in {"ACCEPT_DELAY", "HIGH_RISK_OVERRIDE"}:
        return {"status": "APPROVED_RECORDED", "message": "高风险决定已记录；后续正式字段修改仍需单独UPDATE_ORDER审批"}
    raise HTTPException(422, "审批动作尚未实现")


def run_inspection_logic(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    a = actor(payload)
    run_id = str(payload.get("run_id") or new_id("AGR"))
    trigger_type = str(payload.get("trigger_type") or "MANUAL").upper()
    started = time.perf_counter()
    conn.execute(
        """INSERT OR IGNORE INTO agent_runs(run_id,organization_id,current_user_id,current_role,goal,trigger_type,status,
           max_tool_calls,max_duration_seconds,started_at,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, a["organization_id"], a["current_user_id"], a["current_role"],
         payload.get("goal") or "检查今天最需要处理的订单", trigger_type, "RUNNING", 8, 60, iso(), iso()),
    )
    screened = list_candidate_orders_logic(conn, {**payload, **a, "limit": 50})
    all_candidates: list[dict[str, Any]] = []
    for order in screened["items"]:
        result = build_anomaly_logic(conn, {**payload, **a, "run_id": run_id, "order_id": order["order_id"]}, persist=True)
        all_candidates.extend(result["items"])
    ranked = rank_candidates_logic(all_candidates, max(1, min(int(payload.get("top_n") or 7), 20)))
    report_id = new_id("RPT")
    report = {
        "report_id": report_id, "run_id": run_id, "generated_at": iso(), "scope": screened["scope"],
        "screened_order_count": screened["count"], "anomaly_candidate_count": len(all_candidates),
        "top_items": ranked, "summary": {
            "critical": sum(1 for x in ranked if x.get("severity") == "CRITICAL"),
            "high": sum(1 for x in ranked if x.get("severity") == "HIGH"),
            "needs_information": sum(1 for x in ranked if x.get("anomaly_type") == "INFORMATION_GAP"),
        },
        "human_confirmation_required": True,
    }
    duration_ms = int((time.perf_counter() - started) * 1000)
    conn.execute(
        """INSERT INTO daily_inspection_reports(report_id,run_id,organization_id,current_user_id,inspection_date,timezone,
           scope_json,report_json,status,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (report_id, run_id, a["organization_id"], a["current_user_id"], now_cn().date().isoformat(), "Asia/Shanghai",
         json.dumps(screened["scope"], ensure_ascii=False), json.dumps(report, ensure_ascii=False), "COMPLETED", iso()),
    )
    conn.execute(
        "UPDATE agent_runs SET status='COMPLETED',result_json=?,stop_reason='DIAGNOSIS_COMPLETED',duration_ms=?,completed_at=? WHERE run_id=?",
        (json.dumps(report, ensure_ascii=False), duration_ms, iso(), run_id),
    )
    audit_event(conn, "agent_run", run_id, "AGENT_INSPECTION_COMPLETED", report, a["current_user_id"])
    track_event(
        conn, "priority_diagnosis_completed", organization_id=a["organization_id"], user_id=a["current_user_id"],
        user_role=a["current_role"], run_id=run_id, source="website_inspection",
        properties={"screened_order_count": screened["count"], "anomaly_candidate_count": len(all_candidates),
                    "top_count": len(ranked), "duration_ms": duration_ms},
    )
    track_event(
        conn, "agent_run_completed", organization_id=a["organization_id"], user_id=a["current_user_id"],
        user_role=a["current_role"], run_id=run_id, source="website_inspection",
        properties={"status": "COMPLETED", "stop_reason": "DIAGNOSIS_COMPLETED", "duration_ms": duration_ms,
                    "final_response_generated": True},
    )
    return report


def register_agent_api(app) -> None:
    router = APIRouter()

    @router.get("/api/agent/status")
    def agent_status() -> dict[str, Any]:
        return {
            "version": "6.1.1",
            "agent_name": "FlowOrder订单异常诊断Agent",
            "tool_auth_configured": bool(AGENT_API_KEY),
            "cron_auth_configured": bool(CRON_API_KEY or AGENT_API_KEY),
            "coze_agent_configured": coze_agent_status()["configured"],
            "daily_schedule": {"timezone": "Asia/Shanghai", "time": "08:30", "days": "DAILY"},
            "limits": {"max_tool_calls": 8, "max_duration_seconds": 60, "top_n": 7},
            "composite_tools": ["parse_bulk_order_updates", "diagnose_priority_orders"],
            "analytics_enabled": True,
        }

    # ---- Coze plugin tools (API-key protected) ----
    @router.post("/api/agent/tools/runs/start")
    def tool_start_run(payload: AnyPayload, x_floworder_agent_key: str | None = Header(None)) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump()
        a = actor(body)
        run_id = new_id("AGR")
        with db() as conn:
            conn.execute(
                """INSERT INTO agent_runs(run_id,organization_id,current_user_id,current_role,goal,trigger_type,status,max_tool_calls,
                   max_duration_seconds,started_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (run_id, a["organization_id"], a["current_user_id"], a["current_role"], body.get("goal") or "订单异常诊断",
                 str(body.get("trigger_type") or "USER").upper(), "RUNNING", 8, 60, iso(), iso()),
            )
            track_event(
                conn, "agent_run_started", organization_id=a["organization_id"], user_id=a["current_user_id"],
                user_role=a["current_role"], run_id=run_id, source="agent_tool",
                properties={"goal": str(body.get("goal") or "订单异常诊断")[:120], "trigger_type": str(body.get("trigger_type") or "USER").upper()},
            )
            conn.commit()
        return {"run_id": run_id, "status": "RUNNING", "max_tool_calls": 8, "max_duration_seconds": 60}

    @router.post("/api/agent/tools/candidate-orders/list")
    def tool_list_candidate_orders(payload: AnyPayload, x_floworder_agent_key: str | None = Header(None)) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump(); started = time.perf_counter()
        with db() as conn:
            enforce_run_budget(conn, body.get("run_id"))
            result = list_candidate_orders_logic(conn, body)
            log_tool_call(conn, run_id=body.get("run_id"), tool_name="list_candidate_orders", request=body, response=result,
                          status="SUCCESS", duration_ms=int((time.perf_counter()-started)*1000))
            conn.commit()
        return result

    @router.post("/api/agent/tools/orders/context")
    def tool_get_order_context(payload: AnyPayload, x_floworder_agent_key: str | None = Header(None)) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump(); started = time.perf_counter()
        with db() as conn:
            enforce_run_budget(conn, body.get("run_id"))
            result = order_context_logic(conn, body)
            log_tool_call(conn, run_id=body.get("run_id"), tool_name="get_order_diagnostic_context", request=body,
                          response={"order_id": body.get("order_id"), "evidence_summary": result["evidence_summary"]},
                          status="SUCCESS", duration_ms=int((time.perf_counter()-started)*1000))
            conn.commit()
        return result

    @router.post("/api/agent/tools/anomalies/build")
    def tool_build_anomaly(payload: AnyPayload, x_floworder_agent_key: str | None = Header(None)) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump(); started = time.perf_counter()
        with db() as conn:
            enforce_run_budget(conn, body.get("run_id"))
            result = build_anomaly_logic(conn, body, persist=True)
            log_tool_call(conn, run_id=body.get("run_id"), tool_name="build_anomaly_candidate", request=body,
                          response={"count": result["count"], "candidate_ids": [x["candidate_id"] for x in result["items"]]},
                          status="SUCCESS", duration_ms=int((time.perf_counter()-started)*1000))
            conn.commit()
        return result

    @router.post("/api/agent/tools/anomalies/rank")
    def tool_rank_anomalies(payload: AnyPayload, x_floworder_agent_key: str | None = Header(None)) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump(); items = body.get("items") or []
        if not isinstance(items, list): raise HTTPException(422, "items必须是数组")
        ranked = rank_candidates_logic(items, max(1, min(int(body.get("top_n") or 7), 20)))
        with db() as conn:
            enforce_run_budget(conn, body.get("run_id"))
            log_tool_call(conn, run_id=body.get("run_id"), tool_name="rank_anomaly_candidates", request={"count": len(items)},
                          response={"count": len(ranked)}, status="SUCCESS", duration_ms=0); conn.commit()
        return {"count": len(ranked), "items": ranked}

    @router.post("/api/agent/tools/task-drafts/create")
    def tool_create_task_draft(payload: AnyPayload, x_floworder_agent_key: str | None = Header(None)) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump(); a = actor(body)
        with db() as conn:
            enforce_run_budget(conn, body.get("run_id"))
            order = _assert_order_access(conn, str(body.get("order_id") or ""), a)
            draft_id = new_id("TDRAFT")
            task = {
                "task_draft_id": draft_id, "order_id": order["order_id"], "title": body.get("title") or "处理订单异常",
                "recommended_action": body.get("recommended_action") or body.get("title") or "处理订单异常",
                "target": body.get("target") or "internal", "owner_user_id": body.get("owner_user_id") or order.get("owner") or a["current_user_id"],
                "risk_level": body.get("risk_level") or "medium", "business_deadline": body.get("business_deadline"),
                "evidence": body.get("evidence") or [], "status": "DRAFT", "requires_approval": True,
            }
            audit_event(conn, "task_draft", draft_id, "AGENT_TASK_DRAFT_CREATED", task, a["current_user_id"])
            track_event(
                conn, "task_draft_created", organization_id=a["organization_id"], user_id=a["current_user_id"],
                user_role=a["current_role"], order_id=order["order_id"], run_id=body.get("run_id"), source="agent_tool",
                properties={"task_draft_id": draft_id, "risk_level": task["risk_level"], "requires_approval": True},
            )
            conn.commit()
        return task

    @router.post("/api/agent/tools/message-drafts/create")
    def tool_create_message_draft(payload: AnyPayload, x_floworder_agent_key: str | None = Header(None)) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump(); a = actor(body)
        with db() as conn:
            enforce_run_budget(conn, body.get("run_id"))
            ctx = order_context_logic(conn, body)
            order = ctx["order"]
            recipient = str(body.get("recipient_role") or "supplier")
            questions = body.get("questions_to_ask") or []
            if isinstance(questions, str): questions = [questions]
            facts = [f"订单{order.get('order_no')}"]
            if order.get("requested_delivery_date"): facts.append(f"客户正式交期为{order.get('requested_delivery_date')}")
            if order.get("latest_supplier_commitment"): facts.append(f"工厂当前承诺为{order.get('latest_supplier_commitment')}")
            if order.get("current_progress") is not None: facts.append(f"当前记录进度为{round(float(order.get('current_progress'))*100)}%")
            if recipient in {"supplier", "factory"}:
                body_text = f"您好，关于{order.get('order_no')}，请协助确认当前准确进度、明确完工时间"
                if questions: body_text += "，并回复" + "、".join(str(x) for x in questions[:4])
                body_text += "。如存在风险，请同时说明原因、补救方案和负责人。谢谢。"
                subject = f"请确认{order.get('order_no')}当前进度与完工安排"
            else:
                body_text = f"您好，关于{order.get('order_no')}，我们正在核对最新进展。"
                if questions: body_text += "烦请确认" + "、".join(str(x) for x in questions[:4]) + "。"
                body_text += "确认后我们会及时同步对后续安排的影响。"
                subject = f"关于{order.get('order_no')}待确认事项"
            draft_id = new_id("ADRAFT")
            draft = {"draft_id": draft_id, "subject": subject, "draft": body_text, "fact_ids_used": facts,
                     "questions_to_ask": questions, "status": "DRAFT", "requires_human_review": True, "auto_send": False}
            audit_event(conn, "agent_draft", draft_id, "AGENT_MESSAGE_DRAFT_CREATED", draft, a["current_user_id"]); conn.commit()
        return draft

    @router.post("/api/agent/tools/approvals/create")
    def tool_create_approval(payload: AnyPayload, x_floworder_agent_key: str | None = Header(None)) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        with db() as conn:
            body = payload.model_dump()
            enforce_run_budget(conn, body.get("run_id"))
            result = create_approval_logic(conn, body)
            a = actor(body)
            track_event(
                conn, "approval_created", organization_id=a["organization_id"], user_id=a["current_user_id"],
                user_role=a["current_role"], order_id=body.get("order_id"), run_id=body.get("run_id"), source="agent_tool",
                properties={"approval_id": result.get("approval_id"), "action_type": body.get("action_type"),
                            "required_role": result.get("required_role"), "duplicate_skipped": bool(result.get("duplicate_skipped"))},
            )
            conn.commit()
        return result

    @router.post("/api/agent/tools/approvals/status")
    def tool_approval_status(payload: AnyPayload, x_floworder_agent_key: str | None = Header(None)) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        approval_id = str(payload.model_dump().get("approval_id") or "")
        with db() as conn:
            enforce_run_budget(conn, payload.model_dump().get("run_id"))
            row = conn.execute("SELECT * FROM approval_requests WHERE approval_id=?", (approval_id,)).fetchone()
        if not row: raise HTTPException(404, "审批请求不存在")
        result = dict(row); result["payload"] = safe_json(result.pop("payload_json"), {})
        return result

    @router.post("/api/agent/tools/runs/complete")
    def tool_complete_run(payload: AnyPayload, x_floworder_agent_key: str | None = Header(None)) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump(); run_id = str(body.get("run_id") or "")
        with db() as conn:
            run = conn.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
            status = body.get("status") or "COMPLETED"
            completed_at = iso()
            conn.execute("UPDATE agent_runs SET status=?,result_json=?,stop_reason=?,completed_at=? WHERE run_id=?",
                         (status, json.dumps(body.get("result") or {}, ensure_ascii=False),
                          body.get("stop_reason") or "AGENT_COMPLETED", completed_at, run_id))
            if run:
                started_at = parse_dt(run["started_at"] or run["created_at"])
                duration_ms = int(max(0, (now_cn() - started_at).total_seconds()) * 1000) if started_at else None
                track_event(
                    conn, "agent_run_completed", organization_id=run["organization_id"], user_id=run["current_user_id"],
                    user_role=run["current_role"], run_id=run_id, source="agent_tool",
                    properties={"status": status, "stop_reason": body.get("stop_reason") or "AGENT_COMPLETED",
                                "duration_ms": duration_ms, "final_response_generated": True},
                )
            conn.commit()
        return {"run_id": run_id, "status": status}

    # ---- FlowOrder website / human approval endpoints ----
    @router.get("/api/agent/overview")
    def agent_overview(current_user_id: str = Query("USER-1"), current_role: str = Query("operator")) -> dict[str, Any]:
        a = actor({"current_user_id": current_user_id, "current_role": current_role})
        with db() as conn:
            scope_sql, params = _order_scope_sql(a)
            cands = [dict(r) for r in conn.execute(
                f"""SELECT a.*,o.order_no,o.customer_name,o.owner FROM anomaly_candidates a JOIN orders o ON o.order_id=a.order_id
                    WHERE {scope_sql.replace('owner','o.owner')} ORDER BY a.created_at DESC LIMIT 100""", params)]
            approvals = [dict(r) for r in conn.execute(
                f"""SELECT p.*,o.order_no,o.customer_name FROM approval_requests p LEFT JOIN orders o ON o.order_id=p.order_id
                    WHERE ({scope_sql.replace('owner','o.owner')}) OR p.requested_by=? ORDER BY p.created_at DESC LIMIT 50""", [*params, current_user_id])]
            reports = [dict(r) for r in conn.execute(
                "SELECT * FROM daily_inspection_reports WHERE current_user_id=? ORDER BY created_at DESC LIMIT 10", (current_user_id,))]
        for item in cands:
            item["evidence"] = safe_json(item.pop("evidence_json"), [])
            item["missing_information"] = safe_json(item.pop("missing_information_json"), [])
        for item in approvals:
            item["payload"] = safe_json(item.pop("payload_json"), {})
        for item in reports:
            item["report"] = safe_json(item.pop("report_json"), {})
            item["scope"] = safe_json(item.pop("scope_json"), {})
        return {
            "summary": {
                "candidate_count": sum(1 for x in cands if x["status"] in {"ANOMALY_CANDIDATE", "PENDING_CONFIRMATION"}),
                "critical_count": sum(1 for x in cands if x["severity"] == "CRITICAL" and x["status"] != "RESOLVED"),
                "pending_approval_count": sum(1 for x in approvals if x["status"] == "PENDING"),
                "report_count": len(reports),
            }, "candidates": cands, "approvals": approvals, "reports": reports,
        }

    @router.post("/api/agent/chat")
    def agent_chat(payload: AnyPayload) -> dict[str, Any]:
        body = payload.model_dump()
        question = str(body.get("question") or "").strip()
        if not question:
            raise HTTPException(422, "问题不能为空")
        user_id = str(body.get("current_user_id") or "USER-1")
        role = str(body.get("current_role") or ("manager" if user_id in MANAGER_IDS else "operator"))
        parameters = {
            "organization_id": str(body.get("organization_id") or "ORG-DEMO"),
            "current_user_id": user_id,
            "current_role": role,
            "allowed_owner_ids": body.get("allowed_owner_ids") or [user_id],
            "default_due_within_days": 14,
            "default_top_n": 7,
        }
        try:
            result = run_agent_chat(user_id=user_id, question=question, parameters=parameters,
                                    conversation_id=body.get("conversation_id"), timeout_seconds=90)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        return result

    @router.post("/api/agent/inspection/run")
    def run_inspection(payload: AnyPayload) -> dict[str, Any]:
        body = payload.model_dump()
        with db() as conn:
            result = run_inspection_logic(conn, body); conn.commit()
        return result

    @router.post("/api/agent/inspection/scheduled")
    def run_scheduled_inspection(payload: AnyPayload, x_floworder_cron_key: str | None = Header(None)) -> dict[str, Any]:
        _require_cron_key(x_floworder_cron_key)
        body = payload.model_dump(); user_ids = body.get("user_ids") or ["USER-1", "USER-2", "USER-3"]
        results = []
        with db() as conn:
            for user_id in user_ids:
                results.append(run_inspection_logic(conn, {**body, "current_user_id": user_id, "current_role": "operator", "trigger_type": "SCHEDULED"}))
            conn.commit()
        return {"status": "COMPLETED", "inspection_time": iso(), "results": results}

    @router.post("/api/agent/candidates/{candidate_id}/decision")
    def decide_candidate(candidate_id: str, payload: AnyPayload) -> dict[str, Any]:
        body = payload.model_dump(); operator_id = str(body.get("operator_id") or "USER-1")
        decision = str(body.get("decision") or "").upper()
        if decision not in {"CONFIRM", "REJECT", "RESOLVE"}: raise HTTPException(422, "decision必须为CONFIRM/REJECT/RESOLVE")
        new_status = {"CONFIRM":"CONFIRMED", "REJECT":"REJECTED", "RESOLVE":"RESOLVED"}[decision]
        with db() as conn:
            row = conn.execute("SELECT a.*,o.owner FROM anomaly_candidates a JOIN orders o ON o.order_id=a.order_id WHERE candidate_id=?", (candidate_id,)).fetchone()
            if not row: raise HTTPException(404, "异常候选不存在")
            if not owner_allowed(row["owner"], operator_id, body.get("current_role")): raise HTTPException(403, "无权处理该异常候选")
            conn.execute("UPDATE anomaly_candidates SET status=?,confirmed_by=?,confirmed_at=?,resolution_note=?,updated_at=? WHERE candidate_id=?",
                         (new_status, operator_id if decision=="CONFIRM" else None, iso() if decision=="CONFIRM" else None,
                          body.get("note"), iso(), candidate_id))
            audit_event(conn, "anomaly_candidate", candidate_id, f"ANOMALY_{new_status}", body, operator_id); conn.commit()
        return {"candidate_id": candidate_id, "status": new_status}

    @router.post("/api/agent/approvals/{approval_id}/decision")
    def decide_approval(approval_id: str, payload: AnyPayload) -> dict[str, Any]:
        body = payload.model_dump(); operator_id = str(body.get("operator_id") or "USER-1")
        decision = str(body.get("decision") or "").upper()
        if decision not in {"APPROVE", "REJECT"}: raise HTTPException(422, "decision必须为APPROVE或REJECT")
        with db() as conn:
            enforce_run_budget(conn, payload.model_dump().get("run_id"))
            row = conn.execute("SELECT * FROM approval_requests WHERE approval_id=?", (approval_id,)).fetchone()
            if not row: raise HTTPException(404, "审批请求不存在")
            approval = dict(row)
            if approval["status"] != "PENDING": return {"approval_id": approval_id, "status": approval["status"], "duplicate_skipped": True}
            if approval.get("order_id"):
                _assert_order_access(conn, approval["order_id"], actor({"current_user_id": operator_id, "current_role": body.get("current_role")}))
            if approval["required_role"] == "manager" and not is_manager(operator_id, body.get("current_role")):
                raise HTTPException(403, "该高风险动作需要主管审批")
            result = None
            if decision == "APPROVE": result = _commit_approved_action(conn, approval, operator_id)
            new_status = "APPROVED" if decision == "APPROVE" else "REJECTED"
            conn.execute("UPDATE approval_requests SET status=?,decided_by=?,decision_note=?,decided_at=?,result_json=?,updated_at=? WHERE approval_id=?",
                         (new_status, operator_id, body.get("note"), iso(), json.dumps(result or {}, ensure_ascii=False), iso(), approval_id))
            audit_event(conn, "approval", approval_id, f"AGENT_APPROVAL_{new_status}", body, operator_id)
            track_event(
                conn, "approval_decided", organization_id="ORG-DEMO", user_id=operator_id,
                user_role=str(body.get("current_role") or "operator"), order_id=approval.get("order_id"),
                run_id=approval.get("run_id"), source="website",
                properties={"approval_id": approval_id, "decision": decision, "status": new_status,
                            "action_type": approval.get("action_type")},
            )
            conn.commit()
        return {"approval_id": approval_id, "status": new_status, "result": result}

    @router.get("/api/agent/runs/{run_id}/trace")
    def get_run_trace(run_id: str, current_user_id: str = Query("USER-1"), current_role: str = Query("operator")) -> dict[str, Any]:
        with db() as conn:
            run = conn.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
            if not run: raise HTTPException(404, "Agent运行不存在")
            if not is_manager(current_user_id, current_role) and run["current_user_id"] != current_user_id:
                raise HTTPException(403, "无权查看该运行轨迹")
            calls = [dict(r) for r in conn.execute("SELECT * FROM agent_tool_calls WHERE run_id=? ORDER BY created_at", (run_id,))]
        result = dict(run); result["result"] = safe_json(result.pop("result_json"), {})
        for call in calls:
            call["request"] = safe_json(call.pop("request_json"), {})
            call["response"] = safe_json(call.pop("response_json"), {})
        return {"run": result, "tool_calls": calls}

    app.include_router(router)
