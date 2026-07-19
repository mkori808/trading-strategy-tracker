"""Synthetic OHLCV fixtures for isolated strategy/engine unit tests."""

from __future__ import annotations

import pandas as pd
import pytest

NY = "America/New_York"


def _ohlcv(closes, index, volumes=None, highs=None, lows=None, opens=None):
    closes = pd.Series(closes, index=index, dtype=float)
    if opens is None:
        opens = closes.shift(1).fillna(closes.iloc[0])
    else:
        opens = pd.Series(opens, index=index, dtype=float)
    if highs is None:
        highs = pd.concat([opens, closes], axis=1).max(axis=1)
    else:
        highs = pd.Series(highs, index=index, dtype=float)
    if lows is None:
        lows = pd.concat([opens, closes], axis=1).min(axis=1)
    else:
        lows = pd.Series(lows, index=index, dtype=float)
    volumes = (
        pd.Series(volumes, index=index, dtype=float)
        if volumes is not None
        else pd.Series(1_000_000, index=index, dtype=float)
    )
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes})


@pytest.fixture
def daily_bars_factory():
    def _make(closes, volumes=None, highs=None, lows=None, opens=None, start="2024-01-02"):
        index = pd.bdate_range(start=start, periods=len(closes), tz=NY)
        return _ohlcv(closes, index, volumes, highs, lows, opens)

    return _make


@pytest.fixture
def intraday_bars_factory():
    def _make(
        closes, volumes=None, highs=None, lows=None, opens=None,
        start="2024-01-02 09:30", freq="5min",
    ):
        index = pd.date_range(start=pd.Timestamp(start, tz=NY), periods=len(closes), freq=freq)
        return _ohlcv(closes, index, volumes, highs, lows, opens)

    return _make
