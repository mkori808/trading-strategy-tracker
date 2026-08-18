"""Forward-test tracking for the frozen Dual Momentum config.

Tracks THREE series in parallel from the freeze date (2026-08-11). They answer
different questions and are deliberately not interchangeable -- the temptation
during a bad stretch is to report whichever looks best, so all three are
computed together and the stop is bound to exactly one of them.

    1. vs EQUAL-WEIGHT POINT-IN-TIME DOW   -> DRIVES THE STOP
       "Does the ranking work?" Holds the same names the strategy picks from,
       so it controls for the universe. This is the thing genuinely uncertain.

    2. vs SPY                              -> ALLOCATION QUESTION ONLY
       "Should capital be here instead of an index fund?" Explicitly does NOT
       drive the stop: measured in backtest, the strategy trailed SPY by 16.4pp
       over 2023-04..2024-12 while BEATING equal-weight Dow by 0.6pp. The whole
       shortfall was the Dow trailing the S&P. A SPY-bound stop would have
       fired on a universe effect.

    3. vs RANDOM 5-of-29                   -> LIVE CONCENTRATION CONTROL
       Isolates ranking from concentration in live data rather than backtest.
       Random 5-name portfolios returned 70.6% against equal-weight Dow's 74.0%
       in the development window, so ~0.44%/yr of the strategy's shortfall
       versus a broad index is the cost of holding five names rather than any
       failure of the signal. Without this series that cost is invisible and
       gets misattributed to the ranking.

Thresholds come from FROZEN_DUAL_MOMENTUM.md and are NOT redefined here -- a
second copy is a second thing to drift. This module reads them as constants and
reports against them; it never decides them.

Nothing here modifies the config. It measures.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from engine import data as data_module
from engine.sanity import check_return, check_window

# --- Frozen constants, mirrored from FROZEN_DUAL_MOMENTUM.md -----------------
FREEZE_DATE = date(2026, 8, 11)
STOP_SHORTFALL_PP = 15.0     # vs EW point-in-time Dow
STOP_HORIZON_MONTHS = 24
CONTINUE_HORIZON_MONTHS = 48
RANDOM_CONTROL_DRAWS = 400

TRACKING_PATH = Path(__file__).resolve().parent.parent / "logs" / "forward_tracking.json"


@dataclass
class SeriesPoint:
    """One observation of all three benchmarks at a point in time."""

    asof: str
    months_elapsed: float
    strategy_return_pct: float
    ew_pit_dow_return_pct: float | None
    spy_return_pct: float | None
    random_median_return_pct: float | None
    #: strategy minus EW PIT Dow. THE STOP SERIES.
    vs_ew_pit_dow_pp: float | None
    #: strategy minus SPY. Allocation question; never drives the stop.
    vs_spy_pp: float | None
    #: strategy minus the median random 5-name portfolio. Concentration control.
    vs_random_pp: float | None


@dataclass
class StopDecision:
    """What the frozen rule says right now -- not a judgement call."""

    months_elapsed: float
    shortfall_vs_ew_pp: float | None
    triggered: bool
    verdict: str
    reasoning: str


def evaluate_stop(months: float, vs_ew_pp: float | None) -> StopDecision:
    """Apply the frozen rule mechanically. No discretion anywhere in here.

    The three outcomes at 24 months are fixed in the frozen file. Two of them
    are "continue unchanged", and that is deliberate: ambiguity is the EXPECTED
    result at this horizon, not a signal. Encoding it means the decision is made
    by the rule written before the data existed rather than by whoever is
    reading the numbers.
    """
    if vs_ew_pp is None:
        return StopDecision(months, None, False,
                            "insufficient data",
                            "No benchmark comparison available yet.")
    if months < STOP_HORIZON_MONTHS:
        return StopDecision(
            months, vs_ew_pp, False, "running",
            f"{months:.1f} of {STOP_HORIZON_MONTHS} months elapsed. The stop is "
            f"evaluated AT {STOP_HORIZON_MONTHS} months, not continuously -- an "
            "early breach is inside observed dispersion and is not a stop.",
        )
    if vs_ew_pp < -STOP_SHORTFALL_PP:
        return StopDecision(
            months, vs_ew_pp, True, "STOP - falsified",
            f"Trails equal-weight point-in-time Dow by {abs(vs_ew_pp):.1f}pp, "
            f"beyond the {STOP_SHORTFALL_PP:.0f}pp threshold. The frozen rule "
            "says stop and do NOT tune.",
        )
    return StopDecision(
        months, vs_ew_pp, False, "not falsified - NOT confirmed",
        f"Ahead of, or within {STOP_SHORTFALL_PP:.0f}pp of, equal-weight "
        f"point-in-time Dow at {months:.1f} months. This is NOT confirmation: "
        f"~{months:.0f} monthly observations cannot distinguish a +2%/yr effect "
        f"from noise with 3-year swings to -12pp. Continue unchanged to "
        f"{CONTINUE_HORIZON_MONTHS} months.",
    )


def _window_return(bars: pd.DataFrame, start: date, end: date) -> float | None:
    if bars is None or bars.empty:
        return None
    tz = getattr(bars.index, "tz", None)
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    if tz is not None:
        lo, hi = lo.tz_localize(tz), hi.tz_localize(tz)
    seg = bars[(bars.index >= lo) & (bars.index <= hi)]["Close"]
    if len(seg) < 2:
        return None
    return float(seg.iloc[-1] / seg.iloc[0] - 1) * 100


def equal_weight_return(symbols: list[str], start: date, end: date) -> float | None:
    """Equal-weight buy-and-hold across `symbols` -- the diagnostic benchmark.

    Point-in-time membership is the caller's responsibility: pass the roster as
    it stood at `start`, not today's. Using today's roster here would reinstate
    exactly the survivorship the frozen file records as the largest unresolved
    weakness.
    """
    rets = []
    for sym in symbols:
        bars = data_module.get_bars(sym, "1d", start, end)
        check_window(bars, start, end, label=f"{sym} tracking window")
        r = _window_return(bars, start, end)
        if r is not None:
            check_return(r, label=f"{sym} window return")
            rets.append(r)
    return float(np.mean(rets)) if rets else None


def random_control_returns(
    symbols: list[str], start: date, end: date, top_n: int, draws: int = RANDOM_CONTROL_DRAWS
) -> list[float]:
    """Buy-and-hold returns of `draws` random `top_n`-name portfolios.

    The live counterpart of the backtest randomization. Buy-and-hold rather than
    rebalanced because it isolates CONCENTRATION alone -- adding rebalancing
    would fold turnover cost into a control meant to measure only the effect of
    holding few names instead of many.
    """
    rng = np.random.default_rng(20260811)
    per_symbol: dict[str, float] = {}
    for sym in symbols:
        bars = data_module.get_bars(sym, "1d", start, end)
        r = _window_return(bars, start, end)
        if r is not None:
            per_symbol[sym] = r
    if len(per_symbol) < top_n:
        return []
    names = list(per_symbol)
    out = []
    for _ in range(draws):
        pick = rng.choice(len(names), size=top_n, replace=False)
        out.append(float(np.mean([per_symbol[names[i]] for i in pick])))
    return out


def record_observation(point: SeriesPoint, path: Path = TRACKING_PATH) -> None:
    """Append one observation. Append-only: never rewrite a past point.

    A forward test whose history can be edited is not a forward test. Same
    reasoning as versioning metrics rows rather than overwriting them.
    """
    path.parent.mkdir(exist_ok=True)
    existing = json.loads(path.read_text()) if path.exists() else []
    existing.append(asdict(point))
    path.write_text(json.dumps(existing, indent=2))


def load_history(path: Path = TRACKING_PATH) -> list[dict]:
    return json.loads(path.read_text()) if path.exists() else []
