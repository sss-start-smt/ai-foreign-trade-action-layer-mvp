from __future__ import annotations

import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import d13_model_provider as provider

BASE = {
    "trigger_type": "USER_REQUEST",
    "current_datetime": "2026-08-17T12:00:00+08:00",
    "timezone": "Asia/Shanghai",
    "authenticated_scope": {
        "organization_id": "ORG-A",
        "user_id": "OPERATOR-A1",
        "role": "operator",
    },
    "trusted_context": {"active_context": {"order_no": "PO-1001"}},
    "goal": "查看这个订单",
    "observations": [],
}


def endpoint(model: str) -> provider.ModelEndpoint:
    return provider.ModelEndpoint(
        provider="TEST",
        model=model,
        api_base="https://example.invalid/v1",
        api_key_env="NO_KEY",
        timeout_seconds=45,
        max_attempts=2,
        json_format_retries=1,
        input_cny_per_million=8.0 if model == "glm-5.2" else 12.0,
        output_cny_per_million=28.0 if model == "glm-5.2" else 36.0,
    )


def valid(*, latency: int, prompt: int = 100, completion: int = 10):
    return {
        "choices": [{
            "message": {
                "content": (
                    '{"decision":"NO_ACTION","tool_calls":[],'
                    '"clarification_question":null,"response_draft":"无需动作",'
                    '"evidence_refs":[]}'
                )
            }
        }],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
        "_d13_latency_ms": latency,
    }


def run():
    rows = []

    calls = []
    def timeout_then_fallback(ep, messages):
        calls.append(ep.model)
        if ep.model == "glm-5.2":
            raise provider.D13ModelProviderError(
                "full request timeout",
                error_kind="PROVIDER_TIMEOUT",
                latency_ms=45000,
            )
        return valid(latency=12000, prompt=120, completion=12)

    r1 = provider.plan_next_turn(
        **BASE,
        primary=endpoint("glm-5.2"),
        fallback=endpoint("qwen3.8-max"),
        transport=timeout_then_fallback,
    )
    rows.append({
        "case": "FULL_TIMEOUT_DIRECT_FALLBACK",
        "pass": calls == ["glm-5.2", "qwen3.8-max"] and r1.route == "FALLBACK",
        "calls": calls,
        "route": r1.route,
        "attempts": list(r1.attempts),
        "simulated_latency_ms": sum(int(x.get("latency_ms") or 0) for x in r1.attempts),
    })

    calls = []
    n = {"v": 0}
    def quick_transient_then_primary(ep, messages):
        calls.append(ep.model)
        n["v"] += 1
        if n["v"] == 1:
            raise provider.D13ModelProviderError(
                "connection reset",
                error_kind="PROVIDER_TRANSIENT",
                latency_ms=100,
            )
        return valid(latency=8000)

    r2 = provider.plan_next_turn(
        **BASE,
        primary=endpoint("glm-5.2"),
        fallback=endpoint("qwen3.8-max"),
        transport=quick_transient_then_primary,
    )
    rows.append({
        "case": "QUICK_TRANSIENT_RETRY_PRIMARY",
        "pass": calls == ["glm-5.2", "glm-5.2"] and r2.route == "PRIMARY",
        "calls": calls,
        "route": r2.route,
        "attempts": list(r2.attempts),
    })

    calls = []
    def preferred_fallback_healthy(ep, messages):
        calls.append(ep.model)
        return valid(latency=10000)
    r3 = provider.plan_next_turn(
        **BASE,
        primary=endpoint("glm-5.2"),
        fallback=endpoint("qwen3.8-max"),
        transport=preferred_fallback_healthy,
        prefer_fallback=True,
    )
    rows.append({
        "case": "PREFERRED_FALLBACK_FIRST_WHEN_HEALTHY",
        "pass": calls == ["qwen3.8-max"] and r3.route == "FALLBACK",
        "calls": calls,
        "route": r3.route,
        "attempts": list(r3.attempts),
    })

    # Reproduce the R8.5 live-smoke failure shape for a later planning turn:
    # fallback was preferred because it recovered the prior turn, then fallback
    # itself fully timed out. V3 must give the primary one rescue opportunity
    # instead of failing the whole run immediately.
    calls = []
    def preferred_fallback_timeout_primary_rescue(ep, messages):
        calls.append(ep.model)
        if ep.model == "qwen3.8-max":
            raise provider.D13ModelProviderError(
                "fallback full timeout",
                error_kind="PROVIDER_TIMEOUT",
                latency_ms=45000,
            )
        return valid(latency=7000)
    r4 = provider.plan_next_turn(
        **BASE,
        primary=endpoint("glm-5.2"),
        fallback=endpoint("qwen3.8-max"),
        transport=preferred_fallback_timeout_primary_rescue,
        prefer_fallback=True,
    )
    rows.append({
        "case": "PREFERRED_FALLBACK_TIMEOUT_PRIMARY_RESCUE",
        "pass": calls == ["qwen3.8-max", "glm-5.2"] and r4.route == "PRIMARY",
        "calls": calls,
        "route": r4.route,
        "attempts": list(r4.attempts),
        "simulated_latency_ms": sum(int(x.get("latency_ms") or 0) for x in r4.attempts),
    })

    calls = []
    def semantic_invalid(ep, messages):
        calls.append(ep.model)
        return {
            "choices": [{"message": {"content": (
                '{"decision":"TOOL_CALLS","tool_calls":[{"tool_name":"erp_write_direct",'
                '"task_id":null,"payload":{},"evidence_refs":[]}],'
                '"clarification_question":null,"response_draft":null,"evidence_refs":[]}'
            )}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
            "_d13_latency_ms": 10,
        }
    semantic_pass = False
    semantic_error_kind = None
    try:
        provider.plan_next_turn(
            **BASE,
            primary=endpoint("glm-5.2"),
            fallback=endpoint("qwen3.8-max"),
            transport=semantic_invalid,
        )
    except provider.D13ModelUnavailable as exc:
        semantic_error_kind = exc.error_kind
        semantic_pass = calls == ["glm-5.2"] and exc.error_kind == "MODEL_PLAN_INVALID"
    rows.append({
        "case": "SEMANTIC_ERROR_NEVER_RETRY_OR_FALLBACK",
        "pass": semantic_pass,
        "calls": calls,
        "error_kind": semantic_error_kind,
    })

    status = "PASS" if all(x["pass"] for x in rows) else "FAIL"
    result = {
        "status": status,
        "policy": {
            "full_timeout_same_model_retry": False,
            "quick_transient_same_model_retry": True,
            "json_format_retry_max": 1,
            "preferred_route_follows_last_success": True,
            "preferred_route_cross_model_rescue": True,
            "fallback_on_semantic_error": False,
        },
        "cases": rows,
    }
    out = Path(__file__).with_name("D13_RETRY_FALLBACK_RESILIENCE_ATTACK.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    print("status=" + status)
    return 0 if status == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(run())
