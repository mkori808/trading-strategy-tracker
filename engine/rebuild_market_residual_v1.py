"""Rebuild the frozen Market-Residual V1 after its execution-boundary fix.

The first V1 implementation aligned the benchmark through the execution day
while the cross-sectional engine correctly supplied stock history only through
the prior close.  After the configured skip, that one-row mismatch made every
ranking empty.  The resulting cash-only attempt remains in the database and in
the multiple-testing family.  This script registers a distinct implementation
revision *before* replaying the unchanged dates, universe, and parameters.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from unittest.mock import patch

from engine import data_quality, logging_db
from engine.frozen_protocol import family_record
from engine.metrics import portfolio_status
from engine.run_frozen_neighbors import IMPLEMENTATION_REVISIONS
from engine.runner import (
    ALPACA_COMMISSION_BPS,
    RunRequest,
    _benchmark_window_return,
    mean_spread_bps,
    run_cross_sectional,
)
from engine.sanity import check_return, check_sharpe
from engine.validation import validate_cross_sectional


STRATEGY = "Market-Residual Momentum"
UNIVERSE = "dow_pit"


def _find_check(report: dict, key: str) -> dict | None:
    for dimension in report.get("dimensions", []):
        for check in dimension.get("checks", []):
            if check.get("key") == key:
                return check
    return None


def rebuild() -> dict:
    protocol = family_record(STRATEGY)
    assert protocol is not None
    revision = IMPLEMENTATION_REVISIONS[STRATEGY]
    family = str(protocol["searchFamily"])

    conn = logging_db.get_connection()
    conn.row_factory = sqlite3.Row
    finished = conn.execute(
        "SELECT p.* FROM portfolio_runs p JOIN research_experiments e "
        "ON e.id=p.experiment_id WHERE p.strategy_name=? AND p.is_canonical=1 "
        "AND e.config_json LIKE ? ORDER BY p.id DESC LIMIT 1",
        (STRATEGY, f'%"implementationRevision": "{revision}"%'),
    ).fetchone()
    if finished is not None and finished["validation_json"]:
        conn.close()
        return {
            "status": "already-completed", "runId": int(finished["id"]),
            "experimentId": int(finished["experiment_id"]),
            "returnPct": float(finished["return_pct"]),
        }

    original = conn.execute(
        "SELECT * FROM portfolio_runs WHERE strategy_name=? ORDER BY id LIMIT 1",
        (STRATEGY,),
    ).fetchone()
    if original is None or not original["validation_json"]:
        conn.close()
        raise RuntimeError("The original frozen Market-Residual V1 is missing")
    original_report = json.loads(original["validation_json"])
    original_experiment = conn.execute(
        "SELECT * FROM research_experiments WHERE id=?",
        (original["experiment_id"],),
    ).fetchone()
    if original_experiment is None:
        conn.close()
        raise RuntimeError("The original V1 preregistration is missing")

    start = date.fromisoformat(original["start_date"])
    end = date.fromisoformat(original["end_date"])
    risk_free_rate = float(original["risk_free_rate"])
    old_config = json.loads(original_experiment["config_json"])
    pre_result_power = (original_report.get("research") or {}).get("preResultPower")
    correction_config = {
        "correctedFrozenCanonical": True,
        "implementationRevision": revision,
        "correctionReason": (
            "benchmark history now ends strictly before the next-open execution date"
        ),
        "start": start.isoformat(), "end": end.isoformat(),
        "symbols": json.loads(original["symbols"]),
        "params": protocol["canonical"], "universeId": UNIVERSE,
        "minimumTradableAlphaPct": old_config.get("minimumTradableAlphaPct", 2.0),
        "preResultPower": pre_result_power,
    }
    encoded = json.dumps(correction_config, sort_keys=True)
    registered = conn.execute(
        "SELECT * FROM research_experiments WHERE search_family=? AND config_json=?",
        (family, encoded),
    ).fetchone()
    conn.close()
    if registered is None:
        experiment_id, family_number = logging_db.register_experiment(
            strategy_name=STRATEGY,
            engine="cross_sectional",
            hypothesis=original_experiment["hypothesis"],
            config=correction_config,
            primary_benchmark=original_experiment["primary_benchmark"],
            primary_criterion=original_experiment["primary_criterion"],
            planned_universes=[UNIVERSE],
            search_family=family,
            is_preregistered=True,
            universe_id=UNIVERSE,
            pre_result_mda_pct=float(original_experiment["pre_result_mda_pct"]),
        )
    else:
        experiment_id = int(registered["id"])
        family_number = int(registered["family_search_number"])

    family_count = logging_db.family_search_count(family)
    logging_db.set_family_search_count(family, family_count)
    request = RunRequest(start=start, end=end, universe_id=UNIVERSE)
    # Pin the same pre-result risk-free input as the original attempt.  This
    # isolates the implementation correction from later vendor-rate updates.
    with patch("engine.runner.data_module.risk_free_rate", return_value=risk_free_rate):
        result = run_cross_sectional(STRATEGY, request, persist=False)

    years = max(0.0, (result.end - result.start).days / 365.25)
    check_return(result.return_pct, label=STRATEGY, years=years)
    check_sharpe(result.sharpe, label=STRATEGY)
    active = int(result.rebalances["holdings"].map(bool).sum())
    if active < max(36, int(0.8 * len(result.rebalances))):
        logging_db.complete_experiment(
            experiment_id, "failed", f"only {active}/{len(result.rebalances)} active rebalances",
        )
        raise RuntimeError("Corrected Market-Residual replay is still effectively cash-only")

    benchmark = _benchmark_window_return(result.start, result.end)
    status = portfolio_status(result.return_pct, result.sharpe, benchmark)
    run_id = logging_db.log_portfolio_run(
        strategy_name=STRATEGY, symbols=result.symbols,
        start=result.start, end=result.end, final_equity=result.final_equity,
        return_pct=result.return_pct, cagr_pct=result.cagr_pct,
        max_drawdown_pct=result.max_drawdown_pct, sharpe=result.sharpe,
        sortino=result.sortino, risk_free_rate=result.risk_free_rate,
        params=None, is_canonical=True, benchmark_return_pct=benchmark,
        status=status,
        slippage_bps=mean_spread_bps(result.symbols, start, end, UNIVERSE),
        commission_bps=ALPACA_COMMISSION_BPS,
        measured_start=result.equity_curve.index[0].date(),
        measured_end=result.equity_curve.index[-1].date(), universe_id=UNIVERSE,
    )
    result.run_id = run_id
    context = {
        "experimentId": experiment_id,
        "familySearchNumber": family_number,
        "familySearchCount": family_count,
        "isPreregistered": True,
        "universeId": UNIVERSE,
        "preResultPower": pre_result_power,
        "dataQuality": data_quality.audit_universe(
            result.symbols, "1d", result.start, result.end,
        ).to_dict(),
        "frozenConfigOnly": True,
        "implementationRevision": revision,
        "correctionReason": correction_config["correctionReason"],
        "invalidatedAttemptRunId": int(original["id"]),
    }
    report = validate_cross_sectional(
        result, applied_params=None, research_context=context,
    ).to_dict()
    logging_db.attach_validation("portfolio_runs", run_id, report)
    logging_db.complete_experiment(
        experiment_id, "completed", report["verdict"]["headline"],
    )

    # Demote, but never delete, the invalid attempt.  Its experiment remains
    # part of multiplicity because it was genuinely executed and inspected.
    original_report.setdefault("research", {}).update({
        "invalidatedBy": revision,
        "invalidationReason": correction_config["correctionReason"],
        "replacementRunId": run_id,
    })
    original_report["verdict"] = {
        **(original_report.get("verdict") or {}),
        "headline": "Invalid implementation - benchmark/execution cutoff mismatch",
        "identifiedEdge": False,
        "promotionBlocked": True,
    }
    conn = logging_db.get_connection()
    with conn:
        conn.execute(
            "UPDATE portfolio_runs SET is_canonical=0, edge_verdict=?, validation_json=? "
            "WHERE id=?",
            (original_report["verdict"]["headline"], json.dumps(original_report), original["id"]),
        )
        conn.execute(
            "UPDATE research_experiments SET verdict=? WHERE id=?",
            ("invalid implementation; retained in family count", original_experiment["id"]),
        )
    conn.close()
    return {
        "status": "completed", "runId": run_id, "experimentId": experiment_id,
        "familySearchNumber": family_number, "familySearchCount": family_count,
        "returnPct": result.return_pct, "cagrPct": result.cagr_pct,
        "sharpe": result.sharpe, "maxDrawdownPct": result.max_drawdown_pct,
        "rebalances": len(result.rebalances), "activeRebalances": active,
        "modeledCosts": result.total_costs,
        "grossTradedNotional": result.total_traded_notional,
        "verdict": report["verdict"],
    }


if __name__ == "__main__":
    print(json.dumps(rebuild(), indent=2, default=str))
