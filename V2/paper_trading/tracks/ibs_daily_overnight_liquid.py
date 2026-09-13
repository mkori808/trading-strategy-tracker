"""Track 4B: preregistered top-liquidity-quintile daily IBS overnight experiment."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import daily_overnight_engine as engine
import rebalance_engine as weekly_engine

TRACK_NAME = "ibs_daily_overnight_liquid"


def dry_run(as_of=None):
    return engine.dry_run(TRACK_NAME, weekly_engine.load_config(), as_of)
