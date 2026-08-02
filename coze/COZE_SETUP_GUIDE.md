# Coze配置指南｜FlowOrder订单异常诊断Agent

## 1. 导入插件

1. 在Agent编排页展开“插件”，点击`＋`。
2. 点击“创建插件”。
3. 选择“云端插件—基于已有服务创建”。
4. 点击“导入”，上传：
   `coze/floworder_agent_plugin_openapi.yaml`
5. 插件URL应为：
   `https://ai-ft-action-layer-mvp.onrender.com`
6. 在Header列表添加：
   - Key：`X-FlowOrder-Agent-Key`
   - Value：与Render环境变量`FLOWORDER_AGENT_API_KEY`完全相同的随机密钥
7. 不要选择“不需要授权”的正式配置。
8. 发布插件版本，例如：`v1.0.0`。

## 2. 添加工具

Agent首版建议启用以下10个工具：

- `start_agent_run`
- `list_candidate_orders`
- `get_order_diagnostic_context`
- `build_anomaly_candidate`
- `rank_anomaly_candidates`
- `create_task_draft`
- `draft_message`
- `create_approval_request`
- `get_approval_status`
- `complete_agent_run`

不要直接暴露数据库写入工具。

## 3. 添加工作流

保留并添加：

- FT01 客户消息识别
- FT02 工厂回复识别
- FT03 人工确认写回
- FT04 行动排序
- FT05 沟通转任务
- FT06 受控沟通草稿

普通用户回复中不要出现FT编号；编号只用于维护和排障。

## 4. 设置模型

### 主Agent

先使用“豆包·1.6·自动深度思考”进行评测；再与DeepSeek-V3工具调用对比。复杂冲突场景才考虑豆包1.8深度思考。

### FT05/FT06

导入优化包后，手动将LLM节点切换到“豆包·1.6·极速速度·250828”，并完成评测再发布。

## 5. 粘贴系统提示词

将`coze/AGENT_SYSTEM_PROMPT.md`中的正文粘贴到“人设与回复逻辑”。

建议不要开启自动知识库调用。第一版动态订单数据全部通过API工具获取；知识库仅适合未来存放稳定SOP和异常处置规范。

## 6. 配置变量

建议添加：

| 变量 | 默认值 | 说明 |
|---|---|---|
| organization_id | ORG-DEMO | 演示组织 |
| current_user_id | USER-1 | 当前用户 |
| current_role | operator | operator或manager |
| allowed_owner_ids | ["USER-1"] | 数据范围 |
| default_due_within_days | 14 | 默认交期窗口 |
| default_top_n | 7 | 最多返回数 |

网站通过Coze API调用时会传入这些变量。

## 7. 配置每日触发器

使用`coze/DAILY_TRIGGER_PROMPT.md`：

- 周期：每天
- 时间：08:30
- 时区：Asia/Shanghai

若个人版触发器无法分别传递不同用户，可先建立主管团队巡检；或为USER-1、USER-2、USER-3分别创建任务。

## 8. 发布API

在发布页面勾选API渠道。发布后：

1. 记录Bot ID；
2. Render设置`COZE_AGENT_BOT_ID`；
3. 保持`COZE_API_TOKEN`可调用该Agent；
4. 重新部署网站；
5. 访问“异常诊断”页面，使用“让Agent诊断”。

网站使用官方`cozepy` SDK流式调用已发布Agent。

## 9. 隐私声明

插件会处理：

- 当前用户ID和角色；
- 订单字段；
- 客户/工厂消息；
- 任务、联系和等待记录。

因此发布时应按实际情况声明会传输业务数据。测试阶段只使用脱敏或合成数据。

## 10. 联调顺序

1. 访问`/health`，确认版本为6.0.0且持久盘正常；
2. 调用`/api/agent/status`；
3. 在Coze单独测试`list_candidate_orders`；
4. 测试订单上下文；
5. 测试异常候选；
6. 测试排序；
7. 测试任务和消息草稿；
8. 测试审批请求；
9. 最后测试完整自然语言诊断。
