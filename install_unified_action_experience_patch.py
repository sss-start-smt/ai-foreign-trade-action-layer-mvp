"""Deprecated compatibility shim. The real UI is now part of static/."""
from __future__ import annotations

def main() -> int:
    print("[unified-action-ui] deprecated: no DOM injection installed; static UI is already integrated")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
