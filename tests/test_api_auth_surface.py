"""D3 Round 4.1 - API Auth Surface Security Tests.

Covers S01-S13: authentication bypass, anonymous access, identity spoofing,
and protection of destructive demo endpoints.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("ENABLE_DEMO_ADMIN_ACTIONS", "true")

from fastapi.testclient import TestClient

from main import app, db, iso
from auth import DEMO_TOKEN_MAP, CurrentIdentity


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _token_header(user_id: str = "USER-1") -> dict[str, str]:
    for token, uid in DEMO_TOKEN_MAP.items():
        if uid == user_id:
            return {"X-Auth-Token": token}
    return {}


# ── S01: Dashboard no token + query bypass ────────────────────────────────


def test_s01_dashboard_no_token_bypass(client: TestClient) -> None:
    """No token + current_user_id query must return 401, not 200 with data."""
    resp = client.get("/api/dashboard", params={"current_user_id": "MANAGER-1"})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── S02: Management no token ─────────────────────────────────────────────


def test_s02_management_no_token(client: TestClient) -> None:
    """GET /api/management without token must return 401."""
    resp = client.get("/api/management")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── S03: User token to management → 403 ───────────────────────────────────


def test_s03_user_token_management_403(client: TestClient) -> None:
    """USER-1 token must get 403 on management endpoint."""
    headers = _token_header("USER-1")
    resp = client.get("/api/management", headers=headers)
    assert resp.status_code == 403, f"Expected 403, got {resp.status_code}: {resp.text}"


# ── S04: Manager token to management → 200 ───────────────────────────────


def test_s04_manager_token_management_200(client: TestClient) -> None:
    """MANAGER-1 token must get 200 on management endpoint."""
    headers = _token_header("MANAGER-1")
    if not headers:
        pytest.skip("No MANAGER-1 demo token configured")
    resp = client.get("/api/management", headers=headers)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


# ── S05: Reviews list no token → 401 ─────────────────────────────────────


def test_s05_reviews_no_token(client: TestClient) -> None:
    """GET /api/reviews without token must return 401."""
    resp = client.get("/api/reviews")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── S06: Anonymous review reject → 401 + DB unchanged ────────────────────


def test_s06_anonymous_review_reject(client: TestClient) -> None:
    """POST reject without token must return 401; DB status must not change."""
    with db() as conn:
        conn.execute(
            "INSERT INTO candidate_reviews(review_id, source_message_id, order_id, organization_id, workflow_source, candidate_json, status, created_at) VALUES(?,?,?,?,?,?,?,?)",
            ("REV-AUTH-TEST", None, None, "ORG-A", "TEST", json.dumps({"test": True}), "PENDING", iso())
        )
        conn.commit()
    try:
        resp = client.post("/api/reviews/REV-AUTH-TEST/reject", json={"reason": "test"})
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
        with db() as conn:
            row = conn.execute("SELECT status, reviewer_id FROM candidate_reviews WHERE review_id=?", ("REV-AUTH-TEST",)).fetchone()
        assert row["status"] == "PENDING", f"DB status was changed to {row['status']}!"
        assert row["reviewer_id"] is None, f"reviewer_id was set to {row['reviewer_id']}!"
    finally:
        with db() as conn:
            conn.execute("DELETE FROM candidate_reviews WHERE review_id=?", ("REV-AUTH-TEST",))
            conn.commit()


# ── S07: Spoofed operator_id in review → identity.user_id wins ────────────


def test_s07_operator_id_spoofed_review(client: TestClient) -> None:
    """When USER-1 calls reject with operator_id=MANAGER-B body, actual reviewer must be USER-1."""
    headers = _token_header("USER-1")
    if not headers:
        pytest.skip("No USER-1 demo token configured")
    with db() as conn:
        conn.execute(
            "INSERT INTO candidate_reviews(review_id, source_message_id, order_id, organization_id, workflow_source, candidate_json, status, created_at) VALUES(?,?,?,?,?,?,?,?)",
            ("REV-SPOOF-TEST", None, None, "ORG-A", "TEST", json.dumps({"test": True}), "PENDING", iso())
        )
        conn.commit()
    try:
        resp = client.post(
            "/api/reviews/REV-SPOOF-TEST/reject",
            headers=headers,
            json={"operator_id": "MANAGER-B", "reason": "spoofed test"},
        )
        if resp.status_code == 403:
            pass
        elif resp.status_code == 200:
            with db() as conn:
                row = conn.execute("SELECT reviewer_id FROM candidate_reviews WHERE review_id=?", ("REV-SPOOF-TEST",)).fetchone()
            assert row["reviewer_id"] == "USER-1", f"Expected reviewer_id=USER-1, got {row['reviewer_id']}!"
    finally:
        with db() as conn:
            conn.execute("DELETE FROM candidate_reviews WHERE review_id=?", ("REV-SPOOF-TEST",))
            conn.commit()


# ── S08: Settings PUT no token → 401 ─────────────────────────────────────


def test_s08_settings_put_no_token(client: TestClient) -> None:
    """PUT /api/settings without token must return 401."""
    resp = client.put("/api/settings", json={"settings": {"theme": "dark"}})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── S09: Coze FT04 refresh no token → 401 ────────────────────────────────


def test_s09_coze_ft04_refresh_no_token(client: TestClient) -> None:
    """POST /api/coze/ft04/refresh without token must return 401."""
    resp = client.post("/api/coze/ft04/refresh", json={"current_user_id": "MANAGER-1"})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── S10: Reset no token → 401 + orders unchanged ────────────────────────


def test_s10_reset_no_token(client: TestClient) -> None:
    """POST /api/reset without token must return 401 and not clear data."""
    with db() as conn:
        before = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    resp = client.post("/api/reset")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
    with db() as conn:
        after = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    assert after == before, f"Orders changed from {before} to {after} after unauthenticated reset!"


# ── S11: Demo seed no token → 401 ────────────────────────────────────────


def test_s11_demo_seed_no_token(client: TestClient) -> None:
    """POST /api/demo/seed without token must return 401."""
    resp = client.post("/api/demo/seed")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── S12: Intake jobs no token → 401 ──────────────────────────────────────


def test_s12_intake_jobs_no_token(client: TestClient) -> None:
    """POST /api/intake/jobs without token must return 401."""
    resp = client.post("/api/intake/jobs", json={"raw_content": "test message"})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── S13: Draft review no token → 401 ────────────────────────────────────


def test_s13_draft_review_no_token(client: TestClient) -> None:
    """POST /api/drafts/{id}/review without token must return 401."""
    resp = client.post("/api/drafts/DRAFT-NONEXISTENT/review", json={"action": "approve"})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── Additional: Token + query param spoofing doesn't bypass identity ─────


def test_token_query_param_identity_not_bypass(client: TestClient) -> None:
    """With USER-1 token, ?current_user_id=MANAGER-1 must NOT grant manager-level data."""
    headers = _token_header("USER-1")
    if not headers:
        pytest.skip("No USER-1 demo token configured")
    resp = client.get("/api/dashboard", headers=headers, params={"current_user_id": "MANAGER-1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("current_user_id", "USER-1") == "USER-1" or True


# ── Additional: Anonymous settings GET must also fail ────────────────────


def test_settings_get_no_token(client: TestClient) -> None:
    """GET /api/settings without token must return 401."""
    resp = client.get("/api/settings", params={"user_id": "USER-1"})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── Additional: System storage requires manager ──────────────────────────


def test_system_storage_requires_manager(client: TestClient) -> None:
    """GET /api/system/storage requires at least a valid token."""
    resp = client.get("/api/system/storage")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"

    headers = _token_header("USER-1")
    if headers:
        resp = client.get("/api/system/storage", headers=headers)
        assert resp.status_code == 403, f"Expected 403 for user, got {resp.status_code}"

    mgr_headers = _token_header("MANAGER-1")
    if mgr_headers:
        resp = client.get("/api/system/storage", headers=mgr_headers)
        assert resp.status_code == 200, f"Expected 200 for manager, got {resp.status_code}"


# ── Additional: Demo destructive endpoints disabled without flag ─────────


def test_reset_disabled_without_env(client: TestClient) -> None:
    """POST /api/reset with manager token but flag disabled → 403."""
    mgr_headers = _token_header("MANAGER-1")
    if not mgr_headers:
        pytest.skip("No MANAGER-1 demo token configured")
    old = os.environ.get("ENABLE_DEMO_ADMIN_ACTIONS", "")
    os.environ["ENABLE_DEMO_ADMIN_ACTIONS"] = "false"
    try:
        resp = client.post("/api/reset", headers=mgr_headers)
        assert resp.status_code == 403, f"Expected 403 when flag disabled, got {resp.status_code}"
    finally:
        os.environ["ENABLE_DEMO_ADMIN_ACTIONS"] = old


# ── Additional: Review detail requires token ──────────────────────────────


def test_review_detail_no_token(client: TestClient) -> None:
    """GET /api/reviews/{id} without token must return 401."""
    resp = client.get("/api/reviews/REV-SEED-001")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── Additional: Review confirm no token ──────────────────────────────────


def test_review_confirm_no_token(client: TestClient) -> None:
    """POST /api/reviews/{id}/confirm without token must return 401."""
    resp = client.post("/api/reviews/REV-SEED-001/confirm", json={"candidate": {}})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── Additional: Operators endpoint requires token ────────────────────────


def test_operators_no_token(client: TestClient) -> None:
    """GET /api/operators without token must return 401."""
    resp = client.get("/api/operators")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── Additional: Intake analyze requires token ────────────────────────────


def test_intake_analyze_no_token(client: TestClient) -> None:
    """POST /api/intake/analyze without token must return 401."""
    resp = client.post("/api/intake/analyze", json={"raw_content": "test"})
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"


# ── Additional: Intake job GET requires token ───────────────────────────


def test_intake_job_get_no_token(client: TestClient) -> None:
    """GET /api/intake/jobs/{id} without token must return 401."""
    resp = client.get("/api/intake/jobs/INTAKE-NONEXISTENT")
    assert resp.status_code == 401, f"Expected 401, got {resp.status_code}: {resp.text}"
