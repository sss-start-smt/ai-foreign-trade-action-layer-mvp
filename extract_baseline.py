"""
FlowOrder V6.1.4.1.3 基线提取脚本
提取：
1. FastAPI 路由元数据
2. SQLite 数据库 schema
3. 环境信息（Python版本、依赖版本等）
"""
import sys
import os
import inspect
import json
import sqlite3
from pathlib import Path
from datetime import datetime

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

def extract_routes():
    """提取 FastAPI 路由信息"""
    from main import app
    
    routes_info = []
    
    for route in app.routes:
        if hasattr(route, 'methods') and hasattr(route, 'path'):
            # 跳过静态文件路由
            if route.path.startswith('/static'):
                continue
                
            # 获取 endpoint 函数信息
            endpoint = route.endpoint
            func_name = endpoint.__name__
            
            # 获取源文件和行号
            try:
                source_file = inspect.getfile(endpoint)
                source_lines, line_no = inspect.getsourcelines(endpoint)
                # 转换为相对路径
                try:
                    rel_path = str(Path(source_file).relative_to(Path(__file__).parent))
                except ValueError:
                    rel_path = source_file
            except (TypeError, OSError):
                rel_path = "unknown"
                line_no = 0
            
            # 检测认证方式
            auth_method = "None"
            
            # 检查函数签名中的参数
            try:
                sig = inspect.signature(endpoint)
                params = list(sig.parameters.keys())
                if 'current_tenant' in params or 'tenant_id' in params:
                    auth_method = "TenantHeader"
                elif 'current_user' in params:
                    auth_method = "UserAuth"
            except (ValueError, TypeError):
                pass
            
            # 检查是否是前端调用（基于路径和函数名）
            is_frontend = False
            frontend_keywords = ['orders', 'tasks', 'import', 'waits', 'agent', 'risk', 'approval']
            if any(kw in route.path.lower() for kw in frontend_keywords):
                is_frontend = True
            
            # 检查是否仅限 DEV
            is_dev_only = False
            if 'dev' in route.path.lower() or 'debug' in route.path.lower() or 'test' in route.path.lower():
                is_dev_only = True
            
            for method in route.methods:
                if method in ['GET', 'POST', 'PUT', 'DELETE', 'PATCH']:
                    routes_info.append({
                        'method': method,
                        'path': route.path,
                        'endpoint': func_name,
                        'file': rel_path,
                        'line': line_no,
                        'auth': auth_method,
                        'frontend': is_frontend,
                        'dev_only': is_dev_only
                    })
    
    return routes_info

def extract_sqlite_schema():
    """提取 SQLite 数据库 schema"""
    # 尝试多个可能的数据库文件
    db_candidates = [
        Path(__file__).parent / "data" / "action_layer.db",
        Path(__file__).parent / "data" / "floworder.db",
    ]
    
    db_path = None
    for candidate in db_candidates:
        if candidate.exists() and candidate.stat().st_size > 0:
            db_path = candidate
            break
    
    if not db_path or not db_path.exists() or db_path.stat().st_size == 0:
        # 尝试创建数据库
        from main import init_db
        init_db()
        # 再次检查
        for candidate in db_candidates:
            if candidate.exists() and candidate.stat().st_size > 0:
                db_path = candidate
                break
    
    if not db_path or not db_path.exists():
        return {'tables': [], 'indexes': [], 'triggers': [], 'views': []}
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    schema_info = {
        'tables': [],
        'indexes': [],
        'triggers': [],
        'views': []
    }
    
    # 获取所有表
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = cursor.fetchall()
    
    for table_name in tables:
        table_name = table_name[0]
        
        # 获取表结构
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = cursor.fetchall()
        
        # 获取外键
        cursor.execute(f"PRAGMA foreign_key_list({table_name})")
        foreign_keys = cursor.fetchall()
        
        # 获取索引
        cursor.execute(f"PRAGMA index_list({table_name})")
        indexes = cursor.fetchall()
        
        table_info = {
            'name': table_name,
            'columns': [],
            'foreign_keys': [],
            'indexes': []
        }
        
        for col in columns:
            col_info = {
                'cid': col[0],
                'name': col[1],
                'type': col[2],
                'notnull': col[3],
                'default_value': col[4],
                'pk': col[5]
            }
            table_info['columns'].append(col_info)
        
        for fk in foreign_keys:
            fk_info = {
                'id': fk[0],
                'seq': fk[1],
                'table': fk[2],
                'from': fk[3],
                'to': fk[4],
                'on_update': fk[5],
                'on_delete': fk[6]
            }
            table_info['foreign_keys'].append(fk_info)
        
        for idx in indexes:
            idx_name = idx[1]
            cursor.execute(f"PRAGMA index_info({idx_name})")
            idx_columns = cursor.fetchall()
            
            idx_info = {
                'name': idx_name,
                'unique': idx[2],
                'columns': [c[2] for c in idx_columns]
            }
            table_info['indexes'].append(idx_info)
            schema_info['indexes'].append(idx_info)
        
        schema_info['tables'].append(table_info)
    
    # 获取所有触发器
    cursor.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='trigger'")
    triggers = cursor.fetchall()
    for trigger in triggers:
        trigger_info = {
            'name': trigger[0],
            'table': trigger[1],
            'sql': trigger[2]
        }
        schema_info['triggers'].append(trigger_info)
    
    # 获取所有视图
    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='view'")
    views = cursor.fetchall()
    for view in views:
        view_info = {
            'name': view[0],
            'sql': view[1]
        }
        schema_info['views'].append(view_info)
    
    conn.close()
    return schema_info

def extract_environment_info():
    """提取环境信息"""
    import platform
    import subprocess
    
    # Python 版本
    python_version = sys.version
    
    # 获取依赖版本
    requirements = {}
    req_file = Path(__file__).parent / "requirements.txt"
    if req_file.exists():
        with open(req_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    # 解析 package==version 或 package>=version
                    if '==' in line:
                        pkg, ver = line.split('==', 1)
                        requirements[pkg.strip()] = ver.strip()
                    elif '>=' in line:
                        pkg, ver = line.split('>=', 1)
                        requirements[pkg.strip()] = f">={ver.strip()}"
                    else:
                        requirements[line] = "latest"
    
    # 数据库路径
    db_path = Path(__file__).parent / "data" / "floworder.db"
    
    # 启动命令
    startup_command = "uvicorn main:app --host 0.0.0.0 --port 8000"
    
    # 应用版本
    app_version = "V6.1.4.1.3"
    
    # Git commit (如果有的话)
    git_commit = "N/A"
    try:
        result = subprocess.run(['git', 'rev-parse', 'HEAD'], 
                              capture_output=True, text=True, 
                              cwd=Path(__file__).parent)
        if result.returncode == 0:
            git_commit = result.stdout.strip()[:8]
    except:
        pass
    
    return {
        'python_version': python_version,
        'platform': platform.platform(),
        'requirements': requirements,
        'db_path': str(db_path),
        'startup_command': startup_command,
        'app_version': app_version,
        'git_commit': git_commit,
        'timestamp': datetime.now().isoformat()
    }

def find_dynamic_schema_creation():
    """查找动态创建表的位置"""
    dynamic_creations = []
    
    # 搜索 CREATE TABLE 语句
    for py_file in Path(__file__).parent.glob('*.py'):
        with open(py_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            lines = content.split('\n')
            
            for i, line in enumerate(lines, 1):
                if 'CREATE TABLE' in line.upper() or 'create_table' in line.lower():
                    dynamic_creations.append({
                        'file': str(py_file.relative_to(Path(__file__).parent)),
                        'line': i,
                        'code': line.strip()
                    })
    
    return dynamic_creations

if __name__ == '__main__':
    print("提取 FastAPI 路由...")
    routes = extract_routes()
    
    print(f"提取到 {len(routes)} 个路由")
    
    # 保存路由信息
    with open('routes_export.json', 'w', encoding='utf-8') as f:
        json.dump(routes, f, indent=2, ensure_ascii=False)
    
    print("提取 SQLite schema...")
    schema = extract_sqlite_schema()
    
    print(f"提取到 {len(schema['tables'])} 个表")
    
    # 保存 schema 信息
    with open('sqlite_schema.json', 'w', encoding='utf-8') as f:
        json.dump(schema, f, indent=2, ensure_ascii=False)
    
    print("提取环境信息...")
    env_info = extract_environment_info()
    
    # 保存环境信息
    with open('environment_info.json', 'w', encoding='utf-8') as f:
        json.dump(env_info, f, indent=2, ensure_ascii=False)
    
    print("查找动态 schema 创建位置...")
    dynamic_schema = find_dynamic_schema_creation()
    
    # 保存动态 schema 信息
    with open('dynamic_schema.json', 'w', encoding='utf-8') as f:
        json.dump(dynamic_schema, f, indent=2, ensure_ascii=False)
    
    print(f"找到 {len(dynamic_schema)} 处动态 schema 创建")
    
    print("\n提取完成！")
    print(f"- routes_export.json: {len(routes)} 个路由")
    print(f"- sqlite_schema.json: {len(schema['tables'])} 个表")
    print(f"- environment_info.json: 环境信息")
    print(f"- dynamic_schema.json: {len(dynamic_schema)} 处动态创建")
