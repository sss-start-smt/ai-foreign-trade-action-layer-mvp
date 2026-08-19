$ErrorActionPreference = "Stop"

if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $true
}

if (-not $env:DASHSCOPE_API_KEY) {
    throw "DASHSCOPE_API_KEY is not set."
}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $Here
$Sep = [IO.Path]::PathSeparator

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$ProjectRoot$Sep$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $ProjectRoot
}

$Cases = Join-Path $Here "d13_model_selection_cases_v0_3.json"
$ConfigDir = Join-Path $Here "d13_finalists_bailian_2026-08-17"
$OutDir = Join-Path $Here "results_d13_final_2026-08-17"
$Zip = Join-Path $Here "results_d13_final_2026-08-17.zip"

if (Test-Path $OutDir) {
    Remove-Item -Recurse -Force $OutDir
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (Test-Path $Zip) {
    Remove-Item -Force $Zip
}

function Run-Python {
    param([string[]]$PyArgs)

    & python @PyArgs

    if ($LASTEXITCODE -ne 0) {
        throw "Python failed exit=$($LASTEXITCODE): python $($PyArgs -join ' ')"
    }
}

# Import/CLI smoke test before spending provider quota.
Run-Python @(
    (Join-Path $Here "d13_model_selection_runner.py"),
    "--help"
)

$Models = @(
    "qwen3.8-max",
    "glm-5.2",
    "deepseek-v4-pro",
    "kimi-k2.6"
)

$Runs = @(
    "RUN1",
    "RUN2"
)

foreach ($Model in $Models) {
    $Config = Join-Path $ConfigDir "$Model.json"

    if (-not (Test-Path $Config)) {
        throw "Missing finalist config: $Config"
    }

    foreach ($Run in $Runs) {
        $Out = Join-Path $OutDir "${Model}_${Run}.json"
        $Score = Join-Path $OutDir "${Model}_${Run}_SCORE.json"

        Write-Host "=== FINAL $Model $Run ==="

        Run-Python @(
            (Join-Path $Here "d13_model_selection_runner.py"),
            "--candidate-config", $Config,
            "--cases", $Cases,
            "--run-id", $Run,
            "--output", $Out
        )

        if (-not (Test-Path $Out)) {
            throw "Missing model result: $Out"
        }

        Run-Python @(
            (Join-Path $Here "d13_model_selection_score_v2.py"),
            $Out,
            "--output", $Score
        )

        if (-not (Test-Path $Score)) {
            throw "Missing score result: $Score"
        }
    }
}

$rawCount = @(Get-ChildItem $OutDir -Filter "*_RUN?.json").Count
$scoreCount = @(Get-ChildItem $OutDir -Filter "*_RUN?_SCORE.json").Count

if ($rawCount -ne 8 -or $scoreCount -ne 8) {
    throw "Incomplete final outputs: raw=$rawCount/8 scores=$scoreCount/8"
}

Run-Python @(
    (Join-Path $Here "d13_model_final_compare.py"),
    "--results-dir", $OutDir,
    "--output", (Join-Path $OutDir "D13_FINAL_MODEL_COMPARISON.json")
)

$comparison = Join-Path $OutDir "D13_FINAL_MODEL_COMPARISON.json"
if (-not (Test-Path $comparison)) {
    throw "Missing final comparison: $comparison"
}

Compress-Archive -Path "$OutDir\*" -DestinationPath $Zip -Force

Write-Host ""
Write-Host "DONE"
Write-Host "ZIP: $Zip"
