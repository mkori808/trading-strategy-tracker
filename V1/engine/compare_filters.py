"""Filtered vs. unfiltered comparison: what do the two pre-filters actually
add or remove?

Runs every per-symbol strategy twice -- once as the existing pipeline runs it
today, once with the market-regime gate and the Minervini Trend Template in
front of its entry rule -- holding the symbol universe, the date range, the
interval, the cost model (engine/data.py:estimate_spread), the risk-free rate
and the metrics identical across both. The filters are the only variable.

Deliberately does NOT write to engine/logging_db.py, for the same reason
engine/compare_universe.py doesn't: that DB is keyed on strategy name with no
"which filters were active" field, so logging a filtered run there would
silently shadow the unfiltered result the dashboard shows. This is a
standalone comparison, not a replacement for the primary logged pipeline.

Run with:
    python -m engine.compare_filters              # swing strategies (daily bars)
    python -m engine.compare_filters --all        # + day-trading strategies
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date

import pandas as pd

from engine import data as data_module
from engine import regime as regime_module
from engine import runner
from engine import trend_template
from engine.backtest import StrategyBacktestResult, run_strategy_backtest
from engine.filters import FilterDiagnostics, run_filtered_backtest
from engine.metrics import BacktestMetrics
from engine.universe import EQUITY_UNIVERSE, daily_date_range
from strategies.registry import (
    DAY_TRADING_STRATEGIES,
    PEAD_NAME,
    SECTOR_ROTATION_NAME,
    SWING_TRADING_STRATEGIES_NO_BENCHMARK,
)
from strategies.swing.pead import PostEarningsDrift

# Strategies excluded from this comparison, and why. Each runs on an engine
# that isn't the per-symbol one the FilteredStrategy wrapper plugs into --
# gating them would mean re-expressing the filters for a different execution
# shape, which is a separate piece of work, not a flag.
EXCLUDED: dict[str, str] = {
    "Overnight Hold": "runs on the close->open engine (engine/overnight.py)",
    "Dual Momentum": "cross-sectional -- ranks the whole universe, no per-symbol entry_signal",
    "Pairs / Stat Arb": "two legs traded as one position (engine/pairs.py)",
}


@dataclass
class FilterComparison:
    strategy_name: str
    interval: str
    unfiltered: BacktestMetrics
    filtered: BacktestMetrics
    diagnostics: FilterDiagnostics

    def row(self) -> dict:
        u, f = self.unfiltered, self.filtered
        return {
            "strategy": self.strategy_name,
            "interval": self.interval,
            "trades_before": u.trades_taken,
            "trades_after": f.trades_taken,
            "trades_removed_pct": _pct_removed(u.trades_taken, f.trades_taken),
            "expectancy_before": u.expectancy_r,
            "expectancy_after": f.expectancy_r,
            "win_rate_before": u.win_rate,
            "win_rate_after": f.win_rate,
            "profit_factor_before": u.profit_factor,
            "profit_factor_after": f.profit_factor,
            "sharpe_before": u.sharpe,
            "sharpe_after": f.sharpe,
            "alpha_before": u.alpha_pct,
            "alpha_after": f.alpha_pct,
            "max_dd_before": u.max_drawdown_pct,
            "max_dd_after": f.max_drawdown_pct,
            "status_before": u.status,
            "status_after": f.status,
        }


def _pct_removed(before: int, after: int) -> float | None:
    return None if before == 0 else (before - after) / before


def _build_strategy(strategy_name: str, start: date, end: date):
    """The strategy (or per-symbol factory) to compare.

    Delegates to engine/runner.py:build_strategy so both arms of this
    comparison are constructed exactly the way the live pipeline constructs
    them -- PEAD is the one case runner builds inline rather than through
    build_strategy (it needs per-symbol earnings seeding), so it branches
    here the same way runner._run_pead does.
    """
    if strategy_name == PEAD_NAME:
        return lambda symbol: PostEarningsDrift(data_module.positive_earnings_dates(symbol))
    return runner.build_strategy(strategy_name, start, end)


def _unfiltered(
    strategy_name: str, strategy, symbols: list[str], interval: str,
    start: date, end: date, rf: float,
) -> StrategyBacktestResult:
    # A Strategy instance isn't callable; a per-symbol factory is.
    if callable(strategy):
        from engine.backtest import run_strategy_backtest_seeded

        return run_strategy_backtest_seeded(
            strategy_name, strategy, symbols, interval, start, end, risk_free_rate=rf
        )
    return run_strategy_backtest(
        strategy_name, strategy, symbols, interval, start, end, risk_free_rate=rf
    )


def compare_one(strategy_name: str) -> FilterComparison:
    interval, symbols, start, end = runner.run_config(strategy_name)
    strategy = _build_strategy(strategy_name, start, end)

    # One risk-free rate, one universe, one window, one cost model -- resolved
    # once and handed to BOTH runs so the filters are the only difference.
    rf = data_module.risk_free_rate(start, end)

    unfiltered = _unfiltered(strategy_name, strategy, symbols, interval, start, end, rf)
    filtered, diagnostics = run_filtered_backtest(
        strategy_name, strategy, symbols, interval, start, end, risk_free_rate=rf
    )
    return FilterComparison(
        strategy_name, interval, unfiltered.metrics, filtered.metrics, diagnostics
    )


def comparable_strategies(include_day_trading: bool) -> list[str]:
    names = [
        *SWING_TRADING_STRATEGIES_NO_BENCHMARK,
        SECTOR_ROTATION_NAME,
        PEAD_NAME,
    ]
    if include_day_trading:
        names = [*DAY_TRADING_STRATEGIES, *names]
    return names


def _format_number(value, spec: str) -> str:
    return "n/a" if value is None or pd.isna(value) else format(value, spec)


def print_comparison(comparison: FilterComparison) -> None:
    u, f = comparison.unfiltered, comparison.filtered
    print(f"\n{comparison.strategy_name}  [{comparison.interval}]")
    print("-" * (len(comparison.strategy_name) + len(comparison.interval) + 4))
    print(f"  {'metric':<18}{'unfiltered':>14}{'filtered':>14}")
    rows = [
        ("Trades Taken", f"{u.trades_taken}", f"{f.trades_taken}"),
        ("Win Rate", f"{u.win_rate:.1%}", f"{f.win_rate:.1%}"),
        ("Expectancy (R)", f"{u.expectancy_r:+.3f}", f"{f.expectancy_r:+.3f}"),
        ("Profit Factor", _format_number(u.profit_factor, ".2f"), _format_number(f.profit_factor, ".2f")),
        ("Sharpe", _format_number(u.sharpe, ".2f"), _format_number(f.sharpe, ".2f")),
        ("Alpha %", _format_number(u.alpha_pct, "+.1f"), _format_number(f.alpha_pct, "+.1f")),
        ("Max Drawdown %", _format_number(u.max_drawdown_pct, ".1f"), _format_number(f.max_drawdown_pct, ".1f")),
    ]
    for label, before, after in rows:
        print(f"  {label:<18}{before:>14}{after:>14}")
    print(f"  {'Status':<18}{u.status}")
    print(f"  {'':<18}-> {f.status}")
    print(f"  {comparison.diagnostics.summary()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all", action="store_true",
        help="Also compare day-trading strategies (5-minute bars, much slower). "
        "The filters are daily-bar, swing-oriented by construction; on intraday "
        "strategies they resolve to the PRIOR session's daily values.",
    )
    parser.add_argument("--csv", help="Write the comparison table to this path")
    parser.add_argument("--strategy", help="Compare a single strategy by exact name")
    args = parser.parse_args()

    names = [args.strategy] if args.strategy else comparable_strategies(args.all)

    start, end = daily_date_range()
    spy = regime_module.load_spy_bars(start, end)
    labels = regime_module.regime_series(spy)
    window_labels = labels.loc[labels.index >= pd.Timestamp(start, tz=labels.index.tz)]
    print("=" * 68)
    print(f"Filter comparison -- {start} to {end}, universe: {len(EQUITY_UNIVERSE)} names")
    print(regime_module.format_distribution(window_labels))
    print("=" * 68)

    rows = []
    for name in names:
        if name in EXCLUDED:
            print(f"\nSkipping {name} -- {EXCLUDED[name]}")
            continue
        print(f"\nRunning {name} (unfiltered + filtered)...", flush=True)
        comparison = compare_one(name)
        print_comparison(comparison)
        if not comparison.diagnostics.scan_summary.empty:
            print(f"  {trend_template.format_scan_summary(comparison.diagnostics.scan_summary)}")
        rows.append(comparison.row())

    if args.csv and rows:
        pd.DataFrame(rows).to_csv(args.csv, index=False)
        print(f"\nWrote {len(rows)} rows to {args.csv}")


if __name__ == "__main__":
    main()
