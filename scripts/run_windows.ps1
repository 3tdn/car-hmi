<#
Run helper for Windows (PowerShell)

Usage:
  .\scripts\setup_windows.ps1   # install dependencies / create venv
  .\scripts\run_windows.ps1     # start the application
#>

param(
    [string]$Config = "config/bus.yaml",
    [string]$LogLevel = "INFO"
)

function Write-Log([string]$m){ Write-Host "[run] $m" }

$venvPy = Join-Path -Path (Get-Location) -ChildPath ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Error "Virtualenv not found. Run .\scripts\setup_windows.ps1 first."
    exit 1
}

Write-Log "Starting CAN-HMI (press Ctrl+C to stop)"
& $venvPy -m src.core.runner --config $Config --log-level $LogLevel
