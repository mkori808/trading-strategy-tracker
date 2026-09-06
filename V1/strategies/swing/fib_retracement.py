"""Fibonacci Retracement Entry -- buy a 50%-61.8% pullback after a strong
up move, with a reversal signal. Lower win rate but larger avg winners
due to a tight stop vs. target."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.base import Strategy
from strategies.params import param_field
from strategies.swing._utils import is_bullish_candle


@dataclass
class FibonacciRetracementEntry(Strategy):
    name = "Fibonacci Retracement Entry"
    timeframe = "1d"
    direction = "long"

    lookback: int = param_field(
        40, label="Swing lookback (bars)", minimum=15, maximum=90, step=5,
    )
    min_move_pct: float = param_field(
        0.08, label="Min swing size (fraction)", minimum=0.02, maximum=0.30, step=0.01,
        help="0.08 = 8%. The prior up-move must be at least this large to qualify.",
    )
    stop_buffer_pct: float = param_field(
        0.01, label="Stop buffer below zone (fraction)", minimum=0.0, maximum=0.05, step=0.005,
        help="Extra cushion below the deep (61.8%) fib bound used for the stop.",
    )

    def _swing(self, bars: pd.DataFrame) -> tuple[float, float] | None:
        window = bars.iloc[:-1].tail(self.lookback)
        if len(window) < 15:
            return None
        high_idx = window["High"].idxmax()
        pre_high = window.loc[:high_idx]
        if len(pre_high) < 5:
            return None
        swing_low_price = pre_high["Low"].min()
        swing_high_price = window.loc[high_idx, "High"]
        if swing_high_price <= swing_low_price:
            return None
        move_pct = (swing_high_price - swing_low_price) / swing_low_price
        if move_pct < self.min_move_pct:
            return None
        return swing_low_price, swing_high_price

    def _fib_zone(self, bars: pd.DataFrame) -> tuple[float, float] | None:
        swing = self._swing(bars)
        if swing is None:
            return None
        low, high = swing
        diff = high - low
        return high - 0.618 * diff, high - 0.5 * diff  # (deep, shallow) bounds

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        zone = self._fib_zone(bars)
        if zone is None:
            return False
        deep, shallow = zone
        last = bars.iloc[-1]
        in_zone = deep <= last["Low"] <= shallow
        return in_zone and is_bullish_candle(last)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        deep, _ = self._fib_zone(bars)
        return deep * (1 - self.stop_buffer_pct)

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        _, swing_high_price = self._swing(bars)
        return swing_high_price  # retest of the prior swing high
