from __future__ import annotations
import argparse, json
from pathlib import Path
from statistics import mean


def load(p): return json.loads(Path(p).read_text(encoding='utf-8'))

def structure(plan):
    if not plan: return None
    return {
        'decision': plan.get('decision'),
        'tool_calls': [
            {'tool_name': c.get('tool_name'), 'task_id': c.get('task_id'), 'payload': c.get('payload') or {}}
            for c in plan.get('tool_calls', [])
        ],
        'clarification_present': bool(plan.get('clarification_question')),
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--results-dir', required=True)
    ap.add_argument('--output', required=True)
    args=ap.parse_args()
    root=Path(args.results_dir)
    groups={}
    for p in sorted(root.glob('*_RUN[12].json')):
        if p.name.endswith('_SCORE.json'): continue
        a=load(p)
        groups.setdefault(a['model'], {})[a['run_id'].upper()] = (p,a)
    summary=[]
    for model, runs in sorted(groups.items()):
        if 'RUN1' not in runs or 'RUN2' not in runs: continue
        p1,a1=runs['RUN1']; p2,a2=runs['RUN2']
        s1=load(p1.with_name(p1.stem+'_SCORE.json'))
        s2=load(p2.with_name(p2.stem+'_SCORE.json'))
        m2={r['case_id']:r for r in a2['results']}
        stable=[]
        unstable=[]
        for r1 in a1['results']:
            r2=m2.get(r1['case_id'])
            ok=bool(r2) and structure(r1.get('plan')) == structure(r2.get('plan'))
            stable.append(ok)
            if not ok: unstable.append(r1['case_id'])
        summary.append({
            'model': model,
            'candidate_name': a1.get('candidate_name'),
            'hard_gate_pass_both': bool(s1['hard_gate_pass'] and s2['hard_gate_pass']),
            'hard_failures_run1': s1['hard_failures'],
            'hard_failures_run2': s2['hard_failures'],
            'valid_plan_rate_avg': mean([s1['valid_plan_count']/s1['case_count'], s2['valid_plan_count']/s2['case_count']]),
            'decision_accuracy_avg': mean([s1['decision_accuracy'],s2['decision_accuracy']]),
            'tool_exact_accuracy_avg': mean([s1['tool_exact_accuracy'],s2['tool_exact_accuracy']]),
            'task_binding_accuracy_avg': mean([s1['task_binding_accuracy'],s2['task_binding_accuracy']]),
            'payload_accuracy_avg': mean([s1['payload_accuracy'],s2['payload_accuracy']]),
            'run_to_run_structural_stability': sum(stable)/len(stable) if stable else 0,
            'unstable_case_ids': unstable,
            'latency_ms_median_run1': s1['latency_ms_median'],
            'latency_ms_median_run2': s2['latency_ms_median'],
            'prompt_tokens_total_2runs': (s1.get('prompt_tokens_total') or 0)+(s2.get('prompt_tokens_total') or 0),
            'completion_tokens_total_2runs': (s1.get('completion_tokens_total') or 0)+(s2.get('completion_tokens_total') or 0),
        })
    out={'comparison_version':'D13_MODEL_COMPARE_V0_2','candidates':summary}
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(args.output)

if __name__=='__main__': main()
