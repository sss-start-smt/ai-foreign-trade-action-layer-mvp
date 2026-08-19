# D8 Action Case Contract

**Status:** D8 FROZEN  
**Date:** 2026-08-12 (Revised Round 3)  
**Baseline:** D7 Final (102 passed / 0 failed)

---

## 1. action_case vs task: Why They Are Separate Objects

| Aspect | action_case | task |
|--------|-------------|------|
| **本质** | 一个业务目标/业务问题 | 执行对象 |
| **生命周期** | 业务事项生命周期（NEEDS_JUDGMENT → CLOSED） | 执行状态生命周期（OPEN → DONE） |
| **数量** | 一笔订单可多个并行 action_case | 一个 action_case 下挂多个 task |
| **触发** | D7 风险评估 → D8 intent derivation | action_case 阶段推进 → D9 task creation |
| **归属** | organization_id + order_id + action_intent_key | 归属 action_case_id |

**核心区别**：action_case 回答"**需要解决什么问题**"，task 回答"**具体做什么**"。

```
Order: PO-2026-001
  │
  ├── action_case: v1:LOGISTICS_RECOVERY (物流异常恢复)
  │     stage: IN_PROGRESS
  │     │
  │     ├── task: 联系供应商 (DO_NOW)
  │     ├── task: 核实物流信息 (DO_TODAY)
  │     └── task: 更新承诺交期 (WAITING_RESULT)
  │
  └── action_case: v1:CUSTOMER_CONFIRMATION (客户确认)
        stage: WAITING_RESULT
        │
        └── task: 发送确认请求
```

## 2. action_intent_key V1 规则

**格式**: `v1:{INTENT_TYPE}`

**确定性**:
- 相同输入 → 相同 key（重复运行保持一致）
- 不依赖 risk_signal_id / UUID
- 不依赖 priority_score
- 不依赖 action bucket

**Intent Mapping**:

| D7 Risk Type | action_intent_key | 初始 Stage |
|-------------|-------------------|------------|
| LOGISTICS_EXCEPTION | v1:LOGISTICS_RECOVERY | READY_FOR_ACTION |
| SUPPLIER_COMMITMENT_OVERDUE | v1:SUPPLIER_FOLLOWUP | READY_FOR_ACTION |
| CUSTOMER_CONFIRMATION_BLOCKING | v1:CUSTOMER_CONFIRMATION | READY_FOR_ACTION |
| SOURCE_CONFLICT | v1:FACT_CONFLICT_RESOLUTION | NEEDS_JUDGMENT |
| INFORMATION_GAP | v1:INFORMATION_COMPLETION | NEEDS_JUDGMENT |
| OWNER_MISSING | v1:OWNER_ASSIGNMENT | READY_FOR_ACTION |
| DELIVERY_RISK | v1:DELIVERY_RECOVERY | READY_FOR_ACTION |

**唯一范围**: `organization_id + order_id + action_intent_key`

## 3. Root-Cause Suppression 规则

**规则**: 如果 DELIVERY_RISK 与以下更具体的根因同时存在：
- LOGISTICS_EXCEPTION
- SUPPLIER_COMMITMENT_OVERDUE
- CUSTOMER_CONFIRMATION_BLOCKING

则：
- DELIVERY_RISK 作为结果性风险**保留在 Evidence 中**
- 不额外创建泛化 DELIVERY_RECOVERY action_case
- 只创建更具体的 root cause action_case

**示例**:
```
DELIVERY_RISK + LOGISTICS_EXCEPTION
→ 只创建 LOGISTICS_RECOVERY (含 DELIVERY_RISK evidence)
→ 不创建 DELIVERY_RECOVERY

DELIVERY_RISK only
→ 创建 DELIVERY_RECOVERY

DELIVERY_RISK + SUPPLIER_COMMITMENT_OVERDUE
→ 只创建 SUPPLIER_FOLLOWUP (含 DELIVERY_RISK evidence)
→ 不创建 DELIVERY_RECOVERY
```

## 4. FSM 合法/非法转换

### 合法转换

```
NEEDS_JUDGMENT → READY_FOR_ACTION, CLOSED
READY_FOR_ACTION → IN_PROGRESS, CLOSED
IN_PROGRESS → WAITING_RESULT, RESUMED_OR_ESCALATED, CLOSED
WAITING_RESULT → RESUMED_OR_ESCALATED, CLOSED
RESUMED_OR_ESCALATED → READY_FOR_ACTION, IN_PROGRESS, WAITING_RESULT, CLOSED
CLOSED → (终态，不可继续转换)
```

### 非法转换（必须拒绝）

| From | To | 原因 |
|------|----|------|
| CLOSED | 任何 | 终态不可逆转 |
| NEEDS_JUDGMENT | IN_PROGRESS | 跳过 READY_FOR_ACTION |
| NEEDS_JUDGMENT | WAITING_RESULT | 跳过中间阶段 |
| READY_FOR_ACTION | WAITING_RESULT | 未开始就等待 |
| READY_FOR_ACTION | RESUMED_OR_ESCALATED | 未开始就升级 |
| IN_PROGRESS | READY_FOR_ACTION | 执行中不可回退 |
| WAITING_RESULT | IN_PROGRESS | 等待中不可直接执行 |

### Close Reasons

- RESOLVED — 成功解决（唯一表示成功）
- NO_LONGER_NEEDED — 不再需要
- DISMISSED — 人工驳回
- DUPLICATE — 重复
- MERGED — 已合并
- SUPERSEDED — 已被取代
- CANCELLED — 已取消
- INVALIDATED — 已失效

## 5. 风险消失 ≠ Case 自动关闭

**核心原则**: Risk disappearance != business resolution

**原因**:
1. D7 本轮可能未检测到风险信号（数据源延迟）
2. 风险可能暂时消失但业务问题仍存在
3. 业务 case 的正式解决需要人工确认/流程完成
4. 自动关闭会导致 case 丢失历史追踪

**行为**:
- 风险消失 → `observation_status = NOT_OBSERVED`
- `lifecycle_status` 保持 `ACTIVE`
- `stage` 不变
- 只有显式 transition 到 CLOSED 才会关闭 case

## 6. 完整 Observation Feed（Round 3 核心修复）

### 问题根因（ChatGPT Review Round 2 失败原因）

**之前的错误**: D8 使用 D7 的 Ranked Queue（`my_action_items`, `team_action_items`）作为唯一的 observation source。这些字段是 D7 的排序/展示结果，受 Top-N 截断影响。

```
Ranked Queue (Top-N) ≠ Observation Snapshot

"没进入Top-N" ≠ "该风险已经不存在"
```

### 修复方案：D7 新增 `action_case_observations`

D7 在排名截断之前，维护一个独立的 `action_case_observations` 列表（与 `order_results` 分离），用于 D8 内部消费。

**实现方式**:
- `order_results` 继续用于 D7 排名/UI（仅包含有 risk_signals 的订单）
- `action_case_observations` 是独立列表，包含所有被 screen 的订单：
  - 有风险的订单（同时也进入 order_results）
  - INFORMATION_GAP-only 订单
  - **零风险订单**（risk_signals=[]，不进入 order_results，但进入 action_case_observations）

**`action_case_observations` 包含**:
- 有风险的订单
- INFORMATION_GAP-only 订单
- 没有风险的已 screen 订单

**不参与**:
- Top N
- Operator UI Queue
- Manager UI Queue
- Priority ranking

D7 原有返回字段（`my_action_items`, `team_action_items`, `unassigned_orders`, `information_gaps`）**全部保持原语义和结果**。

### D8 消费规则

`reconcile_action_cases()` **只使用 `action_case_observations`** 做状态判断：

1. **Case create/reuse**: 从完整 observation feed 派生 intents
2. **OBSERVED / NOT_OBSERVED**: 基于 `observed_case_keys = {(order_id, action_intent_key)}`
3. **scope_order_ids**: 限制 NOT_OBSERVED 范围为本次 feed 内的订单

**绝对禁止**: 用 `my_action_items` / `team_action_items` 的缺席推导 risk disappearance。

### Information Gap 必须进入 Action Case

Operator D7 将纯 INFORMATION_GAP 放入 `information_gaps`（而非 `my_action_items`）。由于 `action_case_observations` 包含所有被 screen 的订单，INFORMATION_GAP 自然进入 intent derivation。

验证：
```
INFORMATION_GAP → INFORMATION_COMPLETION → NEEDS_JUDGMENT
```

### Observation 示例

```python
d7_result = {
    # Ranked display queues (for UI only)
    "my_action_items": [item_A],  # Top-N only shows item_A
    "information_gaps": [item_B],  # INFORMATION_GAP separately

    # Authoritative observation feed (for D8 reconciliation)
    "action_case_observations": [item_A, item_B, item_C],
    # item_B is INFORMATION_GAP-only → creates INFORMATION_COMPLETION case
    # item_C has no risk → no intent created
}
```

## 7. 数据库约束

### Partial Unique Index（SQLite/PostgreSQL 兼容）

```sql
CREATE UNIQUE INDEX uq_action_cases_active
ON action_cases(organization_id, order_id, action_intent_key)
WHERE lifecycle_status = 'ACTIVE';
```

**效果**:
- 每个 (org, order, intent_key) 最多一个 ACTIVE case
- CLOSED case 可以有多个（历史记录）
- 同一 intent 关闭后可以重新创建新的 ACTIVE case

### 其他索引

```sql
CREATE INDEX idx_action_cases_org_order ON action_cases(organization_id, order_id, lifecycle_status);
CREATE INDEX idx_action_cases_stage ON action_cases(stage, lifecycle_status);
CREATE INDEX idx_action_cases_intent ON action_cases(action_intent_key, lifecycle_status);
```

## 8. 权限隔离与授权边界

### 角色权限

| 角色 | 可见范围 | 操作权限 |
|------|---------|---------|
| Operator | 自己负责订单的 action_cases | transition 自己的 cases |
| Manager | 本 organization 所有 action_cases | transition 所有 cases |
| Admin | 本 organization 所有 action_cases | transition 所有 cases |
| Supervisor | 本 organization 所有 action_cases | transition 所有 cases |

**跨 organization**: 必须 0 泄漏

**owner=NULL**: Operator 不可见；Manager 可见 OWNER_ASSIGNMENT case

### Reconcile 授权边界（DB 是唯一事实源）

#### 核心原则

Payload **不是** authority source。数据库中的 `orders` 表是权限事实源。

#### 验证流程

对每个 observation item：

```python
# 1. 查询数据库
order = SELECT * FROM orders WHERE order_id = ?

# 2. DB organization_id 必须 == identity organization_id
order.organization_id == identity.organization_id

# 3. claimed_org_id (payload) 一致性验证
if claimed_org_id and claimed_org_id != db_org_id:
    raise ReconcileAuthError  # payload 篡改检测

# 4. Operator: order.owner 必须 == identity.user_id
#    Manager/Admin/Supervisor: 同组织内所有订单
```

#### 两类失败

| 失败类型 | 行为 | 场景 |
|---------|------|------|
| **Critical** (`ReconcileAuthError`) | 拒绝整个 reconcile | 订单不存在、DB org 不匹配、payload org 篡改 |
| **Non-critical** (`OrderNotAuthorizedError`) | 跳过该订单 | Operator 不拥有订单、订单无 owner |

整个 payload 必须先完成 authority validation，之后才能开始任何 Action Case INSERT/UPDATE。**禁止 partial write**。

### Operator 隔离（防止 Observation 污染）

Operator reconcile **只允许改变**本次 authoritative observation scope 内由该 Operator 负责的订单 Case。不得修改同组织其他 Operator 的 Case。

```python
mark_cases_not_observed(
    organization_id=?,
    observed_case_keys=?,
    scope_order_ids=?,  # ← 本次 reconcile 的订单范围
)

# WHERE organization_id=?
#   AND lifecycle_status='ACTIVE'
#   AND order_id IN (scope_order_ids)  ← 关键限制
#   AND (order_id, action_intent_key) NOT IN observed_case_keys
```

Manager 如果使用完整 organization observation snapshot，才可以进行组织级 Observation reconciliation。

## 9. D9 明确未实现内容

- [ ] Task 创建与分配
- [ ] Waiting records 到期恢复
- [ ] Outbox / Redis / Taskiq
- [ ] ERP 写回
- [ ] Agent 自动执行
- [ ] Task / waiting / approval 挂到 action_case 下
- [ ] D9 task/waiting 闭环