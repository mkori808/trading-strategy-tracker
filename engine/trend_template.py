"""Minervini Trend Template -- an 8-point stock-selection pre-filter.

This is not an entry signal. It answers a prior question: is this symbol even
a candidate today? All 8 criteria must hold simultaneously; any single
failure disqualifies the symbol for that date, with no partial credit and no
weighting.

Computable from daily OHLCV alone (plus SPY for the relative-strength leg) --
no fundamental data. Evaluated once per symbol per scan date, not per bar:
`trend_template_frame` computes the whole history vectorized in one pass and
callers look up the date they need (see engine/filters.py).

The 8 criteria, in the canonical order:
  1. Close > 150-day SMA and Close > 200-day SMA
  2. 150-day SMA > 200-day SMA
  3. 200-day SMA rising -- positive linear-regression slope over the last
     SLOPE_LOOKBACK bars (>= MIN_SLOPE_LOOKBACK required)
  4. 50-day SMA > 150-day SMA and 50-day SMA > 200-day SMA
  5. Close > 50-day SMA
  6. Close >= 52-week low * 1.25  (at least 25% off the low)
  7. Close >= 52-week high * 0.75 (within 25% of the high)
  8. Relative strength: the symbol's 12-month return > SPY's 12-month return

Criterion 8 note, stated rather than glossed: Minervini's own screen uses an
IBD-style RS *rank* of 70+, which is a percentile against the entire market
-- that needs a full-market cross-section this project doesn't have. The rule
implemented here is the benchmark-relative version specified for this build
(beat SPY over 12 months). It is a weaker filter than a true 70th-percentile
rank: beating SPY puts a name somewhere above roughly the median, not
necessarily in the top 30%. Treat the pass counts accordingly.

Look-ahead prevention -- every one of these is a place it could creep in:
  * SMAs use pandas' default right-aligned rolling window (the window ENDS at
    the bar being evaluated), never `center=True`.
  * The 200-SMA slope is a rolling regression over the trailing window, not a
    fit over the whole series.
  * 52-week high/low are `rolling(252).max()/min()`, never a full-series
    `.max()/.min()` -- the classic version of this bug, where every bar gets
    to see the entire backtest's extremes.
  * The RS leg uses `pct_change(252)` (current vs. 252 bars ago) and aligns
    SPY onto the symbol's index with a FORWARD fill, which carries the last
    PRIOR SPY close onto a gap -- a backward fill here would pull a future
    SPY price into today's comparison.
  * Warmup history is prepended BEFORE the window start (older data), never
    borrowed from after it.
Together these make row i a function of rows <= i only, which
tests/test_engine/test_trend_template.py::test_no_lookahead asserts directly
by recomputing on truncated data and comparing.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from engine.indicators import sma

FAST_PERIOD = 50
MID_PERIOD = 150
SLOW_PERIOD = 200

TRADING_DAYS_PER_YEAR = 252

# Criterion 3: 21 trading days is the stated minimum; 84-105 is the preferred
# range because a 21-bar regression on a 200-bar average is short enough to
# flip on ordinary noise. Default to the top of that range.
SLOPE_LOOKBACK = 105
MIN_SLOPE_LOOKBACK = 21

# Criterion 6/7 thresholds.
MIN_ABOVE_52W_LOW = 1.25
MAX_BELOW_52W_HIGH = 0.75

# Calendar days of history to prepend before a backtest window. The deepest
# dependency is the 200-day SMA feeding a 105-bar regression = 305 trading
# days ~= 445 calendar days. 650 leaves real margin (LESSONS.md: a parameter
# sitting exactly at its limit is a bug waiting for the wrong input).
TREND_WARMUP_DAYS = 650

CRITERIA = [
    "above_150_and_200",
    "sma150_above_sma200",
    "sma200_rising",
    "sma50_above_150_and_200",
    "above_sma50",
    "above_52w_low",
    "near_52w_high",
    "rs_beats_benchmark",
]


def rolling_slope(series: pd.Series, window: int) -> pd.Series:
    """Least-squares slope of `series` over a trailing `window`-bar window,
    one value per bar. Right-aligned: the window ends at the bar it labels.

    Closed-form rather than np.polyfit per window -- x is a fixed 0..n-1 ramp
    so its mean and variance are constants, and only the y side varies."""
    x = np.arange(window, dtype=float)
    x_centered = x - x.mean()
    x_var = float((x_centered**2).sum())

    def _slope(y: np.ndarray) -> float:
        return float((x_centered * (y - y.mean())).sum() / x_var)

    return series.rolling(window).apply(_slope, raw=True)


def trend_template_frame(
    bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame,
    slope_lookback: int = SLOPE_LOOKBACK,
) -> pd.DataFrame:
    """Per-bar evaluation of all 8 criteria plus a combined `passes` column.

    Returning the individual criteria (not just the verdict) is deliberate:
    "which criterion rejected this name" is the diagnostic that makes the
    filter auditable instead of a black box, and it's what `scan_summary`
    reports on.

    Bars where any input isn't warm yet evaluate to False, never NaN -- an
    unknown is a fail for a filter whose whole contract is "all 8 or nothing."
    """
    if slope_lookback < MIN_SLOPE_LOOKBACK:
        raise ValueError(
            f"slope_lookback={slope_lookback} is below the {MIN_SLOPE_LOOKBACK}-day "
            "minimum for a meaningful 200-SMA trend regression"
        )
    if bars.empty:
        return pd.DataFrame(columns=[*CRITERIA, "passes"])

    close = bars["Close"]
    sma50 = sma(close, FAST_PERIOD)
    sma150 = sma(close, MID_PERIOD)
    sma200 = sma(close, SLOW_PERIOD)

    # Trailing 52 weeks, right-aligned. NOT close.max()/min() over the series.
    high_52w = bars["High"].rolling(TRADING_DAYS_PER_YEAR).max()
    low_52w = bars["Low"].rolling(TRADING_DAYS_PER_YEAR).min()

    twelve_month_return = close.pct_change(TRADING_DAYS_PER_YEAR)
    benchmark_return = _aligned_benchmark_return(benchmark_bars, bars.index)

    criteria = pd.DataFrame(index=bars.index)
    criteria["above_150_and_200"] = (close > sma150) & (close > sma200)
    criteria["sma150_above_sma200"] = sma150 > sma200
    criteria["sma200_rising"] = rolling_slope(sma200, slope_lookback) > 0
    criteria["sma50_above_150_and_200"] = (sma50 > sma150) & (sma50 > sma200)
    criteria["above_sma50"] = close > sma50
    criteria["above_52w_low"] = close >= low_52w * MIN_ABOVE_52W_LOW
    criteria["near_52w_high"] = close >= high_52w * MAX_BELOW_52W_HIGH
    criteria["rs_beats_benchmark"] = twelve_month_return > benchmark_return

    criteria = criteria.fillna(False).astype(bool)
    criteria["passes"] = criteria[CRITERIA].all(axis=1)
    return criteria


def _aligned_benchmark_return(benchmark_bars: pd.DataFrame, index: pd.Index) -> pd.Series:
    """SPY's trailing 12-month return aligned onto `index`.

    Forward fill, deliberately: on a date the benchmark has no bar for, this
    carries the last PRIOR benchmark observation forward. A backward fill --
    or an interpolate -- would reach into the future for a value the filter
    could not have known, which is precisely the bug this filter is supposed
    to be free of. Missing benchmark data yields NaN, which fails criterion 8
    rather than silently passing it."""
    if benchmark_bars.empty:
        return pd.Series(np.nan, index=index)
    benchmark_return = benchmark_bars["Close"].pct_change(TRADING_DAYS_PER_YEAR)
    return benchmark_return.reindex(index, method="ffill")


def passes_trend_template(
    bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame,
    slope_lookback: int = SLOPE_LOOKBACK,
) -> bool:
    """True only if all 8 criteria hold as of the LAST bar of `bars`.

    The single-date entry point: pass bars truncated at the scan date and
    nothing after it is visible, by construction rather than by convention."""
    frame = trend_template_frame(bars, benchmark_bars, slope_lookback)
    return bool(frame["passes"].iloc[-1]) if not frame.empty else False


def failed_criteria(
    bars: pd.DataFrame,
    benchmark_bars: pd.DataFrame,
    slope_lookback: int = SLOPE_LOOKBACK,
) -> list[str]:
    """Which criteria a symbol failed as of its last bar -- the 'why' behind
    a rejection, for scan logs and dashboard drill-down."""
    frame = trend_template_frame(bars, benchmark_bars, slope_lookback)
    if frame.empty:
        return list(CRITERIA)
    last = frame.iloc[-1]
    return [name for name in CRITERIA if not bool(last[name])]


def load_bars_with_warmup(
    symbol: str,
    start: date,
    end: date,
    warmup_days: int = TREND_WARMUP_DAYS,
) -> pd.DataFrame:
    """Daily bars for [start, end] plus `warmup_days` of PRIOR history, so the
    200-SMA and 52-week windows are warm on the window's first bar. Imported
    lazily so this module stays importable (and unit-testable) without the
    data layer."""
    from engine import data as data_module

    return data_module.get_bars(symbol, "1d", start - timedelta(days=warmup_days), end)


def scan_summary(
    per_symbol_frames: dict[str, pd.DataFrame],
    dates: pd.Index | None = None,
) -> pd.DataFrame:
    """How selective the template actually is: pass/fail counts per scan date
    across the universe, plus the most common reason for rejection.

    CLAUDE.md asks for this explicitly, and for the same reason the regime
    distribution matters -- a filter that passes 95% of the universe every
    day isn't filtering, and a filter that passes 0% isn't either. Both
    failure modes are invisible without counting.
    """
    if not per_symbol_frames:
        return pd.DataFrame(columns=["date", "passed", "failed", "pass_rate", "top_failure"])

    if dates is None:
        dates = sorted(set().union(*(frame.index for frame in per_symbol_frames.values())))

    rows = []
    for scan_date in dates:
        passed = 0
        evaluated = 0
        failure_counts: dict[str, int] = {}
        for frame in per_symbol_frames.values():
            if scan_date not in frame.index:
                continue
            evaluated += 1
            row = frame.loc[scan_date]
            if bool(row["passes"]):
                passed += 1
                continue
            for name in CRITERIA:
                if not bool(row[name]):
                    failure_counts[name] = failure_counts.get(name, 0) + 1
        if not evaluated:
            continue
        rows.append(
            {
                "date": scan_date,
                "passed": passed,
                "failed": evaluated - passed,
                "pass_rate": passed / evaluated,
                "top_failure": max(failure_counts, key=failure_counts.get) if failure_counts else None,
            }
        )
    return pd.DataFrame(rows)


def format_scan_summary(summary: pd.DataFrame) -> str:
    """One-line selectivity summary over a whole scan history, for run logs."""
    if summary.empty:
        return "Trend Template: no scan dates evaluated"
    mean_rate = float(summary["pass_rate"].mean())
    zero_days = int((summary["passed"] == 0).sum())
    top = summary["top_failure"].dropna()
    common = top.mode().iloc[0] if not top.empty else "n/a"
    return (
        f"Trend Template over {len(summary)} scan dates: "
        f"mean pass rate {mean_rate:.1%}, "
        f"{zero_days} dates with zero candidates, "
        f"most common rejection: {common}"
    )
