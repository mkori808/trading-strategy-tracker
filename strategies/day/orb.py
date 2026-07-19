"""Opening Range Breakout -- trade a break of the opening range on volume,
stop on the opposite side of the range. Best on trending days (per
strategy_tracker.xlsx's "Best Market Condition").

The trend-day filter below is what actually enforces that market-condition
qualifier: a breakout bar whose close has already fallen back across session
VWAP to the wrong side is exactly the failed-breakout/chop signature the
tracker's condition is meant to exclude, so it's rejected here rather than
left to the stop to clean up.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import vwap
from strategies.base import Strategy
from strategies.day._utils import opening_range, session_bars
from strategies.params import param_field


@dataclass
class OpeningRangeBreakout(Strategy):
    name = "Opening Range Breakout (ORB)"
    timeframe = "5min"
    direction = "both"

    range_minutes: int = param_field(
        15, label="Opening range length (min)", minimum=5, maximum=60, step=5,
        help="How much of the session open defines the range to break out of.",
    )
    volume_multiple: float = param_field(
        1.5, label="Breakout volume multiple", minimum=1.0, maximum=5.0, step=0.1,
        help="Breakout bar's volume must be at least this many times the session average.",
    )
    target_range_multiple: float = param_field(
        2.0, label="Target (x opening range)", minimum=0.5, maximum=5.0, step=0.5,
        help="Measured-move target: N x the opening range, projected from the breakout.",
    )

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        rng = opening_range(bars, self.range_minutes)
        if rng is None:
            return False
        high, low = rng
        sess = session_bars(bars)
        last = bars.iloc[-1]
        if last.name <= sess.index[0] + pd.Timedelta(minutes=self.range_minutes):
            return False
        avg_vol = sess["Volume"].mean()
        if avg_vol == 0 or last["Volume"] < self.volume_multiple * avg_vol:
            return False
        if last["Close"] > high:
            return last["Close"] > vwap(sess).iloc[-1]
        if last["Close"] < low:
            return last["Close"] < vwap(sess).iloc[-1]
        return False

    def entry_direction(self, bars: pd.DataFrame) -> str:
        rng = opening_range(bars, self.range_minutes)
        high, low = rng
        last_close = bars.iloc[-1]["Close"]
        return "long" if last_close > high else "short"

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        high, low = opening_range(bars, self.range_minutes)
        return low if entry_price > high else high

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        high, low = opening_range(bars, self.range_minutes)
        range_size = high - low
        if entry_price > high:
            return entry_price + self.target_range_multiple * range_size
        return entry_price - self.target_range_multiple * range_size
