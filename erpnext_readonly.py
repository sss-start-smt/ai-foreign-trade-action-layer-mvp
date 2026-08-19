from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query

from database import db, table_exists
from auth import (
    CurrentIdentity,
    get_current_identity,
    require_manager,
    require_same_org,
)

_log = logging.getLogger("erpnext_readonly")
CN_TZ = timezone(timedelta(hours=8))

DOCTYPES = ("Sales Order", "Customer", "Item")
SYNC_MODES = ("full", "incremental")
FRESHNESS_STATUSES = ("NEVER_SYNCED", "FRESH", "STALE", "UNAVAILABLE")


def _now_iso() -> str:
    return datetime.now(CN_TZ).isoformat(timespec="seconds")


def _iso_to_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ERPNextConfig:
    def __init__(self) -> None:
        self.base_url: str = os.getenv("ERPNEXT_BASE_URL", "").rstrip("/")
        self.api_key: str = os.getenv("ERPNEXT_API_KEY", "").strip()
        self.api_secret: str = os.getenv("ERPNEXT_API_SECRET", "").strip()
        self.organization_id: str = os.getenv("ERPNEXT_ORGANIZATION_ID", "").strip()
        self.stale_after_seconds: int = int(os.getenv("ERPNEXT_STALE_AFTER_SECONDS", "3600"))
        self.page_size: int = int(os.getenv("ERPNEXT_PAGE_SIZE", "50"))
        self.timeout_seconds: int = int(os.getenv("ERPNEXT_TIMEOUT_SECONDS", "30"))

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.api_secret)

    @property
    def auth_header(self) -> str:
        if not self.api_key or not self.api_secret:
            return ""
        return f"token {self.api_key}:{self.api_secret}"

    def mask(self) -> dict[str, Any]:
        return {
            "base_url": self.base_url or None,
            "organization_id": self.organization_id or None,
            "stale_after_seconds": self.stale_after_seconds,
            "page_size": self.page_size,
            "timeout_seconds": self.timeout_seconds,
            "configured": self.configured,
        }


CONFIG = ERPNextConfig()


class ERPNextReadOnlyClient:
    """GET-only REST client for ERPNext. No POST/PUT/PATCH/DELETE."""

    def __init__(self, base_url: str | None = None, auth_header: str | None = None, timeout: int | None = None) -> None:
        self._base_url = (base_url or CONFIG.base_url).rstrip("/")
        self._auth_header = auth_header or CONFIG.auth_header
        self._timeout = timeout or CONFIG.timeout_seconds

    @property
    def base_url(self) -> str:
        return self._base_url

    def _get_headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self._auth_header:
            headers["Authorization"] = self._auth_header
        return headers

    def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._base_url:
            raise ERPNextClientError("ERPNEXT_BASE_URL not configured", code="NOT_CONFIGURED")
        full_url = f"{self._base_url}{url}"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(full_url, headers=self._get_headers(), params=params)
        except httpx.TimeoutException:
            raise ERPNextClientError(f"Timeout fetching {url}", code="TIMEOUT")
        except httpx.ConnectError as exc:
            raise ERPNextClientError(f"Cannot connect to ERPNext: {exc}", code="CONNECTION_ERROR")
        except httpx.HTTPError as exc:
            raise ERPNextClientError(f"HTTP error: {exc}", code="HTTP_ERROR")

        if resp.status_code == 401:
            raise ERPNextClientError("ERPNext authentication failed (401)", code="AUTH_FAILED", status_code=401)
        if resp.status_code == 403:
            raise ERPNextClientError("ERPNext permission denied (403)", code="PERMISSION_DENIED", status_code=403)
        if resp.status_code >= 500:
            raise ERPNextClientError(f"ERPNext server error ({resp.status_code})", code="SERVER_ERROR", status_code=resp.status_code)
        if resp.status_code >= 400:
            raise ERPNextClientError(f"ERPNext client error ({resp.status_code}): {resp.text[:200]}", code="CLIENT_ERROR", status_code=resp.status_code)

        try:
            return resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise ERPNextClientError(f"Malformed JSON response from {url}: {exc}", code="MALFORMED_JSON")

    def get_list(self, doctype: str, fields: list[str], filters: list[list[Any]] | None = None,
                 limit_start: int = 0, limit_page_length: int = 50, order_by: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "fields": json.dumps(fields),
            "limit_start": limit_start,
            "limit_page_length": limit_page_length,
        }
        if filters:
            params["filters"] = json.dumps(filters)
        if order_by:
            params["order_by"] = order_by
        return self._get(f"/api/resource/{doctype}", params=params)

    def get_doc(self, doctype: str, name: str) -> dict[str, Any]:
        """Fetch a complete single-document via Frappe GET path.

        No ``fields`` filter is sent — the full document is retrieved and the
        ``ERPNextNormalizer`` is responsible for extracting the fields used by
        FlowOrder. The document ``name`` is URL-encoded so values containing
        spaces or slashes remain safe.
        """
        safe_name = urllib.parse.quote(name, safe="")
        return self._get(f"/api/resource/{doctype}/{safe_name}")


class ERPNextClientError(Exception):
    def __init__(self, message: str, code: str = "UNKNOWN", status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


_SALES_ORDER_LIST_FIELDS = [
    "name", "customer", "delivery_date", "status", "docstatus",
    "transaction_date", "modified", "owner",
]
_SALES_ORDER_DETAIL_FIELDS = [
    "name", "customer", "delivery_date", "status", "docstatus",
    "transaction_date", "modified", "owner", "items",
]
_CUSTOMER_FIELDS = ["name", "customer_name", "customer_group", "territory", "modified"]
_ITEM_FIELDS = ["name", "item_code", "item_name", "stock_uom", "modified"]

_DOCTYPE_LIST_FIELDS: dict[str, list[str]] = {
    "Sales Order": _SALES_ORDER_LIST_FIELDS,
    "Customer": _CUSTOMER_FIELDS,
    "Item": _ITEM_FIELDS,
}

_DOCTYPE_DETAIL_FIELDS: dict[str, list[str]] = {
    "Sales Order": _SALES_ORDER_DETAIL_FIELDS,
    "Customer": _CUSTOMER_FIELDS,
    "Item": _ITEM_FIELDS,
}

_ORDER_BY: dict[str, str] = {
    "Sales Order": "modified asc, name asc",
    "Customer": "modified asc, name asc",
    "Item": "modified asc, name asc",
}


class ERPNextNormalizer:
    @staticmethod
    def normalize(doctype: str, raw: dict[str, Any]) -> dict[str, Any]:
        if doctype == "Sales Order":
            return ERPNextNormalizer._normalize_sales_order(raw)
        elif doctype == "Customer":
            return ERPNextNormalizer._normalize_customer(raw)
        elif doctype == "Item":
            return ERPNextNormalizer._normalize_item(raw)
        raise ValueError(f"Unknown doctype: {doctype}")

    @staticmethod
    def _normalize_sales_order(raw: dict[str, Any]) -> dict[str, Any]:
        items_raw = raw.get("items") or []
        normalized_items: list[dict[str, Any]] = []
        for item in items_raw:
            if not isinstance(item, dict):
                continue
            normalized_items.append({
                "source_line_key": item.get("name") or "",
                "item_code": item.get("item_code") or "",
                "item_name": item.get("item_name") or "",
                "qty": item.get("qty"),
                "delivered_qty": item.get("delivered_qty"),
                "delivery_date": item.get("delivery_date"),
                "rate": item.get("rate"),
                "amount": item.get("amount"),
            })
        return {
            "external_id": raw.get("name") or "",
            "external_order_id": raw.get("name") or "",
            "source_order_key": raw.get("name") or "",
            "customer_external_id": raw.get("customer") or "",
            "customer_due_date": raw.get("delivery_date"),
            "order_status": raw.get("status") or "",
            "docstatus": raw.get("docstatus"),
            "transaction_date": raw.get("transaction_date"),
            "source_modified_at": raw.get("modified") or "",
            "erp_owner": raw.get("owner") or "",
            "items": normalized_items,
        }

    @staticmethod
    def _normalize_customer(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "external_id": raw.get("name") or "",
            "customer_name": raw.get("customer_name") or "",
            "customer_group": raw.get("customer_group") or "",
            "territory": raw.get("territory") or "",
            "source_modified_at": raw.get("modified") or "",
        }

    @staticmethod
    def _normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
        return {
            "external_id": raw.get("name") or "",
            "item_code": raw.get("item_code") or "",
            "item_name": raw.get("item_name") or "",
            "stock_uom": raw.get("stock_uom") or "",
            "source_modified_at": raw.get("modified") or "",
        }


def _ensure_erp_schema(conn: Any) -> None:
    if getattr(conn, "is_pg", False):
        missing = []
        if not table_exists(conn, "erp_sync_state"):
            missing.append("erp_sync_state")
        if not table_exists(conn, "erp_read_snapshots"):
            missing.append("erp_read_snapshots")
        if missing:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "MIGRATION_REQUIRED",
                    "message": f"ERPNext D6 tables missing: {', '.join(missing)}. Run 'alembic upgrade head' to create them.",
                },
            )
        return

    if not table_exists(conn, "erp_sync_state"):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS erp_sync_state (
                organization_id TEXT NOT NULL,
                doctype TEXT NOT NULL,
                last_success_cursor TEXT,
                last_success_at TEXT,
                last_attempt_at TEXT,
                sync_status TEXT NOT NULL DEFAULT 'NEVER_SYNCED',
                last_error_code TEXT,
                records_seen INTEGER NOT NULL DEFAULT 0,
                records_changed INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (organization_id, doctype)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_erp_sync_state_org ON erp_sync_state(organization_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_erp_sync_state_status ON erp_sync_state(sync_status)")

    if not table_exists(conn, "erp_read_snapshots"):
        conn.execute("""
            CREATE TABLE IF NOT EXISTS erp_read_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL,
                doctype TEXT NOT NULL,
                external_id TEXT NOT NULL,
                source_modified_at TEXT,
                normalized_json TEXT NOT NULL,
                raw_sha256 TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (organization_id, doctype, external_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_erp_snapshots_org ON erp_read_snapshots(organization_id, doctype)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_erp_snapshots_external ON erp_read_snapshots(doctype, external_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_erp_snapshots_fetched ON erp_read_snapshots(fetched_at)")


def _get_sync_state(conn: Any, org_id: str, doctype: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM erp_sync_state WHERE organization_id=? AND doctype=?",
        (org_id, doctype),
    ).fetchone()
    return dict(row) if row else None


def _upsert_sync_state(conn: Any, org_id: str, doctype: str, **kwargs: Any) -> None:
    existing = _get_sync_state(conn, org_id, doctype)
    ts = _now_iso()
    if existing:
        sets = []
        vals: list[Any] = []
        for key, val in kwargs.items():
            sets.append(f"{key}=?")
            vals.append(val)
        sets.append("updated_at=?")
        vals.append(ts)
        vals.extend([org_id, doctype])
        conn.execute(
            f"UPDATE erp_sync_state SET {','.join(sets)} WHERE organization_id=? AND doctype=?",
            vals,
        )
    else:
        cols = ["organization_id", "doctype"] + list(kwargs.keys()) + ["updated_at"]
        vals = [org_id, doctype] + list(kwargs.values()) + [ts]
        placeholders = ", ".join(["?"] * len(cols))
        conn.execute(
            f"INSERT INTO erp_sync_state({','.join(cols)}) VALUES({placeholders})",
            vals,
        )


def _upsert_snapshot(conn: Any, org_id: str, doctype: str, external_id: str,
                     source_modified_at: str | None, normalized: dict[str, Any],
                     raw_sha256: str, fetched_at: str) -> tuple[str, bool]:
    existing = conn.execute(
        "SELECT snapshot_id, raw_sha256 FROM erp_read_snapshots WHERE organization_id=? AND doctype=? AND external_id=?",
        (org_id, doctype, external_id),
    ).fetchone()
    if existing and existing["raw_sha256"] == raw_sha256:
        snapshot_id = existing["snapshot_id"]
        conn.execute(
            "UPDATE erp_read_snapshots SET fetched_at=?, updated_at=? WHERE snapshot_id=?",
            (fetched_at, _now_iso(), snapshot_id),
        )
        return snapshot_id, False
    snapshot_id = existing["snapshot_id"] if existing else f"ERP-SNAP-{uuid.uuid4().hex[:10].upper()}"
    normalized_json = json.dumps(normalized, ensure_ascii=False)
    if existing:
        conn.execute(
            """UPDATE erp_read_snapshots
               SET source_modified_at=?, normalized_json=?, raw_sha256=?, fetched_at=?, updated_at=?
               WHERE snapshot_id=?""",
            (source_modified_at, normalized_json, raw_sha256, fetched_at, _now_iso(), snapshot_id),
        )
    else:
        ts = _now_iso()
        conn.execute(
            """INSERT INTO erp_read_snapshots
               (snapshot_id, organization_id, doctype, external_id, source_modified_at,
                normalized_json, raw_sha256, fetched_at, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (snapshot_id, org_id, doctype, external_id, source_modified_at,
             normalized_json, raw_sha256, fetched_at, ts, ts),
        )
    return snapshot_id, True


def compute_freshness(sync_state: dict[str, Any] | None) -> str:
    if not sync_state:
        return "NEVER_SYNCED"
    status = sync_state.get("sync_status", "")
    if status in ("UNAVAILABLE", "STALE"):
        return status
    if status == "NEVER_SYNCED":
        return "NEVER_SYNCED"
    if status == "FRESH":
        last_success = _iso_to_dt(sync_state.get("last_success_at"))
        if not last_success:
            return "NEVER_SYNCED"
        now = datetime.now(CN_TZ)
        elapsed = (now - last_success).total_seconds()
        stale_threshold = CONFIG.stale_after_seconds
        if elapsed > stale_threshold:
            return "STALE"
        return "FRESH"
    return status or "NEVER_SYNCED"


def compute_global_freshness(states: list[dict[str, Any] | None]) -> str:
    if not states or all(s is None for s in states):
        return "NEVER_SYNCED"
    freshnesses = [compute_freshness(s) for s in states]
    if "UNAVAILABLE" in freshnesses:
        return "UNAVAILABLE"
    if "STALE" in freshnesses:
        return "STALE"
    if "NEVER_SYNCED" in freshnesses:
        return "NEVER_SYNCED"
    return "FRESH"


class ERPReadSyncService:
    def __init__(self, client: ERPNextReadOnlyClient | None = None) -> None:
        self._client = client or ERPNextReadOnlyClient()

    def full_sync(self, org_id: str, identity: CurrentIdentity) -> dict[str, Any]:
        return self._do_sync(org_id, identity, mode="full")

    def incremental_sync(self, org_id: str, identity: CurrentIdentity) -> dict[str, Any]:
        return self._do_sync(org_id, identity, mode="incremental")

    def _do_sync(self, org_id: str, identity: CurrentIdentity, mode: str) -> dict[str, Any]:
        _require_manager_or_admin(identity)
        _require_org_binding(identity)

        results: dict[str, Any] = {}
        overall_success = True
        total_seen = 0
        total_changed = 0

        for doctype in DOCTYPES:
            try:
                seen, changed = self._sync_doctype(org_id, doctype, mode)
                results[doctype] = {
                    "status": "SUCCESS",
                    "records_seen": seen,
                    "records_changed": changed,
                }
                total_seen += seen
                total_changed += changed
            except ERPNextClientError as exc:
                overall_success = False
                results[doctype] = {
                    "status": "FAILED",
                    "error_code": exc.code,
                    "error_message": str(exc),
                }
                _log.warning("ERPNext sync failed for %s/%s: %s", org_id, doctype, exc)
                is_unavailable = exc.code in ("AUTH_FAILED", "PERMISSION_DENIED", "NOT_CONFIGURED")
                self._write_failure_state(org_id, doctype, is_unavailable, exc.code)
            except Exception as exc:
                overall_success = False
                results[doctype] = {
                    "status": "FAILED",
                    "error_code": "INTERNAL_ERROR",
                    "error_message": str(exc),
                }
                _log.exception("Unexpected error syncing %s/%s", org_id, doctype)
                self._write_failure_state(org_id, doctype, False, "INTERNAL_ERROR")

        return {
            "mode": mode,
            "overall_status": "SUCCESS" if overall_success else "PARTIAL_FAILURE",
            "doctypes": results,
            "total_records_seen": total_seen,
            "total_records_changed": total_changed,
            "freshness": self._compute_freshness(org_id),
        }

    def _write_failure_state(self, org_id: str, doctype: str, is_unavailable: bool, error_code: str) -> None:
        with db() as conn:
            try:
                _ensure_erp_schema(conn)
            except HTTPException:
                _log.warning("Cannot write failure state: D6 tables missing in PG")
                return
            try:
                existing = _get_sync_state(conn, org_id, doctype)
                if is_unavailable:
                    sync_status = "UNAVAILABLE"
                elif existing and existing.get("last_success_at"):
                    sync_status = "STALE"
                else:
                    sync_status = "UNAVAILABLE"
                _upsert_sync_state(conn, org_id, doctype,
                    sync_status=sync_status,
                    last_error_code=error_code,
                    last_attempt_at=_now_iso(),
                )
                conn.commit()
            except Exception:
                _log.exception("Failed to write failure state for %s/%s", org_id, doctype)

    def _compute_freshness(self, org_id: str) -> str:
        states = []
        with db() as conn:
            for dt in DOCTYPES:
                states.append(_get_sync_state(conn, org_id, dt))
        return compute_global_freshness(states)

    def _sync_doctype(self, org_id: str, doctype: str, mode: str) -> tuple[int, int]:
        list_fields = _DOCTYPE_LIST_FIELDS.get(doctype, [])
        order_by = _ORDER_BY.get(doctype)
        page_sz = CONFIG.page_size

        if mode == "incremental":
            with db() as conn:
                existing = _get_sync_state(conn, org_id, doctype)
            cursor = existing.get("last_success_cursor") if existing else None
            filters: list[list[Any]] | None = None
            if cursor:
                filters = [["modified", ">=", cursor]]
            else:
                mode = "full"

        if mode == "full":
            filters = None

        all_records: list[dict[str, Any]] = []
        start = 0
        while True:
            data = self._client.get_list(doctype, list_fields, filters=filters,
                                          limit_start=start, limit_page_length=page_sz,
                                          order_by=order_by)
            records = data.get("data", [])
            if not records:
                break
            all_records.extend(records)
            if len(records) < page_sz:
                break
            start += page_sz

        changed = 0
        seen = len(all_records)
        now_ts = _now_iso()
        with db() as conn:
            _ensure_erp_schema(conn)

            for raw in all_records:
                if not isinstance(raw, dict):
                    continue
                external_id = raw.get("name") or ""
                if not external_id:
                    continue

                if doctype == "Sales Order":
                    full_detail = self._client.get_doc(doctype, external_id)
                    if isinstance(full_detail, dict) and "data" in full_detail:
                        doc_raw = full_detail["data"]
                    else:
                        doc_raw = full_detail
                    if not isinstance(doc_raw, dict):
                        doc_raw = {"name": external_id}
                    normalized = ERPNextNormalizer._normalize_sales_order(doc_raw)
                    raw_json = json.dumps(doc_raw, ensure_ascii=False, sort_keys=True)
                else:
                    normalized = ERPNextNormalizer.normalize(doctype, raw)
                    raw_json = json.dumps(raw, ensure_ascii=False, sort_keys=True)

                raw_hash = _sha256(raw_json)
                source_modified_at = normalized.get("source_modified_at")
                _, is_changed = _upsert_snapshot(conn, org_id, doctype, external_id,
                                                  source_modified_at, normalized, raw_hash, now_ts)
                if is_changed:
                    changed += 1

            cursor = self._compute_cursor(conn, org_id, doctype, mode)
            _upsert_sync_state(conn, org_id, doctype,
                last_success_cursor=cursor,
                last_success_at=now_ts,
                last_attempt_at=now_ts,
                sync_status="FRESH",
                last_error_code=None,
                records_seen=seen,
                records_changed=changed,
            )
            conn.commit()

        return seen, changed

    def _compute_cursor(self, conn: Any, org_id: str, doctype: str, mode: str) -> str | None:
        row = conn.execute(
            "SELECT MAX(source_modified_at) as max_mod FROM erp_read_snapshots WHERE organization_id=? AND doctype=?",
            (org_id, doctype),
        ).fetchone()
        if row and row["max_mod"]:
            return str(row["max_mod"])
        existing = _get_sync_state(conn, org_id, doctype)
        if existing and existing.get("last_success_cursor"):
            return existing["last_success_cursor"]
        return None

    def get_status(self, org_id: str) -> dict[str, Any]:
        with db() as conn:
            try:
                _ensure_erp_schema(conn)
            except HTTPException:
                return {
                    "organization_id": org_id,
                    "freshness": "NEVER_SYNCED",
                    "configured": CONFIG.configured,
                    "stale_after_seconds": CONFIG.stale_after_seconds,
                    "doctypes": {
                        dt: {
                            "sync_status": "NEVER_SYNCED",
                            "last_success_at": None,
                            "last_attempt_at": None,
                            "last_error_code": "MIGRATION_REQUIRED",
                            "last_success_cursor": None,
                            "records_seen": 0,
                            "records_changed": 0,
                            "freshness": "NEVER_SYNCED",
                            "snapshot_count": 0,
                            "last_fetched_at": None,
                        }
                        for dt in DOCTYPES
                    },
                    "config": CONFIG.mask(),
                }

            states: dict[str, dict[str, Any] | None] = {}
            for doctype in DOCTYPES:
                states[doctype] = _get_sync_state(conn, org_id, doctype)

            snapshots_summary: dict[str, dict[str, Any]] = {}
            for doctype in DOCTYPES:
                row = conn.execute(
                    "SELECT COUNT(*) as cnt, MAX(fetched_at) as last_fetched FROM erp_read_snapshots WHERE organization_id=? AND doctype=?",
                    (org_id, doctype),
                ).fetchone()
                snapshots_summary[doctype] = {
                    "snapshot_count": row["cnt"] if row else 0,
                    "last_fetched_at": row["last_fetched"] if row else None,
                }

        global_freshness = compute_global_freshness(list(states.values()))
        return {
            "organization_id": org_id,
            "freshness": global_freshness,
            "configured": CONFIG.configured,
            "stale_after_seconds": CONFIG.stale_after_seconds,
            "doctypes": {
                dt: {
                    "sync_status": (s or {}).get("sync_status", "NEVER_SYNCED"),
                    "last_success_at": (s or {}).get("last_success_at"),
                    "last_attempt_at": (s or {}).get("last_attempt_at"),
                    "last_error_code": (s or {}).get("last_error_code"),
                    "last_success_cursor": (s or {}).get("last_success_cursor"),
                    "records_seen": (s or {}).get("records_seen", 0),
                    "records_changed": (s or {}).get("records_changed", 0),
                    "freshness": compute_freshness(s),
                    "snapshot_count": snapshots_summary[dt]["snapshot_count"],
                    "last_fetched_at": snapshots_summary[dt]["last_fetched_at"],
                }
                for dt, s in states.items()
            },
            "config": CONFIG.mask(),
        }


def _require_manager_or_admin(identity: CurrentIdentity) -> None:
    if identity.is_manager():
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "MANAGER_REQUIRED",
            "message": "ERPNext sync requires Manager or Admin role",
            "actor": identity.to_dict(),
        },
    )


def _require_org_binding(identity: CurrentIdentity) -> None:
    bound = CONFIG.organization_id
    if not bound:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "ERPNEXT_ORG_NOT_BOUND",
                "message": "ERPNext integration requires ERPNEXT_ORGANIZATION_ID binding.",
            },
        )
    if identity.organization_id != bound:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "ERP_ORG_MISMATCH",
                "message": f"ERPNext is bound to organization '{bound}'. User organization '{identity.organization_id}' cannot sync.",
                "bound_org_id": bound,
                "user_org_id": identity.organization_id,
            },
        )


_service = ERPReadSyncService()


def register_erpnext_routes(app: FastAPI) -> None:
    if getattr(app.state, "erpnext_routes_registered", False):
        return
    app.state.erpnext_routes_registered = True

    @app.get("/api/integrations/erpnext/status")
    def erpnext_status(identity: CurrentIdentity = Depends(get_current_identity)) -> dict[str, Any]:
        return _service.get_status(identity.organization_id)

    @app.post("/api/integrations/erpnext/sync")
    def erpnext_sync(
        mode: str = Query("incremental", pattern="^(full|incremental)$"),
        identity: CurrentIdentity = Depends(get_current_identity),
    ) -> dict[str, Any]:
        _require_manager_or_admin(identity)
        _require_org_binding(identity)
        # D16 pilot/kill-switch: disabling sync never deletes snapshots; it only blocks new reads.
        import d16_observability as d16
        with db() as conn:
            flag = d16.resolve_feature_flag(
                conn, flag_key=d16.FLAG_ERP_SYNC,
                organization_id=identity.organization_id, user_id=identity.user_id,
            )
        if not flag["effective_enabled"]:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "FEATURE_DISABLED",
                    "feature": d16.FLAG_ERP_SYNC,
                    "message": "ERP read-only sync is temporarily disabled. Existing snapshots remain readable with freshness metadata.",
                },
            )
        if not CONFIG.configured:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "ERPNEXT_NOT_CONFIGURED",
                    "message": "ERPNext integration is not configured. Set ERPNEXT_BASE_URL, ERPNEXT_API_KEY, and ERPNEXT_API_SECRET.",
                },
            )
        if mode == "full":
            return _service.full_sync(identity.organization_id, identity)
        return _service.incremental_sync(identity.organization_id, identity)

    @app.get("/api/integrations/erpnext/snapshots")
    def erpnext_snapshots(
        doctype: str = Query(..., pattern="^(Sales Order|Customer|Item)$"),
        identity: CurrentIdentity = Depends(get_current_identity),
    ) -> dict[str, Any]:
        _require_manager_or_admin(identity)
        with db() as conn:
            try:
                _ensure_erp_schema(conn)
            except HTTPException:
                return {"error": "D6 tables not found. Run migrations."}
            rows = conn.execute(
                "SELECT snapshot_id, external_id, source_modified_at, normalized_json, fetched_at, created_at, updated_at FROM erp_read_snapshots WHERE organization_id=? AND doctype=? ORDER BY fetched_at DESC LIMIT 100",
                (identity.organization_id, doctype),
            ).fetchall()
        items = []
        for row in rows:
            d = dict(row)
            d["normalized"] = json.loads(d.pop("normalized_json") or "{}")
            d.pop("raw_sha256", None)
            items.append(d)
        return {
            "doctype": doctype,
            "organization_id": identity.organization_id,
            "count": len(items),
            "items": items,
        }
