"""Exposure-matched benchmark calculations for event and swing trades.

Prices are matched only on the exact represented benchmark bar. There is no
forward/backward fill: a missing SPY bar withholds the comparison instead of
changing entry or exit economics.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


MATCH_COLUMNS = (
    "TradeReturn", "MatchedSPYReturn", "ExcessVsSPY",
    "MatchedSPYEntryTime", "MatchedSPYExitTime",
)


@dataclass(frozen=True)
class MatchedBenchmarkSummary:
    benchmark: str
    matched_return_pct: float | None
    matched_excess_pct: float | None
    annualized_excess_pct: float | None
    alpha_annual_pct: float | None
    beta: float | None
    matched_trades: int
    missing_trades: int
    execution_note: str

    def to_dict(self) -> dict:
        return {
            "benchmark": self.benchmark,
            "matchedReturnPct": self.matched_return_pct,
            "matchedExcessPct": self.matched_excess_pct,
            "annualizedExcessPct": self.annualized_excess_pct,
            "alphaAnnualPct": self.alpha_annual_pct,
            "beta": self.beta,
            "matchedTrades": self.matched_trades,
            "missingTrades": self.missing_trades,
            "executionNote": self.execution_note,
        }


def _exact_row(bars: pd.DataFrame, timestamp: object) -> pd.Series | None:
    if bars.empty:
        return None
    stamp = pd.Timestamp(timestamp)
    try:
        row = bars.loc[stamp]
        return row.iloc[-1] if isinstance(row, pd.DataFrame) else row
    except (KeyError, TypeError):
        pass
    # A unique represented session date is exact at daily resolution even
    # when two vendors disagree on timezone. It never crosses a missing day.
    if len(bars.index) and all(
        getattr(value, "hour", 0) == 0 and getattr(value, "minute", 0) == 0
        for value in bars.index[: min(3, len(bars.index))]
    ):
        candidates = bars.loc[bars.index.date == stamp.date()]
        if len(candidates) == 1:
            return candidates.iloc[0]
    return None


def annotate_trades(
    trades: pd.DataFrame,
    benchmark_bars: pd.DataFrame,
    *,
    entry_field: str = "Open",
    exit_field: str = "Close",
) -> pd.DataFrame:
    """Add per-trade strategy, matched-SPY, and excess returns in decimals."""
    output = trades.copy()
    if output.empty:
        for column in MATCH_COLUMNS:
            output[column] = pd.Series(dtype="object")
        return output

    records = []
    for _, trade in output.iterrows():
        trade_return = float(trade["ReturnPct"])
        entry = _exact_row(benchmark_bars, trade["EntryTime"])
        exit_ = _exact_row(benchmark_bars, trade["ExitTime"])
        if (
            entry is None or exit_ is None
            or entry_field not in entry.index or exit_field not in exit_.index
            or not np.isfinite(float(entry[entry_field]))
            or not np.isfinite(float(exit_[exit_field]))
            or float(entry[entry_field]) <= 0
        ):
            records.append((trade_return, None, None, None, None))
            continue
        matched = float(exit_[exit_field]) / float(entry[entry_field]) - 1.0
        records.append((
            trade_return, matched, trade_return - matched,
            trade["EntryTime"], trade["ExitTime"],
        ))

    output["TradeReturn"] = [record[0] for record in records]
    output["MatchedSPYReturn"] = [record[1] for record in records]
    output["ExcessVsSPY"] = [record[2] for record in records]
    output["MatchedSPYEntryTime"] = [record[3] for record in records]
    output["MatchedSPYExitTime"] = [record[4] for record in records]
    return output


def summarize_matches(
    trades: pd.DataFrame,
    *,
    capital_base: float,
    measured_start=None,
    measured_end=None,
    benchmark: str = "SPY",
    execution_note: str = "benchmark Open at entry bar to benchmark Close at exit bar",
) -> MatchedBenchmarkSummary:
    matched = trades.dropna(subset=["MatchedSPYReturn"]) if "MatchedSPYReturn" in trades else trades.iloc[:0]
    missing = len(trades) - len(matched)
    if matched.empty or capital_base <= 0:
        return MatchedBenchmarkSummary(
            benchmark, None, None, None, None, None, 0, missing, execution_note
        )

    notional = matched["Size"].abs() * matched["EntryPrice"].abs()
    benchmark_pnl = notional * matched["MatchedSPYReturn"]
    strategy_pnl = matched["PnL"]
    matched_return_pct = float(benchmark_pnl.sum() / capital_base * 100.0)
    matched_excess_pct = float((strategy_pnl.sum() - benchmark_pnl.sum()) / capital_base * 100.0)

    annualized = None
    if measured_start is not None and measured_end is not None:
        years = (pd.Timestamp(measured_end) - pd.Timestamp(measured_start)).days / 365.25
        strategy_total = float(strategy_pnl.sum() / capital_base)
        benchmark_total = float(benchmark_pnl.sum() / capital_base)
        if years >= 1.0 and strategy_total > -1.0 and benchmark_total > -1.0:
            annualized = float(
                ((1.0 + strategy_total) ** (1.0 / years)
                 - (1.0 + benchmark_total) ** (1.0 / years)) * 100.0
            )

    beta = alpha = None
    if len(matched) >= 2:
        durations = (
            pd.to_datetime(matched["ExitTime"]) - pd.to_datetime(matched["EntryTime"])
        ).dt.total_seconds().div(86400).clip(lower=1.0)
        strategy_daily = np.expm1(np.log1p(matched["TradeReturn"].clip(lower=-0.999999)) / durations)
        benchmark_daily = np.expm1(np.log1p(matched["MatchedSPYReturn"].clip(lower=-0.999999)) / durations)
        variance = float(np.var(benchmark_daily, ddof=1))
        if variance > 0:
            beta = float(np.cov(strategy_daily, benchmark_daily, ddof=1)[0, 1] / variance)
            if len(matched) >= 30:
                alpha = float((strategy_daily.mean() - beta * benchmark_daily.mean()) * 252 * 100)

    return MatchedBenchmarkSummary(
        benchmark, matched_return_pct, matched_excess_pct, annualized,
        alpha, beta, len(matched), missing, execution_note,
    )
