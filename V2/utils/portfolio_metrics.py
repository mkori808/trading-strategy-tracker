"""Portfolio and aligned-benchmark return metrics.

The functions deliberately use total-return series supplied by callers and
never label a CAGR difference as alpha.  Dates are part of the calculation:
strategy and benchmark returns must describe the same start/end interval.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime
from math import prod
from typing import Iterable


DateLike = str | date | datetime


def _as_date(value: DateLike) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def elapsed_years(start_date: DateLike, end_date: DateLike) -> float:
    start, end = _as_date(start_date), _as_date(end_date)
    if end <= start:
        raise ValueError("end_date must be after start_date")
    return (end - start).days / 365.2425


def cagr(starting_equity: float, ending_equity: float, start_date: DateLike, end_date: DateLike) -> float:
    if starting_equity <= 0 or ending_equity <= 0:
        raise ValueError("equity values must be positive")
    years = elapsed_years(start_date, end_date)
    return (ending_equity / starting_equity) ** (1.0 / years) - 1.0


def compound_returns(returns: Iterable[float]) -> float:
    return prod(1.0 + float(value) for value in returns)


@dataclass(frozen=True)
class PortfolioBenchmarkMetrics:
    start_date: str
    end_date: str
    years: float
    strategy_cagr: float
    spy_cagr: float
    cagr_spread: float
    strategy_ending_value: float
    spy_ending_value: float
    dollar_advantage: float

    def to_dict(self) -> dict:
        return asdict(self)


def portfolio_benchmark_metrics(
    strategy_returns: Iterable[float],
    spy_total_returns: Iterable[float],
    start_date: DateLike,
    end_date: DateLike,
    initial_value: float = 250_000.0,
) -> PortfolioBenchmarkMetrics:
    """Compute aligned portfolio/SPY metrics for one declared date window.

    The caller is responsible for selecting only returns inside that exact
    interval.  Keeping the interval explicit prevents accidental comparisons
    between different start or end dates.
    """
    if initial_value <= 0:
        raise ValueError("initial_value must be positive")
    start, end = _as_date(start_date), _as_date(end_date)
    strategy_ending = initial_value * compound_returns(strategy_returns)
    spy_ending = initial_value * compound_returns(spy_total_returns)
    strategy_cagr = cagr(initial_value, strategy_ending, start, end)
    spy_cagr = cagr(initial_value, spy_ending, start, end)
    return PortfolioBenchmarkMetrics(
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        years=elapsed_years(start, end),
        strategy_cagr=strategy_cagr,
        spy_cagr=spy_cagr,
        cagr_spread=strategy_cagr - spy_cagr,
        strategy_ending_value=strategy_ending,
        spy_ending_value=spy_ending,
        dollar_advantage=strategy_ending - spy_ending,
    )
