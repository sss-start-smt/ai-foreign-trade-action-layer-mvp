import json

import coze_integration as ci


class FakeResponse:
    status_code = 200

    def json(self):
        return {
            "code": 0,
            "msg": "Success",
            "data": json.dumps({"result_json": json.dumps({"run_status": "candidate_ready", "fields": []})}),
            "debug_url": "https://www.coze.cn/work_flow?execute_id=debug",
        }


class FakeClient:
    last_json = None

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        self.__class__.last_json = json
        assert url == "https://api.coze.cn/v1/workflow/run"
        assert headers["Authorization"] == "Bearer test-token"
        return FakeResponse()


def test_run_workflow_parses_nested_result(monkeypatch):
    monkeypatch.setenv("COZE_API_TOKEN", "test-token")
    monkeypatch.setattr(ci.httpx, "Client", FakeClient)
    result = ci.run_workflow("ft01", {"raw_content": "PO-1001"})
    assert result.result["run_status"] == "candidate_ready"
    assert result.debug_url
    assert FakeClient.last_json["workflow_id"] == ci.DEFAULT_WORKFLOW_IDS["ft01"]
    assert json.loads(FakeClient.last_json["parameters"])["raw_content"] == "PO-1001"


def test_normalize_ft02_for_review():
    run = ci.WorkflowRun(
        run_id="RUN-1", workflow_key="ft02", workflow_id="2", parameters={}, raw_data={},
        debug_url=None, duration_ms=10, envelope={},
        result={
            "schema_version": "4.2",
            "progress": {"percentage": 0.7, "raw_expression": "差不多七成"},
            "supplier_commitment": {"latest_date": "2026-07-29", "raw_expression": "应该下周三完成"},
            "risk_signals": [{"type": "commitment_uncertain", "evidence": "应该下周三完成"}],
            "action_candidates": [{"action_type": "ask_for_remedy"}],
        },
    )
    candidate = ci.normalize_ft02(
        run.result,
        order={"order_id": "ORD-1001", "order_no": "PO-1001", "current_progress": 0.55},
        task={"task_id": "TASK-1"},
        run=run,
    )
    assert candidate["order_match"]["status"] == "unique_match"
    assert any(x["field_name"] == "current_progress" and x["normalized_value"] == 0.7 for x in candidate["fields"])
    assert candidate["action_candidates"][0]["recommended_action"]
    assert candidate["_integration"]["task_id"] == "TASK-1"


def test_confirmed_payload_keeps_customer_delivery_separate():
    payload = ci.confirmed_payload({
        "order_match": {"status": "unique_match", "selected_order_id": "ORD-1001"},
        "fields": [
            {"field_name": "latest_supplier_commitment", "normalized_value": "2026-07-29", "source_quote": "应该下周三完成"},
        ],
        "risk_signals": [{"type": "commitment_uncertain", "evidence": "应该下周三完成"}],
        "action_candidates": [{"action_type": "confirm_commitment"}],
    })
    assert payload["order_changes"][0]["field_name"] == "latest_supplier_commitment"
    assert all(x["field_name"] != "requested_delivery_date" for x in payload["order_changes"])
    assert payload["action_decision"]["confirmation_status"] == "confirmed"
