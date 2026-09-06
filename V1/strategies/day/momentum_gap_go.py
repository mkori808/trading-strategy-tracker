"""Momentum / Gap and Go -- chase a pre-market gap's continuation after the
first pullback, on a volume surge. Stop below/above the pullback extreme."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import atr
from strategies.base import Strategy
from strategies.day._utils import session_bars
from strategies.params import param_field


def _prior_session_close(bars: pd.DataFrame) -> float | None:
    last_date = bars.index[-1].date()
    prior = bars[bars.index.date < last_date]
    return None if prior.empty else prior["Close"].iloc[-1]


@dataclass
class MomentumGapAndGo(Strategy):
    name = "Momentum / Gap and Go"
    timeframe = "5min"
    direction = "both"

    gap_threshold: float = param_field(
        0.02, label="Min gap size (fraction)", minimum=0.005, maximum=0.10, step=0.005,
        help="0.02 = 2%. Session open must gap this far from the prior close.",
    )
    volume_multiple: float = param_field(
        1.5, label="Breakout volume multiple", minimum=1.0, maximum=5.0, step=0.1,
        help="Breakout bar's volume must be at least this many times the session average.",
    )
    min_pullback_atr: float = param_field(
        0.3, label="Min pullback depth (x ATR)", minimum=0.1, maximum=2.0, step=0.1,
        help="The post-gap pullback must retrace at least this many ATRs.",
    )
    atr_period: int = param_field(
        14, label="ATR period (bars)", minimum=5, maximum=30, step=1,
    )

    def _setup(self, bars: pd.DataFrame):
        sess = session_bars(bars)
        if len(sess) < 6:
            return None
        prior_close = _prior_session_close(bars)
        if prior_close is None:
            return None
        gap_pct = (sess["Open"].iloc[0] - prior_close) / prior_close
        if abs(gap_pct) < self.gap_threshold:
            return None
        up = gap_pct > 0
        pre, last = sess.iloc[:-1], sess.iloc[-1]
        extreme = pre["High"].max() if up else pre["Low"].min()
        pullback_extreme = pre["Low"].min() if up else pre["High"].max()
        pullback_size = (extreme - pullback_extreme) if up else (pullback_extreme - extreme)
        bar_atr = atr(bars, self.atr_period).iloc[-1]
        if bar_atr <= 0 or pullback_size < self.min_pullback_atr * bar_atr:
            return None
        avg_vol = sess["Volume"].mean()
        if avg_vol == 0 or last["Volume"] < self.volume_multiple * avg_vol:
            return None
        return up, extreme, pullback_extreme, last

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        setup = self._setup(bars)
        if setup is None:
            return False
        up, extreme, _, last = setup
        return last["Close"] > extreme if up else last["Close"] < extreme

    def entry_direction(self, bars: pd.DataFrame) -> str:
        up, _, _, _ = self._setup(bars)
        return "long" if up else "short"

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        setup = self._setup(bars)
        _, _, pullback_extreme, _ = setup
        return pullback_extreme

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        # measured move: project the pullback-to-breakout leg forward from the breakout level
        up, extreme, pullback_extreme, _ = self._setup(bars)
        leg = (extreme - pullback_extreme) if up else (pullback_extreme - extreme)
        return extreme + leg if up else extreme - leg
