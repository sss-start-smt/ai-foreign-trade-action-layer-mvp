from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

CN_TZ = timezone(timedelta(hours=8))


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def seed(db_path: Path, anchor: datetime, reset_agent_demo: bool = False) -> dict:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    schema = (Path(__file__).resolve().parent / "schema.sql").read_text("utf-8")
    conn.executescript(schema)
    now = anchor

    if reset_agent_demo:
        for table in ("approval_requests", "anomaly_candidates", "daily_inspection_reports", "agent_tool_calls", "agent_runs",
                      "logistics_events", "order_dependencies", "risk_signals", "tasks", "source_messages", "commitment_history"):
            conn.execute(f"DELETE FROM {table}")

    orders = [
        ("PO-2026-001", "Northwind Trading", "帆布托特包", "USER-1", 3, 0.62, "生产中", -1),
        ("PO-2026-002", "Blue Harbor GmbH", "化妆包", "USER-1", 5, 0.35, "物料准备", 2),
        ("PO-2026-003", "Maple Retail", "儿童双肩包", "USER-2", 6, 0.25, "样品确认", 3),
        ("PO-2026-004", "Atlas Home", "收纳袋", "USER-2", 8, 0.72, "生产中", 5),
        ("PO-2026-005", "Luma Stores", "抱枕套", "USER-3", 10, 0.10, "待开产", 7),
        ("PO-2026-006", "Nordic Living", "桌面收纳盒", "USER-3", 12, 0.88, "包装中", 9),
        ("PO-2026-007", "Bright Kids", "午餐包", "USER-1", 14, 0.55, "生产中", 11),
        ("PO-2026-008", "Terra Market", "棉质围裙", "USER-2", 18, 0.30, "裁剪", 14),
        ("PO-2026-009", "Casa Verde", "再生购物袋", "USER-3", 22, 0.18, "物料准备", 18),
        ("PO-2026-010", "Urban Ease", "旅行收纳六件套", "USER-1", 25, 0.48, "生产中", 21),
        ("PO-2026-011", "Willow & Co.", "厨房毛巾", "USER-2", 30, 0.40, "织造", 26),
        ("PO-2026-012", "Mira Beauty", "香水礼袋", "USER-3", 35, 0.10, "打样", 31),
        ("PO-2026-013", "Ever Trail", "防水干湿分离包", "USER-1", 40, 0.52, "生产中", 36),
        ("PO-2026-014", "Oak & Pine", "面包收纳篮", "USER-2", 45, 0.25, "物料准备", 41),
        ("PO-2026-015", "Petal House", "花艺工具包", "USER-3", 50, 0.15, "待确认", 46),
        ("PO-2026-016", "Sunrise Foods", "保温杯套", "USER-1", 55, 0.60, "生产中", 51),
        ("PO-2026-017", "Quiet Corner", "布艺书套", "USER-2", 60, 0.42, "印花", 56),
        ("PO-2026-018", "Silver Finch", "眼镜盒", "USER-3", 65, 0.12, "开模", 61),
        ("PO-2026-019", "Forest Lane", "瑜伽垫背带", "USER-1", 70, None, "", None),
        ("PO-2026-020", "Golden Hour", "折叠雨伞套", "USER-2", 75, None, "", None),
    ]
    order_ids = {}
    for no, customer, product, owner, due_days, progress, node, commitment_days in orders:
        existing = conn.execute("SELECT order_id FROM orders WHERE order_no=?", (no,)).fetchone()
        order_id = existing[0] if existing else uid("ORD")
        order_ids[no] = order_id
        delivery = (now + timedelta(days=due_days)).date().isoformat()
        commitment = (now + timedelta(days=commitment_days)).date().isoformat() if commitment_days is not None else None
        conn.execute(
            """INSERT INTO orders(order_id,order_no,customer_name,product_name,packaging_method,requested_delivery_date,
               latest_supplier_commitment,current_progress,current_node,status,owner,action_readiness,contact_status,issue_status,
               created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,'ACTIVE',?,'ACTION_GENERATED','UNKNOWN','UNKNOWN',?,?)
               ON CONFLICT(order_no) DO UPDATE SET customer_name=excluded.customer_name,product_name=excluded.product_name,
               requested_delivery_date=excluded.requested_delivery_date,latest_supplier_commitment=excluded.latest_supplier_commitment,
               current_progress=excluded.current_progress,current_node=excluded.current_node,owner=excluded.owner,status='ACTIVE',updated_at=excluded.updated_at""",
            (order_id, no, customer, product, "测试包装", delivery, commitment, progress, node, owner, iso(now), iso(now)),
        )

    def msg(order_no: str, role: str, content: str, hours_ago: int = 1, channel: str = "email"):
        conn.execute("INSERT INTO source_messages(message_id,order_id,organization_id,source_channel,sender_role,message_type,raw_content,source_time,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                     (uid("MSG"), order_ids[order_no], "ORG-DEMO", channel, role, "TEXT", content, iso(now-timedelta(hours=hours_ago)), iso(now)))

    def task(order_no: str, title: str, owner: str, *, pending=False, waiting_on=None, reply_hours=None, urgent=False, risk="medium", target="internal"):
        promised = iso(now + timedelta(hours=reply_hours)) if reply_hours is not None else None
        conn.execute("""INSERT INTO tasks(task_id,related_order_id,title,recommended_action,target,status,owner_user_id,responsibility_status,
                      waiting_on,promised_reply_at,next_action_at,risk_level,urgent,pending_confirmation,evidence_json,created_at,updated_at)
                      VALUES(?,?,?,?,?,'OPEN',?,'assigned',?,?,?,?,?,?,'[]',?,?)""",
                     (uid("TASK"), order_ids[order_no], title, title, target, owner, waiting_on, promised, promised, risk, int(urgent), int(pending), iso(now), iso(now)))

    def dependency(order_no: str, dtype: str, name: str, status: str, party: str, due_days: int, seq: int):
        conn.execute("""INSERT INTO order_dependencies(dependency_id,order_id,dependency_type,dependency_name,sequence_no,status,
                      blocking_party,due_at,evidence_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                     (uid("DEP"), order_ids[order_no], dtype, name, seq, status, party,
                      iso(now+timedelta(days=due_days)), '[]', iso(now), iso(now)))

    def logistics(order_no: str, event_type: str, status: str, description: str, eta_days: int | None = None):
        conn.execute("""INSERT INTO logistics_events(logistics_event_id,order_id,event_type,status,location,description,event_time,
                      estimated_arrival_at,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                     (uid("LOG"), order_ids[order_no], event_type, status, "测试节点", description, iso(now-timedelta(hours=2)),
                      iso(now+timedelta(days=eta_days)) if eta_days is not None else None, "SYNTHETIC_EVAL", iso(now), iso(now)))

    # Supplier commitment overdue and waiting-overdue cases.
    msg("PO-2026-001", "factory", "目前只有六成左右，原定昨天完成没有做到，补救方案今晚给。")
    task("PO-2026-001", "确认工厂实际进度与补救方案", "USER-1", waiting_on="factory", reply_hours=-3, urgent=True, risk="high", target="factory")
    msg("PO-2026-007", "factory", "拉链还没到，预计下周才能完成。")
    task("PO-2026-007", "追踪拉链到货和新完工承诺", "USER-1", waiting_on="factory", reply_hours=-10, risk="high", target="factory")

    # Customer confirmation blocking.
    msg("PO-2026-003", "customer", "Logo请改成新版文件，包装是否能改彩盒请今天确认。")
    task("PO-2026-003", "等待客户确认新版Logo终稿", "USER-2", pending=True, risk="high", target="customer")
    dependency("PO-2026-003", "ARTWORK", "新版Logo确认", "WAITING_CONFIRMATION", "customer", 0, 1)
    msg("PO-2026-005", "customer", "彩盒颜色还要内部确认，先不要开产。")
    task("PO-2026-005", "催客户确认彩盒颜色", "USER-3", pending=True, target="customer")
    dependency("PO-2026-005", "PACKAGING", "彩盒颜色确认", "BLOCKED", "customer", 1, 1)

    # Delivery risk and unresolved dependencies.
    dependency("PO-2026-002", "MATERIAL", "关键面料到货", "PENDING", "factory", 1, 1)
    msg("PO-2026-002", "factory", "主料到货时间还不能确定。")
    dependency("PO-2026-004", "INSPECTION", "出货前验货", "PENDING", "internal", 5, 2)
    dependency("PO-2026-006", "BOOKING", "订舱确认", "PENDING", "forwarder", 7, 3)

    # Logistics exceptions; PO-009 is outside 14-day window but must be included by exception.
    logistics("PO-2026-006", "CUSTOMS", "CUSTOMS_HOLD", "报关资料缺少材质声明，当前被海关暂扣。", None)
    logistics("PO-2026-009", "VESSEL", "DELAYED", "原船期取消，货代尚未提供替代船期。", None)
    msg("PO-2026-009", "forwarder", "船公司通知原船期取消，新船期还在确认。")

    # Valid waiting: should not be diagnosed as overdue before promise time.
    msg("PO-2026-004", "factory", "已收到，今天下午三点前回复准确完工时间。")
    task("PO-2026-004", "等待工厂确认准确完工时间", "USER-2", waiting_on="factory", reply_hours=4, risk="high", target="factory")

    # No-action and information-gap controls.
    msg("PO-2026-008", "factory", "生产正常，按计划推进。")
    msg("PO-2026-010", "customer", "谢谢，已收到。")
    # PO-019/020 intentionally have missing state and no recent messages.

    conn.commit()
    conn.close()
    return {"db_path": str(db_path), "anchor": iso(now), "orders": len(orders), "note": "仅用于脱敏合成评测，不代表真实业务结果"}


def main():
    parser = argparse.ArgumentParser(description="Seed FlowOrder Agent synthetic evaluation data")
    parser.add_argument("--db", default="data/action_layer.db")
    parser.add_argument("--anchor", help="ISO datetime, default now in UTC+8")
    parser.add_argument("--reset-agent-demo", action="store_true")
    args = parser.parse_args()
    anchor = datetime.fromisoformat(args.anchor) if args.anchor else datetime.now(CN_TZ)
    if anchor.tzinfo is None:
        anchor = anchor.replace(tzinfo=CN_TZ)
    print(json.dumps(seed(Path(args.db), anchor.astimezone(CN_TZ), args.reset_agent_demo), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
