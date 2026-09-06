"""Preregistered loss-day antecedent audit for DM underperformance vs SPY."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine import data as data_module
from engine.dm_regime_dependence import (
    _bh,
    _condition_frame,
    _normalize_series,
    _strategy_frame,
)

ROOT = Path(__file__).resolve().parent.parent
PREREGISTRATION = ROOT / "research" / "dm_spy_underperformance_v1_preregistration.json"
REGIME_PREREGISTRATION = ROOT / "research" / "dm_regime_dependence_v1_preregistration.json"
OUTPUT_DIR = ROOT / "reports" / "dm_spy_underperformance_v1"
QUALIFICATIONS = (
    "spy_trend", "market_volatility", "cross_sectional_dispersion",
    "prior_drawdown_5pct", "strategy_volatility",
)
BOOTSTRAPS = 5_000
BLOCK = 21
SEED = 20_260_826


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _spy_returns(index: pd.DatetimeIndex) -> pd.Series:
    bars = data_module.get_bars(
        "SPY", "1d", index.min().date() - timedelta(days=10), index.max().date()
    )
    if bars.empty:
        raise RuntimeError("SPY bars unavailable for active-return calculation.")
    return _normalize_series(bars["Close"]).pct_change(fill_method=None).reindex(index)


def _analysis_frame(run_id: int, regime_prereg: dict[str, Any]) -> tuple[pd.DataFrame, dict[str, Any]]:
    frame, result, provenance = _strategy_frame(run_id, regime_prereg)
    conditions, diagnostics = _condition_frame(result, frame.index)
    frame = frame.join(conditions)
    frame["spy_return"] = _spy_returns(frame.index)
    frame["active_return"] = frame["return"] - frame["spy_return"]
    frame["underperformed_spy"] = frame["active_return"] < 0
    frame["absolute_loss"] = frame["return"] < 0
    frame["joint_damaging"] = frame["absolute_loss"] & frame["underperformed_spy"]

    equity = (1.0 + frame["return"]).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    frame["prior_drawdown_5pct"] = (drawdown.shift(1) <= -0.05).astype("boolean")
    strategy_vol = frame["return"].rolling(21, min_periods=21).std(ddof=1) * np.sqrt(252.0)
    prior_vol = strategy_vol.shift(1)
    prior_reference = strategy_vol.shift(2).rolling(63, min_periods=63).median()
    frame["strategy_volatility"] = (prior_vol > prior_reference).where(prior_reference.notna()).astype("boolean")

    rolling_cov = frame["return"].rolling(252, min_periods=126).cov(frame["spy_return"])
    rolling_var = frame["spy_return"].rolling(252, min_periods=126).var()
    frame["prior_beta"] = (rolling_cov / rolling_var).shift(1)
    frame["beta_adjusted_residual"] = frame["return"] - frame["prior_beta"] * frame["spy_return"]
    frame["residual_underperformed"] = frame["beta_adjusted_residual"] < 0
    return frame, {"provenance": provenance, "conditionDiagnostics": diagnostics}


def _event_summary(frame: pd.DataFrame) -> dict[str, Any]:
    aligned = frame.dropna(subset=["return", "spy_return", "active_return"])
    loss, under = aligned["absolute_loss"], aligned["underperformed_spy"]
    quadrant_masks = {
        "loss_and_underperformed": loss & under,
        "loss_but_outperformed": loss & ~under,
        "profit_but_underperformed": ~loss & under,
        "profit_and_outperformed": ~loss & ~under,
    }
    split = max(1, int(len(aligned) * 0.60))
    training, holdout = aligned.iloc[:split], aligned.iloc[split:]
    severe_cutoff = float(training["active_return"].quantile(0.10))
    beta_usable = aligned.dropna(subset=["beta_adjusted_residual"])
    return {
        "alignedObservations": len(aligned),
        "underperformanceEvents": int(under.sum()),
        "underperformanceRate": float(under.mean()),
        "absoluteLossRate": float(loss.mean()),
        "jointDamagingRate": float((loss & under).mean()),
        "quadrants": {
            key: {"n": int(mask.sum()), "rate": float(mask.mean())}
            for key, mask in quadrant_masks.items()
        },
        "activeReturn": {
            "mean": float(aligned["active_return"].mean()),
            "median": float(aligned["active_return"].median()),
            "p10": float(aligned["active_return"].quantile(0.10)),
            "worst": float(aligned["active_return"].min()),
        },
        "severeRelativeMiss": {
            "trainingObservations": len(training), "holdoutObservations": len(holdout),
            "frozenTrainingP10Cutoff": severe_cutoff,
            "holdoutEvents": int((holdout["active_return"] <= severe_cutoff).sum()),
            "holdoutEventRate": float((holdout["active_return"] <= severe_cutoff).mean()),
        },
        "betaAdjustedSensitivity": {
            "observations": len(beta_usable),
            "residualUnderperformanceRate": (
                float(beta_usable["residual_underperformed"].mean()) if len(beta_usable) else None
            ),
            "meanResidualReturn": (
                float(beta_usable["beta_adjusted_residual"].mean()) if len(beta_usable) else None
            ),
        },
    }


def _effect(frame: pd.DataFrame, qualification: str) -> dict[str, Any]:
    usable = frame.dropna(subset=["underperformed_spy", qualification, "active_return"]).copy()
    qualified = usable[qualification].astype(bool)
    event = usable["underperformed_spy"].astype(bool)
    n_true, n_false = int(qualified.sum()), int((~qualified).sum())
    events_true = int((event & qualified).sum())
    events_false = int((event & ~qualified).sum())
    risk_true = events_true / n_true if n_true else np.nan
    risk_false = events_false / n_false if n_false else np.nan
    risk_difference = risk_true - risk_false
    risk_ratio = risk_true / risk_false if risk_false > 0 else None
    # Haldane-Anscombe correction keeps a finite descriptive odds ratio if a
    # sparse cell is zero; it does not manufacture inferential evidence.
    a, b = events_true + 0.5, n_true - events_true + 0.5
    c, d = events_false + 0.5, n_false - events_false + 0.5
    odds_ratio = (a * d) / (b * c)
    return {
        "usableObservations": len(usable),
        "calendarYears": sorted(int(year) for year in usable.index.year.unique()),
        "qualificationBaseRate": float(qualified.mean()),
        "qualified": {"n": n_true, "events": events_true, "eventRate": risk_true},
        "notQualified": {"n": n_false, "events": events_false, "eventRate": risk_false},
        "riskDifference": float(risk_difference),
        "riskRatio": None if risk_ratio is None else float(risk_ratio),
        "oddsRatio": float(odds_ratio),
        "precision": float(risk_true),
        "recall": float(events_true / int(event.sum())) if event.sum() else None,
        "meanActiveReturnQualified": float(usable.loc[qualified, "active_return"].mean()),
        "meanActiveReturnNotQualified": float(usable.loc[~qualified, "active_return"].mean()),
        "meanEventSeverityQualified": (
            float(usable.loc[qualified & event, "active_return"].mean()) if events_true else None
        ),
        "meanEventSeverityNotQualified": (
            float(usable.loc[(~qualified) & event, "active_return"].mean()) if events_false else None
        ),
        "_event": event.to_numpy(bool),
        "_qualification": qualified.to_numpy(bool),
        "_years": usable.index.year.to_numpy(),
    }


def _bootstrap(event: np.ndarray, qualification: np.ndarray, rng: np.random.Generator) -> dict[str, Any]:
    n = len(event)
    blocks = int(np.ceil(n / BLOCK))
    starts = rng.integers(0, n, size=(BOOTSTRAPS, blocks))
    indices = ((starts[:, :, None] + np.arange(BLOCK)) % n).reshape(BOOTSTRAPS, -1)[:, :n]
    sampled_event, sampled_qualification = event[indices], qualification[indices]
    n_true = sampled_qualification.sum(axis=1)
    n_false = n - n_true
    valid = (n_true > 0) & (n_false > 0)
    risk_true = (sampled_event & sampled_qualification).sum(axis=1)[valid] / n_true[valid]
    risk_false = (sampled_event & ~sampled_qualification).sum(axis=1)[valid] / n_false[valid]
    differences = risk_true - risk_false
    return {
        "replications": len(differences),
        "ciLow": float(np.percentile(differences, 2.5)),
        "ciHigh": float(np.percentile(differences, 97.5)),
        "twoSidedP": float(min(1.0, 2.0 * min(np.mean(differences <= 0), np.mean(differences >= 0)))),
    }


def _loyo(event: np.ndarray, qualification: np.ndarray, years: np.ndarray, full: float) -> dict[str, Any]:
    folds = []
    for year in sorted(np.unique(years)):
        keep = years != year
        q, e = qualification[keep], event[keep]
        if q.sum() == 0 or (~q).sum() == 0:
            difference = None
        else:
            difference = float(e[q].mean() - e[~q].mean())
        folds.append({"leftOutYear": int(year), "riskDifference": difference,
                      "nQualified": int(q.sum()), "nNotQualified": int((~q).sum())})
    usable = [row["riskDifference"] for row in folds if row["riskDifference"] is not None]
    direction = np.sign(full)
    agreement = float(np.mean([np.sign(value) == direction for value in usable])) if usable and direction else None
    return {"folds": folds, "usableFolds": len(usable), "directionalAgreement": agreement}


def _classify(row: dict[str, Any], optimized: bool, total_events: int) -> tuple[str, list[str]]:
    reasons = []
    if row["qualified"]["n"] < 63 or row["notQualified"]["n"] < 63:
        reasons.append("fewer than 63 observations in at least one qualification state")
    if total_events < 30:
        reasons.append("fewer than 30 SPY-underperformance events")
    if len(row["calendarYears"]) < 3:
        reasons.append("fewer than three contributing calendar years")
    if not (row["bootstrap"]["ciLow"] > 0 or row["bootstrap"]["ciHigh"] < 0):
        reasons.append("95% block-bootstrap risk-difference interval includes zero")
    if row["qValue"] > 0.10:
        reasons.append("10-test BH q-value exceeds 0.10")
    agreement = row["leaveOneYearOut"]["directionalAgreement"]
    if agreement is None or agreement < 0.80:
        reasons.append("leave-one-year-out directional agreement is below 80%")
    if optimized:
        reasons.append("optimized configuration is selection-contaminated and capped at exploratory historical evidence")
        return "exploratory_historical", reasons
    return ("stable_candidate_for_independent_validation" if not reasons else "unsupported_or_unstable"), reasons


def run(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    regime_prereg = json.loads(REGIME_PREREGISTRATION.read_text(encoding="utf-8"))
    strategies: dict[str, Any] = {}
    tests: list[dict[str, Any]] = []
    for strategy_number, (key, run_id) in enumerate((
        ("dm_optimized_63d_daily", 33), ("canonical_dm_189d_monthly", 26),
    )):
        frame, provenance = _analysis_frame(run_id, regime_prereg)
        summary = _event_summary(frame)
        strategy_tests = []
        for qualification_number, qualification in enumerate(QUALIFICATIONS):
            row = {"strategy": key, "qualification": qualification, **_effect(frame, qualification)}
            event = row.pop("_event"); state = row.pop("_qualification"); years = row.pop("_years")
            row["bootstrap"] = _bootstrap(
                event, state, np.random.default_rng(SEED + strategy_number * 100 + qualification_number)
            )
            row["leaveOneYearOut"] = _loyo(event, state, years, row["riskDifference"])
            tests.append(row); strategy_tests.append(row)
        strategies[key] = {**provenance, "outcomes": summary, "tests": strategy_tests}
    _bh(tests)
    for row in tests:
        classification, reasons = _classify(
            row, row["strategy"] == "dm_optimized_63d_daily",
            strategies[row["strategy"]]["outcomes"]["underperformanceEvents"],
        )
        row["classification"] = classification
        row["classificationReasons"] = reasons
    payload = {
        "study": prereg["study"],
        "preregistration": str(PREREGISTRATION.relative_to(ROOT)).replace("\\", "/"),
        "preregistrationSha256": _sha256(PREREGISTRATION),
        "regimePreregistrationSha256": _sha256(REGIME_PREREGISTRATION),
        "simulation": {"bootstrapReplications": BOOTSTRAPS, "blockLength": BLOCK, "seed": SEED},
        "strategies": strategies, "primaryTests": tests,
        "interpretationGuards": prereg["interpretationGuards"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    pd.DataFrame([{
        "strategy": row["strategy"], "qualification": row["qualification"],
        "n_qualified": row["qualified"]["n"], "n_not_qualified": row["notQualified"]["n"],
        "event_rate_qualified": row["qualified"]["eventRate"],
        "event_rate_not_qualified": row["notQualified"]["eventRate"],
        "risk_difference": row["riskDifference"], "risk_ratio": row["riskRatio"],
        "odds_ratio": row["oddsRatio"], "precision": row["precision"], "recall": row["recall"],
        "ci_low": row["bootstrap"]["ciLow"], "ci_high": row["bootstrap"]["ciHigh"],
        "p_value": row["bootstrap"]["twoSidedP"], "q_value": row["qValue"],
        "loyo_agreement": row["leaveOneYearOut"]["directionalAgreement"],
        "classification": row["classification"],
    } for row in tests]).to_csv(output_dir / "primary_tests.csv", index=False)
    (output_dir / "report.md").write_text(_report(payload), encoding="utf-8")
    return payload


def _report(payload: dict[str, Any]) -> str:
    labels = {"dm_optimized_63d_daily": "Optimized DM 63D/Daily", "canonical_dm_189d_monthly": "Canonical DM 189D/Monthly"}
    lines = ["# Dual Momentum SPY-Underperformance Antecedent Audit v1", "",
             "Primary event: same-day strategy return minus SPY return is below zero. Every qualification is known by T-1.", "",
             "## Event decomposition", "",
             "| Strategy | Days | Underperform rate | Absolute loss rate | Joint damaging rate | Severe holdout rate | Beta-adjusted residual underperform rate |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for key, item in payload["strategies"].items():
        out = item["outcomes"]
        beta = out["betaAdjustedSensitivity"]["residualUnderperformanceRate"]
        lines.append(f"| {labels[key]} | {out['alignedObservations']} | {out['underperformanceRate']:.1%} | {out['absoluteLossRate']:.1%} | {out['jointDamagingRate']:.1%} | {out['severeRelativeMiss']['holdoutEventRate']:.1%} | {'n/a' if beta is None else f'{beta:.1%}'} |")
    lines += ["", "## Primary qualification tests", "",
              "| Strategy | T-1 qualification | Event rate true | Event rate false | Risk difference | Risk ratio | 95% block CI | BH q | LOYO agreement | Classification |",
              "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for row in payload["primaryTests"]:
        agreement = row["leaveOneYearOut"]["directionalAgreement"]
        risk_ratio = "n/a" if row["riskRatio"] is None else f"{row['riskRatio']:.2f}"
        agreement_text = "n/a" if agreement is None else f"{agreement:.0%}"
        lines.append(f"| {labels[row['strategy']]} | {row['qualification'].replace('_', ' ')} | {row['qualified']['eventRate']:.1%} | {row['notQualified']['eventRate']:.1%} | {row['riskDifference']:+.1%} | {risk_ratio} | [{row['bootstrap']['ciLow']:+.1%}, {row['bootstrap']['ciHigh']:+.1%}] | {row['qValue']:.3f} | {agreement_text} | {row['classification'].replace('_', ' ')} |")
    lines += ["", "## Guardrails", "",
              "These are predictive-base-rate comparisons, not counts of qualifications among bad days alone. Secondary absolute-loss, severe-miss, and beta-adjusted outcomes cannot promote a failed primary result. Optimized DM remains exploratory because its configuration was selected on the analyzed history. No live rule changed."]
    return "\n".join(lines) + "\n"


def main() -> None:
    argparse.ArgumentParser().parse_args()
    result = run()
    print(json.dumps({"study": result["study"], "output": str(OUTPUT_DIR)}, indent=2))


if __name__ == "__main__":
    main()
