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
from datetime import date, timedelta
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
from engine.execution_calibration import spread_for_universe
from engine.filters import build_filter_factory
from engine.frozen_event import run_frozen_event_backtest
from engine.logging_db import log_portfolio_run, log_run
from engine.overnight import run_overnight_backtest
from engine.pairs import PairsResult, run_pairs_backtest
from engine.pit_all_stocks import load_eligibility_universe
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
from engine.universe_ledger import resolve_schedule
from engine.universe_registry import registered_universe
from strategies.params import apply_params, describe_params
from strategies.registry import (
    AVWAP_BREAKOUT_NAME,
    CROSS_SECTIONAL_STRATEGY_NAMES,
    DAY_TRADING_STRATEGIES,
    DUAL_MOMENTUM_PULLBACK_NAME,
    FROZEN_EVENT_STRATEGY_NAMES,
    OVERNIGHT_NAME,
    PAIRS_STRATEGY_NAMES,
    PEAD_NAME,
    SECTOR_ROTATION_NAME,
    SWING_TRADING_STRATEGIES_NO_BENCHMARK,
    UNAVAILABLE_RESEARCH_STRATEGIES,
    build_cross_sectional_strategy,
    build_frozen_event_strategy,
    build_pairs_strategy,
    build_swing_strategies,
)
from strategies.swing.avwap_breakout import AvwapBreakout
from strategies.swing.dual_momentum import DualMomentum
from strategies.swing.dual_momentum_pullback import DualMomentumPullbackSwing
from strategies.swing.overnight_hold import OvernightHold
from strategies.swing.pairs_stat_arb import PairsStatArb
from strategies.swing.pead import PostEarningsDrift
from strategies.swing.frozen_research import (
    High52WeekMomentum,
    MarketResidualMomentum,
    UnavailableResearchStrategy,
)

# Sector Rotation Play's universe is structural (sector ETFs ranked against
# SPY specifically) -- swapping it for an arbitrary symbol list changes what
# the strategy even means, the same reasoning engine/compare_universe.py
# already documents for excluding it from universe comparisons. Param and
# date overrides still work.
SYMBOL_OVERRIDE_DISALLOWED_NAMES = {
    SECTOR_ROTATION_NAME, *FROZEN_EVENT_STRATEGY_NAMES,
    "52-Week-High Momentum", "Market-Residual Momentum",
}

# Explicitly zero, not defaulted to zero -- the distinction that let the
# cross-sectional engine trade free for its whole life. Alpaca charges no
# commission on US stocks/ETFs (CLAUDE.md, "Broker: Alpaca"), so 0.0 is the
# CORRECT value here rather than a placeholder, and inventing a non-zero
# commission would model a broker this project doesn't use.
#
# Not modelled: SEC Section 31 fees (~0.3bps, sell side only) and FINRA TAF.
# Real but an order of magnitude below the 1-3bps spread already charged per
# symbol, and both are per-notional pass-throughs rather than commissions.
# Named here so a future reader sees the omission was decided, not missed.
ALPACA_COMMISSION_BPS = 0.0

# Selectable universe_id -> the key its date-effective roster is stored
# under in data/universe_membership.json (engine/universe_ledger.py). Only
# universes with a real, sourced AND price-complete ledger belong here; selecting one that
# isn't listed runs on that universe's JSON `symbols` list exactly as
# before -- e.g. sp400_current/sp600_current. The open S&P 500 membership
# history is deliberately not enabled here yet: its 1,207 historical ticker
# strings include hundreds with incomplete Yahoo tenure coverage and dozens
# with disjoint/reused identities. Membership reconstruction alone is not a
# survivor-free price dataset. That blocker is disclosed via the universe's
# applicableGates.pit_membership rather than silently faked here.
PIT_LEDGER_KEYS: dict[str, str] = {
    "dow_pit": "dow_jones_industrial_average",
}


@dataclass
class RunRequest:
    symbols: list[str] | None = None
    start: date | None = None
    end: date | None = None
    params: dict[str, Any] | None = None
    universe_id: str | None = None

    def is_default(self) -> bool:
        return not (self.symbols or self.start or self.end or self.params or self.universe_id)


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
    if strategy_name in FROZEN_EVENT_STRATEGY_NAMES or strategy_name in UNAVAILABLE_RESEARCH_STRATEGIES:
        start, end = daily_date_range()
        return "1d", EQUITY_UNIVERSE, start, end
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
    if strategy_name == "52-Week-High Momentum":
        return High52WeekMomentum
    if strategy_name == "Market-Residual Momentum":
        return MarketResidualMomentum
    if strategy_name in FROZEN_EVENT_STRATEGY_NAMES:
        return type(build_frozen_event_strategy(strategy_name))
    if strategy_name in UNAVAILABLE_RESEARCH_STRATEGIES:
        return UnavailableResearchStrategy
    if strategy_name == "Pairs / Stat Arb":
        return PairsStatArb
    if strategy_name == DUAL_MOMENTUM_PULLBACK_NAME:
        return DualMomentumPullbackSwing
    return type(SWING_TRADING_STRATEGIES_NO_BENCHMARK[strategy_name])


def build_strategy(strategy_name: str, start: date, end: date):
    if strategy_name in DAY_TRADING_STRATEGIES:
        return DAY_TRADING_STRATEGIES[strategy_name]
    if strategy_name == SECTOR_ROTATION_NAME:
        benchmark_bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", start, end)
        return build_swing_strategies(benchmark_bars)[strategy_name]
    return SWING_TRADING_STRATEGIES_NO_BENCHMARK[strategy_name]


def run_backtest(
    strategy_name: str, request: RunRequest | None = None, *, persist: bool = True
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
        return _run_pead(request, persist=persist)
    if strategy_name == OVERNIGHT_NAME:
        return _run_overnight(request, persist=persist)
    if strategy_name == AVWAP_BREAKOUT_NAME:
        return _run_avwap_breakout(request, persist=persist)
    if strategy_name == DUAL_MOMENTUM_PULLBACK_NAME:
        return _run_dual_momentum_pullback(request, persist=persist)
    if strategy_name in UNAVAILABLE_RESEARCH_STRATEGIES:
        raise ValueError(UNAVAILABLE_RESEARCH_STRATEGIES[strategy_name])
    if strategy_name in FROZEN_EVENT_STRATEGY_NAMES:
        return _run_frozen_event(strategy_name, request, persist=persist)

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
    fixed_spread = _fixed_universe_spread(request, symbols, start, end)
    result = run_strategy_backtest(
        strategy_name, strategy, symbols, interval, start, end, risk_free_rate=rf,
        **({"spread": fixed_spread} if fixed_spread is not None else {}),
    )
    if persist:
        result.run_id = log_run(
            result.metrics, symbols,
            params=request.params if request else None,
            is_canonical=request is None or request.is_default(),
            slippage_bps=mean_spread_bps(symbols, start, end, request.universe_id if request else None),
            commission_bps=ALPACA_COMMISSION_BPS,
            universe_id=request.universe_id if request else None,
        )
        write_excursion_report(strategy_name, result.excursions)
    return result


def _run_pead(request: RunRequest | None = None, *, persist: bool = True) -> StrategyBacktestResult:
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

    fixed_spread = _fixed_universe_spread(request, symbols, start, end)
    result = run_strategy_backtest_seeded(
        PEAD_NAME, factory, symbols, "1d", start, end, risk_free_rate=rf,
        **({"spread": fixed_spread} if fixed_spread is not None else {}),
    )
    if persist:
        result.run_id = log_run(
            result.metrics, symbols, params=params,
            is_canonical=request is None or request.is_default(),
            slippage_bps=mean_spread_bps(symbols, start, end, request.universe_id if request else None),
            commission_bps=ALPACA_COMMISSION_BPS,
            universe_id=request.universe_id if request else None,
        )
        write_excursion_report(PEAD_NAME, result.excursions)
    return result


def _run_dual_momentum_pullback(request: RunRequest | None = None, *, persist: bool = True) -> StrategyBacktestResult:
    """Run the short-term pullback strategy with the shared SPY regime gate.

    Each candidate receives a fresh instance because the injected benchmark
    and real risk-free rate are structural inputs.  The trade simulation is
    still the standard daily swing engine, so stops, targets, and the
    mean-reversion exit remain discrete per-symbol trades.
    """
    start, end = daily_date_range()
    symbols = EQUITY_UNIVERSE
    if request:
        symbols = request.symbols or symbols
        start = request.start or start
        end = request.end or end
    rf = data_module.risk_free_rate(start, end)
    benchmark_bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", start, end)
    params = request.params if request else None

    def factory(_symbol: str) -> DualMomentumPullbackSwing:
        strategy = DualMomentumPullbackSwing(
            benchmark_bars=benchmark_bars, risk_free_rate=rf,
        )
        return apply_params(strategy, params)

    fixed_spread = _fixed_universe_spread(request, symbols, start, end)
    result = run_strategy_backtest_seeded(
        DUAL_MOMENTUM_PULLBACK_NAME, factory, symbols, "1d", start, end,
        risk_free_rate=rf,
        **({"spread": fixed_spread} if fixed_spread is not None else {}),
    )
    if persist:
        result.run_id = log_run(
            result.metrics, symbols, params=params,
            is_canonical=request is None or request.is_default(),
            slippage_bps=mean_spread_bps(symbols, start, end, request.universe_id if request else None),
            commission_bps=ALPACA_COMMISSION_BPS,
            universe_id=request.universe_id if request else None,
        )
        write_excursion_report(DUAL_MOMENTUM_PULLBACK_NAME, result.excursions)
    return result


def _run_avwap_breakout(request: RunRequest | None = None, *, persist: bool = True) -> StrategyBacktestResult:
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
    fixed_spread = _fixed_universe_spread(request, symbols, start, end)
    result = run_strategy_backtest_seeded(
        AVWAP_BREAKOUT_NAME, strategy_for, symbols, "1d", start, end, risk_free_rate=rf,
        **({"spread": fixed_spread} if fixed_spread is not None else {}),
    )
    if persist:
        result.run_id = log_run(
            result.metrics, symbols, params=params,
            is_canonical=request is None or request.is_default(),
            slippage_bps=mean_spread_bps(symbols, start, end, request.universe_id if request else None),
            commission_bps=ALPACA_COMMISSION_BPS,
            universe_id=request.universe_id if request else None,
        )
        write_excursion_report(AVWAP_BREAKOUT_NAME, result.excursions)
    return result


def _run_overnight(request: RunRequest | None = None, *, persist: bool = True) -> StrategyBacktestResult:
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
    if persist:
        result.run_id = log_run(
            result.metrics, symbols,
            params=request.params if request else None,
            is_canonical=request is None or request.is_default(),
            slippage_bps=mean_spread_bps(symbols, start, end, request.universe_id if request else None),
            commission_bps=ALPACA_COMMISSION_BPS,
            universe_id=request.universe_id if request else None,
        )
    return result


def _run_frozen_event(
    strategy_name: str, request: RunRequest | None = None, *, persist: bool = True,
) -> StrategyBacktestResult:
    """Run a pre-registered event V1 on date-effective Dow membership."""
    if request and request.universe_id not in (None, "dow_pit"):
        raise ValueError(
            f"{strategy_name} V1 is pre-registered on dow_pit; changing universe "
            "would create an unregistered hypothesis."
        )
    start, end = daily_date_range()
    if request:
        start = request.start or start
        end = request.end or end
    schedule = resolve_schedule(
        "dow_jones_industrial_average", start, end, require_complete=False
    )
    if schedule is None:
        raise ValueError("Dow point-in-time membership ledger is unavailable")
    symbols = schedule.symbols
    strategy = apply_params(
        build_frozen_event_strategy(strategy_name), request.params if request else None
    )
    fetch_start = start - timedelta(days=430)
    bars_by_symbol: dict[str, Any] = {}
    price_failures: dict[str, str] = {}
    for symbol in symbols:
        try:
            bars = data_module.get_bars(symbol, "1d", fetch_start, end)
            if bars.empty:
                price_failures[symbol] = "no price data"
            else:
                bars_by_symbol[symbol] = bars
        except Exception as exc:
            price_failures[symbol] = f"{type(exc).__name__}: {exc}"
    market_bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", fetch_start, end)
    rf = data_module.risk_free_rate(start, end)
    result = run_frozen_event_backtest(
        strategy_name, strategy, symbols, bars_by_symbol, market_bars,
        start, end, rf, schedule.membership_at,
    )
    result.research_metadata["priceCoverageFailures"] = price_failures
    result.research_metadata["preRegisteredUniverse"] = "dow_pit"
    if persist:
        result.run_id = log_run(
            result.metrics, symbols,
            params=request.params if request else None,
            is_canonical=request is None or request.is_default(),
            slippage_bps=mean_spread_bps(
                list(bars_by_symbol), start, end, "dow_pit"
            ) if bars_by_symbol else None,
            commission_bps=ALPACA_COMMISSION_BPS,
            universe_id="dow_pit",
        )
    return result


def _measured_window(equity_curve) -> dict:
    """The window a portfolio result's equity curve actually covers.

    Distinct from the requested start/end for the same reason it is on the
    per-symbol path: what was asked for and what the data supported are
    different facts, and only the second describes the numbers.
    """
    if equity_curve is None or len(equity_curve) == 0:
        return {"measured_start": None, "measured_end": None}
    return {
        "measured_start": equity_curve.index[0].date(),
        "measured_end": equity_curve.index[-1].date(),
    }


def _fixed_universe_spread(
    request: RunRequest | None, symbols: list[str], start: date, end: date,
) -> float | None:
    if not request or not request.universe_id or not symbols:
        return None
    spreads = {
        spread_for_universe(symbol, start, end, request.universe_id) for symbol in symbols
    }
    return next(iter(spreads)) if len(spreads) == 1 else None


def mean_spread_bps(
    symbols: list[str], start: date, end: date, universe_id: str | None = None,
) -> float | None:
    """Universe-mean spread in bps, for the provenance columns on a logged run.

    The per-symbol engine charges engine/data.py:estimate_spread PER SYMBOL
    inside run_symbol_backtest, so a single stored number is necessarily a
    summary of a vector.

    DESCRIPTIVE, NOT REPRODUCIBLE. This value says "spread was charged, and
    roughly this much on average". It is NOT sufficient to recompute a stored
    run's costs: the real charge weights each symbol's own spread by that
    symbol's own traded notional, which the mean discards. Anyone reconstructing
    a historical result from this column will find a small unexplained gap and
    should not go looking for a bug. Storing the true detail would mean ~29
    per-symbol values per row; the mean was judged the right trade, and
    estimate_spread() remains deterministic given a symbol and window if the
    exact vector is ever needed.

    Recorded because NULL now means UNKNOWN. Leaving these NULL on rows that
    demonstrably charged spread would assert the same thing about them as
    about a pre-migration legacy row, which is exactly the ambiguity the
    provenance columns exist to remove. log_run and log_portfolio_run must
    populate the SAME non-null provenance set; they diverged once already,
    when the columns were added to one writer and not the other.
    """
    if not symbols:
        return None
    spreads = [spread_for_universe(sym, start, end, universe_id) for sym in symbols]
    return 10_000.0 * sum(spreads) / len(spreads)


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
    strategy_name: str, request: RunRequest | None = None, *, persist: bool = True
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
    selected_universe = (
        registered_universe(request.universe_id)
        if request and request.universe_id else None
    )
    pit_universe = None
    if selected_universe and selected_universe.membership_mode == "dynamic_pit_security_master":
        if request and request.start is None and selected_universe.coverage_start:
            start = date.fromisoformat(selected_universe.coverage_start)
        if request and request.end is None and selected_universe.coverage_end:
            end = date.fromisoformat(selected_universe.coverage_end)
        requested_params = request.params or {} if request else {}
        pit_universe = load_eligibility_universe(
            start, end,
            lookback_days=int(requested_params.get("lookback_trading_days", 189)),
            minimum_price=float(requested_params.get("pit_minimum_price", 5.0)),
            minimum_history_days=252,
            liquidity_lookback_days=int(requested_params.get("pit_liquidity_lookback_days", 60)),
            minimum_average_dollar_volume=float(
                requested_params.get("pit_minimum_average_dollar_volume", 1_000_000.0)
            ),
            minimum_market_cap=(
                float(requested_params["pit_minimum_market_cap"])
                if float(requested_params.get("pit_minimum_market_cap", 0.0)) > 0 else None
            ),
        )
        symbols = pit_universe.security_ids
    # PIT_LEDGER_KEYS maps a selectable universe_id to the date-effective
    # roster ledger it should resolve through (engine/universe_ledger.py,
    # data/universe_membership.json) instead of the flat `symbols` list its
    # own JSON file carries -- that JSON list is a fallback identity (used
    # for spread estimation and any universe with no ledger), not what a
    # cross-sectional rebalance actually ranks against once a ledger exists.
    # No explicit request.universe_id (the registered default call) falls
    # back to the original symbol-set heuristic, so that path stays
    # byte-identical to before this generalization.
    ledger_key = (
        PIT_LEDGER_KEYS.get(selected_universe.universe_id)
        if selected_universe
        else ("dow_jones_industrial_average" if set(symbols) == set(EQUITY_UNIVERSE) else None)
    )
    # require_complete=False: both ledgers honestly disclose historical
    # members with no fetchable price history (Dow: EK, old-GM, SBC
    # pre-rename, UTX, KFT, DWDP, WBA; S&P 500: see the ledger's own
    # per-record `unfetchableOrDelisted`). Strict mode would treat that
    # disclosure as disqualifying and silently fall back to a static
    # snapshot for EVERY run, including ones whose window extends before the
    # ledger's anchor -- exactly the membership-drift risk PIT resolution
    # exists to close. This is a no-op for a window entirely inside a
    # ledger's static tail and only changes behavior for a run whose
    # requested start predates that. See
    # engine/universe_ledger.py:audit_membership's docstring.
    schedule = (
        resolve_schedule(ledger_key, start, end, require_complete=False)
        if ledger_key else None
    )
    if schedule is not None:
        symbols = schedule.symbols
    rf = data_module.risk_free_rate(start, end)
    benchmark_bars = (
        data_module.get_bars(SECTOR_BENCHMARK, "1d", start - timedelta(days=430), end)
        if strategy_name == "Market-Residual Momentum" else None
    )
    strategy = build_cross_sectional_strategy(
        strategy_name, risk_free_rate=rf, benchmark_bars=benchmark_bars,
    )
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
    # Per-symbol spread, calibrated from reconciled paper fills once enough
    # observations exist and otherwise falling back to the historical estimator.
    # per-symbol strategy. Until now this call passed neither slippage nor
    # commission, so both defaulted to 0.0 and this engine backtested at zero
    # transaction cost -- while every strategy it was ranked against on the
    # same leaderboard paid estimate_spread(). Dual Momentum was the only
    # shortlisted row on that board and also the only cost-free one, on a
    # daily-rebalance configuration where free trading flatters most.
    # The normalized PIT bundle uses permanent IDs that no quote provider can
    # look up as tickers. Until the historical liquidity-tier callback is
    # applied below, use a conservative 10 bps all-in spread assumption; cost
    # stress reports also show 2x and 3x this charge.
    spread_by_symbol = (
        {s: 0.0010 for s in symbols}
        if pit_universe is not None
        else {
            s: spread_for_universe(s, start, end, request.universe_id if request else None)
            for s in symbols
        }
    )
    result = run_cross_sectional_backtest(
        strategy_name, strategy, symbols, start, end, risk_free_rate=rf,
        rebalance_frequency=rebalance_frequency,
        spread_by_symbol=spread_by_symbol,
        commission_bps=ALPACA_COMMISSION_BPS,
        bars_by_symbol=pit_universe.bars_by_security if pit_universe else None,
        membership_at=(pit_universe.membership_at if pit_universe else schedule.membership_at if schedule else None),
        universe_key=(selected_universe.universe_id if pit_universe else schedule.universe_key if schedule else ledger_key),
        # Once a real ledger resolves, membership_at() already excludes any
        # symbol that wasn't a genuine member at `start` -- the recent-IPO
        # warmup gap this flag exists to tolerate for a flat "today's full
        # roster" list doesn't arise anymore, so enforce InsufficientHistory
        # normally in that case rather than silently tolerating it.
        allow_incomplete_warmup=bool(
            selected_universe
            and selected_universe.membership_mode == "full_current_constituents_static_history"
            and schedule is None
        ),
    )
    if pit_universe is not None:
        result.pit_diagnostics = pit_universe.integrity_diagnostics(strategy.top_n)
        result.pit_diagnostics["capacity"] = pit_universe.capacity_diagnostics(
            result.rebalances, result.equity_curve,
            maximum_adv_participation_pct=float(strategy.pit_max_adv_participation_pct),
            liquidity_lookback_days=int(strategy.pit_liquidity_lookback_days),
        )
        result.security_labels = {
            security_id: pit_universe.ticker_at(security_id, end)
            for security_id in result.symbols
        }
        result.validation_bars = pit_universe.bars_by_security
        result.membership_at_runtime = pit_universe.membership_at
    # No verdict for a run that never rebalanced (no data) -- status stays
    # NULL and the UI keeps its old "Backtested" fallback.
    benchmark = _benchmark_window_return(result.start, result.end)
    status = (
        None
        if result.rebalances.empty
        else portfolio_status(
            result.return_pct, result.sharpe, benchmark,
            warmup_ok=not result.incomplete_warmup,
        )
    )
    if persist:
        result.run_id = log_portfolio_run(
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
            # Spread is estimated PER SYMBOL, so the row stores the universe mean
            # -- a single summary number for a vector. Recorded so a stored result
            # is never again ambiguous about what it charged; the per-symbol detail
            # is reproducible from estimate_spread() given the symbols and window,
            # both of which this row already carries.
            slippage_bps=(
                10_000.0 * sum(spread_by_symbol.values()) / len(spread_by_symbol)
                if spread_by_symbol else None
            ),
            commission_bps=ALPACA_COMMISSION_BPS,
            **_measured_window(result.equity_curve),
            universe_id=request.universe_id if request else None,
        )
    return result


def run_pairs(
    strategy_name: str, request: RunRequest | None = None, *, persist: bool = True
) -> PairsResult:
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
    spread_by_symbol = {
        s: spread_for_universe(s, start, end, request.universe_id if request else None)
        for s in symbols
    }
    result = run_pairs_backtest(
        strategy_name, strategy, symbols, start, end, risk_free_rate=rf,
        spread_by_symbol=spread_by_symbol,
        commission_bps=ALPACA_COMMISSION_BPS,
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
    if persist:
        result.run_id = log_portfolio_run(
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
            slippage_bps=(
                10_000.0 * sum(spread_by_symbol.values()) / len(spread_by_symbol)
                if spread_by_symbol else None
            ),
            commission_bps=ALPACA_COMMISSION_BPS,
            universe_id=request.universe_id if request else None,
        )
    return result


def is_cross_sectional(strategy_name: str) -> bool:
    return strategy_name in CROSS_SECTIONAL_STRATEGY_NAMES


def is_pairs(strategy_name: str) -> bool:
    return strategy_name in PAIRS_STRATEGY_NAMES
