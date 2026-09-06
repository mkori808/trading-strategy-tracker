from datetime import date

import pandas as pd

from engine.fetch_sp500_pit_free_prices import (
    OK_MISSING_FRACTION_THRESHOLD,
    _covers,
    _missing_sessions,
    assess,
    ticker_windows,
)


def _write_symbol_map(path, rows):
    pd.DataFrame(rows, columns=[
        "historical_symbol", "vendor_symbol", "start_date", "end_date", "reason",
    ]).to_csv(path, index=False)
    return path


def test_ticker_windows_unions_multiple_tenures(tmp_path):
    path = _write_symbol_map(tmp_path / "symbol_map.csv", [
        ("RIG", "RIG", "2000-01-01", "2010-01-01", "identity_ambiguous_multi_tenure"),
        ("RIG", "RIG", "2015-01-01", "2020-01-01", "identity_ambiguous_multi_tenure"),
        ("AAPL", "AAPL", "1996-01-02", "", "standard"),
    ])
    windows = ticker_windows(path)

    assert windows["RIG"] == (date(2000, 1, 1), date(2020, 1, 1))
    assert windows["AAPL"] == (date(1996, 1, 2), date.today())


def test_covers_requires_warmup_before_the_requested_start():
    index = pd.date_range("2020-01-01", "2020-12-31", freq="B")
    frame = pd.DataFrame({"Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1}, index=index)

    # Frame starts 2020-01-01; a requested start late enough in the year that
    # 300 days of warmup before it still lands inside the frame is covered.
    assert _covers(frame, date(2020, 11, 1), date(2020, 12, 1)) is True
    # Requested start is only two weeks after the frame begins -- not enough
    # warmup (WARMUP_CALENDAR_DAYS=300) before it.
    assert _covers(frame, date(2020, 1, 15), date(2020, 12, 1)) is False


def test_covers_is_false_for_an_empty_frame():
    assert _covers(pd.DataFrame(columns=["Open"]), date(2020, 1, 1), date(2020, 6, 1)) is False


def test_missing_sessions_counts_gaps_inside_a_real_series():
    index = pd.bdate_range("2020-01-01", "2020-01-31").delete([2, 5])  # drop 2 business days
    frame = pd.DataFrame({"Open": 1}, index=index)

    missing = _missing_sessions(frame, date(2020, 1, 1), date(2020, 1, 31))
    assert missing == 2


def test_missing_sessions_is_full_expected_count_for_empty_frame():
    missing = _missing_sessions(pd.DataFrame(columns=["Open"]), date(2020, 1, 1), date(2020, 1, 31))
    assert missing == len(pd.bdate_range("2020-1-1", "2020-1-31"))


def test_assess_marks_a_complete_series_ok_despite_holiday_noise(tmp_path, monkeypatch):
    import engine.fetch_sp500_pit_free_prices as module
    monkeypatch.setattr(module, "PRICES_DIR", tmp_path)
    # A real multi-year business-day series will be missing ~3.5% of
    # bdate_range's expected sessions purely from market holidays it can't
    # see -- this must still classify as OK, not PARTIAL_COVERAGE.
    index = pd.bdate_range("2010-01-01", "2020-12-31")
    keep = [i for i in range(len(index)) if i % 28 != 0]  # ~3.6% dropped, holiday-like rate
    frame = pd.DataFrame(
        {"Open": 1, "High": 1, "Low": 1, "Close": 1, "Volume": 1}, index=index[keep],
    )
    frame.to_parquet(tmp_path / "TEST.parquet")

    report = assess({"TEST": (date(2010, 1, 1), date(2020, 12, 31))})

    assert report["TEST"]["status"] == "OK"
    assert report["TEST"]["missing_sessions"] / report["TEST"]["expected_sessions"] < OK_MISSING_FRACTION_THRESHOLD


def test_assess_flags_missing_history_without_dropping_the_symbol(tmp_path, monkeypatch):
    import engine.fetch_sp500_pit_free_prices as module
    monkeypatch.setattr(module, "PRICES_DIR", tmp_path)

    report = assess({"GHOST": (date(2000, 1, 1), date(2005, 1, 1))})

    assert report["GHOST"]["status"] == "MISSING_DELISTED_PRICE_HISTORY"
    assert report["GHOST"]["ticker"] == "GHOST"  # present in the report, not dropped
    assert report["GHOST"]["missing_sessions"] == report["GHOST"]["expected_sessions"]
