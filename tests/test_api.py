import json
import os
from pathlib import Path

os.environ["DB_PATH"] = str(Path(__file__).parent / "test_action_layer.db")
os.environ["APP_API_KEY"] = "test-key"
os.environ["SEED_DEMO_DATA"] = "true"
os.environ["COZE_ALLOW_LOCAL_WHEN_UNCONFIGURED"] = "true"
os.environ["COZE_ALLOW_LOCAL_CONFIRM_WHEN_UNCONFIGURED"] = "true"

from fastapi.testclient import TestClient
from main import app, init_db

client = TestClient(app)


def setup_function():
    # Windows cannot delete an SQLite file while a previous connection is
    # still being released. Reset rows through the API instead of unlinking.
    init_db()
    response = client.post("/api/reset")
    assert response.status_code == 200
    seeded = client.post("/api/demo/seed")
    assert seeded.status_code == 200


def test_health_and_dashboard():
    assert client.get("/health").status_code == 200
    data = client.get("/api/dashboard").json()
    assert data["summary"]["total"] >= 4
    states = {x["action_state"] for x in data["items"]}
    assert "WAITING_EXTERNAL" in states
    assert "NEEDS_CONFIRMATION" in states
    assert "ESCALATE" in states


def test_demo_full_loop_and_rerank():
    r1 = client.post("/api/demo/apply-ft01")
    assert r1.status_code == 200
    assert r1.json()["status"] == "COMMITTED"
    board = client.get("/api/dashboard").json()
    task = next(x for x in board["items"] if x["task_id"] == "TASK-PO1001-CONFIRM")
    assert task["action_state"] in {"DO_NOW", "DO_TODAY"}

    contact = client.post(
        "/api/tasks/TASK-PO1001-CONFIRM/contacted",
        json={"waiting_on": "factory", "promised_reply_at": "2030-07-26T15:00:00+08:00"},
    )
    assert contact.status_code == 200
    board = client.get("/api/dashboard").json()
    task = next(x for x in board["items"] if x["task_id"] == "TASK-PO1001-CONFIRM")
    assert task["action_state"] == "WAITING_EXTERNAL"
    assert task["ranking_suppressed"] is True

    r2 = client.post("/api/demo/apply-ft02")
    assert r2.status_code == 200
    order = client.get("/api/orders/ORD-1001").json()["order"]
    assert order["current_progress"] == 0.7
    assert order["latest_supplier_commitment"] == "2026-07-29"


def test_writeback_idempotency():
    payload = {
        "transaction_json": "{\"idempotency_key\":\"TEST|ONE\",\"change_set\":[]}",
        "existing_business_state_json": "{\"order_id\":\"ORD-1001\"}",
    }
    first = client.post("/api/writeback", headers={"X-API-Key": "test-key"}, json=payload)
    second = client.post("/api/writeback", headers={"X-API-Key": "test-key"}, json=payload)
    assert first.status_code == 200
    assert first.json()["status"] == "COMMITTED"
    assert second.json()["status"] == "DUPLICATE_SKIPPED"


def test_writeback_rejects_bad_key():
    r = client.post("/api/writeback", headers={"X-API-Key": "wrong"}, json={})
    assert r.status_code == 401



def test_orders_and_management_pages_api():
    orders = client.get('/api/orders')
    assert orders.status_code == 200
    assert orders.json()['total'] >= 5
    assert 'open_task_count' in orders.json()['items'][0]
    management = client.get('/api/management')
    assert management.status_code == 200
    assert len(management.json()['workload']) >= 3


def test_intake_review_confirm_flow():
    analyzed = client.post('/api/intake/analyze', json={
        'source_channel': 'email',
        'sender_role': 'customer',
        'order_id': 'ORD-1001',
        'raw_content': 'PO-1001的包装方式请改为彩盒，并请今天确认是否会影响8月20日交期。'
    })
    assert analyzed.status_code == 200
    review_id = analyzed.json()['review_id']
    reviews = client.get('/api/reviews?status=PENDING').json()
    assert any(x['review_id'] == review_id for x in reviews['items'])
    confirmed = client.post(f'/api/reviews/{review_id}/confirm', json={'operator_id': 'USER-1'})
    assert confirmed.status_code == 200
    assert confirmed.json()['status'] == 'CONFIRMED'
    order = client.get('/api/orders/ORD-1001').json()['order']
    assert order['packaging_method'] == '彩盒'


def test_task_transfer_escalate_and_settings():
    moved = client.post('/api/tasks/TASK-TODAY-001/transfer', json={'owner_user_id': 'USER-2'})
    assert moved.status_code == 200
    escalated = client.post('/api/tasks/TASK-TODAY-001/escalate', json={'reason': '测试升级'})
    assert escalated.status_code == 200
    board = client.get('/api/dashboard').json()
    task = next(x for x in board['items'] if x['task_id'] == 'TASK-TODAY-001')
    assert task['risk_level'] == 'critical'
    saved = client.put('/api/settings', json={'settings': {'accent': 'green', 'compact': True, 'show_demo': False, 'notifications': {}}})
    assert saved.status_code == 200
    assert client.get('/api/settings').json()['settings']['accent'] == 'green'


def test_workflow_parameter_builders_are_valid():
    import main

    order = {
        "order_id": "ORD-1001",
        "order_no": "PO-1001",
        "customer_name": "Northwind Trading",
    }
    ft01 = main.build_ft01_parameters(
        {
            "raw_content": "PO-1001包装改为彩盒",
            "source_channel": "internal",
            "sender_role": "customer",
        },
        order,
        None,
    )
    assert ft01["source_channel"] == "manual_input"
    assert ft01["input_type"] == "text"
    assert json.loads(ft01["existing_order_context"])["order_id"] == "ORD-1001"

    ft02 = main.build_ft02_parameters(
        {
            "raw_content": "差不多七成，应该下周三完成",
            "source_channel": "email",
            "sender_role": "factory",
            "order_id": "ORD-1001",
        },
        order,
        None,
    )
    assert ft02["source_channel"] == "email"
    assert ft02["sender_role"] == "factory"
    assert json.loads(ft02["order_context"])["order_id"] == "ORD-1001"
    assert json.loads(ft02["task_context"])["questions"]


def test_first_value_activation_flow():
    created = client.post('/api/orders', json={
        'order_no': 'PO-ACTIVATE-001',
        'customer_name': 'Activation Customer',
        'product_name': 'Canvas Bag',
        'customer_delivery_date': '2026-08-15',
    })
    assert created.status_code == 200
    order_id = created.json()['order_id']

    order = client.get(f'/api/orders/{order_id}').json()['order']
    assert order['action_readiness'] == 'BASE_ONLY'
    summary = client.get('/api/activation/summary').json()
    assert any(x['order_id'] == order_id for x in summary['recommended_orders'])

    initialized = client.post(f'/api/orders/{order_id}/initialize', json={
        'current_node': '生产中',
        'contact_status': 'NOT_CONTACTED',
        'issue_status': 'UNKNOWN',
        'operator_id': 'USER-1',
    })
    assert initialized.status_code == 200
    assert initialized.json()['action_readiness'] == 'ACTION_GENERATED'
    task_id = initialized.json()['task_id']
    assert task_id

    order = client.get(f'/api/orders/{order_id}').json()['order']
    assert order['action_readiness'] == 'ACTION_GENERATED'
    board = client.get('/api/dashboard').json()
    task = next(x for x in board['items'] if x['task_id'] == task_id)
    assert task['action_state'] == 'DO_NOW'
    assert '计划处理时间' in ' '.join(task['priority_reasons'])


def test_excel_import_creates_base_orders_without_fake_tasks():
    import base64

    csv_bytes = (
        '订单号,客户名称,产品名称,数量,单位,客户正式交期\n'
        'PO-BASE-IMPORT-001,Base Customer,Storage Bag,500,pcs,2026-09-01\n'
    ).encode('utf-8-sig')
    preview = client.post('/api/import/preview', json={
        'filename': 'base_orders.csv',
        'content_base64': base64.b64encode(csv_bytes).decode('ascii'),
    })
    assert preview.status_code == 200
    assert preview.json()['summary']['new'] == 1

    committed = client.post('/api/import/commit', json={
        'batch_id': preview.json()['batch_id'],
        'import_key': 'test-key',
        'row_actions': {},
    })
    assert committed.status_code == 200
    result = committed.json()
    assert result['inserted'] == 1
    assert result['tasks_created'] == 0
    assert result['base_orders_created'] == 1
    assert result['next_step_url'] == '/#activation'

    orders = client.get('/api/orders?q=PO-BASE-IMPORT-001').json()['items']
    assert len(orders) == 1
    imported = orders[0]
    assert imported['action_readiness'] == 'BASE_ONLY'
    detail = client.get(f"/api/orders/{imported['order_id']}").json()
    assert detail['tasks'] == []
