# Proposed Track 4 scheduler wrapper. It is intentionally not registered.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $here 'logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
Set-Location $here
& python3.11 .\run_rebalance.py --record-daily *>> (Join-Path $logDir 'daily_record.log')
if ($LASTEXITCODE -ne 0) { throw "--record-daily failed with exit code $LASTEXITCODE" }
