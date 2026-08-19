"""D3 WorkBuddy INDEPENDENT acceptance probe.

Independently re-verifies the 12 mandated scenarios with REAL seeded cross-org
data, covering gaps the four mandated tests do NOT cover:
  - S4/S5/S6 cross-org reads with REAL ORG-B data (mandated tests only check
    anonymous 401, never ORG-A-vs-ORG-B with real data)
  - S10 INVALID token must return 401 (not covered by mandated tests)
  - Public /api/* routes must not leak business data
  - S7 operator cannot do manager-only action

Does NOT modify product code; seeds temp data and cleans up.
"""
import os
os.environ.setdefault("APP_API_KEY", "test-key")
os.environ.setdefault("SEED_DEMO_DATA", "true")
os.environ.setdefault("FLOWORDER_AGENT_API_KEY", "agent-test-key")

import json
import uuid
import pytest
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from main import app, db
from auth import DEMO_TOKEN_MAP
from communication_workflows_patch import _ensure_patch_schema as _ensure_comm_schema
from excel_import_patch import _ensure_patch_schema as _ensure_import_schema

client = TestClient(app)
TOK = {v: k for k, v in DEMO_TOKEN_MAP.items()}
H = lambda uid: {"X-Auth-Token": TOK[uid]}


def _now():
    return (datetime.utcnow() + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def _new(prefix):
    return f"{prefix}-D3-{uuid.uuid4().hex[:8].upper()}"


@pytest.fixture
def seeded():
    s = {}
    with db() as conn:
        _ensure_comm_schema(conn)
        _ensure_import_schema(conn)
        conn.execute("PRAGMA foreign_keys = OFF")
        s["order_b"] = _new("ORD")
        conn.execute(
            "INSERT INTO orders(order_id,order_no,customer_name,product_name,status,owner,organization_id,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (s["order_b"], "PO-D3-B", "B SECRET", "B PROD", "ACTIVE", "OPERATOR-B1", "ORG-B", _now(), _now()),
        )
        s["cand_b"] = _new("CAND")
        conn.execute(
            "INSERT INTO communication_task_candidates(candidate_id,request_id,order_id,order_no,communication_text,result_json,task_candidate_json,run_status,review_status,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (s["cand_b"], s["cand_b"] + "-REQ", s["order_b"], "PO-D3-B", "ORG-B SECRET COMM",
             '{}', '{}', "PENDING", "PENDING", _now()),
        )
        s["batch_b"] = _new("BATCH")
        summary = json.dumps({
            "total_rows": 1, "importable_rows": 1, "error_rows": 0,
            "_auth": {"organization_id": "ORG-B", "created_by": "OPERATOR-B1"},
        }, ensure_ascii=False)
        conn.execute(
            "INSERT INTO order_import_batches(batch_id,source_filename,source_sha256,status,total_rows,importable_rows,error_rows,mapping_json,summary_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (s["batch_b"], "b.csv", "deadbeef", "PREVIEWED", 1, 1, 0, '{}', summary, _now()),
        )
        s["job_b"] = _new("JOB")
        conn.execute(
            "INSERT INTO agent_chat_jobs(job_id,organization_id,current_user_id,current_role,question,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (s["job_b"], "ORG-B", "OPERATOR-B1", "operator", "B secret question", "RUNNING", _now(), _now()),
        )
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
    yield s
    with db() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM communication_task_candidates WHERE candidate_id=?", (s["cand_b"],))
        conn.execute("DELETE FROM order_import_batches WHERE batch_id=?", (s["batch_b"],))
        conn.execute("DELETE FROM agent_chat_jobs WHERE job_id=?", (s["job_b"],))
        conn.execute("DELETE FROM orders WHERE order_id=?", (s["order_b"],))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()


def test_S4_org_a_cannot_view_org_b_communication_history(seeded):
    r = client.get("/api/communication/history", headers=H("OPERATOR-A1"))
    assert r.status_code == 200, f"S4 status {r.status_code}"
    data = r.json()
    leaked = [c for c in data.get("candidates", []) if c.get("order_id") == seeded["order_b"]]
    leaked += [d for d in data.get("drafts", []) if d.get("order_id") == seeded["order_b"]]
    assert not leaked, f"S4 LEAK: ORG-B comm data visible to ORG-A ({len(leaked)})"


def test_S5_org_a_cannot_read_org_b_import_batch(seeded):
    r = client.get(f"/api/import/batches/{seeded['batch_b']}", headers=H("OPERATOR-A1"))
    assert r.status_code in (403, 404), f"S5 FAIL: {r.status_code}"
    # control: ORG-B owner can read own
    rc = client.get(f"/api/import/batches/{seeded['batch_b']}", headers=H("OPERATOR-B1"))
    assert rc.status_code == 200, f"S5 control owner read failed: {rc.status_code}"


def test_S6_org_a_cannot_read_org_b_agent_job(seeded):
    r = client.get(f"/api/agent/chat/jobs/{seeded['job_b']}", headers=H("OPERATOR-A1"))
    assert r.status_code in (403, 404), f"S6 FAIL: {r.status_code}"
    rc = client.get(f"/api/agent/chat/jobs/{seeded['job_b']}", headers=H("OPERATOR-B1"))
    assert rc.status_code == 200, f"S6 control owner read failed: {rc.status_code}"


@pytest.mark.parametrize("ep", [
    "/api/orders", "/api/dashboard", "/api/management", "/api/communication/history",
    "/api/import/batches/XYZ", "/api/agent/chat/jobs/XYZ", "/api/reviews", "/api/settings",
])
def test_S9_anonymous_business_api_requires_401(ep):
    r = client.get(ep)
    assert r.status_code == 401, f"S9 FAIL on {ep}: {r.status_code}"


@pytest.mark.parametrize("ep", [
    "/api/orders", "/api/dashboard", "/api/management", "/api/communication/history",
    "/api/import/batches/XYZ", "/api/agent/chat/jobs/XYZ",
])
def test_S10_invalid_token_requires_401(ep):
    bad = {"X-Auth-Token": "tok-nonexistent-xyz"}
    r = client.get(ep, headers=bad)
    assert r.status_code == 401, f"S10 FAIL on {ep}: {r.status_code}"


def test_S8_query_spoof_does_not_grant_cross_org_visibility():
    r = client.get("/api/orders", headers=H("OPERATOR-A1"),
                   params={"organization_id": "ORG-B", "role": "manager"})
    data = r.json()
    vis_b = [o for o in data.get("items", []) if o.get("organization_id") == "ORG-B"]
    assert not vis_b, f"S8 FAIL: spoofed query allowed ORG-B visibility ({len(vis_b)})"


def test_S7_operator_cannot_reach_manager_action():
    r = client.get("/api/management", headers=H("OPERATOR-A1"))
    assert r.status_code == 403, f"S7 FAIL: {r.status_code}"


@pytest.mark.parametrize("ep", ["/api/import/capabilities", "/api/import/template.csv", "/api/v61/status"])
def test_public_routes_do_not_leak_business_data(ep):
    r = client.get(ep)
    body = r.text
    assert "ORG-B" not in body and "B SECRET" not in body and "SECRET COMM" not in body and "B PROD" not in body, \
        f"PUB LEAK on {ep}"
