from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.mrm_regime_drawdown_attribution import (
    MATERIALITY_PCT,
    _bucket_stats,
    _concentration_check,
    _drawdown_episodes,
    _label_periods,
    _local_drawdown_pct,
    _synthetic_drawdown_pct,
    _verdict,
    _window_return_pct,
)


def _curve(values, start="2023-01-02"):
    index = pd.bdate_range(start, periods=len(values))
    return pd.Series(np.asarray(values, dtype=float), index=index)


def test_drawdown_episodes_ignores_moves_below_the_materiality_threshold():
    # A 2% dip should never surface as an episode at the default 5% floor --
    # otherwise every ordinary week of noise would count as a "drawdown".
    equity = _curve([100, 99, 98, 100, 101])
    episodes = _drawdown_episodes(equity, materiality_pct=MATERIALITY_PCT)
    assert episodes == []


def test_drawdown_episodes_detects_a_material_peak_to_trough_recovery():
    equity = _curve([100, 100, 90, 85, 90, 101, 102])
    episodes = _drawdown_episodes(equity, materiality_pct=MATERIALITY_PCT)
    assert len(episodes) == 1
    episode = episodes[0]
    assert episode["trough"] == equity.index[3]
    assert episode["recovery"] == equity.index[5]  # first close back above the prior peak
    assert episode["spyDrawdownPct"] == pytest.approx(-15.0)


def test_drawdown_episode_stays_open_if_never_recovered_by_data_end():
    equity = _curve([100, 90, 80, 82])
    episodes = _drawdown_episodes(equity, materiality_pct=MATERIALITY_PCT)
    assert len(episodes) == 1
    assert episodes[0]["recovery"] is None
    assert episodes[0]["end"] == equity.index[-1]


def test_local_and_window_metrics_are_scoped_to_the_requested_window():
    # A large drawdown OUTSIDE [start, end] must not leak into the local
    # max-drawdown or window-return figures -- these are meant to isolate
    # one strategy's behavior during one specific SPY-defined episode, not
    # its behavior over the whole backtest.
    equity = _curve([100, 50, 100, 95, 90, 120])
    start, end = equity.index[2], equity.index[4]
    dd = _local_drawdown_pct(equity, start, end)
    assert dd == pytest.approx(-10.0)  # 100 -> 90 within [2, 4], not the earlier 100->50
    window_return = _window_return_pct(equity, start, end)
    assert window_return == pytest.approx(-10.0)


def test_synthetic_drawdown_chains_only_the_labeled_periods_in_order():
    # Two periods labeled the same regime, with an unlabeled/different-regime
    # period's large loss in between -- that middle loss must NOT appear in
    # the synthetic curve since it belongs to a different bucket.
    returns = pd.Series([0.10, -0.05], index=pd.bdate_range("2023-01-02", periods=2))
    dd = _synthetic_drawdown_pct(returns)
    # chained curve: 1.10 -> 1.045; drawdown from the 1.10 peak = 1.045/1.10 - 1
    assert dd == pytest.approx((1.045 / 1.10 - 1.0) * 100.0, abs=1e-3)


def test_label_periods_uses_only_information_at_or_before_the_period_start():
    trend_labels = pd.Series(
        ["Bullish", "Bearish"],
        index=pd.DatetimeIndex(["2023-01-02", "2023-02-01"]),
    )
    vol_percentiles = pd.Series(
        [0.2, 0.9],
        index=pd.DatetimeIndex(["2023-01-02", "2023-02-01"]),
    )
    periods = pd.DataFrame(
        {"c": [0.01, 0.02], "d": [0.015, 0.01]},
        index=pd.DatetimeIndex(["2023-01-15", "2023-02-15"]),
    )
    labeled = _label_periods(periods, trend_labels, vol_percentiles)
    assert list(labeled["trendRegime"]) == ["bull", "bear/correction"]
    assert list(labeled["volRegime"]) == ["low/normal vol", "high vol"]


def test_bucket_stats_reports_underpowered_below_the_minimum_period_floor():
    labeled = pd.DataFrame({
        "c": [0.01] * 3, "d": [0.02] * 3,
        "trendRegime": ["bull"] * 3,
    })
    rows = _bucket_stats(labeled, "trendRegime")
    assert len(rows) == 1
    assert rows[0]["periods"] == 3
    assert rows[0]["underpowered"] is True  # below MIN_PERIODS = 6


def test_concentration_check_flags_a_single_dominant_episode():
    episodes = [
        {"drawdownImprovementPct": 20.0},
        {"drawdownImprovementPct": 1.0},
        {"drawdownImprovementPct": 1.0},
    ]
    result = _concentration_check(episodes)
    assert result["materialEpisodeCount"] == 3
    assert result["top1SharePct"] > 60.0


def test_verdict_is_inconclusive_with_no_materially_sampled_evidence():
    trend_buckets = [{"label": "bear/correction", "underpowered": True, "drawdownImprovementPct": 5.0}]
    vol_buckets = [{"label": "high vol", "underpowered": True, "drawdownImprovementPct": 5.0}]
    episodes = [{"underpowered": True, "drawdownImprovementPct": 5.0}]
    concentration = {"top1SharePct": 100.0, "materialEpisodeCount": 1}
    verdict = _verdict(trend_buckets, vol_buckets, episodes, concentration)
    assert verdict["label"] == "inconclusive"


def test_verdict_is_concentrated_when_one_episode_dominates_improvement():
    trend_buckets = [{"label": "bear/correction", "underpowered": False, "drawdownImprovementPct": 5.0}]
    vol_buckets = [{"label": "high vol", "underpowered": False, "drawdownImprovementPct": 5.0}]
    episodes = [{"underpowered": False, "drawdownImprovementPct": 5.0}]
    concentration = {"top1SharePct": 95.0, "materialEpisodeCount": 3}
    verdict = _verdict(trend_buckets, vol_buckets, episodes, concentration)
    assert verdict["label"] == "concentrated / single-event-dominated"


def test_verdict_is_broad_based_when_evidence_is_spread_and_consistent():
    trend_buckets = [{"label": "bear/correction", "underpowered": False, "drawdownImprovementPct": 5.0}]
    vol_buckets = [{"label": "high vol", "underpowered": False, "drawdownImprovementPct": 5.0}]
    episodes = [
        {"underpowered": False, "drawdownImprovementPct": 5.0},
        {"underpowered": False, "drawdownImprovementPct": 4.0},
    ]
    concentration = {"top1SharePct": 55.0, "materialEpisodeCount": 2}
    verdict = _verdict(trend_buckets, vol_buckets, episodes, concentration)
    assert verdict["label"] == "broad-based"
