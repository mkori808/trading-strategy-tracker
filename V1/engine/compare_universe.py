"""One-off comparison: re-run the strategy book against MIDCAP_UNIVERSE
instead of EQUITY_UNIVERSE (the Dow), to test whether "nothing here clears
the shortlist bar" is a property of the strategies or a property of testing
exclusively on the most efficiently-priced large caps in the market. See
LESSONS.md and engine/universe.py's MIDCAP_UNIVERSE docstring for the
selection methodology and its disclosed limitations.

Deliberately does NOT write to engine/logging_db.py -- that DB backs the
webapp dashboard's "latest run" view, keyed only by strategy name with no
universe field, so logging a mid-cap run there would silently shadow the
Dow-universe result the dashboard shows. This is a standalone comparison,
not a replacement for the primary (logged) Dow-universe pipeline.

Run with: python -m engine.compare_universe
"""

from __future__ import annotations

from engine import data as data_module
from engine.backtest import run_strategy_backtest, run_strategy_backtest_seeded
from engine.cross_sectional import run_cross_sectional_backtest
from engine.overnight import run_overnight_backtest
from engine.pairs import run_pairs_backtest
from engine.universe import (
    INTRADAY_INTERVAL,
    MIDCAP_UNIVERSE,
    daily_date_range,
    intraday_date_range,
)
from strategies.registry import (
    ALL_STRATEGY_NAMES,
    CROSS_SECTIONAL_STRATEGY_NAMES,
    DAY_TRADING_STRATEGIES,
    OVERNIGHT_NAME,
    PAIRS_STRATEGY_NAMES,
    PEAD_NAME,
    SECTOR_ROTATION_NAME,
    SWING_TRADING_STRATEGIES_NO_BENCHMARK,
    build_cross_sectional_strategy,
    build_pairs_strategy,
)
from strategies.swing.overnight_hold import OvernightHold
from strategies.swing.pead import PostEarningsDrift

# Sector Rotation Play structurally uses SECTOR_UNIVERSE (sector ETFs vs.
# SPY, per strategies/swing/sector_rotation.py), not EQUITY_UNIVERSE --
# there's no meaningful "mid-cap version" of it without redefining what the
# strategy even means, so it's excluded from this comparison.
COMPARABLE_STRATEGY_NAMES = [n for n in ALL_STRATEGY_NAMES if n != SECTOR_ROTATION_NAME]


def run_one_on_midcap(strategy_name: str) -> dict:
    if strategy_name in DAY_TRADING_STRATEGIES:
        strategy = DAY_TRADING_STRATEGIES[strategy_name]
        start, end = intraday_date_range()
        rf = data_module.risk_free_rate(start, end)
        result = run_strategy_backtest(
            strategy_name, strategy, MIDCAP_UNIVERSE, INTRADAY_INTERVAL, start, end, risk_free_rate=rf
        )
        m = result.metrics
        return {
            "strategy": strategy_name, "kind": "per-symbol",
            "trades": m.trades_taken, "expectancy_r": m.expectancy_r,
            "profit_factor": m.profit_factor, "sharpe": m.sharpe,
            "alpha_pct": m.alpha_pct, "status": m.status,
        }

    if strategy_name in SWING_TRADING_STRATEGIES_NO_BENCHMARK:
        strategy = SWING_TRADING_STRATEGIES_NO_BENCHMARK[strategy_name]
        start, end = daily_date_range()
        rf = data_module.risk_free_rate(start, end)
        result = run_strategy_backtest(
            strategy_name, strategy, MIDCAP_UNIVERSE, "1d", start, end, risk_free_rate=rf
        )
        m = result.metrics
        return {
            "strategy": strategy_name, "kind": "per-symbol",
            "trades": m.trades_taken, "expectancy_r": m.expectancy_r,
            "profit_factor": m.profit_factor, "sharpe": m.sharpe,
            "alpha_pct": m.alpha_pct, "status": m.status,
        }

    if strategy_name in CROSS_SECTIONAL_STRATEGY_NAMES:
        start, end = daily_date_range()
        rf = data_module.risk_free_rate(start, end)
        strategy = build_cross_sectional_strategy(strategy_name, risk_free_rate=rf)
        result = run_cross_sectional_backtest(
            strategy_name, strategy, MIDCAP_UNIVERSE, start, end, risk_free_rate=rf
        )
        return {
            "strategy": strategy_name, "kind": "cross-sectional",
            "trades": len(result.rebalances), "return_pct": result.return_pct,
            "sharpe": result.sharpe, "cagr_pct": result.cagr_pct,
        }

    if strategy_name in PAIRS_STRATEGY_NAMES:
        start, end = daily_date_range()
        rf = data_module.risk_free_rate(start, end)
        strategy = build_pairs_strategy(strategy_name)
        result = run_pairs_backtest(
            strategy_name, strategy, MIDCAP_UNIVERSE, start, end, risk_free_rate=rf
        )
        return {
            "strategy": strategy_name, "kind": "pairs",
            "pair": f"{result.pair.symbol_a}/{result.pair.symbol_b}" if result.pair else None,
            "trades": len(result.trades), "return_pct": result.return_pct, "sharpe": result.sharpe,
        }

    if strategy_name == PEAD_NAME:
        start, end = daily_date_range()
        rf = data_module.risk_free_rate(start, end)

        def factory(symbol: str) -> PostEarningsDrift:
            return PostEarningsDrift(data_module.positive_earnings_dates(symbol))

        result = run_strategy_backtest_seeded(
            PEAD_NAME, factory, MIDCAP_UNIVERSE, "1d", start, end, risk_free_rate=rf
        )
        m = result.metrics
        return {
            "strategy": strategy_name, "kind": "per-symbol",
            "trades": m.trades_taken, "expectancy_r": m.expectancy_r,
            "profit_factor": m.profit_factor, "sharpe": m.sharpe,
            "alpha_pct": m.alpha_pct, "status": m.status,
        }

    if strategy_name == OVERNIGHT_NAME:
        start, end = daily_date_range()
        rf = data_module.risk_free_rate(start, end)
        result = run_overnight_backtest(
            OVERNIGHT_NAME, OvernightHold(), MIDCAP_UNIVERSE, start, end, risk_free_rate=rf
        )
        m = result.metrics
        return {
            "strategy": strategy_name, "kind": "per-symbol",
            "trades": m.trades_taken, "expectancy_r": m.expectancy_r,
            "profit_factor": m.profit_factor, "sharpe": m.sharpe,
            "alpha_pct": m.alpha_pct, "status": m.status,
        }

    raise ValueError(f"Unknown strategy {strategy_name!r}")


def main() -> None:
    for name in COMPARABLE_STRATEGY_NAMES:
        print(f"Running {name} on MIDCAP_UNIVERSE...", flush=True)
        result = run_one_on_midcap(name)
        print(f"  {result}", flush=True)


if __name__ == "__main__":
    main()
