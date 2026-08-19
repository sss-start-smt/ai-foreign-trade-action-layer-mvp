# D9-R2 修复报告：封闭 Task FSM 公共状态修改后门

> 轮次定位：D9 冻结前最后一轮 targeted hardening。本轮**只修一个**新发现的问题——
> `update_task_status` 作为公开 API 导出，可被绕过业务入口直接改写 Task 状态，破坏 Task /
> Waiting 一致性。**不重启 R1 已通过的修复，不修改 D8 冻结合同，不进 D10。**
>
> 完成标准（来自用户验收）：D9 专项 40 passed、全项目 459 passed / 26 skipped → **D9 可冻结**。

---

## 1. 原 P0 / R1 实现为什么仍遗漏这个后门

P0 与 R1 都把注意力放在**业务入口的合法性**上：`put_task_on_waiting` 校验只有 `IN_PROGRESS`
能进 `WAITING`、`complete_task`/`cancel_task` 收口关联 Waiting、Due Recovery 与 Reply 处理
Closed Case 竞态。这些入口彼此调用内部的 `_update_task_status_internal`（当时名为
`update_task_status`）完成受控状态转移——是正确的。

但 `update_task_status` **同时也被导出到了 `__all__`**，作为 D9 公开 API 的一部分。这意味着：

- 任何外部调用方都可以 `d9.update_task_status(task_id, "WAITING")` 这样的"任意设状态"语句，
  完全绕过 `put_task_on_waiting` / `complete_task` 等业务入口的校验。
- 状态机在"业务入口"这一层是封闭的，但在"底层 setter"这一层是敞开的——等于给状态机留了一扇
  后门。独立验收正是通过直接调用这个公开函数，复现了 P0/R1 测试从未覆盖的非法态。

R1 的攻击测试验证了"业务入口不会产出非法态"，但没有验证"非法态无法通过公开 API 被直接拼出来"。
封口之前，只要有一行 `d9.update_task_status(...)`，R1 的全部守卫都会失效。

---

## 2. 独立验收复现的三类非法态（问题 1 / 2 / 3）

| # | 输入 | 封口前结果 | 违反 |
|---|------|-----------|------|
| 1 | 新 Task：`TODO`、无 Active Waiting → `update_task_status("WAITING")` | Task=`WAITING`、Active Waiting=`None` | **Invariant A**（Task=WAITING 须有 Active Waiting） |
| 2 | 正常：`Task=WAITING` + `Waiting=ACTIVE` → `update_task_status("IN_PROGRESS")` | Task=`IN_PROGRESS` + `Waiting=ACTIVE` | **Invariant B**（"仍在等待"与"已恢复可执行"同时成立） |
| 3 | 正常：`Task=WAITING` + `Waiting=ACTIVE` → `update_task_status("TODO")` | Task=`TODO` + `Waiting=ACTIVE` | **Invariant B**（Task 已非 WAITING 却仍悬挂 Active Waiting） |

三者共同根因：存在一个"可任意写 Task 状态"的公开入口，它不感知 Waiting 实体的存在，也不强制
Task / Waiting 的一致性。

---

## 3. 为什么必须封闭 update_task_status 公共后门

产品决策非常明确：**Task 状态不能被外部调用方作为普通字段任意修改；D9 必须使用"业务动作驱动
状态变化"**。

保留 `update_task_status` 作为公开 API 的危险在于：

- **一致性无法静态保证**：每一处外部调用都可能拼出非法 (Task, Waiting) 组合，而守卫分散在调用方，
  无法收敛到一个审计点。
- **职责错位**：状态转移的业务语义（"为什么从 WAITING 回到 IN_PROGRESS"）应该写在
  `put_task_on_waiting` / resolve / expire / cancel 里，而不是交给调用方自由发挥。
- **冻结风险**：D9 冻结后，任何下游接入代码只要 import 到这个 setter，就能悄悄绕过整个 FSM。

因此最小修复是：**把通用 setter 收口为模块私有 `_update_task_status_internal`，并从 `__all__`
移除**。从此 D9 公开 API 只剩 8 类业务动作（create / start / complete / cancel / put_on_waiting /
record_waiting_reply / resolve_waiting / cancel_waiting / run_due_recovery），Task 状态只能经由
它们驱动。

---

## 4. 为什么即便改成内部 helper，仍需保留守卫

把函数改成私有后，唯一的调用方是 D9 自身的业务入口——理论上它们都"知道自己在做什么"。但把守卫
留在内部 helper 仍是必要的，理由：

- **防御性深度（defense-in-depth）**：业务入口的逻辑未来会被修改/扩展，若某次改动漏掉
  "先终结 Waiting 再改 Task" 的顺序，守卫会在最后一关拦截，而不是让非法态落库。
- **Invariant A（进入 WAITING 须已有 ACTIVE Waiting）**：只在 `put_task_on_waiting` 建好
  ACTIVE Waiting 之后才允许 Task 变为 WAITING，杜绝 TODO→WAITING 这类僵尸。
- **Invariant B/C（离开 WAITING 须已无 ACTIVE Waiting）**：Task 要离开 WAITING（去
  IN_PROGRESS / DONE / CANCELLED）之前，其 ACTIVE Waiting 必须已被 RESOLVED / EXPIRED /
  CANCELLED。否则就会出现"Task 已可执行却仍 ACTIVE Waiting"的冲突态。
- **终态守卫**：DONE / CANCELLED 不可再转移，杜绝复活。

这些守卫与 R1 的终态守卫一脉相承，只是把"Task/Waiting 联动"的校验从调用方代码**收敛并固化到
唯一的写入漏斗**，使不变量成为结构性保证而非约定。

---

## 5. 本轮新增的攻击测试（共 6 个，D9 现 40 passed）

### 5.1 公开 API 封口 + 后门拒绝（`TestPublicApiNoStatusBackdoor`）
- `test_update_task_status_not_in_public_api`：断言 `"update_task_status" not in d9.__all__`
  且 `not hasattr(d9, "update_task_status")`——公开合同不再暴露任意状态 setter。
- `test_todo_cannot_become_waiting_via_internal_backdoor`：即便调用私有漏斗，
  TODO + 无 Active Waiting → `WAITING` 仍被 `D9StateError` 拒绝（问题 1 / Invariant A）。
- `test_waiting_plus_active_cannot_become_in_progress_via_backdoor`：WAITING + ACTIVE Waiting
  → `IN_PROGRESS` 被拒绝，状态保持不变（问题 2 / Invariant B）。
- `test_waiting_plus_active_cannot_become_todo_via_backdoor`：WAITING + ACTIVE Waiting → `TODO`
  被拒绝，状态保持不变（问题 3 / Invariant B）。

### 5.2 严格不变量终态校验（`TestStrictInvariantsAfterBusinessOps`）
- `test_full_flows_preserve_invariants`：依次跑 5 条完整业务流
  （create→start→waiting→reply→resume；→due→resume；→manual cancel→resume；
  waiting task→complete→DONE；waiting task→cancel→CANCELLED），在**每个稳定态**调用全局不变量
  扫描 `_assert_global_invariants`，断言 Invariant A/B/C 全部成立。
- `test_terminal_task_keeps_no_active_waiting`：验证终态 Task 绝不悬挂 Active Waiting，且
  （无公开后门）无法被推回 WAITING。

> 全部以断言验证最终 DB 状态，无跳过、无 xfail，失败不写 PASS。**R1 原有 34 项测试一个未删、
> 一条断言未放宽**。

---

## 6. 为什么本轮不影响 R1 已通过的修复

- **未触碰 R1 逻辑**：Terminal Task 不可复活、Closed Case 真实竞态（B1–B4）、Late Reply ghost、
  Waiting Cancel 三态语义、Mixed timezone Due——这些 R1 代码原样保留。
- **`complete_task` / `cancel_task` 重排而非改写语义**：仅为兼容新守卫，把"取消 ACTIVE Waiting"
  调整到"改 Task 终态"**之前**。最终态与 R1 完全一致（Task 终态 + Waiting 正确收口为
  TASK_DONE / TASK_CANCELLED），`TestCancelSemantics` / `TestNoAutoCloseCase` 等仍全绿。
- **公开 API 契约收窄**：移除 `update_task_status` 不影响任何业务入口（它们改用私有名），也不影响
  外部——本项目无其他 `.py` 引用该函数（已 grep 确认，仅合同文档以名字描述）。

---

## 7. D9 完整回归结果

| 套件 | 结果 |
|------|------|
| `tests/test_d9_task_waiting.py`（D9 专项） | **40 passed**（R1 的 34 + R2 的 6） |
| 全项目 `pytest -q` | **459 passed / 26 skipped**（R1 基线 453 + R2 的 6，无回退） |

- 原 439 全项目基线 → R1 +14（453）→ R2 +6（459）。
- D7 / D8 冻结合同测试**零改动**、零失败。
- `python -m pytest tests/test_d9_task_waiting.py -q` 与 `python -m pytest -q` 均无删测试、无放宽断言。

---

## 8. 文档同步与守界 / 冻结结论

### 8.1 同步文档
- `D9_TASK_WAITING_CONTRACT.md`：§3.1（更新为私有漏斗 + R2 封口说明）、§4.1（新增 Invariant
  A/B/C/D 四条一致性不变量）、§13（R2 接口封口章节 + 测试对照 + 冻结结论）。
- `D9_P0_CHANGELOG.md`：新增 §9（R2 接口封口）。
- `D9_R2_FIX_REPORT.md`：本报告（8 节）。

### 8.2 守界确认（明确禁止项均未违反）
未修改 D8 Action Case 关闭规则；未新增 BusinessAction / Outbox / ERP / CRM 写回 / 自动邮件 /
自动联系供应商；未新增 UI / 新 Agent / 新 Due 能力 / 新 Task 功能 / D10 能力；未大规模重构
（仅收口一个函数 + 两处调用顺序重排）；未删除 R1 任何测试、未放宽断言。

### 8.3 冻结结论
D9 已逐项满足用户验收的六类问题：
1. Terminal Task 不可复活 ✅（R1 终态守卫）
2. Closed Case 真实竞态 ✅（R1 B1–B4）
3. Late Reply ghost action ✅（R1 B2）
4. Waiting Cancel 僵尸 Task ✅（R1 C）
5. Mixed timezone Due 判断 ✅（R1 D）
6. 公开状态修改后门 ✅（R2：封口 `__all__` + Invariant A/B/C 守卫）

**D9 可冻结。**
