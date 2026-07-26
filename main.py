from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import re

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "action_layer.db")))
API_KEY = os.getenv("APP_API_KEY", "demo-key")
CN_TZ = timezone(timedelta(hours=8))

app = FastAPI(title="AI外贸跟单行动层 MVP", version="3.0.0")
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
        "user_settings", "candidate_reviews", "idempotency_records", "event_logs",
        "confirmation_snapshots", "commitment_history", "risk_signals", "tasks",
        "source_messages", "orders"
    ]:
        conn.execute(f"DELETE FROM {table}")

    now = now_cn()
    orders = [
        ("ORD-1001", "PO-1001", "Northwind Trading", "帆布包", "普通盒", "2026-08-20", None, 0.55, "生产中"),
        ("ORD-1002", "PO-1002", "Blue Harbor", "拉链袋", "普通包装", "2026-08-05", "2026-07-30", 0.70, "生产中"),
        ("ORD-1003", "PO-1003", "Green Field", "礼品盒", "彩盒", "2026-08-01", None, 0.30, "待确认"),
        ("ORD-1004", "PO-2043", "Atlas Retail", "收纳袋", "OPP袋", "2026-08-18", "2026-08-11", 0.42, "备料中"),
        ("ORD-1005", "PO-3321", "Morgen GmbH", "香薰礼盒", "礼盒", "2026-08-28", None, 0.18, "设计确认"),
    ]
    for row in orders:
        conn.execute(
            """INSERT INTO orders(order_id,order_no,customer_name,product_name,packaging_method,
               requested_delivery_date,latest_supplier_commitment,current_progress,current_node,
               created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (*row, iso(now), iso(now)),
        )

    tasks = [
        ("TASK-WAIT-001", "ORD-1002", "等待工厂确认拉链到料时间", "等待工厂回复", "factory", "OPEN", "USER-1", "assigned", "factory", iso(now + timedelta(hours=3)), iso(now + timedelta(hours=3)), iso(now + timedelta(days=2)), iso(now - timedelta(hours=20)), "high", 0, 0, None, ["工厂承诺3小时内回复"]),
        ("TASK-CONFIRM-001", "ORD-1003", "确认客户新增Logo版本", "审核候选变化并确认", "customer", "OPEN", "USER-1", "assigned", None, None, None, iso(now + timedelta(days=1)), None, "medium", 0, 1, None, ["客户发来新版本设计稿"]),
        ("TASK-ESC-001", "ORD-1002", "处理客户取消订单风险", "请求主管介入", "manager", "OPEN", None, "unassigned", None, None, None, iso(now - timedelta(hours=12)), None, "critical", 1, 0, None, ["客户表示若仍无明确答复将取消订单"]),
        ("TASK-TODAY-001", "ORD-1003", "今天确认彩盒样品", "联系客户确认样品", "customer", "OPEN", "USER-1", "assigned", None, None, None, iso(now + timedelta(hours=6)), None, "medium", 0, 0, None, ["样品确认截止今天"]),
        ("TASK-2043-001", "ORD-1004", "核对唛头文件版本", "向客户确认最终唛头文件", "customer", "OPEN", "USER-2", "assigned", None, None, None, iso(now + timedelta(days=1, hours=4)), None, "medium", 0, 0, None, ["客户邮件中出现两个不同版本的唛头文件"]),
        ("TASK-2043-002", "ORD-1004", "确认面料到厂时间", "联系工厂确认面料到厂时间", "factory", "OPEN", "USER-1", "assigned", None, None, None, iso(now + timedelta(hours=9)), None, "high", 0, 0, None, ["面料仍未进入裁剪工序"]),
        ("TASK-3321-001", "ORD-1005", "确认香型标签翻译", "转交翻译并回传客户", "customer", "OPEN", "USER-3", "assigned", None, None, None, iso(now + timedelta(days=2)), None, "low", 0, 0, None, ["客户要求确认德语标签内容"]),
        ("TASK-3321-002", "ORD-1005", "等待客户确认设计稿", "等待客户回复", "customer", "OPEN", "USER-1", "assigned", "customer", iso(now + timedelta(days=1)), iso(now + timedelta(days=1)), iso(now + timedelta(days=4)), iso(now - timedelta(hours=4)), "low", 0, 0, None, ["设计稿已发送客户，客户承诺明天下午前回复"]),
    ]
    for t in tasks:
        conn.execute(
            """INSERT INTO tasks(task_id,related_order_id,title,recommended_action,target,status,
               owner_user_id,responsibility_status,waiting_on,promised_reply_at,next_action_at,
               business_deadline,last_contact_at,risk_level,urgent,pending_confirmation,
               source_message_id,evidence_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*t[:-1], json.dumps(t[-1], ensure_ascii=False), iso(now), iso(now)),
        )

    risks = [
        ("RISK-ESC-001", "ORD-1002", "TASK-ESC-001", "customer_cancellation", "critical", "客户表示若仍无明确答复将取消订单", "R_CUSTOMER_COMPLAINT"),
        ("RISK-2043-001", "ORD-1004", "TASK-2043-001", "document_conflict", "medium", "客户邮件中出现两个不同版本的唛头文件", "R_DOCUMENT_CONFLICT"),
        ("RISK-2043-002", "ORD-1004", "TASK-2043-002", "delivery_risk", "high", "面料仍未进入裁剪工序", "R_DELIVERY_RISK"),
    ]
    for r in risks:
        conn.execute(
            """INSERT INTO risk_signals(risk_id,order_id,task_id,risk_type,risk_level,evidence,rule_id,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (*r, "OPEN", iso(now), iso(now)),
        )

    msg_id = "MSG-SEED-001"
    raw = "PO-1001的包装方式请改为彩盒，并请今天确认是否会影响8月20日交期。"
    conn.execute(
        """INSERT INTO source_messages(message_id,order_id,source_channel,sender_role,message_type,raw_content,source_time,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (msg_id, "ORD-1001", "email", "customer", "customer_request", raw, iso(now - timedelta(minutes=18)), iso(now - timedelta(minutes=18))),
    )
    candidate = {
        "message_type": "customer_request",
        "order_match": {"status": "unique_match", "selected_order_id": "ORD-1001", "matched_order_no": "PO-1001"},
        "fields": [
            {"field_name": "packaging_method", "old_value": "普通盒", "normalized_value": "彩盒", "source_quote": "包装方式请改为彩盒", "confidence": 0.98},
            {"field_name": "requested_delivery_date", "old_value": "2026-08-20", "normalized_value": "2026-08-20", "source_quote": "8月20日交期", "confidence": 0.96},
        ],
        "risk_signals": [{"type": "delivery_impact_unknown", "risk_level": "high", "evidence": "请今天确认是否会影响8月20日交期"}],
        "action_candidates": [{"action_type": "confirm_with_factory", "title": "确认包装调整是否影响交期", "recommended_action": "联系工厂确认包装调整是否影响交期", "target": "factory"}],
    }
    conn.execute(
        """INSERT INTO candidate_reviews(review_id,source_message_id,order_id,workflow_source,candidate_json,status,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        ("REV-SEED-001", msg_id, "ORD-1001", "COZE_FT01_SAMPLE", json.dumps(candidate, ensure_ascii=False), "PENDING", iso(now - timedelta(minutes=17))),
    )
    defaults = {"accent": "blue", "compact": False, "show_demo": True, "notifications": {"urgent": True, "waiting_overdue": True, "writeback": True, "daily_summary": False}}
    conn.execute("INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES(?,?,?)", ("USER-1", json.dumps(defaults, ensure_ascii=False), iso(now)))
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
            conn.execute(
                "INSERT INTO event_logs VALUES(?,?,?,?,?,?,?)",
                (new_id("EVT"), "order", target_id, "ORDER_FIELD_UPDATED",
                 json.dumps({"field": field, "value": value, "idempotency_key": key}, ensure_ascii=False), operator_id, timestamp),
            )
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
            conn.execute(
                "INSERT INTO event_logs VALUES(?,?,?,?,?,?,?)",
                (new_id("EVT"), "task", target_id, "TASK_FIELD_UPDATED",
                 json.dumps({"field": field, "value": value, "idempotency_key": key}, ensure_ascii=False), operator_id, timestamp),
            )

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



OWNER_NAMES = {"USER-1": "李梅", "USER-2": "张晓", "USER-3": "陈静", "MANAGER-1": "王主管", None: "未分配"}
RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def rowdict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def extract_order_numbers(raw: str) -> list[str]:
    found = re.findall(r"\\bPO[-_ ]?\\d+\\b", raw or "", re.I)
    return list(dict.fromkeys(x.replace("_", "-").replace(" ", "-").upper() for x in found))


def normalize_cn_date(source_time: str | None, text: str) -> str | None:
    m = re.search(r"(\\d{1,2})月(\\d{1,2})日", text or "")
    if not m:
        return None
    base = parse_dt(source_time) or now_cn()
    month, day = int(m.group(1)), int(m.group(2))
    year = base.year + (1 if month * 100 + day + 100 < base.month * 100 + base.day else 0)
    try:
        return datetime(year, month, day, tzinfo=CN_TZ).date().isoformat()
    except ValueError:
        return None


def local_candidate(raw: str, sender_role: str, source_time: str | None, order: dict[str, Any] | None) -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    order_nos = extract_order_numbers(raw)
    for no in order_nos:
        fields.append({"field_name": "order_no", "normalized_value": no, "source_quote": no, "confidence": 1.0})

    pack = re.search(r"包装(?:方式)?(?:请)?改为([^，。；,;\\s]+)", raw)
    if pack:
        fields.append({"field_name": "packaging_method", "old_value": order.get("packaging_method") if order else None, "normalized_value": pack.group(1), "source_quote": pack.group(1), "confidence": 0.98})
    date_value = normalize_cn_date(source_time, raw)
    if date_value:
        fields.append({"field_name": "requested_delivery_date", "old_value": order.get("requested_delivery_date") if order else None, "normalized_value": date_value, "source_quote": re.search(r"\\d{1,2}月\\d{1,2}日", raw).group(0), "confidence": 0.95})
    progress_match = re.search(r"差不多([一二三四五六七八九十])成|([一二三四五六七八九十])成|(\\d+(?:\\.\\d+)?)%", raw)
    if progress_match:
        cn = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}
        if progress_match.group(3): val = float(progress_match.group(3))/100
        else: val = cn.get(progress_match.group(1) or progress_match.group(2), 0)/10
        fields.append({"field_name": "current_progress", "old_value": order.get("current_progress") if order else None, "normalized_value": val, "source_quote": progress_match.group(0), "confidence": 0.9, "certainty": "approximate" if "差不多" in progress_match.group(0) else "confirmed"})
    if re.search(r"(是否|会不会|能否).{0,12}影响.{0,8}交期|影响.{0,8}交期", raw):
        risks.append({"type": "delivery_impact_unknown", "risk_level": "high", "evidence": raw})
        actions.append({"action_type": "confirm_with_factory", "title": "确认变更是否影响交期", "recommended_action": "联系工厂确认变更是否影响交期", "target": "factory"})
    if re.search(r"取消订单|投诉|索赔|严重不满|非常不满", raw):
        risks.append({"type": "customer_complaint", "risk_level": "critical", "evidence": raw})
        actions.append({"action_type": "reply_customer", "title": "立即处理客户投诉", "recommended_action": "立即回复客户并同步主管", "target": "customer"})
    if re.search(r"应该|大概|预计|尽量|可能", raw) and re.search(r"完成|交期|下周|本周|日期", raw):
        risks.append({"type": "commitment_uncertain", "risk_level": "medium", "evidence": raw})
        actions.append({"action_type": "confirm_commitment", "title": "确认明确承诺日期", "recommended_action": "向工厂确认明确完工日期", "target": "factory"})
    if re.search(r"补救方案.*(晚点|稍后|待确认)|晚点回复", raw):
        actions.append({"action_type": "ask_for_remedy", "title": "补问补救方案", "recommended_action": "补问补救措施、负责人和完成时间", "target": "factory"})
    if not actions:
        actions.append({"action_type": "check_order", "title": "核对消息并安排下一步", "recommended_action": "核对订单状态并确定后续动作", "target": sender_role or "unknown"})
    match_status = "unique_match" if order else ("multiple_matches" if len(order_nos) > 1 else "no_match")
    return {
        "message_type": "complaint" if any(x["type"] == "customer_complaint" for x in risks) else ("factory_update" if sender_role == "factory" else "customer_request"),
        "order_match": {"status": match_status, "selected_order_id": order.get("order_id") if order else None, "matched_order_no": order.get("order_no") if order else None, "candidate_order_nos": order_nos},
        "fields": fields,
        "risk_signals": risks,
        "action_candidates": actions,
        "manual_review_required": not bool(order),
    }


def review_to_transaction(review: dict[str, Any], candidate: dict[str, Any], operator_id: str) -> tuple[dict[str, Any], str | None]:
    order_id = review.get("order_id") or (candidate.get("order_match") or {}).get("selected_order_id")
    changes: list[dict[str, Any]] = []
    for field in candidate.get("fields") or []:
        name = field.get("field_name")
        if name in ORDER_FIELDS and field.get("normalized_value") is not None:
            changes.append({"domain": "order_changes", "entity_id": order_id, "field_name": name, "new_value": field.get("normalized_value"), "evidence": field.get("source_quote")})
    for risk in candidate.get("risk_signals") or []:
        changes.append({"domain": "risk_changes", "field_name": "risk_type", "new_value": risk.get("type") or "other", "risk_level": risk.get("risk_level") or "medium", "evidence": risk.get("evidence")})
    return {"idempotency_key": f"REVIEW|{review['review_id']}|1", "operator_id": operator_id, "change_set": changes}, order_id


@app.get("/api/tasks")
def task_list(
    current_user_id: str = Query("USER-1"),
    state: str | None = Query(None),
    q: str | None = Query(None),
) -> dict[str, Any]:
    data = dashboard(current_user_id=current_user_id, current_time=None)
    items = data["items"]
    if state and state != "ALL":
        items = [x for x in items if x["action_state"] == state]
    if q:
        needle = q.lower()
        items = [x for x in items if needle in " ".join(str(v or "") for v in [x.get("title"), x.get("recommended_action"), (x.get("order") or {}).get("order_no"), (x.get("order") or {}).get("customer_name")]).lower()]
    return {"items": items, "summary": data["summary"], "total": len(items)}


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, payload: AnyPayload) -> dict[str, Any]:
    body = payload.model_dump()
    allowed = TASK_FIELDS | {"urgent"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(422, "没有可更新字段")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone():
            raise HTTPException(404, "任务不存在")
        timestamp = iso()
        parts, values = [], []
        for key, value in updates.items():
            parts.append(f"{key}=?")
            values.append(int(value) if key in {"urgent", "pending_confirmation"} and isinstance(value, bool) else value)
        values += [timestamp, task_id]
        conn.execute(f"UPDATE tasks SET {', '.join(parts)}, updated_at=? WHERE task_id=?", values)
        conn.execute("INSERT INTO event_logs VALUES(?,?,?,?,?,?,?)", (new_id("EVT"), "task", task_id, "TASK_UPDATED_FROM_UI", json.dumps(updates, ensure_ascii=False), body.get("operator_id") or "USER-1", timestamp))
        conn.commit()
    return {"status": "updated", "task_id": task_id, "changes": updates}


@app.post("/api/tasks/{task_id}/transfer")
def transfer_task(task_id: str, payload: AnyPayload) -> dict[str, Any]:
    body = payload.model_dump()
    owner = body.get("owner_user_id")
    if not owner:
        raise HTTPException(422, "缺少owner_user_id")
    return update_task(task_id, AnyPayload(owner_user_id=owner, responsibility_status="assigned", operator_id=body.get("operator_id") or "USER-1"))


@app.post("/api/tasks/{task_id}/escalate")
def escalate_task(task_id: str, payload: AnyPayload) -> dict[str, Any]:
    body = payload.model_dump()
    with db() as conn:
        row = conn.execute("SELECT related_order_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        timestamp = iso()
        conn.execute("UPDATE tasks SET risk_level='critical', urgent=1, target='manager', owner_user_id='MANAGER-1', updated_at=? WHERE task_id=?", (timestamp, task_id))
        conn.execute("INSERT INTO risk_signals VALUES(?,?,?,?,?,?,?,?,?,?)", (new_id("RISK"), row["related_order_id"], task_id, "manager_escalation", "critical", body.get("reason") or "一线人员请求主管介入", "R_MANUAL_ESCALATION", "OPEN", timestamp, timestamp))
        conn.execute("INSERT INTO event_logs VALUES(?,?,?,?,?,?,?)", (new_id("EVT"), "task", task_id, "TASK_ESCALATED", json.dumps(body, ensure_ascii=False), body.get("operator_id") or "USER-1", timestamp))
        conn.commit()
    return {"status": "escalated", "task_id": task_id}


@app.get("/api/orders")
def order_list(q: str | None = Query(None), status: str | None = Query(None)) -> dict[str, Any]:
    with db() as conn:
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY updated_at DESC").fetchall()]
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()]
        risks = [dict(r) for r in conn.execute("SELECT * FROM risk_signals WHERE status='OPEN'").fetchall()]
    result = []
    for order in orders:
        otasks = [t for t in tasks if t.get("related_order_id") == order["order_id"] and t.get("status") != "DONE"]
        orisks = [r for r in risks if r.get("order_id") == order["order_id"]]
        max_risk = max((r.get("risk_level") or "none" for r in orisks), key=lambda x: RISK_ORDER.get(x, 0), default="none")
        item = dict(order)
        item.update({"open_task_count": len(otasks), "waiting_task_count": sum(bool(t.get("waiting_on")) for t in otasks), "risk_count": len(orisks), "max_risk": max_risk, "next_action_at": min((t.get("next_action_at") or t.get("business_deadline") for t in otasks if t.get("next_action_at") or t.get("business_deadline")), default=None)})
        result.append(item)
    if q:
        needle = q.lower()
        result = [x for x in result if needle in " ".join(str(x.get(k) or "") for k in ["order_no","customer_name","product_name","current_node"]).lower()]
    if status and status != "ALL":
        result = [x for x in result if x.get("status") == status]
    return {"items": result, "total": len(result), "summary": {"active": sum(x["status"] == "ACTIVE" for x in result), "risk_orders": sum(x["max_risk"] in {"high","critical"} for x in result), "pending_tasks": sum(x["open_task_count"] for x in result), "commitments": sum(bool(x.get("latest_supplier_commitment")) for x in result)}}


@app.post("/api/orders/{order_id}/tasks")
def create_order_task(order_id: str, payload: AnyPayload) -> dict[str, Any]:
    body = payload.model_dump()
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "缺少任务标题")
    with db() as conn:
        if not conn.execute("SELECT 1 FROM orders WHERE order_id=?", (order_id,)).fetchone():
            raise HTTPException(404, "订单不存在")
        task_id, timestamp = new_id("TASK"), iso()
        conn.execute(
            """INSERT INTO tasks(task_id,related_order_id,title,recommended_action,target,status,owner_user_id,responsibility_status,waiting_on,promised_reply_at,next_action_at,business_deadline,last_contact_at,risk_level,urgent,pending_confirmation,source_message_id,evidence_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, order_id, title, body.get("recommended_action") or title, body.get("target") or "factory", "OPEN", body.get("owner_user_id") or "USER-1", "assigned", None, None, body.get("next_action_at"), body.get("business_deadline"), None, body.get("risk_level") or "medium", int(bool(body.get("urgent"))), 0, None, json.dumps(body.get("evidence") or [], ensure_ascii=False), timestamp, timestamp),
        )
        conn.execute("INSERT INTO event_logs VALUES(?,?,?,?,?,?,?)", (new_id("EVT"), "order", order_id, "TASK_CREATED_FROM_UI", json.dumps({"task_id": task_id, **body}, ensure_ascii=False), body.get("operator_id") or "USER-1", timestamp))
        conn.commit()
    return {"status": "created", "task_id": task_id, "order_id": order_id}


@app.post("/api/intake/analyze")
def analyze_intake(payload: AnyPayload) -> dict[str, Any]:
    body = payload.model_dump()
    raw = str(body.get("raw_content") or "").strip()
    if not raw:
        raise HTTPException(422, "消息内容不能为空")
    sender_role = body.get("sender_role") or "customer"
    source_channel = body.get("source_channel") or "email"
    source_time = body.get("source_time") or iso()
    order_id = body.get("order_id")
    with db() as conn:
        order = rowdict(conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()) if order_id else None
        if not order:
            order_nos = extract_order_numbers(raw)
            if len(order_nos) == 1:
                order = rowdict(conn.execute("SELECT * FROM orders WHERE order_no=?", (order_nos[0],)).fetchone())
                order_id = order.get("order_id") if order else None
        candidate = local_candidate(raw, sender_role, source_time, order)
        message_id, review_id, timestamp = new_id("MSG"), new_id("REV"), iso()
        conn.execute("INSERT INTO source_messages VALUES(?,?,?,?,?,?,?,?)", (message_id, order_id, source_channel, sender_role, candidate.get("message_type"), raw, source_time, timestamp))
        conn.execute("INSERT INTO candidate_reviews(review_id,source_message_id,order_id,workflow_source,candidate_json,status,created_at) VALUES(?,?,?,?,?,?,?)", (review_id, message_id, order_id, "LOCAL_RULE_DEMO", json.dumps(candidate, ensure_ascii=False), "PENDING", timestamp))
        conn.commit()
    return {"status": "analyzed", "review_id": review_id, "message_id": message_id, "candidate": candidate, "boundary": "本地规则用于网页演示；正式语义理解仍由Coze FT01/FT02完成"}


@app.post("/api/reviews/import")
def import_review(payload: AnyPayload) -> dict[str, Any]:
    body = payload.model_dump()
    raw_result = body.get("result_json") or body.get("candidate")
    if isinstance(raw_result, str):
        try: candidate = json.loads(raw_result)
        except json.JSONDecodeError as exc: raise HTTPException(422, f"result_json不是合法JSON：{exc}")
    elif isinstance(raw_result, dict): candidate = raw_result
    else: raise HTTPException(422, "缺少result_json")
    order_id = body.get("order_id") or (candidate.get("order_match") or {}).get("selected_order_id")
    with db() as conn:
        review_id, timestamp = new_id("REV"), iso()
        conn.execute("INSERT INTO candidate_reviews(review_id,source_message_id,order_id,workflow_source,candidate_json,status,created_at) VALUES(?,?,?,?,?,?,?)", (review_id, body.get("source_message_id"), order_id, body.get("workflow_source") or "COZE_IMPORT", json.dumps(candidate, ensure_ascii=False), "PENDING", timestamp))
        conn.commit()
    return {"status": "imported", "review_id": review_id}


@app.get("/api/reviews")
def review_list(status: str | None = Query(None)) -> dict[str, Any]:
    with db() as conn:
        sql = "SELECT r.*, m.raw_content, m.sender_role, m.source_channel, o.order_no, o.customer_name FROM candidate_reviews r LEFT JOIN source_messages m ON m.message_id=r.source_message_id LEFT JOIN orders o ON o.order_id=r.order_id"
        params: list[Any] = []
        if status and status != "ALL":
            sql += " WHERE r.status=?"; params.append(status)
        sql += " ORDER BY CASE r.status WHEN 'PENDING' THEN 0 ELSE 1 END, r.created_at DESC"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for row in rows: row["candidate"] = json.loads(row.pop("candidate_json"))
    return {"items": rows, "total": len(rows), "pending": sum(x["status"] == "PENDING" for x in rows)}


@app.get("/api/reviews/{review_id}")
def review_detail(review_id: str) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT r.*, m.raw_content, m.sender_role, m.source_channel, o.order_no, o.customer_name FROM candidate_reviews r LEFT JOIN source_messages m ON m.message_id=r.source_message_id LEFT JOIN orders o ON o.order_id=r.order_id WHERE r.review_id=?", (review_id,)).fetchone()
        if not row: raise HTTPException(404, "候选记录不存在")
    result = dict(row); result["candidate"] = json.loads(result.pop("candidate_json")); return result


@app.post("/api/reviews/{review_id}/confirm")
def confirm_review(review_id: str, payload: AnyPayload) -> dict[str, Any]:
    body = payload.model_dump()
    with db() as conn:
        row = conn.execute("SELECT * FROM candidate_reviews WHERE review_id=?", (review_id,)).fetchone()
        if not row: raise HTTPException(404, "候选记录不存在")
        review = dict(row)
        if review["status"] == "CONFIRMED":
            return {"status": "DUPLICATE_SKIPPED", "review_id": review_id, "order_id": review.get("order_id")}
        candidate = body.get("candidate") or json.loads(review["candidate_json"])
        plan, order_id = review_to_transaction(review, candidate, body.get("operator_id") or "USER-1")
        if not order_id: raise HTTPException(422, "候选未唯一关联订单，请先选择订单")
        conn.execute("BEGIN IMMEDIATE")
        result = apply_writeback(conn, {"order_id": order_id, "operator_id": body.get("operator_id") or "USER-1"}, plan)
        action = (candidate.get("action_candidates") or [None])[0]
        task_id = None
        if action:
            task_id, timestamp = new_id("TASK"), iso()
            evidence = [x.get("source_quote") or x.get("evidence") for x in (candidate.get("fields") or []) + (candidate.get("risk_signals") or []) if x.get("source_quote") or x.get("evidence")]
            risk_level = max((r.get("risk_level") or "none" for r in candidate.get("risk_signals") or []), key=lambda x: RISK_ORDER.get(x,0), default="medium")
            conn.execute("""INSERT INTO tasks(task_id,related_order_id,title,recommended_action,target,status,owner_user_id,responsibility_status,waiting_on,promised_reply_at,next_action_at,business_deadline,last_contact_at,risk_level,urgent,pending_confirmation,source_message_id,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (task_id, order_id, action.get("title") or "处理AI候选行动", action.get("recommended_action") or action.get("title") or "处理AI候选行动", action.get("target") or "factory", "OPEN", "USER-1", "assigned", None, None, iso(now_cn() + timedelta(hours=4)), iso(now_cn() + timedelta(hours=8)), None, risk_level, int(risk_level == "critical"), 0, review.get("source_message_id"), json.dumps(evidence, ensure_ascii=False), timestamp, timestamp))
            conn.execute("INSERT INTO event_logs VALUES(?,?,?,?,?,?,?)", (new_id("EVT"), "order", order_id, "AI_CANDIDATE_CONFIRMED", json.dumps({"review_id": review_id, "task_id": task_id}, ensure_ascii=False), body.get("operator_id") or "USER-1", timestamp))
        timestamp = iso()
        conn.execute("UPDATE candidate_reviews SET candidate_json=?, status='CONFIRMED', reviewer_id=?, reviewed_at=? WHERE review_id=?", (json.dumps(candidate, ensure_ascii=False), body.get("operator_id") or "USER-1", timestamp, review_id))
        if review.get("source_message_id"): conn.execute("UPDATE source_messages SET order_id=? WHERE message_id=?", (order_id, review["source_message_id"]))
        conn.commit()
    return {"status": "CONFIRMED", "review_id": review_id, "order_id": order_id, "task_id": task_id, "writeback": result, "boundary": "网页确认使用同一确定性适配器写入演示数据库；正式企业流程仍由Coze FT03执行"}


@app.post("/api/reviews/{review_id}/reject")
def reject_review(review_id: str, payload: AnyPayload) -> dict[str, Any]:
    body = payload.model_dump()
    with db() as conn:
        changed = conn.execute("UPDATE candidate_reviews SET status='REJECTED', reviewer_id=?, reviewed_at=? WHERE review_id=?", (body.get("operator_id") or "USER-1", iso(), review_id)).rowcount
        if not changed: raise HTTPException(404, "候选记录不存在")
        conn.commit()
    return {"status": "REJECTED", "review_id": review_id}


@app.get("/api/management")
def management_dashboard() -> dict[str, Any]:
    data = dashboard(current_user_id="", current_time=None)
    items = data["items"]
    workload: dict[str, dict[str, Any]] = {}
    waiting: dict[str, int] = {}
    risks: dict[str, int] = {"critical":0,"high":0,"medium":0,"low":0,"none":0}
    states: dict[str, int] = {}
    for item in items:
        owner = item.get("owner_user_id")
        name = OWNER_NAMES.get(owner, owner or "未分配")
        bucket = workload.setdefault(name, {"owner_user_id": owner, "name": name, "total": 0, "urgent": 0, "waiting": 0, "overdue": 0})
        bucket["total"] += 1
        if item["action_state"] in {"DO_NOW","ESCALATE"}: bucket["urgent"] += 1
        if item["action_state"] == "WAITING_EXTERNAL": bucket["waiting"] += 1
        if item["action_state"] == "DO_NOW" and any("截止时间已过" in x or "超过" in x for x in item.get("priority_reasons") or []): bucket["overdue"] += 1
        target = item.get("waiting_on")
        if target: waiting[target] = waiting.get(target, 0) + 1
        risks[item.get("risk_level") or "none"] = risks.get(item.get("risk_level") or "none", 0) + 1
        states[item["action_state"]] = states.get(item["action_state"],0)+1
    escalations = [x for x in items if x["action_state"] == "ESCALATE" or x.get("risk_level") == "critical"]
    return {"summary": data["summary"], "workload": sorted(workload.values(), key=lambda x: (x["urgent"],x["total"]), reverse=True), "waiting_distribution": waiting, "risk_distribution": risks, "state_distribution": states, "escalations": escalations[:6], "generated_at": iso()}


@app.get("/api/settings")
def get_settings(user_id: str = Query("USER-1")) -> dict[str, Any]:
    defaults = {"accent": "blue", "compact": False, "show_demo": True, "notifications": {"urgent": True, "waiting_overdue": True, "writeback": True, "daily_summary": False}}
    with db() as conn:
        row = conn.execute("SELECT settings_json,updated_at FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    return {"user_id": user_id, "settings": json.loads(row["settings_json"]) if row else defaults, "updated_at": row["updated_at"] if row else None}


@app.put("/api/settings")
def put_settings(payload: AnyPayload, user_id: str = Query("USER-1")) -> dict[str, Any]:
    settings = payload.model_dump().get("settings") or payload.model_dump()
    timestamp = iso()
    with db() as conn:
        conn.execute("INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at", (user_id, json.dumps(settings, ensure_ascii=False), timestamp))
        conn.commit()
    return {"status": "saved", "user_id": user_id, "settings": settings, "updated_at": timestamp}


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    init_db()
    return {"status": "ok", "version": "3.0.0", "db": str(DB_PATH)}


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
