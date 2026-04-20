<#
Run helper for Windows (PowerShell)

Usage:
  .\scripts\setup_windows.ps1          # install dependencies / create venv
  .\scripts\run_windows.ps1            # start the application
  .\scripts\run_windows.ps1 -Port 9000 # start on custom port
#>

param(
    [string]$Config = "config/system.json",
    [string]$LogLevel = "INFO",
    [int]$Port = 8000
)

function Write-Log([string]$m) {
    Write-Host "[run] $m"
}

function Stop-ProcessOnPort {
    param([int]$PortNum)
    try {
        $conns = Get-NetTCPConnection -LocalPort $PortNum -ErrorAction SilentlyContinue
        if ($conns) {
            $ownerPids = $conns | Select-Object -ExpandProperty OwningProcess -Unique
            foreach ($procId in $ownerPids) {
                $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
                if ($proc) {
                    Write-Log "Stopping '$($proc.ProcessName)' (PID $procId) on port $PortNum"
                    Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                }
            }
            Start-Sleep -Milliseconds 800
            Write-Log "Port $PortNum cleared."
        }
    }
    catch {
        Write-Log "Note: could not inspect port $PortNum ($_)"
    }
}

$venvPy = Join-Path -Path (Get-Location) -ChildPath ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPy)) {
    Write-Log "Virtualenv not found — running setup first..."
    $setupScript = Join-Path -Path (Get-Location) -ChildPath "scripts\setup_windows.ps1"
    if (Test-Path $setupScript) {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $setupScript
    }
    else {
        Write-Error "setup_windows.ps1 not found at: $setupScript"
    }
}

if (-not (Test-Path $venvPy)) {
    Write-Warning ".venv still missing — falling back to system Python."
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $venvPy = "python"
    }
    elseif (Get-Command py -ErrorAction SilentlyContinue) {
        $venvPy = "py"
    }
    else {
        Write-Error "No Python interpreter found. Install Python >= 3.10 and retry."
        exit 1
    }
}

Stop-ProcessOnPort -PortNum $Port

Write-Log "Starting CAN-HMI on port $Port (press Ctrl+C to stop)"
& $venvPy -m src.core.runner --config $Config --log-level $LogLevel
