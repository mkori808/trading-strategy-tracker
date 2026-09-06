"""engine/observations.py: turning an already-computed backtest result into
a point-in-time observation dataset.

Two properties are load-bearing and get their own tests: the decision bar is
always STRICTLY BEFORE the fill (never the fill's own bar), and an
observation whose outcome cannot be computed is dropped rather than kept
with a fabricated value.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine import observations as observations_module
from engine import pit_features
from engine.backtest import StrategyBacktestResult, SymbolBacktestResult
from tests.test_engine._conditional_helpers import make_trades, monkeypatch_bars, synthetic_bars

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def universe_bars():
    return {
        "SPY": synthetic_bars(500, seed=1),
        "AAPL": synthetic_bars(500, seed=2, drift=0.0005),
    }


def _standard_result(bars, trades) -> StrategyBacktestResult:
    per_symbol = {"AAPL": SymbolBacktestResult("AAPL", stats=None, trades=trades, equity_curve=None, excursions=None)}
    return StrategyBacktestResult(
        strategy_name="Synthetic Strategy", symbols=["AAPL"],
        start=bars.index[300].date(), end=bars.index[-1].date(), per_symbol=per_symbol,
        metrics=None,
    )


def test_decision_bar_is_strictly_before_entry_bar(monkeypatch, universe_bars):
    monkeypatch_bars(monkeypatch, pit_features, universe_bars)
    monkeypatch_bars(monkeypatch, observations_module, universe_bars)
    bars = universe_bars["AAPL"]
    entry_time = bars.index[400]
    exit_time = bars.index[402]
    trades = make_trades([(entry_time, exit_time, 100.0, 105.0, 95.0, 10.0)])
    result = _standard_result(bars, trades)

    obs = observations_module.build_observations(result, engine="standard", universe=["AAPL"])
    assert len(obs) == 1
    row = obs.frame.iloc[0]
    assert row["decision_time"] < entry_time
    # The decision bar must be the LAST bar before entry, not merely any
    # earlier one.
    feature_index = bars.index[bars.index < entry_time]
    assert row["decision_time"] == feature_index[-1]


def test_observation_with_uncomputable_outcome_is_dropped(monkeypatch, universe_bars):
    monkeypatch_bars(monkeypatch, pit_features, universe_bars)
    monkeypatch_bars(monkeypatch, observations_module, universe_bars)
    bars = universe_bars["AAPL"]
    entry_time = bars.index[400]
    exit_time = bars.index[402]
    # SL == EntryPrice -> risk_per_share is 0 -> r_multiples is NaN for this trade.
    trades = make_trades([(entry_time, exit_time, 100.0, 105.0, 100.0, 10.0)])
    result = _standard_result(bars, trades)

    obs = observations_module.build_observations(result, engine="standard", universe=["AAPL"])
    assert len(obs) == 0
    assert any("dropped" in w for w in obs.warnings)


def test_trade_before_any_feature_bar_is_excluded_with_warning(monkeypatch, universe_bars):
    monkeypatch_bars(monkeypatch, pit_features, universe_bars)
    monkeypatch_bars(monkeypatch, observations_module, universe_bars)
    bars = universe_bars["AAPL"]
    # An entry at the very first bar has no prior feature row.
    entry_time = bars.index[0]
    exit_time = bars.index[2]
    trades = make_trades([(entry_time, exit_time, 100.0, 101.0, 95.0, 10.0)])
    result = _standard_result(bars, trades)

    obs = observations_module.build_observations(result, engine="standard", universe=["AAPL"])
    assert len(obs) == 0
    assert any("no prior feature bar" in w for w in obs.warnings)


def test_coverage_report_counts_non_missing_values(monkeypatch, universe_bars):
    monkeypatch_bars(monkeypatch, pit_features, universe_bars)
    monkeypatch_bars(monkeypatch, observations_module, universe_bars)
    bars = universe_bars["AAPL"]
    entries = [
        (bars.index[i], bars.index[i + 1], 100.0, 101.0, 95.0, 10.0)
        for i in (350, 360, 370)
    ]
    trades = make_trades(entries)
    result = _standard_result(bars, trades)
    obs = observations_module.build_observations(result, engine="standard", universe=["AAPL"])
    report = {row["key"]: row for row in obs.coverage_report()}
    assert report["ret_20d"]["present"] == 3
    assert report["ret_20d"]["total"] == 3
    # A feature that needs 200+ bars of warmup is still present this deep
    # into a 500-bar series.
    assert report["dist_sma200"]["coveragePct"] == 100.0


def test_exit_reason_classification():
    trade = pd.Series({"ExitPrice": 105.0, "SL": 95.0, "TP": 105.0})
    assert observations_module._exit_reason(trade) == "target"
    trade = pd.Series({"ExitPrice": 95.02, "SL": 95.0, "TP": 110.0})
    assert observations_module._exit_reason(trade) == "stop"
    trade = pd.Series({"ExitPrice": 101.0, "SL": 95.0, "TP": 110.0})
    assert observations_module._exit_reason(trade) == "signal"


def test_concurrency_profile_matches_hand_computed_average():
    frame = pd.DataFrame({
        "entry_time": pd.to_datetime(["2024-01-01", "2024-01-02"]),
        "exit_time": pd.to_datetime(["2024-01-05", "2024-01-06"]),
    })
    profile = observations_module.concurrency_profile(frame)
    # Day 1: 1 open. Days 2-5: 2 open (overlap). Day 6: 1 open (closing).
    # Time-weighted average over the 5-day span from first entry to last exit.
    assert profile["maxConcurrent"] == 2
    assert profile["distinctEntryDays"] == 2
    assert 1.0 < profile["averageConcurrent"] <= 2.0


def test_concurrency_profile_empty_frame():
    profile = observations_module.concurrency_profile(pd.DataFrame(columns=["entry_time", "exit_time"]))
    assert profile["observations"] == 0
    assert profile["maxConcurrent"] == 0


def test_unsupported_engine_raises():
    result = StrategyBacktestResult(
        strategy_name="X", symbols=[], start=date(2020, 1, 1), end=date(2020, 1, 2),
        per_symbol={}, metrics=None,
    )
    with pytest.raises(ValueError, match="Unsupported engine"):
        observations_module.build_observations(result, engine="pairs")
