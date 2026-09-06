from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.dm_replacement_quality import (
    _future_path, _historical_state, _shuffle_outcomes_by_cluster, build_event_ledger,
)


def _bars(periods: int = 220) -> pd.DataFrame:
    index = pd.bdate_range("2023-01-02", periods=periods)
    close = np.linspace(100.0, 150.0, periods)
    return pd.DataFrame({"Open": close - .2, "High": close + 1, "Low": close - 1,
                         "Close": close, "Volume": 1_000}, index=index)


def test_recent_features_use_only_bars_before_decision() -> None:
    bars = _bars()
    day = bars.index[200]
    state = _historical_state(bars, day, bars.index[150])
    expected20 = bars.Close.iloc[199] / bars.Close.iloc[179] - 1
    assert state["return_20d"] == pytest.approx(expected20)
    assert state["holding_sessions"] == 50
    assert state["currently_profitable"] is True


def test_future_pair_path_uses_same_open_to_open_window() -> None:
    bars = _bars(40)
    result = _future_path(bars, bars.index[10], bars.index[30])
    assert result is not None
    assert result["return"] == pytest.approx(bars.Open.iloc[30] / bars.Open.iloc[10] - 1)
    assert result["mfe"] == pytest.approx(bars.High.iloc[10:30].max() / bars.Open.iloc[10] - 1)


def test_cluster_shuffle_preserves_each_outcome_vector_and_cluster_sizes() -> None:
    frame = pd.DataFrame({"rebalance_date": ["a", "a", "b", "b", "c"],
                          "replacement_advantage": [1.0, 2.0, 3.0, 4.0, 5.0]})
    shuffled = _shuffle_outcomes_by_cluster(frame, np.random.default_rng(4))
    assert sorted(shuffled) == [1, 2, 3, 4, 5]
    # The singleton cannot exchange with a two-row cluster.
    assert shuffled[-1] == 5.0
    assert tuple(shuffled[:2]) in ((1.0, 2.0), (3.0, 4.0))


def test_canonical_dm_replacement_ledger_reconciles() -> None:
    # build_event_ledger reruns the canonical strategy against LIVE data up to
    # today (run_cross_sectional(..., persist=False)), so the exact event
    # count grows by one every time a new real-world monthly rebalance
    # occurs -- pinned at 81 on the date this test was written, observed at
    # 82 as of 2026-09-06 (one more month of canonical paper trading). A
    # floor, not an exact match, is the correct invariant: past events never
    # disappear, so a real regression that drops or fails to load events
    # would still be caught, while ordinary calendar progress would not.
    events, audit, _result = build_event_ledger("Dual Momentum")
    assert len(events) >= 81
    assert audit["effectiveN"] >= 53
    assert audit["duplicatePairs"] == 0
    assert audit["excludedMissingPricePairs"] == 0
    assert audit["identicalWindows"]
    assert audit["allRemovals"] == (
        audit["completedPairs"] + audit["censoredLastRebalancePairs"]
        + audit["unpairedRemovalsFromCashChanges"]
    )
    assert events[["score_spread", "rank_gap", "outgoing_unrealized_return"]].notna().all().all()
