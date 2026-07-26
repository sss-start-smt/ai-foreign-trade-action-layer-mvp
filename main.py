from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "action_layer.db")))
API_KEY = os.getenv("APP_API_KEY", "demo-key")
CN_TZ = timezone(timedelta(hours=8))

app = FastAPI(title="AI外贸跟单行动层 MVP", version="1.0.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


class AnyPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def iso(dt: datetime | None = None) -> str:
    return (dt or now_cn()).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


@contextmanager
def db():
    """Create and always close a SQLite connection.

    sqlite3.Connection's built-in context manager commits or rolls back, but
    does not close the connection. Explicit closing prevents WinError 32 on
    Windows when pytest resets the test database.
    """
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))
        count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        if count == 0:
            reset_demo_data(conn)


def reset_demo_data(conn: sqlite3.Connection) -> None:
    for table in [
        "idempotency_records", "event_logs", "confirmation_snapshots",
        "commitment_history", "risk_signals", "tasks", "source_messages", "orders"
    ]:
        conn.execute(f"DELETE FROM {table}")

    now = now_cn()
    orders = [
        ("ORD-1001", "PO-1001", "Northwind Trading", "帆布包", "普通盒", "2026-08-20", None, 0.55, "生产中"),
        ("ORD-1002", "PO-1002", "Blue Harbor", "拉链袋", "普通包装", "2026-08-05", "2026-07-30", 0.70, "生产中"),
        ("ORD-1003", "PO-1003", "Green Field", "礼品盒", "彩盒", "2026-08-01", None, 0.30, "待确认"),
    ]
    for row in orders:
        conn.execute(
            """INSERT INTO orders(order_id,order_no,customer_name,product_name,packaging_method,
               requested_delivery_date,latest_supplier_commitment,current_progress,current_node,
               created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (*row, iso(now), iso(now)),
        )

    tasks = [
        (
            "TASK-WAIT-001", "ORD-1002", "等待工厂确认拉链到料时间", "等待工厂回复",
            "factory", "OPEN", "USER-1", "assigned", "factory",
            iso(now + timedelta(hours=3)), iso(now + timedelta(hours=3)),
            iso(now + timedelta(days=2)), iso(now - timedelta(hours=20)),
            "high", 0, 0, None, json.dumps(["工厂承诺3小时内回复"], ensure_ascii=False)
        ),
        (
            "TASK-CONFIRM-001", "ORD-1003", "确认客户新增Logo版本", "审核候选变化并确认",
            "customer", "OPEN", "USER-1", "assigned", None,
            None, None, iso(now + timedelta(days=1)), None,
            "medium", 0, 1, None, json.dumps(["客户发来新版本设计稿"], ensure_ascii=False)
        ),
        (
            "TASK-ESC-001", "ORD-1002", "处理客户取消订单风险", "请求主管介入",
            "manager", "OPEN", None, "unassigned", None,
            None, None, iso(now - timedelta(hours=12)), None,
            "critical", 1, 0, None, json.dumps(["客户表示若仍无明确答复将取消订单"], ensure_ascii=False)
        ),
        (
            "TASK-TODAY-001", "ORD-1003", "今天确认彩盒样品", "联系客户确认样品",
            "customer", "OPEN", "USER-1", "assigned", None,
            None, None, iso(now + timedelta(hours=6)), None,
            "medium", 0, 0, None, json.dumps(["样品确认截止今天"], ensure_ascii=False)
        ),
    ]
    for t in tasks:
        conn.execute(
            """INSERT INTO tasks(task_id,related_order_id,title,recommended_action,target,status,
               owner_user_id,responsibility_status,waiting_on,promised_reply_at,next_action_at,
               business_deadline,last_contact_at,risk_level,urgent,pending_confirmation,
               source_message_id,evidence_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*t, iso(now), iso(now)),
        )

    conn.execute(
        """INSERT INTO risk_signals(risk_id,order_id,task_id,risk_type,risk_level,evidence,rule_id,status,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        ("RISK-ESC-001", "ORD-1002", "TASK-ESC-001", "customer_cancellation", "critical",
         "客户表示若仍无明确答复将取消订单", "R_CUSTOMER_COMPLAINT", "OPEN", iso(now), iso(now)),
    )
    conn.commit()


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        value_dt = datetime.fromisoformat(text)
        if value_dt.tzinfo is None:
            value_dt = value_dt.replace(tzinfo=CN_TZ)
        return value_dt.astimezone(CN_TZ)
    except ValueError:
        return None


def next_workday_9(now: datetime) -> datetime:
    candidate = now + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate.replace(hour=9, minute=0, second=0, microsecond=0)


def decide_task(task: dict[str, Any], current: datetime, current_user_id: str) -> dict[str, Any]:
    due = parse_dt(task.get("business_deadline"))
    promise = parse_dt(task.get("promised_reply_at"))
    risk = str(task.get("risk_level") or "none").lower()
    urgent = bool(task.get("urgent")) or risk == "critical"
    weekend = current.weekday() >= 5
    hard = False
    suppressed = False
    next_action = task.get("next_action_at")
    reasons: list[str] = []

    if str(task.get("status") or "").upper() == "DONE":
        state, action = "DONE", "无需处理"
        reasons.append("任务已完成")
    elif task.get("responsibility_status") == "not_mine" or (
        task.get("owner_user_id")
        and current_user_id
        and task.get("owner_user_id") != current_user_id
        and task.get("responsibility_status") == "assigned"
    ):
        state, action = "NOT_MY_RESPONSIBILITY", "转交给正确负责人并记录"
        reasons.append("不属于当前用户责任")
    elif bool(task.get("pending_confirmation")):
        state, action = "NEEDS_CONFIRMATION", "审核候选变化并确认是否生效"
        reasons.append("存在待确认候选或高责任字段")
    elif (risk == "critical" and not task.get("owner_user_id")) or (
        risk == "critical" and due and (current - due).total_seconds() >= 8 * 3600
    ):
        state, action, hard = "ESCALATE", "请求主管介入并明确负责人", True
        reasons.append("重大事项无负责人" if not task.get("owner_user_id") else "重大事项已严重逾期")
    elif task.get("waiting_on") and promise and promise > current:
        state = "WAITING_EXTERNAL"
        action = f"等待{task.get('waiting_on')}回复"
        next_action = task.get("promised_reply_at")
        suppressed = True
        reasons.append("已完成当前动作，仍在对方承诺回复窗口内")
    elif task.get("waiting_on") and promise and promise <= current:
        state, action, hard = "DO_NOW", f"再次跟进{task.get('waiting_on')}", True
        reasons.append("已超过对方承诺回复时间")
    elif due and due <= current:
        state, action, hard = "DO_NOW", task.get("recommended_action") or "立即处理逾期事项", True
        reasons.append("业务截止时间已过")
    elif urgent:
        state, action, hard = "DO_NOW", task.get("recommended_action") or "立即人工处理紧急事项", True
        reasons.append("存在关键风险")
    elif due and (due - current).total_seconds() <= 12 * 3600:
        state, action = "DO_TODAY", task.get("recommended_action") or "今天完成"
        reasons.append("任务将在今天到期")
    else:
        state, action = "SCHEDULED", task.get("recommended_action") or "按计划处理"
        next_action = next_action or task.get("business_deadline")
        reasons.append("尚未进入立即处理窗口")

    if weekend and not urgent and not hard and state in {"DO_NOW", "DO_TODAY"}:
        state, action = "SCHEDULED", "安排在下一个工作日处理"
        next_action = iso(next_workday_9(current))
        reasons.append("周末且非紧急，不制造全天候回复压力")

    state_weight = {
        "ESCALATE": 1000, "DO_NOW": 900, "NEEDS_CONFIRMATION": 800,
        "DO_TODAY": 700, "SCHEDULED": 300, "WAITING_EXTERNAL": 100,
        "NOT_MY_RESPONSIBILITY": 0, "DONE": -1000,
    }
    risk_weight = {"critical": 300, "high": 180, "medium": 80, "low": 20, "none": 0}
    score = state_weight[state] + risk_weight.get(risk, 0)
    if due:
        remaining = (due - current).total_seconds() / 3600
        if remaining <= 0:
            score += 200
        elif remaining <= 24:
            score += 100
    if suppressed:
        score = -1000

    result = dict(task)
    result.update({
        "action_state": state,
        "recommended_action": action,
        "next_action_at": next_action,
        "ranking_suppressed": suppressed,
        "priority_score": score,
        "priority_reasons": reasons,
        "evidence": json.loads(task.get("evidence_json") or "[]"),
    })
    return result


def get_order_id(conn: sqlite3.Connection, wrapper: dict[str, Any], plan: dict[str, Any]) -> str | None:
    state = wrapper.get("existing_business_state_json") or wrapper.get("existing_business_state")
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except json.JSONDecodeError:
            state = {}
    state = state or {}
    return (
        wrapper.get("order_id")
        or state.get("order_id")
        or plan.get("order_id")
        or (plan.get("entity_context") or {}).get("order_id")
    )


def get_task_id(wrapper: dict[str, Any], plan: dict[str, Any]) -> str | None:
    state = wrapper.get("existing_task_state_json") or wrapper.get("existing_task_state")
    if isinstance(state, str):
        try:
            state = json.loads(state)
        except json.JSONDecodeError:
            state = {}
    state = state or {}
    return (
        wrapper.get("task_id")
        or state.get("task_id")
        or plan.get("task_id")
        or (plan.get("entity_context") or {}).get("task_id")
    )


ORDER_FIELDS = {
    "customer_name", "product_name", "packaging_method", "requested_delivery_date",
    "latest_supplier_commitment", "current_progress", "current_node", "status"
}
TASK_FIELDS = {
    "title", "recommended_action", "target", "status", "owner_user_id",
    "responsibility_status", "waiting_on", "promised_reply_at", "next_action_at",
    "business_deadline", "last_contact_at", "risk_level", "urgent", "pending_confirmation"
}


def apply_writeback(conn: sqlite3.Connection, wrapper: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    key = str(plan.get("idempotency_key") or wrapper.get("idempotency_key") or "")
    if not key:
        raise HTTPException(422, "缺少idempotency_key")

    duplicate = conn.execute(
        "SELECT result_json FROM idempotency_records WHERE idempotency_key=?", (key,)
    ).fetchone()
    if duplicate:
        old = json.loads(duplicate["result_json"])
        return {"status": "DUPLICATE_SKIPPED", "idempotency_key": key, "previous_result": old}

    order_id = get_order_id(conn, wrapper, plan)
    task_id = get_task_id(wrapper, plan)
    operator_id = wrapper.get("operator_id") or plan.get("operator_id") or "COZE"
    changes = plan.get("change_set") or []
    applied: list[dict[str, Any]] = []
    warnings: list[str] = []
    timestamp = iso()

    conn.execute(
        "INSERT INTO confirmation_snapshots VALUES(?,?,?,?,?)",
        (new_id("CONF"), key, operator_id, json.dumps(wrapper, ensure_ascii=False), timestamp),
    )

    for change in changes:
        domain = change.get("domain")
        field = change.get("field_name")
        value = change.get("new_value")
        entity_id = change.get("entity_id")

        if domain == "order_changes":
            target_id = entity_id or order_id
            if not target_id or field not in ORDER_FIELDS:
                warnings.append(f"跳过订单变更：{field}")
                continue
            conn.execute(f"UPDATE orders SET {field}=?, updated_at=? WHERE order_id=?", (value, timestamp, target_id))
            applied.append({"domain": domain, "entity_id": target_id, "field": field, "value": value})
            if field == "latest_supplier_commitment" and value:
                conn.execute(
                    """INSERT INTO commitment_history(commitment_id,order_id,commitment_type,commitment_value,
                       source_message_id,confirmed_by,created_at) VALUES(?,?,?,?,?,?,?)""",
                    (new_id("COM"), target_id, "supplier_commitment", str(value), None, operator_id, timestamp),
                )

        elif domain == "task_changes":
            target_id = entity_id or task_id
            if not target_id or field not in TASK_FIELDS:
                warnings.append(f"跳过任务变更：{field}")
                continue
            conn.execute(f"UPDATE tasks SET {field}=?, updated_at=? WHERE task_id=?", (value, timestamp, target_id))
            applied.append({"domain": domain, "entity_id": target_id, "field": field, "value": value})

        elif domain == "action_decision":
            target_id = entity_id or task_id
            if not target_id:
                warnings.append("跳过行动状态：缺少task_id")
                continue
            updates = {
                "next_action_at": change.get("next_action_at"),
                "waiting_on": change.get("waiting_on"),
                "recommended_action": change.get("recommended_action"),
            }
            for f, v in updates.items():
                if v is not None:
                    conn.execute(f"UPDATE tasks SET {f}=?, updated_at=? WHERE task_id=?", (v, timestamp, target_id))
            applied.append({"domain": domain, "entity_id": target_id, "state_candidate": value})

        elif domain == "risk_changes":
            target_order = order_id
            target_task = task_id
            risk_type = str(value or field or "other")
            risk_level = str(change.get("risk_level") or "medium")
            conn.execute(
                """INSERT INTO risk_signals(risk_id,order_id,task_id,risk_type,risk_level,evidence,rule_id,status,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (new_id("RISK"), target_order, target_task, risk_type, risk_level,
                 change.get("evidence"), change.get("rule_id"), "OPEN", timestamp, timestamp),
            )
            applied.append({"domain": domain, "risk_type": risk_type})

    result = {
        "status": "COMMITTED",
        "idempotency_key": key,
        "order_id": order_id,
        "task_id": task_id,
        "applied_changes": applied,
        "warnings": warnings,
        "committed_at": timestamp,
    }
    conn.execute(
        "INSERT INTO event_logs VALUES(?,?,?,?,?,?,?)",
        (new_id("EVT"), "transaction", key, "FT03_WRITEBACK_COMMITTED",
         json.dumps(result, ensure_ascii=False), operator_id, timestamp),
    )
    conn.execute(
        "INSERT INTO idempotency_records VALUES(?,?,?,?)",
        (key, "COMMITTED", json.dumps(result, ensure_ascii=False), timestamp),
    )
    return result


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    init_db()
    return {"status": "ok", "version": "1.0.0", "db": str(DB_PATH)}


@app.post("/api/reset")
def reset() -> dict[str, Any]:
    with db() as conn:
        reset_demo_data(conn)
    return {"status": "reset", "at": iso()}


@app.get("/api/dashboard")
def dashboard(
    current_user_id: str = Query("USER-1"),
    current_time: str | None = Query(None),
) -> dict[str, Any]:
    current = parse_dt(current_time) or now_cn()
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()]
        orders = {r["order_id"]: dict(r) for r in conn.execute("SELECT * FROM orders").fetchall()}
    items = [decide_task(r, current, current_user_id) for r in rows]
    items.sort(key=lambda x: x["priority_score"], reverse=True)
    for item in items:
        item["order"] = orders.get(item.get("related_order_id"))
    top = [
        x for x in items
        if x["action_state"] not in {"DONE", "NOT_MY_RESPONSIBILITY"}
        and not x["ranking_suppressed"]
    ][:5]
    summary = {
        "total": len(items),
        "do_now": sum(x["action_state"] == "DO_NOW" for x in items),
        "do_today": sum(x["action_state"] == "DO_TODAY" for x in items),
        "waiting": sum(x["action_state"] == "WAITING_EXTERNAL" for x in items),
        "needs_confirmation": sum(x["action_state"] == "NEEDS_CONFIRMATION" for x in items),
        "scheduled": sum(x["action_state"] == "SCHEDULED" for x in items),
        "escalate": sum(x["action_state"] == "ESCALATE" for x in items),
    }
    return {"current_time": iso(current), "summary": summary, "items": items, "top_actions": top}


@app.get("/api/orders/{order_id}")
def order_detail(order_id: str) -> dict[str, Any]:
    with db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not order:
            raise HTTPException(404, "订单不存在")
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks WHERE related_order_id=? ORDER BY created_at DESC", (order_id,))]
        risks = [dict(r) for r in conn.execute("SELECT * FROM risk_signals WHERE order_id=? ORDER BY created_at DESC", (order_id,))]
        messages = [dict(r) for r in conn.execute("SELECT * FROM source_messages WHERE order_id=? ORDER BY created_at DESC", (order_id,))]
        commitments = [dict(r) for r in conn.execute("SELECT * FROM commitment_history WHERE order_id=? ORDER BY created_at DESC", (order_id,))]
        events = [dict(r) for r in conn.execute("SELECT * FROM event_logs WHERE entity_id=? ORDER BY created_at DESC", (order_id,))]
    for task in tasks:
        task["evidence"] = json.loads(task.pop("evidence_json") or "[]")
    return {
        "order": dict(order), "tasks": tasks, "risks": risks,
        "messages": messages, "commitments": commitments, "events": events,
    }


@app.post("/api/tasks/{task_id}/contacted")
def contacted(task_id: str, payload: AnyPayload) -> dict[str, Any]:
    body = payload.model_dump()
    promised_reply_at = body.get("promised_reply_at")
    waiting_on = body.get("waiting_on") or "factory"
    if not promised_reply_at:
        raise HTTPException(422, "缺少promised_reply_at")
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        timestamp = iso()
        conn.execute(
            """UPDATE tasks SET waiting_on=?, promised_reply_at=?, next_action_at=?,
               last_contact_at=?, pending_confirmation=0, updated_at=? WHERE task_id=?""",
            (waiting_on, promised_reply_at, promised_reply_at, timestamp, timestamp, task_id),
        )
        conn.execute(
            "INSERT INTO event_logs VALUES(?,?,?,?,?,?,?)",
            (new_id("EVT"), "task", task_id, "CONTACT_RECORDED",
             json.dumps(body, ensure_ascii=False), body.get("operator_id") or "USER-1", timestamp),
        )
        conn.commit()
    return {"status": "updated", "task_id": task_id, "waiting_on": waiting_on, "promised_reply_at": promised_reply_at}


@app.post("/api/tasks/{task_id}/complete")
def complete(task_id: str) -> dict[str, Any]:
    with db() as conn:
        timestamp = iso()
        changed = conn.execute(
            "UPDATE tasks SET status='DONE', updated_at=? WHERE task_id=?", (timestamp, task_id)
        ).rowcount
        if not changed:
            raise HTTPException(404, "任务不存在")
        conn.commit()
    return {"status": "done", "task_id": task_id}


@app.post("/api/writeback")
def writeback(
    payload: AnyPayload,
    x_api_key: str | None = Header(None),
) -> dict[str, Any]:
    if x_api_key != API_KEY:
        raise HTTPException(401, "X-API-Key无效")
    wrapper = payload.model_dump()
    raw_plan = wrapper.get("transaction_json") or wrapper.get("transaction") or wrapper
    if isinstance(raw_plan, str):
        try:
            plan = json.loads(raw_plan)
        except json.JSONDecodeError as exc:
            raise HTTPException(422, f"transaction_json不是合法JSON：{exc}")
    else:
        plan = raw_plan
    with db() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = apply_writeback(conn, wrapper, plan)
            conn.commit()
            return result
        except HTTPException:
            conn.rollback()
            raise
        except Exception as exc:
            conn.rollback()
            raise HTTPException(500, f"写回事务失败：{exc}")


@app.post("/api/demo/apply-ft01")
def demo_ft01() -> dict[str, Any]:
    payload = json.loads((BASE_DIR / "demo_payloads" / "FT01_confirmed_writeback.json").read_text(encoding="utf-8"))
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        result = apply_writeback(conn, payload, json.loads(payload["transaction_json"]))
        # Create a task if not already present.
        exists = conn.execute("SELECT 1 FROM tasks WHERE task_id='TASK-PO1001-CONFIRM'").fetchone()
        if not exists:
            timestamp = iso()
            conn.execute(
                """INSERT INTO tasks(task_id,related_order_id,title,recommended_action,target,status,
                   owner_user_id,responsibility_status,waiting_on,promised_reply_at,next_action_at,
                   business_deadline,last_contact_at,risk_level,urgent,pending_confirmation,
                   source_message_id,evidence_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("TASK-PO1001-CONFIRM", "ORD-1001", "确认包装调整是否影响交期",
                 "联系工厂确认包装调整是否影响交期", "factory", "OPEN", "USER-1",
                 "assigned", None, None, None, iso(now_cn() + timedelta(hours=4)), None,
                 "high", 1, 0, None,
                 json.dumps(["包装方式请改为彩盒，并请今天确认是否会影响8月20日交期"], ensure_ascii=False),
                 timestamp, timestamp),
            )
        conn.commit()
    return result


@app.post("/api/demo/apply-ft02")
def demo_ft02() -> dict[str, Any]:
    payload = json.loads((BASE_DIR / "demo_payloads" / "FT02_confirmed_writeback.json").read_text(encoding="utf-8"))
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        result = apply_writeback(conn, payload, json.loads(payload["transaction_json"]))
        timestamp = iso()
        conn.execute(
            """UPDATE tasks SET title=?, recommended_action=?, waiting_on=NULL,
               promised_reply_at=NULL, next_action_at=?, risk_level='high',
               pending_confirmation=0, updated_at=? WHERE task_id='TASK-PO1001-CONFIRM'""",
            ("确认工厂明确完工日期和补救方案", "向工厂确认明确日期、补救措施、负责人和完成时间",
             timestamp, timestamp),
        )
        conn.commit()
    return result


init_db()
