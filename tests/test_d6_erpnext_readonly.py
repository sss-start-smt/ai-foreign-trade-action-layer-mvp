"""
D6 Tests: ERPNext Read-Only Integration

Covers:
  1. Sales Order/Customer/Item三类读取
  2. Full Sync分页
  3. Incremental使用modified >= cursor（真实cursor）
  4. 边界重复记录幂等
  5. Cursor只在完整成功后推进
  6. Timeout/5xx失败保留旧快照
  7. 401/403显式失败
  8. malformed JSON不污染snapshot/cursor
  9. Secret脱敏
  10. Client无POST/PUT/PATCH/DELETE
  11. ERP owner不映射为FlowOrder owner
  12. 组织隔离
  13. Freshness状态（NEVER_SYNCED/FRESH/STALE/UNAVAILABLE）
  14. Frappe filter数组格式 [["modified", ">=", cursor]]
  15. Sales Order list→detail模式
  16. ERPNEXT_ORGANIZATION_ID绑定
  17. PG runtime不建表
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "lib"))

import pytest
from fastapi.testclient import TestClient

import auth
import database
from database import db, table_exists
import erpnext_readonly
from erpnext_readonly import (
    ERPNextReadOnlyClient,
    ERPNextNormalizer,
    ERPReadSyncService,
    ERPNextConfig,
    ERPNextClientError,
    compute_freshness,
    compute_global_freshness,
    DOCTYPES,
    _DOCTYPE_LIST_FIELDS,
    _DOCTYPE_DETAIL_FIELDS,
    _ORDER_BY,
    _ensure_erp_schema,
    _get_sync_state,
    CONFIG,
    _service,
)


# ─── Mock Helpers ──────────────────────────────────────────────────────────

class MockResponse:
    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json_data = json_data
        self._text = text
        self.headers = headers or {}

    def json(self):
        if self._json_data is not None:
            return self._json_data
        raise json.JSONDecodeError("Invalid JSON", "", 0)

    @property
    def text(self):
        return self._text or json.dumps(self._json_data) if self._json_data else self._text


class MockErpClient(ERPNextReadOnlyClient):
    """Mock HTTP client. Stores call log with headers. Supports list→detail for Sales Order."""

    def __init__(self, responses=None, raise_error=None, base_url="http://test.erpnext.local",
                 auth_token="token test_key:test_secret"):
        super().__init__(base_url=base_url, auth_header=auth_token)
        self._responses = responses or {}
        self._raise_error = raise_error
        self._call_log = []
        self._last_headers = None

    def _get(self, url, params=None):
        headers = self._get_headers()
        self._last_headers = headers
        self._call_log.append({"url": url, "params": params, "headers": headers})
        if self._raise_error:
            raise self._raise_error
        key = url
        if key in self._responses:
            resp = self._responses[key]
            if callable(resp):
                resp = resp(url, headers, params)
            if isinstance(resp, MockResponse):
                if resp.status_code == 401:
                    raise ERPNextClientError("401", code="AUTH_FAILED", status_code=401)
                if resp.status_code == 403:
                    raise ERPNextClientError("403", code="PERMISSION_DENIED", status_code=403)
                if resp.status_code >= 500:
                    raise ERPNextClientError(f"{resp.status_code}", code="SERVER_ERROR", status_code=resp.status_code)
                if resp.status_code >= 400:
                    raise ERPNextClientError(f"{resp.status_code}", code="CLIENT_ERROR", status_code=resp.status_code)
                try:
                    return resp.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    raise ERPNextClientError(f"Malformed JSON: {exc}", code="MALFORMED_JSON")
            return resp
        return {"data": []}


def _make_identity(org_id="ORG-A", user_id="USER-1", user_role="manager"):
    return auth.CurrentIdentity(
        user_id=user_id,
        organization_id=org_id,
        role=user_role,
        name="Test User",
    )


_USER_TOKEN_LOOKUP = {v: k for k, v in auth.DEMO_TOKEN_MAP.items()}

def _auth_headers(user_id="MANAGER-1"):
    token = _USER_TOKEN_LOOKUP.get(user_id)
    if not token:
        raise KeyError(f"Unknown user_id: {user_id}")
    return {"X-Auth-Token": token}


# ─── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def _db_path(tmp_path_factory):
    """Module-scoped DB path for D6 tests only."""
    path = str(tmp_path_factory.mktemp("floworder_d6_test") / "test.db")
    return path

@pytest.fixture(scope="module", autouse=True)
def _setup_db(_db_path):
    """Module-level: set DB_PATH for D6 tests ONLY, restore after module finishes.
    
    Saves original DB_PATH before tests, creates schema, then restores
    so D5 tests in other modules are not polluted.
    """
    old_db_path = os.environ.get("DB_PATH")
    os.environ["DB_PATH"] = _db_path
    from database import db as _db
    with _db() as conn:
        _ensure_erp_schema(conn)
        conn.commit()
    yield
    if old_db_path is not None:
        os.environ["DB_PATH"] = old_db_path
    else:
        os.environ.pop("DB_PATH", None)

@pytest.fixture(autouse=True)
def _clean_erp_tables():
    """Clean ERP tables after each test (D6 module only)."""
    yield
    with db() as conn:
        for dt in DOCTYPES:
            conn.execute("DELETE FROM erp_read_snapshots WHERE doctype=?", (dt,))
            conn.execute("DELETE FROM erp_sync_state WHERE doctype=?", (dt,))
        conn.commit()


@pytest.fixture(scope="module")
def _app(_setup_db):
    """Import main app with proper env setup. Saves/restores env vars."""
    _saved_env = {}
    for key in ("APP_API_KEY", "SEED_DEMO_DATA", "COZE_ALLOW_LOCAL_WHEN_UNCONFIGURED",
                "COZE_ALLOW_LOCAL_CONFIRM_WHEN_UNCONFIGURED", "ENABLE_DEMO_ADMIN_ACTIONS"):
        _saved_env[key] = os.environ.get(key)

    os.environ["APP_API_KEY"] = "test-key"
    os.environ["SEED_DEMO_DATA"] = "true"
    os.environ["COZE_ALLOW_LOCAL_WHEN_UNCONFIGURED"] = "true"
    os.environ["COZE_ALLOW_LOCAL_CONFIRM_WHEN_UNCONFIGURED"] = "true"
    os.environ["ENABLE_DEMO_ADMIN_ACTIONS"] = "true"

    from main import app, init_db
    init_db()
    yield app

    for key, val in _saved_env.items():
        if val is not None:
            os.environ[key] = val
        else:
            os.environ.pop(key, None)


@pytest.fixture
def setup_client(_app):
    with TestClient(_app) as client:
        yield client


@pytest.fixture(autouse=True)
def reset_erp_config():
    """Reset ERPNextConfig after each test."""
    saved = (
        CONFIG.base_url,
        CONFIG.api_key,
        CONFIG.api_secret,
        CONFIG.organization_id,
        CONFIG.page_size,
    )
    yield
    CONFIG.base_url, CONFIG.api_key, CONFIG.api_secret, CONFIG.organization_id, CONFIG.page_size = saved
    erpnext_readonly._service = ERPReadSyncService()


def _prime_cursor(conn, org_id, doctype, cursor_value):
    """Insert a sync state with a cursor to simulate prior successful sync."""
    _ensure_erp_schema(conn)
    ts = "2026-08-11T10:00:00+08:00"
    conn.execute(
        """INSERT OR REPLACE INTO erp_sync_state
           (organization_id, doctype, last_success_cursor, last_success_at, last_attempt_at,
            sync_status, last_error_code, records_seen, records_changed, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (org_id, doctype, cursor_value, ts, ts, "FRESH", None, 1, 1, ts),
    )
    conn.commit()


# ─── Test: Module Import / No DB_PATH Pollution ─────────────────────────────

class TestNoDBPathPollution:
    """Verify D6 module does NOT set DB_PATH at import time."""

    def test_db_path_not_modified_by_import(self, monkeypatch):
        """Importing test_d6 must not change os.environ['DB_PATH'] at module level."""
        test_path = "/clean/test/path.db"
        monkeypatch.setenv("DB_PATH", test_path)
        import importlib
        import tests.test_d6_erpnext_readonly
        importlib.reload(tests.test_d6_erpnext_readonly)
        assert os.environ.get("DB_PATH") == test_path


# ─── Test: Client ───────────────────────────────────────────────────────────

class TestERPNextClient:
    """Test ERPNextReadOnlyClient basic behavior."""

    def test_no_write_methods(self):
        client = ERPNextReadOnlyClient()
        for method in ("post", "put", "patch", "delete"):
            assert not hasattr(client, method)

    def test_outbound_auth_header_sent(self):
        """Outbound HTTP to ERPNext MUST include Authorization: token ... header."""
        client = ERPNextReadOnlyClient(base_url="http://test.local", auth_header="token test_key:test_secret")
        headers = client._get_headers()
        assert "Authorization" in headers
        assert headers["Authorization"] == "token test_key:test_secret"

    def test_no_auth_header_when_not_configured(self):
        """_get_headers returns no Authorization when no token configured."""
        client = ERPNextReadOnlyClient(base_url="http://test.local", auth_header="")
        headers = client._get_headers()
        assert "Authorization" not in headers

    def test_auth_header_in_mock_call(self):
        """MockErpClient call_log captures Authorization header on outbound request."""
        mock_resp = MockResponse(200, {"data": []})
        responses = {"/api/resource/Customer": mock_resp}
        client = MockErpClient(responses=responses, auth_token="token mykey:mysecret")
        client.get_list("Customer", ["name"])
        call_headers = client._call_log[0]["headers"]
        assert "Authorization" in call_headers
        assert call_headers["Authorization"] == "token mykey:mysecret"

    def test_frappe_filter_array_format(self):
        """get_list sends filters as array-of-arrays per Frappe spec."""
        mock_resp = MockResponse(200, {"data": []})
        responses = {"/api/resource/Sales Order": mock_resp}
        client = MockErpClient(responses=responses)
        filters = [["modified", ">=", "2026-08-11T10:00:00+08:00"]]
        client.get_list("Sales Order", ["name"], filters=filters, limit_start=0, limit_page_length=50)
        call_params = client._call_log[0]["params"]
        parsed_filters = json.loads(call_params["filters"])
        assert parsed_filters == [["modified", ">=", "2026-08-11T10:00:00+08:00"]]

    def test_order_by_in_params(self):
        """get_list includes order_by param for stable pagination."""
        mock_resp = MockResponse(200, {"data": []})
        responses = {"/api/resource/Sales Order": mock_resp}
        client = MockErpClient(responses=responses)
        client.get_list("Sales Order", ["name"], order_by="modified asc, name asc")
        call_params = client._call_log[0]["params"]
        assert call_params.get("order_by") == "modified asc, name asc"


# ─── Test: Secret Handling ──────────────────────────────────────────────────

class TestSecretHandling:
    """Secret redaction - no api_secret in status response."""

    def test_mask_no_secret(self):
        """mask() must NOT include api_secret."""
        c = ERPNextConfig()
        c.api_key = "test_key_1234"
        c.api_secret = "sk-super-secret"
        masked = c.mask()
        assert "api_secret" not in masked
        assert "Authorization" not in masked
        assert "api_key" not in masked
        assert "base_url" in masked
        assert "configured" in masked

    def test_status_no_secret(self, setup_client):
        """Status endpoint must not include api_secret or api_key."""
        CONFIG.base_url = "http://test.erpnext.local"
        CONFIG.api_key = "test_key"
        CONFIG.api_secret = "test_secret"
        CONFIG.organization_id = "ORG-A"
        resp = setup_client.get(
            "/api/integrations/erpnext/status",
            headers=_auth_headers("MANAGER-1"),
        )
        body = resp.json()
        assert "api_secret" not in str(body)
        assert "Authorization" not in str(body)
        config = body.get("config", {})
        assert "api_secret" not in config
        assert "api_key" not in config

    def test_secret_not_in_logs(self):
        """Config secret must not appear in mask or status."""
        c = ERPNextConfig()
        c.api_secret = "sk-super-secret-value"
        masked = c.mask()
        assert "sk-super-secret" not in str(masked)


# ─── Test: Normalizer ────────────────────────────────────────────────────────

class TestERPNextNormalizer:

    def test_normalize_sales_order(self):
        raw = {
            "name": "SO-001", "status": "Completed", "customer": "CUST-001",
            "items": [{"item_code": "ITEM-001", "qty": 2, "rate": 100}],
            "modified": "2025-01-15 10:00:00",
        }
        result = ERPNextNormalizer._normalize_sales_order(raw)
        assert result["external_id"] == "SO-001"
        assert result["order_status"] == "Completed"
        assert len(result["items"]) == 1

    def test_normalize_customer(self):
        raw = {"name": "CUST-001", "customer_name": "Test", "modified": "2025-01-15 10:00:00"}
        result = ERPNextNormalizer._normalize_customer(raw)
        assert result["external_id"] == "CUST-001"
        assert result["customer_name"] == "Test"

    def test_normalize_item(self):
        raw = {"name": "ITEM-001", "item_code": "ITEM-001", "item_name": "Widget", "modified": "2025-01-15 10:00:00"}
        result = ERPNextNormalizer._normalize_item(raw)
        assert result["external_id"] == "ITEM-001"
        assert result["item_name"] == "Widget"

    def test_erp_owner_not_mapped(self):
        """ERP 'owner' must NOT become FlowOrder owner_id."""
        raw = {"name": "SO-001", "owner": "user@example.com", "modified": "2025-01-15 10:00:00"}
        result = ERPNextNormalizer._normalize_sales_order(raw)
        assert "owner_id" not in result
        assert "owner" not in result


# ─── Test: Sales Order List → Detail ────────────────────────────────────────

class TestSalesOrderListDetail:
    """Sales Order: list gets parent fields, then GET /{name} for items."""

    def test_list_fields_exclude_items(self):
        """List fields should not include 'items'."""
        assert "items" not in _DOCTYPE_LIST_FIELDS["Sales Order"]
        assert "items" in _DOCTYPE_DETAIL_FIELDS["Sales Order"]

    def test_detail_fields_include_items(self):
        """Detail fields should include 'items'."""
        assert "items" in _DOCTYPE_DETAIL_FIELDS["Sales Order"]

    def test_list_then_detail_flow(self):
        """Full sync of SO triggers list call + detail call for each record."""
        CONFIG.organization_id = "ORG-A"
        list_resp = MockResponse(200, {
            "data": [
                {"name": "SO-001", "status": "Completed", "customer": "C1", "modified": "2026-08-11T10:00:00+08:00"},
            ],
        })
        detail_resp = MockResponse(200, {
            "data": {
                "name": "SO-001", "status": "Completed", "customer": "C1",
                "items": [{"item_code": "ITEM-1", "qty": 2, "rate": 100}],
                "modified": "2026-08-11T10:00:00+08:00",
            },
        })
        responses = {
            "/api/resource/Sales Order": list_resp,
            "/api/resource/Sales Order/SO-001": detail_resp,
        }
        mock_client = MockErpClient(responses=responses)
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["doctypes"]["Sales Order"]["records_seen"] == 1

        call_urls = [c["url"] for c in mock_client._call_log]
        assert "/api/resource/Sales Order" in call_urls
        assert "/api/resource/Sales Order/SO-001" in call_urls


# ─── Test: Full Sync ─────────────────────────────────────────────────────────

class TestERPNextFullSync:

    def test_full_sync_sales_orders(self):
        CONFIG.organization_id = "ORG-A"
        so_resp = MockResponse(200, {
            "data": [
                {"name": "SO-001", "status": "Completed", "customer": "C1", "modified": "2026-08-11T10:00:00+08:00"},
                {"name": "SO-002", "status": "Draft", "customer": "C2", "modified": "2026-08-11T11:00:00+08:00"},
            ],
        })
        detail_singleton = MockResponse(200, {
            "data": {"name": "SO-001", "status": "Completed", "customer": "C1", "items": [], "modified": "2026-08-11T10:00:00+08:00"},
        })
        def detail_factory(url, headers, params):
            name = url.split("/")[-1]
            return MockResponse(200, {"data": {"name": name, "items": [], "modified": "2026-08-11T10:00:00+08:00"}})

        responses = {
            "/api/resource/Sales Order": so_resp,
            "/api/resource/Sales Order/SO-001": detail_factory,
            "/api/resource/Sales Order/SO-002": detail_factory,
        }
        mock_client = MockErpClient(responses=responses)
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["overall_status"] == "SUCCESS"
        assert result["doctypes"]["Sales Order"]["records_seen"] == 2

    def test_full_sync_customers(self):
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {
            "data": [
                {"name": "C1", "customer_name": "Alice", "modified": "2026-08-11T10:00:00+08:00"},
                {"name": "C2", "customer_name": "Bob", "modified": "2026-08-11T11:00:00+08:00"},
                {"name": "C3", "customer_name": "Carol", "modified": "2026-08-11T12:00:00+08:00"},
            ],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["overall_status"] == "SUCCESS"
        assert result["doctypes"]["Customer"]["records_seen"] == 3

    def test_full_sync_items(self):
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {
            "data": [
                {"name": "I1", "item_name": "Widget", "modified": "2026-08-11T10:00:00+08:00"},
                {"name": "I2", "item_name": "Gadget", "modified": "2026-08-11T11:00:00+08:00"},
            ],
        })
        mock_client = MockErpClient(responses={"/api/resource/Item": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["overall_status"] == "SUCCESS"
        assert result["doctypes"]["Item"]["records_seen"] == 2

    def test_full_sync_pagination(self):
        CONFIG.organization_id = "ORG-A"
        CONFIG.page_size = 5
        page1 = MockResponse(200, {
            "data": [{"name": f"SO-{i:03d}", "modified": "2026-08-11T10:00:00+08:00"} for i in range(5)],
        })
        page2 = MockResponse(200, {
            "data": [{"name": f"SO-{i:03d}", "modified": "2026-08-11T10:00:00+08:00"} for i in range(5, 8)],
        })
        call_count = [0]
        def paginated(url, headers, params):
            call_count[0] += 1
            start = params.get("limit_start", 0)
            return page1 if start == 0 else page2

        def detail_factory(url, headers, params):
            name = url.split("/")[-1]
            return MockResponse(200, {"data": {"name": name, "items": [], "modified": "2026-08-11T10:00:00+08:00"}})

        responses = {"/api/resource/Sales Order": paginated}
        for i in range(8):
            responses[f"/api/resource/Sales Order/SO-{i:03d}"] = detail_factory
        mock_client = MockErpClient(responses=responses)
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["doctypes"]["Sales Order"]["records_seen"] == 8
        assert call_count[0] == 2

    def test_full_sync_hash_unchanged_means_zero_changed(self):
        """Same data twice → records_changed=0 on second run."""
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "Alice", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result1 = service.full_sync("ORG-A", identity)
        assert result1["doctypes"]["Customer"]["records_changed"] == 1

        result2 = service.full_sync("ORG-A", identity)
        assert result2["doctypes"]["Customer"]["records_changed"] == 0
        assert result2["doctypes"]["Customer"]["records_seen"] == 1

    def test_full_sync_per_doctype_stats(self):
        """Each doctype gets its own seen/changed, not total."""
        CONFIG.organization_id = "ORG-A"
        so_resp = MockResponse(200, {"data": [{"name": "SO-1", "modified": "2026-08-11T10:00:00+08:00"}]})
        cust_resp = MockResponse(200, {"data": [{"name": "C-1", "modified": "2026-08-11T10:00:00+08:00"}, {"name": "C-2", "modified": "2026-08-11T10:00:00+08:00"}]})
        item_resp = MockResponse(200, {"data": [{"name": "I-1", "modified": "2026-08-11T10:00:00+08:00"}, {"name": "I-2", "modified": "2026-08-11T10:00:00+08:00"}, {"name": "I-3", "modified": "2026-08-11T10:00:00+08:00"}]})
        so_detail = MockResponse(200, {"data": {"name": "SO-1", "items": [], "modified": "2026-08-11T10:00:00+08:00"}})
        responses = {
            "/api/resource/Sales Order": so_resp,
            "/api/resource/Sales Order/SO-1": so_detail,
            "/api/resource/Customer": cust_resp,
            "/api/resource/Item": item_resp,
        }
        mock_client = MockErpClient(responses=responses)
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["doctypes"]["Sales Order"]["records_seen"] == 1
        assert result["doctypes"]["Customer"]["records_seen"] == 2
        assert result["doctypes"]["Item"]["records_seen"] == 3
        assert result["total_records_seen"] == 6


# ─── Test: Incremental Sync With REAL Cursor ─────────────────────────────────

class TestERPNextIncrementalSync:

    def test_incremental_with_real_cursor_filter_format(self):
        """Incremental with cursor sends Frappe array filter [["modified", ">=", cursor]]."""
        CONFIG.organization_id = "ORG-A"
        with db() as conn:
            _prime_cursor(conn, "ORG-A", "Sales Order", "2026-08-11T10:00:00+08:00")

        mock_resp = MockResponse(200, {
            "data": [{"name": "SO-NEW", "modified": "2026-08-11T12:00:00+08:00"}],
        })
        call_log = []
        def tracking_response(url, headers, params):
            call_log.append({"url": url, "params": params})
            return mock_resp

        responses = {"/api/resource/Sales Order": tracking_response}
        mock_client = MockErpClient(responses=responses)
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.incremental_sync("ORG-A", identity)
        assert result["mode"] == "incremental"
        assert len(call_log) > 0

        params = call_log[0]["params"]
        filters = json.loads(params["filters"])
        assert filters == [["modified", ">=", "2026-08-11T10:00:00+08:00"]]

    def test_incremental_no_cursor_falls_back_full(self):
        """Without cursor, incremental falls back to full (filters=None)."""
        CONFIG.organization_id = "ORG-A"
        mock_resp = MockResponse(200, {
            "data": [{"name": "SO-1", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        call_log = []
        def tracking(url, headers, params):
            call_log.append({"url": url, "params": params})
            return mock_resp

        responses = {"/api/resource/Sales Order": tracking}
        mock_client = MockErpClient(responses=responses)
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.incremental_sync("ORG-A", identity)
        assert result["mode"] == "incremental"

        params = call_log[0]["params"]
        assert "filters" not in params

    def test_incremental_boundary_overlap_idempotent(self):
        """Incremental with boundary overlap: same record fetched twice → not double-counted."""
        CONFIG.organization_id = "ORG-A"
        with db() as conn:
            _prime_cursor(conn, "ORG-A", "Customer", "2026-08-11T10:00:00+08:00")

        resp = MockResponse(200, {
            "data": [
                {"name": "C1", "customer_name": "Alice", "modified": "2026-08-11T10:30:00+08:00"},
                {"name": "C2", "customer_name": "Bob", "modified": "2026-08-11T11:00:00+08:00"},
            ],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result1 = service.incremental_sync("ORG-A", identity)

        result2 = service.incremental_sync("ORG-A", identity)
        assert result2["doctypes"]["Customer"]["records_seen"] == 2
        assert result2["doctypes"]["Customer"]["records_changed"] == 0

    def test_incremental_cursor_advances_after_success(self):
        """Cursor advances after successful incremental."""
        CONFIG.organization_id = "ORG-A"
        with db() as conn:
            _prime_cursor(conn, "ORG-A", "Customer", "2026-08-11T10:00:00+08:00")

        resp = MockResponse(200, {
            "data": [{"name": "C-NEW", "customer_name": "New", "modified": "2026-08-11T12:00:00+08:00"}],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.incremental_sync("ORG-A", identity)
        assert result["overall_status"] == "SUCCESS"

        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Customer")
        assert state["last_success_cursor"] >= "2026-08-11T12"

    def test_incremental_empty_cursor_preserves_old(self):
        """No new records → cursor stays at old value, no now() injection."""
        CONFIG.organization_id = "ORG-A"
        with db() as conn:
            _prime_cursor(conn, "ORG-A", "Item", "2026-08-11T10:00:00+08:00")

        resp = MockResponse(200, {"data": []})
        mock_client = MockErpClient(responses={"/api/resource/Item": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.incremental_sync("ORG-A", identity)
        assert result["doctypes"]["Item"]["records_seen"] == 0

        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Item")
        assert state["last_success_cursor"] == "2026-08-11T10:00:00+08:00"

    def test_incremental_cross_page_boundary(self):
        """Incremental with cursor spanning multiple pages: all pages fetched, cursor advances to max."""
        CONFIG.organization_id = "ORG-A"
        CONFIG.page_size = 2
        with db() as conn:
            _prime_cursor(conn, "ORG-A", "Customer", "2026-08-11T10:00:00+08:00")

        page1 = MockResponse(200, {
            "data": [
                {"name": "C1", "customer_name": "A", "modified": "2026-08-11T11:00:00+08:00"},
                {"name": "C2", "customer_name": "B", "modified": "2026-08-11T11:30:00+08:00"},
            ],
        })
        page2 = MockResponse(200, {
            "data": [
                {"name": "C3", "customer_name": "C", "modified": "2026-08-11T12:00:00+08:00"},
            ],
        })
        call_log = []
        def paginated(url, headers, params):
            call_log.append({"url": url, "params": params})
            start = params.get("limit_start", 0)
            return page1 if start == 0 else page2

        responses = {"/api/resource/Customer": paginated}
        mock_client = MockErpClient(responses=responses)
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.incremental_sync("ORG-A", identity)
        assert result["doctypes"]["Customer"]["records_seen"] == 3
        assert result["doctypes"]["Customer"]["records_changed"] == 3
        assert len(call_log) >= 2

        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Customer")
        assert state["last_success_cursor"] >= "2026-08-11T12"

        CONFIG.page_size = 50


# ─── Test: Idempotency ───────────────────────────────────────────────────────

class TestERPNextIdempotency:

    def test_double_sync_idempotent(self):
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "Alice", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        r1 = service.full_sync("ORG-A", identity)
        r2 = service.full_sync("ORG-A", identity)
        assert r1["total_records_seen"] == r2["total_records_seen"]


# ─── Test: Cursor Management ─────────────────────────────────────────────────

class TestERPNextCursorManagement:

    def test_cursor_advances_on_success(self):
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "A", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["overall_status"] == "SUCCESS"
        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Customer")
        assert state["last_success_cursor"] is not None
        assert state["last_success_cursor"] >= "2026-08-11"

    def test_cursor_not_advanced_on_failure(self):
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "A", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        service.full_sync("ORG-A", identity)
        with db() as conn:
            state_before = _get_sync_state(conn, "ORG-A", "Customer")
        cursor_before = state_before.get("last_success_cursor")

        mock_client._raise_error = ERPNextClientError("Connect failed", code="CONNECTION_ERROR")
        service.incremental_sync("ORG-A", identity)
        with db() as conn:
            state_after = _get_sync_state(conn, "ORG-A", "Customer")
        cursor_after = state_after.get("last_success_cursor")
        assert cursor_after == cursor_before

    def test_cursor_none_when_no_source_records(self):
        """Empty full sync: cursor stays None (not set to now())."""
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {"data": []})
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["doctypes"]["Customer"]["records_seen"] == 0
        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Customer")
        assert state["last_success_cursor"] is None or state["last_success_cursor"] == ""


# ─── Test: Error Handling ────────────────────────────────────────────────────

class TestERPNextErrorHandling:

    def test_timeout_preserves_snapshot(self):
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "A", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        service.full_sync("ORG-A", identity)

        mock_client._raise_error = ERPNextClientError("Timeout", code="TIMEOUT")
        result = service.incremental_sync("ORG-A", identity)
        assert result["doctypes"]["Customer"]["status"] == "FAILED"
        assert result["doctypes"]["Customer"]["error_code"] == "TIMEOUT"
        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Customer")
        assert state["sync_status"] == "STALE"

    def test_5xx_preserves_snapshot(self):
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "A", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        service.full_sync("ORG-A", identity)

        error_resp = MockResponse(500, text="Server Error")
        mock_client._responses = {"/api/resource/Customer": error_resp}
        result = service.incremental_sync("ORG-A", identity)
        assert result["doctypes"]["Customer"]["status"] == "FAILED"
        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Customer")
        assert state["sync_status"] == "STALE"

    def test_401_auth_failure(self):
        CONFIG.organization_id = "ORG-A"
        error_resp = MockResponse(401, text="Unauthorized")
        mock_client = MockErpClient(responses={"/api/resource/Customer": error_resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["doctypes"]["Customer"]["status"] == "FAILED"
        assert result["doctypes"]["Customer"]["error_code"] == "AUTH_FAILED"
        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Customer")
        assert state["sync_status"] == "UNAVAILABLE"

    def test_403_permission_denied(self):
        CONFIG.organization_id = "ORG-A"
        error_resp = MockResponse(403, text="Forbidden")
        mock_client = MockErpClient(responses={"/api/resource/Customer": error_resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["doctypes"]["Customer"]["status"] == "FAILED"
        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Customer")
        assert state["sync_status"] == "UNAVAILABLE"

    def test_malformed_json(self):
        CONFIG.organization_id = "ORG-A"
        good_resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "A", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": good_resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        service.full_sync("ORG-A", identity)

        bad_resp = MockResponse(200, text="not valid json {{{")
        mock_client._responses = {"/api/resource/Customer": bad_resp}
        result = service.incremental_sync("ORG-A", identity)
        assert result["doctypes"]["Customer"]["status"] == "FAILED"
        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Customer")
        assert state["sync_status"] == "STALE"

    def test_network_error(self):
        CONFIG.organization_id = "ORG-A"
        good_resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "A", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": good_resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        service.full_sync("ORG-A", identity)

        mock_client._raise_error = ERPNextClientError("Cannot connect", code="CONNECTION_ERROR")
        result = service.incremental_sync("ORG-A", identity)
        assert result["doctypes"]["Customer"]["status"] == "FAILED"
        with db() as conn:
            state = _get_sync_state(conn, "ORG-A", "Customer")
        assert state["sync_status"] == "STALE"


# ─── Test: Freshness Semantics ───────────────────────────────────────────────

class TestFreshnessSemantics:

    def test_stale_priority_over_fresh(self):
        """sync_status=STALE must return STALE, not FRESH."""
        state = {
            "sync_status": "STALE",
            "last_success_at": "2026-08-11T08:00:00+08:00",
            "last_attempt_at": "2026-08-11T09:00:00+08:00",
        }
        assert compute_freshness(state) == "STALE"

    def test_unavailable_priority(self):
        state = {"sync_status": "UNAVAILABLE", "last_success_at": None}
        assert compute_freshness(state) == "UNAVAILABLE"

    def test_never_synced_with_failure_is_unavailable(self):
        """Never succeeded + source error → UNAVAILABLE (not NEVER_SYNCED)."""
        state = {
            "sync_status": "UNAVAILABLE",
            "last_success_at": None,
            "last_error_code": "AUTH_FAILED",
        }
        assert compute_freshness(state) == "UNAVAILABLE"

    def test_fresh_becomes_stale_after_timeout(self):
        CONFIG.stale_after_seconds = 1
        state = {
            "sync_status": "FRESH",
            "last_success_at": "2026-08-11T10:00:00+08:00",
        }
        import time
        time.sleep(1.1)
        assert compute_freshness(state) == "STALE"

    def test_fresh_stays_fresh_within_threshold(self):
        CONFIG.stale_after_seconds = 3600
        from datetime import timedelta
        CN_TZ = timezone(timedelta(hours=8))
        now_str = datetime.now(CN_TZ).isoformat(timespec="seconds")
        state = {
            "sync_status": "FRESH",
            "last_success_at": now_str,
        }
        result = compute_freshness(state)
        assert result == "FRESH"

    def test_no_state_is_never_synced(self):
        assert compute_freshness(None) == "NEVER_SYNCED"
        assert compute_freshness({}) == "NEVER_SYNCED"

    def test_old_snapshot_failure_does_not_show_fresh(self):
        """Old snapshot + new failure → STALE or UNAVAILABLE, never FRESH."""
        state = {
            "sync_status": "STALE",
            "last_success_at": "2026-08-10T10:00:00+08:00",
            "last_error_code": "TIMEOUT",
        }
        assert compute_freshness(state) == "STALE"
        assert compute_freshness(state) != "FRESH"


# ─── Test: Organization Binding ──────────────────────────────────────────────

class TestOrgBinding:

    def test_sync_denied_for_unbound_org(self, setup_client):
        """No ERPNEXT_ORGANIZATION_ID set → 503 ERPNEXT_ORG_NOT_BOUND."""
        CONFIG.base_url = "http://test.erpnext.local"
        CONFIG.api_key = "test_key"
        CONFIG.api_secret = "test_secret"
        CONFIG.organization_id = ""
        resp = setup_client.post(
            "/api/integrations/erpnext/sync?mode=full",
            headers=_auth_headers("MANAGER-1"),
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body.get("detail", {}).get("code") == "ERPNEXT_ORG_NOT_BOUND"

    def test_sync_denied_for_wrong_org(self, setup_client):
        """Non-bound org (ORG-B) tries to sync → 403 ERP_ORG_MISMATCH."""
        CONFIG.base_url = "http://test.erpnext.local"
        CONFIG.api_key = "test_key"
        CONFIG.api_secret = "test_secret"
        CONFIG.organization_id = "ORG-A"
        resp = setup_client.post(
            "/api/integrations/erpnext/sync?mode=full",
            headers=_auth_headers("MANAGER-B"),
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body.get("detail", {}).get("code") == "ERP_ORG_MISMATCH"

    def test_sync_allowed_for_bound_org(self, setup_client):
        """Bound org with matching token can sync."""
        CONFIG.base_url = "http://test.erpnext.local"
        CONFIG.api_key = "test_key"
        CONFIG.api_secret = "test_secret"
        CONFIG.organization_id = "ORG-A"
        mock_resp = MockResponse(200, {"data": []})
        responses = {
            "/api/resource/Sales Order": mock_resp,
            "/api/resource/Customer": mock_resp,
            "/api/resource/Item": mock_resp,
        }
        mock_client = MockErpClient(responses=responses)
        erpnext_readonly._service = ERPReadSyncService(mock_client)
        try:
            resp = setup_client.post(
                "/api/integrations/erpnext/sync?mode=full",
                headers=_auth_headers("MANAGER-1"),
            )
            assert resp.status_code == 200
        finally:
            erpnext_readonly._service = ERPReadSyncService()


# ─── Test: Per-DocType Independent Success ─────────────────────────────────────

class TestPerDocTypeIndependentSuccess:

    def test_customer_success_item500_customer_state_written(self):
        """Customer succeeds + Item 500 → PARTIAL_FAILURE, but Customer state/cursor written, Item NOT."""
        CONFIG.organization_id = "ORG-A"
        cust_resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "Alice", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        err_resp = MockResponse(500, text="Error")
        responses = {
            "/api/resource/Sales Order": MockResponse(200, {"data": []}),
            "/api/resource/Customer": cust_resp,
            "/api/resource/Item": err_resp,
        }
        mock_client = MockErpClient(responses=responses)
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["overall_status"] == "PARTIAL_FAILURE"
        assert result["doctypes"]["Customer"]["status"] == "SUCCESS"
        assert result["doctypes"]["Item"]["status"] == "FAILED"

        with db() as conn:
            cust_state = _get_sync_state(conn, "ORG-A", "Customer")
            item_state = _get_sync_state(conn, "ORG-A", "Item")
        assert cust_state is not None
        assert cust_state["sync_status"] == "FRESH"
        assert cust_state["last_success_cursor"] is not None
        assert cust_state["records_seen"] == 1
        assert item_state is not None
        assert item_state["sync_status"] == "UNAVAILABLE"
        assert item_state["last_success_cursor"] is None

    def test_failed_doctype_does_not_block_others(self):
        """SO 500 → Customer and Item still write their own success states."""
        CONFIG.organization_id = "ORG-A"
        err_resp = MockResponse(500, text="Error")
        cust_resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "A", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        item_resp = MockResponse(200, {
            "data": [{"name": "I1", "item_name": "W", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        responses = {
            "/api/resource/Sales Order": err_resp,
            "/api/resource/Customer": cust_resp,
            "/api/resource/Item": item_resp,
        }
        mock_client = MockErpClient(responses=responses)
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["overall_status"] == "PARTIAL_FAILURE"

        with db() as conn:
            cust_state = _get_sync_state(conn, "ORG-A", "Customer")
            item_state = _get_sync_state(conn, "ORG-A", "Item")
        assert cust_state["sync_status"] == "FRESH"
        assert item_state["sync_status"] == "FRESH"


# ─── Test: Snapshots Endpoint RBAC ────────────────────────────────────────────

class TestSnapshotsRBAC:

    def test_viewer_cannot_access_snapshots(self, setup_client):
        CONFIG.base_url = "http://test.erpnext.local"
        CONFIG.api_key = "test_key"
        CONFIG.api_secret = "test_secret"
        CONFIG.organization_id = "ORG-A"
        CONFIG.page_size = 50
        resp = setup_client.get(
            "/api/integrations/erpnext/snapshots?doctype=Customer",
            headers=_auth_headers("USER-1"),
        )
        assert resp.status_code == 403

    def test_manager_can_access_snapshots(self, setup_client):
        CONFIG.base_url = "http://test.erpnext.local"
        CONFIG.api_key = "test_key"
        CONFIG.api_secret = "test_secret"
        CONFIG.organization_id = "ORG-A"
        CONFIG.page_size = 50
        resp = setup_client.get(
            "/api/integrations/erpnext/snapshots?doctype=Customer",
            headers=_auth_headers("MANAGER-1"),
        )
        assert resp.status_code == 200


# ─── Test: RBAC ──────────────────────────────────────────────────────────────

class TestERPNextRBAC:

    def test_viewer_cannot_sync(self, setup_client):
        CONFIG.base_url = "http://test.erpnext.local"
        CONFIG.api_key = "test_key"
        CONFIG.api_secret = "test_secret"
        CONFIG.organization_id = "ORG-A"
        resp = setup_client.post(
            "/api/integrations/erpnext/sync?mode=full",
            headers=_auth_headers("USER-1"),
        )
        assert resp.status_code == 403

    def test_viewer_can_view_status(self, setup_client):
        CONFIG.base_url = "http://test.erpnext.local"
        CONFIG.api_key = "test_key"
        CONFIG.api_secret = "test_secret"
        CONFIG.organization_id = "ORG-A"
        resp = setup_client.get(
            "/api/integrations/erpnext/status",
            headers=_auth_headers("USER-1"),
        )
        assert resp.status_code == 200

    def test_manager_can_sync(self, setup_client):
        CONFIG.base_url = "http://test.erpnext.local"
        CONFIG.api_key = "test_key"
        CONFIG.api_secret = "test_secret"
        CONFIG.organization_id = "ORG-A"
        CONFIG.page_size = 50
        mock_resp = MockResponse(200, {"data": []})
        responses = {
            "/api/resource/Sales Order": mock_resp,
            "/api/resource/Customer": mock_resp,
            "/api/resource/Item": mock_resp,
        }
        mock_client = MockErpClient(responses=responses)
        erpnext_readonly._service = ERPReadSyncService(mock_client)
        try:
            resp = setup_client.post(
                "/api/integrations/erpnext/sync?mode=full",
                headers=_auth_headers("MANAGER-1"),
            )
            assert resp.status_code == 200
        finally:
            erpnext_readonly._service = ERPReadSyncService()


# ─── Test: PG Runtime Behavior ──────────────────────────────────────────────

class TestPGRuntimeBehavior:

    def test_pg_missing_tables_raises_not_creates(self):
        """PG: _ensure_erp_schema raises MIGRATION_REQUIRED, does NOT create tables."""
        from fastapi import HTTPException
        import erpnext_readonly

        class FakePGConn:
            is_pg = True
            def execute(self, *a, **kw):
                return self
            def fetchone(self):
                return None

        orig_table_exists = erpnext_readonly.table_exists
        erpnext_readonly.table_exists = lambda conn, name: False
        try:
            try:
                _ensure_erp_schema(FakePGConn())
                assert False, "Should have raised HTTPException"
            except HTTPException as exc:
                assert exc.status_code == 503
                assert "MIGRATION_REQUIRED" in str(exc.detail)
        finally:
            erpnext_readonly.table_exists = orig_table_exists


# ─── Test: Edge Cases ────────────────────────────────────────────────────────

class TestERPNextEdgeCases:

    def test_empty_response(self):
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {"data": []})
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["doctypes"]["Customer"]["records_seen"] == 0
        assert result["overall_status"] == "SUCCESS"

    def test_partial_failure(self):
        CONFIG.organization_id = "ORG-A"
        ok_resp = MockResponse(200, {"data": [{"name": "C1", "modified": "2026-08-11T10:00:00+08:00"}]})
        err_resp = MockResponse(500, text="Error")
        responses = {
            "/api/resource/Sales Order": ok_resp,
            "/api/resource/Customer": ok_resp,
            "/api/resource/Item": err_resp,
        }
        mock_client = MockErpClient(responses=responses)
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        result = service.full_sync("ORG-A", identity)
        assert result["overall_status"] == "PARTIAL_FAILURE"
        assert result["doctypes"]["Item"]["status"] == "FAILED"


# ─── Test: End-to-End Flow ───────────────────────────────────────────────────

class TestERPNextFullFlow:

    def test_full_then_incremental(self):
        CONFIG.organization_id = "ORG-A"
        resp = MockResponse(200, {
            "data": [{"name": "C1", "customer_name": "Alice", "modified": "2026-08-11T10:00:00+08:00"}],
        })
        mock_client = MockErpClient(responses={"/api/resource/Customer": resp})
        service = ERPReadSyncService(mock_client)
        identity = _make_identity()
        r1 = service.full_sync("ORG-A", identity)
        assert r1["overall_status"] == "SUCCESS"
        r2 = service.incremental_sync("ORG-A", identity)
        assert r2["mode"] == "incremental"
        assert r2["overall_status"] == "SUCCESS"

    def test_status_endpoint_no_secret(self, setup_client):
        CONFIG.base_url = "http://test.erpnext.local"
        CONFIG.api_key = "test_key_abcde"
        CONFIG.api_secret = "super-secret-value"
        CONFIG.organization_id = "ORG-A"
        resp = setup_client.get(
            "/api/integrations/erpnext/status",
            headers=_auth_headers("MANAGER-1"),
        )
        body = resp.text
        assert "super-secret-value" not in body
        assert "api_secret" not in body
        assert "Authorization" not in body
        assert "token" not in body.lower()
