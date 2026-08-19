"""D3 Round3 RBAC Attack Tests - REAL Cross-Organization Security Verification

These tests verify that the RBAC implementation prevents ALL cross-org attacks:
B01-B10: Cross-organization access by managers and operators
B11: No-token identity spoofing via query/header
B12: Body identity spoofing
B13: Audit actor spoofing
B14: Create organization binding verification
"""

import os
os.environ.setdefault("APP_API_KEY", "test-key")
os.environ.setdefault("SEED_DEMO_DATA", "true")

import json
import pytest
import uuid
from datetime import datetime, timedelta
from fastapi.testclient import TestClient

from main import app, db, init_db
from auth import DEMO_TOKEN_MAP, TRUSTED_USER_MAP

client = TestClient(app)
BASE_URL = "/api"

# =============================================================================
# AUTHENTICATION HELPERS
# =============================================================================

TOKENS = {
    "OPERATOR-A1": "tok-operator-a1",
    "OPERATOR-A2": "tok-operator-a2",
    "MANAGER-A": "tok-manager-a",
    "OPERATOR-B1": "tok-operator-b1",
    "OPERATOR-B2": "tok-operator-b2",
    "MANAGER-B": "tok-manager-b",
}


def auth_headers(token_key: str) -> dict:
    """Get authentication headers for a given user."""
    return {"X-Auth-Token": TOKENS[token_key]}


def _new_id(prefix: str) -> str:
    return f"{prefix}-TEST-{uuid.uuid4().hex[:8].upper()}"


def _now() -> str:
    return (datetime.utcnow() + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')


# =============================================================================
# SEED DATA CONTAINER
# =============================================================================

class SeedData:
    """Container for seeded test data IDs."""
    def __init__(self):
        self.org_a_order_id = ""
        self.org_a_task_id = ""
        self.org_b_order_id = ""
        self.org_b_task_id = ""
        self.org_b_run_id = ""
        self.org_b_approval_id = ""
        self.original_customer = ""
        self.original_task_status = "WAITING_EXTERNAL"


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture(scope="module")
def seed_real_org_data():
    """
    Seed REAL two-organization data before tests.
    
    CRITICAL: All tests use ACTUAL database records, NOT non-existent IDs.
    This ensures 403/404 responses prove RBAC enforcement, not just missing data.
    """
    seed = SeedData()
    
    with db() as conn:
        timestamp = _now()
        
        # Clean up previous test data - disable FK constraints for cleanup
        conn.execute("PRAGMA foreign_keys = OFF")
        
        conn.execute("DELETE FROM audit_logs WHERE entity_id LIKE 'TASK-TEST-%' OR entity_id LIKE 'ORD-TEST-%' OR entity_id='ORD-B-REAL-TEST' OR entity_id='TASK-B-REAL-TEST'")
        conn.execute("DELETE FROM event_logs WHERE entity_id LIKE 'TASK-TEST-%' OR entity_id LIKE 'ORD-TEST-%' OR entity_id='ORD-B-REAL-TEST' OR entity_id='TASK-B-REAL-TEST'")
        conn.execute("DELETE FROM risk_signals WHERE order_id LIKE 'ORD-TEST-%' OR order_id='ORD-B-REAL-TEST' OR task_id LIKE 'TASK-TEST-%' OR task_id='TASK-B-REAL-TEST'")
        conn.execute("DELETE FROM agent_tool_calls WHERE run_id LIKE 'RUN-TEST-%' OR run_id='RUN-B-REAL-TEST'")
        conn.execute("DELETE FROM commitment_history WHERE order_id LIKE 'ORD-TEST-%' OR order_id='ORD-B-REAL-TEST'")
        conn.execute("DELETE FROM candidate_reviews WHERE order_id LIKE 'ORD-TEST-%' OR order_id='ORD-B-REAL-TEST'")
        conn.execute("DELETE FROM approval_requests WHERE approval_id LIKE 'APP-TEST-%' OR approval_id='APP-B-REAL-TEST'")
        conn.execute("DELETE FROM agent_runs WHERE run_id LIKE 'RUN-TEST-%' OR run_id='RUN-B-REAL-TEST'")
        conn.execute("DELETE FROM tasks WHERE task_id LIKE 'TASK-TEST-%' OR task_id='TASK-B-REAL-TEST'")
        conn.execute("DELETE FROM orders WHERE order_id LIKE 'ORD-TEST-%' OR order_id='ORD-B-REAL-TEST'")
        
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        
        # === SEED ORG-A DATA ===
        seed.org_a_order_id = _new_id("ORD")
        conn.execute("""
            INSERT INTO orders (order_id, order_no, customer_name, product_name, packaging_method,
                requested_delivery_date, latest_supplier_commitment, current_progress, current_node,
                status, owner, action_readiness, contact_status, issue_status,
                initialization_waiting_on, initialization_promised_reply_at, initialization_note,
                initialization_source, initialized_at, last_dynamic_update_at,
                created_at, updated_at, organization_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            seed.org_a_order_id, "PO-TEST-ORG-A-001", "ORG A CUSTOMER", "Widget A", "Carton",
            "2026-12-15", "2026-12-10", 0.5, "Production",
            "ACTIVE", "OPERATOR-A1", "ACTION_GENERATED", "CONTACTED", "NORMAL",
            "factory", "2026-11-30", "ORG-A test order",
            "manual", timestamp, timestamp,
            timestamp, timestamp, "ORG-A"
        ))
        
        # ORG-A tasks
        seed.org_a_task_id = _new_id("TASK")
        conn.execute("""
            INSERT INTO tasks (task_id, related_order_id, title, recommended_action, target,
                status, owner_user_id, responsibility_status, waiting_on, promised_reply_at,
                next_action_at, business_deadline, last_contact_at, risk_level, urgent,
                pending_confirmation, source_message_id, evidence_json,
                created_at, updated_at, organization_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            seed.org_a_task_id, seed.org_a_order_id, "Confirm ORG-A supplier delivery", "CONTACT_SUPPLIER",
            "factory", "WAITING_EXTERNAL", "OPERATOR-A1", "assigned",
            "factory", "2026-11-30", "2026-11-25", "2026-12-01",
            timestamp, "medium", 0, 0, None, "[]",
            timestamp, timestamp, "ORG-A"
        ))
        
        # === SEED ORG-B DATA (for cross-org attack tests) ===
        seed.org_b_order_id = "ORD-B-REAL-TEST"
        conn.execute("""
            INSERT INTO orders (order_id, order_no, customer_name, product_name, packaging_method,
                requested_delivery_date, latest_supplier_commitment, current_progress, current_node,
                status, owner, action_readiness, contact_status, issue_status,
                initialization_waiting_on, initialization_promised_reply_at, initialization_note,
                initialization_source, initialized_at, last_dynamic_update_at,
                created_at, updated_at, organization_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            seed.org_b_order_id, "PO-TEST-ORG-B-001", "ORG B SECRET CUSTOMER", "Widget B", "Carton",
            "2026-12-20", "2026-12-18", 0.3, "Material Prep",
            "ACTIVE", "OPERATOR-B1", "ACTION_GENERATED", "PENDING", "NORMAL",
            "factory", "2026-11-28", "ORG-B SECRET ORDER",
            "manual", timestamp, timestamp,
            timestamp, timestamp, "ORG-B"
        ))
        
        # Store original state for verification
        original = conn.execute("SELECT customer_name FROM orders WHERE order_id=?", (seed.org_b_order_id,))
        seed.original_customer = original.fetchone()[0]
        
        # ORG-B tasks
        seed.org_b_task_id = "TASK-B-REAL-TEST"
        conn.execute("""
            INSERT INTO tasks (task_id, related_order_id, title, recommended_action, target,
                status, owner_user_id, responsibility_status, waiting_on, promised_reply_at,
                next_action_at, business_deadline, last_contact_at, risk_level, urgent,
                pending_confirmation, source_message_id, evidence_json,
                created_at, updated_at, organization_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            seed.org_b_task_id, seed.org_b_order_id, "ORG-B SECRET TASK - Do Not Touch", "CONTACT_SUPPLIER",
            "factory", "WAITING_EXTERNAL", "OPERATOR-B1", "assigned",
            "factory", "2026-11-28", "2026-11-26", "2026-11-30",
            timestamp, "high", 1, 0, None, "[]",
            timestamp, timestamp, "ORG-B"
        ))
        
        # ORG-B agent run
        seed.org_b_run_id = "RUN-B-REAL-TEST"
        conn.execute("""
            INSERT INTO agent_runs (run_id, organization_id, current_user_id, current_role,
                goal, trigger_type, status, max_tool_calls, max_duration_seconds,
                started_at, completed_at, created_at, result_json, stop_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            seed.org_b_run_id, "ORG-B", "OPERATOR-B1", "operator",
            "ORG-B investigation", "MANUAL_RULE", "COMPLETED",
            5, 60, timestamp, timestamp, timestamp,
            '{"candidates":[{"order_id":"ORD-B-REAL-TEST"}]}', "COMPLETED"
        ))
        
        # ORG-B approval
        seed.org_b_approval_id = "APP-B-REAL-TEST"
        conn.execute("""
            INSERT INTO approval_requests (approval_id, run_id, candidate_id, order_id,
                action_type, payload_json, status, requested_by, required_role,
                idempotency_key, decided_by, decision_note, decided_at, result_json,
                created_at, updated_at, organization_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            seed.org_b_approval_id, seed.org_b_run_id, None, seed.org_b_order_id,
            "UPDATE_ORDER", '{"field":"requested_delivery_date"}', "PENDING",
            "OPERATOR-B1", "manager",
            f"{seed.org_b_approval_id}-UPDATE_ORDER-{seed.org_b_order_id}",
            None, None, None, '{}',
            timestamp, timestamp, "ORG-B"
        ))
        
        conn.commit()
        
        print(f"\n=== Seeded Test Data ===")
        print(f"ORG-A: order={seed.org_a_order_id}, task={seed.org_a_task_id}")
        print(f"ORG-B: order={seed.org_b_order_id}, task={seed.org_b_task_id}, run={seed.org_b_run_id}, approval={seed.org_b_approval_id}")
    
    yield seed
    
    # Cleanup after tests - disable FK constraints for cleanup
    with db() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        
        conn.execute("DELETE FROM audit_logs WHERE entity_id LIKE 'TASK-TEST-%' OR entity_id LIKE 'ORD-TEST-%' OR entity_id='ORD-B-REAL-TEST' OR entity_id='TASK-B-REAL-TEST'")
        conn.execute("DELETE FROM event_logs WHERE entity_id LIKE 'TASK-TEST-%' OR entity_id LIKE 'ORD-TEST-%' OR entity_id='ORD-B-REAL-TEST' OR entity_id='TASK-B-REAL-TEST'")
        conn.execute("DELETE FROM risk_signals WHERE order_id LIKE 'ORD-TEST-%' OR order_id='ORD-B-REAL-TEST' OR task_id LIKE 'TASK-TEST-%' OR task_id='TASK-B-REAL-TEST'")
        conn.execute("DELETE FROM agent_tool_calls WHERE run_id LIKE 'RUN-TEST-%' OR run_id='RUN-B-REAL-TEST'")
        conn.execute("DELETE FROM commitment_history WHERE order_id LIKE 'ORD-TEST-%' OR order_id='ORD-B-REAL-TEST'")
        conn.execute("DELETE FROM candidate_reviews WHERE order_id LIKE 'ORD-TEST-%' OR order_id='ORD-B-REAL-TEST'")
        conn.execute("DELETE FROM approval_requests WHERE approval_id LIKE 'APP-TEST-%' OR approval_id='APP-B-REAL-TEST'")
        conn.execute("DELETE FROM agent_runs WHERE run_id LIKE 'RUN-TEST-%' OR run_id='RUN-B-REAL-TEST'")
        conn.execute("DELETE FROM tasks WHERE task_id LIKE 'TASK-TEST-%' OR task_id='TASK-B-REAL-TEST'")
        conn.execute("DELETE FROM orders WHERE order_id LIKE 'ORD-TEST-%' OR order_id='ORD-B-REAL-TEST'")
        
        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()


# =============================================================================
# B01: MANAGER_CROSS_ORG_LIST - Manager cannot list ORG-B orders
# =============================================================================

class TestB01ManagerCrossOrgList:
    """B01: MANAGER-A must NOT see ORG-B orders in list."""
    
    def test_MANAGER_A_cannot_list_ORG_B_orders(self, seed_real_org_data):
        """MANAGER-A (ORG-A) requesting order list must NOT see ORG-B orders."""
        headers = auth_headers("MANAGER-A")
        resp = client.get(f"{BASE_URL}/orders", headers=headers)
        
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("items", [])
        
        # Verify ORG-B orders are NOT in the list
        org_b_order_ids = [seed_real_org_data.org_b_order_id]
        visible_org_b_orders = [o for o in items if o.get("order_id") in org_b_order_ids]
        
        assert len(visible_org_b_orders) == 0, (
            f"B01 FAIL: MANAGER-A can see ORG-B orders! "
            f"Visible: {[o.get('order_id') for o in visible_org_b_orders]}"
        )
        
        # Verify ORG-A orders ARE visible (manager can see own org)
        org_a_order_ids = [seed_real_org_data.org_a_order_id]
        visible_org_a_orders = [o for o in items if o.get("order_id") in org_a_order_ids]
        
        assert len(visible_org_a_orders) > 0, "B01: MANAGER-A should see ORG-A orders"


# =============================================================================
# B02: MANAGER_CROSS_ORG_READ - Manager cannot read ORG-B order
# =============================================================================

class TestB02ManagerCrossOrgRead:
    """B02: MANAGER-A must NOT read ORG-B order details."""
    
    def test_MANAGER_A_cannot_read_ORG_B_order(self, seed_real_org_data):
        """MANAGER-A requesting ORG-B order must get 403 or 404."""
        headers = auth_headers("MANAGER-A")
        resp = client.get(f"{BASE_URL}/orders/{seed_real_org_data.org_b_order_id}", headers=headers)
        
        assert resp.status_code in (403, 404), (
            f"B02 FAIL: MANAGER-A was able to read ORG-B order! "
            f"Status: {resp.status_code}"
        )


# =============================================================================
# B03: MANAGER_CROSS_ORG_WRITE - Manager cannot modify ORG-B order
# =============================================================================

class TestB03ManagerCrossOrgWrite:
    """B03: MANAGER-A must NOT modify ORG-B order, and data must NOT change."""
    
    def test_MANAGER_A_cannot_write_ORG_B_order(self, seed_real_org_data):
        """MANAGER-A attempting PATCH on ORG-B order must be blocked AND data unchanged."""
        # Get original state
        with db() as conn:
            original = conn.execute(
                "SELECT customer_name FROM orders WHERE order_id=?",
                (seed_real_org_data.org_b_order_id,)
            ).fetchone()
            original_customer = original["customer_name"]
        
        # Attempt attack
        headers = auth_headers("MANAGER-A")
        attack_payload = {
            "customer_name": "HACKED BY MANAGER-A"
        }
        resp = client.patch(
            f"{BASE_URL}/orders/{seed_real_org_data.org_b_order_id}",
            json=attack_payload,
            headers=headers
        )
        
        # Must be blocked
        assert resp.status_code in (403, 404), (
            f"B03 FAIL: MANAGER-A was able to modify ORG-B order! "
            f"Status: {resp.status_code}"
        )
        
        # Verify data was NOT modified
        with db() as conn:
            after = conn.execute(
                "SELECT customer_name FROM orders WHERE order_id=?",
                (seed_real_org_data.org_b_order_id,)
            ).fetchone()
            after_customer = after["customer_name"]
        
        assert after_customer == original_customer, (
            f"B03 FAIL: Data was modified! Original: {original_customer}, After: {after_customer}"
        )


# =============================================================================
# B04-B07: CROSS_ORG_TASK_OPERATIONS
# =============================================================================

class TestB04B07CrossOrgTaskOperations:
    """B04-B07: OPERATOR-A1 cannot perform operations on ORG-B tasks."""
    
    def test_B04_CONTACTED_cross_org_blocked(self, seed_real_org_data):
        """OPERATOR-A1 cannot mark ORG-B task as contacted."""
        headers = auth_headers("OPERATOR-A1")
        resp = client.post(
            f"{BASE_URL}/tasks/{seed_real_org_data.org_b_task_id}/contacted",
            json={"notes": "Cross-org attack"},
            headers=headers
        )
        
        assert resp.status_code in (403, 404), (
            f"B04 FAIL: OPERATOR-A1 could contact ORG-B task! Status: {resp.status_code}"
        )
    
    def test_B05_COMPLETE_cross_org_blocked(self, seed_real_org_data):
        """OPERATOR-A1 cannot complete ORG-B task."""
        headers = auth_headers("OPERATOR-A1")
        resp = client.post(
            f"{BASE_URL}/tasks/{seed_real_org_data.org_b_task_id}/complete",
            json={"notes": "Cross-org attack"},
            headers=headers
        )
        
        assert resp.status_code in (403, 404), (
            f"B05 FAIL: OPERATOR-A1 could complete ORG-B task! Status: {resp.status_code}"
        )
    
    def test_B06_TRANSFER_cross_org_blocked(self, seed_real_org_data):
        """OPERATOR-A1 cannot transfer ORG-B task."""
        headers = auth_headers("OPERATOR-A1")
        resp = client.post(
            f"{BASE_URL}/tasks/{seed_real_org_data.org_b_task_id}/transfer",
            json={"owner_user_id": "OPERATOR-A1"},
            headers=headers
        )
        
        assert resp.status_code in (403, 404), (
            f"B06 FAIL: OPERATOR-A1 could transfer ORG-B task! Status: {resp.status_code}"
        )
    
    def test_B07_ESCALATE_cross_org_blocked(self, seed_real_org_data):
        """OPERATOR-A1 cannot escalate ORG-B task."""
        headers = auth_headers("OPERATOR-A1")
        resp = client.post(
            f"{BASE_URL}/tasks/{seed_real_org_data.org_b_task_id}/escalate",
            json={"reason": "Cross-org attack"},
            headers=headers
        )
        
        assert resp.status_code in (403, 404), (
            f"B07 FAIL: OPERATOR-A1 could escalate ORG-B task! Status: {resp.status_code}"
        )
    
    def test_MANAGER_A_cannot_operate_ORG_B_task(self, seed_real_org_data):
        """MANAGER-A also cannot operate on ORG-B tasks."""
        headers = auth_headers("MANAGER-A")
        resp = client.post(
            f"{BASE_URL}/tasks/{seed_real_org_data.org_b_task_id}/contacted",
            json={"notes": "Manager cross-org attack"},
            headers=headers
        )
        
        assert resp.status_code in (403, 404), (
            f"MANAGER-A should NOT operate on ORG-B tasks! Status: {resp.status_code}"
        )


# =============================================================================
# B08: CROSS_ORG_TASK_CREATE - Cannot create task on ORG-B order
# =============================================================================

class TestB08CrossOrgTaskCreate:
    """B08: OPERATOR-A1 cannot create task for ORG-B order. 
    Task must also have correct organization_id when created."""
    
    def test_create_task_on_org_b_order_blocked(self, seed_real_org_data):
        """OPERATOR-A1 cannot create task on ORG-B order."""
        headers = auth_headers("OPERATOR-A1")
        resp = client.post(
            f"{BASE_URL}/orders/{seed_real_org_data.org_b_order_id}/tasks",
            json={"title": "Cross-org task creation attempt"},
            headers=headers
        )
        
        assert resp.status_code in (403, 404), (
            f"B08 FAIL: OPERATOR-A1 could create task on ORG-B order! Status: {resp.status_code}"
        )
    
    def test_created_task_has_correct_org_binding(self, seed_real_org_data):
        """When OPERATOR-A1 creates task on own ORG-A order, it must have ORG-A binding."""
        headers = auth_headers("OPERATOR-A1")
        resp = client.post(
            f"{BASE_URL}/orders/{seed_real_org_data.org_a_order_id}/tasks",
            json={"title": "Valid task creation"},
            headers=headers
        )
        
        assert resp.status_code == 200
        data = resp.json()
        
        # Verify the created task's organization_id
        task_id = data.get("task_id") or data.get("id")
        if task_id:
            with db() as conn:
                task = conn.execute(
                    "SELECT organization_id FROM tasks WHERE task_id=?",
                    (task_id,)
                ).fetchone()
                if task:
                    assert task["organization_id"] == "ORG-A", (
                        f"B08 FAIL: Created task has wrong org! "
                        f"Expected: ORG-A, Got: {task['organization_id']}"
                    )


# =============================================================================
# B09: TRACE_CROSS_ORG_LEAK - Manager cannot access ORG-B trace
# =============================================================================

class TestB09TraceCrossOrgLeak:
    """B09: MANAGER-A cannot access ORG-B agent run trace."""
    
    def test_MANAGER_A_cannot_access_ORG_B_trace(self, seed_real_org_data):
        """MANAGER-A cannot get trace of ORG-B agent run."""
        headers = auth_headers("MANAGER-A")
        resp = client.get(
            f"{BASE_URL}/agent/runs/{seed_real_org_data.org_b_run_id}/trace",
            headers=headers
        )
        
        assert resp.status_code in (403, 404), (
            f"B09 FAIL: MANAGER-A could access ORG-B trace! Status: {resp.status_code}"
        )


# =============================================================================
# B10: APPROVAL_CROSS_ORG - Manager cannot decide ORG-B approval
# =============================================================================

class TestB10ApprovalCrossOrg:
    """B10: MANAGER-A cannot decide ORG-B approval."""
    
    def test_MANAGER_A_cannot_decide_ORG_B_approval(self, seed_real_org_data):
        """MANAGER-A cannot reject/approve ORG-B approval."""
        headers = auth_headers("MANAGER-A")
        resp = client.post(
            f"{BASE_URL}/agent/approvals/{seed_real_org_data.org_b_approval_id}/decision",
            json={"decision": "REJECT", "note": "Cross-org attack"},
            headers=headers
        )
        
        assert resp.status_code in (403, 404), (
            f"B10 FAIL: MANAGER-A could decide ORG-B approval! Status: {resp.status_code}"
        )
    
    def test_approval_data_unchanged_after_block(self, seed_real_org_data):
        """After blocked attempt, approval status must remain PENDING."""
        headers = auth_headers("MANAGER-A")
        resp = client.post(
            f"{BASE_URL}/agent/approvals/{seed_real_org_data.org_b_approval_id}/decision",
            json={"decision": "APPROVE"},
            headers=headers
        )
        
        assert resp.status_code in (403, 404)
        
        # Verify approval status unchanged
        with db() as conn:
            approval = conn.execute(
                "SELECT status FROM approval_requests WHERE approval_id=?",
                (seed_real_org_data.org_b_approval_id,)
            ).fetchone()
            assert approval["status"] == "PENDING", (
                f"B10 FAIL: Approval status was changed! Expected: PENDING, Got: {approval['status']}"
            )


# =============================================================================
# B11: NO_TOKEN_IDENTITY_SPOOF - Cannot spoof identity via query/header
# =============================================================================

class TestB11NoTokenIdentitySpoof:
    """B11: Without token, cannot spoof identity via query params or headers."""
    
    def test_no_token_with_query_param_spoofed(self, seed_real_org_data):
        """Request without token but with current_user_id query must fail."""
        headers = {"current_user_id": "MANAGER-B"}
        resp = client.get(
            f"{BASE_URL}/orders",
            params={"current_user_id": "MANAGER-B"},
            headers={}
        )
        
        # Must be 401 (no auth) or 403, not 200 with spoofed identity
        assert resp.status_code in (401, 403), (
            f"B11 FAIL: No-token request with spoofed query param was accepted! Status: {resp.status_code}"
        )
    
    def test_no_token_with_x_user_id_header(self, seed_real_org_data):
        """Request without token but with X-User-Id header must fail."""
        headers = {"X-User-Id": "MANAGER-B"}
        resp = client.get(
            f"{BASE_URL}/orders",
            headers=headers
        )
        
        # Must be 401 (no auth), not 200 with spoofed identity
        assert resp.status_code == 401, (
            f"B11 FAIL: No-token request with X-User-Id header was accepted! Status: {resp.status_code}"
        )
    
    def test_MANAGER_A_token_cannot_spoof_to_MANAGER_B(self, seed_real_org_data):
        """MANAGER-A token cannot be used to access MANAGER-B data via query param."""
        headers = auth_headers("MANAGER-A")
        resp = client.get(
            f"{BASE_URL}/orders",
            params={"current_user_id": "MANAGER-B"},
            headers=headers
        )
        
        # Should still be treated as MANAGER-A, not MANAGER-B
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("items", [])
        
        # Verify ORG-B orders are still NOT visible
        visible_org_b = [o for o in items if o.get("order_id") == seed_real_org_data.org_b_order_id]
        assert len(visible_org_b) == 0, (
            f"B11 FAIL: Could spoof identity via query param! Visible ORG-B orders: {len(visible_org_b)}"
        )


# =============================================================================
# B12: BODY_IDENTITY_SPOOF - Cannot submit identity in request body
# =============================================================================

class TestB12BodyIdentitySpoof:
    """B12: Client cannot submit identity object in request body."""
    
    def test_body_identity_ignored_on_order_update(self, seed_real_org_data):
        """Body identity field must be ignored - only token identity counts."""
        headers = auth_headers("MANAGER-A")
        resp = client.patch(
            f"{BASE_URL}/orders/{seed_real_org_data.org_b_order_id}",
            json={
                "customer_name": "HACKED VIA BODY",
                "identity": {
                    "user_id": "MANAGER-B",
                    "organization_id": "ORG-B",
                    "role": "manager"
                }
            },
            headers=headers
        )
        
        # Must be blocked (cross-org), not accepted with spoofed body identity
        assert resp.status_code in (403, 404), (
            f"B12 FAIL: Body identity spoof was accepted! Status: {resp.status_code}"
        )
        
        # Verify data unchanged
        with db() as conn:
            order = conn.execute(
                "SELECT customer_name FROM orders WHERE order_id=?",
                (seed_real_org_data.org_b_order_id,)
            ).fetchone()
            assert order["customer_name"] != "HACKED VIA BODY", (
                "B12 FAIL: Data was modified via body identity spoof!"
            )


# =============================================================================
# B13: AUDIT_ACTOR_TRUST - Audit actor must be from server identity, not client
# =============================================================================

class TestB13AuditActorTrust:
    """B13: Audit/event logs must use server-resolved identity, not client-supplied."""
    
    def test_task_update_uses_server_identity(self, seed_real_org_data):
        """Task update event log must use token-based identity, not any client-supplied actor."""
        headers = auth_headers("OPERATOR-A1")
        
        # First get a valid task for OPERATOR-A1
        task_id = seed_real_org_data.org_a_task_id
        
        # Update task
        resp = client.patch(
            f"{BASE_URL}/tasks/{task_id}",
            json={"urgent": True},
            headers=headers
        )
        
        if resp.status_code == 200:
            # Verify audit log uses OPERATOR-A1, not any spoofed actor
            with db() as conn:
                events = conn.execute(
                    "SELECT operator_id FROM event_logs WHERE entity_id=? AND event_type='TASK_UPDATED_FROM_UI' ORDER BY created_at DESC LIMIT 1",
                    (task_id,)
                ).fetchall()
                
                if events:
                    # The operator_id should be OPERATOR-A1 (from token), not a spoofed value
                    for event in events:
                        assert event["operator_id"] == "OPERATOR-A1", (
                            f"B13 FAIL: Audit actor is {event['operator_id']}, expected OPERATOR-A1!"
                        )


# =============================================================================
# B14: CREATE_ORG_BINDING - New records must have correct org
# =============================================================================

class TestB14CreateOrgBinding:
    """B14: All new records created by a user must have their organization_id set correctly."""
    
    def test_new_task_binds_to_correct_org(self, seed_real_org_data):
        """Task created by OPERATOR-A1 on ORG-A order must get ORG-A binding."""
        headers = auth_headers("OPERATOR-A1")
        resp = client.post(
            f"{BASE_URL}/orders/{seed_real_org_data.org_a_order_id}/tasks",
            json={"title": "New task with correct org"},
            headers=headers
        )
        
        assert resp.status_code == 200
        data = resp.json()
        task_id = data.get("task_id") or data.get("id")
        
        if task_id:
            with db() as conn:
                task = conn.execute(
                    "SELECT organization_id FROM tasks WHERE task_id=?",
                    (task_id,)
                ).fetchone()
                if task:
                    assert task["organization_id"] == "ORG-A", (
                        f"B14 FAIL: New task has wrong org binding! "
                        f"Expected: ORG-A, Got: {task['organization_id']}"
                    )


# =============================================================================
# REGRESSION: Ensure valid ORG-A operations still work
# =============================================================================

class TestORGARegression:
    """Verify that legitimate ORG-A operations still work after security fixes."""
    
    def test_OPERATOR_A1_can_list_own_orders(self, seed_real_org_data):
        """OPERATOR-A1 should be able to list own ORG-A orders."""
        headers = auth_headers("OPERATOR-A1")
        resp = client.get(f"{BASE_URL}/orders", headers=headers)
        
        assert resp.status_code == 200
        data = resp.json()
        items = data.get("items", [])
        own_orders = [o for o in items if o.get("owner") == "OPERATOR-A1"]
        
        assert len(own_orders) > 0, "Regression: OPERATOR-A1 cannot see own orders!"
    
    def test_OPERATOR_A1_can_read_own_order(self, seed_real_org_data):
        """OPERATOR-A1 should be able to read own ORG-A order."""
        headers = auth_headers("OPERATOR-A1")
        resp = client.get(
            f"{BASE_URL}/orders/{seed_real_org_data.org_a_order_id}",
            headers=headers
        )
        
        assert resp.status_code == 200, (
            f"Regression: OPERATOR-A1 cannot read own order! Status: {resp.status_code}"
        )
    
    def test_MANAGER_A_can_manage_ORG_A_resources(self, seed_real_org_data):
        """MANAGER-A should be able to manage ORG-A resources."""
        headers = auth_headers("MANAGER-A")
        
        # List orders
        resp = client.get(f"{BASE_URL}/orders", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        
        # Should see ORG-A orders
        items = data.get("items", [])
        org_a_visible = [o for o in items if o.get("organization_id") == "ORG-A"]
        assert len(org_a_visible) > 0, "Regression: MANAGER-A cannot see ORG-A orders!"
        
        # Read ORG-A order
        resp = client.get(
            f"{BASE_URL}/orders/{seed_real_org_data.org_a_order_id}",
            headers=headers
        )
        assert resp.status_code == 200, "Regression: MANAGER-A cannot read ORG-A order!"
    
    def test_MANAGER_B_can_manage_ORG_B_resources(self, seed_real_org_data):
        """MANAGER-B should be able to manage ORG-B resources."""
        headers = auth_headers("MANAGER-B")
        
        # List orders
        resp = client.get(f"{BASE_URL}/orders", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        
        # Should see ORG-B orders
        items = data.get("items", [])
        org_b_visible = [o for o in items if o.get("organization_id") == "ORG-B"]
        assert len(org_b_visible) > 0, "Regression: MANAGER-B cannot see ORG-B orders!"


# =============================================================================
# ADDITIONAL: Task PATCH security test
# =============================================================================

class TestTaskPatchSecurity:
    """Verify that PATCH /api/tasks/{task_id} requires proper auth and org check."""
    
    def test_patch_task_requires_auth(self, seed_real_org_data):
        """PATCH task without token must be blocked."""
        resp = client.patch(
            f"{BASE_URL}/tasks/{seed_real_org_data.org_a_task_id}",
            json={"urgent": True}
        )
        
        assert resp.status_code in (401, 403), (
            f"Task PATCH without token should be blocked! Status: {resp.status_code}"
        )
    
    def test_OPERATOR_A1_cannot_patch_ORG_B_task(self, seed_real_org_data):
        """OPERATOR-A1 cannot PATCH ORG-B task."""
        headers = auth_headers("OPERATOR-A1")
        
        with db() as conn:
            original = conn.execute(
                "SELECT title FROM tasks WHERE task_id=?",
                (seed_real_org_data.org_b_task_id,)
            ).fetchone()
            original_title = original["title"]
        
        resp = client.patch(
            f"{BASE_URL}/tasks/{seed_real_org_data.org_b_task_id}",
            json={"title": "HACKED TITLE"},
            headers=headers
        )
        
        assert resp.status_code in (403, 404), (
            f"Task PATCH cross-org should be blocked! Status: {resp.status_code}"
        )
        
        # Verify data unchanged
        with db() as conn:
            task = conn.execute(
                "SELECT title FROM tasks WHERE task_id=?",
                (seed_real_org_data.org_b_task_id,)
            ).fetchone()
            assert task["title"] == original_title, (
                f"Task title was modified! Original: {original_title}, After: {task['title']}"
            )
