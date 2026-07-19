"""Range Trading -- buy support / sell resistance inside a defined
intraday range, with a rejection candle at the boundary. Best in
low-volatility, choppy sessions."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from strategies.base import Strategy
from strategies.day._utils import is_bearish_candle, is_bullish_candle, session_bars
from strategies.params import param_field


@dataclass
class RangeTrading(Strategy):
    name = "Range Trading"
    timeframe = "15min"
    direction = "both"

    range_lookback_bars: int = param_field(
        20, label="Range lookback (bars)", minimum=5, maximum=60, step=5,
    )
    boundary_tolerance: float = param_field(
        0.001, label="Boundary tolerance (fraction)", minimum=0.0001, maximum=0.01, step=0.0001,
        help="How close to the range boundary counts as a touch.",
    )

    def _range(self, bars: pd.DataFrame) -> tuple[float, float] | None:
        sess = session_bars(bars)
        window = sess.iloc[:-1].tail(self.range_lookback_bars)
        if len(window) < 6:
            return None
        return window["High"].max(), window["Low"].min()

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        rng = self._range(bars)
        if rng is None:
            return False
        high, low = rng
        last = bars.iloc[-1]
        at_low = last["Low"] <= low * (1 + self.boundary_tolerance)
        at_high = last["High"] >= high * (1 - self.boundary_tolerance)
        if at_low and is_bullish_candle(last) and last["Close"] > low:
            return True
        if at_high and is_bearish_candle(last) and last["Close"] < high:
            return True
        return False

    def entry_direction(self, bars: pd.DataFrame) -> str:
        high, low = self._range(bars)
        last = bars.iloc[-1]
        return "long" if last["Low"] <= low * (1 + self.boundary_tolerance) else "short"

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        high, low = self._range(bars)
        last = bars.iloc[-1]
        if last["Low"] <= low * (1 + self.boundary_tolerance):
            return low * 0.998
        return high * 1.002

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        high, low = self._range(bars)
        last = bars.iloc[-1]
        # target the opposite boundary of the range from the entry side
        return high if last["Low"] <= low * (1 + self.boundary_tolerance) else low
