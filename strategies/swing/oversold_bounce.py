"""Oversold Bounce (RSI<30) -- counter-trend bounce at support. Explicitly
counter-trend; use smaller size and tighter stops than trend-following
setups (enforced by risk sizing elsewhere, not by this rule)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import rsi
from strategies.base import Strategy
from strategies.params import param_field
from strategies.swing._utils import is_bullish_candle, swing_low


@dataclass
class OversoldBounce(Strategy):
    name = "Oversold Bounce (RSI<30)"
    timeframe = "1d"
    direction = "long"

    rsi_period: int = param_field(
        14, label="RSI period", minimum=5, maximum=30, step=1,
    )
    rsi_threshold: int = param_field(
        30, label="RSI oversold threshold", minimum=10, maximum=45, step=1,
    )
    support_lookback: int = param_field(
        20, label="Support lookback (bars)", minimum=5, maximum=60, step=5,
    )
    support_tolerance: float = param_field(
        0.02, label="Support tolerance (fraction)", minimum=0.005, maximum=0.10, step=0.005,
        help="0.02 = 2%. How close to the prior swing low counts as 'near support'.",
    )
    target_risk_multiple: float = param_field(
        2.0, label="Target (x risk)", minimum=0.5, maximum=5.0, step=0.5,
        help="Take profit at N times the initial risk (a quick counter-trend bounce).",
    )

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        if len(bars) < self.rsi_period + self.support_lookback:
            return False
        r = rsi(bars["Close"], self.rsi_period)
        last = bars.iloc[-1]
        support = swing_low(bars.iloc[:-1], self.support_lookback)
        near_support = last["Low"] <= support * (1 + self.support_tolerance)
        return r.iloc[-1] < self.rsi_threshold and near_support and is_bullish_candle(last)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return bars.iloc[-1]["Low"] * 0.99

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        stop = self.stop_price(bars, entry_price)
        return entry_price + self.target_risk_multiple * (entry_price - stop)
