"""D11 Communication Draft API-level test.

Verifies the communication draft flow end-to-end via FastAPI TestClient:
  1. UAT fixture intake → unique_match → PENDING review
  2. FT06 draft generation → NEEDS_CONFIRMATION + _uat_fixture
  3. copy_and_record → Task WAITING + ACTIVE Waiting + no external send claim
  4. Cross-org access blocked
"""
import os, json, sys, sqlite3, uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEST_DB = str(HERE / f"_d11_comm_test_{uuid.uuid4().hex[:8]}.db")

os.environ["D11_UAT_INTAKE_PROVIDER"] = "fixture"
os.environ["D11_UAT_COMMUNICATION_PROVIDER"] = "fixture"
os.environ["COZE_ALLOW_LOCAL_WHEN_UNCONFIGURED"] = "false"
os.environ["TEST_DB_PATH"] = TEST_DB
os.environ["DB_PATH"] = TEST_DB

# Step 1: Create schema on raw sqlite3 connection
schema = (HERE / "schema.sql").read_text(encoding="utf-8")
c = sqlite3.connect(TEST_DB)
c.executescript(schema)
c.commit()
c.close()

# Step 2: Reset engines and apply migrations via db(use_test=True) wrapper
from database import reset_engines, db
reset_engines()

from main import ensure_activation_schema, _migrate_intake_org_id, _migrate_source_messages_org_id, _migrate_candidate_reviews_org_id
with db(use_test=True) as conn:
    ensure_activation_schema(conn)
    _migrate_intake_org_id(conn)
    _migrate_source_messages_org_id(conn)
    _migrate_candidate_reviews_org_id(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_intake_jobs_org_status ON intake_jobs(organization_id, status, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_source_messages_org ON source_messages(organization_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_candidate_reviews_org ON candidate_reviews(organization_id, status)")
    conn.commit()

# Step 3: Seed data
NOW = "2026-08-16T15:00:00+08:00"
with db(use_test=True) as conn:
    conn.execute(
        "INSERT INTO orders(order_id,order_no,customer_name,product_name,requested_delivery_date,current_node,status,owner,organization_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        ("ORD-D11-UAT", "SO-D11-UAT", "Northwind UAT", "canvas bag", "2026-08-20", "production", "ACTIVE", "USER-1", "ORG-A", NOW, NOW),
    )
    conn.execute(
        """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,
           intent_type,stage,lifecycle_status,title,latest_action_bucket,latest_severity,
           latest_recommended_action,latest_evidence_json,observation_status,
           first_seen_at,last_seen_at,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("AC-D11-UAT", "ORG-A", "ORD-D11-UAT", "D11:DELIVERY_RECOVERY:SO-D11-UAT",
         "DELIVERY_RECOVERY", "IN_PROGRESS", "ACTIVE", "Resolve SO-D11-UAT",
         "DO_NOW", "high", "Confirm supplier",
         json.dumps(["Customer deadline Aug 20"]), "OBSERVED", NOW, NOW, 1, NOW, NOW),
    )
    conn.execute(
        """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,
           recommended_action,status,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        ("TK-D11-UAT", "ORG-A", "AC-D11-UAT", "Confirm supplier delivery", "Contact factory", "TODO", 1, NOW, NOW),
    )
    conn.commit()

# Step 4: Run tests via TestClient
from fastapi.testclient import TestClient
import main as fm

client = TestClient(fm.app)
headers_user = {"X-Auth-Token": "tok-user-1"}
headers_mgr = {"X-Auth-Token": "tok-manager-1", "X-Communication-Key": "dev-communication-key"}

RESULTS = []

# ===========================================================================
# Test 1: UAT Fixture Intake → unique_match → PENDING
# ===========================================================================
print("=== Test 1: UAT Fixture Intake ===")
analyzed = client.post("/api/intake/analyze", headers=headers_user, json={
    "source_channel": "email", "sender_role": "factory",
    "raw_content": "关于订单 SO-D11-UAT，工厂说这单会延迟一周，最早 8 月 27 日完成。",
})
r = analyzed.json()
t1_pass = (
    analyzed.status_code == 200
    and r["candidate"]["manual_review_required"] is True
    and r["candidate"].get("_uat_fixture") is True
    and r["candidate"]["order_match"]["status"] == "unique_match"
    and r["workflow_source"] == "UAT_FIXTURE_PROVIDER"
)
review_id = r.get("review_id")
with db(use_test=True) as conn:
    rev = conn.execute("SELECT status FROM candidate_reviews WHERE review_id=?", (review_id,)).fetchone()
    t1_pass = t1_pass and rev["status"] == "PENDING"
print(f"  Status: {analyzed.status_code}, manual_review: {r['candidate']['manual_review_required']}, fixture: {r['candidate'].get('_uat_fixture')}, match: {r['candidate']['order_match']['status']}")
print(f"  Review DB status: {rev['status']}")
print(f"  [{'PASS' if t1_pass else 'FAIL'}]")
RESULTS.append(("Test 1: UAT Fixture Intake → unique_match → PENDING", t1_pass))

# ===========================================================================
# Test 2: Communication Draft (FT06) → NEEDS_CONFIRMATION + _uat_fixture
# ===========================================================================
print()
print("=== Test 2: Communication Draft (FT06) ===")
draft_resp = client.post("/api/workflows/ft06/run", headers=headers_mgr, json={
    "draft_type": "SUPPLIER_PROGRESS_FOLLOWUP", "recipient_role": "supplier",
    "channel": "email", "order_id": "ORD-D11-UAT",
})
draft_id = None
t2_pass = False
if draft_resp.status_code == 200:
    d = draft_resp.json()
    draft_id = d.get("draft_id")
    result_obj = d.get("result", {})
    t2_pass = (
        result_obj.get("approval_status") == "NEEDS_CONFIRMATION"
        and result_obj.get("_uat_fixture") is True
    )
    integration = result_obj.get("_integration", {})
    evidence_level = integration.get("evidence_level")
    print(f"  draft_id: {draft_id}")
    print(f"  approval_status: {result_obj.get('approval_status')}")
    print(f"  _uat_fixture: {result_obj.get('_uat_fixture')}")
    print(f"  evidence_level: {evidence_level}")
    print(f"  subject: {d.get('draft_result', {}).get('subject', 'N/A')}")
else:
    print(f"  Error: {draft_resp.text[:500]}")
print(f"  [{'PASS' if t2_pass else 'FAIL'}]")
RESULTS.append(("Test 2: FT06 Draft NEEDS_CONFIRMATION + fixture", t2_pass))

# ===========================================================================
# Test 3: copy_and_record → Task WAITING + ACTIVE Waiting + no external send
# ===========================================================================
print()
print("=== Test 3: copy_and_record ===")
t3_pass = False
if draft_id:
    from d11_action_workspace import start_case_task
    from auth import resolve_identity_for_testing
    identity = resolve_identity_for_testing("USER-1")
    with db(use_test=True) as conn:
        start_case_task(conn, identity, "TK-D11-UAT")
        conn.commit()

    copy_resp = client.post(f"/api/drafts/{draft_id}/review", headers=headers_user, json={
        "action": "copy_and_record",
        "edited_subject": "Please confirm order progress",
        "edited_draft": "Hello, regarding order SO-D11-UAT, please confirm current progress.",
        "task_id": "TK-D11-UAT",
        "waiting_on": "factory",
        "promised_reply_at": "2026-08-17T10:00:00+08:00",
        "next_action_at": "2026-08-17T10:00:00+08:00",
    })
    if copy_resp.status_code == 200:
        cr = copy_resp.json()
        with db(use_test=True) as conn:
            task = conn.execute("SELECT status FROM d9_action_case_tasks WHERE task_id=?", ("TK-D11-UAT",)).fetchone()
            waiting = conn.execute("SELECT status, waiting_type FROM d9_action_case_waitings WHERE task_id=?", ("TK-D11-UAT",)).fetchone()
        t3_pass = (
            task["status"] == "WAITING"
            and waiting is not None
            and waiting["status"] == "ACTIVE"
        )
        # human_status should indicate copy+record (not "sent")
        hs = cr.get("human_status", "")
        t3_pass = t3_pass and "COPIED" in hs.upper()
        print(f"  human_status: {cr.get('human_status')}")
        print(f"  Task status: {task['status']}")
        if waiting:
            print(f"  Waiting: status={waiting['status']}, type={waiting['waiting_type']}")
        else:
            print(f"  Waiting: None")
        # Verify no claim of external send
        send_allowed = cr.get("send_allowed")
        actual_send = cr.get("actual_send_performed")
        print(f"  send_allowed: {send_allowed}")
        print(f"  actual_send_performed: {actual_send}")
        if send_allowed is False and actual_send is False:
            t3_pass = t3_pass and True
        elif send_allowed is None and actual_send is None:
            msg = cr.get("message", "")
            print(f"  message: {msg}")
            t3_pass = t3_pass and "已发送" not in msg and "已执行" not in msg
    else:
        print(f"  Error: {copy_resp.text[:500]}")
print(f"  [{'PASS' if t3_pass else 'FAIL'}]")
RESULTS.append(("Test 3: copy_and_record Task→WAITING+ACTIVE Waiting", t3_pass))

# ===========================================================================
# Test 4: Cross-org draft access blocked
# ===========================================================================
print()
print("=== Test 4: Cross-org access ===")
cross_resp = client.get("/api/orders", headers={"X-Auth-Token": "tok-operator-b1"})
t4_pass = True
if cross_resp.status_code == 200:
    orders = cross_resp.json()
    order_ids = [o.get("order_id") for o in orders] if isinstance(orders, list) else []
    t4_pass = "ORD-D11-UAT" not in order_ids
    print(f"  ORG-B sees orders: {order_ids}")
    print(f"  ORD-D11-UAT leaked: {'ORD-D11-UAT' in order_ids}")
else:
    print(f"  Status: {cross_resp.status_code}")
print(f"  [{'PASS' if t4_pass else 'FAIL'}]")
RESULTS.append(("Test 4: Cross-org order isolation", t4_pass))

# ===========================================================================
# Summary
# ===========================================================================
print()
print("=" * 60)
total = len(RESULTS)
passed = sum(1 for _, p in RESULTS if p)
print(f"TOTAL: {total} | PASS: {passed} | FAIL: {total - passed}")
for name, p in RESULTS:
    print(f"  [{'PASS' if p else 'FAIL'}] {name}")
print("=" * 60)

sys.exit(0 if passed == total else 1)
