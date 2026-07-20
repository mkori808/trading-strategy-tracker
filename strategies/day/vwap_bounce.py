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
    atr_period: int = param_field(
        14, label="ATR period (bars)", minimum=5, maximum=30, step=1,
        help="Lookback window for the ATR used to size the VWAP band.",
    )
    stop_buffer_pct: float = param_field(
        0.001, label="Stop buffer beyond extreme (fraction)", minimum=0.0001, maximum=0.01, step=0.0001,
        help="Extra cushion beyond the band/candle extreme used for the stop.",
    )

    def _band(self, bars: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        vw = vwap(bars)
        width = atr(bars, self.atr_period) * self.band_atr_multiple
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
            return min(lower.iloc[-1], last["Low"]) * (1 - self.stop_buffer_pct)
        return max(upper.iloc[-1], last["High"]) * (1 + self.stop_buffer_pct)

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        vw = vwap(bars)
        return float(vw.iloc[-1])  # reversion back to the mean
