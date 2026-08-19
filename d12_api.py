from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

import d10_business_action as d10
import d12_human_review as d12
from auth import CurrentIdentity, get_current_identity
from database import db


class AnyPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, d12.D12NotFoundError):
        return HTTPException(404, {"code": "D12_REVIEW_NOT_FOUND", "message": str(exc)})
    if isinstance(exc, d12.D12ForbiddenError):
        return HTTPException(403, {"code": "D12_FORBIDDEN", "message": str(exc)})
    if isinstance(exc, d12.D12StaleReview):
        return HTTPException(409, {"code": "D12_STALE_REVIEW", "message": str(exc)})
    if isinstance(exc, d12.D12ConflictError):
        return HTTPException(409, {"code": "D12_REVIEW_CONFLICT", "message": str(exc)})
    if isinstance(exc, (d12.D12StateError, d10.D10StateError, d10.D10TaskActionConflict)):
        return HTTPException(409, {"code": "D12_INVALID_STATE", "message": str(exc)})
    if isinstance(exc, d10.D10NotFoundError):
        return HTTPException(404, {"code": "D12_SOURCE_NOT_FOUND", "message": str(exc)})
    if isinstance(exc, d10.D10IdempotencyConflict):
        return HTTPException(409, {"code": "D10_IDEMPOTENCY_CONFLICT", "message": str(exc)})
    return HTTPException(500, {"code": "D12_INTERNAL_ERROR", "message": str(exc)})


def register_d12_api(app: Any) -> None:
    router = APIRouter()

    @router.get("/api/d12/policy")
    def get_policy(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        return {
            "policy_version": d12.D12_POLICY_VERSION,
            "actions": d12.ACTION_POLICY,
            "principle": "先机构，再角色，再动作权限，再审批；审批通过后仍进入D10 BusinessAction/Outbox。",
        }

    @router.post("/api/d12/reviews")
    def create_review(payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        body = payload.model_dump()
        submission = d10.BusinessActionSubmission(
            organization_id=identity.organization_id,
            task_id=str(body.get("task_id") or "").strip(),
            action_type=str(body.get("action_type") or "").strip(),
            target_type=str(body.get("target_type") or "").strip(),
            target_id=str(body.get("target_id") or "").strip(),
            payload=body.get("payload") if isinstance(body.get("payload"), dict) else {},
            idempotency_key=str(body.get("idempotency_key") or "").strip(),
            actor=identity.user_id,
            request_id=str(body.get("request_id") or f"D12-REQ-{uuid.uuid4().hex[:12].upper()}"),
            source="D12_ACTION_WORKSPACE",
            reason=body.get("reason"),
        )
        try:
            with db() as conn:
                result = d12.request_review(
                    conn,
                    d12.ReviewRequest(
                        submission=submission,
                        expires_in_hours=int(body.get("expires_in_hours") or d12.DEFAULT_REVIEW_TTL_HOURS),
                    ),
                    identity=identity,
                )
            return result
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.get("/api/d12/reviews")
    def review_queue(
        status: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=200),
        identity: CurrentIdentity = Depends(get_current_identity),
    ) -> dict[str, Any]:
        with db() as conn:
            items = d12.list_reviews(conn, identity=identity, status=status, limit=limit)
        return {"items": items, "count": len(items), "policy_version": d12.D12_POLICY_VERSION}

    @router.get("/api/d12/reviews/{review_id}")
    def review_detail(review_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        try:
            with db() as conn:
                return d12.get_review(conn, review_id=review_id, identity=identity)
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/api/d12/reviews/{review_id}/decision")
    def review_decision(review_id: str, payload: AnyPayload, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        body = payload.model_dump()
        try:
            with db() as conn:
                return d12.decide_review(
                    conn,
                    review_id=review_id,
                    identity=identity,
                    decision=str(body.get("decision") or ""),
                    note=body.get("note"),
                )
        except Exception as exc:
            raise _http_error(exc) from exc

    @router.post("/api/d12/reviews/{review_id}/submit")
    def review_submit(review_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        try:
            with db() as conn:
                return d12.submit_after_review(conn, review_id=review_id, identity=identity)
        except Exception as exc:
            raise _http_error(exc) from exc

    app.include_router(router)
