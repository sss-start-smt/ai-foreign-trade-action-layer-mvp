#!/usr/bin/env bash
set -euo pipefail
: "${DASHSCOPE_API_KEY:?请先 export DASHSCOPE_API_KEY=...}"

HERE="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$HERE/.." && pwd)"
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+::$PYTHONPATH}"

CASES="$HERE/d13_model_qualification_cases_v0_1.json"
CONFIG_DIR="$HERE/d13_candidates_bailian_qualification_2026-08-17"
OUT_DIR="$HERE/results_d13_bailian_qualification_2026-08-17"
ZIP="$HERE/results_d13_bailian_qualification_2026-08-17.zip"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
rm -f "$ZIP"

# Fail before spending API quota if project imports are broken.
python "$HERE/d13_model_selection_runner.py" --help >/dev/null

models=(
  qwen3.8-max qwen3.7-plus qwen3.7-flash
  deepseek-v4-pro deepseek-v4-flash glm-5.2 kimi-k2.6 MiniMax-M3 mimo-v2.5-pro
)

for model in "${models[@]}"; do
  echo "=== QUALIFICATION $model ==="
  test -f "$CONFIG_DIR/$model.json"
  python "$HERE/d13_model_selection_runner.py" \
    --candidate-config "$CONFIG_DIR/$model.json" \
    --cases "$CASES" \
    --run-id QUAL \
    --output "$OUT_DIR/${model}_QUAL.json"
  python "$HERE/d13_model_selection_score.py" \
    "$OUT_DIR/${model}_QUAL.json" \
    --output "$OUT_DIR/${model}_QUAL_SCORE.json"
done

result_count="$(find "$OUT_DIR" -maxdepth 1 -name '*_QUAL.json' | wc -l | tr -d ' ')"
score_count="$(find "$OUT_DIR" -maxdepth 1 -name '*_QUAL_SCORE.json' | wc -l | tr -d ' ')"
if [[ "$result_count" != "${#models[@]}" || "$score_count" != "${#models[@]}" ]]; then
  echo "资格赛输出不完整：results=$result_count/${#models[@]}, scores=$score_count/${#models[@]}" >&2
  exit 1
fi

python "$HERE/d13_model_qualification_compare.py" \
  --results-dir "$OUT_DIR" \
  --output "$OUT_DIR/D13_MODEL_QUALIFICATION_COMPARISON.json"

(cd "$OUT_DIR" && zip -qr "$ZIP" .)
echo "ZIP: $ZIP"
