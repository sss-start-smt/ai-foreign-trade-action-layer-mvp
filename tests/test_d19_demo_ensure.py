from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from database import reset_engines


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'ensure.db'))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.setenv('SEED_D19_DEMO_DATA', 'true')
    reset_engines()
    from main import app, init_db
    init_db()
    yield TestClient(app)
    reset_engines()


def test_demo_ensure_seeds_same_runtime_database(client):
    headers = {'X-Auth-Token': 'tok-user-1'}
    before = client.get('/api/orders', headers=headers)
    assert before.status_code == 200
    assert before.json()['items'] == []

    ensured = client.post('/api/d19/demo/ensure', headers=headers, json={})
    assert ensured.status_code == 200
    payload = ensured.json()
    assert payload['enabled'] is True
    assert payload['order_count'] == 17

    after = client.get('/api/orders', headers=headers)
    assert after.status_code == 200
    assert len([x for x in after.json()['items'] if x['order_id'].startswith('ORD-D19-DEMO-')]) == 17

    again = client.post('/api/d19/demo/ensure', headers=headers, json={})
    assert again.status_code == 200
    assert again.json()['order_count'] == 17
    assert sum(again.json()['inserted'].values()) == 0


def test_demo_ensure_is_disabled_without_flag(tmp_path, monkeypatch):
    monkeypatch.setenv('DB_PATH', str(tmp_path / 'disabled.db'))
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('SEED_D19_DEMO_DATA', raising=False)
    reset_engines()
    from main import app, init_db
    init_db()
    c = TestClient(app)
    resp = c.post('/api/d19/demo/ensure', headers={'X-Auth-Token': 'tok-user-1'}, json={})
    assert resp.status_code == 200
    assert resp.json()['status'] == 'DISABLED'
    assert c.get('/api/orders', headers={'X-Auth-Token': 'tok-user-1'}).json()['items'] == []
    reset_engines()
