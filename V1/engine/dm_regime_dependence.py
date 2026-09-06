"""Preregistered regime-dependence audit for two frozen Dual Momentum variants."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine import data as data_module, logging_db
from engine.prop_scaling_survival_study import _turnover
from engine.runner import RunRequest, run_cross_sectional

ROOT = Path(__file__).resolve().parent.parent
PREREGISTRATION = ROOT / "research" / "dm_regime_dependence_v1_preregistration.json"
OUTPUT_DIR = ROOT / "reports" / "dm_regime_dependence_v1"
BOOTSTRAPS = 5_000
BLOCK = 21
SEED = 20_260_826
CONDITIONS = ("spy_trend", "market_volatility", "cross_sectional_dispersion")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portfolio_row(run_id: int) -> dict[str, Any]:
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    row = conn.execute("SELECT * FROM portfolio_runs WHERE id=?", (run_id,)).fetchone()
    conn.close()
    if row is None:
        raise RuntimeError(f"Frozen portfolio run {run_id} is missing.")
    return dict(row)


def _normalize_series(series: pd.Series) -> pd.Series:
    output = pd.to_numeric(series, errors="coerce").copy()
    idx = pd.DatetimeIndex(pd.to_datetime(output.index))
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    output.index = idx.normalize()
    return output[~output.index.duplicated(keep="last")].sort_index()


def _run_frozen(run_id: int) -> tuple[Any, dict[str, Any]]:
    row = _portfolio_row(run_id)
    params = json.loads(row.get("params") or "{}")
    params.setdefault("top_n", 5)
    result = run_cross_sectional(
        row["strategy_name"],
        RunRequest(
            symbols=json.loads(row["symbols"]),
            start=date.fromisoformat(row["start_date"]),
            end=date.fromisoformat(row["end_date"]),
            params=params,
        ),
        persist=False,
    )
    return result, row


def _strategy_frame(run_id: int, prereg: dict[str, Any]) -> tuple[pd.DataFrame, Any, dict[str, Any]]:
    result, source = _run_frozen(run_id)
    returns = _normalize_series(result.equity_curve).pct_change().dropna()
    turnover = _turnover(result)
    turnover = _normalize_series(turnover).reindex(returns.index).fillna(0.0)
    provenance: dict[str, Any] = {
        "sourceRunId": run_id,
        "requestedStart": source["start_date"],
        "requestedEnd": source["end_date"],
        "parameters": json.loads(source.get("params") or "{}"),
        "symbols": json.loads(source["symbols"]),
        "observationsBeforeConditions": len(returns),
        "totalCosts": float(result.total_costs),
        "totalTradedNotional": float(result.total_traded_notional),
        "pitMembershipApplied": bool(result.pit_membership_applied),
        "pitDiagnostics": result.pit_diagnostics,
        "loggedMetrics": {
            "returnPct": source.get("return_pct"), "sharpeVsRiskFree": source.get("sharpe"),
            "maxDrawdownPct": source.get("max_drawdown_pct"), "metricsVersion": source.get("metrics_version"),
        },
        "reconstructedMetrics": {
            "returnPct": float(result.return_pct), "sharpeVsRiskFree": result.sharpe,
            "maxDrawdownPct": float(result.max_drawdown_pct),
        },
    }
    if run_id == 33:
        frozen = prereg["strategies"][0]["executionTreatment"]["observedCalibrationFrozenAtPreregistration"]
        fill_ratio = float(frozen["meanFillRatio"])
        observed_bps = float(frozen["medianAdverseSlippageBps"])
        baseline_bps = float(prereg["strategies"][0]["executionTreatment"]["historicalBacktestSlippageBps"])
        incremental = max(0.0, observed_bps - baseline_bps) / 10_000.0
        returns = returns * fill_ratio - turnover * incremental
        provenance["executionCalibration"] = {
            **frozen,
            "applied": True,
            "incrementalSlippageBps": incremental * 10_000.0,
            "treatment": "daily return multiplied by mean fill ratio; incremental slippage charged on turnover",
        }
    else:
        provenance["executionCalibration"] = {
            "applied": False,
            "treatment": "registered canonical Dow costs retained; optimized small-cap calibration not transferred",
        }
    return pd.DataFrame({"return": returns, "turnover": turnover}), result, provenance


def _dispersion_series(result: Any) -> pd.Series:
    bars = result.validation_bars or {}
    closes: dict[str, pd.Series] = {}
    for symbol, frame in bars.items():
        if frame is not None and "Close" in frame and not frame.empty:
            closes[str(symbol)] = _normalize_series(frame["Close"])
    if not closes:
        return pd.Series(dtype=float)
    returns = pd.DataFrame(closes).pct_change(fill_method=None)
    values: dict[pd.Timestamp, float] = {}
    for day, row in returns.iterrows():
        members = (
            result.membership_at_runtime(day.date())
            if result.membership_at_runtime is not None else set(closes)
        )
        usable = row.reindex([member for member in members if member in row.index]).dropna()
        values[day] = float(usable.std(ddof=1)) if len(usable) >= 5 else np.nan
    return pd.Series(values, dtype=float).sort_index()


def _condition_frame(result: Any, strategy_index: pd.DatetimeIndex) -> tuple[pd.DataFrame, dict[str, Any]]:
    warmup_start = strategy_index.min().date() - timedelta(days=900)
    end = strategy_index.max().date()
    spy = data_module.get_bars("SPY", "1d", warmup_start, end)
    if spy.empty:
        raise RuntimeError("SPY daily bars are unavailable for regime classification.")
    close = _normalize_series(spy["Close"])
    prior_close = close.shift(1)
    trend = prior_close > prior_close.rolling(200, min_periods=200).mean()
    spy_return = close.pct_change(fill_method=None)
    realized_vol = spy_return.rolling(21, min_periods=21).std(ddof=1) * np.sqrt(252.0)
    prior_vol = realized_vol.shift(1)
    vol_reference = realized_vol.shift(2).rolling(252, min_periods=252).median()
    high_vol = prior_vol > vol_reference
    dispersion = _dispersion_series(result)
    prior_dispersion = dispersion.shift(1)
    dispersion_reference = dispersion.shift(2).rolling(63, min_periods=63).median()
    high_dispersion = prior_dispersion > dispersion_reference
    conditions = pd.DataFrame({
        "spy_trend": trend,
        "market_volatility": high_vol,
        "cross_sectional_dispersion": high_dispersion,
    }).reindex(strategy_index)
    # Comparisons involving missing warmup are never silently treated as the
    # low state; pandas' nullable boolean preserves unknown observations.
    for column in conditions:
        source = {"spy_trend": prior_close, "market_volatility": vol_reference,
                  "cross_sectional_dispersion": dispersion_reference}[column]
        conditions[column] = conditions[column].where(source.reindex(strategy_index).notna()).astype("boolean")
    diagnostics = {
        "spyBars": len(close),
        "dispersionBars": int(dispersion.notna().sum()),
        "dispersionUniversePolicy": "point-in-time membership" if result.membership_at_runtime else "fixed frozen symbols",
        "missingByCondition": {column: int(conditions[column].isna().sum()) for column in conditions},
    }
    return conditions, diagnostics


def _metrics(values: pd.Series, turnover: pd.Series) -> dict[str, Any]:
    values = pd.to_numeric(values, errors="coerce").dropna()
    if values.empty:
        return {"n": 0}
    std = float(values.std(ddof=1)) if len(values) > 1 else 0.0
    equity = (1.0 + values).cumprod()
    drawdown = equity / equity.cummax() - 1.0
    return {
        "n": len(values),
        "calendarYears": sorted(int(year) for year in values.index.year.unique()),
        "meanDailyReturn": float(values.mean()),
        "medianDailyReturn": float(values.median()),
        "annualizedMeanReturn": float(values.mean() * 252.0),
        "annualizedVolatility": float(std * np.sqrt(252.0)),
        "sharpe": float(values.mean() / std * np.sqrt(252.0)) if std > 0 else None,
        "positiveDayRate": float((values > 0).mean()),
        "worstDay": float(values.min()),
        "cumulativeAttributedReturn": float(equity.iloc[-1] - 1.0),
        "maximumAttributedDrawdown": float(drawdown.min()),
        "meanTurnover": float(turnover.reindex(values.index).fillna(0.0).mean()),
    }


def _block_bootstrap_difference(values: np.ndarray, states: np.ndarray, rng: np.random.Generator) -> dict[str, Any]:
    n = len(values)
    blocks = int(np.ceil(n / BLOCK))
    starts = rng.integers(0, n, size=(BOOTSTRAPS, blocks))
    indices = ((starts[:, :, None] + np.arange(BLOCK)) % n).reshape(BOOTSTRAPS, -1)[:, :n]
    sampled_values = values[indices]
    sampled_states = states[indices]
    n_one = sampled_states.sum(axis=1)
    n_zero = n - n_one
    sums_one = (sampled_values * sampled_states).sum(axis=1)
    sums_zero = (sampled_values * (~sampled_states)).sum(axis=1)
    valid = (n_one > 0) & (n_zero > 0)
    differences = sums_one[valid] / n_one[valid] - sums_zero[valid] / n_zero[valid]
    return {
        "replications": int(len(differences)),
        "ciLow": float(np.percentile(differences, 2.5)),
        "ciHigh": float(np.percentile(differences, 97.5)),
        "twoSidedP": float(min(1.0, 2.0 * min(np.mean(differences <= 0), np.mean(differences >= 0)))),
    }


def _leave_one_year_out(frame: pd.DataFrame, condition: str, full_difference: float) -> dict[str, Any]:
    rows = []
    for year in sorted(frame.index.year.unique()):
        sample = frame.loc[frame.index.year != year].dropna(subset=["return", condition])
        one = sample.loc[sample[condition].astype(bool), "return"]
        zero = sample.loc[~sample[condition].astype(bool), "return"]
        if len(one) == 0 or len(zero) == 0:
            difference = None
        else:
            difference = float(one.mean() - zero.mean())
        rows.append({"leftOutYear": int(year), "difference": difference,
                     "nStateOne": len(one), "nStateZero": len(zero)})
    usable = [row["difference"] for row in rows if row["difference"] is not None]
    direction = np.sign(full_difference)
    agreement = float(np.mean([np.sign(value) == direction for value in usable])) if usable and direction else None
    return {"folds": rows, "usableFolds": len(usable), "directionalAgreement": agreement}


def _bh(rows: list[dict[str, Any]]) -> None:
    ordered = sorted(enumerate(rows), key=lambda item: item[1]["bootstrap"]["twoSidedP"])
    m = len(rows)
    adjusted = [0.0] * m
    running = 1.0
    for rank_index in range(m - 1, -1, -1):
        original_index, row = ordered[rank_index]
        rank = rank_index + 1
        running = min(running, row["bootstrap"]["twoSidedP"] * m / rank)
        adjusted[original_index] = running
    for row, q_value in zip(rows, adjusted):
        row["qValue"] = float(q_value)


def _classification(row: dict[str, Any], optimized: bool) -> tuple[str, list[str]]:
    reasons = []
    if row["stateOne"]["n"] < 63 or row["stateZero"]["n"] < 63:
        reasons.append("fewer than 63 observations in at least one state")
    if len(row["calendarYears"]) < 3:
        reasons.append("fewer than three contributing calendar years")
    if not (row["bootstrap"]["ciLow"] > 0 or row["bootstrap"]["ciHigh"] < 0):
        reasons.append("95% block-bootstrap interval includes zero")
    if row["qValue"] > 0.10:
        reasons.append("six-test BH q-value exceeds 0.10")
    agreement = row["leaveOneYearOut"]["directionalAgreement"]
    if agreement is None or agreement < 0.80:
        reasons.append("leave-one-year-out directional agreement is below 80%")
    if optimized:
        reasons.append("optimized configuration is selection-contaminated and capped at exploratory historical evidence")
        return "exploratory_historical", reasons
    return ("stable_candidate_for_independent_validation" if not reasons else "unsupported_or_unstable"), reasons


def run(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    strategy_specs = (("dm_optimized_63d_daily", 33), ("canonical_dm_189d_monthly", 26))
    all_tests: list[dict[str, Any]] = []
    strategies: dict[str, Any] = {}
    for strategy_number, (key, run_id) in enumerate(strategy_specs):
        frame, result, provenance = _strategy_frame(run_id, prereg)
        conditions, diagnostics = _condition_frame(result, frame.index)
        frame = frame.join(conditions)
        strategy_tests = []
        for condition_number, condition in enumerate(CONDITIONS):
            usable = frame.dropna(subset=["return", condition]).copy()
            state = usable[condition].astype(bool)
            one = usable.loc[state]
            zero = usable.loc[~state]
            if one.empty or zero.empty:
                raise RuntimeError(f"{key}/{condition} has an empty state.")
            difference = float(one["return"].mean() - zero["return"].mean())
            row = {
                "strategy": key,
                "condition": condition,
                "stateOneLabel": prereg["conditions"][condition_number]["stateOne"],
                "stateZeroLabel": prereg["conditions"][condition_number]["stateZero"],
                "usableObservations": len(usable),
                "missingObservations": int(frame[condition].isna().sum()),
                "calendarYears": sorted(int(year) for year in usable.index.year.unique()),
                "stateOne": _metrics(one["return"], one["turnover"]),
                "stateZero": _metrics(zero["return"], zero["turnover"]),
                "differenceMeanDaily": difference,
                "differenceAnnualizedMean": difference * 252.0,
                "bootstrap": _block_bootstrap_difference(
                    usable["return"].to_numpy(float), state.to_numpy(bool),
                    np.random.default_rng(SEED + strategy_number * 100 + condition_number),
                ),
                "leaveOneYearOut": _leave_one_year_out(usable, condition, difference),
            }
            strategy_tests.append(row)
            all_tests.append(row)
        strategies[key] = {
            "provenance": provenance,
            "conditionDiagnostics": diagnostics,
            "unconditional": _metrics(frame["return"], frame["turnover"]),
            "tests": strategy_tests,
        }
    _bh(all_tests)
    for row in all_tests:
        classification, reasons = _classification(row, row["strategy"] == "dm_optimized_63d_daily")
        row["classification"] = classification
        row["classificationReasons"] = reasons
    payload = {
        "study": prereg["study"],
        "preregistration": str(PREREGISTRATION.relative_to(ROOT)).replace("\\", "/"),
        "preregistrationSha256": _sha256(PREREGISTRATION),
        "simulation": {"bootstrapReplications": BOOTSTRAPS, "blockLength": BLOCK, "seed": SEED},
        "historicalEvidenceClassification": {
            "dm_optimized_63d_daily": "selected development history; exploratory only",
            "canonical_dm_189d_monthly": "canonical historical audit; candidates still require independent validation",
        },
        "strategies": strategies,
        "primaryTests": all_tests,
        "guards": prereg["prohibitions"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    pd.DataFrame([{
        "strategy": row["strategy"], "condition": row["condition"],
        "n_state_one": row["stateOne"]["n"], "n_state_zero": row["stateZero"]["n"],
        "mean_state_one": row["stateOne"]["meanDailyReturn"],
        "mean_state_zero": row["stateZero"]["meanDailyReturn"],
        "difference_daily": row["differenceMeanDaily"],
        "difference_annualized": row["differenceAnnualizedMean"],
        "ci_low": row["bootstrap"]["ciLow"], "ci_high": row["bootstrap"]["ciHigh"],
        "p_value": row["bootstrap"]["twoSidedP"], "q_value": row["qValue"],
        "loyo_agreement": row["leaveOneYearOut"]["directionalAgreement"],
        "classification": row["classification"],
    } for row in all_tests]).to_csv(output_dir / "primary_tests.csv", index=False)
    (output_dir / "report.md").write_text(_report(payload), encoding="utf-8")
    return payload


def _report(payload: dict[str, Any]) -> str:
    labels = {"dm_optimized_63d_daily": "Optimized DM 63D/Daily", "canonical_dm_189d_monthly": "Canonical DM 189D/Monthly"}
    lines = ["# Dual Momentum Regime-Dependence Audit v1", "",
             "This audit preserves both strategies. It measures state dependence; it does not create a trading filter.", "",
             "| Strategy | Condition | State-one N | State-zero N | Annualized mean difference | 95% block CI (daily) | p | BH q | LOYO agreement | Classification |",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for row in payload["primaryTests"]:
        agreement = row["leaveOneYearOut"]["directionalAgreement"]
        lines.append(
            f"| {labels[row['strategy']]} | {row['condition'].replace('_', ' ')} | {row['stateOne']['n']} | {row['stateZero']['n']} | "
            f"{row['differenceAnnualizedMean']:+.2%} | [{row['bootstrap']['ciLow']:+.3%}, {row['bootstrap']['ciHigh']:+.3%}] | "
            f"{row['bootstrap']['twoSidedP']:.3f} | {row['qValue']:.3f} | {'n/a' if agreement is None else f'{agreement:.0%}'} | {row['classification'].replace('_', ' ')} |"
        )
    lines += ["", "## Interpretation guards", "",
              "- Positive differences mean state one had the higher daily mean; negative differences mean state zero did.",
              "- Six primary tests share one Benjamini-Hochberg correction.",
              "- Optimized DM is selection-contaminated and cannot be promoted by this historical result.",
              "- Daily subgroup metrics are attribution diagnostics, not a backtest of switching the strategy on and off.",
              "- Any surviving canonical condition is only a candidate for a new preregistered independent validation."]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    result = run()
    print(json.dumps({"study": result["study"], "output": str(OUTPUT_DIR)}, indent=2))


if __name__ == "__main__":
    main()
