$ErrorActionPreference = "Stop"

if (-not $env:DASHSCOPE_API_KEY) {
    throw "DASHSCOPE_API_KEY is not set."
}

$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $Here
$Sep = [IO.Path]::PathSeparator
if ($env:PYTHONPATH) { $env:PYTHONPATH = "$ProjectRoot$Sep$env:PYTHONPATH" } else { $env:PYTHONPATH = $ProjectRoot }

$OutDir = Join-Path $Here "results_d13_runtime_live_smoke_2026-08-17"
$Db = Join-Path $OutDir "d13_runtime_live_smoke.db"
$Out = Join-Path $OutDir "D13_RUNTIME_LIVE_SMOKE.json"
$Zip = Join-Path $Here "results_d13_runtime_live_smoke_2026-08-17.zip"

if (Test-Path $OutDir) { Remove-Item -Recurse -Force $OutDir }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
if (Test-Path $Zip) { Remove-Item -Force $Zip }

& python (Join-Path $Here "d13_runtime_live_smoke.py") --db $Db --output $Out
$SmokeExit = $LASTEXITCODE

# Always preserve evidence, including FAIL runs.  Previously the script threw
# before Compress-Archive, forcing manual packaging of the most useful badcase.
if (Test-Path $OutDir) {
    Compress-Archive -Path "$OutDir\*" -DestinationPath $Zip -Force
    Write-Host "ZIP: $Zip"
}

if ($SmokeExit -ne 0) {
    throw "D13 runtime live smoke failed exit=$($SmokeExit). Evidence ZIP was preserved."
}

Write-Host "DONE"
