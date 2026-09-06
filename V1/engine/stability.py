"""Chronological stability check: split a strategy's pooled trade log at its
midpoint and compute metrics for each half separately.

A strategy whose overall positive expectancy comes entirely from one lucky
stretch (rather than holding up across the tested window) is a weaker
promotion-to-paper-trading candidate than the pooled number alone suggests --
this is a first pass at that check, split by trade count (not by calendar
date) so each half carries comparable statistical weight.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine.backtest import StrategyBacktestResult
from engine.metrics import BacktestMetrics, compute_metrics


@dataclass
class StabilitySplit:
    strategy_name: str
    first_half: BacktestMetrics
    second_half: BacktestMetrics


def _pooled_trades(result: StrategyBacktestResult) -> pd.DataFrame:
    frames = [
        r.trades.assign(Symbol=symbol)
        for symbol, r in result.per_symbol.items()
        if not r.trades.empty
    ]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values("EntryTime").reset_index(drop=True)


def split_half_metrics(result: StrategyBacktestResult) -> StabilitySplit:
    trades = _pooled_trades(result)
    midpoint = len(trades) // 2
    first, second = trades.iloc[:midpoint], trades.iloc[midpoint:]

    def _window(df: pd.DataFrame) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
        if df.empty:
            return None, None
        return df["EntryTime"].min(), df["ExitTime"].max()

    first_start, first_end = _window(first)
    second_start, second_end = _window(second)

    return StabilitySplit(
        strategy_name=result.strategy_name,
        first_half=compute_metrics(
            result.strategy_name, "ALL", first, start=first_start, end=first_end
        ),
        second_half=compute_metrics(
            result.strategy_name, "ALL", second, start=second_start, end=second_end
        ),
    )
