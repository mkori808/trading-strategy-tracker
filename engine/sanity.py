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

import pandas as pd

from engine.metrics import PLAUSIBLE_CAGR_PCT, PLAUSIBLE_SHARPE


class SanityError(AssertionError):
    """An analysis-script result outside the bounds a human would accept."""


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
    """Same asymmetric band the engine uses -- see PLAUSIBLE_SHARPE."""
    if sharpe is None:
        return
    if not (PLAUSIBLE_SHARPE[0] <= sharpe <= PLAUSIBLE_SHARPE[1]):
        raise SanityError(f"{label or 'Sharpe'} {sharpe:.3f} outside plausible {PLAUSIBLE_SHARPE}")
