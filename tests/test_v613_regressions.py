import os
from pathlib import Path

os.environ.setdefault("DB_PATH", str(Path(__file__).parent / "test_action_layer.db"))
os.environ.setdefault("APP_API_KEY", "test-key")
os.environ.setdefault("SEED_DEMO_DATA", "true")

from fastapi.testclient import TestClient

import agent_api
from main import app, db, init_db

client = TestClient(app)
HEADERS = {"X-FlowOrder-Agent-Key": "agent-test-key"}


def setup_function():
    agent_api.AGENT_API_KEY = "agent-test-key"
    init_db()
    assert client.post("/api/reset").status_code == 200
    assert client.post("/api/demo/seed").status_code == 200


def test_date_only_supplier_commitment_is_not_overdue_during_same_day():
    with db() as conn:
        conn.execute(
            "UPDATE orders SET latest_supplier_commitment=?,current_progress=? WHERE order_id=?",
            ("2026-08-03", 0.45, "ORD-1001"),
        )
        conn.commit()
    response = client.post(
        "/api/agent/tools/anomalies/build",
        headers=HEADERS,
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "order_id": "ORD-1001",
            "current_time": "2026-08-03T16:30:00+08:00",
            "anomaly_types": ["SUPPLIER_COMMITMENT_OVERDUE"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 0


def test_date_only_supplier_commitment_becomes_overdue_next_day():
    with db() as conn:
        conn.execute(
            "UPDATE orders SET latest_supplier_commitment=?,current_progress=? WHERE order_id=?",
            ("2026-08-03", 0.45, "ORD-1001"),
        )
        conn.commit()
    response = client.post(
        "/api/agent/tools/anomalies/build",
        headers=HEADERS,
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "order_id": "ORD-1001",
            "current_time": "2026-08-04T00:01:00+08:00",
            "anomaly_types": ["SUPPLIER_COMMITMENT_OVERDUE"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert response.json()["items"][0]["anomaly_type"] == "SUPPLIER_COMMITMENT_OVERDUE"


def test_information_gap_is_separated_and_never_pads_risk_top_n():
    items = [
        {
            "candidate_id": "C1",
            "order_id": "O1",
            "order_no": "PO-1",
            "anomaly_type": "DELIVERY_RISK",
            "severity": "HIGH",
            "score": 80,
            "evidence": ["交期临近"],
            "missing_information": [],
            "recommended_action": "确认生产进度",
        },
        {
            "candidate_id": "C2",
            "order_id": "O1",
            "order_no": "PO-1",
            "anomaly_type": "SUPPLIER_COMMITMENT_OVERDUE",
            "severity": "HIGH",
            "score": 70,
            "evidence": ["工厂承诺已过期"],
            "missing_information": [],
            "recommended_action": "联系工厂",
        },
        {
            "candidate_id": "C3",
            "order_id": "O2",
            "order_no": "PO-2",
            "anomaly_type": "INFORMATION_GAP",
            "severity": "LOW",
            "score": 20,
            "evidence": [],
            "missing_information": ["当前进度"],
            "recommended_action": "补充信息",
        },
    ]
    result = agent_api.aggregate_order_candidates(items, top_n=7)
    assert len(result["risk_items"]) == 1
    assert result["risk_items"][0]["order_id"] == "O1"
    assert result["risk_items"][0]["order_anomaly_count"] == 2
    assert result["risk_items"][0]["secondary_anomaly_types"] == ["SUPPLIER_COMMITMENT_OVERDUE"]
    assert result["information_gap_order_count"] == 1
    assert result["information_gaps"][0]["order_id"] == "O2"


def test_rule_inspection_is_explicitly_labeled_and_reports_separate_counts():
    response = client.post(
        "/api/agent/inspection/run",
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "due_within_days": 60,
            "top_n": 7,
            "trigger_type": "MANUAL_RULE",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["execution_mode"] == "RULE_INSPECTION"
    assert data["selection_strategy"]["not_padded"] is True
    assert data["risk_order_count"] == len(data["top_items"])
    assert "information_gap_order_count" in data
    assert all(item.get("anomaly_type") != "INFORMATION_GAP" for item in data["top_items"])


def test_status_exposes_backend_agent_and_rule_modes_separately():
    response = client.get("/api/agent/status")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "6.1.3.1"
    assert data["backend"]["online"] is True
    assert data["rule_inspection"]["available"] is True
    assert data["rule_inspection"]["silent_fallback"] is False
    assert "coze_agent" in data


def test_midnight_iso_supplier_commitment_is_still_day_level():
    with db() as conn:
        conn.execute(
            "UPDATE orders SET latest_supplier_commitment=?,current_progress=? WHERE order_id=?",
            ("2026-08-03T00:00:00+08:00", 0.45, "ORD-1001"),
        )
        conn.commit()
    response = client.post(
        "/api/agent/tools/anomalies/build",
        headers=HEADERS,
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "order_id": "ORD-1001",
            "current_time": "2026-08-03T19:30:00+08:00",
            "anomaly_types": ["SUPPLIER_COMMITMENT_OVERDUE"],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 0


def test_repeated_rule_inspection_reuses_active_candidates_instead_of_inflating_badge():
    payload = {
        "current_user_id": "USER-1",
        "current_role": "operator",
        "due_within_days": 60,
        "top_n": 7,
        "trigger_type": "MANUAL_RULE",
        "current_time": "2026-08-03T19:30:00+08:00",
    }
    first = client.post("/api/agent/inspection/run", json=payload)
    assert first.status_code == 200, first.text
    with db() as conn:
        active_after_first = conn.execute(
            "SELECT COUNT(*) FROM anomaly_candidates WHERE status IN ('ANOMALY_CANDIDATE','PENDING_CONFIRMATION')"
        ).fetchone()[0]
    second = client.post("/api/agent/inspection/run", json=payload)
    assert second.status_code == 200, second.text
    with db() as conn:
        active_after_second = conn.execute(
            "SELECT COUNT(*) FROM anomaly_candidates WHERE status IN ('ANOMALY_CANDIDATE','PENDING_CONFIRMATION')"
        ).fetchone()[0]
        duplicate_groups = conn.execute(
            """SELECT COUNT(*) FROM (
                SELECT order_id,anomaly_type,COUNT(*) AS c FROM anomaly_candidates
                WHERE status IN ('ANOMALY_CANDIDATE','PENDING_CONFIRMATION')
                GROUP BY order_id,anomaly_type HAVING c>1
            )"""
        ).fetchone()[0]
    assert active_after_second == active_after_first
    assert duplicate_groups == 0


def test_rule_rerun_retires_stale_same_day_false_positive():
    with db() as conn:
        conn.execute(
            "UPDATE orders SET latest_supplier_commitment=?,current_progress=? WHERE order_id=?",
            ("2026-08-03T00:00:00+08:00", 0.45, "ORD-1001"),
        )
        conn.execute(
            """INSERT INTO anomaly_candidates(candidate_id,run_id,order_id,anomaly_type,severity,confidence,score,
               evidence_json,missing_information_json,recommended_action,status,created_by,created_at,updated_at)
               VALUES('STALE-1',NULL,'ORD-1001','SUPPLIER_COMMITMENT_OVERDUE','HIGH',0.9,80,'[]','[]','联系工厂',
               'ANOMALY_CANDIDATE','USER-1','2026-08-03T10:00:00+08:00','2026-08-03T10:00:00+08:00')"""
        )
        conn.commit()
    response = client.post(
        "/api/agent/inspection/run",
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "due_within_days": 60,
            "top_n": 7,
            "trigger_type": "MANUAL_RULE",
            "current_time": "2026-08-03T19:30:00+08:00",
        },
    )
    assert response.status_code == 200, response.text
    with db() as conn:
        row = conn.execute("SELECT status FROM anomaly_candidates WHERE candidate_id='STALE-1'").fetchone()
    assert row[0] == 'SUPERSEDED'
