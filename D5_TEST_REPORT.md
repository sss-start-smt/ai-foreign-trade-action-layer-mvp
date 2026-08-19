# D5 Test Report

**Date**: 2026-08-11  
**Test File**: `tests/test_d5_excel_import.py`  
**Test Framework**: pytest  
**Revision**: R3 (2026-08-11, final closeout fix for no-source-line-key multi-line duplicate)

---

## 0. R1 → R2 测试回顾

### R1 独立验收测试结果

| 指标 | 数值 |
|------|------|
| Full Regression | 198 passed / 2 failed / 26 skipped |
| D5 Focused | 18 passed |
| D4 Regression | `test_d4_r2_multi_line_order_persistence` FAILED |
| 测试污染 | D5 模块级 `os.environ` 污染 `test_api.py` |
| 假绿色测试 | Retry/Correction 缺少真实 failure injection |

### R2 目标
1. 修复所有 D5 相关 bug
2. 消除测试环境污染
3. 增加真实 failure injection 测试
4. 修复 D4 regression
5. 验证 full regression 全绿

---

## 0.5 R3 测试回顾：无 source_line_key 多行重复误判

### R3 问题

R2 修复后，在无 `source_line_key` 的多行 Excel 二次上传场景中仍存在误判：
- `_preview_rows()` 在 `order_lines` 中找到完全一致的 line facts（`found_match=True`）
- 但旧的 `line_level_changes`（基于 orders 表第一条产品生成）仍非空
- 导致 `DUPLICATE_NOOP` 被错误覆盖为 `LINE_IDENTITY_AMBIGUOUS`

### R3 目标

1. 修复 `elif not src_line_key and existing_lines` 分支逻辑
2. `found_match=True` → 直接 `DUPLICATE_NOOP`，跳过 `line_level_changes`
3. `found_match=False` → `LINE_IDENTITY_AMBIGUOUS`，使用准确 ambiguity 描述
4. 验证 D5 focused 全绿 + D4 regression 通过
5. 验证 full regression 0 failed

---

## 1. Test Summary

| Metric | R1 | R2 | R3 |
|--------|----|----|----|
| Total D5 Tests | 18 | 24 | 25 |
| Passed | 18 | **24** | **25** |
| Failed | 0 | **0** | **0** |
| Skipped | 0 | 0 | 0 |
| Error | 0 | 0 | 0 |
| **Pass Rate** | **100%** | **100%** | **100%** |

---

## 2. Test Case Results

### R1 原有 18 项 (全部通过)

| # | Test Case | R1 | R2 |
|---|-----------|----|----|
| 01 | First import → SUCCESS | ✅ PASSED | ✅ PASSED |
| 02 | Re-upload same data → DUPLICATE_NOOP | ✅ PASSED | ✅ PASSED |
| 03 | Existing conflict → CONFLICT (blocked commit) | ✅ PASSED | ✅ PASSED |
| 04 | Explicit apply_correction → CORRECTED | ✅ PASSED | ✅ PASSED |
| 05 | Intra-batch conflict → CONFLICT_IN_BATCH | ✅ PASSED | ✅ PASSED |
| 06 | Line duplicate (identical facts) → DUPLICATE_NOOP | ✅ PASSED | ✅ PASSED |
| 07 | Line facts differ (no source_line_key) → LINE_IDENTITY_AMBIGUOUS | ✅ PASSED | ✅ PASSED |
| 08 | Line identity match (no source_line_key) → DUPLICATE_NOOP | ✅ PASSED | ✅ PASSED |
| 09 | Line facts differ → LINE_IDENTITY_AMBIGUOUS | ✅ PASSED | ✅ PASSED |
| 10 | Owner empty → BLOCK | ✅ PASSED | ✅ PASSED |
| 11 | Owner unresolved → BLOCK | ✅ PASSED | ✅ PASSED |
| 12 | Multi-batch partial import | ✅ PASSED | ✅ PASSED |
| 13 | Retry with no failures → 400 error | ✅ PASSED | ✅ PASSED |
| 14 | Retry idempotency | ✅ PASSED | ✅ PASSED |
| 15 | Retry re-preflight on state change | ✅ PASSED | ✅ PASSED |
| 16 | Correction atomicity | ✅ PASSED | ✅ PASSED |
| 17 | Report shows duplicate_noop_count | ✅ PASSED | ✅ PASSED |
| 18 | Correction creates audit record | ✅ PASSED | ✅ PASSED |

### R2 新增 6 项

| # | Test Case | Fix Ref | Status |
|---|-----------|---------|--------|
| 19 | Retry with failed orders (真实失败重试) | FIX 01 | ✅ PASSED |
| 20 | Multi-line exact re-upload → DUPLICATE_NOOP (order_lines 匹配) | FIX 02, 07 | ✅ PASSED |
| 21 | Line correction via _update_order_line (非 INSERT) | FIX 04, 08 | ✅ PASSED |
| 22 | Intra-batch: delivery_date 差异不触发 CONFLICT_IN_BATCH | FIX 03 | ✅ PASSED |
| 23 | Correction failure injection → rollback (monkeypatch) | FIX 05 | ✅ PASSED |
| 24 | Retry failure injection → 400 on retry (monkeypatch) | FIX 06 | ✅ PASSED |

### R3 新增 1 项

| # | Test Case | Fix Ref | Status |
|---|-----------|---------|--------|
| 25 | Multi-line exact re-upload without source_line_key → all DUPLICATE_NOOP | FIX R3-01, 02, 03 | ✅ PASSED |

---

## 3. D5 Focused Test Results

### R2 最终结果

```
tests/test_d5_excel_import.py ....... 24 passed
```

All 24 D5-specific tests pass successfully.

### R3 最终结果

```
tests/test_d5_excel_import.py ....... 25 passed
```

All 25 D5-specific tests pass successfully. R3 新增 `test_d5_r3_multi_line_exact_reupload_without_source_line_key` 验证通过：
- 无 `source_line_key` 的多行 Excel 二次上传全部 `DUPLICATE_NOOP`
- DB 中 `orders=1`, `order_lines=3` 不变

---

## 4. D4 Regression Results

### R1 状态: FAILED

`test_d4_r2_multi_line_order_persistence` 在 R1 中失败，根因是 D5 的多行重复检测仅基于 orders 表的 `product_name`/`quantity` 字段，导致多行订单的第二条及以后行被误判为 `LINE_CONFLICT`。

### R2 状态: PASSED ✅

修复后，D4 所有回归测试通过：

```
tests/test_api.py::test_d4_r2_multi_line_order_persistence PASSED
```

**修复根因**: FIX 02 改用 `order_lines` 表构建 `order_lines_index`，精确匹配每一行的实际数据，不再误判。

### R3 状态: PASSED ✅

R3 修复未影响 D4 回归：

```
tests/test_api.py::test_d4_r2_multi_line_order_persistence PASSED
```

---

## 5. Full Regression Results (R3)

```
210 passed, 0 failed, 23 skipped
```

| Suite | R1 | R2 | R3 |
|-------|----|----|----|
| D5 Focused (test_d5_excel_import.py) | 18 passed | 24 passed | **25 passed** |
| D4 Regression (test_api.py) | 1 failed | 0 failed | **0 failed** |
| **Full Pytest Total** | **198 passed / 2 failed / 26 skipped** | **209 passed / 0 failed / 23 skipped** | **210 passed / 0 failed / 23 skipped** |

**R3 提升**: +1 passed, 0 failed, 核心 Gate 维持 **0 failed**

### 与独立环境对比说明

| 环境 | Passed | Failed | Skipped |
|------|--------|--------|---------|
| Trae execution environment | 210 | 0 | 23 |
| ChatGPT independent environment | 206 | 0 | 26 |

**核心 Gate**: `0 failed` — 两个环境均通过。passed/skipped 数字因环境不同可如实记录，以 Trae 执行环境为准。

---

## 6. Test Coverage Analysis

### Covered Scenarios

| Scenario | Test Cases | Coverage |
|----------|------------|----------|
| First import creates order | 01 | ✅ |
| Duplicate detection (same data) | 02, 06, 08, 20 | ✅ |
| Conflict detection (order-level) | 03, 04 | ✅ |
| Intra-batch conflict (order-level only) | 05, 22 | ✅ |
| Line-level conflict | 07, 09 | ✅ |
| Line-level correction (UPDATE not INSERT) | 21 | ✅ |
| Correction application | 04, 16, 18, 21 | ✅ |
| Owner validation | 10, 11 | ✅ |
| Multi-order batch | 12 | ✅ |
| Multi-line re-upload exact match | 20 | ✅ |
| Multi-line no-source-line-key exact re-upload | 25 | ✅ |
| Retry (no failures) | 13 | ✅ |
| Retry (with failed orders) | 14, 19 | ✅ |
| State-change re-preflight | 15 | ✅ |
| Correction failure injection | 23 | ✅ |
| Retry failure injection | 24 | ✅ |
| Report extension | 17 | ✅ |
| Audit trail | 18 | ✅ |

### R2 新增覆盖

| 场景 | 覆盖 |
|------|------|
| 真实 failure injection (monkeypatch) | ✅ Cases 23-24 |
| 多行 exact re-upload 不误判 | ✅ Case 20 |
| Line correction 走 _update_order_line | ✅ Case 21 |
| INTRA_BATCH 仅订单级字段 | ✅ Case 22 |
| Retry 真实失败重试流程 | ✅ Case 19 |

### R3 新增覆盖

| 场景 | 覆盖 |
|------|------|
| 无 source_line_key 多行二次上传全部 DUPLICATE_NOOP | ✅ Case 25 |
| found_match=True 跳过 line_level_changes | ✅ Case 25 |
| DB orders/order_lines 数量不变验证 | ✅ Case 25 |

### 仍需未来测试数据

- **source_line_key present**: Current CSV format doesn't expose `source_line_key`. LINE_CONFLICT test with actual `source_line_key` requires CSV header extension.
- **混合变更部分成功**: 多字段同时变更，仅部分字段纠正成功的边界场景
- **PG runtime verification**: 未在 PostgreSQL 环境执行

---

## 7. Key Assertions Validated

### Duplicate Prevention
- Re-importing same Excel → `DUPLICATE_NOOP`
- No duplicate orders created (order count stays at 1)
- No duplicate order_lines (line count unchanged) — **R2 验证: 基于 order_lines 表**
- `duplicate_noop_count` incremented correctly

### Multi-line Duplicate (R2 FIX 02 + R3 FIX R3-01)
- 多行订单第二条及以后行不再被误判
- 重新上传完全相同的多行订单 → 所有行均判定为 `DUPLICATE_NOOP`
- `order_lines` 表行数不变
- **R3**: 无 `source_line_key` 的多行 Excel 二次上传全部 `DUPLICATE_NOOP`（Case 25）
- **R3**: `found_match=True` 直接 `DUPLICATE_NOOP`，不被 `line_level_changes` 覆盖
- **R3**: DB 中 `orders=1`, `order_lines=3` 不变

### Conflict Detection
- Changed delivery_date → `CONFLICT_EXISTING`
- Intra-batch contradictory order-level data → `CONFLICT_IN_BATCH`
- **R2**: Intra-batch delivery_date 差异 → 不触发 `CONFLICT_IN_BATCH` (Case 22)
- Commit without resolution → `UNRESOLVED_IMPORT_CONFLICT` (400)
- Original data preserved when conflict not resolved

### Correction (R2 FIX 04)
- `apply_correction` → order updated + `corrected_count=1`
- delivery_date changes from 2026-09-01 to 2026-09-10
- Order status in result = "CORRECTED"
- **R2**: Line-level correction 走 `_update_order_line`，`order_lines` 数量不变

### Correction Failure Injection (R2 FIX 05)
- monkeypatch `_insert_correction_record` 抛异常 → 事务回滚
- monkeypatch `_insert_order` 抛异常 → 事务回滚
- 纠正记录写入失败 → order/line 数据不变

### Owner Validation
- Empty owner → BLOCK (error >= 1)
- Unknown owner → BLOCK (error >= 1)
- No orders created for blocked rows

### Retry (R2 FIX 01)
- Retry with no failures → 400 error ("no retryable")
- Order count unchanged after retry of successful batch
- **R2**: Retry 正确提取 `raw_json`，不再依赖 `order_no` 字段
- **R2**: Retry 使用正确 `_preview_rows` 签名
- **R2**: 重试批次保存原始行数据

### Retry Failure Injection (R2 FIX 06)
- monkeypatch retry 流程中的 `_insert_order` → 返回 COMMIT_FAILED
- 失败订单可被重试

---

## 8. Test Infrastructure (R2 FIX 11)

### R1 问题
- D5 test module 在 module level 设置 `os.environ`，污染全局环境
- 影响 `test_api.py` 的 `communication_drafts` 表测试

### R2 修复
- 改用 pytest fixture 管理环境变量
- 使用 `monkeypatch` fixture 替代模块级 `os.environ`
- 测试隔离：每个测试独立管理自己的环境变量

### Environment
- Python 3.10.11
- pytest 8.3.3
- SQLite (test_d5.db, test_d5_debug.db)
- FastAPI TestClient
- **R2**: pytest fixture 管理环境变量

---

## 9. Verification Checklist

- [x] D5 classification logic implemented
- [x] D5 classification logic tested (24/24 pass)
- [x] Duplicate prevention verified (including multi-line)
- [x] Conflict detection verified
- [x] Correction mechanism verified (line-level UPDATE)
- [x] Owner validation verified
- [x] Retry mechanism verified
- [x] Retry bug (IndexError) fixed
- [x] Multi-line duplicate bug fixed
- [x] INTRA_BATCH field restriction verified
- [x] Correction INSERT → UPDATE fix verified
- [x] Failure injection tests added
- [x] Multi-line re-upload tests added
- [x] Test isolation (fixture-based) verified
- [x] D4 regression passed
- [x] Full regression: 210 passed, 0 failed, 23 skipped
- [x] R3: 无 source_line_key 多行重复修复验证（Case 25）
- [x] R3: found_match=True → DUPLICATE_NOOP 逻辑验证
- [x] R3: D4 regression 未受影响（R3 PASSED）
- [x] Import report extension verified
- [x] D5 schema migration created
- [x] No unauthorized schema changes
- [x] Backward compatibility maintained
- [ ] PG runtime verification (known debt)

---

TEST_REPORT_COMPLETE_R3