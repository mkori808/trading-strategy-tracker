"""Internal Bar Strength (IBS) -- fade bars that close near their low,
expecting reversion, filtered to only trade in a long-term uptrend. The
signal is bar position (where today's close sits within today's own
high-low range), not momentum -- a genuinely different signal from the
RSI-based mean-reversion strategies already in this book (Oversold Bounce,
Connors Mean Reversion).

The tracker doesn't specify a stop rule beyond the signal-based exit
(IBS > 0.9) -- unlike target_price, stop_price is required by the Strategy
interface for every strategy, so this uses the same swing-low-based
technical stop the other signal-exit swing strategies here use (see
9/21 EMA Crossover, Pullback to 21 EMA).
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import ema
from strategies.base import Strategy
from strategies.params import param_field
from strategies.swing._utils import swing_low


def _ibs(bar: pd.Series) -> float | None:
    bar_range = bar["High"] - bar["Low"]
    if bar_range <= 0:
        return None
    return (bar["Close"] - bar["Low"]) / bar_range


@dataclass
class InternalBarStrength(Strategy):
    name = "Internal Bar Strength (IBS)"
    timeframe = "1d"
    direction = "long"

    trend_ema_period: int = param_field(
        252, label="Trend filter EMA period", minimum=50, maximum=300, step=10,
    )
    ibs_entry_threshold: float = param_field(
        0.2, label="IBS entry threshold", minimum=0.05, maximum=0.5, step=0.05,
        help="Enter when today's IBS falls below this (close near the day's low).",
    )
    ibs_exit_threshold: float = param_field(
        0.9, label="IBS exit threshold", minimum=0.5, maximum=0.99, step=0.05,
        help="Exit when IBS rises above this (close near the day's high).",
    )

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < self.trend_ema_period:
            return False
        ibs = _ibs(bars.iloc[-1])
        if ibs is None:
            return False
        trend = ema(bars["Close"], self.trend_ema_period)
        return ibs < self.ibs_entry_threshold and bool(bars["Close"].iloc[-1] > trend.iloc[-1])

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return swing_low(bars) * 0.99

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        ibs = _ibs(bars.iloc[-1])
        return ibs is not None and ibs > self.ibs_exit_threshold
