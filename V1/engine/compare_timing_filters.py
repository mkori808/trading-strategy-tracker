"""Baseline vs. FOMC-day exclusion vs. SPY volatility-regime gating, on the
strategies where a timing gate could plausibly help: every swing strategy
whose canonical run shows positive expectancy, plus Overnight Hold (the one
current shortlist member, gated through engine/overnight.py's entry_allowed
hook since it doesn't run on the per-symbol engine).

Four arms per strategy, holding universe, window, interval, cost model and
risk-free rate identical so the gate is the only variable:
  baseline   -- the pipeline exactly as the canonical run executes it
  fomc       -- no new entries on FOMC meeting days (both days of each
                two-day meeting; see engine/timing_filters.py)
  vol calm   -- entries only when SPY 21-day realized vol is at or below
                its trailing-252-day 70th percentile
  vol storm  -- entries only above that percentile (the complement, so the
                two vol arms partition the same threshold rather than
                cherry-picking two overlapping windows)

Deliberately does NOT write to engine/logging_db.py -- same reasoning as
engine/compare_filters.py: the runs schema has no "which gate was active"
field, so a gated run would silently shadow the canonical dashboard result.

Run with:
    python -m engine.compare_timing_filters
    python -m engine.compare_timing_filters --strategy "Overnight Hold"
    python -m engine.compare_timing_filters --csv out.csv
"""

from __future__ import annotations

import argparse
from datetime import date

import pandas as pd

from engine import data as data_module
from engine import runner
from engine.backtest import (
    StrategyBacktestResult,
    run_strategy_backtest,
    run_strategy_backtest_seeded,
)
from engine.metrics import BacktestMetrics
from engine.overnight import run_overnight_backtest
from engine.timing_filters import (
    GateDiagnostics,
    build_fomc_gate,
    build_vol_gate,
    fomc_gate_predicate,
    run_gated_backtest,
    vol_gate_predicate,
)
from strategies.registry import OVERNIGHT_NAME, PEAD_NAME
from strategies.swing.overnight_hold import OvernightHold
from strategies.swing.pead import PostEarningsDrift

# Swing strategies whose CANONICAL run shows positive expectancy as of
# 2026-07-20 (see the Compare tab / LESSONS.md), plus Overnight Hold. A
# pre-registered list, fixed before running -- not re-derived from whatever
# today's leaderboard says, so re-running this script later still compares
# the same strategies.
TARGET_STRATEGIES = [
    "Pullback to 21 EMA",
    "Breakout from Consolidation",
    "9/21 EMA Crossover",
    "Oversold Bounce (RSI<30)",
    "Connors Mean Reversion (RSI2)",
    "Internal Bar Strength (IBS)",
    "Sector Rotation Play",
    PEAD_NAME,
    OVERNIGHT_NAME,
]

VOL_THRESHOLD = 0.7

ARMS = ["baseline", "fomc", "vol calm", "vol storm"]


def _build_strategy(strategy_name: str, start: date, end: date):
    """Same construction the live pipeline uses -- PEAD branches for its
    per-symbol earnings seeding, mirroring engine/compare_filters.py."""
    if strategy_name == PEAD_NAME:
        return lambda symbol: PostEarningsDrift(data_module.positive_earnings_dates(symbol))
    return runner.build_strategy(strategy_name, start, end)


def _run_arm_standard(
    strategy_name: str, arm: str, strategy, symbols: list[str], interval: str,
    start: date, end: date, rf: float,
) -> tuple[StrategyBacktestResult, GateDiagnostics | None]:
    if arm == "baseline":
        if callable(strategy):
            result = run_strategy_backtest_seeded(
                strategy_name, strategy, symbols, interval, start, end, risk_free_rate=rf
            )
        else:
            result = run_strategy_backtest(
                strategy_name, strategy, symbols, interval, start, end, risk_free_rate=rf
            )
        return result, None
    if arm == "fomc":
        strategy_for, diagnostics = build_fomc_gate(strategy, start, end)
    else:
        mode = "calm" if arm == "vol calm" else "storm"
        strategy_for, diagnostics = build_vol_gate(
            strategy, start, end, mode, VOL_THRESHOLD
        )
    result = run_gated_backtest(
        strategy_name, strategy_for, symbols, interval, start, end, risk_free_rate=rf
    )
    return result, diagnostics


def _run_arm_overnight(
    arm: str, symbols: list[str], start: date, end: date, rf: float
) -> tuple[StrategyBacktestResult, GateDiagnostics | None]:
    config = OvernightHold()
    if arm == "baseline":
        allowed_at, diagnostics = None, None
    elif arm == "fomc":
        allowed_at, diagnostics = fomc_gate_predicate(start, end)
    else:
        mode = "calm" if arm == "vol calm" else "storm"
        allowed_at, diagnostics, _ = vol_gate_predicate(start, end, mode, VOL_THRESHOLD)
    result = run_overnight_backtest(
        OVERNIGHT_NAME, config, symbols, start, end,
        risk_free_rate=rf, entry_allowed=allowed_at,
    )
    return result, diagnostics


def compare_one(strategy_name: str) -> list[dict]:
    interval, symbols, start, end = runner.run_config(strategy_name)
    rf = data_module.risk_free_rate(start, end)
    strategy = (
        None if strategy_name == OVERNIGHT_NAME
        else _build_strategy(strategy_name, start, end)
    )

    rows = []
    for arm in ARMS:
        if strategy_name == OVERNIGHT_NAME:
            result, diagnostics = _run_arm_overnight(arm, symbols, start, end, rf)
        else:
            result, diagnostics = _run_arm_standard(
                strategy_name, arm, strategy, symbols, interval, start, end, rf
            )
        m: BacktestMetrics = result.metrics
        rows.append({
            "strategy": strategy_name,
            "arm": arm,
            "trades": m.trades_taken,
            "win_rate": m.win_rate,
            "expectancy_r": m.expectancy_r,
            "profit_factor": m.profit_factor,
            "sharpe": m.sharpe,
            "alpha_pct": m.alpha_pct,
            "max_dd_pct": m.max_drawdown_pct,
            "exposure_pct": m.exposure_pct,
            "status": m.status,
            "gate_footprint": (
                diagnostics.window_days_blocked_pct if diagnostics else None
            ),
            "gate_summary": diagnostics.summary() if diagnostics else "",
        })
    return rows


def _fmt(value, spec: str) -> str:
    return "n/a" if value is None or pd.isna(value) else format(value, spec)


def print_strategy(rows: list[dict]) -> None:
    name = rows[0]["strategy"]
    print(f"\n{name}")
    print("-" * len(name))
    header = f"  {'arm':<11}{'trades':>7}{'win%':>7}{'expR':>8}{'PF':>7}{'sharpe':>8}{'alpha%':>8}{'maxDD%':>8}  status"
    print(header)
    for r in rows:
        print(
            f"  {r['arm']:<11}{r['trades']:>7}"
            f"{_fmt(r['win_rate'], '.1%'):>7}"
            f"{_fmt(r['expectancy_r'], '+.3f'):>8}"
            f"{_fmt(r['profit_factor'], '.2f'):>7}"
            f"{_fmt(r['sharpe'], '.2f'):>8}"
            f"{_fmt(r['alpha_pct'], '+.1f'):>8}"
            f"{_fmt(r['max_dd_pct'], '.1f'):>8}"
            f"  {r['status']}"
        )
    for r in rows:
        if r["gate_summary"]:
            print(f"  {r['arm']}: {r['gate_summary']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", help="Compare a single strategy by exact name")
    parser.add_argument("--csv", help="Write the comparison table to this path")
    args = parser.parse_args()

    names = [args.strategy] if args.strategy else TARGET_STRATEGIES
    all_rows: list[dict] = []
    for name in names:
        print(f"\nRunning {name} ({len(ARMS)} arms)...", flush=True)
        rows = compare_one(name)
        print_strategy(rows)
        all_rows.extend(rows)

    if args.csv and all_rows:
        pd.DataFrame(all_rows).to_csv(args.csv, index=False)
        print(f"\nWrote {len(all_rows)} rows to {args.csv}")


if __name__ == "__main__":
    main()
