# FlowOrder V6.1.4.1 部署与配置步骤

## 一、GitHub代码部署（必须）

1. 在当前仓库新建备份分支：`backup-before-v6.1.4.1`。
2. 推荐解压并上传 `FlowOrder_V6.1.4.1_GitHub_Ready.zip` 中的全部内容到仓库根目录，覆盖同名文件。
3. 不要把ZIP本身上传，不要多套一层目录。
4. 提交信息：`feat(agent): add hybrid intent router and shared FT04 ranking`。
5. 等待Railway自动部署。
6. 不删除Railway Volume，不清空数据库，不重新导入订单。

## 二、Railway环境变量（必须核对）

保留原有：

- `DB_PATH=/data/action_layer.db`
- `COZE_API_TOKEN`
- `COZE_AGENT_BOT_ID`
- `FLOWORDER_AGENT_API_KEY`

建议明确设置：

- `COZE_AGENT_TIMEOUT_SECONDS=60`

代码已将开放Coze请求上限限制为60秒；标准风险诊断不依赖Coze。

## 三、部署后接口检查（必须）

打开：

- `/health`：状态应为ok，版本为6.1.4.1；
- `/api/system/storage`：`on_persistent_path=true`、`writable=true`；
- `/api/agent/status`：
  - `performance_profile.name=HYBRID_ROUTED_AGENT`
  - `hybrid_intent_router=true`
  - `multi_intent_plan=true`
  - `shared_ranking_rule=FT04_SHARED_V1`

浏览器强制刷新后，Network应加载：

- `/static/app.js?v=6.1.4.1-hybrid-router`

## 四、Coze生产插件（必须完成一次）

推荐将正式网站Bot从12工具插件切换到6工具精简插件：

`coze/floworder_agent_plugin_openapi_production_6tools_railway.json`

包含：

1. `diagnose_priority_orders`
2. `parse_bulk_order_updates`
3. `get_order_diagnostic_context`
4. `create_task_draft`
5. `draft_message`
6. `get_approval_status`

操作原则：

1. 在Coze工作空间中新建或更新FlowOrder生产插件；
2. 导入上述OpenAPI JSON；
3. 插件认证Header继续使用现有 `X-FlowOrder-Agent-Key`；
4. 将服务地址保持为当前Railway正式域名；
5. 在正式Bot中移除旧12工具插件，挂载新的6工具插件；
6. 12工具插件保留给调试Bot，不挂正式网站Bot。

## 五、Coze系统提示词（必须完成一次）

1. 打开正式FlowOrder Bot的人设/系统提示词；
2. 用 `coze/AGENT_SYSTEM_PROMPT_V6_1.md` 的完整内容覆盖；
3. 保存并发布Bot；
4. 确认其中包含“V6.1.4.1 混合意图路由与工具选择”。

## 六、验收用例（必须）

### 1. 口语化风险诊断

输入：

> 我今天一来事情特别多，客户也在催，工厂也有几笔不太对，你先别发消息，帮我看看接下来两周到底哪些订单最危险，我应该先处理哪个。

预期：

- 秒级或数秒内完成；
- `execution_mode=HYBRID_DETERMINISTIC_PLAN`；
- 识别为 `RISK_DIAGNOSIS`；
- 时间范围14天；
- 不调用Coze；
- 不创建任务；
- 不发送消息。

### 2. 多目标

输入：

> 检查未来两周最危险的订单，解释第一笔为什么优先，再给它建一个任务，但不要发消息。

预期工具顺序：

`diagnose_priority_orders → create_task_draft → backend_finalize_agent_run`

并返回真实 `task_draft_id` 和 `approval_id`。

### 3. 同会话追问

输入：

> 为什么第一笔排在最前？

预期：

- 使用上一轮 `previous_run_id`；
- 不重新扫描全部订单；
- 不调用Coze；
- 依据上一轮真实 `priority_reasons` 回答。

### 4. 批量进展

粘贴两笔以上订单进展。

预期：

- 调用 `parse_bulk_order_updates`；
- 生成候选和确认入口；
- 不直接写回；
- 同一句中若还要求风险排序，先要求确认候选，不能用未确认数据排序。

### 5. 模糊指令

输入：

> 订单好像有点问题，你处理一下。

预期：只提出一个澄清问题，不调用业务工具。

### 6. 权限

跟单员输入“检查团队所有订单”。

预期：后端仍按本人订单权限执行或提示权限不足，不扩大范围。

## 七、回滚

出现严重问题时：

1. 将GitHub切回 `backup-before-v6.1.4.1`；
2. Railway重新部署；
3. 数据库无需回滚，本版本没有数据库结构迁移。
