"""News Fade -- fade an extreme, high-volume spike that fails to hold.

Proxy implementation: no news/econ-release feed is wired up yet, so this
detects the price/volume signature of a news-driven spike (a bar whose
range and volume are both well above normal, closing back away from the
spike extreme) rather than a true news-event trigger. Revisit once a news
API is available.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import atr
from strategies.base import Strategy
from strategies.day._utils import session_bars
from strategies.params import param_field


@dataclass
class NewsFade(Strategy):
    name = "News Fade"
    timeframe = "5min"
    direction = "both"

    range_atr_multiple: float = param_field(
        2.0, label="Spike range (x ATR)", minimum=1.0, maximum=5.0, step=0.25,
        help="The spike bar's range must be at least this many ATRs.",
    )
    volume_multiple: float = param_field(
        2.0, label="Spike volume multiple", minimum=1.0, maximum=5.0, step=0.25,
        help="The spike bar's volume must be at least this many times the session average.",
    )
    fade_retrace_fraction: float = param_field(
        0.5, label="Retrace fraction", minimum=0.1, maximum=0.9, step=0.05,
        help="How much of the spike's range must be given back by the close to count as a fade.",
    )
    atr_period: int = param_field(
        14, label="ATR period (bars)", minimum=5, maximum=30, step=1,
        help="Lookback window for the ATR used to size the spike-range threshold.",
    )
    volume_lookback_bars: int = param_field(
        20, label="Volume baseline lookback (bars)", minimum=10, maximum=40, step=5,
        help="Bars (excluding the spike bar) used to compute the normal-volume baseline.",
    )

    def _spike(self, bars: pd.DataFrame):
        # Session-scoped, not the raw multi-day array: the ATR/volume
        # baseline must come from today's own trading, or the first bar of
        # every new session gets compared against yesterday afternoon's
        # baseline and its normal overnight gap misreads as a "spike".
        sess = session_bars(bars)
        if len(sess) < self.volume_lookback_bars + 1:
            return None
        last = sess.iloc[-1]
        bar_atr = atr(sess, self.atr_period).iloc[-2]  # ATR excluding the spike bar itself
        avg_vol = sess["Volume"].iloc[-(self.volume_lookback_bars + 1):-1].mean()
        bar_range = last["High"] - last["Low"]
        if bar_atr <= 0 or avg_vol <= 0:
            return None
        if bar_range < self.range_atr_multiple * bar_atr or last["Volume"] < self.volume_multiple * avg_vol:
            return None
        up_spike = (last["High"] - max(last["Open"], last["Close"])) > self.fade_retrace_fraction * bar_range
        down_spike = (min(last["Open"], last["Close"]) - last["Low"]) > self.fade_retrace_fraction * bar_range
        if up_spike:
            return "short", last["High"]
        if down_spike:
            return "long", last["Low"]
        return None

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        return self._spike(bars) is not None

    def entry_direction(self, bars: pd.DataFrame) -> str:
        direction, _ = self._spike(bars)
        return direction

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        _, extreme = self._spike(bars)
        return extreme

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        return float(bars.iloc[-1]["Open"])  # fade back toward the pre-spike level
