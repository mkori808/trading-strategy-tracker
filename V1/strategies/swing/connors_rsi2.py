"""Connors Mean Reversion (RSI2) -- buy short-term weakness in a long-term
uptrend. Classic Connors methodology: price above the 200-day SMA (trend
filter) with a 2-period RSI oversold reading (short-term panic), on the
thesis that strong uptrends tend to absorb short-term dips rather than
break down from them.

Simplification, disclosed: the tracker's stop rule is "time stop (exit
after N days) OR close > 5-day MA; optional 3x ATR hard stop." The Strategy
interface has no notion of "N days since entry" -- exit_signal only sees
the current bars, not when the position was opened -- so this implements
the two conditions that ARE expressible: exit on close reclaiming the
5-day SMA (the primary, signal-based exit), backed by a 3x ATR hard stop
for downside protection.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import atr, rsi, sma
from strategies.base import Strategy
from strategies.params import param_field


@dataclass
class ConnorsMeanReversion(Strategy):
    name = "Connors Mean Reversion (RSI2)"
    timeframe = "1d"
    direction = "long"

    trend_sma_period: int = param_field(
        200, label="Trend filter SMA period", minimum=50, maximum=250, step=10,
    )
    rsi_period: int = param_field(
        2, label="RSI period", minimum=2, maximum=10, step=1,
    )
    rsi_threshold: int = param_field(
        10, label="RSI oversold threshold", minimum=1, maximum=40, step=1,
    )
    exit_sma_period: int = param_field(
        5, label="Exit SMA period", minimum=2, maximum=20, step=1,
    )
    stop_atr_multiple: float = param_field(
        3.0, label="Stop distance (x ATR)", minimum=1.0, maximum=6.0, step=0.5,
    )
    atr_period: int = param_field(
        14, label="ATR period (days)", minimum=5, maximum=30, step=1,
    )

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < self.trend_sma_period:
            return False
        trend = sma(bars["Close"], self.trend_sma_period)
        r = rsi(bars["Close"], self.rsi_period)
        return bool(bars["Close"].iloc[-1] > trend.iloc[-1] and r.iloc[-1] < self.rsi_threshold)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return entry_price - self.stop_atr_multiple * atr(bars, self.atr_period).iloc[-1]

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < self.exit_sma_period:
            return False
        exit_sma = sma(bars["Close"], self.exit_sma_period)
        return bool(bars["Close"].iloc[-1] > exit_sma.iloc[-1])
