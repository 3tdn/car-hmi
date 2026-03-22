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
& $venvPy -m pytest tests/ -q --tb=short --cov=src --cov-fail-under=60
