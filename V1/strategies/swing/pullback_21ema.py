"""Pullback to 21 EMA -- buy dips to a rising 21 EMA in an established
uptrend, with a reversal candle. Called the most reliable setup across
market conditions."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import atr, ema
from strategies.base import Strategy
from strategies.params import param_field
from strategies.swing._utils import is_bullish_candle, swing_low


@dataclass
class PullbackTo21Ema(Strategy):
    name = "Pullback to 21 EMA"
    timeframe = "1d"
    direction = "long"

    pullback_atr_tolerance: float = param_field(
        0.5, label="Pullback tolerance (x ATR)", minimum=0.1, maximum=2.0, step=0.1,
        help="How close the low must come to the 21 EMA (in ATRs) to count as a pullback.",
    )
    trend_lookback: int = param_field(
        10, label="Trend lookback (bars)", minimum=3, maximum=30, step=1,
        help="Bars back the 21 EMA must have climbed over to confirm an uptrend.",
    )
    stop_swing_lookback: int = param_field(
        20, label="Stop swing-low lookback (bars)", minimum=5, maximum=60, step=5,
        help="Bars back to search for the swing low used in the stop calculation.",
    )
    stop_buffer_pct: float = param_field(
        0.01, label="Stop buffer below level (fraction)", minimum=0.0, maximum=0.05, step=0.005,
        help="Extra cushion below the EMA/swing-low stop level.",
    )

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < 30:
            return False
        ema21 = ema(bars["Close"], 21)
        uptrend = (
            ema21.iloc[-1] > ema21.iloc[-self.trend_lookback]
            and bars["Close"].iloc[-1] > ema21.iloc[-1] * 0.98
        )
        if not uptrend:
            return False
        last = bars.iloc[-1]
        near_ema = abs(last["Low"] - ema21.iloc[-1]) <= self.pullback_atr_tolerance * atr(bars).iloc[-1]
        return near_ema and is_bullish_candle(last) and last["Close"] >= ema21.iloc[-1]

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        ema21 = ema(bars["Close"], 21)
        level = min(ema21.iloc[-1], swing_low(bars, self.stop_swing_lookback))
        return level * (1 - self.stop_buffer_pct)

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        # ride the trend until price closes back below the 21 EMA it's pulling back to
        if len(bars) < 21:
            return False
        ema21 = ema(bars["Close"], 21)
        return bars["Close"].iloc[-1] < ema21.iloc[-1]
