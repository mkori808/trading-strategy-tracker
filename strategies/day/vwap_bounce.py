"""VWAP Bounce / Reversion -- fade or bounce off a VWAP band with a
reversal candle. Stop beyond the band extreme. Best on range-bound days."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import atr, vwap
from strategies.base import Strategy
from strategies.day._utils import is_bearish_candle, is_bullish_candle
from strategies.params import param_field


@dataclass
class VwapBounce(Strategy):
    name = "VWAP Bounce / Reversion"
    timeframe = "5min"
    direction = "both"

    band_atr_multiple: float = param_field(
        1.0, label="VWAP band width (x ATR)", minimum=0.25, maximum=3.0, step=0.25,
        help="How far price must stray from VWAP (in ATRs) to count as a touch.",
    )

    def _band(self, bars: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        vw = vwap(bars)
        width = atr(bars) * self.band_atr_multiple
        return vw + width, vw - width

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < 15:
            return False
        upper, lower = self._band(bars)
        last = bars.iloc[-1]
        touched_lower = last["Low"] <= lower.iloc[-1]
        touched_upper = last["High"] >= upper.iloc[-1]
        if touched_lower and is_bullish_candle(last):
            return True
        if touched_upper and is_bearish_candle(last):
            return True
        return False

    def entry_direction(self, bars: pd.DataFrame) -> str:
        upper, lower = self._band(bars)
        last = bars.iloc[-1]
        return "long" if last["Low"] <= lower.iloc[-1] else "short"

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        upper, lower = self._band(bars)
        last = bars.iloc[-1]
        if last["Low"] <= lower.iloc[-1]:
            return min(lower.iloc[-1], last["Low"]) * 0.999
        return max(upper.iloc[-1], last["High"]) * 1.001

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        vw = vwap(bars)
        return float(vw.iloc[-1])  # reversion back to the mean
