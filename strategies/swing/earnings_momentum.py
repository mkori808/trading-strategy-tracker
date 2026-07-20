"""Earnings Momentum / Gap-Hold -- ride the second-leg move after an
earnings gap settles into a consolidation.

Proxy implementation: no earnings-calendar feed is wired up yet, so a
qualifying gap is detected purely from price/volume (a large overnight gap
on a volume surge) rather than confirmed against an actual earnings date.
Revisit once an earnings-calendar source is available.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.indicators import ema
from strategies.base import Strategy
from strategies.params import param_field


@dataclass
class EarningsMomentumGapHold(Strategy):
    name = "Earnings Momentum / Gap-Hold"
    timeframe = "1d"
    direction = "long"

    gap_threshold: float = param_field(
        0.04, label="Min gap size (fraction)", minimum=0.01, maximum=0.15, step=0.01,
        help="0.04 = 4%. Overnight gap must be at least this large.",
    )
    volume_multiple: float = param_field(
        2.0, label="Gap volume multiple", minimum=1.0, maximum=5.0, step=0.25,
        help="Gap bar's volume must be at least this many times the trailing average.",
    )
    gap_lookback: int = param_field(
        10, label="Gap search window (bars)", minimum=5, maximum=30, step=1,
    )
    consolidation_min_bars: int = param_field(
        2, label="Min consolidation length (bars)", minimum=1, maximum=10, step=1,
    )
    stop_buffer_pct: float = param_field(
        0.01, label="Stop buffer below consolidation (fraction)", minimum=0.0, maximum=0.05, step=0.005,
        help="Extra cushion below the post-gap consolidation low used for the stop.",
    )
    exit_ema_period: int = param_field(
        9, label="Exit momentum EMA period", minimum=3, maximum=30, step=1,
        help="Exit when price closes back below this EMA (momentum failure).",
    )

    def _setup(self, bars: pd.DataFrame):
        if len(bars) < self.gap_lookback + 5:
            return None
        window = bars.iloc[:-1].tail(self.gap_lookback)
        avg_vol = window["Volume"].mean()
        gap_idx = None
        for i in range(1, len(window)):
            prev_close = window["Close"].iloc[i - 1]
            open_ = window["Open"].iloc[i]
            gap_pct = (open_ - prev_close) / prev_close
            if gap_pct >= self.gap_threshold and avg_vol > 0 and window["Volume"].iloc[i] >= self.volume_multiple * avg_vol:
                gap_idx = i
        if gap_idx is None:
            return None
        post_gap = window.iloc[gap_idx:]
        if len(post_gap) < self.consolidation_min_bars:
            return None
        consolidation_low = post_gap["Low"].min()
        consolidation_high = post_gap["High"].max()
        return consolidation_low, consolidation_high

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        setup = self._setup(bars)
        if setup is None:
            return False
        _, consolidation_high = setup
        return bars.iloc[-1]["Close"] > consolidation_high

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        consolidation_low, _ = self._setup(bars)
        return consolidation_low * (1 - self.stop_buffer_pct)

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        # ride the second-leg move until short-term momentum fails
        if len(bars) < self.exit_ema_period:
            return False
        exit_ema = ema(bars["Close"], self.exit_ema_period)
        return bars["Close"].iloc[-1] < exit_ema.iloc[-1]
