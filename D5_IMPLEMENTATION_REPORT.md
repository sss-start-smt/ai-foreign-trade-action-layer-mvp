# D5 Implementation Report: Excel Duplicate, Conflict, Correction & Retry

**Status**: IMPLEMENTATION_READY_FOR_REVIEW  
**Date**: 2026-08-11  
**Author**: FlowOrder D5 Implementation  
**Revision**: R3 (2026-08-11, final closeout fix for no-source-line-key multi-line duplicate)

---

## 0. R1 → R2 修订记录

### 上一版 (R1) 独立验收发现的问题

| # | 问题类别 | 描述 | 严重度 |
|---|----------|------|--------|
| 1 | 测试失败 | Full regression: 198 passed / 2 failed / 26 skipped | 高 |
| 2 | D4 regression | `test_d4_r2_multi_line_order_persistence` 回归失败 | 高 |
| 3 | 环境测试污染 | D5 模块级 `os.environ` 设置污染全局，影响 `test_api.py` | 高 |
| 4 | 假绿色测试 | Retry/Correction 缺少真实 failure injection，测试形同虚设 | 中 |
| 5 | Retry Bug | `order_import_rows.order_no` 字段不存在，读取导致 `IndexError` | 高 |
| 6 | Retry Bug | Retry 调用 `_preview_rows` 参数签名错误 | 高 |
| 7 | Retry Bug | 重试批次未正确保存原始行数据 | 高 |
| 8 | 多行重复 Bug | 仅用 orders 表的 `product_name`/`quantity` 对比，未查询 `order_lines` | 高 |
| 9 | 多行重复 Bug | 多行订单第二条及以后行被误判为 `LINE_CONFLICT` | 高 |
| 10 | Correction Bug | `apply_correction` 对 `LINE_CONFLICT` 行无条件 INSERT 新 `order_line` | 中 |
| 11 | Correction Bug | 纠正后 `order_lines` 数量不当增加 | 中 |
| 12 | INTRA_BATCH Bug | 检测未限制为订单级字段，`delivery_date` 等行级字段差异误触发 `CONFLICT_IN_BATCH` | 高 |

### R2 修复清单

| Fix # | 类别 | 修复内容 |
|-------|------|----------|
| FIX 01 | Retry 重写 | 从原 batch 提取失败行 `raw_json`，使用正确 `_preview_rows(conn, records, mapping, user_id)` 签名 |
| FIX 02 | Line Duplicate/Conflict | 改用 `order_lines` 表查询，构建 `order_lines_index` |
| FIX 03 | INTRA_BATCH | 限制为 `INTRA_BATCH_ORDER_LEVEL_FIELDS` (`customer_name`, `owner`, `supplier_name`, `order_status`) |
| FIX 04 | Correction | 区分 order-level 与 line-level，line-level 用 `_update_order_line` 而非 INSERT |
| FIX 05-06 | 真实 Failure Injection | monkeypatch `_insert_correction_record` / `_insert_order` 注入真实失败 |
| FIX 07-08 | 多行自动化测试 | 多行 exact re-upload / line correction 自动化测试 |
| FIX 09 | event_logs | 删除 `changes` 字段，仅保留 `batch_id`/`order_id`/`correction_id` |
| FIX 10 | 前端支持 | 新分类状态、`order_actions`、retry UI |
| FIX 11 | 测试隔离 | 改用 pytest fixture 管理环境变量，消除模块级污染 |

---

## 0.5 R3 修订记录：无 source_line_key 多行重复误判修复

### R3 Bug 根因

在无 `source_line_key` 的多行 Excel 二次上传场景中：

- `_preview_rows()` 已在 `order_lines` 中找到完全一致的 line facts（`found_match=True`）
- 但旧的 `line_level_changes`（基于 orders 表第一条产品生成）仍然非空
- 导致 `DUPLICATE_NOOP` 被错误覆盖为 `LINE_IDENTITY_AMBIGUOUS`

**本质**：`elif not src_line_key and existing_lines` 分支中，`found_match=True` 本应直接判定为 `DUPLICATE_NOOP`，但被残留的 `line_level_changes` 覆盖。

### R3 修复方案

在 `elif not src_line_key and existing_lines` 分支中：

| 条件 | 修复前 | 修复后 |
|------|--------|--------|
| `found_match=True` | 仍检查 `line_level_changes`，可能误判为 `LINE_IDENTITY_AMBIGUOUS` | 直接判定 `DUPLICATE_NOOP`，不再检查 `line_level_changes` |
| `found_match=False` | 使用假的 old/new line 对比生成 `conflict_details` | 判定 `LINE_IDENTITY_AMBIGUOUS`，`conflict_details` 改为准确的 ambiguity 描述 |

### R3 修复清单

| Fix # | 类别 | 修复内容 |
|-------|------|----------|
| FIX R3-01 | 多行重复检测 | `found_match=True` → 直接 `DUPLICATE_NOOP`，跳过 `line_level_changes` 检查 |
| FIX R3-02 | 冲突详情准确性 | `found_match=False` → `LINE_IDENTITY_AMBIGUOUS` 使用准确 ambiguity 描述，不再用假的 old/new line 对比 |
| FIX R3-03 | 新增测试 | `test_d5_r3_multi_line_exact_reupload_without_source_line_key` 验证无 source_line_key 多行二次上传全部 `DUPLICATE_NOOP` |

---

## 1. Overview

D5 solves four critical Excel import issues:

1. **Duplicate Prevention**: Re-importing identical data no longer creates duplicate orders or order lines.
2. **Conflict Detection**: Business fact differences are detected as conflicts, never silently overwritten.
3. **Correction Mechanism**: Users can explicitly apply corrections with full audit trail.
4. **Safe Retry**: Only failed orders can be retried; successful orders are never re-executed.

---

## 2. Files Modified

| File | Action | Purpose |
|------|--------|---------|
| `excel_import_patch.py` | Modified | Core D5 logic: classification, conflict detection, correction, retry |
| `alembic/versions/c8d9e1f2a3b4_d5_conflict_and_retry.py` | Created | D5 schema migration |
| `tests/test_d5_excel_import.py` | Modified | 24 D5 test cases (R2 expanded from 18) |

---

## 3. Duplicate Detection Rules

### Exact Duplicate (DUPLICATE_NOOP)
- **Condition**: Same `source_order_key` exists AND all business facts are identical
- **Action**: No INSERT, no UPDATE, no new order_lines
- **Counted**: `duplicate_noop_count` incremented
- **Status**: Not BLOCKED, not FAILED - silently skipped

### Order-level Duplicate
- When an entire row matches an existing order's facts exactly → `DUPLICATE_NOOP`

### Line-level Duplicate (R2 FIX 02)
- Duplicate detection now queries the `order_lines` table directly via `order_lines_index`
- Same `source_line_key` + identical line facts → `DUPLICATE_NOOP`
- No `source_line_key` but line facts match existing `order_lines` → `DUPLICATE_NOOP`
- **R2 修复**: 不再仅依赖 orders 表的 `product_name`/`quantity` 字段对比，而是基于 `order_lines` 表构建精确索引，彻底解决多行订单第二条及以后行被误判的问题
- **R3 修复**: 无 `source_line_key` 时，`found_match=True` 直接判定 `DUPLICATE_NOOP`，不再被残留的 `line_level_changes` 覆盖；`found_match=False` 则判定 `LINE_IDENTITY_AMBIGUOUS` 并使用准确的 ambiguity 描述

---

## 4. Conflict Detection Rules

### Existing Conflict (CONFLICT_EXISTING)
- **Condition**: Same `source_order_key` exists, order-level business facts differ
- **Order-level fields**: `customer_name`, `delivery_date`, `owner`, `supplier_name`, `order_status`
- **Action**: Preview shows conflict; Commit requires explicit `apply_correction` or `skip`

### Intra-batch Conflict (CONFLICT_IN_BATCH) — R2 FIX 03
- **Condition**: Multiple rows in same batch share `source_order_key` but have contradictory **order-level** facts only
- **R2 限制字段**: `INTRA_BATCH_ORDER_LEVEL_FIELDS` = `{customer_name, owner, supplier_name, order_status}`
- **R2 修复**: `delivery_date` 等行级字段差异不再触发 `CONFLICT_IN_BATCH`，仅订单级字段差异才判定为批次内冲突
- **Action**: Entire order blocked; user must fix source file and re-preview

### Line Conflict (LINE_CONFLICT) — R2 FIX 02
- **Condition**: Existing order, same `source_line_key`, line facts differ
- **Line-level fields**: `product_name`, `order_qty`, `completed_qty`, `notes`
- **R2 修复**: 基于 `order_lines` 表精确查询，正确识别 line-level 冲突
- **Action**: Blocked; requires explicit correction

### Line Identity Ambiguous (LINE_IDENTITY_AMBIGUOUS)
- **Condition**: Existing order, no `source_line_key`, line facts differ
- **Rationale**: Cannot determine if this is a new line or a correction
- **Action**: Blocked; user must provide `source_line_key` or clarifying data
- **R3 修复**: 仅在 `found_match=False` 时判定为此状态，`conflict_details` 使用准确的 ambiguity 描述（不再使用假的 old/new line 对比）

---

## 5. Correction Transaction Rules

### Atomic Correction
1. Correction runs within per-order savepoint/transaction
2. Order/line update + correction record are in the same transaction
3. If correction record write fails → full rollback of order/line changes
4. If order/line update fails → correction record not written

### Correction Target Resolution (R2 FIX 04)
- **Order-level correction**: Updates `orders` table fields directly
- **Line-level correction**: Uses `_update_order_line(line_id, updates)` to modify existing `order_lines` records
- **R2 修复**: `apply_correction` 对 `LINE_CONFLICT` 行不再无条件 INSERT 新 `order_line`，而是通过 `target_type` 区分 order-level 与 line-level 操作，line-level 走 UPDATE 而非 INSERT，纠正后 `order_lines` 数量保持不变

### Correction Record Structure
```
order_corrections:
  correction_id    TEXT PK
  order_id         TEXT NOT NULL
  source_order_key TEXT
  batch_id         TEXT
  actor_user_id    TEXT NOT NULL
  target_type      TEXT NOT NULL  (order | order_line)
  target_id        TEXT
  changes_json     TEXT NOT NULL  (field, old_value, new_value)
  created_at       TEXT NOT NULL
```

### Correction Audit (R2 FIX 09)
- `changes_json` stores: `[{field, old_value, new_value}]`
- `event_logs` stores only IDs (`batch_id`, `order_id`, `correction_id`) — **R2 删除了 `changes` 字段**
- Full audit chain: correction_id → order_id → batch_id → actor_user_id

---

## 6. Retry Scope

### Retry-eligible Status
Only `COMMIT_FAILED` orders can be retried. The following statuses are excluded:
- SUCCESS, SUCCESS_WITH_WARNING, CORRECTED, DUPLICATE_NOOP, BLOCKED, CONFLICT, SKIPPED

### Retry Mechanism (R2 FIX 01)
1. `POST /api/import/batches/{batch_id}/retry-failed`
2. Queries original batch's COMMIT_FAILED rows
3. **R2 修复**: 从原 batch 的 `order_import_rows` 中提取失败行的 `raw_json`（不再依赖不存在的 `order_no` 字段）
4. **R2 修复**: 使用正确的 `_preview_rows(conn, records, mapping, user_id)` 函数签名
5. Creates new retry child batch (`retry_of_batch_id`, `retry_attempt`)
6. Re-preflights against current database state
7. Returns new preview with fresh classification
8. User commits retry batch separately

### Retry Idempotency
- Successful orders are never included in retry
- Each retry creates a new batch; original batch history preserved
- Re-preflight may re-classify as DUPLICATE_NOOP or CONFLICT if DB state changed
- **R2 修复**: 重试批次正确保存原始行数据，不丢失字段

---

## 7. Classification Status Values

| Status | Description |
|--------|-------------|
| NEW | First import, no conflicts |
| DUPLICATE_NOOP | Exact match with existing data |
| CONFLICT_EXISTING | Order-level facts differ |
| CONFLICT_IN_BATCH | Intra-batch contradiction (order-level fields only) |
| LINE_CONFLICT | Line facts differ with source_line_key |
| LINE_IDENTITY_AMBIGUOUS | Line facts differ without identity |
| ERROR | Validation error |

---

## 8. Owner Resolution

| Error Code | Meaning |
|------------|---------|
| IMPORT_OWNER_MISSING | Owner field is empty |
| IMPORT_OWNER_UNRESOLVED | Owner value doesn't map to valid system user |

Both errors cause BLOCK. No auto-binding to current user or "待分配".

---

## 9. Import Report Extension

### New Summary Fields
- `duplicate_noop_count`
- `conflict_count`
- `corrected_count`
- `retryable_failed_count`

### New Order Result Statuses
- SUCCESS, SUCCESS_WITH_WARNING
- DUPLICATE_NOOP, CONFLICT, CORRECTED
- BLOCKED, COMMIT_FAILED, SKIPPED

---

## 10. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/import/preview` | Preview with D5 classification |
| POST | `/api/import/commit` | Commit with conflict resolution |
| POST | `/api/import/batches/{batch_id}/retry-failed` | Retry failed orders |
| GET | `/api/import/batches/{batch_id}` | Batch report with D5 fields |

### Commit Payload Extension
```json
{
  "batch_id": "...",
  "projection_hash": "...",
  "row_actions": {"row_number": "import|skip"},
  "order_actions": {"order_key": "apply_correction|skip"}
}
```

---

## 11. Security

- `event_logs` records only IDs (correction_id, order_id, batch_id, action type) — **R2 删除了 `changes` 字段**
- No customer names, product names, supplier names, or notes in event_logs
- Business correction details exist only in controlled `order_corrections` table
- RBAC enforced: only import creator or same-org manager can view/retry batches

---

## 12. Backward Compatibility

- D4 `Preview → projection_hash → Commit` flow preserved
- BLOCK/WARNING rules preserved
- Multi-line orders preserved
- Atomic per-order commit preserved
- Import Report and CSV Export extended (not replaced)
- Organization/RBAC unchanged
- All D3/D4 existing endpoints and responses maintained

---

## 13. Database Schema Changes

### New Table: `order_corrections`
- Fields: correction_id, order_id, source_order_key, batch_id, actor_user_id, target_type, target_id, changes_json, created_at
- Indexes on order_id and batch_id

### New Columns: `order_import_batches`
- retry_of_batch_id, retry_attempt
- duplicate_noop_count, conflict_count, corrected_count
- source_file_name, source_file_size, file_sha256, has_header, start_row
- organization_id, created_by

### New Columns: `order_import_rows`
- source_system, source_order_key, source_line_key
- conflict_type, conflict_details_json, order_action

### event_logs 变更 (R2 FIX 09)
- 删除 `changes` 字段
- 仅保留: `batch_id`, `order_id`, `correction_id`, `action_type`, `created_at`

---

## 14. PostgreSQL Status

**PG_RUNTIME_NOT_VERIFIED**

Migration SQL uses SQLite-compatible DDL with `_ensure_column` pattern. PostgreSQL path uses Alembic migration (`alembic upgrade head`) which is PostgreSQL-compatible. However, no actual PostgreSQL instance was available for runtime verification.

All SQL statements are SQLite-compatible. PostgreSQL runtime verification requires a dedicated PG instance and is tracked as known debt.

---

## 15. Remaining Known Debt

1. **PG runtime verification**: 未在 PostgreSQL 环境验证，所有 SQL 针对 SQLite
2. **source_line_key in CSV**: Current CSV format doesn't expose `source_line_key`. Line-level duplicate detection relies on factual matching. A future enhancement may add `source_line_key` column mapping.
3. **Batch-level retry**: Currently retries all COMMIT_FAILED orders in a batch. Future may support selective retry.
4. **Real-time conflict resolution**: Currently a manual process. Future may support auto-resolve rules for specific field types.
5. **Front-end UI (FIX 10)**: 已实现最小化支持（新分类状态、order_actions、retry UI），仍需完整前端 UI sprint
6. **更多边界场景**: 混合变更部分成功场景 (multiple fields changed, partial correction) 尚未覆盖自动化测试
7. **R3 已关闭**: 无 `source_line_key` 多行重复误判问题已在 R3 修复完成

---

IMPLEMENTATION_READY_FOR_REVIEW_R3