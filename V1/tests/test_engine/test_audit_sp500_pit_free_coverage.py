from datetime import date

from engine.audit_sp500_pit_free_coverage import (
    _has_bar_near,
    compute_coverage_by_date,
    determine_usable_window,
    rebalance_dates,
    roster_by_date,
)


def test_rebalance_dates_span_first_of_month_across_open_and_closed_intervals():
    intervals = [
        ("A", date(2020, 1, 15), date(2020, 3, 10)),
        ("B", date(2020, 2, 1), None),
    ]
    dates = rebalance_dates(intervals)
    assert dates[0] == date(2020, 1, 1)
    assert dates[-1] == date(2020, 3, 1)


def test_roster_by_date_respects_open_ended_and_closed_tenures():
    dates = [date(2020, 1, 1), date(2020, 2, 1), date(2020, 3, 1)]
    intervals = [
        ("A", date(2020, 1, 15), date(2020, 2, 15)),
        ("B", date(2020, 2, 1), None),
    ]
    rosters = roster_by_date(intervals, dates)
    assert rosters[date(2020, 1, 1)] == set()  # A starts mid-January
    assert rosters[date(2020, 2, 1)] == {"A", "B"}
    assert rosters[date(2020, 3, 1)] == {"B"}  # A's tenure ended before March


def test_has_bar_near_respects_tolerance():
    bars = [date(2020, 1, 1), date(2020, 1, 20)]
    assert _has_bar_near(bars, date(2020, 1, 3), 7) is True
    assert _has_bar_near(bars, date(2020, 1, 10), 7) is False
    assert _has_bar_near([], date(2020, 1, 1), 7) is False


def test_compute_coverage_by_date_counts_only_tickers_with_a_nearby_bar(tmp_path):
    import pandas as pd

    index = pd.date_range("2020-01-01", "2020-12-31", freq="B")
    pd.DataFrame({"Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1}, index=index).to_parquet(
        tmp_path / "HASPRICE.parquet"
    )
    intervals = [
        ("HASPRICE", date(2020, 1, 1), None),
        ("NOPRICE", date(2020, 1, 1), None),
    ]
    dates = [date(2020, 6, 1)]
    rows = compute_coverage_by_date(intervals, dates, prices_dir=tmp_path)

    assert rows == [{
        "date": "2020-06-01", "pit_members": 2, "with_valid_prices": 1, "coverage_pct": 50.0,
    }]


def test_usable_window_picks_the_longest_qualifying_trailing_suffix():
    rows = [
        {"date": "2000-01-01", "coverage_pct": 50.0},
        {"date": "2000-02-01", "coverage_pct": 60.0},
        {"date": "2000-03-01", "coverage_pct": 99.0},
        {"date": "2000-04-01", "coverage_pct": 99.0},
        {"date": "2000-05-01", "coverage_pct": 99.0},
    ]
    result = determine_usable_window(rows)
    assert result["usableStartDate"] == "2000-03-01"
    assert result["usableEndDate"] == "2000-05-01"
    assert result["verdict"] == "USABLE_WINDOW_DETERMINED"


def test_usable_window_reports_no_window_rather_than_lowering_the_bar():
    rows = [{"date": "2000-01-01", "coverage_pct": 10.0}]
    result = determine_usable_window(rows)
    assert result["verdict"] == "NO_WINDOW_CLEARS_THE_PREREGISTERED_THRESHOLD"
    assert result["usableStartDate"] is None
