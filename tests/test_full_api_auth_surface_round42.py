"""D3 Round 4.2 - closure tests for previously omitted API surfaces."""
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)
USER = {"X-Auth-Token": "tok-user-1"}
MANAGER = {"X-Auth-Token": "tok-manager-1"}


def test_communication_orders_requires_token():
    assert client.get('/api/communication/orders').status_code == 401


def test_communication_history_requires_token():
    assert client.get('/api/communication/history').status_code == 401


def test_communication_capabilities_requires_token():
    assert client.get('/api/communication/capabilities').status_code == 401


def test_communication_write_surfaces_require_token_before_lookup():
    r = client.post('/api/communication/candidates/NO-SUCH/reject', json={"operator_id":"MANAGER-B","note":"x"})
    assert r.status_code == 401
    r = client.post('/api/communication/drafts/NO-SUCH/review', json={"action":"reject","operator_id":"MANAGER-B"})
    assert r.status_code == 401


def test_workflow_surfaces_require_token():
    assert client.post('/api/workflows/ft05/run', json={}).status_code == 401
    assert client.post('/api/workflows/ft06/run', json={}).status_code == 401


def test_import_preview_requires_token():
    r = client.post('/api/import/preview', json={"filename":"x.csv","content_base64":"YQ=="})
    assert r.status_code == 401


def test_import_commit_requires_token_and_ignores_spoofed_actor():
    assert client.post('/api/import/commit', json={"batch_id":"NO-SUCH","current_user_id":"MANAGER-B","projection_hash":"fake"}).status_code == 401
    # Authenticated operator reaches resource lookup; spoofed body actor does not grant anything.
    assert client.post('/api/import/commit', headers=USER, json={"batch_id":"NO-SUCH","current_user_id":"MANAGER-B","projection_hash":"fake"}).status_code == 404
    assert client.post('/api/import/commit', headers=MANAGER, json={"batch_id":"NO-SUCH","current_user_id":"MANAGER-B","projection_hash":"fake"}).status_code == 404


def test_import_batch_read_requires_token():
    assert client.get('/api/import/batches/NO-SUCH').status_code == 401
    assert client.get('/api/import/batches/NO-SUCH', headers=USER).status_code == 404


def test_agent_job_poll_requires_token_before_lookup():
    assert client.get('/api/agent/chat/jobs/NO-SUCH').status_code == 401
    assert client.get('/api/agent/chat/jobs/NO-SUCH', headers=USER).status_code == 404
