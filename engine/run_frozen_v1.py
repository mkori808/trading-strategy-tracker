"""Run pre-registered canonical V1 jobs sequentially through the real API path.

The API path registers the pre-result MDA and experiment before dispatching
the backtest. Validation begins only after the canonical run row is stored.
"""

from __future__ import annotations

import argparse
import json
import time

from api import main as api
from engine import logging_db
from engine import data_quality
from engine.portfolio import run_portfolio_backtest
from engine.runner import run_backtest
from engine.sanity import check_return, check_sharpe
from engine.validation import validate_standard


FROZEN_V1 = [
    ("cross_sectional", "52-Week-High Momentum"),
    ("cross_sectional", "Market-Residual Momentum"),
    ("standard", "Negative Return + Volume Shock Reversal"),
    ("standard", "Volume-Shock Continuation (Long)"),
    ("standard", "Volume-Shock Continuation (Short)"),
    ("standard", "MAX Lottery-Return Reversal (Short)"),
    ("standard", "Volatility-Conditioned Pullback"),
]


def run_one(engine: str, strategy_name: str) -> dict:
    # A prior process may have been interrupted after the canonical row was
    # committed but before validation attached. Resume that row without
    # creating a duplicate canonical run or resetting the search count.
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    existing = None
    experiment = None
    if engine == "standard":
        existing = conn.execute(
            "SELECT * FROM runs WHERE strategy_name = ? AND is_canonical = 1 "
            "ORDER BY id DESC LIMIT 1", (strategy_name,),
        ).fetchone()
        experiment = conn.execute(
            "SELECT * FROM research_experiments WHERE strategy_name = ? "
            "ORDER BY id DESC LIMIT 1", (strategy_name,),
        ).fetchone()
    else:
        existing = conn.execute(
            "SELECT * FROM portfolio_runs WHERE strategy_name = ? AND is_canonical = 1 "
            "ORDER BY id DESC LIMIT 1", (strategy_name,),
        ).fetchone()
    conn.close()
    if existing is not None and existing["validation_json"] is not None:
        validation = json.loads(existing["validation_json"])
        return {
            "strategy": strategy_name, "engine": engine,
            "status": "already-completed", "runId": int(existing["id"]),
            "returnPct": (
                existing["total_return_pct"] if engine == "standard"
                else existing["return_pct"]
            ),
            "sharpe": existing["sharpe"],
            "validation": validation.get("verdict", {}),
            "preResultPower": (validation.get("research") or {}).get("preResultPower"),
        }
    if existing is not None and existing["validation_json"] is None and experiment is not None:
        result = run_backtest(strategy_name, persist=False)
        result.run_id = int(existing["id"])
        portfolio = run_portfolio_backtest(
            result, risk_free_rate=result.metrics.risk_free_rate or 0.0
        )
        config = json.loads(experiment["config_json"])
        context = {
            "experimentId": int(experiment["id"]),
            "familySearchNumber": int(experiment["family_search_number"]),
            "familySearchCount": logging_db.family_search_count(experiment["search_family"]),
            "isPreregistered": True,
            "universeId": experiment["universe_id"],
            "preResultPower": config.get("preResultPower"),
            "frozenConfigOnly": True,
        }
        context["dataQuality"] = data_quality.audit_universe(
            list(result.symbol_results), "1d", result.start, result.end,
        ).to_dict()
        validation = validate_standard(result, portfolio, research_context=context)
        payload = validation.to_dict()
        # A plausibility failure must never leave an apparently valid research
        # report attached to the canonical row.
        years = max(0.0, (result.end - result.start).days / 365.25)
        check_return(portfolio.return_pct, label=strategy_name, years=years)
        check_sharpe(portfolio.sharpe, label=strategy_name)
        logging_db.attach_validation("runs", result.run_id, payload)
        logging_db.complete_experiment(
            int(experiment["id"]), "completed", payload["verdict"]["headline"]
        )
        return {
            "strategy": strategy_name, "engine": engine,
            "status": "completed-resumed", "experimentId": int(experiment["id"]),
            "runId": result.run_id, "returnPct": portfolio.return_pct,
            "sharpe": portfolio.sharpe, "validation": payload["verdict"],
            "preResultPower": context["preResultPower"],
        }

    job = api.start_validation_job(engine, strategy_name, None)
    while job["status"] in {"queued", "running"}:
        time.sleep(0.25)
        job = api.validation_job(job["jobId"])
    if job["status"] != "completed":
        return {
            "strategy": strategy_name,
            "engine": engine,
            "status": job["status"],
            "error": job.get("error"),
            "experimentId": job.get("experimentId"),
        }
    result = job["result"]
    if engine == "standard":
        return_pct = result["portfolio"]["returnPct"]
        sharpe = result["portfolio"]["sharpe"]
    else:
        return_pct = result["returnPct"]
        sharpe = result["sharpe"]
    years = max(0.0, (
        api.date.fromisoformat(result["end"]) - api.date.fromisoformat(result["start"])
    ).days / 365.25)
    check_return(return_pct, label=strategy_name, years=years)
    check_sharpe(sharpe, label=strategy_name)
    return {
        "strategy": strategy_name,
        "engine": engine,
        "status": "completed",
        "experimentId": job.get("experimentId"),
        "runId": result.get("runId"),
        "returnPct": return_pct,
        "sharpe": sharpe,
        "validation": result.get("validation", {}).get("verdict", {}),
        "preResultPower": result.get("validation", {}).get("research", {}).get("preResultPower"),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", help="Run one registered V1 by exact name")
    args = parser.parse_args()
    selected = [item for item in FROZEN_V1 if args.strategy in (None, item[1])]
    if not selected:
        raise SystemExit(f"Unknown frozen V1 strategy: {args.strategy}")
    results = [run_one(engine, name) for engine, name in selected]
    print(json.dumps(results, indent=2, default=str))


if __name__ == "__main__":
    main()
