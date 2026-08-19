from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from auth import CurrentIdentity, DEMO_TOKEN_MAP, TRUSTED_USER_MAP, get_current_identity


class AnyPayload(BaseModel):
    model_config = ConfigDict(extra="allow")


# D19 Shadow login aliases. The business APIs still trust only the existing
# token->identity mapping in auth.py; this route only turns a demo credential
# into one of those already-controlled tokens.
LOGIN_ACCOUNTS: dict[str, dict[str, str]] = {
    "limin": {
        "user_id": "USER-1",
        "display_name": "李敏",
        "ui_role": "operator",
        "role_label": "跟单员",
        "avatar": "李",
    },
    "manager": {
        "user_id": "MANAGER-1",
        "display_name": "王主管",
        "ui_role": "manager",
        "role_label": "主管",
        "avatar": "王",
    },
    # Admin is intentionally a UI profile over the manager identity for this
    # internal demo. Server-side protected settings remain manager-gated.
    "admin": {
        "user_id": "MANAGER-1",
        "display_name": "系统管理员",
        "ui_role": "admin",
        "role_label": "管理员",
        "avatar": "管",
    },
}


def _demo_password() -> str:
    return (os.getenv("FLOWORDER_DEMO_PASSWORD") or "demo123").strip()


def _token_for_user(user_id: str) -> str:
    for token, mapped_user in DEMO_TOKEN_MAP.items():
        if mapped_user == user_id:
            return token
    raise RuntimeError(f"No demo token mapped for {user_id}")


def register_d19_auth_api(app: Any) -> None:
    router = APIRouter()

    @router.post("/auth/login")
    def login(payload: AnyPayload) -> dict[str, Any]:
        body = payload.model_dump()
        username = str(body.get("username") or "").strip().lower()
        password = str(body.get("password") or "")
        account = LOGIN_ACCOUNTS.get(username)
        if not account or not hmac.compare_digest(password, _demo_password()):
            raise HTTPException(
                status_code=401,
                detail={"code": "INVALID_CREDENTIALS", "message": "账号或密码不正确"},
            )
        user_id = account["user_id"]
        trusted = TRUSTED_USER_MAP[user_id]
        return {
            "access_token": _token_for_user(user_id),
            "token_type": "demo-token",
            "identity": {
                "user_id": user_id,
                "organization_id": trusted["organization_id"],
                "role": trusted["role"],
                "name": trusted.get("name", user_id),
            },
            "profile": {
                "username": username,
                "display_name": account["display_name"],
                "ui_role": account["ui_role"],
                "role_label": account["role_label"],
                "avatar": account["avatar"],
            },
            "boundary": "D19 internal demo credential exchange; business APIs remain token/RBAC enforced.",
        }

    @router.get("/auth/me")
    def me(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        return {"identity": identity.to_dict()}

    app.include_router(router)
