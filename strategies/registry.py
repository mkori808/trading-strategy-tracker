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

from dataclasses import dataclass

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
from strategies.swing.frozen_research import (
    High52WeekMomentum,
    MarketResidualMomentum,
    MaxLotteryReversal,
    NegativeVolumeShockReversal,
    UnavailableResearchStrategy,
    VolatilityConditionedPullback,
    VolumeShockContinuation,
)

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

# This user-defined variant is intentionally separate from the tracker-backed
# catalogue above: it combines the existing Dual Momentum regime logic with a
# short-term pullback entry and is surfaced in the UI for research.  Add it to
# the tracker only after the user decides its rule definition is final.
DUAL_MOMENTUM_PULLBACK_NAME = "Dual Momentum Pullback Swing"
USER_DEFINED_STRATEGY_NAMES: list[str] = [DUAL_MOMENTUM_PULLBACK_NAME]

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
CROSS_SECTIONAL_STRATEGY_NAMES: list[str] = [
    "Dual Momentum", "52-Week-High Momentum", "Market-Residual Momentum",
]


def build_cross_sectional_strategy(
    name: str, risk_free_rate: float, benchmark_bars: pd.DataFrame | None = None,
) -> CrossSectionalStrategy:
    if name == "Dual Momentum":
        return DualMomentum(risk_free_rate=risk_free_rate)
    if name == "52-Week-High Momentum":
        return High52WeekMomentum()
    if name == "Market-Residual Momentum":
        return MarketResidualMomentum(benchmark_bars=benchmark_bars)
    raise ValueError(f"Unknown cross-sectional strategy {name!r}")


FROZEN_EVENT_STRATEGY_NAMES = [
    "Negative Return + Volume Shock Reversal",
    "Volume-Shock Continuation (Long)",
    "Volume-Shock Continuation (Short)",
    "MAX Lottery-Return Reversal (Short)",
    "Volatility-Conditioned Pullback",
]


def build_frozen_event_strategy(name: str):
    if name == "Negative Return + Volume Shock Reversal":
        return NegativeVolumeShockReversal()
    if name == "Volume-Shock Continuation (Long)":
        return VolumeShockContinuation(direction="long")
    if name == "Volume-Shock Continuation (Short)":
        return VolumeShockContinuation(direction="short")
    if name == "MAX Lottery-Return Reversal (Short)":
        return MaxLotteryReversal()
    if name == "Volatility-Conditioned Pullback":
        return VolatilityConditionedPullback()
    raise ValueError(f"Unknown frozen event strategy {name!r}")


# Clean refusals required by the protocol. These remain registered so the UI
# explains why no result exists instead of silently omitting the hypothesis.
UNAVAILABLE_RESEARCH_STRATEGIES: dict[str, str] = {
    "Earnings Announcement Return Drift (EAR)": (
        "Unavailable: the installed earnings feed is not a complete point-in-time event ledger "
        "for historical Dow members, so cross-sectional EAR ranks would have selective event coverage."
    ),
    "Sector-Relative Momentum": (
        "Unavailable: no point-in-time historical sector-classification ledger is installed; "
        "today's sector labels are prohibited."
    ),
    "Overnight Idiosyncratic Shock Reversal (Long)": (
        "Execution unsupported: observing Open[T] and filling that same Open[T] cannot be modeled "
        "honestly with daily bars."
    ),
    "Overnight Idiosyncratic Shock Reversal (Short)": (
        "Execution unsupported: observing Open[T] and filling that same Open[T] cannot be modeled "
        "honestly with daily bars."
    ),
}


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
    + FROZEN_EVENT_STRATEGY_NAMES
    + list(UNAVAILABLE_RESEARCH_STRATEGIES)
    + USER_DEFINED_STRATEGY_NAMES
)


@dataclass(frozen=True)
class ArchivedStrategy:
    """A strategy retired from the ACTIVE dashboard/leaderboard after a
    large-enough sample showed decisively negative expectancy (or, for the
    portfolio engines, negative return). See ARCHIVED_STRATEGIES.md for the
    full rationale per strategy and LESSONS.md's 2026-07-20 entries for how
    each number was reached."""

    reason: str
    trades_taken: int
    expectancy_r: float | None  # None for portfolio-engine strategies (no R-multiples)
    return_pct: float | None  # only set for portfolio-engine strategies
    archived_at: str  # ISO date


# Purely additive: does NOT remove anything from DAY_TRADING_STRATEGIES /
# SWING_TRADING_STRATEGIES_NO_BENCHMARK / ALL_STRATEGY_NAMES above, all of
# which must still match strategy_tracker.xlsx 1:1 (see
# tests/test_engine/test_registry.py) -- the tracker is the full candidate
# list; this is which of those candidates the app still actively surfaces
# by default. Every archived strategy's code, run history, and backtest
# reproducibility are fully intact -- only default visibility in
# /api/strategies and the webapp changes (see api/main.py's `archived`
# field and the webapp's "Show archived" toggle). Numbers are each
# strategy's canonical figures as of its archive date.
ARCHIVED_STRATEGY_NAMES: dict[str, ArchivedStrategy] = {
    "Opening Range Breakout (ORB)": ArchivedStrategy(
        reason="Large sample, consistently negative expectancy.",
        trades_taken=6121, expectancy_r=-0.012, return_pct=None, archived_at="2026-07-20",
    ),
    "VWAP Bounce / Reversion": ArchivedStrategy(
        reason="Large sample, consistently negative expectancy.",
        trades_taken=104672, expectancy_r=-0.107, return_pct=None, archived_at="2026-07-20",
    ),
    "Scalping (3-5 min)": ArchivedStrategy(
        reason="Large sample, consistently negative expectancy.",
        trades_taken=21774, expectancy_r=-0.200, return_pct=None, archived_at="2026-07-20",
    ),
    "Mean Reversion Scalp": ArchivedStrategy(
        reason="Large sample, consistently negative expectancy.",
        trades_taken=419, expectancy_r=-0.102, return_pct=None, archived_at="2026-07-20",
    ),
    "News Fade": ArchivedStrategy(
        reason="Large sample, consistently negative expectancy.",
        trades_taken=1047, expectancy_r=-0.260, return_pct=None, archived_at="2026-07-20",
    ),
    "Range Trading": ArchivedStrategy(
        reason="Large sample, consistently negative expectancy.",
        trades_taken=3293, expectancy_r=-0.066, return_pct=None, archived_at="2026-07-20",
    ),
    "Fibonacci Retracement Entry": ArchivedStrategy(
        reason="Large sample, consistently negative expectancy.",
        trades_taken=421, expectancy_r=-0.045, return_pct=None, archived_at="2026-07-20",
    ),
    "Gap Fade (daily)": ArchivedStrategy(
        reason="Large sample, consistently negative expectancy.",
        trades_taken=328, expectancy_r=-0.150, return_pct=None, archived_at="2026-07-20",
    ),
    "Turnaround Tuesday": ArchivedStrategy(
        reason="Large sample, consistently negative expectancy.",
        trades_taken=346, expectancy_r=-0.034, return_pct=None, archived_at="2026-07-20",
    ),
    "Pairs / Stat Arb": ArchivedStrategy(
        reason="Negative return and Sharpe on the portfolio engine "
        "(no R-multiple trades to express as expectancy).",
        trades_taken=0, expectancy_r=None, return_pct=-13.2, archived_at="2026-07-20",
    ),
}
