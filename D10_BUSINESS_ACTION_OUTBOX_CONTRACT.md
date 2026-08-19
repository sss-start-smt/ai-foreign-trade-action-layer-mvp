# D10 BusinessAction + Transactional Outbox Contract

**Status:** D10 P0 implementation candidate  
**Date:** 2026-08-14  
**Depends on:** D8 Action Case (FROZEN), D9 Task/Waiting (FROZEN)  
**Policy:** `D10_BUSINESS_ACTION_V1`

## 0. 今天到底解决什么问题

D9 已经解决：一个业务问题（Action Case）如何拆成具体 Task，以及 Task 等待回复后如何恢复。

D10 只补 Task 的下游：**当某个 Task 真的需要产生正式业务变化时，FlowOrder 怎样先把“要执行什么”可靠记住，并保证重复点击不会产生第二次动作。**

对象链固定为：

```text
Risk Signal → Action Case → Task → BusinessAction → Outbox
```

- `Action Case`：要解决什么业务问题。
- `Task`：当前具体要做什么。
- `BusinessAction`：准备对业务世界产生什么正式变化。
- `Outbox`：把“这个正式变化待执行”可靠记录下来，供后续 Worker/Adapter 消费。

**ACCEPTED 只表示 FlowOrder 已可靠接收动作，不表示 ERPNext/邮件/CRM 已经执行成功。**

## 1. D10 V1 产品边界

### In Scope

1. `BusinessActionSubmission`：用户/系统一次正式提交需要的字段。
2. `BusinessActionPlan`：提交前确定性校验、规范化、hash。
3. 一个短事务同时写入：
   - Idempotency reservation
   - BusinessAction
   - Outbox Event
   - Audit Event
4. 重复请求幂等。
5. 同一 idempotency key 携带不同请求时硬拒绝。
6. 任一点失败全部 rollback，不允许 BusinessAction/Outbox 半截存在。
7. organization / Action Case / Task 状态边界校验。

### Explicitly Out of Scope

- 不写 ERPNext 生产环境。
- 不调用邮件、CRM、供应商接口。
- 不实现 Redis/Taskiq Dispatcher（D12）。
- 不实现主管审批（D11）。
- 不实现 `RESULT_UNCERTAIN` 外部执行对账（后续故障治理）。
- 不修改 D8 Action Case FSM。
- 不修改 D9 Task/Waiting FSM。

## 2. Task → BusinessAction 的 V1 关系

D10 V1 冻结为：

```text
Task 1 ── 0..1 BusinessAction
```

原因：如果两个副作用可以分别成功/失败/审批/重试，就应该拆成两个 Task，避免一个 Task 出现“半成功”。

例：

```text
Action Case：解决 SO-001 交期异常
  ├─ Task：更新 ERP 预计交期
  │    └─ BusinessAction：UPDATE_EXPECTED_DELIVERY_DATE
  └─ Task：通知客户交期变化
       └─ BusinessAction：SEND_CUSTOMER_DELIVERY_UPDATE
```

如果未来真实业务证明“一个不可拆 Task 必须原子地产生多个 BusinessAction”，必须重新打开此合同，不在 D10 偷偷扩展。

## 3. BusinessActionSubmission

必填：

| 字段 | 含义 |
|---|---|
| `organization_id` | 租户 |
| `task_id` | D9 Task |
| `action_type` | 动作类型，如 `UPDATE_EXPECTED_DELIVERY_DATE` |
| `target_type` | 目标类型，如 `ERP_SALES_ORDER` |
| `target_id` | 目标业务对象，如 `SO-001` |
| `payload` | 结构化动作负载 |
| `idempotency_key` | 客户端/调用方本次逻辑提交的唯一键 |
| `actor` | 谁发起 |
| `request_id` | 本次请求追踪 ID |
| `source` | 来源，默认 `ACTION_WORKSPACE` |
| `reason` | 可选原因 |

## 4. BusinessActionPlan

Plan 是**无外部副作用**的确定性结果，包含：

- Task → Action Case → Order 的真实 DB 归属；
- 规范化后的 action / target；
- `request_hash`：判断同一 idempotency key 是否真的是同一请求；
- `effect_hash`：描述预期正式副作用；
- `policy_version=D10_BUSINESS_ACTION_V1`。

禁止只相信前端 payload 里的 org/case/order 归属；权威关系必须从 DB 读取。

## 5. 状态语义

### BusinessAction

D10 只新增：

```text
ACCEPTED
```

含义：BusinessAction + Outbox 已同事务持久化。

**不得把 ACCEPTED 翻译成“ERP 已修改成功”。**

执行/审批状态由后续 Day 扩展。

### Outbox

D10 初始：

```text
PENDING
```

D10 不消费它。D12 Dispatcher/Worker 才负责 lease / retry / publish。

## 6. Transactional Outbox 原子性

一次新提交必须在同一个短事务里完成：

```text
reserve idempotency
  → insert BusinessAction
  → insert Outbox
  → insert Audit
  → COMMIT
```

任一步失败：

```text
ROLLBACK ALL
```

必须证明不存在：

- BusinessAction 有、Outbox 无；
- Outbox 有、BusinessAction 无；
- idempotency 已占用但动作不存在；
- audit 声称接受但业务动作未提交。

## 7. 幂等规则

作用域：

```text
(organization_id, idempotency_key)
```

### 同 key + 同 request_hash

返回第一次的：

- `business_action_id`
- `outbox_event_id`
- `effect_hash`

并标记：

```text
replayed = true
```

不创建第二条动作、Outbox 或 Audit。

### 同 key + 不同 request_hash

硬拒绝：`D10IdempotencyConflict`。

不能采用“最后一次覆盖前一次”。

## 8. D8/D9 不变量

D10 是 D9 下游，不拥有上游状态机：

- BusinessAction submit 不自动 start/complete/cancel Task；
- BusinessAction submit 不创建/结束 Waiting；
- BusinessAction submit 不关闭 Action Case；
- CLOSED Case 不允许新 BusinessAction；
- WAITING/DONE/CANCELLED Task 不允许提交新 BusinessAction；
- 跨 organization 必须拒绝。

## 9. Audit 最小合同

每个成功的新 BusinessAction 同事务追加一个不可变 Audit Event，至少保留：

- organization_id
- actor
- request_id
- entity_type/entity_id
- event_type
- before/after
- reason
- source
- created_at

重复幂等 replay 不重复追加成功审计，避免把一次业务动作误记成多次正式提交。

## 10. D10 Gate

D10 P0 通过需要至少证明：

1. 正常提交：1 BusinessAction + 1 PENDING Outbox + 1 idempotency + 1 Audit。
2. 重复提交：仍然只有上述各 1 条，返回同一 ID。
3. 同 key 改 payload：硬冲突，不覆盖第一次动作。
4. 在 action/outbox/audit 中间强制抛错：四类记录全部为 0。
5. 服务重连后重复提交仍幂等。
6. 跨组织、CLOSED Case、WAITING/DONE/CANCELLED Task 被拒绝。
7. D8/D9 状态在 D10 提交前后保持不变。
8. D8+D9 回归保持通过。
9. D10 不发生任何 ERPNext/CRM/邮件真实写入。

满足以上条件才可进入 D11；不以“代码能跑”代替 Gate。
