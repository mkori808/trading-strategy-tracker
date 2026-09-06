"""Wires the market-regime gate and the Minervini Trend Template in front of
any existing strategy, without touching the existing engine.

The gate is applied by WRAPPING a strategy rather than by adding a filter
parameter to engine/backtest.py. That keeps the change purely additive: the
per-symbol engine, the portfolio simulator, the metrics, the logging DB and
the API all see an ordinary `strategies.base.Strategy` and behave exactly as
before. `FilteredStrategy` delegates stop_price / target_price / exit_signal
straight through to the inner strategy, so the filters change WHICH entries
happen and nothing about how a position is managed once it exists.

Evaluation order, per spec:
  1. Market regime -- not Bullish, no new long entry on this bar at all
  2. Trend Template for this symbol on this date -- fails, skip this symbol
  3. Only then, the strategy's own entry_signal()

Existing positions are never force-closed by a regime change; in Bearish the
gate logs a warning about open exposure and leaves the exit to the strategy.

Look-ahead, the part specific to this module: the filters are computed on
DAILY bars, but day-trading strategies run on 5-minute bars. Looking up "the
daily filter value for today" from inside a 10:00am bar would leak today's
daily CLOSE into a decision made hours before it exists. So intraday lookups
resolve to the last daily bar STRICTLY BEFORE the current session (see
`_asof`). Daily strategies do use the current bar's own daily value, which
matches how every strategy in this project already works -- entry_signal
reads the current close and the engine fills at the next bar's open.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Literal

import pandas as pd

from engine import regime as regime_module
from engine import trend_template
from engine.backtest import StrategyBacktestResult, run_strategy_backtest_seeded
from strategies.base import Strategy

DAILY_INTERVAL = "1d"


@dataclass
class FilterDiagnostics:
    """Why entries were blocked, counted rather than assumed.

    Without this you can't tell "the filter removed the bad trades" from
    "the filter removed everything" -- both show up as a lower trade count.
    """

    blocked_by_regime: int = 0
    blocked_by_template: int = 0
    passed_filters: int = 0
    bearish_bars_with_open_position: int = 0
    regime_distribution: dict[str, float] = field(default_factory=dict)
    scan_summary: pd.DataFrame = field(default_factory=pd.DataFrame)

    def summary(self) -> str:
        checked = self.blocked_by_regime + self.blocked_by_template + self.passed_filters
        if not checked:
            return "Filters: no entry opportunities evaluated"
        return (
            f"Filters: {checked} entry opportunities gated -- "
            f"{self.blocked_by_regime} blocked by regime, "
            f"{self.blocked_by_template} blocked by trend template, "
            f"{self.passed_filters} reached entry_signal()"
        )


def _asof(series: pd.Series, timestamp: pd.Timestamp, intraday: bool):
    """Value of a daily `series` as known at `timestamp`.

    Intraday: the last daily bar strictly before the current session, because
    the current session's daily bar contains a close that hasn't happened yet.
    Daily: the bar at `timestamp` itself, consistent with the engine's
    signal-on-close / fill-at-next-open convention.
    """
    if series.empty:
        return None

    # searchsorted rather than Series.asof: asof needs a cutoff timestamp, and
    # building one by subtracting a nanosecond from midnight raises outright
    # when the index's datetime unit is coarser than nanoseconds ("Cannot
    # losslessly convert units"). Shifting the SIDE of the search instead
    # expresses "strictly before today" without inventing a sub-unit instant.
    #   intraday -> side="left":  last bar strictly before this session
    #   daily    -> side="right": the bar at this timestamp itself
    cutoff = timestamp.normalize() if intraday else timestamp
    position = series.index.searchsorted(cutoff, side="left" if intraday else "right") - 1
    if position < 0:
        return None
    value = series.iloc[position]
    return None if pd.isna(value) else value


class FilteredStrategy(Strategy):
    """An existing strategy with the regime + trend-template pre-check in
    front of its entry rule. Everything else passes through unchanged."""

    def __init__(
        self,
        inner: Strategy,
        regime_labels: pd.Series,
        template_passes: pd.Series,
        diagnostics: FilterDiagnostics,
    ) -> None:
        self._inner = inner
        self._regime_labels = regime_labels
        self._template_passes = template_passes
        self._diagnostics = diagnostics
        self._intraday = inner.timeframe != DAILY_INTERVAL

        self.name = inner.name
        self.timeframe = inner.timeframe
        self.direction = inner.direction

    def _gate_open(self, bars: pd.DataFrame) -> bool:
        timestamp = bars.index[-1]

        regime = _asof(self._regime_labels, timestamp, self._intraday)
        if regime != regime_module.BULLISH:
            self._diagnostics.blocked_by_regime += 1
            return False

        if not bool(_asof(self._template_passes, timestamp, self._intraday)):
            self._diagnostics.blocked_by_template += 1
            return False

        self._diagnostics.passed_filters += 1
        return True

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if bars.empty:
            return False

        # Long-only strategies take the spec's ordering exactly: gate first,
        # entry rule second. A short or both-sided strategy has to resolve
        # which side fired before we know whether the long-only gate even
        # applies, so for those the inner signal is evaluated first and the
        # gate is applied only to a resolved long. The filters never block a
        # short -- they are a long-entry gate, not a no-trade switch.
        if self.direction == "long":
            return self._gate_open(bars) and self._inner.entry_signal(bars)

        if not self._inner.entry_signal(bars):
            return False
        if self._inner.entry_direction(bars) != "long":
            return True
        return self._gate_open(bars)

    def entry_direction(self, bars: pd.DataFrame) -> Literal["long", "short"]:
        return self._inner.entry_direction(bars)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return self._inner.stop_price(bars, entry_price)

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        return self._inner.target_price(bars, entry_price)

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        # Regime is deliberately NOT consulted here. A regime flip does not
        # close an open position -- it only stops new ones. Bearish bars with
        # live exposure are counted (and surfaced as a warning) so tighter
        # manual stop management is an informed choice, not an automatic one.
        if not bars.empty:
            regime = _asof(self._regime_labels, bars.index[-1], self._intraday)
            if regime == regime_module.BEARISH:
                self._diagnostics.bearish_bars_with_open_position += 1
        return self._inner.exit_signal(bars)


def _window(labels: pd.Series, start: date) -> pd.Series:
    """Trim the warmup prefix off a filter series, leaving the traded window."""
    if labels.empty:
        return labels
    return labels.loc[labels.index >= pd.Timestamp(start, tz=labels.index.tz)]


StrategySource = Strategy | Callable[[str], Strategy]


def build_filter_factory(
    inner: StrategySource,
    symbols: list[str],
    start: date,
    end: date,
    slope_lookback: int = trend_template.SLOPE_LOOKBACK,
) -> tuple[Callable[[str], Strategy], FilterDiagnostics]:
    """Precompute both filters once for the whole run and return a
    `strategy_for(symbol)` factory the existing seeded engine already knows
    how to drive (engine/backtest.py:run_strategy_backtest_seeded).

    `inner` is either one shared strategy instance or a `strategy_for(symbol)`
    callable, mirroring run_strategy_backtest vs. run_strategy_backtest_seeded
    -- so a strategy that needs per-symbol construction (PEAD's real earnings
    dates) can be filtered exactly like a shared one.

    Both filters are computed vectorized over warmup-extended daily history
    here, not recomputed per bar -- which is both far cheaper and the reason
    the trend template runs "once per symbol per scan date" as specified.
    """
    # SPY does double duty -- it's both the regime input and the trend
    # template's RS benchmark. Fetch it once, at the deeper of the two warmup
    # requirements, and feed both. (Extra leading history is harmless to the
    # regime labels: they're a per-bar function of trailing windows.)
    spy_bars = trend_template.load_bars_with_warmup(regime_module.BENCHMARK, start, end)
    regime_labels = regime_module.regime_series(spy_bars)
    benchmark_bars = spy_bars

    frames: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        bars = trend_template.load_bars_with_warmup(symbol, start, end)
        frames[symbol] = trend_template.trend_template_frame(
            bars, benchmark_bars, slope_lookback
        )

    # Report the regime mix over the BACKTEST window only -- including the
    # warmup prefix would describe a period the strategy never traded.
    diagnostics = FilterDiagnostics(
        regime_distribution=regime_module.regime_distribution(_window(regime_labels, start)),
        scan_summary=trend_template.scan_summary(frames),
    )

    build_inner = inner if callable(inner) else (lambda _symbol: inner)

    def strategy_for(symbol: str) -> Strategy:
        frame = frames.get(symbol)
        passes = (
            frame["passes"] if frame is not None and not frame.empty
            else pd.Series(dtype=bool)
        )
        return FilteredStrategy(build_inner(symbol), regime_labels, passes, diagnostics)

    return strategy_for, diagnostics


def run_filtered_backtest(
    strategy_name: str,
    strategy: StrategySource,
    symbols: list[str],
    interval: str,
    start: date,
    end: date,
    risk_free_rate: float = 0.0,
    slope_lookback: int = trend_template.SLOPE_LOOKBACK,
    **kwargs,
) -> tuple[StrategyBacktestResult, FilterDiagnostics]:
    """Same signature and same result shape as
    engine/backtest.py:run_strategy_backtest, with both pre-filters active.

    Runs through the existing seeded engine unchanged -- same data pipeline,
    same cost model, same portfolio simulator, same metrics -- so a filtered
    and unfiltered run differ in exactly one variable: the gate.
    """
    strategy_for, diagnostics = build_filter_factory(
        strategy, symbols, start, end, slope_lookback
    )
    result = run_strategy_backtest_seeded(
        strategy_name, strategy_for, symbols, interval, start, end,
        risk_free_rate=risk_free_rate, **kwargs,
    )
    return result, diagnostics
