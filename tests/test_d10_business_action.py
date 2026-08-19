"""D10 P0 tests: BusinessActionSubmission + Transactional Outbox."""

import json
import os
import sys

import pytest
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import d10_business_action as d10
from database import _ConnectionWrapper

NOW = "2026-08-14T11:00:00+08:00"

SCHEMA_SQL = """
CREATE TABLE orders (
    order_id TEXT PRIMARY KEY,
    order_no TEXT UNIQUE NOT NULL,
    customer_name TEXT,
    status TEXT NOT NULL DEFAULT 'ACTIVE',
    owner TEXT,
    organization_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE action_cases (
    action_case_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    action_intent_key TEXT NOT NULL,
    intent_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'ACTIVE',
    observation_status TEXT NOT NULL DEFAULT 'OBSERVED',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    close_reason TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(order_id) REFERENCES orders(order_id)
);
CREATE TABLE d9_action_case_tasks (
    task_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    title TEXT NOT NULL,
    recommended_action TEXT,
    status TEXT NOT NULL DEFAULT 'TODO',
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(action_case_id) REFERENCES action_cases(action_case_id)
);
CREATE TABLE d10_business_actions (
    business_action_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    action_case_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    order_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    request_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    effect_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ACCEPTED',
    actor TEXT NOT NULL,
    source TEXT NOT NULL,
    reason TEXT,
    policy_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(action_case_id) REFERENCES action_cases(action_case_id),
    FOREIGN KEY(task_id) REFERENCES d9_action_case_tasks(task_id),
    FOREIGN KEY(order_id) REFERENCES orders(order_id),
    UNIQUE(organization_id, task_id),
    UNIQUE(organization_id, idempotency_key)
);
CREATE TABLE d10_outbox_events (
    event_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    business_action_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    dedupe_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    published_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(business_action_id) REFERENCES d10_business_actions(business_action_id),
    UNIQUE(organization_id, business_action_id),
    UNIQUE(organization_id, dedupe_key)
);
CREATE TABLE d10_idempotency_records (
    organization_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    business_action_id TEXT NOT NULL,
    result_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(organization_id, idempotency_key)
);
CREATE TABLE d10_audit_events (
    audit_id TEXT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    request_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    reason TEXT,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(entity_id) REFERENCES d10_business_actions(business_action_id)
);
"""


def _exec_script(conn, script: str) -> None:
    for stmt in script.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(text(stmt))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "d10.db"
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as conn:
        _exec_script(conn, SCHEMA_SQL)
    raw = engine.connect()
    wrapper = _ConnectionWrapper(raw, is_sqlite=True)
    yield {"wrapper": wrapper, "path": str(path)}
    try:
        raw.close()
    except Exception:
        pass
    engine.dispose()


def _reconnect(path: str):
    engine = create_engine(f"sqlite:///{path}")
    raw = engine.connect()
    return engine, raw, _ConnectionWrapper(raw, is_sqlite=True)


def _seed(conn, *, org="ORG-A", order_id="ORD-1", case_id="AC-1", task_id="TK-1", task_status="IN_PROGRESS", case_status="ACTIVE"):
    conn.execute(
        "INSERT INTO orders(order_id,order_no,status,owner,organization_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (order_id, order_id, "ACTIVE", "USER-1", org, NOW, NOW),
    )
    conn.execute(
        """INSERT INTO action_cases(action_case_id,organization_id,order_id,action_intent_key,intent_type,stage,
           lifecycle_status,observation_status,first_seen_at,last_seen_at,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (case_id, org, order_id, "v1:DELIVERY_RECOVERY", "DELIVERY_RECOVERY", "IN_PROGRESS", case_status,
         "OBSERVED", NOW, NOW, 1, NOW, NOW),
    )
    conn.execute(
        """INSERT INTO d9_action_case_tasks(task_id,organization_id,action_case_id,title,recommended_action,status,version,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (task_id, org, case_id, "更新预计交期", "更新预计交期到8月23日", task_status, 1, NOW, NOW),
    )
    conn.commit()


def _submission(**overrides):
    base = dict(
        organization_id="ORG-A",
        task_id="TK-1",
        action_type="UPDATE_EXPECTED_DELIVERY_DATE",
        target_type="ERP_SALES_ORDER",
        target_id="SO-001",
        payload={"expected_delivery_date": "2026-08-23"},
        idempotency_key="idem-001",
        actor="USER-1",
        request_id="REQ-001",
        source="ACTION_WORKSPACE",
        reason="供应商确认最早8月23日交货",
    )
    base.update(overrides)
    return d10.BusinessActionSubmission(**base)


def _count(conn, table):
    return conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]


def test_normal_submission_is_one_atomic_durable_acceptance(db):
    conn = db["wrapper"]
    _seed(conn)
    result = d10.submit_business_action(conn, _submission())

    assert result["status"] == "ACCEPTED"
    assert result["replayed"] is False
    assert result["external_effect_executed"] is False
    assert _count(conn, "d10_business_actions") == 1
    assert _count(conn, "d10_outbox_events") == 1
    assert _count(conn, "d10_idempotency_records") == 1
    assert _count(conn, "d10_audit_events") == 1

    action = d10.get_business_action_by_id(conn, result["business_action_id"])
    outbox = d10.get_outbox_for_action(conn, result["business_action_id"])
    assert action["status"] == "ACCEPTED"
    assert outbox["status"] == "PENDING"
    assert outbox["attempt_count"] == 0
    payload = json.loads(outbox["payload_json"])
    assert payload["action_type"] == "UPDATE_EXPECTED_DELIVERY_DATE"
    assert payload["payload"]["expected_delivery_date"] == "2026-08-23"


def test_submission_does_not_mutate_d8_or_d9_state(db):
    conn = db["wrapper"]
    _seed(conn)
    before_task = dict(conn.execute("SELECT status,version FROM d9_action_case_tasks WHERE task_id='TK-1'").fetchone())
    before_case = dict(conn.execute("SELECT stage,lifecycle_status,version FROM action_cases WHERE action_case_id='AC-1'").fetchone())

    d10.submit_business_action(conn, _submission())

    after_task = dict(conn.execute("SELECT status,version FROM d9_action_case_tasks WHERE task_id='TK-1'").fetchone())
    after_case = dict(conn.execute("SELECT stage,lifecycle_status,version FROM action_cases WHERE action_case_id='AC-1'").fetchone())
    assert after_task == before_task
    assert after_case == before_case


def test_same_idempotency_same_request_returns_original_ids_and_no_new_rows(db):
    conn = db["wrapper"]
    _seed(conn)
    first = d10.submit_business_action(conn, _submission())
    # request_id is transport metadata and may change on a retry.
    second = d10.submit_business_action(conn, _submission(request_id="REQ-RETRY-002"))

    assert second["replayed"] is True
    assert second["business_action_id"] == first["business_action_id"]
    assert second["outbox_event_id"] == first["outbox_event_id"]
    for table in ("d10_business_actions", "d10_outbox_events", "d10_idempotency_records", "d10_audit_events"):
        assert _count(conn, table) == 1


def test_canonical_payload_key_order_is_idempotent(db):
    conn = db["wrapper"]
    _seed(conn)
    payload1 = {"expected_delivery_date": "2026-08-23", "note": "supplier confirmed"}
    payload2 = {"note": "supplier confirmed", "expected_delivery_date": "2026-08-23"}
    first = d10.submit_business_action(conn, _submission(payload=payload1))
    second = d10.submit_business_action(conn, _submission(payload=payload2, request_id="REQ-2"))
    assert second["business_action_id"] == first["business_action_id"]
    assert second["replayed"] is True


def test_same_idempotency_key_with_changed_effect_is_hard_conflict(db):
    conn = db["wrapper"]
    _seed(conn)
    first = d10.submit_business_action(conn, _submission())
    with pytest.raises(d10.D10IdempotencyConflict):
        d10.submit_business_action(
            conn,
            _submission(payload={"expected_delivery_date": "2026-08-24"}, request_id="REQ-2"),
        )
    assert _count(conn, "d10_business_actions") == 1
    assert d10.get_business_action_by_id(conn, first["business_action_id"])["payload_json"] == '{"expected_delivery_date":"2026-08-23"}'


@pytest.mark.parametrize("fail_stage", [
    "after_idempotency_reservation",
    "after_action_insert",
    "after_outbox_insert",
    "after_audit_insert",
])
def test_failure_anywhere_rolls_back_every_d10_record(db, fail_stage):
    conn = db["wrapper"]
    _seed(conn)

    def injector(stage):
        if stage == fail_stage:
            raise RuntimeError(f"forced failure at {stage}")

    with pytest.raises(d10.D10SubmissionError) as exc:
        d10.submit_business_action(conn, _submission(), failure_injector=injector)
    assert exc.value.stage in {
        "reserve_idempotency", "insert_business_action", "insert_outbox", "insert_audit"
    }
    for table in ("d10_business_actions", "d10_outbox_events", "d10_idempotency_records", "d10_audit_events"):
        assert _count(conn, table) == 0

    # D8/D9 seed state was committed before the D10 UoW and remains intact.
    assert _count(conn, "action_cases") == 1
    assert _count(conn, "d9_action_case_tasks") == 1


def test_restart_then_retry_is_still_idempotent(db):
    conn = db["wrapper"]
    _seed(conn)
    first = d10.submit_business_action(conn, _submission())
    conn.close()

    engine2, raw2, conn2 = _reconnect(db["path"])
    try:
        second = d10.submit_business_action(conn2, _submission(request_id="REQ-AFTER-RESTART"))
        assert second["replayed"] is True
        assert second["business_action_id"] == first["business_action_id"]
        assert _count(conn2, "d10_business_actions") == 1
        assert _count(conn2, "d10_outbox_events") == 1
    finally:
        raw2.close()
        engine2.dispose()


def test_second_distinct_business_action_on_same_task_is_rejected(db):
    conn = db["wrapper"]
    _seed(conn)
    d10.submit_business_action(conn, _submission())
    with pytest.raises(d10.D10TaskActionConflict):
        d10.submit_business_action(
            conn,
            _submission(
                idempotency_key="idem-002",
                request_id="REQ-002",
                action_type="SEND_CUSTOMER_DELIVERY_UPDATE",
                target_type="CUSTOMER_CONTACT",
                target_id="CUSTOMER-1",
                payload={"message": "交期调整到8月23日"},
            ),
        )
    assert _count(conn, "d10_business_actions") == 1


@pytest.mark.parametrize("task_status", ["WAITING", "DONE", "CANCELLED"])
def test_non_actionable_task_status_rejected(db, task_status):
    conn = db["wrapper"]
    _seed(conn, task_status=task_status)
    with pytest.raises(d10.D10StateError):
        d10.submit_business_action(conn, _submission())
    assert _count(conn, "d10_business_actions") == 0


def test_closed_action_case_rejected(db):
    conn = db["wrapper"]
    _seed(conn, case_status="CLOSED")
    with pytest.raises(d10.D10StateError):
        d10.submit_business_action(conn, _submission())
    assert _count(conn, "d10_business_actions") == 0


def test_cross_org_submission_rejected(db):
    conn = db["wrapper"]
    _seed(conn, org="ORG-A")
    with pytest.raises(d10.D10StateError):
        d10.submit_business_action(conn, _submission(organization_id="ORG-B"))
    assert _count(conn, "d10_business_actions") == 0


def test_plan_reads_case_and_order_from_db_not_from_payload(db):
    conn = db["wrapper"]
    _seed(conn, order_id="ORD-REAL", case_id="AC-REAL", task_id="TK-REAL")
    plan = d10.build_business_action_plan(conn, _submission(task_id="TK-REAL"))
    assert plan.action_case_id == "AC-REAL"
    assert plan.order_id == "ORD-REAL"
    assert plan.policy_version == "D10_BUSINESS_ACTION_V1"


def test_payload_must_be_json_object_and_serializable(db):
    conn = db["wrapper"]
    _seed(conn)
    with pytest.raises(d10.D10StateError):
        d10.build_business_action_plan(conn, _submission(payload=["not", "object"]))
    with pytest.raises(d10.D10StateError):
        d10.build_business_action_plan(conn, _submission(payload={"bad": {1, 2, 3}}))


def test_concurrent_duplicate_submissions_converge_to_one_action(db):
    """Attack the DB uniqueness boundary with separate connections.

    This is intentionally a file-backed SQLite test because each worker owns a
    separate connection, approximating concurrent HTTP requests. PostgreSQL
    uses the same unique constraints and transaction contract.
    """
    from concurrent.futures import ThreadPoolExecutor

    conn = db["wrapper"]
    _seed(conn)
    conn.close()

    def submit_from_connection(i: int):
        engine = create_engine(f"sqlite:///{db['path']}", connect_args={"timeout": 30})
        raw = engine.connect()
        wrapper = _ConnectionWrapper(raw, is_sqlite=True)
        try:
            return d10.submit_business_action(
                wrapper, _submission(request_id=f"REQ-CONCURRENT-{i}")
            )
        finally:
            raw.close()
            engine.dispose()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit_from_connection, range(20)))

    ids = {r["business_action_id"] for r in results}
    assert len(ids) == 1
    assert sum(1 for r in results if r["replayed"] is False) == 1

    engine2, raw2, conn2 = _reconnect(db["path"])
    try:
        for table in ("d10_business_actions", "d10_outbox_events", "d10_idempotency_records", "d10_audit_events"):
            assert _count(conn2, table) == 1
    finally:
        raw2.close()
        engine2.dispose()


def test_d10_defense_in_depth_rejects_invalid_delivery_date_before_outbox(db):
    conn = db["wrapper"]
    _seed(conn)
    with pytest.raises(d10.D10StateError):
        d10.submit_business_action(
            conn,
            _submission(
                payload={"expected_delivery_date": "2026-13-99"},
                idempotency_key="bad-date-idem",
                request_id="REQ-BAD-DATE",
            ),
        )
    assert _count(conn, "d10_business_actions") == 0
    assert _count(conn, "d10_outbox_events") == 0
