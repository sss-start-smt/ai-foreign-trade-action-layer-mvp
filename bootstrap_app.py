from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

ASGIApp = Callable[[dict[str, Any], Callable[..., Awaitable[dict[str, Any]]], Callable[..., Awaitable[None]]], Awaitable[None]]
VERSION = "6.1.4.1.3"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _send_json(send: Callable[..., Awaitable[None]], status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class BootstrapDispatcher:
    """Bind the Railway port first, then load the business application.

    Railway healthchecks must be reachable even when an environment-specific
    import or startup error occurs. The actual error is exposed at
    ``/bootstrap-status`` instead of being hidden behind a generic healthcheck
    timeout.
    """

    def __init__(self) -> None:
        self.main_app: Any | None = None
        self.main_module: Any | None = None
        self.load_event: asyncio.Event | None = None
        self.load_task: asyncio.Task[None] | None = None
        self.started_at = _utc_now()
        self.loaded_at: str | None = None
        self.load_started_at: str | None = None
        self.load_duration_ms: int | None = None
        self.load_error: str | None = None
        self.load_traceback: str | None = None

    async def _load_main(self) -> None:
        started = time.perf_counter()
        self.load_started_at = _utc_now()
        try:
            module = await asyncio.to_thread(importlib.import_module, "main")
            app = getattr(module, "app", None)
            if app is None:
                raise RuntimeError("main.py did not expose an ASGI application named 'app'")
            router = getattr(app, "router", None)
            startup = getattr(router, "startup", None)
            if callable(startup):
                await startup()
            self.main_module = module
            self.main_app = app
            self.loaded_at = _utc_now()
            print("[bootstrap] main application loaded", flush=True)
        except Exception as exc:  # Keep bootstrap alive so the error is inspectable.
            self.load_error = f"{type(exc).__name__}: {exc}"
            self.load_traceback = traceback.format_exc()
            print(f"[bootstrap-error] {self.load_error}", flush=True)
            print(self.load_traceback, flush=True)
        finally:
            self.load_duration_ms = int((time.perf_counter() - started) * 1000)
            if self.load_event is not None:
                self.load_event.set()

    def _status(self) -> dict[str, Any]:
        return {
            "status": "ok",
            "version": VERSION,
            "service": "floworder-bootstrap",
            "bootstrap_ready": True,
            "main_loaded": self.main_app is not None,
            "main_load_failed": bool(self.load_error),
            "main_load_error": self.load_error,
            "started_at": self.started_at,
            "load_started_at": self.load_started_at,
            "loaded_at": self.loaded_at,
            "load_duration_ms": self.load_duration_ms,
            "python": sys.version.split()[0],
            "port": os.getenv("PORT"),
            "db_path": os.getenv("DB_PATH"),
            "railway_environment": bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID")),
        }

    async def _handle_lifespan(self, receive: Callable[..., Awaitable[dict[str, Any]]], send: Callable[..., Awaitable[None]]) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                self.load_event = asyncio.Event()
                self.load_task = asyncio.create_task(self._load_main(), name="floworder-main-loader")
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                if self.load_task and not self.load_task.done():
                    self.load_task.cancel()
                if self.main_app is not None:
                    router = getattr(self.main_app, "router", None)
                    shutdown = getattr(router, "shutdown", None)
                    if callable(shutdown):
                        try:
                            await shutdown()
                        except Exception:
                            traceback.print_exc()
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def __call__(self, scope: dict[str, Any], receive: Callable[..., Awaitable[dict[str, Any]]], send: Callable[..., Awaitable[None]]) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._handle_lifespan(receive, send)
            return
        if scope_type != "http":
            if self.main_app is not None:
                await self.main_app(scope, receive, send)
                return
            await send({"type": "websocket.close", "code": 1013})
            return

        path = scope.get("path") or "/"
        if path == "/health":
            await _send_json(send, 200, self._status())
            return
        if path == "/bootstrap-status":
            payload = self._status()
            payload["main_load_traceback"] = self.load_traceback
            await _send_json(send, 200, payload)
            return

        if self.main_app is None and not self.load_error and self.load_event is not None:
            try:
                await asyncio.wait_for(self.load_event.wait(), timeout=30)
            except TimeoutError:
                pass

        if self.main_app is None:
            await _send_json(send, 503, {
                "status": "starting" if not self.load_error else "degraded",
                "version": VERSION,
                "message": "FlowOrder business application is not ready",
                "main_load_error": self.load_error,
                "diagnostic_endpoint": "/bootstrap-status",
            })
            return

        await self.main_app(scope, receive, send)


app = BootstrapDispatcher()
