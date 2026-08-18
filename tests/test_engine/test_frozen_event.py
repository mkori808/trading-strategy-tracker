from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from engine.frozen_event import (
    precompute_signal_features, run_event_symbol, signals_from_features,
)
from strategies.swing.frozen_research import (
    FrozenEventStrategy, MaxLotteryReversal, NegativeVolumeShockReversal,
    VolatilityConditionedPullback, VolumeShockContinuation,
)


class AlwaysSignal(FrozenEventStrategy):
    name = "causal probe"
    holding_sessions = 3
    requires_earnings_exclusion = False

    def signal(self, bars, market_bars):
        return "long"


class EarningsExcluded(AlwaysSignal):
    requires_earnings_exclusion = True


class EarningsOnly(AlwaysSignal):
    def earnings_mode(self):
        return "earnings_only_diagnostic"


class EarningsIncluded(AlwaysSignal):
    def earnings_mode(self):
        return "included"


def _bars():
    index = pd.bdate_range("2024-01-02", periods=25)
    close = np.linspace(100, 124, len(index))
    return pd.DataFrame({
        "Open": close - .5, "High": close + 1, "Low": close - 1,
        "Close": close, "Volume": 1_000.0,
    }, index=index)


def _long_bars():
    rng = np.random.default_rng(17)
    index = pd.bdate_range("2022-01-03", periods=330)
    close = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.018, len(index))))
    # Add deterministic shocks so each rule has something to classify.
    close[220] *= 0.94
    close[221:] *= close[220] / close[219] / 0.94
    close[280] *= 1.10
    close[281:] *= close[280] / close[279] / 1.10
    volume = rng.integers(800_000, 1_200_000, len(index)).astype(float)
    volume[[220, 280]] = 3_000_000
    spread = rng.uniform(0.005, 0.02, len(index))
    return pd.DataFrame({
        "Open": close * (1 + rng.normal(0, 0.002, len(index))),
        "High": close * (1 + spread), "Low": close * (1 - spread),
        "Close": close, "Volume": volume,
    }, index=index)


def test_event_signal_at_close_enters_next_open_and_exits_third_session_close(monkeypatch):
    bars = _bars()
    monkeypatch.setattr("engine.frozen_event.spread_for", lambda *args: 0.0)
    result = run_event_symbol(
        AlwaysSignal(), "AAA", bars, bars,
        date(2024, 1, 2), date(2024, 2, 5), 0.0,
        lambda _day: {"AAA"}, set(), None,
    )
    first = result.trades.iloc[0]
    assert first["EntryTime"] == bars.index[1]
    assert first["EntryPrice"] == bars["Open"].iloc[1]
    assert first["ExitTime"] == bars.index[3]
    assert first["ExitPrice"] == bars["Close"].iloc[3]


def test_point_in_time_membership_blocks_signal_before_effective_date(monkeypatch):
    bars = _bars()
    monkeypatch.setattr("engine.frozen_event.spread_for", lambda *args: 0.0)
    eligible_from = bars.index[10].date()
    result = run_event_symbol(
        AlwaysSignal(), "AAA", bars, bars,
        bars.index[0].date(), bars.index[-1].date(), 0.0,
        lambda day: {"AAA"} if day >= eligible_from else set(), set(), None,
    )
    assert result.trades.iloc[0]["EntryTime"] == bars.index[11]


def test_unknown_earnings_coverage_is_not_treated_as_non_earnings(monkeypatch):
    bars = _bars()
    monkeypatch.setattr("engine.frozen_event.spread_for", lambda *args: 0.0)
    result = run_event_symbol(
        EarningsExcluded(), "AAA", bars, bars,
        bars.index[0].date(), bars.index[-1].date(), 0.0,
        lambda _day: {"AAA"}, set(), "no timestamped earnings events available",
    )
    assert result.trades.empty


def test_earnings_only_diagnostic_rejects_non_event_sessions(monkeypatch):
    bars = _bars()
    monkeypatch.setattr("engine.frozen_event.spread_for", lambda *args: 0.0)
    event_day = bars.index[4].date()
    result = run_event_symbol(
        EarningsOnly(), "AAA", bars, bars,
        bars.index[0].date(), bars.index[-1].date(), 0.0,
        lambda _day: {"AAA"}, {event_day}, None,
    )
    assert not result.trades.empty
    assert result.trades.iloc[0]["EntryTime"] == bars.index[5]


def test_explicit_included_diagnostic_does_not_require_event_coverage(monkeypatch):
    bars = _bars()
    monkeypatch.setattr("engine.frozen_event.spread_for", lambda *args: 0.0)
    result = run_event_symbol(
        EarningsIncluded(), "AAA", bars, bars,
        bars.index[0].date(), bars.index[-1].date(), 0.0,
        lambda _day: {"AAA"}, set(), "coverage unavailable",
    )
    assert not result.trades.empty


def test_modeled_spread_cost_is_recorded_and_reduces_pnl(monkeypatch):
    bars = _bars()
    monkeypatch.setattr("engine.frozen_event.spread_for", lambda *args: 0.001)
    with_cost = run_event_symbol(
        AlwaysSignal(), "AAA", bars, bars,
        bars.index[0].date(), bars.index[-1].date(), 0.0,
        lambda _day: {"AAA"}, set(), None,
    )
    assert (with_cost.trades["ModeledCost"] > 0).all()
    assert with_cost.trades.iloc[0]["EntryPrice"] > bars["Open"].iloc[1]


def test_cached_features_are_exactly_signal_equivalent_to_prefix_replay():
    bars = _long_bars()
    market = bars.copy()
    features = precompute_signal_features(bars, market)
    strategies = (
        NegativeVolumeShockReversal(),
        VolumeShockContinuation(direction="long"),
        VolumeShockContinuation(direction="short"),
        MaxLotteryReversal(),
        VolatilityConditionedPullback(),
    )
    for strategy in strategies:
        expected = {}
        for position, stamp in enumerate(bars.index):
            direction = strategy.signal(
                bars.iloc[:position + 1], market.loc[market.index <= stamp]
            )
            if direction is not None:
                expected[stamp.date()] = direction
        assert signals_from_features(strategy, features) == expected, strategy.name


def test_cached_simulator_path_is_trade_for_trade_equivalent(monkeypatch):
    bars = _long_bars()
    market = bars.copy()
    features = precompute_signal_features(bars, market)
    monkeypatch.setattr("engine.frozen_event.spread_for", lambda *args: 0.001)
    risks = {
        stamp.date(): float(value)
        for stamp, value in features["atr14"].dropna().items()
    }
    signal_exits = {
        stamp.date() for stamp in features.index[
            (features["Close"] > features["sma5"]).fillna(False)
        ]
    }
    for strategy in (
        NegativeVolumeShockReversal(), VolatilityConditionedPullback(),
    ):
        ordinary = run_event_symbol(
            strategy, "AAA", bars, market,
            bars.index[0].date(), bars.index[-1].date(), 0.0,
            lambda _day: {"AAA"}, set(), None,
        )
        cached = run_event_symbol(
            strategy, "AAA", bars, market,
            bars.index[0].date(), bars.index[-1].date(), 0.0,
            lambda _day: {"AAA"}, set(), None,
            signals_from_features(strategy, features), risks,
            signal_exits if isinstance(strategy, VolatilityConditionedPullback) else set(),
        )
        pd.testing.assert_frame_equal(
            ordinary.trades.reset_index(drop=True),
            cached.trades.reset_index(drop=True),
        )
        pd.testing.assert_series_equal(
            ordinary.equity_curve["Equity"], cached.equity_curve["Equity"]
        )
