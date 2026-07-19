"""Sector Rotation Play -- ride capital flow into a sector ETF whose
relative strength vs. SPY is turning up. Weekly timeframe; less discussed
day-to-day, more of an intermediate/advanced approach.

Needs a benchmark series (SPY) in addition to the sector ETF's own bars, so
unlike the other strategies this one takes `benchmark_bars` at construction
time rather than deriving everything from `bars` alone. `benchmark_bars` is
a plain (non-tunable) field -- it's data the engine supplies, not a rule
parameter a user picks -- so it's declared without `param_field()` and is
excluded from the schema `describe_params()` builds for the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from engine.indicators import ema
from strategies.base import Strategy
from strategies.params import param_field


def _weekly(df: pd.DataFrame) -> pd.DataFrame:
    return df.resample("W").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    ).dropna()


@dataclass
class SectorRotationPlay(Strategy):
    name = "Sector Rotation Play"
    timeframe = "1wk"
    direction = "long"

    benchmark_bars: pd.DataFrame = field(default_factory=pd.DataFrame)

    rs_fast: int = param_field(
        4, label="RS fast EMA (weeks)", minimum=2, maximum=13, step=1,
    )
    rs_slow: int = param_field(
        13, label="RS slow EMA (weeks)", minimum=5, maximum=40, step=1,
    )
    support_lookback_weeks: int = param_field(
        8, label="Stop support lookback (weeks)", minimum=2, maximum=26, step=1,
    )

    def _rs_series(self, bars: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame] | None:
        sector_w = _weekly(bars)
        bench_w = _weekly(self.benchmark_bars.loc[: bars.index[-1]])
        common = sector_w.index.intersection(bench_w.index)
        if len(common) < self.rs_slow + 2:
            return None
        sector_w, bench_w = sector_w.loc[common], bench_w.loc[common]
        rs = sector_w["Close"] / bench_w["Close"]
        return rs, sector_w

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        result = self._rs_series(bars)
        if result is None:
            return False
        rs, _ = result
        rs_fast, rs_slow = ema(rs, self.rs_fast), ema(rs, self.rs_slow)
        return rs_fast.iloc[-2] <= rs_slow.iloc[-2] and rs_fast.iloc[-1] > rs_slow.iloc[-1]

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        _, sector_w = self._rs_series(bars)
        return sector_w["Low"].tail(self.support_lookback_weeks).min() * 0.99

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        # ride the rotation until relative strength turns back down
        result = self._rs_series(bars)
        if result is None:
            return False
        rs, _ = result
        rs_fast, rs_slow = ema(rs, self.rs_fast), ema(rs, self.rs_slow)
        return rs_fast.iloc[-2] >= rs_slow.iloc[-2] and rs_fast.iloc[-1] < rs_slow.iloc[-1]
