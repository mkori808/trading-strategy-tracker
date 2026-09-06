"""Point-in-time dividend math.

All network access is monkeypatched out -- these test the alignment and
window arithmetic, which is where the real bugs were.
"""

from __future__ import annotations

import pandas as pd
import pytest

from engine import fundamentals as fm

NY = "America/New_York"


def _payments(dates_and_amounts) -> pd.Series:
    """Dividend payments stamped 09:30 NY, the way yfinance returns them."""
    index = pd.DatetimeIndex([pd.Timestamp(f"{d} 09:30", tz=NY) for d, _ in dates_and_amounts])
    return pd.Series([a for _, a in dates_and_amounts], index=index, dtype=float)


def _bars(start: str, end: str) -> pd.DatetimeIndex:
    """Daily bar index stamped 20:00 on the PREVIOUS calendar day -- the quirk
    engine/data.py's UTC->NY localization produces, and the reason a naive
    date-match silently loses almost every dividend."""
    days = pd.bdate_range(start=start, end=end)
    stamped = [pd.Timestamp(f"{d.date()} 20:00", tz=NY) for d in days]
    return pd.DatetimeIndex(stamped) - pd.Timedelta(days=1)


def _quarterly(amounts_by_year: dict[int, float]) -> pd.Series:
    rows = []
    for year, amount in sorted(amounts_by_year.items()):
        for month, day in ((1, 10), (4, 10), (7, 10), (10, 10)):
            rows.append((f"{year}-{month:02d}-{day:02d}", amount))
    return _payments(rows)


@pytest.fixture
def raiser(monkeypatch):
    """A serial raiser: $0.85/quarter in 2019 climbing to $1.20 by 2024."""
    payments = _quarterly({
        2019: 0.85, 2020: 0.90, 2021: 0.95,
        2022: 1.00, 2023: 1.10, 2024: 1.20, 2025: 1.20,
    })
    monkeypatch.setattr(fm, "dividends", lambda symbol, **k: payments)
    return payments


def test_trailing_dividends_survive_the_bar_timestamp_offset(raiser):
    """Regression: the first implementation matched payment DATES to bar
    DATES. Bars are stamped 20:00 the prior evening, so nearly every payment
    missed and VZ -- a serial dividend RAISER -- was reported as cutting on
    379 bars. TTM must find all four quarters regardless of stamping."""
    ttm = fm.trailing_dividends("X", _bars("2023-01-02", "2023-12-01"))
    assert ttm.iloc[-1] == pytest.approx(4.40, abs=1e-6)  # four $1.10 payments


def test_trailing_dividends_never_exceeds_one_year_of_payments(raiser):
    ttm = fm.trailing_dividends("X", _bars("2022-01-03", "2025-06-01"))
    assert ttm.max() <= 4.80 + 1e-9  # four $1.20 payments, never more


def test_dividend_growth_yoy_is_available_on_the_first_bar(raiser):
    """Windows come from full payment history, not from the bars in view, so
    a YoY figure exists immediately rather than after a 252-bar warmup."""
    growth = fm.dividend_growth_yoy_pct("X", _bars("2023-11-01", "2023-12-01"))
    assert growth.notna().all()
    assert growth.iloc[-1] == pytest.approx(10.0, abs=0.5)  # 4.40 vs 4.00


def test_dividend_cagr_5y(raiser):
    # TTM 4 x $1.20 = 4.80 against 4 x $0.90 = 3.60 five years earlier.
    cagr = fm.dividend_cagr_5y_pct("X", _bars("2025-11-01", "2025-12-01"))
    assert cagr.iloc[-1] == pytest.approx(5.92, abs=0.05)


def test_dividend_cagr_is_nan_without_five_years_of_history(raiser):
    """Insufficient history must be NaN -- which fails a screen -- rather
    than a number derived from a partial window."""
    cagr = fm.dividend_cagr_5y_pct("X", _bars("2021-02-01", "2021-03-01"))
    assert cagr.isna().all()


def test_a_raiser_is_never_flagged_as_cutting(raiser):
    assert not fm.dividend_cut_series("X", _bars("2022-06-01", "2025-11-01")).any()


def test_a_real_cut_is_detected(monkeypatch):
    payments = _quarterly({2021: 1.00, 2022: 1.00, 2023: 0.25, 2024: 0.25})
    monkeypatch.setattr(fm, "dividends", lambda symbol, **k: payments)
    cuts = fm.dividend_cut_series("X", _bars("2023-01-02", "2023-12-01"))
    assert cuts.any()
    assert cuts.iloc[-1]


def test_a_suspension_is_detected(monkeypatch):
    """Payments simply stop -- the sharpest version of the floor giving way."""
    payments = _quarterly({2021: 1.00, 2022: 1.00})
    monkeypatch.setattr(fm, "dividends", lambda symbol, **k: payments)
    assert fm.dividend_cut_series("X", _bars("2023-06-01", "2023-12-01")).any()


def test_a_payment_date_slip_is_not_a_cut(monkeypatch):
    """Quarterly payments sit ~91 days apart against a 365-day window, so the
    window intermittently holds 3 payments instead of 4 and shows a phantom
    ~25% cut that resolves within days. Measured at 24 such bars on AAPL,
    which has never cut. Only a decline that PERSISTS counts."""
    payments = _payments([
        ("2021-01-10", 1.0), ("2021-04-10", 1.0), ("2021-07-10", 1.0), ("2021-10-10", 1.0),
        ("2022-01-05", 1.0), ("2022-04-05", 1.0), ("2022-07-05", 1.0), ("2022-10-05", 1.0),
        # Next year's first payment slips two weeks, briefly emptying the
        # trailing window of one quarter.
        ("2023-01-20", 1.0), ("2023-04-05", 1.0), ("2023-07-05", 1.0), ("2023-10-05", 1.0),
        ("2024-01-05", 1.0), ("2024-04-05", 1.0), ("2024-07-05", 1.0), ("2024-10-05", 1.0),
    ])
    monkeypatch.setattr(fm, "dividends", lambda symbol, **k: payments)
    assert not fm.dividend_cut_series("X", _bars("2022-06-01", "2024-06-01")).any()


def test_no_dividend_history_yields_zero_not_an_error(monkeypatch):
    monkeypatch.setattr(fm, "dividends", lambda symbol, **k: pd.Series(dtype=float))
    index = _bars("2023-01-02", "2023-03-01")
    assert (fm.trailing_dividends("X", index) == 0).all()
    assert not fm.dividend_cut_series("X", index).any()


def test_yield_is_nan_without_price_data_never_zero(monkeypatch, raiser):
    """A missing price must FAIL a '> 4%' screen, not silently pass a
    '< X%' one. NaN propagates; zero would not."""
    monkeypatch.setattr(fm, "unadjusted_close", lambda *a, **k: pd.Series(dtype=float))
    assert fm.trailing_yield_pct("X", _bars("2023-01-02", "2023-03-01")).isna().all()


def test_yield_uses_unadjusted_price(monkeypatch, raiser):
    index = _bars("2023-01-02", "2023-12-01")
    monkeypatch.setattr(fm, "unadjusted_close", lambda *a, **k: pd.Series(100.0, index=index))
    # four $1.10 payments on a $100 price = 4.4%
    assert fm.trailing_yield_pct("X", index).iloc[-1] == pytest.approx(4.4, abs=1e-6)


def test_window_sum_never_counts_a_payment_after_the_bar(raiser):
    """Look-ahead guard: a bar's value must not move when later bars and
    later payments come into view."""
    early = _bars("2022-05-02", "2022-08-01")
    late = _bars("2022-05-02", "2025-06-01")
    assert fm.trailing_dividends("X", early).iloc[-1] == pytest.approx(
        fm.trailing_dividends("X", late).iloc[len(early) - 1]
    )


def test_snapshot_missing_fields_are_none_not_defaults(monkeypatch, tmp_path):
    class _Blank:
        info: dict = {}

    monkeypatch.setattr(fm, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fm.yf, "Ticker", lambda symbol: _Blank())
    snap = fm.snapshot("FAKE", force_refresh=True)
    assert snap.market_cap is None
    assert snap.analyst_rating is None
    assert snap.eps_growth_yoy_pct is None


def test_snapshot_survives_a_feed_error(monkeypatch, tmp_path):
    def _boom(symbol):
        raise RuntimeError("network down")

    monkeypatch.setattr(fm, "DATA_DIR", tmp_path)
    monkeypatch.setattr(fm.yf, "Ticker", _boom)
    snap = fm.snapshot("FAKE", force_refresh=True)
    assert snap.symbol == "FAKE"
    assert snap.market_cap is None
