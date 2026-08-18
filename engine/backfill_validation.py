"""Re-run every canonical config WITH its validation report attached.

`engine/validation.py` has always produced a full ValidationReport, but until
2026-08-11 the API computed it after logging and returned it live, so nothing
was persisted. Every historical row therefore reads "not recorded" -- which
means *predates persistence*, NOT *failed validation*.

Deleting on `validation_json IS NULL` without re-running first would discard 105
of 107 rows and the whole version-2 backfill. This script closes that gap: it
reproduces each canonical configuration, computes the same report the API
computes, and attaches it to the row it validated.

Mirrors api/main.py's three engine branches deliberately rather than importing
from it: the API layer owns HTTP concerns, and a batch job that needs a running
server to backfill its own database would be worse than a little duplication.

Usage:
    python -m engine.backfill_validation --dry-run
    python -m engine.backfill_validation
    python -m engine.backfill_validation --prune   # delete unvalidated after
"""

from __future__ import annotations

import argparse
import sqlite3

from datetime import date

from engine import logging_db
from engine.cross_sectional import InsufficientHistory
from engine.logging_db import ImplausibleMetrics, attach_standard_benchmark, attach_validation
from engine.portfolio import run_portfolio_backtest
from engine.runner import (
    is_cross_sectional,
    is_pairs,
    run_backtest,
    run_cross_sectional,
    run_pairs,
)
from engine.validation import validate_cross_sectional, validate_pairs, validate_standard
from strategies.registry import ALL_STRATEGY_NAMES


def unvalidated_counts() -> dict[str, tuple[int, int]]:
    """(total, unvalidated) per table."""
    conn = logging_db.get_connection()
    out = {}
    for table in ("runs", "portfolio_runs"):
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        missing = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE validation_json IS NULL"
        ).fetchone()[0]
        out[table] = (total, missing)
    conn.close()
    return out


def revalidate(name: str) -> str:
    """Re-run one strategy's canonical config and attach its report.

    Canonical means registered defaults, so it runs with NO RunRequest -- the
    same reasoning the metrics backfill uses. Passing stored dates would both
    mislabel the row as an experiment and pin a stale window.
    """
    if is_cross_sectional(name):
        result = run_cross_sectional(name)
        attach_validation("portfolio_runs", result.run_id,
                          validate_cross_sectional(result, applied_params=None).to_dict())
        return f"return {result.return_pct:+.1f}%"
    if is_pairs(name):
        result = run_pairs(name)
        attach_validation("portfolio_runs", result.run_id,
                          validate_pairs(result).to_dict())
        return f"return {result.return_pct:+.1f}%"

    result = run_backtest(name)
    portfolio = run_portfolio_backtest(
        result, risk_free_rate=result.metrics.risk_free_rate or 0.0
    )
    report = validate_standard(result, portfolio)
    report_payload = report.to_dict()
    attach_validation("runs", result.run_id, report_payload)
    # Mirrors api/main.py's live-run path: the benchmark row (Gap vs SPY, and
    # the exact window it was measured over -- see logging_db.py's
    # _STANDARD_BENCHMARK_COLUMNS) is written from the SAME beats_spy check
    # the validation report just produced, so a backfilled row and a live-run
    # row agree by construction rather than by two call sites staying in sync.
    beats_spy_details = next(
        (
            check.get("details", {})
            for dimension in report_payload.get("dimensions", [])
            for check in dimension.get("checks", [])
            if check.get("key") == "beats_spy"
        ),
        {},
    )
    spy_return = beats_spy_details.get("spyReturnPct")
    window_start = beats_spy_details.get("measuredStart")
    window_end = beats_spy_details.get("measuredEnd")
    attach_standard_benchmark(
        result.run_id,
        strategy_return_pct=portfolio.return_pct,
        benchmark_return_pct=spy_return,
        benchmark_window_start=date.fromisoformat(window_start) if window_start else None,
        benchmark_window_end=date.fromisoformat(window_end) if window_end else None,
    )
    return f"{result.metrics.trades_taken} trades"


def prune_unvalidated() -> int:
    """Delete rows with no stored validation report.

    Run ONLY after revalidation. Called first it would empty the database, since
    "no report" describes every row written before persistence existed.
    """
    conn = logging_db.get_connection()
    removed = 0
    with conn:
        for table in ("runs", "portfolio_runs"):
            removed += conn.execute(
                f"DELETE FROM {table} WHERE validation_json IS NULL"
            ).rowcount
    conn.close()
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prune", action="store_true",
                        help="delete unvalidated rows AFTER revalidating")
    args = parser.parse_args()

    before = unvalidated_counts()
    for table, (total, missing) in before.items():
        print(f"  {table}: {total} rows, {missing} unvalidated")
    print()

    if args.dry_run:
        print(f"would revalidate {len(ALL_STRATEGY_NAMES)} canonical configs")
        return 0

    ok = failed = refused = 0
    for name in ALL_STRATEGY_NAMES:
        try:
            detail = revalidate(name)
            ok += 1
            print(f"  ok       {name[:38]:38} {detail}")
        except InsufficientHistory as exc:
            refused += 1
            print(f"  REFUSED  {name[:38]:38} {str(exc).splitlines()[0][:50]}")
        except ImplausibleMetrics as exc:
            failed += 1
            print(f"  IMPLAUS  {name[:38]:38} {exc}")
        except Exception as exc:  # noqa: BLE001 - one bad strategy must not abort the batch
            failed += 1
            print(f"  FAILED   {name[:38]:38} {type(exc).__name__}: {str(exc)[:44]}")

    print(f"\nrevalidated {ok}   refused {refused}   failed {failed}")

    after = unvalidated_counts()
    for table, (total, missing) in after.items():
        print(f"  {table}: {total} rows, {missing} still unvalidated")

    if args.prune:
        removed = prune_unvalidated()
        print(f"\npruned {removed} unvalidated row(s)")
        for table, (total, missing) in unvalidated_counts().items():
            print(f"  {table}: {total} rows remaining, {missing} unvalidated")
    else:
        print("\nRe-run with --prune to delete the rows that remain unvalidated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
