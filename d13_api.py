from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict

import d10_business_action as d10
import d12_human_review as d12
import d13_agent_runtime as d13_runtime
import d13_agent_skill as d13
import d16_observability as d16
from agent_api import _require_agent_key
from auth import CurrentIdentity, get_current_identity
from database import db


class AnyPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def _require_agent_assist(conn: Any, identity: CurrentIdentity) -> None:
    flag = d16.resolve_feature_flag(
        conn, flag_key=d16.FLAG_AGENT_ASSIST,
        organization_id=identity.organization_id, user_id=identity.user_id,
    )
    if not flag["effective_enabled"]:
        raise HTTPException(
            503,
            {
                "code": "FEATURE_DISABLED",
                "feature": d16.FLAG_AGENT_ASSIST,
                "message": "Agent assist is disabled for this rollout scope. Manual workspace and Human Review remain available.",
            },
        )


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, d13_runtime.D13ModelExecutionError):
        # Do not echo provider exception text to HTTP clients. The runtime
        # deliberately raises a public-safe message and exposes only an
        # allow-listed error kind for diagnosis.
        detail = {"code": "D13_MODEL_UNAVAILABLE", "message": "Model provider unavailable"}
        error_kind = getattr(exc, "error_kind", None)
        if error_kind:
            detail["error_kind"] = str(error_kind)
        return HTTPException(503, detail)
    if isinstance(exc, (d13_runtime.D13RunNotFound, d13.D13NotFoundError, d12.D12NotFoundError, d10.D10NotFoundError)):
        return HTTPException(404, {"code": "D13_NOT_FOUND", "message": str(exc)})
    if isinstance(exc, (d13_runtime.D13RunForbidden, d13.D13ForbiddenError, d12.D12ForbiddenError)):
        return HTTPException(403, {"code": "D13_FORBIDDEN", "message": str(exc)})
    if isinstance(exc, (d13_runtime.D13RunStateError, d13.D13ValidationError, d12.D12StateError, d10.D10StateError)):
        return HTTPException(422, {"code": "D13_INVALID_REQUEST", "message": str(exc)})
    if isinstance(exc, (d12.D12ConflictError, d12.D12StaleReview, d10.D10ConflictError)):
        return HTTPException(409, {"code": "D13_CONFLICT", "message": str(exc)})
    return HTTPException(500, {"code": "D13_INTERNAL_ERROR", "message": str(exc)})


def register_d13_api(app: Any) -> None:
    router = APIRouter()

    @router.get("/api/d13/skill/manifest")
    def skill_manifest(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        return d13.tool_manifest()

    @router.post("/api/d13/tools/request-action")
    def request_action(
        payload: AnyPayload,
        identity: CurrentIdentity = Depends(get_current_identity),
        x_floworder_agent_key: str | None = Header(None),
    ) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump()
        try:
            with db() as conn:
                _require_agent_assist(conn, identity)
                result = d13.request_controlled_action(
                    conn,
                    tool_name=str(body.get("tool_name") or ""),
                    task_id=str(body.get("task_id") or ""),
                    payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
                    identity=identity,
                    idempotency_key=str(body.get("idempotency_key") or ""),
                    reason=body.get("reason"),
                    request_id=body.get("request_id"),
                )
                conn.commit()
                return result
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/api/d13/runs/start")
    def start_run(
        payload: AnyPayload,
        identity: CurrentIdentity = Depends(get_current_identity),
        x_floworder_agent_key: str | None = Header(None),
    ) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump()
        try:
            with db() as conn:
                _require_agent_assist(conn, identity)
                result = d13_runtime.start_run(
                    conn,
                    identity=identity,
                    request=d13_runtime.StartRunRequest(
                        goal=str(body.get("goal") or ""),
                        trigger_type=str(body.get("trigger_type") or "USER_REQUEST"),
                        trigger_ref=body.get("trigger_ref"),
                        current_datetime=body.get("current_datetime"),
                        timezone=str(body.get("timezone") or "Asia/Shanghai"),
                        context_refs=tuple(body.get("context_refs") or ()),
                        active_order_id=body.get("active_order_id"),
                        active_order_no=body.get("active_order_no"),
                        model_provider=body.get("model_provider"),
                        model_name=body.get("model_name"),
                    ),
                )
                conn.commit()
                return result
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/api/d13/runs/{run_id}/execute")
    def execute_run(
        run_id: str,
        identity: CurrentIdentity = Depends(get_current_identity),
        x_floworder_agent_key: str | None = Header(None),
    ) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        try:
            with db() as conn:
                _require_agent_assist(conn, identity)
                result = d13_runtime.run_with_selected_model(
                    conn, run_id=run_id, identity=identity
                )
                conn.commit()
                return result
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/api/d13/runs/{run_id}/plan")
    def apply_plan(
        run_id: str,
        payload: AnyPayload,
        identity: CurrentIdentity = Depends(get_current_identity),
        x_floworder_agent_key: str | None = Header(None),
    ) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        try:
            with db() as conn:
                _require_agent_assist(conn, identity)
                result = d13_runtime.apply_model_plan(
                    conn,
                    run_id=run_id,
                    identity=identity,
                    raw_plan=payload.model_dump(),
                )
                conn.commit()
                return result
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/api/d13/runs/{run_id}/complete")
    def complete_run(
        run_id: str,
        payload: AnyPayload,
        identity: CurrentIdentity = Depends(get_current_identity),
        x_floworder_agent_key: str | None = Header(None),
    ) -> dict[str, Any]:
        _require_agent_key(x_floworder_agent_key)
        body = payload.model_dump()
        try:
            with db() as conn:
                result = d13_runtime.complete_with_response(
                    conn,
                    run_id=run_id,
                    identity=identity,
                    response=str(body.get("response") or ""),
                )
                conn.commit()
                return result
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/api/d13/runs/{run_id}/trace")
    def get_trace(
        run_id: str,
        identity: CurrentIdentity = Depends(get_current_identity),
    ) -> dict[str, Any]:
        try:
            with db() as conn:
                return d13_runtime.get_run_trace(conn, run_id=run_id, identity=identity)
        except Exception as exc:
            raise _http_error(exc) from exc

    app.include_router(router)
