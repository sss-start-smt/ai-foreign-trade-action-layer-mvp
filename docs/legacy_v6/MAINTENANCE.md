# 后期维护手册

## 版本分层

- Agent系统提示词：`coze/AGENT_SYSTEM_PROMPT.md`
- 插件接口：`coze/floworder_agent_plugin_openapi.yaml`
- 业务规则：`agent_api.py`
- Coze调用：`coze_agent_client.py`
- FT05/FT06：独立ZIP和工作流版本
- 评测：`evaluation/`

## 修改规则

### 新增异常类型

1. 先定义业务事实和停止条件；
2. 在评测集中增加正例、负例、信息不足和有效等待案例；
3. 在`ANOMALY_TYPES`中登记；
4. 在`build_anomaly_logic`实现确定性规则；
5. 更新OpenAPI枚举和系统提示词；
6. 运行全量测试后发布。

### 修改工具

- 不直接改工具含义；
- 新增可选字段优先，避免破坏Coze插件；
- 破坏性变更必须发布新operationId或插件大版本；
- 每个工具保持单一职责和结构化输出。

### 修改模型

必须使用同一评测集比较，记录：

- 模型名称与版本；
- Prompt版本；
- 正确率和安全指标；
- P50/P95耗时；
- 费用；
- 回滚模型。

## 故障定位

1. 查看`agent_runs`判断是否完成；
2. 查看`agent_tool_calls`定位失败工具；
3. 查看`event_logs`确认审批和写回；
4. 查看Coze运行日志；
5. 查看Render日志；
6. 验证密钥和Bot ID；
7. 检查持久盘。

## 不要做

- 把所有规则塞进总Prompt；
- 让Agent直接写数据库；
- 为了减少一步调用合并读取和正式写入；
- 未评测就升级模型；
- 在源码或OpenAPI中写真实密钥；
- 把合成物流数据当真实接入能力宣传。
