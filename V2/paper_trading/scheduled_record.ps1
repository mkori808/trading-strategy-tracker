# Runs `run_rebalance.py --record` (Friday target-portfolio snapshot) and
# appends timestamped output to logs\record.log. Invoked by the
# "PaperTrading-Friday-Record" Windows Task Scheduler task -- see
# TASK_SCHEDULER.md for the schedule and how to inspect/modify it.
$ErrorActionPreference = 'Continue'
Set-Location -Path $PSScriptRoot
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'record.log'
$python = 'C:\Users\micha\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\python.exe'

"===== $(Get-Date -Format o) : --record starting =====" | Add-Content -Path $logFile
try {
    & $python run_rebalance.py --record 2>&1 | Add-Content -Path $logFile
    "===== $(Get-Date -Format o) : --record finished (exit $LASTEXITCODE) =====" | Add-Content -Path $logFile
} catch {
    "===== $(Get-Date -Format o) : --record FAILED: $_ =====" | Add-Content -Path $logFile
}
