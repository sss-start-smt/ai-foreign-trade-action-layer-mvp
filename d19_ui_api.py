from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from auth import CurrentIdentity, get_current_identity, require_manager
from database import db


D19_DEMO_EXPECTED_ORDERS = 17


MANAGER_FIELDS = {
    "requested_delivery_date",
    "customer_delivery_date",
    "formal_delivery_date",
    "formal_customer_commitment",
    "customer_commitment",
}


def candidate_requires_manager(candidate: dict[str, Any] | None) -> bool:
    candidate = candidate if isinstance(candidate, dict) else {}
    fields = candidate.get("fields") if isinstance(candidate.get("fields"), list) else []
    for field in fields:
        name = str((field or {}).get("field_name") or "").strip()
        old_value = (field or {}).get("old_value")
        new_value = (field or {}).get("normalized_value")
        # A no-op mention of the current date is not itself a formal-date change.
        if name in MANAGER_FIELDS and str(old_value or "") != str(new_value or ""):
            return True
    # High business risk alone does not mean manager takeover. D14/D12 keep
    # Risk Attention separate from Governance. Manager review is required only
    # when the candidate actually changes a formal commitment / delivery field.
    return False


def confirmation_severity(candidate: dict[str, Any] | None) -> str:
    if candidate_requires_manager(candidate):
        return "HIGH"
    fields = candidate.get("fields") if isinstance(candidate, dict) and isinstance(candidate.get("fields"), list) else []
    if len(fields) >= 2:
        return "IMPORTANT"
    return "NORMAL"


def register_d19_ui_api(app: Any) -> None:
    router = APIRouter()

    @router.get("/api/d19/demo/status")
    def demo_seed_status(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        enabled = os.getenv("SEED_D19_DEMO_DATA", "false").lower() == "true"
        with db() as conn:
            count = int(conn.execute(
                "SELECT COUNT(*) FROM orders WHERE order_id LIKE 'ORD-D19-DEMO-%' AND organization_id=?",
                (identity.organization_id,),
            ).fetchone()[0] or 0)
        return {
            "enabled": enabled,
            "order_count": count,
            "expected_order_count": D19_DEMO_EXPECTED_ORDERS,
            "organization_id": identity.organization_id,
        }

    @router.post("/api/d19/demo/ensure")
    def ensure_demo_seed(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        enabled = os.getenv("SEED_D19_DEMO_DATA", "false").lower() == "true"
        if not enabled:
            return {"enabled": False, "status": "DISABLED", "order_count": 0}
        if identity.organization_id != "ORG-A":
            raise HTTPException(403, {"code": "DEMO_SEED_SCOPE_DENIED", "message": "当前组织不允许初始化 D19 演示数据"})
        try:
            from d19_demo_seed import seed_d19_demo
            result = seed_d19_demo()
        except Exception as exc:
            print(f"[d19-demo-ensure-warning] {type(exc).__name__}: {exc}")
            raise HTTPException(500, {"code": "DEMO_SEED_FAILED", "message": "演示数据初始化失败，请检查部署日志"})
        return {"enabled": True, "status": "READY", **result.to_dict()}

    @router.post("/api/d19/reviews/{review_id}/submit-manager-review")
    def submit_manager_review(review_id: str, identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        with db() as conn:
            row = conn.execute(
                "SELECT * FROM candidate_reviews WHERE review_id=? AND organization_id=?",
                (review_id, identity.organization_id),
            ).fetchone()
            if not row:
                raise HTTPException(404, {"code": "REVIEW_NOT_FOUND", "message": "候选记录不存在"})
            review = dict(row)
            if review["status"] == "APPROVAL_PENDING":
                return {"status": "APPROVAL_PENDING", "review_id": review_id}
            if review["status"] != "PENDING":
                raise HTTPException(409, {"code": "INVALID_REVIEW_STATE", "message": f"当前状态为 {review['status']}"})
            candidate = json.loads(review.get("candidate_json") or "{}")
            if not candidate_requires_manager(candidate):
                raise HTTPException(409, {"code": "MANAGER_REVIEW_NOT_REQUIRED", "message": "该事项不需要主管审批"})
            conn.execute(
                "UPDATE candidate_reviews SET status='APPROVAL_PENDING', reviewer_id=? WHERE review_id=? AND organization_id=?",
                (identity.user_id, review_id, identity.organization_id),
            )
            conn.commit()
        return {"status": "APPROVAL_PENDING", "review_id": review_id, "submitted_by": identity.user_id}

    @router.get("/api/d19/review-summary")
    def review_summary(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        from main import iso, now_cn  # local import avoids a module cycle during registration

        now = now_cn()
        with db() as conn:
            task_rows = [dict(r) for r in conn.execute(
                "SELECT * FROM tasks WHERE organization_id=?",
                (identity.organization_id,),
            ).fetchall()]
            if not identity.is_manager():
                task_rows = [r for r in task_rows if r.get("owner_user_id") == identity.user_id]

            order_ids = sorted({r.get("related_order_id") for r in task_rows if r.get("related_order_id")})
            orders: dict[str, dict[str, Any]] = {}
            if order_ids:
                placeholders = ",".join("?" for _ in order_ids)
                rows = conn.execute(f"SELECT * FROM orders WHERE order_id IN ({placeholders})", order_ids).fetchall()
                orders = {r["order_id"]: dict(r) for r in rows}

            changes = []
            for risk in conn.execute(
                "SELECT * FROM risk_signals WHERE status='OPEN' ORDER BY updated_at DESC LIMIT 8"
            ).fetchall():
                r = dict(risk)
                order = orders.get(r.get("order_id"))
                if not order:
                    order_row = conn.execute("SELECT * FROM orders WHERE order_id=?", (r.get("order_id"),)).fetchone()
                    order = dict(order_row) if order_row else None
                if not order or order.get("organization_id") != identity.organization_id:
                    continue
                if not identity.is_manager() and order.get("owner") != identity.user_id:
                    continue
                changes.append({
                    "order_id": order["order_id"],
                    "order_no": order.get("order_no"),
                    "customer_name": order.get("customer_name"),
                    "text": r.get("evidence") or r.get("risk_type") or "风险状态发生变化",
                    "risk_level": r.get("risk_level") or "none",
                })

            daily = []
            for days_back in range(4, -1, -1):
                day = (now - timedelta(days=days_back)).date().isoformat()
                count = 0
                # Business-handled proxy: task updates + confirmations/rejections on that day.
                for t in task_rows:
                    stamp = str(t.get("updated_at") or "")[:10]
                    if stamp == day:
                        count += 1
                # Count in Python for SQLite/PostgreSQL portability. On PostgreSQL
                # reviewed_at may be a TIMESTAMP, and `TIMESTAMP LIKE text` raises
                # an operator error (the Railway 500 observed during D19 smoke).
                review_rows = conn.execute(
                    "SELECT reviewed_at FROM candidate_reviews WHERE organization_id=? AND reviewed_at IS NOT NULL",
                    (identity.organization_id,),
                ).fetchall()
                review_count = sum(1 for r in review_rows if str(r["reviewed_at"] or "")[:10] == day)
                count += int(review_count or 0)
                daily.append({"date": day, "count": count})

        open_tasks = [r for r in task_rows if r.get("status") != "DONE"]
        waiting = [r for r in open_tasks if r.get("waiting_on")]
        completed_today = [r for r in task_rows if r.get("status") == "DONE" and str(r.get("updated_at") or "")[:10] == now.date().isoformat()]
        unclosed = []
        seen = set()
        for t in sorted(open_tasks, key=lambda r: str(r.get("updated_at") or ""), reverse=True):
            oid = t.get("related_order_id")
            if not oid or oid in seen:
                continue
            seen.add(oid)
            order = orders.get(oid) or {}
            unclosed.append({
                "order_id": oid,
                "order_no": order.get("order_no") or oid,
                "customer_name": order.get("customer_name"),
                "title": t.get("title") or "尚未收口",
                "waiting_on": t.get("waiting_on"),
                "risk_level": t.get("risk_level") or "none",
            })
            if len(unclosed) >= 5:
                break
        return {
            "generated_at": iso(now),
            "today_completed": len(completed_today),
            "waiting": len(waiting),
            "unclosed": len(unclosed),
            "key_changes": len(changes),
            "daily_handled": daily,
            "unclosed_orders": unclosed,
            "changes": changes[:6],
        }

    app.include_router(router)
