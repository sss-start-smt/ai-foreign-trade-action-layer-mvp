from __future__ import annotations

import html
import json
import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

import agent_api
from action_rules import decide_task
from analytics import build_analytics_summary, ensure_analytics_schema, track_event
from auth import (
    CurrentIdentity,
    get_current_identity,
    get_current_identity_optional,
    resolve_identity_for_testing,
    TRUSTED_USER_MAP,
    DEMO_TOKEN_MAP,
)

VERSION = "6.1.4.1"

SAFE_ORDER_FIELDS = {
    "current_progress",
    "current_node",
    "latest_supplier_commitment",
    "initialization_waiting_on",
    "initialization_promised_reply_at",
    "initialization_note",
}
HIGH_RISK_ORDER_FIELDS = {"requested_delivery_date"}
FIELD_LABELS = {
    "current_progress": "当前进度",
    "current_node": "当前节点",
    "latest_supplier_commitment": "最新工厂承诺",
    "initialization_waiting_on": "等待对象",
    "initialization_promised_reply_at": "承诺回复时间",
    "initialization_note": "最新进展备注",
    "requested_delivery_date": "客户正式交期",
}
NODE_KEYWORDS = [
    ("尚未开工", "未开工"),
    ("还没开工", "未开工"),
    ("未开工", "未开工"),
    ("物料采购", "物料采购"),
    ("面料到厂", "面料到厂"),
    ("备料", "备料中"),
    ("裁剪中", "裁剪中"),
    ("正在裁剪", "裁剪中"),
    ("生产中", "生产中"),
    ("正在生产", "生产中"),
    ("包装中", "包装中"),
    ("正在包装", "包装中"),
    ("验货", "验货准备"),
    ("订舱", "已订舱"),
    ("出运", "已出运"),
    ("发货", "已出运"),
]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _safe_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _value_text(field_name: str, value: Any) -> str:
    """Return a Coze-safe string without changing the typed value used for writeback."""
    if value is None:
        return ""
    if field_name == "current_progress" and isinstance(value, (int, float)) and not isinstance(value, bool):
        percent = float(value) * 100
        return f"{percent:g}%"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def ensure_v61_schema(conn) -> None:
    ensure_analytics_schema(conn)
    if getattr(conn, "is_pg", False):
        from database import table_exists
        required = ("bulk_update_batches", "bulk_update_candidates")
        missing = [name for name in required if not table_exists(conn, name)]
        if missing:
            raise RuntimeError(f"PostgreSQL v6.1 schema missing {missing}; run `alembic upgrade head`.")
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS bulk_update_batches (
            batch_id TEXT PRIMARY KEY,
            organization_id TEXT NOT NULL DEFAULT 'ORG-DEMO',
            current_user_id TEXT NOT NULL,
            current_role TEXT NOT NULL,
            source_text TEXT NOT NULL,
            parser_mode TEXT NOT NULL DEFAULT 'hybrid_rules_v1',
            status TEXT NOT NULL DEFAULT 'PARSED',
            summary_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            confirmed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS bulk_update_candidates (
            update_id TEXT PRIMARY KEY,
            batch_id TEXT NOT NULL,
            order_id TEXT,
            order_no TEXT,
            source_segment TEXT NOT NULL,
            match_confidence REAL NOT NULL DEFAULT 0,
            field_name TEXT NOT NULL,
            old_value_json TEXT,
            new_value_json TEXT,
            confidence REAL NOT NULL DEFAULT 0,
            risk_level TEXT NOT NULL DEFAULT 'normal',
            requires_approval INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'PENDING',
            edited_value_json TEXT,
            approval_id TEXT,
            created_at TEXT NOT NULL,
            decided_at TEXT,
            FOREIGN KEY(batch_id) REFERENCES bulk_update_batches(batch_id),
            FOREIGN KEY(order_id) REFERENCES orders(order_id)
        );
        CREATE INDEX IF NOT EXISTS idx_bulk_update_batch_status
            ON bulk_update_candidates(batch_id,status);
        CREATE INDEX IF NOT EXISTS idx_bulk_update_order
            ON bulk_update_candidates(order_id,created_at DESC);
        """
    )


def _split_segments(text: str) -> list[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    coarse = [x.strip(" \t，,。；;") for x in re.split(r"[\n；;]+", text) if x.strip(" \t，,。；;")]
    result: list[str] = []
    for segment in coarse:
        # Split a single long paragraph before a new explicit PO/order reference.
        pieces = re.split(r"(?=(?:PO[-_A-Z0-9]{2,}|订单\s*[A-Z0-9_-]{3,})\s*)", segment, flags=re.I)
        pieces = [p.strip(" ，,。") for p in pieces if p.strip(" ，,。")]
        result.extend(pieces or [segment])
    return result[:50]


def _order_rows(conn, actor: dict[str, Any], identity: CurrentIdentity | None = None) -> list[dict[str, Any]]:
    scope_sql, scope_params = agent_api._order_scope_sql(actor, identity)
    return [dict(r) for r in conn.execute(
        f"SELECT * FROM orders WHERE {scope_sql} AND UPPER(COALESCE(status,'ACTIVE')) NOT IN ('DONE','CLOSED','CANCELLED','COMPLETED')",
        scope_params,
    )]


def _order_match(segment: str, orders: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float, list[str]]:
    lowered = segment.lower()
    explicit: list[tuple[float, dict[str, Any], str]] = []
    for order in orders:
        order_no = str(order.get("order_no") or "").strip()
        if not order_no:
            continue
        if order_no.lower() in lowered:
            explicit.append((0.99, order, f"明确订单号：{order_no}"))
            continue
        suffix = re.sub(r"\D", "", order_no)[-3:]
        if suffix and re.search(rf"(?<!\d){re.escape(suffix)}(?!\d)", segment):
            explicit.append((0.90, order, f"订单号尾号：{suffix}"))
    if explicit:
        explicit.sort(key=lambda x: x[0], reverse=True)
        top_score = explicit[0][0]
        top = [x for x in explicit if x[0] == top_score]
        if len(top) == 1:
            return top[0][1], top_score, [top[0][2]]
        return None, 0.45, ["存在多个同等匹配订单，需要补充完整订单号"]

    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    for order in orders:
        score = 0.0
        reasons: list[str] = []
        for key, weight, label in (
            ("customer_name", 0.42, "客户"),
            ("product_name", 0.36, "产品"),
            ("factory_name", 0.30, "工厂"),
        ):
            value = str(order.get(key) or "").strip()
            if value and len(value) >= 2 and value.lower() in lowered:
                score += weight
                reasons.append(f"{label}匹配：{value}")
        if score:
            scored.append((min(score, 0.88), order, reasons))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return None, 0.0, ["未识别到可匹配的订单号、客户、产品或工厂"]
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 0.08:
        return None, scored[0][0], ["存在多个相近订单匹配，需要人工选择"]
    return scored[0][1], scored[0][0], scored[0][2]


def _relative_date(expr: str, current: datetime, *, default_hour: int = 10) -> datetime | None:
    text = expr.strip()
    dt = current.replace(hour=default_hour, minute=0, second=0, microsecond=0)
    if "今天" in text:
        result = dt
    elif "明天" in text:
        result = dt + timedelta(days=1)
    elif "后天" in text:
        result = dt + timedelta(days=2)
    else:
        weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
        m_week = re.search(r"(?:本|这|下)?周([一二三四五六日天])", text)
        if m_week:
            target = weekday_map[m_week.group(1)]
            delta = (target - current.weekday()) % 7
            if "下周" in text:
                delta = delta + 7 if delta else 7
            elif delta == 0:
                delta = 7
            result = dt + timedelta(days=delta)
        else:
            m = re.search(r"(?:(20\d{2})[-/.年])?(\d{1,2})[-/.月](\d{1,2})日?", text)
            if not m:
                return None
            year = int(m.group(1) or current.year)
            try:
                result = datetime(year, int(m.group(2)), int(m.group(3)), default_hour, tzinfo=current.tzinfo)
            except ValueError:
                return None
            if not m.group(1) and result.date() < current.date() - timedelta(days=30):
                try:
                    result = result.replace(year=current.year + 1)
                except ValueError:
                    return None
    if "上午" in text:
        result = result.replace(hour=10)
    elif "中午" in text:
        result = result.replace(hour=12)
    elif "下午" in text:
        result = result.replace(hour=15)
    elif "晚上" in text:
        result = result.replace(hour=19)
    m_time = re.search(r"(\d{1,2})[:：点](\d{1,2})?", text)
    if m_time:
        hour = int(m_time.group(1))
        minute = int(m_time.group(2) or 0)
        if "下午" in text and hour < 12:
            hour += 12
        result = result.replace(hour=min(hour, 23), minute=min(minute, 59))
    return result


def _date_expressions(segment: str) -> list[tuple[str, datetime]]:
    current = agent_api.now_cn()
    patterns = [
        r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?(?:上午|下午|晚上|中午)?(?:\d{1,2}[:：点]\d{0,2})?",
        r"\d{1,2}[-/.月]\d{1,2}日?(?:上午|下午|晚上|中午)?(?:\d{1,2}[:：点]\d{0,2})?",
        r"(?:今天|明天|后天|(?:本|这|下)?周[一二三四五六日天])(?:上午|下午|晚上|中午)?(?:\d{1,2}[:：点]\d{0,2})?",
    ]
    found: list[tuple[int, str, datetime]] = []
    for pattern in patterns:
        for m in re.finditer(pattern, segment):
            parsed = _relative_date(m.group(0), current)
            if parsed:
                found.append((m.start(), m.group(0), parsed))
    found.sort(key=lambda x: x[0])
    return [(raw, dt) for _, raw, dt in found]


def _extract_updates(segment: str, order: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {}

    progress_matches = list(re.finditer(r"(?:进度|完成|做到|做了|已完成)[^\d]{0,6}(\d{1,3})\s*%", segment))
    if not progress_matches:
        progress_matches = list(re.finditer(r"(\d{1,3})\s*%(?:左右|上下)?", segment))
    if progress_matches:
        value = min(100, max(0, int(progress_matches[-1].group(1)))) / 100
        updates.append({
            "field_name": "current_progress",
            "new_value": value,
            "confidence": 0.96 if "进度" in segment or "完成" in segment else 0.86,
            "risk_level": "normal",
            "requires_approval": False,
            "source_quote": progress_matches[-1].group(0),
        })

    for keyword, normalized in NODE_KEYWORDS:
        if keyword in segment:
            updates.append({
                "field_name": "current_node",
                "new_value": normalized,
                "confidence": 0.90,
                "risk_level": "normal",
                "requires_approval": False,
                "source_quote": keyword,
            })
            break

    dates = _date_expressions(segment)
    # Supplier commitment: choose the last date in a segment mentioning supplier/factory completion.
    if dates and any(x in segment for x in ("完工", "做完", "交货", "出货", "发货")):
        raw, dt = dates[-1]
        updates.append({
            "field_name": "latest_supplier_commitment",
            "new_value": dt.date().isoformat(),
            "confidence": 0.92 if any(x in segment for x in ("承诺", "答应", "确定", "确认")) else 0.82,
            "risk_level": "normal",
            "requires_approval": False,
            "source_quote": raw,
        })

    # Customer formal delivery date changes require manager approval.
    if dates and "客户" in segment and "交期" in segment and any(x in segment for x in ("改到", "改成", "调整到", "延到", "同意")):
        raw, dt = dates[-1]
        updates.append({
            "field_name": "requested_delivery_date",
            "new_value": dt.date().isoformat(),
            "confidence": 0.88,
            "risk_level": "high",
            "requires_approval": True,
            "source_quote": raw,
        })

    waiting_party = None
    if "客户" in segment and any(x in segment for x in ("回复", "确认", "给结果", "反馈")):
        waiting_party = "customer"
    elif any(x in segment for x in ("工厂", "供应商")) and any(x in segment for x in ("回复", "确认", "给排期", "给时间")):
        waiting_party = "supplier"
    if waiting_party:
        updates.append({
            "field_name": "initialization_waiting_on",
            "new_value": waiting_party,
            "confidence": 0.91,
            "risk_level": "normal",
            "requires_approval": False,
            "source_quote": "客户" if waiting_party == "customer" else "工厂/供应商",
        })
        if dates:
            raw, dt = dates[-1]
            updates.append({
                "field_name": "initialization_promised_reply_at",
                "new_value": dt.isoformat(timespec="seconds"),
                "confidence": 0.86,
                "risk_level": "normal",
                "requires_approval": False,
                "source_quote": raw,
            })

    person = re.search(r"([\u4e00-\u9fa5]{1,4}(?:师傅|经理|主管|先生|女士|总))(?:负责|跟进|对接)", segment)
    if person:
        metadata["contact_person"] = person.group(1)

    # Always retain a concise progress note. It is safe and gives audit evidence.
    updates.append({
        "field_name": "initialization_note",
        "new_value": segment[:500],
        "confidence": 1.0,
        "risk_level": "normal",
        "requires_approval": False,
        "source_quote": segment[:160],
    })

    # Deduplicate by field, keeping the last and most context-specific value.
    dedup: dict[str, dict[str, Any]] = {}
    for item in updates:
        dedup[item["field_name"]] = item
    return list(dedup.values()), metadata


def _coze_safe_bulk_update_response(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize mixed typed update values for Coze response-schema validation.

    The core parser deliberately keeps typed values (for example 0.82 for
    progress and ISO strings for dates) because the website confirmation flow
    needs those values for writeback. Coze output schemas do not support a
    reliable union such as string | number | null, so the plugin response uses
    readable strings while the website endpoint continues to receive typed
    values.
    """
    safe_result = {**result, "orders": []}
    for order in result.get("orders") or []:
        safe_order = {**order, "updates": []}
        for update in order.get("updates") or []:
            field_name = str(update.get("field_name") or "")
            safe_update = {
                **update,
                "old_value": _value_text(field_name, update.get("old_value")),
                "new_value": _value_text(field_name, update.get("new_value")),
                "new_value_text": _value_text(field_name, update.get("new_value")),
            }
            safe_order["updates"].append(safe_update)
        safe_result["orders"].append(safe_order)
    return safe_result


def parse_bulk_order_updates_logic(conn, payload: dict[str, Any], *, persist: bool = True, identity: CurrentIdentity | None = None) -> dict[str, Any]:
    ensure_v61_schema(conn)
    actor = agent_api.actor(payload, identity)
    text = str(payload.get("text") or payload.get("update_text") or "").strip()
    if not text:
        raise HTTPException(422, "缺少需要解析的批量进展文本")
    if len(text) > 20000:
        raise HTTPException(422, "单次文本不能超过20000个字符")
    started = time.perf_counter()
    segments = _split_segments(text)
    orders = _order_rows(conn, actor, identity)
    batch_id = _new_id("BUP")
    parsed_orders: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    candidate_count = 0
    high_risk_count = 0

    track_event(
        conn,
        "bulk_update_submitted",
        organization_id=actor["organization_id"],
        user_id=actor["current_user_id"],
        user_role=actor["current_role"],
        run_id=payload.get("run_id"),
        source=str(payload.get("source") or "agent_tool"),
        properties={"text_length": len(text), "segment_count": len(segments)},
    )
    if persist:
        conn.execute(
            """
            INSERT INTO bulk_update_batches(
                batch_id,organization_id,current_user_id,current_role,source_text,
                parser_mode,status,summary_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                batch_id,
                actor["organization_id"],
                actor["current_user_id"],
                actor["current_role"],
                text,
                "hybrid_rules_v1",
                "PARSING",
                "{}",
                agent_api.iso(),
            ),
        )

    for segment in segments:
        order, match_confidence, match_reasons = _order_match(segment, orders)
        if not order:
            unmatched.append({
                "source_segment": segment,
                "match_confidence": round(match_confidence, 2),
                "reason": "；".join(match_reasons),
            })
            continue
        updates, metadata = _extract_updates(segment, order)
        item = {
            "order_id": order["order_id"],
            "order_no": order.get("order_no"),
            "customer_name": order.get("customer_name"),
            "match_confidence": round(match_confidence, 2),
            "match_reasons": match_reasons,
            "source_segment": segment,
            "metadata": metadata,
            "updates": [],
        }
        for update in updates:
            field_name = update["field_name"]
            update_id = _new_id("BUPD")
            old_value = order.get(field_name)
            candidate = {
                "update_id": update_id,
                "field_name": field_name,
                "field_label": FIELD_LABELS.get(field_name, field_name),
                "old_value": old_value,
                "new_value": update["new_value"],
                "new_value_text": _value_text(field_name, update["new_value"]),
                "confidence": update["confidence"],
                "risk_level": update["risk_level"],
                "requires_approval": bool(update["requires_approval"]),
                "source_quote": update["source_quote"],
                "status": "PENDING",
            }
            item["updates"].append(candidate)
            candidate_count += 1
            high_risk_count += int(candidate["requires_approval"])
            if persist:
                conn.execute(
                    """
                    INSERT INTO bulk_update_candidates(
                        update_id,batch_id,order_id,order_no,source_segment,match_confidence,
                        field_name,old_value_json,new_value_json,confidence,risk_level,
                        requires_approval,status,created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        update_id,
                        batch_id,
                        order["order_id"],
                        order.get("order_no"),
                        segment,
                        match_confidence,
                        field_name,
                        _json(old_value),
                        _json(update["new_value"]),
                        update["confidence"],
                        update["risk_level"],
                        int(bool(update["requires_approval"])),
                        "PENDING",
                        agent_api.iso(),
                    ),
                )
        parsed_orders.append(item)

    duration_ms = int((time.perf_counter() - started) * 1000)
    summary = {
        "segment_count": len(segments),
        "matched_order_count": len(parsed_orders),
        "unmatched_segment_count": len(unmatched),
        "candidate_field_count": candidate_count,
        "high_risk_field_count": high_risk_count,
        "duration_ms": duration_ms,
    }
    if persist:
        conn.execute(
            "UPDATE bulk_update_batches SET status='PARSED',summary_json=? WHERE batch_id=?",
            (_json(summary), batch_id),
        )
    track_event(
        conn,
        "bulk_update_parsed",
        organization_id=actor["organization_id"],
        user_id=actor["current_user_id"],
        user_role=actor["current_role"],
        run_id=payload.get("run_id"),
        source=str(payload.get("source") or "agent_tool"),
        properties=summary,
    )
    return {
        "batch_id": batch_id,
        "status": "PARSED",
        "parser_mode": "hybrid_rules_v1",
        "summary": summary,
        "orders": parsed_orders,
        "unmatched_segments": unmatched,
        "confirmation_required": True,
        "review_url": f"/bulk-update?batch_id={batch_id}",
        "safety_note": "普通状态字段经人工确认后写回；客户正式交期等高风险字段只创建审批，不直接修改。",
    }


def confirm_bulk_updates_logic(conn, payload: dict[str, Any], identity: CurrentIdentity | None = None) -> dict[str, Any]:
    ensure_v61_schema(conn)
    actor = agent_api.actor(payload, identity)
    batch_id = str(payload.get("batch_id") or "").strip()
    if not batch_id:
        raise HTTPException(422, "缺少batch_id")
    batch = conn.execute("SELECT * FROM bulk_update_batches WHERE batch_id=?", (batch_id,)).fetchone()
    if not batch:
        raise HTTPException(404, "批量更新批次不存在")
    # ENFORCE: Organization boundary check
    batch_org = str(batch["organization_id"] or "").strip() if batch["organization_id"] else ""
    if batch_org and identity:
        agent_api.require_same_org(identity, batch_org)
    if not agent_api.is_manager(actor["current_user_id"], actor["current_role"]) and batch["current_user_id"] != actor["current_user_id"]:
        raise HTTPException(403, "无权确认其他用户的批量更新")

    decisions = payload.get("decisions") or []
    if not isinstance(decisions, list) or not decisions:
        raise HTTPException(422, "请至少确认或拒绝一条更新候选")

    accepted = edited = rejected = 0
    updated_orders: set[str] = set()
    approval_items: list[dict[str, Any]] = []
    order_messages_written: set[str] = set()

    for decision in decisions:
        update_id = str(decision.get("update_id") or "").strip()
        action = str(decision.get("decision") or "REJECT").upper()
        row = conn.execute("SELECT * FROM bulk_update_candidates WHERE update_id=? AND batch_id=?", (update_id, batch_id)).fetchone()
        if not row:
            raise HTTPException(404, f"更新候选不存在：{update_id}")
        candidate = dict(row)
        if candidate["status"] != "PENDING":
            continue
        order = agent_api._assert_order_access(conn, candidate["order_id"], actor, identity)
        if action == "REJECT":
            conn.execute(
                "UPDATE bulk_update_candidates SET status='REJECTED',decided_at=? WHERE update_id=?",
                (agent_api.iso(), update_id),
            )
            rejected += 1
            continue
        if action not in {"ACCEPT", "EDIT"}:
            raise HTTPException(422, "decision必须为ACCEPT、EDIT或REJECT")
        final_value = decision.get("final_value") if action == "EDIT" else _safe_json(candidate["new_value_json"], None)
        field_name = candidate["field_name"]

        if field_name in HIGH_RISK_ORDER_FIELDS or int(candidate["requires_approval"] or 0) == 1:
            approval_payload = {
                **actor,
                "run_id": payload.get("run_id"),
                "order_id": candidate["order_id"],
                "action_type": "UPDATE_ORDER",
                "high_risk": True,
                "idempotency_key": f"BULK_UPDATE:{update_id}",
                "action_payload": {
                    "order_id": candidate["order_id"],
                    "updates": {field_name: final_value},
                    "source": "bulk_update_confirmation",
                    "update_id": update_id,
                },
            }
            approval = agent_api.create_approval_logic(conn, approval_payload, identity=identity)
            approval_items.append(approval)
            conn.execute(
                """
                UPDATE bulk_update_candidates
                SET status='APPROVAL_PENDING',edited_value_json=?,approval_id=?,decided_at=?
                WHERE update_id=?
                """,
                (_json(final_value) if action == "EDIT" else None, approval["approval_id"], agent_api.iso(), update_id),
            )
        elif field_name in SAFE_ORDER_FIELDS:
            conn.execute(
                f"UPDATE orders SET {field_name}=?,action_readiness='READY_FOR_RANKING',last_dynamic_update_at=?,updated_at=? WHERE order_id=?",
                (final_value, agent_api.iso(), agent_api.iso(), candidate["order_id"]),
            )
            conn.execute(
                """
                UPDATE bulk_update_candidates
                SET status='CONFIRMED',edited_value_json=?,decided_at=?
                WHERE update_id=?
                """,
                (_json(final_value) if action == "EDIT" else None, agent_api.iso(), update_id),
            )
            updated_orders.add(candidate["order_id"])
        else:
            raise HTTPException(422, f"字段暂不支持写回：{field_name}")

        if candidate["order_id"] not in order_messages_written:
            conn.execute(
                """
                INSERT INTO source_messages(message_id,order_id,organization_id,source_channel,sender_role,message_type,raw_content,source_time,created_at)
                VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    _new_id("MSG"),
                    candidate["order_id"],
                    str(order.get("organization_id") or (identity.organization_id if identity else batch_org) or "ORG-QUARANTINE"),
                    "bulk_text",
                    "operator",
                    "bulk_progress_update",
                    candidate["source_segment"],
                    agent_api.iso(),
                    agent_api.iso(),
                ),
            )
            order_messages_written.add(candidate["order_id"])

        accepted += int(action == "ACCEPT")
        edited += int(action == "EDIT")
        agent_api.audit_event(
            conn,
            "order",
            candidate["order_id"],
            "BULK_UPDATE_CONFIRMED" if field_name not in HIGH_RISK_ORDER_FIELDS else "BULK_UPDATE_APPROVAL_CREATED",
            {
                "batch_id": batch_id,
                "update_id": update_id,
                "field_name": field_name,
                "decision": action,
                "final_value": final_value,
            },
            actor["current_user_id"],
        )

    pending = conn.execute(
        "SELECT COUNT(*) FROM bulk_update_candidates WHERE batch_id=? AND status='PENDING'",
        (batch_id,),
    ).fetchone()[0]
    status = "PARTIAL_CONFIRMED" if pending else "CONFIRMED"
    conn.execute(
        "UPDATE bulk_update_batches SET status=?,confirmed_at=? WHERE batch_id=?",
        (status, agent_api.iso(), batch_id),
    )
    total_candidates = conn.execute(
        "SELECT COUNT(*) FROM bulk_update_candidates WHERE batch_id=?",
        (batch_id,),
    ).fetchone()[0]
    properties = {
        "candidate_field_count": total_candidates,
        "accepted_count": accepted,
        "edited_count": edited,
        "rejected_count": rejected,
        "approval_created_count": len(approval_items),
        "updated_order_count": len(updated_orders),
    }
    track_event(
        conn,
        "bulk_update_confirmed",
        organization_id=actor["organization_id"],
        user_id=actor["current_user_id"],
        user_role=actor["current_role"],
        run_id=payload.get("run_id"),
        source=str(payload.get("source") or "website"),
        properties=properties,
    )
    if rejected:
        track_event(
            conn,
            "bulk_update_rejected",
            organization_id=actor["organization_id"],
            user_id=actor["current_user_id"],
            user_role=actor["current_role"],
            source=str(payload.get("source") or "website"),
            properties={"batch_id": batch_id, "rejected_count": rejected},
        )
    return {
        "batch_id": batch_id,
        "status": status,
        **properties,
        "updated_order_ids": sorted(updated_orders),
        "approvals": approval_items,
        "next_step": "重新运行diagnose_priority_orders，查看更新后的Top 7。",
    }



def _attach_shared_task_priority(conn, items: list[dict[str, Any]], actor: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge the shared FT04 task-action priority into order-level diagnosis.

    Agent anomaly rules remain evidence signals, while waiting windows, deadlines,
    responsibility and task action states come from the exact same decide_task()
    function used by the normal task workspace.
    """
    current = agent_api.now_cn()
    enriched: list[dict[str, Any]] = []
    for item in items:
        order_id = item.get("order_id")
        rows = conn.execute(
            "SELECT * FROM tasks WHERE related_order_id=? AND status!='DONE' ORDER BY updated_at DESC",
            (order_id,),
        ).fetchall() if order_id else []
        evaluated: list[dict[str, Any]] = []
        for row in rows:
            task = dict(row)
            effective_user = (task.get("owner_user_id") or actor["current_user_id"]) if agent_api.is_manager(actor["current_user_id"], actor["current_role"]) else actor["current_user_id"]
            evaluated.append(decide_task(task, current, effective_user))
        actionable = [x for x in evaluated if not x.get("ranking_suppressed") and x.get("action_state") not in {"DONE", "NOT_MY_RESPONSIBILITY"}]
        top_task = max(actionable, key=lambda x: float(x.get("priority_score") or 0), default=None)
        anomaly_score = float(item.get("priority_score") or 0)
        task_score = max(0.0, float(top_task.get("priority_score") or 0)) if top_task else 0.0
        combined = round(anomaly_score + task_score, 2)
        reasons = list(item.get("priority_reasons") or [])
        if top_task:
            for reason in top_task.get("priority_reasons") or []:
                if reason not in reasons:
                    reasons.append(reason)
        enriched.append({
            **item,
            "anomaly_priority_score": anomaly_score,
            "task_priority_score": task_score,
            "priority_score": combined,
            "task_action_state": top_task.get("action_state") if top_task else None,
            "top_task_id": top_task.get("task_id") if top_task else None,
            "top_task_title": top_task.get("title") if top_task else None,
            "priority_reasons": reasons[:5],
            "ranking_rule_version": "FT04_SHARED_V1",
        })
    enriched.sort(key=lambda x: (-float(x.get("priority_score") or 0), str(x.get("order_no") or "")))
    for index, item in enumerate(enriched, 1):
        item["rank"] = index
    return enriched

def diagnose_priority_orders_logic(conn, payload: dict[str, Any], identity: CurrentIdentity | dict[str, Any] | None = None) -> dict[str, Any]:
    """Composite diagnosis: one Agent tool call performs screening, evidence lookup,
    anomaly creation and deterministic Top-N ranking.
    """
    ensure_v61_schema(conn)
    actor = agent_api.actor(payload, identity)
    due_days = max(1, min(int(payload.get("due_within_days") or 14), 90))
    top_n = max(1, min(int(payload.get("top_n") or 7), 7))
    scan_limit = max(top_n, min(int(payload.get("scan_limit") or 50), 200))
    started = time.perf_counter()
    screened = agent_api.list_candidate_orders_logic(conn, {**payload, **actor, "due_within_days": due_days, "limit": scan_limit}, identity=identity)
    all_candidates: list[dict[str, Any]] = []
    for order in screened["items"]:
        result = agent_api.build_anomaly_logic(
            conn,
            {**payload, **actor, "order_id": order["order_id"]},
            persist=bool(payload.get("persist_candidates", True)),
            identity=identity,
        )
        all_candidates.extend(result["items"])
    # D14.2: preserve anomaly-candidate persistence/audit, but select the visible
    # Top-N from the same Risk Attention ranking used by D7 and the controlled
    # Agent read tool.  This removes the historical split-brain where the Agent
    # page could rank a different order first from the product risk engine.
    from d7_risk_engine import run_d7_pipeline

    # Backend-managed Agent plans carry a trusted identity dict using
    # current_user_id/current_role aliases. Normalize it to the D7 identity
    # contract before entering the risk engine; HTTP routes may already pass a
    # CurrentIdentity object and remain unchanged.
    d7_identity = identity
    if isinstance(identity, dict):
        d7_identity = {
            "user_id": identity.get("user_id") or identity.get("current_user_id") or actor.get("current_user_id"),
            "organization_id": identity.get("organization_id") or actor.get("organization_id"),
            "role": identity.get("role") or identity.get("current_role") or actor.get("current_role"),
        }
    d7 = run_d7_pipeline(
        conn,
        d7_identity,
        top_n=top_n,
        due_within_days=due_days,
    )
    attention_items = (
        d7.get("risk_attention_items")
        or d7.get("my_action_items")
        or d7.get("team_action_items")
        or d7.get("items")
        or []
    )

    candidates_by_order: dict[str, list[dict[str, Any]]] = {}
    for candidate in all_candidates:
        if candidate.get("anomaly_type") == "INFORMATION_GAP":
            continue
        candidates_by_order.setdefault(str(candidate.get("order_id") or ""), []).append(candidate)

    ranked: list[dict[str, Any]] = []
    for index, attention in enumerate(attention_items[:top_n], 1):
        order_id = str(attention.get("order_id") or "")
        order_candidates = candidates_by_order.get(order_id) or []
        if order_candidates:
            merged_candidates = agent_api.aggregate_order_candidates(order_candidates, top_n=7)["risk_items"]
            base = dict(merged_candidates[0]) if merged_candidates else {}
        else:
            base = {
                "candidate_id": None,
                "order_id": order_id,
                "order_no": attention.get("order_no"),
                "customer_name": attention.get("customer_name"),
                "anomaly_type": attention.get("primary_anomaly_type"),
                "primary_anomaly_type": attention.get("primary_anomaly_type"),
                "secondary_anomaly_types": attention.get("secondary_anomaly_types") or [],
                "order_anomaly_count": attention.get("order_anomaly_count") or len(attention.get("risk_signals") or []),
                "status": "DETERMINISTIC_RISK_SIGNAL",
                "evidence": attention.get("evidence") or [],
                "missing_information": attention.get("missing_information") or [],
            }
        ranked.append({
            **base,
            "order_id": order_id,
            "order_no": attention.get("order_no") or base.get("order_no"),
            "customer_name": attention.get("customer_name") or base.get("customer_name"),
            "rank": index,
            "priority_score": attention.get("risk_attention_score") or attention.get("priority_score"),
            "risk_attention_score": attention.get("risk_attention_score") or attention.get("priority_score"),
            "risk_attention_band": attention.get("risk_attention_band"),
            "priority_reasons": attention.get("risk_attention_reasons") or attention.get("priority_reasons") or base.get("priority_reasons") or [],
            "action_bucket": attention.get("action_bucket"),
            "current_actionability": attention.get("current_actionability"),
            "governance_escalation_required": attention.get("governance_escalation_required"),
            "severity": attention.get("severity") or base.get("severity"),
            "recommended_action": attention.get("recommended_action") or base.get("recommended_action"),
            "ranking_rule_version": attention.get("ranking_rule_version") or "D14_2_ATTENTION_V1",
        })

    information_gaps = d7.get("information_gaps") or []
    duration_ms = int((time.perf_counter() - started) * 1000)
    result = {
        "scope": screened["scope"],
        "due_within_days": due_days,
        "screened_order_count": screened["count"],
        "anomaly_candidate_count": len(all_candidates),
        "anomaly_signal_count": len(all_candidates),
        "risk_order_count": int(d7.get("risk_order_count") or len(ranked)),
        "information_gap_order_count": int(d7.get("information_gap_order_count") or len(information_gaps)),
        "information_gaps": information_gaps,
        "count": len(ranked),
        "items": ranked,
        "selection_strategy": {
            **(d7.get("selection_strategy") or {}),
            "candidate_pool": "当前用户有权限的活跃订单；异常候选仍用于证据与人工确认，但可见Top-N由统一Risk Attention排序选择",
            "ranking": "Risk Attention先排序；Action Bucket只说明当前怎么做；Governance Escalation只说明谁需要介入",
            "not_padded": True,
            "max_items": top_n,
            "unit": "unique_order",
            "ranking_rule_version": "D14_2_ATTENTION_V1",
        },
        "human_confirmation_required": True,
        "duration_ms": duration_ms,
    }
    track_event(
        conn,
        "priority_diagnosis_completed",
        organization_id=actor["organization_id"],
        user_id=actor["current_user_id"],
        user_role=actor["current_role"],
        run_id=payload.get("run_id"),
        source=str(payload.get("source") or "agent_tool"),
        properties={
            "screened_order_count": screened["count"],
            "anomaly_candidate_count": len(all_candidates),
            "risk_order_count": len(ranked),
            "information_gap_order_count": int(d7.get("information_gap_order_count") or len(information_gaps)),
            "top_count": len(ranked),
            "duration_ms": duration_ms,
        },
    )
    return result


BULK_UPDATE_HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>批量更新订单进展 · FlowOrder</title><style>
:root{font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;color:#17201d;background:#f4f5f3}*{box-sizing:border-box}body{margin:0}.top{background:#0a2d26;color:#fff;padding:22px 5vw;display:flex;align-items:center;gap:18px}.top a{color:#d8ede6;text-decoration:none}.top h1{font-size:22px;margin:0}.wrap{max-width:1120px;margin:26px auto;padding:0 18px 70px}.panel{background:#fff;border:1px solid #dce2de;border-radius:16px;padding:22px;margin-bottom:16px;box-shadow:0 8px 24px rgba(24,52,45,.05)}h2{margin:0 0 8px;font-size:18px}p{color:#69746f;line-height:1.7}textarea{width:100%;min-height:180px;border:1px solid #cdd6d0;border-radius:12px;padding:14px;font:inherit;line-height:1.7}button{border:0;border-radius:10px;padding:11px 16px;font-weight:700;cursor:pointer}.primary{background:#0a2d26;color:#fff}.soft{background:#e8efeb;color:#17372f}.actions{display:flex;gap:10px;flex-wrap:wrap}.summary{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:16px 0}.metric{background:#f7f9f7;border-radius:12px;padding:14px}.metric strong{display:block;font-size:24px}.order-card{border:1px solid #dfe5e1;border-radius:14px;padding:16px;margin-top:12px}.order-card h3{margin:0 0 5px}.segment{background:#f7f8f6;padding:10px;border-radius:10px;color:#59665f}.update{display:grid;grid-template-columns:28px 1.2fr 1fr 32px 1fr 100px;gap:10px;align-items:center;border-top:1px solid #edf0ed;padding:11px 0}.tag{font-size:11px;border-radius:999px;padding:4px 8px;background:#e8efeb;display:inline-flex}.high{background:#f8e9e6;color:#9a3e35}.muted{color:#738078;font-size:12px}.unmatched{border-left:4px solid #c28b38;padding:10px;background:#fbf4e8;margin-top:8px}.hidden{display:none}.toast{position:fixed;right:20px;bottom:20px;background:#17201d;color:#fff;padding:12px 16px;border-radius:10px;display:none}.toast.show{display:block}@media(max-width:760px){.summary{grid-template-columns:repeat(2,1fr)}.update{grid-template-columns:28px 1fr}.update>*{grid-column:auto}}
</style></head><body><div class="top"><a href="/">← 返回系统</a><h1>批量更新订单进展</h1></div><main class="wrap"><section class="panel"><h2>粘贴一段工作播报</h2><p>系统会拆分订单、匹配订单号并生成字段更新候选。普通状态必须人工确认后写回；客户正式交期等高风险字段只创建审批。</p><textarea id="text">PO-AGENT-001工厂说现在做到60%，原来答应今天完工，但现在说要到8月5日。
PO-AGENT-002的彩盒客户还没确认，客户说明天下午回复。
PO-AGENT-008现在还没有正式开工，工厂周五给排期，交期可能有风险。</textarea><div class="actions"><button id="parse" class="primary">解析更新</button><a href="/validation" style="align-self:center;color:#0a2d26">查看数据与验证</a></div></section><section id="result" class="panel hidden"></section></main><div id="toast" class="toast"></div><script>
const $=s=>document.querySelector(s);let currentBatch=null;function toast(t){const x=$('#toast');x.textContent=t;x.classList.add('show');setTimeout(()=>x.classList.remove('show'),2600)}function esc(v){return String(v??'').replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}function display(v){if(typeof v==='number'&&v>=0&&v<=1)return Math.round(v*100)+'%';return v??'—'}
async function api(url,opt={}){const r=await fetch(url,{headers:{'Content-Type':'application/json',...(opt.headers||{})},...opt});const d=await r.json();if(!r.ok)throw new Error(d.detail||'请求失败');return d}
async function loadExistingBatch(){const id=new URLSearchParams(location.search).get('batch_id');if(!id)return;try{const user=localStorage.getItem('currentUserId')||'USER-1';const role=user==='MANAGER-1'?'manager':'operator';const raw=await api(`/api/agent/bulk-updates/${encodeURIComponent(id)}?current_user_id=${encodeURIComponent(user)}&current_role=${role}`);const orders=[];const byOrder={};raw.items.filter(x=>x.status==='PENDING').forEach(x=>{if(!byOrder[x.order_id])byOrder[x.order_id]={order_id:x.order_id,order_no:x.order_no,customer_name:'',match_confidence:x.match_confidence,source_segment:x.source_segment,updates:[]};byOrder[x.order_id].updates.push({update_id:x.update_id,field_name:x.field_name,field_label:({'current_progress':'当前进度','current_node':'当前节点','latest_supplier_commitment':'最新工厂承诺','initialization_waiting_on':'等待对象','initialization_promised_reply_at':'承诺回复时间','initialization_note':'最新进展备注','requested_delivery_date':'客户正式交期'})[x.field_name]||x.field_name,old_value:x.old_value,new_value:x.new_value,confidence:x.confidence,risk_level:x.risk_level,requires_approval:!!x.requires_approval,status:x.status})});Object.values(byOrder).forEach(x=>orders.push(x));currentBatch={batch_id:raw.batch_id,summary:raw.summary,orders,unmatched_segments:[]};render(currentBatch);toast('已加载Agent解析结果，请确认')}catch(e){toast(e.message)}}
$('#parse').onclick=async()=>{const b=$('#parse');b.disabled=true;try{const d=await api('/api/agent/bulk-updates/parse',{method:'POST',body:JSON.stringify({text:$('#text').value,current_user_id:localStorage.getItem('currentUserId')||'USER-1',current_role:(localStorage.getItem('currentUserId')==='MANAGER-1'?'manager':'operator'),source:'website'})});currentBatch=d;render(d);toast('解析完成，请逐项确认')}catch(e){toast(e.message)}finally{b.disabled=false}};
function render(d){const r=$('#result');r.classList.remove('hidden');r.innerHTML=`<h2>更新候选确认</h2><div class="summary"><div class="metric"><span>匹配订单</span><strong>${d.summary.matched_order_count}</strong></div><div class="metric"><span>候选字段</span><strong>${d.summary.candidate_field_count}</strong></div><div class="metric"><span>高风险字段</span><strong>${d.summary.high_risk_field_count}</strong></div><div class="metric"><span>未匹配片段</span><strong>${d.summary.unmatched_segment_count}</strong></div></div>${d.orders.map(o=>`<article class="order-card"><h3>${esc(o.order_no)} · ${esc(o.customer_name||'')}</h3><div class="muted">订单匹配置信度 ${Math.round(o.match_confidence*100)}%</div><p class="segment">${esc(o.source_segment)}</p>${o.updates.map(u=>`<div class="update"><input type="checkbox" checked data-id="${u.update_id}"><strong>${esc(u.field_label)}</strong><span>${esc(display(u.old_value))}</span><span>→</span><span contenteditable="true" data-value="${u.update_id}">${esc(display(u.new_value))}</span><span class="tag ${u.requires_approval?'high':''}">${u.requires_approval?'需审批':'可确认'}</span></div>`).join('')}</article>`).join('')}${d.unmatched_segments.map(x=>`<div class="unmatched"><strong>未匹配：</strong>${esc(x.source_segment)}<div class="muted">${esc(x.reason)}</div></div>`).join('')}<div class="actions" style="margin-top:18px"><button id="confirm" class="primary">确认选中更新</button><button id="allReject" class="soft">全部取消</button></div>`;$('#confirm').onclick=confirm;$('#allReject').onclick=()=>{r.querySelectorAll('input[type=checkbox]').forEach(x=>x.checked=false);confirm()}}
async function confirm(){const decisions=[];document.querySelectorAll('[data-id]').forEach(cb=>{const id=cb.dataset.id;const original=currentBatch.orders.flatMap(o=>o.updates).find(u=>u.update_id===id);const shown=document.querySelector(`[data-value="${id}"]`).textContent.trim();let finalValue=original.new_value;const originalDisplay=String(display(original.new_value));let decision=cb.checked?'ACCEPT':'REJECT';if(cb.checked&&shown!==originalDisplay){decision='EDIT';finalValue=shown.endsWith('%')?Number(shown.replace('%',''))/100:shown}decisions.push({update_id:id,decision,final_value:finalValue})});try{const d=await api('/api/agent/bulk-updates/confirm',{method:'POST',body:JSON.stringify({batch_id:currentBatch.batch_id,decisions,current_user_id:localStorage.getItem('currentUserId')||'USER-1',current_role:(localStorage.getItem('currentUserId')==='MANAGER-1'?'manager':'operator'),source:'website'})});toast(`已更新${d.updated_order_count}笔订单，创建${d.approval_created_count}条审批`);$('#result').innerHTML=`<h2>批量更新完成</h2><p>已更新订单：${d.updated_order_ids.map(esc).join('、')||'无'}；待审批动作：${d.approval_created_count}。</p><div class="actions"><a href="/#agent">查看Agent诊断</a><a href="/validation">查看埋点数据</a></div>`}catch(e){toast(e.message)}}
loadExistingBatch();
</script></body></html>'''


VALIDATION_HTML = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>数据与验证 · FlowOrder</title><style>:root{font-family:Inter,"PingFang SC","Microsoft YaHei",sans-serif;color:#17201d;background:#f4f5f3}*{box-sizing:border-box}body{margin:0}.top{background:#0a2d26;color:#fff;padding:22px 5vw;display:flex;gap:18px;align-items:center}.top a{color:#d8ede6;text-decoration:none}.wrap{max-width:1180px;margin:26px auto;padding:0 18px 70px}.panel{background:#fff;border:1px solid #dce2de;border-radius:16px;padding:22px;margin-bottom:16px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.metric{background:#f7f9f7;border-radius:12px;padding:15px}.metric strong{font-size:26px;display:block;margin-top:5px}.muted{color:#6e7b74;font-size:12px}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #edf0ed;text-align:left;font-size:13px}@media(max-width:780px){.grid{grid-template-columns:repeat(2,1fr)}}</style></head><body><div class="top"><a href="/">← 返回系统</a><h1>数据与验证</h1></div><main class="wrap"><section class="panel"><h2>激活漏斗</h2><div id="activation" class="grid"></div></section><section class="panel"><h2>AI价值</h2><div id="value" class="grid"></div></section><section class="panel"><h2>系统质量</h2><div id="quality" class="grid"></div></section><section class="panel"><h2>最近事件</h2><table><thead><tr><th>时间</th><th>事件</th><th>用户</th><th>订单/运行</th><th>属性</th></tr></thead><tbody id="events"></tbody></table></section></main><script>const pct=v=>v==null?'—':Math.round(v*100)+'%';const metric=(n,v,t='')=>`<div class="metric"><span>${n}</span><strong>${v??'—'}</strong><div class="muted">${t}</div></div>`;fetch('/api/analytics/summary?days=30&organization_id=ORG-DEMO').then(r=>r.json()).then(d=>{const a=d.activation_funnel,v=d.ai_value,q=d.system_quality;document.querySelector('#activation').innerHTML=metric('导入成功用户',a.order_import_completed_users)+metric('提交批量进展用户',a.bulk_update_submitted_users)+metric('确认AI更新用户',a.bulk_update_confirmed_users)+metric('形成有效行动用户',a.effective_action_users);document.querySelector('#value').innerHTML=metric('候选字段',v.candidate_field_count)+metric('候选采用率',pct(v.candidate_adoption_rate))+metric('直接采用率',pct(v.direct_adoption_rate))+metric('审批通过率',pct(v.approval_approval_rate));document.querySelector('#quality').innerHTML=metric('Agent启动',q.agent_run_started)+metric('组合诊断完成',q.priority_diagnosis_completed)+metric('Agent超时',q.agent_run_timeout)+metric('P95响应',q.duration_p95_ms==null?'—':q.duration_p95_ms+'ms');document.querySelector('#events').innerHTML=d.recent_events.map(e=>`<tr><td>${e.server_timestamp}</td><td>${e.event_name}</td><td>${e.user_id||'—'}</td><td>${e.order_id||e.run_id||'—'}</td><td><code>${JSON.stringify(e.properties)}</code></td></tr>`).join('')})</script></body></html>'''


def register_v61_extensions(app) -> None:
    if getattr(app.state, "v61_extensions_registered", False):
        return
    app.state.v61_extensions_registered = True
    router = APIRouter()

    # Use the secure token-only identity resolution from auth.py
    resolve_identity_dependency = get_current_identity

    @router.get("/api/v61/status")
    def v61_status() -> dict[str, Any]:
        return {
            "version": VERSION,
            "new_agent_tools": ["parse_bulk_order_updates", "diagnose_priority_orders"],
            "analytics_enabled": True,
            "bulk_update_confirmation": True,
        }

    @router.post("/api/agent/tools/bulk-updates/parse")
    def tool_parse_bulk_updates(payload: agent_api.AnyPayload, x_floworder_agent_key: str | None = Header(None), identity: CurrentIdentity | None = Depends(get_current_identity_optional)) -> dict[str, Any]:
        agent_api._require_agent_key(x_floworder_agent_key)
        body = payload.model_dump()
        resolved = agent_api.get_agent_identity(body, identity)
        started = time.perf_counter()
        with agent_api.db() as conn:
            agent_api.enforce_run_budget(conn, body.get("run_id"))
            result = parse_bulk_order_updates_logic(conn, body, persist=True, identity=resolved)
            agent_api.log_tool_call(
                conn,
                run_id=body.get("run_id"),
                tool_name="parse_bulk_order_updates",
                request={"text_length": len(str(body.get("text") or body.get("update_text") or ""))},
                response={"batch_id": result["batch_id"], **result["summary"]},
                status="SUCCESS",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            conn.commit()
        return _coze_safe_bulk_update_response(result)

    @router.post("/api/agent/tools/priority-orders/diagnose")
    def tool_diagnose_priority_orders(payload: agent_api.AnyPayload, x_floworder_agent_key: str | None = Header(None), identity: CurrentIdentity | None = Depends(get_current_identity_optional)) -> dict[str, Any]:
        agent_api._require_agent_key(x_floworder_agent_key)
        body = payload.model_dump()
        resolved = agent_api.get_agent_identity(body, identity)
        started = time.perf_counter()
        with agent_api.db() as conn:
            agent_api.enforce_run_budget(conn, body.get("run_id"))
            result = diagnose_priority_orders_logic(conn, body, identity=resolved)
            agent_api.log_tool_call(
                conn,
                run_id=body.get("run_id"),
                tool_name="diagnose_priority_orders",
                request={
                    "due_within_days": body.get("due_within_days", 14),
                    "top_n": body.get("top_n", 7),
                },
                response={
                    "screened_order_count": result["screened_order_count"],
                    "anomaly_candidate_count": result["anomaly_candidate_count"],
                    "risk_order_count": result["risk_order_count"],
                    "information_gap_order_count": result["information_gap_order_count"],
                    "count": result["count"],
                },
                status="SUCCESS",
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
            conn.commit()
        return result

    # Website endpoints deliberately do not require the Coze plugin key. They still
    # enforce FlowOrder order ownership and explicit human confirmation.
    @router.post("/api/agent/bulk-updates/parse")
    def web_parse_bulk_updates(payload: agent_api.AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        with agent_api.db() as conn:
            result = parse_bulk_order_updates_logic(conn, {**payload.model_dump(), "source": "website"}, persist=True, identity=identity)
            conn.commit()
        return result

    @router.post("/api/agent/bulk-updates/confirm")
    def web_confirm_bulk_updates(payload: agent_api.AnyPayload, request: Request, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        with agent_api.db() as conn:
            result = confirm_bulk_updates_logic(conn, {**payload.model_dump(), "source": "website"}, identity=identity)
            conn.commit()
        return result

    @router.get("/api/agent/bulk-updates/{batch_id}")
    def get_bulk_update_batch(batch_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        with agent_api.db() as conn:
            ensure_v61_schema(conn)
            batch = conn.execute("SELECT * FROM bulk_update_batches WHERE batch_id=?", (batch_id,)).fetchone()
            if not batch:
                raise HTTPException(404, "批量更新批次不存在")
            # ENFORCE: Organization boundary check
            batch_org = str(batch["organization_id"] or "").strip() if batch["organization_id"] else ""
            if batch_org:
                agent_api.require_same_org(identity, batch_org)
            # ENFORCE: Role check - managers can see all, operators can only see their own
            if not identity.is_manager() and batch["current_user_id"] != identity.user_id:
                raise HTTPException(403, "无权查看该批次")
            items = [dict(r) for r in conn.execute("SELECT * FROM bulk_update_candidates WHERE batch_id=? ORDER BY created_at", (batch_id,))]
        for item in items:
            item["old_value"] = _safe_json(item.pop("old_value_json"), None)
            item["new_value"] = _safe_json(item.pop("new_value_json"), None)
            item["new_value_text"] = _value_text(item.get("field_name") or "", item["new_value"])
            item["edited_value"] = _safe_json(item.pop("edited_value_json"), None)
        result = dict(batch)
        result["summary"] = _safe_json(result.pop("summary_json"), {})
        result["items"] = items
        return result

    @router.post("/api/analytics/events")
    def record_event(payload: agent_api.AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        body = payload.model_dump()
        event_name = str(body.get("event_name") or "").strip()
        if not event_name:
            raise HTTPException(422, "缺少event_name")
        properties = body.get("properties") or {}
        blocked = {"agent_key", "api_key", "token", "raw_content", "full_text", "email_body"}
        if isinstance(properties, dict) and blocked.intersection({str(k).lower() for k in properties}):
            raise HTTPException(422, "埋点properties不得包含密钥或沟通全文")
        with agent_api.db() as conn:
            event_id = track_event(
                conn,
                event_name,
                organization_id=identity.organization_id,
                user_id=identity.user_id,
                user_role=identity.role,
                session_id=body.get("session_id"),
                order_id=body.get("order_id"),
                run_id=body.get("run_id"),
                source=body.get("source") or "web",
                properties=properties if isinstance(properties, dict) else {},
                client_timestamp=body.get("client_timestamp"),
            )
            conn.commit()
        return {"event_id": event_id, "status": "RECORDED"}

    @router.get("/api/analytics/summary")
    def analytics_summary(days: int = Query(30, ge=1, le=365), identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        with agent_api.db() as conn:
            # ENFORCE: Use authenticated user's organization, not client-supplied one
            return build_analytics_summary(conn, days=days, organization_id=identity.organization_id)

    @router.get("/bulk-update", response_class=HTMLResponse)
    def bulk_update_page() -> HTMLResponse:
        return HTMLResponse(BULK_UPDATE_HTML)

    @router.get("/validation", response_class=HTMLResponse)
    def validation_page() -> HTMLResponse:
        return HTMLResponse(VALIDATION_HTML)

    app.include_router(router)
