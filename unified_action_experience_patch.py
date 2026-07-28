"""Deprecated compatibility module.

The former V3 implementation injected CSS and JavaScript into rendered HTML and
tried to infer order/task context from visible text. Version 5 integrates UI and
workflow actions directly in the main SPA, so this module intentionally does
nothing. It remains only to avoid import failures in older deployments.
"""

from __future__ import annotations

from fastapi import FastAPI


def register_unified_action_experience_patch(app: FastAPI) -> None:
    """No-op compatibility hook; the unified experience is now native."""
    return None
