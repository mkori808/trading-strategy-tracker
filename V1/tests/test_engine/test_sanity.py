import pandas as pd
import pytest

from engine.sanity import SanityError, calendar_daily_series, check_sharpe


def test_calendar_daily_series_forward_fills_sparse_gaps():
    # Three widely-spaced points, as engine/portfolio.py:run_portfolio_backtest
    # produces (one point per trade entry/exit, not per calendar day).
    index = pd.DatetimeIndex(["2024-01-02", "2024-01-16", "2024-02-13"])
    sparse = pd.Series([100.0, 110.0, 121.0], index=index)

    daily = calendar_daily_series(sparse)

    # Business days between the first and last point, not just the 3 events.
    assert len(daily) > 20
    # A flat stretch between events forward-fills, not NaN or interpolated.
    assert daily.loc["2024-01-10"] == 100.0


def test_calendar_daily_series_handles_tz_aware_index():
    # Real equity curves from this project's engines carry America/New_York
    # tz-aware timestamps (see CLAUDE.md's timezone convention). pd.bdate_range
    # infers tz from tz-aware start/end Timestamps in current pandas and
    # returns an ALREADY tz-aware index -- a second tz_localize() on top of
    # that raises TypeError. Caught only when run against a real (tz-aware)
    # equity curve; an earlier naive-index synthetic check missed it.
    index = pd.date_range("2024-01-02", periods=3, freq="14D", tz="America/New_York")
    sparse = pd.Series([100.0, 110.0, 121.0], index=index)

    daily = calendar_daily_series(sparse)

    assert len(daily) > 10
    assert daily.index.tz is not None


def test_calendar_daily_series_too_short_returns_empty():
    assert calendar_daily_series(pd.Series(dtype=float)).empty
    assert calendar_daily_series(pd.Series([1.0], index=[pd.Timestamp("2024-01-01")])).empty


def test_calendar_daily_series_noop_on_already_daily_series():
    index = pd.bdate_range("2024-01-01", periods=10)
    already_daily = pd.Series(range(10), index=index, dtype=float)

    daily = calendar_daily_series(already_daily)

    assert len(daily) == 10
    assert daily.tolist() == already_daily.tolist()


def test_sharpe_sanity_check_has_no_finite_range_blocker():
    check_sharpe(51_844.877)
    check_sharpe(-11_725_571_887.534)


@pytest.mark.parametrize("sharpe", [float("nan"), float("inf"), float("-inf")])
def test_sharpe_sanity_check_still_rejects_non_finite_values(sharpe):
    with pytest.raises(SanityError, match="not finite"):
        check_sharpe(sharpe)
