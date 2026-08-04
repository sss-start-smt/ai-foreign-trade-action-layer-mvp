from __future__ import annotations

import os
import sys

import uvicorn


def resolve_port() -> int:
    raw = (os.getenv("PORT") or "8000").strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"PORT must be an integer, got {raw!r}") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError(f"PORT out of range: {port}")
    return port


def main() -> None:
    port = resolve_port()
    print(
        "[bootstrap] starting FlowOrder "
        f"python={sys.version.split()[0]} host=0.0.0.0 port={port} "
        f"railway_environment={bool(os.getenv('RAILWAY_ENVIRONMENT'))}",
        flush=True,
    )
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    main()
