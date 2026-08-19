from __future__ import annotations
import argparse, json
from pathlib import Path


def load(p: Path):
    return json.loads(p.read_text(encoding='utf-8'))


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--results-dir', required=True)
    ap.add_argument('--output', required=True)
    args=ap.parse_args()
    root=Path(args.results_dir)
    rows=[]
    for score_path in sorted(root.glob('*_QUAL_SCORE.json')):
        stem=score_path.name[:-len('_QUAL_SCORE.json')]
        result_path=root/f'{stem}_QUAL.json'
        if not result_path.exists():
            continue
        score=load(score_path)
        art=load(result_path)
        meta=art.get('candidate_meta') or {}
        n=score.get('case_count') or 1
        valid_rate=(score.get('valid_plan_count') or 0)/n
        # Qualification ranking is descriptive only. Hard Gate is not a weighted score.
        quality=(
            float(score.get('decision_accuracy') or 0)
            + float(score.get('tool_exact_accuracy') or 0)
            + float(score.get('task_binding_accuracy') or 0)
            + float(score.get('payload_accuracy') or 0)
        )/4
        rows.append({
            'model': art.get('model'),
            'candidate_name': art.get('candidate_name'),
            'family': meta.get('family'),
            'selection_role': meta.get('selection_role'),
            'structured_output_supported': meta.get('structured_output_supported'),
            'hard_gate_pass': bool(score.get('hard_gate_pass')),
            'hard_failures': score.get('hard_failures') or [],
            'valid_plan_rate': valid_rate,
            'decision_accuracy': score.get('decision_accuracy'),
            'tool_exact_accuracy': score.get('tool_exact_accuracy'),
            'task_binding_accuracy': score.get('task_binding_accuracy'),
            'payload_accuracy': score.get('payload_accuracy'),
            'qualification_quality_mean': quality,
            'latency_ms_median': score.get('latency_ms_median'),
            'latency_ms_max': score.get('latency_ms_max'),
            'prompt_tokens_total': score.get('prompt_tokens_total'),
            'completion_tokens_total': score.get('completion_tokens_total'),
        })
    if not rows:
        raise RuntimeError(
            "No qualification candidates found. Expected *_QUAL.json and "
            "*_QUAL_SCORE.json pairs; refusing to emit an empty comparison."
        )
    rows.sort(key=lambda r:(
        not r['hard_gate_pass'],
        -r['qualification_quality_mean'],
        -r['valid_plan_rate'],
        r['latency_ms_median'] if r['latency_ms_median'] is not None else 10**12,
    ))
    out={
        'comparison_version':'D13_MODEL_QUALIFICATION_COMPARE_V0_1',
        'selection_policy':{
            'automatic_disqualifier':'Any Hard Safety Gate failure.',
            'ranking_note':'qualification_quality_mean is descriptive (equal mean of decision/tool/task/payload accuracy), not the final winner score.',
            'finalist_decision':'Do not auto-pick. Review hard failures, bad cases, family coverage, quality, latency and token usage before freezing finalists.',
        },
        'candidates':rows,
    }
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(args.output)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
