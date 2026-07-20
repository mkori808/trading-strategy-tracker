"""Close->open execution engine for Overnight Hold.

The per-symbol bracket engine (engine/backtest.py) can't express a hold from
one bar's close to the next bar's open -- it fills entries at the next open
and exits on closes. This computes that trade directly, per symbol, and emits
the same SymbolBacktestResult / StrategyBacktestResult shapes so the result
flows through logging, the API, and the dashboard exactly like any other
per-symbol strategy (via aggregate_symbol_results).

No stop is placed (the overnight gap is the risk); a nominal ATR risk unit is
used purely for position sizing and R-multiple normalization -- disclosed, not
a real stop. See strategies/swing/overnight_hold.py and LESSONS.md.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from engine import data as data_module
from engine.backtest import (
    DEFAULT_CASH,
    StrategyBacktestResult,
    SymbolBacktestResult,
    aggregate_symbol_results,
)
from engine.indicators import atr, sma
from engine.portfolio import annualized_stats
from strategies.swing.overnight_hold import OvernightHold


def _run_symbol(config: OvernightHold, symbol: str, start: date, end: date,
                risk_free_rate: float,
                entry_allowed: Callable[[pd.Timestamp], bool] | None = None,
                ) -> SymbolBacktestResult:
    bars = data_module.get_bars(symbol, "1d", start, end)
    period = config.trend_sma_period
    if bars.empty or len(bars) < period + 2:
        return SymbolBacktestResult(symbol, None, pd.DataFrame(), None)

    trend = sma(bars["Close"], period)
    atr14 = atr(bars)
    equity = DEFAULT_CASH
    rows: list[dict] = []
    eq_times = [bars.index[0]]
    eq_vals = [equity]

    for t in range(period, len(bars) - 1):  # need t+1 for the next open
        if entry_allowed is not None and not entry_allowed(bars.index[t]):
            continue
        close_t = float(bars["Close"].iloc[t])
        if not close_t > float(trend.iloc[t]):
            continue
        nominal_risk = float(atr14.iloc[t])
        if not nominal_risk > 0:
            continue
        open_next = float(bars["Open"].iloc[t + 1])
        size = min(int((equity * config.risk_pct) // nominal_risk), int(equity // close_t))
        if size < 1:
            continue
        pnl = (open_next - close_t) * size
        equity += pnl
        rows.append({
            "EntryTime": bars.index[t], "ExitTime": bars.index[t + 1],
            "Size": size, "EntryPrice": close_t, "ExitPrice": open_next,
            "SL": np.nan, "TP": np.nan, "PnL": pnl,
            "ReturnPct": open_next / close_t - 1, "Tag": nominal_risk,
        })
        eq_times.append(bars.index[t + 1])
        eq_vals.append(equity)

    if not rows:
        return SymbolBacktestResult(symbol, None, pd.DataFrame(), None)

    trades = pd.DataFrame(rows)
    equity_curve = pd.DataFrame({"Equity": eq_vals}, index=pd.DatetimeIndex(eq_times))
    stats = _symbol_stats(equity_curve["Equity"], len(trades), len(bars), risk_free_rate)
    return SymbolBacktestResult(symbol, stats, trades, equity_curve)


def _symbol_stats(equity: pd.Series, n_trades: int, n_bars: int, risk_free_rate: float) -> pd.Series:
    cagr, sharpe, sortino = annualized_stats(equity, risk_free_rate)
    drawdown = (equity / equity.cummax() - 1).min() * 100  # negative
    ret_pct = (equity.iloc[-1] / equity.iloc[0] - 1) * 100
    # Exposure ~ share of sessions carrying an overnight position. Approximate
    # (each trade is one night); it's a rough occupancy figure, not exact.
    exposure = n_trades / n_bars * 100 if n_bars else np.nan
    return pd.Series({
        "Sharpe Ratio": sharpe if sharpe is not None else np.nan,
        "Sortino Ratio": sortino if sortino is not None else np.nan,
        "Max. Drawdown [%]": drawdown,
        "Return [%]": ret_pct,
        "CAGR [%]": cagr if cagr is not None else np.nan,
        "Exposure Time [%]": exposure,
        "Alpha [%]": np.nan,   # not computed vs a benchmark for this engine
        "Beta": np.nan,
    })


def run_overnight_backtest(
    strategy_name: str,
    config: OvernightHold,
    symbols: list[str],
    start: date,
    end: date,
    risk_free_rate: float = 0.0,
    entry_allowed: Callable[[pd.Timestamp], bool] | None = None,
) -> StrategyBacktestResult:
    """`entry_allowed` is the timing-gate hook for this engine, since the
    strategies.base.Strategy wrapper (engine/timing_filters.py:EntryGate)
    can't drive a close->open loop: when set, a session whose timestamp it
    rejects takes no new overnight position. None (every pre-existing
    caller) is byte-identical to the original behavior."""
    per_symbol = {
        symbol: _run_symbol(config, symbol, start, end, risk_free_rate, entry_allowed)
        for symbol in symbols
    }
    return aggregate_symbol_results(strategy_name, symbols, per_symbol, start, end, risk_free_rate)
