# Changelog V6.0

## Added

- FlowOrder订单异常诊断Agent；
- 4类异常＋信息缺口候选；
- Top 7确定性排序；
- 普通用户/主管权限范围；
- 人工审批和高风险主管审批；
- Agent运行、工具调用和每日巡检Trace；
- Coze OpenAPI插件；
- 网站异常诊断页和Coze对话入口；
- 每日08:30巡检接口；
- 24条Agent评测集；
- FT05/FT06低延迟优化包。

## Changed

- FT05开放任务上下文压缩至最多20条关键字段；
- FT06事实目录最多40条、开放任务最多10条、最近沟通最多8条；
- 版本升级至6.0.0；
- Render默认使用`/var/data/action_layer.db`。

## Security

- Agent插件Header API Key；
- 服务端强制8次工具/60秒预算；
- 每笔订单服务端二次权限检查；
- 审批幂等；
- 高风险动作主管审批；
- 首版禁止自动发送外部消息。
