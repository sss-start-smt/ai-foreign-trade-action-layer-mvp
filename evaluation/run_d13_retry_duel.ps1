$ErrorActionPreference = "Stop"
if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $true
}
if (-not $env:DASHSCOPE_API_KEY) { throw "DASHSCOPE_API_KEY is not set." }

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $Here
$Sep = [IO.Path]::PathSeparator
if ($env:PYTHONPATH) { $env:PYTHONPATH = "$ProjectRoot$Sep$env:PYTHONPATH" } else { $env:PYTHONPATH = $ProjectRoot }

$Cases = Join-Path $Here "d13_model_selection_cases_v0_3.json"
$ConfigDir = Join-Path $Here "d13_retry_duel_candidates_2026-08-17"
$OutDir = Join-Path $Here "results_d13_retry_duel_2026-08-17"
$Zip = Join-Path $Here "results_d13_retry_duel_2026-08-17.zip"
if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
if (Test-Path $Zip) { Remove-Item -Force $Zip }

function Run-Python {
    param([string[]]$PyArgs)
    & python @PyArgs
    if ($LASTEXITCODE -ne 0) { throw "Python failed exit=$($LASTEXITCODE): python $($PyArgs -join ' ')" }
}

Run-Python @((Join-Path $Here "d13_model_selection_runner.py"), "--help")

$Models = @("qwen3.8-max", "glm-5.2")
$Runs = @("RUN1", "RUN2")
foreach ($Model in $Models) {
    $Config = Join-Path $ConfigDir "$Model.json"
    foreach ($Run in $Runs) {
        $Out = Join-Path $OutDir "${Model}_${Run}.json"
        $Score = Join-Path $OutDir "${Model}_${Run}_SCORE.json"
        Write-Host "=== RETRY DUEL $Model $Run ==="
        Run-Python @((Join-Path $Here "d13_model_selection_runner.py"), "--candidate-config", $Config, "--cases", $Cases, "--run-id", $Run, "--output", $Out)
        Run-Python @((Join-Path $Here "d13_model_selection_score_v3.py"), $Out, "--output", $Score)
    }
}

$rawCount = @(Get-ChildItem $OutDir -Filter "*_RUN?.json").Count
$scoreCount = @(Get-ChildItem $OutDir -Filter "*_RUN?_SCORE.json").Count
if ($rawCount -ne 4 -or $scoreCount -ne 4) { throw "Incomplete duel outputs: raw=$rawCount/4 scores=$scoreCount/4" }

Run-Python @((Join-Path $Here "d13_model_retry_duel_compare.py"), "--results-dir", $OutDir, "--output", (Join-Path $OutDir "D13_RETRY_DUEL_COMPARISON.json"))
Compress-Archive -Path "$OutDir\*" -DestinationPath $Zip -Force
Write-Host "DONE"
Write-Host "ZIP: $Zip"
