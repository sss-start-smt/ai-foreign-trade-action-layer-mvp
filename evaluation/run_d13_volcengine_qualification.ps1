$ErrorActionPreference = "Stop"

if (Get-Variable PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $true
}

if (-not $env:ARK_API_KEY) {
    throw "ARK_API_KEY is not set. Run: `$env:ARK_API_KEY='YOUR_KEY'"
}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $Here
$Sep = [IO.Path]::PathSeparator

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$ProjectRoot$Sep$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $ProjectRoot
}

$Cases = Join-Path $Here "d13_model_qualification_cases_v0_1.json"
$ConfigDir = Join-Path $Here "d13_candidates_volcengine_qualification_2026-08-17"
$OutDir = Join-Path $Here "results_d13_volcengine_qualification_2026-08-17"
$Zip = Join-Path $Here "results_d13_volcengine_qualification_2026-08-17.zip"
$TempDir = Join-Path $Here ".tmp_d13_volcengine_configs"

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
if (Test-Path $TempDir) { Remove-Item -Recurse -Force $TempDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
if (Test-Path $Zip) { Remove-Item -Force $Zip }

function Run-Python {
    param([string[]]$PyArgs)
    & python @PyArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed (exit=$LASTEXITCODE): python $($PyArgs -join ' ')"
    }
}

function Prepare-Config {
    param(
        [string]$SourceConfig,
        [string]$TargetConfig,
        [string]$ModelOverride
    )

    $cfg = Get-Content $SourceConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($ModelOverride) {
        $cfg.model = $ModelOverride
    }
    $json = $cfg | ConvertTo-Json -Depth 20
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($TargetConfig, $json, $utf8NoBom)
}

function Assert-Real-Result {
    param(
        [string]$ResultFile,
        [string]$ModelLabel
    )

    $raw = Get-Content $ResultFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $valid = @($raw.results | Where-Object { $_.plan -ne $null }).Count

    if ($valid -eq 0) {
        $firstError = ($raw.results | Where-Object { $_.error } | Select-Object -First 1).error
        throw "$($ModelLabel) returned 0 valid plans. First error: $firstError"
    }

    if ($valid -ne $raw.case_count) {
        Write-Warning "$($ModelLabel): valid plans $valid/$($raw.case_count). Keep the result ZIP for analysis."
    }
}

# Fail before any model request if FlowOrder imports are broken.
Run-Python @(
    (Join-Path $Here "d13_model_selection_runner.py"),
    "--help"
)

$Candidates = @(
    @{
        Label = "doubao-seed-2-1-pro-260628"
        Config = "doubao-seed-2-1-pro-260628.json"
        Override = $env:ARK_SEED_21_PRO_MODEL
    },
    @{
        Label = "doubao-seed-evolving"
        Config = "doubao-seed-evolving.json"
        Override = $env:ARK_SEED_EVOLVING_MODEL
    }
)

foreach ($C in $Candidates) {
    $SourceConfig = Join-Path $ConfigDir $C.Config
    $TempConfig = Join-Path $TempDir $C.Config

    if (-not (Test-Path $SourceConfig)) {
        throw "Missing candidate config: $SourceConfig"
    }

    Prepare-Config `
        -SourceConfig $SourceConfig `
        -TargetConfig $TempConfig `
        -ModelOverride $C.Override

    $Out = Join-Path $OutDir "$($C.Label)_QUAL.json"
    $Score = Join-Path $OutDir "$($C.Label)_QUAL_SCORE.json"

    Write-Host "=== VOLCENGINE QUALIFICATION: $($C.Label) ==="
    if ($C.Override) {
        Write-Host "Using model/endpoint override: $($C.Override)"
    }

    Run-Python @(
        (Join-Path $Here "d13_model_selection_runner.py"),
        "--candidate-config", $TempConfig,
        "--cases", $Cases,
        "--run-id", "QUAL",
        "--output", $Out
    )

    Assert-Real-Result `
        -ResultFile $Out `
        -ModelLabel $C.Label

    Run-Python @(
        (Join-Path $Here "d13_model_selection_score.py"),
        $Out,
        "--output", $Score
    )
}

$ResultCount = @(Get-ChildItem $OutDir -Filter "*_QUAL.json").Count
$ScoreCount = @(Get-ChildItem $OutDir -Filter "*_QUAL_SCORE.json").Count

if ($ResultCount -ne 2 -or $ScoreCount -ne 2) {
    throw "Incomplete Volcengine qualification outputs: results=$ResultCount/2 scores=$ScoreCount/2"
}

Run-Python @(
    (Join-Path $Here "d13_model_qualification_compare.py"),
    "--results-dir", $OutDir,
    "--output", (Join-Path $OutDir "D13_VOLCENGINE_QUALIFICATION_COMPARISON.json")
)

Compress-Archive -Path "$OutDir\*" -DestinationPath $Zip -Force
Remove-Item -Recurse -Force $TempDir

Write-Host ""
Write-Host "DONE"
Write-Host "Result directory: $OutDir"
Write-Host "ZIP: $Zip"
Write-Host ""
Write-Host "If Ark requires Endpoint IDs, set these optional overrides:"
Write-Host '  $env:ARK_SEED_21_PRO_MODEL="ep-xxxxx"'
Write-Host '  $env:ARK_SEED_EVOLVING_MODEL="ep-yyyyy"'
Write-Host "Then run this script again."
