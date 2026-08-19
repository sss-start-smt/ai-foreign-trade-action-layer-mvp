"""
D8 Tests: Action Case — Risk/Action Judgment → Deterministic Action Intent
==========================================================================

Test Scenarios:
  S01  Same order + same intent reconciled 10 times → 1 ACTIVE case, same case_id
  S02  Same order, two different intents → 2 ACTIVE cases
  S03  Same intent bucket changes (DO_TODAY→DO_NOW) → same case, bucket updated
  S04  Same intent severity changes → no new case
  S05  Case CLOSED, same intent reappears → new ACTIVE case, old history kept
  S06  Risk disappears → NOT_OBSERVED, still ACTIVE, not auto-closed
  S07  Illegal FSM transition → rejected, state unchanged
  S08  SOURCE_CONFLICT → initial NEEDS_JUDGMENT
  S09  DELIVERY_RISK only → DELIVERY_RECOVERY
  S10  DELIVERY_RISK + LOGISTICS_EXCEPTION → only LOGISTICS_RECOVERY
  S11  DELIVERY_RISK + SUPPLIER_COMMITMENT_OVERDUE → supplier case, delivery suppressed
  S12  Operator can only see own cases
  S13  Manager sees team + unassigned OWNER_ASSIGNMENT
  S14  ORG-A cannot read/transition ORG-B cases
  S15  Concurrent duplicate create → DB unique constraint prevents 2 ACTIVE same-intent
"""

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

CN_TZ = timezone(timedelta(hours=8))


# ─── Database Setup ────────────────────────────────────────────────────

@pytest.fixture
def db_conn():
    """Create a fresh SQLite test database with core + D7 + D8 tables."""
    from sqlalchemy import create_engine

    tmpdir = tempfile.mkdtemp(prefix="floworder_d8test_")
    db_path = os.path.join(tmpdir, "test_d8.db")
    engine = create_engine(f"sqlite:///{db_path}")

    with engine.begin() as conn:
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

        conn.execute(text("""
            CREATE TABLE action_cases (
                action_case_id TEXT PRIMARY KEY,
                organization_id TEXT NOT NULL,
                order_id TEXT NOT NULL,
                action_intent_key TEXT NOT NULL,
                intent_type TEXT NOT NULL,
                stage TEXT NOT NULL,
                lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE',
                title TEXT,
                latest_action_bucket TEXT,
                latest_severity TEXT,
                latest_recommended_action TEXT,
                latest_evidence_json TEXT NOT NULL DEFAULT '[]',
                observation_status TEXT NOT NULL DEFAULT 'OBSERVED',
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_reconciled_at TEXT,
                source_policy_version TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                close_reason TEXT,
                closed_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(order_id) REFERENCES orders(order_id)
            )
        """))

        conn.execute(text("""
            CREATE UNIQUE INDEX uq_action_cases_active
            ON action_cases(organization_id, order_id, action_intent_key)
            WHERE lifecycle_status = 'ACTIVE'
        """))

        conn.execute(text("""
            CREATE INDEX idx_action_cases_org_order
            ON action_cases(organization_id, order_id, lifecycle_status)
        """))

        conn.execute(text("""
            CREATE INDEX idx_action_cases_stage
            ON action_cases(stage, lifecycle_status)
        """))

        conn.execute(text("""
            CREATE INDEX idx_action_cases_intent
            ON action_cases(action_intent_key, lifecycle_status)
        """))

        conn.commit()

    from database import _ConnectionWrapper
    raw_conn = engine.connect()
    wrapper = _ConnectionWrapper(raw_conn, is_sqlite=True)

    yield wrapper

    raw_conn.close()
    engine.dispose()


# ─── Helpers ──────────────────────────────────────────────────────────

def _make_order(**kwargs: Any) -> dict[str, Any]:
    defaults = {
        "order_id": "ORD-TEST",
        "order_no": "PO-TEST",
        "customer_name": "Test Customer",
        "product_name": "Test Product",
        "packaging_method": "Default",
        "requested_delivery_date": None,
        "latest_supplier_commitment": None,
        "current_progress": None,
        "current_node": None,
        "status": "ACTIVE",
        "owner": None,
        "organization_id": "ORG-A",
        "created_at": "2026-08-01T00:00:00+08:00",
        "updated_at": "2026-08-01T00:00:00+08:00",
    }
    defaults.update(kwargs)
    return defaults


def _insert_order(conn: Any, order: dict[str, Any]) -> None:
    columns = list(order.keys())
    placeholders = ",".join(["?"] * len(columns))
    conn.execute(
        f"INSERT INTO orders ({','.join(columns)}) VALUES ({placeholders})",
        tuple(order[col] for col in columns),
    )


def _make_risk_signal(**kwargs: Any) -> dict[str, Any]:
    defaults = {
        "risk_signal_id": f"RS-TEST-{uuid.uuid4().hex[:8].upper()}",
        "order_id": "",
        "order_no": None,
        "risk_type": "",
        "severity": "MEDIUM",
        "status": "OPEN",
        "detected_at": "2026-08-12T00:00:00+08:00",
        "evidence": [],
        "missing_information": [],
        "source_type": "LOCAL_RULE",
        "source_id": None,
        "source_modified_at": None,
        "fetched_at": "2026-08-12T00:00:00+08:00",
        "freshness": "FRESH",
        "rule_id": None,
        "explanation": "",
        "organization_id": None,
    }
    defaults.update(kwargs)
    return defaults


def _make_d7_item(**kwargs: Any) -> dict[str, Any]:
    defaults = {
        "order_id": "ORD-TEST",
        "order_no": "PO-TEST",
        "organization_id": "ORG-A",
        "risk_signals": [],
        "action_bucket": "DO_NOW",
        "bucket_reasons": [],
        "recommended_action": "立即处理",
        "evidence": [],
        "severity": "HIGH",
        "ranking_suppressed": False,
        "priority_score": 100.0,
    }
    defaults.update(kwargs)
    return defaults


def _make_d7_result(items: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
    defaults = {
        "policy_version": "D7_RISK_POLICY_V1",
        "generated_at": "2026-08-12T00:00:00+08:00",
        "scope": {
            "organization_id": "ORG-A",
            "user_id": "USER-1",
            "user_role": "operator",
        },
        "my_action_items": items,
        "team_action_items": [],
        "unassigned_orders": [],
        "items": items,
        "count": len(items),
        "information_gaps": [],
    }
    defaults.update(kwargs)
    return defaults


def _make_identity(user_id: str, org_id: str, role: str) -> dict[str, Any]:
    return {"user_id": user_id, "organization_id": org_id, "role": role}


# Import D8 module
import d8_action_case as d8


# ─── S01: Same order + same intent → 1 ACTIVE case, same case_id ──────

class TestS01SameIntentReuse:
    def test_10_reconciles_produce_one_case(self, db_conn):
        order = _make_order(
            order_id="ORD-S01", order_no="PO-S01",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S01", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["客户交期临近"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S01", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["客户交期临近"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        first_case_id = None
        for i in range(10):
            result = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
            assert result["status"] == "OK"
            expected_created = 1 if i == 0 else 0
            expected_reused = 0 if i == 0 else 1
            assert result["created_count"] == expected_created
            assert result["reused_count"] == expected_reused

            cases = d8.list_cases(
                db_conn, organization_id="ORG-A", order_id="ORD-S01"
            )
            active = [c for c in cases if c["lifecycle_status"] == "ACTIVE"]
            assert len(active) == 1

            if first_case_id is None:
                first_case_id = active[0]["action_case_id"]
            else:
                assert active[0]["action_case_id"] == first_case_id

    def test_case_id_is_deterministic(self, db_conn):
        order = _make_order(
            order_id="ORD-S01B", order_no="PO-S01B",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S01B", risk_type="SUPPLIER_COMMITMENT_OVERDUE",
            severity="HIGH", evidence=["供应商承诺过期"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S01B", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["供应商承诺过期"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        result = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        assert result["created_count"] == 1

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S01B")
        active = [c for c in cases if c["lifecycle_status"] == "ACTIVE"]
        assert len(active) == 1
        assert active[0]["action_intent_key"] == "v1:SUPPLIER_FOLLOWUP"


# ─── S02: Two different intents → 2 ACTIVE cases ────────────────────

class TestS02TwoIntents:
    def test_two_intents_create_two_cases(self, db_conn):
        order = _make_order(
            order_id="ORD-S02", order_no="PO-S02",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal1 = _make_risk_signal(
            order_id="ORD-S02", risk_type="CUSTOMER_CONFIRMATION_BLOCKING",
            severity="HIGH", evidence=["客户确认阻塞"], organization_id="ORG-A",
        )
        signal2 = _make_risk_signal(
            order_id="ORD-S02", risk_type="LOGISTICS_EXCEPTION",
            severity="CRITICAL", evidence=["物流异常"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S02", organization_id="ORG-A",
            risk_signals=[signal1, signal2],
            action_bucket="DO_NOW",
            evidence=["客户确认阻塞", "物流异常"],
            severity="CRITICAL",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        result = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        assert result["created_count"] == 2

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S02")
        active = [c for c in cases if c["lifecycle_status"] == "ACTIVE"]
        assert len(active) == 2

        intent_keys = {c["action_intent_key"] for c in active}
        assert "v1:CUSTOMER_CONFIRMATION" in intent_keys
        assert "v1:LOGISTICS_RECOVERY" in intent_keys


# ─── S03: Bucket change → same case, bucket updated ─────────────────

class TestS03BucketChange:
    def test_bucket_change_preserves_case(self, db_conn):
        order = _make_order(
            order_id="ORD-S03", order_no="PO-S03",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S03", risk_type="DELIVERY_RISK",
            severity="MEDIUM", evidence=["交期临近"], organization_id="ORG-A",
        )
        item_v1 = _make_d7_item(
            order_id="ORD-S03", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_TODAY",
            evidence=["交期临近"], severity="MEDIUM",
        )
        d7_result_v1 = _make_d7_result([item_v1])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        result1 = d8.reconcile_action_cases(db_conn, d7_result_v1, identity=identity)
        assert result1["created_count"] == 1

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S03")
        case_id = cases[0]["action_case_id"]
        assert cases[0]["latest_action_bucket"] == "DO_TODAY"

        item_v2 = _make_d7_item(
            order_id="ORD-S03", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期临近"], severity="MEDIUM",
        )
        d7_result_v2 = _make_d7_result([item_v2])

        result2 = d8.reconcile_action_cases(db_conn, d7_result_v2, identity=identity)
        assert result2["created_count"] == 0
        assert result2["reused_count"] == 1

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S03")
        assert len(cases) == 1
        assert cases[0]["action_case_id"] == case_id
        assert cases[0]["latest_action_bucket"] == "DO_NOW"
        assert cases[0]["stage"] == "READY_FOR_ACTION"


# ─── S04: Severity change → no new case ──────────────────────────────

class TestS04SeverityChange:
    def test_severity_change_no_new_case(self, db_conn):
        order = _make_order(
            order_id="ORD-S04", order_no="PO-S04",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal_low = _make_risk_signal(
            order_id="ORD-S04", risk_type="DELIVERY_RISK",
            severity="LOW", evidence=["轻微风险"], organization_id="ORG-A",
        )
        item_v1 = _make_d7_item(
            order_id="ORD-S04", organization_id="ORG-A",
            risk_signals=[signal_low], action_bucket="SCHEDULED",
            evidence=["轻微风险"], severity="LOW",
        )
        d7_result_v1 = _make_d7_result([item_v1])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result_v1, identity=identity)
        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S04")
        assert len(cases) == 1
        case_id = cases[0]["action_case_id"]
        assert cases[0]["latest_severity"] == "LOW"

        signal_high = _make_risk_signal(
            order_id="ORD-S04", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["严重风险"], organization_id="ORG-A",
        )
        item_v2 = _make_d7_item(
            order_id="ORD-S04", organization_id="ORG-A",
            risk_signals=[signal_high], action_bucket="DO_NOW",
            evidence=["严重风险"], severity="HIGH",
        )
        d7_result_v2 = _make_d7_result([item_v2])

        result2 = d8.reconcile_action_cases(db_conn, d7_result_v2, identity=identity)
        assert result2["created_count"] == 0
        assert result2["reused_count"] == 1

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S04")
        assert len(cases) == 1
        assert cases[0]["action_case_id"] == case_id
        assert cases[0]["latest_severity"] == "HIGH"


# ─── S05: CLOSED case, same intent reappears → new ACTIVE case ────────

class TestS05ClosedReopen:
    def test_closed_then_new_case(self, db_conn):
        order = _make_order(
            order_id="ORD-S05", order_no="PO-S05",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S05", risk_type="OWNER_MISSING",
            severity="HIGH", evidence=["负责人缺失"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S05", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="ESCALATE",
            evidence=["负责人缺失"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        result1 = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        assert result1["created_count"] == 1

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S05")
        assert len(cases) == 1
        case_id_v1 = cases[0]["action_case_id"]
        assert cases[0]["lifecycle_status"] == "ACTIVE"

        d8.transition_action_case(
            db_conn, case_id_v1, "CLOSED",
            close_reason="RESOLVED", identity=identity,
        )
        closed = d8.get_case_by_id(db_conn, case_id_v1)
        assert closed["lifecycle_status"] == "CLOSED"

        result2 = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        assert result2["created_count"] == 1

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S05")
        assert len(cases) == 2

        active = [c for c in cases if c["lifecycle_status"] == "ACTIVE"]
        assert len(active) == 1
        assert active[0]["action_case_id"] != case_id_v1
        assert active[0]["action_intent_key"] == "v1:OWNER_ASSIGNMENT"

        closed_cases = [c for c in cases if c["lifecycle_status"] == "CLOSED"]
        assert len(closed_cases) == 1
        assert closed_cases[0]["action_case_id"] == case_id_v1


# ─── S06: Risk disappears → NOT_OBSERVED, still ACTIVE ───────────────

class TestS06RiskDisappears:
    def test_risk_disappears_not_closed(self, db_conn):
        order = _make_order(
            order_id="ORD-S06", order_no="PO-S06",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S06", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S06", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result(
            [item],
            action_case_observations=[item],
        )
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S06")
        assert len(cases) == 1
        assert cases[0]["observation_status"] == "OBSERVED"

        item_no_risk = _make_d7_item(
            order_id="ORD-S06", organization_id="ORG-A",
            risk_signals=[], action_bucket="SCHEDULED",
            evidence=[], severity="LOW",
        )
        d7_result_empty = _make_d7_result(
            [item_no_risk],
            action_case_observations=[item_no_risk],
        )

        d8.reconcile_action_cases(db_conn, d7_result_empty, identity=identity)

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S06")
        assert len(cases) == 1
        assert cases[0]["lifecycle_status"] == "ACTIVE"
        assert cases[0]["observation_status"] == "NOT_OBSERVED"


# ─── S07: Illegal FSM transition → rejected ───────────────────────────

class TestS07IllegalTransition:
    def test_illegal_transition_rejected(self, db_conn):
        order = _make_order(
            order_id="ORD-S07", order_no="PO-S07",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S07", risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH", evidence=["物流异常"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S07", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["物流异常"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S07")
        case_id = cases[0]["action_case_id"]
        assert cases[0]["stage"] == "READY_FOR_ACTION"
        assert cases[0]["version"] == 1

        with pytest.raises(d8.ActionCaseFSMError) as exc_info:
            d8.transition_action_case(
                db_conn, case_id, "WAITING_RESULT", identity=identity,
            )
        assert exc_info.value.current_stage == "READY_FOR_ACTION"
        assert exc_info.value.target_stage == "WAITING_RESULT"

        case_after = d8.get_case_by_id(db_conn, case_id)
        assert case_after["stage"] == "READY_FOR_ACTION"
        assert case_after["version"] == 1

    def test_closed_cannot_transition(self, db_conn):
        order = _make_order(
            order_id="ORD-S07B", order_no="PO-S07B",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S07B", risk_type="SUPPLIER_COMMITMENT_OVERDUE",
            severity="HIGH", evidence=["供应商承诺过期"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S07B", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["供应商承诺过期"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S07B")
        case_id = cases[0]["action_case_id"]

        d8.transition_action_case(
            db_conn, case_id, "CLOSED",
            close_reason="RESOLVED", identity=identity,
        )

        with pytest.raises(d8.ActionCaseFSMError):
            d8.transition_action_case(
                db_conn, case_id, "IN_PROGRESS", identity=identity,
            )

    def test_invalid_close_reason_rejected(self, db_conn):
        order = _make_order(
            order_id="ORD-S07C", order_no="PO-S07C",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S07C", risk_type="INFORMATION_GAP",
            severity="LOW", evidence=["信息缺失"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S07C", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="SCHEDULED",
            evidence=["信息缺失"], severity="LOW",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S07C")
        case_id = cases[0]["action_case_id"]

        with pytest.raises(ValueError, match="close_reason is required"):
            d8.transition_action_case(
                db_conn, case_id, "CLOSED", identity=identity,
            )

        with pytest.raises(ValueError, match="Invalid close_reason"):
            d8.transition_action_case(
                db_conn, case_id, "CLOSED",
                close_reason="NOT_A_REASON", identity=identity,
            )


# ─── S08: SOURCE_CONFLICT → initial NEEDS_JUDGMENT ────────────────────

class TestS08SourceConflictStage:
    def test_source_conflict_needs_judgment(self, db_conn):
        order = _make_order(
            order_id="ORD-S08", order_no="PO-S08",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S08", risk_type="SOURCE_CONFLICT",
            severity="MEDIUM", evidence=["来源冲突"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S08", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="NEEDS_CONFIRMATION",
            evidence=["来源冲突"], severity="MEDIUM",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S08")
        assert len(cases) == 1
        assert cases[0]["intent_type"] == "FACT_CONFLICT_RESOLUTION"
        assert cases[0]["stage"] == "NEEDS_JUDGMENT"
        assert cases[0]["action_intent_key"] == "v1:FACT_CONFLICT_RESOLUTION"


# ─── S09: DELIVERY_RISK only → DELIVERY_RECOVERY ──────────────────────

class TestS09DeliveryOnly:
    def test_delivery_only_creates_delivery_recovery(self, db_conn):
        order = _make_order(
            order_id="ORD-S09", order_no="PO-S09",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S09", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S09", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S09")
        assert len(cases) == 1
        assert cases[0]["action_intent_key"] == "v1:DELIVERY_RECOVERY"
        assert cases[0]["intent_type"] == "DELIVERY_RECOVERY"


# ─── S10: DELIVERY_RISK + LOGISTICS_EXCEPTION → only LOGISTICS_RECOVERY

class TestS10DeliverySuppressedByLogistics:
    def test_delivery_suppressed_by_logistics(self, db_conn):
        order = _make_order(
            order_id="ORD-S10", order_no="PO-S10",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        sig_delivery = _make_risk_signal(
            order_id="ORD-S10", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        sig_logistics = _make_risk_signal(
            order_id="ORD-S10", risk_type="LOGISTICS_EXCEPTION",
            severity="CRITICAL", evidence=["物流异常"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S10", organization_id="ORG-A",
            risk_signals=[sig_delivery, sig_logistics],
            action_bucket="DO_NOW",
            evidence=["交期风险", "物流异常"],
            severity="CRITICAL",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S10")
        assert len(cases) == 1
        assert cases[0]["action_intent_key"] == "v1:LOGISTICS_RECOVERY"

        intent_keys = {c["action_intent_key"] for c in cases}
        assert "v1:DELIVERY_RECOVERY" not in intent_keys

    def test_delivery_evidence_preserved(self, db_conn):
        order = _make_order(
            order_id="ORD-S10B", order_no="PO-S10B",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        sig_delivery = _make_risk_signal(
            order_id="ORD-S10B", risk_type="DELIVERY_RISK",
            severity="MEDIUM", evidence=["交期临近"], organization_id="ORG-A",
        )
        sig_logistics = _make_risk_signal(
            order_id="ORD-S10B", risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH", evidence=["物流延迟"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S10B", organization_id="ORG-A",
            risk_signals=[sig_delivery, sig_logistics],
            action_bucket="DO_NOW",
            evidence=["交期临近", "物流延迟"],
            severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S10B")
        assert len(cases) == 1

        evidence = json.loads(cases[0]["latest_evidence_json"])
        assert "交期临近" in evidence


# ─── S11: DELIVERY_RISK + SUPPLIER_COMMITMENT_OVERDUE → supplier case ──

class TestS11DeliverySuppressedBySupplier:
    def test_delivery_suppressed_by_supplier(self, db_conn):
        order = _make_order(
            order_id="ORD-S11", order_no="PO-S11",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        sig_delivery = _make_risk_signal(
            order_id="ORD-S11", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        sig_supplier = _make_risk_signal(
            order_id="ORD-S11", risk_type="SUPPLIER_COMMITMENT_OVERDUE",
            severity="HIGH", evidence=["供应商承诺过期"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S11", organization_id="ORG-A",
            risk_signals=[sig_delivery, sig_supplier],
            action_bucket="DO_NOW",
            evidence=["交期风险", "供应商承诺过期"],
            severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S11")
        assert len(cases) == 1
        assert cases[0]["action_intent_key"] == "v1:SUPPLIER_FOLLOWUP"

        intent_keys = {c["action_intent_key"] for c in cases}
        assert "v1:DELIVERY_RECOVERY" not in intent_keys


# ─── S12: Operator only sees own cases ───────────────────────────────

class TestS12OperatorVisibility:
    def test_operator_sees_own_cases(self, db_conn):
        order1 = _make_order(
            order_id="ORD-S12A", order_no="PO-S12A",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order1)

        order2 = _make_order(
            order_id="ORD-S12B", order_no="PO-S12B",
            owner="USER-2", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order2)

        # Each operator reconciles their OWN order
        # USER-1 reconciles ORD-S12A (owner=USER-1)
        signal1 = _make_risk_signal(
            order_id="ORD-S12A", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item1 = _make_d7_item(
            order_id="ORD-S12A", organization_id="ORG-A",
            risk_signals=[signal1], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result1 = _make_d7_result([item1])
        identity_user1 = _make_identity("USER-1", "ORG-A", "operator")
        d8.reconcile_action_cases(db_conn, d7_result1, identity=identity_user1)

        # USER-2 reconciles ORD-S12B (owner=USER-2)
        signal2 = _make_risk_signal(
            order_id="ORD-S12B", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item2 = _make_d7_item(
            order_id="ORD-S12B", organization_id="ORG-A",
            risk_signals=[signal2], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result2 = _make_d7_result([item2])
        identity_user2 = _make_identity("USER-2", "ORG-A", "operator")
        d8.reconcile_action_cases(db_conn, d7_result2, identity=identity_user2)

        # Each operator only sees their own cases
        my_cases = d8.list_my_cases(db_conn, identity_user1)
        assert len(my_cases) == 1
        assert my_cases[0]["order_id"] == "ORD-S12A"

        my_cases2 = d8.list_my_cases(db_conn, identity_user2)
        assert len(my_cases2) == 1
        assert my_cases2[0]["order_id"] == "ORD-S12B"


# ─── S13: Manager sees team + unassigned OWNER_ASSIGNMENT ────────────

class TestS13ManagerVisibility:
    def test_manager_sees_all_in_org(self, db_conn):
        order1 = _make_order(
            order_id="ORD-S13A", order_no="PO-S13A",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order1)

        order2 = _make_order(
            order_id="ORD-S13B", order_no="PO-S13B",
            owner="USER-2", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order2)

        order3 = _make_order(
            order_id="ORD-S13C", order_no="PO-S13C",
            owner=None, organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order3)

        signal = _make_risk_signal(
            order_id="ORD-S13C", risk_type="OWNER_MISSING",
            severity="HIGH", evidence=["负责人缺失"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S13C", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="ESCALATE",
            evidence=["负责人缺失"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("MANAGER-A", "ORG-A", "manager")
        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        manager_cases = d8.list_my_cases(db_conn, identity)
        assert len(manager_cases) >= 1

        owner_assignment_cases = [
            c for c in manager_cases
            if c["action_intent_key"] == "v1:OWNER_ASSIGNMENT"
        ]
        assert len(owner_assignment_cases) >= 1


# ─── S14: Cross-org isolation ────────────────────────────────────────

class TestS14CrossOrgIsolation:
    def test_org_a_cannot_read_org_b_cases(self, db_conn):
        order_b = _make_order(
            order_id="ORD-S14B", order_no="PO-S14B",
            owner="USER-B1", organization_id="ORG-B",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order_b)

        signal = _make_risk_signal(
            order_id="ORD-S14B", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-B",
        )
        item = _make_d7_item(
            order_id="ORD-S14B", organization_id="ORG-B",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result(
            [item],
            scope={"organization_id": "ORG-B", "user_id": "USER-B1", "user_role": "operator"},
        )
        identity_b = _make_identity("USER-B1", "ORG-B", "operator")
        d8.reconcile_action_cases(db_conn, d7_result, identity=identity_b)

        identity_a_operator = _make_identity("USER-1", "ORG-A", "operator")
        cases_a = d8.list_cases(db_conn, organization_id="ORG-A")
        assert len(cases_a) == 0

        identity_a_manager = _make_identity("MANAGER-A", "ORG-A", "manager")
        cases_a_mgr = d8.list_cases(db_conn, organization_id="ORG-A")
        assert len(cases_a_mgr) == 0

        org_b_cases = d8.list_cases(db_conn, organization_id="ORG-B")
        assert len(org_b_cases) == 1
        case_id_b = org_b_cases[0]["action_case_id"]

        fetched = d8.get_my_case(db_conn, identity_a_operator, case_id_b)
        assert fetched is None

        fetched_mgr = d8.get_my_case(db_conn, identity_a_manager, case_id_b)
        assert fetched_mgr is None

    def test_cross_org_transition_blocked(self, db_conn):
        order_b = _make_order(
            order_id="ORD-S14C", order_no="PO-S14C",
            owner="USER-B1", organization_id="ORG-B",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order_b)

        signal = _make_risk_signal(
            order_id="ORD-S14C", risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH", evidence=["物流异常"], organization_id="ORG-B",
        )
        item = _make_d7_item(
            order_id="ORD-S14C", organization_id="ORG-B",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["物流异常"], severity="HIGH",
        )
        d7_result = _make_d7_result(
            [item],
            scope={"organization_id": "ORG-B", "user_id": "USER-B1", "user_role": "operator"},
        )
        identity_b = _make_identity("USER-B1", "ORG-B", "operator")
        d8.reconcile_action_cases(db_conn, d7_result, identity=identity_b)

        org_b_cases = d8.list_cases(db_conn, organization_id="ORG-B")
        case_id = org_b_cases[0]["action_case_id"]
        case_before = d8.get_case_by_id(db_conn, case_id)
        assert case_before["stage"] == "READY_FOR_ACTION"
        assert case_before["version"] == 1

        # ACTUAL cross-org transition attempt with ORG-A identity
        identity_a = _make_identity("MANAGER-A", "ORG-A", "manager")
        with pytest.raises(d8.ActionCaseAuthError):
            d8.transition_action_case(
                db_conn, case_id, "IN_PROGRESS", identity=identity_a,
            )

        # Verify case unchanged after rejection
        case_after = d8.get_case_by_id(db_conn, case_id)
        assert case_after["stage"] == "READY_FOR_ACTION"
        assert case_after["version"] == 1

        # Verify cross-org read still blocked
        fetched = d8.get_my_case(db_conn, identity_a, case_id)
        assert fetched is None


# ─── S15: DB unique constraint prevents duplicate ACTIVE same-intent ──

class TestS15UniqueConstraint:
    def test_duplicate_create_blocked_by_db(self, db_conn):
        order = _make_order(
            order_id="ORD-S15", order_no="PO-S15",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S15", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S15", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        result1 = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        assert result1["created_count"] == 1

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S15")
        assert len(cases) == 1

        with pytest.raises(Exception):
            d8.create_action_case(
                db_conn,
                organization_id="ORG-A",
                order_id="ORD-S15",
                action_intent_key="v1:DELIVERY_RECOVERY",
                intent_type="DELIVERY_RECOVERY",
                stage="READY_FOR_ACTION",
            )

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S15")
        active = [c for c in cases if c["lifecycle_status"] == "ACTIVE"]
        assert len(active) == 1

    def test_closed_allows_new_active(self, db_conn):
        order = _make_order(
            order_id="ORD-S15B", order_no="PO-S15B",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-S15B", risk_type="SUPPLIER_COMMITMENT_OVERDUE",
            severity="HIGH", evidence=["供应商承诺过期"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-S15B", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["供应商承诺过期"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S15B")
        case_id_v1 = cases[0]["action_case_id"]

        d8.transition_action_case(
            db_conn, case_id_v1, "CLOSED",
            close_reason="RESOLVED", identity=identity,
        )

        new_case = d8.create_action_case(
            db_conn,
            organization_id="ORG-A",
            order_id="ORD-S15B",
            action_intent_key="v1:SUPPLIER_FOLLOWUP",
            intent_type="SUPPLIER_FOLLOWUP",
            stage="READY_FOR_ACTION",
        )
        assert new_case["action_case_id"] != case_id_v1
        assert new_case["lifecycle_status"] == "ACTIVE"

        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-S15B")
        active = [c for c in cases if c["lifecycle_status"] == "ACTIVE"]
        closed = [c for c in cases if c["lifecycle_status"] == "CLOSED"]
        assert len(active) == 1
        assert len(closed) == 1


# ─── Additional Edge Case Tests ───────────────────────────────────────

class TestFSMCompleteFlow:
    def test_full_fsm_lifecycle(self, db_conn):
        order = _make_order(
            order_id="ORD-FSM", order_no="PO-FSM",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-FSM", risk_type="CUSTOMER_CONFIRMATION_BLOCKING",
            severity="HIGH", evidence=["客户确认阻塞"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-FSM", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="NEEDS_CONFIRMATION",
            evidence=["客户确认阻塞"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-FSM")
        case_id = cases[0]["action_case_id"]
        assert cases[0]["stage"] == "READY_FOR_ACTION"

        d8.transition_action_case(db_conn, case_id, "IN_PROGRESS", identity=identity)
        case = d8.get_case_by_id(db_conn, case_id)
        assert case["stage"] == "IN_PROGRESS"
        assert case["version"] == 2

        d8.transition_action_case(db_conn, case_id, "WAITING_RESULT", identity=identity)
        case = d8.get_case_by_id(db_conn, case_id)
        assert case["stage"] == "WAITING_RESULT"
        assert case["version"] == 3

        d8.transition_action_case(db_conn, case_id, "RESUMED_OR_ESCALATED", identity=identity)
        case = d8.get_case_by_id(db_conn, case_id)
        assert case["stage"] == "RESUMED_OR_ESCALATED"
        assert case["version"] == 4

        d8.transition_action_case(
            db_conn, case_id, "CLOSED",
            close_reason="RESOLVED", identity=identity,
        )
        case = d8.get_case_by_id(db_conn, case_id)
        assert case["stage"] == "CLOSED"
        assert case["lifecycle_status"] == "CLOSED"
        assert case["close_reason"] == "RESOLVED"
        assert case["version"] == 5
        assert case["closed_at"] is not None

    def test_resumed_can_go_back_to_ready(self, db_conn):
        order = _make_order(
            order_id="ORD-FSM2", order_no="PO-FSM2",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-FSM2", risk_type="INFORMATION_GAP",
            severity="LOW", evidence=["信息缺失"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-FSM2", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="SCHEDULED",
            evidence=["信息缺失"], severity="LOW",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-FSM2")
        case_id = cases[0]["action_case_id"]
        assert cases[0]["stage"] == "NEEDS_JUDGMENT"

        d8.transition_action_case(db_conn, case_id, "READY_FOR_ACTION", identity=identity)
        d8.transition_action_case(db_conn, case_id, "IN_PROGRESS", identity=identity)
        d8.transition_action_case(db_conn, case_id, "RESUMED_OR_ESCALATED", identity=identity)
        d8.transition_action_case(db_conn, case_id, "READY_FOR_ACTION", identity=identity)
        case = d8.get_case_by_id(db_conn, case_id)
        assert case["stage"] == "READY_FOR_ACTION"


class TestIntentKeyDeterminism:
    def test_intent_key_format(self, db_conn):
        test_cases = [
            ("DELIVERY_RISK", "v1:DELIVERY_RECOVERY"),
            ("SUPPLIER_COMMITMENT_OVERDUE", "v1:SUPPLIER_FOLLOWUP"),
            ("CUSTOMER_CONFIRMATION_BLOCKING", "v1:CUSTOMER_CONFIRMATION"),
            ("SOURCE_CONFLICT", "v1:FACT_CONFLICT_RESOLUTION"),
            ("OWNER_MISSING", "v1:OWNER_ASSIGNMENT"),
            ("LOGISTICS_EXCEPTION", "v1:LOGISTICS_RECOVERY"),
            ("INFORMATION_GAP", "v1:INFORMATION_COMPLETION"),
        ]

        for risk_type, expected_key in test_cases:
            order = _make_order(
                order_id=f"ORD-KEY-{risk_type}",
                order_no=f"PO-KEY-{risk_type}",
                owner="USER-1", organization_id="ORG-A",
                requested_delivery_date="2026-08-15", current_progress=0.3,
            )
            _insert_order(db_conn, order)

            signal = _make_risk_signal(
                order_id=order["order_id"], risk_type=risk_type,
                severity="HIGH", evidence=["test"], organization_id="ORG-A",
            )
            item = _make_d7_item(
                order_id=order["order_id"], organization_id="ORG-A",
                risk_signals=[signal], action_bucket="DO_NOW",
                evidence=["test"], severity="HIGH",
            )
            d7_result = _make_d7_result([item])
            identity = _make_identity("USER-1", "ORG-A", "operator")

            d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
            cases = d8.list_cases(
                db_conn, organization_id="ORG-A", order_id=order["order_id"]
            )
            assert len(cases) == 1
            assert cases[0]["action_intent_key"] == expected_key


class TestVersionIncrement:
    def test_version_increments_on_transition(self, db_conn):
        order = _make_order(
            order_id="ORD-VER", order_no="PO-VER",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-VER", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-VER", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        cases = d8.list_cases(db_conn, organization_id="ORG-A", order_id="ORD-VER")
        case_id = cases[0]["action_case_id"]
        assert cases[0]["version"] == 1

        d8.transition_action_case(db_conn, case_id, "IN_PROGRESS", identity=identity)
        case = d8.get_case_by_id(db_conn, case_id)
        assert case["version"] == 2

        d8.transition_action_case(
            db_conn, case_id, "CLOSED",
            close_reason="RESOLVED", identity=identity,
        )
        case = d8.get_case_by_id(db_conn, case_id)
        assert case["version"] == 3


class TestRunD8Pipeline:
    def test_full_pipeline(self, db_conn):
        order = _make_order(
            order_id="ORD-PIPE", order_no="PO-PIPE",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15",
            current_progress=0.3, status="ACTIVE",
        )
        _insert_order(db_conn, order)

        identity = _make_identity("USER-1", "ORG-A", "operator")

        result = d8.run_d8_pipeline(db_conn, identity)
        assert "d7_result" in result
        assert "d8_result" in result

        d7 = result["d7_result"]
        d8_part = result["d8_result"]

        assert d7["policy_version"] == "D7_RISK_POLICY_V1"
        assert d8_part["status"] == "OK"
        assert "created_count" in d8_part
        assert "reused_count" in d8_part


class TestDeterministicReconciliation:
    def test_same_input_same_output(self, db_conn):
        order = _make_order(
            order_id="ORD-DET", order_no="PO-DET",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-DET", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-DET", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        result1 = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        result2 = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        assert result1["created_count"] == 1
        assert result2["created_count"] == 0
        assert result2["reused_count"] == 1

        case_id_1 = result1["results"][0]["action_case_id"]
        case_id_2 = result2["results"][0]["action_case_id"]
        assert case_id_1 == case_id_2


class TestSourcePolicyVersion:
    def test_source_policy_version_stored(self, db_conn):
        order = _make_order(
            order_id="ORD-VER-POL", order_no="PO-VER-POL",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-VER-POL", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["test"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-VER-POL", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["test"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        result = d8.reconcile_action_cases(
            db_conn, d7_result, identity=identity,
            policy_version="CUSTOM_POLICY_V2",
        )
        assert result["created_count"] == 1

        cases = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-VER-POL"
        )
        assert cases[0]["source_policy_version"] == "CUSTOM_POLICY_V2"


# ═══════════════════════════════════════════════════════════════════════
# Adversarial Tests (A01-A07) — ChatGPT Review Round 2
# ═══════════════════════════════════════════════════════════════════════


# ─── A01: Cross-org actual transition attack ──────────────────────────

class TestA01CrossOrgTransitionAttack:
    def test_org_a_manager_cannot_transition_org_b_case(self, db_conn):
        """ORG-A manager attempts to transition ORG-B case → REJECT."""
        order_b = _make_order(
            order_id="ORD-A01", order_no="PO-A01",
            owner="USER-B1", organization_id="ORG-B",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order_b)

        signal = _make_risk_signal(
            order_id="ORD-A01", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-B",
        )
        item = _make_d7_item(
            order_id="ORD-A01", organization_id="ORG-B",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result(
            [item],
            scope={"organization_id": "ORG-B", "user_id": "USER-B1", "user_role": "operator"},
        )
        identity_b = _make_identity("USER-B1", "ORG-B", "operator")
        d8.reconcile_action_cases(db_conn, d7_result, identity=identity_b)

        case_id = d8.list_cases(db_conn, organization_id="ORG-B")[0]["action_case_id"]
        case_before = d8.get_case_by_id(db_conn, case_id)
        assert case_before["stage"] == "READY_FOR_ACTION"
        assert case_before["version"] == 1

        identity_a_mgr = _make_identity("MANAGER-A", "ORG-A", "manager")
        with pytest.raises(d8.ActionCaseAuthError) as exc_info:
            d8.transition_action_case(
                db_conn, case_id, "IN_PROGRESS", identity=identity_a_mgr,
            )
        assert "Cross-organization" in str(exc_info.value)

        # DB unchanged
        case_after = d8.get_case_by_id(db_conn, case_id)
        assert case_after["stage"] == "READY_FOR_ACTION"
        assert case_after["version"] == 1
        assert case_after["lifecycle_status"] == "ACTIVE"

    def test_org_a_operator_cannot_transition_org_b_case(self, db_conn):
        """ORG-A operator attempts to transition ORG-B case → REJECT."""
        order_b = _make_order(
            order_id="ORD-A01B", order_no="PO-A01B",
            owner="USER-B1", organization_id="ORG-B",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order_b)

        signal = _make_risk_signal(
            order_id="ORD-A01B", risk_type="SUPPLIER_COMMITMENT_OVERDUE",
            severity="HIGH", evidence=["供应商承诺过期"], organization_id="ORG-B",
        )
        item = _make_d7_item(
            order_id="ORD-A01B", organization_id="ORG-B",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["供应商承诺过期"], severity="HIGH",
        )
        d7_result = _make_d7_result(
            [item],
            scope={"organization_id": "ORG-B", "user_id": "USER-B1", "user_role": "operator"},
        )
        identity_b = _make_identity("USER-B1", "ORG-B", "operator")
        d8.reconcile_action_cases(db_conn, d7_result, identity=identity_b)

        case_id = d8.list_cases(db_conn, organization_id="ORG-B")[0]["action_case_id"]

        identity_a = _make_identity("USER-1", "ORG-A", "operator")
        with pytest.raises(d8.ActionCaseAuthError):
            d8.transition_action_case(
                db_conn, case_id, "IN_PROGRESS", identity=identity_a,
            )

        case_after = d8.get_case_by_id(db_conn, case_id)
        assert case_after["stage"] == "READY_FOR_ACTION"
        assert case_after["version"] == 1


# ─── A02: Same-org other-operator transition attack ──────────────────

class TestA02SameOrgOtherOperatorAttack:
    def test_operator_cannot_transition_other_operator_case(self, db_conn):
        """USER-2 operator attempts to transition USER-1's order case → REJECT."""
        order = _make_order(
            order_id="ORD-A02", order_no="PO-A02",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-A02", risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH", evidence=["物流异常"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-A02", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["物流异常"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity_user1 = _make_identity("USER-1", "ORG-A", "operator")
        d8.reconcile_action_cases(db_conn, d7_result, identity=identity_user1)

        case_id = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-A02"
        )[0]["action_case_id"]

        # USER-2 (different operator) tries to transition USER-1's case
        identity_user2 = _make_identity("USER-2", "ORG-A", "operator")
        with pytest.raises(d8.ActionCaseAuthError) as exc_info:
            d8.transition_action_case(
                db_conn, case_id, "IN_PROGRESS", identity=identity_user2,
            )
        assert "cannot transition case for order owned by" in str(exc_info.value)

        # DB unchanged
        case_after = d8.get_case_by_id(db_conn, case_id)
        assert case_after["stage"] == "READY_FOR_ACTION"
        assert case_after["version"] == 1

    def test_operator_cannot_transition_null_owner_case(self, db_conn):
        """Operator attempts to transition owner=NULL case → REJECT."""
        order = _make_order(
            order_id="ORD-A02B", order_no="PO-A02B",
            owner=None, organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-A02B", risk_type="OWNER_MISSING",
            severity="HIGH", evidence=["负责人缺失"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-A02B", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="ESCALATE",
            evidence=["负责人缺失"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])

        # Manager creates the case (managers can access all orders)
        identity_mgr = _make_identity("MGR-1", "ORG-A", "manager")
        d8.reconcile_action_cases(db_conn, d7_result, identity=identity_mgr)

        case_id = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-A02B"
        )[0]["action_case_id"]

        # Operator cannot transition owner=NULL case
        identity_user1 = _make_identity("USER-1", "ORG-A", "operator")
        with pytest.raises(d8.ActionCaseAuthError) as exc_info:
            d8.transition_action_case(
                db_conn, case_id, "IN_PROGRESS", identity=identity_user1,
            )
        assert "has no owner" in str(exc_info.value)


# ─── A03: Manager same-org valid transition ──────────────────────────

class TestA03ManagerValidTransition:
    def test_manager_can_transition_any_case_in_org(self, db_conn):
        """Manager can transition any case in their organization."""
        order = _make_order(
            order_id="ORD-A03", order_no="PO-A03",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-A03", risk_type="CUSTOMER_CONFIRMATION_BLOCKING",
            severity="HIGH", evidence=["客户确认阻塞"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-A03", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="NEEDS_CONFIRMATION",
            evidence=["客户确认阻塞"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity_user1 = _make_identity("USER-1", "ORG-A", "operator")
        d8.reconcile_action_cases(db_conn, d7_result, identity=identity_user1)

        case_id = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-A03"
        )[0]["action_case_id"]

        # Manager (MANAGER-A) transitions USER-1's case
        identity_manager = _make_identity("MANAGER-A", "ORG-A", "manager")
        result = d8.transition_action_case(
            db_conn, case_id, "IN_PROGRESS", identity=identity_manager,
        )
        assert result["stage"] == "IN_PROGRESS"
        assert result["version"] == 2

        # Manager can also close it
        result2 = d8.transition_action_case(
            db_conn, case_id, "CLOSED",
            close_reason="RESOLVED", identity=identity_manager,
        )
        assert result2["stage"] == "CLOSED"
        assert result2["lifecycle_status"] == "CLOSED"
        assert result2["version"] == 3

    def test_admin_can_transition_any_case_in_org(self, db_conn):
        """Admin can transition any case in their organization."""
        order = _make_order(
            order_id="ORD-A03B", order_no="PO-A03B",
            owner="USER-2", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-A03B", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-A03B", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity_user2 = _make_identity("USER-2", "ORG-A", "operator")
        d8.reconcile_action_cases(db_conn, d7_result, identity=identity_user2)

        case_id = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-A03B"
        )[0]["action_case_id"]

        identity_admin = _make_identity("ADMIN-A", "ORG-A", "admin")
        result = d8.transition_action_case(
            db_conn, case_id, "IN_PROGRESS", identity=identity_admin,
        )
        assert result["stage"] == "IN_PROGRESS"


# ─── A04: Cross-org reconcile payload injection ──────────────────────

class TestA04CrossOrgReconcileInjection:
    def test_reconcile_rejects_cross_org_item(self, db_conn):
        """Identity=ORG-A, item.organization_id=ORG-B → reconcile REJECTED."""
        order_a = _make_order(
            order_id="ORD-A04", order_no="PO-A04",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order_a)

        # Item claims to be from ORG-B but identity is ORG-A
        signal = _make_risk_signal(
            order_id="ORD-A04", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-B",
        )
        item = _make_d7_item(
            order_id="ORD-A04", organization_id="ORG-B",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result(
            [item],
            scope={"organization_id": "ORG-A", "user_id": "USER-1", "user_role": "operator"},
        )
        identity = _make_identity("USER-1", "ORG-A", "operator")

        with pytest.raises(d8.ReconcileAuthError) as exc_info:
            d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        assert "Cross-organization" in str(exc_info.value) or "does not match" in str(exc_info.value)

        # Verify no cases created for either org
        cases_a = d8.list_cases(db_conn, organization_id="ORG-A")
        assert len(cases_a) == 0
        cases_b = d8.list_cases(db_conn, organization_id="ORG-B")
        assert len(cases_b) == 0

    def test_reconcile_rejects_mismatched_scope(self, db_conn):
        """Identity=ORG-A, payload scope=ORG-B → reconcile REJECTED."""
        order_a = _make_order(
            order_id="ORD-A04B", order_no="PO-A04B",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order_a)

        signal = _make_risk_signal(
            order_id="ORD-A04B", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-A04B", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        # Payload scope says ORG-B but identity is ORG-A
        d7_result = _make_d7_result(
            [item],
            scope={"organization_id": "ORG-B", "user_id": "USER-1", "user_role": "operator"},
        )
        identity = _make_identity("USER-1", "ORG-A", "operator")

        with pytest.raises(d8.ReconcileAuthError):
            d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        cases = d8.list_cases(db_conn, organization_id="ORG-A")
        assert len(cases) == 0


# ─── A05: Same-order one-intent-disappears ───────────────────────────

class TestA05OneIntentDisappears:
    def test_one_intent_disappears_other_stays_observed(self, db_conn):
        """
        Round 1: ORD-1 has [CUSTOMER_CONFIRMATION, LOGISTICS_RECOVERY]
        Round 2: ORD-1 has [CUSTOMER_CONFIRMATION] only

        Result:
          - CUSTOMER_CONFIRMATION → OBSERVED
          - LOGISTICS_RECOVERY → NOT_OBSERVED (still ACTIVE)
        """
        order = _make_order(
            order_id="ORD-A05", order_no="PO-A05",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        # Round 1: two intents
        sig_customer = _make_risk_signal(
            order_id="ORD-A05", risk_type="CUSTOMER_CONFIRMATION_BLOCKING",
            severity="HIGH", evidence=["客户确认阻塞"], organization_id="ORG-A",
        )
        sig_logistics = _make_risk_signal(
            order_id="ORD-A05", risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH", evidence=["物流异常"], organization_id="ORG-A",
        )
        item_round1 = _make_d7_item(
            order_id="ORD-A05", organization_id="ORG-A",
            risk_signals=[sig_customer, sig_logistics],
            action_bucket="DO_NOW",
            evidence=["客户确认阻塞", "物流异常"], severity="HIGH",
        )
        d7_result_r1 = _make_d7_result(
            [item_round1],
            action_case_observations=[item_round1],
        )
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result_r1, identity=identity)
        cases_r1 = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-A05"
        )
        assert len(cases_r1) == 2
        assert {c["action_intent_key"] for c in cases_r1} == {
            "v1:CUSTOMER_CONFIRMATION", "v1:LOGISTICS_RECOVERY"
        }
        for c in cases_r1:
            assert c["observation_status"] == "OBSERVED"
            assert c["lifecycle_status"] == "ACTIVE"

        # Round 2: only CUSTOMER_CONFIRMATION
        item_round2 = _make_d7_item(
            order_id="ORD-A05", organization_id="ORG-A",
            risk_signals=[sig_customer],
            action_bucket="DO_NOW",
            evidence=["客户确认阻塞"], severity="HIGH",
        )
        d7_result_r2 = _make_d7_result(
            [item_round2],
            action_case_observations=[item_round2],
        )

        d8.reconcile_action_cases(db_conn, d7_result_r2, identity=identity)
        cases_r2 = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-A05"
        )
        assert len(cases_r2) == 2

        customer_case = [
            c for c in cases_r2
            if c["action_intent_key"] == "v1:CUSTOMER_CONFIRMATION"
        ][0]
        logistics_case = [
            c for c in cases_r2
            if c["action_intent_key"] == "v1:LOGISTICS_RECOVERY"
        ][0]

        # Customer confirmed observed
        assert customer_case["observation_status"] == "OBSERVED"
        assert customer_case["lifecycle_status"] == "ACTIVE"
        assert customer_case["stage"] == "READY_FOR_ACTION"

        # Logistics disappeared → NOT_OBSERVED, still ACTIVE
        assert logistics_case["observation_status"] == "NOT_OBSERVED"
        assert logistics_case["lifecycle_status"] == "ACTIVE"
        assert logistics_case["stage"] == "READY_FOR_ACTION"
        assert logistics_case["version"] == 1  # unchanged by NOT_OBSERVED


# ─── A06: Create unexpected DB error not swallowed ────────────────────

class TestA06UnexpectedErrorNotSwallowed:
    def test_integrity_error_without_existing_case_reraises(self, db_conn):
        """
        If IntegrityError is caught but no ACTIVE case is found,
        the error must be re-raised, not silently swallowed.
        """
        order = _make_order(
            order_id="ORD-A06", order_no="PO-A06",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-A06", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-A06", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        # First reconcile works fine
        result1 = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        assert result1["created_count"] == 1

        # Simulate: create ACTIVE case manually, then mark it CLOSED
        # to create a scenario where IntegrityError would occur but no ACTIVE exists
        cases = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-A06"
        )
        case_id = cases[0]["action_case_id"]
        d8.transition_action_case(
            db_conn, case_id, "CLOSED",
            close_reason="RESOLVED", identity=identity,
        )

        # Now try reconcile again — it should create a new ACTIVE case
        # because the old one is CLOSED, and the unique index only covers ACTIVE
        result2 = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        assert result2["created_count"] == 1
        assert result2["reused_count"] == 0

        # Verify: 1 ACTIVE + 1 CLOSED = 2 total
        all_cases = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-A06"
        )
        assert len(all_cases) == 2
        active = [c for c in all_cases if c["lifecycle_status"] == "ACTIVE"]
        closed = [c for c in all_cases if c["lifecycle_status"] == "CLOSED"]
        assert len(active) == 1
        assert len(closed) == 1


# ─── A07: Optimistic concurrency CAS miss ────────────────────────────

class TestA07OptimisticConcurrencyCASMiss:
    def test_stale_version_prevents_transition(self, db_conn):
        """
        Simulate stale version / CAS miss.
        If the version has changed since read, transition must fail
        with ActionCaseVersionConflict.
        """
        order = _make_order(
            order_id="ORD-A07", order_no="PO-A07",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-A07", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-A07", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        cases = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-A07"
        )
        case_id = cases[0]["action_case_id"]
        assert cases[0]["version"] == 1

        # First, do a valid transition to bump version from 1 → 2
        d8.transition_action_case(
            db_conn, case_id, "IN_PROGRESS", identity=identity,
        )
        after_bump = d8.get_case_by_id(db_conn, case_id)
        assert after_bump["version"] == 2
        assert after_bump["stage"] == "IN_PROGRESS"

        # Now simulate stale read: caller read version=1 (before the bump)
        # and tries to transition with _expected_version=1.
        # Since actual version is now 2, CAS check should fail.
        with pytest.raises(d8.ActionCaseVersionConflict) as exc_info:
            d8.transition_action_case(
                db_conn, case_id, "WAITING_RESULT",
                identity=identity, _expected_version=1,
            )
        assert exc_info.value.expected_version == 1

        # Verify case state unchanged (still IN_PROGRESS, version=2)
        case_final = d8.get_case_by_id(db_conn, case_id)
        assert case_final["stage"] == "IN_PROGRESS"
        assert case_final["lifecycle_status"] == "ACTIVE"
        assert case_final["version"] == 2  # unchanged

    def test_concurrent_transition_only_one_succeeds(self, db_conn):
        """
        Two sequential transitions on same case: both succeed
        because each reads the latest version after the previous one.
        """
        order = _make_order(
            order_id="ORD-A07B", order_no="PO-A07B",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-A07B", risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH", evidence=["物流异常"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-A07B", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["物流异常"], severity="HIGH",
        )
        d7_result = _make_d7_result([item])
        identity = _make_identity("USER-1", "ORG-A", "operator")

        d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        case_id = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-A07B"
        )[0]["action_case_id"]

        # First transition succeeds
        result1 = d8.transition_action_case(
            db_conn, case_id, "IN_PROGRESS", identity=identity,
        )
        assert result1["version"] == 2
        assert result1["stage"] == "IN_PROGRESS"

        # Second transition on already-bumped version also works
        # (it reads the current version=2, then updates to version=3)
        result2 = d8.transition_action_case(
            db_conn, case_id, "WAITING_RESULT", identity=identity,
        )
        assert result2["version"] == 3
        assert result2["stage"] == "WAITING_RESULT"


# ═══════════════════════════════════════════════════════════════════════
# B01-B06: Adversarial / Security Tests — Complete Observation Feed
# ═══════════════════════════════════════════════════════════════════════


# ─── B01: Operator same-org write attack ──────────────────────────────

class TestB01OperatorSameOrgWriteAttack:
    def test_operator_cannot_reconcile_same_org_other_owner(self, db_conn):
        """
        B01: USER-2 (operator in ORG-A) tries to reconcile ORD-1
        which is owned by USER-1 (also in ORG-A).

        The payload may claim ORD-1, but since USER-2 doesn't own it,
        the reconciliation must:
          - REJECT the item (skip, not create a case)
          - 0 case created for ORD-1
          - 0 case changed
        """
        # ORD-1 owned by USER-1
        order = _make_order(
            order_id="ORD-B01", order_no="PO-B01",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        # USER-2 (different operator, same org) sends reconciliation
        signal = _make_risk_signal(
            order_id="ORD-B01", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-B01", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        # Include action_case_observations to simulate full feed
        d7_result = _make_d7_result(
            [item],
            action_case_observations=[item],
            scope={"organization_id": "ORG-A", "user_id": "USER-2", "user_role": "operator"},
        )
        identity = _make_identity("USER-2", "ORG-A", "operator")

        result = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        # No cases created — USER-2 doesn't own ORD-B01
        assert result["created_count"] == 0
        assert result["reused_count"] == 0
        assert result["intents_count"] == 0
        assert result["status"] == "OK"

        # Verify no cases exist
        cases = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-B01"
        )
        assert len(cases) == 0


# ─── B02: Operator observation pollution ────────────────────────────────

class TestB02OperatorObservationPollution:
    def test_operator_cannot_change_other_operators_observation(self, db_conn):
        """
        B02: USER-1 has ORD-1 with existing ACTIVE OBSERVED case.
        USER-2 reconciles only their own ORD-2.
        Result: USER-1's ORD-1 case must remain OBSERVED.

        Without scope_order_ids, USER-2's reconcile would accidentally
        mark ORD-1 as NOT_OBSERVED because it's not in USER-2's feed.
        """
        # ORD-1 owned by USER-1
        order1 = _make_order(
            order_id="ORD-B02A", order_no="PO-B02A",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order1)

        # ORD-2 owned by USER-2
        order2 = _make_order(
            order_id="ORD-B02B", order_no="PO-B02B",
            owner="USER-2", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order2)

        # USER-1 creates LOGISTICS_RECOVERY case for ORD-B02A
        sig1 = _make_risk_signal(
            order_id="ORD-B02A", risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH", evidence=["物流异常"], organization_id="ORG-A",
        )
        item1 = _make_d7_item(
            order_id="ORD-B02A", organization_id="ORG-A",
            risk_signals=[sig1], action_bucket="DO_NOW",
            evidence=["物流异常"], severity="HIGH",
        )
        d7_r1 = _make_d7_result(
            [item1],
            action_case_observations=[item1],
            scope={"organization_id": "ORG-A", "user_id": "USER-1", "user_role": "operator"},
        )
        identity_u1 = _make_identity("USER-1", "ORG-A", "operator")
        d8.reconcile_action_cases(db_conn, d7_r1, identity=identity_u1)

        # Verify: ORD-B02A has ACTIVE OBSERVED LOGISTICS_RECOVERY case
        cases1 = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-B02A"
        )
        assert len(cases1) == 1
        assert cases1[0]["action_intent_key"] == "v1:LOGISTICS_RECOVERY"
        assert cases1[0]["observation_status"] == "OBSERVED"
        assert cases1[0]["lifecycle_status"] == "ACTIVE"

        # USER-2 reconciles ONLY their own ORD-B02B
        sig2 = _make_risk_signal(
            order_id="ORD-B02B", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item2 = _make_d7_item(
            order_id="ORD-B02B", organization_id="ORG-A",
            risk_signals=[sig2], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_r2 = _make_d7_result(
            [item2],
            action_case_observations=[item2],
            scope={"organization_id": "ORG-A", "user_id": "USER-2", "user_role": "operator"},
        )
        identity_u2 = _make_identity("USER-2", "ORG-A", "operator")
        d8.reconcile_action_cases(db_conn, d7_r2, identity=identity_u2)

        # CRITICAL: USER-1's ORD-B02A case must STILL be OBSERVED
        # It should NOT be marked NOT_OBSERVED by USER-2's reconciliation
        cases1_after = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-B02A"
        )
        assert len(cases1_after) == 1
        assert cases1_after[0]["action_intent_key"] == "v1:LOGISTICS_RECOVERY"
        assert cases1_after[0]["observation_status"] == "OBSERVED"  # MUST remain OBSERVED
        assert cases1_after[0]["lifecycle_status"] == "ACTIVE"


# ─── B03: DB org is authority ──────────────────────────────────────────

class TestB03DBOrgIsAuthority:
    def test_payload_org_cannot_override_db_org(self, db_conn):
        """
        B03: Database has ORD-X with organization_id=ORG-B.
        Payload falsely claims organization_id=ORG-A.
        Identity=ORG-A manager.

        Must: REJECT entire reconcile. Cannot create
        ORG-A action_case → ORG-B order.
        """
        # ORDER in ORG-B (different org)
        order = _make_order(
            order_id="ORD-B03", order_no="PO-B03",
            owner="USER-B", organization_id="ORG-B",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        # Payload claims ORG-A but DB says ORG-B
        signal = _make_risk_signal(
            order_id="ORD-B03", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",  # spoofed
        )
        item = _make_d7_item(
            order_id="ORD-B03", organization_id="ORG-A",  # spoofed
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_result = _make_d7_result(
            [item],
            action_case_observations=[item],
            scope={"organization_id": "ORG-A", "user_id": "MGR-1", "user_role": "manager"},
        )
        identity = _make_identity("MGR-1", "ORG-A", "manager")

        with pytest.raises(d8.ReconcileAuthError) as exc_info:
            d8.reconcile_action_cases(db_conn, d7_result, identity=identity)
        assert "organization_id" in str(exc_info.value).lower() or "organization" in str(exc_info.value).lower()

        # Verify no cases created for any org
        cases_a = d8.list_cases(db_conn, organization_id="ORG-A")
        assert len(cases_a) == 0
        cases_b = d8.list_cases(db_conn, organization_id="ORG-B")
        assert len(cases_b) == 0


# ─── B04: Information Gap must enter Action Case ────────────────────────

class TestB04InformationGapEnterActionCase:
    def test_information_gap_creates_information_completion_case(self, db_conn):
        """
        B04: Operator order has risk_signals=[INFORMATION_GAP].
        D7 puts it in information_gaps (NOT my_action_items).
        D8's full Observation Feed must still process it.

        Must create:
          action_intent_key = v1:INFORMATION_COMPLETION
          stage = NEEDS_JUDGMENT
        """
        order = _make_order(
            order_id="ORD-B04", order_no="PO-B04",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        # Only INFORMATION_GAP signal
        signal = _make_risk_signal(
            order_id="ORD-B04", risk_type="INFORMATION_GAP",
            severity="MEDIUM", evidence=["缺少客户确认信息"],
            organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-B04", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="ESCALATE",
            evidence=["缺少客户确认信息"], severity="MEDIUM",
        )

        # Simulate: INFORMATION_GAP is in action_case_observations (full feed)
        # but NOT in my_action_items (ranked queue)
        d7_result = _make_d7_result(
            [],  # my_action_items is empty
            action_case_observations=[item],  # full feed has it
            information_gaps=[item],  # D7 would put it here
            scope={"organization_id": "ORG-A", "user_id": "USER-1", "user_role": "operator"},
        )
        identity = _make_identity("USER-1", "ORG-A", "operator")

        result = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        # Must have created 1 case
        assert result["created_count"] == 1
        assert result["intents_count"] == 1

        # Verify the case
        cases = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-B04"
        )
        assert len(cases) == 1
        assert cases[0]["action_intent_key"] == "v1:INFORMATION_COMPLETION"
        assert cases[0]["stage"] == "NEEDS_JUDGMENT"
        assert cases[0]["lifecycle_status"] == "ACTIVE"
        assert cases[0]["observation_status"] == "OBSERVED"


# ─── B05: Top-N absence != NOT_OBSERVED ────────────────────────────────

class TestB05TopNAbsenceNotNotObserved:
    def test_case_remains_observed_when_not_in_top_n(self, db_conn):
        """
        B05: Same operator, same order.

        Round 1: ORD-1 has LOGISTICS_EXCEPTION → LOGISTICS_RECOVERY case created.
        Round 2: ORD-1 is in action_case_observations (full feed) with LOGISTICS_EXCEPTION
                 BUT my_action_items Top-N doesn't include ORD-1.

        Result: ORD-1's LOGISTICS_RECOVERY case must remain OBSERVED.
        The absence from the Top-N UI queue must NOT cause NOT_OBSERVED.
        """
        order = _make_order(
            order_id="ORD-B05", order_no="PO-B05",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        signal = _make_risk_signal(
            order_id="ORD-B05", risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH", evidence=["物流异常"], organization_id="ORG-A",
        )
        item = _make_d7_item(
            order_id="ORD-B05", organization_id="ORG-A",
            risk_signals=[signal], action_bucket="DO_NOW",
            evidence=["物流异常"], severity="HIGH",
        )

        identity = _make_identity("USER-1", "ORG-A", "operator")

        # Round 1: ORD-B05 is in BOTH my_action_items AND action_case_observations
        d7_r1 = _make_d7_result(
            [item],
            action_case_observations=[item],
            scope={"organization_id": "ORG-A", "user_id": "USER-1", "user_role": "operator"},
        )
        d8.reconcile_action_cases(db_conn, d7_r1, identity=identity)

        # Verify case created
        cases_r1 = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-B05"
        )
        assert len(cases_r1) == 1
        assert cases_r1[0]["action_intent_key"] == "v1:LOGISTICS_RECOVERY"
        assert cases_r1[0]["observation_status"] == "OBSERVED"

        # Round 2: ORD-B05 is in action_case_observations (full feed)
        # BUT NOT in my_action_items (simulating Top-N truncation)
        d7_r2 = _make_d7_result(
            [],  # my_action_items is empty — Top-N doesn't include ORD-B05
            action_case_observations=[item],  # full feed still has it
            scope={"organization_id": "ORG-A", "user_id": "USER-1", "user_role": "operator"},
        )
        result_r2 = d8.reconcile_action_cases(db_conn, d7_r2, identity=identity)

        # Case should remain OBSERVED — NOT_OBSERVED marking uses
        # action_case_observations (authoritative), not my_action_items
        cases_r2 = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-B05"
        )
        assert len(cases_r2) == 1
        assert cases_r2[0]["action_intent_key"] == "v1:LOGISTICS_RECOVERY"
        assert cases_r2[0]["observation_status"] == "OBSERVED"  # MUST remain OBSERVED
        assert cases_r2[0]["lifecycle_status"] == "ACTIVE"


# ─── B06: Real disappearance (verified screen, risk gone) ──────────────

class TestB06RealDisappearance:
    def test_real_risk_disappearance_marks_not_observed(self, db_conn):
        """
        B06: Full Observation Snapshot contains ORD-1 (truly screened).
        But this round, ORD-1's risk_signals no longer have LOGISTICS_EXCEPTION.

        Result: LOGISTICS_RECOVERY → NOT_OBSERVED.
        lifecycle still ACTIVE. stage unchanged. No auto-CLOSED.
        """
        order = _make_order(
            order_id="ORD-B06", order_no="PO-B06",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15", current_progress=0.3,
        )
        _insert_order(db_conn, order)

        identity = _make_identity("USER-1", "ORG-A", "operator")

        # Round 1: LOGISTICS_EXCEPTION exists → LOGISTICS_RECOVERY case
        sig_logistics = _make_risk_signal(
            order_id="ORD-B06", risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH", evidence=["物流异常"], organization_id="ORG-A",
        )
        item_r1 = _make_d7_item(
            order_id="ORD-B06", organization_id="ORG-A",
            risk_signals=[sig_logistics], action_bucket="DO_NOW",
            evidence=["物流异常"], severity="HIGH",
        )
        d7_r1 = _make_d7_result(
            [item_r1],
            action_case_observations=[item_r1],
            scope={"organization_id": "ORG-A", "user_id": "USER-1", "user_role": "operator"},
        )
        d8.reconcile_action_cases(db_conn, d7_r1, identity=identity)

        # Verify: LOGISTICS_RECOVERY case created and OBSERVED
        cases_r1 = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-B06"
        )
        assert len(cases_r1) == 1
        assert cases_r1[0]["action_intent_key"] == "v1:LOGISTICS_RECOVERY"
        assert cases_r1[0]["observation_status"] == "OBSERVED"
        assert cases_r1[0]["lifecycle_status"] == "ACTIVE"

        # Round 2: ORD-B06 is still screened (in action_case_observations)
        # BUT LOGISTICS_EXCEPTION is gone — only DELIVERY_RISK remains
        sig_delivery = _make_risk_signal(
            order_id="ORD-B06", risk_type="DELIVERY_RISK",
            severity="HIGH", evidence=["交期风险"], organization_id="ORG-A",
        )
        item_r2 = _make_d7_item(
            order_id="ORD-B06", organization_id="ORG-A",
            risk_signals=[sig_delivery],  # LOGISTICS_EXCEPTION is GONE
            action_bucket="DO_NOW",
            evidence=["交期风险"], severity="HIGH",
        )
        d7_r2 = _make_d7_result(
            [item_r2],
            action_case_observations=[item_r2],
            scope={"organization_id": "ORG-A", "user_id": "USER-1", "user_role": "operator"},
        )
        d8.reconcile_action_cases(db_conn, d7_r2, identity=identity)

        # LOGISTICS_RECOVERY should now be NOT_OBSERVED
        # because ORD-B06 was fully screened, but the LOGISTICS_EXCEPTION
        # risk signal truly disappeared
        cases_r2 = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-B06"
        )
        logistics_case = [
            c for c in cases_r2
            if c["action_intent_key"] == "v1:LOGISTICS_RECOVERY"
        ][0]
        assert logistics_case["observation_status"] == "NOT_OBSERVED"
        assert logistics_case["lifecycle_status"] == "ACTIVE"  # still ACTIVE
        assert logistics_case["stage"] == "READY_FOR_ACTION"  # stage unchanged

        # DELIVERY_RECOVERY case should be OBSERVED (new intent, new case)
        delivery_case = [
            c for c in cases_r2
            if c["action_intent_key"] == "v1:DELIVERY_RECOVERY"
        ]
        assert len(delivery_case) == 1
        assert delivery_case[0]["observation_status"] == "OBSERVED"


# ─── C01: Zero-risk screened order appears in Observation Feed ────────────

class TestC01ZeroRiskObservationFeed:
    def test_zero_risk_order_in_action_case_observations(self, db_conn):
        """
        C01: An order is fully screened by D7 but has zero risk_signals.

        The order MUST appear in action_case_observations so D8 can
        track the screen event. It must NOT appear in my_action_items
        or information_gaps (UI queues stay clean).
        """
        from d7_risk_engine import run_d7_pipeline

        order = _make_order(
            order_id="ORD-C01", order_no="PO-C01",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-12-31",  # far future → no DELIVERY_RISK
            current_progress=0.9,  # high progress → no progress risk
            current_node="生产中",
            latest_supplier_commitment="2026-12-20",
        )
        _insert_order(db_conn, order)

        # No risk signal in DB — assess_risks_from_facts returns []
        identity = _make_identity("USER-1", "ORG-A", "operator")

        result = run_d7_pipeline(
            db_conn,
            identity,
            top_n=50,
        )

        # Screened count = 1 (the order was fully processed)
        assert result["screened_order_count"] == 1

        # Action case observations MUST contain the zero-risk order
        observations = result.get("action_case_observations") or []
        assert len(observations) == 1
        obs = observations[0]
        assert obs["order_id"] == "ORD-C01"
        assert obs["risk_signals"] == []
        assert obs.get("is_screened") is True

        # UI queues must NOT be polluted
        assert result.get("my_action_items") == []
        assert result.get("information_gaps") == []


# ─── C02: Complete risk disappearance ─────────────────────────────────────

class TestC02CompleteRiskDisappearance:
    def test_zero_risk_causes_not_observed(self, db_conn):
        """
        C02: Round 1 — ORD-C02 has LOGISTICS_EXCEPTION → creates LOGISTICS_RECOVERY.
        Round 2 — ORD-C02 is still fully screened (in action_case_observations)
                  but risk_signals=[] (risk truly disappeared).

        Result: LOGISTICS_RECOVERY → NOT_OBSERVED, lifecycle=ACTIVE, stage unchanged.
        """
        order = _make_order(
            order_id="ORD-C02", order_no="PO-C02",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15",
            current_progress=0.3,
        )
        _insert_order(db_conn, order)

        identity = _make_identity("USER-1", "ORG-A", "operator")

        # Round 1: ORD-C02 has LOGISTICS_EXCEPTION
        signal_r1 = _make_risk_signal(
            order_id="ORD-C02", risk_type="LOGISTICS_EXCEPTION",
            severity="HIGH", evidence=["物流异常"], organization_id="ORG-A",
        )
        item_r1 = _make_d7_item(
            order_id="ORD-C02", organization_id="ORG-A",
            risk_signals=[signal_r1], action_bucket="DO_NOW",
            evidence=["物流异常"], severity="HIGH",
        )
        d7_r1 = _make_d7_result(
            [item_r1],
            action_case_observations=[item_r1],
            scope={"organization_id": "ORG-A", "user_id": "USER-1", "user_role": "operator"},
        )
        d8.reconcile_action_cases(db_conn, d7_r1, identity=identity)

        # Verify LOGISTICS_RECOVERY case exists
        cases_r1 = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-C02"
        )
        assert len(cases_r1) == 1
        assert cases_r1[0]["action_intent_key"] == "v1:LOGISTICS_RECOVERY"
        assert cases_r1[0]["observation_status"] == "OBSERVED"
        assert cases_r1[0]["lifecycle_status"] == "ACTIVE"

        # Round 2: ORD-C02 is fully screened (in action_case_observations)
        # BUT risk_signals=[] (the LOGISTICS_EXCEPTION truly disappeared)
        zero_risk_item = _make_d7_item(
            order_id="ORD-C02", organization_id="ORG-A",
            risk_signals=[],  # Zero risk signals
            evidence=[], severity=None,
        )
        d7_r2 = _make_d7_result(
            [],  # my_action_items empty (no risks)
            action_case_observations=[zero_risk_item],  # FULL feed includes it
            scope={"organization_id": "ORG-A", "user_id": "USER-1", "user_role": "operator"},
        )
        d8.reconcile_action_cases(db_conn, d7_r2, identity=identity)

        # LOGISTICS_RECOVERY should be NOT_OBSERVED
        # because ORD-C02 was fully screened but the risk truly disappeared
        cases_r2 = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-C02"
        )
        assert len(cases_r2) == 1
        assert cases_r2[0]["action_intent_key"] == "v1:LOGISTICS_RECOVERY"
        assert cases_r2[0]["observation_status"] == "NOT_OBSERVED"
        assert cases_r2[0]["lifecycle_status"] == "ACTIVE"  # still ACTIVE
        assert cases_r2[0]["stage"] == "READY_FOR_ACTION"  # stage unchanged


# ─── C03: No-risk Observation must not create Case ────────────────────────

class TestC03NoRiskNoCaseCreation:
    def test_zero_risk_does_not_create_case(self, db_conn):
        """
        C03: action_case_observation contains ORD-C03 with risk_signals=[].
        No historical Action Case exists for this order.

        Result: reconcile creates 0 cases, intents_count=0.
        Observation Scope and Business Intent are strictly separated.
        """
        order = _make_order(
            order_id="ORD-C03", order_no="PO-C03",
            owner="USER-1", organization_id="ORG-A",
            requested_delivery_date="2026-08-15",
            current_progress=0.5,
        )
        _insert_order(db_conn, order)

        identity = _make_identity("USER-1", "ORG-A", "operator")

        zero_risk_item = _make_d7_item(
            order_id="ORD-C03", organization_id="ORG-A",
            risk_signals=[],  # Zero risk signals
            evidence=[], severity=None,
        )
        d7_result = _make_d7_result(
            [],  # my_action_items empty
            action_case_observations=[zero_risk_item],  # Full feed has it
            scope={"organization_id": "ORG-A", "user_id": "USER-1", "user_role": "operator"},
        )
        result = d8.reconcile_action_cases(db_conn, d7_result, identity=identity)

        # No cases should be created — screen event ≠ business intent
        assert result["created_count"] == 0
        assert result["intents_count"] == 0

        # Verify no cases in DB
        cases = d8.list_cases(
            db_conn, organization_id="ORG-A", order_id="ORD-C03"
        )
        assert len(cases) == 0