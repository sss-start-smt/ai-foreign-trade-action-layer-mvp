# V6.1.4.1.1 Railway 部署步骤

1. 备份当前 GitHub 分支。
2. 将完整包内容上传到仓库根目录并覆盖同名文件，或仅覆盖增量包中的 `main.py`、`Dockerfile`、`railway.json`。
3. 不删除 Railway Volume，不清空 `/data/action_layer.db`。
4. 提交：`fix(deploy): make Railway healthcheck independent from database startup`
5. 等待 Railway 自动部署。
6. 部署详情中 Network / Healthcheck 应在数秒内通过。
7. 打开 `/health`，预期 HTTP 200，返回版本 `6.1.4.1.1`。
8. 打开 `/ready`：初始化完成后应返回 HTTP 200 和 `database_ready:true`；若返回 503，查看其中 `startup_error`，并同时查看 Deploy Logs。
9. 打开 `/api/system/storage`，确认 `/data/action_layer.db`、`on_persistent_path:true`、`writable:true`。
10. 打开 `/api/agent/status`，确认 Agent 业务版本仍为 `6.1.4.1`，混合路由配置存在。
