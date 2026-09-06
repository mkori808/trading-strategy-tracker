import pandas as pd
import pytest

from engine.recover_dow_pit_extended_prices import LINEAGE, _backward_adjust_in_window


def test_every_requested_blocker_has_explicit_lineage():
    assert list(LINEAGE) == ["WBA", "SBC", "DWDP", "DOW", "AA", "UTX", "KFT", "EK", "GM"]
    assert all(row["required"] and row["event"] and row["identitySource"] for row in LINEAGE.values())


def test_equity_extinguishing_events_are_not_rename_classifications():
    assert LINEAGE["EK"]["event"] == "bankruptcy_equity_extinguished"
    assert LINEAGE["GM"]["event"] == "bankruptcy_asset_sale_equity_extinguished"
    assert LINEAGE["DOW"]["event"] == "genuinely_new_spinoff_security"


def _raw_frame(dates, close_values):
    index = pd.DatetimeIndex(dates)
    return pd.DataFrame({
        "Open": close_values, "High": close_values, "Low": close_values,
        "Close": close_values, "Volume": [1000] * len(close_values),
    }, index=index)


def test_in_window_split_needs_no_reapplication():
    """Regression test for the double-adjustment bug: yfinance's raw
    (auto_adjust=False) Close is already permanently split-adjusted, so a
    split dated INSIDE the window must be left alone -- the series must stay
    perfectly flat/continuous across it, not show a step."""
    dates = ["2010-01-04", "2010-01-05", "2010-01-06", "2010-01-07"]
    frame = _raw_frame(dates, [50.0, 50.5, 50.2, 50.8])
    splits = pd.Series([2.0], index=pd.DatetimeIndex(["2010-01-06"]))
    dividends = pd.Series(dtype=float)

    adjusted, _ = _backward_adjust_in_window(
        frame, splits, dividends, pd.Timestamp("2010-01-01"), pd.Timestamp("2010-01-31"),
    )

    assert list(adjusted["Close"]) == [50.0, 50.5, 50.2, 50.8]


def test_post_window_split_is_reversed_for_the_entire_window():
    """Regression test for the missing-reversal bug: a split/merger-ratio
    dated AFTER window_end is still retroactively baked into every bar in
    the window by the provider, and must be multiplied back out uniformly."""
    dates = ["2010-01-04", "2010-01-05", "2010-01-06"]
    frame = _raw_frame(dates, [50.0, 51.0, 52.0])
    splits = pd.Series([1.589], index=pd.DatetimeIndex(["2020-04-03"]))
    dividends = pd.Series(dtype=float)

    adjusted, detail = _backward_adjust_in_window(
        frame, splits, dividends, pd.Timestamp("2010-01-01"), pd.Timestamp("2010-01-31"),
    )

    expected = [50.0 * 1.589, 51.0 * 1.589, 52.0 * 1.589]
    assert list(adjusted["Close"]) == pytest.approx(expected)
    assert any(row["kind"] == "post_window_split_reversed" for row in detail["adjustmentsApplied"])


def test_split_before_window_start_is_ignored_not_reapplied():
    """Regression test: an ancient split from long before the fetch window
    (e.g. a company's own 1960s-70s split history) has no bars to apply to
    and must not spuriously affect the window -- caught when DD/RTX's full
    split history included pre-1998 entries that an earlier <=window_end-only
    filter incorrectly matched."""
    dates = ["2016-01-04", "2016-01-05"]
    frame = _raw_frame(dates, [100.0, 101.0])
    splits = pd.Series([2.0], index=pd.DatetimeIndex(["1973-05-10"]))
    dividends = pd.Series(dtype=float)

    adjusted, detail = _backward_adjust_in_window(
        frame, splits, dividends, pd.Timestamp("2016-01-01"), pd.Timestamp("2016-12-31"),
    )

    assert list(adjusted["Close"]) == [100.0, 101.0]
    assert detail["adjustmentsApplied"] == []


def test_in_window_dividend_is_applied_backward_cascading():
    dates = ["2010-01-04", "2010-01-05", "2010-01-06"]
    frame = _raw_frame(dates, [100.0, 100.0, 100.0])
    splits = pd.Series(dtype=float)
    dividends = pd.Series([1.0], index=pd.DatetimeIndex(["2010-01-06"]))

    adjusted, _ = _backward_adjust_in_window(
        frame, splits, dividends, pd.Timestamp("2010-01-01"), pd.Timestamp("2010-01-31"),
    )

    # Bars strictly before the ex-date get reduced by (1 - dividend/prevClose);
    # the ex-date's own bar and later are untouched.
    assert adjusted["Close"].iloc[0] == pytest.approx(99.0)
    assert adjusted["Close"].iloc[1] == pytest.approx(99.0)
    assert adjusted["Close"].iloc[2] == pytest.approx(100.0)


def test_dividend_outside_window_is_ignored():
    dates = ["2010-01-04", "2010-01-05"]
    frame = _raw_frame(dates, [100.0, 100.0])
    splits = pd.Series(dtype=float)
    dividends = pd.Series([5.0], index=pd.DatetimeIndex(["2010-06-01"]))  # after window_end

    adjusted, detail = _backward_adjust_in_window(
        frame, splits, dividends, pd.Timestamp("2010-01-01"), pd.Timestamp("2010-01-31"),
    )

    assert list(adjusted["Close"]) == [100.0, 100.0]
    assert detail["adjustmentsApplied"] == []


def test_post_window_split_and_in_window_dividend_compose_correctly():
    dates = ["2010-01-04", "2010-01-05", "2010-01-06"]
    frame = _raw_frame(dates, [100.0, 100.0, 100.0])
    splits = pd.Series([2.0], index=pd.DatetimeIndex(["2011-01-01"]))
    dividends = pd.Series([1.0], index=pd.DatetimeIndex(["2010-01-06"]))

    adjusted, _ = _backward_adjust_in_window(
        frame, splits, dividends, pd.Timestamp("2010-01-01"), pd.Timestamp("2010-01-31"),
    )

    # Split reversal (x2) applies to the whole window; the dividend then
    # additionally reduces bars strictly before its ex-date.
    assert adjusted["Close"].iloc[0] == pytest.approx(100.0 * 2.0 * 0.99)
    assert adjusted["Close"].iloc[2] == pytest.approx(100.0 * 2.0)
