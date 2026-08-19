# FlowOrder Agent V6.0 架构说明

## 设计目标

在V1订单行动工作流之上增加受控型异常诊断Agent，同时保持：

- 原有FT01—FT06可独立维护；
- 确定性规则不交给模型自由发挥；
- 正式业务动作必须人工审批；
- Coze和FlowOrder网站使用同一个已发布Agent；
- 每次工具调用可追踪、可审计、可回滚。

## 架构

```text
Coze主编排Agent
├── FlowOrder Agent插件（OpenAPI）
│   ├── 候选订单筛选
│   ├── 订单诊断上下文
│   ├── 异常候选生成
│   ├── 跨订单排序
│   ├── 任务/沟通草稿
│   └── 人工审批请求
├── FT01—FT06工作流
└── 定时触发器（每天08:30）

FlowOrder FastAPI
├── 原有订单/任务/消息/等待/写回
├── Agent工具API
├── 异常候选与审批
├── Agent运行与工具Trace
├── 巡检报告
└── Coze Agent API客户端

SQLite持久化
├── orders / tasks / source_messages
├── order_dependencies / logistics_events
├── anomaly_candidates / approval_requests
└── agent_runs / agent_tool_calls / daily_inspection_reports
```

## 为什么首版不做多Agent

首版采用“一个主编排Agent＋模块化工具/工作流”，因为：

- 四类异常共用同一数据范围；
- FT01—FT06已经是专业能力模块；
- 多Agent会增加延迟、Token、结论冲突和排障成本；
- 现阶段更应验证动态工具选择和业务价值。

当单Agent工具选择率明显下降、物流需要独立长上下文或不同团队独立维护时，再拆分专业Agent。

## 关键边界

Agent生成的是`ANOMALY_CANDIDATE`，不是正式异常。状态流：

```text
ANOMALY_CANDIDATE
→ PENDING_CONFIRMATION
→ CONFIRMED / REJECTED
→ RESOLVED
```

正式任务、订单修改、联系记录和高风险决定通过`approval_requests`完成。
