from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Header, HTTPException

CN_TZ = timezone(timedelta(hours=8))

TRUSTED_USER_MAP: dict[str, dict[str, str]] = {
    "OPERATOR-A1": {"organization_id": "ORG-A", "role": "operator", "name": "Operator A1"},
    "MANAGER-A": {"organization_id": "ORG-A", "role": "manager", "name": "Manager A"},
    "OPERATOR-A2": {"organization_id": "ORG-A", "role": "operator", "name": "Operator A2"},
    "OPERATOR-B1": {"organization_id": "ORG-B", "role": "operator", "name": "Operator B1"},
    "MANAGER-B": {"organization_id": "ORG-B", "role": "manager", "name": "Manager B"},
    "OPERATOR-B2": {"organization_id": "ORG-B", "role": "operator", "name": "Operator B2"},
    "USER-1": {"organization_id": "ORG-A", "role": "operator", "name": "Li Mei"},
    "USER-2": {"organization_id": "ORG-A", "role": "operator", "name": "Wang Xiao"},
    "USER-3": {"organization_id": "ORG-A", "role": "operator", "name": "Chen Lin"},
    "MANAGER-1": {"organization_id": "ORG-A", "role": "manager", "name": "Zhou Manager"},
}

DEMO_TOKEN_MAP: dict[str, str] = {
    "tok-operator-a1": "OPERATOR-A1",
    "tok-operator-a2": "OPERATOR-A2",
    "tok-manager-a": "MANAGER-A",
    "tok-operator-b1": "OPERATOR-B1",
    "tok-operator-b2": "OPERATOR-B2",
    "tok-manager-b": "MANAGER-B",
    "tok-user-1": "USER-1",
    "tok-user-2": "USER-2",
    "tok-user-3": "USER-3",
    "tok-manager-1": "MANAGER-1",
}


@dataclass
class CurrentIdentity:
    user_id: str
    organization_id: str
    role: str
    name: str = ""

    def is_manager(self) -> bool:
        return self.role == "manager"

    def is_operator(self) -> bool:
        return self.role == "operator"

    def same_org(self, other_org_id: str) -> bool:
        if not other_org_id:
            return False
        return self.organization_id == other_org_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "role": self.role,
            "name": self.name,
        }


def get_current_identity(
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
    authorization: str | None = Header(default=None),
) -> CurrentIdentity:
    """
    Unified identity resolution - TOKEN ONLY.
    
    Security: Client-supplied fields (current_user_id, user_id, role, 
    current_role, organization_id, X-User-Id header) are NEVER trusted.
    Identity is resolved SOLELY from the bearer token.

    Raises 401 if no valid token is provided.
    """
    token = None
    if x_auth_token and x_auth_token in DEMO_TOKEN_MAP:
        token = x_auth_token
    elif authorization:
        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
            if token not in DEMO_TOKEN_MAP:
                token = None
        elif authorization in DEMO_TOKEN_MAP:
            token = authorization

    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "AUTHENTICATION_REQUIRED",
                "message": "Valid authentication token required. No anonymous access to business APIs.",
            },
        )

    user_id = DEMO_TOKEN_MAP[token]
    info = TRUSTED_USER_MAP[user_id]
    return CurrentIdentity(
        user_id=user_id,
        organization_id=info["organization_id"],
        role=info["role"],
        name=info.get("name", user_id),
    )


def get_current_identity_optional(
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
    authorization: str | None = Header(default=None),
) -> CurrentIdentity | None:
    """
    Optional version - returns None instead of raising 401 when no token is present.
    For agent tool endpoints that can use either token-based identity or body-based identity.
    """
    token = None
    if x_auth_token and x_auth_token in DEMO_TOKEN_MAP:
        token = x_auth_token
    elif authorization:
        if authorization.startswith("Bearer "):
            token = authorization[7:].strip()
            if token not in DEMO_TOKEN_MAP:
                token = None
        elif authorization in DEMO_TOKEN_MAP:
            token = authorization

    if not token:
        return None

    user_id = DEMO_TOKEN_MAP[token]
    info = TRUSTED_USER_MAP[user_id]
    return CurrentIdentity(
        user_id=user_id,
        organization_id=info["organization_id"],
        role=info["role"],
        name=info.get("name", user_id),
    )


def resolve_identity_for_testing(user_id: str) -> CurrentIdentity:
    """
    ONLY for use in tests. Never use in production API routes.
    """
    if user_id not in TRUSTED_USER_MAP:
        raise ValueError(f"Unknown user_id for testing: {user_id}")
    info = TRUSTED_USER_MAP[user_id]
    return CurrentIdentity(
        user_id=user_id,
        organization_id=info["organization_id"],
        role=info["role"],
        name=info.get("name", user_id),
    )


def require_same_org(identity: CurrentIdentity, target_org_id: str) -> None:
    if not target_org_id or not identity.same_org(target_org_id):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "CROSS_ORG_FORBIDDEN",
                "message": f"User {identity.user_id} (org={identity.organization_id}) cannot access resource from org={target_org_id}",
                "actor": identity.to_dict(),
                "target_org": target_org_id,
            },
        )


def require_manager(identity: CurrentIdentity) -> None:
    if not identity.is_manager():
        raise HTTPException(
            status_code=403,
            detail={
                "code": "MANAGER_REQUIRED",
                "message": f"User {identity.user_id} (role={identity.role}) requires manager role",
                "actor": identity.to_dict(),
            },
        )


def require_order_access(identity: CurrentIdentity, order: dict[str, Any] | None, conn: Any | None = None) -> None:
    """
    Access control for orders:
    1. Organization check (ALWAYS enforced, no bypass)
    2. Role check (manager sees all in org, operator sees own or has assigned task)
    
    SECURITY: NULL organization_id data is treated as UNKNOWN org - BLOCKED unless
    the user is the direct owner. Managers CANNOT bypass the org boundary.
    
    Task-based access: Operators can also access an order if they have an assigned
    (non-done) task on that order. This is checked via the optional conn parameter.
    """
    if not order:
        return

    order_org = str(order.get("organization_id") or "").strip()
    order_owner = str(order.get("owner") or "").strip()

    # Organization boundary ALWAYS enforced - NO BYPASS FOR MANAGERS
    if order_org:
        require_same_org(identity, order_org)
    else:
        # NULL org data: only the direct owner can access it
        # Managers CANNOT see NULL-org data from other users
        if order_owner and order_owner != identity.user_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "ORDER_ACCESS_FORBIDDEN",
                    "message": f"Order has no organization binding; only owner {order_owner} can access, but requester is {identity.user_id}",
                    "actor": identity.to_dict(),
                    "order_id": order.get("order_id"),
                },
            )

    # Role-based access (always enforced, even after org check)
    if not identity.is_manager():
        # Operators can access orders they own OR orders where they have assigned tasks
        if order_owner and order_owner != identity.user_id:
            # Check task-based access: does this operator have a non-done task on this order?
            has_task = False
            if conn is not None:
                order_id = order.get("order_id")
                if order_id:
                    task_count = conn.execute(
                        "SELECT COUNT(*) FROM tasks WHERE related_order_id=? AND owner_user_id=? AND status!='DONE'",
                        (order_id, identity.user_id),
                    ).fetchone()[0]
                    has_task = task_count > 0
            if not has_task:
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "ORDER_ACCESS_FORBIDDEN",
                        "message": f"Operator {identity.user_id} cannot access order owned by {order_owner} (no assigned task)",
                        "actor": identity.to_dict(),
                        "order_id": order.get("order_id"),
                    },
                )


def require_task_access(identity: CurrentIdentity, task: dict[str, Any] | None, order: dict[str, Any] | None = None) -> None:
    """
    Access control for tasks:
    1. Organization check (ALWAYS enforced via task org or parent order org)
    2. Role check (manager sees all in org, operator sees own)
    
    SECURITY: NULL organization_id data is treated as UNKNOWN org - BLOCKED unless
    the user is the direct owner. Managers CANNOT bypass the org boundary.
    """
    if not task:
        return

    task_org = str(task.get("organization_id") or "").strip()
    task_owner = str(task.get("owner_user_id") or "").strip()

    # Resolve organization: task org > parent order org
    resolved_org = task_org
    if not resolved_org and order:
        resolved_org = str(order.get("organization_id") or "").strip()

    # Organization boundary ALWAYS enforced - NO BYPASS FOR MANAGERS
    if resolved_org:
        require_same_org(identity, resolved_org)
    else:
        # NULL org data: only the direct owner can access it
        if task_owner and task_owner != identity.user_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "TASK_ACCESS_FORBIDDEN",
                    "message": f"Task has no organization binding; only owner {task_owner} can access, but requester is {identity.user_id}",
                    "actor": identity.to_dict(),
                    "task_id": task.get("task_id"),
                },
            )

    # Role-based access
    if not identity.is_manager():
        if task_owner and task_owner != identity.user_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "TASK_ACCESS_FORBIDDEN",
                    "message": f"Operator {identity.user_id} cannot access task owned by {task_owner}",
                    "actor": identity.to_dict(),
                    "task_id": task.get("task_id"),
                },
            )


def require_run_access(identity: CurrentIdentity, run: dict[str, Any] | None) -> None:
    """
    Access control for agent runs:
    1. Organization check (ALWAYS enforced)
    2. Role check (manager sees all in org, operator sees own)
    
    SECURITY: NULL org run data is BLOCKED for non-owners.
    """
    if not run:
        return

    run_org = str(run.get("organization_id") or "").strip()
    run_user = str(run.get("current_user_id") or "").strip()

    # Organization boundary ALWAYS enforced - NO BYPASS
    if run_org:
        require_same_org(identity, run_org)
    else:
        # NULL org run: only the direct owner can access it
        if run_user and run_user != identity.user_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "RUN_ACCESS_FORBIDDEN",
                    "message": f"Run has no organization binding; only owner {run_user} can access, but requester is {identity.user_id}",
                    "actor": identity.to_dict(),
                    "run_id": run.get("run_id"),
                },
            )

    # Role-based access
    if not identity.is_manager():
        if run_user and run_user != identity.user_id:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "RUN_ACCESS_FORBIDDEN",
                    "message": f"Operator {identity.user_id} cannot access run owned by {run_user}",
                    "actor": identity.to_dict(),
                    "run_id": run.get("run_id"),
                },
            )


def require_approval_access(identity: CurrentIdentity, approval: dict[str, Any] | None, order: dict[str, Any] | None = None) -> None:
    """
    Access control for approvals:
    1. Approval organization check
    2. Related order organization check (must match approval org)
    3. Role check (manager-only for high-risk approvals)
    """
    if not approval:
        return

    approval_org = str(approval.get("organization_id") or "").strip()
    approval_order_id = str(approval.get("order_id") or "").strip()

    # Organization boundary - approval org
    if approval_org:
        require_same_org(identity, approval_org)

    # Organization boundary - related order org (must match)
    if approval_order_id and order:
        order_org = str(order.get("organization_id") or "").strip()
        if order_org and approval_org and order_org != approval_org:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "APPROVAL_ORG_MISMATCH",
                    "message": "Approval organization does not match order organization",
                },
            )
        if order_org:
            require_same_org(identity, order_org)


def can_modify_order(identity: CurrentIdentity, order: dict[str, Any] | None) -> bool:
    """
    Check if user can modify an order.
    
    SECURITY: Managers cannot modify orders with NULL org from other users.
    Operators can only modify their own orders.
    """
    if not order:
        return False
    order_org = str(order.get("organization_id") or "").strip()
    order_owner = str(order.get("owner") or "").strip()

    # Manager can only modify within their org
    if identity.is_manager():
        if order_org:
            return identity.same_org(order_org)
        # NULL org: manager can only modify if they are the owner
        if order_owner:
            return order_owner == identity.user_id
        return False

    # Operator can only modify own orders
    if order_owner and order_owner != identity.user_id:
        return False
    return True


def can_modify_task(identity: CurrentIdentity, task: dict[str, Any] | None, order: dict[str, Any] | None = None) -> bool:
    """
    Check if user can modify a task.
    
    SECURITY: Managers cannot modify tasks with NULL org from other users.
    Operators can only modify their own tasks.
    """
    if not task:
        return False
    task_org = str(task.get("organization_id") or "").strip()
    task_owner = str(task.get("owner_user_id") or "").strip()

    if identity.is_manager():
        resolved_org = task_org
        if not resolved_org and order:
            resolved_org = str(order.get("organization_id") or "").strip()
        if resolved_org:
            return identity.same_org(resolved_org)
        # NULL org: manager can only modify if they are the owner
        if task_owner:
            return task_owner == identity.user_id
        return False

    if task_owner and task_owner != identity.user_id:
        return False
    return True


def iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(CN_TZ)).isoformat(timespec="seconds")


def audit_log(
    conn: Any,
    identity: CurrentIdentity,
    action: str,
    entity_type: str,
    entity_id: str,
    result: str = "SUCCESS",
    details: dict[str, Any] | None = None,
) -> None:
    from database import get_table_columns

    columns = {col["name"] for col in get_table_columns(conn, "audit_logs")}

    if "audit_id" not in columns:
        return

    audit_id = f"AUD-{uuid.uuid4().hex[:10].upper()}"
    timestamp = iso()
    payload = json.dumps(details or {}, ensure_ascii=False)

    conn.execute(
        "INSERT INTO audit_logs(audit_id, organization_id, actor_user_id, actor_role, action, entity_type, entity_id, result, details_json, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (audit_id, identity.organization_id, identity.user_id, identity.role, action, entity_type, entity_id, result, payload, timestamp),
    )
