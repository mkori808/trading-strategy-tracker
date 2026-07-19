"""Turnaround Tuesday -- a seasonal mean-reversion effect: short-term
weakness going into a Monday close tends to bounce early in the week. Enter
on a Monday close that is short-term oversold within a longer uptrend, exit
on the first sign of the bounce.

Two disclosed simplifications:
- "Tuesday" is expressed as *entering on Monday's close* (weekday 0) to catch
  the next session; holiday Mondays simply produce no signal that week rather
  than shifting the rule to Tuesday.
- The tracker's "1-4 day hold" is a time stop, and the Strategy interface
  can't see how long a position has been open (exit_signal only sees current
  bars -- same limit noted in connors_rsi2.py). So the hold is expressed as a
  signal exit: close on the first up-close (the bounce), backed by an ATR stop.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import atr, rsi, sma
from strategies.base import Strategy
from strategies.params import param_field


@dataclass
class TurnaroundTuesday(Strategy):
    name = "Turnaround Tuesday"
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
    stop_atr_multiple: float = param_field(
        2.0, label="Stop distance (x ATR)", minimum=0.5, maximum=5.0, step=0.5,
    )

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < self.trend_sma_period:
            return False
        last = bars.iloc[-1]
        if last.name.weekday() != 0:  # Monday only
            return False
        trend = sma(bars["Close"], self.trend_sma_period)
        r = rsi(bars["Close"], self.rsi_period)
        return bool(last["Close"] > trend.iloc[-1] and r.iloc[-1] < self.rsi_threshold)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return entry_price - self.stop_atr_multiple * atr(bars).iloc[-1]

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < 2:
            return False
        return bool(bars["Close"].iloc[-1] > bars["Close"].iloc[-2])
