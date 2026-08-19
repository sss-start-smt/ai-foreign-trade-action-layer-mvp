$ErrorActionPreference = "Stop"
if (-not $env:DASHSCOPE_API_KEY) { throw "请先设置 `$env:DASHSCOPE_API_KEY" }
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Cases = Join-Path $Here "d13_model_selection_cases_v0_2.json"
$ConfigDir = Join-Path $Here "d13_candidates_bailian_2026-08-17"
$OutDir = Join-Path $Here "results_d13_bailian_2026-08-17"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Models = @("qwen3.7-plus","qwen3.8-max","deepseek-v4-pro","glm-5.2")
foreach ($Model in $Models) {
  foreach ($Run in @("RUN1","RUN2")) {
    $Config = Join-Path $ConfigDir "$Model.json"
    $Out = Join-Path $OutDir "${Model}_${Run}.json"
    $Score = Join-Path $OutDir "${Model}_${Run}_SCORE.json"
    Write-Host "=== $Model $Run ==="
    python (Join-Path $Here "d13_model_selection_runner.py") --candidate-config $Config --cases $Cases --run-id $Run --output $Out
    python (Join-Path $Here "d13_model_selection_score.py") $Out --output $Score
  }
}
python (Join-Path $Here "d13_model_selection_compare.py") --results-dir $OutDir --output (Join-Path $OutDir "D13_MODEL_COMPARISON.json")
Compress-Archive -Path "$OutDir\*" -DestinationPath (Join-Path $Here "results_d13_bailian_2026-08-17.zip") -Force
Write-Host "DONE: $OutDir"
Write-Host "ZIP:  $(Join-Path $Here 'results_d13_bailian_2026-08-17.zip')"
