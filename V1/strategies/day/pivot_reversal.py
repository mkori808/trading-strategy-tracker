"""Pivot-Level ETF Reversal -- fade the first touch of a floor-trader pivot
support/resistance band and revert toward the daily pivot. Best on
range-bound/mean-reverting days (per the tracker's market condition), which
is why it targets the pivot P rather than chasing a breakout through it.

Levels are computed from the *prior completed session's* H/L/C (never the
current session), so there's no look-ahead: the whole day trades against
levels that were fully known at yesterday's close. Trades both sides -- long
the S1 reclaim, short the R1 rejection.
"""

from __future__ import annotations

import pandas as pd

from engine.indicators import floor_pivots
from strategies.base import Strategy
from strategies.day._utils import previous_session, session_bars


class PivotLevelEtfReversal(Strategy):
    name = "Pivot-Level ETF Reversal"
    timeframe = "5min"
    direction = "both"

    def _signal(self, bars: pd.DataFrame) -> tuple[str, dict[str, float]] | None:
        prev = previous_session(bars)
        if prev.empty:
            return None
        pivots = floor_pivots(prev["High"].max(), prev["Low"].min(), prev["Close"].iloc[-1])
        if pivots["R2"] - pivots["S2"] <= 0:  # zero-range prior day -> no levels
            return None

        sess = session_bars(bars)
        if len(sess) < 2:  # need a prior in-session bar to confirm a reclaim
            return None
        last = bars.iloc[-1]
        prior_close = bars["Close"].iloc[-2]

        # Long: session dipped to S1, prior bar sat at/below it, this bar
        # reclaims it -- and P is still overhead, so there's room to the target.
        touched_s1 = sess["Low"].min() <= pivots["S1"]
        if touched_s1 and prior_close <= pivots["S1"] < last["Close"] < pivots["P"]:
            return "long", pivots

        # Short: mirror at R1.
        touched_r1 = sess["High"].max() >= pivots["R1"]
        if touched_r1 and prior_close >= pivots["R1"] > last["Close"] > pivots["P"]:
            return "short", pivots

        return None

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        return self._signal(bars) is not None

    def entry_direction(self, bars: pd.DataFrame) -> str:
        sig = self._signal(bars)
        return sig[0] if sig else "long"

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        pivots = floor_pivots(
            *self._prev_hlc(bars)
        )
        # Long fades support (entry below P) -> stop at the outer S2 band;
        # short fades resistance (entry above P) -> stop at R2.
        return pivots["S2"] if entry_price < pivots["P"] else pivots["R2"]

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        pivots = floor_pivots(*self._prev_hlc(bars))
        return pivots["P"]

    @staticmethod
    def _prev_hlc(bars: pd.DataFrame) -> tuple[float, float, float]:
        prev = previous_session(bars)
        return prev["High"].max(), prev["Low"].min(), prev["Close"].iloc[-1]
