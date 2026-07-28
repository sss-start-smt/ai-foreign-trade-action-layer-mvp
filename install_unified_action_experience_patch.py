"""Idempotently install the unified UI/UX and contextual communication experience."""
from __future__ import annotations
import ast
import shutil
import sys
from pathlib import Path

MARKER_IMPORT = "from unified_action_experience_patch import register_unified_action_experience_patch"
MARKER_CALL = "register_unified_action_experience_patch(app)"

def find_app_assignment(tree):
    for node in getattr(tree,"body",[]):
        value=None;targets=[]
        if isinstance(node,ast.Assign): value=node.value;targets=node.targets
        elif isinstance(node,ast.AnnAssign): value=node.value;targets=[node.target]
        if not isinstance(value,ast.Call): continue
        if not any(isinstance(t,ast.Name) and t.id=="app" for t in targets): continue
        f=value.func
        if (isinstance(f,ast.Name) and f.id=="FastAPI") or (isinstance(f,ast.Attribute) and f.attr=="FastAPI"): return node
    return None

def install(main_path:Path)->bool:
    source=main_path.read_text(encoding="utf-8")
    if MARKER_IMPORT in source and MARKER_CALL in source:
        print("[unified-action-ui] main.py already patched");return False
    tree=ast.parse(source);assignment=find_app_assignment(tree)
    if assignment is None or assignment.end_lineno is None: raise RuntimeError("未找到模块顶层 app = FastAPI(...)")
    lines=source.splitlines(keepends=True);before=assignment.lineno-1;after=assignment.end_lineno
    if MARKER_IMPORT not in source: lines.insert(before,MARKER_IMPORT+"\n");after+=1
    if MARKER_CALL not in source: lines.insert(after,MARKER_CALL+"\n")
    patched="".join(lines);ast.parse(patched)
    backup_dir=main_path.parent/".patch_backups";backup_dir.mkdir(exist_ok=True)
    backup=backup_dir/"main.py.before_unified_action_experience"
    if not backup.exists(): shutil.copy2(main_path,backup)
    main_path.write_text(patched,encoding="utf-8");print(f"[unified-action-ui] installed into {main_path}");return True

def main():
    path=Path(sys.argv[1] if len(sys.argv)>1 else "main.py")
    if not path.exists(): print(f"[unified-action-ui] ERROR: {path} not found",file=sys.stderr);return 2
    try: install(path)
    except Exception as e: print(f"[unified-action-ui] ERROR: {e}",file=sys.stderr);return 1
    return 0
if __name__=="__main__": raise SystemExit(main())
