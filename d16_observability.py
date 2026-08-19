from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import d13_agent_skill as d13_skill
import d13_model_provider as d13_model
import d13_skill_runtime as d13_skill_runtime
import d15_durable_execution as d15
import d7_risk_engine as d7

CN_TZ = timezone(timedelta(hours=8))
D16_POLICY_VERSION = "D16_OBSERVABILITY_FLAGS_V1"

FLAG_AGENT_ASSIST = "agent_assist_enabled"
FLAG_ATTENTION_DASHBOARD = "attention_dashboard_enabled"
FLAG_ERP_SYNC = "erp_readonly_sync_enabled"
FLAG_EXTERNAL_DISPATCH = "external_action_dispatch_enabled"

FLAG_DEFINITIONS: dict[str, dict[str, Any]] = {
    FLAG_AGENT_ASSIST: {
        "default_enabled": True,
        "risk_level": "MEDIUM",
        "safe_off_behavior": "Stop new Agent planning/tool requests; manual workspace, Human Review and durable business actions remain available.",
        "pilot_strategy": "Start with selected users, then organization rollout.",
    },
    FLAG_ATTENTION_DASHBOARD: {
        "default_enabled": True,
        "risk_level": "LOW",
        "safe_off_behavior": "Hide the attention dashboard and fall back to the ordinary order/workspace list; never silently revert to an older ranking policy.",
        "pilot_strategy": "Organization or user rollout.",
    },
    FLAG_ERP_SYNC: {
        "default_enabled": True,
        "risk_level": "MEDIUM",
        "safe_off_behavior": "Block new ERP sync calls while keeping the last successful read-only snapshots visible with freshness metadata.",
        "pilot_strategy": "Organization rollout only in normal operations; user override is allowed for test cohorts.",
    },
    FLAG_EXTERNAL_DISPATCH: {
        "default_enabled": False,
        "risk_level": "HIGH",
        "safe_off_behavior": "D10 may accept durable intent, but no external ERP/message adapter dispatch is allowed.",
        "pilot_strategy": "Must remain OFF until a real external write adapter is present and separately accepted.",
    },
}

ALERT_RULES: tuple[dict[str, Any], ...] = (
    {"code": "D15_RESULT_UNCERTAIN", "severity": "P1", "owner_role": "manager", "condition": "Any unresolved RESULT_UNCERTAIN", "auto_action": "Pause automatic dispatch; require reconciliation."},
    {"code": "D15_HUMAN_REQUIRED", "severity": "P1", "owner_role": "manager", "condition": "Any unresolved HUMAN_REQUIRED", "auto_action": "Keep automation paused and surface human takeover."},
    {"code": "D13_MODEL_FAILURE_RATE", "severity": "P1", "owner_role": "manager", "condition": ">=5 model attempts and failure rate >=20% in window", "auto_action": "No permission expansion; investigate provider/route availability."},
    {"code": "D13_TOOL_ERROR_RATE", "severity": "P1", "owner_role": "manager", "condition": ">=5 tool calls and error rate >=10% in window", "auto_action": "Do not retry semantic/permission errors; inspect tool/policy trace."},
    {"code": "D13_FALLBACK_RATE", "severity": "P2", "owner_role": "manager", "condition": ">=5 model attempts and fallback selection rate >=30%", "auto_action": "Keep serving via fallback but investigate primary provider health."},
    {"code": "ERP_FRESHNESS", "severity": "P1/P2", "owner_role": "manager", "condition": "ERP UNAVAILABLE/NEVER_SYNCED=P1; STALE=P2", "auto_action": "Preserve snapshot; label freshness; do not claim real-time data."},
    {"code": "FLAG_CONFIGURATION_RISK", "severity": "P0", "owner_role": "manager", "condition": "External dispatch flag ON without a real external adapter", "auto_action": "Reject the configuration change."},
)


class D16Error(RuntimeError):
    pass


class D16ValidationError(D16Error):
    pass


class D16ForbiddenConfiguration(D16Error):
    pass


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12].upper()}"


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        if hasattr(row, "keys"):
            return {k: row[k] for k in row.keys()}
        return {}


def _table_exists(conn: Any, name: str) -> bool:
    try:
        row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if row:
            return True
    except Exception:
        pass
    try:
        conn.execute(f"SELECT 1 FROM {name} WHERE 1=0").fetchone()
        return True
    except Exception:
        return False


def _ensure_tables(conn: Any) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS d16_feature_flag_overrides (
           override_id TEXT PRIMARY KEY,
           organization_id TEXT NOT NULL,
           scope_type TEXT NOT NULL,
           scope_id TEXT NOT NULL,
           flag_key TEXT NOT NULL,
           enabled INTEGER NOT NULL,
           rollout_percent INTEGER NOT NULL DEFAULT 100,
           reason TEXT,
           updated_by TEXT NOT NULL,
           created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL,
           UNIQUE(scope_type, scope_id, flag_key)
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_d16_flags_org_key
           ON d16_feature_flag_overrides(organization_id, flag_key, scope_type, scope_id)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS d16_feature_flag_events (
           event_id TEXT PRIMARY KEY,
           organization_id TEXT NOT NULL,
           flag_key TEXT NOT NULL,
           scope_type TEXT NOT NULL,
           scope_id TEXT NOT NULL,
           action TEXT NOT NULL,
           old_value_json TEXT NOT NULL DEFAULT '{}',
           new_value_json TEXT NOT NULL DEFAULT '{}',
           actor TEXT NOT NULL,
           created_at TEXT NOT NULL
        )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_d16_flag_events_org_time
           ON d16_feature_flag_events(organization_id, created_at)"""
    )


def _external_adapter_present() -> bool:
    return os.getenv("FLOWORDER_EXTERNAL_WRITE_ADAPTER_PRESENT", "0").strip().lower() in {"1", "true", "yes", "on"}


def _rollout_bucket(flag_key: str, organization_id: str, user_id: str) -> int:
    raw = f"{flag_key}:{organization_id}:{user_id}".encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:8], 16) % 100


def _validate_flag(flag_key: str) -> None:
    if flag_key not in FLAG_DEFINITIONS:
        raise D16ValidationError(f"Unknown feature flag: {flag_key}")


def _override_row(conn: Any, *, organization_id: str, flag_key: str, scope_type: str, scope_id: str) -> dict[str, Any] | None:
    _ensure_tables(conn)
    row = conn.execute(
        "SELECT * FROM d16_feature_flag_overrides WHERE organization_id=? AND flag_key=? AND scope_type=? AND scope_id=?",
        (organization_id, flag_key, scope_type, scope_id),
    ).fetchone()
    return _row_dict(row) if row else None


def resolve_feature_flag(conn: Any, *, flag_key: str, organization_id: str, user_id: str) -> dict[str, Any]:
    _validate_flag(flag_key)
    definition = FLAG_DEFINITIONS[flag_key]
    user_override = _override_row(
        conn, organization_id=organization_id, flag_key=flag_key, scope_type="USER", scope_id=user_id
    )
    org_override = _override_row(
        conn, organization_id=organization_id, flag_key=flag_key, scope_type="ORG", scope_id=organization_id
    )
    source = "DEFAULT"
    override = None
    if user_override:
        source, override = "USER", user_override
    elif org_override:
        source, override = "ORG", org_override

    configured_enabled = bool(override["enabled"]) if override else bool(definition["default_enabled"])
    rollout_percent = int(override["rollout_percent"]) if override else 100
    bucket = _rollout_bucket(flag_key, organization_id, user_id)
    effective_enabled = configured_enabled and bucket < rollout_percent
    if flag_key == FLAG_EXTERNAL_DISPATCH and effective_enabled and not _external_adapter_present():
        # Defense-in-depth: even a stale/bad database override cannot activate a capability that does not exist.
        effective_enabled = False
        source = f"{source}_BLOCKED_NO_ADAPTER"
    return {
        "policy_version": D16_POLICY_VERSION,
        "flag_key": flag_key,
        "effective_enabled": effective_enabled,
        "configured_enabled": configured_enabled,
        "rollout_percent": rollout_percent,
        "rollout_bucket": bucket,
        "source": source,
        "risk_level": definition["risk_level"],
        "safe_off_behavior": definition["safe_off_behavior"],
    }


def resolve_all_flags(conn: Any, *, organization_id: str, user_id: str) -> dict[str, Any]:
    return {
        "policy_version": D16_POLICY_VERSION,
        "organization_id": organization_id,
        "user_id": user_id,
        "items": [
            resolve_feature_flag(conn, flag_key=key, organization_id=organization_id, user_id=user_id)
            for key in FLAG_DEFINITIONS
        ],
    }


def set_feature_flag_override(
    conn: Any,
    *,
    flag_key: str,
    organization_id: str,
    scope_type: str,
    scope_id: str,
    enabled: bool,
    rollout_percent: int,
    reason: str | None,
    actor: str,
) -> dict[str, Any]:
    _validate_flag(flag_key)
    scope_type = str(scope_type or "").upper()
    if scope_type not in {"ORG", "USER"}:
        raise D16ValidationError("scope_type must be ORG or USER")
    if scope_type == "ORG" and scope_id != organization_id:
        raise D16ValidationError("ORG scope_id must equal organization_id")
    if not 0 <= int(rollout_percent) <= 100:
        raise D16ValidationError("rollout_percent must be between 0 and 100")
    if flag_key == FLAG_EXTERNAL_DISPATCH and enabled and not _external_adapter_present():
        raise D16ForbiddenConfiguration(
            "external_action_dispatch_enabled cannot be enabled before a real external write adapter is present"
        )
    _ensure_tables(conn)
    old = _override_row(
        conn, organization_id=organization_id, flag_key=flag_key, scope_type=scope_type, scope_id=scope_id
    )
    now = _now_iso()
    if old:
        conn.execute(
            """UPDATE d16_feature_flag_overrides
               SET enabled=?,rollout_percent=?,reason=?,updated_by=?,updated_at=?
               WHERE override_id=?""",
            (1 if enabled else 0, int(rollout_percent), (reason or "")[:500] or None, actor, now, old["override_id"]),
        )
        override_id = old["override_id"]
        action = "UPDATE"
    else:
        override_id = _new_id("D16FLAG")
        conn.execute(
            """INSERT INTO d16_feature_flag_overrides
               (override_id,organization_id,scope_type,scope_id,flag_key,enabled,rollout_percent,reason,updated_by,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (override_id, organization_id, scope_type, scope_id, flag_key, 1 if enabled else 0, int(rollout_percent), (reason or "")[:500] or None, actor, now, now),
        )
        action = "CREATE"
    new_value = {
        "enabled": bool(enabled),
        "rollout_percent": int(rollout_percent),
        "reason": (reason or "")[:500] or None,
    }
    conn.execute(
        """INSERT INTO d16_feature_flag_events
           (event_id,organization_id,flag_key,scope_type,scope_id,action,old_value_json,new_value_json,actor,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            _new_id("D16EVT"), organization_id, flag_key, scope_type, scope_id, action,
            json.dumps(old or {}, ensure_ascii=False, sort_keys=True, default=str),
            json.dumps(new_value, ensure_ascii=False, sort_keys=True), actor, now,
        ),
    )
    return {
        "override_id": override_id,
        "organization_id": organization_id,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "flag_key": flag_key,
        **new_value,
        "updated_by": actor,
        "updated_at": now,
    }


def delete_feature_flag_override(conn: Any, *, flag_key: str, organization_id: str, scope_type: str, scope_id: str, actor: str) -> bool:
    _validate_flag(flag_key)
    _ensure_tables(conn)
    old = _override_row(
        conn, organization_id=organization_id, flag_key=flag_key, scope_type=scope_type, scope_id=scope_id
    )
    if not old:
        return False
    conn.execute("DELETE FROM d16_feature_flag_overrides WHERE override_id=?", (old["override_id"],))
    conn.execute(
        """INSERT INTO d16_feature_flag_events
           (event_id,organization_id,flag_key,scope_type,scope_id,action,old_value_json,new_value_json,actor,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            _new_id("D16EVT"), organization_id, flag_key, scope_type, scope_id, "DELETE",
            json.dumps(old, ensure_ascii=False, sort_keys=True, default=str), "{}", actor, _now_iso(),
        ),
    )
    return True


def list_flag_overrides(conn: Any, *, organization_id: str) -> list[dict[str, Any]]:
    _ensure_tables(conn)
    rows = conn.execute(
        "SELECT * FROM d16_feature_flag_overrides WHERE organization_id=? ORDER BY flag_key,scope_type,scope_id",
        (organization_id,),
    ).fetchall()
    return [_row_dict(r) for r in rows]


def list_flag_events(conn: Any, *, organization_id: str, limit: int = 100) -> list[dict[str, Any]]:
    _ensure_tables(conn)
    limit = max(1, min(int(limit), 500))
    rows = conn.execute(
        f"SELECT * FROM d16_feature_flag_events WHERE organization_id=? ORDER BY created_at DESC LIMIT {limit}",
        (organization_id,),
    ).fetchall()
    items = []
    for row in rows:
        item = _row_dict(row)
        for key in ("old_value_json", "new_value_json"):
            try:
                item[key[:-5]] = json.loads(item.pop(key) or "{}")
            except Exception:
                item[key[:-5]] = {}
        items.append(item)
    return items


def _count(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
        return int(row[0] if row is not None else 0)
    except Exception:
        return 0


def _erp_snapshot(conn: Any, *, organization_id: str) -> dict[str, Any]:
    if not _table_exists(conn, "erp_sync_state"):
        return {"available": False, "freshness": "NOT_INSTALLED", "doctypes": []}
    rows = conn.execute(
        "SELECT * FROM erp_sync_state WHERE organization_id=? ORDER BY doctype",
        (organization_id,),
    ).fetchall()
    threshold = int(os.getenv("ERPNEXT_STALE_AFTER_SECONDS", "3600"))
    now = datetime.now(CN_TZ)
    items: list[dict[str, Any]] = []
    freshnesses: list[str] = []
    for row in rows:
        item = _row_dict(row)
        status = str(item.get("sync_status") or "NEVER_SYNCED")
        freshness = status
        if status == "FRESH":
            last_success = item.get("last_success_at")
            try:
                dt = datetime.fromisoformat(str(last_success).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=CN_TZ)
                if (now - dt.astimezone(CN_TZ)).total_seconds() > threshold:
                    freshness = "STALE"
            except Exception:
                freshness = "STALE"
        item["freshness"] = freshness
        items.append(item)
        freshnesses.append(freshness)
    if not freshnesses:
        global_freshness = "NEVER_SYNCED"
    elif "UNAVAILABLE" in freshnesses:
        global_freshness = "UNAVAILABLE"
    elif "STALE" in freshnesses:
        global_freshness = "STALE"
    elif "NEVER_SYNCED" in freshnesses:
        global_freshness = "NEVER_SYNCED"
    else:
        global_freshness = "FRESH"
    return {"available": True, "freshness": global_freshness, "stale_after_seconds": threshold, "doctypes": items}


def version_registry() -> dict[str, Any]:
    return {
        "d7_risk_policy": d7.D7_POLICY_VERSION,
        "d14_attention_ranking": d7.ATTENTION_RANKING_VERSION,
        "d13_skill": d13_skill.D13_SKILL_VERSION,
        "d13_tool_contract": d13_skill.D13_TOOL_CONTRACT_VERSION,
        "d13_semantic_guard": d13_skill.D13_SEMANTIC_GUARD_VERSION,
        "d13_transcription": d13_skill_runtime.D13_TRANSCRIPTION_VERSION,
        "d13_model_routing": d13_model.D13_MODEL_ROUTING_VERSION,
        "d15_durable_execution": d15.D15_POLICY_VERSION,
        "d16_observability": D16_POLICY_VERSION,
    }


def observability_summary(conn: Any, *, organization_id: str, window_minutes: int = 60) -> dict[str, Any]:
    window_minutes = max(5, min(int(window_minutes), 24 * 60))
    cutoff = (datetime.now(CN_TZ) - timedelta(minutes=window_minutes)).isoformat(timespec="seconds")

    d13_total = _count(conn, "SELECT COUNT(*) FROM d13_agent_runs WHERE organization_id=? AND created_at>=?", (organization_id, cutoff)) if _table_exists(conn, "d13_agent_runs") else 0
    d13_failed = _count(conn, "SELECT COUNT(*) FROM d13_agent_runs WHERE organization_id=? AND created_at>=? AND status='FAILED'", (organization_id, cutoff)) if _table_exists(conn, "d13_agent_runs") else 0
    model_attempts = model_errors = fallback_selected = tool_calls = tool_errors = policy_blocks = 0
    if _table_exists(conn, "d13_agent_trace_events") and _table_exists(conn, "d13_agent_runs"):
        base = " FROM d13_agent_trace_events e JOIN d13_agent_runs r ON r.run_id=e.run_id WHERE r.organization_id=? AND e.created_at>=?"
        params = (organization_id, cutoff)
        model_attempts = _count(conn, "SELECT COUNT(*)" + base + " AND e.event_type='MODEL_ATTEMPT'", params)
        model_errors = _count(conn, "SELECT COUNT(*)" + base + " AND e.event_type='MODEL_ATTEMPT' AND e.status='ERROR'", params)
        fallback_selected = _count(conn, "SELECT COUNT(*)" + base + " AND e.event_type IN ('MODEL_FALLBACK_SELECTED','MODEL_FALLBACK_PREFERRED')", params)
        tool_calls = _count(conn, "SELECT COUNT(*)" + base + " AND e.event_type='TOOL_CALL'", params)
        tool_errors = _count(conn, "SELECT COUNT(*)" + base + " AND e.event_type='TOOL_CALL' AND e.status='ERROR'", params)
        policy_blocks = _count(conn, "SELECT COUNT(*)" + base + " AND e.status='BLOCKED'", params)

    d15_counts: dict[str, int] = {}
    if _table_exists(conn, "d15_outbox_execution_state"):
        rows = conn.execute(
            "SELECT state,COUNT(*) AS c FROM d15_outbox_execution_state WHERE organization_id=? GROUP BY state",
            (organization_id,),
        ).fetchall()
        for row in rows:
            item = _row_dict(row)
            d15_counts[str(item.get("state") or row[0])] = int(item.get("c") if "c" in item else row[1])

    erp = _erp_snapshot(conn, organization_id=organization_id)
    return {
        "policy_version": D16_POLICY_VERSION,
        "organization_id": organization_id,
        "window_minutes": window_minutes,
        "generated_at": _now_iso(),
        "versions": version_registry(),
        "d13_agent": {
            "run_count": d13_total,
            "failed_run_count": d13_failed,
            "failed_run_rate": round(d13_failed / d13_total, 4) if d13_total else 0.0,
            "model_attempt_count": model_attempts,
            "model_error_count": model_errors,
            "model_error_rate": round(model_errors / model_attempts, 4) if model_attempts else 0.0,
            "fallback_selection_count": fallback_selected,
            "fallback_selection_rate": round(fallback_selected / model_attempts, 4) if model_attempts else 0.0,
            "tool_call_count": tool_calls,
            "tool_error_count": tool_errors,
            "tool_error_rate": round(tool_errors / tool_calls, 4) if tool_calls else 0.0,
            "policy_block_count": policy_blocks,
        },
        "d15_execution": {
            "state_counts": d15_counts,
            "result_uncertain": d15_counts.get("RESULT_UNCERTAIN", 0),
            "human_required": d15_counts.get("HUMAN_REQUIRED", 0),
            "retryable": d15_counts.get("RETRYABLE", 0),
            "in_flight": d15_counts.get("IN_FLIGHT", 0),
        },
        "erp_readonly": erp,
        "trace_scope": "BUSINESS_EXECUTION_FACTS_ONLY",
        "hidden_chain_of_thought_recorded": False,
    }


def evaluate_alerts(summary: dict[str, Any]) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    d13s = summary.get("d13_agent") or {}
    d15s = summary.get("d15_execution") or {}
    erp = summary.get("erp_readonly") or {}

    def add(code: str, severity: str, title: str, reason: str, action: str) -> None:
        alerts.append({
            "code": code,
            "severity": severity,
            "title": title,
            "reason": reason,
            "owner_role": "manager",
            "delivery": "IN_PRODUCT_OBSERVABILITY_VIEW",
            "recommended_action": action,
            "external_notification_sent": False,
        })

    if int(d15s.get("result_uncertain") or 0) > 0:
        add("D15_RESULT_UNCERTAIN", "P1", "存在待对账的外部动作", f"{d15s['result_uncertain']} 个 RESULT_UNCERTAIN", "暂停自动重试并完成对账。")
    if int(d15s.get("human_required") or 0) > 0:
        add("D15_HUMAN_REQUIRED", "P1", "存在需要人工接管的动作", f"{d15s['human_required']} 个 HUMAN_REQUIRED", "由主管核对、授权或处理后显式恢复。")
    if int(d13s.get("model_attempt_count") or 0) >= 5 and float(d13s.get("model_error_rate") or 0) >= 0.20:
        add("D13_MODEL_FAILURE_RATE", "P1", "模型可用性异常", f"模型失败率 {d13s['model_error_rate']:.0%}", "检查Provider/路由；不要放宽语义或权限边界。")
    if int(d13s.get("tool_call_count") or 0) >= 5 and float(d13s.get("tool_error_rate") or 0) >= 0.10:
        add("D13_TOOL_ERROR_RATE", "P1", "Tool错误率异常", f"Tool错误率 {d13s['tool_error_rate']:.0%}", "按Tool/权限/输入错误分类处理，禁止无差别重试。")
    if int(d13s.get("model_attempt_count") or 0) >= 5 and float(d13s.get("fallback_selection_rate") or 0) >= 0.30:
        add("D13_FALLBACK_RATE", "P2", "Fallback使用率偏高", f"Fallback选择率 {d13s['fallback_selection_rate']:.0%}", "保持服务可用，同时排查Primary Provider。")
    freshness = str(erp.get("freshness") or "")
    if freshness in {"UNAVAILABLE", "NEVER_SYNCED"}:
        add("ERP_FRESHNESS", "P1", "ERP事实源不可用", f"ERP freshness={freshness}", "保留上次成功Snapshot并显式标注非实时。")
    elif freshness == "STALE":
        add("ERP_FRESHNESS", "P2", "ERP数据已陈旧", "ERP freshness=STALE", "优先恢复只读同步；页面持续显示freshness。")
    return alerts


def product_contract() -> dict[str, Any]:
    return {
        "policy_version": D16_POLICY_VERSION,
        "product_principles": [
            "Observe business execution facts, not hidden chain-of-thought.",
            "A flag must define a safe OFF behavior before it can be used for rollout.",
            "Safety controls such as Human Review are not optional flags.",
            "Feature flags may stop additive automation; they must not silently downgrade to an unaccepted old policy.",
            "P0 configuration risks are rejected, not merely alerted.",
        ],
        "feature_flags": FLAG_DEFINITIONS,
        "alert_rules": list(ALERT_RULES),
        "versions": version_registry(),
        "notification_scope": {
            "implemented": "In-product manager observability view/API",
            "not_claimed": "No email/WeCom/PagerDuty notification adapter is implemented in D16 baseline.",
        },
    }
