from __future__ import annotations

import pytest

import d13_model_provider as provider


BASE_KWARGS = {
    "trigger_type": "USER_REQUEST",
    "current_datetime": "2026-08-17T12:00:00+08:00",
    "timezone": "Asia/Shanghai",
    "authenticated_scope": {"organization_id": "ORG-A", "user_id": "OPERATOR-A1", "role": "operator"},
    "trusted_context": {"active_context": {"order_no": "PO-1001"}},
    "goal": "查看这个订单",
    "observations": [],
}


def endpoint(model: str, *, attempts: int = 2) -> provider.ModelEndpoint:
    return provider.ModelEndpoint(
        provider="TEST",
        model=model,
        api_base="https://example.invalid/v1",
        api_key_env="NO_KEY_NEEDED_IN_FAKE",
        timeout_seconds=1,
        max_attempts=attempts,
        json_format_retries=1,
        input_cny_per_million=8.0 if model == "glm-5.2" else 12.0,
        output_cny_per_million=28.0 if model == "glm-5.2" else 36.0,
    )


def valid_response(decision="NO_ACTION", *, latency=10, prompt_tokens=100, completion_tokens=10):
    if decision == "NO_ACTION":
        content = '{"decision":"NO_ACTION","tool_calls":[],"clarification_question":null,"response_draft":"无需动作","evidence_refs":[]}'
    else:
        raise AssertionError("unsupported test response")
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens},
        "_d13_latency_ms": latency,
    }


def test_same_model_retry_recovers_before_fallback():
    calls = []

    def fake_transport(ep, messages):
        calls.append(ep.model)
        if len(calls) == 1:
            raise provider.D13ModelProviderError("timeout", error_kind="PROVIDER_TRANSIENT", latency_ms=100)
        return valid_response(latency=12)

    result = provider.plan_next_turn(
        **BASE_KWARGS,
        primary=endpoint("glm-5.2"),
        fallback=endpoint("qwen3.8-max"),
        transport=fake_transport,
    )
    assert result.route == "PRIMARY"
    assert calls == ["glm-5.2", "glm-5.2"]
    assert len(result.attempts) == 2
    assert result.attempts[0]["success"] is False
    assert result.attempts[1]["success"] is True


def test_retry_exhaustion_uses_cross_model_fallback():
    calls = []

    def fake_transport(ep, messages):
        calls.append(ep.model)
        if ep.model == "glm-5.2":
            raise provider.D13ModelProviderError("timeout", error_kind="PROVIDER_TRANSIENT", latency_ms=100)
        return valid_response(latency=15)

    result = provider.plan_next_turn(
        **BASE_KWARGS,
        primary=endpoint("glm-5.2"),
        fallback=endpoint("qwen3.8-max"),
        transport=fake_transport,
    )
    assert result.route == "FALLBACK"
    assert result.model == "qwen3.8-max"
    assert calls == ["glm-5.2", "glm-5.2", "qwen3.8-max"]
    assert [x["route"] for x in result.attempts] == ["PRIMARY", "PRIMARY", "FALLBACK"]


def test_invalid_business_plan_never_retries_or_falls_back():
    calls = []

    def fake_transport(ep, messages):
        calls.append(ep.model)
        # Valid JSON but invalid D13 plan: forbidden direct ERP tool.
        return {
            "choices": [{"message": {"content": '{"decision":"TOOL_CALLS","tool_calls":[{"tool_name":"erp_write_direct","payload":{}}],"clarification_question":null,"response_draft":null,"evidence_refs":[]}'}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            "_d13_latency_ms": 10,
        }

    with pytest.raises(provider.D13ModelUnavailable) as exc:
        provider.plan_next_turn(
            **BASE_KWARGS,
            primary=endpoint("glm-5.2"),
            fallback=endpoint("qwen3.8-max"),
            transport=fake_transport,
        )
    assert exc.value.error_kind == "MODEL_PLAN_INVALID"
    assert calls == ["glm-5.2"]
    assert exc.value.attempts[0]["usage"]["prompt_tokens"] == 100


def test_format_retry_preserves_failed_attempt_token_cost():
    calls = []

    def fake_transport(ep, messages):
        calls.append(ep.model)
        if len(calls) == 1:
            return {
                "choices": [{"message": {"content": "not-json"}}],
                "usage": {"prompt_tokens": 200, "completion_tokens": 30, "total_tokens": 230},
                "_d13_latency_ms": 9,
            }
        return valid_response(latency=8, prompt_tokens=210, completion_tokens=15)

    result = provider.plan_next_turn(
        **BASE_KWARGS,
        primary=endpoint("glm-5.2"),
        fallback=endpoint("qwen3.8-max"),
        transport=fake_transport,
    )
    assert result.route == "PRIMARY"
    assert len(result.attempts) == 2
    first = result.attempts[0]
    assert first["error_kind"] == "MODEL_FORMAT_FAILURE"
    assert first["usage"]["prompt_tokens"] == 200
    assert first["usage"]["completion_tokens"] == 30
    assert first["estimated_cost_cny"] is not None


def test_full_request_timeout_skips_same_model_retry_and_goes_directly_to_fallback():
    calls = []

    def fake_transport(ep, messages):
        calls.append(ep.model)
        if ep.model == "glm-5.2":
            raise provider.D13ModelProviderError(
                "full request timeout",
                error_kind="PROVIDER_TIMEOUT",
                latency_ms=45000,
            )
        return valid_response(latency=17)

    result = provider.plan_next_turn(
        **BASE_KWARGS,
        primary=endpoint("glm-5.2"),
        fallback=endpoint("qwen3.8-max"),
        transport=fake_transport,
    )
    assert result.route == "FALLBACK"
    assert calls == ["glm-5.2", "qwen3.8-max"]
    assert len(result.attempts) == 2
    assert result.attempts[0]["error_kind"] == "PROVIDER_TIMEOUT"


def test_prefer_fallback_skips_primary_probe_for_next_planning_turn():
    calls = []

    def fake_transport(ep, messages):
        calls.append(ep.model)
        return valid_response(latency=11)

    result = provider.plan_next_turn(
        **BASE_KWARGS,
        primary=endpoint("glm-5.2"),
        fallback=endpoint("qwen3.8-max"),
        transport=fake_transport,
        prefer_fallback=True,
    )
    assert result.route == "FALLBACK"
    assert result.model == "qwen3.8-max"
    assert calls == ["qwen3.8-max"]


def test_preferred_fallback_timeout_can_fail_back_to_primary_once():
    calls = []

    def fake_transport(ep, messages):
        calls.append(ep.model)
        if ep.model == "qwen3.8-max":
            raise provider.D13ModelProviderError(
                "fallback full timeout",
                error_kind="PROVIDER_TIMEOUT",
                latency_ms=45000,
            )
        return valid_response(latency=7000)

    result = provider.plan_next_turn(
        **BASE_KWARGS,
        primary=endpoint("glm-5.2"),
        fallback=endpoint("qwen3.8-max"),
        transport=fake_transport,
        prefer_fallback=True,
    )
    assert result.route == "PRIMARY"
    assert result.model == "glm-5.2"
    assert calls == ["qwen3.8-max", "glm-5.2"]
    assert result.attempts[0]["error_kind"] == "PROVIDER_TIMEOUT"
    assert result.attempts[1]["success"] is True


def test_timeout_cost_is_unknown_not_zero_without_usage():
    ep = endpoint("glm-5.2")

    def fake_transport(endpoint, messages):
        raise provider.D13ModelProviderError(
            "timeout",
            error_kind="PROVIDER_TIMEOUT",
            latency_ms=45000,
        )

    # Primary timeout then fallback timeout -> unavailable; both attempts have
    # unknown usage/cost rather than a misleading zero-cost estimate.
    with pytest.raises(provider.D13ModelUnavailable) as exc:
        provider.plan_next_turn(
            **BASE_KWARGS,
            primary=ep,
            fallback=endpoint("qwen3.8-max"),
            transport=fake_transport,
        )
    assert len(exc.value.attempts) == 2
    assert all(x["estimated_cost_cny"] is None for x in exc.value.attempts)
