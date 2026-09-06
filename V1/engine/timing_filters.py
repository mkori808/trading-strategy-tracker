"""Entry-timing gates: FOMC-day exclusion and SPY volatility-regime gating.

Both follow engine/filters.py's pattern exactly: a WRAPPING gate that
re-implements the strategies.base.Strategy interface, so it drops into the
existing per-symbol engine (run_strategy_backtest_seeded) with no engine
changes. Stop/target/exit always delegate through untouched -- the gates
change WHICH entries happen, never how a position is managed.

Unlike the regime/trend-template gate (long-only by thesis), both gates here
apply to BOTH directions: event risk around an FOMC decision and the market's
volatility state are direction-agnostic conditions, so a short entry is gated
the same as a long one.

Look-ahead safety:
- FOMC meeting dates are scheduled and published by the Federal Reserve
  roughly a year in advance, so "is today an FOMC day" is genuinely knowable
  at entry time -- including intraday, with no prior-session shift. The list
  below is a hardcoded copy of the Fed's published meeting calendars
  (federalreserve.gov), a fixed public record, not market data. Extend it
  when the Fed publishes a new year; `fomc_blocked_dates` raises if a
  requested window runs past the last covered year rather than silently
  gating nothing.
- The volatility percentile is computed from strictly trailing windows
  (right-aligned rolling realized vol, then a trailing rolling percentile
  rank), and intraday lookups resolve to the PRIOR session via the same
  `_asof` convention engine/filters.py documents -- today's realized vol
  includes today's close, which doesn't exist at 10:00am.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Literal

import numpy as np
import pandas as pd

from engine import regime as regime_module
from engine.backtest import StrategyBacktestResult, run_strategy_backtest_seeded
from engine.filters import _asof
from strategies.base import Strategy

DAILY_INTERVAL = "1d"

# FOMC DECISION days (the second day of each two-day meeting -- the day the
# statement is released at 2pm ET). Source: the Federal Reserve's published
# meeting calendars. Meeting day 1 is blocked via the default
# buffer_before=1 in fomc_blocked_dates() rather than listed here.
FOMC_DECISION_DATES: tuple[date, ...] = (
    # 2021
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28), date(2021, 6, 16),
    date(2021, 7, 28), date(2021, 9, 22), date(2021, 11, 3), date(2021, 12, 15),
    # 2022
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4), date(2022, 6, 15),
    date(2022, 7, 27), date(2022, 9, 21), date(2022, 11, 2), date(2022, 12, 14),
    # 2023
    date(2023, 2, 1), date(2023, 3, 22), date(2023, 5, 3), date(2023, 6, 14),
    date(2023, 7, 26), date(2023, 9, 20), date(2023, 11, 1), date(2023, 12, 13),
    # 2024
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1), date(2024, 6, 12),
    date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    # 2025
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    # 2026
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
)

FOMC_LAST_COVERED_YEAR = max(d.year for d in FOMC_DECISION_DATES)

# Volatility-regime defaults: 21-trading-day realized vol of SPY, ranked
# against its own trailing 252-day history. 450 calendar days comfortably
# covers the 21+252 trading days of warmup both windows need before the
# first in-window bar has a real percentile.
VOL_WINDOW = 21
VOL_PERCENTILE_WINDOW = 252
VOL_WARMUP_DAYS = 450

VolMode = Literal["calm", "storm"]


def fomc_blocked_dates(
    start: date,
    end: date,
    buffer_before: int = 1,
    buffer_after: int = 0,
) -> frozenset[date]:
    """Calendar dates on which new entries are blocked, for a window that
    must fall inside the years the hardcoded calendar covers. The default
    buffer_before=1 blocks meeting day 1 of each two-day meeting (decisions
    land on day 2); buffers are calendar days, disclosed as such."""
    if end.year > FOMC_LAST_COVERED_YEAR:
        raise ValueError(
            f"FOMC calendar only covers through {FOMC_LAST_COVERED_YEAR}; "
            f"requested window ends {end}. Extend FOMC_DECISION_DATES."
        )
    blocked: set[date] = set()
    for decision in FOMC_DECISION_DATES:
        for offset in range(-buffer_before, buffer_after + 1):
            blocked.add(decision + timedelta(days=offset))
    return frozenset(d for d in blocked if start <= d <= end)


@dataclass
class GateDiagnostics:
    """Selectivity, counted rather than assumed -- same reasoning as
    engine/filters.py:FilterDiagnostics: a gate passing ~100% or ~0% of
    entries is not filtering, and both look like 'a changed trade count'
    without these numbers."""

    gate_name: str
    blocked_entries: int = 0
    passed_entries: int = 0
    # Share of the backtest window's trading days on which the gate would
    # block a new entry -- the gate's footprint independent of how often
    # the strategy actually wanted to enter.
    window_days_blocked_pct: float | None = None

    def summary(self) -> str:
        checked = self.blocked_entries + self.passed_entries
        footprint = (
            f"; gate blocks {self.window_days_blocked_pct:.1%} of window days"
            if self.window_days_blocked_pct is not None
            else ""
        )
        if not checked:
            return f"{self.gate_name}: no entry opportunities evaluated{footprint}"
        return (
            f"{self.gate_name}: {checked} entry opportunities -- "
            f"{self.blocked_entries} blocked, {self.passed_entries} passed{footprint}"
        )


class EntryGate(Strategy):
    """An existing strategy with a timestamp-based allow/deny check in front
    of its entry rule (both directions). Everything else passes through."""

    def __init__(
        self,
        inner: Strategy,
        allowed_at: Callable[[pd.Timestamp], bool],
        diagnostics: GateDiagnostics,
    ) -> None:
        self._inner = inner
        self._allowed_at = allowed_at
        self._diagnostics = diagnostics
        self.name = inner.name
        self.timeframe = inner.timeframe
        self.direction = inner.direction

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if bars.empty:
            return False
        if not self._allowed_at(bars.index[-1]):
            # Counted only when the inner rule would actually have fired --
            # "blocked a real entry," not "blocked a bar nobody wanted."
            if self._inner.entry_signal(bars):
                self._diagnostics.blocked_entries += 1
            return False
        fired = self._inner.entry_signal(bars)
        if fired:
            self._diagnostics.passed_entries += 1
        return fired

    def entry_direction(self, bars: pd.DataFrame) -> Literal["long", "short"]:
        return self._inner.entry_direction(bars)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return self._inner.stop_price(bars, entry_price)

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        return self._inner.target_price(bars, entry_price)

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        return self._inner.exit_signal(bars)


def spy_realized_vol(
    start: date, end: date, vol_window: int = VOL_WINDOW
) -> pd.Series:
    """SPY's trailing `vol_window`-day annualized realized volatility
    (right-aligned rolling std of log returns -- every value at bar i
    depends only on bars <= i)."""
    bars = regime_module.load_spy_bars(start, end, warmup_days=VOL_WARMUP_DAYS)
    if bars.empty:
        return pd.Series(dtype=float)
    log_returns = np.log(bars["Close"] / bars["Close"].shift(1))
    return log_returns.rolling(vol_window).std() * np.sqrt(252)


def spy_vol_percentile(
    start: date,
    end: date,
    vol_window: int = VOL_WINDOW,
    percentile_window: int = VOL_PERCENTILE_WINDOW,
) -> pd.Series:
    """`spy_realized_vol` expressed as its percentile rank within its own
    trailing `percentile_window` days -- a trailing rolling rank, so this
    stays causal on top of the already-causal input series."""
    realized_vol = spy_realized_vol(start, end, vol_window)
    if realized_vol.empty:
        return realized_vol
    return realized_vol.rolling(percentile_window).rank(pct=True)


def vol_allowed_series(
    percentiles: pd.Series, mode: VolMode, threshold: float = 0.7
) -> pd.Series:
    """Daily allow/deny series from the percentile series. "calm" allows
    entries when vol is at or below the threshold percentile (blocks the
    most volatile tail); "storm" allows only above it. The two modes
    partition the same threshold, so comparing them is a clean split, not
    two overlapping cherry-picks. Days with no percentile yet (warmup)
    are blocked -- an unknown regime is not a known-good one."""
    if mode == "calm":
        return percentiles <= threshold
    return percentiles > threshold


StrategySource = Strategy | Callable[[str], Strategy]


def _entries_gated_factory(
    inner: StrategySource,
    allowed_at: Callable[[pd.Timestamp], bool],
    diagnostics: GateDiagnostics,
) -> Callable[[str], Strategy]:
    build_inner = inner if callable(inner) else (lambda _symbol: inner)
    return lambda symbol: EntryGate(build_inner(symbol), allowed_at, diagnostics)


def fomc_gate_predicate(
    start: date,
    end: date,
    buffer_before: int = 1,
    buffer_after: int = 0,
) -> tuple[Callable[[pd.Timestamp], bool], GateDiagnostics]:
    """allowed_at predicate + diagnostics for the FOMC-day gate, buildable
    without a strategy so engines the Strategy wrapper can't drive
    (engine/overnight.py's entry_allowed hook) can share the exact gate.
    Safe for both daily and intraday timestamps: FOMC membership of a
    calendar day is known in advance, so no prior-session shift applies."""
    blocked = fomc_blocked_dates(start, end, buffer_before, buffer_after)
    spy = regime_module.load_spy_bars(start, end, warmup_days=0)
    window_days = [ts.date() for ts in spy.index if ts.date() >= start]
    footprint = (
        sum(1 for d in window_days if d in blocked) / len(window_days)
        if window_days else None
    )
    diagnostics = GateDiagnostics("FOMC-day gate", window_days_blocked_pct=footprint)

    def allowed_at(timestamp: pd.Timestamp) -> bool:
        return timestamp.date() not in blocked

    return allowed_at, diagnostics


def build_fomc_gate(
    inner: StrategySource,
    start: date,
    end: date,
    buffer_before: int = 1,
    buffer_after: int = 0,
) -> tuple[Callable[[str], Strategy], GateDiagnostics]:
    """Gate factory blocking entries on FOMC meeting days."""
    allowed_at, diagnostics = fomc_gate_predicate(start, end, buffer_before, buffer_after)
    return _entries_gated_factory(inner, allowed_at, diagnostics), diagnostics


def vol_gate_predicate(
    start: date,
    end: date,
    mode: VolMode,
    threshold: float = 0.7,
) -> tuple[Callable[[pd.Timestamp], bool], GateDiagnostics, pd.Series]:
    """Daily-convention allowed_at predicate + diagnostics + the underlying
    daily allow/deny series for the SPY volatility-regime gate. The
    predicate reads the timestamp's own day (signal-on-close convention) --
    correct for daily-bar engines (engine/overnight.py); intraday strategies
    must go through build_vol_gate, whose wrapper shifts lookups to the
    prior session."""
    percentiles = spy_vol_percentile(start, end)
    allowed = vol_allowed_series(percentiles, mode, threshold)
    window = allowed.loc[allowed.index >= pd.Timestamp(start, tz=allowed.index.tz)]
    footprint = float((~window).mean()) if len(window) else None
    diagnostics = GateDiagnostics(
        f"SPY vol gate ({mode}, threshold p{threshold:.0%})",
        window_days_blocked_pct=footprint,
    )

    def allowed_at(timestamp: pd.Timestamp) -> bool:
        value = _asof(allowed, timestamp, False)
        return bool(value) if value is not None else False

    return allowed_at, diagnostics, allowed


def build_vol_gate(
    inner: StrategySource,
    start: date,
    end: date,
    mode: VolMode,
    threshold: float = 0.7,
) -> tuple[Callable[[str], Strategy], GateDiagnostics]:
    """Gate factory allowing entries only in the requested SPY volatility
    regime. Intraday strategies' lookups resolve to the prior session (see
    module docstring); daily ones use the bar's own day, consistent with
    the engine's signal-on-close/fill-at-next-open convention."""
    _, diagnostics, allowed = vol_gate_predicate(start, end, mode, threshold)

    def allowed_at_for(strategy: Strategy) -> Callable[[pd.Timestamp], bool]:
        intraday = strategy.timeframe != DAILY_INTERVAL

        def check(timestamp: pd.Timestamp) -> bool:
            value = _asof(allowed, timestamp, intraday)
            return bool(value) if value is not None else False

        return check

    build_inner = inner if callable(inner) else (lambda _symbol: inner)

    def strategy_for(symbol: str) -> Strategy:
        built = build_inner(symbol)
        return EntryGate(built, allowed_at_for(built), diagnostics)

    return strategy_for, diagnostics


class VolTargetedCrossSectional:
    """Wraps a strategies.cross_sectional.CrossSectionalStrategy and scales
    its rebalance weights down (never up -- see `target_annual_vol`'s
    docstring) when SPY's trailing realized vol is elevated, so a systemic
    shock finds the portfolio partially de-risked instead of fully invested
    across every holding at once. This targets a DIFFERENT failure mode
    than engine/filters.py's regime gate or this module's entry gates: those
    change WHICH positions get entered; this changes HOW MUCH capital a
    already-selected basket gets, on every rebalance, including holds.

    Motivating case (see LESSONS.md): Dual Momentum's worst drawdown
    (-23.3%, 2025-04-07) landed while it held 5 equal-weighted, fully-
    invested large caps with zero exposure scaling, during the April 2025
    tariff-shock selloff -- a realized-vol spike that this overlay would
    have caught going INTO the rebalance that set that basket."""

    def __init__(
        self,
        inner,
        realized_vol: pd.Series,
        target_annual_vol: float = 0.12,
    ) -> None:
        self._inner = inner
        self._realized_vol = realized_vol
        # 12% is a conventional single-digit-to-low-teens equity vol target
        # (SPY's own long-run realized vol is roughly 15-18%), chosen so the
        # overlay is inactive (scale ~1.0) in ordinary conditions and only
        # bites during genuine spikes -- not tuned to this specific window.
        self._target = target_annual_vol
        self.name = inner.name
        self.timeframe = inner.timeframe

    def rebalance(
        self, universe_bars: dict[str, pd.DataFrame], as_of: pd.Timestamp
    ) -> dict[str, float]:
        weights = self._inner.rebalance(universe_bars, as_of)
        if not weights:
            return weights
        vol = _asof(self._realized_vol, as_of, intraday=False)
        if vol is None or not vol > 0:
            return weights  # no vol reading yet (warmup) -- don't scale blind
        # Capped at 1.0: this overlay only ever REDUCES exposure. Scaling
        # above 1.0 would mean leverage, which engine/cross_sectional.py's
        # "weights need not sum to 1.0" convention doesn't model (remainder
        # is cash, never margin) -- out of scope for this overlay.
        scale = min(1.0, self._target / float(vol))
        return {symbol: weight * scale for symbol, weight in weights.items()}


def build_vol_targeted_overlay(
    inner, start: date, end: date, target_annual_vol: float = 0.12,
    vol_window: int = VOL_WINDOW,
) -> "VolTargetedCrossSectional":
    """Convenience constructor: computes SPY's realized vol for the window
    and wraps `inner` in the overlay above."""
    realized_vol = spy_realized_vol(start, end, vol_window)
    return VolTargetedCrossSectional(inner, realized_vol, target_annual_vol)


def run_gated_backtest(
    strategy_name: str,
    strategy_for: Callable[[str], Strategy],
    symbols: list[str],
    interval: str,
    start: date,
    end: date,
    risk_free_rate: float = 0.0,
    **kwargs,
) -> StrategyBacktestResult:
    """Thin alias over the existing seeded engine, mirroring
    engine/filters.py:run_filtered_backtest -- same data pipeline, cost
    model, portfolio simulator and metrics, so a gated and ungated run
    differ in exactly one variable: the gate."""
    return run_strategy_backtest_seeded(
        strategy_name, strategy_for, symbols, interval, start, end,
        risk_free_rate=risk_free_rate, **kwargs,
    )
