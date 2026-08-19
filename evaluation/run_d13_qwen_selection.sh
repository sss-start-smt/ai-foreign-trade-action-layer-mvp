#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$ROOT/results_d13_qwen_2026-08-17}"
mkdir -p "$OUT"
: "${DASHSCOPE_API_KEY:?Please export DASHSCOPE_API_KEY first}"
for model in qwen3.7-plus qwen3.7-max qwen3.7-flash; do
  for run in RUN1 RUN2; do
    raw="$OUT/D13_MODEL_SELECTION_${model}_${run}.json"
    score="$OUT/D13_MODEL_SELECTION_${model}_${run}_SCORE.json"
    python "$ROOT/d13_model_selection_runner.py" \
      --candidate-config "$ROOT/d13_candidates_qwen_2026-08-17/${model}.json" \
      --run-id "$run" --output "$raw"
    python "$ROOT/d13_model_selection_score.py" "$raw" --output "$score" >/dev/null
    echo "completed $model $run"
  done
done
