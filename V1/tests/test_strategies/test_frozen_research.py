from __future__ import annotations

import numpy as np
import pandas as pd

from strategies.swing.frozen_research import (
    High52WeekMomentum,
    MarketResidualMomentum,
    MaxLotteryReversal,
    NegativeVolumeShockReversal,
    VolumeShockContinuation,
)


def _bars(close, volume=None):
    close = np.asarray(close, dtype=float)
    index = pd.bdate_range("2023-01-02", periods=len(close))
    volume = np.full(len(close), 1_000.0) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * .99,
        "Close": close, "Volume": volume,
    }, index=index)


def test_52_week_high_v1_ranks_high_ratio_after_positive_momentum_filter():
    near_high = _bars(np.linspace(50, 100, 260))
    farther = _bars(np.r_[np.linspace(50, 110, 250), np.linspace(109, 90, 10)])
    negative = _bars(np.linspace(100, 80, 260))
    strategy = High52WeekMomentum(top_n=1)
    weights = strategy.rebalance(
        {"NEAR": near_high, "FAR": farther, "NEG": negative}, near_high.index[-1]
    )
    assert weights == {"NEAR": 1.0}


def test_market_residual_momentum_uses_only_history_through_as_of():
    market = _bars(np.linspace(100, 130, 220))
    stock = _bars(np.linspace(80, 140, 220))
    strategy = MarketResidualMomentum(benchmark_bars=market, lookback=63, top_n=1)
    as_of = stock.index[180]
    execution_history = stock.loc[stock.index < as_of]
    before = strategy.rebalance({"AAA": execution_history}, as_of)
    changed_future = stock.copy()
    changed_future.loc[changed_future.index >= as_of, "Close"] *= 10
    after = strategy.rebalance(
        {"AAA": changed_future.loc[changed_future.index < as_of]}, as_of
    )
    assert before == after == {"AAA": 1.0}


def test_negative_volume_shock_v1_exact_frozen_thresholds():
    close = np.linspace(100, 120, 205)
    close[-1] = close[-2] * .95
    volume = np.full(205, 1_000.0)
    volume[-1] = 2_100.0
    assert NegativeVolumeShockReversal().signal(_bars(close, volume), pd.DataFrame()) == "long"


def test_volume_continuation_sides_are_reported_separately():
    up = np.linspace(100, 110, 30)
    down = np.linspace(110, 100, 30)
    volume = np.full(30, 1_000.0)
    volume[-1] = 2_100.0
    up_bars = _bars(up, volume)
    up_bars.loc[up_bars.index[-1], ["High", "Low", "Close"]] = [111.0, 100.0, 110.5]
    down_bars = _bars(down, volume)
    down_bars.loc[down_bars.index[-1], ["High", "Low", "Close"]] = [110.0, 99.0, 99.5]
    assert VolumeShockContinuation(direction="long").signal(up_bars, pd.DataFrame()) == "long"
    assert VolumeShockContinuation(direction="short").signal(down_bars, pd.DataFrame()) == "short"


def test_max_lottery_v1_detects_recent_eight_percent_event_with_volume():
    close = np.full(30, 100.0)
    close[-3:] = [109.0, 109.0, 109.0]
    volume = np.full(30, 1_000.0)
    volume[-3] = 1_600.0
    assert MaxLotteryReversal().signal(_bars(close, volume), pd.DataFrame()) == "short"
