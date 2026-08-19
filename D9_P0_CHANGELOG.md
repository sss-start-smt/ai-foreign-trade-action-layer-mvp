# D9-P0 CHANGELOG — Task / Waiting / Due Recovery 最小执行闭环

- **Policy 版本**：`D9_TASK_WAITING_V1`
- **交付日期**：2026-08-13
- **依赖基线**：D8 Action Case 合同（冻结，未改动）；FlowOrder 版本 `6.1.4.1.3`
- **状态**：独立验收就绪（测试全绿，未改动生产代码 / 未改动 D8 测试期待 / 未扩大范围）

---

## 1. 本轮目标（唯一）

实现最小执行闭环：**Action Case → Task → Waiting → Resolve/Due → Task 恢复**。
D9 严格建立在**已冻结的 D8 Action Case 合同之上**，只新建独立的 `d9_*` 表，
**永不写入或改动 `action_cases`、永不改动 D8 的创建/状态/关闭规则**。

---

## 2. 改动文件清单（按相对路径）

### 2.1 新建

| 文件 | 作用 |
|------|------|
| `d9_task_waiting.py` | D9 核心实现：三层状态机、幂等 Due Recovery、Trace。复用 D8 的 `_new_id / _now_iso / _row_to_dict / _conn_exec / CN_TZ` 与只读 `get_case_by_id`。 |
| `d9_trace_example.py` | 可在真实 SQLite 上跑通的演示脚本，输出 Action Case→Task→Waiting→Partial Reply→Due 到期→Task 恢复的完整 Trace（用于本文档第 8 节示例）。 |
| `alembic/versions/g9d0e1f2a3b4_add_d9_task_waiting.py` | 三张 `d9_*` 表的 Alembic 迁移（`down_revision='f8a3b7c2d1e4'`），含 partial unique index `uq_d9_waitings_active` 与 `downgrade()`。 |
| `tests/test_d9_task_waiting.py` | 20 个测试，覆盖第 3 节要求的全部攻击面。 |

### 2.2 修改

| 文件 | 改动 |
|------|------|
| `schema.sql` | 在 `idx_action_cases_intent` 之后追加 `d9_action_case_tasks`、`d9_action_case_waitings`（含 `uq_d9_waitings_active` partial 唯一索引、`idx_d9_waitings_due_scan` 到期扫描索引）、`d9_trace_events` 三张表及索引。 |

### 2.3 未改动（守界证明）

- `d8_action_case.py` —— D8 冻结合同，零改动。
- `action_cases` 表结构 / 任何 D8 状态机 —— 零改动。
- 任何 BusinessAction / Outbox / ERP / CRM / 邮件 / 自动分配 / 新 Identity 算法 / UI —— 不在范围内。
- 任何 D7 / D8 既有测试文件 —— 零改动，回归全绿。

---

## 3. 测试与验收结果

### 3.1 D9-P0 新增测试（20 passed）

| 类 | 覆盖的验收点 |
|----|-------------|
| `TestNormalResolveFlow` | 正常 Task→Waiting→Reply(RESOLVE)→Resume；Case 不被改动 |
| `TestPartialReply` | Partial Reply **不结束** Waiting；仅完整回复结束 |
| `TestDuplicateReply` | 同 `reply_id` 重复消费幂等；仅 1 次 RESOLVE、1 次恢复 |
| `TestDueRecoveryExpiry` | 到期 → EXPIRED 且 Task 恢复 |
| `TestDueRecoveryIdempotency` | 重复扫描 / 重启后扫描 / 已 EXPIRED 再扫描——均幂等 no-op |
| `TestWaitingCancel` | 取消排除于 Due 扫描；重复取消幂等 |
| `TestMultipleTasks` | 一 Case 多 Task：Waiting 一个不抑制另一个；Case 不冻结 |
| `TestMultipleWaitings` | 一 Case 多 Waiting：A 到期不影响 B；Future B 不被误恢复 |
| `TestClosedCaseRace` | Closed Case + Active Waiting：CANCELLED(PARENT_CASE_CLOSED)，不重开 Case、不建 ghost Task |
| `TestNoAutoCloseCase` | Task 完成 / Waiting Resolve / Waiting 到期 **都不**自动关闭 Case |
| `TestInvariants` | 一 Task 仅一个 ACTIVE Waiting（幂等返回）；组织不匹配拒绝；Case 必须存在 |
| `TestTraceChain` | Trace 能重建"任务为何又回到待办" |

> 运行命令（在 `02_ENGINEERING_CURRENT/source/floworder` 下）：

```
python -m pytest tests/test_d9_task_waiting.py -q    # 20 passed
```

### 3.2 D7 / D8 回归结果（全绿）

| 套件 | 结果 |
|------|------|
| `tests/test_d8_action_case.py` | **49 passed** |
| `tests/test_d7_risk_engine.py` | **57 passed** |
| `tests/test_d7_integration.py` | **45 passed** |

> 注：D7 集成与引擎测试依赖 `fastapi / pydantic / httpx / uvicorn`（D9 之前环境中缺失），
> 安装后全部转绿；这些失败与 D9 改动无关（根因为 `ModuleNotFoundError`，非逻辑回归）。

---

## 4. 本轮新增决策（详见 `D9_TASK_WAITING_CONTRACT.md` 第 10 节）

1. **为何 Task / Action Case 分离**：三层是独立状态机，Task 完成/取消绝不反向修改 Case。
2. **为何 Waiting 只抑制所属 Task**：进入 Waiting 仅把该 Task 置 WAITING，不冻结 Case、不抑制同 Case 其他 Task（测试 `TestMultipleTasks` 证明）。
3. **为何 Due 只恢复 Task、不操作 Case**：Due Recovery 仅 EXPIRED→恢复 Task，且 Case CLOSED/缺失时短路 CANCEL，绝不重开或写入 `action_cases`。
4. **幂等如何实现**：所有状态变更用 `UPDATE … WHERE status='ACTIVE'`（条件 CAS）+ rowcount 判定；reply 用 `reply_id` 去重；partial unique index 保证一 Task 一 ACTIVE Waiting；trace 事件仅在 rowcount>0 时写入。
5. **Partial Reply 判定**：`satisfies_completion=False` 只记录证据、保持 ACTIVE；"收到一条消息"不等于结束等待，必须由调用方显式判定（目前由外部判定器/人工注入 `satisfies_completion`）。
6. **Closed Case + Active Waiting 处理**：Due 扫描先读 Case 状态，CLOSED/缺失 → `CANCELLED(reason=PARENT_CASE_CLOSED)`，不恢复 Task、不重开 Case、不产生 ghost。

---

## 5. 已知限制（Known Limitations，详见合同第 9 节）

- Due Recovery 为 **单机顺序扫描**（非分布式锁）。多实例并发仅靠 DB 条件 UPDATE 的原子性保证"仅一次有效恢复"，但建议部署期配合独立 worker 或行锁以避免重复扫描开销。
- `satisfies_completion` 的判定逻辑（"什么算完整回复"）**不在本模块内**——由外部判定器/人工注入；本模块只消费该布尔值。这是有意的边界收敛。
- 未实现 Waiting 的自动超时重试策略编排（如 N 次 EXPIRED 后升级）。当前 EXPIRED 后 Task 回到 IN_PROGRESS，由上层决定下一步。
- Trace 事件时间戳精度为秒；同秒内的多事件排序依赖 `trace_id` 字典序，文档示例已按业务时序重新整理。

---

## 6. Future Work（明确不在本轮范围，仅记录）

- BusinessAction / Outbox 写回、ERP/CRM 集成、自动邮件/自动联系供应商。
- 自动 Task 分配 / 负载均衡 / 新 Identity 路由算法。
- D9 接入 API 层（当前 D8 也未接 API；D9 仅提供领域层函数）。
- 跨组织管理员视图、Waiting 看板 UI 大改。

---

## 7. 复现步骤

```bash
# 1) 建库（schema.sql 已含 d9 表；或 alembic upgrade head）
# 2) 跑 D9 测试
python -m pytest tests/test_d9_task_waiting.py -q
# 3) 跑真实 Trace 演示
python d9_trace_example.py
```

> 运行期依赖（已验证）：Python 3.13.12 管理版 + `sqlalchemy==2.0.35`、`pytest==9.1.1`、`fastapi==0.141.1`、`pydantic`、`httpx`、`uvicorn`。

---

## 8. D9-R1 加固补丁（2026-08-13）

### 8.1 目标
修复独立验收发现的 P0 状态一致性问题，使系统不可能产生：① Terminal Task 被重新激活；
② Closed Case 下 ghost action；③ 无 Active Waiting 的僵尸 WAITING Task；④ 不同时区导致的错误 Due 判断。
**仅改 `d9_task_waiting.py` 与测试，D8 零改动，不进 D10。**

### 8.2 代码改动（`d9_task_waiting.py`）
- **A — 冻结 Task FSM**：`create_task` 移除 `status` 参数（新 Task 仅能从 `TODO` 起步）；
  `update_task_status` 对 `DONE`/`CANCELLED` 终态拒绝任何后续转移；
  `put_task_on_waiting` 仅允许 `IN_PROGRESS` Task 进入 `WAITING`（TODO 须先 start，DONE/CANCELLED 拒绝）。
- **B — 真实 Closed Case 竞态**：`create_task` 在父 Case CLOSED 时拒绝（B3）；
  `put_task_on_waiting` 在父 Case CLOSED/缺失时拒绝（B4）；
  `_resolve_waiting_internal` 与 `run_due_recovery` 在父 Case CLOSED 时**不恢复 Task**，
  改为把 `WAITING` 的 Task 安全收口为 `CANCELLED(PARENT_CASE_CLOSED)`（B1/B2）。
- **C — 取消 Waiting 不留僵尸**：`_cancel_waiting_internal` 按 `cancel_reason` 三分支——
  MANUAL(父Case ACTIVE)→Task 恢复 `IN_PROGRESS`；TASK_DONE/TASK_CANCELLED→Task 保持终态；
  PARENT_CASE_CLOSED→Task 收口 `CANCELLED`（绝不恢复）。
- **D — 时区规范化**：新增 `_normalize_iso_to_utc`；`due_at` 写入与 `run_due_recovery(current_time)`
  比较前统一规范化为 UTC ISO8601，naive（无时区）时间直接拒绝。

### 8.3 测试
- D9 套件：**20 → 34 passed**（新增 14 个攻击测试，见合同 §12.1）。
- 全项目回归：**453 passed / 26 skipped**（原 439 passed 基线 + 14，无回退）。
- 原 `TestClosedCaseRace`（先 CLOSED 再建 Task，前提已被 B3/B4 禁止且非真实竞态）由
  `TestClosedCaseRaceReal`（B1–B4）取代；原 `test_cancel_and_excluded_from_due` 的
  Task 状态断言已按新合同（MANUAL 取消→恢复 IN_PROGRESS）更正，未放宽。
- 所有新增测试均以断言验证最终 DB 状态，无跳过、无 xfail，失败不写 PASS。

### 8.4 交付物
- `d9_task_waiting.py`（R1 加固）
- `tests/test_d9_task_waiting.py`（34 tests）
- `D9_TASK_WAITING_CONTRACT.md`（已更新 §3.1/§3.2/§6/§6.5/§12）
- `D9_R1_FIX_REPORT.md`（8 节修复报告）
- `D9_P0_CHANGELOG.md`（本 §8）

---

## 9. D9-R2 接口封口（2026-08-13，冻结前最后一轮）

### 9.1 目标
修复独立验收发现的最后一个 P0 级接口问题：通用状态修改函数 `update_task_status` 作为**公开
API** 导出，可被外部调用方绕过业务入口直接改写 Task 状态，破坏 Task / Waiting 一致性。本轮只封
这一个后门，**不重启任何已通过的 R1 设计**，D8 零改动，不进 D10。

### 9.2 代码改动（`d9_task_waiting.py`）
- 将 `update_task_status` 收口为模块私有 `_update_task_status_internal`，并**从 `__all__` 移除**——
  D9 公开 API 不再暴露"任意设状态"的入口；Task 状态只能经由业务动作驱动。
- 在私有漏斗内新增 Invariant A/B/C 守卫：进入 `WAITING` 须已有 `ACTIVE` Waiting（杜绝
  Task=WAITING 无 Active Waiting 僵尸）；离开 `WAITING` 须已无 `ACTIVE` Waiting（杜绝
  Task≠WAITING 却仍 ACTIVE Waiting 的冲突态）；终态（DONE/CANCELLED）不可再转移。
- `complete_task` / `cancel_task` 重排为**先取消 ACTIVE Waiting** 再改 Task 终态，以兼容新守卫
  且保持 R1 语义（Waiting 正确收口、不留僵尸）。
- 所有内部调用方（start / complete / cancel / put_on_waiting / resolve / expire /
  cancel_waiting）改用新名，行为不变。

### 9.3 测试
- D9 套件：**34 → 40 passed**（新增 6 个攻击测试：`TestPublicApiNoStatusBackdoor` 4 个 +
  `TestStrictInvariantsAfterBusinessOps` 2 个，见合同 §13.2）。
- 全项目回归：**459 passed / 26 skipped**（453 基线 + 6，无回退、无放宽断言、未删 R1 测试）。
- 全部以断言验证最终 DB 状态，无跳过、无 xfail，失败不写 PASS。

### 9.4 交付物
- `d9_task_waiting.py`（R2 接口封口）
- `tests/test_d9_task_waiting.py`（40 tests）
- `D9_TASK_WAITING_CONTRACT.md`（已更新 §3.1/§4.1/§13）
- `D9_R2_FIX_REPORT.md`（8 节修复报告）
- `D9_P0_CHANGELOG.md`（本 §9）
