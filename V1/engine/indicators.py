"""Hand-rolled technical indicators on pandas Series/DataFrames.

Kept dependency-free (no pandas_ta / ta-lib) since only a handful of
indicators are needed across all 14 strategies.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.fillna(50)


def macd(
    series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP -- resets each calendar day. Assumes a tz-aware,
    America/New_York-localized DatetimeIndex."""
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = typical_price * df["Volume"]
    day = df.index.date
    cum_pv = pv.groupby(day).cumsum()
    cum_vol = df["Volume"].groupby(day).cumsum()
    return cum_pv / cum_vol.replace(0, np.nan)


def relative_strength(series: pd.Series, benchmark: pd.Series) -> pd.Series:
    """Ratio of `series` to `benchmark`, rebased to 1.0 at the first bar."""
    ratio = series / benchmark
    return ratio / ratio.iloc[0]


def floor_pivots(high: float, low: float, close: float) -> dict[str, float]:
    """Classic floor-trader pivot levels from a prior session's H/L/C.
    P is the pivot; S1/R1 the first support/resistance (mean-reversion fade
    targets sit between a level and P); S2/R2 the outer band used as stops."""
    p = (high + low + close) / 3
    rng = high - low
    return {
        "P": p,
        "R1": 2 * p - low,
        "S1": 2 * p - high,
        "R2": p + rng,
        "S2": p - rng,
    }
