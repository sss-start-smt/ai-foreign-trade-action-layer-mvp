from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

import d15_durable_execution as d15
from auth import CurrentIdentity, get_current_identity, require_manager
from database import db


class AnyPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, d15.D15NotFound):
        return HTTPException(404, {"code": "D15_NOT_FOUND", "message": str(exc)})
    if isinstance(exc, d15.D15Forbidden):
        return HTTPException(403, {"code": "D15_FORBIDDEN", "message": str(exc)})
    if isinstance(exc, d15.D15StateError):
        return HTTPException(409, {"code": "D15_INVALID_STATE", "message": str(exc)})
    return HTTPException(500, {"code": "D15_INTERNAL_ERROR", "message": "Durable execution service unavailable"})


def _assert_same_org(status: dict[str, Any], identity: CurrentIdentity) -> None:
    if status.get("organization_id") != identity.organization_id:
        raise d15.D15Forbidden("Execution state belongs to another organization")


def register_d15_api(app: Any) -> None:
    router = APIRouter()

    @router.get("/api/d15/contract")
    def contract(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        # Authentication is required because this is an internal product contract,
        # even though it contains no business data.
        _ = identity
        return d15.failure_contract()

    @router.get("/api/d15/outbox/{event_id}")
    def execution_status(event_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        try:
            with db() as conn:
                status = d15.get_execution_status(conn, event_id, create=True)
                if not status:
                    raise d15.D15NotFound(f"Outbox event {event_id} not found")
                _assert_same_org(status, identity)
                return status
        except Exception as exc:
            raise _http_error(exc) from None

    @router.get("/api/d15/outbox/{event_id}/trace")
    def execution_trace(event_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        try:
            with db() as conn:
                status = d15.get_execution_status(conn, event_id, create=True)
                if not status:
                    raise d15.D15NotFound(f"Outbox event {event_id} not found")
                _assert_same_org(status, identity)
                items = d15.list_execution_trace(conn, event_id)
                return {"policy_version": d15.D15_POLICY_VERSION, "items": items, "count": len(items)}
        except Exception as exc:
            raise _http_error(exc) from None

    @router.post("/api/d15/outbox/{event_id}/reconcile")
    def reconcile(event_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        require_manager(identity)
        body = payload.model_dump()
        try:
            with db() as conn:
                status = d15.get_execution_status(conn, event_id, create=True)
                if not status:
                    raise d15.D15NotFound(f"Outbox event {event_id} not found")
                _assert_same_org(status, identity)
                return d15.reconcile_outbox_event(
                    conn,
                    event_id=event_id,
                    result=str(body.get("result") or ""),
                    actor=identity.user_id,
                    evidence_ref=str(body.get("evidence_ref") or "")[:240] or None,
                )
        except Exception as exc:
            raise _http_error(exc) from None

    @router.post("/api/d15/outbox/{event_id}/requeue")
    def requeue(event_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        require_manager(identity)
        try:
            with db() as conn:
                status = d15.get_execution_status(conn, event_id, create=True)
                if not status:
                    raise d15.D15NotFound(f"Outbox event {event_id} not found")
                _assert_same_org(status, identity)
                return d15.requeue_after_confirmed_no_effect(conn, event_id=event_id, actor=identity.user_id)
        except Exception as exc:
            raise _http_error(exc) from None

    app.include_router(router)
