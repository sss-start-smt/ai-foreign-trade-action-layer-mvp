"""Provider-agnostic FlowOrder D13 formal model-selection runner.

The runner evaluates the *Primary Agent Execution Model* against one frozen
Skill/Tool contract. Cases include trusted Runtime Context; this avoids testing
which model is best at guessing missing order facts or the current date.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import http.client
import socket
from pathlib import Path
from typing import Any
import sys

# Allow this script to be invoked as `python evaluation/d13_model_selection_runner.py`
# from the FlowOrder source root. Python otherwise places only the evaluation/
# directory on sys.path, causing project modules such as d13_agent_skill to fail
# before any provider API call is made.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import d13_agent_skill as d13
from d13_skill_runtime import RuntimeContext, compile_skill_instruction, validate_model_plan


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


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


def _endpoint(config: dict[str, Any]) -> str:
    if config.get("endpoint"):
        return str(config["endpoint"]).rstrip("/")
    return str(config["api_base"]).rstrip("/") + "/chat/completions"


def _case_user_message(case: dict[str, Any]) -> str:
    runtime_envelope = {
        "trigger_type": case.get("trigger_type") or "USER_REQUEST",
        "current_datetime": case.get("current_datetime"),
        "timezone": case.get("timezone") or "Asia/Shanghai",
        "authenticated_scope": case.get("authenticated_scope") or {},
        "context": case.get("context") or {},
    }
    return (
        "[FLOWORDER_TRUSTED_RUNTIME_CONTEXT_BEGIN]\n"
        + json.dumps(runtime_envelope, ensure_ascii=False, sort_keys=True)
        + "\n[FLOWORDER_TRUSTED_RUNTIME_CONTEXT_END]\n\n"
        + "[USER_GOAL_BEGIN]\n"
        + str(case.get("prompt") or "")
        + "\n[USER_GOAL_END]"
    )


RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}


class ProviderHTTPError(RuntimeError):
    def __init__(self, code: int, detail: str, *, latency_ms: int | None = None):
        self.code = int(code)
        self.detail = str(detail)
        self.latency_ms = latency_ms
        self.usage: dict[str, Any] = {}
        super().__init__(f"HTTP {self.code}: {self.detail[:1000]}")


def _error_kind(exc: Exception) -> str:
    if isinstance(exc, json.JSONDecodeError):
        return "MODEL_FORMAT_FAILURE"
    if isinstance(exc, ProviderHTTPError):
        return "PROVIDER_TRANSIENT" if exc.code in RETRYABLE_HTTP_CODES else "PROVIDER_PERMANENT"
    if isinstance(exc, (TimeoutError, socket.timeout, ConnectionResetError, ConnectionAbortedError,
                        http.client.RemoteDisconnected, urllib.error.URLError)):
        return "PROVIDER_TRANSIENT"
    return "NON_RETRYABLE"


def _retry_delay_seconds(retry_index: int) -> float:
    # Diagnostic policy only: bounded deterministic backoff. Retry dependence is
    # recorded separately and remains part of the final model decision.
    return min(2.0, 0.5 * (2 ** max(0, retry_index - 1)))


def _attempt_cost_cny(config: dict[str, Any], usage: dict[str, Any]) -> float | None:
    input_rate = config.get("pricing_input_cny_per_million")
    output_rate = config.get("pricing_output_cny_per_million")
    if input_rate is None or output_rate is None:
        return None
    return (
        int(usage.get("prompt_tokens") or 0) * float(input_rate)
        + int(usage.get("completion_tokens") or 0) * float(output_rate)
    ) / 1_000_000.0


def _call_once(config: dict[str, Any], case: dict[str, Any]) -> tuple[dict[str, Any], int, dict[str, Any], str]:
    key_env = str(config.get("api_key_env") or "").strip()
    api_key = os.getenv(key_env, "").strip() if key_env else ""
    if not api_key:
        raise RuntimeError(f"missing API key env: {key_env}")

    runtime_context = RuntimeContext(
        trigger_type=str(case.get("trigger_type") or "USER_REQUEST"),
        current_datetime=case.get("current_datetime"),
        timezone=str(case.get("timezone") or "Asia/Shanghai"),
        context_refs=tuple(),
    )
    manifest = d13.tool_manifest()
    output_schema = (
        "只返回JSON对象，不要markdown。结构："
        '{"decision":"TOOL_CALLS|RESPOND_ONLY|DRAFT_ONLY|CLARIFY|NO_ACTION|REFUSE",'
        '"tool_calls":[{"tool_name":"...","task_id":"可空","payload":{},"evidence_refs":[]}],'
        '"clarification_question":null,"response_draft":null,"evidence_refs":[]}。'
        "RESPOND_ONLY表示已有足够可信上下文，可直接回答且不需要Tool。"
        "不要输出organization_id、role、manager_id、approve、required_review、target_id。"
    )
    system = (
        compile_skill_instruction(runtime_context)
        + "\n以下Tool Manifest是服务器权威合同：\n"
        + json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        + "\n"
        + output_schema
    )
    user_message = _case_user_message(case)
    body = {
        "model": config["model"],
        "temperature": config.get("temperature", 0),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_message},
        ],
    }
    if isinstance(config.get("extra_body"), dict):
        body.update(config["extra_body"])
    req = urllib.request.Request(
        _endpoint(config),
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=int(config.get("timeout_seconds", 60))) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        latency_ms = int((time.perf_counter() - started) * 1000)
        detail = exc.read().decode("utf-8", errors="replace")
        raise ProviderHTTPError(exc.code, detail, latency_ms=latency_ms) from exc
    latency_ms = int((time.perf_counter() - started) * 1000)
    usage = raw.get("usage") or {}
    try:
        content = raw["choices"][0]["message"]["content"]
        parsed = _extract_json(content)
        # D13PlanError / tool-policy validation failure is intentionally NOT retried.
        validated = validate_model_plan(parsed)
    except Exception as exc:
        setattr(exc, "d13_attempt_usage", dict(usage))
        setattr(exc, "d13_attempt_latency_ms", latency_ms)
        raise
    return validated, latency_ms, usage, content


def _call_with_retry(config: dict[str, Any], case: dict[str, Any]) -> tuple[dict[str, Any], int, dict[str, Any], str, dict[str, Any]]:
    """Call one model with bounded same-model retry.

    Eligible:
    - provider-transient failure (timeout/connection/429/5xx): up to max attempts;
    - JSON parse failure: at most one retry.

    Not eligible:
    - validated semantic/tool/policy mistakes;
    - forbidden/unknown tools or other D13PlanError failures;
    - permanent provider/auth/model-availability errors.
    """
    max_attempts = max(1, int(config.get("retry_max_attempts", 3)))
    max_format_retries = max(0, int(config.get("retry_json_format_max", 1)))
    attempts: list[dict[str, Any]] = []
    total_latency_ms = 0
    format_retries_used = 0

    for attempt in range(1, max_attempts + 1):
        started = time.perf_counter()
        try:
            plan, latency_ms, usage, content = _call_once(config, case)
            total_latency_ms += latency_ms
            attempts.append({
                "attempt": attempt,
                "success": True,
                "error_kind": None,
                "error": None,
                "latency_ms": latency_ms,
                "usage": usage,
                "estimated_cost_cny": _attempt_cost_cny(config, usage),
            })
            return plan, total_latency_ms, usage, content, {
                "attempt_count": attempt,
                "first_attempt_success": attempt == 1,
                "retry_recovered": attempt > 1,
                "retry_exhausted": False,
                "attempts": attempts,
                "total_latency_ms_including_retries": total_latency_ms,
            }
        except Exception as exc:
            elapsed_ms = getattr(exc, "d13_attempt_latency_ms", None)
            if elapsed_ms is None:
                elapsed_ms = getattr(exc, "latency_ms", None)
            if elapsed_ms is None:
                elapsed_ms = int((time.perf_counter() - started) * 1000)
            elapsed_ms = int(elapsed_ms)
            total_latency_ms += elapsed_ms
            kind = _error_kind(exc)
            attempt_usage = dict(getattr(exc, "d13_attempt_usage", {}) or getattr(exc, "usage", {}) or {})
            attempts.append({
                "attempt": attempt,
                "success": False,
                "error_kind": kind,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_ms": elapsed_ms,
                "usage": attempt_usage,
                "estimated_cost_cny": _attempt_cost_cny(config, attempt_usage),
            })

            retry = False
            if attempt < max_attempts and kind == "PROVIDER_TRANSIENT":
                retry = True
            elif attempt < max_attempts and kind == "MODEL_FORMAT_FAILURE" and format_retries_used < max_format_retries:
                format_retries_used += 1
                retry = True

            if not retry:
                setattr(exc, "d13_retry_telemetry", {
                    "attempt_count": attempt,
                    "first_attempt_success": False,
                    "retry_recovered": False,
                    "retry_exhausted": kind in {"PROVIDER_TRANSIENT", "MODEL_FORMAT_FAILURE"},
                    "attempts": attempts,
                    "total_latency_ms_including_retries": total_latency_ms,
                })
                raise

            time.sleep(_retry_delay_seconds(attempt))

    raise RuntimeError("retry loop exited unexpectedly")

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-config", required=True)
    parser.add_argument("--cases", default=str(Path(__file__).with_name("d13_model_selection_cases_v0_2.json")))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    config = _load_json(args.candidate_config)
    cases = _load_json(args.cases)
    results = []
    for case in cases:
        row = {
            "case_id": case["case_id"],
            "category": case.get("category"),
            "prompt": case["prompt"],
            "runtime_context": {
                "trigger_type": case.get("trigger_type"),
                "current_datetime": case.get("current_datetime"),
                "timezone": case.get("timezone"),
                "authenticated_scope": case.get("authenticated_scope"),
                "context": case.get("context"),
            },
            "expected": case,
        }
        try:
            plan, latency_ms, usage, raw_content, retry_telemetry = _call_with_retry(config, case)
            row.update({
                "plan": plan,
                "latency_ms": latency_ms,
                "usage": usage,
                "raw_model_content": raw_content,
                "retry": retry_telemetry,
                "error": None,
            })
        except Exception as exc:
            row.update({
                "plan": None,
                "latency_ms": None,
                "usage": {},
                "raw_model_content": None,
                "retry": getattr(exc, "d13_retry_telemetry", {
                    "attempt_count": 1,
                    "first_attempt_success": False,
                    "retry_recovered": False,
                    "retry_exhausted": False,
                    "attempts": [{
                        "attempt": 1,
                        "success": False,
                        "error_kind": _error_kind(exc),
                        "error": f"{type(exc).__name__}: {exc}",
                        "latency_ms": None,
                    }],
                    "total_latency_ms_including_retries": None,
                }),
                "error": f"{type(exc).__name__}: {exc}",
            })
        results.append(row)

    artifact = {
        "candidate_name": config.get("name") or config.get("model"),
        "provider": config.get("provider"),
        "model": config.get("model"),
        "run_id": args.run_id,
        "temperature": config.get("temperature", 0),
        "candidate_meta": {
            "family": config.get("family"),
            "selection_role": config.get("selection_role"),
            "structured_output_supported": config.get("structured_output_supported"),
            "qualification_pool_source": config.get("qualification_pool_source"),
            "pricing_snapshot": config.get("pricing_snapshot"),
            "pricing_input_cny_per_million": config.get("pricing_input_cny_per_million"),
            "pricing_output_cny_per_million": config.get("pricing_output_cny_per_million"),
        },
        "case_set": Path(args.cases).name,
        "skill_version": d13.D13_SKILL_VERSION,
        "tool_contract_version": d13.D13_TOOL_CONTRACT_VERSION,
        "case_count": len(results),
        "retry_policy": {
            "max_attempts": int(config.get("retry_max_attempts", 3)),
            "retryable_provider_http_codes": sorted(RETRYABLE_HTTP_CODES),
            "retry_provider_transient": True,
            "retry_json_parse_failure": "AT_MOST_ONCE",
            "retry_semantic_or_policy_failure": False,
            "cross_model_fallback_enabled": False,
        },
        "results": results,
    }
    Path(args.output).write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
