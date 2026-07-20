"""Insider Buying report driver: runs Variant A (Significant Single Buy) and
Variant B (Cluster Buy), prints the task's sanity checks, and saves the
three required CSVs (logs/insider_significant_buy.csv,
logs/insider_cluster_buy.csv, logs/insider_sector_breakdown.csv).

Does NOT write to engine/logging_db.py -- same reasoning as
engine/compare_universe.py / compare_filters.py / compare_dividend_hybrid.py:
this strategy is not yet registered (strategies/registry.py), the sector
filter is a documented no-op pending real input, and the universe currently
covered by the EDGAR feed is a 1-year Dow-29 window, not the strategy's
eventual broad universe -- none of that should be able to shadow a future
canonical run once one exists.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from engine import data as data_module
from engine.insider_buy import (
    InsiderBuyResult,
    SECTOR_FILTER_WARNING,
    VARIANT_A,
    VARIANT_B,
    run_insider_buy_backtest,
)
from engine.universe import EQUITY_UNIVERSE
from strategies.swing.insider_buy import InsiderBuy

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

CSV_PATHS = {
    VARIANT_A: LOGS_DIR / "insider_significant_buy.csv",
    VARIANT_B: LOGS_DIR / "insider_cluster_buy.csv",
}
SECTOR_BREAKDOWN_PATH = LOGS_DIR / "insider_sector_breakdown.csv"

MIN_SIGNALS_FOR_VERDICT = 30
OVERALL_WIN_RATE_SUSPECT_PCT = 65.0
BETA_EXPECTED_RANGE = (0.05, 0.15)


def _save_trades_csv(result: InsiderBuyResult, path: Path) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    result.trades.to_csv(path, index=False)


def _save_sector_breakdown_csv() -> None:
    """GICS sub-industry data has no source anywhere in this codebase yet
    (see strategies/swing/insider_buy.py's module docstring) -- this file
    is the honest placeholder the task's own file list requires, not a
    fabricated breakdown. Overwritten with a real per-sector win-rate table
    the moment a GICS data source and PRE_REGISTERED_SECTORS both exist."""
    LOGS_DIR.mkdir(exist_ok=True)
    pd.DataFrame(
        [{
            "status": "NOT_AVAILABLE",
            "reason": (
                "No GICS sub-industry data source exists in this codebase, and "
                "PRE_REGISTERED_SECTORS (strategies/swing/insider_buy.py) is None "
                "-- the Reddit post's sector list was never supplied. Per the "
                "task's own instruction, a symbol with no GICS data must be "
                "excluded rather than guessed; currently that is every symbol."
            ),
        }]
    ).to_csv(SECTOR_BREAKDOWN_PATH, index=False)


def _sanity_checks(result: InsiderBuyResult) -> list[str]:
    lines = []
    trades = result.trades
    closed = trades[trades["ExitReason"] != "still_held"] if not trades.empty else trades

    if len(closed) == 0:
        lines.append("No closed trades -- no sanity checks are evaluable.")
        return lines

    win_rate = float((closed["PnL"] > 0).mean() * 100)
    lines.append(f"Overall win rate: {win_rate:.1f}% ({len(closed)} closed trades)")
    if win_rate > OVERALL_WIN_RATE_SUSPECT_PCT:
        lines.append(
            f"  FLAG: win rate above {OVERALL_WIN_RATE_SUSPECT_PCT:.0f}% -- the "
            "original backtest found ~50%; this high a number without a sector "
            "filter applied warrants checking for look-ahead bias before trusting it."
        )

    lines.append(
        "Sector-filtered win rate vs. unfiltered: N/A -- sector filter is a "
        "documented no-op (see SECTOR_FILTER_WARNING). This comparison cannot "
        "be made until a real sector list and GICS data source both exist."
    )

    if result.beta is not None:
        lo, hi = BETA_EXPECTED_RANGE
        in_range = "within" if lo <= result.beta <= hi else "OUTSIDE"
        lines.append(f"Beta vs. SPY: {result.beta:.3f} ({in_range} the expected {lo}-{hi} range)")
    else:
        lines.append("Beta vs. SPY: not computable (insufficient overlapping data).")

    if result.n_signals < MIN_SIGNALS_FOR_VERDICT:
        lines.append(
            f"FLAG: only {result.n_signals} total signals generated (sector-filtered "
            f"universe would be fewer still) -- below the task's own {MIN_SIGNALS_FOR_VERDICT}-signal "
            "floor. Per the task's own instruction, widen the universe/window before "
            "concluding anything about edge; this run is a pipeline/logic check, not a verdict."
        )

    return lines


def _print_result(result: InsiderBuyResult) -> None:
    print(f"\n=== Variant {result.variant} ===")
    print(SECTOR_FILTER_WARNING)
    print(
        f"Signals generated: {result.n_signals}  |  Entries taken: {result.n_entries}  "
        f"(blocked -- regime: {result.n_blocked_by_regime}, liquidity: {result.n_blocked_by_liquidity}, "
        f"sector: {result.n_blocked_by_sector})"
    )
    m = result.metrics
    print(
        f"Trades: {m.trades_taken}  Win rate: {m.win_rate * 100:.1f}%  "
        f"Expectancy(R): {m.expectancy_r:.3f}  Profit factor: {m.profit_factor:.2f}  "
        f"Max DD: {m.max_drawdown_pct}  Sharpe: {m.sharpe}  CAGR%: {m.cagr_pct}  "
        f"Alpha%: {m.alpha_pct}  Beta: {m.beta}  Status: {m.status}"
    )
    if result.mean_filing_lag_days is not None:
        print(f"Mean filing lag across signals used (filed date - transaction date): {result.mean_filing_lag_days:.2f} days")
    if result.pct_exit_time_stop is not None:
        print(
            f"Exits: {result.pct_exit_time_stop:.1f}% time-stop (day-{5}), "
            f"{result.pct_exit_hard_stop:.1f}% hard-stop"
        )
    print("Sanity checks:")
    for line in _sanity_checks(result):
        print(f"  - {line}")
    for w in result.warnings[1:]:  # index 0 is always SECTOR_FILTER_WARNING, already printed
        print(f"  {w}")


def run_report(
    symbols: list[str] | None = None,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, InsiderBuyResult]:
    """Run both variants and save the required CSVs. Defaults to
    EQUITY_UNIVERSE and the EDGAR feed's currently-fetched window -- the
    caller widens either once more EDGAR data has been fetched (see
    LESSONS.md's "5yr fetch call" entry)."""
    symbols = symbols or EQUITY_UNIVERSE
    if start is None or end is None:
        from datetime import timedelta
        end = end or date.today()
        start = start or (end - timedelta(days=365))

    risk_free_rate = data_module.risk_free_rate(start, end)
    config = InsiderBuy()

    results = {}
    for variant in (VARIANT_A, VARIANT_B):
        result = run_insider_buy_backtest(
            config, symbols, start, end, variant, risk_free_rate=risk_free_rate
        )
        results[variant] = result
        _save_trades_csv(result, CSV_PATHS[variant])
        _print_result(result)

    _save_sector_breakdown_csv()
    print(f"\nSaved: {CSV_PATHS[VARIANT_A]}")
    print(f"Saved: {CSV_PATHS[VARIANT_B]}")
    print(f"Saved: {SECTOR_BREAKDOWN_PATH}")
    return results


if __name__ == "__main__":
    run_report()
