"""Generic immutable forward experiments and automatic lifecycle demotion."""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from engine import execution_db, logging_db

DEFAULT_MIN_DAYS = 180
DEFAULT_MIN_OBSERVATIONS = 20
DEFAULT_MAX_SHORTFALL_PCT = 10.0
SLIPPAGE_DRIFT_MULTIPLE = 2.0


def _report_for_portfolio_run(strategy_name: str, run_id: int) -> tuple[Any, dict]:
    row, report = logging_db.canonical_portfolio_validation(strategy_name, run_id)
    if row is None or report is None:
        raise ValueError("A current persisted validated portfolio run is required")
    return row, report


def start(
    strategy_name: str,
    validation_run_id: int,
    *,
    min_calendar_days: int = DEFAULT_MIN_DAYS,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
    max_shortfall_pct: float = DEFAULT_MAX_SHORTFALL_PCT,
    override: bool = False,
    override_reason: str | None = None,
) -> dict[str, Any]:
    """`override=True` promotes a strategy to a (paper-only, never production
    -- production_capital_worthy is untouched by this) forward test despite
    failing `forwardTestWorthy`. An explicit, per-call, LOGGED bypass: the
    exact blockers present at override time are frozen into
    override_blockers_json rather than left to a later live-recomputed
    status, since that status can legitimately change (see
    engine/metrics.py:derive_status's "recomputed, never trusted as logged"
    rule) and the override's own justification must not drift with it.
    The immutable-fingerprint requirement below is NOT part of the override:
    it is not a validation gate, it is "does a real run exist to attach an
    experiment to" -- there is nothing to override if that fails.
    """
    # Idempotent short-circuit BEFORE the forward_worthy/override gate below,
    # not after: api/main.py:set_execution_config computes `override` as
    # `overridePassedGates and not eligible`, and a PRIOR successful override
    # for this exact run makes paper_execution_eligibility() report
    # eligible=True on every later call -- so `override` evaluates to False
    # forever afterward. Toggling a strategy off and back on (or any other
    # repeat call for the same already-locked run) must reuse the existing
    # experiment, not re-run a gate check whose "override" input structurally
    # can never be True again for a run that already has one.
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    existing = conn.execute(
        "SELECT * FROM forward_experiments WHERE source_table='portfolio_runs' AND validation_run_id=?",
        (validation_run_id,),
    ).fetchone()
    if existing is not None:
        conn.close()
        return _serialize(existing, [])
    conn.close()

    row, report = _report_for_portfolio_run(strategy_name, validation_run_id)
    verdict = report.get("verdict") or {}
    forward_worthy = bool(verdict.get("forwardTestWorthy"))
    if not forward_worthy and not override:
        raise ValueError("The validation report is not forward-test worthy")
    blockers_at_override = verdict.get("blockers") or [] if not forward_worthy else []
    research = report.get("research") or {}
    manifest = research.get("manifest") or {}
    fingerprint = manifest.get("runFingerprint")
    if not fingerprint:
        raise ValueError("The validation report has no immutable run fingerprint")
    spec = research.get("validationSpec") or {}
    config = manifest.get("config") or {}
    conn = logging_db.get_connection()
    with conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO forward_experiments (
                strategy_name, source_table, validation_run_id, started_at,
                frozen_manifest_hash, frozen_config_json, benchmark,
                primary_criterion, min_calendar_days, min_observations,
                max_shortfall_pct, status, locked,
                override_used, override_reason, override_blockers_json, override_at
            ) VALUES (?, 'portfolio_runs', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', 1, ?, ?, ?, ?)
            """,
            (
                strategy_name, validation_run_id, datetime.now().isoformat(timespec="seconds"),
                fingerprint, json.dumps(config, sort_keys=True),
                spec.get("primaryBenchmark") or "registered benchmark",
                spec.get("primaryCriterion") or "positive forward contribution",
                int(min_calendar_days), int(min_observations), float(max_shortfall_pct),
                int(bool(override) and not forward_worthy),
                override_reason if (override and not forward_worthy) else None,
                json.dumps(blockers_at_override) if (override and not forward_worthy) else None,
                datetime.now().isoformat(timespec="seconds") if (override and not forward_worthy) else None,
            ),
        )
    conn.row_factory = __import__("sqlite3").Row
    experiment = conn.execute(
        "SELECT * FROM forward_experiments WHERE source_table='portfolio_runs' AND validation_run_id=?",
        (validation_run_id,),
    ).fetchone()
    conn.close()
    return _serialize(experiment, [])


def record_observation(
    experiment_id: int,
    *,
    as_of: date,
    strategy_return_pct: float,
    benchmark_return_pct: float,
    trade_count: int | None = None,
    realized_slippage_bps: float | None = None,
    expected_slippage_bps: float | None = None,
    turnover_pct: float | None = None,
) -> dict[str, Any]:
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    experiment = conn.execute("SELECT * FROM forward_experiments WHERE id=?", (experiment_id,)).fetchone()
    if experiment is None:
        conn.close()
        raise ValueError("Forward experiment not found")
    if not experiment["locked"]:
        conn.close()
        raise ValueError("Forward experiment is not locked")
    if experiment["status"] in {"falsified", "superseded"}:
        conn.close()
        raise ValueError(f"Cannot append to a {experiment['status']} experiment")
    with conn:
        conn.execute(
            """
            INSERT INTO forward_observations (
                forward_experiment_id, as_of, strategy_return_pct,
                benchmark_return_pct, trade_count, realized_slippage_bps,
                expected_slippage_bps, turnover_pct, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (experiment_id, as_of.isoformat(), float(strategy_return_pct),
             float(benchmark_return_pct), trade_count, realized_slippage_bps,
             expected_slippage_bps, turnover_pct, datetime.now().isoformat(timespec="seconds")),
        )
    conn.close()
    return evaluate(experiment_id)


def _set_lifecycle(experiment, lifecycle: str, conclusion: str, production: bool) -> None:
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    row = conn.execute(
        f"SELECT validation_json FROM {experiment['source_table']} WHERE id=?",
        (experiment["validation_run_id"],),
    ).fetchone()
    if row and row["validation_json"]:
        report = json.loads(row["validation_json"])
        report.setdefault("verdict", {})["lifecycleStage"] = lifecycle
        report["verdict"]["productionCapitalWorthy"] = production
        if lifecycle == "suspended":
            report["verdict"]["forwardTestWorthy"] = False
            blockers = report["verdict"].setdefault("blockers", [])
            if "Forward experiment falsified" not in blockers:
                blockers.append("Forward experiment falsified")
        report.setdefault("research", {})["lifecycleStage"] = lifecycle
        with conn:
            conn.execute(
                f"UPDATE {experiment['source_table']} SET lifecycle_stage=?, validation_json=? WHERE id=?",
                (lifecycle, json.dumps(report), experiment["validation_run_id"]),
            )
    conn.close()
    if lifecycle == "suspended":
        execution_db.set_enabled(experiment["strategy_name"], False, datetime.now().isoformat())


def evaluate(experiment_id: int) -> dict[str, Any]:
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    experiment = conn.execute("SELECT * FROM forward_experiments WHERE id=?", (experiment_id,)).fetchone()
    if experiment is None:
        conn.close()
        raise ValueError("Forward experiment not found")
    observations = conn.execute(
        "SELECT * FROM forward_observations WHERE forward_experiment_id=? ORDER BY as_of, id",
        (experiment_id,),
    ).fetchall()
    if not observations:
        conn.close()
        return _serialize(experiment, observations)
    latest = observations[-1]
    elapsed = (date.fromisoformat(latest["as_of"]) - date.fromisoformat(experiment["started_at"][:10])).days
    contribution = latest["strategy_return_pct"] - latest["benchmark_return_pct"]
    slippage_drift = bool(
        latest["realized_slippage_bps"] is not None
        and latest["expected_slippage_bps"] not in (None, 0)
        and latest["realized_slippage_bps"] > SLIPPAGE_DRIFT_MULTIPLE * latest["expected_slippage_bps"]
    )
    mature = elapsed >= experiment["min_calendar_days"] and len(observations) >= experiment["min_observations"]
    if mature and (contribution < -experiment["max_shortfall_pct"] or slippage_drift):
        status = "falsified"
        conclusion = (
            f"Forward contribution {contribution:+.2f}pp; slippage drift={slippage_drift}. "
            "Production and paper automation were automatically suspended."
        )
        lifecycle, production = "suspended", False
    elif mature and contribution > 0 and not slippage_drift:
        status = "forward_validated"
        conclusion = f"Forward contribution is {contribution:+.2f}pp after the frozen minimum horizon."
        lifecycle, production = "production_eligible", True
    else:
        status = "running"
        conclusion = (
            f"{elapsed}/{experiment['min_calendar_days']} days and "
            f"{len(observations)}/{experiment['min_observations']} observations; configuration remains locked."
        )
        lifecycle, production = "paper_eligible", False
    with conn:
        conn.execute(
            "UPDATE forward_experiments SET status=?, conclusion=?, last_evaluated_at=? WHERE id=?",
            (status, conclusion, datetime.now().isoformat(timespec="seconds"), experiment_id),
        )
    experiment = conn.execute("SELECT * FROM forward_experiments WHERE id=?", (experiment_id,)).fetchone()
    conn.close()
    _set_lifecycle(experiment, lifecycle, conclusion, production)
    return _serialize(experiment, observations)


def for_strategy(strategy_name: str) -> list[dict[str, Any]]:
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    experiments = conn.execute(
        "SELECT * FROM forward_experiments WHERE strategy_name=? ORDER BY id DESC",
        (strategy_name,),
    ).fetchall()
    rows = []
    for experiment in experiments:
        observations = conn.execute(
            "SELECT * FROM forward_observations WHERE forward_experiment_id=? ORDER BY as_of, id",
            (experiment["id"],),
        ).fetchall()
        rows.append(_serialize(experiment, observations))
    conn.close()
    return rows


def _serialize(experiment, observations) -> dict[str, Any]:
    latest = observations[-1] if observations else None
    return {
        "id": experiment["id"],
        "strategyName": experiment["strategy_name"],
        "validationRunId": experiment["validation_run_id"],
        "startedAt": experiment["started_at"],
        "frozenManifestHash": experiment["frozen_manifest_hash"],
        "frozenConfig": json.loads(experiment["frozen_config_json"]),
        "benchmark": experiment["benchmark"],
        "primaryCriterion": experiment["primary_criterion"],
        "minCalendarDays": experiment["min_calendar_days"],
        "minObservations": experiment["min_observations"],
        "maxShortfallPct": experiment["max_shortfall_pct"],
        "status": experiment["status"],
        "conclusion": experiment["conclusion"],
        "locked": bool(experiment["locked"]),
        "observationCount": len(observations),
        "latest": None if latest is None else {key: latest[key] for key in latest.keys()},
        "overrideUsed": bool(experiment["override_used"]) if "override_used" in experiment.keys() else False,
        "overrideReason": experiment["override_reason"] if "override_reason" in experiment.keys() else None,
        "overrideBlockers": (
            json.loads(experiment["override_blockers_json"])
            if "override_blockers_json" in experiment.keys() and experiment["override_blockers_json"]
            else []
        ),
        "overrideAt": experiment["override_at"] if "override_at" in experiment.keys() else None,
    }
