from __future__ import annotations
import argparse, json, statistics
from pathlib import Path

def load(p): return json.loads(Path(p).read_text(encoding="utf-8-sig"))

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--results-dir",required=True); ap.add_argument("--output",required=True); args=ap.parse_args()
    d=Path(args.results_dir); scores={}
    for p in d.glob("*_SCORE.json"):
        x=load(p); scores[(x.get("model"),x.get("run_id"))]=x
    models=sorted({m for m,_ in scores})
    rows=[]
    for m in models:
        r1=scores.get((m,"RUN1")); r2=scores.get((m,"RUN2"))
        if not r1 or not r2: continue
        sig1={x["case_id"]:x.get("plan_signature") for x in r1["rows"]}; sig2={x["case_id"]:x.get("plan_signature") for x in r2["rows"]}
        comparable=[cid for cid in sig1 if sig1[cid] is not None and sig2.get(cid) is not None]
        stable=sum(sig1[cid]==sig2[cid] for cid in comparable)
        lats=[x for x in [r1.get("latency_ms_median"),r2.get("latency_ms_median")] if x is not None]
        rows.append({
            "model":m,"provider":r1.get("provider"),
            "hard_gate_pass_both":bool(r1.get("hard_gate_pass") and r2.get("hard_gate_pass")),
            "hard_failures_run1":r1.get("hard_failures") or [],"hard_failures_run2":r2.get("hard_failures") or [],
            "valid_rate_run1":r1.get("valid_rate"),"valid_rate_run2":r2.get("valid_rate"),
            "semantic_accuracy_run1":r1.get("semantic_case_accuracy"),"semantic_accuracy_run2":r2.get("semantic_case_accuracy"),
            "semantic_accuracy_mean":statistics.mean([r1.get("semantic_case_accuracy",0),r2.get("semantic_case_accuracy",0)]),
            "structural_stability":stable/len(comparable) if comparable else None,"comparable_cases":len(comparable),
            "median_latency_ms_mean":statistics.mean(lats) if lats else None,
            "completion_tokens_total_two_runs":(r1.get("completion_tokens_total") or 0)+(r2.get("completion_tokens_total") or 0),
            "prompt_tokens_total_two_runs":(r1.get("prompt_tokens_total") or 0)+(r2.get("prompt_tokens_total") or 0),
        })
    rows.sort(key=lambda x:(not x["hard_gate_pass_both"],-x["semantic_accuracy_mean"],-(x["structural_stability"] or 0),-min(x["valid_rate_run1"],x["valid_rate_run2"]),x["median_latency_ms_mean"] or 10**12))
    out={"status":"DESCRIPTIVE_COMPARISON_ONLY_NO_AUTOMATIC_WINNER","selection_rule":"Safety hard gate first; then business semantic quality, run-to-run stability, provider reliability, latency/token and manual language review. No weighted total auto-selects winner.","models":rows}
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=="__main__": main()
