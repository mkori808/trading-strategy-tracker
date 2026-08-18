"""Plausibility assertions for ANALYSIS SCRIPTS, not just the engine.

engine/metrics.py:implausible_metrics guards what gets written to the database.
Nothing guarded the throwaway analysis scripts -- and this session's fourth
near-miss came from exactly there: a subperiod harness whose cache patch ignored
the requested date range, so every "subperiod" silently ran to the end of the
data. It reported +148.9pp against SPY and was caught only because the strategy
return, 144.1%, exactly matched a full-period figure someone happened to
recognise.

That is a thinner margin than the engine now runs on, and it is the wrong way
round: throwaway code is where the checking is thinnest precisely when the
result matters most. Bugs do not respect the boundary between production and
scratch.

Three lines to use:

    from engine.sanity import check_window, check_return
    check_window(bars, requested_start, requested_end)
    check_return(pct, label="subperiod 1")
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from engine.metrics import PLAUSIBLE_CAGR_PCT


class SanityError(AssertionError):
    """An analysis-script result outside the bounds a human would accept."""


def calendar_daily_series(series: pd.Series) -> pd.Series:
    """Reindex a possibly SPARSE series onto the real trading-day calendar it
    spans, forward-filling gaps, so an idle stretch contributes flat (zero
    return) observations instead of being silently absent.

    This exists because two independent "collapse to daily" helpers
    (engine/advanced_validation.py:_daily_returns and
    engine/research_governance.py:_daily_equity) both did
    `series.groupby(index.normalize()).last()` and NOTHING else -- which is a
    no-op on an already-daily curve but silently wrong on a SPARSE one.

    engine/portfolio.py:run_portfolio_backtest (the shared-capital simulation
    validate_standard() runs for every per-symbol strategy) only appends an
    equity point on a trade ENTRY or EXIT, not every trading day. A low-
    exposure strategy's curve is therefore a handful of points spaced days to
    weeks apart. Fed through the old helpers, `.pct_change()` on those points
    produces one "return" per EVENT GAP -- a three-week price move compressed
    into a single observation -- and every downstream calculation (Sharpe,
    factor regression, minimum-detectable-alpha) then annualizes it as if it
    were a single TRADING DAY's return, compounding 252 times a year.

    Measured directly: Earnings Momentum, Sector Rotation Play, Breakout from
    Consolidation and similar low-exposure per-symbol strategies reported
    implied annual volatilities of 80-100% on long-only Dow equities and
    minimum-detectable-alphas of 35-48%/yr through this path -- not because
    the strategies are volatile, but because `years = len(returns) / 252`
    silently counted event-gaps as trading days, understating the true
    calendar span by an order of magnitude and inflating apparent volatility
    by a similar amount. This is the same denominator-collapse shape as the
    original -rf/sigma bug: a ratio whose sample size quietly shrank.

    Reindexing here is intentionally cheap and dependency-free (a business-day
    calendar via `pd.bdate_range`, not a fetched market calendar) -- a handful
    of spurious flat NYSE-holiday observations are harmless no-ops, while
    fetching a real calendar here would make this shared statistics helper
    depend on network data.
    """
    if series is None or len(series) < 2:
        return pd.Series(dtype=float)
    values = pd.to_numeric(series, errors="coerce").dropna().sort_index()
    if not isinstance(values.index, pd.DatetimeIndex) or len(values) < 2:
        return pd.Series(dtype=float)
    collapsed = values.groupby(values.index.normalize()).last()
    if len(collapsed) < 2:
        return collapsed
    calendar = pd.bdate_range(collapsed.index[0], collapsed.index[-1])
    # pd.bdate_range infers tz from tz-aware start/end Timestamps and returns
    # an already-tz-aware index -- tz_localize()'ing that raises TypeError.
    # Only localize when it didn't (a naive index built from naive inputs).
    if collapsed.index.tz is not None and calendar.tz is None:
        calendar = calendar.tz_localize(collapsed.index.tz)
    return collapsed.reindex(calendar, method="ffill").dropna()


def check_window(bars: pd.DataFrame, start: date, end: date, *, label: str = "") -> None:
    """The data actually used must lie inside the window that was asked for.

    Catches the cache-patch class of bug directly: a fixture that returns more
    than it was asked for produces results for a window nobody requested, and
    every downstream comparison is then against a different period.
    """
    if bars.empty:
        return
    lo, hi = bars.index[0], bars.index[-1]
    tz = getattr(bars.index, "tz", None)
    want_hi = pd.Timestamp(end) + pd.Timedelta(days=2)
    if tz is not None:
        want_hi = want_hi.tz_localize(tz)
    if hi > want_hi:
        raise SanityError(
            f"{label or 'window'}: data runs to {hi.date()} but {end} was requested -- "
            "the source is ignoring the range (a cache or fixture returning everything)"
        )


def check_return(pct: float | None, *, label: str = "", years: float | None = None) -> None:
    """A return outside what this asset class can produce is a bug report."""
    if pct is None:
        return
    lo, hi = PLAUSIBLE_CAGR_PCT
    if years and years > 0:
        lo, hi = lo, ((1 + hi / 100) ** years - 1) * 100
    if not (lo <= pct <= hi):
        raise SanityError(f"{label or 'return'} {pct:.1f}% outside plausible ({lo:.0f}, {hi:.0f})")


def check_sharpe(sharpe: float | None, *, label: str = "") -> None:
    """Reject broken arithmetic, but never block a finite Sharpe value."""
    if sharpe is None:
        return
    if not np.isfinite(float(sharpe)):
        raise SanityError(f"{label or 'Sharpe'} {sharpe} is not finite")
