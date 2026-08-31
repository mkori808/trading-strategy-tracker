"""Preregistered four-state persistence/reversal audit for five momentum portfolios."""
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
    _block_bootstrap_difference,
    _normalize_series,
    _strategy_frame,
)
from engine.prop_scaling_survival_study import CandidatePath, _blend

ROOT = Path(__file__).resolve().parent.parent
PREREGISTRATION = ROOT / "research" / "momentum_persistence_reversal_v1_preregistration.json"
REGIME_PREREGISTRATION = ROOT / "research" / "dm_regime_dependence_v1_preregistration.json"
OUTPUT_DIR = ROOT / "reports" / "momentum_persistence_reversal_v1"
STATES = ("bullish_persistence", "pullback", "bearish_persistence", "rebound")
PERSISTENCE = frozenset(("bullish_persistence", "bearish_persistence"))
BOOTSTRAPS = 5_000
BLOCK = 21
SEED = 20_260_827


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _candidate(key: str, label: str, frame: pd.DataFrame, provenance: dict[str, Any]) -> CandidatePath:
    values = frame["return"].rename("close_return")
    envelope = pd.DataFrame({
        "close_return": values,
        "low_return": values,
        "high_return": values,
    }, index=values.index)
    return CandidatePath(key, label, envelope, provenance)


def _strategy_paths(regime_prereg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    optimized, _, optimized_provenance = _strategy_frame(33, regime_prereg)
    canonical_dm, _, dm_provenance = _strategy_frame(26, regime_prereg)
    mrm, _, mrm_provenance = _strategy_frame(24, regime_prereg)
    dm_candidate = _candidate("canonical_dm_189d_monthly", "Canonical DM", canonical_dm, dm_provenance)
    mrm_candidate = _candidate("canonical_mrm", "Canonical MRM", mrm, mrm_provenance)
    fixed = _blend(dm_candidate, mrm_candidate, vol_scaled=False)
    vol_scaled = _blend(dm_candidate, mrm_candidate, vol_scaled=True)
    return {
        "dm_optimized_63d_daily": {
            "label": "Optimized DM 63D/Daily", "return": optimized["return"],
            "provenance": optimized_provenance,
        },
        "canonical_dm_189d_monthly": {
            "label": "Canonical DM 189D/Monthly", "return": canonical_dm["return"],
            "provenance": dm_provenance,
        },
        "canonical_mrm": {
            "label": "Canonical MRM", "return": mrm["return"],
            "provenance": mrm_provenance,
        },
        "fixed_50_50_dm_mrm": {
            "label": "Fixed 50/50 DM/MRM", "return": fixed.frame["close_return"],
            "provenance": fixed.provenance,
        },
        "vol_scaled_dm_mrm": {
            "label": "Vol-Scaled DM/MRM", "return": vol_scaled.frame["close_return"],
            "provenance": vol_scaled.provenance,
        },
    }


def _state_frame(index: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict[str, Any]]:
    bars = data_module.get_bars(
        "SPY", "1d", index.min().date() - timedelta(days=430), index.max().date()
    )
    if bars.empty:
        raise RuntimeError("SPY daily bars are unavailable for persistence-state construction.")
    close = _normalize_series(bars["Close"])
    prior_close = close.shift(1)
    prior_sma = prior_close.rolling(200, min_periods=200).mean()
    long_trend = prior_close > prior_sma
    prior_21_return = prior_close / prior_close.shift(21) - 1.0
    short_positive = prior_21_return > 0
    known = prior_sma.notna() & prior_21_return.notna()
    state = pd.Series(pd.NA, index=close.index, dtype="string")
    state.loc[known & long_trend & short_positive] = "bullish_persistence"
    state.loc[known & long_trend & ~short_positive] = "pullback"
    state.loc[known & ~long_trend & ~short_positive] = "bearish_persistence"
    state.loc[known & ~long_trend & short_positive] = "rebound"
    output = pd.DataFrame({
        "spy_return": close.pct_change(fill_method=None),
        "state": state,
        "prior_spy_close": prior_close,
        "prior_spy_sma_200": prior_sma,
        "prior_spy_return_21": prior_21_return,
    }).reindex(index)
    return output, {
        "spyBars": len(close),
        "missingStateObservations": int(output["state"].isna().sum()),
        "firstUsableState": (
            output["state"].dropna().index[0].date().isoformat()
            if output["state"].notna().any() else None
        ),
        "informationCutoff": "T-1 close for both state inputs",
    }


def _state_metrics(sample: pd.DataFrame, total: int) -> dict[str, Any]:
    if sample.empty:
        return {
            "n": 0,
            "observationShare": 0.0 if total else None,
            "calendarYears": [],
            "meanDailyReturn": None,
            "annualizedMeanReturn": None,
            "meanDailyActiveReturn": None,
            "annualizedMeanActiveReturn": None,
            "positiveReturnRate": None,
            "positiveActiveReturnRate": None,
            "worstDay": None,
            "stateAttributedMaximumDrawdown": None,
            "stateAttributedCumulativeReturn": None,
        }
    returns = sample["return"]
    active = sample["active_return"]
    equity = (1.0 + returns).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return {
        "n": len(sample),
        "observationShare": float(len(sample) / total) if total else None,
        "calendarYears": sorted(int(year) for year in sample.index.year.unique()),
        "meanDailyReturn": float(returns.mean()),
        "annualizedMeanReturn": float(returns.mean() * 252.0),
        "meanDailyActiveReturn": float(active.mean()),
        "annualizedMeanActiveReturn": float(active.mean() * 252.0),
        "positiveReturnRate": float((returns > 0).mean()),
        "positiveActiveReturnRate": float((active > 0).mean()),
        "worstDay": float(returns.min()),
        "stateAttributedMaximumDrawdown": float(drawdown.min()),
        "stateAttributedCumulativeReturn": float(equity.iloc[-1] - 1.0),
    }


def _loyo(frame: pd.DataFrame, full_difference: float) -> dict[str, Any]:
    folds = []
    for year in sorted(frame.index.year.unique()):
        sample = frame.loc[frame.index.year != year]
        persistence = sample.loc[sample["is_persistence"], "active_return"]
        transition = sample.loc[~sample["is_persistence"], "active_return"]
        difference = (
            float(persistence.mean() - transition.mean())
            if len(persistence) and len(transition) else None
        )
        folds.append({
            "leftOutYear": int(year), "differenceMeanDailyActiveReturn": difference,
            "nPersistence": len(persistence), "nTransition": len(transition),
        })
    usable = [row["differenceMeanDailyActiveReturn"] for row in folds if row["differenceMeanDailyActiveReturn"] is not None]
    direction = np.sign(full_difference)
    agreement = (
        float(np.mean([np.sign(value) == direction for value in usable]))
        if usable and direction else None
    )
    return {"folds": folds, "usableFolds": len(usable), "directionalAgreement": agreement}


def _primary_test(frame: pd.DataFrame, strategy_number: int) -> dict[str, Any]:
    sample = frame.dropna(subset=["return", "active_return", "state"]).copy()
    sample["is_persistence"] = sample["state"].isin(PERSISTENCE)
    persistence = sample.loc[sample["is_persistence"]]
    transition = sample.loc[~sample["is_persistence"]]
    difference = float(persistence["active_return"].mean() - transition["active_return"].mean())
    bootstrap = _block_bootstrap_difference(
        sample["active_return"].to_numpy(float),
        sample["is_persistence"].to_numpy(bool),
        np.random.default_rng(SEED + strategy_number),
    )
    return {
        "usableObservations": len(sample),
        "calendarYears": sorted(int(year) for year in sample.index.year.unique()),
        "persistence": {
            "n": len(persistence),
            "meanDailyReturn": float(persistence["return"].mean()),
            "meanDailyActiveReturn": float(persistence["active_return"].mean()),
        },
        "transition": {
            "n": len(transition),
            "meanDailyReturn": float(transition["return"].mean()),
            "meanDailyActiveReturn": float(transition["active_return"].mean()),
        },
        "differenceMeanDailyActiveReturn": difference,
        "differenceAnnualizedMeanActiveReturn": difference * 252.0,
        "bootstrap": bootstrap,
        "leaveOneYearOut": _loyo(sample, difference),
    }


def _secondary_contrasts(states: dict[str, dict[str, Any]]) -> dict[str, Any]:
    def difference(left: str, right: str) -> dict[str, float | None]:
        left_return = states[left]["meanDailyReturn"]
        right_return = states[right]["meanDailyReturn"]
        left_active = states[left]["meanDailyActiveReturn"]
        right_active = states[right]["meanDailyActiveReturn"]
        return {
            "differenceMeanDailyReturn": (
                left_return - right_return
                if left_return is not None and right_return is not None else None
            ),
            "differenceMeanDailyActiveReturn": (
                left_active - right_active
                if left_active is not None and right_active is not None else None
            ),
        }
    return {
        "bullishPersistenceMinusRebound": difference("bullish_persistence", "rebound"),
        "bearishPersistenceMinusPullback": difference("bearish_persistence", "pullback"),
        "inference": "descriptive_only_cannot_rescue_primary_test",
    }


def _classify(row: dict[str, Any], evidence_cap: str, gates: dict[str, Any]) -> tuple[str, list[str]]:
    reasons = []
    if row["persistence"]["n"] < gates["minimumObservationsPerPrimarySide"] or row["transition"]["n"] < gates["minimumObservationsPerPrimarySide"]:
        reasons.append("fewer than 63 observations on at least one primary side")
    if len(row["calendarYears"]) < gates["minimumContributingCalendarYears"]:
        reasons.append("fewer than three contributing calendar years")
    if row["minimumDisplayedStateN"] < gates["minimumObservationsPerDisplayedState"]:
        reasons.append("fewer than 21 observations in at least one displayed state")
    if row["differenceMeanDailyActiveReturn"] <= 0:
        reasons.append("persistence-minus-transition active return is not positive")
    if not (row["bootstrap"]["ciLow"] > 0 or row["bootstrap"]["ciHigh"] < 0):
        reasons.append("95% block-bootstrap interval includes zero")
    if row["qValue"] > gates["maximumBhQ"]:
        reasons.append("five-test BH q-value exceeds 0.10")
    agreement = row["leaveOneYearOut"]["directionalAgreement"]
    if agreement is None or agreement < gates["minimumLeaveOneYearOutDirectionalAgreement"]:
        reasons.append("leave-one-year-out directional agreement is below 80%")
    if row["persistence"]["meanDailyActiveReturn"] <= 0:
        reasons.append("persistence-state mean active return is not positive")
    if evidence_cap.startswith("exploratory"):
        reasons.append("historical selection/design contamination caps this strategy at exploratory evidence")
        return "exploratory_historical", reasons
    return ("stable_candidate_for_independent_validation" if not reasons else "unsupported_or_unstable"), reasons


def run(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    regime_prereg = json.loads(REGIME_PREREGISTRATION.read_text(encoding="utf-8"))
    evidence_caps = {row["key"]: row["evidenceCap"] for row in prereg["strategySources"]}
    strategies = _strategy_paths(regime_prereg)
    primary_tests = []
    for strategy_number, (key, item) in enumerate(strategies.items()):
        returns = _normalize_series(item["return"])
        states, diagnostics = _state_frame(returns.index)
        frame = pd.DataFrame({"return": returns}).join(states)
        frame["active_return"] = frame["return"] - frame["spy_return"]
        usable = frame.dropna(subset=["return", "active_return", "state"])
        state_metrics = {
            state: _state_metrics(usable.loc[usable["state"] == state], len(usable))
            for state in STATES
        }
        primary = {"strategy": key, **_primary_test(frame, strategy_number)}
        primary["minimumDisplayedStateN"] = min(
            metrics["n"] for metrics in state_metrics.values()
        )
        primary_tests.append(primary)
        populated_states = [
            state for state in STATES
            if state_metrics[state]["meanDailyReturn"] is not None
        ]
        item.update({
            "evidenceCap": evidence_caps[key],
            "stateDiagnostics": diagnostics,
            "usableObservations": len(usable),
            "states": state_metrics,
            "bestStateByMeanReturn": max(
                populated_states, key=lambda state: state_metrics[state]["meanDailyReturn"]
            ) if populated_states else None,
            "bestStateByMeanActiveReturn": max(
                populated_states, key=lambda state: state_metrics[state]["meanDailyActiveReturn"]
            ) if populated_states else None,
            "secondaryContrasts": _secondary_contrasts(state_metrics),
            "primaryTest": primary,
        })
        item.pop("return")
    _bh(primary_tests)
    for row in primary_tests:
        classification, reasons = _classify(
            row, evidence_caps[row["strategy"]], prereg["stabilityGates"]
        )
        row["classification"] = classification
        row["classificationReasons"] = reasons
    payload = {
        "study": prereg["study"],
        "preregistration": str(PREREGISTRATION.relative_to(ROOT)).replace("\\", "/"),
        "preregistrationSha256": _sha256(PREREGISTRATION),
        "regimeExecutionPreregistrationSha256": _sha256(REGIME_PREREGISTRATION),
        "simulation": {"bootstrapReplications": BOOTSTRAPS, "blockLength": BLOCK, "seed": SEED},
        "strategies": strategies,
        "primaryTests": primary_tests,
        "interpretationGuards": prereg["interpretationGuards"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    pd.DataFrame([{
        "strategy": row["strategy"],
        "n_persistence": row["persistence"]["n"], "n_transition": row["transition"]["n"],
        "persistence_mean_active": row["persistence"]["meanDailyActiveReturn"],
        "transition_mean_active": row["transition"]["meanDailyActiveReturn"],
        "difference_daily_active": row["differenceMeanDailyActiveReturn"],
        "difference_annualized_active": row["differenceAnnualizedMeanActiveReturn"],
        "ci_low": row["bootstrap"]["ciLow"], "ci_high": row["bootstrap"]["ciHigh"],
        "p_value": row["bootstrap"]["twoSidedP"], "q_value": row["qValue"],
        "loyo_agreement": row["leaveOneYearOut"]["directionalAgreement"],
        "classification": row["classification"],
    } for row in primary_tests]).to_csv(output_dir / "primary_tests.csv", index=False)
    state_rows = []
    for key, item in strategies.items():
        for state, metrics in item["states"].items():
            state_rows.append({"strategy": key, "state": state, **metrics})
    pd.DataFrame(state_rows).to_csv(output_dir / "state_metrics.csv", index=False)
    (output_dir / "report.md").write_text(_report(payload), encoding="utf-8")
    return payload


def _report(payload: dict[str, Any]) -> str:
    def pct(value: float | None, digits: int = 2, signed: bool = True) -> str:
        if value is None or not np.isfinite(value):
            return "n/a"
        sign = "+" if signed else ""
        return f"{value:{sign}.{digits}%}"

    lines = [
        "# Momentum Persistence / Reversal Regime Audit v1", "",
        "Primary outcome is daily strategy return minus SPY return. Every state is known by T-1. No trading filter was created.", "",
        "## Primary persistence-versus-transition tests", "",
        "| Strategy | Persistence N | Transition N | Persistence active mean | Transition active mean | Difference annualized | 95% block CI daily | BH q | LOYO | Classification |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload["primaryTests"]:
        item = payload["strategies"][row["strategy"]]
        agreement = row["leaveOneYearOut"]["directionalAgreement"]
        lines.append(
            f"| {item['label']} | {row['persistence']['n']} | {row['transition']['n']} | "
            f"{row['persistence']['meanDailyActiveReturn']:+.3%} | {row['transition']['meanDailyActiveReturn']:+.3%} | "
            f"{row['differenceAnnualizedMeanActiveReturn']:+.2%} | [{row['bootstrap']['ciLow']:+.3%}, {row['bootstrap']['ciHigh']:+.3%}] | "
            f"{row['qValue']:.3f} | {'n/a' if agreement is None else f'{agreement:.0%}'} | {row['classification'].replace('_', ' ')} |"
        )
    lines += ["", "## Four-state descriptive map", ""]
    for key, item in payload["strategies"].items():
        lines += [f"### {item['label']}", "", "| State | N | Share | Annualized mean return | Annualized mean active return | Positive days | Worst day | State-attributed max DD |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
        for state in STATES:
            metrics = item["states"][state]
            lines.append(
                f"| {state.replace('_', ' ')} | {metrics['n']} | {pct(metrics['observationShare'], 1, False)} | "
                f"{pct(metrics['annualizedMeanReturn'])} | {pct(metrics['annualizedMeanActiveReturn'])} | "
                f"{pct(metrics['positiveReturnRate'], 1, False)} | {pct(metrics['worstDay'])} | "
                f"{pct(metrics['stateAttributedMaximumDrawdown'])} |"
            )
        best_return = item["bestStateByMeanReturn"]
        best_active = item["bestStateByMeanActiveReturn"]
        lines += [
            "",
            f"Descriptive best absolute-return state: **{best_return.replace('_', ' ') if best_return else 'n/a'}**. "
            f"Best SPY-relative state: **{best_active.replace('_', ' ') if best_active else 'n/a'}**.",
            "",
        ]
    lines += [
        "## Guardrails", "",
        "The primary family contains five tests and uses one BH correction. State rankings and secondary contrasts are descriptive and cannot rescue a failed primary result. Optimized DM and both derived overlays remain exploratory because their selection/design used this historical period. No live rule, order, or size changed.",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    argparse.ArgumentParser().parse_args()
    result = run()
    print(json.dumps({"study": result["study"], "output": str(OUTPUT_DIR)}, indent=2))


if __name__ == "__main__":
    main()
