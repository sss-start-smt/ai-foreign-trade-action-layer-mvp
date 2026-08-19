# D9_R1_FIX_REPORT — Task / Waiting 状态一致性与 Closed Case 竞态修复

- **轮次**：D9-R1（Targeted Hardening，非 D10）
- **日期**：2026-08-13
- **Policy 版本**：`D9_TASK_WAITING_V1`（同 P0，仅补丁）
- **改动范围**：仅 `d9_task_waiting.py` + `tests/test_d9_task_waiting.py`
- **D8 冻结合同**：零改动 ｜ **禁止项**：未新增 BusinessAction/Outbox/ERP-CRM 写回/自动邮件/
  自动联系供应商/新 Agent/UI 重构/负载均衡/D10

---

## 1. 原 P0 实现为什么"20 passed"仍遗漏这些问题

P0 的 20 个测试都是**正向/幂等**场景，且大部分**预设父 Case 一直 ACTIVE**。它们验证的是
"正常闭环不崩"，却没验证三条"状态收口"硬约束：

1. **Terminal Task 复活**：`put_task_on_waiting` 没有校验入参 Task 的状态，DONE/CANCELLED
   可以被直接改回 WAITING——P0 测试从未把 DONE 的 Task 再 `put_task_on_waiting`。
2. **Closed Case 竞态**：原 `TestClosedCaseRace` 的做法是"先把 Case 设成 CLOSED，再建 Task、
   再建 Waiting、再扫描"。这**不是真实竞态**——真实竞态是"Waiting 建好、Case 还是 ACTIVE，
   之后 Case 才被合法关闭，Reply/Due 随后才到达"。旧测试既没模拟这个窗口，又因前提是
   '先 CLOSED 再建 Task' 而与 R1 的 B3/B4 直接冲突。
3. **Cancel 僵尸**：旧 `cancel_waiting` 只改 Waiting、不碰 Task，导致 `Task=WAITING 但无
   Active Waiting`。旧测试断言 `Task 仍 WAITING`，等于**把 bug 写成了期望**。
4. **时区**：旧代码直接 `due_at <= current_time` 字符串比较，P0 测试全部用同一 `+08:00` 偏移，
   永远测不出跨 offset 误判。

一句话：**测试覆盖了"快乐路径"和"幂等"，但没覆盖"非法状态转移"和"关闭后到达"这两类对抗场景。**

---

## 2. 原 Closed Case 测试为什么没有模拟真正的竞态

原 `TestClosedCaseRace.test_closed_case_waiting_cancelled_no_ghost` 的步骤是：

```
_seed_case(lifecycle_status="CLOSED")   # 一开始就 CLOSED
→ create_task()                         # 在已 CLOSED 的 Case 下建 Task
→ put_task_on_waiting()
→ run_due_recovery()
```

这在 R1 之前能跑通，但有两个根本问题：
- **时序颠倒**：真实系统的顺序是 `ACTIVE → 建 Waiting → Case 关闭 → 之后 Due/Reply 到达`。
  旧测试把"关闭"放在最前，根本没有竞态窗口。
- **前提已被新合同禁止**：R1 的 B3 要求 `create_task` 在 CLOSED Case 下**必须拒绝**，B4 要求
  `put_task_on_waiting` 在 CLOSED Case 下**必须拒绝**。旧测试的核心动作现在本身就是非法操作。

因此 R1 用 `TestClosedCaseRaceReal`（B1–B4）**取代**旧测试，真实复现"关后再到达"窗口。

---

## 3. Terminal Task 为什么必须不可复活

DONE / CANCELLED 代表"该行动已终结"的业务结论。若允许它们被 `put_task_on_waiting` 改回
WAITING（进而恢复 IN_PROGRESS），会产生：
- **审计失真**：已完成/已取消的行动重新出现在待办，一线无法解释"为什么又回来了"。
- **权责混乱**：谁有权"复活"一个已关闭的行动？这等价于隐式重开业务结论。
- **违反三层分离精神**：Task 的终态应由明确的 `complete_task`/`cancel_task` 决定，不能被
  Waiting 子系统私自翻转。

R1 做法：
- `update_task_status` 对 `DONE`/`CANCELLED` 直接抛 `D9StateError`（终态守卫）。
- `put_task_on_waiting` 入口二次校验 `task.status == "IN_PROGRESS"`，否则拒绝。
- `create_task` 移除 `status` 参数，新 Task 永远只能从 `TODO` 起步（无后门）。

---

## 4. Waiting Cancel 为什么不能留下孤立 WAITING Task

旧 `cancel_waiting` 只把 Waiting 置 CANCELLED，Task 仍停在 WAITING。结果是：

```
Task.status = WAITING   但   get_active_waiting_for_task() = None
```

这就是"Task 声称在等某个条件，但实际上没有任何等待对象"的僵尸态。它在 UI 上表现为
"一个永远等不到结果的待办"，且 Due Worker 再也不会碰它（没有 Active Waiting 可扫）。

R1 按 `cancel_reason` 三分支解决（绝不混为一谈）：
- **MANUAL**（父 Case ACTIVE）：行动不再被外部条件阻塞 → Task 恢复 `IN_PROGRESS`，
  "原行动重新可处理"。
- **TASK_DONE / TASK_CANCELLED**：Task 已是终态，保持不动（Waiting 只是被顺带取消）。
- **PARENT_CASE_CLOSED**：Task **绝不恢复**，安全收口为 `CANCELLED`，避免 ghost action。

所有分支都保证：取消 Waiting 后，**要么 Task 回到可执行（IN_PROGRESS），要么进入终态
（DONE/CANCELLED），绝不卡在 WAITING 且无 Active Waiting**。

---

## 5. Closed Case + Late Reply 为什么会产生 ghost action

旧 `record_waiting_reply(satisfies_completion=True)` 在解析 Waiting 时**不检查父 Case 状态**，
无条件把 Task 从 WAITING 恢复为 IN_PROGRESS。于是当 `Case=CLOSED` 却迟到一条"完整回复"时：

```
Case = CLOSED
Waiting = RESOLVED
Task = IN_PROGRESS      ← 在一个已关闭的 Case 下，凭空多出一个"可执行行动" = ghost
```

这个 IN_PROGRESS Task 既不被任何 Active Waiting 驱动，也不该在已关闭 Case 下推进，却会出现在
一线待办里——正是 ghost action。

R1 修复（`_resolve_waiting_internal` 内）：
- 解析前读父 Case；若 `CLOSED/缺失` → **不恢复 Task**，RESOLVED 记录回复证据后，把 Task 收口为
  `CANCELLED(PARENT_CASE_CLOSED)`。
- 结果：`Case=CLOSED, Waiting=RESOLVED, Task=CANCELLED`——无新可执行行动，且不留僵尸
  （Task 是终态，非 WAITING）。

---

## 6. 为什么 ISO8601 带不同时区 offset 不能直接做字符串时间比较

`due_at` 与 `current_time` 都是 ISO8601 字符串，但**偏移不同**时，字典序 ≠ 时间序。例：

```
due_at     = 2026-08-13T02:30:00+00:00   (北京 10:30)
current_time = 2026-08-13T10:00:00+08:00  (北京 10:00 = UTC 02:00)
```

逐字符比较，`"02:30:00+00:00"` 的 `"02:30"` 段 < `"10:00:00+08:00"` 的 `"10:00"` 段，
于是系统**误判 due_at 已过期**（实际还差 30 分钟）。反向：若 due 在 `+00:00` 已过期、current
在 `+08:00` 看似更早，又会被**漏扫**。

R1 做法（`_normalize_iso_to_utc`）：
- 所有写入与比较前的 due 时间**统一规范化为 UTC ISO8601**（`+00:00` 偏移）。
- 数据库内再做同格式字符串比较——偏移一致后，字典序即时间序，正确。
- **naive（无时区）时间直接拒绝**，而不是用某个默认时区去猜（猜错比报错更危险）。
- 不引入新时间服务/基础设施，仅一处纯函数。

---

## 7. 本轮具体新增了哪些攻击测试

D9 套件由 **20 → 34 passed**（+14）。新增（均验证最终 DB 状态，无放宽、无跳过）：

| 类别 | 测试 | 验证 |
|------|------|------|
| A 终态 | `TestTerminalTaskFsm.test_done_task_rejects_put_on_waiting` | DONE 进 WAITING 被拒 |
| A 终态 | `TestTerminalTaskFsm.test_cancelled_task_rejects_put_on_waiting` | CANCELLED 进 WAITING 被拒 |
| A 终态 | `TestTerminalTaskFsm.test_todo_task_rejects_put_on_waiting` | TODO 须先 start |
| B1 | `TestClosedCaseRaceReal.test_b1_due_after_close_no_resume` | 关后再 Due：Case 不重开、Waiting→CANCELLED、Task 收口 CANCELLED、无 ghost、无新 Case |
| B2 | `TestClosedCaseRaceReal.test_b2_late_full_reply_after_close_no_resume` | 关后晚到完整 Reply：Waiting=RESOLVED、Task=CANCELLED、不恢复 |
| B3 | `TestClosedCaseRaceReal.test_b3_create_task_on_closed_case_rejected` | CLOSED Case 拒绝建 Task |
| B4 | `TestClosedCaseRaceReal.test_b4_put_on_waiting_after_close_rejected` | Case 关后拒绝建 Waiting、Task 仍 IN_PROGRESS |
| C | `TestCancelSemantics.test_manual_cancel_resumes_task` | MANUAL 取消→Task 恢复 IN_PROGRESS、无 Active Waiting |
| C | `TestCancelSemantics.test_task_done_keeps_done_on_waiting_cancel` | TASK_DONE→Task 保持 DONE |
| C | `TestCancelSemantics.test_task_cancelled_keeps_cancelled_on_waiting_cancel` | TASK_CANCELLED→Task 保持 CANCELLED |
| C | `TestCancelSemantics.test_parent_case_closed_cancel_no_resume` | PARENT_CASE_CLOSED→Task 不恢复 |
| D | `TestDueAtTimezone.test_mixed_offset_future_not_early_expired` | 混合 offset 未来不提前过期 |
| D | `TestDueAtTimezone.test_mixed_offset_past_must_expire` | 混合 offset 过去必须过期 |
| D | `TestDueAtTimezone.test_naive_due_at_rejected` | naive 时间被拒 |
| E | `TestNoZombieWaiting.test_no_zombie_waiting_task_after_normal_ops` | 不得出现 WAITING 无 Active Waiting |

> 旧 `TestClosedCaseRace`（先 CLOSED 再建 Task）已**被取代**而非保留——其前提现在被 B3/B4
> 明确禁止，且未模拟真实竞态。原 `test_cancel_and_excluded_from_due` 的 Task 断言由
> `== "WAITING"` 更正为 `== "IN_PROGRESS"`（按新合同 MANUAL 取消应恢复），属修正错误预期，
> 非放宽。

---

## 8. D9 完整回归结果

### 8.1 D9 专项
```
python -m pytest tests/test_d9_task_waiting.py -q   →  34 passed
```

### 8.2 全项目
```
python -m pytest -q   →  453 passed, 26 skipped   （原基线 439 passed + 14 新增，无回退）
```
- D8（`test_d8_action_case.py`）未改动、全绿。
- D7 引擎 / 集成未改动、全绿。
- 全项目无任何 D9 相关失败；未放宽任何既有断言以换取通过。

### 8.3 守界确认
- 未修改 `action_cases` 或任何 D8 状态机 / 关闭规则。
- 未新增 BusinessAction / Outbox / ERP / CRM / 邮件 / 分配 / 新 Identity / UI / Agent / D10。
- 未重写整个 D9 模块——仅 `d9_task_waiting.py` 内针对性加固 + 测试补强。

---

## 附：复现
```bash
cd 02_ENGINEERING_CURRENT/source/floworder
python -m pytest tests/test_d9_task_waiting.py -q   # 34 passed
python -m pytest -q                                 # 453 passed / 26 skipped
python d9_trace_example.py                          # 真实 Trace 演示仍正常
```
