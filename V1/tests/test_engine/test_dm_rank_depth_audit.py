from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.dm_rank_depth_audit import (
    GROUPS,
    MATERIAL_H1,
    _forward_path,
    compare_groups,
    drawdown_behavior,
    rank_inference,
    security_concentration,
    stability_analysis,
    shuffled_rank_null,
)
from strategies.swing.dual_momentum import DualMomentum


def _ledger(months: int = 8) -> pd.DataFrame:
    rows = []
    for month, day in enumerate(pd.date_range("2024-01-01", periods=months, freq="MS")):
        returns = np.linspace(.10, .01, 10) + month * .0001
        for rank, value in enumerate(returns, 1):
            rows.append({
                "rebalance_date": day,
                "next_rebalance_date": day + pd.offsets.MonthBegin(),
                "symbol": f"S{rank}",
                "rank": rank,
                "forward_return": value,
                "forward_mfe": value + .01,
                "forward_mae": min(value - .02, 0),
                "beat_spy": value > .03,
                "beat_universe_median": rank <= 5,
                "future_top_quartile": rank <= 3,
            })
    return pd.DataFrame(rows)


def test_preregistered_groups_and_canonical_top_n_are_unchanged() -> None:
    assert GROUPS["top3"] == (1, 2, 3)
    assert GROUPS["lowerSelected"] == (4, 5)
    assert GROUPS["nearMisses"] == (6, 7, 8, 9, 10)
    assert MATERIAL_H1 == .01
    strategy = DualMomentum()
    assert strategy.lookback_trading_days == 189
    assert strategy.top_n == 5
    assert strategy.rebalance_frequency == "monthly"


def test_forward_path_requires_exact_rebalance_prices() -> None:
    index = pd.date_range("2024-01-02", periods=5, freq="B")
    frame = pd.DataFrame({
        "Open": [100, 101, 102, 103, 104],
        "High": [101, 103, 104, 105, 106],
        "Low": [99, 100, 101, 102, 103],
        "Close": [100, 102, 103, 104, 105],
    }, index=index)
    outcome = _forward_path(frame, index[0], index[4])
    assert outcome is not None
    assert outcome[0] == pytest.approx(.04)
    # A missing decision session must not silently substitute the next row.
    assert _forward_path(frame, pd.Timestamp("2024-01-01"), index[4]) is None


def test_group_comparison_clusters_by_rebalance_date() -> None:
    result = compare_groups(_ledger(), GROUPS["top3"], GROUPS["lowerSelected"], draws=100)
    assert result["effectiveN"] == 8
    assert result["meanReturnDifference"] == pytest.approx(.025)
    assert result["clusterCi95"][0] > 0


def test_rank_inference_uses_rebalance_as_effective_n() -> None:
    result = rank_inference(_ledger(), draws=100)
    assert result["effectiveN"] == 8
    assert result["rawN"] == 80
    assert result["spearman"] < -.99
    assert result["strictlyMonotonicMeanCurve"] is True


def test_within_rebalance_null_detects_ordered_synthetic_edge() -> None:
    result = shuffled_rank_null(_ledger(20), draws=250)
    assert result["H1Top3MinusRanks4To5"]["observed"] > 0
    assert result["H1Top3MinusRanks4To5"]["directionalEmpiricalP"] < .05
    assert result["H2Top5MinusRanks6To10"]["directionalEmpiricalP"] < .05
    assert result["H3RankReturnSpearman"]["directionalEmpiricalP"] < .05


def test_drawdown_boundaries_accept_timezone_aware_canonical_dates() -> None:
    frame = _ledger()
    frame["rebalance_date"] = frame.rebalance_date.dt.tz_localize("America/New_York")
    frame["next_rebalance_date"] = frame.next_rebalance_date.dt.tz_localize("America/New_York")
    assert len(drawdown_behavior(frame)) == 3


def test_security_stability_keeps_rank_and_rebalance_structure() -> None:
    frame = _ledger()
    stability = stability_analysis(frame)
    assert stability["firstHalfTop3MinusRanks4To5"] == pytest.approx(.025)
    assert stability["allLeaveOneSecurityOutPositive"] is True
    contributors = security_concentration(frame)
    assert {row["rank"] for row in contributors["byRank"]} == set(range(1, 11))
    assert {row["rankGroup"] for row in contributors["byGroup"]} == {
        "top3", "ranks4To5", "ranks6To10",
    }
