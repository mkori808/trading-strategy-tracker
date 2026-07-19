"""Shared helpers for intraday strategies operating on session bars."""

from __future__ import annotations

import pandas as pd


def session_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """Bars from the same calendar session as the last bar."""
    last_date = bars.index[-1].date()
    return bars[bars.index.date == last_date]


def opening_range(bars: pd.DataFrame, minutes: int = 15) -> tuple[float, float] | None:
    """(high, low) of the first `minutes` of the current session, or None if
    the session doesn't have that much history yet."""
    sess = session_bars(bars)
    if sess.empty:
        return None
    window_end = sess.index[0] + pd.Timedelta(minutes=minutes)
    window = sess[sess.index <= window_end]
    if window.empty or sess.index[-1] < window_end:
        return None
    return window["High"].max(), window["Low"].min()


def previous_session(bars: pd.DataFrame) -> pd.DataFrame:
    """Bars from the session immediately before the last bar's session, or an
    empty frame if there's no prior day in `bars` yet."""
    dates = sorted(set(bars.index.date))
    if len(dates) < 2:
        return bars.iloc[0:0]
    last_date = bars.index[-1].date()
    prior = [d for d in dates if d < last_date]
    if not prior:
        return bars.iloc[0:0]
    return bars[bars.index.date == prior[-1]]


def is_bullish_candle(bar: pd.Series) -> bool:
    return bar["Close"] > bar["Open"]


def is_bearish_candle(bar: pd.Series) -> bool:
    return bar["Close"] < bar["Open"]
