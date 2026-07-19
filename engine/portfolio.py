"""Real portfolio-level simulation on top of per-symbol backtest results.

engine/backtest.py runs each symbol in its own isolated $10K account and
`compute_metrics` reports the *mean* (Sharpe) or *max* (drawdown) across
those independent runs -- neither is a portfolio metric, because neither
accounts for shared capital or cross-symbol correlation. See LESSONS.md,
"Structural gap: per-symbol backtests aren't a portfolio".

This module doesn't re-simulate bar-level fills (that machinery, in
backtest.py, is already correct and stays as the source of truth for *when*
and *at what price* each symbol's strategy would have traded). It replays
those already-computed trades chronologically against ONE shared cash pool,
so position sizing reflects real available capital and a concurrent-position
cap, and the resulting equity curve reflects real simultaneous drawdown
across correlated names instead of the max of independent ones.

Simplification, disclosed rather than hidden: equity available for sizing a
new entry is `cash + cost basis of currently open positions`, not a full
mark-to-market of open positions at that instant (that would require
re-walking every open symbol's bar-level price series). This slightly
understates equity during winning stretches and overstates it during losing
ones, but avoids reimplementing intrabar fills a second time.

Second simplification: a short position reserves `size * entry_price` of
cash exactly like a long, rather than modeling margin/collateral mechanics.
This is symmetric and conservative (it never lets a short consume less
capital than an equivalent long) but isn't a real margin model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.backtest import DEFAULT_CASH, DEFAULT_RISK_PCT, StrategyBacktestResult

TRADING_DAYS_PER_YEAR = 252


@dataclass
class PortfolioResult:
    strategy_name: str
    trades: pd.DataFrame
    equity_curve: pd.Series
    skipped_for_capacity: int
    final_equity: float
    return_pct: float
    cagr_pct: float | None
    max_drawdown_pct: float
    sharpe: float | None
    sortino: float | None
    max_concurrent_positions: int
    risk_free_rate: float


@dataclass
class _Intent:
    id: int
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    risk_per_share: float
    direction_sign: int  # +1 long, -1 short (sign of the original Size)
    pnl_per_unit: float  # PnL / |Size| -- unsigned magnitude, sign = win/loss only,
    # independent of direction. Replayed size is always a positive magnitude
    # (see run_portfolio_backtest), so pairing it with a *signed* per-share
    # PnL would flip the sign of every winning short -- dividing by |Size|
    # instead keeps "positive = profitable" true regardless of direction.


def _extract_intents(per_symbol_trades: dict[str, pd.DataFrame]) -> list[_Intent]:
    intents: list[_Intent] = []
    next_id = 0
    for symbol, trades in per_symbol_trades.items():
        if trades.empty:
            continue
        fallback = (trades["EntryPrice"] - trades["SL"]).abs()
        risk_per_share = (
            pd.to_numeric(trades["Tag"], errors="coerce").fillna(fallback)
            if "Tag" in trades.columns
            else fallback
        )
        pnl_per_unit = trades["PnL"] / trades["Size"].abs()
        for i in range(len(trades)):
            row = trades.iloc[i]
            intents.append(
                _Intent(
                    id=next_id,
                    symbol=symbol,
                    entry_time=row["EntryTime"],
                    exit_time=row["ExitTime"],
                    entry_price=float(row["EntryPrice"]),
                    risk_per_share=float(risk_per_share.iloc[i]),
                    direction_sign=1 if row["Size"] > 0 else -1,
                    pnl_per_unit=float(pnl_per_unit.iloc[i]),
                )
            )
            next_id += 1
    return intents


def annualized_stats(
    equity_curve: pd.Series, risk_free_rate: float
) -> tuple[float | None, float | None, float | None]:
    """(CAGR%, Sharpe, Sortino) from a daily-resampled equity curve, mirroring
    backtesting.py's own methodology (geometric mean day return, compounded)
    closely enough to be comparable, without forcing a multi-asset portfolio
    through machinery built for one instrument's OHLC series."""
    daily = equity_curve.resample("D").last().ffill().dropna()
    if len(daily) < 3:
        return None, None, None
    day_returns = daily.pct_change().dropna()
    if day_returns.empty or day_returns.std() == 0:
        return None, None, None

    gmean = np.exp(np.log1p(day_returns).sum() / len(day_returns)) - 1
    ann_return = (1 + gmean) ** TRADING_DAYS_PER_YEAR - 1
    ann_vol = day_returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
    sharpe = (ann_return - risk_free_rate) / ann_vol if ann_vol else None

    downside = day_returns.clip(upper=0)
    downside_dev = np.sqrt((downside**2).mean()) * np.sqrt(TRADING_DAYS_PER_YEAR)
    sortino = (ann_return - risk_free_rate) / downside_dev if downside_dev else None

    years = (daily.index[-1] - daily.index[0]).days / 365.25
    cagr = ((daily.iloc[-1] / daily.iloc[0]) ** (1 / years) - 1) * 100 if years > 0 else None

    return cagr, sharpe, sortino


def run_portfolio_backtest(
    result: StrategyBacktestResult,
    cash: float = DEFAULT_CASH,
    risk_pct: float = DEFAULT_RISK_PCT,
    max_concurrent_positions: int = 5,
    risk_free_rate: float = 0.0,
) -> PortfolioResult:
    per_symbol_trades = {s: r.trades for s, r in result.per_symbol.items()}
    intents = _extract_intents(per_symbol_trades)

    if not intents:
        empty_curve = pd.Series([cash], index=[pd.Timestamp(result.start)])
        return PortfolioResult(
            result.strategy_name, pd.DataFrame(), empty_curve, 0, cash, 0.0,
            None, 0.0, None, None, max_concurrent_positions, risk_free_rate,
        )

    # Exits before entries at the same timestamp -- frees capital first,
    # the conservative (and realistic) ordering.
    events: list[tuple[pd.Timestamp, int, str, _Intent]] = []
    for it in intents:
        events.append((it.entry_time, 1, "entry", it))
        events.append((it.exit_time, 0, "exit", it))
    events.sort(key=lambda e: (e[0], e[1]))

    cash_balance = cash
    active: dict[int, dict] = {}  # intent.id -> {size, entry_price, pnl_per_share, symbol}
    skipped = 0
    equity_points: list[tuple[pd.Timestamp, float]] = [(events[0][0], cash)]
    closed_trades = []

    def _committed() -> float:
        return sum(p["size"] * p["entry_price"] for p in active.values())

    for time, _, kind, it in events:
        if kind == "entry":
            open_slots = max_concurrent_positions - len(active)
            if open_slots <= 0:
                skipped += 1
                continue
            equity_now = cash_balance + _committed()
            size_by_risk = int((equity_now * risk_pct) // it.risk_per_share) if it.risk_per_share > 0 else 0
            # Ration cash across the *remaining* open slots, not the whole
            # balance -- a tight-stop symbol's size_by_risk can be huge (risk
            # dollars / a tiny risk_per_share), so size_by_cash is often the
            # binding constraint, and without rationing the first entry in
            # any batch would claim the entire pool and starve every other
            # slot regardless of max_concurrent_positions. Equal-weight
            # budgeting across open slots is a simple, disclosed policy
            # choice, not the only reasonable one.
            per_slot_cash = cash_balance / open_slots
            size_by_cash = int(per_slot_cash // it.entry_price) if it.entry_price > 0 else 0
            size = min(size_by_risk, size_by_cash)
            if size < 1:
                skipped += 1
                continue
            cash_balance -= size * it.entry_price
            active[it.id] = {
                "size": size, "entry_price": it.entry_price, "pnl_per_unit": it.pnl_per_unit,
                "direction_sign": it.direction_sign, "symbol": it.symbol,
            }
        else:  # exit
            pos = active.pop(it.id, None)
            if pos is None:
                continue  # this intent was skipped at entry; nothing to close
            # size is always a positive magnitude; pnl_per_unit's sign alone
            # carries win/loss, so this is correct for shorts too (see
            # _Intent.pnl_per_unit's docstring).
            realized_pnl = pos["size"] * pos["pnl_per_unit"]
            cash_balance += pos["size"] * pos["entry_price"] + realized_pnl
            closed_trades.append({
                "Symbol": pos["symbol"], "EntryTime": it.entry_time, "ExitTime": it.exit_time,
                "Size": pos["size"] * pos["direction_sign"], "PnL": realized_pnl,
            })

        equity_points.append((time, cash_balance + _committed()))

    equity_curve = pd.Series(
        [v for _, v in equity_points], index=pd.DatetimeIndex([t for t, _ in equity_points])
    ).sort_index()
    trades_df = pd.DataFrame(closed_trades)

    final_equity = float(equity_curve.iloc[-1])
    return_pct = (final_equity / cash - 1) * 100
    running_max = equity_curve.cummax()
    max_dd = float(((equity_curve - running_max) / running_max).min() * 100)
    cagr, sharpe, sortino = annualized_stats(equity_curve, risk_free_rate)

    return PortfolioResult(
        strategy_name=result.strategy_name,
        trades=trades_df,
        equity_curve=equity_curve,
        skipped_for_capacity=skipped,
        final_equity=final_equity,
        return_pct=return_pct,
        cagr_pct=cagr,
        max_drawdown_pct=abs(max_dd),
        sharpe=sharpe,
        sortino=sortino,
        max_concurrent_positions=max_concurrent_positions,
        risk_free_rate=risk_free_rate,
    )
