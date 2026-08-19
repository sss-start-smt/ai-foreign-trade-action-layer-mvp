# D6 Test Report: ERPNext Read-Only Integration

**Test Date:** 2026-08-11  
**Test Command:** `PYTHONPATH=. python -m pytest -q`  
**Result:** ✅ 271 passed, 23 skipped, 0 failed  
**D6 Tests:** 61 passed, 0 failed  
**REAL_ERPNEXT_NOT_VERIFIED:** All tests use mocked HTTP clients. No real ERPNext instance was contacted.

---

## 1. Test Summary

| Metric | Value |
|--------|-------|
| Full suite | 271 passed, 23 skipped |
| D6 tests only | 61 passed, 0 failed |
| D5 existing + other | 210 passed, 23 skipped |
| Duration | ~56s (full suite) |
| Test classes (D6) | 19 |

## 2. Test Coverage Matrix

### 2.1 Test Isolation (1 test)
| # | Test | Status |
|---|------|--------|
| 1 | `TestNoDBPathPollution::test_db_path_not_modified_by_import` | ✅ |

### 2.2 Client Read-Only & Auth (6 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestERPNextClient::test_no_write_methods` | ✅ |
| 2 | `TestERPNextClient::test_outbound_auth_header_sent` | ✅ |
| 3 | `TestERPNextClient::test_no_auth_header_when_not_configured` | ✅ |
| 4 | `TestERPNextClient::test_auth_header_in_mock_call` | ✅ |
| 5 | `TestERPNextClient::test_frappe_filter_array_format` | ✅ |
| 6 | `TestERPNextClient::test_order_by_in_params` | ✅ |

### 2.3 Secret Handling (3 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestSecretHandling::test_mask_no_secret` | ✅ |
| 2 | `TestSecretHandling::test_status_no_secret` | ✅ |
| 3 | `TestSecretHandling::test_secret_not_in_logs` | ✅ |

### 2.4 Normalization (4 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestERPNextNormalizer::test_normalize_sales_order` | ✅ |
| 2 | `TestERPNextNormalizer::test_normalize_customer` | ✅ |
| 3 | `TestERPNextNormalizer::test_normalize_item` | ✅ |
| 4 | `TestERPNextNormalizer::test_erp_owner_not_mapped` | ✅ |

### 2.5 Sales Order List→Detail (3 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestSalesOrderListDetail::test_list_fields_exclude_items` | ✅ |
| 2 | `TestSalesOrderListDetail::test_detail_fields_include_items` | ✅ |
| 3 | `TestSalesOrderListDetail::test_list_then_detail_flow` | ✅ |

### 2.6 Full Sync (6 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestERPNextFullSync::test_full_sync_sales_orders` | ✅ |
| 2 | `TestERPNextFullSync::test_full_sync_customers` | ✅ |
| 3 | `TestERPNextFullSync::test_full_sync_items` | ✅ |
| 4 | `TestERPNextFullSync::test_full_sync_pagination` | ✅ |
| 5 | `TestERPNextFullSync::test_full_sync_hash_unchanged_means_zero_changed` | ✅ |
| 6 | `TestERPNextFullSync::test_full_sync_per_doctype_stats` | ✅ |

### 2.7 Incremental Sync (6 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestERPNextIncrementalSync::test_incremental_with_real_cursor_filter_format` | ✅ |
| 2 | `TestERPNextIncrementalSync::test_incremental_no_cursor_falls_back_full` | ✅ |
| 3 | `TestERPNextIncrementalSync::test_incremental_boundary_overlap_idempotent` | ✅ |
| 4 | `TestERPNextIncrementalSync::test_incremental_cursor_advances_after_success` | ✅ |
| 5 | `TestERPNextIncrementalSync::test_incremental_empty_cursor_preserves_old` | ✅ |
| 6 | `TestERPNextIncrementalSync::test_incremental_cross_page_boundary` | ✅ |

### 2.8 Idempotency (1 test)
| # | Test | Status |
|---|------|--------|
| 1 | `TestERPNextIdempotency::test_double_sync_idempotent` | ✅ |

### 2.9 Cursor Management (3 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestERPNextCursorManagement::test_cursor_advances_on_success` | ✅ |
| 2 | `TestERPNextCursorManagement::test_cursor_not_advanced_on_failure` | ✅ |
| 3 | `TestERPNextCursorManagement::test_cursor_none_when_no_source_records` | ✅ |

### 2.10 Error Handling (6 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestERPNextErrorHandling::test_timeout_preserves_snapshot` | ✅ |
| 2 | `TestERPNextErrorHandling::test_5xx_preserves_snapshot` | ✅ |
| 3 | `TestERPNextErrorHandling::test_401_auth_failure` | ✅ |
| 4 | `TestERPNextErrorHandling::test_403_permission_denied` | ✅ |
| 5 | `TestERPNextErrorHandling::test_malformed_json` | ✅ |
| 6 | `TestERPNextErrorHandling::test_network_error` | ✅ |

### 2.11 Freshness Semantics (7 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestFreshnessSemantics::test_stale_priority_over_fresh` | ✅ |
| 2 | `TestFreshnessSemantics::test_unavailable_priority` | ✅ |
| 3 | `TestFreshnessSemantics::test_never_synced_with_failure_is_unavailable` | ✅ |
| 4 | `TestFreshnessSemantics::test_fresh_becomes_stale_after_timeout` | ✅ |
| 5 | `TestFreshnessSemantics::test_fresh_stays_fresh_within_threshold` | ✅ |
| 6 | `TestFreshnessSemantics::test_no_state_is_never_synced` | ✅ |
| 7 | `TestFreshnessSemantics::test_old_snapshot_failure_does_not_show_fresh` | ✅ |

### 2.12 Per-DocType Independent Success (2 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestPerDocTypeIndependentSuccess::test_customer_success_item500_customer_state_written` | ✅ |
| 2 | `TestPerDocTypeIndependentSuccess::test_failed_doctype_does_not_block_others` | ✅ |

### 2.13 Organization Binding (3 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestOrgBinding::test_sync_denied_for_unbound_org` | ✅ |
| 2 | `TestOrgBinding::test_sync_denied_for_wrong_org` | ✅ |
| 3 | `TestOrgBinding::test_sync_allowed_for_bound_org` | ✅ |

### 2.14 RBAC + Snapshots RBAC (5 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestSnapshotsRBAC::test_viewer_cannot_access_snapshots` | ✅ |
| 2 | `TestSnapshotsRBAC::test_manager_can_access_snapshots` | ✅ |
| 3 | `TestERPNextRBAC::test_viewer_cannot_sync` | ✅ |
| 4 | `TestERPNextRBAC::test_viewer_can_view_status` | ✅ |
| 5 | `TestERPNextRBAC::test_manager_can_sync` | ✅ |

### 2.15 PG Runtime Behavior (1 test)
| # | Test | Status |
|---|------|--------|
| 1 | `TestPGRuntimeBehavior::test_pg_missing_tables_raises_not_creates` | ✅ |

### 2.16 Edge Cases (2 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestERPNextEdgeCases::test_empty_response` | ✅ |
| 2 | `TestERPNextEdgeCases::test_partial_failure` | ✅ |

### 2.17 Full Flow (2 tests)
| # | Test | Status |
|---|------|--------|
| 1 | `TestERPNextFullFlow::test_full_then_incremental` | ✅ |
| 2 | `TestERPNextFullFlow::test_status_endpoint_no_secret` | ✅ |

## 3. Requirement Coverage

| # | Requirement | Tests | Status |
|---|-------------|-------|--------|
| 1 | 三DocType读取 (SO/Customer/Item) | normalization + full sync tests | ✅ |
| 2 | Full按limit_start/limit_page_length分页 | `test_full_sync_pagination` | ✅ |
| 3 | Incremental按[["modified",">=",cursor]]数组 | `test_incremental_with_real_cursor_filter_format` | ✅ |
| 4 | 边界重复幂等Upsert | `test_incremental_boundary_overlap_idempotent`, `test_incremental_cross_page_boundary` | ✅ |
| 5 | Per-DocType独立cursor推进 | `test_cursor_advances_on_success`, `test_customer_success_item500_customer_state_written` | ✅ |
| 6 | 失败不删旧快照/不推进Cursor | `test_timeout_preserves_snapshot`, `test_5xx_preserves_snapshot`, `test_cursor_not_advanced_on_failure` | ✅ |
| 7 | NEVER_SYNCED/FRESH/STALE/UNAVAILABLE | 7 freshness tests + error handling tests | ✅ |
| 8 | sync_status=STALE优先STALE | `test_stale_priority_over_fresh` | ✅ |
| 9 | 从未成功且本次来源失败→UNAVAILABLE | `test_never_synced_with_failure_is_unavailable` | ✅ |
| 10 | 旧快照失败不得显示FRESH | `test_old_snapshot_failure_does_not_show_fresh` | ✅ |
| 11 | 401/403显式失败(UNAVAILABLE) | `test_401_auth_failure`, `test_403_permission_denied` | ✅ |
| 12 | malformed JSON不污染 | `test_malformed_json` | ✅ |
| 13 | Secret脱敏(无api_secret/Authorization in API响应) | `test_mask_no_secret`, `test_status_no_secret`, `test_secret_not_in_logs` | ✅ |
| 14 | 无POST/PUT/PATCH/DELETE | `test_no_write_methods` | ✅ |
| 15 | owner不误映射 | `test_erp_owner_not_mapped` | ✅ |
| 16 | Sales Order list→detail模式 | `test_list_fields_exclude_items`, `test_detail_fields_include_items`, `test_list_then_detail_flow` | ✅ |
| 17 | 跨组织隔离(org绑定) | `test_sync_denied_for_unbound_org`, `test_sync_denied_for_wrong_org`, `test_sync_allowed_for_bound_org` | ✅ |
| 18 | RBAC (Manager/Admin) | `test_viewer_cannot_sync`, `test_manager_can_sync` + `test_viewer_cannot_access_snapshots`, `test_manager_can_access_snapshots` | ✅ |
| 19 | DB_PATH测试隔离(import不污染) | `test_db_path_not_modified_by_import` | ✅ |
| 20 | PG运行时不CREATE表 | `test_pg_missing_tables_raises_not_creates` | ✅ |
| 21 | hash未变→records_changed=0 | `test_full_sync_hash_unchanged_means_zero_changed` | ✅ |
| 22 | 每doctype独立seen/changed | `test_full_sync_per_doctype_stats` | ✅ |
| 23 | 无source记录cursor不伪造 | `test_incremental_empty_cursor_preserves_old`, `test_cursor_none_when_no_source_records` | ✅ |
| 24 | 稳定order_by | `test_order_by_in_params` | ✅ |
| 25 | Outbound Authorization header真实发出 | `test_outbound_auth_header_sent`, `test_auth_header_in_mock_call` | ✅ |
| 26 | Snapshots端点RBAC(Manager/Admin) | `test_viewer_cannot_access_snapshots`, `test_manager_can_access_snapshots` | ✅ |
| 27 | Per-DocType独立成功(失败不阻塞) | `test_customer_success_item500_customer_state_written`, `test_failed_doctype_does_not_block_others` | ✅ |
| 28 | get_doc URL安全编码 | (verified via `test_list_then_detail_flow` using encoded name) | ✅ |

## 4. D5 Baseline Regression Check

Full pytest with D6 present passes cleanly:

```
$ PYTHONPATH=. python -m pytest -q
271 passed, 23 skipped, 0 failed
```

| Suite | Tests | Notes |
|-------|-------|-------|
| Full suite (D3/D4/D5/D6 + other) | **271 passed, 23 skipped** | Zero failures |
| D6 test module only (`test_d6_erpnext_readonly.py`) | 61 passed, 0 failed | 19 test classes |
| D5 Excel module only (`test_d5_excel_import.py`) | 25 passed | No D5 regression |
| Other existing tests (auth, agent, v61, migrations, etc.) | 185 passed, 23 skipped | No regressions |

**Conclusion:** D5 baseline has **NOT regressed**. Running `PYTHONPATH=. pytest -q` → **0 failed**. The D5 Excel chain remains untouched and passes independently (25 passed) alongside D6 (61 passed).

## 5. Test Details

### 5.1 Mock Strategy
- `MockErpClient` extends `ERPNextReadOnlyClient`, captures outbound headers in call log
- Responses keyed by URL path, supporting both static and callable responses
- Callable responses enable pagination simulation (different responses per page)
- HTTP error codes (401/403/5xx) raised as `ERPNextClientError` with proper error codes
- `httpx.TimeoutException` and `httpx.ConnectError` simulated via `_raise_error`
- Outbound `Authorization` header verified via `test_auth_header_in_mock_call`

### 5.2 Database Isolation
- Module-scoped fixture `_db_path` uses `tmp_path_factory` for unique DB per module
- Module-scoped fixture `_setup_db` with autouse saves/restores `DB_PATH` env var
- Tests import时不修改DB_PATH — verified by `TestNoDBPathPollution`
- ERP tables (`erp_sync_state`, `erp_read_snapshots`) cleaned between tests
- D5/D6 tables isolated, no cross-contamination

### 5.3 Auth Simulation
- Uses existing `auth.DEMO_TOKEN_MAP` for token-based identity
- `_auth_headers()` maps user_id → token via reversed map
- `setup_client` fixture provides `TestClient` with FastAPI app
- Role-based access verified via Manager-1 / USER-1 tokens
- Snapshots RBAC verified: viewer blocked (403), manager allowed (200)

## 6. Known Limitations

1. **REAL_ERPNEXT_NOT_VERIFIED:** No real ERPNext API calls were made during testing
2. **No Pagination Stress Test:** Pagination tested with multiple pages, not production-scale volumes
3. **No Network Latency Simulation:** Timeout tested via direct exception, not actual latency
4. **SQLite-Only:** PostgreSQL compatibility inferred from SQL dialect + mock connection test, not tested on real PG
5. **No Concurrent Sync:** Single-threaded tests; concurrent sync behavior not verified
6. **Frappe Filter Array Format:** Verified in mock; real ERPNext acceptance pending
7. **Sales Order List→Detail:** Mock-verified; real ERPNext items field behavior pending
8. **Authorization Token Format:** Verified mock sends `Authorization: token key:secret`; real ERPNext acceptance pending