# Runs `run_rebalance.py --fill` (Monday virtual-fill against actual
# Monday-open prices) and appends timestamped output to logs\fill.log.
# Invoked by the "PaperTrading-Monday-Fill" Windows Task Scheduler task --
# see TASK_SCHEDULER.md for the schedule and how to inspect/modify it.
$ErrorActionPreference = 'Continue'
Set-Location -Path $PSScriptRoot
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'fill.log'
$python = 'C:\Users\micha\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0\python.exe'

"===== $(Get-Date -Format o) : --fill starting =====" | Add-Content -Path $logFile
try {
    & $python run_rebalance.py --fill 2>&1 | Add-Content -Path $logFile
    "===== $(Get-Date -Format o) : --fill finished (exit $LASTEXITCODE) =====" | Add-Content -Path $logFile
} catch {
    "===== $(Get-Date -Format o) : --fill FAILED: $_ =====" | Add-Content -Path $logFile
}
