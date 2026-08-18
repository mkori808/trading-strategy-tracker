"""Launch the API (uvicorn) and the webapp (Vite) together.

Usage: python start.py
Ctrl+C stops both.
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEBAPP = ROOT / "webapp"
IS_WINDOWS = sys.platform == "win32"


def main() -> int:
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.main:app", "--port", "8794"],
        cwd=ROOT,
    )
    webapp = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=WEBAPP,
        shell=IS_WINDOWS,
    )

    print("API running on http://localhost:8794 (PID %d)" % api.pid)
    print("Webapp dev server starting (PID %d) — see its output above for the URL" % webapp.pid)
    print("Press Ctrl+C to stop both.")

    try:
        while True:
            if api.poll() is not None:
                print("API process exited unexpectedly with code %s" % api.returncode)
                break
            if webapp.poll() is not None:
                print("Webapp process exited unexpectedly with code %s" % webapp.returncode)
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        for proc in (api, webapp):
            if proc.poll() is None:
                proc.terminate()
        for proc in (api, webapp):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
