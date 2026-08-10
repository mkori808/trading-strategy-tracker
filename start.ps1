# Launches the API (FastAPI/uvicorn) and the webapp (Vite) in separate windows.
# Usage: .\start.ps1

$root = $PSScriptRoot

Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command',
    "Set-Location '$root'; uvicorn api.main:app --port 8791 --reload"
)

Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command',
    "Set-Location '$root\webapp'; npm run dev"
)

Write-Host "Started API (port 8791) and webapp dev server in separate windows."
