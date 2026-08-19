import importlib.util
import json
from pathlib import Path

import pytest

import d13_skill_runtime

MODULE_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "d13_model_selection_runner.py"
spec = importlib.util.spec_from_file_location("d13_model_selection_runner_retry_test", MODULE_PATH)
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(runner)


def _config(**overrides):
    base = {"retry_max_attempts": 3, "retry_json_format_max": 1}
    base.update(overrides)
    return base


def test_retry_recovers_timeout(monkeypatch):
    calls = {"n": 0}

    def fake_call(config, case):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("temporary")
        return {"decision": "NO_ACTION", "tool_calls": []}, 25, {}, "{}"

    monkeypatch.setattr(runner, "_call_once", fake_call)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    plan, latency, _, _, telemetry = runner._call_with_retry(_config(), {})
    assert plan["decision"] == "NO_ACTION"
    assert telemetry["attempt_count"] == 2
    assert telemetry["first_attempt_success"] is False
    assert telemetry["retry_recovered"] is True
    assert telemetry["attempts"][0]["error_kind"] == "PROVIDER_TRANSIENT"


def test_retry_recovers_single_json_format_failure(monkeypatch):
    calls = {"n": 0}

    def fake_call(config, case):
        calls["n"] += 1
        if calls["n"] == 1:
            raise json.JSONDecodeError("bad json", "", 0)
        return {"decision": "NO_ACTION", "tool_calls": []}, 10, {}, "{}"

    monkeypatch.setattr(runner, "_call_once", fake_call)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    _, _, _, _, telemetry = runner._call_with_retry(_config(), {})
    assert telemetry["attempt_count"] == 2
    assert telemetry["retry_recovered"] is True
    assert telemetry["attempts"][0]["error_kind"] == "MODEL_FORMAT_FAILURE"


def test_json_format_failure_only_gets_one_retry(monkeypatch):
    calls = {"n": 0}

    def fake_call(config, case):
        calls["n"] += 1
        raise json.JSONDecodeError("bad json", "", 0)

    monkeypatch.setattr(runner, "_call_once", fake_call)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    with pytest.raises(json.JSONDecodeError) as excinfo:
        runner._call_with_retry(_config(retry_max_attempts=4, retry_json_format_max=1), {})
    telemetry = getattr(excinfo.value, "d13_retry_telemetry")
    assert calls["n"] == 2
    assert telemetry["attempt_count"] == 2
    assert telemetry["retry_exhausted"] is True


def test_plan_policy_failure_is_not_retried(monkeypatch):
    calls = {"n": 0}

    def fake_call(config, case):
        calls["n"] += 1
        raise d13_skill_runtime.D13PlanError("forbidden tool requested")

    monkeypatch.setattr(runner, "_call_once", fake_call)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    with pytest.raises(d13_skill_runtime.D13PlanError) as excinfo:
        runner._call_with_retry(_config(), {})
    telemetry = getattr(excinfo.value, "d13_retry_telemetry")
    assert calls["n"] == 1
    assert telemetry["attempts"][0]["error_kind"] == "NON_RETRYABLE"
    assert telemetry["retry_exhausted"] is False


def test_http_429_retries_but_401_does_not(monkeypatch):
    calls = {"n": 0}

    def fake_429(config, case):
        calls["n"] += 1
        if calls["n"] == 1:
            raise runner.ProviderHTTPError(429, "rate limited")
        return {"decision": "NO_ACTION", "tool_calls": []}, 5, {}, "{}"

    monkeypatch.setattr(runner, "_call_once", fake_429)
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    _, _, _, _, telemetry = runner._call_with_retry(_config(), {})
    assert telemetry["attempt_count"] == 2

    calls["n"] = 0
    def fake_401(config, case):
        calls["n"] += 1
        raise runner.ProviderHTTPError(401, "bad key")

    monkeypatch.setattr(runner, "_call_once", fake_401)
    with pytest.raises(runner.ProviderHTTPError) as excinfo:
        runner._call_with_retry(_config(), {})
    telemetry = getattr(excinfo.value, "d13_retry_telemetry")
    assert calls["n"] == 1
    assert telemetry["attempts"][0]["error_kind"] == "PROVIDER_PERMANENT"
