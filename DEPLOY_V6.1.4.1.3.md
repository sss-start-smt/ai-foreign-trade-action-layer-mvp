# FlowOrder V6.1.4.1.3 Railway安全启动修复

## 目标

先绑定Railway注入的`PORT`并提供`/health`，再后台加载`main.py`。这样Railway特有的环境变量或导入错误不会再被伪装成5分钟的Healthcheck failure。

## 部署后检查

1. `/health`：必须立即返回200。
2. `/bootstrap-status`：`main_loaded=true`才代表完整业务应用已加载。
3. `/ready`：数据库初始化完成后返回200。

若`/health`仍失败，问题不在Python源码，而在Railway服务的端口、根目录、启动命令或网络配置。
