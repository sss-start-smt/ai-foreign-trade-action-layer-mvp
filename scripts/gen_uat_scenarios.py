#!/usr/bin/env python3
"""Generate three D11 UAT scenario databases for UI verification.

Scenarios:
  A) 1 action + 0 waiting
  B) multi action + multi waiting
  C) 0 action + waiting
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

NOW = "2026-08-15T09:00:00+08:00"
ORG = "ORG-A"
USER = "USER-1"


def seed_base(raw: sqlite3.Connection) -> None:
    base = Path(__file__).resolve().parents[1]
    schema = (base / "schema.sql").read_text(encoding="utf-8")
    raw.executescript(schema)
    cols = {r[1] for r in raw.execute("PRAGMA table_info(orders)").fetchall()}
    if "organization_id" not in cols:
        raw.execute("ALTER TABLE orders ADD COLUMN organization_id TEXT")
    task_cols = {r[1] for r in raw.execute("PRAGMA table_info(tasks)").fetchall()}
    if "organization_id" not in task_cols:
        raw.execute("ALTER TABLE tasks ADD COLUMN organization_id TEXT")


def insert_order(raw: sqlite3.Connection, order_id: str, order_no: str, customer: str, product: str, node: str, delivery: str) -> None:
    raw.execute(
        """INSERT INTO orders(order_id,order_no,customer_name,product_name,requested_delivery_date,
           latest_supplier_commitment,current_node,status,owner,organization_id,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (order_id, order_no, customer, product, delivery, None, node, "ACTIVE", USER, ORG, NOW, NOW),
    )


def insert_case(raw: sqlite3.Connection, case_id: str, order_id: str, intent: str, bucket: str,
                severity: str, recommended: str, evidence: list[str]) -> None:
    raw.execute(
        """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,
           stage,lifecycle_status,title,latest_action_bucket,latest_severity,latest_recommended_action,
           latest_evidence_json,observation_status,first_seen_at,last_seen_at,source_policy_version,
           version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (case_id, ORG, order_id, f"D11:{intent}:{order_id}", intent, "IN_PROGRESS", "ACTIVE",
         recommended, bucket, severity, recommended,
         json.dumps(evidence, ensure_ascii=False), "OBSERVED", NOW, NOW, "D11_UAT_SEED", 1, NOW, NOW),
    )


def insert_task(raw: sqlite3.Connection, task_id: str, case_id: str, title: str, recommended: str,
                status: str) -> None:
    raw.execute(
        """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,
           status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
        (task_id, ORG, case_id, title, recommended, status, 1, NOW, NOW),
    )


def insert_waiting(raw: sqlite3.Connection, waiting_id: str, task_id: str, case_id: str,
                   reason: str, due_at: str, reply_count: int = 0) -> None:
    raw.execute(
        """INSERT INTO d9_action_case_waitings(waiting_id,organization_id,task_id,action_case_id,waiting_type,
           reason,due_at,status,reply_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (waiting_id, ORG, task_id, case_id, "EXTERNAL_REPLY", reason, due_at, "ACTIVE", reply_count, NOW, NOW),
    )


def build_scenario_a(out_path: Path) -> None:
    """1 action + 0 waiting."""
    if out_path.exists():
        out_path.unlink()
    raw = sqlite3.connect(out_path)
    try:
        seed_base(raw)
        insert_order(raw, "ORD-SC-A-1", "SO-SC-A-1", "Northwind Ltd", "帆布包", "生产中", "2026-08-20")
        insert_case(raw, "AC-SC-A-1", "ORD-SC-A-1", "DELIVERY_RECOVERY", "DO_NOW",
                    "high", "跟催供应商确认 8 月 20 日能否交货",
                    ["客户交期为 8 月 20 日", "供应商尚未确认"])
        insert_task(raw, "TK-SC-A-1", "AC-SC-A-1", "联系供应商确认交期", "联系供应商给出明确可交付日期", "TODO")
        raw.commit()
    finally:
        raw.close()


def build_scenario_b(out_path: Path) -> None:
    """Multi action + multi waiting."""
    if out_path.exists():
        out_path.unlink()
    raw = sqlite3.connect(out_path)
    try:
        seed_base(raw)
        # Orders
        orders = [
            ("ORD-SC-B-1", "SO-SC-B-1", "Northwind Ltd", "帆布包", "生产中", "2026-08-18"),
            ("ORD-SC-B-2", "SO-SC-B-2", "Acme Trade", "涤纶面料", "备货/采购", "2026-08-22"),
            ("ORD-SC-B-3", "SO-SC-B-3", "Global Tex", "牛仔布", "出货", "2026-08-19"),
        ]
        for oid, ono, cust, prod, node, delivery in orders:
            insert_order(raw, oid, ono, cust, prod, node, delivery)
        # Action Case 1 - ACTIONABLE with one TODO task
        insert_case(raw, "AC-SC-B-1", "ORD-SC-B-1", "DELIVERY_RECOVERY", "DO_NOW",
                    "high", "紧急跟催 SO-SC-B-1 供应商",
                    ["客户交期 8/18 已临近", "供应商尚未回复"])
        insert_task(raw, "TK-SC-B-1-1", "AC-SC-B-1", "电话跟催供应商", "电话跟催供应商确认", "TODO")
        # Action Case 2 - ACTIONABLE with two TODO tasks
        insert_case(raw, "AC-SC-B-2", "ORD-SC-B-2", "MATERIAL_FOLLOWUP", "DO_NOW",
                    "medium", "确认 SO-SC-B-2 备料情况",
                    ["物料交期 8/22", "需要确认库存"])
        insert_task(raw, "TK-SC-B-2-1", "AC-SC-B-2", "跟催物料到位", "联系工厂确认物料", "TODO")
        insert_task(raw, "TK-SC-B-2-2", "AC-SC-B-2", "核对替代方案", "核对内部库存", "TODO")
        # Action Case 3 - WAITING (with ACTIVE waiting, status=TODO? actually waiting)
        insert_case(raw, "AC-SC-B-3", "ORD-SC-B-3", "LOGISTICS_FOLLOWUP", "WAIT_ONLY",
                    "medium", "等待 SO-SC-B-3 物流回复",
                    ["已联系货代", "等待物流报价"])
        # Task in WAITING state
        insert_task(raw, "TK-SC-B-3-1", "AC-SC-B-3", "等待物流报价", "物流回复后安排出货", "WAITING")
        # Insert active waiting row (used by d11v2Waitings aggregation)
        insert_waiting(raw, "WT-SC-B-3-1", "TK-SC-B-3-1", "AC-SC-B-3",
                       reason="等待货代报价", due_at="2026-08-17T18:00:00+08:00")
        # Another waiting order
        insert_order(raw, "ORD-SC-B-4", "SO-SC-B-4", "Pacific Imp", "纱线", "生产中", "2026-08-21")
        insert_case(raw, "AC-SC-B-4", "ORD-SC-B-4", "QC_FOLLOWUP", "WAIT_ONLY",
                    "low", "等待 SO-SC-B-4 质检结果",
                    ["质检样本已送", "等待结果"])
        insert_task(raw, "TK-SC-B-4-1", "AC-SC-B-4", "等待质检结果", "收到结果后推进", "WAITING")
        insert_waiting(raw, "WT-SC-B-4-1", "TK-SC-B-4-1", "AC-SC-B-4",
                       reason="等待质检报告", due_at="2026-08-17T12:00:00+08:00")
        raw.commit()
    finally:
        raw.close()


def build_scenario_c(out_path: Path) -> None:
    """0 action + waiting."""
    if out_path.exists():
        out_path.unlink()
    raw = sqlite3.connect(out_path)
    try:
        seed_base(raw)
        insert_order(raw, "ORD-SC-C-1", "SO-SC-C-1", "Orient Co", "丝绸", "生产中", "2026-08-25")
        insert_case(raw, "AC-SC-C-1", "ORD-SC-C-1", "PAYMENT_FOLLOWUP", "WAIT_ONLY",
                    "low", "等待 SO-SC-C-1 客户付款",
                    ["PI 已发送", "等待客户付款"])
        insert_task(raw, "TK-SC-C-1-1", "AC-SC-C-1", "等待客户付款", "客户付款后安排生产", "WAITING")
        insert_waiting(raw, "WT-SC-C-1-1", "TK-SC-C-1-1", "AC-SC-C-1",
                       reason="等待客户付款", due_at="2026-08-18T10:00:00+08:00")
        insert_order(raw, "ORD-SC-C-2", "SO-SC-C-2", "Hanover GmbH", "羊毛", "备货/采购", "2026-08-23")
        insert_case(raw, "AC-SC-C-2", "ORD-SC-C-2", "MATERIAL_WAIT", "WAIT_ONLY",
                    "medium", "等待 SO-SC-C-2 备货",
                    ["备货进行中", "等待供应商确认"])
        insert_task(raw, "TK-SC-C-2-1", "AC-SC-C-2", "等待供应商备货", "供应商备货完成后跟进", "WAITING")
        insert_waiting(raw, "WT-SC-C-2-1", "TK-SC-C-2-1", "AC-SC-C-2",
                       reason="等待供应商备货", due_at="2026-08-19T18:00:00+08:00")
        raw.commit()
    finally:
        raw.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True, choices=["A", "B", "C"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    out = Path(args.output).expanduser().resolve()
    if out.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite {out}. Use --force.")
    out.parent.mkdir(parents=True, exist_ok=True)
    builders = {"A": build_scenario_a, "B": build_scenario_b, "C": build_scenario_c}
    builders[args.scenario](out)
    print(out)


if __name__ == "__main__":
    main()
