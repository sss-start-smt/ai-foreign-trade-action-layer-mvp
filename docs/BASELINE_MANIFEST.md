# FlowOrder V6.1.4.1.3 基线清单 (Baseline Manifest)

> 机器可验证的基线快照，用于交付验收与回归比对。
> 生成时间：2026-08-07T11:48:00

---

## 1. 环境信息 (Environment Info)

| 项目 | 值 |
|---|---|
| Python | 3.10.11 (tags/v3.10.11:7d4cc5a, Apr 5 2023, 00:38:17) [MSC v.1929 64 bit (AMD64)] |
| Platform | Windows-10-10.0.26200-SP0 |
| FastAPI | 0.116.1 |
| uvicorn[standard] | 0.35.0 |
| pydantic | 2.11.7 |
| pytest | 9.0.2 |
| httpx | 0.28.1 |
| cozepy | 0.20.0 |
| DB 路径 | `D:\外贸跟单项目\交付包\source\ai-foreign-trade-action-layer-mvp-main\data\action_layer.db` |
| DB 大小 | 335872 bytes |
| 启动命令 | `uvicorn main:app --host 0.0.0.0 --port 8000` |
| App 版本 | V6.1.4.1.3（来源：`main.py:42` — `app = FastAPI(title="AI外贸跟单行动系统", version="6.1.4.1.3")`） |
| Git Commit | N/A（无 git 仓库） |
| 时间戳 | 2026-08-07T11:48:00 |

---

## 2. 测试结果 (Test Results)

| 项目 | 值 |
|---|---|
| 执行命令 | `python -m pytest -q` |
| 结果 | **66 passed, 2 warnings in 12.14s** |
| Warnings | `DeprecationWarning` for `on_event` — `main.py:1790` 及 `fastapi/applications.py:4495` |

### 测试文件清单（8 个）

| # | 文件 |
|---|---|
| 1 | `tests/test_agent_api.py` |
| 2 | `tests/test_agent_identity_context.py` |
| 3 | `tests/test_agent_router.py` |
| 4 | `tests/test_api.py` |
| 5 | `tests/test_coze_bulk_output_schema.py` |
| 6 | `tests/test_coze_integration.py` |
| 7 | `tests/test_v613_regressions.py` |
| 8 | `tests/test_v61_extensions.py` |

### ⚠️ 重要说明

> **66 tests passing 仅为当前回归基线，不代表生产就绪 (NOT production-ready)：**
>
> - Coze 调用使用 mocks，非真实 API 调用
> - 缺少完整的端到端集成测试
> - 缺少权限控制测试
> - 缺少故障恢复测试

---

## 3. 源文件清单 (Source Files)

### 3.1 核心 Python 文件

| 文件 | 说明 |
|---|---|
| `main.py` | FastAPI 应用、核心路由、DB 初始化 |
| `agent_api.py` | Agent API 路由与工具执行 |
| `agent_router.py` | Agent 路由逻辑 |
| `coze_integration.py` | Coze 工作流客户端 |
| `coze_agent_client.py` | Coze Agent 客户端 |
| `excel_import_patch.py` | Excel 导入功能 |
| `communication_workflows_patch.py` | 沟通工作流 |
| `v61_extensions.py` | V6.1 批量更新、分析功能 |
| `action_rules.py` | 任务决策规则 |
| `analytics.py` | 分析事件 |
| `bootstrap_app.py` | 应用引导 |
| `run_server.py` | 服务器启动器 |
| `schema.sql` | 数据库 Schema 定义 |

### 3.2 前端文件

| 文件 | 说明 |
|---|---|
| `static/index.html` | 前端入口页面 |
| `static/app.js` | 前端逻辑 |
| `static/styles.css` | 样式表 |

### 3.3 测试文件（8 个）

见 [第 2 节 - 测试文件清单](#测试文件清单8-个)。

---

## 4. 路由统计 (Route Count)

| 项目 | 数量 |
|---|---|
| 总路由数 | **96** |
| 框架路由（openapi / docs / redoc） | 4 |
| 业务路由 | **92** |

---

## 5. 数据库 (Database)

| 项目 | 值 |
|---|---|
| 活跃 DB | `data/action_layer.db` |
| 文件大小 | 335872 bytes |
| 表 (Tables) | 24 |
| 索引 (Indexes) | 54 |
| 触发器 (Triggers) | 0 |
| 视图 (Views) | 0 |

---

## 6. 动态 Schema 创建位置 (Dynamic Schema Creation Locations)

共 **10 处**，分布在 **4 个文件**中：

| # | 文件:行号 | DDL 语句 |
|---|---|---|
| 1 | `analytics.py:19` | `CREATE TABLE analytics_events` |
| 2 | `communication_workflows_patch.py:220` | `CREATE TABLE communication_task_candidates` |
| 3 | `communication_workflows_patch.py:242` | `CREATE TABLE communication_drafts` |
| 4 | `communication_workflows_patch.py:273` | `CREATE TABLE communication_workflow_runs` |
| 5 | `communication_workflows_patch.py:289` | `CREATE TABLE communication_events` |
| 6 | `excel_import_patch.py:185` | `CREATE TABLE order_import_batches` |
| 7 | `excel_import_patch.py:198` | `CREATE TABLE order_import_rows` |
| 8 | `excel_import_patch.py:219` | `CREATE TABLE orders` |
| 9 | `v61_extensions.py:93` | `CREATE TABLE bulk_update_batches` |
| 10 | `v61_extensions.py:105` | `CREATE TABLE bulk_update_candidates` |

---

## 7. 验证命令 (Verification Commands)

以下命令可在项目根目录 `d:\外贸跟单项目\交付包\source\ai-foreign-trade-action-layer-mvp-main` 下执行，用于复现本清单中的各项基线事实。

### 7.1 环境验证

```powershell
# Python 版本
python --version

# 依赖包版本
python -c "import fastapi; print(fastapi.__version__)"
python -c "import uvicorn; print(uvicorn.__version__)"
python -c "import pydantic; print(pydantic.__version__)"
python -c "import pytest; print(pytest.__version__)"
python -c "import httpx; print(httpx.__version__)"
python -c "import cozepy; print(cozepy.__version__)"

# 平台信息
python -c "import platform; print(platform.platform())"
```

### 7.2 App 版本验证

```powershell
# 从 main.py 第 42 行提取版本号
python -c "
with open('main.py', encoding='utf-8') as f:
    lines = f.readlines()
print(lines[41].strip())
"
```

### 7.3 测试执行

```powershell
# 运行全部测试
python -m pytest -q

# 预期输出: 66 passed, 2 warnings in ~12s
```

### 7.4 路由数量验证

```powershell
# 启动服务后通过 OpenAPI JSON 统计路由数
python -c "
from main import app
routes = [r for r in app.routes if hasattr(r, 'methods')]
print(f'Total routes: {len(routes)}')
"
```

### 7.5 数据库验证

```powershell
# 数据库文件大小
python -c "
import os
size = os.path.getsize('data/action_layer.db')
print(f'DB size: {size} bytes')
"

# 表、索引、触发器、视图数量
python -c "
import sqlite3
conn = sqlite3.connect('data/action_layer.db')
cur = conn.cursor()
cur.execute(\"SELECT type, count(*) FROM sqlite_master GROUP BY type\")
for row_type, cnt in cur.fetchall():
    print(f'{row_type}: {cnt}')
conn.close()
"
```

### 7.6 动态 Schema 位置验证

```powershell
# 验证 10 处动态 CREATE TABLE 语句
python -c "
import re
targets = [
    ('analytics.py', 19, 'analytics_events'),
    ('communication_workflows_patch.py', 220, 'communication_task_candidates'),
    ('communication_workflows_patch.py', 242, 'communication_drafts'),
    ('communication_workflows_patch.py', 273, 'communication_workflow_runs'),
    ('communication_workflows_patch.py', 289, 'communication_events'),
    ('excel_import_patch.py', 185, 'order_import_batches'),
    ('excel_import_patch.py', 198, 'order_import_rows'),
    ('excel_import_patch.py', 219, 'orders'),
    ('v61_extensions.py', 93, 'bulk_update_batches'),
    ('v61_extensions.py', 105, 'bulk_update_candidates'),
]
for filepath, lineno, table_name in targets:
    with open(filepath, encoding='utf-8') as f:
        lines = f.readlines()
    line = lines[lineno - 1].strip()
    ok = 'CREATE TABLE' in line and table_name in line
    status = 'OK' if ok else 'MISMATCH'
    print(f'[{status}] {filepath}:{lineno} -> {line[:80]}')
"
```

### 7.7 源文件存在性验证

```powershell
# 验证所有核心源文件存在
python -c "
import os
files = [
    'main.py', 'agent_api.py', 'agent_router.py',
    'coze_integration.py', 'coze_agent_client.py',
    'excel_import_patch.py', 'communication_workflows_patch.py',
    'v61_extensions.py', 'action_rules.py', 'analytics.py',
    'bootstrap_app.py', 'run_server.py', 'schema.sql',
    'static/index.html', 'static/app.js', 'static/styles.css',
    'tests/test_agent_api.py', 'tests/test_agent_identity_context.py',
    'tests/test_agent_router.py', 'tests/test_api.py',
    'tests/test_coze_bulk_output_schema.py', 'tests/test_coze_integration.py',
    'tests/test_v613_regressions.py', 'tests/test_v61_extensions.py',
]
for f in files:
    exists = os.path.isfile(f)
    status = 'OK' if exists else 'MISSING'
    print(f'[{status}] {f}')
"
```

---

*本清单由自动化流程生成，可作为后续变更比对的基准。如需更新，请重新执行上述验证命令并刷新对应章节数据。*
