from __future__ import annotations
import argparse, json, statistics
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--results-dir", required=True)
    ap.add_argument("--output", required=True)
    args=ap.parse_args()
    d=Path(args.results_dir)
    scores={}
    for p in d.glob("*_SCORE.json"):
        x=load(p)
        scores[(x.get("model"),x.get("run_id"))]=x
    rows=[]
    for model in sorted({m for m,_ in scores}):
        r1=scores.get((model,"RUN1")); r2=scores.get((model,"RUN2"))
        if not r1 or not r2:
            continue
        lats=[x for x in (r1.get("latency_ms_median"),r2.get("latency_ms_median")) if x is not None]
        rows.append({
            "model": model,
            "hard_gate_pass_both": bool(r1.get("hard_gate_pass") and r2.get("hard_gate_pass")),
            "first_attempt_success_run1": r1.get("first_attempt_success_rate"),
            "first_attempt_success_run2": r2.get("first_attempt_success_rate"),
            "final_valid_rate_run1": r1.get("valid_rate"),
            "final_valid_rate_run2": r2.get("valid_rate"),
            "retry_recovered_run1": r1.get("retry_recovered_count"),
            "retry_recovered_run2": r2.get("retry_recovered_count"),
            "retry_exhausted_run1": r1.get("retry_exhausted_count"),
            "retry_exhausted_run2": r2.get("retry_exhausted_count"),
            "first_failure_kinds_run1": r1.get("first_failure_kinds") or {},
            "first_failure_kinds_run2": r2.get("first_failure_kinds") or {},
            "semantic_accuracy_run1": r1.get("semantic_case_accuracy"),
            "semantic_accuracy_run2": r2.get("semantic_case_accuracy"),
            "task_binding_run1": r1.get("task_binding_accuracy"),
            "task_binding_run2": r2.get("task_binding_accuracy"),
            "payload_accuracy_run1": r1.get("payload_accuracy"),
            "payload_accuracy_run2": r2.get("payload_accuracy"),
            "median_latency_ms_mean_including_retry": statistics.mean(lats) if lats else None,
            "p95_latency_ms_max_two_runs": max([x for x in (r1.get("latency_ms_p95"),r2.get("latency_ms_p95")) if x is not None], default=None),
            "retry_added_latency_ms_total_two_runs": (r1.get("retry_added_latency_ms_total") or 0)+(r2.get("retry_added_latency_ms_total") or 0),
            "total_attempts_two_runs": (r1.get("total_attempt_count") or 0)+(r2.get("total_attempt_count") or 0),
            "prompt_tokens_total_all_attempts_two_runs": (r1.get("prompt_tokens_total_all_attempts") or 0)+(r2.get("prompt_tokens_total_all_attempts") or 0),
            "completion_tokens_total_all_attempts_two_runs": (r1.get("completion_tokens_total_all_attempts") or 0)+(r2.get("completion_tokens_total_all_attempts") or 0),
            "retry_tokens_total_two_runs": (r1.get("retry_prompt_tokens") or 0)+(r1.get("retry_completion_tokens") or 0)+(r2.get("retry_prompt_tokens") or 0)+(r2.get("retry_completion_tokens") or 0),
            "estimated_cost_cny_total_all_attempts_two_runs": round((r1.get("estimated_cost_cny_total_all_attempts") or 0)+(r2.get("estimated_cost_cny_total_all_attempts") or 0),8),
            "retry_estimated_cost_cny_two_runs": round((r1.get("retry_estimated_cost_cny") or 0)+(r2.get("retry_estimated_cost_cny") or 0),8),
        })
    out={
        "status":"RETRY_DUEL_DESCRIPTIVE_ONLY_NO_AUTOMATIC_WINNER",
        "policy":{
            "same_model_retry_only":True,
            "cross_model_fallback_enabled":False,
            "max_attempts":3,
            "json_format_retry_max":1,
            "semantic_or_policy_error_retry":False,
            "selection_note":"Compare first-attempt reliability separately from final reliability after retry. All-attempt token/cost and retry inflation remain visible; semantic/policy mistakes are never retried."
        },
        "models":rows,
    }
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=="__main__":
    main()
