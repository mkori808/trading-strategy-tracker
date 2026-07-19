"""9/21 EMA Crossover -- mechanical trend-following signal. Objective but
acknowledged as a lagging signal; exits on the crossunder rather than a
fixed target."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import ema
from strategies.base import Strategy
from strategies.params import param_field
from strategies.swing._utils import swing_low


@dataclass
class Ema9_21Crossover(Strategy):
    name = "9/21 EMA Crossover"
    timeframe = "1d"
    direction = "long"

    slope_lookback: int = param_field(
        5, label="Slope confirmation lookback (bars)", minimum=2, maximum=20, step=1,
        help="Bars back both EMAs must have climbed over to confirm the crossover isn't chop.",
    )

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < 25:
            return False
        ema9, ema21 = ema(bars["Close"], 9), ema(bars["Close"], 21)
        crossed_up = ema9.iloc[-2] <= ema21.iloc[-2] and ema9.iloc[-1] > ema21.iloc[-1]
        sloping_up = (
            ema9.iloc[-1] > ema9.iloc[-self.slope_lookback]
            and ema21.iloc[-1] > ema21.iloc[-self.slope_lookback]
        )
        return crossed_up and sloping_up

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        ema21 = ema(bars["Close"], 21)
        return min(ema21.iloc[-1], swing_low(bars)) * 0.99

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < 25:
            return False
        ema9, ema21 = ema(bars["Close"], 9), ema(bars["Close"], 21)
        return ema9.iloc[-2] >= ema21.iloc[-2] and ema9.iloc[-1] < ema21.iloc[-1]
