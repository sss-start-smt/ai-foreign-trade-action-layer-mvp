# D6 Implementation Report: ERPNext Read-Only Integration

**Status:** DELIVERED (Mock-Verified, REAL_ERPNEXT_NOT_VERIFIED)  
**Date:** 2026-08-11  
**Contract:** 02_ENGINEERING_CURRENT/reports/independent_acceptance/D6/FlowOrder_D6_FO01_ERPNext_ReadOnly_Contract_FROZEN.md

---

## 1. Architecture Overview

```
┌──────────────────────────────────────────────────────────────┐
│                        FlowOrder API                         │
│  /api/integrations/erpnext/                                  │
│  ├── sync?mode=full|incremental  (POST, Manager/Admin only) │
│  ├── status                     (GET, authenticated)         │
│  └── snapshots?doctype=...      (GET, Manager/Admin only)  │
└──────────────┬───────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│              ERPReadSyncService (orchestrator)              │
│  ├── Full Sync: limit_start/limit_page_length pagination    │
│  ├── Incremental: [["modified",">=",cursor]] filter array   │
│  ├── Idempotent Upsert via SHA256 hash                      │
│  ├── Per-DocType cursor management (independent advance)    │
│  ├── Order-by stability (order_by=modified asc)             │
│  └── Freshness computation (NEVER_SYNCED/FRESH/STALE/UNAVAILABLE)
└──────────────┬───────────────────────────────────────────────┘
               │
       ┌───────┴────────┐
       ▼                ▼
┌─────────────┐  ┌──────────────────────────────────────────────┐
│ Normalizer  │  │        ERPNextReadOnlyClient (httpx)         │
│ ─────────── │  │  GET-only REST client                        │
│ Sales Order │  │  Authorization: token key:secret (outbound)  │
│ Customer    │  │  Secret masked in logs/API responses         │
│ Item        │  │  No POST/PUT/PATCH/DELETE methods            │
└─────────────┘  └──────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│                    Database (PostgreSQL/SQLite)               │
│  ├── erp_sync_state: cursor, status, timestamps, errors       │
│  └── erp_read_snapshots: normalized + raw JSON, SHA256 hash   │
│  PG: migration-only (no runtime CREATE)                      │
│  SQLite: ensure schema in tests                               │
└──────────────────────────────────────────────────────────────┘
```

## 2. Authentication Architecture

FlowOrder uses **two independent authentication boundaries** that must never be confused:

```
┌───────────────────────────────────────────────────────────────┐
│ ① FlowOrder Inbound Auth        (client → FlowOrder backend) │
│   Mechanism: FlowOrder token (auth.DEMO_TOKEN_MAP)             │
│   Header:    X-Auth-Token (custom FlowOrder header)           │
│   Purpose:   Verifies WHO is calling the FlowOrder API         │
│   Result:    CurrentIdentity(user_id, organization_id, role) │
│   Used by:   Depends(get_current_identity) + RBAC checks      │
└───────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌───────────────────────────────────────────────────────────────┐
│ ② ERPNext Outbound Auth         (FlowOrder backend → ERPNext) │
│   Mechanism: ERPNext API Key + API Secret from env             │
│   Header:    Authorization: token <api_key>:<api_secret>      │
│   Purpose:   Authenticates FlowOrder AS A CLIENT to ERPNext   │
│   Built in:  ERPNextReadOnlyClient._get_headers()            │
│   Config:    ERPNEXT_API_KEY / ERPNEXT_API_SECRET env vars    │
│   Leak rule: NEVER exposed in FlowOrder status/logs/DB        │
└───────────────────────────────────────────────────────────────┘
```

### 2.1 FlowOrder Inbound Auth (unchanged, reused from existing auth.py)
- Users authenticate via FlowOrder's existing token system (`auth.DEMO_TOKEN_MAP`)
- Custom header `X-Auth-Token` is validated by `get_current_identity`
- `CurrentIdentity` (user_id, organization_id, role) injected via `Depends(get_current_identity)`
- RBAC: Manager/Admin required for `POST /sync` and `GET /snapshots`
- Organization isolation: `ERPNEXT_ORGANIZATION_ID` binding restricts which org can trigger ERP sync
- **The inbound X-Auth-Token is a FlowOrder credential — it is NEVER forwarded to ERPNext.**

### 2.2 ERPNext Outbound Auth (new in D6)
- `ERPNextReadOnlyClient` reads `ERPNEXT_API_KEY` + `ERPNEXT_API_SECRET` from environment variables
- Constructs **`Authorization: token <api_key>:<api_secret>`** header for every outbound HTTP request to ERPNext
- This header is built inside `_get_headers()` and sent automatically on every GET request
- When no ERPNext credentials are configured (`CONFIG.configured=False`), `Authorization` header is NOT added
- **The ERPNext API secret is never exposed in FlowOrder:**
  - `ERPNextConfig.mask()` returns only non-secret fields (base_url, organization_id, configured, etc.)
  - Status API response contains **no `Authorization` header, no `api_secret`, no `api_key`, no secret fragments**
  - Secret is never logged, never stored in DB, never returned in API responses
- **Test-verified:**
  - `test_outbound_auth_header_sent` → `_get_headers()` contains `Authorization: token ...`
  - `test_auth_header_in_mock_call` → MockErpClient call log captures the outbound Authorization header
  - `test_status_no_secret` → Status API response contains no secret/Authorization fragment
  - `test_mask_no_secret` → `ERPNextConfig.mask()` returns no `api_secret`/`api_key`/`Authorization` fields

### 2.3 Critical Distinction
| Concern | FlowOrder Inbound | ERPNext Outbound |
|---------|-------------------|------------------|
| Who authenticates to whom | User → FlowOrder | FlowOrder → ERPNext |
| Header | `X-Auth-Token` (custom) | `Authorization: token ...` (HTTP standard) |
| Token source | `auth.DEMO_TOKEN_MAP` | `ERPNEXT_API_KEY` + `ERPNEXT_API_SECRET` |
| Shown in status API | ❌ Never returned | ❌ Never returned |
| Used for RBAC | ✅ Role check | ❌ Not used for FlowOrder RBAC |

**These are completely separate credential systems.** The inbound X-Auth-Token is a FlowOrder authentication mechanism and is **not** used for ERPNext outbound auth. The ERPNext API key/secret is **only** used for outbound HTTP calls and is **never** returned in API responses or logs.

## 3. Module Structure

### New Files
| File | Purpose |
|------|---------|
| `erpnext_readonly.py` | Core module: Client, Normalizer, SyncService, FastAPI routes |
| `alembic/versions/d6e7f8a9b0c1_erpnext_readonly.py` | Alembic migration for `erp_sync_state` and `erp_read_snapshots` |
| `tests/test_d6_erpnext_readonly.py` | Comprehensive test suite (61 tests, 19 test classes) |

### Modified Files
| File | Change |
|------|--------|
| `alembic/env.py` | Updated `target_metadata` to include D6 tables |
| `main.py` | Added `register_erpnext_routes(app)` call and `PG_REQUIRED_TABLES` entry |

### No Changes
- **D5 Excel chain:** untouched, no refactoring
- **Existing auth.py/RBAC:** reused as-is
- **schema.sql:** not modified (Alembic manages DDL)

## 4. Key Design Decisions

### 4.1 Read-Only Enforcement
- `ERPNextReadOnlyClient` has **no** `post()`, `put()`, `patch()`, `delete()` methods
- Only `_get()` → `get_list()` (GET-based) and `get_doc()` for detail fetch exists
- `hasattr(client, "post")` → False (verified in test)

### 4.2 Secret Management
- Token read from `ERPNEXT_API_KEY` + `ERPNEXT_API_SECRET` environment variables
- `CONFIG.mask()` returns only `api_key[:4] + "****"` — **no api_secret field at all**
- Status API response contains **no Authorization header**, no `api_secret`, no secret fragments
- Secret never logged, never stored in DB, never returned in API responses
- Outbound HTTP requests DO include `Authorization: token <key>:<secret>` — this is required for ERPNext authentication
- Test-verified: `test_auth_header_in_mock_call` captures outbound headers and confirms Authorization is present
- Test-verified: `test_status_no_secret` confirms status API returns no secret fragments

### 4.3 Per-DocType Independent Success
- Each DocType (Sales Order, Customer, Item) writes its cursor/state **independently** upon successful completion
- If Customer succeeds and Item fails: Customer's cursor advances, `sync_status=FRESH`, records updated; Item's cursor NOT advanced, `sync_status=UNAVAILABLE` (first failure) or `STALE` (previously succeeded)
- No DocType blocks another's success — partial failure is supported
- Overall status: SUCCESS (all succeed), PARTIAL_FAILURE (some succeed), FAILED (all fail)
- Test-verified: `test_customer_success_item500_customer_state_written`, `test_failed_doctype_does_not_block_others`

### 4.4 Cursor Safety
- Cursor advances **per DocType** after each completes successfully
- Failed DocType → cursor preserved at last good position, `sync_status=UNAVAILABLE` or `STALE`
- Empty sync (no source records) → cursor preserved at old value, not forged from local `now()`
- Incremental sync without cursor falls back to full sync mode

### 4.5 Idempotent Upsert
- Each snapshot keyed by `(organization_id, doctype, external_id)`
- Raw JSON SHA256 hash compared on each sync
- Identical data → `updated_at` refreshed, `records_changed=0`
- Modified data → full row update with new `raw_sha256`, `records_changed++`
- Each doctype state writes its own seen/changed, not total across all doctypes

### 4.6 Freshness State Machine
```
NEVER_SYNCED ──full/incremental success──▶ FRESH
    ▲                                        │
    │                                        │ stale_after_seconds elapsed
    │                                        ▼
    └────── errors (timeout, 5xx, network) ◀── STALE
                                                  │
                                                  │
          AUTH_FAILED / PERMISSION_DENIED ──▶ UNAVAILABLE

  Priority: UNAVAILABLE > STALE > FRESH > NEVER_SYNCED
  - sync_status=STALE → 显示 STALE (even if last_success_at is recent)
  - 从未成功且本次来源失败 → UNAVAILABLE
  - 旧快照失败 → 不得显示 FRESH
```

### 4.7 Organization Isolation
- All sync operations scoped by `organization_id`
- **ERPNEXT_ORGANIZATION_ID** env var binding — only the bound org can trigger sync
- Other orgs get 403 "Organization not bound to ERPNext integration"
- Snapshots and sync states partitioned per org
- Cross-org sync produces separate records for same `external_id`

### 4.8 Owner Mapping Rejection
- `ERPNextNormalizer._normalize_sales_order()` maps `raw["owner"]` → `normalized["erp_owner"]`
- **Does NOT** map to `owner_id` (FlowOrder's 跟单负责人 field)
- Verified: `"owner_id" not in normalized` ✅

### 4.9 Sales Order List→Detail Pattern
- List request: only parent fields (`name`, `modified`, `status`, `customer`, `grand_total`) — no `items`
- Detail request: `GET /api/resource/Sales Order/{name}` (URL-encoded name, no fields param)
- Full document fetched, Normalizer extracts items field
- Mock verified: list→detail flow with proper field separation
- No POST/PUT/PATCH/DELETE — only GET

### 4.10 Database Schema Management
- **PostgreSQL:** D6 tables must exist via Alembic migration. Runtime `_ensure_erp_schema()` raises `MIGRATION_REQUIRED` error — no runtime CREATE
- **SQLite (testing):** `_ensure_erp_schema()` can create tables for test isolation

### 4.11 Frappe Filter Array Format
- Incremental sync uses proper Frappe array filter: `filters=[["modified", ">=", cursor]]`
- Stable ordering: `order_by="modified asc"` to ensure deterministic pagination
- Boundary overlap: page N includes cursor boundary records; idempotent upsert prevents duplicates

### 4.12 Snapshots RBAC
- `GET /api/integrations/erpnext/snapshots` requires Manager/Admin role
- `_require_manager_or_admin(identity)` enforced before any data access
- Snapshots contain full normalized JSON — sensitive data access restricted
- Regular users can still view status (cursor, freshness) via `/status` endpoint

## 5. Database Schema

### 5.1 erp_sync_state
```sql
CREATE TABLE erp_sync_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id TEXT NOT NULL,
    doctype         TEXT NOT NULL,
    last_success_cursor TEXT,
    last_success_at TEXT,
    last_attempt_at TEXT,
    last_error_code TEXT,
    sync_status     TEXT NOT NULL DEFAULT 'NEVER_SYNCED',
    records_seen    INTEGER DEFAULT 0,
    records_changed INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(organization_id, doctype)
);

CREATE INDEX ix_erp_sync_state_org ON erp_sync_state(organization_id);
CREATE INDEX ix_erp_sync_state_status ON erp_sync_state(sync_status);
```

### 5.2 erp_read_snapshots
```sql
CREATE TABLE erp_read_snapshots (
    snapshot_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    organization_id TEXT NOT NULL,
    doctype         TEXT NOT NULL,
    external_id     TEXT NOT NULL,
    source_modified_at TEXT,
    normalized_json TEXT NOT NULL,
    raw_sha256      TEXT NOT NULL,
    fetched_at      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(organization_id, doctype, external_id)
);

CREATE INDEX ix_erp_snapshots_org ON erp_read_snapshots(organization_id);
CREATE INDEX ix_erp_snapshots_doctype ON erp_read_snapshots(doctype);
CREATE INDEX ix_erp_snapshots_external ON erp_read_snapshots(external_id);
```

## 6. API Endpoints

### POST /api/integrations/erpnext/sync?mode={full|incremental}
- **Auth:** Bearer token + Manager/Admin role required + org must match `ERPNEXT_ORGANIZATION_ID`
- **Request:** Query param `mode` (default: `incremental`)
- **Response:**
```json
{
    "mode": "full",
    "overall_status": "SUCCESS",
    "doctypes": {
        "Sales Order": {"status": "SUCCESS", "records_seen": 10, "records_changed": 10},
        "Customer": {"status": "SUCCESS", "records_seen": 5, "records_changed": 5},
        "Item": {"status": "SUCCESS", "records_seen": 20, "records_changed": 20}
    },
    "total_records_seen": 35,
    "total_records_changed": 35,
    "freshness": "FRESH"
}
```

### GET /api/integrations/erpnext/status
- **Auth:** Authenticated users only
- **Response:** Per-doctype sync status, cursor, timestamps, snapshot counts, **config mask (no secrets)**
- **No:** Authorization header, api_secret, or any secret fragment returned

### GET /api/integrations/erpnext/snapshots?doctype={Sales Order|Customer|Item}
- **Auth:** Manager/Admin role required
- **Response:** Latest 100 normalized snapshots for the specified doctype

## 7. Error Handling Matrix

| Scenario | HTTP Status | DB sync_status | Error Code |
|----------|------------|----------------|------------|
| Not configured | 503 | NEVER_SYNCED | ERPNEXT_NOT_CONFIGURED |
| Org not bound | 403 | — | ORG_NOT_BOUND |
| Timeout | 200 (partial) | STALE | TIMEOUT |
| 5xx server error | 200 (partial) | STALE | SERVER_ERROR |
| 401 unauthorized | 200 (partial) | UNAVAILABLE | AUTH_FAILED |
| 403 forbidden | 200 (partial) | UNAVAILABLE | PERMISSION_DENIED |
| Malformed JSON | 200 (partial) | STALE | MALFORMED_JSON |
| Network error | 200 (partial) | STALE | CONNECTION_ERROR |
| PG missing tables | 503 | — | MIGRATION_REQUIRED |
| No permissions (role) | 403 | — | MANAGER_REQUIRED |
| Snapshots RBAC | 403 | — | MANAGER_REQUIRED |

## 8. Configuration

### Required Environment Variables
```bash
ERPNEXT_BASE_URL=https://erp.example.com
ERPNEXT_API_KEY=your_api_key
ERPNEXT_API_SECRET=your_api_secret
ERPNEXT_ORGANIZATION_ID=your_org_id   # Only this org can trigger sync
```

### Optional Environment Variables
```bash
ERPNEXT_STALE_AFTER_SECONDS=3600   # Freshness threshold (default: 3600)
ERPNEXT_PAGE_SIZE=50                # Pagination page size (default: 50)
ERPNEXT_TIMEOUT_SECONDS=30          # HTTP timeout (default: 30)
```

## 9. REAL_ERPNEXT_NOT_VERIFIED

This implementation uses **mock-based verification**. All 61 tests pass with mocked HTTP responses. The integration has **NOT** been tested against a real ERPNext instance. Before production deployment:

1. Configure `ERPNEXT_BASE_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`, `ERPNEXT_ORGANIZATION_ID`
2. Run a manual full sync against a staging ERPNext instance
3. Verify data mapping correctness for all 3 DocTypes
4. Test pagination with >50 records per DocType
5. Validate cursor behavior across multiple incremental syncs
6. Confirm organization isolation with multi-tenant test data
7. Verify RBAC enforcement with operator-role users (viewers blocked from snapshots)
8. Test freshness degradation after `stale_after_seconds`
9. Verify Frappe filter array format works with real ERPNext (`[["modified",">=",cursor]]`)
10. Test Sales Order list→detail pattern returns items correctly
11. Verify `Authorization: token <key>:<secret>` header is accepted by ERPNext
12. Confirm secret masking: status/logs show no secret fragments

---

## Appendix: Data Flow Example

**Full Sync Flow:**
1. Manager calls `POST /api/integrations/erpnext/sync?mode=full`
2. Service checks `CONFIG.configured` + org binding → True
3. For each DocType (Sales Order, Customer, Item):
   - Page through results: `GET /api/resource/{DocType}?fields=[...]&limit_start=0&limit_page_length=50&order_by=modified asc`
   - For Sales Order: list first (parent fields), then detail GET for changed/new records
   - Parse response, normalize each record
   - Compute SHA256 hash of raw JSON
   - Upsert snapshot (idempotent on hash match)
   - Write per-doctype state: cursor, FRESH, seen/changed
4. Return aggregate success response (may be PARTIAL_FAILURE if some doctypes failed)

**Incremental Sync Flow:**
1. Manager calls `POST /api/integrations/erpnext/sync?mode=incremental`
2. For each DocType, read `last_success_cursor` from `erp_sync_state`
3. If cursor exists: `GET /api/resource/{DocType}?filters=[["modified",">=",cursor]]&order_by=modified asc`
4. If no cursor: fall back to full sync mode
5. Same upsert logic as full sync
6. Each successful DocType advances its own cursor independently
7. Failed DocType → cursor preserved, no advancement
8. Empty result → cursor preserved at old value, not set to local now()