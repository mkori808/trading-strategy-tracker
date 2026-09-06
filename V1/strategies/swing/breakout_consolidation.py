"""Breakout from Consolidation -- buy a close above the consolidation
range high on 1.5-2x average volume. No-volume breaks are usually fake."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.base import Strategy
from strategies.params import param_field


@dataclass
class BreakoutFromConsolidation(Strategy):
    name = "Breakout from Consolidation"
    timeframe = "1d"
    direction = "long"

    consolidation_lookback: int = param_field(
        20, label="Consolidation lookback (bars)", minimum=5, maximum=60, step=5,
    )
    volume_multiple: float = param_field(
        1.5, label="Breakout volume multiple", minimum=1.0, maximum=5.0, step=0.1,
        help="Breakout bar's volume must be at least this many times the trailing average.",
    )
    stop_range_fraction: float = param_field(
        0.5, label="Stop position within range (0=low, 1=high)", minimum=0.0, maximum=1.0, step=0.1,
        help="Where within the consolidation range the stop sits.",
    )
    target_measured_move_multiple: float = param_field(
        1.0, label="Target projection (x range height)", minimum=0.5, maximum=2.5, step=0.25,
        help="Target = breakout level + this multiple of the consolidation range height.",
    )

    def _range(self, bars: pd.DataFrame) -> tuple[float, float] | None:
        window = bars.iloc[:-1].tail(self.consolidation_lookback)
        if len(window) < 10:
            return None
        return window["High"].max(), window["Low"].min()

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        rng = self._range(bars)
        if rng is None:
            return False
        high, _ = rng
        last = bars.iloc[-1]
        avg_vol = bars["Volume"].iloc[:-1].tail(self.consolidation_lookback).mean()
        volume_ok = avg_vol > 0 and last["Volume"] >= self.volume_multiple * avg_vol
        return volume_ok and last["Close"] > high

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        high, low = self._range(bars)
        return low + self.stop_range_fraction * (high - low)  # back inside the range

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        high, low = self._range(bars)
        return high + self.target_measured_move_multiple * (high - low)  # measured move
