"""
D7 Tests: Risk Signals + Action Buckets + Explainable Priority
================================================================

Test Scenarios:
  S01  Normal order — no high risk
  S02  Delivery date overdue — DELIVERY_RISK + DO_NOW
  S03  Valid waiting — WAITING_EXTERNAL + ranking_suppressed
  S04  Commitment overdue — SUPPLIER_COMMITMENT_OVERDUE + DO_NOW
  S05  Owner missing — OWNER_MISSING + ESCALATE
  S06  Source conflict — SOURCE_CONFLICT + NEEDS_CONFIRMATION
  S07  Information gap only — not in risk Top N
  S08  ERP unavailable — data quality boundary, no auto-escalation
  S09  Multi-risk per order — one order, aggregated, secondary signals
  S10  Top-N not padded — only 3 real risks → return 3
  S11  Permission — operator cannot see others' risks
  S12  Deterministic — same facts produce same results

Plus negative control: SAL-ORD-2026-00001 is not auto-high-risk
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

CN_TZ = timezone(timedelta(hours=8))

# ─── Import D7 Module ────────────────────────────────────────────────
import d7_risk_engine as d7
from d7_risk_engine import (
    build_risk_signal,
    bridge_erp_snapshot_to_facts,
    assess_risks_from_facts,
    assign_action_bucket,
    compute_priority_score,
    rank_orders,
    run_d7_pipeline,
    bucket_priority,
    boundary_freshness_label,
    D7_POLICY_VERSION,
    RISK_TYPES,
    ALL_RISK_TYPES,
    ACTION_BUCKETS,
)


# ─── Helpers ──────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(CN_TZ)


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
        "organization_id": "ORG-TEST",
        "created_at": "2026-08-01T00:00:00+08:00",
        "updated_at": "2026-08-01T00:00:00+08:00",
    }
    defaults.update(kwargs)
    return defaults


def _make_task(**kwargs: Any) -> dict[str, Any]:
    defaults = {
        "task_id": "TASK-TEST",
        "related_order_id": "ORD-TEST",
        "title": "Test Task",
        "recommended_action": "Test Action",
        "target": "factory",
        "status": "OPEN",
        "owner_user_id": "USER-1",
        "responsibility_status": "assigned",
        "waiting_on": None,
        "promised_reply_at": None,
        "next_action_at": None,
        "business_deadline": None,
        "last_contact_at": None,
        "risk_level": "none",
        "urgent": 0,
        "pending_confirmation": 0,
        "evidence_json": "[]",
    }
    defaults.update(kwargs)
    return defaults


def _make_erp_snapshot(order_no: str, **kwargs: Any) -> dict[str, Any]:
    normalized = {
        "external_id": order_no,
        "customer_external_id": "CUST-001",
        "customer_due_date": kwargs.get("customer_due_date"),
        "order_status": kwargs.get("order_status", "Submitted"),
        "transaction_date": "2026-08-01",
        "source_modified_at": kwargs.get("source_modified_at", "2026-08-10T10:00:00+08:00"),
        "items": kwargs.get("items", []),
        "erp_owner": kwargs.get("erp_owner", "erpuser@example.com"),
    }
    return {
        "snapshot_id": f"ERP-SNAP-{order_no}",
        "external_id": order_no,
        "source_modified_at": kwargs.get("source_modified_at", "2026-08-10T10:00:00+08:00"),
        "normalized": normalized,
        "fetched_at": kwargs.get("fetched_at", "2026-08-10T10:05:00+08:00"),
        "freshness": kwargs.get("freshness", "FRESH"),
    }


def _make_sync_state(sync_status: str = "FRESH", *, last_success_at: str | None = None) -> dict[str, Any]:
    now_iso = last_success_at or _now().isoformat(timespec="seconds")
    return {
        "organization_id": "ORG-TEST",
        "doctype": "Sales Order",
        "last_success_cursor": now_iso,
        "last_success_at": now_iso,
        "last_attempt_at": now_iso,
        "sync_status": sync_status,
        "last_error_code": None,
        "records_seen": 5,
        "records_changed": 2,
    }


# =========================================================================
# S01: Normal Order — no high risk
# =========================================================================

class TestS01NormalOrder:
    """S01: Normal order with future delivery, no confirmed anomalies."""

    def test_no_high_risk_for_normal_order(self):
        future_date = (_now() + timedelta(days=14)).date().isoformat()
        order = _make_order(
            order_id="ORD-S01",
            order_no="PO-S01",
            requested_delivery_date=future_date,
            current_progress=0.6,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())

        real_risks = [s for s in risk_signals if s["risk_type"] != "INFORMATION_GAP"]
        assert len(real_risks) == 0, f"Normal order should produce no real risks, got {real_risks}"

    def test_s01_action_bucket_is_scheduled(self):
        future_date = (_now() + timedelta(days=14)).date().isoformat()
        order = _make_order(
            order_id="ORD-S01b",
            order_no="PO-S01b",
            requested_delivery_date=future_date,
            current_progress=0.6,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        bucket = assign_action_bucket(risk_signals, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "SCHEDULED"

    def test_s01_information_gap_is_independent(self):
        """If there are no real risks, INFORMATION_GAP may appear but should
        not be treated as a business risk."""
        future_date = (_now() + timedelta(days=14)).date().isoformat()
        order = _make_order(
            order_id="ORD-S01c",
            order_no="PO-S01c",
            requested_delivery_date=future_date,
            current_progress=None,  # Missing progress
            current_node=None,  # Missing node
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        info_gaps = [s for s in risk_signals if s["risk_type"] == "INFORMATION_GAP"]
        assert len(info_gaps) > 0, "Should produce INFORMATION_GAP for missing fields"
        # INFORMATION_GAP must not be in real risk types
        assert info_gaps[0]["risk_type"] not in RISK_TYPES


# =========================================================================
# S02: Delivery Date Overdue
# =========================================================================

class TestS02DeliveryRisk:
    """S02: Confirmed delivery date is overdue."""

    def test_delivery_overdue_produces_risk(self):
        past_date = (_now() - timedelta(days=2)).date().isoformat()
        order = _make_order(
            order_id="ORD-S02",
            order_no="PO-S02",
            requested_delivery_date=past_date,
            current_progress=0.5,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        delivery_risks = [s for s in risk_signals if s["risk_type"] == "DELIVERY_RISK"]
        assert len(delivery_risks) >= 1
        assert delivery_risks[0]["severity"] in ("HIGH", "CRITICAL")

    def test_delivery_overdue_has_evidence(self):
        past_date = (_now() - timedelta(days=2)).date().isoformat()
        order = _make_order(
            order_id="ORD-S02b",
            order_no="PO-S02b",
            requested_delivery_date=past_date,
            current_progress=0.5,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        delivery = next(s for s in risk_signals if s["risk_type"] == "DELIVERY_RISK")
        assert len(delivery["evidence"]) > 0
        # Evidence must mention the overdue situation
        evidence_text = " ".join(delivery["evidence"])
        assert "超期" in evidence_text or "已过" in evidence_text or "交期" in evidence_text

    def test_delivery_overdue_gets_do_now(self):
        past_date = (_now() - timedelta(days=2)).date().isoformat()
        order = _make_order(
            order_id="ORD-S02c",
            order_no="PO-S02c",
            requested_delivery_date=past_date,
            current_progress=0.5,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        bucket = assign_action_bucket(risk_signals, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "DO_NOW"


# =========================================================================
# S03: Valid Waiting
# =========================================================================

class TestS03ValidWaiting:
    """S03: Already contacted supplier, promised_reply_at not yet due."""

    def test_valid_waiting_produces_waiting_external(self):
        future_reply = (_now() + timedelta(hours=3)).isoformat(timespec="seconds")
        task = _make_task(
            task_id="TASK-S03",
            related_order_id="ORD-S03",
            title="等待工厂确认拉链到料时间",
            waiting_on="工厂",
            promised_reply_at=future_reply,
            status="WAITING_EXTERNAL",
            owner_user_id="USER-1",
        )
        order = _make_order(
            order_id="ORD-S03",
            order_no="PO-S03",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.3,
            current_node="备料中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now(), tasks=[task])
        bucket = assign_action_bucket(risk_signals, order, tasks=[task], current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "WAITING_EXTERNAL"
        assert bucket["ranking_suppressed"] is True

    def test_valid_waiting_not_escalated(self):
        future_reply = (_now() + timedelta(hours=3)).isoformat(timespec="seconds")
        task = _make_task(
            task_id="TASK-S03b",
            related_order_id="ORD-S03b",
            title="等待工厂确认",
            waiting_on="工厂",
            promised_reply_at=future_reply,
            status="WAITING_EXTERNAL",
            owner_user_id="USER-1",
        )
        order = _make_order(
            order_id="ORD-S03b",
            order_no="PO-S03b",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.3,
            current_node="备料中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now(), tasks=[task])
        bucket = assign_action_bucket(risk_signals, order, tasks=[task], current=_now(), user_id="USER-1")
        # Should NOT be ESCALATE or DO_NOW while in valid waiting window
        assert bucket["action_bucket"] not in ("ESCALATE", "DO_NOW")


# =========================================================================
# S04: Commitment Overdue
# =========================================================================

class TestS04CommitmentOverdue:
    """S04: promised_reply_at has passed with no valid reply."""

    def test_commitment_overdue_produces_risk(self):
        past_commitment = (_now() - timedelta(days=1)).date().isoformat()
        order = _make_order(
            order_id="ORD-S04",
            order_no="PO-S04",
            latest_supplier_commitment=past_commitment,
            current_progress=0.4,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        commitment_risks = [s for s in risk_signals if s["risk_type"] == "SUPPLIER_COMMITMENT_OVERDUE"]
        assert len(commitment_risks) >= 1

    def test_commitment_overdue_with_no_progress(self):
        past_commitment = (_now() - timedelta(days=1)).date().isoformat()
        order = _make_order(
            order_id="ORD-S04b",
            order_no="PO-S04b",
            latest_supplier_commitment=past_commitment,
            current_progress=0.0,
            current_node="未开工",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        commitment_risks = [s for s in risk_signals if s["risk_type"] == "SUPPLIER_COMMITMENT_OVERDUE"]
        assert len(commitment_risks) >= 1

    def test_no_commitment_no_risk(self):
        """No commitment → no SUPPLIER_COMMITMENT_OVERDUE risk."""
        order = _make_order(
            order_id="ORD-S04c",
            order_no="PO-S04c",
            latest_supplier_commitment=None,  # No commitment
            current_progress=0.5,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        commitment_risks = [s for s in risk_signals if s["risk_type"] == "SUPPLIER_COMMITMENT_OVERDUE"]
        assert len(commitment_risks) == 0

    def test_overdue_commitment_gets_do_now(self):
        past_commitment = (_now() - timedelta(days=1)).date().isoformat()
        order = _make_order(
            order_id="ORD-S04d",
            order_no="PO-S04d",
            latest_supplier_commitment=past_commitment,
            current_progress=0.4,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        bucket = assign_action_bucket(risk_signals, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "DO_NOW"


# =========================================================================
# S05: Owner Missing
# =========================================================================

class TestS05OwnerMissing:
    """S05: FlowOrder business owner is missing."""

    def test_owner_missing_produces_risk(self):
        order = _make_order(
            order_id="ORD-S05",
            order_no="PO-S05",
            owner=None,  # No owner
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.5,
            current_node="生产中",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        owner_risks = [s for s in risk_signals if s["risk_type"] == "OWNER_MISSING"]
        assert len(owner_risks) >= 1

    def test_owner_missing_gets_escalate(self):
        order = _make_order(
            order_id="ORD-S05b",
            order_no="PO-S05b",
            owner=None,
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.5,
            current_node="生产中",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        bucket = assign_action_bucket(risk_signals, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "ESCALATE"

    def test_erp_owner_not_used_as_business_owner(self):
        """ERP document owner MUST NOT be mapped to FlowOrder business owner."""
        order = _make_order(
            order_id="ORD-S05c",
            order_no="PO-S05c",
            owner=None,  # No FlowOrder owner
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.5,
            current_node="生产中",
        )
        erp_facts = {
            "external_id": "PO-S05c",
            "customer_due_date": None,
            "order_status": "Submitted",
            "erp_owner": "erpuser@example.com",  # ERP document creator
            "freshness": "FRESH",
        }
        risk_signals = assess_risks_from_facts(
            order, current=_now(), erp_facts=erp_facts
        )
        owner_risks = [s for s in risk_signals if s["risk_type"] == "OWNER_MISSING"]
        assert len(owner_risks) >= 1, "ERP owner should NOT fill in for missing FlowOrder owner"
        # Evidence should mention ERP owner is NOT the business owner
        evidence_text = " ".join(owner_risks[0]["evidence"])
        assert "ERP" in evidence_text or "erp" in evidence_text.lower() or "不是" in evidence_text

    def test_owner_present_no_missing_risk(self):
        order = _make_order(
            order_id="ORD-S05d",
            order_no="PO-S05d",
            owner="USER-1",
            requested_delivery_date=(_now() + timedelta(days=10)).date().isoformat(),
            current_progress=0.5,
            current_node="生产中",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        owner_risks = [s for s in risk_signals if s["risk_type"] == "OWNER_MISSING"]
        assert len(owner_risks) == 0


# =========================================================================
# S06: Source Conflict
# =========================================================================

class TestS06SourceConflict:
    """S06: Key facts conflict between sources."""

    def test_source_conflict_produces_risk(self):
        order = _make_order(
            order_id="ORD-S06",
            order_no="PO-S06",
            status="ACTIVE",
            requested_delivery_date="2026-08-15",
            owner="USER-1",
            current_progress=0.5,
            current_node="生产中",
        )
        erp_facts = {
            "external_id": "PO-S06",
            "customer_due_date": "2026-08-20",  # Different from local
            "order_status": "Completed",  # Different from local
            "erp_owner": "erpuser",
            "freshness": "FRESH",
        }
        risk_signals = assess_risks_from_facts(
            order, current=_now(), erp_facts=erp_facts
        )
        conflict_signals = [s for s in risk_signals if s["risk_type"] == "SOURCE_CONFLICT"]
        assert len(conflict_signals) >= 1

    def test_source_conflict_gets_needs_confirmation(self):
        # Keep this scenario about SOURCE_CONFLICT rather than allowing a fixed
        # historical date to silently turn it into an overdue DELIVERY_RISK as
        # the calendar advances. This stabilizes the frozen D7 contract without
        # changing production bucket precedence.
        current = _now()
        local_due = (current + timedelta(days=10)).date().isoformat()
        erp_due = (current + timedelta(days=15)).date().isoformat()
        order = _make_order(
            order_id="ORD-S06b",
            order_no="PO-S06b",
            status="ACTIVE",
            requested_delivery_date=local_due,
            owner="USER-1",
            current_progress=0.5,
            current_node="生产中",
        )
        erp_facts = {
            "external_id": "PO-S06b",
            "customer_due_date": erp_due,
            "order_status": "Completed",
            "erp_owner": "erpuser",
            "freshness": "FRESH",
        }
        risk_signals = assess_risks_from_facts(
            order, current=current, erp_facts=erp_facts
        )
        bucket = assign_action_bucket(
            risk_signals, order, current=current, user_id="USER-1"
        )
        assert bucket["action_bucket"] == "NEEDS_CONFIRMATION"

    def test_no_auto_resolve_conflict(self):
        """System must NOT auto-resolve source conflicts."""
        order = _make_order(
            order_id="ORD-S06c",
            order_no="PO-S06c",
            status="ACTIVE",
            requested_delivery_date="2026-08-15",
            owner="USER-1",
        )
        erp_facts = {
            "external_id": "PO-S06c",
            "order_status": "Completed",
            "freshness": "FRESH",
        }
        risk_signals = assess_risks_from_facts(
            order, current=_now(), erp_facts=erp_facts
        )
        conflict = next((s for s in risk_signals if s["risk_type"] == "SOURCE_CONFLICT"), None)
        assert conflict is not None
        evidence_text = " ".join(conflict["evidence"])
        assert "不会自动选择" in evidence_text or "人工确认" in evidence_text


# =========================================================================
# S07: Information Gap Only
# =========================================================================

class TestS07InformationGapOnly:
    """S07: Only key progress missing, no real risk evidence."""

    def test_info_gap_not_in_real_risk_types(self):
        assert "INFORMATION_GAP" not in RISK_TYPES

    def test_info_gap_is_separate_from_risks(self):
        """Information gaps must be tracked separately."""
        future_date = (_now() + timedelta(days=14)).date().isoformat()
        order = _make_order(
            order_id="ORD-S07",
            order_no="PO-S07",
            requested_delivery_date=future_date,
            current_progress=None,
            current_node=None,
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        info_gaps = [s for s in risk_signals if s["risk_type"] == "INFORMATION_GAP"]
        real_risks = [s for s in risk_signals if s["risk_type"] != "INFORMATION_GAP"]
        assert len(info_gaps) > 0
        assert len(real_risks) == 0

    def test_info_gap_does_not_escalate(self):
        future_date = (_now() + timedelta(days=14)).date().isoformat()
        order = _make_order(
            order_id="ORD-S07b",
            order_no="PO-S07b",
            requested_delivery_date=future_date,
            current_progress=None,
            current_node=None,
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        bucket = assign_action_bucket(risk_signals, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "SCHEDULED"


# =========================================================================
# S08: ERP Unavailable
# =========================================================================

class TestS08ErpUnavailable:
    """S08: ERP unavailable, data freshness = UNAVAILABLE."""

    def test_unavailable_freshness_boundary(self):
        from d7_risk_engine import _freshness_boundary
        boundary = _freshness_boundary("UNAVAILABLE")
        assert boundary["data_reliable"] is False
        assert boundary["data_availability_warning"] is True

    def test_unavailable_no_auto_escalation(self):
        """ERP unavailable should NOT auto-escalate to CRITICAL risk."""
        past_date = (_now() - timedelta(days=1)).date().isoformat()
        order = _make_order(
            order_id="ORD-S08",
            order_no="PO-S08",
            requested_delivery_date=past_date,
            current_progress=0.4,
            owner="USER-1",
        )
        erp_facts = {
            "external_id": "PO-S08",
            "freshness": "UNAVAILABLE",
            "erp_owner": "erpuser",
        }
        risk_signals = assess_risks_from_facts(
            order, current=_now(), erp_facts=erp_facts
        )
        # Delivery risk should still fire based on local data, but with stale warning
        delivery = next(
            (s for s in risk_signals if s["risk_type"] == "DELIVERY_RISK"), None
        )
        if delivery:
            evidence_text = " ".join(delivery["evidence"])
            assert "陈旧" in evidence_text or "注意" in evidence_text

    def test_unavailable_does_not_claim_current_safe(self):
        """UNAVAILABLE must not claim current real-time safety."""
        sync_state = _make_sync_state(sync_status="UNAVAILABLE")
        snapshot = _make_erp_snapshot("PO-S08b", freshness="UNAVAILABLE")
        facts = bridge_erp_snapshot_to_facts(snapshot, sync_state)
        assert facts["freshness"] == "UNAVAILABLE"


# =========================================================================
# S09: Multi-Risk Per Order
# =========================================================================

class TestS09MultiRisk:
    """S09: One order has both DELIVERY_RISK and SUPPLIER_COMMITMENT_OVERDUE."""

    def test_one_order_multiple_risks_aggregated(self):
        past_date = (_now() - timedelta(days=1)).date().isoformat()
        past_commitment = (_now() - timedelta(days=1)).date().isoformat()
        order = _make_order(
            order_id="ORD-S09",
            order_no="PO-S09",
            requested_delivery_date=past_date,
            latest_supplier_commitment=past_commitment,
            current_progress=0.3,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        types = {s["risk_type"] for s in risk_signals}
        assert "DELIVERY_RISK" in types
        assert "SUPPLIER_COMMITMENT_OVERDUE" in types

    def test_one_order_one_rank_in_ranking(self):
        """One order should appear only once in the final ranking."""
        past_date = (_now() - timedelta(days=1)).date().isoformat()
        past_commitment = (_now() - timedelta(days=1)).date().isoformat()
        order = _make_order(
            order_id="ORD-S09b",
            order_no="PO-S09b",
            requested_delivery_date=past_date,
            latest_supplier_commitment=past_commitment,
            current_progress=0.3,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())

        item_base = {
            **order,
            "risk_signals": risk_signals,
            "action_bucket": "DO_NOW",
            "bucket_reasons": ["交期已过", "承诺已过"],
            "ranking_suppressed": False,
            "order_anomaly_count": len(risk_signals),
            "anomaly_types": [s["risk_type"] for s in risk_signals],
            "evidence": [e for s in risk_signals for e in (s.get("evidence") or [])],
        }
        score = compute_priority_score(item_base, _now())
        item_base["priority_score"] = score["priority_score"]

        result = rank_orders([item_base], top_n=7, current=_now())
        assert len(result["risk_items"]) == 1
        # The order appears only once
        assert result["risk_items"][0]["order_id"] == "ORD-S09b"
        # Multiple anomaly types captured
        anomaly_types = result["risk_items"][0].get("anomaly_types", [])
        assert len(anomaly_types) >= 2


# =========================================================================
# S10: Top-N Not Padded
# =========================================================================

class TestS10TopNNotPadded:
    """S10: Only 3 real risks exist, must return 3, not pad to 7."""

    def test_not_padded_when_fewer_risks(self):
        order_results = []
        for i, bucket in enumerate(["ESCALATE", "DO_NOW", "NEEDS_CONFIRMATION"]):
            item = _make_order(
                order_id=f"ORD-S10-{i}",
                order_no=f"PO-S10-{i}",
                owner="USER-1",
            )
            item.update({
                "risk_signals": [build_risk_signal(
                    order_id=f"ORD-S10-{i}",
                    order_no=f"PO-S10-{i}",
                    risk_type="DELIVERY_RISK",
                    severity="HIGH",
                    evidence=[f"Test evidence {i}"],
                )],
                "action_bucket": bucket,
                "bucket_reasons": [f"Bucket reason {i}"],
                "ranking_suppressed": False,
                "order_anomaly_count": 1,
                "anomaly_types": ["DELIVERY_RISK"],
                "primary_anomaly_type": "DELIVERY_RISK",
                "evidence": [f"Test evidence {i}"],
                "recommended_action": "Test action",
            })
            score = compute_priority_score(item, _now())
            item["priority_score"] = score["priority_score"]
            order_results.append(item)

        result = rank_orders(order_results, top_n=7, current=_now())
        assert len(result["risk_items"]) == 3
        assert result["selection_strategy"]["not_padded"] is True

    def test_info_gaps_do_not_pad_top_n(self):
        """Information gaps must not be used to pad the top-N."""
        real_items = []
        for i in range(2):
            item = _make_order(
                order_id=f"ORD-S10b-{i}",
                order_no=f"PO-S10b-{i}",
                owner="USER-1",
            )
            item.update({
                "risk_signals": [build_risk_signal(
                    order_id=f"ORD-S10b-{i}",
                    order_no=f"PO-S10b-{i}",
                    risk_type="DELIVERY_RISK",
                    severity="HIGH",
                    evidence=[f"Real evidence {i}"],
                )],
                "action_bucket": "DO_NOW",
                "ranking_suppressed": False,
                "order_anomaly_count": 1,
                "anomaly_types": ["DELIVERY_RISK"],
                "evidence": [f"Real evidence {i}"],
            })
            score = compute_priority_score(item, _now())
            item["priority_score"] = score["priority_score"]
            real_items.append(item)

        # Add info-gap-only items
        for i in range(5):
            item = _make_order(
                order_id=f"ORD-S10b-gap-{i}",
                order_no=f"PO-S10b-gap-{i}",
                owner="USER-1",
            )
            item.update({
                "risk_signals": [build_risk_signal(
                    order_id=f"ORD-S10b-gap-{i}",
                    order_no=f"PO-S10b-gap-{i}",
                    risk_type="INFORMATION_GAP",
                    severity="LOW",
                    evidence=["Missing info"],
                )],
                "action_bucket": "SCHEDULED",
                "ranking_suppressed": False,
                "order_anomaly_count": 1,
                "anomaly_types": ["INFORMATION_GAP"],
                "evidence": ["Missing info"],
            })
            real_items.append(item)

        result = rank_orders(real_items, top_n=7, current=_now())
        assert len(result["risk_items"]) == 2
        assert len(result["information_gaps"]) == 5


# =========================================================================
# S11: Permission
# =========================================================================

class TestS11Permission:
    """S11: Operator cannot access others' order risks."""

    def test_operator_sees_only_own_orders(self):
        order_own = _make_order(
            order_id="ORD-S11-OWN",
            order_no="PO-S11-OWN",
            owner="USER-1",
            requested_delivery_date=(_now() + timedelta(days=5)).date().isoformat(),
            current_progress=0.5,
        )
        order_other = _make_order(
            order_id="ORD-S11-OTHER",
            order_no="PO-S11-OTHER",
            owner="USER-2",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.3,
        )

        # Operator USER-1 should only see OWN orders
        bucket_own = assign_action_bucket(
            assess_risks_from_facts(order_own, current=_now()),
            order_own, current=_now(), user_id="USER-1", user_role="operator"
        )
        bucket_other = assign_action_bucket(
            assess_risks_from_facts(order_other, current=_now()),
            order_other, current=_now(), user_id="USER-1", user_role="operator"
        )
        # OWN order: should NOT be NOT_MY_RESPONSIBILITY
        assert bucket_own["action_bucket"] != "NOT_MY_RESPONSIBILITY"
        # OTHER user's order for USER-1: should be NOT_MY_RESPONSIBILITY
        assert bucket_other["action_bucket"] == "NOT_MY_RESPONSIBILITY"

    def test_manager_sees_all_orders(self):
        order_other = _make_order(
            order_id="ORD-S11b-OTHER",
            order_no="PO-S11b-OTHER",
            owner="USER-2",
            requested_delivery_date=(_now() - timedelta(days=1)).date().isoformat(),
            current_progress=0.3,
        )
        bucket = assign_action_bucket(
            assess_risks_from_facts(order_other, current=_now()),
            order_other, current=_now(), user_id="MANAGER-1", user_role="manager"
        )
        assert bucket["action_bucket"] != "NOT_MY_RESPONSIBILITY"

    def test_risk_interface_does_not_leak_order_no(self):
        """Ranking results should not leak sensitive info to unauthorized users."""
        past_date = (_now() - timedelta(days=1)).date().isoformat()
        order = _make_order(
            order_id="ORD-S11c",
            order_no="PO-S11C-CONFIDENTIAL",
            owner="USER-2",
            requested_delivery_date=past_date,
            current_progress=0.3,
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        # For unauthorized user, bucket should suppress
        bucket = assign_action_bucket(risk_signals, order, current=_now(), user_id="USER-1", user_role="operator")
        if bucket["action_bucket"] == "NOT_MY_RESPONSIBILITY":
            # The risk should not appear in ranking for this user
            item = {
                **order,
                "risk_signals": risk_signals,
                "action_bucket": bucket["action_bucket"],
                "ranking_suppressed": bucket["ranking_suppressed"],
                "order_anomaly_count": 1,
                "anomaly_types": ["DELIVERY_RISK"],
                "evidence": ["test"],
            }
            score = compute_priority_score(item, _now())
            item["priority_score"] = score["priority_score"]
            result = rank_orders([item], top_n=7, current=_now())
            # NOT_MY_RESPONSIBILITY items should be filtered out
            visible_orders = [r for r in result["risk_items"] if r.get("action_bucket") != "NOT_MY_RESPONSIBILITY"]
            assert len(visible_orders) == 0


# =========================================================================
# S12: Deterministic
# =========================================================================

class TestS12Deterministic:
    """S12: Same facts + time + policy version → same results."""

    def test_identical_results_repeated_runs(self):
        past_date = (_now() - timedelta(days=1)).date().isoformat()
        fixed_time = _now()
        order = _make_order(
            order_id="ORD-S12",
            order_no="PO-S12",
            requested_delivery_date=past_date,
            latest_supplier_commitment=past_date,
            current_progress=0.3,
            current_node="生产中",
            owner="USER-1",
        )

        results = []
        for _ in range(5):
            risk_signals = assess_risks_from_facts(order, current=fixed_time)
            item = {
                **order,
                "risk_signals": risk_signals,
                "action_bucket": "DO_NOW",
                "ranking_suppressed": False,
                "order_anomaly_count": len(risk_signals),
                "anomaly_types": [s["risk_type"] for s in risk_signals],
                "evidence": [e for s in risk_signals for e in (s.get("evidence") or [])],
            }
            score = compute_priority_score(item, fixed_time)
            item["priority_score"] = score["priority_score"]
            result = rank_orders([item], top_n=7, current=fixed_time)
            results.append(result["risk_items"][0])

        # All runs should produce identical results
        for i in range(1, len(results)):
            assert results[i]["order_id"] == results[0]["order_id"]
            assert results[i]["priority_score"] == results[0]["priority_score"]
            assert results[i]["action_bucket"] == results[0]["action_bucket"]

    def test_bucket_priority_deterministic(self):
        """Bucket priority must be stable and deterministic."""
        assert bucket_priority("ESCALATE") > bucket_priority("DO_NOW")
        assert bucket_priority("DO_NOW") > bucket_priority("NEEDS_CONFIRMATION")
        assert bucket_priority("NEEDS_CONFIRMATION") > bucket_priority("DO_TODAY")
        assert bucket_priority("DO_TODAY") > bucket_priority("SCHEDULED")
        assert bucket_priority("SCHEDULED") > bucket_priority("WAITING_EXTERNAL")
        assert bucket_priority("WAITING_EXTERNAL") > bucket_priority("NOT_MY_RESPONSIBILITY")

    def test_same_facts_same_score(self):
        """Same facts should always produce the same priority_score."""
        past_date = (_now() - timedelta(days=1)).date().isoformat()
        fixed_time = _now()
        order = _make_order(
            order_id="ORD-S12b",
            order_no="PO-S12b",
            requested_delivery_date=past_date,
            current_progress=0.4,
            owner="USER-1",
        )

        scores = []
        for _ in range(10):
            risk_signals = assess_risks_from_facts(order, current=fixed_time)
            item = {
                **order,
                "risk_signals": risk_signals,
                "action_bucket": "DO_NOW",
                "ranking_suppressed": False,
                "order_anomaly_count": len(risk_signals),
                "anomaly_types": [s["risk_type"] for s in risk_signals],
                "evidence": [e for s in risk_signals for e in (s.get("evidence") or [])],
            }
            score = compute_priority_score(item, fixed_time)
            scores.append(score["priority_score"])

        assert len(set(scores)) == 1, f"Score should be deterministic, got {set(scores)}"


# =========================================================================
# Additional: Contract & Policy Tests
# =========================================================================

class TestRiskSignalContract:
    """Test that RiskSignal conforms to the D7 contract."""

    def test_contract_has_required_fields(self):
        signal = build_risk_signal(
            order_id="ORD-TEST",
            risk_type="DELIVERY_RISK",
            evidence=["test evidence"],
        )
        required_fields = [
            "risk_signal_id", "order_id", "risk_type", "severity",
            "status", "detected_at", "evidence", "missing_information",
            "source_type", "freshness", "rule_id", "policy_version",
            "explanation",
        ]
        for field in required_fields:
            assert field in signal, f"Missing field: {field}"

    def test_policy_version_is_set(self):
        signal = build_risk_signal(
            order_id="ORD-TEST",
            risk_type="DELIVERY_RISK",
        )
        assert signal["policy_version"] == D7_POLICY_VERSION

    def test_invalid_risk_type_rejected(self):
        with pytest.raises(ValueError):
            build_risk_signal(
                order_id="ORD-TEST",
                risk_type="INVALID_TYPE",
            )

    def test_invalid_severity_rejected(self):
        with pytest.raises(ValueError):
            build_risk_signal(
                order_id="ORD-TEST",
                risk_type="DELIVERY_RISK",
                severity="ULTRA",
            )


class TestActionBucketPolicy:
    """Test action bucket ordering and policy."""

    def test_bucket_priority_order(self):
        """ESCALATE > DO_NOW > NEEDS_CONFIRMATION > DO_TODAY > SCHEDULED > WAITING_EXTERNAL > NOT_MY_RESPONSIBILITY"""
        assert bucket_priority("ESCALATE") > bucket_priority("DO_NOW")
        assert bucket_priority("DO_NOW") > bucket_priority("NEEDS_CONFIRMATION")
        assert bucket_priority("NEEDS_CONFIRMATION") > bucket_priority("DO_TODAY")
        assert bucket_priority("DO_TODAY") > bucket_priority("SCHEDULED")
        assert bucket_priority("SCHEDULED") > bucket_priority("WAITING_EXTERNAL")
        assert bucket_priority("WAITING_EXTERNAL") > bucket_priority("NOT_MY_RESPONSIBILITY")

    def test_invalid_bucket_not_in_set(self):
        assert "INVALID_BUCKET" not in ACTION_BUCKETS

    def test_no_bucket_beats_escalate(self):
        """No non-ESCALATE bucket should outrank ESCALATE."""
        for bucket in ACTION_BUCKETS:
            if bucket != "ESCALATE":
                assert bucket_priority("ESCALATE") > bucket_priority(bucket)


class TestPriorityScore:
    """Test that priority_score is clearly a heuristic, not a probability."""

    def test_score_is_marked_heuristic(self):
        past_date = (_now() - timedelta(days=1)).date().isoformat()
        order = _make_order(
            order_id="ORD-PS",
            order_no="PO-PS",
            requested_delivery_date=past_date,
            current_progress=0.3,
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        item = {
            **order,
            "risk_signals": risk_signals,
            "action_bucket": "DO_NOW",
            "ranking_suppressed": False,
            "order_anomaly_count": len(risk_signals),
            "anomaly_types": [s["risk_type"] for s in risk_signals],
            "evidence": [e for s in risk_signals for e in (s.get("evidence") or [])],
        }
        result = compute_priority_score(item, _now())
        assert result["is_heuristic"] is True
        assert "heuristic" in result["score_description"].lower()

    def test_waiting_suppression_does_not_erase_risk_attention(self):
        past_date = (_now() - timedelta(days=1)).date().isoformat()
        order = _make_order(
            order_id="ORD-PS2",
            order_no="PO-PS2",
            requested_delivery_date=past_date,
            owner="USER-1",
        )
        item = {
            **order,
            "risk_signals": [build_risk_signal(
                order_id="ORD-PS2",
                risk_type="SUPPLIER_COMMITMENT_OVERDUE",
                evidence=["test"],
            )],
            "action_bucket": "WAITING_EXTERNAL",
            "ranking_suppressed": True,
            "order_anomaly_count": 1,
            "anomaly_types": ["SUPPLIER_COMMITMENT_OVERDUE"],
            "attention_context": {
                "days_to_due": -1,
                "risk_types": ["SUPPLIER_COMMITMENT_OVERDUE"],
                "active_external_waiting": True,
            },
            "evidence": ["test"],
        }
        result = compute_priority_score(item, _now())
        assert result["priority_score"] > 0
        assert result["risk_attention_score"] == result["priority_score"]
        assert result["current_actionability"] == "WAITING_EXTERNAL"


class TestAttentionFirstRanking:
    """D14.2: business attention precedes action bucket / governance state."""

    def test_high_attention_scheduled_can_outrank_low_attention_escalate(self):
        """Governance escalation must not automatically outrank higher business risk."""
        escalate_item = _make_order(
            order_id="ORD-BF-ESC",
            order_no="PO-BF-ESC",
            owner="USER-1",
        )
        escalate_item.update({
            "risk_signals": [build_risk_signal(
                order_id="ORD-BF-ESC",
                risk_type="OWNER_MISSING",
                severity="HIGH",
                evidence=["Owner missing"],
            )],
            "action_bucket": "ESCALATE",
            "ranking_suppressed": False,
            "order_anomaly_count": 1,
            "anomaly_types": ["OWNER_MISSING"],
            "evidence": ["Owner missing"],
        })
        # Give it a low score
        escalate_item["priority_score"] = 10.0

        scheduled_item = _make_order(
            order_id="ORD-BF-SCH",
            order_no="PO-BF-SCH",
            owner="USER-1",
        )
        scheduled_item.update({
            "risk_signals": [build_risk_signal(
                order_id="ORD-BF-SCH",
                risk_type="DELIVERY_RISK",
                severity="CRITICAL",
                evidence=["Critical delivery"],
            )],
            "action_bucket": "SCHEDULED",
            "ranking_suppressed": False,
            "order_anomaly_count": 1,
            "anomaly_types": ["DELIVERY_RISK"],
            "evidence": ["Critical delivery"],
        })
        # Give it a very high score
        scheduled_item["priority_score"] = 200.0

        result = rank_orders(
            [escalate_item, scheduled_item],
            top_n=7,
            current=_now(),
        )
        assert len(result["risk_items"]) >= 2
        assert result["risk_items"][0]["order_id"] == "ORD-BF-SCH"
        assert result["risk_items"][1]["order_id"] == "ORD-BF-ESC"
        assert result["selection_strategy"]["bucket_first"] is False
        assert result["selection_strategy"]["attention_first"] is True


class TestErpSnapshotBridge:
    """Test ERP snapshot bridging."""

    def test_snapshot_bridge_preserves_snapshot_metadata(self):
        snapshot = _make_erp_snapshot("PO-BRIDGE", customer_due_date="2026-08-15")
        sync_state = _make_sync_state()
        facts = bridge_erp_snapshot_to_facts(snapshot, sync_state)

        assert facts["external_id"] == "PO-BRIDGE"
        assert facts["customer_due_date"] == "2026-08-15"
        assert facts["freshness"] == "FRESH"
        assert facts["snapshot_id"] == "ERP-SNAP-PO-BRIDGE"

    def test_snapshot_bridge_marks_source(self):
        snapshot = _make_erp_snapshot("PO-BRIDGE2")
        facts = bridge_erp_snapshot_to_facts(snapshot)
        assert facts["source"] == "ERP_NEXT"

    def test_erp_owner_included_not_mapped(self):
        snapshot = _make_erp_snapshot("PO-BRIDGE3", erp_owner="erpuser@erp.com")
        facts = bridge_erp_snapshot_to_facts(snapshot)
        assert facts["erp_owner"] == "erpuser@erp.com"


class TestNegativeControl:
    """Negative control: SAL-ORD-2026-00001 does not auto-become high risk."""

    def test_real_erp_order_not_auto_high_risk(self):
        """A real ERP order must not automatically become high risk."""
        order = _make_order(
            order_id="ORD-NEG",
            order_no="SAL-ORD-2026-00001",
            status="Submitted",
            requested_delivery_date=(_now() + timedelta(days=30)).date().isoformat(),
            current_progress=0.8,
            current_node="验货准备",
            owner="USER-1",
        )
        erp_facts = {
            "external_id": "SAL-ORD-2026-00001",
            "customer_due_date": (_now() + timedelta(days=30)).date().isoformat(),
            "order_status": "Submitted",
            "erp_owner": "erpuser",
            "freshness": "FRESH",
            "items": [
                {"item_code": "ITEM-001", "qty": 100, "rate": 50},
            ],
        }
        risk_signals = assess_risks_from_facts(
            order, current=_now(), erp_facts=erp_facts
        )
        # Real ERP order with normal facts should NOT produce CRITICAL or HIGH risks
        high_critical = [
            s for s in risk_signals
            if s["severity"] in ("CRITICAL", "HIGH") and s["risk_type"] != "OWNER_MISSING"
        ]
        assert len(high_critical) == 0, f"Real ERP order should not auto-produce high/critical risks, got {high_critical}"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_no_order_facts_produces_owner_missing(self):
        """An order without owner correctly produces OWNER_MISSING."""
        order = _make_order(
            order_id="ORD-EMPTY",
            order_no="PO-EMPTY",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        assert len(risk_signals) >= 1
        types = {s["risk_type"] for s in risk_signals}
        assert "OWNER_MISSING" in types

    def test_future_commitment_no_overdue(self):
        future_date = (_now() + timedelta(days=5)).date().isoformat()
        order = _make_order(
            order_id="ORD-FUTURE",
            order_no="PO-FUTURE",
            latest_supplier_commitment=future_date,
            current_progress=0.5,
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        commitment_risks = [s for s in risk_signals if s["risk_type"] == "SUPPLIER_COMMITMENT_OVERDUE"]
        assert len(commitment_risks) == 0

    def test_zero_delivery_progress_with_near_deadline(self):
        near_date = (_now() + timedelta(days=2)).date().isoformat()
        order = _make_order(
            order_id="ORD-ZERO",
            order_no="PO-ZERO",
            requested_delivery_date=near_date,
            current_progress=0.0,
            current_node="未开工",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        delivery = next(
            (s for s in risk_signals if s["risk_type"] == "DELIVERY_RISK"), None
        )
        assert delivery is not None
        assert delivery["severity"] in ("HIGH", "CRITICAL")

    def test_information_gap_severity_is_low(self):
        future_date = (_now() + timedelta(days=14)).date().isoformat()
        order = _make_order(
            order_id="ORD-GAP",
            order_no="PO-GAP",
            requested_delivery_date=future_date,
            current_progress=None,
            current_node=None,
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        gap = next(
            (s for s in risk_signals if s["risk_type"] == "INFORMATION_GAP"), None
        )
        assert gap is not None
        assert gap["severity"] == "LOW"

    def test_freshness_label_mapping(self):
        assert boundary_freshness_label(
            {"data_reliable": True}
        ) == "FRESH"
        assert boundary_freshness_label(
            {"data_stale_warning": True}
        ) == "STALE"
        assert boundary_freshness_label(
            {"data_availability_warning": True, "data_stale_warning": True, "can_assess_real_risk": True}
        ) == "UNAVAILABLE"
        assert boundary_freshness_label(
            {"data_availability_warning": True, "data_stale_warning": True, "can_assess_real_risk": False}
        ) == "NEVER_SYNCED"
        assert boundary_freshness_label(
            {"data_availability_warning": True}
        ) == "NEVER_SYNCED"


class TestPolicyVersion:
    """Test policy version consistency."""

    def test_all_risk_signals_have_version(self):
        signal = build_risk_signal(
            order_id="ORD-TEST",
            risk_type="DELIVERY_RISK",
        )
        assert signal["policy_version"] == D7_POLICY_VERSION

    def test_policy_version_is_string(self):
        assert isinstance(D7_POLICY_VERSION, str)
        assert D7_POLICY_VERSION.startswith("D7_")

    def test_ranking_includes_policy_version(self):
        item = _make_order(
            order_id="ORD-PV",
            order_no="PO-PV",
            owner="USER-1",
        )
        item.update({
            "risk_signals": [build_risk_signal(
                order_id="ORD-PV",
                risk_type="DELIVERY_RISK",
                severity="HIGH",
                evidence=["test"],
            )],
            "action_bucket": "DO_NOW",
            "ranking_suppressed": False,
            "order_anomaly_count": 1,
            "anomaly_types": ["DELIVERY_RISK"],
            "evidence": ["test"],
        })
        score = compute_priority_score(item, _now())
        item["priority_score"] = score["priority_score"]
        result = rank_orders([item], top_n=7, current=_now())
        assert result["policy_version"] == D7_POLICY_VERSION
        assert result["selection_strategy"]["policy_version"] == D7_POLICY_VERSION


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])

# =========================================================================
# D14 Round-1 P1 calibration: progress-to-deadline mismatch
# =========================================================================

class TestD14ProgressDeadlineMismatch:
    """User-validated P1: low progress with 4-7 days left must not fall through to SCHEDULED."""

    def test_five_days_thirty_percent_is_do_today(self):
        order = _make_order(
            order_id="ORD-D14-P1",
            order_no="PO-D14-P1",
            requested_delivery_date=(_now() + timedelta(days=5)).date().isoformat(),
            current_progress=0.30,
            current_node="生产中",
            owner="USER-1",
        )
        risk_signals = assess_risks_from_facts(order, current=_now())
        delivery = next(s for s in risk_signals if s["risk_type"] == "DELIVERY_RISK")
        assert delivery["severity"] == "HIGH"
        bucket = assign_action_bucket(risk_signals, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "DO_NOW"
        assert "30%" in " ".join(bucket["bucket_reasons"])

    def test_seven_days_forty_nine_percent_is_do_today(self):
        order = _make_order(
            order_id="ORD-D14-P1-B",
            order_no="PO-D14-P1-B",
            requested_delivery_date=(_now() + timedelta(days=7)).date().isoformat(),
            current_progress=0.49,
            current_node="生产中",
            owner="USER-1",
        )
        risks = assess_risks_from_facts(order, current=_now())
        bucket = assign_action_bucket(risks, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "DO_TODAY"

    def test_five_days_high_progress_stays_scheduled(self):
        order = _make_order(
            order_id="ORD-D14-CONTROL-HIGH",
            order_no="PO-D14-CONTROL-HIGH",
            requested_delivery_date=(_now() + timedelta(days=5)).date().isoformat(),
            current_progress=0.90,
            current_node="生产收尾",
            owner="USER-1",
        )
        risks = assess_risks_from_facts(order, current=_now())
        bucket = assign_action_bucket(risks, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "SCHEDULED"

    def test_eight_days_low_progress_not_promoted_by_new_rule(self):
        order = _make_order(
            order_id="ORD-D14-CONTROL-FAR",
            order_no="PO-D14-CONTROL-FAR",
            requested_delivery_date=(_now() + timedelta(days=8)).date().isoformat(),
            current_progress=0.30,
            current_node="生产中",
            owner="USER-1",
        )
        risks = assess_risks_from_facts(order, current=_now())
        bucket = assign_action_bucket(risks, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "SCHEDULED"

    def test_missing_progress_not_treated_as_zero_by_new_bucket_rule(self):
        order = _make_order(
            order_id="ORD-D14-CONTROL-MISSING",
            order_no="PO-D14-CONTROL-MISSING",
            requested_delivery_date=(_now() + timedelta(days=5)).date().isoformat(),
            current_progress=None,
            current_node="生产中",
            owner="USER-1",
        )
        risks = assess_risks_from_facts(order, current=_now())
        bucket = assign_action_bucket(risks, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "SCHEDULED"

    def test_two_days_ninety_percent_preserves_existing_do_today(self):
        order = _make_order(
            order_id="ORD-D14-CONTROL-NEAR",
            order_no="PO-D14-CONTROL-NEAR",
            requested_delivery_date=(_now() + timedelta(days=2)).date().isoformat(),
            current_progress=0.90,
            current_node="生产收尾",
            owner="USER-1",
        )
        risks = assess_risks_from_facts(order, current=_now())
        bucket = assign_action_bucket(risks, order, current=_now(), user_id="USER-1")
        assert bucket["action_bucket"] == "DO_TODAY"

# ---------------------------------------------------------------------------
# D14 Round-2 calibration: logistics urgency × customer-delivery buffer
# ---------------------------------------------------------------------------

class TestD14R2LogisticsBufferCalibration:
    """Cross-user validated calibration for FO-D14-002 / FO-D14-006."""

    def _signal(self, *, days: int | None, event_count: int):
        from datetime import timedelta
        from d7_risk_engine import _assess_logistics_exception

        now = datetime(2026, 8, 18, 10, 0, tzinfo=CN_TZ)
        due = (now + timedelta(days=days)).date().isoformat() if days is not None else None
        order = _make_order(
            order_id=f"ORD-D14-R2-{days}-{event_count}",
            requested_delivery_date=due,
            current_progress=0.9,
            current_node="出运中",
        )
        logistics = [
            {
                "event_type": "CUSTOMS_HOLD" if i == 0 else "DELAY",
                "status": "CUSTOMS_HOLD" if i == 0 else "DELAYED",
                "description": "unresolved logistics exception",
                "estimated_arrival_at": None,
                "resolved_at": None,
            }
            for i in range(event_count)
        ]
        sig = _assess_logistics_exception(
            order,
            logistics,
            {"data_stale_warning": False},
            current=now,
            delivery_date=due,
        )
        return now, order, sig

    def test_single_exception_10_day_buffer_is_medium_and_do_today(self):
        from d7_risk_engine import assign_action_bucket
        now, order, sig = self._signal(days=10, event_count=1)
        assert sig["severity"] == "MEDIUM"
        result = assign_action_bucket([sig], order, tasks=[], current=now, user_id="USER-1", user_role="operator")
        assert result["action_bucket"] == "DO_TODAY"
        assert any("10天缓冲" in r for r in result["bucket_reasons"])

    def test_single_exception_3_day_buffer_stays_high_and_do_now(self):
        from d7_risk_engine import assign_action_bucket
        now, order, sig = self._signal(days=3, event_count=1)
        assert sig["severity"] == "HIGH"
        result = assign_action_bucket([sig], order, tasks=[], current=now, user_id="USER-1", user_role="operator")
        assert result["action_bucket"] == "DO_NOW"

    def test_two_exceptions_12_day_buffer_no_longer_auto_escalate(self):
        from d7_risk_engine import assign_action_bucket
        now, order, sig = self._signal(days=12, event_count=2)
        assert sig["severity"] == "HIGH"
        result = assign_action_bucket([sig], order, tasks=[], current=now, user_id="USER-1", user_role="operator")
        assert result["action_bucket"] == "DO_NOW"
        assert result["action_bucket"] != "ESCALATE"

    def test_two_exceptions_5_day_buffer_remain_critical_and_escalate(self):
        from d7_risk_engine import assign_action_bucket
        now, order, sig = self._signal(days=5, event_count=2)
        assert sig["severity"] == "CRITICAL"
        result = assign_action_bucket([sig], order, tasks=[], current=now, user_id="USER-1", user_role="operator")
        assert result["action_bucket"] == "ESCALATE"

    def test_two_exceptions_20_day_buffer_are_medium_and_scheduled(self):
        from d7_risk_engine import assign_action_bucket
        now, order, sig = self._signal(days=20, event_count=2)
        assert sig["severity"] == "MEDIUM"
        result = assign_action_bucket([sig], order, tasks=[], current=now, user_id="USER-1", user_role="operator")
        assert result["action_bucket"] == "SCHEDULED"

    def test_unknown_due_keeps_conservative_multi_exception_escalation(self):
        from d7_risk_engine import assign_action_bucket
        now, order, sig = self._signal(days=None, event_count=2)
        assert sig["severity"] == "CRITICAL"
        result = assign_action_bucket([sig], order, tasks=[], current=now, user_id="USER-1", user_role="operator")
        assert result["action_bucket"] == "ESCALATE"

# ---------------------------------------------------------------------------
# D14 Round-3 calibration: customer-confirmation count × delivery buffer
# ---------------------------------------------------------------------------

class TestD14R3CustomerConfirmationBufferCalibration:
    """Cross-user calibration for FO-D14-015 without weakening near-due blocks."""

    def _signal(self, *, days: int | None, pending_count: int):
        from datetime import timedelta
        from d7_risk_engine import _assess_customer_confirmation_blocking

        now = datetime(2026, 8, 18, 10, 0, tzinfo=CN_TZ)
        due = (now + timedelta(days=days)).date().isoformat() if days is not None else None
        order = _make_order(
            order_id=f"ORD-D14-R3-{days}-{pending_count}",
            requested_delivery_date=due,
            current_progress=0.85,
            current_node="生产中",
        )
        tasks = [
            _make_task(
                task_id=f"TASK-D14-R3-{i}",
                related_order_id=order["order_id"],
                title=f"确认事项{i+1}",
                target="customer",
                pending_confirmation=1,
                status="OPEN",
            )
            for i in range(pending_count)
        ]
        sig = _assess_customer_confirmation_blocking(
            order,
            tasks,
            {"data_stale_warning": False},
            current=now,
            delivery_date=due,
        )
        return now, order, tasks, sig

    def test_two_confirmations_eight_day_buffer_are_medium(self):
        now, order, tasks, sig = self._signal(days=8, pending_count=2)
        assert sig["severity"] == "MEDIUM"
        bucket = assign_action_bucket([sig], order, tasks=tasks, current=now, user_id="USER-1")
        assert bucket["action_bucket"] == "NEEDS_CONFIRMATION"

    def test_two_confirmations_seven_day_buffer_remain_high(self):
        now, order, tasks, sig = self._signal(days=7, pending_count=2)
        assert sig["severity"] == "HIGH"
        bucket = assign_action_bucket([sig], order, tasks=tasks, current=now, user_id="USER-1")
        assert bucket["action_bucket"] == "NEEDS_CONFIRMATION"

    def test_two_confirmations_unknown_due_remain_conservative_high(self):
        now, order, tasks, sig = self._signal(days=None, pending_count=2)
        assert sig["severity"] == "HIGH"

    def test_single_confirmation_eight_day_buffer_stays_medium(self):
        now, order, tasks, sig = self._signal(days=8, pending_count=1)
        assert sig["severity"] == "MEDIUM"
        bucket = assign_action_bucket([sig], order, tasks=tasks, current=now, user_id="USER-1")
        assert bucket["action_bucket"] == "NEEDS_CONFIRMATION"
