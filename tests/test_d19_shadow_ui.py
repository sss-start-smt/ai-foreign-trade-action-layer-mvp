from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from main import app, db, iso
from tests.conftest import auth_headers


client = TestClient(app)


def test_d19_login_exchanges_demo_credential_for_existing_auth_token():
    response = client.post('/auth/login', json={'username': 'limin', 'password': 'demo123'})
    assert response.status_code == 200
    payload = response.json()
    assert payload['access_token'] == 'tok-user-1'
    assert payload['identity']['user_id'] == 'USER-1'
    assert payload['profile']['ui_role'] == 'operator'


def test_d19_login_rejects_bad_password():
    response = client.post('/auth/login', json={'username': 'limin', 'password': 'wrong'})
    assert response.status_code == 401


def test_d19_me_still_uses_server_side_token_identity():
    response = client.get('/auth/me', headers=auth_headers('USER-1'))
    assert response.status_code == 200
    assert response.json()['identity']['user_id'] == 'USER-1'
    assert response.json()['identity']['role'] == 'operator'


def test_d19_high_risk_candidate_cannot_be_directly_confirmed_by_operator():
    review_id = 'REV-D19-HIGH-RISK'
    candidate = {
        'fields': [
            {
                'field_name': 'requested_delivery_date',
                'old_value': '2026-08-20',
                'normalized_value': '2026-08-22',
                'confidence': 0.99,
            }
        ],
        'risk_signals': [{'risk_level': 'high', 'type': 'delivery_change'}],
    }
    with db() as conn:
        conn.execute('DELETE FROM candidate_reviews WHERE review_id=?', (review_id,))
        conn.execute(
            'INSERT INTO candidate_reviews(review_id,source_message_id,order_id,organization_id,workflow_source,candidate_json,status,created_at) VALUES(?,?,?,?,?,?,?,?)',
            (review_id, None, None, 'ORG-A', 'D19_TEST', json.dumps(candidate, ensure_ascii=False), 'PENDING', iso()),
        )
        conn.commit()
    try:
        response = client.post(f'/api/reviews/{review_id}/confirm', headers=auth_headers('USER-1'), json={})
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'MANAGER_REVIEW_REQUIRED'

        submitted = client.post(
            f'/api/d19/reviews/{review_id}/submit-manager-review',
            headers=auth_headers('USER-1'),
            json={},
        )
        assert submitted.status_code == 200
        with db() as conn:
            row = conn.execute('SELECT status FROM candidate_reviews WHERE review_id=?', (review_id,)).fetchone()
        assert row['status'] == 'APPROVAL_PENDING'
    finally:
        with db() as conn:
            conn.execute('DELETE FROM candidate_reviews WHERE review_id=?', (review_id,))
            conn.commit()


def test_d19_review_summary_requires_auth_and_returns_five_days():
    assert client.get('/api/d19/review-summary').status_code == 401
    response = client.get('/api/d19/review-summary', headers=auth_headers('USER-1'))
    assert response.status_code == 200
    assert len(response.json()['daily_handled']) == 5


def test_d19_frontend_serves_new_login_shell_and_live_script():
    root = Path(__file__).resolve().parents[1]
    index = (root / 'static' / 'index.html').read_text(encoding='utf-8')
    js = (root / 'static' / 'd19_app.js').read_text(encoding='utf-8')
    assert 'id="loginScreen"' in index
    assert '/static/d19_app.js' in index
    assert '/auth/login' in js
    assert '/api/dashboard' in js
    assert '/api/orders' in js
    assert '/api/agent/chat/jobs' in js
    assert '/api/d12/reviews' in js
    assert '/api/settings' in js
