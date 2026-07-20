"""Robustness testing for Dual Momentum: does its Sharpe 0.41 / beats-SPY
result hold over a longer history, on other universes, and across market
environments the standard 5-year window doesn't cover -- or is it specific
to this one window and this one universe? See LESSONS.md's 2026-07-20
entries for why Dual Momentum is the strategy worth this scrutiny (the only
positive-Sharpe result in the project) and CLAUDE.md's rule that an
unstressed result is not a verdict.

Three batteries, run independently:

1. LONG HISTORY -- Dual Momentum on EQUITY_UNIVERSE from 2000-01-01 to
   today (26 of 29 symbols have real data back to 1999; V from 2008, CRM
   from 2004, DOW from 2019 -- DualMomentum.rebalance() already handles a
   symbol with insufficient trailing history by excluding it from that
   month's ranking, so no look-ahead risk, just a smaller effective
   universe in the earliest years). One continuous run, so the strategy's
   actual held positions carry through regime transitions realistically --
   not restarted with fresh capital at each boundary.

   CAVEAT, stated plainly: EQUITY_UNIVERSE is a point-in-time Dow-29
   snapshot valid for the Aug-2020-to-Feb-2024 reconstitution window (see
   engine/universe.py's docstring). Extending the test window back to 2000
   uses that same fixed 29-name roster on dates before it was assembled --
   AMGN/HON/CRM joined the real Dow in Aug 2020, so their presence in a
   year-2000 test isn't point-in-time-correct. This is a WEAKER bias than
   hand-picking today's winners (these are pre-existing large industrials
   chosen for 2021 Dow membership, not for how they performed from
   2000-2020), but it is real and disclosed here rather than hidden --
   same treatment CLAUDE.md already requires for MIDCAP_UNIVERSE/
   SMALL_CAP_UNIVERSE's own point-in-time gaps.

2. REGIME SLICES -- the same long-history equity curve, sliced into eight
   contiguous, pre-registered historical periods (dot-com bust, pre-GFC
   bull, GFC crash, post-GFC bull, COVID, 2021, 2022 bear, recent) so
   "does the edge persist across environments" has a real, falsifiable
   answer instead of an eyeballed equity-curve read. Sliced from the ONE
   continuous run above, not independently re-run per period (see #1).

3. UNIVERSE COMPARISON -- Dual Momentum on the standard 5-year canonical
   window, across EQUITY_UNIVERSE (baseline), MIDCAP_UNIVERSE,
   SMALL_CAP_UNIVERSE, and SECTOR_UNIVERSE -- the only universes in this
   project with full 5-year coverage (mid/small-cap history is too spotty
   before ~2015 for a long-history test; see engine/universe.py). Same
   window across arms so the universe is the only variable, matching
   engine/compare_filters.py's discipline.

4. REBALANCE FREQUENCY -- monthly (canonical) vs. weekly vs. daily (see
   engine/cross_sectional.py's RebalanceFrequency), each at both 0bps
   (matching Dual Momentum's canonical zero-cost convention, for direct
   comparison to the number already on the dashboard) and 5bps slippage
   (CLAUDE.md: "model realistic slippage even though Alpaca is
   commission-free" -- 0bps flatters a higher-turnover arm, since more
   frequent rebalancing trades more often; see engine/run_ensemble.py for
   the same 5bps convention used elsewhere). Directly targets the
   mechanism LESSONS.md's 2026-07-20 entries converged on: Dual
   Momentum's worst drawdowns come from shocks landing BETWEEN monthly
   rebalances (the April 2025 tariff shock; the 2008 GFC crash), so this
   tests whether a shorter cadence actually catches them -- not whether
   frequent trading helps in general.

Deliberately does NOT write to engine/logging_db.py -- same reasoning as
every other comparison script in this project: no field there means
"which robustness arm produced this," so writing here would silently
shadow the canonical Dual Momentum row the Compare tab shows.

Run with:
    python -m engine.compare_dual_momentum_robustness
    python -m engine.compare_dual_momentum_robustness --csv out.csv
"""

from __future__ import annotations

import argparse
from datetime import date

import pandas as pd

from engine import data as data_module
from engine.cross_sectional import CrossSectionalResult, run_cross_sectional_backtest
from engine.metrics import portfolio_status
from engine.portfolio import annualized_stats
from engine.universe import (
    EQUITY_UNIVERSE,
    MIDCAP_UNIVERSE,
    SECTOR_UNIVERSE,
    SMALL_CAP_UNIVERSE,
    daily_date_range,
)
from strategies.swing.dual_momentum import DualMomentum

LONG_HISTORY_START = date(2000, 1, 1)

# Contiguous, pre-registered regime slices spanning LONG_HISTORY_START to
# today -- chosen for being well-known, distinct market environments, not
# for making any one slice look better after seeing the results.
REGIME_SLICES: list[tuple[str, date, date]] = [
    ("Dot-com bust", date(2000, 1, 1), date(2002, 12, 31)),
    ("Pre-GFC bull", date(2003, 1, 1), date(2007, 12, 31)),
    ("GFC crash", date(2008, 1, 1), date(2009, 6, 30)),
    ("Post-GFC bull", date(2009, 7, 1), date(2019, 12, 31)),
    ("COVID crash + recovery", date(2020, 1, 1), date(2020, 12, 31)),
    ("2021 (blow-off top)", date(2021, 1, 1), date(2021, 12, 31)),
    ("2022 bear market", date(2022, 1, 1), date(2022, 12, 31)),
    ("2023-present", date(2023, 1, 1), date.today()),
]

UNIVERSE_ARMS: list[tuple[str, list[str]]] = [
    ("EQUITY_UNIVERSE (Dow-29, baseline)", EQUITY_UNIVERSE),
    ("MIDCAP_UNIVERSE", MIDCAP_UNIVERSE),
    ("SMALL_CAP_UNIVERSE", SMALL_CAP_UNIVERSE),
    ("SECTOR_UNIVERSE (11 sector SPDRs)", SECTOR_UNIVERSE),
]


def _spy_return_pct(start: date, end: date) -> float | None:
    bars = data_module.get_bars("SPY", "1d", start, end)
    if bars.empty or len(bars) < 2:
        return None
    return float((bars["Close"].iloc[-1] / bars["Close"].iloc[0] - 1.0) * 100.0)


def _fmt(value, spec: str) -> str:
    return "n/a" if value is None or pd.isna(value) else format(value, spec)


def _row_from_slice(label: str, eq: pd.Series, rf: float, benchmark: float | None) -> dict:
    """Stats for one slice of an equity curve -- same annualized_stats/
    max-drawdown recipe used everywhere else in this project (e.g.
    engine/overnight.py's _symbol_stats), applied to a sub-window."""
    if len(eq) < 2:
        return {
            "period": label, "trading_days": len(eq), "return_pct": None,
            "cagr_pct": None, "sharpe": None, "max_dd_pct": None,
            "benchmark_pct": benchmark, "status": "Not enough data in window",
        }
    cagr, sharpe, _sortino = annualized_stats(eq, rf)
    dd = float((eq / eq.cummax() - 1).min() * 100)
    ret = float((eq.iloc[-1] / eq.iloc[0] - 1) * 100)
    status = portfolio_status(ret, sharpe, benchmark)
    return {
        "period": label, "trading_days": len(eq), "return_pct": ret,
        "cagr_pct": cagr, "sharpe": sharpe, "max_dd_pct": dd,
        "benchmark_pct": benchmark, "status": status,
    }


def run_long_history_and_regimes() -> tuple[dict, list[dict]]:
    end = date.today()
    rf = data_module.risk_free_rate(LONG_HISTORY_START, end)
    strategy = DualMomentum(risk_free_rate=rf)
    result: CrossSectionalResult = run_cross_sectional_backtest(
        "Dual Momentum", strategy, EQUITY_UNIVERSE, LONG_HISTORY_START, end,
        risk_free_rate=rf,
    )
    full_benchmark = _spy_return_pct(LONG_HISTORY_START, end)
    full_row = _row_from_slice("FULL 2000-present", result.equity_curve, rf, full_benchmark)

    regime_rows = []
    tz = result.equity_curve.index.tz
    for label, start, end_ in REGIME_SLICES:
        eq_slice = result.equity_curve.loc[pd.Timestamp(start, tz=tz):pd.Timestamp(end_, tz=tz)]
        slice_rf = data_module.risk_free_rate(start, end_)
        slice_benchmark = _spy_return_pct(start, end_)
        regime_rows.append(_row_from_slice(label, eq_slice, slice_rf, slice_benchmark))

    return full_row, regime_rows


def run_universe_comparison() -> list[dict]:
    start, end = daily_date_range()  # the standard 5-year canonical window
    rf = data_module.risk_free_rate(start, end)
    benchmark = _spy_return_pct(start, end)
    rows = []
    for label, universe in UNIVERSE_ARMS:
        strategy = DualMomentum(risk_free_rate=rf)
        result = run_cross_sectional_backtest(
            "Dual Momentum", strategy, universe, start, end, risk_free_rate=rf
        )
        row = _row_from_slice(label, result.equity_curve, rf, benchmark)
        row["symbols_tested"] = len(universe)
        rows.append(row)
    return rows


REBALANCE_FREQUENCIES = ["monthly", "weekly", "daily"]
SLIPPAGE_ARMS = [0.0, 5.0]  # bps; 5.0 matches engine/run_ensemble.py's convention


def run_rebalance_frequency_comparison() -> list[dict]:
    start, end = daily_date_range()  # the standard 5-year canonical window
    rf = data_module.risk_free_rate(start, end)
    benchmark = _spy_return_pct(start, end)
    rows = []
    for frequency in REBALANCE_FREQUENCIES:
        for slippage_bps in SLIPPAGE_ARMS:
            strategy = DualMomentum(risk_free_rate=rf)
            result = run_cross_sectional_backtest(
                "Dual Momentum", strategy, EQUITY_UNIVERSE, start, end,
                risk_free_rate=rf, rebalance_frequency=frequency,
                slippage_bps=slippage_bps,
            )
            label = f"{frequency} @ {slippage_bps:.0f}bps"
            row = _row_from_slice(label, result.equity_curve, rf, benchmark)
            row["rebalances"] = len(result.rebalances)
            row["total_costs"] = result.total_costs
            rows.append(row)
    return rows


def print_regime_table(full_row: dict, regime_rows: list[dict]) -> None:
    print("\nLONG HISTORY (2000-01-01 -> today, EQUITY_UNIVERSE)")
    print("=" * 78)
    header = f"  {'period':<26}{'days':>6}{'return%':>10}{'cagr%':>8}{'sharpe':>8}{'maxDD%':>8}{'vsSPY%':>8}"
    print(header)
    for row in [full_row, *regime_rows]:
        print(
            f"  {row['period']:<26}{row['trading_days']:>6}"
            f"{_fmt(row['return_pct'], '+.1f'):>10}"
            f"{_fmt(row['cagr_pct'], '+.2f'):>8}"
            f"{_fmt(row['sharpe'], '.2f'):>8}"
            f"{_fmt(row['max_dd_pct'], '.1f'):>8}"
            f"{_fmt(row['benchmark_pct'], '+.1f'):>8}"
        )
    print()
    for row in [full_row, *regime_rows]:
        print(f"  {row['period']:<26}{row['status']}")


def print_universe_table(rows: list[dict]) -> None:
    print("\nUNIVERSE COMPARISON (standard 5-year window, same dates every arm)")
    print("=" * 78)
    header = f"  {'universe':<34}{'n':>4}{'return%':>10}{'sharpe':>8}{'maxDD%':>8}  status"
    print(header)
    for row in rows:
        print(
            f"  {row['period']:<34}{row['symbols_tested']:>4}"
            f"{_fmt(row['return_pct'], '+.1f'):>10}"
            f"{_fmt(row['sharpe'], '.2f'):>8}"
            f"{_fmt(row['max_dd_pct'], '.1f'):>8}  {row['status']}"
        )


LOOKBACK_ARMS = [63, 126, 189, 252]  # within DualMomentum's declared param bounds (63-378)


def run_lookback_frequency_grid() -> list[dict]:
    """Combines a shorter absolute/relative-momentum lookback with a faster
    rebalance cadence -- testing whether cont'd 7's diagnosis (the 252-day
    lookback, not rebalance frequency, is what's blind to a fast shock) is
    actually fixable, or whether a shorter lookback just adds noise-driven
    turnover without protecting the drawdown either. Reports each arm's
    worst-drawdown DATE too, to check directly whether a shorter lookback
    exits (or never enters) ahead of the April 2025 tariff-shock drawdown
    every longer-lookback arm in this file shares."""
    start, end = daily_date_range()
    rf = data_module.risk_free_rate(start, end)
    benchmark = _spy_return_pct(start, end)
    rows = []
    for lookback in LOOKBACK_ARMS:
        for frequency in REBALANCE_FREQUENCIES:
            for slippage_bps in SLIPPAGE_ARMS:
                strategy = DualMomentum(risk_free_rate=rf, lookback_trading_days=lookback)
                result = run_cross_sectional_backtest(
                    "Dual Momentum", strategy, EQUITY_UNIVERSE, start, end,
                    risk_free_rate=rf, rebalance_frequency=frequency,
                    slippage_bps=slippage_bps,
                )
                label = f"lb={lookback}d {frequency}@{slippage_bps:.0f}bps"
                row = _row_from_slice(label, result.equity_curve, rf, benchmark)
                dd = (result.equity_curve / result.equity_curve.cummax() - 1)
                row["lookback"] = lookback
                row["frequency"] = frequency
                row["slippage_bps"] = slippage_bps
                row["rebalances"] = len(result.rebalances)
                row["total_costs"] = result.total_costs
                row["dd_date"] = dd.idxmin().date().isoformat() if len(dd) else None
                rows.append(row)
    return rows


def print_lookback_frequency_table(rows: list[dict]) -> None:
    print("\nLOOKBACK x FREQUENCY GRID (standard 5-year window, EQUITY_UNIVERSE)")
    print("=" * 90)
    header = (
        f"  {'lookback':<10}{'freq':<10}{'slip':>6}{'return%':>10}{'sharpe':>8}"
        f"{'maxDD%':>8}{'dd date':>12}  status"
    )
    print(header)
    for row in rows:
        print(
            f"  {row['lookback']:<10}{row['frequency']:<10}{row['slippage_bps']:>4.0f}bp"
            f"{_fmt(row['return_pct'], '+.1f'):>10}"
            f"{_fmt(row['sharpe'], '.2f'):>8}"
            f"{_fmt(row['max_dd_pct'], '.1f'):>8}"
            f"{row['dd_date'] or 'n/a':>12}  {row['status']}"
        )


def print_frequency_table(rows: list[dict]) -> None:
    print("\nREBALANCE FREQUENCY (standard 5-year window, EQUITY_UNIVERSE)")
    print("=" * 78)
    header = (
        f"  {'frequency':<16}{'rebals':>8}{'return%':>10}{'sharpe':>8}"
        f"{'maxDD%':>8}{'costs$':>10}  status"
    )
    print(header)
    for row in rows:
        print(
            f"  {row['period']:<16}{row['rebalances']:>8}"
            f"{_fmt(row['return_pct'], '+.1f'):>10}"
            f"{_fmt(row['sharpe'], '.2f'):>8}"
            f"{_fmt(row['max_dd_pct'], '.1f'):>8}"
            f"{_fmt(row['total_costs'], ',.0f'):>10}  {row['status']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", help="Write both tables' rows to this path (one combined CSV)")
    parser.add_argument("--skip-long-history", action="store_true")
    parser.add_argument("--skip-universes", action="store_true")
    parser.add_argument("--skip-frequency", action="store_true")
    parser.add_argument("--skip-lookback-grid", action="store_true")
    args = parser.parse_args()

    all_rows = []
    if not args.skip_long_history:
        print("Running long-history + regime slices (one ~26-year backtest)...", flush=True)
        full_row, regime_rows = run_long_history_and_regimes()
        print_regime_table(full_row, regime_rows)
        all_rows.extend([{"battery": "long_history", **full_row}])
        all_rows.extend([{"battery": "regime_slice", **r} for r in regime_rows])

    if not args.skip_universes:
        print("\nRunning universe comparison (4 arms, standard 5-year window)...", flush=True)
        universe_rows = run_universe_comparison()
        print_universe_table(universe_rows)
        all_rows.extend([{"battery": "universe", **r} for r in universe_rows])

    if not args.skip_frequency:
        print("\nRunning rebalance-frequency comparison (6 arms, standard 5-year window)...", flush=True)
        frequency_rows = run_rebalance_frequency_comparison()
        print_frequency_table(frequency_rows)
        all_rows.extend([{"battery": "rebalance_frequency", **r} for r in frequency_rows])

    if not args.skip_lookback_grid:
        print("\nRunning lookback x frequency grid (24 arms, standard 5-year window)...", flush=True)
        grid_rows = run_lookback_frequency_grid()
        print_lookback_frequency_table(grid_rows)
        all_rows.extend([{"battery": "lookback_frequency_grid", **r} for r in grid_rows])

    if args.csv and all_rows:
        pd.DataFrame(all_rows).to_csv(args.csv, index=False)
        print(f"\nWrote {len(all_rows)} rows to {args.csv}")


if __name__ == "__main__":
    main()
