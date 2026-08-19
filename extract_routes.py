"""提取 FastAPI 路由信息"""
import inspect
import sys
from pathlib import Path

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent))

from main import app

def extract_routes():
    """提取所有路由信息"""
    routes_info = []
    
    for route in app.routes:
        if hasattr(route, 'methods') and hasattr(route, 'path'):
            # 获取 endpoint 函数信息
            endpoint = route.endpoint
            func_name = endpoint.__name__
            
            # 获取源文件和行号
            try:
                source_file = inspect.getfile(endpoint)
                source_lines, line_no = inspect.getsourcelines(endpoint)
                # 转换为相对路径
                try:
                    rel_path = Path(source_file).relative_to(Path(__file__).parent)
                except ValueError:
                    rel_path = source_file
            except (TypeError, OSError):
                rel_path = "unknown"
                line_no = 0
            
            # 检测认证方式
            auth_method = "None"
            dependencies = []
            if hasattr(route, 'dependencies'):
                dependencies = route.dependencies
            
            # 检查依赖中是否有认证相关
            for dep in dependencies:
                dep_str = str(dep)
                if 'auth' in dep_str.lower() or 'tenant' in dep_str.lower():
                    auth_method = "Dependency"
                    break
            
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
            
            # 检查是否是前端调用
            is_frontend = False
            frontend_paths = ['/api/', '/static/']
            if any(route.path.startswith(p) for p in ['/api/orders', '/api/tasks', '/api/import', '/api/waits', '/api/agent']):
                is_frontend = True
            
            # 检查是否仅限 DEV
            is_dev_only = False
            if 'dev' in route.path.lower() or 'debug' in route.path.lower():
                is_dev_only = True
            
            for method in route.methods:
                routes_info.append({
                    'method': method,
                    'path': route.path,
                    'endpoint': func_name,
                    'file': str(rel_path),
                    'line': line_no,
                    'auth': auth_method,
                    'frontend': is_frontend,
                    'dev_only': is_dev_only
                })
    
    return routes_info

if __name__ == '__main__':
    routes = extract_routes()
    
    # 输出为 Markdown 表格
    print("# FastAPI Routes Export")
    print()
    print(f"总计: {len(routes)} 个路由")
    print()
    print("| HTTP Method | Path | Endpoint | File | Line | Auth | Frontend | DEV Only |")
    print("|-------------|------|----------|------|------|------|----------|----------|")
    
    for r in sorted(routes, key=lambda x: (x['path'], x['method'])):
        frontend = "Yes" if r['frontend'] else "No"
        dev_only = "Yes" if r['dev_only'] else "No"
        print(f"| {r['method']} | `{r['path']}` | `{r['endpoint']}` | {r['file']} | {r['line']} | {r['auth']} | {frontend} | {dev_only} |")
