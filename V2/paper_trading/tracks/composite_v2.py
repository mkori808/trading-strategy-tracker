"""Track 2: Composite V2 (hand-weighted). All track-specific behavior
lives in config.json under tracks.composite_v2 -- this module just names
it so it can be run/imported individually."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rebalance_engine as engine

TRACK_NAME = "composite_v2"


def run(as_of=None, dry_run: bool = True):
    cfg = engine.load_config()
    as_of = as_of or engine.most_recent_friday()
    if not dry_run:
        raise NotImplementedError("Order submission gated -- see engine.py single-account caveat.")
    return engine.dry_run_report(TRACK_NAME, cfg, as_of)


if __name__ == "__main__":
    r = run()
    print(TRACK_NAME, "held:", len(r["held"]), "buys:", len(r["buys"]), "sells:", len(r["sells"]))
