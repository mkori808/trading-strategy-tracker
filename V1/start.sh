#!/usr/bin/env bash
# Launches the API (FastAPI/uvicorn) and the webapp (Vite) together.
#
# Usage: ./start.sh
# Ctrl+C stops both.
#
# Uses the "trading" conda env's interpreter directly, regardless of what's
# active in the current shell -- PATH-based `python` lookups in this project
# have resolved to broken/mismatched interpreters (Windows Store Python,
# conda "general" with a numpy/pandas ABI mismatch). Override with
# PYTHON_BIN=/path/to/python.exe ./start.sh if you need a different one.
#
# Port 8794 avoids orphaned listeners left on the previous development ports.
# Run a single uvicorn process so cleanup does not leave a reloader child behind.
# Keep this and webapp/vite.config.ts's proxy target synchronized.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/c/Users/micha/Anaconda3/envs/trading/python.exe}"

cleanup() {
    echo
    echo "Stopping..."
    kill "$API_PID" "$WEBAPP_PID" 2>/dev/null
    wait "$API_PID" "$WEBAPP_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

(cd "$ROOT" && "$PYTHON_BIN" -m uvicorn api.main:app --host 0.0.0.0 --port 8794) &
API_PID=$!

(cd "$ROOT/webapp" && npm run dev) &
WEBAPP_PID=$!

echo "API running on http://localhost:8794 (PID $API_PID)"
echo "Webapp dev server starting (PID $WEBAPP_PID) -- see its output above for the URL"
echo "Press Ctrl+C to stop both."

wait "$API_PID" "$WEBAPP_PID"
