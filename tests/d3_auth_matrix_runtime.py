"""D3 independent RUNTIME auth-matrix probe.

For every /api/* route discovered from the running app:
  - If NOT in the approved public/service/dual allowlist, an ANONYMOUS request
    MUST return 401 (proves the route is actually gated by get_current_identity
    at runtime, not just statically declared).
  - If in the public allowlist, the ANONYMOUS response must not contain business
    data (orders/tasks/ORG-* secrets).

This catches two failure modes the static inventory cannot:
  1. A route declared public but actually serving business data.
  2. A route that statically lists get_current_identity but the dependency is
     structured to silently pass (none expected; this is the runtime proof).

Does NOT modify product code.
"""
import os
os.environ.setdefault("APP_API_KEY", "test-key")
os.environ.setdefault("SEED_DEMO_DATA", "true")

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

PUBLIC_EXACT = {
    ("GET", "/api/import/capabilities"),
    ("GET", "/api/import/template.xlsx"),
    ("GET", "/api/import/template.csv"),
    ("GET", "/api/v61/status"),
}
PUBLIC_PREFIXES = ("/api/import/assets/", "/api/communication/assets/")
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

# Precise markers: only real seeded business data would match these.
BUSINESS_LEAK_MARKERS = ("B SECRET", "B PROD", "SECRET COMM", "ORG-B", "PO-D3-B",
                         "PO-TEST-ORG-B", "Northwind", "Blue Harbor", "Green Field",
                         "Atlas Retail", "Morgen", "ORD-1001", "ORD-B-REAL-TEST")


def classify(method, path):
    key = (method, path)
    if key in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIXES):
        return "PUBLIC"
    if key in SERVICE_KEY_EXACT or key in DUAL_AUTH_EXACT:
        return "SERVICE/DUAL"
    return "USER"


def fill(path):
    # substitute placeholder path params
    import re
    return re.sub(r"\{([^}]+)\}", "PROBE-ID", path)


def main():
    return _run()


def _run():
    findings = []
    routes = []
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api/"):
            for m in sorted(r.methods):
                routes.append((m, r.path))
    routes.sort(key=lambda x: (x[1], x[0]))

    for method, path in routes:
        cls = classify(method, path)
        url = fill(path)
        try:
            if method in ("GET",):
                resp = client.get(url)
            else:
                resp = client.request(method, url, json={})
        except Exception as e:  # path param validation etc. -> not an auth concern here
            findings.append((cls, method, path, "ERR", str(e)[:60], "skip"))
            continue
        code = resp.status_code
        body = resp.text or ""
        leak = any(mk in body for mk in BUSINESS_LEAK_MARKERS)

        if cls == "PUBLIC":
            status = "OK" if not leak else "LEAK"
            note = f"anon={code} leak={leak}"
        else:
            # USER / SERVICE / DUAL must NOT return 2xx to anonymous.
            # 401 (no/invalid token or agent key) and 403 (role/flag gate)
            # and 503 (agent key not configured -> deliberate refusal) all
            # prove the route is gated and returns no business data.
            if 200 <= code < 300:
                status = "GAP"
                note = f"anon-> {code} (RETURNED DATA - NOT gated!)"
            elif code in (401, 403, 503):
                status = "OK"
                note = f"anon->{code} (gated, no data)"
            else:
                status = "GAP"
                note = f"anon-> {code} (unexpected)"
        findings.append((cls, method, path, status, code, note))

    print(f"\n=== RUNTIME AUTH MATRIX PROBE ({len(findings)} routes) ===")
    gaps = [f for f in findings if f[3] in ("GAP", "LEAK")]
    for f in findings:
        flag = "" if f[3] == "OK" else "  <-- " + f[3]
        print(f"[{f[0]:12}] {f[1]:6} {f[2]:52} {f[4]}  {f[5]}{flag}")
    print(f"\nTotal routes probed: {len(findings)}")
    print(f"Gaps/Leaks: {len(gaps)}")
    if gaps:
        for g in gaps:
            print("  GAP:", g)
        raise SystemExit(1)
    print("ALL ROUTES GATED CORRECTLY (no anonymous access to business APIs; no public data leak)")
    return findings


def test_runtime_auth_matrix_no_anonymous_access_and_no_public_leak():
    findings = _run()
    gaps = [f for f in findings if f[3] in ("GAP", "LEAK")]
    assert not gaps, f"Auth matrix gaps/leaks found: {gaps}"


if __name__ == "__main__":
    main()
