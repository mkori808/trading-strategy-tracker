"""Sanity/demo script for the Weighted Voting Ensemble
(strategies/swing/ensemble_voting.py) against synthetic dummy pricing data --
no network access, no cached real data, per CLAUDE.md's "no network calls
inside the backtest engine itself." Demonstrates the full pipeline end to
end: macro regime gate, per-sub-strategy scoring, dynamic Sharpe weighting,
inverse-ATR risk-parity sizing, and execution (weekly rebalances, 5bps
slippage) through the existing engine/cross_sectional.py engine.

Run with: python -m engine.demo_ensemble
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd

from engine.cross_sectional import run_cross_sectional_backtest
from strategies.swing.ensemble_voting import EnsembleWeightedVoting

NY = "America/New_York"


def _dummy_bars(
    start_price: float,
    n: int,
    daily_drift: float,
    daily_vol: float,
    seed: int,
    crash_at: int | None = None,
    crash_depth: float = 0.30,
) -> pd.DataFrame:
    """A single synthetic OHLCV series: geometric random walk with drift,
    optionally gapped down sharply at `crash_at` (used on the SPY series to
    force a real DEFENSIVE regime transition partway through the demo, the
    same way tests/test_engine/test_ensemble.py does it deterministically).
    High/Low are widened slightly off Open/Close so ATR is well-defined."""
    rng = np.random.default_rng(seed)
    index = pd.bdate_range("2023-01-02", periods=n, tz=NY)
    rets = rng.normal(daily_drift, daily_vol, n)
    if crash_at is not None:
        rets[crash_at] -= crash_depth
    close = start_price * np.cumprod(1 + rets)
    open_ = np.roll(close, 1)
    open_[0] = start_price
    high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.01, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.01, n))
    volume = rng.uniform(1e6, 3e6, n)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=index
    )


def build_dummy_universe(n: int = 400) -> dict[str, pd.DataFrame]:
    """SPY (regime benchmark, crashes below its own 200-SMA around bar 300
    so the demo actually exercises the DEFENSIVE switch) plus 6 candidate
    symbols with varied drift/vol so different sub-strategies have something
    real to find (uptrends for Breakout/Dual Momentum, a choppy mean-
    reverting name for IBS, a laggard for contrast)."""
    return {
        "SPY": _dummy_bars(420, n, 0.0006, 0.009, seed=0, crash_at=300, crash_depth=0.12),
        "AAA": _dummy_bars(100, n, 0.0012, 0.015, seed=1),   # strong uptrend
        "BBB": _dummy_bars(80, n, 0.0009, 0.014, seed=2),    # moderate uptrend
        "CCC": _dummy_bars(60, n, 0.0000, 0.020, seed=3),    # choppy/flat -- IBS bait
        "DDD": _dummy_bars(150, n, -0.0004, 0.012, seed=4),  # laggard
        "EEE": _dummy_bars(45, n, 0.0010, 0.018, seed=5),    # volatile uptrend
        "FFF": _dummy_bars(200, n, 0.0007, 0.010, seed=6),   # steady uptrend, low vol
    }


def main() -> None:
    universe = build_dummy_universe()
    symbols = list(universe.keys())  # includes "SPY" -- required for the regime gate
    start, end = date(2023, 1, 1), date(2024, 8, 1)

    strategy = EnsembleWeightedVoting(
        risk_free_rate=0.03,
        top_n=6,
        sharpe_window_days=63,
        max_position_weight=0.20,
    )

    with patch(
        "engine.cross_sectional.data_module.get_bars",
        side_effect=lambda symbol, interval, start, end, **kwargs: universe[symbol],
    ), patch(
        "strategies.swing.ensemble_voting.data_module.positive_earnings_dates",
        return_value=[],  # no synthetic earnings calendar -- PEAD honestly contributes 0
    ):
        result = run_cross_sectional_backtest(
            "Weighted Voting Ensemble (demo)",
            strategy,
            symbols,
            start,
            end,
            rebalance_frequency="weekly",
            slippage_bps=5.0,
            commission_bps=0.0,
            risk_free_rate=0.03,
        )

    print(f"=== {result.strategy_name}: dummy-data sanity run ===")
    print(f"Window: {result.start} -> {result.end}  ({len(result.rebalances)} rebalances)")
    print()

    # --- Sanity check 1: regime switching is visible in the rebalance log ---
    defensive_count = sum(1 for h in result.rebalances["holdings"] if not h)
    active_count = len(result.rebalances) - defensive_count
    print(f"Regime switching: {active_count} ACTIVE rebalances (nonempty holdings), "
          f"{defensive_count} DEFENSIVE (fully cash) -- SPY's engineered crash at bar 300 "
          f"should show up as a run of DEFENSIVE rebalances partway through.")
    print()

    # --- Sanity check 2: allocation sums and per-position caps, every rebalance ---
    cap_violations = 0
    sum_violations = 0
    for row in result.rebalances.itertuples():
        holdings: dict[str, float] = row.holdings
        if any(w > strategy.max_position_weight + 1e-9 for w in holdings.values()):
            cap_violations += 1
        if sum(holdings.values()) > 1.0 + 1e-9:
            sum_violations += 1
    print(f"Allocation sanity across all {len(result.rebalances)} rebalances: "
          f"{cap_violations} cap violations (weight > {strategy.max_position_weight:.0%}), "
          f"{sum_violations} sum violations (total weight > 100%).")
    print()

    # --- Sanity check 3: weight normalization on a sample ACTIVE rebalance ---
    sample = next((r for r in result.rebalances.itertuples() if r.holdings), None)
    if sample is not None:
        print(f"Sample ACTIVE rebalance ({sample.date.date()}):")
        for symbol, weight in sorted(sample.holdings.items(), key=lambda kv: -kv[1]):
            print(f"    {symbol:>4s}  {weight:6.2%}")
        print(f"    {'TOTAL':>4s}  {sum(sample.holdings.values()):6.2%}")
    else:
        print("No ACTIVE rebalance produced a position on this particular dummy seed "
              "(synthetic data has no guaranteed edge) -- allocation-cap checks above "
              "still hold vacuously across an all-empty log.")
    print()

    print(f"Final equity: ${result.final_equity:,.2f}  (return {result.return_pct:+.2f}%)")
    print(f"CAGR: {result.cagr_pct if result.cagr_pct is None else f'{result.cagr_pct:.2f}%'}  "
          f"Sharpe: {result.sharpe if result.sharpe is None else f'{result.sharpe:.2f}'}  "
          f"Max DD: {result.max_drawdown_pct:.2f}%")
    print(f"Total slippage+commission paid: ${result.total_costs:,.2f}")


if __name__ == "__main__":
    main()
