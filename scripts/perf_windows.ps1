<#
Run k6 performance test script for car-hmi.
Usage:
  .\scripts\perf_windows.ps1 [-BaseUrl http://localhost:8000]
#>

param(
    [string]$BaseUrl = "http://localhost:8000"
)

function Write-Log([string]$m){ Write-Host "[perf] $m" }

if (-not (Get-Command k6 -ErrorAction SilentlyContinue)) {
    Write-Error "k6 not found. Install k6 first: https://k6.io/docs/get-started/installation/"
    exit 1
}

$reportDir = Join-Path -Path (Get-Location) -ChildPath "tests/3_performance/reports"
if (-not (Test-Path -Path $reportDir)) { New-Item -ItemType Directory -Path $reportDir | Out-Null }

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$outJson = Join-Path -Path $reportDir -ChildPath ("k6_" + $timestamp + ".json")
$scriptPath = "tests/3_performance/scripts/load_test_homepage.js"

Write-Log "Running k6 against: $BaseUrl"
& k6 run --out "json=$outJson" -e "BASE_URL=$BaseUrl" $scriptPath

Write-Log "k6 report saved to: $outJson"
