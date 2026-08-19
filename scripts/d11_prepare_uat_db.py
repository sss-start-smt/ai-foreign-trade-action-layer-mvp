#!/usr/bin/env python3
"""Create an isolated SQLite database for FlowOrder D11 UI/UAT.

This script NEVER touches the configured production DB. It requires an explicit
--output path and refuses to overwrite an existing file unless --force is used.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

NOW = "2026-08-14T15:30:00+08:00"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Path for a NEW SQLite UAT database")
    parser.add_argument("--force", action="store_true", help="Overwrite the output file if it exists")
    args = parser.parse_args()

    out = Path(args.output).expanduser().resolve()
    if out.exists() and not args.force:
        raise SystemExit(f"Refusing to overwrite existing DB: {out}. Use --force only for disposable UAT data.")
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()

    base = Path(__file__).resolve().parents[1]
    schema = (base / "schema.sql").read_text(encoding="utf-8")
    raw = sqlite3.connect(out)
    try:
        raw.row_factory = sqlite3.Row
        raw.executescript(schema)
        cols = {r[1] for r in raw.execute("PRAGMA table_info(orders)").fetchall()}
        if "organization_id" not in cols:
            raw.execute("ALTER TABLE orders ADD COLUMN organization_id TEXT")
        task_cols = {r[1] for r in raw.execute("PRAGMA table_info(tasks)").fetchall()}
        if "organization_id" not in task_cols:
            raw.execute("ALTER TABLE tasks ADD COLUMN organization_id TEXT")

        # Order 1: Abnormal order (SO-D11-UAT with delivery delay issue)
        raw.execute(
            """INSERT INTO orders(order_id,order_no,customer_name,product_name,requested_delivery_date,
               latest_supplier_commitment,current_node,status,owner,organization_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("ORD-D11-UAT", "SO-D11-UAT", "Northwind UAT", "帆布包", "2026-08-20", None,
             "生产中", "ACTIVE", "USER-1", "ORG-A", NOW, NOW),
        )
        raw.execute(
            """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,
               stage,lifecycle_status,title,latest_action_bucket,latest_severity,latest_recommended_action,
               latest_evidence_json,observation_status,first_seen_at,last_seen_at,source_policy_version,
               version,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("AC-D11-UAT", "ORG-A", "ORD-D11-UAT", "D11:DELIVERY_RECOVERY:SO-D11-UAT",
             "DELIVERY_RECOVERY", "IN_PROGRESS", "ACTIVE", "解决 SO-D11-UAT 交期异常",
             "DO_NOW", "high", "先确认供应商能否按 8 月 20 日交货",
             json.dumps(["客户正式交期为 8 月 20 日", "供应商尚未给出确认承诺"], ensure_ascii=False),
             "OBSERVED", NOW, NOW, "D11_UAT_SEED", 1, NOW, NOW),
        )
        raw.execute(
            """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,
               status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            ("TK-D11-UAT-1", "ORG-A", "AC-D11-UAT", "联系供应商确认 8 月 20 日能否交货",
             "联系供应商，要求给出明确可交付日期", "TODO", 1, NOW, NOW),
        )
        raw.execute(
            """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,
               status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            ("TK-D11-UAT-2", "ORG-A", "AC-D11-UAT", "核对是否存在可替代的备货方案",
             "核对内部库存或替代供应方案；不要因为 Task 1 进入等待而隐藏本任务", "TODO", 1, NOW, NOW),
        )
        raw.execute(
            """INSERT INTO d10_business_actions(business_action_id,organization_id,action_case_id,task_id,order_id,
               action_type,target_type,target_id,payload_json,request_id,idempotency_key,request_hash,effect_hash,status,
               actor,source,reason,policy_version,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("BA-D11-UAT", "ORG-A", "AC-D11-UAT", "TK-D11-UAT-2", "ORD-D11-UAT",
             "PREPARE_CONTINGENCY_NOTE", "ORDER", "ORD-D11-UAT", json.dumps({"note": "备选方案待确认"}, ensure_ascii=False),
             "REQ-D11-UAT", "IDEMP-D11-UAT", "hash-request-uat", "hash-effect-uat", "ACCEPTED",
             "USER-1", "D11_UAT_SEED", "用于验证D11不会把ACCEPTED写成ERP成功", "D10_BUSINESS_ACTION_V1", NOW, NOW),
        )
        raw.execute(
            """INSERT INTO d10_outbox_events(event_id,organization_id,business_action_id,event_type,payload_json,
               dedupe_key,status,attempt_count,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("OB-D11-UAT", "ORG-A", "BA-D11-UAT", "BUSINESS_ACTION_ACCEPTED", json.dumps({"business_action_id": "BA-D11-UAT"}),
             "DEDUP-D11-UAT", "PENDING", 0, NOW, NOW),
        )

        # Order 2: Normal order (no issues, waiting for supplier confirmation)
        raw.execute(
            """INSERT INTO orders(order_id,order_no,customer_name,product_name,requested_delivery_date,
               latest_supplier_commitment,current_node,status,owner,organization_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("ORD-D11-NORMAL", "SO-D11-NORMAL", "Globex Corp", "涤纶面料", "2026-09-10", "2026-09-05",
             "备货采购", "ACTIVE", "USER-1", "ORG-A", NOW, NOW),
        )
        raw.execute(
            """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,
               stage,lifecycle_status,title,latest_action_bucket,latest_severity,latest_recommended_action,
               latest_evidence_json,observation_status,first_seen_at,last_seen_at,source_policy_version,
               version,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("AC-D11-NORMAL", "ORG-A", "ORD-D11-NORMAL", "D11:FOLLOW_UP:SO-D11-NORMAL",
             "FOLLOW_UP", "WAITING", "ACTIVE", "跟进 SO-D11-NORMAL 备货进度",
             "WAITING", "low", "等待供应商确认备货完成时间",
             json.dumps(["供应商已承诺 9 月 5 日前交货", "客户交期为 9 月 10 日"], ensure_ascii=False),
             "OBSERVED", NOW, NOW, "D11_UAT_SEED", 1, NOW, NOW),
        )
        raw.execute(
            """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,
               status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            ("TK-D11-NORMAL-1", "ORG-A", "AC-D11-NORMAL", "确认供应商备货完成时间",
             "与供应商确认备货是否按计划进行", "DONE", 1, NOW, NOW),
        )
        raw.execute(
            """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,
               status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            ("TK-D11-NORMAL-2", "ORG-A", "AC-D11-NORMAL", "准备出货文件",
             "准备装箱单、发票等出货文件", "TODO", 1, NOW, NOW),
        )

        raw.commit()
    finally:
        raw.close()

    print(out)
    print("UAT identity: USER-1 / token tok-user-1")
    print("Scenario: Action Case -> Task 1 start -> wait -> reply/recover; Task 2 must remain visible.")
    print("BusinessAction on Task 2 is ACCEPTED + Outbox PENDING and must NOT be shown as ERP success.")


if __name__ == "__main__":
    main()
