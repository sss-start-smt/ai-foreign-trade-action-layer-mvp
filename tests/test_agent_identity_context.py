import os
from pathlib import Path

os.environ.setdefault("DB_PATH", str(Path(__file__).parent / "test_action_layer.db"))
os.environ.setdefault("APP_API_KEY", "test-key")
os.environ.setdefault("SEED_DEMO_DATA", "true")

from fastapi.testclient import TestClient

import agent_api
from main import app, init_db

client = TestClient(app)


def setup_function():
    init_db()
    assert client.post("/api/reset").status_code == 200
    assert client.post("/api/demo/seed").status_code == 200


def test_agent_chat_injects_operator_identity_and_preserves_goal(monkeypatch):
    captured = {}

    def fake_run_agent_chat(**kwargs):
        captured.update(kwargs)
        return {
            "answer": "已完成诊断",
            "conversation_id": "CONV-1",
            "duration_ms": 10,
            "usage": {},
        }

    monkeypatch.setattr(agent_api, "run_agent_chat", fake_run_agent_chat)
    response = client.post(
        "/api/agent/chat",
        json={
            "question": "检查我未来14天内最需要处理的订单。",
            "current_user_id": "USER-1",
            "current_role": "manager",  # spoofing must not elevate the role
            "due_within_days": 14,
            "top_n": 7,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()
    question = captured["question"]
    assert "[FLOWORDER_SYSTEM_CONTEXT_BEGIN]" in question
    assert '"current_user_id":"USER-1"' in question
    assert '"current_user_name":"李梅"' in question
    assert '"current_role":"operator"' in question
    assert '"allowed_owner_ids":["USER-1"]' in question
    assert "不得再次询问用户ID" in question
    assert "检查我未来14天内最需要处理的订单。" in question
    assert captured["parameters"]["current_role"] == "operator"
    assert data["resolved_identity"] == {
        "current_user_id": "USER-1",
        "current_user_name": "李梅",
        "current_role": "operator",
        "scope_description": "本人负责订单",
    }


def test_agent_chat_injects_manager_team_scope(monkeypatch):
    captured = {}

    def fake_run_agent_chat(**kwargs):
        captured.update(kwargs)
        return {"answer": "已完成团队诊断", "duration_ms": 8, "usage": {}}

    monkeypatch.setattr(agent_api, "run_agent_chat", fake_run_agent_chat)
    response = client.post(
        "/api/agent/chat",
        json={"question": "检查团队订单。", "current_user_id": "MANAGER-1"},
    )
    assert response.status_code == 200, response.text
    params = captured["parameters"]
    assert params["current_user_name"] == "周主管"
    assert params["current_role"] == "manager"
    assert params["scope_description"] == "团队订单"
    assert set(params["allowed_owner_ids"]) == {"USER-1", "USER-2", "USER-3"}


def test_agent_chat_rejects_unknown_website_identity(monkeypatch):
    monkeypatch.setattr(
        agent_api,
        "run_agent_chat",
        lambda **kwargs: {"answer": "不应调用", "duration_ms": 1, "usage": {}},
    )
    response = client.post(
        "/api/agent/chat",
        json={"question": "检查订单。", "current_user_id": "UNKNOWN-USER"},
    )
    assert response.status_code == 422
    assert "重新选择身份" in response.text
