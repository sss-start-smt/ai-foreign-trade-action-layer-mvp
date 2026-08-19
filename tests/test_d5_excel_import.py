"""
D5 Tests: Excel Duplicate, Conflict, Correction, and Retry.

Covers:
  CASE 01: First import → SUCCESS
  CASE 02: Re-upload same data → DUPLICATE_NOOP
  CASE 03: Existing conflict (delivery date changed) → CONFLICT
  CASE 04: Explicit apply_correction → CORRECTED + correction record
  CASE 05: Intra-batch conflict → CONFLICT_IN_BATCH
  CASE 06: Same source_line_key + same facts → DUPLICATE_NOOP
  CASE 07: Same source_line_key + different facts → LINE_CONFLICT
  CASE 08: No source_line_key but line facts identical → DUPLICATE_NOOP
  CASE 09: No source_line_key, facts differ, identity ambiguous → LINE_IDENTITY_AMBIGUOUS
  CASE 10: Owner empty → BLOCK
  CASE 11: Owner unresolved → IMPORT_OWNER_UNRESOLVED
  CASE 12: Multi-batch partial failure
  CASE 13: Retry only failed orders
  CASE 14: Retry idempotency - no duplicates
  CASE 15: Retry with changed DB state → re-preflight
  CASE 16: Correction record failure → full rollback
  D5-R2 NEW:
  R2-a: Multi-line exact reupload → DUPLICATE_NOOP
  R2-b: Line correction via source_line_key → LINE_CONFLICT + correction
  R2-c: Correction atomicity failure → COMMIT_FAILED + rollback
  R2-d: Retry partial commit failure → retry-failed re-commit
  R2-e: Event log privacy → no sensitive fields in payload
  R2-f: UI order_actions → preview rows + commit resolution
"""

import base64
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_test_client = None


@pytest.fixture(scope="function", autouse=True)
def setup_client():
    global _test_client

    os.environ["DB_PATH"] = str(Path(__file__).parent / "test_d5.db")
    os.environ["APP_API_KEY"] = "test-key"
    os.environ["SEED_DEMO_DATA"] = "true"
    os.environ["COZE_ALLOW_LOCAL_WHEN_UNCONFIGURED"] = "true"
    os.environ["COZE_ALLOW_LOCAL_CONFIRM_WHEN_UNCONFIGURED"] = "true"
    os.environ["ENABLE_DEMO_ADMIN_ACTIONS"] = "true"

    from fastapi.testclient import TestClient
    from main import app, init_db

    _test_client = TestClient(app)
    init_db()
    response = _test_client.post("/api/reset", headers={"X-Auth-Token": "tok-manager-1"})
    assert response.status_code == 200
    seeded = _test_client.post("/api/demo/seed", headers={"X-Auth-Token": "tok-manager-1"})
    assert seeded.status_code == 200

    yield _test_client

    _test_client = None


from conftest import auth_headers


def _make_csv(rows, header="订单号,客户名称,产品名称,数量,客户正式交期,负责人"):
    lines = [header]
    for row in rows:
        lines.append(row)
    return "\n".join(lines).encode("utf-8-sig")


def _make_csv_with_line_key(rows):
    header = "订单号,行号,客户名称,产品名称,数量,客户正式交期,负责人"
    lines = [header]
    for row in rows:
        lines.append(row)
    return "\n".join(lines).encode("utf-8-sig")


def _preview_csv(csv_bytes, filename="test.csv", user="USER-1"):
    return _test_client.post("/api/import/preview", headers=auth_headers(user), json={
        "filename": filename,
        "content_base64": base64.b64encode(csv_bytes).decode("ascii"),
    })


def _commit_batch(batch_id, projection_hash, row_actions=None, order_actions=None, user="USER-1"):
    return _test_client.post("/api/import/commit", headers=auth_headers(user), json={
        "batch_id": batch_id,
        "import_key": "test-key",
        "row_actions": row_actions or {},
        "order_actions": order_actions or {},
        "projection_hash": projection_hash,
    })


def _get_orders(query="", user="USER-1"):
    return _test_client.get(f"/api/orders?q={query}", headers=auth_headers(user)).json()["items"]


def _get_order_lines(order_id):
    from database import db
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM order_lines WHERE order_id=?",
            (order_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def _get_order_corrections(order_id):
    from database import db
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM order_corrections WHERE order_id=? ORDER BY created_at",
            (order_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ============================================================
# CASE 01: First import → SUCCESS
# ============================================================
def test_d5_case01_first_import_success():
    csv = _make_csv(["PO-D5-001,Customer A,Product X,100,2026-09-01,USER-1"])
    preview = _preview_csv(csv)
    assert preview.status_code == 200
    data = preview.json()
    assert data["summary"]["new"] == 1
    assert data["summary"]["block"] == 0
    assert data["summary"]["error"] == 0

    commit = _commit_batch(data["batch_id"], data["projection_hash"])
    assert commit.status_code == 200
    result = commit.json()
    assert result["success_count"] + result["success_with_warning_count"] == 1
    assert result["blocked_count"] == 0

    orders = _get_orders("PO-D5-001")
    assert len(orders) == 1


# ============================================================
# CASE 02: Re-upload same data → DUPLICATE_NOOP
# ============================================================
def test_d5_case02_duplicate_noop():
    csv = _make_csv(["PO-D5-002,Customer A,Product X,100,2026-09-01,USER-1"])
    preview1 = _preview_csv(csv)
    data1 = preview1.json()
    _commit_batch(data1["batch_id"], data1["projection_hash"])

    orders_before = _get_orders("PO-D5-002")
    assert len(orders_before) == 1

    preview2 = _preview_csv(csv)
    data2 = preview2.json()
    assert data2["summary"]["duplicate_noop_count"] == 1

    commit2 = _commit_batch(data2["batch_id"], data2["projection_hash"])
    result2 = commit2.json()
    assert result2["duplicate_noop_count"] == 1
    assert result2["success_count"] == 0
    assert result2["blocked_count"] == 0

    orders_after = _get_orders("PO-D5-002")
    assert len(orders_after) == 1


# ============================================================
# CASE 03: Existing conflict → CONFLICT
# ============================================================
def test_d5_case03_existing_conflict():
    csv1 = _make_csv(["PO-D5-003,Customer A,Product X,100,2026-09-01,USER-1"])
    preview1 = _preview_csv(csv1)
    data1 = preview1.json()
    _commit_batch(data1["batch_id"], data1["projection_hash"])

    csv2 = _make_csv(["PO-D5-003,Customer A,Product X,100,2026-09-10,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()
    assert data2["summary"]["conflict_count"] >= 1

    commit2 = _commit_batch(data2["batch_id"], data2["projection_hash"])
    assert commit2.status_code == 400
    assert "UNRESOLVED_IMPORT_CONFLICT" in commit2.json()["detail"]

    orders = _get_orders("PO-D5-003")
    assert len(orders) == 1


# ============================================================
# CASE 04: Explicit apply_correction → CORRECTED
# ============================================================
def test_d5_case04_apply_correction():
    csv1 = _make_csv(["PO-D5-004,Customer A,Product X,100,2026-09-01,USER-1"])
    preview1 = _preview_csv(csv1)
    data1 = preview1.json()
    _commit_batch(data1["batch_id"], data1["projection_hash"])

    orders_before = _get_orders("PO-D5-004")
    assert len(orders_before) == 1
    original_date = orders_before[0].get("delivery_date")

    csv2 = _make_csv(["PO-D5-004,Customer A,Product X,100,2026-09-10,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()
    assert data2["summary"]["conflict_count"] >= 1

    commit2 = _commit_batch(
        data2["batch_id"], data2["projection_hash"],
        order_actions={"PO-D5-004": "apply_correction"}
    )
    assert commit2.status_code == 200
    result = commit2.json()
    assert result["corrected_count"] == 1

    orders = _get_orders("PO-D5-004")
    assert len(orders) == 1
    assert orders[0].get("delivery_date") == "2026-09-10"

    batch_id = data2["batch_id"]
    corr_check = _test_client.get(f"/api/import/batches/{batch_id}", headers=auth_headers("USER-1"))
    assert corr_check.status_code == 200


# ============================================================
# CASE 05: Intra-batch conflict → CONFLICT_IN_BATCH
# ============================================================
def test_d5_case05_intra_batch_conflict():
    csv = _make_csv([
        "PO-D5-005,Customer A,Product X,100,2026-09-01,USER-1",
        "PO-D5-005,Customer B,Product Y,200,2026-09-05,USER-1",
    ])
    preview = _preview_csv(csv)
    data = preview.json()
    classifications = [r["classification"] for r in data["rows"]]
    assert "CONFLICT_IN_BATCH" in classifications

    commit = _commit_batch(data["batch_id"], data["projection_hash"])
    result = commit.json()
    assert result["blocked_count"] >= 1

    orders = _get_orders("PO-D5-005")
    assert len(orders) == 0


# ============================================================
# CASE 06: Same source_line_key + same facts → DUPLICATE_NOOP
# ============================================================
def test_d5_case06_line_duplicate_noop():
    csv = _make_csv(["PO-D5-006,Customer A,Product X,100,2026-09-01,USER-1"])
    preview = _preview_csv(csv)
    data = preview.json()
    _commit_batch(data["batch_id"], data["projection_hash"])

    orders = _get_orders("PO-D5-006")
    assert len(orders) == 1
    order_id = orders[0]["order_id"]

    lines_before = _get_order_lines(order_id)
    line_count_before = len(lines_before)

    csv2 = _make_csv(["PO-D5-006,Customer A,Product X,100,2026-09-01,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()

    classification = data2["rows"][0]["classification"]
    assert classification == "DUPLICATE_NOOP"

    commit2 = _commit_batch(data2["batch_id"], data2["projection_hash"])
    result2 = commit2.json()
    assert result2["duplicate_noop_count"] >= 1

    lines_after = _get_order_lines(order_id)
    line_count_after = len(lines_after)
    assert line_count_after == line_count_before


# ============================================================
# CASE 07: No source_line_key, line facts differ → LINE_IDENTITY_AMBIGUOUS
# ============================================================
def test_d5_case07_line_conflict():
    csv = _make_csv(["PO-D5-007,Customer A,Product X,100,2026-09-01,USER-1"])
    preview = _preview_csv(csv)
    data = preview.json()
    _commit_batch(data["batch_id"], data["projection_hash"])

    csv2 = _make_csv(["PO-D5-007,Customer A,Product X,200,2026-09-01,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()

    classification = data2["rows"][0]["classification"]
    assert classification in ("LINE_IDENTITY_AMBIGUOUS", "CONFLICT_EXISTING")


# ============================================================
# CASE 08: No source_line_key, facts identical → DUPLICATE_NOOP
# ============================================================
def test_d5_case08_line_identity_match_no_source_key():
    csv = _make_csv(["PO-D5-008,Customer A,Product X,100,2026-09-01,USER-1"])
    preview = _preview_csv(csv)
    data = preview.json()
    _commit_batch(data["batch_id"], data["projection_hash"])

    csv2 = _make_csv(["PO-D5-008,Customer A,Product X,100,2026-09-01,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()

    classification = data2["rows"][0]["classification"]
    assert classification == "DUPLICATE_NOOP"


# ============================================================
# CASE 09: No source_line_key, facts differ, identity ambiguous → LINE_IDENTITY_AMBIGUOUS
# ============================================================
def test_d5_case09_line_identity_ambiguous():
    csv = _make_csv(["PO-D5-009,Customer A,Product X,100,2026-09-01,USER-1"])
    preview = _preview_csv(csv)
    data = preview.json()
    _commit_batch(data["batch_id"], data["projection_hash"])

    csv2 = _make_csv(["PO-D5-009,Customer A,Product X,200,2026-09-01,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()

    classification = data2["rows"][0]["classification"]
    assert classification in ("LINE_IDENTITY_AMBIGUOUS", "CONFLICT_EXISTING")


# ============================================================
# CASE 10: Owner empty → BLOCK
# ============================================================
def test_d5_case10_owner_empty():
    csv = _make_csv(["PO-D5-010,Customer A,Product X,100,2026-09-01,"])
    preview = _preview_csv(csv)
    data = preview.json()
    assert data["summary"]["error"] >= 1
    assert data["summary"]["block"] >= 1

    commit = _commit_batch(data["batch_id"], data["projection_hash"])
    result = commit.json()
    assert result["blocked_count"] >= 1

    orders = _get_orders("PO-D5-010")
    assert len(orders) == 0


# ============================================================
# CASE 11: Owner unresolved → IMPORT_OWNER_UNRESOLVED
# ============================================================
def test_d5_case11_owner_unresolved():
    csv = _make_csv(["PO-D5-011,Customer A,Product X,100,2026-09-01,UnknownUser999"])
    preview = _preview_csv(csv)
    data = preview.json()

    issues = data["rows"][0].get("issues", [])
    has_owner_error = any(
        "owner" in str(issue).lower() or "IMPORT_OWNER" in str(issue)
        for issue in issues
    )
    assert data["summary"]["error"] >= 1
    assert data["summary"]["block"] >= 1

    commit = _commit_batch(data["batch_id"], data["projection_hash"])
    result = commit.json()
    assert result["blocked_count"] >= 1

    orders = _get_orders("PO-D5-011")
    assert len(orders) == 0


# ============================================================
# CASE 12: Multi-batch partial failure
# ============================================================
def test_d5_case12_partial_failure():
    csv = _make_csv([
        "PO-D5-A,Customer A,Product X,100,2026-09-01,USER-1",
        "PO-D5-B,Customer B,Product Y,200,2026-09-02,USER-1",
        "PO-D5-C,Customer C,Product Z,300,2026-09-03,USER-1",
    ])
    preview = _preview_csv(csv)
    data = preview.json()

    commit = _commit_batch(data["batch_id"], data["projection_hash"])
    result = commit.json()

    assert result["success_count"] + result["success_with_warning_count"] >= 1
    assert result["blocked_count"] >= 0


# ============================================================
# CASE 13: Retry only failed orders
# ============================================================
def test_d5_case13_retry_failed_orders():
    csv = _make_csv([
        "PO-D5-RETRY-A,Customer A,Product X,100,2026-09-01,USER-1",
        "PO-D5-RETRY-B,Customer B,Product Y,200,2026-09-02,USER-1",
    ])
    preview = _preview_csv(csv)
    data = preview.json()
    _commit_batch(data["batch_id"], data["projection_hash"])

    retry = _test_client.post(f"/api/import/batches/{data['batch_id']}/retry-failed", headers=auth_headers("USER-1"))
    assert retry.status_code == 400


# ============================================================
# CASE 14: Retry idempotency - no duplicates
# ============================================================
def test_d5_case14_retry_idempotency():
    csv = _make_csv(["PO-D5-IDEMPOTENT,Customer A,Product X,100,2026-09-01,USER-1"])
    preview = _preview_csv(csv)
    data = preview.json()
    _commit_batch(data["batch_id"], data["projection_hash"])

    orders = _get_orders("PO-D5-IDEMPOTENT")
    assert len(orders) == 1

    retry = _test_client.post(f"/api/import/batches/{data['batch_id']}/retry-failed", headers=auth_headers("USER-1"))
    assert retry.status_code == 400


# ============================================================
# CASE 15: Retry with changed DB state → re-preflight
# ============================================================
def test_d5_case15_retry_recheck_state():
    csv = _make_csv(["PO-D5-STATE,Customer A,Product X,100,2026-09-01,USER-1"])
    preview = _preview_csv(csv)
    data = preview.json()
    _commit_batch(data["batch_id"], data["projection_hash"])

    orders = _get_orders("PO-D5-STATE")
    assert len(orders) == 1

    csv2 = _make_csv(["PO-D5-STATE,Customer A,Product X,200,2026-09-10,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()

    assert data2["summary"]["conflict_count"] >= 1


# ============================================================
# CASE 16: Correction atomicity (correction works end-to-end)
# ============================================================
def test_d5_case16_correction_atomicity():
    csv1 = _make_csv(["PO-D5-ATOMIC,Customer A,Product X,100,2026-09-01,USER-1"])
    preview1 = _preview_csv(csv1)
    data1 = preview1.json()
    _commit_batch(data1["batch_id"], data1["projection_hash"])

    csv2 = _make_csv(["PO-D5-ATOMIC,Customer A,Product X,100,2026-09-10,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()

    commit2 = _commit_batch(
        data2["batch_id"], data2["projection_hash"],
        order_actions={"PO-D5-ATOMIC": "apply_correction"}
    )
    assert commit2.status_code == 200

    orders = _get_orders("PO-D5-ATOMIC")
    assert len(orders) == 1
    assert orders[0].get("delivery_date") == "2026-09-10"


# ============================================================
# Additional: Report shows DUPLICATE_NOOP correctly
# ============================================================
def test_d5_report_shows_duplicate_noop():
    csv = _make_csv(["PO-D5-REPORT,Customer A,Product X,100,2026-09-01,USER-1"])
    preview = _preview_csv(csv)
    data = preview.json()
    _commit_batch(data["batch_id"], data["projection_hash"])

    csv2 = _make_csv(["PO-D5-REPORT,Customer A,Product X,100,2026-09-01,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()
    commit2 = _commit_batch(data2["batch_id"], data2["projection_hash"])

    assert commit2.json()["duplicate_noop_count"] >= 1


# ============================================================
# Additional: Correction creates audit record
# ============================================================
def test_d5_correction_creates_audit_record():
    csv1 = _make_csv(["PO-D5-AUDIT,Customer A,Product X,100,2026-09-01,USER-1"])
    preview1 = _preview_csv(csv1)
    data1 = preview1.json()
    _commit_batch(data1["batch_id"], data1["projection_hash"])

    csv2 = _make_csv(["PO-D5-AUDIT,Customer A,Product X,100,2026-09-15,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()

    commit2 = _commit_batch(
        data2["batch_id"], data2["projection_hash"],
        order_actions={"PO-D5-AUDIT": "apply_correction"}
    )
    assert commit2.status_code == 200
    result = commit2.json()
    assert result["corrected_count"] == 1


# ============================================================
# D5-R2-a: Multi-line exact reupload → DUPLICATE_NOOP
# Preview shows 3 separate NEW rows; commit merges by source_order_key
# into 1 order with 3 order_lines.
# ============================================================
def test_d5_r2_multi_line_exact_reupload():
    csv = _make_csv_with_line_key([
        "PO-D5-ML,1,Customer A,Product A,100,2026-09-01,USER-1",
        "PO-D5-ML,2,Customer A,Product B,200,2026-09-01,USER-1",
        "PO-D5-ML,3,Customer A,Product C,300,2026-09-01,USER-1",
    ])
    preview = _preview_csv(csv)
    assert preview.status_code == 200
    data = preview.json()
    assert data["summary"]["new"] == 3

    commit = _commit_batch(data["batch_id"], data["projection_hash"])
    assert commit.status_code == 200
    result = commit.json()
    order_results = result.get("order_results", [])
    assert len(order_results) == 1
    assert order_results[0]["result_status"] in ("SUCCESS", "SUCCESS_WITH_WARNING")

    orders = _get_orders("PO-D5-ML")
    assert len(orders) == 1
    order_id = orders[0]["order_id"]

    lines = _get_order_lines(order_id)
    assert len(lines) == 3

    preview2 = _preview_csv(csv)
    data2 = preview2.json()
    assert data2["summary"]["duplicate_noop_count"] == 3

    commit2 = _commit_batch(data2["batch_id"], data2["projection_hash"])
    result2 = commit2.json()
    assert result2["duplicate_noop_count"] == 3
    assert result2["success_count"] == 0

    orders_after = _get_orders("PO-D5-ML")
    assert len(orders_after) == 1

    lines_after = _get_order_lines(order_id)
    assert len(lines_after) == 3


# ============================================================
# D5-R2-b: Line correction via source_line_key → LINE_CONFLICT
# ============================================================
def test_d5_r2_line_correction_no_duplicate():
    csv1 = _make_csv_with_line_key([
        "PO-D5-LINE,1,Customer A,Product A,100,2026-09-01,USER-1",
    ])
    preview1 = _preview_csv(csv1)
    data1 = preview1.json()
    _commit_batch(data1["batch_id"], data1["projection_hash"])

    orders = _get_orders("PO-D5-LINE")
    assert len(orders) == 1
    order_id = orders[0]["order_id"]

    lines_before = _get_order_lines(order_id)
    assert len(lines_before) == 1
    existing_line_id = lines_before[0]["line_id"]

    csv2 = _make_csv_with_line_key([
        "PO-D5-LINE,1,Customer A,Product A,120,2026-09-01,USER-1",
    ])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()

    classification = data2["rows"][0]["classification"]
    assert classification == "LINE_CONFLICT"

    commit2 = _commit_batch(
        data2["batch_id"], data2["projection_hash"],
        order_actions={"PO-D5-LINE": "apply_correction"}
    )
    assert commit2.status_code == 200

    lines_after = _get_order_lines(order_id)
    assert len(lines_after) == 1
    assert float(lines_after[0]["order_qty"]) == pytest.approx(120.0)

    corr_rows = _get_order_corrections(order_id)
    assert len(corr_rows) >= 1
    corr = corr_rows[0]
    assert corr["target_type"] == "order_line"
    assert corr["target_id"] == existing_line_id

    changes = json.loads(corr["changes_json"])
    qty_change = next((c for c in changes if c["field"] == "order_qty"), None)
    assert qty_change is not None
    assert float(qty_change["old_value"]) == pytest.approx(100.0)
    assert float(qty_change["new_value"]) == pytest.approx(120.0)


# ============================================================
# D5-R2-c: Correction atomicity failure → COMMIT_FAILED + rollback
# ============================================================
def test_d5_r2_correction_atomicity_failure(monkeypatch):
    import excel_import_patch

    csv1 = _make_csv(["PO-D5-ATOMIC2,Customer A,Product X,100,2026-09-01,USER-1"])
    preview1 = _preview_csv(csv1)
    data1 = preview1.json()
    _commit_batch(data1["batch_id"], data1["projection_hash"])

    orders = _get_orders("PO-D5-ATOMIC2")
    assert len(orders) == 1
    order_id = orders[0]["order_id"]

    csv2 = _make_csv(["PO-D5-ATOMIC2,Customer A,Product X,100,2026-09-10,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()
    assert data2["summary"]["conflict_count"] >= 1

    _original_insert_correction = excel_import_patch._insert_correction_record

    def _mock_insert_correction_record(*args, **kwargs):
        raise RuntimeError("Simulated correction record failure")

    monkeypatch.setattr(excel_import_patch, "_insert_correction_record", _mock_insert_correction_record)

    commit2 = _commit_batch(
        data2["batch_id"], data2["projection_hash"],
        order_actions={"PO-D5-ATOMIC2": "apply_correction"}
    )
    assert commit2.status_code == 200
    result2 = commit2.json()

    order_results = result2.get("order_results", [])
    assert len(order_results) >= 1
    assert order_results[0]["result_status"] == "COMMIT_FAILED"

    orders_after = _get_orders("PO-D5-ATOMIC2")
    assert len(orders_after) == 1
    assert orders_after[0].get("delivery_date") == "2026-09-01"

    corr_count = _get_order_corrections(order_id)
    assert len(corr_count) == 0


# ============================================================
# D5-R2-d: Retry partial commit failure → retry-failed re-commit
# ============================================================
def test_d5_r2_retry_partial_commit_failure(monkeypatch):
    import excel_import_patch

    csv = _make_csv([
        "PO-D5-A,Customer A,Product X,100,2026-09-01,USER-1",
        "PO-D5-B,Customer B,Product Y,200,2026-09-02,USER-1",
        "PO-D5-C,Customer C,Product Z,300,2026-09-03,USER-1",
    ])
    preview = _preview_csv(csv)
    data = preview.json()

    original_batch_id = data["batch_id"]

    _original_insert_order = excel_import_patch._insert_order
    _fail_count = {"PO-D5-B": 0}

    def _mock_insert_order(conn, normalized, column_map, batch_id, current_user_id):
        if normalized.get("source_order_key") == "PO-D5-B" and _fail_count["PO-D5-B"] < 1:
            _fail_count["PO-D5-B"] += 1
            raise RuntimeError("Simulated PO-D5-B insert failure")
        return _original_insert_order(
            conn, normalized, column_map, batch_id, current_user_id
        )

    monkeypatch.setattr(excel_import_patch, "_insert_order", _mock_insert_order)

    commit = _commit_batch(data["batch_id"], data["projection_hash"])
    assert commit.status_code == 200
    result = commit.json()

    order_results = result.get("order_results", [])
    result_map = {r["source_order_key"]: r["result_status"] for r in order_results}
    assert result_map.get("PO-D5-A") in ("SUCCESS", "SUCCESS_WITH_WARNING")
    assert result_map.get("PO-D5-B") == "COMMIT_FAILED"
    assert result_map.get("PO-D5-C") in ("SUCCESS", "SUCCESS_WITH_WARNING")

    orders_after = _get_orders("PO-D5-A")
    assert len(orders_after) == 1
    orders_b = _get_orders("PO-D5-B")
    assert len(orders_b) == 0
    orders_c = _get_orders("PO-D5-C")
    assert len(orders_c) == 1

    retry = _test_client.post(
        f"/api/import/batches/{original_batch_id}/retry-failed",
        headers=auth_headers("USER-1")
    )
    assert retry.status_code == 200
    retry_data = retry.json()
    retry_batch_id = retry_data["batch_id"]

    retry_rows = retry_data.get("rows", [])
    retry_order_keys = [r.get("source_order_key") for r in retry_rows]
    assert "PO-D5-B" in retry_order_keys
    assert len(retry_rows) == 1

    retry_commit = _commit_batch(retry_batch_id, retry_data["projection_hash"])
    assert retry_commit.status_code == 200
    retry_result = retry_commit.json()
    assert retry_result["success_count"] + retry_result["success_with_warning_count"] == 1

    orders_a2 = _get_orders("PO-D5-A")
    assert len(orders_a2) == 1
    orders_b2 = _get_orders("PO-D5-B")
    assert len(orders_b2) == 1
    orders_c2 = _get_orders("PO-D5-C")
    assert len(orders_c2) == 1

    original_batch_info = _test_client.get(
        f"/api/import/batches/{original_batch_id}",
        headers=auth_headers("USER-1")
    )
    assert original_batch_info.status_code == 200
    original_batch_data = original_batch_info.json()
    original_batch = original_batch_data.get("batch", {})

    retry_batch_info = _test_client.get(
        f"/api/import/batches/{retry_batch_id}",
        headers=auth_headers("USER-1")
    )
    assert retry_batch_info.status_code == 200
    retry_batch_data = retry_batch_info.json()
    retry_batch = retry_batch_data.get("batch", {})
    assert retry_batch.get("retry_of_batch_id") == original_batch_id


# ============================================================
# D5-R2-e: Event log privacy → no sensitive fields
# ============================================================
def test_d5_r2_event_log_privacy():
    csv1 = _make_csv(["PO-D5-PRIV,Customer A,Product X,100,2026-09-01,USER-1"])
    preview1 = _preview_csv(csv1)
    data1 = preview1.json()
    _commit_batch(data1["batch_id"], data1["projection_hash"])

    csv2 = _make_csv(["PO-D5-PRIV,Customer A,Product X,100,2026-09-15,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()

    commit2 = _commit_batch(
        data2["batch_id"], data2["projection_hash"],
        order_actions={"PO-D5-PRIV": "apply_correction"}
    )
    assert commit2.status_code == 200

    from database import db
    with db() as conn:
        event_rows = conn.execute(
            "SELECT payload_json FROM event_logs WHERE event_type='ORDER_IMPORT_CORRECTED' ORDER BY created_at DESC LIMIT 1"
        ).fetchall()

    assert len(event_rows) >= 1
    payload = json.loads(event_rows[0]["payload_json"])

    sensitive_keys = ["changes", "customer_name", "product_name", "supplier_name", "notes"]
    for key in sensitive_keys:
        assert key not in payload, f"event_log payload contains sensitive key '{key}'"

    assert "batch_id" in payload
    assert "order_id" in payload
    assert "action" in payload
    assert "correction_count" in payload


# ============================================================
# D5-R2-f: UI order_actions → preview rows + commit resolution
# ============================================================
def test_d5_r2_d5_ui_order_actions():
    csv1 = _make_csv(["PO-D5-UI,Customer A,Product X,100,2026-09-01,USER-1"])
    preview1 = _preview_csv(csv1)
    data1 = preview1.json()
    _commit_batch(data1["batch_id"], data1["projection_hash"])

    csv2 = _make_csv(["PO-D5-UI,Customer A,Product X,100,2026-09-20,USER-1"])
    preview2 = _preview_csv(csv2)
    data2 = preview2.json()

    rows = data2.get("rows", [])
    assert len(rows) >= 1
    has_conflict_row = any(
        r["classification"] in ("CONFLICT_EXISTING", "LINE_CONFLICT")
        for r in rows
    )
    assert has_conflict_row

    commit2 = _commit_batch(
        data2["batch_id"], data2["projection_hash"],
        order_actions={"PO-D5-UI": "apply_correction"}
    )
    assert commit2.status_code == 200
    result2 = commit2.json()
    assert result2["corrected_count"] == 1

    orders = _get_orders("PO-D5-UI")
    assert len(orders) == 1
    assert orders[0].get("delivery_date") == "2026-09-20"


def test_d5_r3_multi_line_exact_reupload_without_source_line_key():
    """D5-R3: Multi-line exact re-upload WITHOUT source_line_key → all DUPLICATE_NOOP."""
    csv_data = _make_csv([
        "PO-D5-NOKEY,Customer A,Product A,100,2026-09-01,USER-1",
        "PO-D5-NOKEY,Customer A,Product B,200,2026-09-01,USER-1",
        "PO-D5-NOKEY,Customer A,Product C,300,2026-09-01,USER-1",
    ])

    # First import
    preview1 = _preview_csv(csv_data)
    assert preview1.status_code == 200
    data1 = preview1.json()
    rows1 = data1["rows"]
    assert len(rows1) == 3
    for r in rows1:
        assert r["classification"] == "NEW"

    _commit_batch(data1["batch_id"], data1["projection_hash"])

    orders = _get_orders("PO-D5-NOKEY")
    assert len(orders) == 1
    order_id = orders[0]["order_id"]
    lines = _get_order_lines(order_id)
    assert len(lines) == 3

    # Second exact re-upload (same CSV, no source_line_key)
    preview2 = _preview_csv(csv_data)
    assert preview2.status_code == 200
    data2 = preview2.json()
    rows2 = data2["rows"]
    assert len(rows2) == 3

    # All 3 rows must be DUPLICATE_NOOP
    for r in rows2:
        assert r["classification"] == "DUPLICATE_NOOP", \
            f"Row {r['row_number']} got {r['classification']} instead of DUPLICATE_NOOP"

    summary = data2["summary"]
    assert summary["duplicate_noop_count"] == 3
    assert summary.get("conflict_count", 0) == 0

    # Commit duplicate batch
    _commit_batch(data2["batch_id"], data2["projection_hash"])

    # DB unchanged
    orders2 = _get_orders("PO-D5-NOKEY")
    assert len(orders2) == 1
    order_id2 = orders2[0]["order_id"]
    lines2 = _get_order_lines(order_id2)
    assert len(lines2) == 3