"""Real-data run of the Weighted Voting Ensemble
(strategies/swing/ensemble_voting.py) against EQUITY_UNIVERSE (Dow-29) plus
SPY as the regime benchmark -- the same baseline universe every other
strategy in this project is judged against first (see engine/universe.py).

Deliberately not wired into strategies/registry.py or engine/logging_db.py
yet (see LESSONS.md, 2026-07-19 cont'd 2): this is the first real-data look
at a strategy that hasn't been evaluated against anything but synthetic
dummy data (engine/demo_ensemble.py) and unit tests so far. Run with:

    python -m engine.run_ensemble
"""

from __future__ import annotations

from engine import data as data_module
from engine.cross_sectional import run_cross_sectional_backtest
from engine.universe import EQUITY_UNIVERSE, daily_date_range
from strategies.swing.ensemble_voting import EnsembleWeightedVoting

REGIME_BENCHMARK = "SPY"
SLIPPAGE_BPS = 5.0  # CLAUDE.md: Alpaca is commission-free but spread/fill slippage still erodes edge
COMMISSION_BPS = 0.0


def main() -> None:
    symbols = [REGIME_BENCHMARK, *EQUITY_UNIVERSE]
    start, end = daily_date_range()
    rf = data_module.risk_free_rate(start, end)

    strategy = EnsembleWeightedVoting(
        risk_free_rate=rf,
        top_n=6,
        sharpe_window_days=63,
        max_position_weight=0.20,
    )

    result = run_cross_sectional_backtest(
        strategy.name,
        strategy,
        symbols,
        start,
        end,
        risk_free_rate=rf,
        rebalance_frequency="weekly",
        slippage_bps=SLIPPAGE_BPS,
        commission_bps=COMMISSION_BPS,
    )

    print(f"=== {result.strategy_name}: real-data run ===")
    print(f"Universe: SPY (regime only) + {len(EQUITY_UNIVERSE)} Dow names")
    print(f"Window: {result.start} -> {result.end}")
    print(f"Risk-free rate used: {rf:.2%}")
    print(f"Rebalance frequency: weekly  |  Slippage: {SLIPPAGE_BPS}bps  Commission: {COMMISSION_BPS}bps")
    print()

    n_rebal = len(result.rebalances)
    active = [r for r in result.rebalances.itertuples() if r.holdings]
    defensive = n_rebal - len(active)
    print(f"Rebalances: {n_rebal} total  |  {len(active)} ACTIVE (nonempty)  |  {defensive} DEFENSIVE (cash)")
    if n_rebal < 30:
        print("NOTE: fewer than 30 rebalances -- sample too small to treat this run's "
              "Sharpe/return as reliable, same 30-sample bar CLAUDE.md applies everywhere else "
              "(this is a rebalance count, not a trade count, but the small-sample caution "
              "applies just as much to a handful of weekly decisions).")
    print()

    cap_violations = sum(
        1 for r in result.rebalances.itertuples()
        if any(w > strategy.max_position_weight + 1e-9 for w in r.holdings.values())
    )
    sum_violations = sum(
        1 for r in result.rebalances.itertuples() if sum(r.holdings.values()) > 1.0 + 1e-9
    )
    print(f"Allocation sanity: {cap_violations} cap violations, {sum_violations} sum violations "
          f"across all {n_rebal} rebalances.")
    print()

    if active:
        print(f"Most recent ACTIVE rebalance ({active[-1].date.date()}):")
        for symbol, weight in sorted(active[-1].holdings.items(), key=lambda kv: -kv[1]):
            print(f"    {symbol:>5s}  {weight:6.2%}")
        print(f"    {'TOTAL':>5s}  {sum(active[-1].holdings.values()):6.2%}")
    else:
        print("No ACTIVE rebalance in this window -- regime stayed DEFENSIVE throughout, "
              "or no sub-strategy ever produced a positive composite score.")
    print()

    print(f"Final equity: ${result.final_equity:,.2f}  (return {result.return_pct:+.2f}%)")
    cagr = "n/a" if result.cagr_pct is None else f"{result.cagr_pct:.2f}%"
    sharpe = "n/a" if result.sharpe is None else f"{result.sharpe:.2f}"
    sortino = "n/a" if result.sortino is None else f"{result.sortino:.2f}"
    print(f"CAGR: {cagr}  Sharpe: {sharpe}  Sortino: {sortino}  Max DD: {result.max_drawdown_pct:.2f}%")
    print(f"Total slippage+commission paid: ${result.total_costs:,.2f}")

    print()
    shortlist = (result.sharpe is not None and result.sharpe > 0.5)
    print(f"Shortlist bar (Sharpe > 0.5, same threshold used everywhere else in this project): "
          f"{'CLEARS' if shortlist else 'does not clear'} ({sharpe}).")


if __name__ == "__main__":
    main()
