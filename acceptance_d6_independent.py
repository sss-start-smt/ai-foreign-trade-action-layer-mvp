"""
FlowOrder D6 — INDEPENDENT ACCEPTANCE VERIFICATION (WorkBuddy, not Trae)

Standalone script (NOT collected by the pytest suite) that re-verifies D6 gates
against the frozen contract without trusting prior reports.

Key gap it closes: the shipped test suite's MockErpClient OVERRIDES `_get()`, so
it never exercises the real `httpx.Client.get()` call. This script uses a real
`httpx.MockTransport` to capture the ACTUAL outbound request and prove the
`Authorization: token <key>:<secret>` header (with the real secret) is sent.

Run:
    PYTHONPATH=. python acceptance_d6_independent.py
from source/floworder.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

# Ensure imports work from this directory.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

PASS = []
FAIL = []
ERROR = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
        print(f"  [PASS] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAIL.append(name)
        print(f"  [FAIL] {name}" + (f" — {detail}" if detail else ""))


print("=" * 78)
print("D6 INDEPENDENT ACCEPTANCE — REVERSE / ATTACK VERIFICATION")
print("=" * 78)

# ---------------------------------------------------------------------------
# 0. DB isolation
# ---------------------------------------------------------------------------
tmp = tempfile.mkdtemp(prefix="floworder_d6_indep_")
os.environ["DB_PATH"] = os.path.join(tmp, "indep.db")

import database  # noqa: E402
from erpnext_readonly import (  # noqa: E402
    ERPNextReadOnlyClient,
    ERPNextConfig,
    ERPNextNormalizer,
    ERPReadSyncService,
    ERPNextClientError,
    CONFIG,
    _ensure_erp_schema,
    _get_sync_state,
    _upsert_snapshot,
    _upsert_sync_state,
)
from database import db  # noqa: E402

# Build D6 schema in this isolated sqlite DB.
with db() as conn:
    _ensure_erp_schema(conn)
    conn.commit()

# ---------------------------------------------------------------------------
# 1. OUTBOUND httpx request ACTUALLY receives Authorization with the REAL secret
# ---------------------------------------------------------------------------
print("\n[1] Outbound httpx request receives Authorization (real transport)")
import httpx  # noqa: E402

captured_requests = []

def _transport(request: httpx.Request) -> httpx.Response:
    captured_requests.append({
        "url": str(request.url),
        "method": request.method,
        "auth": request.headers.get("Authorization"),
    })
    # Return a minimal but valid ERPNext list payload.
    body = {"data": [{"name": "SO-1", "modified": "2026-08-11T10:00:00+08:00"}]}
    return httpx.Response(200, json=body)

real_key = "ak-REALKEY-12345"
real_secret = "as-REALSECRET-67890"
client = ERPNextReadOnlyClient(
    base_url="http://fake.erpnext.local",
    auth_header=f"token {real_key}:{real_secret}",
    timeout=5,
)
# Force the client to use our capturing transport instead of a real network call.
client._get.__globals__  # noop to keep linter calm
# Monkeypatch the httpx.Client usage inside _get by patching httpx.Client.
_orig_client = httpx.Client

class _CapturingClient(_orig_client):
    def __init__(self, *args, **kwargs):
        kwargs.pop("transport", None)
        super().__init__(*args, transport=httpx.MockTransport(_transport), **{k: v for k, v in kwargs.items() if k != "transport"})

httpx.Client = _CapturingClient
try:
    resp = client.get_list("Sales Order", ["name", "modified"])
    check("GET method used for outbound", captured_requests and captured_requests[0]["method"] == "GET",
          f"method={captured_requests[0]['method'] if captured_requests else None}")
    sent_auth = captured_requests[0]["auth"] if captured_requests else None
    check("Real secret present in outbound Authorization", sent_auth == f"token {real_key}:{real_secret}",
          f"sent={sent_auth}")
    check("Outbound Authorization format token key:secret",
          bool(sent_auth) and sent_auth.startswith("token ") and f"{real_key}:{real_secret}" in sent_auth)
    check("get_list returned data", isinstance(resp, dict) and "data" in resp)
finally:
    httpx.Client = _orig_client

# Also confirm: unconfigured (empty auth) sends NO Authorization header.
captured_requests.clear()
client2 = ERPNextReadOnlyClient(base_url="http://fake.erpnext.local", auth_header="", timeout=5)
class _CapturingClient2(_orig_client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, transport=httpx.MockTransport(_transport), **kwargs)
httpx.Client = _CapturingClient2
try:
    client2.get_list("Customer", ["name"])
    sent_auth2 = captured_requests[0]["auth"] if captured_requests else "MISSING"
    check("No Authorization header when unconfigured", sent_auth2 is None,
          f"sent={sent_auth2}")
finally:
    httpx.Client = _orig_client

# Confirm detail GET also carries the header (Sales Order list->detail path).
captured_requests.clear()
client3 = ERPNextReadOnlyClient(base_url="http://fake.erpnext.local",
                                 auth_header=f"token {real_key}:{real_secret}", timeout=5)
class _CapturingClient3(_orig_client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, transport=httpx.MockTransport(_transport), **kwargs)
httpx.Client = _CapturingClient3
try:
    client3.get_doc("Sales Order", "SO 1/2")  # name with space+slash -> URL-encoded
    detail_url = captured_requests[0]["url"] if captured_requests else ""
    detail_auth = captured_requests[0]["auth"] if captured_requests else None
    check("Detail GET (get_doc) carries Authorization", detail_auth == f"token {real_key}:{real_secret}")
    # Safe URL-encoding: space -> %20, slash -> %2F
    check("Sales Order name safely URL-encoded (space/slash)",
          "SO%201%2F2" in detail_url, f"url={detail_url}")
finally:
    httpx.Client = _orig_client

# ---------------------------------------------------------------------------
# 2. ERP client has NO write capability
# ---------------------------------------------------------------------------
print("\n[2] ERP client has no write capability")
write_names = ("post", "put", "patch", "delete", "request", "send", "write")
found_write = [n for n in write_names if hasattr(ERPNextReadOnlyClient, n)]
check("No write method/attr on ERPNextReadOnlyClient", not found_write,
      f"found={found_write}")
# Inspect source of public methods to ensure only GET is issued.
import inspect  # noqa: E402
src = inspect.getsource(ERPNextReadOnlyClient)
check("No httpx post/put/patch/delete in client source",
      not any(tok in src for tok in (".post(", ".put(", ".patch(", ".delete(")))

# ---------------------------------------------------------------------------
# 3. First-time failure (never synced) -> UNAVAILABLE, never FRESH/NEVER_SYNCED
# ---------------------------------------------------------------------------
print("\n[3] First-time source failure exposes UNAVAILABLE (not FRESH/NEVER_SYNCED)")
from auth import CurrentIdentity  # noqa: E402

def _identity(org="ORG-A", role="manager"):
    return CurrentIdentity(user_id="U", organization_id=org, role=role, name="x")

CONFIG.base_url = "http://fake.erpnext.local"
CONFIG.api_key = real_key
CONFIG.api_secret = real_secret
CONFIG.organization_id = "ORG-A"

# 3a. First-time TIMEOUT
svc = ERPReadSyncService()
class _TimeoutClient(ERPNextReadOnlyClient):
    def _get(self, url, params=None):
        raise ERPNextClientError("timeout", code="TIMEOUT")
svc._client = _TimeoutClient(base_url="http://fake.erpnext.local", auth_header="token x:y")
res = svc.full_sync("ORG-A", _identity())
st = _get_sync_state(db().connect() if False else None, "ORG-A", "Customer") if False else None
with db() as conn:
    st = _get_sync_state(conn, "ORG-A", "Customer")
check("First-time TIMEOUT -> status UNAVAILABLE", st["sync_status"] == "UNAVAILABLE",
      f"status={st['sync_status']}")
check("First-time TIMEOUT -> NO FRESH", st["sync_status"] != "FRESH")
check("First-time TIMEOUT -> overall PARTIAL_FAILURE", res["overall_status"] == "PARTIAL_FAILURE")
# No cursor advanced
check("First-time failure -> cursor not set", not st["last_success_cursor"])

# 3b. First-time 401
svc2 = ERPReadSyncService()
class _AuthFailClient(ERPNextReadOnlyClient):
    def _get(self, url, params=None):
        raise ERPNextClientError("401", code="AUTH_FAILED", status_code=401)
svc2._client = _AuthFailClient(base_url="http://fake.erpnext.local", auth_header="token x:y")
svc2.full_sync("ORG-A", _identity())
with db() as conn:
    st2 = _get_sync_state(conn, "ORG-A", "Customer")
check("First-time 401 -> UNAVAILABLE", st2["sync_status"] == "UNAVAILABLE")

# 3c. First-time 403
svc3 = ERPReadSyncService()
class _PermFailClient(ERPNextReadOnlyClient):
    def _get(self, url, params=None):
        raise ERPNextClientError("403", code="PERMISSION_DENIED", status_code=403)
svc3._client = _PermFailClient(base_url="http://fake.erpnext.local", auth_header="token x:y")
svc3.full_sync("ORG-A", _identity())
with db() as conn:
    st3 = _get_sync_state(conn, "ORG-A", "Customer")
check("First-time 403 -> UNAVAILABLE", st3["sync_status"] == "UNAVAILABLE")

# ---------------------------------------------------------------------------
# 4. Boundary cursor with SOURCE-SIDE filtering -> no duplicate snapshot
# ---------------------------------------------------------------------------
print("\n[4] Boundary modified==cursor repeat read: no duplicate snapshot")
# Real source only returns records with modified >= cursor (as a real ERP would).
class _BoundaryClient(ERPNextReadOnlyClient):
    def __init__(self):
        super().__init__(base_url="http://fake.erpnext.local", auth_header="token x:y")
        self._calls = 0
    def _get(self, url, params=None):
        # Simulate server-side filter on modified >= cursor
        filters = None
        if params and "filters" in params:
            import json as _json
            filters = _json.loads(params["filters"])
        cursor = None
        if filters:
            for f in filters:
                if f[0] == "modified" and f[1] == ">=":
                    cursor = f[2]
        data = [
            {"name": "C1", "customer_name": "A", "modified": "2026-08-11T10:30:00+08:00"},
            {"name": "C2", "customer_name": "B", "modified": "2026-08-11T11:00:00+08:00"},
        ]
        if cursor:
            data = [d for d in data if d["modified"] >= cursor]
        return {"data": data}

# Prime a cursor exactly at the boundary of an existing record.
with db() as conn:
    _upsert_snapshot(conn, "ORG-A", "Customer", "C0", "2026-08-11T10:00:00+08:00",
                     {"external_id": "C0", "source_modified_at": "2026-08-11T10:00:00+08:00"},
                     "hash0", "2026-08-11T10:00:00+08:00")
    _upsert_sync_state(conn, "ORG-A", "Customer",
                       last_success_cursor="2026-08-11T10:00:00+08:00",
                       last_success_at="2026-08-11T10:00:00+08:00",
                       sync_status="FRESH")
    conn.commit()

svc4 = ERPReadSyncService(_BoundaryClient())
r1 = svc4.incremental_sync("ORG-A", _identity())
with db() as conn:
    cnt1 = conn.execute("SELECT COUNT(*) as c FROM erp_read_snapshots WHERE organization_id='ORG-A' AND doctype='Customer'").fetchone()["c"]
check("After 1st incremental: 3 snapshots (C0,C1,C2)", cnt1 == 3, f"count={cnt1}")
# Second incremental: cursor advanced to max modified (11:00). Boundary record C2 (==11:00) re-read.
r2 = svc4.incremental_sync("ORG-A", _identity())
with db() as conn:
    cnt2 = conn.execute("SELECT COUNT(*) as c FROM erp_read_snapshots WHERE organization_id='ORG-A' AND doctype='Customer'").fetchone()["c"]
check("Boundary repeat read does NOT create duplicate snapshot", cnt2 == 3, f"count={cnt2}")
check("Second incremental records_changed == 0", r2["doctypes"]["Customer"]["records_changed"] == 0,
      f"changed={r2['doctypes']['Customer']['records_changed']}")

# ---------------------------------------------------------------------------
# 5. Secret never appears in logs / status body
# ---------------------------------------------------------------------------
print("\n[5] Secret not leaked to logs or status body")
log_capture = io.StringIO()
handler = logging.StreamHandler(log_capture)
_logger = logging.getLogger("erpnext_readonly")
_logger.setLevel(logging.DEBUG)
_logger.addHandler(handler)

# Trigger a 401 so an error is logged
svc5 = ERPReadSyncService()
class _AuthFailClient2(ERPNextReadOnlyClient):
    def _get(self, url, params=None):
        raise ERPNextClientError("ERPNext authentication failed (401)", code="AUTH_FAILED", status_code=401)
svc5._client = _AuthFailClient2(base_url="http://fake.erpnext.local", auth_header=f"token {real_key}:{real_secret}")
res5 = svc5.full_sync("ORG-A", _identity())
logs = log_capture.getvalue()
check("Secret NOT in logs", real_secret not in logs, f"log_snippet={logs[:120]!r}")
check("api_secret key NOT in logs", "api_secret" not in logs.lower())
_logger.removeHandler(handler)

# Status body via the service (uses CONFIG.mask)
status = svc5.get_status("ORG-A")
status_str = json.dumps(status)
check("Secret NOT in status body", real_secret not in status_str)
check("Authorization not in status body", "Authorization" not in status_str)
check("api_key not in status body", real_key not in status_str)
# The sync RESPONSE body (the visible API payload) must also carry no secret.
sync_body = json.dumps(res5)
check("Secret NOT in sync response body", real_secret not in sync_body)
check("Authorization not in sync response body", "Authorization" not in sync_body)
check("api_key not in sync response body", real_key not in sync_body)

# ---------------------------------------------------------------------------
# 6. PG path: no runtime CREATE TABLE
# ---------------------------------------------------------------------------
print("\n[6] PostgreSQL path does not auto-create D6 tables")
from fastapi import HTTPException  # noqa: E402

class _FakePGResult:
    def fetchone(self):
        return (False,)

class _FakePGConn:
    is_pg = True
    def __init__(self):
        self.executed = []
    def execute(self, *a, **kw):
        self.executed.append(str(a[0]) if a else "")
        return _FakePGResult()
    def fetchone(self):
        return (False,)

orig_table_exists = database.table_exists
database.table_exists = lambda conn, name: False
try:
    fake = _FakePGConn()
    raised = False
    try:
        _ensure_erp_schema(fake)
    except HTTPException as e:
        raised = True
        check("PG missing tables raises MIGRATION_REQUIRED (503)",
              e.status_code == 503 and "MIGRATION_REQUIRED" in str(e.detail))
    check("PG path raised (did not silently proceed)", raised)
    created = [s for s in fake.executed if "CREATE TABLE" in s.upper()]
    check("PG path issued NO runtime CREATE TABLE", not created, f"created={created}")
finally:
    database.table_exists = orig_table_exists

# ---------------------------------------------------------------------------
# 7. ERP owner not mapped to FlowOrder business owner
# ---------------------------------------------------------------------------
print("\n[7] ERP owner NOT mapped to FlowOrder owner")
norm = ERPNextNormalizer._normalize_sales_order({
    "name": "SO-9", "owner": "boss@example.com", "modified": "2026-08-11T10:00:00+08:00",
    "items": [{"name": "L1", "item_code": "I", "qty": 1}],
})
check("No order_owner_user_id in normalized", "order_owner_user_id" not in norm)
check("owner not promoted to FlowOrder identity", norm.get("erp_owner") == "boss@example.com"
      and "owner_id" not in norm)

# ---------------------------------------------------------------------------
# 8. Per-DocType independent cursor (Customer success + Item 500)
# ---------------------------------------------------------------------------
print("\n[8] Per-DocType cursor independence (Customer ok + Item 500)")
# Clean slate so Item starts with NO prior cursor (isolated test of the gate).
with db() as conn:
    for dt in ("Sales Order", "Customer", "Item"):
        conn.execute("DELETE FROM erp_read_snapshots WHERE organization_id='ORG-A' AND doctype=?", (dt,))
        conn.execute("DELETE FROM erp_sync_state WHERE organization_id='ORG-A' AND doctype=?", (dt,))
    conn.commit()

class _PartialFailClient(ERPNextReadOnlyClient):
    def _get(self, url, params=None):
        if url.endswith("/Item"):
            raise ERPNextClientError("500", code="SERVER_ERROR", status_code=500)
        if url.startswith("/api/resource/Sales Order"):
            if url.rstrip("/").count("/") >= 4:  # detail
                return {"data": {"name": "SO-1", "status": "x", "customer": "c",
                                 "items": [], "modified": "2026-08-11T10:00:00+08:00"}}
            return {"data": [{"name": "SO-1", "status": "x", "customer": "c",
                              "modified": "2026-08-11T10:00:00+08:00"}]}
        # Customer
        return {"data": [{"name": "C1", "customer_name": "A", "modified": "2026-08-11T10:00:00+08:00"}]}
svc6 = ERPReadSyncService(_PartialFailClient(base_url="http://fake.erpnext.local", auth_header="token x:y"))
res6 = svc6.full_sync("ORG-A", _identity())
check("overall PARTIAL_FAILURE", res6["overall_status"] == "PARTIAL_FAILURE")
with db() as conn:
    cust = _get_sync_state(conn, "ORG-A", "Customer")
    item = _get_sync_state(conn, "ORG-A", "Item")
print("   [debug] cust=", dict(cust))
print("   [debug] item=", dict(item))
check("Customer cursor advanced", cust["last_success_cursor"] is not None and cust["sync_status"] == "FRESH")
check("Item cursor NOT advanced (failure did not push cursor)",
      item["last_success_cursor"] is None and item["sync_status"] == "UNAVAILABLE")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print("\n" + "=" * 78)
print(f"INDEPENDENT CHECKS: {len(PASS)} passed, {len(FAIL)} failed, {len(ERROR)} errored")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
print("=" * 78)
sys.exit(1 if FAIL else 0)
