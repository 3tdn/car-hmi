# CAN-HMI Setup Script - Windows (PowerShell 5.1+)
# Prepares a local virtualenv and installs project dependencies from pyproject.toml

$ErrorActionPreference = 'Stop'

function Log { param([string]$Message) Write-Host "[setup] $Message" -ForegroundColor Green }
function LogError { param([string]$Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }

Log "Checking for Python installation..."

# Detect python: prefer 'python', fall back to 'py -3' launcher
$pythonCmd = $null
$pythonArgs = @()

if (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = (Get-Command python).Source
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = (Get-Command py).Source
    $pythonArgs = @('-3')
} else {
    LogError "Python not found! Please install Python 3.10+ from https://www.python.org or Microsoft Store"
    exit 1
}

# Verify version
$verOut = & $pythonCmd @($pythonArgs + @('--version')) 2>&1
if ($LASTEXITCODE -ne 0) { LogError "Failed to execute Python: $verOut"; exit 1 }
Log "Found Python: $verOut"
Log "Python executable: $pythonCmd $($pythonArgs -join ' ')"

# Create virtualenv if missing
if (-not (Test-Path ".venv")) {
    Log "Creating virtualenv (.venv)..."
    & $pythonCmd @($pythonArgs + @('-m','venv','.venv'))
    if ($LASTEXITCODE -ne 0) { LogError "Failed to create virtualenv"; exit 1 }
    Log ".venv created at $(Get-Location)\.venv"
} else {
    Log ".venv already exists, skipping creation"
}

$venvPython = ".\.venv\Scripts\python.exe"
$venvPip = ".\.venv\Scripts\pip.exe"

if (-not (Test-Path $venvPython)) {
    LogError "Virtual environment python not found at $venvPython"
    Log "Attempting to recreate .venv..."
    Remove-Item ".venv" -Recurse -Force -ErrorAction SilentlyContinue
    & $pythonCmd @($pythonArgs + @('-m','venv','.venv'))
    if ($LASTEXITCODE -ne 0) { LogError "Failed to recreate virtualenv"; exit 1 }
}

# Ensure pip available inside venv
Log "Checking pip in virtualenv..."
try {
    $pipCheck = & $venvPython -m pip --version 2>&1
    Log "pip is available: $pipCheck"
} catch {
    Log "pip not found in venv - attempting to install via ensurepip..."
    try {
        & $venvPython -m ensurepip --upgrade
        Log "pip installed via ensurepip"
    } catch {
        Log "ensurepip failed - attempting to bootstrap pip via get-pip.py"
        try {
            $tmp = [System.IO.Path]::GetTempFileName()
            Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $tmp -UseBasicParsing
            & $venvPython $tmp
            $exit = $LASTEXITCODE
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
            if ($exit -ne 0) { LogError "Failed to install pip into venv (get-pip.py returned code $exit)"; exit 1 }
            Log "pip installed via get-pip.py"
        } catch {
            LogError "Failed to bootstrap pip via get-pip.py: $_"
            exit 1
        }
    }
}

# Upgrade pip
Log "Upgrading pip to latest version..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { LogError "Failed to upgrade pip"; exit 1 }

# Install project dependencies (editable + dev extras)
Log "Installing project dependencies (editable + dev packages)..."
Log "This may take a minute or two depending on network and disk speed..."
& $venvPython -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { LogError "Failed to install project dependencies"; exit 1 }

Log ""
Log "Setup complete."
Log "To activate the venv: .\.venv\Scripts\Activate.ps1"
