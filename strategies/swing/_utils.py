"""Shared helpers for daily/weekly swing strategies."""

from __future__ import annotations

import pandas as pd


def swing_low(bars: pd.DataFrame, lookback: int = 20) -> float:
    return bars["Low"].tail(lookback).min()


def swing_high(bars: pd.DataFrame, lookback: int = 20) -> float:
    return bars["High"].tail(lookback).max()


def is_bullish_candle(bar: pd.Series) -> bool:
    return bar["Close"] > bar["Open"]
