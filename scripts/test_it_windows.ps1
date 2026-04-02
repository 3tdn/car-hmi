<#
Integration test runner for Windows (PowerShell)
Usage:
  .\scripts\test_it_windows.ps1 [-InstallBefore] [-Verbose]

This script will:
 - ensure .venv exists (creates if missing)
 - optionally install dependencies if -InstallBefore passed
 - run ONLY tests/test_integration.py with coverage
 - generate HTML test report  → reports/it_report.html
 - generate HTML coverage      → reports/it_coverage_html/
#>

param(
    [switch]$InstallBefore,
    [switch]$Verbose
)

function Write-Log([string]$m){ Write-Host "[it-test] $m" }

# Find python
$pyCmd = $null
if (Get-Command python -ErrorAction SilentlyContinue) { $pyCmd = "python" }
elseif (Get-Command py -ErrorAction SilentlyContinue) { $pyCmd = "py" }
else { Write-Error "Python not found. Install Python >= 3.10 and re-run."; exit 1 }

if (-not (Test-Path -Path ".venv")) {
    Write-Log "Creating virtualenv (.venv)"
    & $pyCmd -m venv .venv
}

$venvPy = Join-Path -Path (Get-Location) -ChildPath ".venv\Scripts\python.exe"

# Install deps optionally
if ($InstallBefore) {
    Write-Log "Installing dependencies (editable + dev)"
    & $venvPy -m pip install --upgrade pip
    & $venvPy -m pip install -e ".[dev]"
}

# Ensure reports directory exists
$reports = Join-Path -Path (Get-Location) -ChildPath "reports"
if (-not (Test-Path -Path $reports)) { New-Item -ItemType Directory -Path $reports | Out-Null }

# Ensure pytest-html is installed
& $venvPy -m pip show pytest-html > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "Installing pytest-html"
    & $venvPy -m pip install pytest-html
}

# Ensure pytest-cov is installed
& $venvPy -m pip show pytest-cov > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "Installing pytest-cov"
    & $venvPy -m pip install pytest-cov
}

# Build verbosity flag
$verbFlag = if ($Verbose) { "-v" } else { "-q" }

Write-Log "Running integration tests (tests/test_integration.py)"
& $venvPy -m pytest tests/test_integration.py $verbFlag --tb=short `
    --cov=src `
    --cov-fail-under=0 `
    --cov-report=html:"$reports\it_coverage_html" `
    --cov-report=term-missing `
    --html="$reports\it_report.html" --self-contained-html

if ($LASTEXITCODE -eq 0) {
    Write-Log "Integration tests PASSED"
    Write-Log "Test report : $reports\it_report.html"
    Write-Log "Coverage    : $reports\it_coverage_html\index.html"
} else {
    Write-Log "Integration tests FAILED (exit code $LASTEXITCODE)"
    exit $LASTEXITCODE
}
