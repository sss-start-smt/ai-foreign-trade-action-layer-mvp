# AI外贸跟单行动系统 V5.0

这是基于完整仓库重构的真实可操作版本，不再使用DOM注入式UI补丁，也不依赖固定订单案例。

## 核心流程

1. 新建订单或通过Excel/CSV导入订单。
2. 在消息接入页录入客户/工厂原始沟通，调用FT01/FT02生成候选。
3. 在AI确认页人工确认字段、风险和行动候选，再通过FT03写回。
4. FT04根据交期、风险、等待状态和承诺回复时间生成行动队列。
5. 在任务或订单上下文中调用FT05转任务，或调用FT06生成受控沟通草稿。
6. 草稿人工确认、复制并记录触达，任务进入等待；到期未回复后重新进入行动队列。

## 页面

- 今日行动：基于真实任务状态排序，不读取页面文字猜测上下文。
- 全部任务：筛选、查看、转交、升级、完成和沟通执行。
- 订单中心：新建、编辑、导入任意订单，并查看任务、风险、消息和历史事件。
- 消息接入：录入任意客户/工厂消息，不要求固定模板。
- AI确认：审核模型候选，未经确认不修改正式订单。
- 管理看板：工作负载、风险分布、等待超时和工作流状态。
- 设置与连接：选择操作人、检查Coze连接、管理密钥和测试数据。

## UI与可访问性

视觉语言使用暖灰画布、深绿色导航、棕金行动强调和蓝色信息辅助。所有组件使用语义类，不再用全局 `!important` 覆盖旧样式。

关键文本对比度：

- 深绿 `#092923` / 白色：15.52:1
- 深色正文 `#17201D` / 暖灰 `#F4F4F3`：15.13:1
- 次级文字 `#68736E` / 白色：4.92:1
- 棕金 `#B38052` 使用深绿文字：4.53:1

## 本地运行

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn main:app --reload
```

访问 `http://127.0.0.1:8000`。

## Render部署

Build Command：

```bash
pip install -r requirements.txt
```

Start Command：

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

旧Build Command仍可运行，因为三个安装器都已兼容集成版：

```bash
pip install -r requirements.txt && python install_excel_import_patch.py && python install_communication_workflows_patch.py && python install_unified_action_experience_patch.py
```

但新部署建议使用简化命令，避免每次构建重复修改源码。

### 持久化数据库

Render免费实例的本地文件系统可能在重新部署后重置。正式试用请挂载Persistent Disk，并将：

```text
DB_PATH=/var/data/action_layer.db
```

写入环境变量。

### 环境变量

复制 `.env.example` 中的配置。生产环境必须至少配置：

- `COZE_API_TOKEN`
- `COZE_FT01_WORKFLOW_ID` 至 `COZE_FT06_WORKFLOW_ID`
- `APP_API_KEY`
- `COMMUNICATION_ADMIN_KEY`
- `IMPORT_ADMIN_KEY`
- `DB_PATH`

生产默认不会自动加载演示数据，也不会在Coze缺失或失败时伪造成功结果。

## 验证

```bash
python -m py_compile main.py communication_workflows_patch.py excel_import_patch.py
node --check static/app.js
PYTHONPATH=. python -m pytest -q
```

当前交付包的自动测试结果为 `12 passed`。真实Render环境、真实Coze令牌与工作流ID仍需在部署后做一次端到端验收。
