"""Command-line entry point for the preregistered IBS conditioning program."""
from __future__ import annotations

import sys
from pathlib import Path

V2_ROOT = Path(__file__).resolve().parents[1]
if str(V2_ROOT) not in sys.path:
    sys.path.insert(0, str(V2_ROOT))

from strategies.ibs_conditioning import main  # noqa: E402


if __name__ == "__main__":
    main()
