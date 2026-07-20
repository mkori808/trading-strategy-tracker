"""Unit tests for engine/compare_dual_momentum_robustness.py's pure helpers
-- _row_from_slice's stat computation and the regime-slice list's own
integrity. No network; the actual backtest runs are exercised by manually
running the script, not by the test suite."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from engine.compare_dual_momentum_robustness import REGIME_SLICES, _row_from_slice


def _synthetic_curve(start_value: float, n: int, daily_return: float) -> pd.Series:
    idx = pd.date_range("2020-01-01", periods=n, freq="B", tz="America/New_York")
    values = [start_value * (1 + daily_return) ** i for i in range(n)]
    return pd.Series(values, index=idx)


def test_row_from_slice_computes_return_and_status() -> None:
    eq = _synthetic_curve(10_000, 300, 0.001)  # steadily rising
    row = _row_from_slice("test period", eq, rf=0.0, benchmark=None)
    assert row["period"] == "test period"
    assert row["trading_days"] == 300
    assert row["return_pct"] > 0
    assert row["status"] in {"Positive return - shortlist", "Positive return but underperforms cash/benchmark - hold"}


def test_row_from_slice_handles_too_short_a_window() -> None:
    eq = pd.Series([10_000.0], index=[pd.Timestamp("2020-01-01", tz="America/New_York")])
    row = _row_from_slice("too short", eq, rf=0.0, benchmark=None)
    assert row["return_pct"] is None
    assert row["status"] == "Not enough data in window"


def test_row_from_slice_flags_underperformance_vs_benchmark() -> None:
    eq = _synthetic_curve(10_000, 300, 0.0005)  # modest, positive gain
    row = _row_from_slice("test period", eq, rf=0.0, benchmark=1000.0)  # SPY +1000% same window
    assert row["return_pct"] > 0
    assert row["status"] == "Positive return but underperforms cash/benchmark - hold"


def test_regime_slices_are_contiguous_and_ordered() -> None:
    for i in range(1, len(REGIME_SLICES)):
        prev_end = REGIME_SLICES[i - 1][2]
        cur_start = REGIME_SLICES[i][1]
        # Each slice starts the day after the previous one ends -- no gap,
        # no overlap.
        assert cur_start == prev_end + timedelta(days=1), (
            f"{REGIME_SLICES[i-1][0]} ends {prev_end}, "
            f"{REGIME_SLICES[i][0]} starts {cur_start}"
        )


def test_regime_slices_span_from_2000_to_today() -> None:
    assert REGIME_SLICES[0][1] == date(2000, 1, 1)
    assert REGIME_SLICES[-1][2] == date.today()
