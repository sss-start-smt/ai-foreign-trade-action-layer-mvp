"""
D7 Integration / Attack Tests
============================

These tests call run_d7_pipeline() directly with full database setup.
They verify:
  - CurrentIdentity dataclass resolution
  - Organization hard isolation (D3 frozen rule)
  - Cross-org attack prevention
  - Owner equality (not substring)
  - Real D6 ERP snapshot provenance
  - Severity propagation to score
  - Not-padding behavior
  - Deterministic decision identity
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

CN_TZ = timezone(timedelta(hours=8))


# ─── Database Setup ────────────────────────────────────────────────────

@pytest.fixture
def db_conn():
    """Create a fresh SQLite test database with schema + D6 tables."""
    from sqlalchemy import create_engine, text

    tmpdir = tempfile.mkdtemp(prefix="floworder_d7test_")
    db_path = os.path.join(tmpdir, "test_d7.db")
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
        # Core tables
        conn.execute(text("""
            CREATE TABLE orders (
                order_id TEXT PRIMARY KEY,
                order_no TEXT UNIQUE NOT NULL,
                customer_name TEXT,
                product_name TEXT,
                packaging_method TEXT,
                requested_delivery_date TEXT,
                latest_supplier_commitment TEXT,
                current_progress REAL,
                current_node TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                owner TEXT,
                organization_id TEXT,
                action_readiness TEXT NOT NULL DEFAULT 'BASE_ONLY',
                contact_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                issue_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                initialization_waiting_on TEXT,
                initialization_promised_reply_at TEXT,
                initialization_note TEXT,
                initialization_source TEXT,
                initialized_at TEXT,
                last_dynamic_update_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE TABLE tasks (
                task_id TEXT PRIMARY KEY,
                related_order_id TEXT,
                title TEXT NOT NULL,
                recommended_action TEXT,
                target TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                owner_user_id TEXT,
                responsibility_status TEXT NOT NULL DEFAULT 'assigned',
                waiting_on TEXT,
                promised_reply_at TEXT,
                next_action_at TEXT,
                business_deadline TEXT,
                last_contact_at TEXT,
                risk_level TEXT NOT NULL DEFAULT 'none',
                urgent INTEGER NOT NULL DEFAULT 0,
                pending_confirmation INTEGER NOT NULL DEFAULT 0,
                source_message_id TEXT,
                evidence_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE TABLE logistics_events (
                logistics_event_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                location TEXT,
                description TEXT,
                event_time TEXT,
                estimated_arrival_at TEXT,
                source TEXT NOT NULL DEFAULT 'SYNTHETIC_OR_MANUAL',
                resolved_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE TABLE commitment_history (
                commitment_id TEXT PRIMARY KEY,
                order_id TEXT NOT NULL,
                commitment_type TEXT NOT NULL,
                commitment_value TEXT NOT NULL,
                source_message_id TEXT,
                confirmed_by TEXT,
                created_at TEXT NOT NULL
            )
        """))

        # D6 tables for ERP snapshot bridging
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS erp_read_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL,
                doctype TEXT NOT NULL,
                external_id TEXT NOT NULL,
                normalized_json TEXT NOT NULL,
                source_modified_at TEXT,
                fetched_at TEXT NOT NULL
            )
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS erp_sync_state (
                sync_id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL,
                doctype TEXT NOT NULL,
                last_success_cursor TEXT,
                last_success_at TEXT,
                last_attempt_at TEXT,
                sync_status TEXT NOT NULL DEFAULT 'FRESH',
                last_error_code TEXT,
                records_seen INTEGER NOT NULL DEFAULT 0,
                records_changed INTEGER NOT NULL DEFAULT 0
            )
        """))

    yield engine.connect()

    engine.dispose()


def _insert_order(conn, **kwargs) -> str:
    """Insert an order and return order_id."""
    defaults = {
        "order_id": kwargs.get("order_id", "ORD-NEW"),
        "order_no": kwargs.get("order_no", "PO-NEW"),
        "customer_name": kwargs.get("customer_name", "Test Customer"),
        "product_name": kwargs.get("product_name", "Test Product"),
        "packaging_method": kwargs.get("packaging_method", "Default"),
        "requested_delivery_date": kwargs.get("requested_delivery_date"),
        "latest_supplier_commitment": kwargs.get("latest_supplier_commitment"),
        "current_progress": kwargs.get("current_progress"),
        "current_node": kwargs.get("current_node"),
        "status": kwargs.get("status", "ACTIVE"),
        "owner": kwargs.get("owner"),
        "organization_id": kwargs.get("organization_id", "ORG-TEST"),
        "action_readiness": kwargs.get("action_readiness", "BASE_ONLY"),
        "contact_status": kwargs.get("contact_status", "UNKNOWN"),
        "issue_status": kwargs.get("issue_status", "UNKNOWN"),
        "initialization_waiting_on": kwargs.get("initialization_waiting_on"),
        "initialization_promised_reply_at": kwargs.get("initialization_promised_reply_at"),
        "initialization_note": kwargs.get("initialization_note"),
        "initialization_source": kwargs.get("initialization_source"),
        "initialized_at": kwargs.get("initialized_at"),
        "last_dynamic_update_at": kwargs.get("last_dynamic_update_at"),
        "created_at": kwargs.get("created_at", "2026-08-01T00:00:00+08:00"),
        "updated_at": kwargs.get("updated_at", "2026-08-01T00:00:00+08:00"),
    }
    columns = list(defaults.keys())
    placeholders = ", ".join([f":{c}" for c in columns])
    col_list = ", ".join(columns)
    conn.execute(
        text(f"INSERT INTO orders ({col_list}) VALUES ({placeholders})"),
        defaults
    )
    conn.commit()
    return defaults["order_id"]


def _insert_erp_snapshot(conn, *, org_id: str, order_no: str,
                         snapshot_id: str, normalized: dict,
                         source_modified_at: str, fetched_at: str) -> None:
    """Insert an ERP snapshot."""
    conn.execute(
        text("INSERT INTO erp_read_snapshots "
        "(snapshot_id, organization_id, doctype, external_id, normalized_json, source_modified_at, fetched_at) "
        "VALUES (:snapshot_id, :org_id, 'Sales Order', :order_no, :normalized_json, :source_modified_at, :fetched_at)"),
        {
            "snapshot_id": snapshot_id,
            "org_id": org_id,
            "order_no": order_no,
            "normalized_json": json.dumps(normalized, ensure_ascii=False),
            "source_modified_at": source_modified_at,
            "fetched_at": fetched_at,
        }
    )
    conn.commit()


def _insert_sync_state(conn, *, org_id: str, status: str = "FRESH",
                       last_success_at: str | None = None) -> None:
    """Insert an ERP sync state."""
    now_iso = last_success_at or datetime.now(CN_TZ).isoformat(timespec="seconds")
    conn.execute(
        text("INSERT INTO erp_sync_state "
        "(sync_id, organization_id, doctype, last_success_cursor, last_success_at, "
        "last_attempt_at, sync_status, last_error_code, records_seen, records_changed) "
        "VALUES (:sync_id, :org_id, 'Sales Order', :now_iso, :now_iso2, :now_iso3, :status, NULL, 0, 0)"),
        {
            "sync_id": f"SYNC-{org_id}",
            "org_id": org_id,
            "now_iso": now_iso,
            "now_iso2": now_iso,
            "now_iso3": now_iso,
            "status": status,
        }
    )
    conn.commit()


def _now() -> datetime:
    return datetime.now(CN_TZ)


# ─── Identity Helpers ──────────────────────────────────────────────────

def _make_current_identity(user_id: str, org_id: str, role: str) -> Any:
    """Create a CurrentIdentity dataclass (imported from auth)."""
    from auth import CurrentIdentity
    return CurrentIdentity(user_id=user_id, organization_id=org_id, role=role)


def _make_dict_identity(user_id: str, org_id: str, role: str) -> dict:
    """Create a dict identity (test helper)."""
    return {"user_id": user_id, "organization_id": org_id, "role": role}


# ─── Tests ─────────────────────────────────────────────────────────────

class TestPipelineIdentity:
    """Test that run_d7_pipeline correctly resolves CurrentIdentity."""

    def test_pipeline_current_identity_not_none(self, db_conn):
        """CurrentIdentity dataclass must be correctly parsed, not None."""
        _insert_order(
            db_conn,
            order_id="ORD-IDENT",
            order_no="PO-IDENT",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.5,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        assert result["scope"]["user_id"] == "USER-1"
        assert result["scope"]["organization_id"] == "ORG-A"
        assert result["scope"]["user_role"] == "operator"
        assert result["screened_order_count"] >= 1
        assert "error" not in result or result.get("error") != "IDENTITY_NOT_RESOLVED"

    def test_pipeline_dict_identity_works(self, db_conn):
        """Dict identity should also work (backward compat)."""
        _insert_order(
            db_conn,
            order_id="ORD-DICT",
            order_no="PO-DICT",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.5,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        identity = _make_dict_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        assert result["scope"]["user_id"] == "USER-1"
        assert result["scope"]["organization_id"] == "ORG-A"
        assert result["scope"]["user_role"] == "operator"

    def test_pipeline_unresolved_identity_fails_closed(self, db_conn):
        """Invalid identity must return empty result with error."""
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, None)
        assert result["error"] == "IDENTITY_NOT_RESOLVED"
        assert result["screened_order_count"] == 0
        assert result["count"] == 0


class TestPipelineOrgIsolation:
    """Test that organization hard filter is enforced (D3 frozen rule)."""

    def test_pipeline_manager_cannot_read_other_org(self, db_conn):
        """Manager from ORG-A must NOT see ORG-B orders."""
        # ORG-A order
        _insert_order(
            db_conn,
            order_id="ORD-A1",
            order_no="PO-A1",
            requested_delivery_date=(_now() - timedelta(days=2)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="USER-A1",
            organization_id="ORG-A",
        )
        # ORG-B order (risky)
        _insert_order(
            db_conn,
            order_id="ORD-B1",
            order_no="PO-B1",
            requested_delivery_date=(_now() - timedelta(days=5)).date().isoformat(),
            current_progress=0.1,
            current_node="未开工",
            owner="USER-B1",
            organization_id="ORG-B",
        )

        identity = _make_current_identity("MANAGER-A", "ORG-A", "manager")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        # Manager-A should only see ORG-A orders
        assert result["screened_order_count"] == 1
        order_ids = [item.get("order_id") for item in result.get("items", [])]
        assert "ORD-B1" not in order_ids
        assert "ORD-A1" in order_ids or result["risk_order_count"] >= 0

    def test_pipeline_operator_cannot_read_other_org(self, db_conn):
        """Operator from ORG-A must NOT see ORG-B orders."""
        # ORG-A user's order
        _insert_order(
            db_conn,
            order_id="ORD-A2",
            order_no="PO-A2",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="USER-A1",
            organization_id="ORG-A",
        )
        # ORG-B order owned by ORG-B user
        _insert_order(
            db_conn,
            order_id="ORD-B2",
            order_no="PO-B2",
            requested_delivery_date=(_now() - timedelta(days=3)).date().isoformat(),
            current_progress=0.2,
            current_node="生产中",
            owner="USER-B1",
            organization_id="ORG-B",
        )

        identity = _make_current_identity("USER-A1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        # Operator should only see their own orders in their org
        assert result["screened_order_count"] == 1
        order_ids = [item.get("order_id") for item in result.get("items", [])]
        assert "ORD-B2" not in order_ids

    def test_operator_cannot_read_other_owner(self, db_conn):
        """Operator must NOT see orders owned by other users in same org."""
        # USER-A1's order
        _insert_order(
            db_conn,
            order_id="ORD-OWN",
            order_no="PO-OWN",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="USER-A1",
            organization_id="ORG-A",
        )
        # USER-A2's order
        _insert_order(
            db_conn,
            order_id="ORD-OTHER",
            order_no="PO-OTHER",
            requested_delivery_date=(_now() - timedelta(days=3)).date().isoformat(),
            current_progress=0.2,
            current_node="生产中",
            owner="USER-A2",
            organization_id="ORG-A",
        )

        identity = _make_current_identity("USER-A1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        # Operator should only see their own orders
        assert result["screened_order_count"] == 1


class TestOwnerEquality:
    """Test that owner check uses exact equality, not substring."""

    def test_owner_equality_not_substring(self, db_conn):
        """USER-1 must NOT match USER-10 via substring."""
        _insert_order(
            db_conn,
            order_id="ORD-SUB",
            order_no="PO-SUB",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.5,
            current_node="生产中",
            owner="USER-10",  # USER-1 is a substring of USER-10
            organization_id="ORG-A",
        )

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        # USER-1 should NOT see USER-10's order (not owner)
        assert result["screened_order_count"] == 0

    def test_exact_owner_match_works(self, db_conn):
        """Exact owner match should work."""
        _insert_order(
            db_conn,
            order_id="ORD-EXACT",
            order_no="PO-EXACT",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        assert result["screened_order_count"] == 1


class TestRealD6OrderStatus:
    """Test that real D6 ERP status mapping avoids false SOURCE_CONFLICT."""

    def test_real_d6_order_status_not_false_conflict(self, db_conn):
        """SAL-ORD-2026-00001: FlowOrder ACTIVE + ERP 'To Deliver and Bill' → NO false conflict."""
        _insert_order(
            db_conn,
            order_id="ORD-D6-1",
            order_no="SAL-ORD-2026-00001",
            status="ACTIVE",
            requested_delivery_date="2026-09-02",
            current_progress=0.6,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        # Insert ERP snapshot with real D6 status
        normalized = {
            "external_id": "SAL-ORD-2026-00001",
            "customer_due_date": "2026-09-02",
            "order_status": "To Deliver and Bill",
            "transaction_date": "2026-08-01",
            "source_modified_at": "2026-08-12T10:00:00+08:00",
            "items": [{"item_code": "ITEM-001", "qty": 100}],
            "erp_owner": "erpuser",
        }
        _insert_erp_snapshot(
            db_conn,
            org_id="ORG-A",
            order_no="SAL-ORD-2026-00001",
            snapshot_id="ERP-SNAP-D6-1",
            normalized=normalized,
            source_modified_at="2026-08-12T10:00:00+08:00",
            fetched_at="2026-08-12T10:05:00+08:00",
        )
        _insert_sync_state(db_conn, org_id="ORG-A", status="FRESH")

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        items = result.get("items", [])
        # Check: no SOURCE_CONFLICT for status mismatch
        conflict_items = []
        for item in items:
            for sig in item.get("risk_signals", []):
                if sig.get("risk_type") == "SOURCE_CONFLICT":
                    conflict_items.append(sig)

        assert len(conflict_items) == 0, (
            f"Should NOT produce SOURCE_CONFLICT for ACTIVE vs 'To Deliver and Bill'. "
            f"Got: {conflict_items}"
        )

    def test_different_semantics_still_conflict(self, db_conn):
        """ genuinely different statuses (COMPLETED vs ACTIVE) SHOULD produce conflict."""
        _insert_order(
            db_conn,
            order_id="ORD-D6-2",
            order_no="SAL-ORD-2026-00002",
            status="ACTIVE",
            requested_delivery_date="2026-09-02",
            current_progress=0.6,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        normalized = {
            "external_id": "SAL-ORD-2026-00002",
            "customer_due_date": "2026-09-02",
            "order_status": "Completed",  # Different semantic!
            "transaction_date": "2026-08-01",
            "source_modified_at": "2026-08-12T10:00:00+08:00",
            "items": [],
            "erp_owner": "erpuser",
        }
        _insert_erp_snapshot(
            db_conn,
            org_id="ORG-A",
            order_no="SAL-ORD-2026-00002",
            snapshot_id="ERP-SNAP-D6-2",
            normalized=normalized,
            source_modified_at="2026-08-12T10:00:00+08:00",
            fetched_at="2026-08-12T10:05:00+08:00",
        )
        _insert_sync_state(db_conn, org_id="ORG-A", status="FRESH")

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        items = result.get("items", [])
        conflict_found = False
        for item in items:
            for sig in item.get("risk_signals", []):
                if sig.get("risk_type") == "SOURCE_CONFLICT":
                    conflict_found = True

        # Completed vs ACTIVE IS a real conflict (different semantic categories)
        assert conflict_found, "Completed vs ACTIVE should be a real SOURCE_CONFLICT"


class TestErpSnapshotProvenance:
    """Test that ERP snapshot risk signals preserve provenance."""

    def test_erp_due_date_risk_keeps_snapshot_provenance(self, db_conn):
        """Delivery risk from ERP snapshot must carry ERP provenance."""
        _insert_order(
            db_conn,
            order_id="ORD-PROV",
            order_no="PO-PROV",
            status="ACTIVE",
            requested_delivery_date="2026-08-15",  # Overridden by ERP
            current_progress=0.3,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        # ERP says due date is 2026-08-10 (past)
        normalized = {
            "external_id": "PO-PROV",
            "customer_due_date": "2026-08-10",  # Past date!
            "order_status": "To Deliver and Bill",
            "transaction_date": "2026-08-01",
            "source_modified_at": "2026-08-12T10:00:00+08:00",
            "items": [],
            "erp_owner": "erpuser",
        }
        _insert_erp_snapshot(
            db_conn,
            org_id="ORG-A",
            order_no="PO-PROV",
            snapshot_id="ERP-SNAP-PROV",
            normalized=normalized,
            source_modified_at="2026-08-12T10:00:00+08:00",
            fetched_at="2026-08-12T10:05:00+08:00",
        )
        _insert_sync_state(db_conn, org_id="ORG-A", status="FRESH")

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity, current_time="2026-08-12T00:00:00+08:00")

        items = result.get("items", [])
        assert len(items) >= 1, "Should have risk items"

        # Find DELIVERY_RISK signals and check provenance
        delivery_signals = []
        for item in items:
            for sig in item.get("risk_signals", []):
                if sig.get("risk_type") == "DELIVERY_RISK":
                    delivery_signals.append(sig)

        assert len(delivery_signals) >= 1, "Should have DELIVERY_RISK"

        sig = delivery_signals[0]
        # Must carry ERP provenance
        assert sig.get("source_type") == "ERP_NEXT", (
            f"Expected source_type=ERP_NEXT, got {sig.get('source_type')}"
        )
        assert sig.get("source_id") == "ERP-SNAP-PROV", (
            f"Expected snapshot_id as source_id, got {sig.get('source_id')}"
        )
        assert sig.get("source_modified_at") == "2026-08-12T10:00:00+08:00", (
            f"Expected source_modified_at from ERP, got {sig.get('source_modified_at')}"
        )
        assert sig.get("organization_id") == "ORG-A", (
            f"Expected organization_id in signal, got {sig.get('organization_id')}"
        )


class TestPipelineSeverity:
    """Test that severity from risk signals reaches the priority score."""

    def test_pipeline_severity_reaches_score(self, db_conn):
        """HIGH/CRITICAL severity must propagate to compute_priority_score."""
        # Create an overdue order with known severity
        _insert_order(
            db_conn,
            order_id="ORD-SEV",
            order_no="PO-SEV",
            requested_delivery_date=(_now() - timedelta(days=10)).date().isoformat(),
            current_progress=0.2,
            current_node="未开工",
            owner="USER-1",
            organization_id="ORG-A",
        )

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        items = result.get("items", [])
        assert len(items) >= 1, "Should have risk items"

        item = items[0]
        # Severity must be set and NOT default MEDIUM
        severity = item.get("severity")
        assert severity is not None, "severity must not be None"
        assert severity in ("HIGH", "CRITICAL"), (
            f"Expected HIGH or CRITICAL severity for overdue order, got {severity}"
        )

        # Priority score must reflect the severity
        score = item.get("priority_score", 0)
        assert score > 0, f"Expected positive priority_score, got {score}"

        # Verify the score is consistent with severity
        # CRITICAL (100) or HIGH (60) weight plus deadline bonus
        risk_signals = item.get("risk_signals", [])
        max_sig_sev = max(
            (s.get("severity", "LOW") for s in risk_signals),
            key=lambda s: {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}.get(s, 0),
            default="LOW"
        )
        assert max_sig_sev == severity, (
            f"Pipeline severity {severity} should match max signal severity {max_sig_sev}"
        )


class TestPipelineNotPadded:
    """Test that Top-N is not padded with fake items."""

    def test_pipeline_not_padded(self, db_conn):
        """If only 2 real risks exist, result should have 2, not pad to 7."""
        for i in range(2):
            _insert_order(
                db_conn,
                order_id=f"ORD-NP-{i}",
                order_no=f"PO-NP-{i}",
                requested_delivery_date=(_now() - timedelta(days=1 + i)).date().isoformat(),
                current_progress=0.3,
                current_node="生产中",
                owner="USER-1",
                organization_id="ORG-A",
            )

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity, top_n=7)

        assert result["count"] == 2, (
            f"Expected exactly 2 items (not padded), got {result['count']}"
        )
        assert result["selection_strategy"]["not_padded"] is True


class TestPipelineDeterministic:
    """Test deterministic decision identity."""

    def test_same_inputs_same_output(self, db_conn):
        """Same facts + identity + time → same result."""
        _insert_order(
            db_conn,
            order_id="ORD-DET",
            order_no="PO-DET",
            requested_delivery_date="2026-08-10",
            current_progress=0.1,
            current_node="未开工",
            owner="USER-1",
            organization_id="ORG-A",
        )

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        fixed_time = "2026-08-12T00:00:00+08:00"

        from d7_risk_engine import run_d7_pipeline
        results = []
        for _ in range(3):
            result = run_d7_pipeline(db_conn, identity, current_time=fixed_time)
            items = result.get("items", [])
            if items:
                item = items[0]
                results.append({
                    "action_bucket": item.get("action_bucket"),
                    "priority_score": item.get("priority_score"),
                    "risk_types": [s.get("risk_type") for s in item.get("risk_signals", [])],
                    "severity": item.get("severity"),
                })

        # All runs should produce identical results
        assert len(results) >= 1
        for i in range(1, len(results)):
            assert results[i] == results[0], (
                f"Run {i} differs from run 0: {results[i]} vs {results[0]}"
            )


class TestPipelineWaitingSuppression:
    """Test waiting suppression in full pipeline."""

    def test_waiting_suppression_in_pipeline(self, db_conn):
        """Active waiting task should suppress ranking."""
        _insert_order(
            db_conn,
            order_id="ORD-WAIT",
            order_no="PO-WAIT",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.3,
            current_node="备料中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        # Insert a waiting task
        future_reply = (_now() + timedelta(hours=3)).isoformat(timespec="seconds")
        now_iso = _now().isoformat(timespec="seconds")
        db_conn.execute(
            text("INSERT INTO tasks "
            "(task_id, related_order_id, title, target, status, owner_user_id, "
            "waiting_on, promised_reply_at, risk_level, urgent, pending_confirmation, "
            "evidence_json, created_at, updated_at) "
            "VALUES (:task_id, :order_id, :title, 'factory', 'WAITING_EXTERNAL', 'USER-1', "
            "'factory', :future_reply, 'none', 0, 0, '[]', :now_iso, :now_iso2)"),
            {
                "task_id": "TASK-WAIT",
                "order_id": "ORD-WAIT",
                "title": "等待工厂确认",
                "future_reply": future_reply,
                "now_iso": now_iso,
                "now_iso2": now_iso,
            }
        )
        db_conn.commit()

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        items = result.get("items", [])
        # Items should be suppressed (WAITING_EXTERNAL ranking_suppressed)
        for item in items:
            if item.get("action_bucket") == "WAITING_EXTERNAL":
                assert item.get("ranking_suppressed") is True


class TestEdgeCases:
    """Additional edge case tests."""

    def test_manager_sees_all_orders_in_org(self, db_conn):
        """Manager should see all orders in their organization."""
        for i, owner in enumerate(["USER-A1", "USER-A2"]):
            _insert_order(
                db_conn,
                order_id=f"ORD-MGR-{i}",
                order_no=f"PO-MGR-{i}",
                requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
                current_progress=0.5,
                current_node="生产中",
                owner=owner,
                organization_id="ORG-A",
            )

        identity = _make_current_identity("MANAGER-A", "ORG-A", "manager")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        assert result["screened_order_count"] == 2, (
            f"Manager should see both ORG-A orders, got {result['screened_order_count']}"
        )

    def test_information_gap_not_padded(self, db_conn):
        """Information gaps should NOT pad the top-N."""
        # Create orders with missing data (no real risk, just info gap)
        for i in range(5):
            _insert_order(
                db_conn,
                order_id=f"ORD-GAP-{i}",
                order_no=f"PO-GAP-{i}",
                requested_delivery_date=(_now() + timedelta(days=30)).date().isoformat(),
                current_progress=None,  # Missing
                current_node=None,  # Missing
                owner="USER-1",
                organization_id="ORG-A",
            )

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        # No real risk items (only INFORMATION_GAP)
        assert result["count"] == 0, "Info gaps should not appear in risk items"
        assert result["information_gap_order_count"] == 5, "Info gaps tracked separately"


# ─── R4 Tests: Effective Delivery Date & Provenance ────────────────────

def _insert_commitment(conn, *, commitment_id: str, order_id: str,
                        commitment_type: str, commitment_value: str,
                        confirmed_by: str | None = None,
                        created_at: str | None = None) -> None:
    """Insert a commitment_history record."""
    now_iso = created_at or datetime.now(CN_TZ).isoformat(timespec="seconds")
    conn.execute(
        text("INSERT INTO commitment_history "
        "(commitment_id, order_id, commitment_type, commitment_value, "
        "source_message_id, confirmed_by, created_at) "
        "VALUES (:commitment_id, :order_id, :commitment_type, :commitment_value, "
        "NULL, :confirmed_by, :created_at)"),
        {
            "commitment_id": commitment_id,
            "order_id": order_id,
            "commitment_type": commitment_type,
            "commitment_value": commitment_value,
            "confirmed_by": confirmed_by,
            "created_at": now_iso,
        }
    )
    conn.commit()


class TestR4EffectiveDeliveryDate:
    """R4: Single effective_delivery_date with provenance."""

    def test_unavailable_erp_local_due_keeps_order_fact_provenance(self, db_conn):
        """STALE/UNAVAILABLE ERP → local date used → provenance must be ORDER_FACTS."""
        _insert_order(
            db_conn,
            order_id="ORD-R4-UAVAIL",
            order_no="PO-R4-UAVAIL",
            status="ACTIVE",
            requested_delivery_date="2026-08-15",
            current_progress=0.3,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        normalized = {
            "external_id": "PO-R4-UAVAIL",
            "customer_due_date": "2026-08-10",
            "order_status": "To Deliver and Bill",
            "transaction_date": "2026-08-01",
            "source_modified_at": "2026-08-05T10:00:00+08:00",
            "items": [],
            "erp_owner": "erpuser",
        }
        _insert_erp_snapshot(
            db_conn,
            org_id="ORG-A",
            order_no="PO-R4-UAVAIL",
            snapshot_id="ERP-SNAP-UAVAIL",
            normalized=normalized,
            source_modified_at="2026-08-05T10:00:00+08:00",
            fetched_at="2026-08-05T10:05:00+08:00",
        )
        _insert_sync_state(db_conn, org_id="ORG-A", status="UNAVAILABLE")

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity, current_time="2026-08-12T00:00:00+08:00")

        items = result.get("items", [])
        assert len(items) >= 1, "Should have risk items"

        item = items[0]
        delivery_signals = [
            s for s in item.get("risk_signals", [])
            if s.get("risk_type") == "DELIVERY_RISK"
        ]
        assert len(delivery_signals) >= 1

        sig = delivery_signals[0]
        assert sig.get("source_type") == "ORDER_FACTS", (
            f"With UNAVAILABLE ERP, source_type must be ORDER_FACTS, got {sig.get('source_type')}"
        )
        assert sig.get("freshness") == "UNAVAILABLE", (
            f"Expected UNAVAILABLE freshness, got {sig.get('freshness')}"
        )

    def test_pipeline_priority_uses_effective_erp_due_date(self, db_conn):
        """FRESH ERP → effective_delivery_date = ERP date → both risk and priority use it."""
        _insert_order(
            db_conn,
            order_id="ORD-R4-FRESH",
            order_no="PO-R4-FRESH",
            status="ACTIVE",
            requested_delivery_date="2026-08-20",
            current_progress=0.3,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        normalized = {
            "external_id": "PO-R4-FRESH",
            "customer_due_date": "2026-08-10",  # ERP says overdue!
            "order_status": "To Deliver and Bill",
            "transaction_date": "2026-08-01",
            "source_modified_at": "2026-08-12T10:00:00+08:00",
            "items": [],
            "erp_owner": "erpuser",
        }
        _insert_erp_snapshot(
            db_conn,
            org_id="ORG-A",
            order_no="PO-R4-FRESH",
            snapshot_id="ERP-SNAP-FRESH",
            normalized=normalized,
            source_modified_at="2026-08-12T10:00:00+08:00",
            fetched_at="2026-08-12T10:05:00+08:00",
        )
        _insert_sync_state(db_conn, org_id="ORG-A", status="FRESH")

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity, current_time="2026-08-12T00:00:00+08:00")

        items = result.get("items", [])
        assert len(items) >= 1

        item = items[0]
        assert item.get("effective_delivery_date") == "2026-08-10", (
            f"effective_delivery_date should be ERP date 2026-08-10, got {item.get('effective_delivery_date')}"
        )
        assert item.get("delivery_source") == "ERP_NEXT", (
            f"delivery_source should be ERP_NEXT, got {item.get('delivery_source')}"
        )

        delivery_signals = [
            s for s in item.get("risk_signals", [])
            if s.get("risk_type") == "DELIVERY_RISK"
        ]
        assert len(delivery_signals) >= 1
        assert delivery_signals[0].get("source_type") == "ERP_NEXT"

        score = item.get("priority_score", 0)
        assert score > 0, "Priority score should be positive for ERP overdue"

    def test_priority_score_uses_effective_delivery_date(self, db_conn):
        """Priority scoring must use effective_delivery_date, not requested_delivery_date."""
        _insert_order(
            db_conn,
            order_id="ORD-R4-SCORE",
            order_no="PO-R4-SCORE",
            status="ACTIVE",
            requested_delivery_date="2026-08-30",
            current_progress=0.5,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        normalized = {
            "external_id": "PO-R4-SCORE",
            "customer_due_date": "2026-08-13",  # ERP says due TOMORROW
            "order_status": "To Deliver and Bill",
            "transaction_date": "2026-08-01",
            "source_modified_at": "2026-08-12T10:00:00+08:00",
            "items": [],
            "erp_owner": "erpuser",
        }
        _insert_erp_snapshot(
            db_conn,
            org_id="ORG-A",
            order_no="PO-R4-SCORE",
            snapshot_id="ERP-SNAP-SCORE",
            normalized=normalized,
            source_modified_at="2026-08-12T10:00:00+08:00",
            fetched_at="2026-08-12T10:05:00+08:00",
        )
        _insert_sync_state(db_conn, org_id="ORG-A", status="FRESH")

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity, current_time="2026-08-12T00:00:00+08:00")

        items = result.get("items", [])
        assert len(items) >= 1

        item = items[0]
        score = item.get("priority_score", 0)
        reasons = item.get("priority_reasons", [])
        reasons_text = " ".join(reasons)

        assert item.get("effective_delivery_date") == "2026-08-13"
        assert "2026-08-30" not in reasons_text, (
            "Priority score must NOT reference the old local date 2026-08-30"
        )

        # The score should be based on ERP date (tomorrow), showing urgency
        assert score > 10, (
            f"Score should reflect ERP due date urgency (>10), got {score}"
        )


class TestR4CommitmentHistory:
    """R4: Confirmed commitment history provenance."""

    def test_supplier_commitment_uses_confirmed_history_provenance(self, db_conn):
        """Confirmed commitment from history → source_type must reflect history."""
        _insert_order(
            db_conn,
            order_id="ORD-R4-COMMIT",
            order_no="PO-R4-COMMIT",
            status="ACTIVE",
            requested_delivery_date="2026-08-20",
            latest_supplier_commitment=None,
            current_progress=0.3,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        _insert_commitment(
            db_conn,
            commitment_id="COMMIT-001",
            order_id="ORD-R4-COMMIT",
            commitment_type="SUPPLIER_COMMITMENT",
            commitment_value="2026-08-10",
            confirmed_by="MANAGER-A",
            created_at="2026-08-01T10:00:00+08:00",
        )

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity, current_time="2026-08-12T00:00:00+08:00")

        items = result.get("items", [])
        assert len(items) >= 1, "Should have risk items"

        item = items[0]
        commitment_signals = [
            s for s in item.get("risk_signals", [])
            if s.get("risk_type") == "SUPPLIER_COMMITMENT_OVERDUE"
        ]
        assert len(commitment_signals) >= 1, "Should have SUPPLIER_COMMITMENT_OVERDUE risk"

        sig = commitment_signals[0]
        assert sig.get("source_type") == "COMMITMENT_HISTORY", (
            f"Expected COMMITMENT_HISTORY source_type, got {sig.get('source_type')}"
        )
        assert sig.get("source_id") == "COMMIT-001", (
            f"Expected source_id=COMMIT-001, got {sig.get('source_id')}"
        )

    def test_unconfirmed_commitment_falls_back_to_order_facts(self, db_conn):
        """Unconfirmed commitment → falls back to ORDER_FACTS provenance."""
        _insert_order(
            db_conn,
            order_id="ORD-R4-UNCONFIRMED",
            order_no="PO-R4-UNCONFIRMED",
            status="ACTIVE",
            requested_delivery_date="2026-08-20",
            latest_supplier_commitment="2026-08-10",
            current_progress=0.3,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        # Unconfirmed commitment (confirmed_by is NULL)
        _insert_commitment(
            db_conn,
            commitment_id="COMMIT-002",
            order_id="ORD-R4-UNCONFIRMED",
            commitment_type="SUPPLIER_COMMITMENT",
            commitment_value="2026-08-10",
            confirmed_by=None,
            created_at="2026-08-01T10:00:00+08:00",
        )

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity, current_time="2026-08-12T00:00:00+08:00")

        items = result.get("items", [])
        assert len(items) >= 1

        item = items[0]
        commitment_signals = [
            s for s in item.get("risk_signals", [])
            if s.get("risk_type") == "SUPPLIER_COMMITMENT_OVERDUE"
        ]
        assert len(commitment_signals) >= 1
        sig = commitment_signals[0]
        assert sig.get("source_type") == "ORDER_FACTS", (
            f"With unconfirmed history, should fall back to ORDER_FACTS, got {sig.get('source_type')}"
        )


# ─── R5 Tests: Final Pre-Freeze Contract Fixes ────────────────────────

class TestR5LogisticsException:
    """R5 baseline + D14 R2 delivery-buffer calibration for logistics risk."""

    def test_single_logistics_exception_goes_do_now(self):
        """Unit test: assign_action_bucket with LOGISTICS_EXCEPTION+HIGH → DO_NOW."""
        from d7_risk_engine import assign_action_bucket, build_risk_signal

        order = {
            "order_id": "ORD-L5-1",
            "order_no": "PO-L5-1",
            "status": "ACTIVE",
            "owner": "USER-1",
            "organization_id": "ORG-A",
            "requested_delivery_date": "2026-08-20",
            "current_progress": 0.5,
            "current_node": "生产中",
        }

        sig = build_risk_signal(
            order_id="ORD-L5-1",
            order_no="PO-L5-1",
            risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH",
            evidence=["物流异常：货代延迟"],
            organization_id="ORG-A",
        )

        result = assign_action_bucket(
            [sig], order, tasks=[], current=_now(),
            user_id="USER-1", user_role="operator",
        )

        assert result["action_bucket"] == "DO_NOW", (
            f"Single LOGISTICS_EXCEPTION+HIGH must go to DO_NOW, got {result['action_bucket']}"
        )
        assert any("物流异常" in r for r in result["bucket_reasons"]), (
            f"Expected logistics action reason, got {result['bucket_reasons']}"
        )

    def test_pipeline_single_logistics_exception_is_actionable(self, db_conn):
        """Full pipeline: single logistics exception must appear in action items."""
        _insert_order(
            db_conn,
            order_id="ORD-L5-PIPE",
            order_no="PO-L5-PIPE",
            status="ACTIVE",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.5,
            current_node="生产中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        # Insert a single unresolved logistics exception
        now_iso = _now().isoformat(timespec="seconds")
        db_conn.execute(
            text("INSERT INTO logistics_events "
            "(logistics_event_id, order_id, event_type, status, description, "
            "source, created_at, updated_at) "
            "VALUES (:eid, :oid, 'DELAY', 'DELAYED', '货代延迟', 'SYNTHETIC', :ts, :ts2)"),
            {
                "eid": "LE-L5-1",
                "oid": "ORD-L5-PIPE",
                "ts": now_iso,
                "ts2": now_iso,
            }
        )
        db_conn.commit()

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        items = result.get("items", [])
        assert len(items) >= 1, "Must have actionable items for logistics exception"

        item = items[0]
        assert item["action_bucket"] == "DO_TODAY", (
            f"Single logistics exception with 10-day buffer should be DO_TODAY after D14 R2 calibration, got {item['action_bucket']}"
        )
        logistics_signals = [s for s in item.get("risk_signals", []) if s.get("risk_type") == "LOGISTICS_EXCEPTION"]
        assert logistics_signals and logistics_signals[0]["severity"] == "MEDIUM"

        # Verify logistics action semantics
        action = item.get("recommended_action", "")
        assert "货代" in action or "物流" in action or "确认" in action, (
            f"Recommended action should include logistics follow-up semantics, got: {action}"
        )

        # Verify risk_order_count > 0 (item is not hidden)
        assert result["risk_order_count"] >= 1, (
            f"risk_order_count should include the logistics exception, got {result['risk_order_count']}"
        )

    def test_logistics_critical_goes_escalate(self):
        """CRITICAL logistics (multiple) should still go to ESCALATE (not broken)."""
        from d7_risk_engine import assign_action_bucket, build_risk_signal

        order = {
            "order_id": "ORD-L5-CRIT",
            "order_no": "PO-L5-CRIT",
            "status": "ACTIVE",
            "owner": "USER-1",
            "organization_id": "ORG-A",
        }

        sig = build_risk_signal(
            order_id="ORD-L5-CRIT",
            order_no="PO-L5-CRIT",
            risk_type="LOGISTICS_EXCEPTION",
            severity="CRITICAL",
            evidence=["Multiple logistics exceptions"],
            organization_id="ORG-A",
        )

        result = assign_action_bucket(
            [sig], order, tasks=[], current=_now(),
            user_id="USER-1", user_role="operator",
        )

        assert result["action_bucket"] == "ESCALATE", (
            f"CRITICAL logistics must go to ESCALATE, got {result['action_bucket']}"
        )


class TestR5OverdueWaitingInfoGap:
    """R5: Overdue waiting must be independent action trigger, not blocked by INFORMATION_GAP."""

    def test_overdue_waiting_info_gap_only_goes_do_now(self):
        """Unit test: overdue waiting + INFORMATION_GAP only → DO_NOW (not SCHEDULED)."""
        from d7_risk_engine import assign_action_bucket, build_risk_signal

        order = {
            "order_id": "ORD-W5-1",
            "order_no": "PO-W5-1",
            "status": "ACTIVE",
            "owner": "USER-1",
            "organization_id": "ORG-A",
            "requested_delivery_date": "2026-08-20",
            "current_progress": 0.5,
            "current_node": "生产中",
        }

        gap_sig = build_risk_signal(
            order_id="ORD-W5-1",
            order_no="PO-W5-1",
            risk_type="INFORMATION_GAP",
            severity="LOW",
            evidence=["信息缺失"],
            organization_id="ORG-A",
        )

        # Create an overdue waiting task
        now = _now()
        overdue_task = {
            "task_id": "TASK-W5-OVER",
            "related_order_id": "ORD-W5-1",
            "title": "跟进工厂",
            "status": "WAITING_EXTERNAL",
            "waiting_on": "工厂",
            "promised_reply_at": (now - timedelta(hours=2)).isoformat(timespec="seconds"),
            "risk_level": "none",
            "urgent": 0,
            "pending_confirmation": 0,
            "evidence_json": "[]",
            "created_at": now.isoformat(timespec="seconds"),
            "updated_at": now.isoformat(timespec="seconds"),
        }

        result = assign_action_bucket(
            [gap_sig], order, tasks=[overdue_task], current=now,
            user_id="USER-1", user_role="operator",
        )

        assert result["action_bucket"] == "DO_NOW", (
            f"Overdue waiting with INFORMATION_GAP only must go to DO_NOW, got {result['action_bucket']}"
        )
        assert "等待回复时间已过" in result["bucket_reasons"][0], (
            f"Expected overdue waiting reason, got {result['bucket_reasons']}"
        )

    def test_pipeline_overdue_waiting_info_gap_only_is_in_action_items(self, db_conn):
        """Full pipeline: overdue waiting + no real risks → must appear in action items."""
        _insert_order(
            db_conn,
            order_id="ORD-W5-PIPE",
            order_no="PO-W5-PIPE",
            status="ACTIVE",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=None,  # Missing → INFORMATION_GAP
            current_node=None,  # Missing → INFORMATION_GAP
            latest_supplier_commitment=None,  # Missing
            owner="USER-1",
            organization_id="ORG-A",
        )

        # Insert an overdue waiting task (promised_reply_at already passed)
        past_reply = (_now() - timedelta(hours=3)).isoformat(timespec="seconds")
        now_iso = _now().isoformat(timespec="seconds")
        db_conn.execute(
            text("INSERT INTO tasks "
            "(task_id, related_order_id, title, target, status, owner_user_id, "
            "waiting_on, promised_reply_at, risk_level, urgent, pending_confirmation, "
            "evidence_json, created_at, updated_at) "
            "VALUES (:task_id, :order_id, :title, 'factory', 'WAITING_EXTERNAL', 'USER-1', "
            "'工厂', :past_reply, 'none', 0, 0, '[]', :now_iso, :now_iso2)"),
            {
                "task_id": "TASK-W5-OVERDUE",
                "order_id": "ORD-W5-PIPE",
                "title": "跟进工厂确认交期",
                "past_reply": past_reply,
                "now_iso": now_iso,
                "now_iso2": now_iso,
            }
        )
        db_conn.commit()

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        items = result.get("items", [])
        # The order MUST appear in actionable items
        assert len(items) >= 1, (
            f"Overdue waiting order must appear in items, got items={items}, count={result.get('count')}"
        )

        item = items[0]
        assert item["action_bucket"] == "DO_NOW", (
            f"Pipeline bucket for overdue waiting must be DO_NOW, got {item['action_bucket']}"
        )
        assert result["risk_order_count"] >= 1, (
            f"risk_order_count must include the overdue waiting order, got {result['risk_order_count']}"
        )

    def test_pure_information_gap_without_action_trigger_stays_out_of_topn(self, db_conn):
        """Pure INFORMATION_GAP without any action trigger → stays out of Top N."""
        _insert_order(
            db_conn,
            order_id="ORD-W5-PURE",
            order_no="PO-W5-PURE",
            status="ACTIVE",
            requested_delivery_date=(_now() + timedelta(days=30)).date().isoformat(),
            current_progress=None,
            current_node=None,
            latest_supplier_commitment=None,
            owner="USER-1",
            organization_id="ORG-A",
        )

        # Insert a FUTURE waiting task (not overdue) → suppression, not DO_NOW
        future_reply = (_now() + timedelta(hours=5)).isoformat(timespec="seconds")
        now_iso = _now().isoformat(timespec="seconds")
        db_conn.execute(
            text("INSERT INTO tasks "
            "(task_id, related_order_id, title, target, status, owner_user_id, "
            "waiting_on, promised_reply_at, risk_level, urgent, pending_confirmation, "
            "evidence_json, created_at, updated_at) "
            "VALUES (:task_id, :order_id, :title, 'factory', 'WAITING_EXTERNAL', 'USER-1', "
            "'工厂', :future_reply, 'none', 0, 0, '[]', :now_iso, :now_iso2)"),
            {
                "task_id": "TASK-W5-FUTURE",
                "order_id": "ORD-W5-PURE",
                "title": "等待工厂回复",
                "future_reply": future_reply,
                "now_iso": now_iso,
                "now_iso2": now_iso,
            }
        )
        db_conn.commit()

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        # Pure info gap with future waiting → should NOT be in actionable items
        items = result.get("items", [])
        assert result["count"] == 0, (
            f"Pure info gap order with future waiting should NOT be in actionable items, got count={result['count']}"
        )
        # Should be tracked as info gap instead
        assert result["information_gap_order_count"] >= 1, (
            f"Info gap should be tracked separately, got {result['information_gap_order_count']}"
        )
        info_gaps = result.get("information_gaps", [])
        assert len(info_gaps) >= 1, "Should have information_gaps entries"

    def test_active_waiting_no_overdue_stays_suppressed(self, db_conn):
        """Existing active waiting (not overdue) must remain suppressed — no regression."""
        _insert_order(
            db_conn,
            order_id="ORD-W5-ACTIVE",
            order_no="PO-W5-ACTIVE",
            status="ACTIVE",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.3,
            current_node="备料中",
            owner="USER-1",
            organization_id="ORG-A",
        )

        # Active waiting: promised_reply_at in the future
        future_reply = (_now() + timedelta(hours=4)).isoformat(timespec="seconds")
        now_iso = _now().isoformat(timespec="seconds")
        db_conn.execute(
            text("INSERT INTO tasks "
            "(task_id, related_order_id, title, target, status, owner_user_id, "
            "waiting_on, promised_reply_at, risk_level, urgent, pending_confirmation, "
            "evidence_json, created_at, updated_at) "
            "VALUES (:task_id, :order_id, :title, 'factory', 'WAITING_EXTERNAL', 'USER-1', "
            "'工厂', :future_reply, 'none', 0, 0, '[]', :now_iso, :now_iso2)"),
            {
                "task_id": "TASK-W5-ACTIVE",
                "order_id": "ORD-W5-ACTIVE",
                "title": "等待工厂确认",
                "future_reply": future_reply,
                "now_iso": now_iso,
                "now_iso2": now_iso,
            }
        )
        db_conn.commit()

        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        items = result.get("items", [])
        for item in items:
            if item.get("action_bucket") == "WAITING_EXTERNAL":
                assert item.get("ranking_suppressed") is True, (
                    "Active waiting must remain suppressed"
                )


# ─── R5.1 Tests: DO_TODAY Reachability / Pre-Freeze Business Gate ─────

class TestR51DoTodayReachability:
    """R5.1: imminent non-overdue delivery must make DO_TODAY reachable."""

    @staticmethod
    def _delivery_signal(order_id: str, severity: str):
        from d7_risk_engine import build_risk_signal
        return build_risk_signal(
            order_id=order_id,
            order_no=f"PO-{order_id}",
            risk_type="DELIVERY_RISK",
            severity=severity,
            evidence=["客户正式交期临近"],
            organization_id="ORG-A",
        )

    @staticmethod
    def _order(order_id: str, due: datetime):
        return {
            "order_id": order_id,
            "order_no": f"PO-{order_id}",
            "status": "ACTIVE",
            "owner": "USER-1",
            "organization_id": "ORG-A",
            "requested_delivery_date": due.date().isoformat(),
            "effective_delivery_date": due.date().isoformat(),
            "current_progress": 0.9,
            "current_node": "生产收尾",
        }

    def test_delivery_due_today_goes_do_today(self):
        from d7_risk_engine import assign_action_bucket
        now = _now().replace(hour=9, minute=0, second=0, microsecond=0)
        order = self._order("R51-TODAY", now)
        result = assign_action_bucket(
            [self._delivery_signal("R51-TODAY", "HIGH")],
            order, [], current=now, user_id="USER-1", user_role="operator",
        )
        assert result["action_bucket"] == "DO_TODAY"
        assert any("今天" in r for r in result["bucket_reasons"])

    def test_delivery_due_tomorrow_goes_do_today(self):
        from d7_risk_engine import assign_action_bucket
        now = _now().replace(hour=9, minute=0, second=0, microsecond=0)
        due = now + timedelta(days=1)
        order = self._order("R51-TOMORROW", due)
        result = assign_action_bucket(
            [self._delivery_signal("R51-TOMORROW", "HIGH")],
            order, [], current=now, user_id="USER-1", user_role="operator",
        )
        assert result["action_bucket"] == "DO_TODAY"

    def test_delivery_due_in_2_days_goes_do_today(self):
        from d7_risk_engine import assign_action_bucket
        now = _now().replace(hour=9, minute=0, second=0, microsecond=0)
        due = now + timedelta(days=2)
        order = self._order("R51-D2", due)
        result = assign_action_bucket(
            [self._delivery_signal("R51-D2", "MEDIUM")],
            order, [], current=now, user_id="USER-1", user_role="operator",
        )
        assert result["action_bucket"] == "DO_TODAY"

    def test_delivery_due_in_3_days_goes_do_today(self):
        from d7_risk_engine import assign_action_bucket
        now = _now().replace(hour=9, minute=0, second=0, microsecond=0)
        due = now + timedelta(days=3)
        order = self._order("R51-D3", due)
        result = assign_action_bucket(
            [self._delivery_signal("R51-D3", "MEDIUM")],
            order, [], current=now, user_id="USER-1", user_role="operator",
        )
        assert result["action_bucket"] == "DO_TODAY"

    def test_delivery_due_in_4_days_stays_scheduled(self):
        from d7_risk_engine import assign_action_bucket
        now = _now().replace(hour=9, minute=0, second=0, microsecond=0)
        due = now + timedelta(days=4)
        order = self._order("R51-D4", due)
        result = assign_action_bucket(
            [self._delivery_signal("R51-D4", "MEDIUM")],
            order, [], current=now, user_id="USER-1", user_role="operator",
        )
        assert result["action_bucket"] == "SCHEDULED"

    def test_overdue_delivery_still_do_now(self):
        from d7_risk_engine import assign_action_bucket
        now = _now().replace(hour=9, minute=0, second=0, microsecond=0)
        due = now - timedelta(days=1)
        order = self._order("R51-OVERDUE", due)
        result = assign_action_bucket(
            [self._delivery_signal("R51-OVERDUE", "HIGH")],
            order, [], current=now, user_id="USER-1", user_role="operator",
        )
        assert result["action_bucket"] == "DO_NOW"

    def test_critical_delivery_still_escalates(self):
        from d7_risk_engine import assign_action_bucket
        now = _now().replace(hour=9, minute=0, second=0, microsecond=0)
        due = now - timedelta(days=8)
        order = self._order("R51-CRITICAL", due)
        result = assign_action_bucket(
            [self._delivery_signal("R51-CRITICAL", "CRITICAL")],
            order, [], current=now, user_id="USER-1", user_role="operator",
        )
        assert result["action_bucket"] == "ESCALATE"

    def test_active_waiting_still_suppresses_imminent_delivery(self):
        from d7_risk_engine import assign_action_bucket
        now = _now().replace(hour=9, minute=0, second=0, microsecond=0)
        due = now + timedelta(days=1)
        order = self._order("R51-WAIT", due)
        task = {
            "status": "WAITING_EXTERNAL",
            "waiting_on": "工厂",
            "promised_reply_at": (now + timedelta(hours=3)).isoformat(timespec="seconds"),
        }
        result = assign_action_bucket(
            [self._delivery_signal("R51-WAIT", "HIGH")],
            order, [task], current=now, user_id="USER-1", user_role="operator",
        )
        assert result["action_bucket"] == "WAITING_EXTERNAL"
        assert result["ranking_suppressed"] is True

    def test_pipeline_imminent_delivery_appears_as_do_today(self, db_conn):
        """Full pipeline uses actual risk assessment: due in 1 day => HIGH => DO_TODAY."""
        now = _now().replace(hour=9, minute=0, second=0, microsecond=0)
        _insert_order(
            db_conn,
            order_id="R51-PIPE",
            order_no="PO-R51-PIPE",
            status="ACTIVE",
            requested_delivery_date=(now + timedelta(days=1)).date().isoformat(),
            current_progress=0.9,
            current_node="生产收尾",
            latest_supplier_commitment=(now + timedelta(days=1)).date().isoformat(),
            owner="USER-1",
            organization_id="ORG-A",
        )
        identity = _make_current_identity("USER-1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity, current_time=now.isoformat())
        items = [i for i in result.get("items", []) if i.get("order_id") == "R51-PIPE"]
        assert len(items) == 1, f"Expected imminent order in actionable items, got {result.get('items')}"
        item = items[0]
        assert item["action_bucket"] == "DO_TODAY", item
        delivery = [s for s in item.get("risk_signals", []) if s.get("risk_type") == "DELIVERY_RISK"]
        assert delivery, item
        assert delivery[0].get("severity") == "HIGH", delivery[0]


# ─── R6 Tests: Role-Aware Queue Separation ─────────────────────────────

class TestR6RoleAwareQueue:
    """R6: Role-aware queue separation for Operator and Manager.
    
    Operator: only sees their own orders (my_action_items).
    Manager: sees assigned team orders (team_action_items) and 
             unassigned orders (unassigned_orders) separately.
    Unassigned orders MUST NOT compete with business risk orders in Top N.
    """

    def test_operator_only_sees_owned_orders(self, db_conn):
        """Operator (U1) can only see orders they own."""
        # Setup: ORG-A has 3 orders
        _insert_order(
            db_conn,
            order_id="O1",
            order_no="PO-O1",
            requested_delivery_date=(_now() - timedelta(days=2)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="U1",
            organization_id="ORG-A",
        )
        _insert_order(
            db_conn,
            order_id="O2",
            order_no="PO-O2",
            requested_delivery_date=(_now() - timedelta(days=3)).date().isoformat(),
            current_progress=0.2,
            current_node="生产中",
            owner="U2",
            organization_id="ORG-A",
        )
        _insert_order(
            db_conn,
            order_id="O3",
            order_no="PO-O3",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.4,
            current_node="备料中",
            owner=None,  # Unassigned
            organization_id="ORG-A",
        )

        # Operator: ORG-A / U1
        identity = _make_current_identity("U1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        my_items = result.get("my_action_items", [])
        my_order_ids = [item.get("order_id") for item in my_items]

        # Operator should ONLY see O1 (their own)
        assert "O1" in my_order_ids, f"Operator U1 must see O1 (owned), got {my_order_ids}"
        assert "O2" not in my_order_ids, f"Operator U1 must NOT see O2 (owned by U2), got {my_order_ids}"
        assert "O3" not in my_order_ids, f"Operator U1 must NOT see O3 (unassigned), got {my_order_ids}"

    def test_operator_cannot_see_unassigned_orders(self, db_conn):
        """owner=NULL must NOT appear in Operator output."""
        _insert_order(
            db_conn,
            order_id="O-ASSIGNED",
            order_no="PO-ASSIGNED",
            requested_delivery_date=(_now() - timedelta(days=2)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="U1",
            organization_id="ORG-A",
        )
        _insert_order(
            db_conn,
            order_id="O-UNASSIGNED",
            order_no="PO-UNASSIGNED",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.5,
            current_node="备料中",
            owner=None,
            organization_id="ORG-A",
        )

        identity = _make_current_identity("U1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        my_items = result.get("my_action_items", [])
        my_order_ids = [item.get("order_id") for item in my_items]

        assert "O-ASSIGNED" in my_order_ids
        assert "O-UNASSIGNED" not in my_order_ids, (
            "Unassigned order (owner=NULL) must NOT appear in Operator's my_action_items"
        )

    def test_manager_sees_assigned_team_orders(self, db_conn):
        """Manager can see all assigned orders in their organization."""
        # ORG-A: O1 owned by U1, O2 owned by U2
        _insert_order(
            db_conn,
            order_id="O1",
            order_no="PO-O1",
            requested_delivery_date=(_now() - timedelta(days=2)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="U1",
            organization_id="ORG-A",
        )
        _insert_order(
            db_conn,
            order_id="O2",
            order_no="PO-O2",
            requested_delivery_date=(_now() - timedelta(days=3)).date().isoformat(),
            current_progress=0.2,
            current_node="生产中",
            owner="U2",
            organization_id="ORG-A",
        )

        identity = _make_current_identity("MGR-1", "ORG-A", "manager")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        team_items = result.get("team_action_items", [])
        team_order_ids = [item.get("order_id") for item in team_items]

        # Manager should see both O1 and O2
        assert "O1" in team_order_ids, f"Manager must see O1 (assigned), got {team_order_ids}"
        assert "O2" in team_order_ids, f"Manager must see O2 (assigned), got {team_order_ids}"

    def test_manager_unassigned_orders_are_separate(self, db_conn):
        """Unassigned orders must go to unassigned_orders, NOT team_action_items."""
        # Setup: ORG-A has assigned + unassigned
        _insert_order(
            db_conn,
            order_id="O-ASSIGNED",
            order_no="PO-ASSIGNED",
            requested_delivery_date=(_now() - timedelta(days=2)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="U1",
            organization_id="ORG-A",
        )
        _insert_order(
            db_conn,
            order_id="O-UNASSIGNED",
            order_no="PO-UNASSIGNED",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.4,
            current_node="备料中",
            owner=None,
            organization_id="ORG-A",
        )

        identity = _make_current_identity("MGR-1", "ORG-A", "manager")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity)

        team_items = result.get("team_action_items", [])
        unassigned = result.get("unassigned_orders", [])

        team_order_ids = [item.get("order_id") for item in team_items]
        unassigned_order_ids = [item.get("order_id") for item in unassigned]

        # O-ASSIGNED in team, O-UNASSIGNED in unassigned
        assert "O-ASSIGNED" in team_order_ids, "Assigned order must be in team_action_items"
        assert "O-UNASSIGNED" not in team_order_ids, (
            "Unassigned order must NOT be in team_action_items"
        )
        assert "O-UNASSIGNED" in unassigned_order_ids, "Unassigned order must be in unassigned_orders"

    def test_unassigned_order_does_not_compete_with_business_risk_topn(self, db_conn):
        """OWNER_MISSING order must NOT compete with business risk orders in Top N."""
        # Setup: 3 orders with different risk types
        # 1. Unassigned order (OWNER_MISSING)
        _insert_order(
            db_conn,
            order_id="O-UNASSIGNED",
            order_no="PO-UNASSIGNED",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.5,
            current_node="备料中",
            owner=None,
            organization_id="ORG-A",
        )
        # 2. LOGISTICS_EXCEPTION + HIGH order
        _insert_order(
            db_conn,
            order_id="O-LOGISTICS",
            order_no="PO-LOGISTICS",
            status="ACTIVE",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.5,
            current_node="生产中",
            owner="U1",
            organization_id="ORG-A",
        )
        # Insert logistics exception for this order
        now_iso = _now().isoformat(timespec="seconds")
        db_conn.execute(
            text("INSERT INTO logistics_events "
            "(logistics_event_id, order_id, event_type, status, description, "
            "source, created_at, updated_at) "
            "VALUES (:eid, :oid, 'DELAY', 'DELAYED', '货代延迟', 'SYNTHETIC', :ts, :ts2)"),
            {
                "eid": "LE-R6-1",
                "oid": "O-LOGISTICS",
                "ts": now_iso,
                "ts2": now_iso,
            }
        )
        db_conn.commit()

        # 3. DELIVERY_RISK order (overdue)
        _insert_order(
            db_conn,
            order_id="O-DELIVERY",
            order_no="PO-DELIVERY",
            requested_delivery_date=(_now() - timedelta(days=5)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="U2",
            organization_id="ORG-A",
        )

        identity = _make_current_identity("MGR-1", "ORG-A", "manager")
        from d7_risk_engine import run_d7_pipeline
        result = run_d7_pipeline(db_conn, identity, top_n=7)

        team_items = result.get("team_action_items", [])
        unassigned = result.get("unassigned_orders", [])

        team_order_ids = [item.get("order_id") for item in team_items]
        unassigned_order_ids = [item.get("order_id") for item in unassigned]

        # Unassigned order must ONLY be in unassigned_orders
        assert "O-UNASSIGNED" not in team_order_ids, (
            "OWNER_MISSING order must NOT compete in team_action_items Top N"
        )
        assert "O-UNASSIGNED" in unassigned_order_ids, (
            "OWNER_MISSING order must be in unassigned_orders"
        )

        # Business risk orders should be in team_action_items
        # Check that O-LOGISTICS and O-DELIVERY are evaluated independently
        # They may or may not appear in Top 7 depending on scoring
        for item in team_items:
            risk_types = [s.get("risk_type") for s in item.get("risk_signals", [])]
            assert "OWNER_MISSING" not in risk_types, (
                "OWNER_MISSING must not be a risk type in team_action_items"
            )

    def test_cross_org_still_isolated(self, db_conn):
        """Cross-org isolation must be preserved for all queues."""
        # ORG-A orders
        _insert_order(
            db_conn,
            order_id="O-A-ASSIGNED",
            order_no="PO-A-ASSIGNED",
            requested_delivery_date=(_now() - timedelta(days=2)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="U1",
            organization_id="ORG-A",
        )
        _insert_order(
            db_conn,
            order_id="O-A-UNASSIGNED",
            order_no="PO-A-UNASSIGNED",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.4,
            current_node="备料中",
            owner=None,
            organization_id="ORG-A",
        )
        # ORG-B orders
        _insert_order(
            db_conn,
            order_id="O-B-ASSIGNED",
            order_no="PO-B-ASSIGNED",
            requested_delivery_date=(_now() - timedelta(days=3)).date().isoformat(),
            current_progress=0.2,
            current_node="生产中",
            owner="U3",
            organization_id="ORG-B",
        )
        _insert_order(
            db_conn,
            order_id="O-B-UNASSIGNED",
            order_no="PO-B-UNASSIGNED",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.5,
            current_node="备料中",
            owner=None,
            organization_id="ORG-B",
        )

        # Test Manager ORG-A
        identity_mgr = _make_current_identity("MGR-1", "ORG-A", "manager")
        from d7_risk_engine import run_d7_pipeline
        result_mgr = run_d7_pipeline(db_conn, identity_mgr)

        team_items = result_mgr.get("team_action_items", [])
        unassigned = result_mgr.get("unassigned_orders", [])
        info_gaps = result_mgr.get("information_gaps", [])

        for item in team_items + unassigned + info_gaps:
            oid = item.get("order_id")
            assert oid and oid.startswith("O-A-"), (
                f"Manager ORG-A must NOT see ORG-B order {oid}"
            )

        # Test Operator ORG-A/U1
        identity_op = _make_current_identity("U1", "ORG-A", "operator")
        result_op = run_d7_pipeline(db_conn, identity_op)

        my_items = result_op.get("my_action_items", [])
        for item in my_items:
            oid = item.get("order_id")
            assert oid and oid.startswith("O-A-"), (
                f"Operator ORG-A/U1 must NOT see ORG-B order {oid}"
            )

    def test_manager_and_operator_use_same_ranking_policy(self, db_conn):
        """Same assigned order must get same risk/bucket/score for both roles."""
        # Create an order owned by U1 with clear risk profile
        _insert_order(
            db_conn,
            order_id="O-SAME",
            order_no="PO-SAME",
            requested_delivery_date=(_now() - timedelta(days=3)).date().isoformat(),
            current_progress=0.3,
            current_node="生产中",
            owner="U1",
            organization_id="ORG-A",
        )

        # Run as Operator U1
        identity_op = _make_current_identity("U1", "ORG-A", "operator")
        from d7_risk_engine import run_d7_pipeline
        result_op = run_d7_pipeline(db_conn, identity_op)

        # Run as Manager
        identity_mgr = _make_current_identity("MGR-1", "ORG-A", "manager")
        result_mgr = run_d7_pipeline(db_conn, identity_mgr)

        # Find the order in both results
        op_items = result_op.get("my_action_items", [])
        mgr_items = result_mgr.get("team_action_items", [])

        op_order = next((i for i in op_items if i.get("order_id") == "O-SAME"), None)
        mgr_order = next((i for i in mgr_items if i.get("order_id") == "O-SAME"), None)

        assert op_order is not None, "Operator should see O-SAME"
        assert mgr_order is not None, "Manager should see O-SAME"

        # Compare core ranking fields — must be identical
        assert op_order["action_bucket"] == mgr_order["action_bucket"], (
            f"Action bucket differs: Operator={op_order['action_bucket']}, "
            f"Manager={mgr_order['action_bucket']}"
        )
        assert op_order["priority_score"] == mgr_order["priority_score"], (
            f"Priority score differs: Operator={op_order['priority_score']}, "
            f"Manager={mgr_order['priority_score']}"
        )
        assert op_order["severity"] == mgr_order["severity"], (
            f"Severity differs: Operator={op_order['severity']}, "
            f"Manager={mgr_order['severity']}"
        )

        # Compare risk signals
        op_signal_types = sorted(s.get("risk_type") for s in op_order.get("risk_signals", []))
        mgr_signal_types = sorted(s.get("risk_type") for s in mgr_order.get("risk_signals", []))
        assert op_signal_types == mgr_signal_types, (
            f"Risk signal types differ: Operator={op_signal_types}, "
            f"Manager={mgr_signal_types}"
        )

        # Compare priority reasons
        op_reasons = op_order.get("priority_reasons", [])
        mgr_reasons = mgr_order.get("priority_reasons", [])
        assert op_reasons == mgr_reasons, (
            f"Priority reasons differ: Operator={op_reasons}, "
            f"Manager={mgr_reasons}"
        )


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
