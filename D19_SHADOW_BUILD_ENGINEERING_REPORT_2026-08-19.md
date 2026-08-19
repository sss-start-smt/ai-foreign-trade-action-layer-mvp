# FlowOrder D19 Shadow Build Candidate｜Engineering Report

日期：2026-08-19  
状态：**SHADOW BUILD CANDIDATE / ENGINEERING PASS**  
重要边界：**这不是 D19 真实目标用户 Shadow PASS，也不是生产上线证明。**

## 1. 本轮目标

把已确认的 V8 登录版前端从 HTML Mock 迁移进真实 FlowOrder D18 Engineering RC 代码，并连接现有 FastAPI、Agent、Human Review、BusinessAction/Outbox、Waiting、ERP Read-Only 与数据库状态，为 D19 真实目标用户 Shadow 提供可部署候选版本。

## 2. 真实前端迁移

真实前端仍由 FastAPI 静态资源提供：

- `static/index.html`：D19 Shadow-ready UI 壳与登录页
- `static/d19.css`：最终视觉样式
- `static/d19_app.js`：真实 API 驱动交互

旧 `static/app.js` 保留用于既有冻结回归合同，但 D19 页面不再加载旧脚本。

已接入的真实业务链：

- 登录：`POST /auth/login` → 交换为现有服务端 demo token
- 会话校验：`GET /auth/me`
- 今日工作台：`GET /api/dashboard`
- 订单：`GET /api/orders`、`GET /api/orders/{order_id}`
- Action Workspace：`GET /api/action-workspace`
- Agent：`POST /api/agent/chat/jobs` + job polling
- 新信息分析：`POST /api/intake/jobs` + job polling
- 候选事实确认：`/api/reviews`
- Human Review：`/api/d12/reviews`
- 联系/Waiting：`POST /api/tasks/{task_id}/contacted`
- 个性化队列/明日计划：复用 `/api/settings`
- 复盘统计：`GET /api/d19/review-summary`
- 异常/观测：D15/D16 + ERP freshness
- 管理设置：D16 Feature Flags

## 3. 登录与权限

新增 `d19_auth_api.py`：

- `/auth/login` 只负责把内部演示账号交换成**已有后端 token identity**。
- 后续业务 API 继续由现有 `X-Auth-Token` / Bearer token、RBAC 与 organization isolation 强制校验。
- 前端隐藏菜单不是权限边界，真正权限仍在后端。

内部演示账号默认密码来自 `FLOWORDER_DEMO_PASSWORD`，未配置时本地 demo fallback 为 `demo123`。

角色展示：

- `limin` → `USER-1` → 跟单员 UI
- `manager` → `MANAGER-1` → 主管 UI
- `admin` → `MANAGER-1` → 管理员 UI Profile

注意：`admin` 当前只是 UI Profile，服务端仍复用 manager identity；**不能对外声称已经建立独立生产 Admin IAM/SSO。**

## 4. 高风险确认与 Governance 边界

新增 `d19_ui_api.py` 与 `main.py` 后端硬 Gate。

关键规则：

> **高业务风险 ≠ 必须主管接管。Risk Attention 与 Governance 分离。**

只有候选事实实际修改以下正式交付/客户承诺字段时，才强制主管审批：

- `requested_delivery_date`
- `customer_delivery_date`
- `formal_delivery_date`
- `formal_customer_commitment`
- `customer_commitment`

普通跟单员绕开 UI 直接调用确认接口仍会收到 `MANAGER_REVIEW_REQUIRED`。

高风险但不涉及正式字段变化的候选：不能静默批量确认，但可以在单独检查后由有权限的一线用户确认。

正式字段变化：`PENDING → APPROVAL_PENDING → Manager confirm`。

## 5. 交互持久化

没有新增数据库表或 Alembic Migration。

以下 Shadow UI 偏好复用现有 `user_settings` JSON：

- `d19_manual_order_ids`：个人行动队列顺序
- `d19_tomorrow_plan`：明日计划

因此首页 Top 5 与完整行动队列使用同一状态源，刷新后仍可恢复个人顺序。

## 6. 仍保持的真实产品边界

- ERPNext：真实 **Read-Only**；没有 ERP Write-back Adapter。
- 沟通草稿：Agent 可以生成/编辑；当前**不会真实发送邮件/企微/消息**。
- `BusinessAction.ACCEPTED` 仍不等于外部动作已成功执行。
- `RESULT_UNCERTAIN` 仍禁止盲目重试，必须先对账。
- Agent 不可绕过 D12 Human Review。
- 不记录 Hidden Chain-of-Thought。

## 7. 测试证据

### D19 定向 / Auth / 回归边界

最终定向集：

- `49 passed / 0 failed`

覆盖：D19 login、token identity、Auth Inventory、Auth Surface、V6.1.3旧前端冻结合同、正式字段主管审批 Gate、既有 intake confirm flow。

### 完整测试套件

由于单条 pytest 命令在当前执行器包装层会触发超时，完整 42 个 `tests/test_*.py` 文件按排序拆成 3 组执行，文件无遗漏：

- Group 1：`183 passed / 0 failed`
- Group 2：`113 passed / 0 failed`
- Group 3：`401 passed / 26 skipped / 0 failed`

总计：

**697 passed / 26 skipped / 0 failed**

这等于 D18 的 691 passed 基线 + D19 新增 6 个测试，skip 数未增加。

### API / Static Smoke

在全新临时 SQLite DB 上初始化并 Seed 后验证：

- `/` → 200，包含 D19 login shell + `d19_app.js`
- `/auth/login` → 200
- `/auth/me` → 200
- `/api/dashboard` → 200
- `/api/orders` → 200
- `/api/action-workspace` → 200
- `/api/reviews?status=ALL` → 200
- `/api/d12/reviews?status=PENDING` → 200
- `/api/d19/review-summary` → 200
- `/api/settings` → 200

结果：**API_STATIC_SMOKE_PASS**

### 浏览器级 Smoke 的证据边界

当前运行环境的 Chromium/Playwright 访问 localhost 被平台策略拦截（`ERR_BLOCKED_BY_ADMINISTRATOR`），因此**不能声称本地 headless 浏览器端到端 Smoke 已通过**。

这不是应用 API 失败，但部署后必须在真实部署 URL 上补一次浏览器手工/自动 Smoke。

## 8. D19 下一 Gate

当前可标记：

**D19 Shadow Build Candidate — Engineering PASS**

当前不可标记：

- D19 Target User Shadow PASS
- Real Pilot PASS
- Production Deployment Proven
- ERP Write-back Proven
- External Message Delivery Proven

下一步：

1. 提交 GitHub feature branch / PR。
2. CI 复核。
3. 合并部署分支并重新部署。
4. 在真实部署 URL 做浏览器 Smoke。
5. 冻结 Shadow Build。
6. 让真实目标跟单用户执行 D19 Shadow 任务并记录行为证据。
