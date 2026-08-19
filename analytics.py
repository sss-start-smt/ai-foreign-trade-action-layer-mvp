from __future__ import annotations

import json
import statistics
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

CN_TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def ensure_analytics_schema(conn) -> None:
    if getattr(conn, "is_pg", False):
        from database import table_exists
        if not table_exists(conn, "analytics_events"):
            raise RuntimeError("PostgreSQL schema missing analytics_events; run `alembic upgrade head`.")
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS analytics_events (
            event_id TEXT PRIMARY KEY,
            event_name TEXT NOT NULL,
            organization_id TEXT,
            user_id TEXT,
            user_role TEXT,
            session_id TEXT,
            order_id TEXT,
            run_id TEXT,
            source TEXT NOT NULL DEFAULT 'server',
            app_version TEXT NOT NULL DEFAULT '6.1.0',
            properties_json TEXT NOT NULL DEFAULT '{}',
            client_timestamp TEXT,
            server_timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_analytics_event_time
            ON analytics_events(event_name, server_timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_analytics_user_time
            ON analytics_events(user_id, server_timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_analytics_run
            ON analytics_events(run_id, server_timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_analytics_order
            ON analytics_events(order_id, server_timestamp DESC);
        """
    )


def track_event(
    conn,
    event_name: str,
    *,
    organization_id: str | None = None,
    user_id: str | None = None,
    user_role: str | None = None,
    session_id: str | None = None,
    order_id: str | None = None,
    run_id: str | None = None,
    source: str = "server",
    app_version: str = "6.1.0",
    properties: dict[str, Any] | None = None,
    client_timestamp: str | None = None,
) -> str:
    """Write a privacy-minimised analytics event.

    Raw customer messages, tokens and API keys must not be passed in properties.
    The helper intentionally stores only JSON-safe metadata needed for product,
    AI-effectiveness and system-quality analysis.
    """
    ensure_analytics_schema(conn)
    event_id = f"AEVT-{uuid.uuid4().hex[:16].upper()}"
    conn.execute(
        """
        INSERT INTO analytics_events(
            event_id,event_name,organization_id,user_id,user_role,session_id,
            order_id,run_id,source,app_version,properties_json,
            client_timestamp,server_timestamp
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            event_id,
            str(event_name),
            organization_id,
            user_id,
            user_role,
            session_id,
            order_id,
            run_id,
            source,
            app_version,
            json.dumps(properties or {}, ensure_ascii=False, separators=(",", ":")),
            client_timestamp,
            now_iso(),
        ),
    )
    return event_id


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    position = (len(ordered) - 1) * p
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 2)


def _unique_users(events: Iterable[dict[str, Any]], names: set[str]) -> int:
    return len({e.get("user_id") for e in events if e.get("event_name") in names and e.get("user_id")})


def build_analytics_summary(conn, *, days: int = 30, organization_id: str | None = None) -> dict[str, Any]:
    ensure_analytics_schema(conn)
    days = max(1, min(int(days), 365))
    since = (datetime.now(CN_TZ) - timedelta(days=days)).isoformat(timespec="seconds")
    sql = "SELECT * FROM analytics_events WHERE server_timestamp>=?"
    params: list[Any] = [since]
    if organization_id:
        sql += " AND organization_id=?"
        params.append(organization_id)
    sql += " ORDER BY server_timestamp DESC"
    rows = [dict(r) for r in conn.execute(sql, params)]
    for row in rows:
        row["properties"] = _safe_json(row.pop("properties_json", "{}"))

    by_name: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_name.setdefault(row["event_name"], []).append(row)

    def count(name: str) -> int:
        return len(by_name.get(name, []))

    # Activation funnel uses distinct users instead of raw click counts.
    activation = {
        "order_import_completed_users": _unique_users(rows, {"order_import_completed"}),
        "bulk_update_submitted_users": _unique_users(rows, {"bulk_update_submitted"}),
        "bulk_update_confirmed_users": _unique_users(rows, {"bulk_update_confirmed"}),
        "effective_action_users": _unique_users(rows, {"task_draft_created", "approval_created"}),
    }

    candidate_total = accepted_total = edited_total = rejected_total = 0
    for event in by_name.get("bulk_update_confirmed", []):
        props = event["properties"]
        candidate_total += int(props.get("candidate_field_count") or 0)
        accepted_total += int(props.get("accepted_count") or 0)
        edited_total += int(props.get("edited_count") or 0)
        rejected_total += int(props.get("rejected_count") or 0)
    adopted_total = accepted_total + edited_total

    run_started = count("agent_run_started")
    run_completed = count("agent_run_completed")
    run_timeout = count("agent_run_timeout")
    diagnosis_completed = count("priority_diagnosis_completed")
    approval_created = count("approval_created")
    approval_decided = by_name.get("approval_decided", [])
    approval_approved = sum(1 for e in approval_decided if e["properties"].get("decision") == "APPROVE")

    durations: list[float] = []
    for event_name in ("agent_run_completed", "priority_diagnosis_completed", "bulk_update_parsed"):
        for event in by_name.get(event_name, []):
            duration = event["properties"].get("duration_ms")
            if isinstance(duration, (int, float)):
                durations.append(float(duration))

    response_missing = sum(
        1 for e in by_name.get("agent_run_timeout", [])
        if e["properties"].get("approval_created") is True
        and e["properties"].get("final_response_generated") is False
    )

    return {
        "period_days": days,
        "generated_at": now_iso(),
        "total_events": len(rows),
        "active_users": len({r.get("user_id") for r in rows if r.get("user_id")}),
        "activation_funnel": activation,
        "ai_value": {
            "candidate_field_count": candidate_total,
            "accepted_count": accepted_total,
            "edited_count": edited_total,
            "rejected_count": rejected_total,
            "candidate_adoption_rate": round(adopted_total / candidate_total, 4) if candidate_total else None,
            "direct_adoption_rate": round(accepted_total / candidate_total, 4) if candidate_total else None,
            "approval_created_count": approval_created,
            "approval_approval_rate": round(approval_approved / len(approval_decided), 4) if approval_decided else None,
        },
        "system_quality": {
            "agent_run_started": run_started,
            "agent_run_completed": run_completed,
            "priority_diagnosis_completed": diagnosis_completed,
            "agent_run_timeout": run_timeout,
            "agent_success_rate": round(run_completed / run_started, 4) if run_started else None,
            "agent_timeout_rate": round(run_timeout / run_started, 4) if run_started else None,
            "approval_created_but_response_missing": response_missing,
            "duration_p50_ms": _percentile(durations, 0.50),
            "duration_p95_ms": _percentile(durations, 0.95),
        },
        "event_counts": {name: len(items) for name, items in sorted(by_name.items())},
        "recent_events": [
            {
                "event_id": r["event_id"],
                "event_name": r["event_name"],
                "user_id": r.get("user_id"),
                "order_id": r.get("order_id"),
                "run_id": r.get("run_id"),
                "properties": r["properties"],
                "server_timestamp": r["server_timestamp"],
            }
            for r in rows[:30]
        ],
    }
