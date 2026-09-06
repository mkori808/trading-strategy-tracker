"""Rebuild run history from current defaults through the governed UI pipeline.

This is intentionally different from the legacy metrics backfill.  A stored
result produced by an obsolete metric convention cannot acquire valid modern
evidence after the fact.  This command removes only research/backtest history,
then submits one current-default run per registered strategy through the same
preflight, preregistration, backtest, and validation path used by the UI.

Usage:
    python -m engine.rebuild_validated_history --dry-run
    python -m engine.rebuild_validated_history
    python -m engine.rebuild_validated_history --resume
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from api.main import start_validation_job, validation_job
from engine import logging_db
from engine.runner import is_cross_sectional, is_pairs
from engine.validation import VALIDATION_REPORT_VERSION
from strategies.registry import ALL_STRATEGY_NAMES


# Child tables precede their parents.  Execution/fill databases and market-data
# caches are separate and deliberately outside this reset.
RESEARCH_TABLES = (
    "forward_observations",
    "forward_experiments",
    "research_equity_curves",
    "research_experiments",
    "portfolio_runs",
    "runs",
)


def _engine(strategy_name: str) -> str:
    if is_cross_sectional(strategy_name):
        return "cross_sectional"
    if is_pairs(strategy_name):
        return "pairs"
    return "standard"


def history_counts() -> dict[str, int]:
    conn = logging_db.get_connection()
    try:
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in RESEARCH_TABLES
        }
    finally:
        conn.close()


def reset_history() -> dict[str, int]:
    """Delete research history atomically and reset only those row IDs."""
    conn = logging_db.get_connection()
    removed: dict[str, int] = {}
    try:
        with conn:
            for table in RESEARCH_TABLES:
                removed[table] = int(conn.execute(f"DELETE FROM {table}").rowcount)
            placeholders = ",".join("?" for _ in RESEARCH_TABLES)
            conn.execute(
                f"DELETE FROM sqlite_sequence WHERE name IN ({placeholders})",
                RESEARCH_TABLES,
            )
    finally:
        conn.close()
    return removed


def completed_defaults() -> set[str]:
    """Strategies already represented by an evaluated current canonical row."""
    conn = logging_db.get_connection()
    try:
        rows = conn.execute(
            "SELECT strategy_name FROM runs "
            "WHERE metrics_version = ? AND is_canonical = 1 AND validation_json IS NOT NULL "
            "AND json_extract(validation_json, '$.version') = ? "
            "UNION SELECT strategy_name FROM portfolio_runs "
            "WHERE metrics_version = ? AND is_canonical = 1 AND validation_json IS NOT NULL "
            "AND json_extract(validation_json, '$.version') = ?",
            (
                logging_db.METRICS_VERSION, VALIDATION_REPORT_VERSION,
                logging_db.METRICS_VERSION, VALIDATION_REPORT_VERSION,
            ),
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        conn.close()


def remove_incomplete_attempts() -> dict[str, int]:
    """Remove only rows left incomplete by an interrupted rebuild.

    A validation attaches its experiment id only at completion, so an abrupt
    process stop can leave both an unvalidated base row and a registered
    experiment.  Neither is evidence and both must disappear before resume.
    """
    conn = logging_db.get_connection()
    removed: dict[str, int] = {}
    try:
        with conn:
            removed["runs"] = int(conn.execute(
                "DELETE FROM runs WHERE validation_json IS NULL"
            ).rowcount)
            removed["portfolio_runs"] = int(conn.execute(
                "DELETE FROM portfolio_runs WHERE validation_json IS NULL"
            ).rowcount)
            removed["research_experiments"] = int(conn.execute(
                "DELETE FROM research_experiments WHERE status <> 'completed'"
            ).rowcount)
    finally:
        conn.close()
    return removed


def _run_one(strategy_name: str) -> dict[str, Any]:
    job = start_validation_job(_engine(strategy_name), strategy_name, None)
    job_id = str(job["jobId"])
    last_stage = ""
    while job["status"] in {"queued", "running"}:
        stage = str(job.get("stage") or "")
        if stage != last_stage:
            print(
                f"    {int(job.get('progressPct') or 0):3d}% {stage}",
                flush=True,
            )
            last_stage = stage
        time.sleep(0.5)
        job = validation_job(job_id)
    return job


def _run_pending(pending: list[str]) -> list[dict[str, str]]:
    """Queue every plan before results, then drain the app's bounded pool."""
    active: dict[str, tuple[str, dict[str, Any]]] = {}
    failures: list[dict[str, str]] = []
    for index, name in enumerate(pending, start=1):
        job = start_validation_job(_engine(name), name, None)
        active[str(job["jobId"])] = (name, job)
        print(f"[{index}/{len(pending)}] QUEUED {name} ({_engine(name)})", flush=True)

    last_stage: dict[str, str] = {}
    while active:
        for job_id, (name, prior) in list(active.items()):
            job = validation_job(job_id)
            stage = str(job.get("stage") or "")
            if stage != last_stage.get(job_id):
                print(
                    f"  {name}: {int(job.get('progressPct') or 0):3d}% {stage}",
                    flush=True,
                )
                last_stage[job_id] = stage
            if job["status"] in {"queued", "running"}:
                active[job_id] = (name, job)
                continue
            del active[job_id]
            if job["status"] != "completed":
                error = str(job.get("error") or "unknown validation failure")
                failures.append({"strategy": name, "error": error})
                print(f"  {name}: FAILED: {error}", flush=True)
                continue
            result = job.get("result") or {}
            verdict = ((result.get("validation") or {}).get("verdict") or {})
            print(
                f"  {name}: COMPLETE: {verdict.get('headline', 'evaluation persisted')}",
                flush=True,
            )
        if active:
            time.sleep(0.5)
    return failures


def audit() -> dict[str, Any]:
    conn = logging_db.get_connection()
    try:
        run_row = conn.execute(
            "SELECT COUNT(*), SUM(validation_json IS NOT NULL AND "
            "json_extract(validation_json, '$.version') = ?) FROM runs",
            (VALIDATION_REPORT_VERSION,),
        ).fetchone()
        portfolio_row = conn.execute(
            "SELECT COUNT(*), SUM(validation_json IS NOT NULL AND "
            "json_extract(validation_json, '$.version') = ?) FROM portfolio_runs",
            (VALIDATION_REPORT_VERSION,),
        ).fetchone()
        verdicts = conn.execute(
            "SELECT edge_verdict, COUNT(*) FROM ("
            "SELECT edge_verdict FROM runs UNION ALL "
            "SELECT edge_verdict FROM portfolio_runs"
            ") GROUP BY edge_verdict ORDER BY edge_verdict"
        ).fetchall()
        names = completed_defaults()
        return {
            "runs": {"total": int(run_row[0]), "evaluated": int(run_row[1] or 0)},
            "portfolioRuns": {
                "total": int(portfolio_row[0]),
                "evaluated": int(portfolio_row[1] or 0),
            },
            "evaluatedStrategies": sorted(names),
            "missingStrategies": sorted(set(ALL_STRATEGY_NAMES) - names),
            "verdictCounts": {str(label): int(count) for label, count in verdicts},
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="keep existing history and run only missing current evaluated defaults",
    )
    args = parser.parse_args()

    before = history_counts()
    print("Current research-history rows:", json.dumps(before, sort_keys=True), flush=True)
    if args.dry_run:
        print(f"Would rebuild {len(ALL_STRATEGY_NAMES)} registered strategies.")
        return 0

    if not args.resume:
        removed = reset_history()
        print("Reset complete:", json.dumps(removed, sort_keys=True), flush=True)
    else:
        removed = remove_incomplete_attempts()
        print("Interrupted attempts removed:", json.dumps(removed, sort_keys=True), flush=True)

    already_done = completed_defaults()
    pending = [name for name in ALL_STRATEGY_NAMES if name not in already_done]
    failures = _run_pending(pending)

    final_audit = audit()
    print("FINAL_AUDIT=" + json.dumps(final_audit, sort_keys=True), flush=True)
    if failures:
        print("FAILURES=" + json.dumps(failures, sort_keys=True), flush=True)
        return 1
    return 0 if not final_audit["missingStrategies"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
