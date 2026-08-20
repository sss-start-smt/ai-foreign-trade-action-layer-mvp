from __future__ import annotations

import hmac
import json
import os
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
import re

from communication_workflows_patch import register_communication_workflows_patch
from action_rules import decide_task, next_workday_9, parse_dt
from excel_import_patch import register_excel_import_patch
from erpnext_readonly import register_erpnext_routes
import agent_api
from agent_api import register_agent_api
from v61_extensions import register_v61_extensions
from d9_task_waiting import D9NotFoundError, D9StateError
from d12_api import register_d12_api
from d13_api import register_d13_api
from d15_api import register_d15_api
from d16_api import register_d16_api
from d19_auth_api import register_d19_auth_api
from d19_ui_api import register_d19_ui_api, candidate_requires_manager
import d16_observability as d16
from d11_action_workspace import (
    build_case_workspace,
    list_action_workspaces,
    create_case_task,
    start_case_task,
    complete_case_task,
    wait_case_task,
    record_case_waiting_reply,
)

from coze_integration import (
    CozeWorkflowError,
    coze_status,
    confirmed_payload,
    normalize_ft01,
    normalize_ft02,
    run_workflow,
)

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from database import (
    db,
    table_exists,
    insert_or_replace,
    get_table_columns,
    begin_transaction,
    get_database_url,
    is_postgres_mode,
)
from auth import (
    get_current_identity,
    get_current_identity_optional,
    resolve_identity_for_testing,
    require_same_org,
    require_order_access,
    require_task_access,
    require_manager,
    require_run_access,
    can_modify_order,
    can_modify_task,
    audit_log,
    CurrentIdentity,
    TRUSTED_USER_MAP,
    DEMO_TOKEN_MAP,
)

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "action_layer.db")))
API_KEY = os.getenv("APP_API_KEY", "").strip()
CN_TZ = timezone(timedelta(hours=8))

app = FastAPI(title="AI外贸跟单行动系统", version="6.1.4.1.3")
FLOWORDER_SERVERLESS_MODE = os.getenv("FLOWORDER_SERVERLESS_MODE", "false").lower() == "true"
APP_STARTUP_STATE: dict[str, Any] = {
    "database_ready": False,
    "database_initializing": False,
    "startup_error": None,
    "initialized_at": None,
}
_STARTUP_LOCK = threading.Lock()
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
register_excel_import_patch(app)
register_communication_workflows_patch(app)
register_agent_api(app)
register_v61_extensions(app)
register_erpnext_routes(app)
register_d12_api(app)
register_d13_api(app)
register_d15_api(app)
register_d16_api(app)
register_d19_auth_api(app)
register_d19_ui_api(app)


def storage_status() -> dict[str, Any]:
    """Describe storage for the active backend without pretending PG is a local file."""
    database_url = get_database_url()
    render_runtime = bool(os.getenv("RENDER")) or Path("/opt/render/project/src").exists()
    railway_runtime = bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID"))
    if is_postgres_mode(database_url):
        return {
            "backend": "postgresql",
            "db_path": None,
            "render_runtime": render_runtime,
            "railway_runtime": railway_runtime,
            "on_persistent_path": True,
            "writable": True,
            "warning": None,
        }

    path = DB_PATH.resolve()
    path_text = str(path)
    persistent_prefixes = ("/var/data/", "/opt/render/project/src/storage/", "/data/")
    on_persistent_path = any(path_text.startswith(prefix) for prefix in persistent_prefixes)
    cloud_runtime = render_runtime or railway_runtime
    warning = None
    if cloud_runtime and not on_persistent_path:
        warning = (
            "当前数据库不在云平台持久卷目录中，重新部署或重启后数据可能丢失。"
            "Railway请挂载/data并设置DB_PATH=/data/action_layer.db；Render请挂载/var/data。"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        writable = os.access(path.parent, os.W_OK)
    except OSError:
        writable = False
    return {
        "backend": "sqlite",
        "db_path": path_text,
        "render_runtime": render_runtime,
        "railway_runtime": railway_runtime,
        "on_persistent_path": on_persistent_path,
        "writable": writable,
        "warning": warning,
    }


class AnyPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def iso(dt: datetime | None = None) -> str:
    return (dt or now_cn()).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


ACTIVATION_COLUMNS: dict[str, str] = {
    "owner": "TEXT",
    "action_readiness": "TEXT NOT NULL DEFAULT 'BASE_ONLY'",
    "contact_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
    "issue_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
    "initialization_waiting_on": "TEXT",
    "initialization_promised_reply_at": "TEXT",
    "initialization_note": "TEXT",
    "initialization_source": "TEXT",
    "initialized_at": "TEXT",
    "last_dynamic_update_at": "TEXT",
    "organization_id": "TEXT",
}
ACTION_READINESS_VALUES = {"BASE_ONLY", "NEEDS_STATUS", "READY_FOR_RANKING", "ACTION_GENERATED", "CLOSED"}
CONTACT_STATUS_VALUES = {"NOT_CONTACTED", "WAITING_REPLY", "REPLIED", "UNKNOWN"}
ISSUE_STATUS_VALUES = {"NONE", "KNOWN", "UNKNOWN"}


def ensure_activation_schema(conn: Any) -> None:
    columns = {col["name"] for col in get_table_columns(conn, "orders")}
    missing = [name for name in ACTIVATION_COLUMNS if name not in columns]
    if getattr(conn, "is_pg", False):
        if missing:
            raise RuntimeError(
                "PostgreSQL schema is behind the application; run `alembic upgrade head`. "
                f"Missing orders columns: {missing}"
            )
    else:
        for name, definition in ACTIVATION_COLUMNS.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE orders ADD COLUMN "{name}" {definition}')
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_action_readiness ON orders(action_readiness, requested_delivery_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_orders_owner ON orders(owner)")
        tasks_cols = {col["name"] for col in get_table_columns(conn, "tasks")}
        if "organization_id" not in tasks_cols:
            conn.execute('ALTER TABLE tasks ADD COLUMN "organization_id" TEXT')
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_org ON tasks(organization_id)")
        logs_cols = {col["name"] for col in get_table_columns(conn, "event_logs")}
        if "organization_id" not in logs_cols:
            conn.execute('ALTER TABLE event_logs ADD COLUMN "organization_id" TEXT')
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_org ON event_logs(organization_id)")
        approval_cols = {col["name"] for col in get_table_columns(conn, "approval_requests")}
        if "organization_id" not in approval_cols:
            conn.execute('ALTER TABLE approval_requests ADD COLUMN "organization_id" TEXT')
            conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_org ON approval_requests(organization_id)")
        if not table_exists(conn, "audit_logs"):
            conn.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
                audit_id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                actor_role TEXT NOT NULL,
                action TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                result TEXT NOT NULL DEFAULT 'SUCCESS',
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )""")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_logs(organization_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_logs(actor_user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action)")
        # Migration: add organization_id to intake_jobs, source_messages, candidate_reviews
        _migrate_intake_org_id(conn)
        _migrate_source_messages_org_id(conn)
        _migrate_candidate_reviews_org_id(conn)
        # Add indexes for org isolation
        conn.execute("CREATE INDEX IF NOT EXISTS idx_intake_jobs_org_status ON intake_jobs(organization_id, status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_source_messages_org ON source_messages(organization_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_candidate_reviews_org ON candidate_reviews(organization_id, status)")
    # Existing orders that already have open tasks are not first-use base records.
    conn.execute(
        """UPDATE orders SET action_readiness='ACTION_GENERATED',
           initialization_source=COALESCE(initialization_source,'MIGRATION_EXISTING_TASK'),
           initialized_at=COALESCE(initialized_at,updated_at),
           last_dynamic_update_at=COALESCE(last_dynamic_update_at,updated_at)
           WHERE EXISTS(SELECT 1 FROM tasks WHERE tasks.related_order_id=orders.order_id AND tasks.status!='DONE')"""
    )
    # Normalize legacy owner names so role-based views remain stable after upgrade.
    if "owner" in columns:
        for owner_name, owner_id in (("李梅", "USER-1"), ("王晓", "USER-2"), ("陈琳", "USER-3"), ("周主管", "MANAGER-1")):
            conn.execute("UPDATE orders SET owner=? WHERE TRIM(COALESCE(owner,''))=?", (owner_id, owner_name))
        # V5.2 and earlier allowed every imported order to remain unassigned. If an
        # upgraded database has no assigned order at all, preserve the old default
        # workspace by binding those legacy records to USER-1 once. Mixed databases
        # keep their unassigned records for the manager to allocate explicitly.
        total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        assigned_orders = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE TRIM(COALESCE(owner,'')) NOT IN ('','待分配','未分配','-','—')"
        ).fetchone()[0]
        if total_orders and not assigned_orders:
            conn.execute(
                "UPDATE orders SET owner='USER-1' WHERE TRIM(COALESCE(owner,'')) IN ('','待分配','未分配','-','—')"
            )
    _enrich_org_id(conn)
    conn.commit()


PG_REQUIRED_TABLES = {
    "orders", "source_messages", "tasks", "risk_signals", "commitment_history",
    "confirmation_snapshots", "event_logs", "idempotency_records", "candidate_reviews",
    "user_settings", "workflow_runs", "task_rankings", "intake_jobs", "order_dependencies",
    "logistics_events", "agent_chat_jobs", "agent_runs", "agent_tool_calls",
    "anomaly_candidates", "approval_requests", "daily_inspection_reports", "bulk_update_batches",
    "bulk_update_candidates", "analytics_events", "communication_events",
    "communication_workflow_runs", "communication_drafts", "communication_task_candidates",
    "order_import_rows", "order_import_batches",
    "action_cases", "d9_action_case_tasks", "d9_action_case_waitings", "d9_trace_events",
    "d10_business_actions", "d10_outbox_events", "d10_idempotency_records", "d10_audit_events",
    "d12_human_reviews", "d13_agent_runs", "d13_agent_trace_events", "d15_outbox_execution_state", "d15_execution_trace_events", "audit_logs",
    "erp_sync_state", "erp_read_snapshots",
}


def _prepare_legacy_sqlite_before_schema(conn: Any) -> None:
    """Repair legacy SQLite columns that current schema.sql indexes reference.

    CREATE TABLE IF NOT EXISTS does not add newly introduced columns to an existing
    SQLite table.  The current schema.sql creates idx_orders_org immediately after
    the table declarations, so an older persistent DB without organization_id would
    fail before ensure_activation_schema() got a chance to migrate it.

    Keep this preflight deliberately narrow and additive: it never drops or rewrites
    user data; it only adds missing activation/tenant columns to tables that already
    exist.  The full schema script and normal migration helpers still run afterwards.
    """
    if table_exists(conn, "orders"):
        order_columns = {col["name"] for col in get_table_columns(conn, "orders")}
        for name, definition in ACTIVATION_COLUMNS.items():
            if name not in order_columns:
                conn.execute(f'ALTER TABLE orders ADD COLUMN "{name}" {definition}')

    for table_name in ("tasks", "event_logs", "approval_requests"):
        if not table_exists(conn, table_name):
            continue
        columns = {col["name"] for col in get_table_columns(conn, table_name)}
        if "organization_id" not in columns:
            conn.execute(f'ALTER TABLE {table_name} ADD COLUMN "organization_id" TEXT')

    conn.commit()


def init_db() -> None:
    with db() as conn:
        if getattr(conn, "is_pg", False):
            # PostgreSQL schema is migration-owned. Check the whole current schema
            # in one round trip; serverless cold starts should not issue ~50
            # information_schema queries before the first request.
            existing_tables = {
                row[0]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = current_schema() AND table_type='BASE TABLE'"
                ).fetchall()
            }
            missing_tables = sorted(PG_REQUIRED_TABLES - existing_tables)
            if missing_tables:
                raise RuntimeError(
                    "PostgreSQL schema is not migrated; apply the CloudBase/PG baseline migration. "
                    f"Missing tables: {missing_tables}"
                )
        else:
            _prepare_legacy_sqlite_before_schema(conn)
            conn.executescript((BASE_DIR / "schema.sql").read_text(encoding="utf-8"))
        ensure_activation_schema(conn)
        count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        if count == 0 and os.getenv("SEED_DEMO_DATA", "false").lower() == "true":
            reset_demo_data(conn)


def reset_demo_data(conn: Any) -> None:
    for table in [
        "d13_agent_trace_events", "d13_agent_runs", "d12_human_reviews", "d15_execution_trace_events", "d15_outbox_execution_state", "d10_outbox_events", "d10_idempotency_records", "d10_audit_events", "d10_business_actions",
        "d9_trace_events", "d9_action_case_waitings", "d9_action_case_tasks", "action_cases",
        "bulk_update_candidates", "bulk_update_batches", "analytics_events",
        "agent_chat_jobs", "agent_tool_calls", "approval_requests", "anomaly_candidates", "daily_inspection_reports", "agent_runs", "logistics_events", "order_dependencies",
        "task_rankings", "workflow_runs", "user_settings", "candidate_reviews",
        "idempotency_records", "event_logs", "confirmation_snapshots",
        "commitment_history", "risk_signals", "tasks", "source_messages", "orders"
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
               organization_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*row, "ORG-A", iso(now), iso(now)),
        )
    for order_id, owner_id in (("ORD-1001", "USER-1"), ("ORD-1002", "USER-1"), ("ORD-1003", "USER-1"), ("ORD-1004", "USER-2"), ("ORD-1005", "USER-3")):
        conn.execute("UPDATE orders SET owner=?, organization_id=? WHERE order_id=?", (owner_id, "ORG-A", order_id))

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
               source_message_id,evidence_json,organization_id,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (*t[:-1], json.dumps(t[-1], ensure_ascii=False), "ORG-A", iso(now), iso(now)),
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
        """INSERT INTO source_messages(message_id,order_id,organization_id,source_channel,sender_role,message_type,raw_content,source_time,created_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (msg_id, "ORD-1001", "ORG-A", "email", "customer", "customer_request", raw, iso(now - timedelta(minutes=18)), iso(now - timedelta(minutes=18))),
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
        """INSERT INTO candidate_reviews(review_id,source_message_id,order_id,organization_id,workflow_source,candidate_json,status,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        ("REV-SEED-001", msg_id, "ORD-1001", "ORG-A", "COZE_FT01_SAMPLE", json.dumps(candidate, ensure_ascii=False), "PENDING", iso(now - timedelta(minutes=17))),
    )
    defaults = {"theme": "upstream", "compact": False, "show_demo": False, "current_user_id": "USER-1", "notifications": {"urgent": True, "waiting_overdue": True, "writeback": True, "daily_summary": False}}
    conn.execute("INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES(?,?,?)", ("USER-1", json.dumps(defaults, ensure_ascii=False), iso(now)))
    conn.execute(
        """UPDATE orders SET action_readiness='ACTION_GENERATED', contact_status='UNKNOWN', issue_status='UNKNOWN',
           initialization_source='DEMO', initialized_at=?, last_dynamic_update_at=?
           WHERE EXISTS(SELECT 1 FROM tasks WHERE tasks.related_order_id=orders.order_id)""",
        (iso(now), iso(now)),
    )
    conn.commit()



def get_order_id(conn: Any, wrapper: dict[str, Any], plan: dict[str, Any]) -> str | None:
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


def apply_writeback(conn: Any, wrapper: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
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
                "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
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
                "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
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
        "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
        (new_id("EVT"), "transaction", key, "FT03_WRITEBACK_COMMITTED",
         json.dumps(result, ensure_ascii=False), operator_id, timestamp),
    )
    conn.execute(
        "INSERT INTO idempotency_records VALUES(?,?,?,?)",
        (key, "COMMITTED", json.dumps(result, ensure_ascii=False), timestamp),
    )
    return result



OPERATORS = [
    {"user_id": "USER-1", "name": "李梅", "role": "跟单专员"},
    {"user_id": "USER-2", "name": "王晓", "role": "高级跟单"},
    {"user_id": "USER-3", "name": "陈琳", "role": "客户协调"},
    {"user_id": "MANAGER-1", "name": "周主管", "role": "业务主管"},
]
OWNER_NAMES = {item["user_id"]: item["name"] for item in OPERATORS} | {None: "未分配", "": "未分配", "待分配": "未分配"}
OWNER_IDS_BY_NAME = {item["name"]: item["user_id"] for item in OPERATORS}
RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def normalize_owner_value(value: Any, *, fallback: str | None = None) -> str | None:
    """Normalize Excel/UI owner names to stable user IDs."""
    text = str(value or "").strip()
    if not text or text in {"待分配", "未分配", "—", "-"}:
        return fallback
    if text in OWNER_NAMES:
        return text
    return OWNER_IDS_BY_NAME.get(text, fallback or text)


def is_manager(user_id: str | None) -> bool:
    if user_id and user_id in TRUSTED_USER_MAP:
        return TRUSTED_USER_MAP[user_id]["role"] == "manager"
    return str(user_id or "").upper() in {"MANAGER-1", "MANAGER-A", "MANAGER-B"}


def owner_matches(owner: Any, user_id: str | None) -> bool:
    if is_manager(user_id):
        return True
    normalized = normalize_owner_value(owner)
    return bool(normalized and normalized == user_id)


# Identity resolution is now handled exclusively by get_current_identity from auth.py
# DO NOT redefine this function - it uses token-only authentication
# Imported at top of file: from auth import get_current_identity


def _enrich_org_id(conn: Any) -> None:
    columns = {col["name"] for col in get_table_columns(conn, "orders")}
    if "organization_id" not in columns:
        return
    user_org_map = {uid: info["organization_id"] for uid, info in TRUSTED_USER_MAP.items()}
    for owner, org_id in [
        ("USER-1", "ORG-A"), ("USER-2", "ORG-A"), ("USER-3", "ORG-A"), ("MANAGER-1", "ORG-A"),
        ("OPERATOR-A1", "ORG-A"), ("OPERATOR-A2", "ORG-A"), ("MANAGER-A", "ORG-A"),
        ("OPERATOR-B1", "ORG-B"), ("OPERATOR-B2", "ORG-B"), ("MANAGER-B", "ORG-B"),
    ]:
        org = user_org_map.get(owner, "ORG-DEMO")
        if owner == "":
            continue
        if owner in columns or "owner" in columns:
            conn.execute("UPDATE orders SET organization_id=? WHERE owner=? AND organization_id IS NULL", (org, owner))
            conn.execute("UPDATE orders SET organization_id=? WHERE owner=? AND organization_id=''", (org, owner))
    conn.execute("UPDATE orders SET organization_id='ORG-DEMO' WHERE organization_id IS NULL OR organization_id=''")
    try:
        task_columns = {col["name"] for col in get_table_columns(conn, "tasks")}
        if "organization_id" in task_columns:
            conn.execute("UPDATE tasks SET organization_id=(SELECT organization_id FROM orders WHERE orders.order_id=tasks.related_order_id) WHERE organization_id IS NULL AND related_order_id IS NOT NULL")
            conn.execute("UPDATE tasks SET organization_id='ORG-DEMO' WHERE organization_id IS NULL")
    except Exception:
        pass
    try:
        log_columns = {col["name"] for col in get_table_columns(conn, "event_logs")}
        if "organization_id" in log_columns:
            conn.execute("UPDATE event_logs SET organization_id='ORG-DEMO' WHERE organization_id IS NULL")
    except Exception:
        pass
    try:
        approval_columns = {col["name"] for col in get_table_columns(conn, "approval_requests")}
        if "organization_id" in approval_columns:
            conn.execute("UPDATE approval_requests SET organization_id='ORG-DEMO' WHERE organization_id IS NULL")
    except Exception:
        pass
    conn.commit()


def _migrate_intake_org_id(conn: Any) -> None:
    """Add organization_id to intake_jobs and backfill from request_json or order."""
    cols = {col["name"] for col in get_table_columns(conn, "intake_jobs")}
    if "organization_id" not in cols:
        conn.execute('ALTER TABLE intake_jobs ADD COLUMN "organization_id" TEXT')
    conn.execute("""
        UPDATE intake_jobs SET organization_id = (
            SELECT o.organization_id FROM orders o WHERE o.order_id = intake_jobs.order_id
        ) WHERE organization_id IS NULL AND order_id IS NOT NULL
    """)
    conn.execute("""
        UPDATE intake_jobs SET organization_id = 'ORG-DEMO' WHERE organization_id IS NULL
    """)


def _migrate_source_messages_org_id(conn: Any) -> None:
    """Add organization_id to source_messages and backfill from orders."""
    cols = {col["name"] for col in get_table_columns(conn, "source_messages")}
    if "organization_id" not in cols:
        conn.execute('ALTER TABLE source_messages ADD COLUMN "organization_id" TEXT')
    conn.execute("""
        UPDATE source_messages SET organization_id = (
            SELECT o.organization_id FROM orders o WHERE o.order_id = source_messages.order_id
        ) WHERE organization_id IS NULL AND order_id IS NOT NULL
    """)
    conn.execute("""
        UPDATE source_messages SET organization_id = 'ORG-DEMO' WHERE organization_id IS NULL
    """)


def _migrate_candidate_reviews_org_id(conn: Any) -> None:
    """Add organization_id to candidate_reviews and backfill from orders or source_messages."""
    cols = {col["name"] for col in get_table_columns(conn, "candidate_reviews")}
    if "organization_id" not in cols:
        conn.execute('ALTER TABLE candidate_reviews ADD COLUMN "organization_id" TEXT')
    conn.execute("""
        UPDATE candidate_reviews SET organization_id = (
            SELECT o.organization_id FROM orders o WHERE o.order_id = candidate_reviews.order_id
        ) WHERE organization_id IS NULL AND order_id IS NOT NULL
    """)
    conn.execute("""
        UPDATE candidate_reviews SET organization_id = (
            SELECT sm.organization_id FROM source_messages sm WHERE sm.message_id = candidate_reviews.source_message_id
        ) WHERE organization_id IS NULL AND source_message_id IS NOT NULL
    """)
    conn.execute("""
        UPDATE candidate_reviews SET organization_id = 'ORG-DEMO' WHERE organization_id IS NULL
    """)


def _log_event(conn: Any, entity_type: str, entity_id: str, event_type: str, payload: Any, operator_id: str | None = None, org_id: str | None = None) -> None:
    columns = {col["name"] for col in get_table_columns(conn, "event_logs")}
    has_org = "organization_id" in columns
    if has_org and org_id is None:
        org_id = "ORG-DEMO"
    op_id = operator_id or "USER-1"
    if has_org:
        conn.execute(
            "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,organization_id,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (new_id("EVT"), entity_type, entity_id, event_type, json.dumps(payload, ensure_ascii=False), op_id, org_id, now_cn_iso()),
        )
    else:
        conn.execute(
            "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (new_id("EVT"), entity_type, entity_id, event_type, json.dumps(payload, ensure_ascii=False), op_id, now_cn_iso()),
        )



def rowdict(row: Any) -> dict[str, Any] | None:
    return dict(row) if row else None


def extract_order_numbers(raw: str) -> list[str]:
    # Match PO/SO/ORD followed by dash, underscore, or space, then alphanumeric parts
    found = re.findall(r"\b(?:PO|SO|ORD)[-_ ][A-Z0-9]+(?:[-_ ][A-Z0-9]+)*\b", raw or "", re.I)
    normalized = []
    for x in found:
        # Normalize: replace underscores and spaces with dashes, then uppercase
        norm = re.sub(r"[_ ]", "-", x).upper()
        # Remove any double dashes
        norm = re.sub(r"-{2,}", "-", norm)
        normalized.append(norm)
    return list(dict.fromkeys(normalized))

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



def record_coze_run(log: dict[str, Any]) -> None:
    with db() as conn:
        insert_or_replace(
            conn, "workflow_runs",
            ["run_id", "workflow_key", "workflow_id", "status", "input_json", "output_json",
             "coze_code", "coze_msg", "debug_url", "duration_ms", "created_at"],
            (
                log.get("run_id"), log.get("workflow_key"), log.get("workflow_id"),
                log.get("status"), log.get("input_json") or "{}", log.get("output_json"),
                log.get("coze_code"), log.get("coze_msg"), log.get("debug_url"),
                log.get("duration_ms"), log.get("created_at") or iso(),
            ),
            conflict_key="run_id",
        )
        conn.commit()


def coze_http_error(exc: CozeWorkflowError) -> HTTPException:
    detail = {
        "message": str(exc),
        "workflow": exc.workflow_key,
        "coze_code": exc.code,
        "debug_url": exc.debug_url,
    }
    return HTTPException(status_code=502 if exc.status_code != 401 else 401, detail=detail)


def order_and_task_context(order_id: str | None, raw: str = "", org_id: str | None = None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    with db() as conn:
        order = None
        if order_id:
            if org_id:
                # P0-2: Enforce organization isolation when explicit order_id is provided
                order = rowdict(conn.execute(
                    "SELECT * FROM orders WHERE order_id=? AND organization_id=?",
                    (order_id, org_id)
                ).fetchone())
            else:
                order = rowdict(conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone())
        if not order:
            order_nos = extract_order_numbers(raw)
            if len(order_nos) == 1:
                if org_id:
                    # Enforce organization isolation: only match orders within the caller's org
                    order = rowdict(conn.execute(
                        "SELECT * FROM orders WHERE order_no=? AND organization_id=?", 
                        (order_nos[0], org_id)
                    ).fetchone())
                else:
                    order = rowdict(conn.execute("SELECT * FROM orders WHERE order_no=?", (order_nos[0],)).fetchone())
        task = None
        if order:
            task = rowdict(conn.execute(
                """SELECT * FROM tasks WHERE related_order_id=? AND status!='DONE'
                   ORDER BY CASE WHEN waiting_on IS NOT NULL THEN 0 ELSE 1 END, updated_at DESC LIMIT 1""",
                (order["order_id"],),
            ).fetchone())
            if task:
                task["evidence"] = json.loads(task.pop("evidence_json") or "[]")
        return order, task



def normalize_source_channel(value: Any) -> str:
    """Map website labels to the channel values used by the workflows."""
    raw = str(value or "").strip().lower()
    aliases = {
        "mail": "email",
        "email": "email",
        "wechat": "wechat",
        "weixin": "wechat",
        "erp": "erp_export",
        "erp_export": "erp_export",
        "internal": "manual_input",
        "manual": "manual_input",
        "manual_input": "manual_input",
    }
    return aliases.get(raw, "manual_input")


def compact_order_context(order: dict[str, Any] | None) -> dict[str, Any]:
    if not order:
        return {}
    fields = (
        "order_id", "order_no", "customer_name", "product_name", "sku",
        "quantity", "unit", "packaging_method", "requested_delivery_date",
        "customer_delivery_date", "latest_supplier_commitment", "current_progress",
        "current_node", "factory_name", "owner", "specification", "material",
        "color", "logo_process", "status",
    )
    return {key: order.get(key) for key in fields if order.get(key) not in (None, "")}


def compact_task_context(task: dict[str, Any] | None) -> dict[str, Any]:
    if not task:
        return {}
    fields = (
        "task_id", "title", "recommended_action", "target", "status",
        "waiting_on", "promised_reply_at", "next_action_at", "business_deadline",
        "last_contact_at", "risk_level", "evidence",
    )
    return {key: task.get(key) for key in fields if task.get(key) not in (None, "", [])}


def build_ft01_parameters(
    body: dict[str, Any],
    order: dict[str, Any] | None,
    task: dict[str, Any] | None,
) -> dict[str, Any]:
    raw_content = str(body.get("raw_content") or "").strip()
    input_type = str(body.get("input_type") or "text").strip().lower()
    if input_type not in {"text", "image", "pdf"}:
        input_type = "text"
    return {
        "input_type": input_type,
        "existing_order_context": json.dumps(compact_order_context(order), ensure_ascii=False),
        "timezone": str(body.get("timezone") or "Asia/Shanghai"),
        "existing_task_context": json.dumps(compact_task_context(task), ensure_ascii=False),
        "source_channel": normalize_source_channel(body.get("source_channel")),
        "sender_role_hint": str(
            body.get("sender_role") or body.get("sender_role_hint") or "customer"
        ).strip().lower(),
        "document_type_hint": str(body.get("document_type_hint") or "").strip(),
        "file_url": str(body.get("file_url") or "").strip(),
        "raw_content": raw_content,
        "source_time": str(body.get("source_time") or iso()),
    }


def build_ft02_parameters(
    body: dict[str, Any],
    order: dict[str, Any] | None,
    task: dict[str, Any] | None,
) -> dict[str, Any]:
    task_context = compact_task_context(task)
    task_context.setdefault(
        "questions",
        [
            "当前准确完成比例是多少？",
            "具体完工日期是什么？",
            "补救方案是什么？",
        ],
    )
    order_context = compact_order_context(order)
    # FT02的order_context是必填String。即使订单未识别，也传合法JSON对象，
    # 避免发送空字符串触发开始节点API参数校验。
    order_context.setdefault("order_id", body.get("order_id"))
    return {
        "task_context": json.dumps(task_context, ensure_ascii=False),
        "message_content": str(
            body.get("raw_content") or body.get("message_content") or ""
        ).strip(),
        "source_channel": normalize_source_channel(body.get("source_channel")),
        "sender_role": str(body.get("sender_role") or "factory").strip().lower(),
        "source_time": str(body.get("source_time") or iso()),
        "timezone": str(body.get("timezone") or "Asia/Shanghai"),
        "order_context": json.dumps(order_context, ensure_ascii=False),
    }

def create_or_update_action_task(conn: Any, review: dict[str, Any], candidate: dict[str, Any], order_id: str) -> str | None:
    action = (candidate.get("action_candidates") or [None])[0]
    if not action:
        return None
    integration = candidate.get("_integration") or {}
    task_id = integration.get("task_id")
    timestamp = iso()
    evidence = [
        x.get("source_quote") or x.get("evidence")
        for x in (candidate.get("fields") or []) + (candidate.get("risk_signals") or [])
        if x.get("source_quote") or x.get("evidence")
    ]
    risk_level = max(
        (r.get("risk_level") or "none" for r in candidate.get("risk_signals") or []),
        key=lambda x: RISK_ORDER.get(x, 0), default="medium",
    )
    if task_id and conn.execute("SELECT 1 FROM tasks WHERE task_id=?", (task_id,)).fetchone():
        conn.execute(
            """UPDATE tasks SET title=?,recommended_action=?,target=?,waiting_on=NULL,
               promised_reply_at=NULL,next_action_at=?,risk_level=?,urgent=?,pending_confirmation=0,
               evidence_json=?,updated_at=? WHERE task_id=?""",
            (
                action.get("title") or "处理AI候选行动",
                action.get("recommended_action") or action.get("title") or "处理AI候选行动",
                action.get("target") or "factory",
                iso(now_cn() + timedelta(hours=4)), risk_level, int(risk_level == "critical"),
                json.dumps(evidence, ensure_ascii=False), timestamp, task_id,
            ),
        )
        return task_id
    task_id = new_id("TASK")
    conn.execute(
        """INSERT INTO tasks(task_id,related_order_id,title,recommended_action,target,status,
           owner_user_id,responsibility_status,waiting_on,promised_reply_at,next_action_at,
           business_deadline,last_contact_at,risk_level,urgent,pending_confirmation,
           source_message_id,evidence_json,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            task_id, order_id, action.get("title") or "处理AI候选行动",
            action.get("recommended_action") or action.get("title") or "处理AI候选行动",
            action.get("target") or "factory", "OPEN", "USER-1", "assigned", None, None,
            iso(now_cn() + timedelta(hours=4)), iso(now_cn() + timedelta(hours=8)), None,
            risk_level, int(risk_level == "critical"), 0, review.get("source_message_id"),
            json.dumps(evidence, ensure_ascii=False), timestamp, timestamp,
        ),
    )
    return task_id


def run_ft04_refresh(current_user_id: str = "USER-1", *, raise_on_error: bool = False) -> dict[str, Any] | None:
    status = coze_status()
    if not status.get("ready"):
        return None
    with db() as conn:
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()]
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders").fetchall()]
        risks = [dict(r) for r in conn.execute("SELECT * FROM risk_signals WHERE status='OPEN'").fetchall()]
    for task in tasks:
        task["evidence"] = json.loads(task.pop("evidence_json") or "[]")
    params = {
        "timezone": "Asia/Shanghai",
        "current_time": iso(),
        "current_user_id": current_user_id,
        "orders_json": json.dumps(orders, ensure_ascii=False),
        "risk_signals_json": json.dumps(risks, ensure_ascii=False),
        "workday_policy_json": json.dumps({"timezone": "Asia/Shanghai"}, ensure_ascii=False),
        "ranking_config_json": json.dumps({"today_due_hours": 12, "escalation_overdue_hours": 8, "top_n": 5}, ensure_ascii=False),
        "tasks_json": json.dumps(tasks, ensure_ascii=False),
    }
    try:
        run = run_workflow("ft04", params, record=record_coze_run)
    except CozeWorkflowError:
        if raise_on_error:
            raise
        return None
    result = run.result
    if result.get("run_status") != "success":
        if raise_on_error:
            raise CozeWorkflowError(f"FT04未成功：{result}", workflow_key="ft04", debug_url=run.debug_url)
        return None
    with db() as conn:
        conn.execute("DELETE FROM task_rankings WHERE current_user_id=?", (current_user_id,))
        for item in result.get("items") or []:
            if not item.get("task_id"):
                continue
            conn.execute(
                """INSERT INTO task_rankings(current_user_id,task_id,action_state,recommended_action,target,
                   next_action_at,ranking_suppressed,priority_score,priority_reasons_json,evidence_json,
                   workflow_run_id,calculated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    current_user_id, item.get("task_id"), item.get("action_state") or "SCHEDULED",
                    item.get("recommended_action"), item.get("target"), item.get("next_action_at"),
                    int(bool(item.get("ranking_suppressed"))), float(item.get("priority_score") or 0),
                    json.dumps(item.get("priority_reasons") or [], ensure_ascii=False),
                    json.dumps(item.get("evidence") or [], ensure_ascii=False), run.run_id, iso(),
                ),
            )
        conn.commit()
    return {"workflow_run_id": run.run_id, "debug_url": run.debug_url, "result": result}


def coze_rankings(current_user_id: str) -> dict[str, dict[str, Any]]:
    with db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM task_rankings WHERE current_user_id=?", (current_user_id,)
        ).fetchall()]
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        row["ranking_suppressed"] = bool(row["ranking_suppressed"])
        row["priority_reasons"] = json.loads(row.pop("priority_reasons_json") or "[]")
        row["evidence"] = json.loads(row.pop("evidence_json") or "[]")
        out[row["task_id"]] = row
    return out


# resolve_identity_dependency is now imported as get_current_identity from auth.py
# Kept as alias for backward compatibility
resolve_identity_dependency = get_current_identity


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


# ── D11 UAT Fixture Provider (TEST/UAT ONLY) ────────────────────────────
# Enabled only when D11_UAT_INTAKE_PROVIDER=fixture is set.
# Produces deterministic candidate changes for known test messages.
# Does NOT call Coze, does NOT bypass human review, does NOT modify orders.
# Safe for local UAT; never active in production.

_UAT_FIXTURE_RULES: list[dict[str, Any]] = [
    {
        "match": "延迟",
        "message_type": "factory_update",
        "order_changes": [
            {"field_name": "current_progress", "normalized_value": 0.6, "source_quote": "生产进度60%", "confidence": 0.9},
        ],
        "risks": [
            {"type": "delivery_impact_unknown", "risk_level": "high", "evidence": "交期可能延迟"},
        ],
        "actions": [
            {"action_type": "confirm_with_factory", "title": "确认变更是否影响交期", "recommended_action": "联系工厂确认延迟影响", "target": "factory"},
        ],
    },
    {
        "match": "取消",
        "message_type": "customer_request",
        "order_changes": [],
        "risks": [
            {"type": "customer_cancellation", "risk_level": "critical", "evidence": "客户要求取消"},
        ],
        "actions": [
            {"action_type": "reply_customer", "title": "立即处理客户取消请求", "recommended_action": "立即回复客户并确认取消原因", "target": "customer"},
        ],
    },
    {
        "match": "投诉",
        "message_type": "complaint",
        "order_changes": [],
        "risks": [
            {"type": "customer_complaint", "risk_level": "critical", "evidence": "客户投诉"},
        ],
        "actions": [
            {"action_type": "reply_customer", "title": "立即处理客户投诉", "recommended_action": "立即回复客户并同步主管", "target": "customer"},
        ],
    },
    {
        "match": "样品",
        "message_type": "factory_update",
        "order_changes": [
            {"field_name": "packaging_method", "normalized_value": "样品包装", "source_quote": "样品包装方式", "confidence": 0.95},
        ],
        "risks": [],
        "actions": [
            {"action_type": "check_order", "title": "确认样品进展", "recommended_action": "核对样品状态并安排后续动作", "target": "factory"},
        ],
    },
    {
        "match": "付款",
        "message_type": "customer_request",
        "order_changes": [
            {"field_name": "payment_status", "normalized_value": "pending", "source_quote": "等待付款", "confidence": 0.9},
        ],
        "risks": [],
        "actions": [
            {"action_type": "check_order", "title": "核对付款状态", "recommended_action": "核对付款状态并确定下一步", "target": "finance"},
        ],
    },
]


def uat_fixture_candidate(raw: str, sender_role: str, source_time: str | None, order: dict[str, Any] | None) -> dict[str, Any]:
    """Deterministic fixture provider for D11 local UAT (TEST/UAT ONLY).

    Scans raw message for known keywords and produces fixed candidate changes.
    Results are clearly marked as UAT_FIXTURE_PROVIDER. Never active in production.
    """
    matched: dict[str, Any] | None = None
    for rule in _UAT_FIXTURE_RULES:
        if rule["match"] in raw:
            matched = rule
            break

    if matched is None:
        matched = {
            "message_type": "customer_request" if sender_role == "customer" else "factory_update",
            "order_changes": [],
            "risks": [],
            "actions": [
                {"action_type": "check_order", "title": "核对消息并安排下一步", "recommended_action": "核对订单状态并确定后续动作", "target": sender_role or "unknown"},
            ],
        }

    order_nos = extract_order_numbers(raw)
    match_status = "unique_match" if order else ("multiple_matches" if len(order_nos) > 1 else "no_match")

    return {
        "message_type": matched["message_type"],
        "order_match": {
            "status": match_status,
            "selected_order_id": order.get("order_id") if order else None,
            "matched_order_no": order.get("order_no") if order else None,
            "candidate_order_nos": order_nos,
        },
        "fields": matched.get("order_changes", []),
        "risk_signals": matched.get("risks", []),
        "action_candidates": matched.get("actions", []),
        "manual_review_required": True,
        "_uat_fixture": True,
        "_integration": {
            "workflow_key": "UAT_FIXTURE_PROVIDER",
            "fallback_reason": "D11_UAT_INTAKE_PROVIDER=fixture",
        },
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
    identity: CurrentIdentity = Depends(get_current_identity),
    state: str | None = Query(None),
    q: str | None = Query(None),
) -> dict[str, Any]:
    data = build_dashboard(identity, current_time=None)
    items = data["items"]
    if state and state != "ALL":
        items = [x for x in items if x["action_state"] == state]
    if q:
        needle = q.lower()
        items = [x for x in items if needle in " ".join(str(v or "") for v in [x.get("title"), x.get("recommended_action"), (x.get("order") or {}).get("order_no"), (x.get("order") or {}).get("customer_name")]).lower()]
    return {"items": items, "summary": data["summary"], "total": len(items)}


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    allowed = TASK_FIELDS | {"urgent"}
    updates = {k: v for k, v in body.items() if k in allowed}
    if not updates:
        raise HTTPException(422, "没有可更新字段")
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        task_dict = dict(row)
        order_id = task_dict.get("related_order_id")
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone() if order_id else None
        order_dict = dict(order_row) if order_row else None
        
        require_task_access(identity, task_dict, order_dict)
        
        timestamp = iso()
        parts, values = [], []
        for key, value in updates.items():
            parts.append(f"{key}=?")
            values.append(int(value) if key in {"urgent", "pending_confirmation"} and isinstance(value, bool) else value)
        values += [timestamp, task_id]
        conn.execute(f"UPDATE tasks SET {', '.join(parts)}, updated_at=? WHERE task_id=?", values)
        op_id = identity.user_id
        conn.execute("INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)", (new_id("EVT"), "task", task_id, "TASK_UPDATED_FROM_UI", json.dumps(updates, ensure_ascii=False), op_id, timestamp))
        audit_log(conn, identity, "TASK_UPDATED", "task", task_id, "SUCCESS", updates)
        conn.commit()
    return {"status": "updated", "task_id": task_id, "changes": updates}


@app.post("/api/tasks/{task_id}/transfer")
def transfer_task(task_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    owner = body.get("owner_user_id")
    if not owner:
        raise HTTPException(422, "缺少owner_user_id")
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        task_dict = dict(row)
        order_id = task_dict.get("related_order_id")
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone() if order_id else None
        order_dict = dict(order_row) if order_row else None
        require_task_access(identity, task_dict, order_dict)
        if not identity.is_manager():
            raise HTTPException(403, "仅主管可转交任务")
    return update_task(task_id, AnyPayload(owner_user_id=owner, responsibility_status="assigned"), identity)


@app.post("/api/tasks/{task_id}/escalate")
def escalate_task(task_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        task_dict = dict(row)
        order_id = task_dict.get("related_order_id")
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone() if order_id else None
        order_dict = dict(order_row) if order_row else None
        require_task_access(identity, task_dict, order_dict)
        if not identity.is_manager():
            raise HTTPException(403, "仅主管可升级任务")
        timestamp = iso()
        conn.execute("UPDATE tasks SET risk_level='critical', urgent=1, target='manager', owner_user_id='MANAGER-1', updated_at=? WHERE task_id=?", (timestamp, task_id))
        conn.execute("INSERT INTO risk_signals VALUES(?,?,?,?,?,?,?,?,?,?)", (new_id("RISK"), order_id, task_id, "manager_escalation", "critical", body.get("reason") or "一线人员请求主管介入", "R_MANUAL_ESCALATION", "OPEN", timestamp, timestamp))
        conn.execute("INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)", (new_id("EVT"), "task", task_id, "TASK_ESCALATED", json.dumps(body, ensure_ascii=False), identity.user_id, timestamp))
        audit_log(conn, identity, "TASK_ESCALATED", "task", task_id, "SUCCESS", body)
        conn.commit()
    return {"status": "escalated", "task_id": task_id}


@app.post("/api/tasks/{task_id}/contacted")
def task_contacted(task_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        task_dict = dict(row)
        order_id = task_dict.get("related_order_id")
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone() if order_id else None
        order_dict = dict(order_row) if order_row else None
        require_task_access(identity, task_dict, order_dict)
        timestamp = iso()
        contact_notes = str(body.get("notes") or body.get("contact_notes") or "")
        waiting_on = str(body.get("waiting_on") or task_dict.get("waiting_on") or "").strip()
        promised_reply_at = body.get("promised_reply_at") or task_dict.get("promised_reply_at")
        next_action_at = body.get("next_action_at") or task_dict.get("next_action_at")
        conn.execute(
            "UPDATE tasks SET status='WAITING_EXTERNAL', last_contact_at=?, waiting_on=?, promised_reply_at=?, next_action_at=?, updated_at=? WHERE task_id=?",
            (timestamp, waiting_on or None, promised_reply_at, next_action_at, timestamp, task_id),
        )
        conn.execute(
            "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (new_id("EVT"), "task", task_id, "TASK_CONTACTED", json.dumps({"notes": contact_notes, "waiting_on": waiting_on}, ensure_ascii=False), identity.user_id, timestamp),
        )
        audit_log(conn, identity, "TASK_CONTACTED", "task", task_id, "SUCCESS", body)
        conn.commit()
    return {"status": "contacted", "task_id": task_id}


@app.post("/api/tasks/{task_id}/complete")
def task_complete(task_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        task_dict = dict(row)
        order_id = task_dict.get("related_order_id")
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone() if order_id else None
        order_dict = dict(order_row) if order_row else None
        require_task_access(identity, task_dict, order_dict)
        timestamp = iso()
        completion_notes = str(body.get("notes") or body.get("completion_notes") or "")
        conn.execute(
            "UPDATE tasks SET status='DONE', updated_at=? WHERE task_id=?",
            (timestamp, task_id),
        )
        conn.execute(
            "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (new_id("EVT"), "task", task_id, "TASK_COMPLETED", json.dumps({"notes": completion_notes}, ensure_ascii=False), identity.user_id, timestamp),
        )
        audit_log(conn, identity, "TASK_COMPLETED", "task", task_id, "SUCCESS", body)
        conn.commit()
    return {"status": "completed", "task_id": task_id}


@app.get("/api/orders/{order_id}")
def order_detail(order_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "订单不存在")
        order_dict = dict(row)
        # CRITICAL: Check organization access before returning
        require_order_access(identity, order_dict, conn)
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks WHERE related_order_id=? AND status!='DONE' ORDER BY updated_at DESC", (order_id,)).fetchall()]
        for task in tasks:
            task["evidence"] = json.loads(task.pop("evidence_json") or "[]")
        events = [dict(r) for r in conn.execute("SELECT * FROM event_logs WHERE entity_id=? ORDER BY created_at DESC LIMIT 30", (order_id,)).fetchall()]
    return {"order": order_dict, "tasks": tasks, "events": events}


@app.post("/api/orders")
def create_order(payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    order_no = str(body.get("order_no") or "").strip()
    customer_name = str(body.get("customer_name") or "").strip()
    if not order_no:
        raise HTTPException(422, "缺少订单号")
    if not customer_name:
        raise HTTPException(422, "缺少客户名称")
    timestamp = iso()
    order_id = str(body.get("order_id") or new_id("ORD"))
    values = {
        "order_id": order_id,
        "order_no": order_no,
        "customer_name": customer_name,
        "product_name": body.get("product_name"),
        "packaging_method": body.get("packaging_method"),
        "requested_delivery_date": body.get("requested_delivery_date") or body.get("customer_delivery_date"),
        "latest_supplier_commitment": body.get("latest_supplier_commitment") or body.get("supplier_completion_commitment_date"),
        "current_progress": body.get("current_progress"),
        "current_node": body.get("current_node"),
        "status": body.get("status") or "ACTIVE",
        "action_readiness": "BASE_ONLY",
        "contact_status": "UNKNOWN",
        "issue_status": "UNKNOWN",
        "initialization_source": "MANUAL_CREATE",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with db() as conn:
        if conn.execute("SELECT 1 FROM orders WHERE order_no=?", (order_no,)).fetchone():
            raise HTTPException(409, "订单号已存在")
        columns = {col["name"] for col in get_table_columns(conn, "orders")}
        operator_id = identity.user_id
        requested_owner = normalize_owner_value(body.get("owner"), fallback=operator_id)
        effective_owner = requested_owner if identity.is_manager() else operator_id
        # Force organization_id from identity - NEVER from client input
        effective_org = identity.organization_id
        optional = {
            "sku": body.get("sku"), "quantity": body.get("quantity"), "unit": body.get("unit"),
            "factory_name": body.get("factory_name"), "owner": effective_owner,
            "specification": body.get("specification"), "material": body.get("material"),
            "color": body.get("color"), "logo_process": body.get("logo_process"),
            "organization_id": effective_org,
        }
        values.update({k: v for k, v in optional.items() if k in columns})
        names = list(values)
        quoted = ", ".join(f'"{name}"' for name in names)
        placeholders = ", ".join("?" for _ in names)
        conn.execute(f"INSERT INTO orders ({quoted}) VALUES ({placeholders})", [values[name] for name in names])
        conn.execute(
            "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (new_id("EVT"), "order", order_id, "ORDER_CREATED_FROM_UI", json.dumps(body, ensure_ascii=False), operator_id, timestamp),
        )
        audit_log(conn, identity, "ORDER_CREATED", "order", order_id, "SUCCESS", body)
        conn.commit()
    return {"status": "created", "order_id": order_id, "order_no": order_no}


@app.patch("/api/orders/{order_id}")
def update_order(order_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    # D12: the customer-facing requested delivery date is a formal commitment.
    # It may no longer be changed through the generic order PATCH route; an
    # approved D12 request must enter the frozen D10 BusinessAction/Outbox path.
    protected_commitment_inputs = {"requested_delivery_date", "customer_delivery_date"}
    if protected_commitment_inputs.intersection(body):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "D12_MANAGER_APPROVAL_REQUIRED",
                "message": "客户正式交期属于受保护承诺字段，请从行动任务发起主管审批。",
                "required_review": "MANAGER_APPROVAL",
            },
        )
    canonical = {
        "order_no": "order_no", "customer_name": "customer_name", "product_name": "product_name",
        "packaging_method": "packaging_method", "requested_delivery_date": "requested_delivery_date",
        "customer_delivery_date": "requested_delivery_date",
        "latest_supplier_commitment": "latest_supplier_commitment",
        "supplier_completion_commitment_date": "latest_supplier_commitment",
        "current_progress": "current_progress", "current_node": "current_node", "status": "status",
        "action_readiness": "action_readiness", "contact_status": "contact_status",
        "issue_status": "issue_status", "initialization_note": "initialization_note",
        "sku": "sku", "quantity": "quantity", "unit": "unit", "factory_name": "factory_name",
        "owner": "owner", "specification": "specification", "material": "material",
        "color": "color", "logo_process": "logo_process",
    }
    with db() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "订单不存在")
        order_dict = dict(row)
        require_order_access(identity, order_dict, conn)
        columns = {col["name"] for col in get_table_columns(conn, "orders")}
        updates = {}
        for source, target in canonical.items():
            if source in body and target in columns:
                if target == "owner":
                    if not identity.is_manager():
                        continue
                    updates[target] = normalize_owner_value(body[source], fallback=order_dict.get("owner"))
                else:
                    updates[target] = body[source]
        if not updates:
            raise HTTPException(422, "没有可更新字段")
        dynamic_fields = {"current_node", "current_progress", "latest_supplier_commitment", "contact_status", "issue_status"}
        if dynamic_fields.intersection(updates) and order_dict.get("action_readiness") in {"BASE_ONLY", "NEEDS_STATUS"}:
            updates.setdefault("action_readiness", "READY_FOR_RANKING")
            if "initialization_source" in columns:
                updates.setdefault("initialization_source", "MANUAL_EDIT")
            if "initialized_at" in columns:
                updates.setdefault("initialized_at", iso())
            if "last_dynamic_update_at" in columns:
                updates.setdefault("last_dynamic_update_at", iso())
        if "order_no" in updates:
            exists = conn.execute("SELECT 1 FROM orders WHERE order_no=? AND order_id<>?", (updates["order_no"], order_id)).fetchone()
            if exists:
                raise HTTPException(409, "订单号已被其他订单使用")
        timestamp = iso()
        updates["updated_at"] = timestamp
        parts = [f'"{name}"=?' for name in updates]
        conn.execute(f"UPDATE orders SET {', '.join(parts)} WHERE order_id=?", [*updates.values(), order_id])
        conn.execute(
            "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (new_id("EVT"), "order", order_id, "ORDER_UPDATED_FROM_UI", json.dumps(updates, ensure_ascii=False), identity.user_id, timestamp),
        )
        audit_log(conn, identity, "ORDER_UPDATED", "order", order_id, "SUCCESS", updates)
        conn.commit()
    return {"status": "updated", "order_id": order_id, "changes": updates}



def activation_readiness_label(value: str | None) -> str:
    return {
        "BASE_ONLY": "仅有基础订单",
        "NEEDS_STATUS": "待补充进展",
        "READY_FOR_RANKING": "可生成行动",
        "ACTION_GENERATED": "已有行动",
        "CLOSED": "已完成",
    }.get(value or "BASE_ONLY", value or "仅有基础订单")


def activation_recommendation_key(order: dict[str, Any]) -> tuple[int, str, str]:
    delivery = parse_dt(order.get("requested_delivery_date"))
    return (0 if delivery else 1, delivery.isoformat() if delivery else "9999-12-31", str(order.get("updated_at") or ""))


def activation_snapshot(conn: Any, current_user_id: str | None = None) -> dict[str, Any]:
    orders = [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY updated_at DESC").fetchall()]
    if current_user_id and not is_manager(current_user_id):
        orders = [o for o in orders if owner_matches(o.get("owner"), current_user_id)]
    active = [o for o in orders if str(o.get("status") or "ACTIVE").upper() not in {"DONE", "COMPLETED", "CANCELLED", "CLOSED"}]
    counts = {key: 0 for key in ACTION_READINESS_VALUES}
    for order in active:
        readiness = order.get("action_readiness") or "BASE_ONLY"
        counts[readiness if readiness in counts else "BASE_ONLY"] += 1
    candidates = [o for o in active if (o.get("action_readiness") or "BASE_ONLY") in {"BASE_ONLY", "NEEDS_STATUS"}]
    candidates.sort(key=activation_recommendation_key)
    recommended = []
    for order in candidates[:6]:
        item = dict(order)
        item["action_readiness"] = item.get("action_readiness") or "BASE_ONLY"
        item["action_readiness_label"] = activation_readiness_label(item["action_readiness"])
        recommended.append(item)
    target = min(3, len(active))
    generated = counts["ACTION_GENERATED"]
    return {
        "total_orders": len(orders),
        "active_orders": len(active),
        "counts": counts,
        "needs_initialization": counts["BASE_ONLY"] + counts["NEEDS_STATUS"],
        "ready_or_action": counts["READY_FOR_RANKING"] + counts["ACTION_GENERATED"],
        "action_generated": generated,
        "activation_target": target,
        "ranking_experience_ready": target > 0 and generated >= target,
        "recommended_orders": recommended,
    }


def activation_risk(order: dict[str, Any], issue_status: str) -> tuple[str, int]:
    delivery = parse_dt(order.get("requested_delivery_date"))
    current = now_cn()
    if issue_status == "KNOWN":
        return "high", 1
    if delivery:
        hours = (delivery - current).total_seconds() / 3600
        if hours <= 0:
            return "high", 1
        if hours <= 72:
            return "high", 0
        if hours <= 24 * 7:
            return "medium", 0
    return "low", 0


def upsert_activation_task(
    conn: Any,
    order: dict[str, Any],
    *,
    contact_status: str,
    issue_status: str,
    waiting_on: str | None,
    promised_reply_at: str | None,
    note: str | None,
    operator_id: str,
) -> str:
    order_id = order["order_id"]
    order_no = order.get("order_no") or order_id
    timestamp = iso()
    delivery = order.get("requested_delivery_date")
    risk_level, urgent = activation_risk(order, issue_status)
    pending_confirmation = 0
    target = waiting_on or "factory"
    waiting_value = None
    promise_value = None
    next_action = timestamp
    evidence = ["QUICK_INITIALIZATION", f"订单{order_no}完成首次状态初始化"]
    if note:
        evidence.append(note)

    if contact_status == "WAITING_REPLY":
        target_label = "客户" if target == "customer" else "工厂"
        title = f"等待{target_label}回复：{order_no}"
        action = f"在承诺回复时间前等待{target_label}；到期后再跟进"
        waiting_value = target
        promise_value = promised_reply_at
        next_action = promised_reply_at
    elif contact_status == "REPLIED":
        title = f"核对{order_no}最新回复"
        action = "粘贴最近回复，确认进度、生产完成承诺、异常与未回答事项"
        pending_confirmation = 1
    elif issue_status == "KNOWN":
        title = f"处理{order_no}已知异常"
        action = "补充异常证据并联系相关方确认影响、完成时间和补救方案"
    elif contact_status == "NOT_CONTACTED":
        title = f"确认{order_no}当前进展"
        action = "联系工厂确认当前节点、准确进度、生产完成承诺和已知异常"
    else:
        title = f"补充{order_no}当前进展"
        action = "粘贴最近沟通，或确认当前节点、联系状态和已知异常"
        pending_confirmation = 1

    existing = conn.execute(
        """SELECT task_id FROM tasks WHERE related_order_id=? AND status!='DONE'
           AND evidence_json LIKE '%QUICK_INITIALIZATION%' ORDER BY updated_at DESC LIMIT 1""",
        (order_id,),
    ).fetchone()
    task_id = existing["task_id"] if existing else new_id("TASK")
    task_owner_id = normalize_owner_value(order.get("owner"), fallback=operator_id) or operator_id
    params = (
        title, action, target, "OPEN", task_owner_id, "assigned", waiting_value,
        promise_value, next_action, delivery, None, risk_level, urgent,
        pending_confirmation, None, json.dumps(evidence, ensure_ascii=False), timestamp,
    )
    if existing:
        conn.execute(
            """UPDATE tasks SET title=?,recommended_action=?,target=?,status=?,owner_user_id=?,
               responsibility_status=?,waiting_on=?,promised_reply_at=?,next_action_at=?,
               business_deadline=?,last_contact_at=?,risk_level=?,urgent=?,pending_confirmation=?,
               source_message_id=?,evidence_json=?,updated_at=? WHERE task_id=?""",
            (*params, task_id),
        )
    else:
        conn.execute(
            """INSERT INTO tasks(task_id,related_order_id,title,recommended_action,target,status,
               owner_user_id,responsibility_status,waiting_on,promised_reply_at,next_action_at,
               business_deadline,last_contact_at,risk_level,urgent,pending_confirmation,
               source_message_id,evidence_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, order_id, *params[:-1], timestamp, timestamp),
        )
    return task_id


@app.get("/api/activation/summary")
def activation_summary(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    with db() as conn:
        return activation_snapshot(conn, identity.user_id)


@app.post("/api/orders/{order_id}/initialize")
def initialize_order(order_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    current_node = str(body.get("current_node") or "").strip()
    contact_status = str(body.get("contact_status") or "UNKNOWN").upper()
    issue_status = str(body.get("issue_status") or "UNKNOWN").upper()
    waiting_on = str(body.get("waiting_on") or "").strip().lower() or None
    promised_reply_at = body.get("promised_reply_at")
    note = str(body.get("initialization_note") or "").strip() or None
    operator_id = identity.user_id
    if contact_status not in CONTACT_STATUS_VALUES:
        raise HTTPException(422, "contact_status无效")
    if issue_status not in ISSUE_STATUS_VALUES:
        raise HTTPException(422, "issue_status无效")
    if waiting_on not in {None, "customer", "factory"}:
        raise HTTPException(422, "waiting_on只能是customer或factory")
    if contact_status == "WAITING_REPLY":
        if not waiting_on:
            raise HTTPException(422, "等待回复时必须选择等待对象")
        if not parse_dt(promised_reply_at):
            raise HTTPException(422, "等待回复时必须填写有效的承诺回复时间")
    timestamp = iso()
    with db() as conn:
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not order_row:
            raise HTTPException(404, "订单不存在")
        order = dict(order_row)
        # CRITICAL: Check organization access before any modification
        require_order_access(identity, order, conn)
        closed = current_node in {"已完成", "已出货", "已取消"} or str(body.get("order_status") or "").upper() in {"DONE", "COMPLETED", "CANCELLED", "CLOSED"}
        if closed:
            status = "CANCELLED" if current_node == "已取消" else "COMPLETED"
            conn.execute(
                """UPDATE orders SET current_node=?,status=?,action_readiness='CLOSED',contact_status=?,
                   issue_status=?,initialization_waiting_on=NULL,initialization_promised_reply_at=NULL,
                   initialization_note=?,initialization_source='QUICK_INITIALIZATION',initialized_at=?,
                   last_dynamic_update_at=?,updated_at=? WHERE order_id=?""",
                (current_node or "已完成", status, contact_status, issue_status, note, timestamp, timestamp, timestamp, order_id),
            )
            conn.execute("UPDATE tasks SET status='DONE',updated_at=? WHERE related_order_id=? AND status!='DONE'", (timestamp, order_id))
            task_id = None
            readiness = "CLOSED"
        else:
            conn.execute(
                """UPDATE orders SET current_node=COALESCE(NULLIF(?,''),current_node),contact_status=?,issue_status=?,
                   initialization_waiting_on=?,initialization_promised_reply_at=?,initialization_note=?,
                   initialization_source='QUICK_INITIALIZATION',initialized_at=COALESCE(initialized_at,?),
                   last_dynamic_update_at=?,updated_at=? WHERE order_id=?""",
                (current_node, contact_status, issue_status, waiting_on, promised_reply_at, note, timestamp, timestamp, timestamp, order_id),
            )
            order.update({"current_node": current_node or order.get("current_node")})
            task_id = upsert_activation_task(
                conn, order, contact_status=contact_status, issue_status=issue_status,
                waiting_on=waiting_on, promised_reply_at=promised_reply_at,
                note=note, operator_id=operator_id,
            )
            readiness = "ACTION_GENERATED"
            conn.execute(
                "UPDATE orders SET action_readiness=?,last_dynamic_update_at=?,updated_at=? WHERE order_id=?",
                (readiness, timestamp, timestamp, order_id),
            )
        conn.execute(
            "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (new_id("EVT"), "order", order_id, "ORDER_STATUS_INITIALIZED", json.dumps({
                "current_node": current_node, "contact_status": contact_status,
                "issue_status": issue_status, "waiting_on": waiting_on,
                "promised_reply_at": promised_reply_at, "task_id": task_id,
                "readiness": readiness,
            }, ensure_ascii=False), operator_id, timestamp),
        )
        conn.commit()
        snapshot = activation_snapshot(conn, operator_id)
    return {
        "status": "initialized", "order_id": order_id, "task_id": task_id,
        "action_readiness": readiness, "activation": snapshot,
    }


@app.get("/api/operators")
def operator_list(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    return {"items": OPERATORS}


@app.get("/api/action-workspace")
def action_workspace_list(
    include_closed: bool = Query(False),
    identity: CurrentIdentity = Depends(get_current_identity),
) -> dict[str, Any]:
    """D11 canonical workspace list. Never reads the legacy tasks table."""
    with db() as conn:
        return list_action_workspaces(conn, identity, include_closed=include_closed)


@app.get("/api/action-workspace/{action_case_id}")
def action_workspace_detail(
    action_case_id: str,
    identity: CurrentIdentity = Depends(get_current_identity),
) -> dict[str, Any]:
    with db() as conn:
        item = build_case_workspace(conn, identity, action_case_id)
        if not item:
            raise HTTPException(404, "行动案例不存在或无权访问")
        return {"item": item}


@app.post("/api/action-workspace/{action_case_id}/tasks")
def action_workspace_create_task(
    action_case_id: str,
    payload: AnyPayload,
    identity: CurrentIdentity = Depends(get_current_identity),
) -> dict[str, Any]:
    body = payload.model_dump()
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "缺少任务标题")
    try:
        with db() as conn:
            task = create_case_task(
                conn, identity, action_case_id=action_case_id, title=title,
                recommended_action=(str(body.get("recommended_action") or "").strip() or None),
            )
            conn.commit()
            item = build_case_workspace(conn, identity, action_case_id)
        return {"task": task, "item": item}
    except D9NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except D9StateError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/action-workspace/tasks/{task_id}/start")
def action_workspace_start_task(
    task_id: str,
    identity: CurrentIdentity = Depends(get_current_identity),
) -> dict[str, Any]:
    try:
        with db() as conn:
            task = start_case_task(conn, identity, task_id)
            conn.commit()
            item = build_case_workspace(conn, identity, task["action_case_id"])
        return {"task": task, "item": item}
    except D9NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except D9StateError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/action-workspace/tasks/{task_id}/complete")
def action_workspace_complete_task(
    task_id: str,
    identity: CurrentIdentity = Depends(get_current_identity),
) -> dict[str, Any]:
    try:
        with db() as conn:
            task = complete_case_task(conn, identity, task_id)
            conn.commit()
            item = build_case_workspace(conn, identity, task["action_case_id"])
        return {"task": task, "item": item}
    except D9NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except D9StateError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/action-workspace/tasks/{task_id}/wait")
def action_workspace_wait_task(
    task_id: str,
    payload: AnyPayload,
    identity: CurrentIdentity = Depends(get_current_identity),
) -> dict[str, Any]:
    body = payload.model_dump()
    waiting_type = str(body.get("waiting_type") or "EXTERNAL_REPLY").strip()
    due_at = str(body.get("due_at") or "").strip()
    if not due_at:
        raise HTTPException(422, "缺少等待截止时间 due_at")
    try:
        with db() as conn:
            waiting = wait_case_task(
                conn, identity, task_id=task_id, waiting_type=waiting_type,
                due_at=due_at, reason=(str(body.get("reason") or "").strip() or None),
            )
            conn.commit()
            item = build_case_workspace(conn, identity, waiting["action_case_id"])
        return {"waiting": waiting, "item": item}
    except D9NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (D9StateError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post("/api/action-workspace/waitings/{waiting_id}/reply")
def action_workspace_waiting_reply(
    waiting_id: str,
    payload: AnyPayload,
    identity: CurrentIdentity = Depends(get_current_identity),
) -> dict[str, Any]:
    body = payload.model_dump()
    try:
        with db() as conn:
            waiting = record_case_waiting_reply(
                conn, identity, waiting_id=waiting_id,
                reply_id=(str(body.get("reply_id") or "").strip() or None),
                reply_payload=body.get("reply_payload"),
                satisfies_completion=bool(body.get("satisfies_completion", False)),
            )
            conn.commit()
            item = build_case_workspace(conn, identity, waiting["action_case_id"])
        return {"waiting": waiting, "item": item}
    except D9NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except D9StateError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/tasks/{task_id}")
def task_detail(task_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        task_dict = dict(row)
        order_id = task_dict.get("related_order_id")
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone() if order_id else None
        order_dict = dict(order_row) if order_row else None
        require_task_access(identity, task_dict, order_dict)
        events = [dict(r) for r in conn.execute("SELECT * FROM event_logs WHERE entity_id=? ORDER BY created_at DESC LIMIT 40", (task_id,)).fetchall()]
        messages = [dict(r) for r in conn.execute("SELECT * FROM source_messages WHERE order_id=? ORDER BY created_at DESC LIMIT 20", (order_id,)).fetchall()] if order_id else []
    return {"task": task_dict, "events": events, "messages": messages}


@app.get("/api/orders")
def order_list(
    q: str | None = Query(None),
    status: str | None = Query(None),
    identity: CurrentIdentity = Depends(get_current_identity),
) -> dict[str, Any]:
    with db() as conn:
        orders = [dict(r) for r in conn.execute("SELECT * FROM orders ORDER BY updated_at DESC").fetchall()]
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()]
        risks = [dict(r) for r in conn.execute("SELECT * FROM risk_signals WHERE status='OPEN'").fetchall()]
    
    # ALWAYS filter by organization - no bypass for managers
    scoped_orders = []
    for order in orders:
        order_org = str(order.get("organization_id") or "").strip()
        if order_org:
            if identity.same_org(order_org):
                scoped_orders.append(order)
        else:
            # NULL org data: only the direct owner can access it
            # Managers CANNOT see NULL-org data from other users
            if owner_matches(order.get("owner"), identity.user_id):
                scoped_orders.append(order)
    
    # Role-based additional filtering for non-managers
    if not identity.is_manager():
        scoped_orders = [order for order in scoped_orders if owner_matches(order.get("owner"), identity.user_id)]
    
    result = []
    for order in scoped_orders:
        otasks = [t for t in tasks if t.get("related_order_id") == order["order_id"] and t.get("status") != "DONE"]
        orisks = [r for r in risks if r.get("order_id") == order["order_id"]]
        max_risk = max((r.get("risk_level") or "none" for r in orisks), key=lambda x: RISK_ORDER.get(x, 0), default="none")
        item = dict(order)
        readiness = item.get("action_readiness") or "BASE_ONLY"
        item.update({
            "action_readiness": readiness,
            "open_task_count": len(otasks),
            "waiting_task_count": sum(bool(t.get("waiting_on")) for t in otasks),
            "risk_count": len(orisks),
            "max_risk": max_risk,
            "next_action_at": min((t.get("next_action_at") or t.get("business_deadline") for t in otasks if t.get("next_action_at") or t.get("business_deadline")), default=None),
        })
        result.append(item)
    if q:
        needle = q.lower()
        result = [x for x in result if needle in " ".join(str(x.get(k) or "") for k in ["order_no","customer_name","product_name","current_node"]).lower()]
    if status and status != "ALL":
        result = [x for x in result if x.get("status") == status]
    return {"items": result, "total": len(result), "summary": {
        "active": sum(x["status"] == "ACTIVE" for x in result),
        "risk_orders": sum(x["max_risk"] in {"high","critical"} for x in result),
        "pending_tasks": sum(x["open_task_count"] for x in result),
        "commitments": sum(bool(x.get("latest_supplier_commitment")) for x in result),
        "base_only": sum(x.get("action_readiness") in {"BASE_ONLY", "NEEDS_STATUS"} for x in result),
        "ranking_ready": sum(x.get("action_readiness") in {"READY_FOR_RANKING", "ACTION_GENERATED"} for x in result),
        "action_generated": sum(x.get("action_readiness") == "ACTION_GENERATED" for x in result),
    }}


@app.post("/api/orders/{order_id}/tasks")
def create_order_task(order_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    title = str(body.get("title") or "").strip()
    if not title:
        raise HTTPException(422, "缺少任务标题")
    with db() as conn:
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not order_row:
            raise HTTPException(404, "订单不存在")
        order_dict = dict(order_row)
        require_order_access(identity, order_dict, conn)
        task_id, timestamp = new_id("TASK"), iso()
        operator_id = identity.user_id
        requested_owner = normalize_owner_value(body.get("owner_user_id"), fallback=operator_id) or operator_id
        effective_owner = requested_owner if identity.is_manager() else operator_id
        # Force organization_id from identity - NEVER from client input
        effective_org = identity.organization_id
        conn.execute(
            """INSERT INTO tasks(task_id,related_order_id,title,recommended_action,target,status,owner_user_id,responsibility_status,waiting_on,promised_reply_at,next_action_at,business_deadline,last_contact_at,risk_level,urgent,pending_confirmation,source_message_id,evidence_json,created_at,updated_at,organization_id)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, order_id, title, body.get("recommended_action") or title, body.get("target") or "factory", "OPEN", effective_owner, "assigned", None, None, body.get("next_action_at"), body.get("business_deadline"), None, body.get("risk_level") or "medium", int(bool(body.get("urgent"))), 0, None, json.dumps(body.get("evidence") or [], ensure_ascii=False), timestamp, timestamp, effective_org),
        )
        conn.execute("INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)", (new_id("EVT"), "order", order_id, "TASK_CREATED_FROM_UI", json.dumps({"task_id": task_id, **body}, ensure_ascii=False), operator_id, timestamp))
        audit_log(conn, identity, "TASK_CREATED", "task", task_id, "SUCCESS", body)
        conn.execute(
            """UPDATE orders SET action_readiness='ACTION_GENERATED',
               initialization_source=COALESCE(initialization_source,'MANUAL_TASK'),
               initialized_at=COALESCE(initialized_at,?),last_dynamic_update_at=?,updated_at=? WHERE order_id=?""",
            (timestamp, timestamp, timestamp, order_id),
        )
        conn.commit()
    return {"status": "created", "task_id": task_id, "order_id": order_id}


def analyze_intake_body(body: dict[str, Any], org_id: str | None = None) -> dict[str, Any]:
    raw = str(body.get("raw_content") or "").strip()
    if not raw:
        raise HTTPException(422, "消息内容不能为空")
    sender_role = str(body.get("sender_role") or "customer")
    source_channel = str(body.get("source_channel") or "email")
    source_time = str(body.get("source_time") or iso())
    order, task = order_and_task_context(body.get("order_id"), raw, org_id=org_id)
    order_id = order.get("order_id") if order else None
    
    # Determine org_id for persistence - from caller or from matched order
    persist_org_id = org_id
    if not persist_org_id and order:
        persist_org_id = order.get("organization_id")
    if not persist_org_id:
        persist_org_id = "ORG-DEMO"

    # ── D11 UAT Fixture Provider (TEST/UAT ONLY) ──────────────────────
    # When D11_UAT_INTAKE_PROVIDER=fixture, use deterministic fixtures
    # instead of Coze or local fallback. Never active in production.
    uat_provider = os.getenv("D11_UAT_INTAKE_PROVIDER", "").strip().lower()
    if uat_provider == "fixture":
        candidate = uat_fixture_candidate(raw, sender_role, source_time, order)
        workflow_source = "UAT_FIXTURE_PROVIDER"
        debug_url = None
    else:
        configured = coze_status().get("ready", False)
        workflow_key = "ft02" if sender_role == "factory" else "ft01"
        workflow_source = f"COZE_{workflow_key.upper()}"
        debug_url = None
        if configured:
            try:
                if workflow_key == "ft02":
                    run = run_workflow("ft02", build_ft02_parameters(body, order, task), record=record_coze_run)
                    candidate = normalize_ft02(run.result, order=order, task=task, run=run)
                else:
                    run = run_workflow("ft01", build_ft01_parameters(body, order, task), record=record_coze_run)
                    candidate = normalize_ft01(run.result, order=order, run=run)
                debug_url = run.debug_url
            except CozeWorkflowError as exc:
                allow = os.getenv("COZE_ALLOW_LOCAL_FALLBACK_ON_ERROR", "false").lower() == "true"
                if not allow:
                    raise coze_http_error(exc)
                candidate = local_candidate(raw, sender_role, source_time, order)
                candidate["_integration"] = {"workflow_key": workflow_key, "fallback_reason": str(exc), "debug_url": exc.debug_url}
                workflow_source = "LOCAL_FALLBACK_AFTER_COZE_ERROR"
        else:
            allow = os.getenv("COZE_ALLOW_LOCAL_WHEN_UNCONFIGURED", "false").lower() == "true"
            if not allow:
                raise HTTPException(503, "Coze尚未配置：请在Render添加COZE_API_TOKEN")
            candidate = local_candidate(raw, sender_role, source_time, order)
            candidate["_integration"] = {"workflow_key": workflow_key, "fallback_reason": "COZE_NOT_CONFIGURED"}
            workflow_source = "LOCAL_FALLBACK_NO_TOKEN"
    message_id, review_id, timestamp = new_id("MSG"), new_id("REV"), iso()
    with db() as conn:
        conn.execute(
            "INSERT INTO source_messages(message_id,order_id,organization_id,source_channel,sender_role,message_type,raw_content,source_time,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (message_id, order_id, persist_org_id, source_channel, sender_role, candidate.get("message_type"), raw, source_time, timestamp),
        )
        conn.execute(
            """INSERT INTO candidate_reviews(review_id,source_message_id,order_id,organization_id,workflow_source,
               candidate_json,status,created_at) VALUES(?,?,?,?,?,?,?,?)""",
            (review_id, message_id, order_id, persist_org_id, workflow_source, json.dumps(candidate, ensure_ascii=False), "PENDING", timestamp),
        )
        conn.commit()
    return {
        "status": "analyzed", "review_id": review_id, "message_id": message_id,
        "candidate": candidate, "workflow_source": workflow_source, "debug_url": debug_url,
        "boundary": "已配置令牌时由Coze FT01/FT02实时识别；所有结果仍需人工确认后才能调用FT03写回。",
    }


@app.post("/api/intake/analyze")
def analyze_intake(payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    """Compatibility endpoint: waits for the workflow to finish."""
    return analyze_intake_body(payload.model_dump(), org_id=identity.organization_id)


def process_intake_job(job_id: str) -> None:
    started_at = iso()
    with db() as conn:
        row = conn.execute("SELECT request_json, organization_id FROM intake_jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            return
        body = json.loads(row["request_json"])
        job_org_id = row["organization_id"]
        conn.execute(
            "UPDATE intake_jobs SET status='PROCESSING', progress_message=?, started_at=?, updated_at=? WHERE job_id=?",
            ("正在调用Coze工作流并提取候选", started_at, started_at, job_id),
        )
        conn.commit()
    try:
        result = analyze_intake_body(body, org_id=job_org_id)
        completed_at = iso()
        with db() as conn:
            conn.execute(
                """UPDATE intake_jobs SET status='COMPLETED', progress_message=?, result_json=?,
                   review_id=?, message_id=?, completed_at=?, updated_at=? WHERE job_id=?""",
                (
                    "识别完成，等待人工确认",
                    json.dumps(result, ensure_ascii=False),
                    result.get("review_id"), result.get("message_id"),
                    completed_at, completed_at, job_id,
                ),
            )
            conn.commit()
    except HTTPException as exc:
        failed_at = iso()
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
        with db() as conn:
            conn.execute(
                "UPDATE intake_jobs SET status='FAILED', progress_message=?, error_json=?, completed_at=?, updated_at=? WHERE job_id=?",
                ("识别失败", json.dumps(detail, ensure_ascii=False), failed_at, failed_at, job_id),
            )
            conn.commit()
    except Exception as exc:  # keep background failures visible to the user
        failed_at = iso()
        with db() as conn:
            conn.execute(
                "UPDATE intake_jobs SET status='FAILED', progress_message=?, error_json=?, completed_at=?, updated_at=? WHERE job_id=?",
                ("识别失败", json.dumps({"message": str(exc)}, ensure_ascii=False), failed_at, failed_at, job_id),
            )
            conn.commit()


@app.post("/api/intake/jobs", status_code=202)
def create_intake_job(payload: AnyPayload, background_tasks: BackgroundTasks, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    raw = str(body.get("raw_content") or "").strip()
    if not raw:
        raise HTTPException(422, "消息内容不能为空")
    sender_role = str(body.get("sender_role") or "customer").strip().lower()
    workflow_key = "ft02" if sender_role == "factory" else "ft01"
    job_id = new_id("INTAKE")
    timestamp = iso()
    raw_order_id = body.get("order_id")
    order_id = str(raw_order_id).strip() if raw_order_id else None
    # P0-1: Bind organization_id from authenticated identity, NEVER from client payload
    org_id = identity.organization_id
    with db() as conn:
        conn.execute(
            """INSERT INTO intake_jobs(job_id,organization_id,status,workflow_key,order_id,request_json,progress_message,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (job_id, org_id, "QUEUED", workflow_key, order_id, json.dumps(body, ensure_ascii=False),
             f"已进入后台队列，即将调用{workflow_key.upper()}", timestamp, timestamp),
        )
        conn.commit()
    if FLOWORDER_SERVERLESS_MODE:
        # HTTP function instances may freeze/recycle immediately after the response.
        # Keep the user-visible job contract, but finish the work inside this request
        # so no business job depends on an in-process background task surviving.
        process_intake_job(job_id)
    else:
        background_tasks.add_task(process_intake_job, job_id)
    return {
        "status": "queued", "job_id": job_id, "workflow_key": workflow_key,
        "message": (
            "消息已完成后台识别，可查看结果。"
            if FLOWORDER_SERVERLESS_MODE
            else "消息已进入后台识别，可继续浏览其他页面。"
        ),
    }


@app.get("/api/intake/jobs/{job_id}")
def get_intake_job(job_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    with db() as conn:
        # P0-3: Enforce organization isolation for intake jobs
        row = conn.execute(
            "SELECT * FROM intake_jobs WHERE job_id=? AND organization_id=?",
            (job_id, identity.organization_id)
        ).fetchone()
    if not row:
        raise HTTPException(404, "识别任务不存在")
    item = dict(row)
    result = json.loads(item.pop("result_json") or "null")
    error = json.loads(item.pop("error_json") or "null")
    item.pop("request_json", None)
    return {**item, "result": result, "error": error}


@app.post("/api/reviews/import")
def import_review(payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    raw_result = body.get("result_json") or body.get("candidate")
    if isinstance(raw_result, str):
        try: candidate = json.loads(raw_result)
        except json.JSONDecodeError as exc: raise HTTPException(422, f"result_json不是合法JSON：{exc}")
    elif isinstance(raw_result, dict): candidate = raw_result
    else: raise HTTPException(422, "缺少result_json")
    order_id = body.get("order_id") or (candidate.get("order_match") or {}).get("selected_order_id")
    
    with db() as conn:
        # P0-H: Validate source_message_id belongs to current organization
        source_message_id = body.get("source_message_id")
        if source_message_id:
            msg_row = conn.execute(
                "SELECT organization_id FROM source_messages WHERE message_id=?",
                (source_message_id,)
            ).fetchone()
            if not msg_row:
                raise HTTPException(404, "源消息不存在")
            if msg_row["organization_id"] != identity.organization_id:
                raise HTTPException(404, "源消息不存在")
        
        # P0-H: Validate order belongs to current organization (if order_id provided)
        if order_id:
            order_row = conn.execute("SELECT organization_id FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if order_row and order_row["organization_id"]:
                require_same_org(identity, order_row["organization_id"])
        
        # P0-H: Cross-validate Review / SourceMessage / Order org consistency
        org_id = identity.organization_id
        if source_message_id:
            msg_org = conn.execute(
                "SELECT organization_id FROM source_messages WHERE message_id=?",
                (source_message_id,)
            ).fetchone()
            if msg_org and msg_org["organization_id"] != org_id:
                raise HTTPException(404, "源消息不存在")
    
    with db() as conn:
        review_id, timestamp = new_id("REV"), iso()
        # P0-4: Use organization_id from identity, not from client payload
        org_id = identity.organization_id
        conn.execute(
            """INSERT INTO candidate_reviews(review_id,source_message_id,order_id,organization_id,workflow_source,candidate_json,status,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (review_id, body.get("source_message_id"), order_id, org_id, body.get("workflow_source") or "COZE_IMPORT", json.dumps(candidate, ensure_ascii=False), "PENDING", timestamp)
        )
        conn.commit()
    return {"status": "imported", "review_id": review_id}


@app.get("/api/reviews")
def review_list(status: str | None = Query(None), identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    with db() as conn:
        # P0-4: Filter by candidate_reviews.organization_id directly, not by order org
        # P0-I: Add m.organization_id = r.organization_id defense-in-depth on JOIN
        sql = "SELECT r.*, m.raw_content, m.sender_role, m.source_channel, o.order_no, o.customer_name, o.organization_id as order_org FROM candidate_reviews r LEFT JOIN source_messages m ON m.message_id=r.source_message_id AND m.organization_id=r.organization_id LEFT JOIN orders o ON o.order_id=r.order_id"
        params: list[Any] = []
        conditions = []
        if status and status != "ALL":
            conditions.append("r.status=?")
            params.append(status)
        # P0-4: Strict organization isolation - only show reviews from current user's org
        conditions.append("r.organization_id=?")
        params.append(identity.organization_id)
        sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY CASE r.status WHEN 'PENDING' THEN 0 ELSE 1 END, r.created_at DESC"
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    for row in rows: row["candidate"] = json.loads(row.pop("candidate_json"))
    return {"items": rows, "total": len(rows), "pending": sum(x["status"] == "PENDING" for x in rows)}


@app.get("/api/reviews/{review_id}")
def review_detail(review_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    with db() as conn:
        # P0-4: Enforce organization isolation on candidate_reviews
        # P0-I: Add m.organization_id = r.organization_id defense-in-depth on JOIN
        row = conn.execute(
            "SELECT r.*, m.raw_content, m.sender_role, m.source_channel, o.order_no, o.customer_name FROM candidate_reviews r LEFT JOIN source_messages m ON m.message_id=r.source_message_id AND m.organization_id=r.organization_id LEFT JOIN orders o ON o.order_id=r.order_id WHERE r.review_id=? AND r.organization_id=?",
            (review_id, identity.organization_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "候选记录不存在")
        # Additional check: if linked order, verify order org matches review org
        order_id = row["order_id"]
        if order_id:
            order_row = conn.execute("SELECT organization_id FROM orders WHERE order_id=?", (order_id,)).fetchone()
            if order_row and order_row["organization_id"]:
                require_same_org(identity, order_row["organization_id"])
    result = dict(row); result["candidate"] = json.loads(result.pop("candidate_json")); return result


@app.post("/api/reviews/{review_id}/confirm")
def confirm_review(review_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    operator_id = identity.user_id
    with db() as conn:
        # P0-4: Also check organization_id on candidate_reviews itself
        row = conn.execute(
            "SELECT * FROM candidate_reviews WHERE review_id=? AND organization_id=?",
            (review_id, identity.organization_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "候选记录不存在")
        review = dict(row)
        if review["status"] == "CONFIRMED":
            return {"status": "DUPLICATE_SKIPPED", "review_id": review_id, "order_id": review.get("order_id")}
        candidate = body.get("candidate") or json.loads(review["candidate_json"])
        if review.get("status") == "REJECTED":
            raise HTTPException(409, detail={"code": "REVIEW_REJECTED", "message": "该候选已被拒绝"})
        if review.get("status") == "APPROVAL_PENDING" and not identity.is_manager():
            raise HTTPException(403, detail={"code": "MANAGER_REVIEW_REQUIRED", "message": "该事项已进入主管审批"})
        if candidate_requires_manager(candidate) and not identity.is_manager():
            raise HTTPException(409, detail={"code": "MANAGER_REVIEW_REQUIRED", "message": "高风险或正式业务事实变更需要主管审批"})
        order_id = review.get("order_id") or (candidate.get("order_match") or {}).get("selected_order_id")
        if not order_id:
            raise HTTPException(422, "候选未唯一关联订单，请先选择订单")
        # ENFORCE: Organization boundary for the order
        order_row = conn.execute("SELECT organization_id FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if order_row and order_row["organization_id"]:
            require_same_org(identity, order_row["organization_id"])
        order = rowdict(conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone())
        task_id = (candidate.get("_integration") or {}).get("task_id")
        task = rowdict(conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()) if task_id else None
        keys = [r["idempotency_key"] for r in conn.execute("SELECT idempotency_key FROM idempotency_records").fetchall()]
    if not coze_status().get("ready"):
        allow_local = os.getenv("COZE_ALLOW_LOCAL_CONFIRM_WHEN_UNCONFIGURED", "false").lower() == "true"
        if not allow_local:
            raise HTTPException(503, "Coze尚未配置，无法从网页调用FT03；请先在Render设置COZE_API_TOKEN")
        plan, _ = review_to_transaction(review, candidate, operator_id)
        with db() as conn:
            begin_transaction(conn)
            local_result = apply_writeback(conn, {"order_id": order_id, "operator_id": operator_id}, plan)
            task_id = create_or_update_action_task(conn, review, candidate, order_id)
            timestamp = iso()
            conn.execute(
                "UPDATE candidate_reviews SET candidate_json=?,status='CONFIRMED',reviewer_id=?,reviewed_at=? WHERE review_id=?",
                (json.dumps(candidate, ensure_ascii=False), operator_id, timestamp, review_id),
            )
            conn.execute(
                """UPDATE orders SET action_readiness=?,initialization_source='MESSAGE_CONFIRMATION',
                   initialized_at=COALESCE(initialized_at,?),last_dynamic_update_at=?,updated_at=? WHERE order_id=?""",
                ("ACTION_GENERATED" if task_id else "READY_FOR_RANKING", timestamp, timestamp, timestamp, order_id),
            )
            conn.commit()
        return {
            "status": "CONFIRMED", "review_id": review_id, "order_id": order_id, "task_id": task_id,
            "writeback": local_result, "integration_mode": "LOCAL_FALLBACK_NO_TOKEN",
            "boundary": "未配置Coze令牌时仅用于本地测试；生产部署配置令牌后会真实调用FT03。",
        }
    confirmed = confirmed_payload(candidate)
    source_document_id = review.get("source_message_id") or f"MSG-{review_id}"
    extraction_run_id = (candidate.get("_integration") or {}).get("workflow_run_id") or f"RUN-{review_id}"
    params = {
        "extraction_run_id": extraction_run_id,
        "operation_time": iso(),
        "adapter_configured": "YES",
        "confirmed_payload_json": json.dumps(confirmed, ensure_ascii=False),
        "existing_task_state_json": json.dumps(task or {}, ensure_ascii=False),
        "operator_id": operator_id,
        "source_document_id": source_document_id,
        "existing_idempotency_keys_json": json.dumps(keys, ensure_ascii=False),
        "confirmation_version": str(body.get("confirmation_version") or "1"),
        "existing_business_state_json": json.dumps(order or {}, ensure_ascii=False),
    }
    try:
        run = run_workflow("ft03", params, record=record_coze_run)
    except CozeWorkflowError as exc:
        raise coze_http_error(exc)
    ft03 = run.result
    persistence = str(ft03.get("persistence_status") or "")
    if persistence not in {"committed", "duplicate_skipped"}:
        raise HTTPException(502, detail={
            "message": "FT03未确认写回成功",
            "result": ft03,
            "debug_url": run.debug_url,
        })
    task_id = None
    with db() as conn:
        begin_transaction(conn)
        task_id = create_or_update_action_task(conn, review, candidate, order_id)
        timestamp = iso()
        conn.execute(
            "UPDATE candidate_reviews SET candidate_json=?,status='CONFIRMED',reviewer_id=?,reviewed_at=? WHERE review_id=?",
            (json.dumps(candidate, ensure_ascii=False), operator_id, timestamp, review_id),
        )
        if review.get("source_message_id"):
            conn.execute("UPDATE source_messages SET order_id=? WHERE message_id=?", (order_id, review["source_message_id"]))
        conn.execute(
            "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (new_id("EVT"), "order", order_id, "COZE_FT03_CONFIRMED_FROM_UI",
             json.dumps({"review_id": review_id, "task_id": task_id, "workflow_run_id": run.run_id}, ensure_ascii=False),
             operator_id, timestamp),
        )
        conn.execute(
            """UPDATE orders SET action_readiness=?,initialization_source='MESSAGE_CONFIRMATION',
               initialized_at=COALESCE(initialized_at,?),last_dynamic_update_at=?,updated_at=? WHERE order_id=?""",
            ("ACTION_GENERATED" if task_id else "READY_FOR_RANKING", timestamp, timestamp, timestamp, order_id),
        )
        conn.commit()
    ranking = run_ft04_refresh("USER-1")
    return {
        "status": "CONFIRMED", "review_id": review_id, "order_id": order_id, "task_id": task_id,
        "ft03": ft03, "ft03_debug_url": run.debug_url,
        "ft04": ranking["result"] if ranking else None,
        "boundary": "网页已真实调用Coze FT03；只有FT03返回committed或duplicate_skipped后才更新确认状态并触发FT04重排。",
    }


@app.post("/api/reviews/{review_id}/reject")
def reject_review(review_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    operator_id = identity.user_id
    with db() as conn:
        # P0-G: Enforce organization isolation - must be same org to reject
        row = conn.execute(
            "SELECT * FROM candidate_reviews WHERE review_id=? AND organization_id=?",
            (review_id, identity.organization_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "候选记录不存在")
        review = dict(row)
        # P0-G removed: no need to check order org separately since we already verified review org
        changed = conn.execute(
            "UPDATE candidate_reviews SET status='REJECTED', reviewer_id=?, reviewed_at=? WHERE review_id=? AND organization_id=?",
            (operator_id, iso(), review_id, identity.organization_id)
        ).rowcount
        if not changed: raise HTTPException(404, "候选记录不存在")
        conn.commit()
    return {"status": "REJECTED", "review_id": review_id}


@app.post("/api/drafts/{draft_id}/review")
def review_draft_locally(draft_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    """Persist a human draft decision locally without another model call."""
    body = payload.model_dump()
    action = str(body.get("action") or "").strip().lower()
    if action not in {"approve", "reject", "save_edit", "copy_and_record"}:
        raise HTTPException(400, "不支持的草稿操作")
    operator_id = identity.user_id
    timestamp = iso()
    with db() as conn:
        if not table_exists(conn, "communication_drafts"):
            raise HTTPException(404, "草稿记录表不存在，请重新生成草稿")
        row = conn.execute("SELECT * FROM communication_drafts WHERE draft_id=?", (draft_id,)).fetchone()
        if not row:
            raise HTTPException(404, "草稿不存在或已过期，请重新生成")
        record = dict(row)
        # Drafts are always order-scoped. Re-check the authenticated user's order
        # access before revealing or mutating a draft so a leaked draft_id cannot
        # cross the organization boundary.
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (record.get("order_id"),)).fetchone()
        if not order_row:
            raise HTTPException(404, "草稿不存在或已过期，请重新生成")
        try:
            require_order_access(identity, dict(order_row), conn)
        except HTTPException as exc:
            raise HTTPException(404, "草稿不存在或已过期，请重新生成") from exc
        subject = body.get("edited_subject") if body.get("edited_subject") is not None else record.get("edited_subject") or record.get("ai_subject") or ""
        draft = body.get("edited_draft") if body.get("edited_draft") is not None else record.get("edited_draft") or record.get("ai_draft") or ""
        note = str(body.get("note") or "").strip()
        override = bool(body.get("risk_override_confirmed"))
        blocked = str(record.get("approval_status") or "").upper().startswith("BLOCKED")
        if action in {"approve", "copy_and_record"} and blocked:
            if not override:
                raise HTTPException(409, "该草稿被安全规则阻断；请修改草稿，或完成人工放行核对")
            if not note:
                raise HTTPException(422, "人工放行时必须填写核对依据")
        if action != "reject" and not str(draft).strip():
            raise HTTPException(422, "草稿正文不能为空")

        previous_status = str(record.get("human_status") or "PENDING")
        if action == "reject":
            human_status, event_type, message = "REJECTED", "DRAFT_REJECTED", "草稿已驳回"
            final_text = record.get("final_text")
            approved_at, copied_at = record.get("approved_at"), record.get("copied_at")
        elif action == "save_edit":
            human_status, event_type, message = "EDITED", "DRAFT_EDIT_SAVED", "修改已保存"
            final_text = draft
            approved_at, copied_at = record.get("approved_at"), record.get("copied_at")
        elif action == "approve":
            human_status, event_type, message = "APPROVED", "DRAFT_APPROVED", "草稿已人工确认，尚未发送"
            final_text = draft
            approved_at, copied_at = record.get("approved_at") or timestamp, record.get("copied_at")
        else:
            human_status, event_type, message = "COPIED_AND_RECORDED", "DRAFT_COPIED_AND_RECORDED", "已复制并记录联系状态"
            final_text = draft
            approved_at, copied_at = record.get("approved_at") or timestamp, record.get("copied_at") or timestamp

        duplicate = previous_status == human_status and action in {"approve", "copy_and_record"}
        conn.execute(
            """UPDATE communication_drafts SET edited_subject=?,edited_draft=?,final_text=?,human_status=?,
               reviewer_id=?,review_note=?,approved_at=?,copied_at=?,updated_at=? WHERE draft_id=?""",
            (subject, draft, final_text, human_status, operator_id, note, approved_at, copied_at, timestamp, draft_id),
        )
        task_update = None
        task_id = body.get("task_id")
        if action == "copy_and_record" and task_id and not duplicate:
            waiting_on = str(body.get("waiting_on") or "").strip() or None
            promised_reply_at = body.get("promised_reply_at")
            next_action_at = body.get("next_action_at") or promised_reply_at

            # D11 canonical tasks live in d9_action_case_tasks. A copied draft is
            # a real contact action, so TODO -> IN_PROGRESS -> WAITING must use the
            # frozen D9 FSM instead of the legacy tasks table.
            d9_row = None
            if table_exists(conn, "d9_action_case_tasks"):
                d9_row = conn.execute(
                    "SELECT * FROM d9_action_case_tasks WHERE task_id=?", (task_id,)
                ).fetchone()
            if d9_row:
                d9_task = dict(d9_row)
                if d9_task.get("organization_id") != identity.organization_id:
                    raise HTTPException(404, "关联任务不存在，尚未记录为已联系")
                if not next_action_at:
                    raise HTTPException(422, "请选择承诺回复时间，再记录为等待回复")
                try:
                    if d9_task.get("status") == "TODO":
                        start_case_task(conn, identity, task_id)
                    elif d9_task.get("status") not in {"IN_PROGRESS", "WAITING"}:
                        raise HTTPException(409, f"当前任务状态 {d9_task.get('status')} 不能记录等待")
                    waiting_type = "SUPPLIER_REPLY" if waiting_on in {"factory", "supplier"} else "CUSTOMER_REPLY"
                    waiting = wait_case_task(
                        conn, identity, task_id=task_id, waiting_type=waiting_type,
                        due_at=str(next_action_at), reason="沟通草稿已复制并记录人工联系",
                    )
                    task_update = {
                        "updated": True, "task_id": task_id,
                        "waiting_id": waiting.get("waiting_id"),
                        "waiting_on": waiting_on, "promised_reply_at": promised_reply_at,
                    }
                except D9NotFoundError as exc:
                    raise HTTPException(404, str(exc)) from exc
                except D9StateError as exc:
                    raise HTTPException(409, str(exc)) from exc
            else:
                # Backward-compatible legacy communication task path. It remains
                # tenant-scoped and is not used by the D11 action workspace.
                legacy = conn.execute(
                    "SELECT task_id FROM tasks WHERE task_id=? AND organization_id=?",
                    (task_id, identity.organization_id),
                ).fetchone()
                if not legacy:
                    raise HTTPException(404, "关联任务不存在，尚未记录为已联系")
                conn.execute(
                    """UPDATE tasks SET status='OPEN',waiting_on=?,promised_reply_at=?,next_action_at=?,
                       last_contact_at=?,updated_at=? WHERE task_id=? AND organization_id=?""",
                    (waiting_on, promised_reply_at, next_action_at, timestamp, timestamp, task_id, identity.organization_id),
                )
                task_update = {"updated": True, "task_id": task_id, "waiting_on": waiting_on, "promised_reply_at": promised_reply_at}
        if not duplicate:
            conn.execute(
                "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
                (new_id("EVT"), "communication_draft", draft_id, event_type,
                 json.dumps({"action": action, "task_id": task_id, "risk_override": override, "note": note}, ensure_ascii=False),
                 operator_id, timestamp),
            )
        conn.commit()
    return {"ok": True, "message": message, "human_status": human_status, "duplicate_skipped": duplicate, "task_update": task_update}


@app.get("/api/management")
def management_dashboard(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    require_manager(identity)
    data = build_dashboard(identity)
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
def get_settings(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    user_id = identity.user_id
    defaults = {"theme": "upstream", "compact": False, "show_demo": False, "current_user_id": user_id, "notifications": {"urgent": True, "waiting_overdue": True, "writeback": True, "daily_summary": False}}
    with db() as conn:
        row = conn.execute("SELECT settings_json,updated_at FROM user_settings WHERE user_id=?", (user_id,)).fetchone()
    return {"user_id": user_id, "settings": json.loads(row["settings_json"]) if row else defaults, "updated_at": row["updated_at"] if row else None}


@app.put("/api/settings")
def put_settings(payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    user_id = identity.user_id
    settings = payload.model_dump().get("settings") or payload.model_dump()
    timestamp = iso()
    with db() as conn:
        conn.execute("INSERT INTO user_settings(user_id,settings_json,updated_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET settings_json=excluded.settings_json,updated_at=excluded.updated_at", (user_id, json.dumps(settings, ensure_ascii=False), timestamp))
        conn.commit()
    return {"status": "saved", "user_id": user_id, "settings": settings, "updated_at": timestamp}


@app.get("/api/coze/status")
def api_coze_status(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    status = coze_status()
    with db() as conn:
        recent = [dict(r) for r in conn.execute(
            """SELECT run_id,workflow_key,status,coze_code,coze_msg,debug_url,duration_ms,created_at
               FROM workflow_runs ORDER BY created_at DESC LIMIT 12"""
        ).fetchall()]
    return {**status, "recent_runs": recent}


@app.post("/api/coze/ft04/refresh")
def api_ft04_refresh(payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    user_id = identity.user_id
    try:
        result = run_ft04_refresh(user_id, raise_on_error=True)
    except CozeWorkflowError as exc:
        raise coze_http_error(exc)
    return {"status": "refreshed", **(result or {})}


def _mark_interrupted_intake_jobs() -> None:
    """Close stale intake jobs after a restart without assuming a legacy schema."""
    timestamp = iso()
    with db() as conn:
        if not table_exists(conn, "intake_jobs"):
            return
        columns = {col["name"] for col in get_table_columns(conn, "intake_jobs")}
        required = {"status", "progress_message", "error_json", "completed_at", "updated_at"}
        if not required.issubset(columns):
            print(f"[startup-warning] intake_jobs legacy schema; skip stale-job cleanup: missing={sorted(required - columns)}")
            return
        conn.execute(
            """UPDATE intake_jobs SET status='FAILED', progress_message='服务重启，后台识别已中断',
               error_json=?, completed_at=?, updated_at=? WHERE status IN ('QUEUED','PROCESSING')""",
            (json.dumps({"message": "服务重启导致后台识别中断，请重新提交消息"}, ensure_ascii=False), timestamp, timestamp),
        )
        conn.commit()


def _initialize_database_worker() -> None:
    """Initialize the persistent database without blocking Railway liveness checks."""
    with _STARTUP_LOCK:
        if APP_STARTUP_STATE["database_ready"] or APP_STARTUP_STATE["database_initializing"]:
            return
        APP_STARTUP_STATE["database_initializing"] = True
    # Serverless startup must fail fast rather than consume a whole invocation on
    # retry sleeps. Persistent Railway keeps the existing bounded retry behavior.
    delays = (0,) if FLOWORDER_SERVERLESS_MODE else (0, 1, 2, 4, 8, 16)
    last_error: Exception | None = None
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            time.sleep(delay)
        try:
            init_db()
            if os.getenv("SEED_D19_DEMO_DATA", "false").lower() == "true":
                try:
                    from d19_demo_seed import seed_d19_demo
                    seed_result = seed_d19_demo()
                    print(f"[d19-demo-seed] {seed_result.to_dict()}")
                except Exception as seed_exc:
                    # Demo data must never make the core service unavailable.
                    # The seed is additive-only by default; surface failure in logs.
                    print(f"[d19-demo-seed-warning] {type(seed_exc).__name__}: {seed_exc}")
            _mark_interrupted_intake_jobs()
            APP_STARTUP_STATE.update({
                "database_ready": True,
                "database_initializing": False,
                "startup_error": None,
                "initialized_at": iso(),
            })
            status = storage_status()
            print(f"[startup] database ready on attempt={attempt}")
            print(f"[storage] db={status['db_path']} persistent={status['on_persistent_path']}")
            if status.get("warning"):
                print(f"[storage-warning] {status['warning']}")
            return
        except Exception as exc:  # Keep the process alive so /health exposes liveness.
            last_error = exc
            APP_STARTUP_STATE["startup_error"] = f"{type(exc).__name__}: {exc}"
            print(f"[startup-error] database init attempt={attempt} failed: {APP_STARTUP_STATE['startup_error']}")
            traceback.print_exc()
    APP_STARTUP_STATE["database_initializing"] = False
    if last_error:
        print("[startup-error] database initialization exhausted all retries")


@app.on_event("startup")
def startup() -> None:
    if FLOWORDER_SERVERLESS_MODE:
        # CloudBase may recycle an HTTP-function instance after any response; a
        # daemon startup thread is therefore not a safe readiness dependency.
        _initialize_database_worker()
    else:
        threading.Thread(target=_initialize_database_worker, name="floworder-db-init", daemon=True).start()


@app.get("/")
def home() -> FileResponse:
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    """Railway liveness endpoint: never runs migrations or external checks."""
    return {
        "status": "ok",
        "version": "6.1.4.1.3",
        "service": "floworder",
        "database_ready": bool(APP_STARTUP_STATE["database_ready"]),
        "database_initializing": bool(APP_STARTUP_STATE["database_initializing"]),
    }


@app.get("/ready")
def ready() -> dict[str, Any]:
    """Application readiness endpoint; returns 503 until the selected database backend is usable."""
    if not APP_STARTUP_STATE["database_ready"]:
        raise HTTPException(
            503,
            {
                "status": "starting" if APP_STARTUP_STATE["database_initializing"] else "degraded",
                "version": "6.1.4.1.3",
                "database_ready": False,
                "startup_error": APP_STARTUP_STATE["startup_error"],
            },
        )
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        APP_STARTUP_STATE["database_ready"] = False
        APP_STARTUP_STATE["startup_error"] = f"{type(exc).__name__}: {exc}"
        raise HTTPException(503, {"status": "degraded", "database_ready": False,
                                 "startup_error": APP_STARTUP_STATE["startup_error"]}) from exc
    return {"status": "ready", "version": "6.1.4.1.3", "database_ready": True,
            "initialized_at": APP_STARTUP_STATE["initialized_at"]}


@app.get("/api/system/storage")
def system_storage(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    require_manager(identity)
    init_db()
    status = storage_status()
    with db() as conn:
        status["order_count"] = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        status["task_count"] = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    status["status"] = "ok" if status["on_persistent_path"] or not (status["render_runtime"] or status.get("railway_runtime")) else "warning"
    return status


def clear_business_data(conn: Any) -> None:
    # Clear all user-created business and audit data, including optional modules.
    # Tables are discovered first so this remains compatible when a patch module
    # is not installed in a particular deployment.
    conn.execute("PRAGMA foreign_keys = OFF")
    tables = [
        "d12_human_reviews", "d15_execution_trace_events", "d15_outbox_execution_state", "d10_outbox_events", "d10_idempotency_records", "d10_audit_events", "d10_business_actions",
        "d9_trace_events", "d9_action_case_waitings", "d9_action_case_tasks", "action_cases",
        "communication_events", "communication_workflow_runs", "communication_drafts",
        "communication_task_candidates", "order_import_rows", "order_import_batches", "intake_jobs",
        "bulk_update_candidates", "bulk_update_batches", "analytics_events",
        "agent_chat_jobs", "agent_tool_calls", "approval_requests", "anomaly_candidates", "daily_inspection_reports", "agent_runs", "logistics_events", "order_dependencies",
        "task_rankings", "workflow_runs", "user_settings", "candidate_reviews",
        "idempotency_records", "event_logs", "confirmation_snapshots",
        "commitment_history", "risk_signals", "tasks", "source_messages", "order_lines", "orders"
    ]
    for table in tables:
        if table_exists(conn, table):
            conn.execute(f'DELETE FROM "{table}"')
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")


@app.post("/api/reset")
def reset(
    identity: CurrentIdentity = Depends(get_current_identity_optional),
    x_floworder_agent_key: str | None = Header(None),
) -> dict[str, Any]:
    """Reset business data. Requires manager token or agent API key."""
    agent_key = os.getenv("FLOWORDER_AGENT_API_KEY", "").strip() or agent_api.AGENT_API_KEY
    if identity is not None:
        require_manager(identity)
    elif x_floworder_agent_key and agent_key and hmac.compare_digest(x_floworder_agent_key, agent_key):
        pass
    else:
        raise HTTPException(401, "RESET_AUTH_REQUIRED")
    if os.getenv("ENABLE_DEMO_ADMIN_ACTIONS", "false").lower() != "true":
        raise HTTPException(403, "RESET_DEMO_ADMIN_ACTIONS_DISABLED")
    with db() as conn:
        clear_business_data(conn)
    return {"status": "cleared", "at": iso()}


@app.post("/api/demo/seed")
def seed_demo(
    identity: CurrentIdentity = Depends(get_current_identity_optional),
    x_floworder_agent_key: str | None = Header(None),
) -> dict[str, Any]:
    """Seed demo data. Requires manager token or agent API key."""
    agent_key = os.getenv("FLOWORDER_AGENT_API_KEY", "").strip() or agent_api.AGENT_API_KEY
    if identity is not None:
        require_manager(identity)
    elif x_floworder_agent_key and agent_key and hmac.compare_digest(x_floworder_agent_key, agent_key):
        pass
    else:
        raise HTTPException(401, "SEED_AUTH_REQUIRED")
    if os.getenv("ENABLE_DEMO_ADMIN_ACTIONS", "false").lower() != "true":
        raise HTTPException(403, "SEED_DEMO_ADMIN_ACTIONS_DISABLED")
    with db() as conn:
        reset_demo_data(conn)
    return {"status": "seeded", "at": iso()}


def build_dashboard(identity: CurrentIdentity, current_time: str | None = None) -> dict[str, Any]:
    """Internal dashboard builder. Requires a validated CurrentIdentity."""
    current = parse_dt(current_time) or now_cn()
    user_id = identity.user_id
    with db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM tasks").fetchall()]
        order_rows = [dict(r) for r in conn.execute("SELECT * FROM orders").fetchall()]
        
        # ENFORCE: Organization boundary filtering
        scoped_orders = []
        for order in order_rows:
            order_org = str(order.get("organization_id") or "").strip()
            if order_org:
                if identity.same_org(order_org):
                    scoped_orders.append(order)
            else:
                if owner_matches(order.get("owner"), identity.user_id):
                    scoped_orders.append(order)
        order_rows = scoped_orders
        
        scoped_tasks = []
        scoped_order_ids = {o["order_id"] for o in scoped_orders}
        for task in rows:
            task_org = str(task.get("organization_id") or "").strip()
            if task_org:
                if identity.same_org(task_org):
                    scoped_tasks.append(task)
            else:
                related_order_id = task.get("related_order_id")
                if related_order_id and related_order_id in scoped_order_ids:
                    scoped_tasks.append(task)
                elif owner_matches(task.get("owner_user_id"), identity.user_id):
                    scoped_tasks.append(task)
        rows = scoped_tasks
        
        if not identity.is_manager():
            rows = [row for row in rows if normalize_owner_value(row.get("owner_user_id")) == identity.user_id]
            assigned_order_ids = {row.get("related_order_id") for row in rows if row.get("related_order_id")}
            order_rows = [order for order in order_rows if owner_matches(order.get("owner"), identity.user_id) or order.get("order_id") in assigned_order_ids]
        orders = {r["order_id"]: r for r in order_rows}
        latest = conn.execute(
            "SELECT MAX(calculated_at) AS last_at FROM task_rankings WHERE current_user_id=?", (identity.user_id,)
        ).fetchone()
        latest_task = conn.execute("SELECT MAX(updated_at) AS last_task_at FROM tasks").fetchone()
        attention_flag = d16.resolve_feature_flag(
            conn,
            flag_key=d16.FLAG_ATTENTION_DASHBOARD,
            organization_id=identity.organization_id,
            user_id=identity.user_id,
        )
    rankings = coze_rankings(identity.user_id)
    cache_seconds = int(os.getenv("COZE_FT04_CACHE_SECONDS", "120"))
    stale = True
    if latest and latest["last_at"]:
        last_dt = parse_dt(latest["last_at"])
        task_dt = parse_dt(latest_task["last_task_at"]) if latest_task and latest_task["last_task_at"] else None
        stale = (
            not last_dt
            or (current - last_dt).total_seconds() > cache_seconds
            or bool(task_dt and task_dt > last_dt)
        )
    if coze_status().get("ready") and (not rankings or stale):
        run_ft04_refresh(identity.user_id)
        rankings = coze_rankings(identity.user_id)
    items = []
    for row in rows:
        local = decide_task(row, current, identity.user_id)
        rank = rankings.get(row.get("task_id"))
        if rank:
            local.update({
                "action_state": rank["action_state"],
                "recommended_action": rank.get("recommended_action") or local.get("recommended_action"),
                "target": rank.get("target") or local.get("target"),
                "next_action_at": rank.get("next_action_at"),
                "ranking_suppressed": rank.get("ranking_suppressed", False),
                "priority_score": rank.get("priority_score", 0),
                "priority_reasons": rank.get("priority_reasons") or [],
                "evidence": rank.get("evidence") or local.get("evidence") or [],
                "ranking_source": "COZE_FT04",
                "ranking_workflow_run_id": rank.get("workflow_run_id"),
            })
        else:
            local["ranking_source"] = "LOCAL_FALLBACK"
        local["order"] = orders.get(local.get("related_order_id"))
        items.append(local)
    items.sort(key=lambda x: x["priority_score"], reverse=True)
    top = [x for x in items if x["action_state"] not in {"DONE", "NOT_MY_RESPONSIBILITY"} and not x["ranking_suppressed"]][:5]
    attention_disabled = not bool(attention_flag.get("effective_enabled"))
    if attention_disabled:
        # Safe OFF: preserve the ordinary workspace list, but hide the prioritized attention surface.
        top = []
    summary = {
        "total": len(items),
        "do_now": sum(x["action_state"] == "DO_NOW" for x in items),
        "do_today": sum(x["action_state"] == "DO_TODAY" for x in items),
        "waiting": sum(x["action_state"] == "WAITING_EXTERNAL" for x in items),
        "needs_confirmation": sum(x["action_state"] == "NEEDS_CONFIRMATION" for x in items),
        "scheduled": sum(x["action_state"] == "SCHEDULED" for x in items),
        "escalate": sum(x["action_state"] == "ESCALATE" for x in items),
    }
    with db() as conn:
        activation = activation_snapshot(conn, identity.user_id)
    return {
        "current_time": iso(current), "summary": summary, "items": items, "top_actions": top,
        "ranking_source": "COZE_FT04" if rankings else "LOCAL_FALLBACK", "activation": activation,
        "feature_flags": {"attention_dashboard": attention_flag},
        "attention_dashboard_disabled": attention_disabled,
    }


@app.get("/api/dashboard")
def dashboard(
    current_time: str | None = Query(None),
    identity: CurrentIdentity = Depends(get_current_identity),
) -> dict[str, Any]:
    return build_dashboard(identity, current_time)


@app.get("/api/orders/{order_id}")
def order_detail(order_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    with db() as conn:
        order = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if not order:
            raise HTTPException(404, "订单不存在")
        order_dict = dict(order)
        require_order_access(identity, order_dict, conn)
        tasks = [dict(r) for r in conn.execute("SELECT * FROM tasks WHERE related_order_id=? ORDER BY created_at DESC", (order_id,))]
        risks = [dict(r) for r in conn.execute("SELECT * FROM risk_signals WHERE order_id=? ORDER BY created_at DESC", (order_id,))]
        messages = [dict(r) for r in conn.execute("SELECT * FROM source_messages WHERE order_id=? ORDER BY created_at DESC", (order_id,))]
        commitments = [dict(r) for r in conn.execute("SELECT * FROM commitment_history WHERE order_id=? ORDER BY created_at DESC", (order_id,))]
        events = [dict(r) for r in conn.execute("SELECT * FROM event_logs WHERE entity_id=? ORDER BY created_at DESC", (order_id,))]
    for task in tasks:
        task["evidence"] = json.loads(task.pop("evidence_json") or "[]")
    return {
        "order": order_dict, "tasks": tasks, "risks": risks,
        "messages": messages, "commitments": commitments, "events": events,
    }


@app.post("/api/tasks/{task_id}/contacted")
def contacted(task_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    body = payload.model_dump()
    promised_reply_at = body.get("promised_reply_at")
    waiting_on = body.get("waiting_on") or "factory"
    if not promised_reply_at:
        raise HTTPException(422, "缺少promised_reply_at")
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        task_dict = dict(row)
        order_id = task_dict.get("related_order_id")
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone() if order_id else None
        order_dict = dict(order_row) if order_row else None
        require_task_access(identity, task_dict, order_dict)
        timestamp = iso()
        conn.execute(
            """UPDATE tasks SET waiting_on=?, promised_reply_at=?, next_action_at=?,
               last_contact_at=?, pending_confirmation=0, updated_at=? WHERE task_id=?""",
            (waiting_on, promised_reply_at, promised_reply_at, timestamp, timestamp, task_id),
        )
        conn.execute(
            "INSERT INTO event_logs(event_id,entity_type,entity_id,event_type,payload_json,operator_id,created_at) VALUES(?,?,?,?,?,?,?)",
            (new_id("EVT"), "task", task_id, "CONTACT_RECORDED",
             json.dumps(body, ensure_ascii=False), identity.user_id, timestamp),
        )
        audit_log(conn, identity, "TASK_CONTACTED", "task", task_id, "SUCCESS", body)
        conn.commit()
    return {"status": "updated", "task_id": task_id, "waiting_on": waiting_on, "promised_reply_at": promised_reply_at}


@app.post("/api/tasks/{task_id}/complete")
def complete(task_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        task_dict = dict(row)
        order_id = task_dict.get("related_order_id")
        order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone() if order_id else None
        order_dict = dict(order_row) if order_row else None
        require_task_access(identity, task_dict, order_dict)
        timestamp = iso()
        changed = conn.execute(
            "UPDATE tasks SET status='DONE', updated_at=? WHERE task_id=?", (timestamp, task_id)
        ).rowcount
        if not changed:
            raise HTTPException(404, "任务不存在")
        audit_log(conn, identity, "TASK_COMPLETED", "task", task_id, "SUCCESS", {})
        conn.commit()
    return {"status": "done", "task_id": task_id}


@app.post("/api/writeback")
def writeback(
    payload: AnyPayload,
    x_api_key: str | None = Header(None),
) -> dict[str, Any]:
    if not API_KEY:
        raise HTTPException(503, "APP_API_KEY尚未配置，正式写回已禁用")
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
            begin_transaction(conn)
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
def demo_ft01(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    require_manager(identity)
    if os.getenv("ENABLE_DEMO_ADMIN_ACTIONS", "false").lower() != "true":
        raise HTTPException(403, "DEMO_ADMIN_ACTIONS_DISABLED")
    payload = json.loads((BASE_DIR / "demo_payloads" / "FT01_confirmed_writeback.json").read_text(encoding="utf-8"))
    with db() as conn:
        begin_transaction(conn)
        result = apply_writeback(conn, payload, json.loads(payload["transaction_json"]))
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
def demo_ft02(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
    require_manager(identity)
    if os.getenv("ENABLE_DEMO_ADMIN_ACTIONS", "false").lower() != "true":
        raise HTTPException(403, "DEMO_ADMIN_ACTIONS_DISABLED")
    payload = json.loads((BASE_DIR / "demo_payloads" / "FT02_confirmed_writeback.json").read_text(encoding="utf-8"))
    with db() as conn:
        begin_transaction(conn)
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
