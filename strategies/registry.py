"""Strategy name -> instance registry.

Names must match strategy_tracker.xlsx's Day Trading / Swing Trading tabs
exactly (see tests/test_engine/test_registry.py, which cross-checks this
against the tracker) so the tracker stays the single source of truth for
what a strategy "is".

Most strategies here are strategies.base.Strategy instances, run through
the per-symbol engine (engine/backtest.py). A few tracker entries need a
genuinely different engine because their signal can't be expressed one
symbol at a time -- see strategies/cross_sectional.py and LESSONS.md.
Those are listed separately below rather than forced into the same dict.
"""

from __future__ import annotations

import pandas as pd

from strategies.base import Strategy
from strategies.cross_sectional import CrossSectionalStrategy
from strategies.day.mean_reversion_scalp import MeanReversionScalp
from strategies.day.momentum_gap_go import MomentumGapAndGo
from strategies.day.news_fade import NewsFade
from strategies.day.orb import OpeningRangeBreakout
from strategies.day.pivot_reversal import PivotLevelEtfReversal
from strategies.day.range_trading import RangeTrading
from strategies.day.scalping import Scalping
from strategies.day.vwap_bounce import VwapBounce
from strategies.swing.breakout_consolidation import BreakoutFromConsolidation
from strategies.swing.connors_rsi2 import ConnorsMeanReversion
from strategies.swing.dual_momentum import DualMomentum
from strategies.swing.earnings_momentum import EarningsMomentumGapHold
from strategies.swing.ema_crossover import Ema9_21Crossover
from strategies.swing.fib_retracement import FibonacciRetracementEntry
from strategies.swing.gap_fade import GapFade
from strategies.swing.internal_bar_strength import InternalBarStrength
from strategies.swing.oversold_bounce import OversoldBounce
from strategies.swing.pairs_stat_arb import PairsStatArb
from strategies.swing.pullback_21ema import PullbackTo21Ema
from strategies.swing.sector_rotation import SectorRotationPlay
from strategies.swing.turnaround_tuesday import TurnaroundTuesday

DAY_TRADING_STRATEGIES: dict[str, Strategy] = {
    s.name: s
    for s in [
        OpeningRangeBreakout(),
        VwapBounce(),
        MomentumGapAndGo(),
        Scalping(),
        MeanReversionScalp(),
        NewsFade(),
        RangeTrading(),
        PivotLevelEtfReversal(),
    ]
}

SWING_TRADING_STRATEGIES_NO_BENCHMARK: dict[str, Strategy] = {
    s.name: s
    for s in [
        PullbackTo21Ema(),
        BreakoutFromConsolidation(),
        Ema9_21Crossover(),
        OversoldBounce(),
        FibonacciRetracementEntry(),
        EarningsMomentumGapHold(),
        ConnorsMeanReversion(),
        InternalBarStrength(),
        GapFade(),
        TurnaroundTuesday(),
    ]
}

SECTOR_ROTATION_NAME = "Sector Rotation Play"


def build_swing_strategies(benchmark_bars: pd.DataFrame) -> dict[str, Strategy]:
    """Sector Rotation Play needs the SPY benchmark series at construction
    time, so swing strategies aren't a single static dict like day-trading
    ones -- call this once you have SPY bars for the backtest window."""
    strategies = dict(SWING_TRADING_STRATEGIES_NO_BENCHMARK)
    strategies[SECTOR_ROTATION_NAME] = SectorRotationPlay(benchmark_bars)
    return strategies


# Cross-sectional strategies (see strategies/cross_sectional.py): run through
# engine/cross_sectional.py, not engine/backtest.py. Also need the window's
# risk-free rate at construction time (for the absolute-momentum filter), so
# -- like Sector Rotation Play needing benchmark bars -- these are built via
# a function once the run window is known, not eagerly at import time.
CROSS_SECTIONAL_STRATEGY_NAMES: list[str] = ["Dual Momentum"]


def build_cross_sectional_strategy(name: str, risk_free_rate: float) -> CrossSectionalStrategy:
    if name == "Dual Momentum":
        return DualMomentum(risk_free_rate=risk_free_rate)
    raise ValueError(f"Unknown cross-sectional strategy {name!r}")


# Pairs strategies (see strategies/swing/pairs_stat_arb.py): run through
# engine/pairs.py -- neither the per-symbol engine (one symbol at a time)
# nor the cross-sectional one (ranks the whole universe at once) can
# express "two synchronized legs traded as one position."
PAIRS_STRATEGY_NAMES: list[str] = ["Pairs / Stat Arb"]


def build_pairs_strategy(name: str) -> PairsStatArb:
    if name == "Pairs / Stat Arb":
        return PairsStatArb()
    raise ValueError(f"Unknown pairs strategy {name!r}")


# PEAD and Overnight Hold produce standard per-symbol results but need bespoke
# construction (real per-symbol earnings seeding / a close->open engine), so
# they're built inside engine/runner.py rather than from a dict here. Named
# here so they're part of the canonical strategy set and the tracker check.
PEAD_NAME = "Post-Earnings Drift (PEAD)"
OVERNIGHT_NAME = "Overnight Hold"

# Anchored VWAP Breakout: also built inside engine/runner.py (per-symbol
# anchor dates need each symbol's own OHLCV -- same reason PEAD is bespoke
# -- plus it's the first strategy whose canonical definition bakes the
# regime + Trend Template gate into its own entry rule; see
# engine/run_avwap_breakout.py and strategies/swing/avwap_breakout.py).
AVWAP_BREAKOUT_NAME = "Anchored VWAP Breakout"


ALL_STRATEGY_NAMES: list[str] = (
    list(DAY_TRADING_STRATEGIES)
    + list(SWING_TRADING_STRATEGIES_NO_BENCHMARK)
    + [SECTOR_ROTATION_NAME]
    + CROSS_SECTIONAL_STRATEGY_NAMES
    + PAIRS_STRATEGY_NAMES
    + [PEAD_NAME, OVERNIGHT_NAME, AVWAP_BREAKOUT_NAME]
)
