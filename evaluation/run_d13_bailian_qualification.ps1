$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
  $PSNativeCommandUseErrorActionPreference = $true
}
if (-not $env:DASHSCOPE_API_KEY) { throw "请先设置 `$env:DASHSCOPE_API_KEY" }

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $Here
$Sep = [IO.Path]::PathSeparator
if ($env:PYTHONPATH) {
  $env:PYTHONPATH = "$ProjectRoot$Sep$env:PYTHONPATH"
} else {
  $env:PYTHONPATH = $ProjectRoot
}

$Cases = Join-Path $Here "d13_model_qualification_cases_v0_1.json"
$ConfigDir = Join-Path $Here "d13_candidates_bailian_qualification_2026-08-17"
$OutDir = Join-Path $Here "results_d13_bailian_qualification_2026-08-17"
$Zip = Join-Path $Here "results_d13_bailian_qualification_2026-08-17.zip"

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
if (Test-Path $Zip) { Remove-Item -Force $Zip }

function Run-Python {
  param([string[]]$PyArgs)
  & python @PyArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Python 命令失败（exit=$LASTEXITCODE）：python $($PyArgs -join ' ')"
  }
}

# Fail before spending API quota if the runner cannot import the FlowOrder runtime.
Run-Python @((Join-Path $Here "d13_model_selection_runner.py"), "--help")

$Models = @(
  "qwen3.8-max",
  "qwen3.7-plus",
  "qwen3.7-flash",
  "deepseek-v4-pro",
  "deepseek-v4-flash",
  "glm-5.2",
  "kimi-k2.6",
  "MiniMax-M3",
  "mimo-v2.5-pro"
)

foreach ($Model in $Models) {
  $Config = Join-Path $ConfigDir "$Model.json"
  $Out = Join-Path $OutDir "${Model}_QUAL.json"
  $Score = Join-Path $OutDir "${Model}_QUAL_SCORE.json"

  if (-not (Test-Path $Config)) { throw "缺少候选配置：$Config" }

  Write-Host "=== QUALIFICATION $Model ==="
  Run-Python @(
    (Join-Path $Here "d13_model_selection_runner.py"),
    "--candidate-config", $Config,
    "--cases", $Cases,
    "--run-id", "QUAL",
    "--output", $Out
  )
  Run-Python @(
    (Join-Path $Here "d13_model_selection_score.py"),
    $Out,
    "--output", $Score
  )
}

$ResultCount = @(Get-ChildItem $OutDir -Filter "*_QUAL.json").Count
$ScoreCount = @(Get-ChildItem $OutDir -Filter "*_QUAL_SCORE.json").Count
if ($ResultCount -ne $Models.Count -or $ScoreCount -ne $Models.Count) {
  throw "资格赛输出不完整：results=$ResultCount/$($Models.Count), scores=$ScoreCount/$($Models.Count)"
}

Run-Python @(
  (Join-Path $Here "d13_model_qualification_compare.py"),
  "--results-dir", $OutDir,
  "--output", (Join-Path $OutDir "D13_MODEL_QUALIFICATION_COMPARISON.json")
)

Compress-Archive -Path "$OutDir\*" -DestinationPath $Zip -Force
Write-Host "DONE: $OutDir"
Write-Host "ZIP:  $Zip"
