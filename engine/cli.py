"""v0 console entry point: run a backtest and print metrics.

Usage:
    python -m engine.cli --strategy "Opening Range Breakout (ORB)"
    python -m engine.cli --all
"""

from __future__ import annotations

import argparse

from engine.backtest import StrategyBacktestResult
from engine.cross_sectional import CrossSectionalResult
from engine.metrics import BacktestMetrics
from engine.pairs import PairsResult
from engine.portfolio import PortfolioResult, run_portfolio_backtest
from engine.runner import (
    is_cross_sectional,
    is_pairs,
    run_backtest,
    run_cross_sectional,
    run_pairs,
)
from strategies.registry import ALL_STRATEGY_NAMES


def run_one(strategy_name: str) -> StrategyBacktestResult:
    return run_backtest(strategy_name)


def _print_metrics(m: BacktestMetrics) -> None:
    print(f"\n{m.strategy_name}")
    print("-" * len(m.strategy_name))
    print(f"  Date range:      {m.start} to {m.end}")
    print(f"  Trades Taken:    {m.trades_taken}")
    print(f"  Wins / Losses:   {m.wins} / {m.losses}")
    print(f"  Win Rate:        {m.win_rate:.1%}")
    print(f"  Avg Win (R):     {m.avg_win_r:.2f}")
    print(f"  Avg Loss (R):    {m.avg_loss_r:.2f}")
    print(f"  Expectancy (R):  {m.expectancy_r:+.3f}")
    pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
    print(f"  Profit Factor:   {pf}")
    if m.max_drawdown_pct is not None:
        print(f"  Max Drawdown:    {m.max_drawdown_pct:.1f}%")
    if m.sharpe is not None:
        rf_note = f" (rf={m.risk_free_rate:.1%})" if m.risk_free_rate is not None else ""
        print(f"  Sharpe:          {m.sharpe:.2f}{rf_note}")
    if m.sortino is not None:
        print(f"  Sortino:         {m.sortino:.2f}")
    if m.alpha_pct is not None:
        print(f"  Alpha:           {m.alpha_pct:+.1f}% vs. buy & hold")
    if m.beta is not None:
        print(f"  Beta:            {m.beta:.3f}")
    if m.cagr_pct is not None:
        print(f"  CAGR:            {m.cagr_pct:.2f}%")
    if m.exposure_pct is not None:
        print(f"  Exposure:        {m.exposure_pct:.1f}% of time in a position")
    print(f"  Status:          {m.status}")


def _print_portfolio(p: PortfolioResult) -> None:
    print("  --- Portfolio (shared capital, real concurrent-position simulation) ---")
    print(f"  Max concurrent:  {p.max_concurrent_positions} positions")
    print(f"  Trades taken:    {len(p.trades)}  (skipped for capacity: {p.skipped_for_capacity})")
    print(f"  Final Equity:    ${p.final_equity:,.2f}  ({p.return_pct:+.1f}%)")
    if p.cagr_pct is not None:
        print(f"  CAGR:            {p.cagr_pct:.2f}%")
    print(f"  Max Drawdown:    {p.max_drawdown_pct:.1f}%  (real, correlation-aware -- not max-of-independents)")
    if p.sharpe is not None:
        print(f"  Sharpe:          {p.sharpe:.2f} (rf={p.risk_free_rate:.1%})")
    if p.sortino is not None:
        print(f"  Sortino:         {p.sortino:.2f}")


def _print_cross_sectional(r: CrossSectionalResult) -> None:
    print(f"\n{r.strategy_name}")
    print("-" * len(r.strategy_name))
    print(f"  Date range:      {r.start} to {r.end}")
    print(f"  Rebalances:      {len(r.rebalances)}")
    print(f"  Final Equity:    ${r.final_equity:,.2f}  ({r.return_pct:+.1f}%)")
    if r.cagr_pct is not None:
        print(f"  CAGR:            {r.cagr_pct:.2f}%")
    print(f"  Max Drawdown:    {r.max_drawdown_pct:.1f}%")
    if r.sharpe is not None:
        print(f"  Sharpe:          {r.sharpe:.2f} (rf={r.risk_free_rate:.1%})")
    if r.sortino is not None:
        print(f"  Sortino:         {r.sortino:.2f}")
    if not r.rebalances.empty:
        last = r.rebalances.iloc[-1]
        print(f"  Last holdings:   {last['holdings']}  ({last['date'].date()})")


def _print_pairs(r: PairsResult) -> None:
    print(f"\n{r.strategy_name}")
    print("-" * len(r.strategy_name))
    if r.pair is None:
        print("  No cointegrated pair found in the training window -- fully in cash.")
        return
    print(f"  Pair:            {r.pair.symbol_a} / {r.pair.symbol_b}  (p={r.pair.p_value:.4f})")
    print(f"  Training window: {r.training_window[0].date()} to {r.training_window[1].date()}")
    print(f"  Trading window:  {r.trading_window[0].date()} to {r.trading_window[1].date()}")
    print(f"  Trades taken:    {len(r.trades)}")
    print(f"  Final Equity:    ${r.final_equity:,.2f}  ({r.return_pct:+.1f}%)")
    if r.cagr_pct is not None:
        print(f"  CAGR:            {r.cagr_pct:.2f}%")
    print(f"  Max Drawdown:    {r.max_drawdown_pct:.1f}%")
    if r.sharpe is not None:
        print(f"  Sharpe:          {r.sharpe:.2f} (rf={r.risk_free_rate:.1%})")
    if r.sortino is not None:
        print(f"  Sortino:         {r.sortino:.2f}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", help="Exact strategy name from strategy_tracker.xlsx")
    parser.add_argument("--all", action="store_true", help="Run every registered strategy")
    parser.add_argument(
        "--portfolio", action="store_true",
        help="Also simulate a real shared-capital portfolio across the universe "
        "(correlation-aware drawdown, capital-constrained concurrent positions)",
    )
    args = parser.parse_args()

    def _run_and_print(name: str) -> None:
        if is_cross_sectional(name):
            _print_cross_sectional(run_cross_sectional(name))
            return
        if is_pairs(name):
            _print_pairs(run_pairs(name))
            return
        result = run_one(name)
        _print_metrics(result.metrics)
        if args.portfolio:
            portfolio = run_portfolio_backtest(
                result, risk_free_rate=result.metrics.risk_free_rate or 0.0
            )
            _print_portfolio(portfolio)

    if args.all:
        for name in ALL_STRATEGY_NAMES:
            print(f"Running {name}...")
            _run_and_print(name)
        return

    if not args.strategy:
        parser.error("--strategy NAME or --all is required")
    if args.strategy not in ALL_STRATEGY_NAMES:
        parser.error(f"Unknown strategy {args.strategy!r}. Choices: {ALL_STRATEGY_NAMES}")
    _run_and_print(args.strategy)


if __name__ == "__main__":
    main()
