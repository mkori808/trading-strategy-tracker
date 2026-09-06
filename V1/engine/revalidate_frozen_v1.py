"""Recompute canonical frozen-event validation on the originally stored window."""

from __future__ import annotations

from datetime import date
import json
from unittest.mock import patch

from engine import data_quality, logging_db
from engine.backfill_frozen_validation import backfill as backfill_neighbors
from engine.portfolio import run_portfolio_backtest
from engine.runner import RunRequest, run_backtest
from engine.sanity import check_return, check_sharpe
from engine.validation import validate_standard


NAMES = (
    "Negative Return + Volume Shock Reversal",
    "Volume-Shock Continuation (Long)",
    "Volume-Shock Continuation (Short)",
    "MAX Lottery-Return Reversal (Short)",
    "Volatility-Conditioned Pullback",
)


def _positive_return(report: dict) -> float | None:
    for dimension in report.get("dimensions", []):
        for check in dimension.get("checks", []):
            if check.get("key") == "positive_return":
                return check.get("value")
    return None


def run() -> list[dict]:
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    output = []
    for name in NAMES:
        row = conn.execute(
            "SELECT * FROM runs WHERE strategy_name=? AND is_canonical=1 "
            "ORDER BY id DESC LIMIT 1", (name,),
        ).fetchone()
        if row is None or not row["validation_json"]:
            raise RuntimeError(f"Missing canonical V1 validation for {name}")
        old = json.loads(row["validation_json"])
        experiment = conn.execute(
            "SELECT * FROM research_experiments WHERE id=?", (row["experiment_id"],),
        ).fetchone()
        if experiment is None:
            raise RuntimeError(f"Missing preregistration for {name}")
        start = date.fromisoformat(row["start_date"])
        end = date.fromisoformat(row["end_date"])
        params = json.loads(row["params"] or "{}")
        # The T-bill cache can receive revised observations after V1. Pin the
        # exact stored rate so revalidation cannot silently rewrite canonical
        # idle-cash accrual while claiming to replay the same experiment.
        with patch(
            "engine.runner.data_module.risk_free_rate",
            return_value=float(row["risk_free_rate"] or 0.0),
        ):
            result = run_backtest(
                name,
                RunRequest(
                    start=start, end=end, params=params or None,
                    universe_id="dow_pit",
                ),
                persist=False,
            )
        result.run_id = int(row["id"])
        portfolio = run_portfolio_backtest(
            result, risk_free_rate=result.metrics.risk_free_rate or 0.0
        )
        years = max(0.0, (end - start).days / 365.25)
        check_return(portfolio.return_pct, label=name, years=years)
        check_sharpe(portfolio.sharpe, label=name)
        prior_return = _positive_return(old)
        replay_drift = (
            None if prior_return is None
            else portfolio.return_pct - float(prior_return)
        )
        # Adjusted-data vendors can revise historical prices by tiny amounts.
        # Refuse economically meaningful drift, but record sub-0.001pp replay
        # drift explicitly instead of pretending byte identity.
        if replay_drift is None or abs(replay_drift) > 0.001:
            raise RuntimeError(
                f"{name} frozen rerun drifted: stored {prior_return}, now {portfolio.return_pct}"
            )
        old_research = old.get("research") or {}
        quality = old_research.get("dataQuality")
        if not quality:
            quality = data_quality.audit_universe(
                json.loads(row["symbols"] or "[]"), "1d", start, end,
            ).to_dict()
        context = {
            "experimentId": int(experiment["id"]),
            "familySearchNumber": int(experiment["family_search_number"]),
            "familySearchCount": logging_db.family_search_count(
                experiment["search_family"]
            ),
            "searchFamily": experiment["search_family"],
            "isPreregistered": True,
            "universeId": "dow_pit",
            "preResultPower": old_research.get("preResultPower"),
            "dataQuality": quality,
            "frozenConfigOnly": True,
            "canonicalReplayDriftPct": replay_drift,
            "canonicalReplayTolerancePct": 0.001,
            "canonicalPortfolioMetrics": {
                "returnPct": portfolio.return_pct,
                "cagrPct": portfolio.cagr_pct,
                "sharpe": portfolio.sharpe,
                "sortino": portfolio.sortino,
                "maxDrawdownPct": portfolio.max_drawdown_pct,
                "trades": result.metrics.trades_taken,
                "winRatePct": result.metrics.win_rate * 100.0,
                "expectancyR": result.metrics.expectancy_r,
                "profitFactor": result.metrics.profit_factor,
                "averageGrossExposurePct": result.metrics.average_gross_exposure_pct,
                "averageNetExposurePct": result.metrics.average_net_exposure_pct,
                "timeInMarketPct": result.metrics.time_in_market_pct,
                "turnoverPct": result.metrics.turnover_pct,
                "modeledCosts": result.metrics.modeled_costs,
                "matchedSpyExcessPct": result.metrics.matched_spy_excess_pct,
                "matchedAlphaAnnualPct": result.metrics.matched_alpha_annual_pct,
                "matchedBeta": result.metrics.matched_beta,
            },
        }
        validation = validate_standard(
            result, portfolio, applied_params=params or None,
            research_context=context,
        ).to_dict()
        logging_db.attach_validation("runs", int(row["id"]), validation)
        logging_db.complete_experiment(
            int(experiment["id"]), "completed", validation["verdict"]["headline"]
        )
        output.append({
            "strategy": name, "runId": int(row["id"]),
            "returnPct": portfolio.return_pct,
            "rollingStatus": next(
                check["status"]
                for dimension in validation["dimensions"]
                for check in dimension["checks"]
                if check["key"] == "historical_stability"
            ),
        })
    conn.close()
    neighbor_output = backfill_neighbors()
    by_name = {row["strategy"]: row for row in neighbor_output}
    for row in output:
        row["ridgeStatus"] = by_name[row["strategy"]]["ridgeStatus"]
    return output


def main() -> None:
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
