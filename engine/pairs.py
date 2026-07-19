"""Pairs / Stat Arb engine: cointegration pair selection + spread z-score
mean-reversion, market-neutral (long one leg, short the other, roughly
equal dollar exposure). Separate from engine/backtest.py (single-symbol
bracket orders) and engine/cross_sectional.py (whole-universe ranking) --
this strategy shape needs two synchronized legs traded as one position,
which neither existing engine can express. See
strategies/swing/pairs_stat_arb.py and LESSONS.md.

Look-ahead discipline: `find_cointegrated_pair` only ever sees the bars
it's handed -- it has no date-range awareness of its own. The caller
(`run_pairs_backtest`) enforces the actual train/trade split: the pair is
selected using only the first half of the requested window, then traded
over the second half. This directly answers the tracker's own warning that
this strategy is "very prone to great in-sample / broken live" -- pair
selection here never sees the data its performance is graded on.

Capital note: unlike the per-symbol and portfolio engines, this sizes each
entry using the FULL current equity split evenly across both legs, not a
fractional risk_pct. That's a deliberate simplification, not an oversight
-- there is only ever one pair open at a time, so there's no concurrent-
capital allocation problem to solve the way engine/portfolio.py has to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import combinations

import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import coint

from engine import data as data_module
from engine.portfolio import annualized_stats
from strategies.swing.pairs_stat_arb import PairsStatArb

DEFAULT_CASH = 10_000.0
COINT_SIGNIFICANCE = 0.05
MIN_OVERLAP_BARS = 60


@dataclass
class PairSelection:
    symbol_a: str
    symbol_b: str
    p_value: float


@dataclass
class PairsResult:
    strategy_name: str
    pair: PairSelection | None
    training_window: tuple[pd.Timestamp, pd.Timestamp]
    trading_window: tuple[pd.Timestamp, pd.Timestamp]
    equity_curve: pd.Series
    trades: pd.DataFrame
    final_equity: float
    return_pct: float
    cagr_pct: float | None
    max_drawdown_pct: float
    sharpe: float | None
    sortino: float | None
    risk_free_rate: float
    # The universe run_pairs_backtest searched for a cointegrated pair --
    # placed last (not e.g. after strategy_name) so the two positional
    # PairsResult(...) call sites below don't have every subsequent
    # positional argument silently shift by one. No default: every call
    # site must state which universe it actually searched, even the
    # early-return "no data"/"no cointegrated pair" cases below -- the
    # caller still specified a real universe in those cases, so silently
    # defaulting to [] would misreport it as empty rather than unsearched.
    symbols: list[str]


def find_cointegrated_pair(
    bars_by_symbol: dict[str, pd.DataFrame],
    lookback: int = MIN_OVERLAP_BARS,
    significance: float = COINT_SIGNIFICANCE,
) -> PairSelection | None:
    """Engle-Granger cointegration test over every pair in `bars_by_symbol`.
    Returns the most significant (lowest p-value) pair clearing
    `significance`, or None if nothing qualifies. Deliberately has no
    concept of "today" or a date range -- whatever slice of history the
    caller passes in is all it can see, so look-ahead safety is the
    caller's responsibility (see module docstring)."""
    best: PairSelection | None = None
    symbols = [s for s, b in bars_by_symbol.items() if len(b) > lookback]
    for a, b in combinations(symbols, 2):
        common = bars_by_symbol[a].index.intersection(bars_by_symbol[b].index)
        if len(common) < lookback:
            continue
        pa = bars_by_symbol[a]["Close"].loc[common]
        pb = bars_by_symbol[b]["Close"].loc[common]
        if (pa <= 0).any() or (pb <= 0).any():
            continue
        try:
            _, p_value, _ = coint(np.log(pa), np.log(pb))
        except Exception:
            continue
        if p_value < significance and (best is None or p_value < best.p_value):
            best = PairSelection(a, b, float(p_value))
    return best


def _spread_zscore(log_a: pd.Series, log_b: pd.Series, lookback: int) -> pd.Series:
    spread = log_a - log_b
    mean = spread.rolling(lookback).mean()
    std = spread.rolling(lookback).std()
    return (spread - mean) / std.replace(0, np.nan)


def run_pairs_backtest(
    strategy_name: str,
    strategy: PairsStatArb,
    symbols: list[str],
    start: date,
    end: date,
    cash: float = DEFAULT_CASH,
    risk_free_rate: float = 0.0,
) -> PairsResult:
    raw_bars = {s: data_module.get_bars(s, "1d", start, end) for s in symbols}
    raw_bars = {s: b for s, b in raw_bars.items() if not b.empty}

    all_dates = sorted(set().union(*(b.index for b in raw_bars.values()))) if raw_bars else []
    if not all_dates:
        empty = pd.Series([cash], index=[pd.Timestamp(start)])
        return PairsResult(
            strategy_name, None, (pd.Timestamp(start), pd.Timestamp(start)),
            (pd.Timestamp(start), pd.Timestamp(end)), empty, pd.DataFrame(),
            cash, 0.0, None, 0.0, None, None, risk_free_rate, symbols,
        )

    midpoint = all_dates[len(all_dates) // 2]
    training_bars = {s: b.loc[:midpoint] for s, b in raw_bars.items()}
    pair = find_cointegrated_pair(training_bars, lookback=strategy.zscore_lookback)

    if pair is None:
        empty = pd.Series([cash], index=[midpoint])
        return PairsResult(
            strategy_name, None, (all_dates[0], midpoint), (midpoint, all_dates[-1]),
            empty, pd.DataFrame(), cash, 0.0, None, 0.0, None, None, risk_free_rate, symbols,
        )

    a, b = pair.symbol_a, pair.symbol_b
    trade_a = raw_bars[a].loc[midpoint:]
    trade_b = raw_bars[b].loc[midpoint:]
    common = trade_a.index.intersection(trade_b.index)
    close_a = trade_a["Close"].loc[common]
    close_b = trade_b["Close"].loc[common]
    zscores = _spread_zscore(np.log(close_a), np.log(close_b), strategy.zscore_lookback)

    cash_balance = cash
    position: str | None = None  # "long_spread" (long A, short B) or "short_spread"
    shares_a = shares_b = 0.0
    entry_a_price = entry_b_price = None
    entry_time = None
    equity_points: list[tuple[pd.Timestamp, float]] = []
    trade_log: list[dict] = []

    for t in common:
        z = zscores.get(t)
        pa, pb = close_a.loc[t], close_b.loc[t]

        if position is not None and pd.notna(z):
            stop_hit = abs(z) > strategy.stop_zscore
            reverted = abs(z) < strategy.exit_zscore
            if stop_hit or reverted:
                pnl = shares_a * (pa - entry_a_price) + shares_b * (pb - entry_b_price)
                cash_balance += abs(shares_a) * entry_a_price + abs(shares_b) * entry_b_price + pnl
                trade_log.append({
                    "EntryTime": entry_time, "ExitTime": t, "Pair": f"{a}/{b}",
                    "Position": position, "PnL": pnl,
                    "Reason": "cointegration_break_stop" if stop_hit else "reverted",
                })
                position = None
                shares_a = shares_b = 0.0

        if position is None and pd.notna(z) and abs(z) >= strategy.entry_zscore:
            leg_dollars = cash_balance / 2
            if z > 0:  # spread too high vs. its mean: short A, long B
                shares_a, shares_b = -leg_dollars / pa, leg_dollars / pb
                position = "short_spread"
            else:  # spread too low: long A, short B
                shares_a, shares_b = leg_dollars / pa, -leg_dollars / pb
                position = "long_spread"
            entry_a_price, entry_b_price, entry_time = pa, pb, t
            cash_balance -= abs(shares_a) * pa + abs(shares_b) * pb

        mark_to_market = 0.0
        if position is not None:
            mark_to_market = (
                shares_a * (pa - entry_a_price) + shares_b * (pb - entry_b_price)
                + abs(shares_a) * entry_a_price + abs(shares_b) * entry_b_price
            )
        equity_points.append((t, cash_balance + mark_to_market))

    equity_curve = pd.Series(
        [v for _, v in equity_points], index=pd.DatetimeIndex([t for t, _ in equity_points])
    )
    trades_df = pd.DataFrame(trade_log)
    final_equity = float(equity_curve.iloc[-1]) if len(equity_curve) else cash
    return_pct = (final_equity / cash - 1) * 100
    running_max = equity_curve.cummax()
    max_dd = float(((equity_curve - running_max) / running_max).min() * 100) if len(equity_curve) else 0.0
    cagr, sharpe, sortino = annualized_stats(equity_curve, risk_free_rate)

    return PairsResult(
        strategy_name=strategy_name,
        pair=pair,
        training_window=(all_dates[0], midpoint),
        trading_window=(midpoint, all_dates[-1]),
        equity_curve=equity_curve,
        trades=trades_df,
        final_equity=final_equity,
        return_pct=return_pct,
        cagr_pct=cagr,
        max_drawdown_pct=abs(max_dd),
        sharpe=sharpe,
        sortino=sortino,
        risk_free_rate=risk_free_rate,
        symbols=symbols,
    )
