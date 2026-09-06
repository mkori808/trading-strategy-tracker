from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.dm_portfolio_concentration_audit import _cluster_spearman_ci, _risk_snapshot, _strict_json


def test_risk_snapshot_uses_only_history_strictly_before_rebalance() -> None:
    index = pd.date_range("2024-01-01", periods=70, freq="B")
    bars = {}
    for i, symbol in enumerate(["A", "B"]):
        close = pd.Series(100 * np.cumprod(1 + .001 * np.sin(np.arange(70) + i)), index=index)
        bars[symbol] = pd.DataFrame({"Close": close})
    rows, summary = _risk_snapshot(index[65], {"A": .5, "B": .5}, bars)
    assert len(rows) == 2
    assert summary["observations"] == 60
    assert sum(r["variance_contribution"] for r in rows) == pytest.approx(1.0)


def test_rank_inference_counts_rebalance_clusters() -> None:
    frame = pd.DataFrame({"rebalance_date": np.repeat(pd.date_range("2024-01-01", periods=4, freq="MS"), 5),
                          "rank": list(range(1, 6)) * 4, "holding_return": list(range(5, 0, -1)) * 4})
    result = _cluster_spearman_ci(frame)
    assert result["effective_n"] == 4
    assert result["spearman"] == -1.0


def test_machine_readable_output_replaces_nonfinite_values() -> None:
    assert _strict_json({"missing": float("nan")}) == {"missing": None}
