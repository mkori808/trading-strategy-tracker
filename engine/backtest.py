"""Adapter that runs our Strategy objects through backtesting.py.

This is the only module that imports backtesting.py -- strategies stay
library-agnostic (see strategies/base.py), and swapping the underlying
engine later only touches this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from backtesting import Backtest
from backtesting import Strategy as BTStrategy
from backtesting._stats import compute_stats as _compute_stats

from engine import data as data_module
from engine.excursion import compute_trade_excursions
from engine.metrics import BacktestMetrics, compute_metrics
from strategies.base import Strategy

DEFAULT_CASH = 10_000.0
DEFAULT_RISK_PCT = 0.01  # fraction of equity risked per trade
MIN_BARS_TO_TRADE = 30


def _make_adapter(strategy: Strategy, risk_pct: float, spread: float) -> type[BTStrategy]:
    class Adapter(BTStrategy):
        def init(self):
            pass

        def next(self):
            bars = self.data.df
            if len(bars) < MIN_BARS_TO_TRADE:
                return

            if self.position:
                if strategy.exit_signal(bars):
                    self.position.close()
                return

            if not strategy.entry_signal(bars):
                return

            entry_price = float(bars["Close"].iloc[-1])
            direction = strategy.entry_direction(bars)
            stop = strategy.stop_price(bars, entry_price)
            target = strategy.target_price(bars, entry_price)

            risk_per_share = abs(entry_price - stop)
            if risk_per_share <= 0:
                return

            # The broker fills at entry_price adjusted for spread, not the raw
            # close -- validate the bracket against that same adjusted price so
            # a too-thin edge is skipped here instead of raising inside the
            # broker (e.g. VWAP Bounce's mean-reversion target sitting inside
            # the spread of the touch bar's close).
            adjusted = entry_price * (1 + spread) if direction == "long" else entry_price * (1 - spread)
            if direction == "long":
                if stop >= adjusted or (target is not None and target <= adjusted):
                    return
            else:
                if stop <= adjusted or (target is not None and target >= adjusted):
                    return

            # Cash account (margin=1.0, no leverage): a tight stop can imply a
            # risk-sized share count whose notional exceeds account equity,
            # which the broker would silently cancel outright (no fill, no
            # warning -- see backtesting.py's Broker._process_orders). Cap by
            # buying power so the order is never larger than equity can cover.
            size_by_risk = int((self.equity * risk_pct) // risk_per_share)
            size_by_equity = int(self.equity // adjusted)
            size = min(size_by_risk, size_by_equity)
            if size < 1:
                return

            if direction == "long":
                self.buy(size=size, sl=stop, tp=target, tag=risk_per_share)
            else:
                self.sell(size=size, sl=stop, tp=target, tag=risk_per_share)

    return Adapter


@dataclass
class SymbolBacktestResult:
    symbol: str
    stats: pd.Series | None
    trades: pd.DataFrame
    equity_curve: pd.DataFrame | None
    # MFE/MAE and exit-quality diagnostics -- see engine/excursion.py. Only
    # populated by run_symbol_backtest (the standard backtesting.py-backed
    # engine, which has EntryBar/ExitBar positional indices to work with);
    # left as None by other engines producing this same result shape (e.g.
    # engine/overnight.py's close->open engine has no intrabar path to walk).
    excursions: pd.DataFrame | None = None


@dataclass
class StrategyBacktestResult:
    strategy_name: str
    symbols: list[str]
    start: date
    end: date
    per_symbol: dict[str, SymbolBacktestResult]
    metrics: BacktestMetrics
    excursions: pd.DataFrame = field(default_factory=pd.DataFrame)


def run_symbol_backtest(
    strategy: Strategy,
    symbol: str,
    interval: str,
    start: date,
    end: date,
    cash: float = DEFAULT_CASH,
    risk_pct: float = DEFAULT_RISK_PCT,
    spread: float | None = None,
    risk_free_rate: float = 0.0,
) -> SymbolBacktestResult:
    bars = data_module.get_bars(symbol, interval, start, end)
    if bars.empty or len(bars) < MIN_BARS_TO_TRADE:
        return SymbolBacktestResult(symbol, None, pd.DataFrame(), None)

    # A flat spread across every symbol either overstates cost for liquid
    # names or understates it for thin ones -- estimate per symbol from real
    # dollar volume unless a caller explicitly pins one (e.g. a sensitivity
    # sweep). See engine/data.py:estimate_spread and LESSONS.md.
    resolved_spread = spread if spread is not None else data_module.estimate_spread(symbol, start, end)

    adapter_cls = _make_adapter(strategy, risk_pct, resolved_spread)
    bt = Backtest(bars, adapter_cls, cash=cash, spread=resolved_spread, margin=1.0)
    stats = bt.run()

    # backtesting.py 0.6.5 hardcodes risk_free_rate=0.0 inside Backtest.run()
    # and doesn't expose it as a parameter -- every Sharpe/Sortino/Alpha it
    # produces otherwise silently assumes cash earns nothing. Recompute with
    # the real rate using the same trades/equity/data it just derived.
    if risk_free_rate:
        stats = _compute_stats(
            trades=stats["_trades"],
            equity=stats["_equity_curve"]["Equity"].values,
            ohlc_data=bars,
            strategy_instance=None,
            risk_free_rate=risk_free_rate,
        )

    trades = stats["_trades"]
    excursions = compute_trade_excursions(bars, trades) if not trades.empty else None
    return SymbolBacktestResult(symbol, stats, trades, stats["_equity_curve"], excursions)


def run_strategy_backtest(
    strategy_name: str,
    strategy: Strategy,
    symbols: list[str],
    interval: str,
    start: date,
    end: date,
    risk_free_rate: float = 0.0,
    **kwargs,
) -> StrategyBacktestResult:
    """Run one strategy instance across every symbol and pool the results."""
    return run_strategy_backtest_seeded(
        strategy_name, lambda _symbol: strategy, symbols, interval, start, end,
        risk_free_rate=risk_free_rate, **kwargs,
    )


def run_strategy_backtest_seeded(
    strategy_name: str,
    strategy_for: "callable[[str], Strategy]",
    symbols: list[str],
    interval: str,
    start: date,
    end: date,
    risk_free_rate: float = 0.0,
    **kwargs,
) -> StrategyBacktestResult:
    """Like run_strategy_backtest, but builds a fresh strategy per symbol via
    `strategy_for(symbol)`. Needed when a strategy depends on symbol-specific
    external data the OHLCV bars don't carry -- e.g. PEAD seeding each name
    with its own real earnings dates (the per-symbol engine otherwise passes
    one shared instance with no symbol identity)."""
    per_symbol: dict[str, SymbolBacktestResult] = {
        symbol: run_symbol_backtest(
            strategy_for(symbol), symbol, interval, start, end,
            risk_free_rate=risk_free_rate, **kwargs,
        )
        for symbol in symbols
    }
    return aggregate_symbol_results(strategy_name, symbols, per_symbol, start, end, risk_free_rate)


def aggregate_symbol_results(
    strategy_name: str,
    symbols: list[str],
    per_symbol: dict[str, SymbolBacktestResult],
    start: date,
    end: date,
    risk_free_rate: float = 0.0,
) -> StrategyBacktestResult:
    """Pool per-symbol results into one StrategyBacktestResult: concat all
    trades for the R-multiple metrics, average the per-symbol risk stats.
    Shared by the per-symbol engine and any engine that produces the same
    SymbolBacktestResult shape (e.g. engine/overnight.py)."""
    all_trades = []
    all_excursions = []
    drawdowns, sharpes, sortinos, alphas, betas, cagrs, exposures = [], [], [], [], [], [], []
    for symbol, result in per_symbol.items():
        if not result.trades.empty:
            tagged = result.trades.copy()
            tagged["Symbol"] = symbol
            all_trades.append(tagged)
        if result.excursions is not None and not result.excursions.empty:
            tagged = result.excursions.copy()
            tagged["Symbol"] = symbol
            all_excursions.append(tagged)
        if result.stats is not None:
            # Every one of these is a *mean of independent per-symbol runs*,
            # not a portfolio metric -- it ignores cross-symbol correlation,
            # so it understates true portfolio drawdown/risk. See LESSONS.md.
            for bucket, key in (
                (drawdowns, "Max. Drawdown [%]"),
                (sharpes, "Sharpe Ratio"),
                (sortinos, "Sortino Ratio"),
                (alphas, "Alpha [%]"),
                (betas, "Beta"),
                (cagrs, "CAGR [%]"),
                (exposures, "Exposure Time [%]"),
            ):
                value = result.stats.get(key)
                if pd.notna(value):
                    bucket.append(abs(value) if key == "Max. Drawdown [%]" else value)

    def _mean(values: list[float]) -> float | None:
        return (sum(values) / len(values)) if values else None

    pooled_trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    pooled_excursions = pd.concat(all_excursions, ignore_index=True) if all_excursions else pd.DataFrame()
    metrics = compute_metrics(
        strategy_name=strategy_name,
        symbol="ALL",
        trades=pooled_trades,
        start=start,
        end=end,
        max_drawdown_pct=max(drawdowns) if drawdowns else None,
        sharpe=_mean(sharpes),
        sortino=_mean(sortinos),
        alpha_pct=_mean(alphas),
        beta=_mean(betas),
        cagr_pct=_mean(cagrs),
        exposure_pct=_mean(exposures),
        risk_free_rate=risk_free_rate,
    )
    return StrategyBacktestResult(
        strategy_name, symbols, start, end, per_symbol, metrics, pooled_excursions
    )
