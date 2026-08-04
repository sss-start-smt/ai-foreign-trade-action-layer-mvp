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
    assert data["version"] == "6.1.4.1"
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


def test_agent_chat_job_returns_immediately_and_can_be_polled(monkeypatch):
    import time

    def should_not_call_coze(**kwargs):
        raise AssertionError("standard risk diagnosis must not call Coze")

    monkeypatch.setattr(agent_api, "run_agent_chat", should_not_call_coze)
    created = client.post(
        "/api/agent/chat/jobs",
        json={
            "question": "检查我未来14天内最需要处理的订单。",
            "current_user_id": "USER-1",
            "current_role": "operator",
            "due_within_days": 14,
            "top_n": 7,
        },
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]
    assert created.json()["status"] in {"QUEUED", "RUNNING"}

    completed = None
    for _ in range(50):
        polled = client.get(f"/api/agent/chat/jobs/{job_id}")
        assert polled.status_code == 200, polled.text
        if polled.json()["status"] == "COMPLETED":
            completed = polled.json()
            break
        time.sleep(0.02)
    assert completed is not None
    assert completed["result"]["execution_mode"] == "HYBRID_DETERMINISTIC_PLAN"
    assert completed["result"]["route_plan"]["intents"][0]["intent"] == "RISK_DIAGNOSIS"
    assert completed["result"]["diagnosis"]["selection_strategy"]["ranking_rule_version"] == "FT04_SHARED_V1"
    assert completed["result"]["resolved_identity"]["current_user_id"] == "USER-1"

def test_status_advertises_async_agent_chat():
    response = client.get("/api/agent/status")
    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "6.1.4.1"
    assert data["async_agent_chat"]["available"] is True
    assert data["async_agent_chat"]["polling"] is True


def test_standard_agent_job_precreates_backend_managed_run(monkeypatch):
    import time

    def should_not_call_coze(**kwargs):
        raise AssertionError("standard diagnosis should use the deterministic plan")

    monkeypatch.setattr(agent_api, "run_agent_chat", should_not_call_coze)
    created = client.post(
        "/api/agent/chat/jobs",
        json={
            "question": "检查我未来14天内最需要处理的订单。",
            "current_user_id": "USER-1",
            "due_within_days": 14,
            "top_n": 7,
            "create_task_draft": True,
            "create_approval_request": True,
        },
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]
    run_id = created.json()["linked_run_id"]
    assert run_id.startswith("AGR-")

    completed = None
    for _ in range(50):
        polled = client.get(f"/api/agent/chat/jobs/{job_id}")
        if polled.json()["status"] == "COMPLETED":
            completed = polled.json()
            break
        time.sleep(0.02)
    assert completed is not None
    assert completed["result"]["execution_mode"] == "HYBRID_DETERMINISTIC_PLAN"
    with db() as conn:
        run = conn.execute("SELECT * FROM agent_runs WHERE run_id=?", (run_id,)).fetchone()
        calls = conn.execute("SELECT tool_name FROM agent_tool_calls WHERE run_id=?", (run_id,)).fetchall()
    assert run["status"] == "COMPLETED"
    assert run["stop_reason"] == "DETERMINISTIC_PLAN_COMPLETED"
    names = {x[0] for x in calls}
    assert "diagnose_priority_orders" in names
    assert "backend_finalize_agent_run" in names

def test_task_draft_can_create_linked_approval_in_same_tool_call():
    started = client.post(
        "/api/agent/tools/runs/start",
        headers=HEADERS,
        json={"current_user_id": "USER-1", "current_role": "operator", "goal": "性能快速链路测试"},
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run_id"]
    response = client.post(
        "/api/agent/tools/task-drafts/create",
        headers=HEADERS,
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "run_id": run_id,
            "order_id": "ORD-1001",
            "title": "确认工厂实际进度",
            "recommended_action": "联系工厂确认进度与补救方案",
            "risk_level": "high",
            "evidence": ["距离交期较近", "进度偏低"],
            "create_approval_request": True,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["task_draft_id"].startswith("TDRAFT-")
    assert data["approval_id"].startswith("APR-")
    assert data["approval_status"] == "PENDING"
    with db() as conn:
        approval = conn.execute("SELECT * FROM approval_requests WHERE approval_id=?", (data["approval_id"],)).fetchone()
        calls = conn.execute("SELECT tool_name FROM agent_tool_calls WHERE run_id=?", (run_id,)).fetchall()
    assert approval is not None
    assert approval["action_type"] == "CREATE_TASK"
    assert [x[0] for x in calls].count("create_task_draft") == 1
    assert "create_approval_request" not in [x[0] for x in calls]


def test_status_advertises_fast_standard_diagnosis_profile():
    response = client.get("/api/agent/status")
    assert response.status_code == 200
    profile = response.json()["performance_profile"]
    assert profile["backend_managed_run"] is True
    assert profile["standard_agent_tool_turns"] == 1
    assert profile["hybrid_intent_router"] is True
    assert profile["shared_ranking_rule"] == "FT04_SHARED_V1"
    assert profile["compact_final_answer"] is True


def test_backend_managed_job_option_can_trigger_linked_approval_without_plugin_schema_change():
    run_id = "AGR-FAST-IMPLICIT"
    now = agent_api.iso()
    with db() as conn:
        conn.execute(
            """INSERT INTO agent_runs(run_id,organization_id,current_user_id,current_role,goal,trigger_type,status,
               max_tool_calls,max_duration_seconds,started_at,created_at)
               VALUES(?,?,?,?,?,'USER_BACKEND_MANAGED','RUNNING',?,?,?,?)""",
            (run_id, "ORG-DEMO", "USER-1", "operator", "隐式审批测试", 8, 120, now, now),
        )
        conn.execute(
            """INSERT INTO agent_chat_jobs(job_id,organization_id,current_user_id,current_role,question,status,
               request_json,linked_run_id,created_at,updated_at)
               VALUES('AJOB-IMPLICIT','ORG-DEMO','USER-1','operator','测试','RUNNING',?,?,?,?)""",
            ('{"create_approval_request":true}', run_id, now, now),
        )
        conn.commit()
    response = client.post(
        "/api/agent/tools/task-drafts/create",
        headers=HEADERS,
        json={
            "current_user_id": "USER-1",
            "current_role": "operator",
            "run_id": run_id,
            "order_id": "ORD-1001",
            "title": "确认工厂进度",
            "risk_level": "high",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["approval_id"].startswith("APR-")


def test_multi_intent_standard_plan_executes_without_coze(monkeypatch):
    import time

    def should_not_call_coze(**kwargs):
        raise AssertionError("risk + explanation + task draft should be executed by the backend plan")

    monkeypatch.setattr(agent_api, "run_agent_chat", should_not_call_coze)
    created = client.post(
        "/api/agent/chat/jobs",
        json={
            "question": "最近事情特别乱，你先检查未来两周最危险的订单，解释第一笔为什么优先，再给它建一个任务，但不要发消息。",
            "current_user_id": "USER-1",
            "current_role": "operator",
            "create_approval_request": True,
        },
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["job_id"]
    completed = None
    for _ in range(60):
        response = client.get(f"/api/agent/chat/jobs/{job_id}")
        if response.json()["status"] == "COMPLETED":
            completed = response.json()
            break
        time.sleep(0.02)
    assert completed is not None
    result = completed["result"]
    assert [x["intent"] for x in result["route_plan"]["intents"]] == [
        "RISK_DIAGNOSIS",
        "EXPLAIN_PRIORITY",
        "CREATE_TASK_DRAFT",
    ]
    assert result["route_plan"]["constraints"]["allow_external_send"] is False
    assert result["task_draft_id"].startswith("TDRAFT-")
    assert result["approval_id"].startswith("APR-")


def test_followup_explanation_reuses_previous_structured_run(monkeypatch):
    import time

    def should_not_call_coze(**kwargs):
        raise AssertionError("structured follow-up should not call Coze")

    monkeypatch.setattr(agent_api, "run_agent_chat", should_not_call_coze)
    first = client.post(
        "/api/agent/chat/jobs",
        json={"question": "检查未来14天最需要处理的订单", "current_user_id": "USER-1"},
    )
    run_id = first.json()["linked_run_id"]
    for _ in range(60):
        if client.get(f"/api/agent/chat/jobs/{first.json()['job_id']}").json()["status"] == "COMPLETED":
            break
        time.sleep(0.02)
    second = client.post(
        "/api/agent/chat/jobs",
        json={
            "question": "为什么第一笔排在最前？",
            "current_user_id": "USER-1",
            "previous_run_id": run_id,
        },
    )
    completed = None
    for _ in range(60):
        response = client.get(f"/api/agent/chat/jobs/{second.json()['job_id']}")
        if response.json()["status"] == "COMPLETED":
            completed = response.json()
            break
        time.sleep(0.02)
    assert completed is not None
    assert completed["result"]["execution_mode"] == "HYBRID_DETERMINISTIC_PLAN"
    assert "第1笔排在这里" in completed["result"]["answer"]
