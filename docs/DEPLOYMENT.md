# 部署指南 V6.0

## Render必须配置

```text
DB_PATH=/var/data/action_layer.db
FLOWORDER_AGENT_API_KEY=<随机长密钥>
FLOWORDER_CRON_API_KEY=<随机长密钥，可与Agent Key不同>
COZE_API_TOKEN=<Coze Token>
COZE_AGENT_BOT_ID=<发布后的Agent Bot ID>
COZE_API_BASE=https://api.coze.cn
```

保留原有FT01—FT06 Workflow ID。

## 持久盘

当前线上`/health`显示数据库位于`/tmp/action_layer.db`，数据可能在重启、休眠或重新部署后丢失。

在Render挂载：

- Mount path：`/var/data`
- Size：1GB起
- DB_PATH：`/var/data/action_layer.db`

`render.yaml`已经包含持久盘声明，但现有服务可能需要在Render控制台手动创建或重新关联。

## 部署步骤

1. 创建Git备份分支；
2. 覆盖本补丁文件；
3. 提交并等待Render构建；
4. 访问`/health`；
5. 确认版本`6.0.0`；
6. 确认`on_persistent_path=true`；
7. 访问`/api/agent/status`；
8. 导入并发布Coze插件和Agent；
9. 设置`COZE_AGENT_BOT_ID`后重新部署；
10. 在网站“异常诊断”页联调。

## 回滚

- 回滚Git到部署前Tag；
- 新增Agent表不会影响旧表；
- 不需要删除新表；
- 若需完全回滚，先导出SQLite备份，再删除Agent相关表。
