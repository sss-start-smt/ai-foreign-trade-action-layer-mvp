from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import d18_rc_gate as d18


def test_current_source_has_no_mcp_runtime_dependency():
    root = Path(__file__).resolve().parents[1]
    assert d18.has_runtime_dependency(root, "mcp") is False
    assert d18.scope_decisions(root)["MCP"]["status"] == "N/A_BY_SCOPE"


def test_decision_review_is_explicit_non_adoption_not_silent_omission():
    root = Path(__file__).resolve().parents[1]
    decision = d18.scope_decisions(root)["DECISION_REVIEW"]
    assert decision["status"] == "NOT_ADOPTED_WITH_EVIDENCE"
    assert "D14" in decision["reason"]
    assert "Risk/Ranking" in decision["reason"]


def test_p0_or_p1_failure_blocks_rc():
    result = d18.rc_decision([
        d18.RCGate("G1", "P0", "PASS", "ok"),
        d18.RCGate("G2", "P1", "FAIL", "bad"),
    ])
    assert result["status"] == "BLOCKED"
    assert result["blocking_count"] == 1


def test_scope_na_and_evidence_based_non_adoption_do_not_block_rc():
    result = d18.rc_decision([
        d18.RCGate("G1", "P0", "PASS", "ok"),
        d18.RCGate("MCP", "P1", "N/A_BY_SCOPE", "no runtime dependency"),
        d18.RCGate("REVIEW", "P1", "NOT_ADOPTED_WITH_EVIDENCE", "D14 no trigger"),
    ])
    assert result["status"] == "PASS"
    assert result["release_label"] == "ENGINEERING_RC"
    assert result["production_claim"] is False


def test_rc_contract_keeps_real_world_limits_visible():
    root = Path(__file__).resolve().parents[1]
    contract = d18.rc_contract(root)
    limits = " ".join(contract["known_limitations_required"])
    assert len(contract["layers"]) == 7
    assert "No real ERP write-back" in limits
    assert "D19 target-user Shadow" in limits
