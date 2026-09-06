"""Market regime filter -- a trade-gating layer over SPY daily bars.

Classifies every bar as Bullish / Neutral / Bearish from SPY's own 50- and
200-day SMAs. The gate is long-only and entry-only: in anything but Bullish,
no NEW long position is opened, but existing positions are never force-closed
by a regime change (a regime flip mid-trade is not an exit signal -- the
strategy's own stop/target/exit_signal still owns the exit).

This module is pure: `regime_series` takes bars and returns labels, with no
network access, so a backtest run against cached bars is reproducible (see
CLAUDE.md). `load_spy_bars` is the one convenience loader and it goes through
the same cached pipeline as everything else (engine/data.py).

Look-ahead: every input is a right-aligned rolling window ending at the bar
being classified, so bar i's label depends only on bars <= i. See
`engine/filters.py` for how that label is looked up at trade time, and
tests/test_engine/test_regime.py::test_no_lookahead for the causality check.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import pandas as pd

from engine import data as data_module
from engine.indicators import sma

BENCHMARK = "SPY"

BULLISH = "Bullish"
NEUTRAL = "Neutral"
BEARISH = "Bearish"

FAST_PERIOD = 50
SLOW_PERIOD = 200

# Calendar days of SPY history to prepend before a backtest window so the
# 200-day SMA is already warm on the window's first bar. Without it the first
# ~200 trading days of every run would classify as Neutral purely because the
# SMA is NaN, which would silently suppress a year of entries and make the
# filtered-vs-unfiltered comparison measure warmup rather than the filter.
# 200 trading days ~= 290 calendar days; 400 is a real margin, not a value
# sitting exactly on the limit (see LESSONS.md on parameters at their bound).
REGIME_WARMUP_DAYS = 400


def regime_series(spy_bars: pd.DataFrame) -> pd.Series:
    """Label every bar in `spy_bars` Bullish / Neutral / Bearish.

    Bullish: close > 50-SMA AND close > 200-SMA AND 50-SMA > 200-SMA
    Bearish: close < 200-SMA AND 50-SMA < 200-SMA
    Neutral: everything else, including bars where the 200-SMA isn't warm yet
             (an unknown regime must gate entries off, not on -- but prepend
             REGIME_WARMUP_DAYS of history so this doesn't happen inside a
             real backtest window).
    """
    if spy_bars.empty:
        return pd.Series(dtype=object)

    close = spy_bars["Close"]
    fast = sma(close, FAST_PERIOD)
    slow = sma(close, SLOW_PERIOD)

    bullish = (close > fast) & (close > slow) & (fast > slow)
    bearish = (close < slow) & (fast < slow)

    labels = pd.Series(NEUTRAL, index=spy_bars.index, dtype=object)
    labels[bearish] = BEARISH
    labels[bullish] = BULLISH
    # NaN SMAs compare False everywhere, so warmup bars fall through to
    # NEUTRAL on their own -- stated explicitly here because relying on
    # NaN comparison semantics silently is exactly how this gets broken later.
    labels[slow.isna()] = NEUTRAL
    return labels


def classify(spy_bars: pd.DataFrame) -> str:
    """Regime as of the last bar of `spy_bars`."""
    labels = regime_series(spy_bars)
    return NEUTRAL if labels.empty else str(labels.iloc[-1])


def load_spy_bars(start: date, end: date, warmup_days: int = REGIME_WARMUP_DAYS) -> pd.DataFrame:
    """SPY daily bars covering [start, end] plus `warmup_days` of prior
    history, from the same cached pipeline every backtest uses. Prior history
    is not look-ahead -- it is strictly older data, fetched so the SMAs are
    already warm on the window's first bar."""
    return data_module.get_bars(BENCHMARK, "1d", start - timedelta(days=warmup_days), end)


def regime_distribution(labels: pd.Series) -> dict[str, float]:
    """Share of bars spent in each regime, as fractions summing to 1.0.

    This is the diagnostic that says whether the filter is doing selective
    work at all: if SPY sat in Bullish for 90% of the window, the gate is
    barely a gate, and any performance difference it produces is close to
    noise rather than a real regime effect.
    """
    if labels.empty:
        return {BULLISH: 0.0, NEUTRAL: 0.0, BEARISH: 0.0}
    counts = Counter(labels)
    total = len(labels)
    return {state: counts.get(state, 0) / total for state in (BULLISH, NEUTRAL, BEARISH)}


def format_distribution(labels: pd.Series) -> str:
    """One-line summary of `regime_distribution`, for run logs."""
    dist = regime_distribution(labels)
    parts = ", ".join(f"{state} {share:.1%}" for state, share in dist.items())
    return f"Regime over {len(labels)} bars: {parts}"


def regime_log(labels: pd.Series) -> pd.DataFrame:
    """Per-bar regime log (date, regime, and whether it changed from the
    prior bar) -- the every-bar record CLAUDE.md's diagnostics call for,
    in a shape that's easy to write to csv or render in the dashboard."""
    if labels.empty:
        return pd.DataFrame(columns=["date", "regime", "changed"])
    return pd.DataFrame(
        {
            "date": labels.index,
            "regime": labels.values,
            "changed": labels.ne(labels.shift(1)).fillna(True).values,
        }
    )
