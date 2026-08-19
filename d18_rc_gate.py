from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Iterable

D18_RC_POLICY_VERSION = "D18_ENGINEERING_RC_GATE_V1"

SEVEN_LAYER_TEST_MODEL = (
    ("L1_BUSINESS_RULES", "Deterministic business rules / D14.2 ranking"),
    ("L2_DATA_SCHEMA", "Schema, migrations and persistence"),
    ("L3_API_SECURITY", "API auth, RBAC and organization isolation"),
    ("L4_AGENT_SKILL_MODEL", "Controlled Agent, Skill, model routing and Tool choice"),
    ("L5_HUMAN_ACTION", "Human Review, Action Workspace and BusinessAction/Outbox"),
    ("L6_DURABILITY_OBSERVABILITY", "RESULT_UNCERTAIN, recovery, observability and feature flags"),
    ("L7_RELEASE_FULL_REGRESSION", "Backup/rollback rehearsal and full regression"),
)

BLOCKING_SEVERITIES = {"P0", "P1"}
PASS_LIKE = {"PASS", "N/A_BY_SCOPE", "NOT_ADOPTED_WITH_EVIDENCE"}


@dataclass(frozen=True)
class RCGate:
    gate_id: str
    severity: str
    status: str
    evidence: str
    limitation: str = ""


def _runtime_imports(root: str | Path) -> str:
    root = Path(root)
    parts: list[str] = []
    for path in root.glob("*.py"):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                parts.append(stripped.lower())
    req = root / "requirements.txt"
    if req.exists():
        parts.extend(x.strip().lower() for x in req.read_text(encoding="utf-8", errors="ignore").splitlines())
    return "\n".join(parts)


def has_runtime_dependency(root: str | Path, token: str) -> bool:
    token = token.lower().strip()
    corpus = _runtime_imports(root)
    return bool(re.search(rf"(^|[^a-z0-9_]){re.escape(token)}([^a-z0-9_]|$)", corpus, flags=re.MULTILINE))


def scope_decisions(source_root: str | Path) -> dict[str, dict[str, str]]:
    mcp_present = has_runtime_dependency(source_root, "mcp")
    return {
        "MCP": {
            "status": "PASS" if mcp_present else "N/A_BY_SCOPE",
            "reason": (
                "Runtime MCP dependency detected and must pass the MCP gate."
                if mcp_present else
                "No MCP client/server runtime dependency exists in the frozen source; Controlled Tools remain direct in-process/API contracts. Do not add MCP for portfolio completeness."
            ),
        },
        "DECISION_REVIEW": {
            "status": "NOT_ADOPTED_WITH_EVIDENCE",
            "reason": (
                "D14 repeated user-ranking calibration found the dominant bad cases were deterministic Risk/Ranking policy and product-semantics issues; Decision Review remained NOT_TRIGGERED / not needed. The RC therefore preserves the simpler controlled-agent path instead of adding an LLM reviewer without demonstrated net benefit."
            ),
        },
    }


def rc_decision(gates: Iterable[RCGate]) -> dict[str, Any]:
    items = list(gates)
    blockers = [
        g for g in items
        if g.severity in BLOCKING_SEVERITIES and g.status not in PASS_LIKE
    ]
    return {
        "policy_version": D18_RC_POLICY_VERSION,
        "status": "PASS" if not blockers else "BLOCKED",
        "gate_count": len(items),
        "blocking_count": len(blockers),
        "blockers": [asdict(x) for x in blockers],
        "gates": [asdict(x) for x in items],
        "release_label": "ENGINEERING_RC" if not blockers else "NOT_RC",
        "production_claim": False,
        "note": "Engineering RC is not a production deployment claim and does not replace D19 target-user Shadow evidence.",
    }


def rc_contract(source_root: str | Path) -> dict[str, Any]:
    return {
        "policy_version": D18_RC_POLICY_VERSION,
        "layers": [{"layer": k, "meaning": v} for k, v in SEVEN_LAYER_TEST_MODEL],
        "scope_decisions": scope_decisions(source_root),
        "hard_red_lines": [
            "Cross-organization access or privilege bypass => block RC.",
            "Human Review / D10 BusinessAction boundary bypass => block RC.",
            "RESULT_UNCERTAIN displayed as success or auto-retried => block RC.",
            "Secret leakage into public trace/API => block RC.",
            "D14.2 ranking regression against frozen tests => block RC.",
            "Backup/restore or rollback artifact integrity failure => block RC.",
        ],
        "known_limitations_required": [
            "No real ERP write-back adapter in current product.",
            "No external email/WeCom notification adapter in current product.",
            "No MCP runtime because no justified multi-client/tool protocol need exists.",
            "Decision Review not adopted because D14 evidence did not show net benefit trigger.",
            "No claim of production deployment/ROI/long-term adoption; D19 target-user Shadow remains separate evidence.",
        ],
    }
