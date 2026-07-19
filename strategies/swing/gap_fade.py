"""Gap Fade (daily) -- fade a large overnight gap, expecting partial
reversion toward the prior close.

Same stop-fill honesty concern News Fade already surfaced for this
codebase (see LESSONS.md): gaps can run straight through a stop rather than
reverting, so the stop sits beyond the gap extreme -- the gap bar's own
high (for a faded-up gap) or low (for a faded-down gap) -- not some
tighter, more "efficient"-looking level.

Engine-architecture note, also shared with News Fade: the tracker says
"enter counter-gap at the open," but this engine always fills at the bar
*after* the signal bar's close (see engine/backtest.py) -- there is no
same-bar-open entry mechanism for any strategy here. This detects the gap
using the signal bar's own Open vs. the prior Close, same as the tracker's
entry rule, but the actual fill happens the way every other strategy's
does: at the next bar's open, priced off the signal bar's close.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import atr
from strategies.base import Strategy
from strategies.params import param_field


@dataclass
class GapFade(Strategy):
    name = "Gap Fade (daily)"
    timeframe = "1d"
    direction = "both"

    gap_atr_multiple: float = param_field(
        1.5, label="Gap size (x ATR)", minimum=0.5, maximum=4.0, step=0.25,
        help="The overnight gap must be at least this many ATRs to qualify.",
    )
    partial_fill_fraction: float = param_field(
        0.5, label="Target reversion fraction", minimum=0.1, maximum=1.0, step=0.05,
        help="Target: this fraction of the gap reverted toward the prior close.",
    )

    def _gap(self, bars: pd.DataFrame) -> tuple[str, float] | None:
        if len(bars) < 21:
            return None
        prev_close = bars["Close"].iloc[-2]
        today_open = bars["Open"].iloc[-1]
        bar_atr = atr(bars).iloc[-2]  # ATR excluding the gap bar itself
        if bar_atr <= 0 or prev_close <= 0:
            return None
        gap = today_open - prev_close
        if abs(gap) < self.gap_atr_multiple * bar_atr:
            return None
        return ("short", today_open) if gap > 0 else ("long", today_open)

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        return self._gap(bars) is not None

    def entry_direction(self, bars: pd.DataFrame) -> str:
        direction, _ = self._gap(bars)
        return direction

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        direction, _ = self._gap(bars)
        last = bars.iloc[-1]
        return last["High"] if direction == "short" else last["Low"]

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        prev_close = bars["Close"].iloc[-2]
        today_open = bars["Open"].iloc[-1]
        return today_open + self.partial_fill_fraction * (prev_close - today_open)
