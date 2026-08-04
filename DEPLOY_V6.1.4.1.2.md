# FlowOrder V6.1.4.1.2 Railway bootstrap diagnostic

本补丁只消除 Railway 启动命令和 PORT 展开歧义，不修改业务逻辑、数据库或前端。

## 覆盖文件

- `run_server.py`（新增）
- `Dockerfile`
- `railway.json`

## Railway 控制台必须同步检查

1. Service → Settings → Deploy → Custom Start Command：清空自定义值，让仓库中的 `railway.json` 生效。
2. Variables：删除手动创建的 `PORT` 变量；Railway 会自动注入。
3. Healthcheck Path：`/health`。
4. Public Networking 的 Target Port：使用自动值，不要固定为旧端口。
5. Redeploy 后查看 Deploy Logs。正常必须出现：

```text
[bootstrap] starting FlowOrder ... host=0.0.0.0 port=<Railway注入端口>
Uvicorn running on http://0.0.0.0:<同一端口>
```

若仍失败，将 Deploy Logs 中从 `Starting Container` 到第一个 traceback/exit 的完整内容提供出来。
