"""Re-run every logged configuration onto the CURRENT metrics_version.

Why a re-run and not a column rewrite
-------------------------------------
The correction factor is not a constant. It depends on each series' own CAGR
and its window's risk-free rate -- measured at 0.712 for Dual Momentum and
0.665 for SPY over the same window -- so there is no multiplier that could be
applied to a stored number. engine/logging_db.py stores aggregate metrics and
no equity curves, so the curves have to be regenerated from data regardless.

Version-0 rows are NEVER modified or deleted. They are the honest record of
what the tool reported when LESSONS.md's "no strategy clears the shortlist"
conclusion was written; destroying them would make that conclusion look
unreasonable rather than correct-given-the-evidence. Every result here is
written as a NEW row carrying the current METRICS_VERSION. Runs at any
older version stay untouched as the record of what the tool reported then.

Refusals are recorded, not skipped
----------------------------------
A configuration whose window the universe cannot support raises
InsufficientHistory (see engine/cross_sectional.py). Those runs are logged
with STATUS_INVALID_WINDOW and the raise message, rather than omitted -- six
of the nine logged Dual Momentum experiments start in 2018, and DOW has no
price history before 2019-03-20 because it was spun out of DowDuPont that
year. Omitting them would leave a reader unable to distinguish "this run was
refused" from "the backfill crashed partway through".

Usage:
    python -m engine.backfill_metrics --dry-run
    python -m engine.backfill_metrics
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass
from datetime import date

from engine import logging_db
from engine.cross_sectional import InsufficientHistory
from engine.logging_db import ImplausibleMetrics
from engine.metrics import STATUS_INVALID_WINDOW
from engine.runner import RunRequest, is_cross_sectional, is_pairs, run_backtest


@dataclass
class _Config:
    """A distinct historical configuration worth reproducing."""

    strategy_name: str
    start: date | None
    end: date | None
    params: dict
    symbols: list[str]
    is_canonical: bool
    source_table: str


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def historical_configs() -> list[_Config]:
    """Every DISTINCT version-0 configuration, canonical first.

    Deduplicated on (strategy, window, params, canonical): re-running the same
    configuration twice would write two identical version-1 rows and make the
    new history look busier than the old one actually was.
    """
    conn = logging_db.get_connection()
    conn.row_factory = sqlite3.Row

    # Anything already reproduced at version 1 is skipped, so the backfill is
    # RESUMABLE. It re-runs every strategy against live data and takes many
    # minutes; without this, an interrupted run followed by a restart would
    # write a second version-1 row for every config it had already completed,
    # inflating the new history with duplicates that look like real re-runs.
    done: set[tuple] = set()
    for table in ("runs", "portfolio_runs"):
        for row in conn.execute(
            f"SELECT strategy_name, start_date, end_date, params, is_canonical "
            f"FROM {table} WHERE metrics_version = ?", (logging_db.METRICS_VERSION,)
        ):
            # A CANONICAL config is keyed on the strategy alone. Re-running it
            # uses today's registered defaults, so its logged window is the
            # CURRENT default -- which never equals the window on the old v0 row
            # it reproduces (defaults are relative to today). Keying canonical
            # rows on the window therefore never matches, and every resume
            # re-ran the entire board. There is exactly one canonical config per
            # strategy by definition, so the name is the whole key.
            if row["is_canonical"]:
                done.add((row["strategy_name"], True))
            else:
                done.add((
                    row["strategy_name"], row["start_date"], row["end_date"],
                    json.dumps(json.loads(row["params"] or "{}"), sort_keys=True),
                    False,
                ))

    seen: set[tuple] = set()
    configs: list[_Config] = []
    for table in ("runs", "portfolio_runs"):
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE metrics_version < ? ORDER BY is_canonical DESC, run_at",
            (logging_db.METRICS_VERSION,),
        ).fetchall()
        for row in rows:
            keys = row.keys()
            params = json.loads(row["params"] or "{}")
            symbols = json.loads(row["symbols"]) if "symbols" in keys and row["symbols"] else []
            key = (
                (row["strategy_name"], True)
                if row["is_canonical"]
                else (
                    row["strategy_name"], row["start_date"], row["end_date"],
                    json.dumps(params, sort_keys=True), False,
                )
            )
            if key in seen or key in done:
                continue
            seen.add(key)
            configs.append(
                _Config(
                    strategy_name=row["strategy_name"],
                    start=_parse_date(row["start_date"]),
                    end=_parse_date(row["end_date"]),
                    params=params,
                    symbols=symbols,
                    is_canonical=bool(row["is_canonical"]),
                    source_table=table,
                )
            )
    conn.close()
    return configs


def _log_refusal(config: _Config, message: str) -> None:
    """Record a configuration the engine refused to measure.

    Every numeric field is left NULL rather than zeroed -- a refused run has no
    return, and writing 0.0 would place it in the ordering as a flat result
    instead of outside it. engine/metrics.py:UNRANKABLE_STATUSES is what keeps
    it out of any leaderboard sort.
    """
    logging_db.log_portfolio_run(
        strategy_name=config.strategy_name,
        symbols=config.symbols,
        start=config.start,
        end=config.end,
        final_equity=None,
        return_pct=None,
        cagr_pct=None,
        max_drawdown_pct=None,
        sharpe=None,
        sortino=None,
        risk_free_rate=None,
        params=config.params,
        is_canonical=config.is_canonical,
        benchmark_return_pct=None,
        status=STATUS_INVALID_WINDOW,
        return_basis=logging_db.RETURN_BASIS_EXCESS,
    )
    print(f"    REFUSED -> {STATUS_INVALID_WINDOW}: {message.splitlines()[0]}")


def backfill(dry_run: bool = False) -> dict[str, int]:
    configs = historical_configs()
    tally = {"rerun": 0, "refused": 0, "implausible": 0, "failed": 0, "skipped": 0}

    print(f"{len(configs)} distinct version-0 configuration(s) to reproduce\n")
    for config in configs:
        label = "canonical" if config.is_canonical else "experiment"
        window = f"{config.start} -> {config.end}" if config.start else "default window"
        param_note = ", ".join(f"{k}={v}" for k, v in config.params.items()) or "registered defaults"
        print(f"  [{label}] {config.strategy_name}  ({window}; {param_note})")

        if dry_run:
            tally["skipped"] += 1
            continue

        # A CANONICAL row is by definition one whose symbols, window and params
        # were all the registered defaults, so reproducing it means calling with
        # NO overrides. Passing the stored start/end instead makes
        # RunRequest.is_default() false, and engine/runner.py derives
        # is_canonical from exactly that -- which silently relabelled every
        # backfilled canonical run as an experiment. The rows were correct and
        # invisible: latest_run_per_strategy() filters to canonical, so the
        # leaderboard showed nothing at all.
        #
        # The stored window is also the default AS IT WAS THEN. Defaults are
        # relative to today (engine/universe.py:daily_date_range), so pinning
        # the old dates would reproduce a stale window rather than the current
        # canonical configuration. Re-running with no request is both the
        # correct label and the correct window.
        request = None
        if not config.is_canonical:
            request = RunRequest(start=config.start, end=config.end, params=config.params or None)

        try:
            if is_cross_sectional(config.strategy_name):
                from engine.runner import run_cross_sectional

                run_cross_sectional(config.strategy_name, request)
            elif is_pairs(config.strategy_name):
                from engine.runner import run_pairs

                run_pairs(config.strategy_name, request)
            else:
                run_backtest(config.strategy_name, request)
            tally["rerun"] += 1
            print("    ok")
        except InsufficientHistory as exc:
            _log_refusal(config, str(exc))
            tally["refused"] += 1
        except ImplausibleMetrics as exc:
            # Distinct from BOTH a refusal and a crash: the run completed and
            # produced a number the plausibility floor rejected, which means a
            # measurement bug rather than a data or code failure. Nothing is
            # written, so the bad value cannot enter the version-1 set.
            tally["implausible"] += 1
            print(f"    IMPLAUSIBLE (not written): {exc}")
        except Exception as exc:  # noqa: BLE001 - one bad config must not abort the batch
            # Distinct from a refusal: this is an unexpected failure and is NOT
            # written to the database, so it can never be mistaken for a
            # measured result or for a deliberate refusal.
            tally["failed"] += 1
            print(f"    FAILED ({type(exc).__name__}): {exc}")

    return tally


def dedupe_version1() -> int:
    """Collapse version-1 rows that describe an identical measured run.

    Dedup at selection time keys on the REQUESTED configuration, but two
    different requests can produce the same measurement. Intraday strategies
    are the real case: the free data tier serves only ~60 days of 5-minute
    bars, so a request for 2024-08-05 and one for 2024-08-10 both resolve to
    the same available window and log identical rows. That is correct engine
    behaviour -- the window genuinely cannot be honoured -- but it leaves two
    indistinguishable rows claiming to be separate runs.

    Keyed on the RESULT (strategy, logged window, params, canonical), keeping
    the earliest. Returns how many were removed.
    """
    conn = logging_db.get_connection()
    conn.row_factory = sqlite3.Row
    removed = 0
    for table in ("runs", "portfolio_runs"):
        rows = conn.execute(
            f"SELECT id, strategy_name, start_date, end_date, params, is_canonical "
            f"FROM {table} WHERE metrics_version = ? ORDER BY id", (logging_db.METRICS_VERSION,)
        ).fetchall()
        seen: set[tuple] = set()
        for row in rows:
            key = (
                row["strategy_name"], row["start_date"], row["end_date"],
                json.dumps(json.loads(row["params"] or "{}"), sort_keys=True),
                bool(row["is_canonical"]),
            )
            if key in seen:
                with conn:
                    conn.execute(f"DELETE FROM {table} WHERE id = ?", (row["id"],))
                removed += 1
            else:
                seen.add(key)
    conn.close()
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="list configurations without running")
    args = parser.parse_args()

    tally = backfill(dry_run=args.dry_run)
    if not args.dry_run:
        collapsed = dedupe_version1()
        if collapsed:
            print(f"collapsed {collapsed} duplicate version-1 row(s) "
                  "(different requested windows, identical measured run)")
    print(
        f"\nre-run: {tally['rerun']}   refused: {tally['refused']}   "
        f"failed: {tally['failed']}   listed-only: {tally['skipped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
