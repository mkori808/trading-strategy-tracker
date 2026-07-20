"""Backtest loop for CrossSectionalStrategy -- rebalance-driven, not the
bar-by-bar entry/exit loop engine/backtest.py runs for single-symbol
Strategy instances. See strategies/cross_sectional.py and LESSONS.md for
why this is a separate engine rather than a variant of the existing one.

Rebalances on a fixed monthly schedule by default (first trading day seen
each calendar month across the universe; `rebalance_frequency="weekly"`
switches to the first trading day of each ISO week -- added for
strategies/swing/ensemble_voting.py, which wants weekly rebalancing; every
existing caller keeps the monthly default so its numbers don't shift), holds
target weights between rebalances, and marks equity to market daily using
each position's close. Positions can be fractional shares -- there's no
discrete stop/target bracket order to model here the way engine/backtest.py's
adapter does, so there's no realism cost to fractional sizing (and real
brokers, including Alpaca, support fractional shares).

No intrabar fills to reason about: every rebalance decision uses only data
up to and including its own rebalance date (enforced by slicing each
symbol's bars to `.loc[:day]` before calling `strategy.rebalance`), so
there's no look-ahead to guard against the way engine/backtest.py's
adapter has to for bracket orders.

Slippage/commission (`slippage_bps`/`commission_bps`) default to 0.0 --
byte-identical to this module's original behavior for every existing caller
(Dual Momentum). A caller that wants realistic costs (e.g. the ensemble
engine) passes them explicitly; they're charged only on the traded delta at
each rebalance, not on the whole position, since only the delta actually
transacts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd

from engine import data as data_module
from engine.portfolio import annualized_stats
from strategies.cross_sectional import CrossSectionalStrategy

DEFAULT_CASH = 10_000.0

RebalanceFrequency = Literal["monthly", "weekly", "daily"]


@dataclass
class CrossSectionalResult:
    strategy_name: str
    symbols: list[str]
    start: date
    end: date
    equity_curve: pd.Series
    rebalances: pd.DataFrame  # one row per rebalance date: {date, holdings}
    final_equity: float
    return_pct: float
    cagr_pct: float | None
    max_drawdown_pct: float
    sharpe: float | None
    sortino: float | None
    risk_free_rate: float
    total_costs: float = 0.0  # sum of slippage + commission paid across all rebalances


def _rebalance_dates(
    calendar: pd.DatetimeIndex, frequency: RebalanceFrequency = "monthly"
) -> set[pd.Timestamp]:
    """First trading day present in the calendar for each period -- each
    (year, month) for 'monthly', each (year, ISO week) for 'weekly', every
    single trading day for 'daily' (added to test whether a strategy's
    drawdowns come from a rebalance cadence too slow to react -- see
    engine/compare_dual_momentum_robustness.py -- without having to
    special-case the main loop, which already just checks membership in
    this set)."""
    if frequency == "daily":
        return set(calendar)
    s = pd.Series(calendar, index=calendar)
    if frequency == "weekly":
        iso = calendar.isocalendar()
        return set(s.groupby([iso.year, iso.week]).first())
    return set(s.groupby([calendar.year, calendar.month]).first())


def run_cross_sectional_backtest(
    strategy_name: str,
    strategy: CrossSectionalStrategy,
    symbols: list[str],
    start: date,
    end: date,
    cash: float = DEFAULT_CASH,
    risk_free_rate: float = 0.0,
    rebalance_frequency: RebalanceFrequency = "monthly",
    slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
) -> CrossSectionalResult:
    raw_bars = {s: data_module.get_bars(s, "1d", start, end) for s in symbols}
    raw_bars = {s: b for s, b in raw_bars.items() if not b.empty}
    if not raw_bars:
        empty_curve = pd.Series([cash], index=[pd.Timestamp(start)])
        return CrossSectionalResult(
            strategy_name, symbols, start, end, empty_curve, pd.DataFrame(),
            cash, 0.0, None, 0.0, None, None, risk_free_rate, 0.0,
        )

    calendar = pd.DatetimeIndex(sorted(set().union(*(b.index for b in raw_bars.values()))))
    rebalance_dates = _rebalance_dates(calendar, rebalance_frequency)
    close_df = pd.DataFrame({s: b["Close"] for s, b in raw_bars.items()}).sort_index().ffill()
    cost_rate = (slippage_bps + commission_bps) / 10_000.0

    shares: dict[str, float] = {}
    cash_balance = cash
    total_costs = 0.0
    equity_points: list[tuple[pd.Timestamp, float]] = []
    rebalance_log: list[dict] = []

    def _positions_value(day: pd.Timestamp) -> float:
        total = 0.0
        for symbol, qty in shares.items():
            px = close_df.loc[day, symbol]
            if pd.notna(px):
                total += qty * px
        return total

    for day in calendar:
        if day in rebalance_dates:
            history = {s: b.loc[:day] for s, b in raw_bars.items()}
            target_weights = strategy.rebalance(history, as_of=day)
            rebalance_log.append({"date": day, "holdings": dict(target_weights)})

            portfolio_value = cash_balance + _positions_value(day)

            # Liquidate anything no longer in the target set.
            for symbol in list(shares):
                if symbol not in target_weights:
                    px = close_df.loc[day, symbol]
                    qty = shares.pop(symbol)
                    if pd.notna(px):
                        proceeds = qty * px
                        cost = abs(proceeds) * cost_rate
                        cash_balance += proceeds - cost
                        total_costs += cost

            # (Re)establish target positions at this rebalance's weights.
            for symbol, weight in target_weights.items():
                if symbol not in close_df.columns:
                    continue
                px = close_df.loc[day, symbol]
                if pd.isna(px) or px <= 0:
                    continue
                target_value = portfolio_value * weight
                current_value = shares.get(symbol, 0.0) * px
                delta_shares = (target_value - current_value) / px
                # Slippage/commission apply to the traded delta only -- an
                # unchanged holding from the prior rebalance doesn't re-pay
                # a cost it already paid to get established.
                cost = abs(delta_shares * px) * cost_rate
                shares[symbol] = shares.get(symbol, 0.0) + delta_shares
                cash_balance -= delta_shares * px + cost
                total_costs += cost

        equity_points.append((day, cash_balance + _positions_value(day)))

    equity_curve = pd.Series(
        [v for _, v in equity_points], index=pd.DatetimeIndex([d for d, _ in equity_points])
    )
    final_equity = float(equity_curve.iloc[-1])
    return_pct = (final_equity / cash - 1) * 100
    running_max = equity_curve.cummax()
    max_dd = float(((equity_curve - running_max) / running_max).min() * 100)
    cagr, sharpe, sortino = annualized_stats(equity_curve, risk_free_rate)

    return CrossSectionalResult(
        strategy_name=strategy_name,
        symbols=symbols,
        start=start,
        end=end,
        equity_curve=equity_curve,
        rebalances=pd.DataFrame(rebalance_log),
        final_equity=final_equity,
        return_pct=return_pct,
        cagr_pct=cagr,
        max_drawdown_pct=abs(max_dd),
        sharpe=sharpe,
        sortino=sortino,
        risk_free_rate=risk_free_rate,
        total_costs=total_costs,
    )
