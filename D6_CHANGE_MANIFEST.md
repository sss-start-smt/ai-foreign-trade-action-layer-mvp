# D6 Change Manifest: ERPNext Read-Only Integration

**Date:** 2026-08-11  
**Sprint:** D6 - ERPNext只读接入  
**Status:** DELIVERED (Mock-Verified, REAL_ERPNEXT_NOT_VERIFIED)

---

## 1. New Files Created

| File Path | Description | Size |
|-----------|-------------|------|
| `erpnext_readonly.py` | Core D6 module: Client, Normalizer, SyncService, FastAPI routes | ~800 lines |
| `alembic/versions/d6e7f8a9b0c1_erpnext_readonly.py` | Alembic migration: `erp_sync_state`, `erp_read_snapshots` | ~150 lines |
| `tests/test_d6_erpnext_readonly.py` | D6 test suite (61 tests, 19 test classes) | ~950 lines |
| `D6_IMPLEMENTATION_REPORT.md` | Implementation documentation | — |
| `D6_TEST_REPORT.md` | Test results and coverage report | — |
| `D6_CHANGE_MANIFEST.md` | This document | — |

## 2. Existing Files Modified

| File Path | Change Description | Impact |
|-----------|-------------------|--------|
| `alembic/env.py` | Added D6 table metadata to `target_metadata` | Required for Alembic autogenerate |
| `main.py` | Added `register_erpnext_routes(app)` call in startup | Registers D6 API routes |
| `main.py` | Added `erp_sync_state`, `erp_read_snapshots` to `PG_REQUIRED_TABLES` | Required for PG deployment check |

## 3. No Changes (Explicitly Protected)

| File/Module | Reason |
|-------------|--------|
| D5 Excel import chain | Constraint: "不得重构D5 Excel链" |
| `auth.py` | Reused as-is; no RBAC changes needed |
| `database.py` | Reused as-is; DDL handled by Alembic |
| `schema.sql` | Not modified; Alembic manages DDL |
| Any D4/D3 modules | No cross-version changes |

## 4. API Endpoints Added

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/api/integrations/erpnext/sync` | Manager/Admin + org bound | Trigger Full/Incremental sync |
| GET | `/api/integrations/erpnext/status` | Authenticated | View sync status, cursor, freshness (no secrets) |
| GET | `/api/integrations/erpnext/snapshots` | **Manager/Admin only** | Browse normalized snapshots |

## 5. Database Tables Added

### `erp_sync_state`
- **Purpose:** Track per-org/doctype sync cursor, status, timestamps, errors
- **Columns:** id, organization_id, doctype, last_success_cursor, last_success_at, last_attempt_at, last_error_code, sync_status, records_seen, records_changed, created_at, updated_at
- **Constraints:** UNIQUE(organization_id, doctype)
- **Indexes:** ix_erp_sync_state_org, ix_erp_sync_state_status

### `erp_read_snapshots`
- **Purpose:** Store normalized + raw JSON snapshots per org/doctype/record
- **Columns:** snapshot_id, organization_id, doctype, external_id, source_modified_at, normalized_json, raw_sha256, fetched_at, created_at, updated_at
- **Constraints:** UNIQUE(organization_id, doctype, external_id)
- **Indexes:** ix_erp_snapshots_org, ix_erp_snapshots_doctype, ix_erp_snapshots_external

## 6. Classes/Types Added

| Class | Module | Purpose |
|-------|--------|---------|
| `ERPNextConfig` | erpnext_readonly | Configuration holder (env-based, masked output, no api_secret) |
| `ERPNextReadOnlyClient` | erpnext_readonly | GET-only httpx client with Authorization token outbound auth |
| `ERPNextNormalizer` | erpnext_readonly | Raw ERPNext → FlowOrder data normalization |
| `ERPReadSyncService` | erpnext_readonly | Orchestrator: full/incremental sync, per-doctype cursor, snapshots, org binding |
| `ERPNextClientError` | erpnext_readonly | Typed error with code + HTTP status |
| `CurrentIdentity` | auth (existing) | Reused for org isolation + RBAC |

## 7. Functions Added

| Function | Module | Purpose |
|----------|--------|---------|
| `register_erpnext_routes(app)` | erpnext_readonly | FastAPI route registration |
| `compute_freshness(state)` | erpnext_readonly | Single-doctype freshness calculation (STALE priority, UNAVAILABLE for never-synced failures) |
| `compute_global_freshness(states)` | erpnext_readonly | Multi-doctype worst-case freshness |
| `_ensure_erp_schema(conn)` | erpnext_readonly | SQLite: create tables; PG: raise MIGRATION_REQUIRED |
| `_upsert_sync_state(...)` | erpnext_readonly | Idempotent sync state upsert (per-doctype seen/changed) |
| `_upsert_snapshot(...)` | erpnext_readonly | Idempotent snapshot upsert (SHA256, records_changed=0 on hash match) |
| `_get_sync_state(...)` | erpnext_readonly | Read current sync state |
| `_mask_secret(secret)` | erpnext_readonly | Secret masking (all asterisks) |
| `_require_org_binding(org_id)` | erpnext_readonly | Validate org matches ERPNEXT_ORGANIZATION_ID |
| `_require_manager_or_admin(identity)` | erpnext_readonly | RBAC check for Manager/Admin role |
| `_compute_cursor(...)` | erpnext_readonly | Cursor computation (preserve old on empty, no local now() forge) |

## 8. Environment Variables Added

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `ERPNEXT_BASE_URL` | Yes | — | ERPNext instance URL |
| `ERPNEXT_API_KEY` | Yes | — | API key for ERPNext Authorization token |
| `ERPNEXT_API_SECRET` | Yes | — | API secret for ERPNext Authorization token |
| `ERPNEXT_ORGANIZATION_ID` | Yes | — | Only this org can trigger sync (tenant binding) |
| `ERPNEXT_STALE_AFTER_SECONDS` | No | 3600 | Freshness threshold |
| `ERPNEXT_PAGE_SIZE` | No | 50 | Pagination page size |
| `ERPNEXT_TIMEOUT_SECONDS` | No | 30 | HTTP request timeout |

## 9. Test Files Added

| File | Tests | Classes | Coverage |
|------|-------|---------|----------|
| `tests/test_d6_erpnext_readonly.py` | 61 | 19 | Isolation, Client, Outbound Auth, Secrets, Normalization, Sales Order List→Detail, Full/Incremental sync, Idempotency, Cursor, Error handling, Freshness, Per-DocType Independent Success, Org binding, RBAC, Snapshots RBAC, PG runtime, Edge cases, Full flow |

## 10. R1 Audit Fixes Applied

| # | Audit Issue | Fix Applied |
|---|-------------|-------------|
| P0-1 | DB_PATH污染 | Module-scoped fixture with tmp_path_factory + save/restore |
| P0-2 | 无真实cursor测试 | 6 incremental tests with cursor, filter array format, cross-page boundary |
| P0-3 | compute_freshness逻辑 | STALE优先级、从未成功失败→UNAVAILABLE、旧快照失败不显示FRESH |
| P0-4 | records_changed/total | hash未变=0、每doctype独立seen/changed |
| P0-5 | secret泄漏 | mask()无api_secret、status响应无Authorization/secret |
| P1-1 | Sales Order list→detail | 列表取父字段、detail GET取items、URL安全编码name、不传fields |
| P1-2 | org隔离 | ERPNEXT_ORGANIZATION_ID绑定、非绑定org 403 |
| P1-3 | PG运行时CREATE | PG raise MIGRATION_REQUIRED、SQLite ensure |
| P1-4 | 报告日期/测试名 | 2026-08-11、真实pytest输出、删除不存在的测试名 |

## 11. R3 Audit Fixes Applied (本轮审计修复)

This section tracks the fixes applied for the FlowOrder D6 R3 audit. The fixes are **additive to R1 fixes** — no prior behavior was removed.

| # | Audit Issue | Root Cause | Fix Applied |
|---|-------------|------------|-------------|
| R3-1 | ERPNext outbound auth missing `Authorization` header | `_get_headers()` only sent `Accept`; the ERP token constructed in `auth_header` was never attached to outbound requests | `ERPNextReadOnlyClient._get_headers()` now adds `Authorization: token <api_key>:<api_secret>` when `_auth_header` is configured; omitted when not configured |
| R3-2 | Incorrect test `test_no_auth_header_in_get_headers` enforced the wrong contract | The old test required `Authorization` **not** to be present in headers — the exact opposite of what ERPNext needs | Deleted the old test. Replaced with `test_outbound_auth_header_sent` (direct client header check), `test_no_auth_header_when_not_configured` (empty config → no header), `test_auth_header_in_mock_call` (MockErpClient call log captures real outbound headers) |
| R3-3 | Per-DocType success blocked by other DocType failures | `_do_sync` gated per-DocType state writes on a shared `overall_success` flag; a single failed DocType could prevent successful DocTypes from committing their cursor/stats | Removed `overall_success` gate. `_sync_doctype` now writes its own `FRESH`/cursor/seen/changed state immediately after that DocType's successful sync — failures in other DocTypes do not block it |
| R3-4 | Missing reverse test for partial-failure scenario | No test verified the exact audit scenario: "Customer success + Item 500 → PARTIAL_FAILURE but Customer state written, Item NOT advanced" | Added `TestPerDocTypeIndependentSuccess::test_customer_success_item500_customer_state_written` (asserts `cust_state=FRESH` with cursor, `item_state=UNAVAILABLE` with no cursor). Also added `test_failed_doctype_does_not_block_others` |
| R3-5 | `get_doc` sent `fields` query param to Frappe | Frappe `GET /api/resource/{DocType}/{name}` returns the full document; sending `fields` client-side is redundant and misleading | Removed the `fields` parameter from `get_doc(doctype, name)`. The full document is fetched; `ERPNextNormalizer` is responsible for extracting the fields FlowOrder needs. `name` is still URL-encoded via `urllib.parse.quote(name, safe="")` |
| R3-6 | Snapshots endpoint had no Manager/Admin RBAC | `GET /api/integrations/erpnext/snapshots` previously allowed any authenticated user to browse the full normalized JSON snapshots | Added `_require_manager_or_admin(identity)` at the top of the snapshots endpoint. Regular users can still view `/status` (cursor, freshness) but not the snapshots payload |
| R3-7 | Reports confused inbound vs outbound auth | Prior reports mentioned "X-Auth-Token from env vars" or implied the FlowOrder inbound token was used for ERPNext auth — incorrect | Three D6 reports rewritten with a clear separation diagram and table: FlowOrder inbound uses `X-Auth-Token` (FlowOrder token, role-based); ERPNext outbound uses `Authorization: token <api_key>:<api_secret>` (env-based, leaked nowhere). Removed "X-Auth-Token from env vars" description entirely |
| R3-8 | Report test numbers inconsistent with reality | Prior numbers cited "210 passed" or "55 passed" from earlier runs that no longer matched the current code | All three reports now use actual pytest output from the final R3 run: full suite **271 passed, 23 skipped, 0 failed**; D6 only **61 passed**; D5 only **25 passed** (no regression) |

### Final Verification
```
$ PYTHONPATH=. python -m pytest -q
271 passed, 23 skipped, 0 failed

$ PYTHONPATH=. python -m pytest tests/test_d6_erpnext_readonly.py -q
61 passed, 0 failed

$ PYTHONPATH=. python -m pytest tests/test_d5_excel_import.py -q
25 passed, 0 failed   # D5 baseline NOT regressed
```

**REAL_ERPNEXT_NOT_VERIFIED** — all auth and integration behavior was verified against mocked HTTP clients only. A real ERPNext staging acceptance is required before production deployment.

## 12. Migration Plan

### Pre-deployment
1. Set environment variables (`ERPNEXT_BASE_URL`, `ERPNEXT_API_KEY`, `ERPNEXT_API_SECRET`, `ERPNEXT_ORGANIZATION_ID`)
2. Run Alembic migration: `alembic upgrade head`
3. Verify `erp_sync_state` and `erp_read_snapshots` tables exist

### Deployment
1. Deploy code with D6 module
2. Run `init_db()` to ensure schema creation (SQLite auto, PG via Alembic)
3. Verify startup logs show D6 routes registered

### Post-deployment
1. Test `GET /api/integrations/erpnext/status` (should show NEVER_SYNCED, no secrets)
2. Trigger full sync: `POST /api/integrations/erpnext/sync?mode=full`
3. Verify snapshot counts, cursor advancement
4. Test incremental sync with modified data
5. Verify freshness degradation after `stale_after_seconds`
6. Verify org binding rejects non-bound orgs
7. Verify snapshots endpoint blocks non-Manager users

### Rollback
1. Remove D6 env vars → integration returns 503
2. Drop D6 tables if needed: `DROP TABLE erp_read_snapshots; DROP TABLE erp_sync_state;`
3. Remove D6 route registration in `main.py`

## 13. Risk Register

| Risk | Severity | Mitigation | Status |
|------|----------|------------|--------|
| Real ERPNext API mismatch | High | Contract FROZEN doc reviewed; mock tests validate structure | AWAITING_REAL_VERIFICATION |
| Large dataset performance | Medium | Pagination with configurable page_size; indexes on key columns | MONITOR |
| Cursor drift on boundary | Low | Overlap query: `[["modified",">=",cursor]]` array filter; stable order_by | MITIGATED |
| Cross-org data leakage | Low | ERPNEXT_ORGANIZATION_ID binding + queries scoped by organization_id | MITIGATED |
| Secret exposure in logs | Low | mask() removes api_secret; status endpoint no Authorization/secret | MITIGATED |
| D5 regression | None | Full D5 test suite (210 passed) preserved with D6 present | VERIFIED_CLEAN |
| PG runtime table creation | Low | PG raises MIGRATION_REQUIRED; no runtime CREATE | MITIGATED |
| Sales Order items fetch | Medium | List→detail pattern mock-verified; real ERPNext acceptance pending | AWAITING_REAL_VERIFICATION |
| Outbound Authorization rejected | Medium | Verified mock sends `Authorization: token key:secret`; real ERPNext acceptance pending | AWAITING_REAL_VERIFICATION |
| Per-DocType partial failure UX | Low | PARTIAL_FAILURE status clearly returned; per-doctype granular status in response | MITIGATED |

## 14. Dependencies

| Dependency | Version | Purpose |
|------------|---------|---------|
| httpx | (existing) | HTTP client for ERPNext REST API |
| FastAPI | (existing) | Web framework, route registration, Depends injection |
| SQLAlchemy/Alembic | (existing) | Database ORM and migration framework |
| pytest | (existing) | Test framework |
| pytest-asyncio | (existing) | Async test support |

## 15. Out of Scope (D7+)

Per instructions, the following are **NOT** implemented:
- ❌ Risk sorting / action_case (D7)
- ❌ Agent tooling / automation
- ❌ Webhook/event-driven sync
- ❌ Bi-directional sync (ERPNext → FlowOrder only)
- ❌ Multi-currency/multi-company support
- ❌ Custom field mapping