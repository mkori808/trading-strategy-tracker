"""Mean Reversion Scalp -- fade RSI extremes back toward VWAP. Highest
win-rate / smallest-target style setup; stop beyond the extreme candle."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import rsi, vwap
from strategies.base import Strategy
from strategies.day._utils import is_bearish_candle, is_bullish_candle
from strategies.params import param_field


@dataclass
class MeanReversionScalp(Strategy):
    name = "Mean Reversion Scalp"
    timeframe = "1min"
    direction = "both"

    rsi_period: int = param_field(
        3, label="RSI period", minimum=2, maximum=14, step=1,
    )
    rsi_low: int = param_field(
        10, label="RSI oversold threshold", minimum=1, maximum=40, step=1,
    )
    rsi_high: int = param_field(
        90, label="RSI overbought threshold", minimum=60, maximum=99, step=1,
    )

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < self.rsi_period + 5:
            return False
        r = rsi(bars["Close"], self.rsi_period)
        last = bars.iloc[-1]
        if r.iloc[-1] < self.rsi_low and is_bullish_candle(last):
            return True
        if r.iloc[-1] > self.rsi_high and is_bearish_candle(last):
            return True
        return False

    def entry_direction(self, bars: pd.DataFrame) -> str:
        r = rsi(bars["Close"], self.rsi_period)
        return "long" if r.iloc[-1] < self.rsi_low else "short"

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        last = bars.iloc[-1]
        r = rsi(bars["Close"], self.rsi_period)
        if r.iloc[-1] < self.rsi_low:
            return last["Low"] * 0.999
        return last["High"] * 1.001

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        return float(vwap(bars).iloc[-1])  # reversion back to VWAP
