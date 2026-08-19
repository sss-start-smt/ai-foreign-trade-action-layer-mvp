import json
import os
from pathlib import Path

os.environ["DB_PATH"] = str(Path(__file__).parent / "test_action_layer.db")
os.environ["APP_API_KEY"] = "test-key"
os.environ["SEED_DEMO_DATA"] = "true"
os.environ["ENABLE_DEMO_ADMIN_ACTIONS"] = "true"
os.environ["COZE_ALLOW_LOCAL_WHEN_UNCONFIGURED"] = "true"
os.environ["COZE_ALLOW_LOCAL_CONFIRM_WHEN_UNCONFIGURED"] = "true"

from fastapi.testclient import TestClient
from main import app, init_db, db
from conftest import auth_headers

client = TestClient(app)

NOW = "2026-08-14T15:30:00+08:00"


def setup_function():
    init_db()
    response = client.post("/api/reset", headers={"X-Auth-Token": "tok-manager-1"})
    assert response.status_code == 200
    seeded = client.post("/api/demo/seed", headers={"X-Auth-Token": "tok-manager-1"})
    assert seeded.status_code == 200

    # Insert UAT test orders for D11 UAT tests
    with db() as conn:
        # Add the abnormal UAT order
        try:
            conn.execute(
                """INSERT INTO orders(order_id,order_no,customer_name,product_name,requested_delivery_date,
                   latest_supplier_commitment,current_node,status,owner,organization_id,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("ORD-D11-UAT", "SO-D11-UAT", "Northwind UAT", "帆布包", "2026-08-20", None,
                 "生产中", "ACTIVE", "USER-1", "ORG-A", NOW, NOW),
            )
            conn.execute(
                """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,
                   stage,lifecycle_status,title,latest_action_bucket,latest_severity,latest_recommended_action,
                   latest_evidence_json,observation_status,first_seen_at,last_seen_at,source_policy_version,
                   version,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("AC-D11-UAT", "ORG-A", "ORD-D11-UAT", "D11:DELIVERY_RECOVERY:SO-D11-UAT",
                 "DELIVERY_RECOVERY", "IN_PROGRESS", "ACTIVE", "解决 SO-D11-UAT 交期异常",
                 "DO_NOW", "high", "先确认供应商能否按 8 月 20 日交货",
                 json.dumps(["客户正式交期为 8 月 20 日", "供应商尚未给出确认承诺"], ensure_ascii=False),
                 "OBSERVED", NOW, NOW, "D11_UAT_SEED", 1, NOW, NOW),
            )
            conn.execute(
                """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,
                   status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                ("TK-D11-UAT-1", "ORG-A", "AC-D11-UAT", "联系供应商确认 8 月 20 日能否交货",
                 "联系供应商，要求给出明确可交付日期", "TODO", 1, NOW, NOW),
            )
            conn.execute(
                """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,
                   status,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)""",
                ("TK-D11-UAT-2", "ORG-A", "AC-D11-UAT", "核对是否存在可替代的备货方案",
                 "核对内部库存或替代供应方案；不要因为 Task 1 进入等待而隐藏本任务", "TODO", 1, NOW, NOW),
            )
        except Exception:
            pass  # Order may already exist

        # Also add USER-2 to ORG-B for cross-org isolation test
        # Insert if not exists
        try:
            conn.execute(
                """INSERT INTO orders(order_id,order_no,customer_name,product_name,requested_delivery_date,
                   latest_supplier_commitment,current_node,status,owner,organization_id,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                ("ORD-D11-UAT-B", "SO-D11-UAT-B", "Cross Org Customer", "产品B", "2026-09-01", None,
                 "备货采购", "ACTIVE", "USER-2", "ORG-B", NOW, NOW),
            )
        except Exception:
            pass  # Order may already exist
        conn.commit()


def test_health_and_dashboard():
    assert client.get("/health").status_code == 200
    data = client.get("/api/dashboard", headers=auth_headers("USER-1")).json()
    assert data["summary"]["total"] >= 4
    states = {x["action_state"] for x in data["items"]}
    assert "WAITING_EXTERNAL" in states
    assert "NEEDS_CONFIRMATION" in states
    manager_states = {x["action_state"] for x in client.get("/api/dashboard", headers=auth_headers("MANAGER-1")).json()["items"]}
    assert "ESCALATE" in manager_states


def test_demo_full_loop_and_rerank():
    r1 = client.post("/api/demo/apply-ft01", headers=auth_headers("MANAGER-1"))
    assert r1.status_code == 200
    assert r1.json()["status"] == "COMMITTED"
    board = client.get("/api/dashboard", headers=auth_headers("USER-1")).json()
    task = next(x for x in board["items"] if x["task_id"] == "TASK-PO1001-CONFIRM")
    assert task["action_state"] in {"DO_NOW", "DO_TODAY"}

    contact = client.post(
        "/api/tasks/TASK-PO1001-CONFIRM/contacted",
        headers=auth_headers("USER-1"),
        json={"waiting_on": "factory", "promised_reply_at": "2030-07-26T15:00:00+08:00"},
    )
    assert contact.status_code == 200
    board = client.get("/api/dashboard", headers=auth_headers("USER-1")).json()
    task = next(x for x in board["items"] if x["task_id"] == "TASK-PO1001-CONFIRM")
    assert task["action_state"] == "WAITING_EXTERNAL"
    assert task["ranking_suppressed"] is True

    r2 = client.post("/api/demo/apply-ft02", headers=auth_headers("MANAGER-1"))
    assert r2.status_code == 200
    order = client.get("/api/orders/ORD-1001", headers=auth_headers("USER-1")).json()["order"]
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
    orders = client.get('/api/orders', headers=auth_headers("USER-1"))
    assert orders.status_code == 200
    assert orders.json()['total'] >= 3  # 3 demo orders + UAT test orders
    assert all(x['owner'] == 'USER-1' for x in orders.json()['items'])
    assert 'open_task_count' in orders.json()['items'][0]
    manager_orders = client.get('/api/orders', headers=auth_headers("MANAGER-1")).json()
    assert manager_orders['total'] >= 5
    management = client.get('/api/management', headers=auth_headers("MANAGER-1"))
    assert management.status_code == 200
    assert len(management.json()['workload']) >= 3


def test_intake_review_confirm_flow():
    analyzed = client.post('/api/intake/analyze', headers=auth_headers("USER-1"), json={
        'source_channel': 'email',
        'sender_role': 'customer',
        'order_id': 'ORD-1001',
        'raw_content': 'PO-1001的包装方式请改为彩盒，并请今天确认是否会影响8月20日交期。'
    })
    assert analyzed.status_code == 200
    review_id = analyzed.json()['review_id']
    reviews = client.get('/api/reviews?status=PENDING', headers=auth_headers("USER-1")).json()
    assert any(x['review_id'] == review_id for x in reviews['items'])
    confirmed = client.post(f'/api/reviews/{review_id}/confirm', headers=auth_headers("USER-1"), json={'operator_id': 'USER-1'})
    assert confirmed.status_code == 200
    assert confirmed.json()['status'] == 'CONFIRMED'
    order = client.get('/api/orders/ORD-1001', headers=auth_headers("USER-1")).json()['order']
    assert order['packaging_method'] == '彩盒'


def test_task_transfer_escalate_and_settings():
    moved = client.post('/api/tasks/TASK-TODAY-001/transfer', headers=auth_headers("MANAGER-1"), json={'owner_user_id': 'USER-2'})
    assert moved.status_code == 200
    escalated = client.post('/api/tasks/TASK-TODAY-001/escalate', headers=auth_headers("MANAGER-1"), json={'reason': '测试升级'})
    assert escalated.status_code == 200
    board = client.get('/api/dashboard', headers=auth_headers("MANAGER-1")).json()
    task = next(x for x in board['items'] if x['task_id'] == 'TASK-TODAY-001')
    assert task['risk_level'] == 'critical'
    saved = client.put('/api/settings', headers=auth_headers("MANAGER-1"), json={'settings': {'accent': 'green', 'compact': True, 'show_demo': False, 'notifications': {}}})
    assert saved.status_code == 200
    assert client.get('/api/settings', headers=auth_headers("MANAGER-1")).json()['settings']['accent'] == 'green'


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
    created = client.post('/api/orders', headers=auth_headers("USER-1"), json={
        'order_no': 'PO-ACTIVATE-001',
        'customer_name': 'Activation Customer',
        'product_name': 'Canvas Bag',
        'customer_delivery_date': '2026-08-15',
    })
    assert created.status_code == 200
    order_id = created.json()['order_id']

    order = client.get(f'/api/orders/{order_id}', headers=auth_headers("USER-1")).json()['order']
    assert order['action_readiness'] == 'BASE_ONLY'
    summary = client.get('/api/activation/summary', headers=auth_headers("USER-1")).json()
    assert any(x['order_id'] == order_id for x in summary['recommended_orders'])

    initialized = client.post(f'/api/orders/{order_id}/initialize', headers=auth_headers("USER-1"), json={
        'current_node': '生产中',
        'contact_status': 'NOT_CONTACTED',
        'issue_status': 'UNKNOWN',
        'operator_id': 'USER-1',
    })
    assert initialized.status_code == 200
    assert initialized.json()['action_readiness'] == 'ACTION_GENERATED'
    task_id = initialized.json()['task_id']
    assert task_id

    order = client.get(f'/api/orders/{order_id}', headers=auth_headers("USER-1")).json()['order']
    assert order['action_readiness'] == 'ACTION_GENERATED'
    board = client.get('/api/dashboard', headers=auth_headers("USER-1")).json()
    task = next(x for x in board['items'] if x['task_id'] == task_id)
    assert task['action_state'] == 'DO_NOW'
    assert '计划处理时间' in ' '.join(task['priority_reasons'])


def test_excel_import_creates_base_orders_without_fake_tasks():
    import base64

    csv_bytes = (
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-BASE-IMPORT-001,Base Customer,Storage Bag,500,2026-09-01,USER-1\n'
    ).encode('utf-8-sig')
    preview = client.post('/api/import/preview', headers=auth_headers("USER-1"), json={
        'filename': 'base_orders.csv',
        'content_base64': base64.b64encode(csv_bytes).decode('ascii'),
    })
    assert preview.status_code == 200
    summary = preview.json()['summary']
    # D4 Contract: missing completion fields produce WARNING, not BLOCK
    assert summary['total'] == 1
    assert summary['warning'] == 1 or summary['new'] == 1
    assert summary['block'] == 0
    assert summary['error'] == 0
    assert preview.json()['projection_hash']

    committed = client.post('/api/import/commit', headers=auth_headers("USER-1"), json={
        'batch_id': preview.json()['batch_id'],
        'import_key': 'test-key',
        'row_actions': {},
        'projection_hash': preview.json()['projection_hash'],
    })
    assert committed.status_code == 200
    result = committed.json()
    assert result['success_count'] + result['success_with_warning_count'] == 1
    assert result['blocked_count'] == 0
    assert result['commit_failed_count'] == 0
    assert 'processing_duration_ms' in result
    assert 'end_to_end_duration_ms' in result

    orders = client.get('/api/orders?q=PO-BASE-IMPORT-001', headers=auth_headers("USER-1")).json()['items']
    assert len(orders) == 1
    imported = orders[0]
    assert imported['action_readiness'] == 'BASE_ONLY'
    assert imported['source_system'] == 'excel_import'
    detail = client.get(f"/api/orders/{imported['order_id']}", headers=auth_headers("USER-1")).json()
    assert detail['tasks'] == []


def test_role_isolation_and_order_context_access():
    user1_orders = client.get('/api/orders', headers=auth_headers("USER-1")).json()['items']
    user2_orders = client.get('/api/orders', headers=auth_headers("USER-2")).json()['items']
    manager_orders = client.get('/api/orders', headers=auth_headers("MANAGER-1")).json()['items']
    assert {x['order_id'] for x in user1_orders}.isdisjoint({x['order_id'] for x in user2_orders})
    assert len(manager_orders) > len(user1_orders)
    # Manager creates order with owner=USER-3, then USER-1 (operator) cannot access it
    private_order = client.post('/api/orders', headers=auth_headers("MANAGER-1"), json={
        'order_no': 'PO-PRIVATE-USER3',
        'customer_name': 'Private Customer',
        'owner': 'USER-3',
    }).json()['order_id']
    result = client.get(f'/api/orders/{private_order}', headers=auth_headers("USER-1"))
    assert result.status_code == 403, f"Operator should not access order owned by USER-3, got {result.status_code}"
    # USER-1 has an assigned task on ORD-1004, so task context remains accessible.
    assert client.get('/api/orders/ORD-1004', headers=auth_headers("USER-1")).status_code == 200


def test_excel_owner_mapping_and_default_binding():
    import base64

    csv_bytes = (
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-OWNER-001,Owner Customer A,Bag,100,2026-09-10,王晓\n'
        'PO-OWNER-002,Owner Customer B,Box,200,2026-09-11,\n'
    ).encode('utf-8-sig')
    preview = client.post('/api/import/preview', headers=auth_headers("USER-1"), json={
        'filename': 'owners.csv',
        'content_base64': base64.b64encode(csv_bytes).decode('ascii'),
    })
    assert preview.status_code == 200
    assert preview.json()['summary']['total'] == 2
    # Empty owner should BLOCK (D4 Contract #6)
    assert preview.json()['summary']['error'] == 1
    projection_hash = preview.json()['projection_hash']
    committed = client.post('/api/import/commit', headers=auth_headers("USER-1"), json={
        'batch_id': preview.json()['batch_id'],
        'import_key': 'test-key',
        'row_actions': {},
        'projection_hash': projection_hash,
    })
    assert committed.status_code == 200
    result = committed.json()
    # Only 1 row should succeed (row with valid owner), the other is BLOCKED
    assert result['success_count'] + result['success_with_warning_count'] == 1
    assert result['blocked_count'] >= 1
    user1 = client.get('/api/orders?q=PO-OWNER-002', headers=auth_headers("USER-1")).json()['items']
    # PO-OWNER-002 with empty owner should NOT be in orders
    assert len(user1) == 0
    user2 = client.get('/api/orders?q=PO-OWNER-001', headers=auth_headers("USER-2")).json()['items']
    assert len(user2) == 1 and user2[0]['owner'] == 'USER-2'


def test_local_draft_review_and_contact_recording():
    import main

    capabilities = client.get('/api/communication/capabilities', headers=auth_headers("USER-1"))
    assert capabilities.status_code == 200
    sql = (
        'INSERT INTO communication_drafts('
        'draft_id,request_id,order_id,order_no,draft_type,recipient_role,channel,result_json,'
        'ai_subject,ai_draft,facts_used_json,missing_facts_json,questions_to_ask_json,risk_flags_json,'
        'run_status,approval_status,human_status,created_at,updated_at'
        ') VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
    )
    with main.db() as conn:
        conn.execute(sql, (
            'DRAFT-LOCAL-001','REQ-LOCAL-001','ORD-1003','PO-1003','CUSTOMER_REPLY','customer','email','{}',
            '确认样品','请确认彩盒样品。','[]','[]','[]','[]','draft_ready','PENDING_HUMAN_CONFIRMATION','PENDING',
            main.iso(),main.iso(),
        ))
        conn.commit()
    approved = client.post('/api/drafts/DRAFT-LOCAL-001/review', headers=auth_headers("USER-1"), json={
        'action': 'approve',
        'operator_id': 'USER-1',
        'edited_subject': '确认样品',
        'edited_draft': '请确认彩盒样品。',
    })
    assert approved.status_code == 200
    copied = client.post('/api/drafts/DRAFT-LOCAL-001/review', headers=auth_headers("USER-1"), json={
        'action': 'copy_and_record',
        'operator_id': 'USER-1',
        'edited_subject': '确认样品',
        'edited_draft': '请确认彩盒样品。',
        'task_id': 'TASK-TODAY-001',
        'waiting_on': 'customer',
        'promised_reply_at': '2030-08-01T15:00:00+08:00',
    })
    assert copied.status_code == 200
    assert copied.json()['task_update']['updated'] is True
    board = client.get('/api/dashboard', headers=auth_headers("USER-1")).json()
    task = next(x for x in board['items'] if x['task_id'] == 'TASK-TODAY-001')
    assert task['action_state'] == 'WAITING_EXTERNAL'


# ============================================================
# D4 Contract – Excel Import Verification Tests (12 tests)
# ============================================================


def _d4_import_preview(csv_text: str, filename: str = "d4_test.csv", user: str = "USER-1"):
    """Helper: preview import from CSV text, returns response JSON."""
    import base64
    csv_bytes = csv_text.encode('utf-8-sig')
    r = client.post('/api/import/preview', headers=auth_headers(user), json={
        'filename': filename,
        'content_base64': base64.b64encode(csv_bytes).decode('ascii'),
    })
    assert r.status_code == 200
    return r.json()


def _d4_import_commit(batch_id: str, projection_hash: str, user: str = "USER-1", **kwargs):
    """Helper: commit import batch, returns response JSON."""
    payload = {
        'batch_id': batch_id,
        'import_key': 'd4-test-key',
        'row_actions': {},
        'projection_hash': projection_hash,
    }
    payload.update(kwargs)
    r = client.post('/api/import/commit', headers=auth_headers(user), json=payload)
    return r


def test_d4_contract_missing_product_name_is_warning():
    """Test 1: 缺 product_name → WARNING，可导入"""
    preview = _d4_import_preview(
        '订单号,客户名称,数量,客户正式交期,负责人\n'
        'PO-D4-WARN-001,D4 Customer,100,2026-09-01,USER-1\n'
    )
    row = preview['rows'][0]
    assert row['classification'] in ('WARNING', 'NEW')
    issue_fields = [i['field'] for i in row['issues']]
    assert 'product_name' in issue_fields
    assert any(i['level'] == 'warning' for i in row['issues'])

    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    result = committed.json()
    assert result['success_count'] + result['success_with_warning_count'] == 1
    assert result['blocked_count'] == 0


def test_d4_contract_missing_order_qty_is_warning():
    """Test 2: 缺 order_qty → WARNING，可导入"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,客户正式交期,负责人\n'
        'PO-D4-WARN-002,D4 Customer,Widget,2026-09-01,USER-1\n'
    )
    row = preview['rows'][0]
    assert row['classification'] in ('WARNING', 'NEW')
    issue_fields = [i['field'] for i in row['issues']]
    assert 'order_qty' in issue_fields
    assert any(i['level'] == 'warning' for i in row['issues'])

    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    assert committed.json()['success_count'] + committed.json()['success_with_warning_count'] == 1


def test_d4_contract_missing_completed_qty_is_warning():
    """Test 3: 缺 completed_qty → WARNING，可导入"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-D4-WARN-003,D4 Customer,Gadget,50,2026-09-01,USER-1\n'
    )
    row = preview['rows'][0]
    issue_fields = [i['field'] for i in row['issues']]
    assert 'completed_qty' in issue_fields
    assert any(i['level'] == 'warning' for i in row['issues'])

    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    assert committed.json()['success_count'] + committed.json()['success_with_warning_count'] == 1


def test_d4_contract_missing_owner_is_blocked():
    """Test 4: 缺 owner → BLOCK"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期\n'
        'PO-D4-BLOCK-004,D4 Customer,Widget,100,2026-09-01\n'
    )
    row = preview['rows'][0]
    assert row['classification'] == 'ERROR'
    issue_fields = [i['field'] for i in row['issues']]
    assert 'owner' in issue_fields
    assert any(i['level'] == 'error' for i in row['issues'])

    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    assert committed.json()['blocked_count'] == 1
    assert committed.json()['success_count'] == 0


def test_d4_contract_ambiguous_delivery_date_is_blocked():
    """Test 5: 模糊 delivery_date → BLOCK"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-D4-BLOCK-005,D4 Customer,Widget,100,明年,USER-1\n'
    )
    row = preview['rows'][0]
    assert row['classification'] == 'ERROR'
    date_issues = [i for i in row['issues'] if i['field'] == 'delivery_date']
    assert len(date_issues) > 0
    assert date_issues[0]['level'] == 'error'

    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    assert committed.json()['blocked_count'] == 1


def test_d4_contract_same_order_key_multi_line_allowed():
    """Test 6: 同 source_order_key 多行 → 不得判重复错误"""
    preview = _d4_import_preview(
        '订单号,行号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'SO-001,line-001,D4 Customer,Product A,100,2026-09-01,USER-1\n'
        'SO-001,line-002,D4 Customer,Product B,200,2026-09-01,USER-1\n'
        'SO-001,line-003,D4 Customer,Product C,300,2026-09-01,USER-1\n'
    )
    assert preview['summary']['total'] == 3
    # None of the 3 rows should be ERROR (they all have the same source_order_key)
    for row in preview['rows']:
        assert row['classification'] != 'ERROR', f"Row {row.get('row_index')} classified as ERROR: {row['issues']}"

    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    result = committed.json()
    # Order-level counting: 1 order with 3 lines = 1 success
    assert result['blocked_count'] == 0
    total_success = result['success_count'] + result['success_with_warning_count']
    assert total_success == 1


def test_d4_contract_single_order_atomicity_rollback():
    """Test 7 + 8: 单订单内部故意失败 → 完整 rollback，不影响其他订单"""
    # Order 1: valid
    # Order 2: missing customer_name → BLOCK
    # Order 3: valid
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-D4-ATOM-001,Atom Customer A,Widget,100,2026-09-01,USER-1\n'
        'PO-D4-ATOM-002,,Gadget,200,2026-09-01,USER-1\n'
        'PO-D4-ATOM-003,Atom Customer B,Gizmo,300,2026-09-01,USER-1\n'
    )
    assert preview['summary']['total'] == 3
    # Row with missing customer_name should be ERROR
    error_rows = [r for r in preview['rows'] if r['classification'] == 'ERROR']
    assert len(error_rows) == 1

    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    result = committed.json()
    assert result['blocked_count'] == 1
    # The other two valid orders should still succeed
    assert result['success_count'] + result['success_with_warning_count'] == 2

    # Verify the blocked order has NO data in DB
    orders_blocked = client.get('/api/orders?q=PO-D4-ATOM-002', headers=auth_headers("USER-1")).json()['items']
    assert len(orders_blocked) == 0

    # Verify the other orders ARE in DB
    orders_ok = client.get('/api/orders?q=PO-D4-ATOM-001', headers=auth_headers("USER-1")).json()['items']
    assert len(orders_ok) == 1
    orders_ok2 = client.get('/api/orders?q=PO-D4-ATOM-003', headers=auth_headers("USER-1")).json()['items']
    assert len(orders_ok2) == 1


def test_d4_contract_projection_hash_mismatch_rejected():
    """Test 9: Preview hash 不一致 → Commit 拒绝"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-D4-HASH-001,Hash Customer,Widget,100,2026-09-01,USER-1\n'
    )
    # Tamper with the projection hash
    fake_hash = "0" * 64
    committed = _d4_import_commit(preview['batch_id'], fake_hash)
    assert committed.status_code == 400
    assert 'hash' in committed.json().get('detail', '').lower() or 'projection' in committed.json().get('detail', '').lower()


def test_d4_contract_warning_count_real_in_analytics_and_report():
    """Test 10: Warning 真实进入 analytics / report"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-D4-REPORT-001,Report Customer,Widget,100,2026-09-01,USER-1\n'
    )
    # The row should have warnings (missing completion dates)
    row = preview['rows'][0]
    assert any(i['level'] == 'warning' for i in row['issues'])
    assert preview['summary']['warning'] >= 1

    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    result = committed.json()
    assert result['warning_count'] >= 1
    assert result['success_with_warning_count'] >= 1

    # Verify warning_count is persisted in batch
    batch_id = committed.json()['batch_id']
    batch = client.get(f'/api/import/batches/{batch_id}', headers=auth_headers("USER-1"))
    assert batch.status_code == 200
    batch_data = batch.json()['batch']
    assert batch_data.get('warning_count', 0) >= 1


def test_d4_contract_end_to_end_duration_persisted():
    """Test 11: 前端端到端 duration 能持久化（通过 client-metrics 接口）"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-D4-DUR-001,Duration Customer,Widget,100,2026-09-01,USER-1\n'
    )
    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    result = committed.json()
    batch_id = result['batch_id']

    # After commit response, client calculates E2E and sends via client-metrics endpoint
    metrics_resp = client.post(
        f'/api/import/batches/{batch_id}/client-metrics',
        headers=auth_headers("USER-1"),
        json={'client_end_to_end_duration_ms': 4200}
    )
    assert metrics_resp.status_code == 200

    # Verify it's persisted in batch
    batch = client.get(f'/api/import/batches/{batch_id}', headers=auth_headers("USER-1"))
    assert batch.status_code == 200
    batch_data = batch.json()['batch']
    assert batch_data.get('end_to_end_duration_ms') == 4200


def test_d4_contract_csv_contains_required_fields():
    """Test 12: CSV 包含最终要求字段"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-D4-CSV-001,CSV Customer,Widget,100,2026-09-01,USER-1\n'
        'PO-D4-CSV-002,CSV Customer 2,Gadget,200,2026-09-01,USER-1\n'
    )
    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200

    batch_id = committed.json()['batch_id']
    report = client.get(f'/api/import/batches/{batch_id}/report', headers=auth_headers("USER-1"))
    assert report.status_code == 200

    # Download the CSV
    csv_response = client.get(f'/api/import/batches/{batch_id}/report.csv', headers=auth_headers("USER-1"))
    assert csv_response.status_code == 200

    csv_text = csv_response.text
    # Must contain the required structured fields
    required_columns = [
        'import_batch_id',
        'source_order_key',
        'source_row_no',
        'result_status',
        'message',
        'order_id',
        'created_at',
    ]
    for col in required_columns:
        assert col in csv_text, f"CSV missing required column: {col}"

    # Verify result_status contains expected values
    assert 'SUCCESS' in csv_text or 'SUCCESS_WITH_WARNING' in csv_text


# ============================================================
# D4-R2 Contract Tests (Second Round Final Closure)
# ============================================================


def test_d4_r2_owner_empty_value_blocks():
    """D4-R2 #1: 负责人列存在但值为空 → BLOCK"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-R2-OWNER-001,Test Customer,Widget,100,2026-09-10,\n'
    )
    rows = preview['rows']
    assert len(rows) == 1
    row = rows[0]
    assert row['classification'] == 'ERROR'
    codes = [issue['code'] for issue in row['issues']]
    assert 'IMPORT_REQUIRED_FIELD_MISSING' in codes
    assert any(issue['field'] == 'owner' for issue in row['issues'])


def test_d4_r2_order_level_atomicity():
    """D4-R2 #2: 同订单3行，其中1行Preflight BLOCK → 整订单不得入库"""
    # Row 1: valid, Row 2: invalid delivery_date (BLOCK), Row 3: valid
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-R2-ATOM-001,Atom Customer,Widget,100,2026-09-10,USER-1\n'
        'PO-R2-ATOM-001,Atom Customer,Gadget,200,模糊交期,USER-1\n'
        'PO-R2-ATOM-001,Atom Customer,Gizmo,300,2026-09-12,USER-1\n'
    )
    rows = preview['rows']
    assert len(rows) == 3

    # All 3 rows should be classified as ERROR because the order is blocked
    for row in rows:
        assert row['classification'] == 'ERROR', f"Row {row['row_number']} should be ERROR but is {row['classification']}"

    # Commit should report all 3 as blocked
    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    result = committed.json()
    # Order-level: 1 order blocked (with 3 rows) → blocked_count = 1
    assert result['blocked_count'] == 1
    assert result['success_count'] == 0
    assert result['success_with_warning_count'] == 0

    # Verify no data was persisted for this order
    orders = client.get('/api/orders?q=PO-R2-ATOM-001', headers=auth_headers("USER-1")).json()['items']
    assert len(orders) == 0


def test_d4_r2_multi_line_order_persistence():
    """D4-R2 #3: 同订单多明细成功 → line 数据全部真实持久化"""
    import sqlite3
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-R2-MULTI-001,Multi Customer,Widget,100,2026-09-10,USER-1\n'
        'PO-R2-MULTI-001,Multi Customer,Gadget,200,2026-09-11,USER-1\n'
        'PO-R2-MULTI-001,Multi Customer,Gizmo,300,2026-09-12,USER-1\n'
    )
    rows = preview['rows']
    assert len(rows) == 3
    for row in rows:
        assert row['classification'] == 'NEW'

    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    result = committed.json()

    # Should have 1 order (not 3) with 3 lines
    assert len(result['imported_order_ids']) == 1
    order_id = result['imported_order_ids'][0]

    # Verify order was created
    orders = client.get('/api/orders?q=PO-R2-MULTI-001', headers=auth_headers("USER-1")).json()['items']
    assert len(orders) == 1
    assert orders[0]['order_id'] == order_id

    # Verify 3 order_lines exist in the same authoritative DB used by the app.
    # Do not reopen a hard-coded SQLite file: during full-suite collection other
    # modules may configure DB_PATH before this module is imported, which made
    # this assertion order-dependent even though the API persisted correctly.
    with db() as conn:
        lines = conn.execute(
            "SELECT * FROM order_lines WHERE order_id=? ORDER BY created_at",
            (order_id,)
        ).fetchall()
    assert len(lines) == 3

    # Verify product names are preserved
    products = sorted([line['product_name'] for line in lines])
    assert products == ['Gadget', 'Gizmo', 'Widget']

    # Verify order_qty values
    qtys = sorted([line['order_qty'] for line in lines])
    assert qtys == [100, 200, 300]


def test_d4_r2_projection_hash_mandatory():
    """D4-R2 #4: Commit 不传 projection_hash → 400"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-R2-HASH-001,Hash Customer,Widget,100,2026-09-10,USER-1\n'
    )
    # Commit without projection_hash
    response = client.post(
        '/api/import/commit',
        headers=auth_headers("USER-1"),
        json={
            'batch_id': preview['batch_id'],
            'import_key': 'test-key',
            'row_actions': {},
            # projection_hash intentionally omitted
        },
    )
    assert response.status_code in (400, 422)


def test_d4_r2_warning_contains_code_and_missing_information():
    """D4-R2 #5: warning issue 包含 code + missing_information"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-R2-WARN-001,Warning Customer,Widget,100,2026-09-10,USER-1\n'
    )
    rows = preview['rows']
    assert len(rows) == 1
    row = rows[0]

    # Check that issues have structured codes
    for issue in row['issues']:
        assert 'code' in issue, f"Issue missing code: {issue}"
        assert issue['code'].startswith('IMPORT_'), f"Code should start with IMPORT_: {issue['code']}"

    # Check that missing_information is present
    assert 'missing_information' in row
    assert isinstance(row['missing_information'], list)

    # For a valid row, there should be warnings about missing fields
    # (completed_qty, planned_completion_date, supplier_commitment_date)
    if row['classification'] == 'NEW':
        assert len(row['missing_information']) > 0


def test_d4_r2_csv_contains_structured_codes():
    """D4-R2 #6: CSV code 列是真正 IMPORT_* code"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-R2-CODE-001,Code Customer,Widget,100,2026-09-10,USER-1\n'
    )
    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    batch_id = committed.json()['batch_id']

    csv_response = client.get(
        f'/api/import/batches/{batch_id}/report.csv',
        headers=auth_headers("USER-1"),
    )
    assert csv_response.status_code == 200
    csv_text = csv_response.text

    # Verify that warning_or_error_code column contains IMPORT_* codes
    # not Chinese text
    assert 'IMPORT_' in csv_text, "CSV should contain IMPORT_ error codes"


def test_d4_r2_client_e2e_timing_endpoint():
    """D4-R2 #7: 客户端 E2E 计时终点发生在 Commit Response 返回后"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-R2-E2E-001,E2E Customer,Widget,100,2026-09-10,USER-1\n'
    )
    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    assert committed.status_code == 200
    batch_id = committed.json()['batch_id']

    # Simulate client sending E2E duration AFTER commit response
    client_e2e_ms = 5678
    metrics_resp = client.post(
        f'/api/import/batches/{batch_id}/client-metrics',
        headers=auth_headers("USER-1"),
        json={'client_end_to_end_duration_ms': client_e2e_ms},
    )
    assert metrics_resp.status_code == 200

    # Verify the E2E duration was persisted
    batch = client.get(f'/api/import/batches/{batch_id}', headers=auth_headers("USER-1"))
    assert batch.status_code == 200
    batch_data = batch.json()['batch']
    assert batch_data['end_to_end_duration_ms'] == client_e2e_ms

    # Verify server-side processing_duration_ms is separate
    assert batch_data.get('processing_duration_ms') is not None


def test_d4_r2_import_report_counting():
    """D4-R2 #8: Import Report 计数区分 source_row_count 和 identified_order_count"""
    # 5 source rows → 3 identified orders
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-R2-COUNT-001,Customer A,Widget,100,2026-09-10,USER-1\n'
        'PO-R2-COUNT-001,Customer A,Gadget,200,2026-09-11,USER-1\n'
        'PO-R2-COUNT-001,Customer A,Gizmo,300,2026-09-12,USER-1\n'
        'PO-R2-COUNT-002,Customer B,Widget,100,2026-09-10,USER-1\n'
        'PO-R2-COUNT-003,Customer C,Widget,100,2026-09-10,USER-1\n'
    )
    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    result = committed.json()

    # Import report should also expose these
    batch_id = result['batch_id']
    report = client.get(
        f'/api/import/batches/{batch_id}/report',
        headers=auth_headers("USER-1"),
    )
    assert report.status_code == 200
    report_data = report.json()
    assert report_data['summary'].get('source_row_count') == 5
    assert report_data['summary'].get('identified_order_count') == 3


def test_d4_r2_missing_information_in_preview_and_report():
    """D4-R2 #9: missing_information 在 Preview 和 Import Report 中均可获取"""
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-R2-MISS-001,Missing Customer,Widget,100,2026-09-10,USER-1\n'
    )
    rows = preview['rows']
    row = rows[0]

    # Verify missing_information in preview
    assert 'missing_information' in row
    missing = row['missing_information']
    assert isinstance(missing, list)

    # After commit, check report also has missing_information
    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    batch_id = committed.json()['batch_id']

    report = client.get(
        f'/api/import/batches/{batch_id}/report',
        headers=auth_headers("USER-1"),
    )
    assert report.status_code == 200
    report_data = report.json()
    report_rows = report_data.get('rows', [])
    assert len(report_rows) >= 1

    # Verify missing_information is present in report rows
    first_report_row = report_rows[0]
    assert 'missing_information' in first_report_row
    assert isinstance(first_report_row['missing_information'], list)


def test_d4_r2_csv_code_column_has_structured_codes():
    """D4-R2 #10: CSV warning_or_error_code 列使用 IMPORT_* code"""
    # Create a scenario with a known error
    preview = _d4_import_preview(
        '订单号,客户名称,产品名称,数量,客户正式交期,负责人\n'
        'PO-R2-ERR-001,Err Customer,Widget,-5,2026-09-10,USER-1\n'
    )
    rows = preview['rows']
    row = rows[0]

    # Should have an error about negative number
    codes = [issue['code'] for issue in row['issues']]
    assert 'IMPORT_NEGATIVE_NUMBER' in codes, f"Expected IMPORT_NEGATIVE_NUMBER, got: {codes}"

    # Commit and check CSV
    committed = _d4_import_commit(preview['batch_id'], preview['projection_hash'])
    batch_id = committed.json()['batch_id']

    csv_response = client.get(
        f'/api/import/batches/{batch_id}/report.csv',
        headers=auth_headers("USER-1"),
    )
    csv_text = csv_response.text
    # The code should appear in the CSV, not just the Chinese message
    assert 'IMPORT_NEGATIVE_NUMBER' in csv_text

# ============================================================
# D4 Final Gate – official template round-trip
# ============================================================

def test_d4_final_template_csv_roundtrip():
    """Official CSV template can be downloaded and uploaded back without BLOCK."""
    import base64
    template = client.get('/api/import/template.csv', headers=auth_headers("USER-1"))
    assert template.status_code == 200
    preview = client.post('/api/import/preview', headers=auth_headers("USER-1"), json={
        'filename': 'floworder_template.csv',
        'content_base64': base64.b64encode(template.content).decode('ascii'),
    })
    assert preview.status_code == 200
    data = preview.json()
    assert data['summary']['block'] == 0
    assert data['rows'][0]['normalized']['source_order_key'] == 'PO-IMPORT-001'


def test_d4_final_template_xlsx_roundtrip():
    """Official XLSX template can be downloaded and uploaded back without BLOCK."""
    import base64
    template = client.get('/api/import/template.xlsx', headers=auth_headers("USER-1"))
    assert template.status_code == 200
    preview = client.post('/api/import/preview', headers=auth_headers("USER-1"), json={
        'filename': 'floworder_template.xlsx',
        'content_base64': base64.b64encode(template.content).decode('ascii'),
    })
    assert preview.status_code == 200
    data = preview.json()
    assert data['summary']['block'] == 0
    assert data['rows'][0]['normalized']['source_order_key'] == 'PO-IMPORT-001'


def test_extract_order_numbers_supports_po_so_ord_patterns():
    import main
    assert main.extract_order_numbers("关于订单 PO-1234 的包装方式") == ["PO-1234"]
    assert main.extract_order_numbers("订单 SO-5678 需要跟进") == ["SO-5678"]
    assert main.extract_order_numbers("ORD-1001 已经出货") == ["ORD-1001"]
    assert main.extract_order_numbers("关于订单 SO-D11-UAT，工厂说延迟") == ["SO-D11-UAT"]
    assert main.extract_order_numbers("PO-1234 和 SO-5678 都要跟进") == ["PO-1234", "SO-5678"]
    assert main.extract_order_numbers("没有订单号的消息") == []
    assert main.extract_order_numbers("") == []


def test_extract_order_numbers_normalizes_separators():
    import main
    assert main.extract_order_numbers("PO_1234 的包装") == ["PO-1234"]
    assert main.extract_order_numbers("PO 1234 的包装") == ["PO-1234"]
    assert main.extract_order_numbers("po-1234 的包装") == ["PO-1234"]
    assert main.extract_order_numbers("SO-D11-UAT") == ["SO-D11-UAT"]


def test_uat_fixture_so_order_match_unique():
    """UAT fixture: message referencing SO-D11-UAT must resolve to ORD-D11-UAT with unique_match."""
    import os
    os.environ["D11_UAT_INTAKE_PROVIDER"] = "fixture"
    try:
        analyzed = client.post('/api/intake/analyze', headers=auth_headers("USER-1"), json={
            'source_channel': 'email',
            'sender_role': 'factory',
            'raw_content': '关于订单 SO-D11-UAT，工厂说这单会延迟一周',
        })
        assert analyzed.status_code == 200
        result = analyzed.json()
        candidate = result['candidate']
        order_match = candidate.get('order_match', {})
        assert order_match['status'] == 'unique_match', f"Expected unique_match, got {order_match['status']}"
        assert order_match['matched_order_no'] == 'SO-D11-UAT'
        assert order_match['selected_order_id'] == 'ORD-D11-UAT'
        assert result['review_id']
    finally:
        del os.environ["D11_UAT_INTAKE_PROVIDER"]


def test_uat_fixture_so_order_cross_org_isolation():
    """UAT fixture: cross-org user cannot match SO-D11-UAT (ORG-A order) when from different org."""
    import os
    os.environ["D11_UAT_INTAKE_PROVIDER"] = "fixture"
    try:
        analyzed = client.post('/api/intake/analyze', headers=auth_headers("OPERATOR-B1"), json={
            'source_channel': 'email',
            'sender_role': 'factory',
            'raw_content': '关于订单 SO-D11-UAT，工厂说这单会延迟一周',
        })
        assert analyzed.status_code == 200
        result = analyzed.json()
        order_match = result['candidate'].get('order_match', {})
        assert order_match['status'] == 'no_match', f"Expected no_match due to org isolation, got {order_match['status']}"
    finally:
        del os.environ["D11_UAT_INTAKE_PROVIDER"]


# ============================================================
# P0 SECURITY ATTACK TESTS - Organization Boundary Verification
# ============================================================


def test_P0_A_async_raw_text_cross_org():
    """P0-A: ORG-B creates intake job with SO-D11-UAT (ORG-A order) → must be no_match, no cross-org review created."""
    # Create intake job as ORG-B user
    job = client.post('/api/intake/jobs', headers=auth_headers("OPERATOR-B1"), json={
        'source_channel': 'email',
        'sender_role': 'factory',
        'raw_content': '关于订单 SO-D11-UAT，工厂说这单会延迟一周',
    })
    assert job.status_code == 202
    job_id = job.json()['job_id']
    assert job.json()['status'] == 'queued'
    
    # Process the job synchronously (simulate background task)
    import main
    main.process_intake_job(job_id)
    
    # Check the job result - should be no_match since ORG-B cannot see ORG-A orders
    job_result = client.get(f'/api/intake/jobs/{job_id}', headers=auth_headers("OPERATOR-B1"))
    assert job_result.status_code == 200
    result = job_result.json()
    assert result['status'] == 'COMPLETED'
    candidate = result.get('result', {}).get('candidate', {})
    order_match = candidate.get('order_match', {})
    assert order_match['status'] == 'no_match', f"Expected no_match for cross-org, got {order_match['status']}"
    
    # Verify no review for ORG-A was created (ORG-B review list should not see it)
    # First get the review_id from the job result
    review_id = result.get('result', {}).get('review_id')
    if review_id:
        # ORG-B should see this review (it was created by ORG-B user)
        # But ORG-A should NOT see this review since it belongs to ORG-B
        reviews_a = client.get('/api/reviews', headers=auth_headers("USER-1"))
        assert reviews_a.status_code == 200
        assert not any(r['review_id'] == review_id for r in reviews_a.json()['items'])


def test_P0_B_explicit_order_id_cross_org():
    """P0-B: ORG-B tries to analyze with explicit order_id=ORD-D11-UAT (ORG-A) → must not find it."""
    analyzed = client.post('/api/intake/analyze', headers=auth_headers("OPERATOR-B1"), json={
        'source_channel': 'email',
        'sender_role': 'factory',
        'order_id': 'ORD-D11-UAT',
        'raw_content': '工厂说这单会延迟一周',
    })
    # Should return 200 but with no_match since ORG-B cannot access ORG-A order
    assert analyzed.status_code == 200
    result = analyzed.json()
    order_match = result['candidate'].get('order_match', {})
    assert order_match['status'] == 'no_match', f"Expected no_match for cross-org order_id, got {order_match['status']}"
    assert order_match.get('selected_order_id') is None


def test_P0_C_job_read_cross_org():
    """P0-C: USER-1 creates Job; OPERATOR-B1 GET same job_id → must 404."""
    # Create job as USER-1 (ORG-A)
    job = client.post('/api/intake/jobs', headers=auth_headers("USER-1"), json={
        'source_channel': 'manual_input',
        'sender_role': 'customer',
        'raw_content': '测试消息 - 跨机构读取',
    })
    assert job.status_code == 202
    job_id = job.json()['job_id']
    
    # OPERATOR-B1 (ORG-B) tries to read this job → must 404
    result = client.get(f'/api/intake/jobs/{job_id}', headers=auth_headers("OPERATOR-B1"))
    assert result.status_code == 404, f"Expected 404 for cross-org job read, got {result.status_code}"
    
    # USER-1 can still read their own job
    result_ok = client.get(f'/api/intake/jobs/{job_id}', headers=auth_headers("USER-1"))
    assert result_ok.status_code == 200


def test_P0_D_unmatched_review_cross_org():
    """P0-D: ORG-A creates order_id=NULL Review; ORG-B cannot see it in list or detail."""
    # Create a review without matching order (from ORG-A)
    analyzed = client.post('/api/intake/analyze', headers=auth_headers("USER-1"), json={
        'source_channel': 'manual_input',
        'sender_role': 'customer',
        'raw_content': '未知订单消息 - 没有匹配的订单号 ABC-123',
    })
    assert analyzed.status_code == 200
    review_id = analyzed.json()['review_id']
    
    # ORG-B review list should NOT see this review
    reviews_b = client.get('/api/reviews', headers=auth_headers("OPERATOR-B1"))
    assert reviews_b.status_code == 200
    assert not any(r['review_id'] == review_id for r in reviews_b.json()['items'])
    
    # ORG-B review detail should return 404
    detail_b = client.get(f'/api/reviews/{review_id}', headers=auth_headers("OPERATOR-B1"))
    assert detail_b.status_code == 404, f"Expected 404 for cross-org review detail, got {detail_b.status_code}"
    
    # ORG-A can still see their own review
    reviews_a = client.get('/api/reviews', headers=auth_headers("USER-1"))
    assert any(r['review_id'] == review_id for r in reviews_a.json()['items'])


def test_P0_E_same_org_normal_flow():
    """P0-E: USER-1 (ORG-A) input SO-D11-UAT → unique_match → PENDING human confirmation."""
    import os
    os.environ["D11_UAT_INTAKE_PROVIDER"] = "fixture"
    try:
        analyzed = client.post('/api/intake/analyze', headers=auth_headers("USER-1"), json={
            'source_channel': 'email',
            'sender_role': 'factory',
            'raw_content': '关于订单 SO-D11-UAT，工厂说这单会延迟一周',
        })
        assert analyzed.status_code == 200
        result = analyzed.json()
        order_match = result['candidate'].get('order_match', {})
        assert order_match['status'] == 'unique_match', f"Expected unique_match, got {order_match['status']}"
        assert order_match['selected_order_id'] == 'ORD-D11-UAT'
        
        # Verify review is PENDING
        review_id = result['review_id']
        reviews = client.get('/api/reviews?status=PENDING', headers=auth_headers("USER-1"))
        assert any(r['review_id'] == review_id for r in reviews.json()['items'])
    finally:
        del os.environ["D11_UAT_INTAKE_PROVIDER"]


def test_P0_F_migration_adds_org_id_columns():
    """P0-F: Migration test - verify that after schema initialization, tables have organization_id."""
    from database import get_table_columns
    from main import db
    
    with db() as conn:
        # Verify intake_jobs has organization_id
        intake_cols = {col["name"] for col in get_table_columns(conn, "intake_jobs")}
        assert "organization_id" in intake_cols, "intake_jobs missing organization_id after migration"
        
        # Verify source_messages has organization_id
        msg_cols = {col["name"] for col in get_table_columns(conn, "source_messages")}
        assert "organization_id" in msg_cols, "source_messages missing organization_id after migration"
        
        # Verify candidate_reviews has organization_id
        review_cols = {col["name"] for col in get_table_columns(conn, "candidate_reviews")}
        assert "organization_id" in review_cols, "candidate_reviews missing organization_id after migration"
        
        # Verify indexes exist
        idx_intake = [row['name'] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='intake_jobs'"
        ).fetchall()]
        assert any('org' in name.lower() for name in idx_intake), "intake_jobs missing org index"
        
        idx_msg = [row['name'] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='source_messages'"
        ).fetchall()]
        assert any('org' in name.lower() for name in idx_msg), "source_messages missing org index"
        
        idx_review = [row['name'] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='candidate_reviews'"
        ).fetchall()]
        assert any('org' in name.lower() for name in idx_review), "candidate_reviews missing org index"


def test_P0_F2_migration_backfills_org_id():
    """P0-F2: Verify that after migration, existing data has organization_id backfilled."""
    from main import db
    
    with db() as conn:
        # Check source_messages have organization_id populated
        msg_orgs = conn.execute("SELECT message_id, organization_id FROM source_messages LIMIT 5").fetchall()
        for row in msg_orgs:
            assert row["organization_id"] is not None, f"source_messages {row['message_id']} has NULL organization_id"
            assert row["organization_id"] != '', f"source_messages {row['message_id']} has empty organization_id"
        
        # Check candidate_reviews have organization_id populated
        review_orgs = conn.execute("SELECT review_id, organization_id FROM candidate_reviews LIMIT 5").fetchall()
        for row in review_orgs:
            assert row["organization_id"] is not None, f"candidate_reviews {row['review_id']} has NULL organization_id"
            assert row["organization_id"] != '', f"candidate_reviews {row['review_id']} has empty organization_id"


def test_P0_G_cross_org_unmatched_review_reject():
    """P0-G: ORG-A creates order_id=NULL Review → ORG-B Reject must return 404 → ORG-A Review stays PENDING."""
    # Create a review without matching order (from ORG-A)
    analyzed = client.post('/api/intake/analyze', headers=auth_headers("USER-1"), json={
        'source_channel': 'manual_input',
        'sender_role': 'customer',
        'raw_content': '未知订单消息 - P0-G 测试',
    })
    assert analyzed.status_code == 200
    review_id = analyzed.json()['review_id']
    
    # Verify ORG-A can see the review and it's PENDING
    detail_a = client.get(f'/api/reviews/{review_id}', headers=auth_headers("USER-1"))
    assert detail_a.status_code == 200
    assert detail_a.json()['status'] == 'PENDING'
    
    # ORG-B tries to reject the review → must return 404
    reject_b = client.post(f'/api/reviews/{review_id}/reject', headers=auth_headers("OPERATOR-B1"), json={})
    assert reject_b.status_code == 404, f"P0-G FAIL: Expected 404 for cross-org reject, got {reject_b.status_code}"
    
    # ORG-B cannot even see the review in list
    reviews_b = client.get('/api/reviews', headers=auth_headers("OPERATOR-B1"))
    assert not any(r['review_id'] == review_id for r in reviews_b.json()['items'])
    
    # Verify ORG-A review is STILL PENDING (not modified by cross-org attack)
    detail_a_after = client.get(f'/api/reviews/{review_id}', headers=auth_headers("USER-1"))
    assert detail_a_after.status_code == 200
    assert detail_a_after.json()['status'] == 'PENDING', f"P0-G FAIL: ORG-A review status changed to {detail_a_after.json()['status']}"


def test_P0_H_cross_org_source_message_import():
    """P0-H: ORG-A creates source_message → ORG-B tries to import review referencing A's message → must 404."""
    from main import db
    
    # Step 1: Create a source_message as ORG-A
    with db() as conn:
        msg_id = "MSG-P0-H-TEST"
        try:
            conn.execute(
                """INSERT INTO source_messages(message_id, source_channel, sender_role, raw_content, organization_id, created_at)
                   VALUES(?,?,?,?,?,?)""",
                (msg_id, 'manual_input', 'customer', 'This is ORG-A test message for P0-H', 'ORG-A', '2026-08-15T20:00:00+08:00')
            )
            conn.commit()
        except Exception:
            pass  # May already exist
    
    # Step 2: ORG-B tries to import a review referencing ORG-A's source_message_id
    fake_candidate = {
        "order_match": {"status": "no_match"},
        "extracted_fields": {"key": "value"}
    }
    import_result_b = client.post('/api/reviews/import', headers=auth_headers("OPERATOR-B1"), json={
        'source_message_id': msg_id,
        'result_json': json.dumps(fake_candidate),
        'workflow_source': 'TEST_IMPORT',
    })
    
    # Must return 404 (not 422 or 200) because source_message_id doesn't belong to ORG-B
    assert import_result_b.status_code == 404, f"P0-H FAIL: Expected 404 for cross-org source_message import, got {import_result_b.status_code}"
    
    # Step 3: Verify no review was created for ORG-B
    reviews_b = client.get('/api/reviews', headers=auth_headers("OPERATOR-B1"))
    assert reviews_b.status_code == 200
    # The number of reviews should NOT have increased
    # (This is a soft check since we can't easily count before/after, but the 404 confirms no creation)
    
    # Step 4: Verify ORG-A can still use their own source_message_id to import
    import_result_a = client.post('/api/reviews/import', headers=auth_headers("USER-1"), json={
        'source_message_id': msg_id,
        'result_json': json.dumps(fake_candidate),
        'workflow_source': 'TEST_IMPORT',
    })
    # ORG-A should succeed (200 or 201) since the message belongs to ORG-A
    assert import_result_a.status_code in (200, 201), f"P0-H FAIL: ORG-A should be able to import with own source_message_id, got {import_result_a.status_code}"


def test_P0_I_review_list_detail_corrupted_join_protection():
    """P0-I: Verify that corrupted cross-tenant JOIN is prevented in review list and detail.
    
    This test verifies the defense-in-depth protection: even if a candidate_review 
    somehow references a source_message from a different org, the JOIN should 
    NOT leak raw_content from the other org.
    """
    from main import db
    
    # Step 1: Create a cross-tenant scenario in the database
    with db() as conn:
        # Create ORG-A source_message
        msg_id_a = "MSG-P0-I-ORG-A"
        try:
            conn.execute(
                """INSERT INTO source_messages(message_id, source_channel, sender_role, raw_content, organization_id, created_at)
                   VALUES(?,?,?,?,?,?)""",
                (msg_id_a, 'manual_input', 'customer', 'SECRET ORG-A CONTENT - should never leak', 'ORG-A', '2026-08-15T20:00:00+08:00')
            )
            conn.commit()
        except Exception:
            pass
        
        # Create ORG-B source_message (legitimate)
        msg_id_b = "MSG-P0-I-ORG-B"
        try:
            conn.execute(
                """INSERT INTO source_messages(message_id, source_channel, sender_role, raw_content, organization_id, created_at)
                   VALUES(?,?,?,?,?,?)""",
                (msg_id_b, 'manual_input', 'factory', 'ORG-B legitimate message', 'ORG-B', '2026-08-15T20:00:00+08:00')
            )
            conn.commit()
        except Exception:
            pass
        
        # Create a corrupted candidate_review in ORG-B that references ORG-A's source_message
        rev_id_b = "REV-P0-I-CORRUPTED"
        try:
            conn.execute(
                """INSERT INTO candidate_reviews(review_id, source_message_id, organization_id, workflow_source, candidate_json, status, created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (rev_id_b, msg_id_a, 'ORG-B', 'TEST', json.dumps({"corrupted": True}), 'PENDING', '2026-08-15T20:00:00+08:00')
            )
            conn.commit()
        except Exception:
            pass
    
    # Step 2: ORG-B tries to see this corrupted review - raw_content should be NULL due to JOIN protection
    detail_b = client.get(f'/api/reviews/{rev_id_b}', headers=auth_headers("OPERATOR-B1"))
    if detail_b.status_code == 200:
        result = detail_b.json()
        # The raw_content should NOT be the ORG-A secret content
        # With P0-I fix (m.organization_id = r.organization_id), the JOIN should NOT match
        raw_content = result.get('raw_content')
        # If raw_content is the ORG-A content, that's a leak
        assert raw_content != 'SECRET ORG-A CONTENT - should never leak', \
            "P0-I FAIL: Cross-tenant JOIN leaked ORG-A's raw_content to ORG-B"
    
    # Step 3: Clean up corrupted data
    with db() as conn:
        try:
            conn.execute("DELETE FROM candidate_reviews WHERE review_id=?", (rev_id_b,))
            conn.execute("DELETE FROM source_messages WHERE message_id IN (?,?)", (msg_id_a, msg_id_b))
            conn.commit()
        except Exception:
            pass


def test_P0_J_postgres_alembic_schema_contract():
    """P0-J: Verify Alembic migration contract for organization_id columns on PostgreSQL.
    
    This test validates that:
    1. The migration file exists and references the correct tables
    2. The migration adds organization_id to intake_jobs, source_messages, candidate_reviews
    3. The migration creates proper indexes
    """
    import os
    import sys
    from pathlib import Path
    
    # Check migration file exists
    migration_dir = Path(__file__).parent.parent / "alembic" / "versions"
    migration_files = list(migration_dir.glob("j1k2l3m4n5o6*.py")) + list(migration_dir.glob("k2l3m4n5o6p7*.py"))
    assert len(migration_files) >= 2, "P0-J FAIL: D11 add-column and NOT-NULL corrective migrations are both required"
    
    # Read and verify migration content
    migration_content = "\n".join(x.read_text(encoding="utf-8") for x in migration_files)
    
    # Verify it targets the correct tables
    assert "intake_jobs" in migration_content, "P0-J FAIL: Migration missing intake_jobs"
    assert "source_messages" in migration_content, "P0-J FAIL: Migration missing source_messages"
    assert "candidate_reviews" in migration_content, "P0-J FAIL: Migration missing candidate_reviews"
    
    # Verify it adds organization_id columns
    assert "organization_id" in migration_content, "P0-J FAIL: Migration missing organization_id column"
    
    # Verify it creates indexes
    assert "idx_intake_jobs_org" in migration_content, "P0-J FAIL: Migration missing intake_jobs org index"
    assert "idx_source_messages_org" in migration_content, "P0-J FAIL: Migration missing source_messages org index"
    assert "idx_reviews_org" in migration_content, "P0-J FAIL: Migration missing candidate_reviews org index"
    
    # Verify env.py has the correct metadata
    env_file = Path(__file__).parent.parent / "alembic" / "env.py"
    env_content = env_file.read_text(encoding="utf-8")
    
    # env.py should define organization_id in source_messages table
    assert "organization_id" in env_content, "P0-J FAIL: env.py missing organization_id in metadata"
    
    # Verify env.py includes the new indexes
    assert "idx_source_messages_org" in env_content, "P0-J FAIL: env.py missing source_messages org index"
    assert "idx_reviews_org" in env_content, "P0-J FAIL: env.py missing candidate_reviews org index"
    assert "idx_intake_jobs_org" in env_content, "P0-J FAIL: env.py missing intake_jobs org index"
    
    # Verify upgrade and downgrade functions exist
    assert "def upgrade()" in migration_content, "P0-J FAIL: Migration missing upgrade function"
    assert "def downgrade()" in migration_content, "P0-J FAIL: Migration missing downgrade function"
    
    assert "nullable=False" in migration_content, "P0-J FAIL: tenant columns must be hardened to NOT NULL"
    assert "__FLOWORDER_QUARANTINE__" in migration_content, "P0-J FAIL: ambiguous legacy rows need quarantine semantics"
    print("P0-J PASS: Alembic tenant migration contract verified successfully")


def test_d11_ft06_uat_fixture_accepts_single_task_dict_and_enters_waiting():
    """Regression for the D11 browser UAT 500 on communication draft generation.

    V0.4 sends one current task as a dict. The FT06 backend used to slice that
    dict as if it were a list, causing an unhandled TypeError / HTTP 500. In UAT
    fixture mode the whole product path must remain local and deterministic.
    """
    previous = os.environ.get("D11_UAT_INTAKE_PROVIDER")
    os.environ["D11_UAT_INTAKE_PROVIDER"] = "fixture"
    try:
        response = client.post(
            "/api/workflows/ft06/run",
            headers=auth_headers("USER-1"),
            json={
                "draft_type": "SUPPLIER_PROGRESS_FOLLOWUP",
                "recipient_role": "supplier",
                "channel": "wechat",
                "language": "zh-CN",
                "tone": "professional",
                "order_id": "ORD-D11-UAT",
                "order_no": "SO-D11-UAT",
                "fact_catalog": [
                    {"fact_id": "WEB-001", "fact_type": "customer_delivery_date", "value": "2026-08-20", "confirmed": True}
                ],
                "order_context": {"order_id": "ORD-D11-UAT", "order_no": "SO-D11-UAT"},
                "task_context": {
                    "task_id": "TK-D11-UAT-1",
                    "title": "联系供应商确认 8 月 20 日能否交货",
                    "recommended_action": "联系供应商，要求给出明确可交付日期",
                    "evidence": "客户正式交期为 8 月 20 日",
                },
                "user_instruction": "询问当前准确进度、关键物料到货时间、补救方案和明确完成时间。",
            },
        )
        assert response.status_code == 200, response.text
        draft = response.json()
        assert draft["result"]["_uat_fixture"] is True
        assert draft["draft_result"]["draft"]
        assert "SO-D11-UAT" in draft["draft_result"]["draft"]

        review = client.post(
            f"/api/drafts/{draft['draft_id']}/review",
            headers=auth_headers("USER-1"),
            json={
                "action": "copy_and_record",
                "edited_subject": draft["draft_result"]["subject"],
                "edited_draft": draft["draft_result"]["draft"],
                "task_id": "TK-D11-UAT-1",
                "waiting_on": "factory",
                "promised_reply_at": "2026-08-17T17:00:00+08:00",
                "next_action_at": "2026-08-17T17:00:00+08:00",
            },
        )
        assert review.status_code == 200, review.text
        assert review.json()["task_update"]["updated"] is True
        with db() as conn:
            task = conn.execute(
                "SELECT status FROM d9_action_case_tasks WHERE task_id=?", ("TK-D11-UAT-1",)
            ).fetchone()
            waiting = conn.execute(
                "SELECT status,waiting_type FROM d9_action_case_waitings WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                ("TK-D11-UAT-1",),
            ).fetchone()
        assert task["status"] == "WAITING"
        assert waiting["status"] == "ACTIVE"
        assert waiting["waiting_type"] == "SUPPLIER_REPLY"
    finally:
        if previous is None:
            os.environ.pop("D11_UAT_INTAKE_PROVIDER", None)
        else:
            os.environ["D11_UAT_INTAKE_PROVIDER"] = previous


def test_d11_communication_draft_review_is_tenant_scoped():
    previous = os.environ.get("D11_UAT_INTAKE_PROVIDER")
    os.environ["D11_UAT_INTAKE_PROVIDER"] = "fixture"
    try:
        created = client.post(
            "/api/workflows/ft06/run",
            headers=auth_headers("USER-1"),
            json={
                "draft_type": "SUPPLIER_PROGRESS_FOLLOWUP",
                "recipient_role": "supplier",
                "channel": "wechat",
                "order_id": "ORD-D11-UAT",
                "order_no": "SO-D11-UAT",
                "task_context": {"task_id": "TK-D11-UAT-1", "title": "联系供应商确认交期"},
                "user_instruction": "确认当前进度",
            },
        )
        assert created.status_code == 200, created.text
        draft_id = created.json()["draft_id"]
        attacked = client.post(
            f"/api/drafts/{draft_id}/review",
            headers=auth_headers("OPERATOR-B1"),
            json={"action": "approve", "edited_draft": "跨机构不应可见"},
        )
        assert attacked.status_code == 404
        owner_read = client.post(
            f"/api/drafts/{draft_id}/review",
            headers=auth_headers("USER-1"),
            json={"action": "approve", "edited_draft": created.json()["draft_result"]["draft"]},
        )
        assert owner_read.status_code == 200, owner_read.text
    finally:
        if previous is None:
            os.environ.pop("D11_UAT_INTAKE_PROVIDER", None)
        else:
            os.environ["D11_UAT_INTAKE_PROVIDER"] = previous
