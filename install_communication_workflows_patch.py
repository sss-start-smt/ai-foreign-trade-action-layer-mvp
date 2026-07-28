"""Idempotently register the FT05/FT06 communication patch in an existing FastAPI main.py.

Render build command example:
    pip install -r requirements.txt && python install_excel_import_patch.py && python install_communication_workflows_patch.py
"""
from __future__ import annotations

import ast
import shutil
import sys
from pathlib import Path

MARKER_IMPORT = "from communication_workflows_patch import register_communication_workflows_patch"
MARKER_CALL = "register_communication_workflows_patch(app)"


def find_app_assignment(tree: ast.AST) -> ast.Assign | ast.AnnAssign | None:
    """Return the module-level ``app = FastAPI(...)`` assignment."""
    for node in getattr(tree, "body", []):
        value = None
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            value = node.value
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            value = node.value
            targets = [node.target]
        if value is None or not isinstance(value, ast.Call):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "app" for target in targets):
            continue
        func = value.func
        is_fastapi = isinstance(func, ast.Name) and func.id == "FastAPI"
        is_fastapi = is_fastapi or (isinstance(func, ast.Attribute) and func.attr == "FastAPI")
        if is_fastapi:
            return node
    return None


def install(main_path: Path) -> bool:
    source = main_path.read_text(encoding="utf-8")
    if MARKER_IMPORT in source and MARKER_CALL in source:
        print("[communication-patch] main.py already patched")
        return False

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RuntimeError(f"main.py语法无法解析：{exc}") from exc

    assignment = find_app_assignment(tree)
    if assignment is None or assignment.end_lineno is None:
        raise RuntimeError("未找到模块顶层 app = FastAPI(...)，无法安全自动安装")

    lines = source.splitlines(keepends=True)
    insert_before = assignment.lineno - 1
    insert_after = assignment.end_lineno

    if MARKER_IMPORT not in source:
        lines.insert(insert_before, MARKER_IMPORT + "\n")
        insert_after += 1
    if MARKER_CALL not in source:
        lines.insert(insert_after, MARKER_CALL + "\n")

    patched = "".join(lines)
    ast.parse(patched)

    backup_dir = main_path.parent / ".patch_backups"
    backup_dir.mkdir(exist_ok=True)
    backup = backup_dir / "main.py.before_communication_workflows"
    if not backup.exists():
        shutil.copy2(main_path, backup)

    main_path.write_text(patched, encoding="utf-8")
    print(f"[communication-patch] installed into {main_path}")
    return True


def main() -> int:
    main_path = Path(sys.argv[1] if len(sys.argv) > 1 else "main.py")
    if not main_path.exists():
        print(f"[communication-patch] ERROR: {main_path} not found", file=sys.stderr)
        return 2
    try:
        install(main_path)
    except Exception as exc:
        print(f"[communication-patch] ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
