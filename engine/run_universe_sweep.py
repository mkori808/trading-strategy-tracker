"""Preregister and run the frozen strategy × registered-universe matrix.

No parameter overrides or neighbor sweeps are permitted. Every experiment is
registered before the first result is computed, then every validation uses the
full universe-independent ``strategy:engine`` family width. Analysis outputs
are guarded by engine.sanity floors.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from time import monotonic
from uuid import uuid4

from api import main as api_module
from engine import logging_db
from engine.research_governance import build_validation_spec, pre_result_power_design
from engine.runner import (
    SYMBOL_OVERRIDE_DISALLOWED_NAMES, is_cross_sectional, is_pairs,
    run_config, strategy_class,
)
from engine.sanity import check_return, check_sharpe
from engine.universe_registry import universe_registry
from strategies.registry import ALL_STRATEGY_NAMES


ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / "reports"


def engine_for(strategy: str) -> str:
    return "cross_sectional" if is_cross_sectional(strategy) else "pairs" if is_pairs(strategy) else "standard"


def _gate_counts(report: dict) -> tuple[int, int]:
    checks = [
        check for dimension in report.get("dimensions", [])
        for check in dimension.get("checks", [])
        if check.get("required") and check.get("status") != "not_applicable"
    ]
    return sum(check.get("status") == "pass" for check in checks), len(checks)


def _persist_result(sweep_id: str, plan: dict, job: dict) -> None:
    experiment_id = plan["experimentId"]
    if job["status"] != "completed":
        error = str(job.get("error") or "validation failed")
        logging_db.record_universe_sweep_cell(
            sweep_id=sweep_id, strategy_name=plan["strategy"], engine=plan["engine"],
            universe_id=plan["universeId"], experiment_id=experiment_id,
            status="failed", pre_result_mda_pct=plan["mda"], error=error,
        )
        return
    result = job["result"]
    report = result["validation"]
    if plan["engine"] == "standard":
        measured_return = (result.get("portfolio") or {}).get("returnPct")
        sharpe = (result.get("portfolio") or {}).get("sharpe")
        gap = (result.get("metrics") or {}).get("benchmarkGapPct")
    else:
        measured_return = result.get("returnPct")
        sharpe = result.get("sharpe")
        conn = logging_db.get_connection()
        row = conn.execute(
            "SELECT return_pct, benchmark_return_pct FROM portfolio_runs WHERE experiment_id = ?",
            (experiment_id,),
        ).fetchone()
        conn.close()
        gap = None if row is None or row[1] is None else float(row[0]) - float(row[1])
    years = max(0.0, (plan["end"] - plan["start"]).days / 365.25)
    check_return(measured_return, label=f"{plan['strategy']} × {plan['universeId']}", years=years)
    check_sharpe(sharpe, label=f"{plan['strategy']} × {plan['universeId']}")
    passed, applicable = _gate_counts(report)
    logging_db.record_universe_sweep_cell(
        sweep_id=sweep_id, strategy_name=plan["strategy"], engine=plan["engine"],
        universe_id=plan["universeId"], experiment_id=experiment_id,
        status="completed", pre_result_mda_pct=plan["mda"],
        benchmark_gap_pct=gap, gates_passed=passed, gates_applicable=applicable,
        verdict=(report.get("verdict") or {}).get("headline"), report=report,
    )


def _execute(plan: dict, sweep_id: str) -> tuple[str, str, str]:
    job_id = uuid4().hex
    api_module._validation_jobs[job_id] = {
        "jobId": job_id, "status": "queued", "stage": "Queued", "progressPct": 0,
        "createdAt": datetime.now(timezone.utc).isoformat(), "createdMonotonic": monotonic(),
        "completedAt": None, "error": None, "result": None, "reused": False,
        "experimentId": plan["experimentId"],
    }
    context = {
        "experimentId": plan["experimentId"],
        "familySearchNumber": plan["familySearchNumber"],
        "familySearchCount": plan["familySearchCount"],
        "isPreregistered": True,
        "symbols": plan["symbols"], "interval": plan["interval"],
        "start": plan["start"].isoformat(), "end": plan["end"].isoformat(),
        "universeId": plan["universeId"], "preResultPower": plan["preResultPower"],
        "frozenConfigOnly": True, "sweepId": sweep_id,
    }
    overrides = api_module.BacktestOverrides(universeId=plan["universeId"])
    api_module._execute_validation_job(
        job_id, plan["engine"], plan["strategy"], overrides, context,
    )
    job = api_module._validation_jobs[job_id]
    try:
        _persist_result(sweep_id, plan, job)
        status = job["status"]
    except Exception as exc:
        # A sanity floor is itself a failed evidence result.  Never let it
        # escape the worker and strand the rest of a preregistered family in
        # ``registered`` state.
        error = f"Analysis sanity floor failed: {exc}"
        logging_db.complete_experiment(plan["experimentId"], "failed", error)
        logging_db.record_universe_sweep_cell(
            sweep_id=sweep_id, strategy_name=plan["strategy"], engine=plan["engine"],
            universe_id=plan["universeId"], experiment_id=plan["experimentId"],
            status="failed", pre_result_mda_pct=plan["mda"], error=error,
        )
        status = "failed"
    return plan["strategy"], plan["universeId"], status


def _recover_persisted_results(sweep_id: str) -> int:
    """Finish matrix persistence after an interrupted collector.

    Validation is attached to the canonical run before the worker returns, so
    a collector failure must reuse that immutable evidence rather than rerun
    the strategy (which would duplicate runs and create a new observation).
    """
    conn = logging_db.get_connection()
    conn.row_factory = sqlite3.Row
    pending = conn.execute(
        """
        SELECT u.*, e.config_json
        FROM universe_sweep_results u
        JOIN research_experiments e ON e.id = u.experiment_id
        WHERE u.sweep_id = ? AND u.status = 'registered'
        ORDER BY u.strategy_name, u.universe_id
        """,
        (sweep_id,),
    ).fetchall()
    recovered = 0
    for cell in pending:
        table = "portfolio_runs" if cell["engine"] in {"cross_sectional", "pairs"} else "runs"
        if table == "runs":
            row = conn.execute(
                """
                SELECT strategy_return_pct AS measured_return, sharpe,
                       benchmark_gap_pct, validation_json, start_date, end_date
                FROM runs WHERE experiment_id = ? ORDER BY id DESC LIMIT 1
                """,
                (cell["experiment_id"],),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT return_pct AS measured_return, sharpe,
                       CASE WHEN benchmark_return_pct IS NULL THEN NULL
                            ELSE return_pct - benchmark_return_pct END AS benchmark_gap_pct,
                       validation_json, start_date, end_date
                FROM portfolio_runs WHERE experiment_id = ? ORDER BY id DESC LIMIT 1
                """,
                (cell["experiment_id"],),
            ).fetchone()
        if row is None or not row["validation_json"]:
            continue
        try:
            start = date.fromisoformat(row["start_date"])
            end = date.fromisoformat(row["end_date"])
            years = max(0.0, (end - start).days / 365.25)
            label = f"{cell['strategy_name']} x {cell['universe_id']} recovered"
            check_return(row["measured_return"], label=label, years=years)
            check_sharpe(row["sharpe"], label=label)
            report = json.loads(row["validation_json"])
            passed, applicable = _gate_counts(report)
            logging_db.record_universe_sweep_cell(
                sweep_id=sweep_id, strategy_name=cell["strategy_name"],
                engine=cell["engine"], universe_id=cell["universe_id"],
                experiment_id=cell["experiment_id"], status="completed",
                pre_result_mda_pct=cell["pre_result_mda_pct"],
                benchmark_gap_pct=row["benchmark_gap_pct"], gates_passed=passed,
                gates_applicable=applicable,
                verdict=(report.get("verdict") or {}).get("headline"), report=report,
            )
        except Exception as exc:
            error = f"Analysis sanity floor failed while recovering persisted evidence: {exc}"
            logging_db.complete_experiment(cell["experiment_id"], "failed", error)
            logging_db.record_universe_sweep_cell(
                sweep_id=sweep_id, strategy_name=cell["strategy_name"],
                engine=cell["engine"], universe_id=cell["universe_id"],
                experiment_id=cell["experiment_id"], status="failed",
                pre_result_mda_pct=cell["pre_result_mda_pct"], error=error,
            )
        recovered += 1
    conn.close()
    return recovered


def _write_report(sweep_id: str) -> None:
    rows = [dict(row) for row in logging_db.universe_sweep_matrix(sweep_id)]
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / f"universe_sweep_{sweep_id}.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    header = "strategy,universe,mda_pct,benchmark_gap_pct,gates_passed,gates_applicable,status,verdict,error\n"
    lines = [header]
    for row in rows:
        values = [
            row["strategy_name"], row["universe_id"], row["pre_result_mda_pct"],
            row["benchmark_gap_pct"], row["gates_passed"], row["gates_applicable"],
            row["status"], row["verdict"], row["error"],
        ]
        lines.append(",".join(json.dumps(value if value is not None else "") for value in values) + "\n")
    (REPORTS / f"universe_sweep_{sweep_id}.csv").write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep-id", default=datetime.now().strftime("%Y%m%dT%H%M%S"))
    parser.add_argument("--workers", type=int, default=2, choices=(1, 2))
    parser.add_argument("--preregister-only", action="store_true")
    parser.add_argument(
        "--recover-sweep",
        help="Persist already-attached validation reports for an interrupted sweep; does not rerun or register experiments.",
    )
    args = parser.parse_args()
    if args.recover_sweep:
        recovered = _recover_persisted_results(args.recover_sweep)
        _write_report(args.recover_sweep)
        rows = logging_db.universe_sweep_matrix(args.recover_sweep)
        incomplete = [row for row in rows if row["status"] in {"registered", "running"}]
        print(
            f"RECOVERED {recovered} persisted cells; cells={len(rows)} incomplete={len(incomplete)}",
            flush=True,
        )
        return 2 if incomplete else 0
    registry = universe_registry()
    plans: list[dict] = []

    # Phase 1: every cell is registered before any result is observed.
    for strategy in ALL_STRATEGY_NAMES:
        engine = engine_for(strategy)
        interval, _, start, end = run_config(strategy)
        cls = strategy_class(strategy)
        family = f"{strategy}:{engine}"
        for universe_id, universe in registry.items():
            power_symbols = list(universe.symbols) or [f"PIT{i}" for i in range(5)]
            power = pre_result_power_design(strategy, engine, power_symbols, cls, start, end)
            mda = float(power["mdaPct"])
            stored_mda = mda if math.isfinite(mda) else None
            spec = build_validation_spec(
                strategy, engine, power_symbols, cls, universe_id=universe_id,
            )
            experiment_id, ordinal = logging_db.register_experiment(
                strategy_name=strategy, engine=engine, hypothesis=spec.hypothesis,
                config={
                    "sweepId": args.sweep_id, "universeId": universe_id,
                    "symbols": list(universe.symbols), "start": start.isoformat(),
                    "end": end.isoformat(), "interval": interval, "params": {},
                    "frozenConfigOnly": True, "preResultPower": power,
                },
                primary_benchmark=universe.primary_benchmark or "N/A",
                primary_criterion=spec.primary_criterion,
                planned_universes=list(registry), search_family=family,
                is_preregistered=True, universe_id=universe_id,
                pre_result_mda_pct=stored_mda,
            )
            plan = {
                "strategy": strategy, "engine": engine, "interval": interval,
                "universeId": universe_id, "symbols": list(universe.symbols),
                "start": start, "end": end, "experimentId": experiment_id,
                "family": family, "familySearchNumber": ordinal,
                "preResultPower": power, "mda": stored_mda,
                "blocked": None,
            }
            if not universe.runnable:
                plan["blocked"] = universe.unavailable_reason
            elif strategy in SYMBOL_OVERRIDE_DISALLOWED_NAMES:
                plan["blocked"] = "Strategy has a structural sector-ETF universe and is not defined on this registered equity basket."
            plans.append(plan)
            logging_db.record_universe_sweep_cell(
                sweep_id=args.sweep_id, strategy_name=strategy, engine=engine,
                universe_id=universe_id, experiment_id=experiment_id,
                status="registered", pre_result_mda_pct=stored_mda,
            )

    for family in {plan["family"] for plan in plans}:
        count = logging_db.family_search_count(family)
        logging_db.set_family_search_count(family, count)
        for plan in plans:
            if plan["family"] == family:
                plan["familySearchCount"] = count

    # Blocked cells are conclusions about data/design availability, not returns.
    for plan in plans:
        if plan["blocked"]:
            logging_db.complete_experiment(plan["experimentId"], "blocked", plan["blocked"])
            logging_db.record_universe_sweep_cell(
                sweep_id=args.sweep_id, strategy_name=plan["strategy"], engine=plan["engine"],
                universe_id=plan["universeId"], experiment_id=plan["experimentId"],
                status="blocked", pre_result_mda_pct=plan["mda"], error=plan["blocked"],
            )

    print(f"PREREGISTERED {len(plans)} cells before results; sweep={args.sweep_id}", flush=True)
    if args.preregister_only:
        _write_report(args.sweep_id)
        return 0

    runnable = [plan for plan in plans if not plan["blocked"]]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_execute, plan, args.sweep_id): plan for plan in runnable}
        for index, future in enumerate(as_completed(futures), 1):
            strategy, universe_id, status = future.result()
            print(f"[{index}/{len(runnable)}] {strategy} × {universe_id}: {status}", flush=True)
    _write_report(args.sweep_id)
    rows = logging_db.universe_sweep_matrix(args.sweep_id)
    bad = [row for row in rows if row["status"] in {"registered", "running"}]
    print(f"FINAL cells={len(rows)} incomplete={len(bad)}", flush=True)
    return 2 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
