"""Track 4: frozen Composite V3.2 forward paper observation.

Canonical specification: IBS 0.69, RSI2 0.25, Turnaround Tuesday 0.06;
Sector Rotation removed; no Gap Fade; no transition filter.  Any change must
use a new strategy/version identifier.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rebalance_engine as engine

TRACK_NAME = "composite_v3_2"


def run(as_of=None, dry_run: bool = True):
    cfg = engine.load_config()
    as_of = as_of or engine.most_recent_friday()
    if not dry_run:
        raise NotImplementedError("Order submission gated -- see engine.py single-account caveat.")
    return engine.dry_run_report(TRACK_NAME, cfg, as_of)


if __name__ == "__main__":
    result = run()
    print(TRACK_NAME, "held:", len(result["held"]), "buys:", len(result["buys"]), "sells:", len(result["sells"]))
