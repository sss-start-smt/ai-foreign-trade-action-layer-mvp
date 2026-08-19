"""Auth inventory gate: every /api route must belong to an explicit auth class."""
from fastapi.routing import APIRoute
from main import app

PUBLIC_EXACT = {
    ("GET", "/api/import/capabilities"),
    ("GET", "/api/import/template.xlsx"),
    ("GET", "/api/import/template.csv"),
    ("GET", "/api/v61/status"),
}
PUBLIC_PREFIXES = (
    "/api/import/assets/",
    "/api/communication/assets/",
)
SERVICE_KEY_EXACT = {
    ("POST", "/api/agent/tools/runs/start"),
    ("POST", "/api/agent/tools/candidate-orders/list"),
    ("POST", "/api/agent/tools/orders/context"),
    ("POST", "/api/agent/tools/anomalies/build"),
    ("POST", "/api/agent/tools/anomalies/rank"),
    ("POST", "/api/agent/tools/task-drafts/create"),
    ("POST", "/api/agent/tools/message-drafts/create"),
    ("POST", "/api/agent/tools/approvals/create"),
    ("POST", "/api/agent/tools/approvals/status"),
    ("POST", "/api/agent/tools/runs/complete"),
    ("POST", "/api/agent/inspection/scheduled"),
    ("POST", "/api/writeback"),
}
DUAL_AUTH_EXACT = {
    ("POST", "/api/agent/tools/bulk-updates/parse"),
    ("POST", "/api/agent/tools/priority-orders/diagnose"),
    ("POST", "/api/reset"),
    ("POST", "/api/demo/seed"),
}


def dependency_names(route: APIRoute) -> set[str]:
    names: set[str] = set()
    stack = list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        call = getattr(dep, "call", None)
        if call:
            names.add(getattr(call, "__name__", str(call)))
        stack.extend(getattr(dep, "dependencies", []) or [])
    return names


def test_all_api_routes_are_explicitly_classified_or_user_authenticated():
    unclassified = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/"):
            continue
        deps = dependency_names(route)
        for method in sorted(route.methods):
            key = (method, route.path)
            if key in PUBLIC_EXACT or route.path.startswith(PUBLIC_PREFIXES):
                continue
            if key in SERVICE_KEY_EXACT or key in DUAL_AUTH_EXACT:
                continue
            if "get_current_identity" in deps:
                continue
            unclassified.append((method, route.path, sorted(deps)))
    assert not unclassified, f"Unclassified /api routes without user auth: {unclassified}"
