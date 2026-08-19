"""D13 production model provider adapter with retry + cross-model fallback.

The adapter is deliberately narrower than the Agent Runtime:
- it only turns trusted runtime context into a validated D13 model plan;
- transient provider/format failures may retry;
- semantic/tool-policy validation failures do NOT retry or fallback;
- every attempt exposes auditable latency/token metadata, never hidden reasoning.

Default V1 routing (2026-08-17 model-selection decision):
    Primary  : glm-5.2
    Fallback : qwen3.8-max
Both currently use Alibaba Cloud Model Studio's OpenAI-compatible endpoint.
The cross-model fallback therefore protects model-level instability, not a
whole-provider outage. A future cross-provider fallback requires a separately
qualified provider/model pair.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

import d13_agent_skill as d13
from d13_skill_runtime import RuntimeContext, compile_skill_instruction, validate_model_plan

RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}
DEFAULT_API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_PRIMARY_MODEL = "glm-5.2"
DEFAULT_FALLBACK_MODEL = "qwen3.8-max"
D13_MODEL_ROUTING_VERSION = "D13_MODEL_ROUTING_V3"

# The retry duel showed GLM's normal P95 at about 36s while repeated full
# 45-second timeouts could make one business Run exceed 190s. Production
# therefore distinguishes a *full request timeout* from quick transient
# failures: a full timeout goes directly to fallback instead of waiting for the
# same model to time out again. Quick 429/5xx/connection failures and one JSON
# format failure may still retry once. These are routing controls, not business
# capability limits.
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_JSON_FORMAT_RETRIES = 1


class D13ModelProviderError(RuntimeError):
    """Base provider error with safe attempt telemetry."""

    def __init__(
        self,
        message: str,
        *,
        error_kind: str,
        usage: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        http_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_kind = error_kind
        self.usage = dict(usage or {})
        self.latency_ms = latency_ms
        self.http_code = http_code


class D13ModelUnavailable(D13ModelProviderError):
    def __init__(self, message: str, *, attempts: list[dict[str, Any]], error_kind: str) -> None:
        super().__init__(message, error_kind=error_kind)
        self.attempts = attempts


@dataclass(frozen=True)
class ModelEndpoint:
    provider: str
    model: str
    api_base: str = DEFAULT_API_BASE
    api_key_env: str = "DASHSCOPE_API_KEY"
    temperature: float = 0.0
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    json_format_retries: int = DEFAULT_JSON_FORMAT_RETRIES
    # Optional pricing snapshot, used only to estimate telemetry cost. These do
    # not affect routing or safety decisions.
    input_cny_per_million: float | None = None
    output_cny_per_million: float | None = None

    @property
    def endpoint(self) -> str:
        return self.api_base.rstrip("/") + "/chat/completions"


@dataclass(frozen=True)
class PlanResult:
    plan: dict[str, Any]
    provider: str
    model: str
    route: str
    attempts: tuple[dict[str, Any], ...]


def default_model_chain() -> tuple[ModelEndpoint, ModelEndpoint]:
    timeout = max(1, int(os.getenv("D13_MODEL_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS))))
    max_attempts = max(1, int(os.getenv("D13_MODEL_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS))))
    api_base = os.getenv("D13_DASHSCOPE_API_BASE", DEFAULT_API_BASE).strip() or DEFAULT_API_BASE
    key_env = os.getenv("D13_MODEL_API_KEY_ENV", "DASHSCOPE_API_KEY").strip() or "DASHSCOPE_API_KEY"
    primary = ModelEndpoint(
        provider="AlibabaCloudModelStudio",
        model=os.getenv("D13_PRIMARY_MODEL", DEFAULT_PRIMARY_MODEL).strip() or DEFAULT_PRIMARY_MODEL,
        api_base=api_base,
        api_key_env=key_env,
        timeout_seconds=timeout,
        max_attempts=max_attempts,
        json_format_retries=DEFAULT_JSON_FORMAT_RETRIES,
        input_cny_per_million=8.0,
        output_cny_per_million=28.0,
    )
    fallback = ModelEndpoint(
        provider="AlibabaCloudModelStudio",
        model=os.getenv("D13_FALLBACK_MODEL", DEFAULT_FALLBACK_MODEL).strip() or DEFAULT_FALLBACK_MODEL,
        api_base=api_base,
        api_key_env=key_env,
        timeout_seconds=timeout,
        max_attempts=max_attempts,
        json_format_retries=DEFAULT_JSON_FORMAT_RETRIES,
        input_cny_per_million=12.0,
        output_cny_per_million=36.0,
    )
    return primary, fallback


def _extract_json(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
        if raw.lower().startswith("json\n"):
            raw = raw[5:]
    return json.loads(raw)


def _usage_cost_cny(usage: dict[str, Any], endpoint: ModelEndpoint) -> float | None:
    if endpoint.input_cny_per_million is None or endpoint.output_cny_per_million is None:
        return None
    # A timeout / disconnected request often has no provider usage payload.
    # Treat that as unknown, not zero: the provider may still have consumed
    # billable tokens even though the client never received usage metadata.
    if not usage or (
        usage.get("prompt_tokens") is None
        and usage.get("completion_tokens") is None
        and usage.get("total_tokens") is None
    ):
        return None
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    return (
        prompt_tokens * endpoint.input_cny_per_million
        + completion_tokens * endpoint.output_cny_per_million
    ) / 1_000_000.0


def _output_schema_instruction() -> str:
    return (
        "只返回JSON对象，不要markdown。结构："
        '{"decision":"TOOL_CALLS|RESPOND_ONLY|DRAFT_ONLY|CLARIFY|NO_ACTION|REFUSE",'
        '"tool_calls":[{"tool_name":"...","task_id":"可空","payload":{},"evidence_refs":[]}],'
        '"clarification_question":null,"response_draft":null,"evidence_refs":[]}。'
        "RESPOND_ONLY表示已有足够可信上下文，可直接回答且不需要Tool。"
        "不要输出organization_id、role、manager_id、approve、required_review、target_id。"
    )


def build_messages(
    *,
    trigger_type: str,
    current_datetime: str,
    timezone: str,
    authenticated_scope: dict[str, Any],
    trusted_context: dict[str, Any],
    goal: str,
    observations: list[dict[str, Any]],
) -> list[dict[str, str]]:
    runtime_context = RuntimeContext(
        trigger_type=trigger_type,
        current_datetime=current_datetime,
        timezone=timezone,
        context_refs=tuple(),
    )
    system = (
        compile_skill_instruction(runtime_context)
        + "\n以下Tool Manifest是服务器权威合同：\n"
        + json.dumps(d13.tool_manifest(), ensure_ascii=False, sort_keys=True)
        + "\n"
        + _output_schema_instruction()
    )
    envelope = {
        "trigger_type": trigger_type,
        "current_datetime": current_datetime,
        "timezone": timezone,
        "authenticated_scope": authenticated_scope,
        "context": trusted_context,
        "tool_observations": observations,
    }
    user = (
        "[FLOWORDER_TRUSTED_RUNTIME_CONTEXT_BEGIN]\n"
        + json.dumps(envelope, ensure_ascii=False, sort_keys=True, default=str)
        + "\n[FLOWORDER_TRUSTED_RUNTIME_CONTEXT_END]\n\n"
        + "[USER_GOAL_BEGIN]\n"
        + str(goal or "")
        + "\n[USER_GOAL_END]"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _http_transport(endpoint: ModelEndpoint, messages: list[dict[str, str]]) -> dict[str, Any]:
    api_key = os.getenv(endpoint.api_key_env, "").strip()
    if not api_key:
        raise D13ModelProviderError(
            f"missing API key env: {endpoint.api_key_env}",
            error_kind="PROVIDER_PERMANENT",
        )
    body = {
        "model": endpoint.model,
        "temperature": endpoint.temperature,
        "messages": messages,
    }
    req = urllib.request.Request(
        endpoint.endpoint,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=endpoint.timeout_seconds) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = exc.read().decode("utf-8", errors="replace")
        kind = ("PROVIDER_TIMEOUT" if exc.code == 408 else ("PROVIDER_TRANSIENT" if exc.code in RETRYABLE_HTTP_CODES else "PROVIDER_PERMANENT"))
        raise D13ModelProviderError(
            f"HTTP {exc.code}: {detail[:1000]}",
            error_kind=kind,
            latency_ms=latency_ms,
            http_code=exc.code,
        ) from exc
    except (TimeoutError, socket.timeout) as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        raise D13ModelProviderError(
            f"{type(exc).__name__}: {exc}",
            error_kind="PROVIDER_TIMEOUT",
            latency_ms=latency_ms,
        ) from exc
    except (ConnectionResetError, ConnectionAbortedError,
            http.client.RemoteDisconnected, urllib.error.URLError) as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        raise D13ModelProviderError(
            f"{type(exc).__name__}: {exc}",
            error_kind="PROVIDER_TRANSIENT",
            latency_ms=latency_ms,
        ) from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    raw["_d13_latency_ms"] = latency_ms
    return raw


def _attempt_once(
    endpoint: ModelEndpoint,
    messages: list[dict[str, str]],
    *,
    transport: Callable[[ModelEndpoint, list[dict[str, str]]], dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    raw = transport(endpoint, messages)
    latency_ms = int(raw.get("_d13_latency_ms") or 0)
    usage = dict(raw.get("usage") or {})
    try:
        content = raw["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise D13ModelProviderError(
            "provider response missing choices[0].message.content",
            error_kind="MODEL_FORMAT_FAILURE",
            usage=usage,
            latency_ms=latency_ms,
        ) from exc
    try:
        parsed = _extract_json(content)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise D13ModelProviderError(
            f"invalid model JSON: {exc}",
            error_kind="MODEL_FORMAT_FAILURE",
            usage=usage,
            latency_ms=latency_ms,
        ) from exc
    try:
        validate_model_plan(parsed)
        plan = parsed
    except Exception as exc:
        # Valid response but invalid D13 plan = semantic/policy contract failure.
        # Do not retry/fallback to avoid "sampling until safe" evaluation/runtime.
        raise D13ModelProviderError(
            f"invalid D13 plan: {exc}",
            error_kind="MODEL_PLAN_INVALID",
            usage=usage,
            latency_ms=latency_ms,
        ) from exc
    attempt = {
        "success": True,
        "error_kind": None,
        "error": None,
        "latency_ms": latency_ms,
        "usage": usage,
        "estimated_cost_cny": _usage_cost_cny(usage, endpoint),
    }
    return plan, attempt


def _run_endpoint(
    endpoint: ModelEndpoint,
    messages: list[dict[str, str]],
    *,
    route: str,
    transport: Callable[[ModelEndpoint, list[dict[str, str]]], dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]] | tuple[None, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    format_retries_used = 0
    for attempt_no in range(1, max(1, endpoint.max_attempts) + 1):
        try:
            plan, item = _attempt_once(endpoint, messages, transport=transport)
            item.update({
                "attempt": attempt_no,
                "route": route,
                "provider": endpoint.provider,
                "model": endpoint.model,
            })
            attempts.append(item)
            return plan, attempts
        except D13ModelProviderError as exc:
            item = {
                "attempt": attempt_no,
                "route": route,
                "provider": endpoint.provider,
                "model": endpoint.model,
                "success": False,
                "error_kind": exc.error_kind,
                "error": str(exc),
                "latency_ms": exc.latency_ms,
                "usage": dict(exc.usage or {}),
                "estimated_cost_cny": _usage_cost_cny(exc.usage, endpoint),
            }
            attempts.append(item)
            if exc.error_kind in {"MODEL_PLAN_INVALID", "PROVIDER_PERMANENT"}:
                raise D13ModelUnavailable(
                    str(exc), attempts=attempts, error_kind=exc.error_kind
                ) from exc
            retry = False
            if attempt_no < endpoint.max_attempts and exc.error_kind == "PROVIDER_TRANSIENT":
                retry = True
            elif (
                attempt_no < endpoint.max_attempts
                and exc.error_kind == "MODEL_FORMAT_FAILURE"
                and format_retries_used < endpoint.json_format_retries
            ):
                format_retries_used += 1
                retry = True
            if not retry:
                return None, attempts
            time.sleep(min(1.0, 0.25 * (2 ** (attempt_no - 1))))
    return None, attempts


def plan_next_turn(
    *,
    trigger_type: str,
    current_datetime: str,
    timezone: str,
    authenticated_scope: dict[str, Any],
    trusted_context: dict[str, Any],
    goal: str,
    observations: list[dict[str, Any]],
    primary: ModelEndpoint | None = None,
    fallback: ModelEndpoint | None = None,
    transport: Callable[[ModelEndpoint, list[dict[str, str]]], dict[str, Any]] | None = None,
    prefer_fallback: bool = False,
) -> PlanResult:
    primary_default, fallback_default = default_model_chain()
    primary = primary or primary_default
    fallback = fallback or fallback_default
    transport = transport or _http_transport
    messages = build_messages(
        trigger_type=trigger_type,
        current_datetime=current_datetime,
        timezone=timezone,
        authenticated_scope=authenticated_scope,
        trusted_context=trusted_context,
        goal=goal,
        observations=observations,
    )

    # R8.6: "sticky fallback" became a single point of failure in live smoke:
    # primary timed out -> fallback recovered Turn 1 -> fallback timed out on
    # Turn 2 -> the whole Agent Run failed without giving the primary a chance
    # to recover.  Keep the successful model as the *preferred first route*,
    # but allow one cross-model rescue when that preferred route later has an
    # availability/format failure. Semantic/policy failures still raise inside
    # _run_endpoint and NEVER cross-model rescue.
    if prefer_fallback:
        plan, preferred_attempts = _run_endpoint(
            fallback, messages, route="FALLBACK", transport=transport
        )
        all_attempts = list(preferred_attempts)
        if plan is not None:
            return PlanResult(
                plan=plan,
                provider=fallback.provider,
                model=fallback.model,
                route="FALLBACK",
                attempts=tuple(all_attempts),
            )

        # Preferred fallback had an availability/format failure. Fail back once
        # to the primary for this planning turn. A full timeout is not retried
        # on the same model, so this does not reintroduce duplicate 45s waits.
        plan, rescue_attempts = _run_endpoint(
            primary, messages, route="PRIMARY", transport=transport
        )
        all_attempts.extend(rescue_attempts)
        if plan is not None:
            return PlanResult(
                plan=plan,
                provider=primary.provider,
                model=primary.model,
                route="PRIMARY",
                attempts=tuple(all_attempts),
            )
        error_kind = all_attempts[-1].get("error_kind") if all_attempts else "PROVIDER_TRANSIENT"
        raise D13ModelUnavailable(
            "preferred fallback and primary rescue did not produce a valid plan",
            attempts=all_attempts,
            error_kind=str(error_kind or "PROVIDER_TRANSIENT"),
        )

    plan, primary_attempts = _run_endpoint(primary, messages, route="PRIMARY", transport=transport)
    all_attempts = list(primary_attempts)
    if plan is not None:
        return PlanResult(
            plan=plan,
            provider=primary.provider,
            model=primary.model,
            route="PRIMARY",
            attempts=tuple(all_attempts),
        )

    # Only retry-exhausted transient/format failures reach here. Semantic plan
    # failures and permanent provider/auth failures raise above and never fall back.
    plan, fallback_attempts = _run_endpoint(fallback, messages, route="FALLBACK", transport=transport)
    all_attempts.extend(fallback_attempts)
    if plan is not None:
        return PlanResult(
            plan=plan,
            provider=fallback.provider,
            model=fallback.model,
            route="FALLBACK",
            attempts=tuple(all_attempts),
        )
    error_kind = all_attempts[-1].get("error_kind") if all_attempts else "PROVIDER_TRANSIENT"
    raise D13ModelUnavailable(
        "primary retry exhausted and fallback did not produce a valid plan",
        attempts=all_attempts,
        error_kind=str(error_kind or "PROVIDER_TRANSIENT"),
    )
