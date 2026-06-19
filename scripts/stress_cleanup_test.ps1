$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$baseCfgPath = Join-Path $root 'config/system.json'
$baseCfgObj = Get-Content $baseCfgPath -Raw | ConvertFrom-Json
$pythonExe = Join-Path $root '.venv/Scripts/python.exe'
if (-not (Test-Path $pythonExe)) { throw "Python venv not found: $pythonExe" }

$runTag = Get-Date -Format 'yyyyMMdd_HHmmss'
$workDir = Join-Path $root ("data/cleanup_stress/" + $runTag)
$logDir = Join-Path $root ("logs/cleanup_stress/" + $runTag)
New-Item -ItemType Directory -Force -Path $workDir | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$baselineDb = Join-Path $root 'data/signals.db'
$results = @()

Write-Host "Stress run tag: $runTag"

for ($i = 1; $i -le 10; $i++) {
    Write-Host "RUN $i/10 starting"

    $cfg = ($baseCfgObj | ConvertTo-Json -Depth 20 | ConvertFrom-Json)
    $cfg.storage.max_disk_mb = 5
    $cfg.storage.retention_days = 30
    $cfg.storage.sqlite_path = "data/cleanup_stress/$runTag/stress_$i.db"
    $cfg.logging.file_path = "logs/cleanup_stress/$runTag/run_$i.log"

    $cfgPath = Join-Path $workDir "system_stress_$i.json"
    $dbPath = Join-Path $root $cfg.storage.sqlite_path
    $logPath = Join-Path $root $cfg.logging.file_path
    $outPath = Join-Path $logDir "run_$i.stdout.log"
    $errPath = Join-Path $logDir "run_$i.stderr.log"

    foreach ($p in @($cfgPath, $dbPath, ($dbPath + '-wal'), ($dbPath + '-shm'), $logPath, $outPath, $errPath)) {
        if (Test-Path $p) { Remove-Item $p -Force }
    }

    if (Test-Path $baselineDb) {
        Copy-Item $baselineDb $dbPath -Force
    }

    $jsonText = $cfg | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($cfgPath, $jsonText, (New-Object System.Text.UTF8Encoding($false)))

    $proc = Start-Process -FilePath $pythonExe -ArgumentList @('-m', 'src.core.runner', '--config', $cfgPath, '--log-level', 'INFO') -PassThru -WorkingDirectory $root -RedirectStandardOutput $outPath -RedirectStandardError $errPath

    try {
        Wait-Process -Id $proc.Id -Timeout 28 -ErrorAction SilentlyContinue
    }
    finally {
        $stillRunning = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if ($stillRunning) {
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }

    $logText = if (Test-Path $logPath) { Get-Content $logPath -Raw } else { '' }
    $stdoutText = if (Test-Path $outPath) { Get-Content $outPath -Raw } else { '' }
    $stderrText = if (Test-Path $errPath) { Get-Content $errPath -Raw } else { '' }
    $combined = "$logText`n$stdoutText`n$stderrText"

    $hasTrim = ($combined -match 'DB size .*trimming oldest records') -or ($combined -match 'DB trim complete:') -or ($combined -match 'Skip VACUUM this cycle:')
    $hasTraceback = $combined -match 'Traceback \(most recent call last\)'
    $hasVacuumStmtErr = $combined -match 'cannot VACUUM - SQL statements in progress'
    $hasTxnErr = $combined -match 'cannot start a transaction within a transaction'
    $hasRollbackErr = $combined -match 'cannot rollback - no transaction is active'
    $hasRetentionFailed = $combined -match 'Retention cleanup failed'

    $dbBytes = 0
    foreach ($p in @($dbPath, ($dbPath + '-wal'), ($dbPath + '-shm'))) {
        if (Test-Path $p) {
            $dbBytes += (Get-Item $p).Length
        }
    }

    $pass = ($hasTrim -and -not $hasTraceback -and -not $hasVacuumStmtErr -and -not $hasTxnErr -and -not $hasRollbackErr -and -not $hasRetentionFailed)
    $results += [pscustomobject]@{
        run = $i
        trim_event = $hasTrim
        traceback = $hasTraceback
        vacuum_stmt_err = $hasVacuumStmtErr
        nested_txn_err = $hasTxnErr
        rollback_err = $hasRollbackErr
        retention_failed = $hasRetentionFailed
        db_mb = [Math]::Round($dbBytes / 1MB, 2)
        pass = $pass
    }

    Write-Host ("RUN {0} done | trim={1} traceback={2} vacuum_stmt_err={3} nested_txn_err={4} rollback_err={5} retention_failed={6} db_mb={7}" -f $i, $hasTrim, $hasTraceback, $hasVacuumStmtErr, $hasTxnErr, $hasRollbackErr, $hasRetentionFailed, ([Math]::Round($dbBytes / 1MB, 2)))
}

$resultPath = Join-Path $logDir 'summary.json'
[System.IO.File]::WriteAllText($resultPath, ($results | ConvertTo-Json -Depth 5), (New-Object System.Text.UTF8Encoding($false)))
$results | Format-Table -AutoSize
$passCount = ($results | Where-Object { $_.pass }).Count
Write-Output "PASS_COUNT=$passCount/10"
Write-Output "SUMMARY_JSON=$resultPath"
