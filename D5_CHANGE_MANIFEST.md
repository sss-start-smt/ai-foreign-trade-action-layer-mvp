# D5 Change Manifest

**Date**: 2026-08-11  
**Scope**: Excel Duplicate, Conflict, Correction, and Retry  
**Revision**: R3 (2026-08-11, final closeout fix for no-source-line-key multi-line duplicate)

---

## 0. R2 修订概览

### R1 验收问题总结

| 问题 | 根因 | 影响 |
|------|------|------|
| 2 test failures in full regression | Multi-line duplicate + test pollution | 阻塞发布 |
| D4 regression 失败 | 多行订单行误判为 LINE_CONFLICT | 破坏 D4 向后兼容 |
| Retry IndexError | `order_import_rows.order_no` 字段不存在 | Retry 功能不可用 |
| Retry 参数错误 | `_preview_rows` 签名不匹配 | Retry 功能不可用 |
| Correction 行数膨胀 | LINE_CONFLICT 无条件 INSERT | 数据不一致 |
| INTRA_BATCH 误触发 | delivery_date 等行级字段差异 | 正常导入被阻塞 |
| 模块级 os.environ | 测试环境污染 | 其他测试失败 |
| 假绿色测试 | 无真实 failure injection | 质量无保障 |

### R2 修复统计

- **修改文件**: 3 个 (excel_import_patch.py, test_d5_excel_import.py, + 前端)
- **Fix 数量**: 11 项 (FIX 01 ~ FIX 11)
- **新增测试**: 6 项 (18 → 24)
- **Full regression**: 198/2 failed → 209/0 failed

---

## 0.5 R3 修订概览

### R3 问题总结

| 问题 | 根因 | 影响 |
|------|------|------|
| 无 source_line_key 多行二次上传误判 | `found_match=True` 被残留 `line_level_changes` 覆盖 | 多行重复被错误标记为 `LINE_IDENTITY_AMBIGUOUS` |
| LINE_IDENTITY_AMBIGUOUS conflict_details 不准确 | 使用假的 old/new line 对比而非真实 ambiguity 描述 | 用户看到误导性冲突详情 |

### R3 修复统计

- **修改文件**: 2 个 (`excel_import_patch.py`, `test_d5_excel_import.py`)
- **Fix 数量**: 3 项 (FIX R3-01 ~ FIX R3-03)
- **新增测试**: 1 项 (24 → 25)
- **Full regression**: 209/0 failed → 210/0 failed

### R3 修复映射

| Fix # | 类别 | 修复内容 |
|-------|------|----------|
| FIX R3-01 | 多行重复检测 | `found_match=True` → 直接 `DUPLICATE_NOOP`，跳过 `line_level_changes` 检查 |
| FIX R3-02 | 冲突详情准确性 | `found_match=False` → `LINE_IDENTITY_AMBIGUOUS` 使用准确 ambiguity 描述 |
| FIX R3-03 | 新增测试 | `test_d5_r3_multi_line_exact_reupload_without_source_line_key` |

---

## 1. Changed Files

### `excel_import_patch.py` (R3 三次修改)

| Section | Lines | R3 Change |
|---------|-------|-----------|
| 分类逻辑 `elif not src_line_key and existing_lines` | ~700 | **FIX R3-01**: `found_match=True` → 直接 `DUPLICATE_NOOP`，跳过 `line_level_changes` 检查 |
| 冲突详情 `found_match=False` 分支 | ~700 | **FIX R3-02**: `LINE_IDENTITY_AMBIGUOUS` 使用准确 ambiguity 描述，不再用假的 old/new line 对比 |

### `tests/test_d5_excel_import.py` (R3 扩充)

| Change | Description |
|--------|-------------|
| **FIX R3-03** | 新增 `test_d5_r3_multi_line_exact_reupload_without_source_line_key` 测试 (Case 25) |

### 前端文件 (FIX 10)

| File | Change |
|------|--------|
| Frontend module | 新增 D5 分类状态显示 (`DUPLICATE_NOOP`, `CONFLICT_EXISTING`, `CORRECTED`, etc.) |
| Frontend module | 新增 `order_actions` 支持 (`apply_correction` / `skip`) |
| Frontend module | 新增 retry UI 按钮和状态 |

---

## 2. R2 Fix 详细变更

### FIX 01: Retry 重写

```python
# BEFORE (R1 - 错误):
row_no = row['order_no']  # IndexError: order_no 字段不存在
_preview_rows(conn, rows, user_id)  # 缺少 mapping 参数

# AFTER (R2 - 正确):
raw_data = json.loads(row['raw_json'])  # 从原始行数据恢复
_preview_rows(conn, records, mapping, user_id)  # 正确的 4 参数签名
```

### FIX 02: Line Duplicate/Conflict 改用 order_lines

```python
# BEFORE (R1):
# 仅查询 orders 表的 product_name/quantity 字段
existing_products = {r['product_name'] for r in fetch_all(...)}

# AFTER (R2):
# 查询 order_lines 表构建完整索引
order_lines_index = {}
for ol in db.query("SELECT order_id, product_name, order_qty, ... FROM order_lines"):
    key = (ol['order_id'], ol['product_name'], ol['order_qty'])
    order_lines_index[key] = ol
```

### FIX 03: INTRA_BATCH 限制为订单级字段

```python
# BEFORE (R1):
INTRA_BATCH_FIELDS = ORDER_LEVEL_FIELDS | LINE_LEVEL_FIELDS  # 包含 delivery_date

# AFTER (R2):
INTRA_BATCH_ORDER_LEVEL_FIELDS = {'customer_name', 'owner', 'supplier_name', 'order_status'}
# delivery_date 差异不再触发 CONFLICT_IN_BATCH
```

### FIX 04: Correction 区分 order-level / line-level

```python
# BEFORE (R1):
# 对 LINE_CONFLICT 行无条件 INSERT 新 order_line
db.execute("INSERT INTO order_lines ...")

# AFTER (R2):
# 区分 target_type
if target_type == 'order':
    db.execute("UPDATE orders SET ...")
elif target_type == 'order_line':
    _update_order_line(line_id, updates)  # UPDATE 而非 INSERT
```

### FIX 05-06: 真实 Failure Injection

```python
# Test 23: Correction 失败注入
def test_correction_atomicity_with_failure_injection(monkeypatch):
    monkeypatch.setattr(excel_import_patch, '_insert_correction_record',
                        lambda *a, **kw: (_ for _ in ()).throw(OperationalError("DB fail")))
    # 验证: order/line 数据回滚，correction 不写入

# Test 24: Retry 失败注入
def test_retry_with_commit_failure(monkeypatch):
    monkeypatch.setattr(excel_import_patch, '_insert_order',
                        lambda *a, **kw: (_ for _ in ()).throw(OperationalError("DB fail")))
    # 验证: 订单标记为 COMMIT_FAILED，可被 retry
```

### FIX 07-08: 多行自动化测试

```python
# Test 20: 多行 exact re-upload
def test_multi_line_exact_reupload():
    # 导入 3 行订单 → 再次上传相同数据
    # 验证: 所有 3 行均 DUPLICATE_NOOP，order_lines 数量不变

# Test 21: Line correction
def test_line_correction_uses_update():
    # 导入订单 → 修改某行 product_name → apply_correction
    # 验证: order_lines 数量不变，仅目标行被 UPDATE
```

### FIX 09: event_logs 删除 changes 字段

```python
# BEFORE (R1):
event_logs.insert(action='CORRECTION', changes=json.dumps({...}))

# AFTER (R2):
event_logs.insert(action='CORRECTION', batch_id=..., order_id=..., correction_id=...)
# 仅保留 ID 引用，不存储业务数据
```

### FIX 10: 前端支持

- 分类状态 badge: `NEW`, `DUPLICATE_NOOP`, `CONFLICT_EXISTING`, `CORRECTED`, `BLOCKED`, `COMMIT_FAILED`
- `order_actions` dropdown: `apply_correction`, `skip`
- Retry 按钮: 当批次有 `COMMIT_FAILED` 订单时显示
- 纠正详情: 显示 `changes_json` 中的 field/old/new 对比

### FIX 11: 测试隔离

```python
# BEFORE (R1):
# test_d5_excel_import.py module level
import os
os.environ['TEST_MODE'] = '1'  # 污染全局

# AFTER (R2):
# 使用 pytest fixture
@pytest.fixture
def isolated_env(monkeypatch):
    monkeypatch.setenv('TEST_MODE', '1')
    monkeypatch.setenv('ENABLE_DEMO_ADMIN_ACTIONS', 'true')
    yield
    # 自动清理
```

---

## 2.5 R3 Fix 详细变更

### FIX R3-01: found_match=True 直接 DUPLICATE_NOOP

```python
# BEFORE (R2 - 错误):
elif not src_line_key and existing_lines:
    if found_match:
        # 仍然检查 line_level_changes，可能被残留数据覆盖
        if line_level_changes:
            classification = 'LINE_IDENTITY_AMBIGUOUS'  # 误判!
        else:
            classification = 'DUPLICATE_NOOP'
    else:
        ...

# AFTER (R3 - 正确):
elif not src_line_key and existing_lines:
    if found_match:
        # 直接判定 DUPLICATE_NOOP，不再检查 line_level_changes
        classification = 'DUPLICATE_NOOP'
    else:
        classification = 'LINE_IDENTITY_AMBIGUOUS'
        # FIX R3-02: 使用准确的 ambiguity 描述
        conflict_details = "无 source_line_key，无法确定此行是新增还是修改"
```

### FIX R3-02: conflict_details 改为准确 ambiguity 描述

```python
# BEFORE (R2):
conflict_details = f"old: {old_line_data} vs new: {new_line_data}"
# old_line_data / new_line_data 基于假的对比数据，不准确

# AFTER (R3):
conflict_details = {
    "reason": "no_source_line_key_identity_ambiguous",
    "message": "无 source_line_key，无法确定此行是新增还是修改",
    "suggestion": "请提供 source_line_key 或确认是否为此行数据变更"
}
# 使用准确的 ambiguity 描述，不再用假的 old/new line 对比
```

### FIX R3-03: 新增测试用例

```python
def test_d5_r3_multi_line_exact_reupload_without_source_line_key():
    # 1. 导入无 source_line_key 的 3 行 Excel
    # 2. 再次上传完全相同的 Excel
    # 3. 验证: 所有 3 行均 DUPLICATE_NOOP
    # 4. 验证: DB 中 orders=1, order_lines=3 不变
    assert result['classification'] == 'DUPLICATE_NOOP'
    assert result['duplicate_noop_count'] == 3
    assert db_count('orders') == 1
    assert db_count('order_lines') == 3
```

---

## 3. New Database Objects

### Table: `order_corrections` (R1 创建，R2 无变更)
```sql
CREATE TABLE order_corrections (
    correction_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    source_order_key TEXT,
    batch_id TEXT,
    actor_user_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    changes_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
```

### Indexes (R1 创建，R2 无变更)
- `idx_order_corrections_order` on `order_corrections(order_id)`
- `idx_order_corrections_batch` on `order_corrections(batch_id)`

### event_logs 变更 (R2 FIX 09)

**R1 (已废弃)**:
| Column | Type |
|--------|------|
| ... | ... |
| changes | TEXT |

**R2 (当前)**:
| Column | Type |
|--------|------|
| log_id | TEXT PK |
| action_type | TEXT |
| batch_id | TEXT |
| order_id | TEXT |
| correction_id | TEXT |
| created_at | TEXT |

**变更**: 删除 `changes` 字段，业务数据仅存于 `order_corrections.changes_json`

### New Columns: `order_import_batches` (R1 创建，R2 无变更)
| Column | Type | Purpose |
|--------|------|---------|
| retry_of_batch_id | TEXT | Parent batch for retry |
| retry_attempt | INTEGER | Retry counter |
| duplicate_noop_count | INTEGER | DUPLICATE_NOOP counter |
| conflict_count | INTEGER | Conflict counter |
| corrected_count | INTEGER | Correction counter |
| source_file_name | TEXT | Retry batch filename |
| source_file_size | INTEGER | Retry batch file size |
| file_sha256 | TEXT | Retry batch hash |
| has_header | INTEGER | Retry batch header flag |
| start_row | INTEGER | Retry batch start row |
| organization_id | TEXT | Retry batch org |
| created_by | TEXT | Retry batch creator |

### New Columns: `order_import_rows` (R1 创建，R2 无变更)
| Column | Type | Purpose |
|--------|------|---------|
| source_system | TEXT | Data source |
| source_order_key | TEXT | Business key |
| source_line_key | TEXT | Line business key |
| conflict_type | TEXT | Conflict classification |
| conflict_details_json | TEXT | Conflict field details |
| order_action | TEXT | User-selected action |

---

## 4. API Changes

### `POST /api/import/batches/{batch_id}/retry-failed` (R2 FIX 01)

**R1 (已废弃)**:
- 从 `order_import_rows.order_no` 读取行数据 → IndexError
- 调用 `_preview_rows(conn, rows, user_id)` → 参数签名错误

**R2 (当前)**:
- 从 `order_import_rows.raw_json` 读取原始行数据 → 正确恢复
- 调用 `_preview_rows(conn, records, mapping, user_id)` → 4 参数正确签名
- 重试批次正确保存原始行数据

**Request**: No body required.

**Response**:
```json
{
  "batch_id": "new-retry-batch-id",
  "retry_of_batch_id": "original-batch-id",
  "retry_attempt": 1,
  "status": "PREVIEW",
  "projection_hash": "...",
  "source_row_count": 2,
  "identified_order_count": 2,
  "has_block": false,
  "has_warning": true,
  "preview": { ... }
}
```

**Error Responses**:
- 400: "该批次没有可重试的失败订单" (no retryable failed orders)
- 403: Auth/RBAC violation
- 404: Batch not found

---

## 5. Modified Classification Logic (R2)

### Order-Level Conflict Detection
Compares `ORDER_LEVEL_FIELDS` across rows with same `source_order_key`:
- `customer_name`, `delivery_date`, `customer_delivery_date`, `owner`, `supplier_name`, `order_status`

### Intra-Batch Conflict Detection (R2 FIX 03)
**R1**: 所有 order-level + line-level 字段差异均触发
**R2**: 仅 `INTRA_BATCH_ORDER_LEVEL_FIELDS` 差异触发
- `customer_name`, `owner`, `supplier_name`, `order_status`
- `delivery_date` 差异不再触发 CONFLICT_IN_BATCH

### Line-Level Detection (R2 FIX 02)
**R1**: 仅查询 orders 表的 `product_name`/`quantity`
**R2**: 查询 `order_lines` 表构建 `order_lines_index`，精确匹配每一行

### Classification Tree (R3)
```
1. Has error? → ERROR
2. Intra-batch order-level conflict? (customer_name/owner/supplier_name/order_status) → CONFLICT_IN_BATCH
3. Existing order + no changes? → DUPLICATE_NOOP
4. Existing order + order-level changes? → CONFLICT_EXISTING
5. Existing order + line changes + source_line_key? → LINE_CONFLICT
6. Existing order + line changes + no source_line_key? → LINE_IDENTITY_AMBIGUOUS
   R3: found_match=True → DUPLICATE_NOOP (跳过 line_level_changes)
   R3: found_match=False → LINE_IDENTITY_AMBIGUOUS (准确 ambiguity 描述)
7. New order? → NEW
```

---

## 6. Correction Flow (R2 FIX 04)

```
apply_correction(order_key, corrections):
  ├── For each field in corrections:
  │   ├── if field is order-level (customer_name, delivery_date, ...):
  │   │     └── UPDATE orders SET field = new_value WHERE order_id = ?
  │   └── if field is line-level (product_name, order_qty, ...):
  │         └── _update_order_line(line_id, {field: new_value})
  │               └── UPDATE order_lines SET field = new_value WHERE line_id = ?
  └── Insert correction record into order_corrections
```

---

## 7. Backward Compatibility

| Aspect | Status | R2 Note |
|--------|--------|---------|
| Preview → projection_hash → Commit | Preserved | 无变化 |
| BLOCK / WARNING rules | Preserved | 无变化 |
| source_order_key business key | Preserved | 无变化 |
| Multi-line orders | Preserved | **FIX 02**: order_lines 查询更精确 |
| Per-order atomic commit | Preserved | **FIX 04**: line-level 走 UPDATE |
| Import Report | Extended (not replaced) | 无变化 |
| CSV Export | Preserved | 无变化 |
| Organization / RBAC | Preserved | 无变化 |
| D3 / D4 endpoints | Preserved | 无变化 |
| D3 / D4 test contracts | Preserved | **FIX 02**: D4 regression 通过 |
| Test isolation | Improved | **FIX 11**: fixture-based env management |

---

## 8. Test Manifest

### R3 Test File: `tests/test_d5_excel_import.py`

**25 tests** covering all D5 scenarios:

| Range | Tests | Source |
|-------|-------|--------|
| 01-18 | 核心场景 | R1 原有 |
| 19 | Retry 真实失败重试 | FIX 01 新增 |
| 20 | Multi-line exact re-upload | FIX 07 新增 |
| 21 | Line correction _update_order_line | FIX 08 新增 |
| 22 | INTRA_BATCH delivery_date 排除 | FIX 03 新增 |
| 23 | Correction failure injection | FIX 05 新增 |
| 24 | Retry failure injection | FIX 06 新增 |
| 25 | Multi-line no-source-line-key exact re-upload | FIX R3-03 新增 |

### Full Regression Results
```
210 passed, 0 failed, 23 skipped
```

### 与独立环境对比

| 环境 | Passed | Failed | Skipped |
|------|--------|--------|---------|
| Trae execution environment | 210 | 0 | 23 |
| ChatGPT independent environment | 206 | 0 | 26 |

**核心 Gate**: `0 failed` — 两环境均通过

---

## 9. Out of Scope (D6+)

- Agent refactoring
- Risk ranking changes
- ERPNext integration
- Communication workflow changes
- Front-end 完整 UI sprint (当前为 FIX 10 最小实现)
- BASE_ONLY rule modification
- Technical debt unrelated to D5
- Extended retry with selective order retry
- Auto-resolve conflict rules
- Real-time collaboration features
- PG runtime verification (PG_RUNTIME_NOT_VERIFIED)
- 混合变更部分成功场景自动化测试

---

CHANGE_MANIFEST_COMPLETE_R3