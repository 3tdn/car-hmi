<#
Run tests helper for Windows (PowerShell)
Usage:
  .\scripts\test_windows.ps1 [-InstallBefore]

This script will:
 - ensure .venv exists (creates if missing)
 - optionally install dependencies if -InstallBefore passed
 - run pytest with coverage
#>

param(
    [switch]$InstallBefore
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
& $venvPy -m pytest tests/ -q --tb=short --cov=src --cov-fail-under=60 `
    --cov-report=html:$reports\coverage_html `
    --cov-report=term-missing `
    --html="$reports\report.html" --self-contained-html
