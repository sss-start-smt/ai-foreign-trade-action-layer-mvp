from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

import d16_observability as d16
from auth import CurrentIdentity, get_current_identity, require_manager
from database import db


class AnyPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, d16.D16ForbiddenConfiguration):
        return HTTPException(409, {"code": "D16_UNSAFE_FLAG_CONFIGURATION", "message": str(exc)})
    if isinstance(exc, d16.D16ValidationError):
        return HTTPException(422, {"code": "D16_INVALID_REQUEST", "message": str(exc)})
    return HTTPException(500, {"code": "D16_INTERNAL_ERROR", "message": "Observability service unavailable"})


def register_d16_api(app: Any) -> None:
    router = APIRouter()

    @router.get("/api/d16/contract")
    def contract(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        _ = identity
        return d16.product_contract()

    @router.get("/api/d16/flags")
    def effective_flags(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        with db() as conn:
            return d16.resolve_all_flags(conn, organization_id=identity.organization_id, user_id=identity.user_id)

    @router.get("/api/d16/flags/admin")
    def flag_admin(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        require_manager(identity)
        with db() as conn:
            return {
                "policy_version": d16.D16_POLICY_VERSION,
                "items": d16.list_flag_overrides(conn, organization_id=identity.organization_id),
                "events": d16.list_flag_events(conn, organization_id=identity.organization_id, limit=100),
            }

    @router.put("/api/d16/flags/{flag_key}")
    def set_flag(flag_key: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        require_manager(identity)
        body = payload.model_dump()
        scope_type = str(body.get("scope_type") or "ORG").upper()
        scope_id = str(body.get("scope_id") or (identity.organization_id if scope_type == "ORG" else "")).strip()
        if scope_type == "USER" and not scope_id:
            raise HTTPException(422, {"code": "D16_INVALID_REQUEST", "message": "USER scope requires scope_id"})
        try:
            with db() as conn:
                result = d16.set_feature_flag_override(
                    conn,
                    flag_key=flag_key,
                    organization_id=identity.organization_id,
                    scope_type=scope_type,
                    scope_id=scope_id,
                    enabled=bool(body.get("enabled")),
                    rollout_percent=int(body.get("rollout_percent", 100)),
                    reason=str(body.get("reason") or "")[:500] or None,
                    actor=identity.user_id,
                )
                conn.commit()
                return result
        except Exception as exc:
            raise _error(exc) from None

    @router.delete("/api/d16/flags/{flag_key}")
    def delete_flag(flag_key: str, scope_type: str = Query("ORG", pattern="^(ORG|USER)$"), scope_id: str | None = None, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        require_manager(identity)
        resolved_scope = scope_id or (identity.organization_id if scope_type == "ORG" else "")
        if not resolved_scope:
            raise HTTPException(422, {"code": "D16_INVALID_REQUEST", "message": "USER scope requires scope_id"})
        try:
            with db() as conn:
                deleted = d16.delete_feature_flag_override(
                    conn,
                    flag_key=flag_key,
                    organization_id=identity.organization_id,
                    scope_type=scope_type,
                    scope_id=resolved_scope,
                    actor=identity.user_id,
                )
                conn.commit()
                return {"deleted": deleted, "flag_key": flag_key, "scope_type": scope_type, "scope_id": resolved_scope}
        except Exception as exc:
            raise _error(exc) from None

    @router.get("/api/d16/observability/summary")
    def observability_summary(window_minutes: int = Query(60, ge=5, le=1440), identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        require_manager(identity)
        with db() as conn:
            summary = d16.observability_summary(conn, organization_id=identity.organization_id, window_minutes=window_minutes)
            summary["alerts"] = d16.evaluate_alerts(summary)
            summary["alert_count"] = len(summary["alerts"])
            return summary

    app.include_router(router)
