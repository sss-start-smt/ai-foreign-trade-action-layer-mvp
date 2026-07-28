
import json

import coze_integration as ci


class FakeResponse:
    def __init__(self, *, code=0, msg="Success", status_code=200):
        self.status_code = status_code
        self._code = code
        self._msg = msg

    def json(self):
        if self._code == 0:
            return {
                "code": 0,
                "msg": "Success",
                "data": json.dumps({
                    "result_json": json.dumps({
                        "run_status": "candidate_ready",
                        "fields": []
                    })
                }),
                "debug_url": "https://www.coze.cn/work_flow?execute_id=debug",
            }
        return {
            "code": self._code,
            "msg": self._msg,
            "data": "",
            "debug_url": None,
        }


class ObjectSuccessClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        self.__class__.calls.append(json)
        assert isinstance(json["parameters"], dict)
        return FakeResponse()


class ObjectRejectStringSuccessClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, headers=None, json=None):
        self.__class__.calls.append(json)
        if isinstance(json["parameters"], dict):
            return FakeResponse(code=4000, msg="Invalid request parameters.")
        assert isinstance(json["parameters"], str)
        return FakeResponse()



def fake_run(key):
    return ci.WorkflowRun(
        run_id=f"TEST-{key.upper()}",
        workflow_key=key,
        workflow_id=ci.workflow_id(key),
        parameters={},
        result={},
        raw_data={},
        debug_url=None,
        duration_ms=1,
        envelope={"code": 0},
    )

def test_run_workflow_uses_object_parameters_first(monkeypatch):
    monkeypatch.setenv("COZE_API_TOKEN", "test-token")
    monkeypatch.delenv("COZE_PARAMETERS_MODE", raising=False)
    ObjectSuccessClient.calls = []
    monkeypatch.setattr(ci.httpx, "Client", ObjectSuccessClient)

    result = ci.run_workflow("ft01", {
        "raw_content": "PO-1001",
        "source_channel": "email",
        "input_type": "text",
        "file_url": "",
    })

    assert result.result["run_status"] == "candidate_ready"
    sent = ObjectSuccessClient.calls[0]["parameters"]
    assert sent["raw_content"] == "PO-1001"
    assert "file_url" not in sent


def test_run_workflow_retries_legacy_string_mode(monkeypatch):
    monkeypatch.setenv("COZE_API_TOKEN", "test-token")
    monkeypatch.delenv("COZE_PARAMETERS_MODE", raising=False)
    ObjectRejectStringSuccessClient.calls = []
    monkeypatch.setattr(ci.httpx, "Client", ObjectRejectStringSuccessClient)

    result = ci.run_workflow("ft02", {
        "message_content": "差不多七成",
        "source_channel": "wechat",
        "sender_role": "factory",
        "order_context": '{"order_id":"ORD-1001"}',
    })

    assert result.result["run_status"] == "candidate_ready"
    assert len(ObjectRejectStringSuccessClient.calls) == 2
    assert isinstance(ObjectRejectStringSuccessClient.calls[0]["parameters"], dict)
    assert isinstance(ObjectRejectStringSuccessClient.calls[1]["parameters"], str)


def test_normalize_ft01_for_review():
    result = {
        "order_match": {"status": "unique_match", "selected_order_id": "ORD-1001"},
        "fields": [{"field_name": "packaging_method", "normalized_value": "彩盒"}],
        "risk_signals": [],
        "action_candidates": [],
    }
    candidate = ci.normalize_ft01(result, order={"order_id": "ORD-1001", "order_no": "PO-1001"}, run=fake_run("ft01"))
    assert candidate["order_match"]["selected_order_id"] == "ORD-1001"


def test_normalize_ft02_for_review():
    result = {
        "progress": {"percentage": 0.7},
        "supplier_commitment": {"latest_date": "2026-07-29"},
        "risk_signals": [{"type": "commitment_uncertain"}],
        "action_candidates": [{"action_type": "confirm_commitment"}],
    }
    candidate = ci.normalize_ft02(
        result,
        order={"order_id": "ORD-1001", "order_no": "PO-1001"},
        task=None,
        run=fake_run("ft02"),
    )
    names = {x["field_name"] for x in candidate["fields"]}
    assert "current_progress" in names
    assert "latest_supplier_commitment" in names
