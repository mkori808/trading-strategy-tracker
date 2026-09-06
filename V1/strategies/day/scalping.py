"""Scalping (3-5 min) -- MACD / EMA9-EMA20 / VWAP confluence signal with a
tight, fixed stop. High frequency, any liquid session."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import ema, macd, vwap
from strategies.base import Strategy
from strategies.params import param_field


@dataclass
class Scalping(Strategy):
    name = "Scalping (3-5 min)"
    timeframe = "5min"
    direction = "both"

    tight_stop_pct: float = param_field(
        0.0015, label="Stop distance (fraction)", minimum=0.0005, maximum=0.01, step=0.0005,
        help="Fixed stop as a fraction of entry price.",
    )
    tight_target_pct: float = param_field(
        0.00225, label="Target distance (fraction)", minimum=0.0005, maximum=0.02, step=0.00025,
        help="Fixed target as a fraction of entry price (default is a 1.5:1 reward:risk).",
    )
    ema_fast_period: int = param_field(
        9, label="Fast EMA period", minimum=3, maximum=15, step=1,
    )
    ema_slow_period: int = param_field(
        20, label="Slow EMA period", minimum=10, maximum=50, step=1,
    )
    macd_fast_period: int = param_field(
        12, label="MACD fast period", minimum=5, maximum=20, step=1,
    )
    macd_slow_period: int = param_field(
        26, label="MACD slow period", minimum=15, maximum=40, step=1,
    )
    macd_signal_period: int = param_field(
        9, label="MACD signal period", minimum=3, maximum=15, step=1,
    )

    def _confluence(self, bars: pd.DataFrame) -> str | None:
        # A fixed floor, not scaled to ema_slow_period/macd_slow_period below:
        # ema()/macd() are ewm()-based, which (unlike a rolling/sma window)
        # never produces NaN for having "too little" history -- it just takes
        # a few periods to numerically converge. 25 bars was already fine at
        # the original fixed 20/26-period defaults; no correctness reason to
        # scale it with the now-tunable periods.
        if len(bars) < 25:
            return None
        ema9, ema20 = ema(bars["Close"], self.ema_fast_period), ema(bars["Close"], self.ema_slow_period)
        _, _, hist = macd(bars["Close"], self.macd_fast_period, self.macd_slow_period, self.macd_signal_period)
        vw = vwap(bars)
        close = bars["Close"].iloc[-1]
        bullish = ema9.iloc[-1] > ema20.iloc[-1] and hist.iloc[-1] > 0 and close > vw.iloc[-1]
        bearish = ema9.iloc[-1] < ema20.iloc[-1] and hist.iloc[-1] < 0 and close < vw.iloc[-1]
        if bullish:
            return "long"
        if bearish:
            return "short"
        return None

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        return self._confluence(bars) is not None

    def entry_direction(self, bars: pd.DataFrame) -> str:
        return self._confluence(bars)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        direction = self._confluence(bars)
        if direction == "long":
            return entry_price * (1 - self.tight_stop_pct)
        return entry_price * (1 + self.tight_stop_pct)

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        direction = self._confluence(bars)
        if direction == "long":
            return entry_price * (1 + self.tight_target_pct)
        return entry_price * (1 - self.tight_target_pct)
