from __future__ import annotations

import os
import sys

import uvicorn


def resolve_port() -> int:
    raw = (os.getenv("PORT") or "8000").strip()
    try:
        port = int(raw)
    except (TypeError, ValueError):
        print(f"[bootstrap-warning] invalid PORT={raw!r}; falling back to 8000", flush=True)
        port = 8000
    if not 1 <= port <= 65535:
        print(f"[bootstrap-warning] out-of-range PORT={port}; falling back to 8000", flush=True)
        port = 8000
    return port


def main() -> None:
    port = resolve_port()
    print(
        "[bootstrap] starting FlowOrder safe dispatcher "
        f"python={sys.version.split()[0]} host=0.0.0.0 port={port} "
        f"railway_environment={bool(os.getenv('RAILWAY_ENVIRONMENT') or os.getenv('RAILWAY_PROJECT_ID'))}",
        flush=True,
    )
    uvicorn.run(
        "bootstrap_app:app",
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
