<#
Run tests helper for Windows (PowerShell)
Usage:
    .\scripts\test_windows.ps1 [-InstallBefore] [-Suite <all|unit|functional|api|ws|integration|security>]

This script will:
 - ensure .venv exists (creates if missing)
 - optionally install dependencies if -InstallBefore passed
 - run pytest with coverage
#>

param(
    [switch]$InstallBefore,
    [ValidateSet("all", "unit", "functional", "api", "ws", "integration", "security")]
    [string]$Suite = "all"
)

function Write-Log([string]$m){ Write-Host "[test] $m" }

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

# Run pytest
Write-Log "Running tests (pytest)"
# Ensure reports directory exists for HTML output
$reports = Join-Path -Path (Get-Location) -ChildPath "reports"
if (-not (Test-Path -Path $reports)) { New-Item -ItemType Directory -Path $reports | Out-Null }

# Ensure pytest-html is installed in the venv (so --html option is available)
& $venvPy -m pip show pytest-html > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "Installing pytest-html into virtualenv"
    & $venvPy -m pip install pytest-html
}

# Ensure pytest-cov is installed (so we can emit HTML coverage report)
& $venvPy -m pip show pytest-cov > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Log "Installing pytest-cov into virtualenv"
    & $venvPy -m pip install pytest-cov
}

# Run pytest with coverage and HTML test report (requires pytest-html and pytest-cov)
switch ($Suite) {
    "all"         { $target = "tests" }
    "unit"        { $target = "tests/1_unit_functions" }
    "functional"  { $target = "tests/2_functional_tests" }
    "api"         { $target = "tests/2_functional_tests/api" }
    "ws"          { $target = "tests/2_functional_tests/websockets" }
    "integration" { $target = "tests/2_functional_tests/integration" }
    "security"    { $target = "tests/4_security" }
}

Write-Log "Suite: $Suite ($target)"

& $venvPy -m pytest $target -q --tb=short --cov=src --cov-fail-under=60 `
    --cov-report=html:$reports\coverage_html `
    --cov-report=term-missing `
    --html="$reports\report.html" --self-contained-html
