"""Post-Earnings Drift (PEAD) -- the well-documented tendency for a stock
that reports a positive earnings surprise to keep drifting in the surprise's
direction for weeks, as the market underreacts and then catches up.

Unlike Earnings Momentum / Gap-Hold (which fakes earnings from a price/volume
gap), this uses REAL earnings dates and surprises from engine.data.
earnings_dates -- each symbol's strategy instance is seeded with that
symbol's positive-surprise dates (the per-symbol engine has no symbol
identity of its own, so runner wires this up via a per-symbol factory).
`positive_earnings` is a plain (non-tunable) field for that reason -- it's
data the engine injects per symbol, not a rule parameter a user picks.

Entry: within a few sessions of a positive-surprise report whose reaction
session actually closed up, while price keeps drifting above that session's
close and sits above its 20-day EMA. Long only.

Disclosed substitute for the tracker's "hold ~weeks" time exit: the Strategy
interface can't see how long a position has been held (see connors_rsi2.py),
so the multi-week hold is expressed as riding the drift until price closes
back below the 20-day EMA, backed by an ATR stop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from engine.indicators import atr, ema
from strategies.base import Strategy
from strategies.params import param_field


@dataclass
class PostEarningsDrift(Strategy):
    name = "Post-Earnings Drift (PEAD)"
    timeframe = "1d"
    direction = "long"

    positive_earnings: list[date] = field(default_factory=list)

    entry_window_bars: int = param_field(
        3, label="Entry window (sessions)", minimum=1, maximum=10, step=1,
        help="Enter within N sessions after the reaction session.",
    )
    ema_period: int = param_field(
        20, label="Drift EMA period", minimum=5, maximum=50, step=1,
    )
    stop_atr_multiple: float = param_field(
        2.0, label="Stop distance (x ATR)", minimum=0.5, maximum=5.0, step=0.5,
    )

    def __post_init__(self) -> None:
        self._events = sorted(self.positive_earnings)

    def _drift_active(self, bars: pd.DataFrame) -> bool:
        if not self._events or len(bars) < self.ema_period:
            return False
        i = len(bars) - 1
        for k in range(max(1, i - self.entry_window_bars), i):
            sess_date = bars.index[k].date()
            prev_date = bars.index[k - 1].date()
            # k is the reaction session if a positive-surprise report landed
            # after the prior session's date and on/before this one.
            if any(prev_date < e <= sess_date for e in self._events):
                reaction_up = bars["Close"].iloc[k] > bars["Close"].iloc[k - 1]
                drift_up = bars["Close"].iloc[-1] > bars["Close"].iloc[k]
                if reaction_up and drift_up:
                    return True
        return False

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if not self._drift_active(bars):
            return False
        ema20 = ema(bars["Close"], self.ema_period).iloc[-1]
        return bool(bars["Close"].iloc[-1] > ema20)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return entry_price - self.stop_atr_multiple * atr(bars).iloc[-1]

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < self.ema_period:
            return False
        ema20 = ema(bars["Close"], self.ema_period).iloc[-1]
        return bool(bars["Close"].iloc[-1] < ema20)
