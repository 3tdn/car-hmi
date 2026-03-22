<#
Setup helper for Windows (PowerShell)
Usage:
  .\scripts\setup_windows.ps1

This script will:
 - ensure a .venv exists (creates if missing)
 - install editable project + dev deps: pip install -e ".[dev]"
#>

param(
    [string]$Python = "python"
)

function Write-Log([string]$m){ Write-Host "[setup] $m" }

# Find python (python or py)
$pyCmd = $null
if (Get-Command $Python -ErrorAction SilentlyContinue) { $pyCmd = $Python }
elif (Get-Command py -ErrorAction SilentlyContinue) { $pyCmd = "py" }
else { Write-Error "Python not found. Install Python >= 3.10 and re-run."; exit 1 }

# Create venv if missing
if (-not (Test-Path -Path ".venv")) {
    Write-Log "Creating virtualenv (.venv)"
    & $pyCmd -m venv .venv
}

$venvPy = Join-Path -Path (Get-Location) -ChildPath ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Log "Virtualenv python not found; recreating"
    & $pyCmd -m venv .venv
}

# Upgrade pip and install dependencies
Write-Log "Upgrading pip and installing dependencies (editable + dev)"
& $venvPy -m pip install --upgrade pip
& $venvPy -m pip install -e ".[dev]"

Write-Log "Setup complete. Run .\scripts\run_windows.ps1 to start the app."