# Launches the API (FastAPI/uvicorn) and the webapp (Vite) in separate windows.
# Usage: .\start.ps1
# Override the interpreter with: $env:PYTHON_BIN = 'C:\path\to\python.exe'

$root = $PSScriptRoot
$configuredPython = $env:PYTHON_BIN
$tradingPython = Join-Path $env:USERPROFILE 'Anaconda3\envs\trading\python.exe'

if ($configuredPython) {
    $python = $configuredPython
} elseif (Test-Path -LiteralPath $tradingPython) {
    $python = $tradingPython
} else {
    $python = (Get-Command python -ErrorAction Stop).Source
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python interpreter not found: $python"
}

$apiCommand = "Set-Location -LiteralPath '$root'; & '$python' -m uvicorn api.main:app --host 0.0.0.0 --port 8794"
$webappRoot = Join-Path $root 'webapp'
$webappCommand = "Set-Location -LiteralPath '$webappRoot'; npm run dev"

Start-Process powershell -ArgumentList @('-NoExit', '-Command', $apiCommand)
Start-Process powershell -ArgumentList @('-NoExit', '-Command', $webappCommand)

Write-Host "Started API (http://localhost:8794) and webapp (http://localhost:5174)."
