from datetime import date

import pandas as pd

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
