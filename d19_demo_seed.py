"""D19 Shadow/Smoke demo dataset.

The seed is intentionally isolated under deterministic D19-DEMO IDs.
Default mode is idempotent: it only inserts missing demo rows.
`reset_demo_seed()` removes only this seed namespace before recreating it;
it never clears unrelated/real business data.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from database import db, get_table_column_names, table_exists

CN_TZ = timezone(timedelta(hours=8))
ORG_ID = "ORG-A"
OWNER_ID = "USER-1"
SEED_VERSION = "D19_DEMO_SEED_V1"


@dataclass(frozen=True)
class SeedResult:
    inserted: dict[str, int]
    skipped: dict[str, int]
    order_count: int
    open_task_count: int
    pending_review_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "seed_version": SEED_VERSION,
            "inserted": self.inserted,
            "skipped": self.skipped,
            "order_count": self.order_count,
            "open_task_count": self.open_task_count,
            "pending_review_count": self.pending_review_count,
        }


def _iso(dt: datetime) -> str:
    return dt.astimezone(CN_TZ).isoformat(timespec="seconds")


def _day(dt: datetime) -> str:
    return dt.astimezone(CN_TZ).date().isoformat()


def _exists(conn: Any, table: str, key: str, value: Any) -> bool:
    return conn.execute(f'SELECT 1 FROM "{table}" WHERE "{key}"=?', (value,)).fetchone() is not None


def _insert_missing(conn: Any, table: str, key: str, values: dict[str, Any]) -> bool:
    if not table_exists(conn, table):
        return False
    key_value = values[key]
    if _exists(conn, table, key, key_value):
        return False
    allowed = get_table_column_names(conn, table)
    payload = {k: v for k, v in values.items() if k in allowed}
    columns = list(payload)
    quoted = ",".join(f'"{c}"' for c in columns)
    placeholders = ",".join("?" for _ in columns)
    conn.execute(
        f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})',
        tuple(payload[c] for c in columns),
    )
    return True


def _count(counter: dict[str, int], table: str, inserted: bool) -> None:
    bucket = counter.setdefault(table, 0)
    counter[table] = bucket + (1 if inserted else 0)


def _order(
    n: str,
    customer: str,
    product: str,
    packaging: str,
    due: datetime,
    progress: float,
    node: str,
    *,
    supplier_commitment: datetime | None = None,
    owner: str | None = OWNER_ID,
    readiness: str = "ACTION_GENERATED",
) -> dict[str, Any]:
    return {
        "order_id": f"ORD-D19-DEMO-{n}",
        "order_no": f"SO-{n}",
        "customer_name": customer,
        "product_name": product,
        "packaging_method": packaging,
        "requested_delivery_date": _day(due),
        "latest_supplier_commitment": _day(supplier_commitment) if supplier_commitment else None,
        "current_progress": progress,
        "current_node": node,
        "status": "ACTIVE",
        "owner": owner,
        "organization_id": ORG_ID,
        "action_readiness": readiness,
        "contact_status": "UNKNOWN",
        "issue_status": "UNKNOWN",
        "initialization_source": SEED_VERSION,
    }


def _task(
    n: str,
    order_n: str,
    title: str,
    action: str,
    target: str,
    *,
    now: datetime,
    risk: str = "none",
    deadline_hours: int | None = 24,
    next_hours: int | None = None,
    waiting_on: str | None = None,
    promise_hours: int | None = None,
    urgent: bool = False,
    pending_confirmation: bool = False,
    owner: str | None = OWNER_ID,
    responsibility: str = "assigned",
    evidence: list[str] | None = None,
    status: str = "OPEN",
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "task_id": f"TASK-D19-DEMO-{n}",
        "related_order_id": f"ORD-D19-DEMO-{order_n}",
        "title": title,
        "recommended_action": action,
        "target": target,
        "status": status,
        "owner_user_id": owner,
        "responsibility_status": responsibility,
        "waiting_on": waiting_on,
        "promised_reply_at": _iso(now + timedelta(hours=promise_hours)) if promise_hours is not None else None,
        "next_action_at": _iso(now + timedelta(hours=next_hours)) if next_hours is not None else None,
        "business_deadline": _iso(now + timedelta(hours=deadline_hours)) if deadline_hours is not None else None,
        "last_contact_at": _iso(now - timedelta(hours=2)) if waiting_on else None,
        "risk_level": risk,
        "urgent": 1 if urgent else 0,
        "pending_confirmation": 1 if pending_confirmation else 0,
        "evidence_json": json.dumps(evidence or [], ensure_ascii=False),
        "organization_id": ORG_ID,
        "created_at": _iso((updated_at or now) - timedelta(hours=1)),
        "updated_at": _iso(updated_at or now),
    }


def _risk(n: str, order_n: str, task_n: str | None, risk_type: str, level: str, evidence: str, now: datetime) -> dict[str, Any]:
    return {
        "risk_id": f"RISK-D19-DEMO-{n}",
        "order_id": f"ORD-D19-DEMO-{order_n}",
        "task_id": f"TASK-D19-DEMO-{task_n}" if task_n else None,
        "risk_type": risk_type,
        "risk_level": level,
        "evidence": evidence,
        "rule_id": f"D19_DEMO_{risk_type.upper()}",
        "status": "OPEN",
        "created_at": _iso(now - timedelta(minutes=20)),
        "updated_at": _iso(now - timedelta(minutes=5)),
    }


def _message(n: str, order_n: str, channel: str, sender: str, raw: str, now: datetime) -> dict[str, Any]:
    return {
        "message_id": f"MSG-D19-DEMO-{n}",
        "order_id": f"ORD-D19-DEMO-{order_n}",
        "organization_id": ORG_ID,
        "source_channel": channel,
        "sender_role": sender,
        "message_type": "business_update",
        "raw_content": raw,
        "source_time": _iso(now - timedelta(minutes=30)),
        "created_at": _iso(now - timedelta(minutes=29)),
    }


def _review(n: str, order_n: str, message_n: str, candidate: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "review_id": f"REV-D19-DEMO-{n}",
        "source_message_id": f"MSG-D19-DEMO-{message_n}",
        "order_id": f"ORD-D19-DEMO-{order_n}",
        "organization_id": ORG_ID,
        "workflow_source": SEED_VERSION,
        "candidate_json": json.dumps(candidate, ensure_ascii=False),
        "status": "PENDING",
        "reviewer_id": None,
        "created_at": _iso(now - timedelta(minutes=20)),
        "reviewed_at": None,
    }


def demo_ids() -> dict[str, list[str]]:
    order_ns = ["1048", "1061", "1032", "1027", "1054", "1084", "1076", "1102", "1115", "1130", "1138", "1009", "1015", "1124", "1088", "1112", "1120"]
    task_ns = ["1048", "1061", "1032", "1027", "1054", "1084", "1076", "1102", "1115", "1130", "1138", "1009", "1015", "1124"]
    history_ns = [f"H{day}-{i}" for day in range(5) for i in range([4, 6, 5, 7, 8][day])]
    risk_ns = ["1048", "1061", "1027", "1084", "1102", "1009"]
    msg_ns = ["1048", "1061", "1032", "1015", "1009", "1076"]
    review_ns = ["1032", "1076", "1048", "1009"]
    event_ns = order_ns
    return {
        "orders": [f"ORD-D19-DEMO-{n}" for n in order_ns],
        "tasks": [f"TASK-D19-DEMO-{n}" for n in task_ns] + [f"TASK-D19-DEMO-{n}" for n in history_ns],
        "risk_signals": [f"RISK-D19-DEMO-{n}" for n in risk_ns],
        "source_messages": [f"MSG-D19-DEMO-{n}" for n in msg_ns],
        "candidate_reviews": [f"REV-D19-DEMO-{n}" for n in review_ns],
        "event_logs": [f"EVT-D19-DEMO-{n}" for n in event_ns],
    }


def _delete_where_in(conn: Any, table: str, column: str, values: list[str] | tuple[str, ...]) -> int:
    if not values or not table_exists(conn, table):
        return 0
    if column not in get_table_column_names(conn, table):
        return 0
    placeholders = ",".join("?" for _ in values)
    result = conn.execute(
        f'DELETE FROM "{table}" WHERE "{column}" IN ({placeholders})',
        tuple(values),
    )
    return max(0, int(getattr(result, "rowcount", 0) or 0))


def _select_ids(conn: Any, table: str, id_column: str, where_column: str, values: list[str]) -> list[str]:
    if not values or not table_exists(conn, table):
        return []
    columns = get_table_column_names(conn, table)
    if id_column not in columns or where_column not in columns:
        return []
    placeholders = ",".join("?" for _ in values)
    rows = conn.execute(
        f'SELECT "{id_column}" FROM "{table}" WHERE "{where_column}" IN ({placeholders})',
        tuple(values),
    ).fetchall()
    return [str(r[id_column]) for r in rows if r[id_column]]


def reset_demo_seed(conn: Any) -> dict[str, int]:
    """Remove D19 demo orders and any derived rows tied to those orders.

    The scope is the deterministic D19 demo order IDs only. This means actions
    created while exercising those demo orders are also removable, while data
    belonging to non-demo orders is untouched.
    """
    order_ids = demo_ids()["orders"]
    deleted: dict[str, int] = {}

    def record(table: str, n: int) -> None:
        if n:
            deleted[table] = deleted.get(table, 0) + n

    # Resolve derived identifiers before deleting parents.
    legacy_task_ids = _select_ids(conn, "tasks", "task_id", "related_order_id", order_ids)
    case_ids = _select_ids(conn, "action_cases", "action_case_id", "order_id", order_ids)
    d9_task_ids: list[str] = []
    if case_ids:
        d9_task_ids = _select_ids(conn, "d9_action_case_tasks", "task_id", "action_case_id", case_ids)
    business_action_ids: list[str] = []
    if case_ids:
        business_action_ids = _select_ids(conn, "d10_business_actions", "business_action_id", "action_case_id", case_ids)
    outbox_ids: list[str] = []
    if business_action_ids:
        outbox_ids = _select_ids(conn, "d10_outbox_events", "event_id", "business_action_id", business_action_ids)

    # D15 / D12 / D10 chain.
    record("d15_execution_trace_events", _delete_where_in(conn, "d15_execution_trace_events", "event_id", outbox_ids))
    record("d15_outbox_execution_state", _delete_where_in(conn, "d15_outbox_execution_state", "event_id", outbox_ids))
    record("d12_human_reviews", _delete_where_in(conn, "d12_human_reviews", "order_id", order_ids))
    record("d10_audit_events", _delete_where_in(conn, "d10_audit_events", "entity_id", business_action_ids))
    record("d10_idempotency_records", _delete_where_in(conn, "d10_idempotency_records", "business_action_id", business_action_ids))
    record("d10_outbox_events", _delete_where_in(conn, "d10_outbox_events", "business_action_id", business_action_ids))
    record("d10_business_actions", _delete_where_in(conn, "d10_business_actions", "business_action_id", business_action_ids))

    # D9 / D8 chain.
    record("d9_action_case_waitings", _delete_where_in(conn, "d9_action_case_waitings", "action_case_id", case_ids))
    if d9_task_ids:
        record("d9_trace_events", _delete_where_in(conn, "d9_trace_events", "entity_id", d9_task_ids))
    if case_ids:
        record("d9_trace_events", _delete_where_in(conn, "d9_trace_events", "entity_id", case_ids))
    record("d9_action_case_tasks", _delete_where_in(conn, "d9_action_case_tasks", "action_case_id", case_ids))
    record("action_cases", _delete_where_in(conn, "action_cases", "action_case_id", case_ids))

    # Legacy/UI-linked child rows, including rows created while exercising demo orders.
    record("task_rankings", _delete_where_in(conn, "task_rankings", "task_id", legacy_task_ids))
    for table in (
        "candidate_reviews", "intake_jobs", "risk_signals", "commitment_history",
        "fact_conflicts", "quality_events", "logistics_events", "order_dependencies",
        "anomaly_candidates",
    ):
        record(table, _delete_where_in(conn, table, "order_id", order_ids))

    # Event logs may point at either the order or a task.
    record("event_logs", _delete_where_in(conn, "event_logs", "entity_id", order_ids))
    record("event_logs", _delete_where_in(conn, "event_logs", "entity_id", legacy_task_ids))
    record("tasks", _delete_where_in(conn, "tasks", "related_order_id", order_ids))
    record("source_messages", _delete_where_in(conn, "source_messages", "order_id", order_ids))
    record("orders", _delete_where_in(conn, "orders", "order_id", order_ids))

    conn.commit()
    return deleted


def seed_d19_demo(*, reset: bool = False, now: datetime | None = None) -> SeedResult:
    now = (now or datetime.now(CN_TZ)).astimezone(CN_TZ).replace(microsecond=0)
    inserted: dict[str, int] = {}
    skipped: dict[str, int] = {}

    with db() as conn:
        required = ["orders", "tasks", "risk_signals", "source_messages", "candidate_reviews", "event_logs"]
        missing = [t for t in required if not table_exists(conn, t)]
        if missing:
            raise RuntimeError(f"D19 demo seed requires migrated tables: {missing}. Run alembic upgrade head first.")
        if reset:
            reset_demo_seed(conn)

        orders = [
            _order("1048", "Northwind", "帆布收纳袋", "普通盒", now + timedelta(days=1), .62, "生产", supplier_commitment=now + timedelta(days=1)),
            _order("1061", "Blue Harbor", "旅行收纳套装", "OPP袋", now + timedelta(days=1), .88, "出货"),
            _order("1032", "Alpine", "礼品袋", "彩盒", now + timedelta(days=5), .46, "生产"),
            _order("1027", "Lumière", "香薰礼盒", "礼盒", now + timedelta(days=3), .71, "生产"),
            _order("1054", "Meridian", "化妆包", "OPP袋", now + timedelta(days=6), .35, "备货"),
            _order("1084", "Aurora", "帆布包", "纸箱", now + timedelta(days=2), .74, "生产"),
            _order("1076", "Harbor", "洗漱包", "彩盒", now + timedelta(days=4), .52, "生产"),
            _order("1102", "Maple", "束口袋", "普通盒", now + timedelta(days=2), .30, "生产"),
            _order("1115", "Pine", "收纳盒", "纸箱", now + timedelta(days=7), .22, "备货"),
            _order("1130", "Terra", "礼品套装", "彩盒", now + timedelta(days=4), .58, "生产"),
            _order("1138", "Vale", "拉链袋", "OPP袋", now + timedelta(days=8), .40, "生产"),
            _order("1009", "Vector", "展示袋", "纸箱", now + timedelta(days=5), .66, "生产"),
            _order("1015", "Nexa", "旅行袋", "纸箱", now + timedelta(days=6), .48, "生产"),
            _order("1124", "Solace", "礼盒内衬", "礼盒", now + timedelta(days=7), .27, "备货"),
            _order("1088", "Atlas", "棉布袋", "OPP袋", now + timedelta(days=8), .63, "生产", readiness="READY_FOR_RANKING"),
            _order("1112", "Cedar", "收纳袋", "纸箱", now + timedelta(days=10), .25, "备货", readiness="READY_FOR_RANKING"),
            _order("1120", "Horizon", "礼品袋", "纸箱", now + timedelta(days=3), .92, "出货", readiness="READY_FOR_RANKING"),
        ]
        for row in orders:
            row["created_at"] = _iso(now - timedelta(days=9))
            row["updated_at"] = _iso(now - timedelta(minutes=5))
            ok = _insert_missing(conn, "orders", "order_id", row)
            _count(inserted if ok else skipped, "orders", True)

        active_tasks = [
            _task("1048", "1048", "确认新生产计划与可交付时间", "联系供应商确认明日能否按计划完工", "factory", now=now, risk="high", deadline_hours=2, urgent=True, evidence=["供应商回复原料晚到 1 天", "客户交期仅剩 1 天"]),
            _task("1061", "1061", "确认物流资料与订舱状态", "立即核对物流资料并锁定出货安排", "logistics", now=now, risk="high", deadline_hours=4, urgent=True, evidence=["订单明日出货", "物流资料仍未确认"]),
            _task("1032", "1032", "确认客户包装规格变更", "核对包装规格版本并完成客户/内部确认", "customer", now=now, risk="medium", deadline_hours=10, pending_confirmation=True, evidence=["客户新增包装规格", "继续生产前需要明确版本"]),
            _task("1027", "1027", "再次跟进供应商回复", "再次联系供应商确认明确完成时间", "factory", now=now, risk="high", waiting_on="factory", promise_hours=-1, deadline_hours=8, evidence=["供应商承诺回复时间已过 40 分钟"]),
            _task("1054", "1054", "处理关键事项升级", "请求主管确认资源与负责人安排", "manager", now=now, risk="critical", deadline_hours=-9, owner=OWNER_ID, responsibility="assigned", evidence=["关键事项已严重逾期，需要主管介入"]),
            _task("1084", "1084", "处理质检阻塞", "确认返工范围与最晚解锁时间", "factory", now=now, risk="high", deadline_hours=5, urgent=True, evidence=["质检问题阻塞出货准备", "需要确认返工完成时间"]),
            _task("1076", "1076", "确认客户最终版本", "今天完成客户版本确认", "customer", now=now, risk="medium", deadline_hours=7, evidence=["客户确认窗口今天到期"]),
            _task("1102", "1102", "核对生产进度与交期缓冲", "今天确认剩余生产计划是否能覆盖交期", "factory", now=now, risk="high", next_hours=9, deadline_hours=20, evidence=["当前生产进度 30%", "交期缓冲偏紧"]),
            _task("1115", "1115", "补齐内部出货资料", "整理并补齐缺失资料", "internal", now=now, risk="low", deadline_hours=28, evidence=["内部资料尚未完整"]),
            _task("1130", "1130", "跟进客户回函", "今天提醒客户确认最终回函", "customer", now=now, risk="medium", deadline_hours=8, evidence=["客户回函今天到期"]),
            _task("1138", "1138", "常规跟进生产节点", "按计划确认下一生产节点", "factory", now=now, risk="low", deadline_hours=36, evidence=["当前无新增高风险事实"]),
            _task("1009", "1009", "核对交期来源冲突", "核对 ERP 与客户邮件的正式交期来源", "internal", now=now, risk="high", deadline_hours=6, pending_confirmation=True, evidence=["ERP 与客户邮件交期不一致", "正式交期不可静默覆盖"]),
            _task("1015", "1015", "等待供应商回复新完工时间", "等待供应商在承诺窗口内回复", "factory", now=now, risk="medium", waiting_on="factory", promise_hours=16, deadline_hours=30, evidence=["已联系供应商", "约定明日回复"]),
            _task("1124", "1124", "等待仓库确认备货结果", "等待内部仓库完成确认", "internal", now=now, risk="low", waiting_on="internal", promise_hours=5, deadline_hours=24, evidence=["仓库承诺 5 小时内确认"]),
        ]
        # Keep active-task update timestamps outside the 5-day review trend window.
        # Review trend is a handled-work proxy, not a count of all currently open tasks.
        for row in active_tasks:
            row["created_at"] = _iso(now - timedelta(days=6, hours=1))
            row["updated_at"] = _iso(now - timedelta(days=6))
            ok = _insert_missing(conn, "tasks", "task_id", row)
            _count(inserted if ok else skipped, "tasks", True)

        # Five-day handled-order history: 4, 6, 5, 7, 8 completed items.
        handled_counts = [4, 6, 5, 7, 8]
        history_orders = ["1088", "1112", "1120", "1115", "1138"]
        for day_idx, count in enumerate(handled_counts):
            stamp = (now - timedelta(days=4 - day_idx)).replace(hour=16, minute=20)
            for i in range(count):
                n = f"H{day_idx}-{i}"
                order_n = history_orders[i % len(history_orders)]
                row = _task(
                    n,
                    order_n,
                    f"历史完成事项 {day_idx + 1}-{i + 1}",
                    "已完成",
                    "internal",
                    now=now,
                    risk="none",
                    deadline_hours=None,
                    status="DONE",
                    evidence=["D19 复盘趋势演示数据"],
                    updated_at=stamp,
                )
                ok = _insert_missing(conn, "tasks", "task_id", row)
                _count(inserted if ok else skipped, "tasks", True)

        risks = [
            _risk("1048", "1048", "1048", "delivery_buffer", "high", "供应商原料晚到 1 天，客户交期窗口被压缩。", now),
            _risk("1061", "1061", "1061", "logistics_unconfirmed", "high", "明日计划出货，但物流资料仍未确认。", now),
            _risk("1027", "1027", "1027", "supplier_reply_overdue", "high", "供应商承诺回复时间已经超过约定窗口。", now),
            _risk("1084", "1084", "1084", "quality_blocking", "high", "质检问题正在阻塞后续交付准备。", now),
            _risk("1102", "1102", "1102", "progress_deadline_mismatch", "high", "当前生产进度与剩余交期缓冲不匹配。", now),
            _risk("1009", "1009", "1009", "fact_conflict", "high", "ERP 交期与客户邮件交期存在冲突，需要核对正式来源。", now),
        ]
        for row in risks:
            ok = _insert_missing(conn, "risk_signals", "risk_id", row)
            _count(inserted if ok else skipped, "risk_signals", True)

        messages = [
            _message("1048", "1048", "supplier_chat", "factory", "SO-1048 原料比计划晚到 1 天，新的完工时间还在确认。", now),
            _message("1061", "1061", "email", "logistics", "SO-1061 明日出货，订舱资料还缺最终确认。", now),
            _message("1032", "1032", "email", "customer", "SO-1032 包装由普通盒改成彩盒，外箱唛头也请同步更新。", now),
            _message("1015", "1015", "supplier_chat", "factory", "SO-1015 新完工时间明天下午 3 点前回复。", now),
            _message("1009", "1009", "email", "customer", f"SO-1009 客户邮件要求交期改为 {_day(now + timedelta(days=3))}。", now),
            _message("1076", "1076", "email", "customer", "SO-1076 客户确认继续使用当前包装版本。", now),
        ]
        for row in messages:
            ok = _insert_missing(conn, "source_messages", "message_id", row)
            _count(inserted if ok else skipped, "source_messages", True)

        candidate_1032 = {
            "message_type": "customer_change",
            "order_match": {"status": "unique_match", "selected_order_id": "ORD-D19-DEMO-1032", "matched_order_no": "SO-1032"},
            "fields": [
                {"field_name": "packaging_method", "old_value": "彩盒", "normalized_value": "加厚彩盒", "source_quote": "包装改成加厚彩盒", "confidence": .96},
                {"field_name": "current_node", "old_value": "生产", "normalized_value": "生产-包装待确认", "source_quote": "外箱唛头同步更新", "confidence": .88},
            ],
            "risk_signals": [{"type": "change_blocking", "risk_level": "medium", "evidence": "包装变更需要内部确认"}],
            "action_candidates": [{"action_type": "confirm_change", "title": "确认包装变更", "recommended_action": "核对规格和影响范围", "target": "internal"}],
        }
        candidate_1076 = {
            "message_type": "customer_confirmation",
            "order_match": {"status": "unique_match", "selected_order_id": "ORD-D19-DEMO-1076", "matched_order_no": "SO-1076"},
            "fields": [{"field_name": "packaging_method", "old_value": "彩盒", "normalized_value": "彩盒", "source_quote": "继续使用当前包装版本", "confidence": .99}],
            "risk_signals": [],
            "action_candidates": [{"action_type": "record_confirmation", "title": "记录客户确认", "recommended_action": "记录来源并继续生产", "target": "internal"}],
        }
        candidate_1048 = {
            "message_type": "supplier_update",
            "order_match": {"status": "unique_match", "selected_order_id": "ORD-D19-DEMO-1048", "matched_order_no": "SO-1048"},
            "fields": [{"field_name": "latest_supplier_commitment", "old_value": None, "normalized_value": _day(now + timedelta(days=1)), "source_quote": "新的完工时间还在确认", "confidence": .72}],
            "risk_signals": [{"type": "delivery_risk", "risk_level": "high", "evidence": "交期缓冲很小"}],
            "action_candidates": [{"action_type": "confirm_with_factory", "title": "确认供应商新承诺", "recommended_action": "要求明确可交付日期", "target": "factory"}],
        }
        old_due = _day(now + timedelta(days=5))
        new_due = _day(now + timedelta(days=3))
        candidate_1009 = {
            "message_type": "customer_commitment_change",
            "order_match": {"status": "unique_match", "selected_order_id": "ORD-D19-DEMO-1009", "matched_order_no": "SO-1009"},
            "fields": [{"field_name": "requested_delivery_date", "old_value": old_due, "normalized_value": new_due, "source_quote": f"交期改为 {new_due}", "confidence": .97}],
            "risk_signals": [{"type": "formal_date_change", "risk_level": "high", "evidence": "正式交期变化需要主管审批"}],
            "action_candidates": [{"action_type": "update_formal_delivery_date", "title": "更新正式交期", "recommended_action": "提交主管审批后再更新", "target": "manager"}],
        }
        reviews = [
            _review("1032", "1032", "1032", candidate_1032, now),
            _review("1076", "1076", "1076", candidate_1076, now),
            _review("1048", "1048", "1048", candidate_1048, now),
            _review("1009", "1009", "1009", candidate_1009, now),
        ]
        for row in reviews:
            ok = _insert_missing(conn, "candidate_reviews", "review_id", row)
            _count(inserted if ok else skipped, "candidate_reviews", True)

        # One visible latest event per order for the drawer timeline.
        for idx, order in enumerate(orders):
            n = order["order_no"].split("-")[-1]
            event = {
                "event_id": f"EVT-D19-DEMO-{n}",
                "entity_type": "order",
                "entity_id": order["order_id"],
                "event_type": "DEMO_LATEST_PROGRESS",
                "payload_json": json.dumps({"seed": SEED_VERSION, "order_no": order["order_no"]}, ensure_ascii=False),
                "operator_id": OWNER_ID,
                "organization_id": ORG_ID,
                "created_at": _iso(now - timedelta(minutes=idx + 2)),
            }
            ok = _insert_missing(conn, "event_logs", "event_id", event)
            _count(inserted if ok else skipped, "event_logs", True)

        conn.commit()

        order_count = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE initialization_source=?",
            (SEED_VERSION,),
        ).fetchone()[0]
        open_task_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE task_id LIKE 'TASK-D19-DEMO-%' AND status!='DONE'"
        ).fetchone()[0]
        pending_review_count = conn.execute(
            "SELECT COUNT(*) FROM candidate_reviews WHERE workflow_source=? AND status IN ('PENDING','APPROVAL_PENDING')",
            (SEED_VERSION,),
        ).fetchone()[0]

    return SeedResult(
        inserted=inserted,
        skipped=skipped,
        order_count=int(order_count or 0),
        open_task_count=int(open_task_count or 0),
        pending_review_count=int(pending_review_count or 0),
    )
