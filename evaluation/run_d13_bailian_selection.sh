#!/usr/bin/env bash
set -euo pipefail
: "${DASHSCOPE_API_KEY:?Please export DASHSCOPE_API_KEY first}"
HERE="$(cd "$(dirname "$0")" && pwd)"
CASES="$HERE/d13_model_selection_cases_v0_2.json"
CONFIG_DIR="$HERE/d13_candidates_bailian_2026-08-17"
OUT="$HERE/results_d13_bailian_2026-08-17"
mkdir -p "$OUT"
models=(qwen3.7-plus qwen3.8-max deepseek-v4-pro glm-5.2)
for model in "${models[@]}"; do
  for run in RUN1 RUN2; do
    echo "=== $model $run ==="
    python "$HERE/d13_model_selection_runner.py" --candidate-config "$CONFIG_DIR/$model.json" --cases "$CASES" --run-id "$run" --output "$OUT/${model}_${run}.json"
    python "$HERE/d13_model_selection_score.py" "$OUT/${model}_${run}.json" --output "$OUT/${model}_${run}_SCORE.json" >/dev/null
  done
done
python "$HERE/d13_model_selection_compare.py" --results-dir "$OUT" --output "$OUT/D13_MODEL_COMPARISON.json"
(cd "$OUT" && zip -qr "$HERE/results_d13_bailian_2026-08-17.zip" .)
echo "DONE: $OUT"
echo "ZIP: $HERE/results_d13_bailian_2026-08-17.zip"
