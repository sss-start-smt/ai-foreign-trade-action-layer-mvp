# D9_TASK_WAITING_CONTRACT.md

> FlowOrder D9-P0 产品/工程合同：Action Case → Task → Waiting → Due Recovery
> Policy 版本：`D9_TASK_WAITING_V1` ｜ 基线：D8 Action Case 合同（冻结）
> 本文档是本次交付的**权威合同**，代码 `d9_task_waiting.py` 必须与之严格一致。

---

## 0. 目标（唯一）

在已冻结的 D8 之上，实现最小执行闭环：

```
Action Case(D8) ──create_task──▶ Task(D9) ──put_task_on_waiting──▶ Waiting(D9)
                                     │                                      │
                                     │                              record_waiting_reply
                                     │                                      │ (satisfies_completion)
                                     │                                      ▼
                                     │                              Waiting RESOLVED → resume Task
                                     │                                      │
                                     └──── run_due_recovery ◀── due_at 到期 ──┘
                                            (EXPIRED → resume Task)
```

**硬约束**：三层是**独立的三个状态机**；Task 完成/Waiting 结束/到期**都绝不**自动修改
Action Case 的 `lifecycle_status` 或 `close_reason`；Action Case 继续遵守 D8 冻结规则
（风险消失 ≠ 自动关闭，关闭必须显式 transition，且仅 CLOSED 为终态）。

---

## 1. 范围与边界

### 1.1 In Scope（本轮）
- 独立 `d9_*` 表 + 领域函数：`create_task / start_task / complete_task / cancel_task`、
  `put_task_on_waiting / record_waiting_reply / resolve_waiting / cancel_waiting`、
  `run_due_recovery`、Trace 查询。
- 一 Case 多 Task；一 Task 至多一个 ACTIVE Waiting。
- 幂等 Due Recovery（P0 验收项）。
- Trace：可重建"任务为何又回到待办"。

### 1.2 Out of Scope（明确禁止，突破须停工并写入报告）
- 改动 `action_cases` 或 D8 的任何状态机 / 关闭规则。
- BusinessAction / Outbox / ERP / CRM 写回、自动邮件、自动联系供应商。
- 自动 Task 分配 / 负载均衡 / 新 Identity 路由算法。
- UI 大改、新 Agent 框架、D9 接入 API 层。

---

## 2. 数据模型（D9 新增三表）

### 2.1 `d9_action_case_tasks`（Task）
| 列 | 说明 |
|----|------|
| `task_id` (PK) | `TK-…` |
| `organization_id` | 租户，创建时与 Case 校验一致 |
| `action_case_id` | 所属 D8 Case（只读引用，外键但本模块不写 action_cases） |
| `title` / `recommended_action` | 任务标题 / 建议动作 |
| `status` | `TODO / IN_PROGRESS / WAITING / DONE / CANCELLED` |
| `version` | 乐观锁 |

### 2.2 `d9_action_case_waitings`（Waiting）
| 列 | 说明 |
|----|------|
| `waiting_id` (PK) | `WT-…` |
| `task_id` / `action_case_id` | 所属 Task / Case |
| `waiting_type` | 等待类型（SUPPLIER_REPLY 等） |
| `due_at` | 到期时间（ISO8601，含时区） |
| `status` | `ACTIVE / RESOLVED / EXPIRED / CANCELLED` |
| `reply_count` / `latest_reply_json` | 已记录回复数与证据数组 |
| `resolved_at / expired_at / cancelled_at / cancel_reason` | 终态时间戳与原因 |
| `uq_d9_waitings_active` | **partial unique index** `(task_id) WHERE status='ACTIVE'` → 一 Task 至多一个 ACTIVE Waiting |

### 2.3 `d9_trace_events`（Trace）
| 列 | 说明 |
|----|------|
| `trace_id` (PK) | `TR-…` |
| `organization_id` / `entity_type` / `entity_id` | 实体维度（task / waiting / case） |
| `trace_kind` | `TASK / WAITING / REPLY / RECOVERY` |
| `event_type` | `TASK_CREATED / TASK_STATUS_CHANGED / WAITING_CREATED / REPLY_RECEIVED / WAITING_RESOLVED / WAITING_EXPIRED / WAITING_CANCELLED` |
| `payload_json` | 事件负载（如 `{from,to}`、`{task_resumed}`） |
| `actor` / `created_at` | 操作者与时间戳 |

---

## 3. 生命周期

### 3.1 Task（最小语义，复用枚举不重造）— R1 已冻结 FSM

```
TODO ──start_task──▶ IN_PROGRESS
  │                        │
  │                  put_task_on_waiting
  │                        ▼
  │                     WAITING ──(resolve/expire/manual-cancel)──▶ IN_PROGRESS
  ├──complete_task──▶ DONE
  └──cancel_task──▶ CANCELLED
```

**R1 冻结规则（A 节）**：
- 合法转移：`TODO→{IN_PROGRESS,DONE,CANCELLED}`、`IN_PROGRESS→{WAITING,DONE,CANCELLED}`、
  `WAITING→{IN_PROGRESS,DONE,CANCELLED}`。
- `DONE` 与 `CANCELLED` 为**终态**：内部状态转换漏斗（`_update_task_status_internal`）对终态 Task
  直接抛 `D9StateError`，彻底杜绝"复活"。
- **R2 API 封口**：通用状态修改函数 `update_task_status` 已被收口为模块私有
  `_update_task_status_internal` 并**移出 `__all__`**——D9 公开 API 不再提供"任意设状态"的入口，
  Task 状态只能经由业务动作（create / start / complete / cancel / put_on_waiting / resolve /
  cancel_waiting / run_due_recovery）驱动，从根本上消除绕过业务入口直接改状态的后门。
- `put_task_on_waiting` 仅允许 `IN_PROGRESS` Task 进入 `WAITING`；`TODO` 必须先 `start_task`，
  `DONE`/`CANCELLED`/异常 `WAITING` 一律拒绝（不再有绕过 FSM 的后门）。
- `create_task` 已**移除 `status` 参数**：新 Task 永远且只能从 `TODO` 起步，无法在创建时
  直接置为 `WAITING`/`DONE`/`CANCELLED`。

### 3.2 Waiting（最小语义）— R1 明确 Cancel 三态语义

```
ACTIVE ──record_waiting_reply(satisfies_completion=True, 父Case ACTIVE)──▶ RESOLVED → 恢复 Task
ACTIVE ──record_waiting_reply(satisfies_completion=True, 父Case CLOSED)──▶ RESOLVED → 收口 Task(CANCELLED)
ACTIVE ──run_due_recovery(到期, 父Case ACTIVE)──▶ EXPIRED → 恢复 Task
ACTIVE ──run_due_recovery(到期, 父Case CLOSED)──▶ CANCELLED(PARENT_CASE_CLOSED) → 收口 Task
ACTIVE ──cancel_waiting(MANUAL, 父Case ACTIVE)──▶ CANCELLED → Task 恢复 IN_PROGRESS
ACTIVE ──cancel_waiting(TASK_DONE/TASK_CANCELLED)──▶ CANCELLED → Task 保持终态
ACTIVE ──cancel_waiting(父Case CLOSED)──▶ CANCELLED → Task 收口 CANCELLED（绝不恢复）
```

**R1 关键修复（C 节）**：取消 Waiting 后必须解决"Task=WAITING 但无 Active Waiting"的僵尸态：
- **MANUAL** 取消（父 Case ACTIVE）：Task 从 `WAITING` 恢复为 `IN_PROGRESS`（"不再等这个条件了，行动重新可处理"）。
- **TASK_DONE / TASK_CANCELLED** 触发的取消：Task 已是终态，保持不动。
- **PARENT_CASE_CLOSED** 触发的取消：Task **绝不恢复** `IN_PROGRESS`，安全收口为 `CANCELLED`，杜绝 ghost action。

- **Partial Reply**：`satisfies_completion=False` → 仅记录证据，保持 ACTIVE。
- **Duplicate Reply**：同 `reply_id` 已记录 → no-op；Waiting 非 ACTIVE 时收到迟到回复 → no-op。
- **迟到完整 Reply（父 Case 已 CLOSED）**：记录证据并 RESOLVED，但**绝不恢复 Task**；Task 收口 CANCELLED。
- **幂等取消**：`ACTIVE→CANCELLED` 用条件 UPDATE，重复调用只产生一次事件。

---

## 4. 核心不变量（Invariants）

1. 三层分离：Task 完成 / Waiting 解决 / 到期 **都不**写 `action_cases`。
2. 一 Case 多 Task：某 Task 进 WAITING **只抑制该 Task**，不冻结 Case、不抑制其他 Task。
3. 一 Task 至多一个 ACTIVE Waiting（partial unique index + 幂等返回）。
4. 组织隔离：创建 Task 时校验 `organization_id` 与 Case 一致。
5. Case 必须存在：创建 Task 前 `get_case_by_id` 校验，拒绝指向缺失/跨组织 Case 的孤儿 Task。
6. 所有"有效副作用"都伴随恰好一次 Trace 事件。

### 4.1 Task / Waiting 一致性不变量（R2 冻结）

R2 在"业务动作驱动状态"的基础上，把 Task 与 Waiting 的**稳定态一致性**写成四条可测试的硬
不变量，由 `_update_task_status_internal` 的守卫与每个业务入口共同保证：

- **Invariant A**：`Task.status == WAITING` ⟹ 恰好存在一个 `ACTIVE` Waiting。
  不存在"Task=WAITING 但无 Active Waiting"的僵尸；也不存在 WAITING 对应多个 Active Waiting
  （`uq_d9_waitings_active` partial unique index 已保证至多一个）。
- **Invariant B**：存在 `ACTIVE` Waiting ⟹ `Task.status == WAITING`。
  系统在"行动仍在等待"与"行动已恢复可执行"之间二选一，不得同时成立（即不得出现
  `Task != WAITING` 却仍有 `ACTIVE` Waiting 的冲突态）。
- **Invariant C**：`Task.status ∈ {DONE, CANCELLED}` ⟹ 不存在 `ACTIVE` Waiting。
  终态 Task 不得悬挂任何 Active Waiting。
- **Invariant D**：`Parent Case == CLOSED` ⟹ 不得有任何可执行 Task 被 Waiting 系统重新恢复。
  （由第 6 节的真实竞态收口逻辑保证；见 `TestClosedCaseRaceReal`。）

实现位置：
- Invariant A/C 由 `_update_task_status_internal` 守卫——进入 `WAITING` 须已有 `ACTIVE` Waiting、
  离开 `WAITING` 须已无 `ACTIVE` Waiting；
- Invariant B 由各业务入口保证（resolve / expire / cancel_waiting 先终结 Waiting 再恢复 Task）；
- 四条不变量在 `TestStrictInvariantsAfterBusinessOps` 中对全部业务流做终态校验。

---

## 5. 幂等设计（P0 验收核心）

| 攻击场景 | 机制 | 结果 |
|----------|------|------|
| Worker 连跑两次 | `UPDATE … WHERE status='ACTIVE'` 条件 CAS + rowcount | 第二次 0 行 → no-op |
| 两次扫描命中同一 Waiting | 同上 + 持久化状态 | 同一 Waiting 仅一次有效恢复 |
| 服务重启后扫描 | 状态落库，新连接读持久状态 | 已 EXPIRED → no-op |
| 已 EXPIRED 再扫描 | `list_overdue_active_waitings` 仅查 ACTIVE | 不进 due 查询 → 0 过期 |
| Duplicate Reply | `reply_id` 去重 + 非 ACTIVE no-op | 仅一次 RESOLVE、一次恢复 |
| Duplicate Cancel | `ACTIVE→CANCELLED` 条件 UPDATE | 仅一次 CANCELLED 事件 |

**关键**：trace 事件**仅在 rowcount>0 时写入**，从根上杜绝"重复恢复 + 重复 trace"。
Due Recovery 返回 `{scanned, expired, cancelled_orphan, skipped, results}`，
`expired` 与 `cancelled_orphan` 即"本轮实际产生的有效副作用数"，可据此对账。

---

## 6. Closed Case 竞态（R1 真实竞态 + 安全收口）

**真实竞态窗口**（R1 要求，区别于 P0 旧测试的"先 CLOSED 再建 Task"）：
`ACTIVE Case → Task → Waiting(ACTIVE) → 父 Case 按 D8 合同合法 CLOSED → 之后 Reply/Due 才到达`。

`run_due_recovery` 对每个 overdue ACTIVE Waiting 先 `get_case_by_id`：
- 若 Case **CLOSED 或缺失** → `_cancel_waiting_internal(reason=PARENT_CASE_CLOSED)`，
  **不恢复 Task、不重开 Case、不建 ghost**；若 Task 仍为 `WAITING` 则安全收口为 `CANCELLED`
  （避免"Task=WAITING 但无 Active Waiting"的僵尸）。
- 否则 → `_expire_waiting_internal`：EXPIRED + 恢复 Task（仅当 Task 仍为 WAITING）。

**迟到完整 Reply（父 Case 已 CLOSED）**：`record_waiting_reply(satisfies_completion=True)`
在 `_resolve_waiting_internal` 内判断父 Case 状态——若 CLOSED/缺失，**绝不恢复 Task**，
而是 RESOLVED + 把 Task 收口为 `CANCELLED`（记录证据/Trace，但**不产生新的可执行行动**）。

**创建侧防线**：
- `create_task` 读取 D8 Case，若 `lifecycle_status == CLOSED` → 拒绝新 Task（B3）。
- `put_task_on_waiting` 在新建 ACTIVE Waiting 前，若父 Case CLOSED/缺失 → 拒绝（B4）。

---

## 6.5 时区规范化（D 节：due_at 统一 UTC）

`due_at` 与 `run_due_recovery(current_time)` 在**写入与比较前**统一规范化为 **UTC ISO8601**
（`d9_task_waiting._normalize_iso_to_utc`）：

- **必须为 timezone-aware**：无时区（naive）时间戳**直接拒绝**（`D9StateError`），绝不猜测。
- 写入 `d9_action_case_waitings.due_at` 的是规范化后的 UTC 字符串。
- Due 扫描时的 `now` 同样规范化，数据库内再做同格式字符串比较（`+00:00` 偏移一致，字典序即时间序）。
- 不引入新的时间服务或基础设施——仅一处纯函数规范化。

> 反例（R1 修复前）：`due_at=2026-08-13T02:30:00+00:00`（北京 10:30）与
> `current_time=2026-08-13T10:00:00+08:00`（UTC 02:00）直接字符串比较会误判为已过期；
> 反之亦然。规范化到 UTC 后两者分别为 `02:30Z` 与 `02:00Z`，比较正确。

---

## 7. Trace 设计意图

`get_trace_for_entity(entity_type, entity_id)` 按 `created_at, trace_id` 升序返回事件链。
用于回答一线问题：**"为什么这个任务今天又重新出现在我的待办里？"**
—— 从 Task 的 trace 可见 `TODO→IN_PROGRESS→WAITING→IN_PROGRESS`，
配合 Waiting 的 `WAITING_CREATED→REPLY_RECEIVED→WAITING_EXPIRED(task_resumed=true)`，
即可完整还原"被置为等待 → 部分回复未结案 → 到期自动恢复"的全过程。

---

## 8. D9 Trace 真实示例

> 取自 `python d9_trace_example.py` 的真实输出（DB：临时 SQLite；Case `AC-100` 由演示脚本 seed，
> 仅读取、不触发 D8 关闭合同）。下列按**业务时序**重排（原始输出同秒事件按 `trace_id` 排序略有交错）。

**场景**：联系供应商确认船期 → 开始执行 → 设为等待供应商回复（due_at 在过去，即已逾期）
→ 收到一条部分回复"在确认中"（不满足结案）→ Due Worker 扫描 → 等待到期 EXPIRED → Task 恢复。

### 8.1 Due Recovery 结果
```json
{
  "status": "OK",
  "policy_version": "D9_TASK_WAITING_V1",
  "organization_id": "ORG-A",
  "scanned": 1, "expired": 1, "cancelled_orphan": 0, "skipped": 0,
  "results": [{"waiting_id": "WT-…", "task_id": "TK-…", "outcome": "EXPIRED", "task_resumed": true}]
}
```

### 8.2 TASK 实体 Trace（回答"为何回到待办"）
```
[TASK_CREATED]            {"action_case_id":"AC-100","title":"联系供应商确认船期","status":"TODO"}
[TASK_STATUS_CHANGED]     {"from":"TODO","to":"IN_PROGRESS"}          // start_task
[TASK_STATUS_CHANGED]     {"from":"IN_PROGRESS","to":"WAITING"}        // put_task_on_waiting
[TASK_STATUS_CHANGED]     {"from":"WAITING","to":"IN_PROGRESS"}        // 到期恢复（EXPIRED）
```

### 8.3 WAITING 实体 Trace
```
[WAITING_CREATED]         {"task_id":"TK-…","action_case_id":"AC-100",
                           "waiting_type":"SUPPLIER_REPLY",
                           "due_at":"2026-08-13T08:55:10+08:00","source_trace_id":null}
[REPLY_RECEIVED]          {"reply_id":"MSG-77","satisfies_completion":false}   // 部分回复，保持 ACTIVE
[WAITING_EXPIRED]         {"task_id":"TK-…","action_case_id":"AC-100","task_resumed":true}
```

### 8.4 最终状态
```
task.status    = IN_PROGRESS     ← 因到期被自动恢复，重新进入待办
waiting.status = EXPIRED
case.lifecycle = ACTIVE          ← Action Case 完全未被本模块改动
```

> 结论：任务"又出现"的根因是 **Waiting 逾期后 Due Recovery 把 Task 从 WAITING 恢复为 IN_PROGRESS**；
> 一条部分回复（`satisfies_completion=false`）不足以结案，故最终由到期兜底恢复，而非被回复解决。
> 全程 Action Case 保持 ACTIVE，符合三层分离合同。

---

## 9. Known Limitations（已知限制）

1. **部署形态**：Due Recovery 为单机顺序扫描，无分布式锁。多实例部署时仅靠 DB 条件 UPDATE 的
   原子性保证"仅一次有效恢复"，建议配合独立 worker / 行锁避免重复扫描开销。
2. **完成判定外置**：`satisfies_completion` 的**语义判定**（什么算完整回复）不在本模块内，
   由外部判定器 / 人工注入。本模块只消费该布尔值——这是有意的边界收敛，避免在本层硬编码业务规则。
3. **无自动升级编排**：Waiting EXPIRED 后 Task 回到 IN_PROGRESS，由上层决定下一步；
   未实现"N 次 EXPIRED 后升级 / 转派"等策略。
4. **Trace 时间精度**：事件时间戳为秒级，同秒多事件排序依赖 `trace_id` 字典序（见第 8 节已重排）。
5. **未接入 API**：D9 仅提供领域层函数与测试；接入 REST/事件总线不在本轮（且 D8 当前也未接 API）。

---

## 10. 本轮新增决策列表（6 项）

> 每项均为在 D8 冻结合同约束下的**最小保守选择**，记录理由以备查。

### 决策 1 — Task 与 Action Case 分离，绝不让 Task 反向修改 Case
**理由**：D8 是已冻结的"业务问题域"状态机，其关闭须经显式判定。若允许 Task 完成自动关闭 Case，
会破坏 D8 的"风险消失 ≠ 自动关闭"铁律，且使"谁有权关闭 Case"的权责模糊。
**做法**：`complete_task`/`cancel_task` 只改 `d9_action_case_tasks`，绝不写 `action_cases`。

### 决策 2 — Waiting 只抑制"所属 Task"，不冻结 Case、不抑制同 Case 其他 Task
**理由**：一个 Case 常含多步动作（催货、改单、对账），任一步进入等待不应阻塞其他步骤，
更不应冻结整个 Case 的推进。
**做法**：`put_task_on_waiting` 仅 `UPDATE task SET status='WAITING' WHERE task_id=?`，
并把该幂等约束写入测试 `TestMultipleTasks` / `TestMultipleWaitings` 做证据。

### 决策 3 — Due 只恢复 Task、绝不操作 Case；Case CLOSED/缺失时短路取消
**理由**：Due Recovery 的职责边界是"等待兜底"，不是"Case 生命周期管理"。在已关闭 Case 上
强行恢复 Task 会产生无意义行动（ghost），重开 Case 更是直接违背后冻结合同。
**做法**：扫描时先读 Case；CLOSED/缺失 → `CANCELLED(PARENT_CASE_CLOSED)` 且不恢复 Task；
否则 EXPIRED + 条件恢复 Task。见 `TestClosedCaseRace`。

### 决策 4 — 幂等靠"条件 UPDATE + rowcount + 持久状态 + reply_id 去重 + 事件门控"
**理由**：Due Worker 重试 / 重启 / 重复扫描在真实生产必然发生；任何"恢复"若可重复触发，
会产生重复 Task 恢复与重复 Trace，污染待办与审计。
**做法**：所有终态转移用 `WHERE status='ACTIVE'` 条件 CAS；trace 仅在 rowcount>0 写入；
`uq_d9_waitings_active` 保证一 Task 一 ACTIVE Waiting。四类攻击见第 5 节与 `TestDueRecoveryIdempotency`。

### 决策 5 — Partial Reply 不结束 Waiting；"收到消息"≠"结案"
**理由**：外贸跟单中"在确认中/稍等"等消息频繁且不构成可结案证据；若按"收到任意消息即恢复"，
会让任务在毫无进展时被错误移出等待，反而掩盖风险。
**做法**：`record_waiting_reply(satisfies_completion=False)` 仅 `REPLY_RECEIVED` 记录证据、
保持 ACTIVE；由外部判定器/人工决定何时 `satisfies_completion=True`。见 `TestPartialReply`。

### 决策 6 — Closed Case + Active Waiting：取消而非恢复，不推断 Case 关闭
**理由**：存在"Waiting 创建后、Due 扫描前 Case 被合法关闭"的竞态窗口。此时应安全收口
（取消等待、不恢复无意义行动、不重开 Case），而不是猜测 Case 状态。
**做法**：`cancel_waiting` 不改 Task 状态、不推断 Case 关闭（保守决策，写入本清单）；
Due 短路见决策 3。两者互补，覆盖"主动取消"与"被动竞态取消"两条路径。

---

## 11. 测试覆盖对照（20 passed）

| 验收点（来自产品合同） | 测试 |
|------------------------|------|
| 正常 Task→Waiting→Reply→Resume | `TestNormalResolveFlow` |
| Partial Reply 不结束 | `TestPartialReply` |
| Duplicate Reply 幂等 | `TestDuplicateReply` |
| 到期 EXPIRED + 恢复 | `TestDueRecoveryExpiry` |
| 重复扫描 / 重启 / 已过期 幂等 | `TestDueRecoveryIdempotency` |
| 取消 + 排除于 Due + 重复取消幂等 | `TestWaitingCancel` |
| 一 Case 多 Task 隔离 | `TestMultipleTasks` |
| 一 Case 多 Waiting 隔离（A 不影响 B） | `TestMultipleWaitings` |
| Closed Case 竞态安全 | `TestClosedCaseRace` |
| 不自动关闭 Case（完成/Resolve/到期） | `TestNoAutoCloseCase` |
| 不变量（一 ACTIVE Waiting / 组织 / 存在性） | `TestInvariants` |
| Trace 重建"为何回到待办" | `TestTraceChain` |

> 失败**不得**写成 PASS：本套件全部以断言验证终态与"有效副作用计数"，无跳过、无 xfail。

---

## 12. D9-R1 加固补丁（Targeted Hardening，非 D10）

R1 不新增产品能力，只修复独立验收发现的 P0 状态一致性问题。代码改动仅限 `d9_task_waiting.py`，
**D8 冻结合同零改动**。

### 12.1 R1 新增/修正的测试（现 34 passed）

| 类别 | 测试 | 覆盖 |
|------|------|------|
| A 终态 FSM | `TestTerminalTaskFsm.test_done_task_rejects_put_on_waiting` | DONE 不可进 WAITING |
| A 终态 FSM | `TestTerminalTaskFsm.test_cancelled_task_rejects_put_on_waiting` | CANCELLED 不可进 WAITING |
| A 终态 FSM | `TestTerminalTaskFsm.test_todo_task_rejects_put_on_waiting` | TODO 须先 start |
| B1 真实竞态 | `TestClosedCaseRaceReal.test_b1_due_after_close_no_resume` | 关后再 Due：不恢复、收口、不重开 |
| B2 真实竞态 | `TestClosedCaseRaceReal.test_b2_late_full_reply_after_close_no_resume` | 关后晚到完整 Reply：不恢复 |
| B3 真实竞态 | `TestClosedCaseRaceReal.test_b3_create_task_on_closed_case_rejected` | CLOSED Case 拒绝建 Task |
| B4 真实竞态 | `TestClosedCaseRaceReal.test_b4_put_on_waiting_after_close_rejected` | Case 关后拒绝建 Waiting |
| C Cancel 语义 | `TestCancelSemantics.test_manual_cancel_resumes_task` | MANUAL 取消→Task 恢复 IN_PROGRESS |
| C Cancel 语义 | `TestCancelSemantics.test_task_done_keeps_done_on_waiting_cancel` | TASK_DONE→Task 保持 DONE |
| C Cancel 语义 | `TestCancelSemantics.test_task_cancelled_keeps_cancelled_on_waiting_cancel` | TASK_CANCELLED→Task 保持 CANCELLED |
| C Cancel 语义 | `TestCancelSemantics.test_parent_case_closed_cancel_no_resume` | PARENT_CASE_CLOSED→Task 不恢复 |
| D 时区 | `TestDueAtTimezone.test_mixed_offset_future_not_early_expired` | 混合 offset 未来不提前过期 |
| D 时区 | `TestDueAtTimezone.test_mixed_offset_past_must_expire` | 混合 offset 过去必须过期 |
| D 时区 | `TestDueAtTimezone.test_naive_due_at_rejected` | naive 时间被拒绝 |
| E 不变量 | `TestNoZombieWaiting.test_no_zombie_waiting_task_after_normal_ops` | 不得出现 WAITING 无 Active Waiting |

> 注意：原 `TestClosedCaseRace`（"先 CLOSED 再建 Task"）已被 `TestClosedCaseRaceReal` 取代——
> 旧测试的前提（在已 CLOSED 的 Case 下直接建 Task）现为 B3/B4 明确禁止，且旧测试并未模拟
> "关后再到达 Reply/Due" 的真实竞态。

### 12.2 R1 决策补遗（在 P0 六决策之上）

- **R1-决策 A**：Task 终态（DONE/CANCELLED）由 `update_task_status` 硬性拒绝后续任何转移，
  并在 `put_task_on_waiting` 入口二次校验，杜绝"复活"。
- **R1-决策 C**：取消 Waiting 后的 Task 走向按 `cancel_reason` 三分支处理，消除僵尸 WAITING Task；
  其中 `PARENT_CASE_CLOSED` 分支与 Due/Reply 的收口逻辑一致，均**绝不恢复可执行 Task**。
- **R1-决策 D**：所有 due 时间统一 UTC 规范化、拒绝 naive，从根上消除跨时区误判。

### 12.3 R1 守界确认

未修改 D8 Action Case 关闭规则；未新增 BusinessAction / Outbox / ERP/CRM 写回 / 自动邮件 /
自动联系供应商 / 新 Agent / UI 重构 / 负载均衡 / D10 能力；未重写整个 D9 模块。

---

## 13. D9-R2 接口封口（冻结前最后一轮 Targeted Hardening）

R2 不新增产品能力，只修复独立验收发现的最后一个 P0 级接口问题：通用状态修改函数
`update_task_status` 作为**公开 API** 导出，可被外部调用方绕过业务入口直接改写 Task 状态，
从而破坏 Task / Waiting 一致性（独立验收已复现三类非法态：TODO→WAITING 无 Active Waiting、
WAITING+ACTIVE→IN_PROGRESS 冲突、WAITING+ACTIVE→TODO 冲突）。代码改动仅限 `d9_task_waiting.py`，
**D8 冻结合同零改动**，**R1 已通过的修复零改动**。

### 13.1 修复内容
- 将 `update_task_status` 收口为模块私有 `_update_task_status_internal`，并从 `__all__` 移除——
  D9 公开 API 不再暴露"任意设状态"的入口。
- 在私有漏斗内新增 Invariant A/B/C 守卫（见 §4.1）：进入 `WAITING` 须已有 `ACTIVE` Waiting；
  离开 `WAITING` 须已无 `ACTIVE` Waiting；终态（DONE/CANCELLED）不可再转移。
- `complete_task` / `cancel_task` 重排为**先取消 ACTIVE Waiting**（TASK_DONE/TASK_CANCELLED）
  再改 Task 终态，以兼容新守卫且保持 R1 语义（Waiting 正确收口、不留僵尸）。
- 所有内部调用方（start / complete / cancel / put_on_waiting / resolve / expire /
  cancel_waiting）改用新名，行为不变。

### 13.2 R2 新增攻击测试（现 40 passed）

| 类别 | 测试 | 覆盖 |
|------|------|------|
| 公开 API 封口 | `TestPublicApiNoStatusBackdoor.test_update_task_status_not_in_public_api` | `"update_task_status" not in __all__` 且无属性 |
| 后门拒绝 TODO→WAITING | `…test_todo_cannot_become_waiting_via_internal_backdoor` | Invariant A 守卫 |
| 后门拒绝 WAITING+ACTIVE→IN_PROGRESS | `…test_waiting_plus_active_cannot_become_in_progress_via_backdoor` | Invariant B 守卫 |
| 后门拒绝 WAITING+ACTIVE→TODO | `…test_waiting_plus_active_cannot_become_todo_via_backdoor` | Invariant B 守卫 |
| 严格不变量全流程 | `TestStrictInvariantsAfterBusinessOps.test_full_flows_preserve_invariants` | 5 条业务流后 A/B/C 校验 |
| 终态不留 Active Waiting | `TestStrictInvariantsAfterBusinessOps.test_terminal_task_keeps_no_active_waiting` | Invariant C |

### 13.3 R2 守界确认

未修改 D8 Action Case 关闭规则；未新增 BusinessAction / Outbox / ERP/CRM / UI / 新 Agent /
新 Due 能力 / 新 Task 功能 / D10 能力；未大规模重构；未删除 R1 任何测试、未放宽断言。

### 13.4 冻结结论

D9 专项：34（R1）→ **40**（R2）passed；全项目回归：**459 passed / 26 skipped**。
逐项满足用户验收的六类问题（Terminal 不可复活 / Closed Case 真实竞态 / Late Reply ghost /
Waiting Cancel 僵尸 / Mixed timezone Due / 公开状态修改后门）。**D9 可冻结。**
