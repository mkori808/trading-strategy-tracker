from __future__ import annotations

import pandas as pd
import pytest

from engine.trade_lifecycle_audit import _future_metrics, _position_row, _rank_bucket


def _bars() -> pd.DataFrame:
    index = pd.date_range("2024-01-02", periods=8, freq="B")
    return pd.DataFrame({
        "Open": [100, 101, 104, 103, 106, 105, 108, 109],
        "High": [102, 105, 106, 107, 108, 109, 110, 111],
        "Low": [98, 99, 101, 100, 104, 103, 106, 107],
        "Close": [101, 104, 103, 106, 105, 108, 109, 110],
        "Volume": [1_000] * 8,
    }, index=index)


def test_position_uses_exit_open_and_excludes_exit_day_high_low() -> None:
    bars = _bars()
    row = _position_row(
        "Dual Momentum", "TEST", bars.index[0], bars.index[5], bars,
        1, 6, .20, .10, "fell below top-N",
    )
    assert row is not None
    assert row["exit_price"] == 105
    assert row["realized_return"] == pytest.approx(.05)
    # Exit-day high is 109, but is unavailable after the open exit.
    assert row["intraday_high_mfe"] == pytest.approx(.08)
    assert row["holding_sessions"] == 5


def test_post_exit_horizon_starts_from_exit_open() -> None:
    bars = _bars()
    metrics = _future_metrics(bars, bars.index[2], 104)
    assert metrics["post_exit_5d_return"] == pytest.approx(109 / 104 - 1)
    assert metrics["post_exit_10d_return"] is None


def test_rank_buckets_are_fixed_relative_to_top_n() -> None:
    assert _rank_bucket(6, 5) == "just outside boundary"
    assert _rank_bucket(10, 5) == "just outside boundary"
    assert _rank_bucket(11, 5) == "moderate deterioration"
    assert _rank_bucket(21, 5) == "severe/unranked"
    assert _rank_bucket(None, 5) == "severe/unranked"
