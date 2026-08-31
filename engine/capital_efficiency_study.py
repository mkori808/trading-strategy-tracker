"""Preregistered prop-versus-self-funded study for frozen Optimized DM.

This module is descriptive research.  It never changes a strategy, submits an
order, or rewrites the frozen prop-survival artifacts it references.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from engine.prop_scaling_survival_study import (
    BLOCK,
    HORIZON,
    N_PATHS,
    SEED,
    Rules,
    _bootstrap_indices,
    _optimized_candidate,
)

ROOT = Path(__file__).resolve().parent.parent
PREREGISTRATION = ROOT / "research" / "prop_vs_self_funded_v1_preregistration.json"
OUTPUT_DIR = ROOT / "reports" / "prop_vs_self_funded_v1"
FROZEN_PROP_RESULTS = ROOT / "reports" / "prop_scaling_survival_v1" / "results.json"
FROZEN_FILES = (
    ROOT / "research" / "prop_scaling_survival_v1_preregistration.json",
    FROZEN_PROP_RESULTS,
    ROOT / "reports" / "prop_scaling_survival_v1" / "frontier.csv",
    ROOT / "reports" / "prop_scaling_survival_v1" / "report.md",
    ROOT / "research" / "optimized_dm_prop_forward_v1_preregistration.json",
)
OPERATING_POINTS = (
    {"key": "020_trailing", "scale": 0.20, "drawdownRule": "trailing_to_breakeven"},
    {"key": "025_static", "scale": 0.25, "drawdownRule": "static"},
)
TARGET = 35_000.0


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quantiles(values: np.ndarray) -> dict[str, float]:
    return {
        "expected": float(np.mean(values)),
        "median": float(np.median(values)),
        "p05": float(np.percentile(values, 5)),
        "p01": float(np.percentile(values, 1)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
    }


def _prop_paths(
    sampled: np.ndarray,
    scale: float,
    rules: Rules,
    drawdown_rule: Literal["static", "trailing_to_breakeven"],
) -> dict[str, np.ndarray]:
    """Frozen prop lifecycle, returning path arrays instead of only summaries."""
    n = sampled.shape[0]
    account = rules.account_size
    equity = np.full(n, account)
    peak = np.full(n, account)
    stage = np.zeros(n, dtype=np.int8)
    funded_days = np.zeros(n, dtype=np.int16)
    payout = np.zeros(n)
    fees = np.full(n, rules.challenge_fee)
    resets = np.zeros(n, dtype=np.int16)
    first_breach = np.zeros(n, dtype=np.int16)
    first_payout = np.zeros(n, dtype=np.int16)
    max_util = np.zeros(n)
    daily_breach_ever = np.zeros(n, dtype=bool)
    drawdown_breach_ever = np.zeros(n, dtype=bool)
    for day in range(HORIZON):
        start = equity.copy()
        close_pnl = sampled[:, day, 0] * account * scale
        low_pnl = sampled[:, day, 1] * account * scale
        high_pnl = sampled[:, day, 2] * account * scale
        high_equity = start + high_pnl
        low_equity = start + low_pnl
        close_equity = start + close_pnl
        intraday_peak = np.maximum(peak, high_equity)
        anchor = account if drawdown_rule == "static" else intraday_peak
        max_util = np.maximum(max_util, np.maximum(0.0, anchor - low_equity) / (account * rules.max_drawdown_pct))
        daily_breach = low_pnl <= -(account * rules.daily_loss_pct)
        floor = (
            account * (1.0 - rules.max_drawdown_pct)
            if drawdown_rule == "static"
            else np.minimum(intraday_peak - account * rules.max_drawdown_pct, account)
        )
        drawdown_breach = (~daily_breach) & (low_equity <= floor)
        breached = daily_breach | drawdown_breach
        first_breach[(first_breach == 0) & breached] = day + 1
        daily_breach_ever |= daily_breach
        drawdown_breach_ever |= drawdown_breach
        alive = ~breached
        equity[alive] = close_equity[alive]
        peak[alive] = intraday_peak[alive]
        funded_days[alive & (stage == 1)] += 1
        passed = alive & (stage == 0) & (equity >= account * (1.0 + rules.target_pct))
        stage[passed] = 1
        equity[passed] = account
        peak[passed] = account
        funded_days[passed] = 0
        due = alive & (stage == 1) & (funded_days > 0) & (funded_days % rules.payout_cadence == 0)
        withdrawable = np.where(due, np.maximum(equity - account, 0.0), 0.0)
        paid = withdrawable * rules.payout_split
        payout += paid
        first_payout[(first_payout == 0) & (paid > 0)] = day + 1
        withdrew = due & (withdrawable > 0)
        equity[withdrew] = account
        peak[withdrew] = account
        resets[breached] += 1
        fees[breached] += rules.reset_fee
        equity[breached] = account
        peak[breached] = account
        stage[breached] = 0
        funded_days[breached] = 0
    return {
        "payout": payout,
        "fees": fees,
        "net": payout - fees,
        "resets": resets,
        "firstBreach": first_breach,
        "firstPayout": first_payout,
        "maxDrawdownUtilization": max_util,
        "dailyBreachEver": daily_breach_ever,
        "drawdownBreachEver": drawdown_breach_ever,
    }


def _self_funded_paths(sampled: np.ndarray, exposure: float) -> dict[str, np.ndarray]:
    n = sampled.shape[0]
    equity = np.full(n, exposure)
    peak = equity.copy()
    max_drawdown = np.zeros(n)
    max_daily_loss = np.zeros(n)
    for day in range(HORIZON):
        start = equity.copy()
        high = start + sampled[:, day, 2] * exposure
        low = start + sampled[:, day, 1] * exposure
        close = start + sampled[:, day, 0] * exposure
        peak = np.maximum(peak, high)
        max_drawdown = np.maximum(max_drawdown, peak - low)
        max_daily_loss = np.maximum(max_daily_loss, -sampled[:, day, 1] * exposure)
        equity = close
    return {
        "profit": equity - exposure,
        "endingEquity": equity,
        "maxDrawdown": max_drawdown,
        "maxDailyLoss": max_daily_loss,
    }


def _prop_summary(arrays: dict[str, np.ndarray]) -> dict[str, Any]:
    first_payout = arrays["firstPayout"]
    paid = first_payout > 0
    return {
        "netProfit": _quantiles(arrays["net"]),
        "grossPayout": _quantiles(arrays["payout"]),
        "fees": _quantiles(arrays["fees"]),
        "probabilityAnnualLoss": float(np.mean(arrays["net"] < 0)),
        "breachProbability12m": float(np.mean(arrays["firstBreach"] > 0)),
        "dailyLimitBreachProbability": float(np.mean(arrays["dailyBreachEver"])),
        "drawdownBreachProbability": float(np.mean(arrays["drawdownBreachEver"])),
        "expectedResets": float(np.mean(arrays["resets"])),
        "probabilityAnyPayout": float(np.mean(paid)),
        "medianDaysToFirstPayoutConditional": float(np.median(first_payout[paid])) if paid.any() else None,
        "drawdownUtilization": _quantiles(arrays["maxDrawdownUtilization"]),
    }


def _self_summary(arrays: dict[str, np.ndarray], exposure: float) -> dict[str, Any]:
    return {
        "netProfit": _quantiles(arrays["profit"]),
        "endingEquity": _quantiles(arrays["endingEquity"]),
        "probabilityAnnualLoss": float(np.mean(arrays["profit"] < 0)),
        "maxDrawdownDollars": _quantiles(arrays["maxDrawdown"]),
        "maxDrawdownPctCommitted": _quantiles(arrays["maxDrawdown"] / exposure),
        "probabilityCrossesEquivalent6000Drawdown": float(np.mean(arrays["maxDrawdown"] >= 6_000.0)),
        "probabilityCrossesEquivalent3000DailyLoss": float(np.mean(arrays["maxDailyLoss"] >= 3_000.0)),
    }


def _frontier_rows(
    point: dict[str, Any], prop: dict[str, np.ndarray], self_funded: dict[str, np.ndarray]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scale = float(point["scale"])
    exposure = 100_000.0 * scale
    for accounts in range(1, 11):
        prop_net = prop["net"] * accounts
        self_profit = self_funded["profit"] * accounts
        prop_fees = prop["fees"] * accounts
        for structure, values, committed, fees in (
            ("prop", prop_net, prop_fees, prop_fees),
            ("self_funded_a", self_profit, np.full(len(self_profit), exposure * accounts), np.zeros(len(self_profit))),
        ):
            summary = _quantiles(values)
            committed_expected = float(np.mean(committed))
            rows.append({
                "operatingPoint": point["key"],
                "scale": scale,
                "drawdownRule": point["drawdownRule"] if structure == "prop" else "none",
                "structure": structure,
                "accounts": accounts,
                "aggregateEffectiveExposure": exposure * accounts,
                "expectedCapitalCommitted": committed_expected,
                "upfrontCapitalCommitted": (500.0 if structure == "prop" else exposure) * accounts,
                "expectedNetProfit": summary["expected"],
                "medianNetProfit": summary["median"],
                "p05NetProfit": summary["p05"],
                "p01NetProfit": summary["p01"],
                "probabilityAnnualLoss": float(np.mean(values < 0)),
                "probabilityTarget35000": float(np.mean(values >= TARGET)),
                "expectedReturnOnCommittedCapital": summary["expected"] / committed_expected if committed_expected else None,
                "expectedFees": float(np.mean(fees)),
                "breachProbabilityAtLeastOne": float(np.mean(prop["firstBreach"] > 0)) if structure == "prop" else None,
                "dependence": "perfectly_correlated_identical_paths",
            })
    return rows


def _audit_matrix() -> list[dict[str, Any]]:
    forward_db = ROOT / "logs" / "prop_forward.db"
    counts: dict[str, int] = {}
    if forward_db.exists():
        conn = sqlite3.connect(forward_db)
        for table in ("raw_equity_snapshots", "shadow_marks", "shadow_events", "session_outcomes"):
            try:
                counts[table] = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                counts[table] = 0
        conn.close()
    return [
        {"requirement": "Frozen historical prop simulation", "source": "reports/prop_scaling_survival_v1/results.json", "status": "verified", "active": False, "observations": 5000, "gap": None},
        {"requirement": "Prop fees, payout split, challenge/funded transitions, reset lifecycle", "source": "engine/prop_scaling_survival_study.py and engine/prop_forward.py", "status": "implemented_and_tested", "active": True, "gap": None},
        {"requirement": "Observed slippage and partial-fill calibration", "source": "logs/execution.db via engine.execution_db.fill_calibration", "status": "calibrated", "active": True, "observations": 30, "gap": "Aggregate approximation, not an order-level historical replay."},
        {"requirement": "Actual Alpaca account-equity stream", "source": "logs/prop_forward.db.raw_equity_snapshots", "status": "collector_active_not_started" if counts.get("raw_equity_snapshots", 0) == 0 else "collecting", "active": True, "observations": counts.get("raw_equity_snapshots", 0), "gap": "No backfill; closed/offline intervals remain blank."},
        {"requirement": "Locked strategy fingerprint and one-account ownership", "source": "research/optimized_dm_prop_forward_v1_preregistration.json plus logs/execution.db", "status": "verified", "active": True, "gap": None},
        {"requirement": "Historical strategy return/exposure series", "source": "portfolio_runs.id=33 rebuilt by frozen parameters", "status": "available_selection_contaminated", "active": False, "observations": 250, "gap": "One selected development year; not independent forward evidence."},
        {"requirement": "Intraday breach path", "source": "daily OHLC envelope historically; five-minute raw account marks prospectively", "status": "approximate_historical_prospective_pending", "active": True, "observations": counts.get("raw_equity_snapshots", 0), "gap": "Daily bars cannot identify cross-name simultaneity or high/low ordering."},
        {"requirement": "Self-funded historical comparator", "source": "engine/capital_efficiency_study.py", "status": "implemented_by_this_study", "active": False, "gap": None},
        {"requirement": "Self-funded prospective ledger", "source": "logs/prop_forward.db self-funded tables", "status": "implemented_not_started", "active": True, "observations": 0, "gap": "Must accumulate strictly prospectively."},
        {"requirement": "Correlated 1-to-10 account capital-efficiency frontier", "source": "reports/prop_vs_self_funded_v1/frontier.csv", "status": "implemented_by_this_study", "active": False, "gap": "Perfect correlation supplies no diversification."},
        {"requirement": "Self-funded stop/restart comparator B", "source": "none", "status": "omitted", "active": False, "gap": "No legitimate preregistered restart rule exists; none was invented."},
    ]


def run(output_dir: Path = OUTPUT_DIR, paths: int = N_PATHS) -> dict[str, Any]:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    before = {str(path.relative_to(ROOT)).replace("\\", "/"): _sha256(path) for path in FROZEN_FILES}
    expected = prereg["frozenInputs"]
    if before[expected["historicalPropPreregistration"]["path"]] != expected["historicalPropPreregistration"]["sha256"]:
        raise RuntimeError("Frozen historical prop preregistration hash changed.")
    if before[expected["historicalPropResults"]["path"]] != expected["historicalPropResults"]["sha256"]:
        raise RuntimeError("Frozen historical prop result hash changed.")
    if before[expected["prospectivePropPreregistration"]["path"]] != expected["prospectivePropPreregistration"]["sha256"]:
        raise RuntimeError("Frozen prospective prop preregistration hash changed.")

    rules = Rules()
    scenarios: dict[str, Any] = {}
    frontier: list[dict[str, Any]] = []
    path_cache: dict[str, dict[str, Any]] = {}
    for calibration_name, calibrated in (("observed_execution_overlay", True), ("registered_costs_only", False)):
        candidate = _optimized_candidate(apply_execution_calibration=calibrated)
        values = candidate.frame[["close_return", "low_return", "high_return"]].replace([np.inf, -np.inf], np.nan).dropna().to_numpy(float)
        indices = _bootstrap_indices(len(values), paths, np.random.default_rng(SEED + sum(candidate.key.encode("utf-8"))))
        sampled = values[indices]
        calibration_results: dict[str, Any] = {"provenance": candidate.provenance, "operatingPoints": {}}
        for point in OPERATING_POINTS:
            scale = float(point["scale"])
            exposure = rules.account_size * scale
            self_paths = _self_funded_paths(sampled, exposure)
            sensitivities: dict[str, Any] = {}
            for rule in ("static", "trailing_to_breakeven"):
                arrays = _prop_paths(sampled, scale, rules, rule)
                sensitivities[rule] = _prop_summary(arrays)
                if calibrated and rule == point["drawdownRule"]:
                    frontier.extend(_frontier_rows(point, arrays, self_paths))
                    path_cache[point["key"]] = {"prop": arrays, "self": self_paths}
            primary_prop = sensitivities[point["drawdownRule"]]
            self_summary = _self_summary(self_paths, exposure)
            expected_return_rate = self_summary["netProfit"]["expected"] / exposure
            calibration_results["operatingPoints"][point["key"]] = {
                **point,
                "effectiveExposure": exposure,
                "prop": primary_prop,
                "selfFundedA": self_summary,
                "propRuleSensitivity": sensitivities,
                "breakEven": {
                    "minimumOwnCapitalToMatchExposure": exposure,
                    "ownCapitalForExpectedSelfFundedProfitToEqualProp": (
                        primary_prop["netProfit"]["expected"] / expected_return_rate if expected_return_rate > 0 else None
                    ),
                    "ownCapitalForExpectedSelfFundedProfitToReach35000": TARGET / expected_return_rate if expected_return_rate > 0 else None,
                    "expectedSelfFundedReturnPerDollar": expected_return_rate,
                },
            }
        scenarios[calibration_name] = calibration_results

    # Exact agreement with the already-published frozen aggregate prop rows.
    frozen = json.loads(FROZEN_PROP_RESULTS.read_text(encoding="utf-8"))["frontier"]
    reproduction: list[dict[str, Any]] = []
    for point in OPERATING_POINTS:
        old = next(row for row in frozen if row["candidate"] == "dm_optimized_63d_daily" and row["scale"] == point["scale"] and row["drawdownRule"] == point["drawdownRule"])
        new = scenarios["observed_execution_overlay"]["operatingPoints"][point["key"]]["prop"]
        checks = {
            "expectedNetPayout": [old["expectedNetPayout"], new["netProfit"]["expected"]],
            "medianNetPayout": [old["medianNetPayout"], new["netProfit"]["median"]],
            "breachProbability12m": [old["breachProbability12m"], new["breachProbability12m"]],
            "expectedFees": [old["expectedFees"], new["fees"]["expected"]],
        }
        max_error = max(abs(a - b) for a, b in checks.values())
        reproduction.append({"operatingPoint": point["key"], "maxAbsoluteError": max_error, "passed": max_error < 1e-8, "checks": checks})
    if not all(row["passed"] for row in reproduction):
        raise RuntimeError("Path-level simulator does not reproduce the frozen prop aggregates.")

    frame = pd.DataFrame(frontier)
    audit = _audit_matrix()
    classification = {
        "result": "depends_on_available_capital",
        "historicalEvidenceGrade": "inconclusive_for_live_capital_allocation",
        "reason": "Self-funding avoids prop fees, payout splits, and stopouts when enough own capital is available; prop accounts provide matched exposure with much less trader cash committed. The selected one-year historical sample cannot establish which advantage will dominate prospectively.",
        "selfFundedComparatorB": "omitted_no_preregistered_restart_rule",
    }
    after = {str(path.relative_to(ROOT)).replace("\\", "/"): _sha256(path) for path in FROZEN_FILES}
    if before != after:
        raise RuntimeError("A frozen prop artifact changed during the comparison study.")
    payload = {
        "study": prereg["study"],
        "preregistration": str(PREREGISTRATION.relative_to(ROOT)).replace("\\", "/"),
        "preregistrationSha256": _sha256(PREREGISTRATION),
        "frozenArtifactHashesBeforeAndAfter": {key: {"before": value, "after": after[key], "unchanged": value == after[key]} for key, value in before.items()},
        "simulation": {"paths": paths, "horizonTradingDays": HORIZON, "movingBlockLength": BLOCK, "seed": SEED, "targetNetProfit": TARGET},
        "capitalDefinitions": {"prop": prereg["propAccount"], "selfFundedA": prereg["selfFundedComparatorA"], "selfFundedB": prereg["selfFundedComparatorB"]},
        "historicalClassification": "selected development sample plus synthetic moving-block Monte Carlo",
        "prospectiveClassification": "separate append-only ledgers; not merged into these results",
        "scenarios": scenarios,
        "frontier": frontier,
        "reproductionChecks": reproduction,
        "auditMatrix": audit,
        "classification": classification,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    frame.to_csv(output_dir / "frontier.csv", index=False)
    (output_dir / "audit_matrix.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    (output_dir / "report.md").write_text(_report(payload, frame), encoding="utf-8")
    return payload


def _report(payload: dict[str, Any], frontier: pd.DataFrame) -> str:
    primary = payload["scenarios"]["observed_execution_overlay"]["operatingPoints"]
    lines = [
        "# Optimized DM: Prop versus Self-Funded Capital Efficiency v1", "",
        "**Classification: depends on available capital. Historical evidence remains inconclusive for a live-capital allocation.**", "",
        "The strategy, execution owner, prop rules, and prior prop artifacts are unchanged. Comparator B is omitted because no legitimate personal stop/restart rule was preregistered.", "",
        "## Same-exposure comparison", "",
        "| Operating point | Prop expected net | Prop median | Prop annual breach | Self-funded capital | Self-funded expected | Self-funded median | Self-funded annual loss |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for point in OPERATING_POINTS:
        row = primary[point["key"]]
        prop, own = row["prop"], row["selfFundedA"]
        lines.append(
            f"| {point['scale']:.2f}x / {point['drawdownRule'].replace('_', ' ')} | ${prop['netProfit']['expected']:,.0f} | ${prop['netProfit']['median']:,.0f} | {prop['breachProbability12m']:.1%} | ${row['effectiveExposure']:,.0f} | ${own['netProfit']['expected']:,.0f} | ${own['netProfit']['median']:,.0f} | {own['probabilityAnnualLoss']:.1%} |"
        )
    lines += ["", "## $35,000 target frontier", "",
              "Accounts are identical and perfectly correlated. Adding accounts scales dollars, not independent bets; breach probability therefore does not diversify.", "",
              "| Point | Structure | Accounts | Exposure | Expected net | Median net | P(net ≥ $35k) | Capital committed |",
              "|---|---|---:|---:|---:|---:|---:|---:|"]
    for row in frontier.itertuples():
        if row.accounts in (1, 5, 10):
            lines.append(f"| {row.operatingPoint} | {row.structure} | {row.accounts} | ${row.aggregateEffectiveExposure:,.0f} | ${row.expectedNetProfit:,.0f} | ${row.medianNetProfit:,.0f} | {row.probabilityTarget35000:.1%} | ${row.expectedCapitalCommitted:,.0f} |")
    lines += ["", "## Interpretation", "",
              "Self-funding is mechanically cleaner once sufficient own capital is available: no challenge/reset fees, payout split, or rule-based termination. Prop access commits only fees and can therefore be more capital-efficient for a capital-constrained trader, but the contractual stopout path remains and multiple identical accounts do not diversify it.", "",
              "Daily OHLC historical breach tests remain approximate. The separate five-minute prospective ledger is the authoritative path-evidence upgrade and starts without backfill.", "",
              "No scale or account count is selected by this report.", "",
              "## Break-even capital and downside", "",
              "The profit-equivalence capital below answers how much self-funded capital would have the same *expected* profit as one prop account at the simulated per-dollar return. It does not match exposure. Matching exposure still requires the full $20,000 or $25,000.", "",
              "| Point | Profit-equivalence own capital | Own capital for $35k expected profit | Self-funded P05 profit | P99 max drawdown | P(max DD at least $6k) |",
              "|---|---:|---:|---:|---:|---:|"]
    for point in OPERATING_POINTS:
        row = primary[point["key"]]
        own, brk = row["selfFundedA"], row["breakEven"]
        lines.append(
            f"| {point['key']} | ${brk['ownCapitalForExpectedSelfFundedProfitToEqualProp']:,.0f} | ${brk['ownCapitalForExpectedSelfFundedProfitToReach35000']:,.0f} | ${own['netProfit']['p05']:,.0f} | ${own['maxDrawdownDollars']['p99']:,.0f} | {own['probabilityCrossesEquivalent6000Drawdown']:.1%} |"
        )
    lines += ["", "## Approved sensitivities", "",
              "| Point | Cost calibration | Prop rule | Expected prop net | Annual breach |",
              "|---|---|---|---:|---:|"]
    for calibration, scenario in payload["scenarios"].items():
        for point in OPERATING_POINTS:
            item = scenario["operatingPoints"][point["key"]]
            for rule, values in item["propRuleSensitivity"].items():
                lines.append(f"| {point['key']} | {calibration.replace('_', ' ')} | {rule.replace('_', ' ')} | ${values['netProfit']['expected']:,.0f} | {values['breachProbability12m']:.1%} |")
    lines += ["", "## Infrastructure audit", "",
              "| Requirement | Status | Active | Remaining gap |",
              "|---|---|---:|---|"]
    for row in payload["auditMatrix"]:
        lines.append(f"| {row['requirement']} | {row['status']} | {'yes' if row['active'] else 'no'} | {row.get('gap') or 'none'} |")
    lines += ["", "## Evidence separation", "",
              "Historical results are the selected 250-session development sample plus 5,000 synthetic moving-block paths. Prospective prop and self-funded ledgers consume the actual parent brokerage mark stream, remain append-only, and are not pooled with historical results. All referenced frozen artifact hashes were verified unchanged before and after this run."]
    return "\n".join(lines) + "\n"


def status(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    result_path = output_dir / "results.json"
    if not result_path.exists():
        return {"available": False, "reason": "Capital-efficiency study has not been run."}
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    return {
        "available": True,
        "classification": payload["classification"],
        "historicalClassification": payload["historicalClassification"],
        "prospectiveClassification": payload["prospectiveClassification"],
        "operatingPoints": payload["scenarios"]["observed_execution_overlay"]["operatingPoints"],
        "frontier": payload["frontier"],
        "reproductionChecks": payload["reproductionChecks"],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", type=int, default=N_PATHS)
    args = parser.parse_args()
    result = run(paths=args.paths)
    print(json.dumps({"classification": result["classification"], "output": str(OUTPUT_DIR)}, indent=2))


if __name__ == "__main__":
    main()
