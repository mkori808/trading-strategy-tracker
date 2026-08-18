from datetime import date

import pandas as pd
import pytest

from engine import data as data_module


def _fake_bars(start="2024-01-02", periods=5):
    idx = pd.bdate_range(start=start, periods=periods, tz="America/New_York")
    return pd.DataFrame(
        {
            "Open": [100.0] * periods,
            "High": [101.0] * periods,
            "Low": [99.0] * periods,
            "Close": [100.5] * periods,
            "Volume": [1e6] * periods,
        },
        index=idx,
    )


def test_get_bars_fetches_once_and_reuses_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(data_module, "DATA_DIR", tmp_path)
    calls = []

    def fake_fetch(symbol, interval, start, end):
        calls.append((symbol, interval, start, end))
        return _fake_bars()

    monkeypatch.setattr(data_module, "_fetch", fake_fetch)

    bars1 = data_module.get_bars("TEST", "1d", date(2024, 1, 2), date(2024, 1, 8))
    assert len(calls) == 1
    assert not bars1.empty

    bars2 = data_module.get_bars("TEST", "1d", date(2024, 1, 2), date(2024, 1, 8))
    assert len(calls) == 1  # served from cache, no second fetch
    # parquet round-tripping drops the DatetimeIndex freq metadata; values are unaffected
    pd.testing.assert_frame_equal(bars1, bars2, check_freq=False)


def test_get_bars_refetches_when_requested_range_extends_beyond_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(data_module, "DATA_DIR", tmp_path)
    calls = []

    def fake_fetch(symbol, interval, start, end):
        calls.append((start, end))
        return _fake_bars(periods=5 if len(calls) == 1 else 25)

    monkeypatch.setattr(data_module, "_fetch", fake_fetch)

    data_module.get_bars("TEST", "1d", date(2024, 1, 2), date(2024, 1, 8))
    data_module.get_bars("TEST", "1d", date(2024, 1, 2), date(2024, 2, 8))
    assert len(calls) == 2


def test_intraday_cache_uses_provider_limited_start_without_refetch(tmp_path, monkeypatch):
    monkeypatch.setattr(data_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data_module, "market_data_client", lambda: (None, "not configured"))
    first_available = date.today() - data_module.timedelta(days=data_module._YF_INTRADAY_CAP_DAYS)
    cached = _fake_bars(start=first_available.isoformat(), periods=10)
    cached.to_parquet(tmp_path / "TEST_5m.parquet")

    def unexpected_fetch(*_args):
        raise AssertionError("provider-limited cache should have been reused")

    monkeypatch.setattr(data_module, "_fetch", unexpected_fetch)
    result = data_module.get_bars(
        "TEST", "5m", date.today() - data_module.timedelta(days=365), cached.index.max().date(),
    )

    assert len(result) == len(cached)


def test_get_bars_merges_narrow_refresh_without_erasing_older_history(tmp_path, monkeypatch):
    monkeypatch.setattr(data_module, "DATA_DIR", tmp_path)
    cached = _fake_bars(start="2020-01-02", periods=10)
    cached.to_parquet(tmp_path / "TEST_1d.parquet")
    fresh = _fake_bars(start="2024-02-01", periods=5)
    monkeypatch.setattr(data_module, "_fetch", lambda *_args: fresh)

    result = data_module.get_bars(
        "TEST", "1d", date(2020, 1, 2), date(2024, 2, 8),
    )
    stored = pd.read_parquet(tmp_path / "TEST_1d.parquet")

    assert stored.index.min() == cached.index.min()
    assert stored.index.max() == fresh.index.max()
    assert len(stored) == len(cached) + len(fresh)
    pd.testing.assert_frame_equal(result, stored, check_freq=False, check_index_type=False)


def test_yfinance_cache_is_relocated_to_writable_project_storage(tmp_path, monkeypatch):
    configured: list[str] = []
    monkeypatch.setattr(data_module.yf, "set_tz_cache_location", configured.append)

    target = data_module._configure_yfinance_cache(tmp_path / "yf-state")

    assert target == tmp_path / "yf-state"
    assert target.is_dir()
    assert configured == [str(target)]


def test_daily_provider_dates_do_not_shift_to_previous_new_york_day(monkeypatch):
    raw = _fake_bars(start="2026-08-12", periods=1)
    raw.index = pd.DatetimeIndex(["2026-08-12"])
    monkeypatch.setattr(data_module.yf, "download", lambda *_args, **_kwargs: raw)

    result = data_module._fetch_yfinance(
        "BTC-USD", "1d", date(2026, 8, 12), date(2026, 8, 13),
    )

    assert result.index[0].date() == date(2026, 8, 12)
    assert str(result.index.tz) == "America/New_York"


def test_existing_shifted_daily_cache_is_normalized_on_read(tmp_path, monkeypatch):
    monkeypatch.setattr(data_module, "DATA_DIR", tmp_path)
    shifted = _fake_bars(start="2026-08-11 20:00", periods=1)
    shifted.index = pd.DatetimeIndex(["2026-08-11 20:00"], tz="America/New_York")
    shifted.to_parquet(tmp_path / "BTC-USD_1d.parquet")
    real_datetime = data_module.datetime

    class NextDay(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(2026, 8, 13, 12, tzinfo=tz)

    monkeypatch.setattr(data_module, "datetime", NextDay)
    monkeypatch.setattr(data_module, "market_data_client", lambda: (None, "not configured"))
    monkeypatch.setattr(data_module, "_fetch", lambda *_args: pytest.fail("normalized cache should cover prior crypto day"))

    result = data_module.get_bars(
        "BTC-USD", "1d", date(2026, 8, 12), date(2026, 8, 13),
    )

    assert len(result) == 1
    assert result.index[0].date() == date(2026, 8, 12)


def test_get_bars_recovers_from_a_corrupt_cache_file(tmp_path, monkeypatch):
    monkeypatch.setattr(data_module, "DATA_DIR", tmp_path)
    cache = tmp_path / "TEST_1d.parquet"
    cache.write_bytes(b"this is not parquet")
    calls = []

    def fake_fetch(symbol, interval, start, end):
        calls.append((symbol, interval, start, end))
        return _fake_bars()

    monkeypatch.setattr(data_module, "_fetch", fake_fetch)

    bars = data_module.get_bars("TEST", "1d", date(2024, 1, 2), date(2024, 1, 8))

    assert len(calls) == 1
    pd.testing.assert_frame_equal(bars, _fake_bars(), check_freq=False, check_index_type=False)
    # The corrupt cache was replaced with a readable parquet file.
    pd.testing.assert_frame_equal(
        pd.read_parquet(cache), _fake_bars(), check_freq=False, check_index_type=False,
    )


def test_get_bars_does_not_replace_cache_when_refresh_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(data_module, "DATA_DIR", tmp_path)
    cached = _fake_bars(periods=10)
    cached.to_parquet(tmp_path / "TEST_1d.parquet")
    monkeypatch.setattr(
        data_module, "_fetch", lambda *_args: pd.DataFrame(columns=cached.columns),
    )

    result = data_module.get_bars("TEST", "1d", date(2024, 1, 2), date(2024, 2, 8))

    # The result only includes the requested date slice, but the underlying
    # historical cache remains intact for a future successful refresh.
    assert not result.empty
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "TEST_1d.parquet"), cached, check_freq=False)


def test_current_daily_request_accepts_prior_session_before_close(tmp_path, monkeypatch):
    monkeypatch.setattr(data_module, "DATA_DIR", tmp_path)
    today = date.today()
    real_datetime = data_module.datetime

    class Midday(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(today.year, today.month, today.day, 12, tzinfo=tz)

    monkeypatch.setattr(data_module, "datetime", Midday)
    prior = (pd.Timestamp(today) - pd.offsets.BDay(1)).date()
    cached = _fake_bars(start=str(prior), periods=1)
    cached.to_parquet(tmp_path / "TEST_1d.parquet")
    monkeypatch.setattr(
        data_module, "_fetch",
        lambda *_args: pytest.fail("prior completed daily session should satisfy a pre-close request"),
    )

    result = data_module.get_bars("TEST", "1d", prior, today)
    assert len(result) == 1


def test_current_intraday_request_accepts_prior_session_before_open(tmp_path, monkeypatch):
    monkeypatch.setattr(data_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(data_module, "market_data_client", lambda: (None, "not configured"))
    today = date.today()
    real_datetime = data_module.datetime

    class BeforeOpen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return real_datetime(today.year, today.month, today.day, 8, tzinfo=tz)

    monkeypatch.setattr(data_module, "datetime", BeforeOpen)
    prior = (pd.Timestamp(today) - pd.offsets.BDay(1)).date()
    first_available = today - data_module.timedelta(days=data_module._YF_INTRADAY_CAP_DAYS)
    cached = _fake_bars(start=first_available.isoformat(), periods=50)
    # Ensure the cached series has a prior-session row even when holidays or
    # the 58-day boundary make the generated business-day count differ.
    cached = pd.concat([cached, _fake_bars(start=prior.isoformat(), periods=1)]).sort_index()
    cached = cached[~cached.index.duplicated(keep="last")]
    cached.to_parquet(tmp_path / "TEST_5m.parquet")
    monkeypatch.setattr(
        data_module, "_fetch",
        lambda *_args: pytest.fail("prior intraday session should satisfy an overnight request"),
    )

    result = data_module.get_bars("TEST", "5m", first_available, today)
    assert not result.empty
