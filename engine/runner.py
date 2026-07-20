"""Shared orchestration: map a strategy name to its universe/interval/date
range, build the strategy instance, run the backtest, and log the run.
Used by both engine/cli.py and api/main.py so they don't duplicate this logic.

`RunRequest` lets a caller override the universe, date range, and/or a
strategy's tunable rule parameters (see strategies/params.py) for one run,
without touching the strategy's registered defaults -- the webapp's Lab tab
is the first caller that does this. `run_backtest(name)` with no request is
byte-identical to the original zero-argument behavior and always logs as
canonical; any override logs as an experiment (see engine/logging_db.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from engine import data as data_module
from engine.backtest import (
    StrategyBacktestResult,
    run_strategy_backtest,
    run_strategy_backtest_seeded,
)
from engine import run_avwap_breakout as avwap_breakout_module
from engine.cross_sectional import CrossSectionalResult, run_cross_sectional_backtest
from engine.excursion import write_excursion_report
from engine.filters import build_filter_factory
from engine.logging_db import log_portfolio_run, log_run
from engine.overnight import run_overnight_backtest
from engine.pairs import PairsResult, run_pairs_backtest
from engine.metrics import portfolio_status
from engine.universe import (
    EQUITY_UNIVERSE,
    ETF_AND_EQUITY_UNIVERSE,
    INTRADAY_INTERVAL,
    SECTOR_BENCHMARK,
    SECTOR_UNIVERSE,
    daily_date_range,
    intraday_date_range,
)
from strategies.params import apply_params, describe_params
from strategies.registry import (
    AVWAP_BREAKOUT_NAME,
    CROSS_SECTIONAL_STRATEGY_NAMES,
    DAY_TRADING_STRATEGIES,
    OVERNIGHT_NAME,
    PAIRS_STRATEGY_NAMES,
    PEAD_NAME,
    SECTOR_ROTATION_NAME,
    SWING_TRADING_STRATEGIES_NO_BENCHMARK,
    build_cross_sectional_strategy,
    build_pairs_strategy,
    build_swing_strategies,
)
from strategies.swing.avwap_breakout import AvwapBreakout
from strategies.swing.dual_momentum import DualMomentum
from strategies.swing.overnight_hold import OvernightHold
from strategies.swing.pairs_stat_arb import PairsStatArb
from strategies.swing.pead import PostEarningsDrift

# Sector Rotation Play's universe is structural (sector ETFs ranked against
# SPY specifically) -- swapping it for an arbitrary symbol list changes what
# the strategy even means, the same reasoning engine/compare_universe.py
# already documents for excluding it from universe comparisons. Param and
# date overrides still work.
SYMBOL_OVERRIDE_DISALLOWED_NAMES = {SECTOR_ROTATION_NAME}


@dataclass
class RunRequest:
    symbols: list[str] | None = None
    start: date | None = None
    end: date | None = None
    params: dict[str, Any] | None = None

    def is_default(self) -> bool:
        return not (self.symbols or self.start or self.end or self.params)


# Day-trading strategies the user asked to test on BOTH ETFs and single names
# (see engine/universe.py:ETF_AND_EQUITY_UNIVERSE). They run through the
# standard per-symbol engine; only their symbol list differs. Overnight Hold
# also spans both universes but bypasses run_config -- it runs on its own
# close->open engine (see _run_overnight).
ETF_AND_EQUITY_STRATEGIES = {"Pivot-Level ETF Reversal"}


def run_config(strategy_name: str) -> tuple[str, list[str], date, date]:
    """Default (interval, symbols, start, end) for `strategy_name`. Covers
    every runnable strategy including PEAD and Overnight Hold, which bypass
    build_strategy() but still need a correct default config for the API's
    /api/params endpoint to describe -- PEAD's default happens to match the
    generic EQUITY_UNIVERSE/daily fallback below; Overnight Hold does not
    (ETF_AND_EQUITY_UNIVERSE) and is branched explicitly."""
    if strategy_name in DAY_TRADING_STRATEGIES:
        start, end = intraday_date_range()
        symbols = (
            ETF_AND_EQUITY_UNIVERSE
            if strategy_name in ETF_AND_EQUITY_STRATEGIES
            else EQUITY_UNIVERSE
        )
        return INTRADAY_INTERVAL, symbols, start, end
    if strategy_name == SECTOR_ROTATION_NAME:
        start, end = daily_date_range()
        return "1d", SECTOR_UNIVERSE, start, end
    if strategy_name == OVERNIGHT_NAME:
        start, end = daily_date_range()
        return "1d", ETF_AND_EQUITY_UNIVERSE, start, end
    start, end = daily_date_range()
    return "1d", EQUITY_UNIVERSE, start, end


def strategy_class(strategy_name: str) -> type:
    """The dataclass (or plain class, for Pivot-Level ETF Reversal) behind
    `strategy_name` -- enough to call strategies.params.describe_params()
    without constructing a real instance (Sector Rotation's benchmark_bars,
    PEAD's earnings dates, etc. aren't needed just to read the schema)."""
    if strategy_name in DAY_TRADING_STRATEGIES:
        return type(DAY_TRADING_STRATEGIES[strategy_name])
    if strategy_name == SECTOR_ROTATION_NAME:
        from strategies.swing.sector_rotation import SectorRotationPlay

        return SectorRotationPlay
    if strategy_name == PEAD_NAME:
        return PostEarningsDrift
    if strategy_name == OVERNIGHT_NAME:
        return OvernightHold
    if strategy_name == AVWAP_BREAKOUT_NAME:
        return AvwapBreakout
    if strategy_name == "Dual Momentum":
        return DualMomentum
    if strategy_name == "Pairs / Stat Arb":
        return PairsStatArb
    return type(SWING_TRADING_STRATEGIES_NO_BENCHMARK[strategy_name])


def build_strategy(strategy_name: str, start: date, end: date):
    if strategy_name in DAY_TRADING_STRATEGIES:
        return DAY_TRADING_STRATEGIES[strategy_name]
    if strategy_name == SECTOR_ROTATION_NAME:
        benchmark_bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", start, end)
        return build_swing_strategies(benchmark_bars)[strategy_name]
    return SWING_TRADING_STRATEGIES_NO_BENCHMARK[strategy_name]


def run_backtest(
    strategy_name: str, request: RunRequest | None = None
) -> StrategyBacktestResult:
    """Run `strategy_name`. `request=None` (every call site before this
    feature existed: engine/cli.py, the API's default call) reproduces the
    original zero-argument behavior exactly -- same universe, same dates,
    same params -- and logs as canonical. A `request` with any field set
    overrides that field only and logs as an experiment."""
    # PEAD and Overnight Hold produce the same StrategyBacktestResult shape as
    # the standard engine (so they log and render like any other strategy) but
    # need bespoke construction -- per-symbol earnings seeding / a close->open
    # engine -- so they branch here rather than through build_strategy.
    if strategy_name == PEAD_NAME:
        return _run_pead(request)
    if strategy_name == OVERNIGHT_NAME:
        return _run_overnight(request)
    if strategy_name == AVWAP_BREAKOUT_NAME:
        return _run_avwap_breakout(request)

    interval, symbols, start, end = run_config(strategy_name)
    if request:
        symbols = request.symbols or symbols
        start = request.start or start
        end = request.end or end

    strategy = build_strategy(strategy_name, start, end)
    if request and request.params:
        strategy = apply_params(strategy, request.params)

    # Real, computed risk-free rate for this exact window (13-week T-bill
    # mean) -- backtesting.py itself hardcodes 0%. See LESSONS.md.
    rf = data_module.risk_free_rate(start, end)
    result = run_strategy_backtest(
        strategy_name, strategy, symbols, interval, start, end, risk_free_rate=rf
    )
    log_run(
        result.metrics, symbols,
        params=request.params if request else None,
        is_canonical=request is None or request.is_default(),
    )
    write_excursion_report(strategy_name, result.excursions)
    return result


def _run_pead(request: RunRequest | None = None) -> StrategyBacktestResult:
    """PEAD on the Dow names, each seeded with its own real positive-surprise
    earnings dates (the per-symbol engine has no symbol identity of its own).
    A params override still applies to every per-symbol instance -- only the
    real earnings seeding differs symbol to symbol."""
    start, end = daily_date_range()
    symbols = EQUITY_UNIVERSE
    if request:
        symbols = request.symbols or symbols
        start = request.start or start
        end = request.end or end
    rf = data_module.risk_free_rate(start, end)
    params = request.params if request else None

    def factory(symbol: str) -> PostEarningsDrift:
        strategy = PostEarningsDrift(data_module.positive_earnings_dates(symbol))
        return apply_params(strategy, params)

    result = run_strategy_backtest_seeded(
        PEAD_NAME, factory, symbols, "1d", start, end, risk_free_rate=rf
    )
    log_run(
        result.metrics, symbols, params=params,
        is_canonical=request is None or request.is_default(),
    )
    write_excursion_report(PEAD_NAME, result.excursions)
    return result


def _run_avwap_breakout(request: RunRequest | None = None) -> StrategyBacktestResult:
    """Anchored VWAP Breakout on the Dow names, each seeded with its own
    per-symbol earnings-gap anchors (same per-symbol-construction reason as
    PEAD) and wrapped with the regime + Trend Template gate
    (engine/filters.py) -- this is the first strategy whose canonical
    definition bakes that gate in rather than treating it as an optional
    overlay; see strategies/swing/avwap_breakout.py. A params override
    still applies per symbol, after the per-symbol anchors are resolved --
    see engine/run_avwap_breakout.py's build_strategy_factory docstring."""
    start, end = daily_date_range()
    symbols = EQUITY_UNIVERSE
    if request:
        symbols = request.symbols or symbols
        start = request.start or start
        end = request.end or end
    rf = data_module.risk_free_rate(start, end)
    params = request.params if request else None

    raw_factory, instances = avwap_breakout_module.build_strategy_factory(
        symbols, start, end, avwap_breakout_module.ANCHOR_TYPE
    )

    def factory(symbol: str) -> AvwapBreakout:
        strategy = apply_params(raw_factory(symbol), params)
        instances[symbol] = strategy  # keep in sync if params replaced the instance
        return strategy

    strategy_for, _filter_diagnostics = build_filter_factory(factory, symbols, start, end)
    result = run_strategy_backtest_seeded(
        AVWAP_BREAKOUT_NAME, strategy_for, symbols, "1d", start, end, risk_free_rate=rf,
    )
    log_run(
        result.metrics, symbols, params=params,
        is_canonical=request is None or request.is_default(),
    )
    write_excursion_report(AVWAP_BREAKOUT_NAME, result.excursions)
    return result


def _run_overnight(request: RunRequest | None = None) -> StrategyBacktestResult:
    """Overnight Hold across both ETFs and Dow names, on the close->open
    engine (engine/overnight.py)."""
    start, end = daily_date_range()
    symbols = ETF_AND_EQUITY_UNIVERSE
    if request:
        symbols = request.symbols or symbols
        start = request.start or start
        end = request.end or end
    rf = data_module.risk_free_rate(start, end)
    config = apply_params(OvernightHold(), request.params if request else None)
    result = run_overnight_backtest(
        OVERNIGHT_NAME, config, symbols, start, end, risk_free_rate=rf
    )
    log_run(
        result.metrics, symbols,
        params=request.params if request else None,
        is_canonical=request is None or request.is_default(),
    )
    return result


def _benchmark_window_return(start: date, end: date) -> float | None:
    """SPY's buy-and-hold total return (%) over [start, end] -- the benchmark
    a portfolio-engine run's own return is judged against in
    engine/metrics.py:portfolio_status(), since these engines have no
    per-symbol alpha. Uses the same adjusted daily bars everything else
    uses (adjusted = correct for computing returns; see
    engine/fundamentals.py for the one place that must NOT use them)."""
    bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", start, end)
    if bars.empty or len(bars) < 2:
        return None
    return float((bars["Close"].iloc[-1] / bars["Close"].iloc[0] - 1.0) * 100.0)


def run_cross_sectional(
    strategy_name: str, request: RunRequest | None = None
) -> CrossSectionalResult:
    """Counterpart to run_backtest() for strategies.cross_sectional.CrossSectionalStrategy
    names -- see strategies/registry.py's CROSS_SECTIONAL_STRATEGY_NAMES.
    `request` overrides symbols/dates/params the same way every other
    `_run_*` helper in this file does -- Dual Momentum's ranking isn't
    structurally tied to EQUITY_UNIVERSE the way Sector Rotation Play is
    tied to sector ETFs vs SPY, so a symbol override is allowed here.
    Logged to engine/logging_db.py's portfolio_runs table (see
    log_portfolio_run below) -- a separate schema from the R-multiple-trade
    `runs` table, since a continuously-rebalanced portfolio has no discrete
    trades to log."""
    start, end = daily_date_range()
    symbols = EQUITY_UNIVERSE
    if request:
        symbols = request.symbols or symbols
        start = request.start or start
        end = request.end or end
    rf = data_module.risk_free_rate(start, end)
    strategy = build_cross_sectional_strategy(strategy_name, risk_free_rate=rf)
    if request and request.params:
        strategy = apply_params(strategy, request.params)
    # rebalance_frequency is a param_field() on the strategy (see
    # strategies/swing/dual_momentum.py) but it's an ENGINE setting, not
    # something strategy.rebalance() itself reads -- pulled off the
    # constructed, param-applied instance so a Lab-tab override reaches it
    # through the same apply_params() validation as every other field.
    # getattr with a "monthly" fallback: a future cross-sectional strategy
    # isn't required to expose this field at all.
    rebalance_frequency = getattr(strategy, "rebalance_frequency", "monthly")
    result = run_cross_sectional_backtest(
        strategy_name, strategy, symbols, start, end, risk_free_rate=rf,
        rebalance_frequency=rebalance_frequency,
    )
    # No verdict for a run that never rebalanced (no data) -- status stays
    # NULL and the UI keeps its old "Backtested" fallback.
    benchmark = _benchmark_window_return(result.start, result.end)
    status = (
        None
        if result.rebalances.empty
        else portfolio_status(result.return_pct, result.sharpe, benchmark)
    )
    log_portfolio_run(
        strategy_name=strategy_name,
        symbols=result.symbols,
        start=result.start,
        end=result.end,
        final_equity=result.final_equity,
        return_pct=result.return_pct,
        cagr_pct=result.cagr_pct,
        max_drawdown_pct=result.max_drawdown_pct,
        sharpe=result.sharpe,
        sortino=result.sortino,
        risk_free_rate=result.risk_free_rate,
        params=request.params if request else None,
        is_canonical=request is None or request.is_default(),
        benchmark_return_pct=benchmark,
        status=status,
    )
    return result


def run_pairs(strategy_name: str, request: RunRequest | None = None) -> PairsResult:
    """Counterpart to run_backtest() for strategies.swing.pairs_stat_arb
    names -- see strategies/registry.py's PAIRS_STRATEGY_NAMES. Same
    override shape as run_cross_sectional above -- note a larger custom
    symbol list means an O(n^2) cointegration search (every pair tested),
    so a big override runs noticeably slower than the 29-symbol default.
    Also not logged to engine/logging_db.py, for the same reason
    run_cross_sectional isn't: the schema doesn't describe a two-leg,
    discovered-pair strategy either."""
    start, end = daily_date_range()
    symbols = EQUITY_UNIVERSE
    if request:
        symbols = request.symbols or symbols
        start = request.start or start
        end = request.end or end
    rf = data_module.risk_free_rate(start, end)
    strategy = build_pairs_strategy(strategy_name)
    if request and request.params:
        strategy = apply_params(strategy, request.params)
    result = run_pairs_backtest(
        strategy_name, strategy, symbols, start, end, risk_free_rate=rf
    )
    # A run that found no cointegrated pair traded nothing -- no verdict
    # (status stays NULL -> the UI's old "Backtested" fallback), same
    # reasoning as run_cross_sectional's empty-rebalances guard above.
    trade_start = result.trading_window[0].date()
    trade_end = result.trading_window[1].date()
    benchmark = _benchmark_window_return(trade_start, trade_end)
    status = (
        None
        if result.pair is None
        else portfolio_status(result.return_pct, result.sharpe, benchmark)
    )
    log_portfolio_run(
        strategy_name=strategy_name,
        symbols=result.symbols,
        start=trade_start,
        end=trade_end,
        final_equity=result.final_equity,
        return_pct=result.return_pct,
        cagr_pct=result.cagr_pct,
        max_drawdown_pct=result.max_drawdown_pct,
        sharpe=result.sharpe,
        sortino=result.sortino,
        risk_free_rate=result.risk_free_rate,
        params=request.params if request else None,
        pair=(
            (result.pair.symbol_a, result.pair.symbol_b, result.pair.p_value)
            if result.pair else None
        ),
        is_canonical=request is None or request.is_default(),
        benchmark_return_pct=benchmark,
        status=status,
    )
    return result


def is_cross_sectional(strategy_name: str) -> bool:
    return strategy_name in CROSS_SECTIONAL_STRATEGY_NAMES


def is_pairs(strategy_name: str) -> bool:
    return strategy_name in PAIRS_STRATEGY_NAMES
