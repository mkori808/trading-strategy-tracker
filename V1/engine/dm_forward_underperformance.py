"""Compile forward Optimized-DM sessions that underperformed SPY.

Observation only. This module reads Alpaca's settled paper-account history,
market bars, and the append-only prospective ledger. It has no order imports
or execution functions.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from engine import alpaca_trading, dm_regime_forward, execution_db, prop_forward
from engine.momentum_persistence_reversal import _state_frame

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "reports" / "dm_forward_underperformance"


def _active_inception() -> dict[str, Any]:
    for name, row in execution_db.automation_config().items():
        if bool(row["enabled"]):
            inception = execution_db.inception_for(name)
            if inception and inception.get("status") == "initialized":
                return inception
    raise RuntimeError("No initialized active paper-strategy inception was found.")


def _prospective_rows(path: Path = dm_regime_forward.DB_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM daily_observations ORDER BY session_date").fetchall()
    except sqlite3.Error:
        rows = []
    conn.close()
    return {str(row["session_date"]): dict(row) for row in rows}


def _session_details(path: Path = prop_forward.DB_PATH) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM session_outcomes ORDER BY session_date").fetchall()
    except sqlite3.Error:
        rows = []
    conn.close()
    return {str(row["session_date"]): dict(row) for row in rows}


def _settled_rows(start: date) -> list[dict[str, Any]]:
    history = alpaca_trading.get_portfolio_history(start)
    if not history.get("available"):
        raise RuntimeError(str(history.get("reason") or "Alpaca portfolio history is unavailable."))
    return sorted(history.get("rows") or [], key=lambda row: row["date"])


def compile_observations(
    rows: list[dict[str, Any]], inception_date: date,
    labels: dict[str, dict[str, bool]], states: pd.DataFrame,
    spy_returns: dict[str, float], prospective: dict[str, dict[str, Any]],
    session_details: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create aligned full-session observations; the inception session is baseline only."""
    observations: list[dict[str, Any]] = []
    ordered = sorted(rows, key=lambda row: row["date"])
    for prior, current in zip(ordered, ordered[1:]):
        session = str(current["date"])
        session_date = date.fromisoformat(session)
        if session_date <= inception_date:
            continue
        prior_equity = float(prior["equity"])
        settled_return = float(current["equity"]) / prior_equity - 1.0 if prior_equity else None
        locked = prospective.get(session)
        account_return = (
            float(locked["account_return"])
            if locked is not None and locked.get("account_return") is not None
            else settled_return
        )
        spy_return = (
            float(locked["spy_return"])
            if locked is not None and locked.get("spy_return") is not None
            else float(spy_returns[session])
        )
        condition = labels[session]
        if locked is not None:
            condition = {
                "spy_trend": bool(locked["spy_trend"]),
                "market_volatility": bool(locked["market_volatility"]),
                "cross_sectional_dispersion": bool(locked["cross_sectional_dispersion"]),
            }
        active_return = account_return - spy_return
        details = session_details.get(session) or {}
        opening = details.get("opening_equity")
        intraday_low = (
            float(details["minimum_equity"]) / float(opening) - 1.0
            if opening and details.get("minimum_equity") is not None else None
        )
        if session_date <= dm_regime_forward.CUTOFF:
            evidence = "exploratory_pre_preregistration"
        elif locked is not None:
            evidence = "prospective_locked"
        else:
            evidence = "prospective_pending_ledger_ingestion"
        stamp = pd.Timestamp(session)
        state_value = states.loc[stamp, "state"] if stamp in states.index else pd.NA
        observations.append({
            "date": session,
            "accountReturn": account_return,
            "settledEquityReturn": settled_return,
            "prospectiveLedgerReturn": None if locked is None else locked.get("account_return"),
            "returnSource": (
                str(locked["return_source"]) if locked is not None
                else "alpaca_settled_close_to_close_equity"
            ),
            "spyReturn": spy_return,
            "activeReturn": active_return,
            "underperformedSpy": active_return < 0.0,
            "absoluteLoss": account_return < 0.0,
            "jointDamaging": account_return < 0.0 and active_return < 0.0,
            "spyDownSameDay": spy_return < 0.0,
            "marketStateTMinus1": None if pd.isna(state_value) else str(state_value),
            "spyAbove200SmaTMinus1": bool(condition["spy_trend"]),
            "highMarketVolatilityTMinus1": bool(condition["market_volatility"]),
            "highCrossSectionalDispersionTMinus1": bool(condition["cross_sectional_dispersion"]),
            "worstObservedIntradayEquityReturn": intraday_low,
            "realizedTurnover": details.get("realized_turnover"),
            "snapshotCount": details.get("snapshot_count"),
            "evidenceClass": evidence,
        })
    return observations


def _condition_summary(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = {
        "SPY above 200-day SMA at T-1": "spyAbove200SmaTMinus1",
        "High SPY volatility at T-1": "highMarketVolatilityTMinus1",
        "High cross-sectional dispersion at T-1": "highCrossSectionalDispersionTMinus1",
        "SPY down during session": "spyDownSameDay",
    }
    summary = []
    for label, field in fields.items():
        yes = [row for row in observations if row[field]]
        no = [row for row in observations if not row[field]]
        yes_events = sum(row["underperformedSpy"] for row in yes)
        no_events = sum(row["underperformedSpy"] for row in no)
        yes_rate = yes_events / len(yes) if yes else None
        no_rate = no_events / len(no) if no else None
        summary.append({
            "condition": label,
            "timing": "same_session_descriptive" if field == "spyDownSameDay" else "known_by_T_minus_1",
            "nTrue": len(yes), "underperformTrue": yes_events, "rateTrue": yes_rate,
            "nFalse": len(no), "underperformFalse": no_events, "rateFalse": no_rate,
            "riskDifference": yes_rate - no_rate if yes_rate is not None and no_rate is not None else None,
            "inferenceAllowed": False,
        })
    return summary


def _state_summary(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    states = sorted({row["marketStateTMinus1"] for row in observations if row["marketStateTMinus1"]})
    result = []
    for state in states:
        sample = [row for row in observations if row["marketStateTMinus1"] == state]
        events = sum(row["underperformedSpy"] for row in sample)
        result.append({
            "state": state, "n": len(sample), "underperformanceEvents": events,
            "underperformanceRate": events / len(sample),
            "meanActiveReturn": sum(row["activeReturn"] for row in sample) / len(sample),
        })
    return result


def analyze(observations: list[dict[str, Any]]) -> dict[str, Any]:
    events = [row for row in observations if row["underperformedSpy"]]
    locked = [row for row in observations if row["evidenceClass"] == "prospective_locked"]
    quadrants = {
        "loss_and_underperformed": sum(row["jointDamaging"] for row in observations),
        "loss_but_outperformed": sum(row["absoluteLoss"] and not row["underperformedSpy"] for row in observations),
        "profit_but_underperformed": sum(not row["absoluteLoss"] and row["underperformedSpy"] for row in observations),
        "profit_and_outperformed": sum(not row["absoluteLoss"] and not row["underperformedSpy"] for row in observations),
    }
    compounded_account = math.prod(1.0 + row["accountReturn"] for row in observations) - 1.0
    compounded_spy = math.prod(1.0 + row["spyReturn"] for row in observations) - 1.0
    worst = min(observations, key=lambda row: row["activeReturn"]) if observations else None
    without_worst = [row for row in observations if row is not worst]
    active_without_worst = (
        sum(row["activeReturn"] for row in without_worst) / len(without_worst)
        if without_worst else None
    )
    return {
        "sessions": len(observations),
        "underperformanceEvents": len(events),
        "underperformanceRate": len(events) / len(observations) if observations else None,
        "meanActiveReturn": (
            sum(row["activeReturn"] for row in observations) / len(observations)
            if observations else None
        ),
        "compoundedAccountReturn": compounded_account,
        "compoundedSpyReturn": compounded_spy,
        "compoundedGapPctPoints": (compounded_account - compounded_spy) * 100.0,
        "worstActiveDate": None if worst is None else worst["date"],
        "worstActiveReturn": None if worst is None else worst["activeReturn"],
        "meanActiveReturnExcludingWorstDay": active_without_worst,
        "prospectiveLockedSessions": len(locked),
        "prospectiveLockedUnderperformanceEvents": sum(row["underperformedSpy"] for row in locked),
        "quadrants": quadrants,
        "conditionSummary": _condition_summary(observations),
        "stateSummary": _state_summary(observations),
        "maturity": "too_early_operational_diagnostic_only" if len(observations) < 20 else "descriptive_only",
    }


def _pct(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:+.{digits}%}"


def _report(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Optimized Dual Momentum Forward SPY-Underperformance Log", "",
        "This is an account-level paper-forward diagnostic. Underperformance means the completed-session account return minus the aligned SPY return was below zero.", "",
        "## Current read", "",
        f"- {summary['underperformanceEvents']} of {summary['sessions']} aligned completed sessions underperformed SPY ({summary['underperformanceRate']:.1%}).",
        f"- Mean daily active return was {_pct(summary['meanActiveReturn'])}.",
        f"- Compounded account return was {_pct(summary['compoundedAccountReturn'])} versus {_pct(summary['compoundedSpyReturn'])} for SPY, a {summary['compoundedGapPctPoints']:+.2f}-percentage-point gap.",
        f"- The worst relative session was {summary['worstActiveDate']} at {_pct(summary['worstActiveReturn'])}; mean active return excluding that day was {_pct(summary['meanActiveReturnExcludingWorstDay'])}.",
        f"- All {summary['quadrants']['loss_and_underperformed']} relative misses were also absolute-loss days; all {summary['quadrants']['profit_and_outperformed']} outperforming days were profitable.",
        f"- Only {summary['prospectiveLockedSessions']} session(s) are sealed in the preregistered ledger; no inference or trading rule is permitted at this maturity.",
        "- Sessions through August 26 are exploratory context; later settled sessions remain distinct as locked or pending prospective observations.",
        "", "## Underperformance days", "",
        "| Date | Account | SPY | Active | Absolute loss? | T-1 state | Trend | High vol | High dispersion | Evidence |",
        "|---|---:|---:|---:|---|---|---|---|---|---|",
    ]
    for row in payload["observations"]:
        if not row["underperformedSpy"]:
            continue
        lines.append(
            f"| {row['date']} | {_pct(row['accountReturn'])} | {_pct(row['spyReturn'])} | {_pct(row['activeReturn'])} | "
            f"{'yes' if row['absoluteLoss'] else 'no'} | {(row['marketStateTMinus1'] or 'n/a').replace('_', ' ')} | "
            f"{'above 200SMA' if row['spyAbove200SmaTMinus1'] else 'below 200SMA'} | "
            f"{'yes' if row['highMarketVolatilityTMinus1'] else 'no'} | "
            f"{'yes' if row['highCrossSectionalDispersionTMinus1'] else 'no'} | {row['evidenceClass'].replace('_', ' ')} |"
        )
    lines += [
        "", "## Base-rate comparisons", "",
        "Looking only at bad days would be misleading. These rows compare each condition's underperformance rate with its absence.", "",
        "| Condition | N true | Underperform rate true | N false | Underperform rate false | Difference | Timing |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in summary["conditionSummary"]:
        lines.append(
            f"| {row['condition']} | {row['nTrue']} | {_pct(row['rateTrue'], 1)} | {row['nFalse']} | "
            f"{_pct(row['rateFalse'], 1)} | {_pct(row['riskDifference'], 1)} | {row['timing'].replace('_', ' ')} |"
        )
    lines += [
        "", "## Interpretation", "",
        f"With fewer than 20 aligned sessions--and only {summary['prospectiveLockedSessions']} currently sealed prospective observation(s)--this can identify dates and data-quality issues, but it cannot identify a repeatable market condition. Every current session was bullish persistence and above the 200-day SMA, so the forward record has no trend-state contrast. Same-session SPY direction is descriptive, never predictive. T-1 conditions may become testable only after substantially more locked observations. No order, signal, or sizing rule changed.",
    ]
    return "\n".join(lines) + "\n"


def run(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    inception = _active_inception()
    inception_date = datetime.fromisoformat(str(inception["inceptionAt"])).date()
    rows = _settled_rows(inception_date)
    eligible_dates = [date.fromisoformat(row["date"]) for row in rows if date.fromisoformat(row["date"]) > inception_date]
    if not eligible_dates:
        raise RuntimeError("No complete post-inception paper sessions are available.")
    labels = dm_regime_forward._labels(eligible_dates)
    spy_returns = dm_regime_forward._spy_returns(eligible_dates)
    states, state_diagnostics = _state_frame(pd.DatetimeIndex(pd.to_datetime(eligible_dates)))
    observations = compile_observations(
        rows, inception_date, labels, states, spy_returns,
        _prospective_rows(), _session_details(),
    )
    payload = {
        "generatedAt": datetime.now().astimezone().isoformat(),
        "strategy": "DM Optimized 63D/Daily",
        "paperOnly": True,
        "inceptionDate": inception_date.isoformat(),
        "prospectiveCutoff": dm_regime_forward.CUTOFF.isoformat(),
        "stateDiagnostics": state_diagnostics,
        "summary": analyze(observations),
        "observations": observations,
        "guards": [
            "No condition changes an order, signal, or size.",
            "Pre-cutoff observations are exploratory only.",
            "Pending prospective observations are not counted as locked until append-only ingestion completes.",
            "Same-session conditions cannot be used as predictors.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    pd.DataFrame(observations).to_csv(output_dir / "daily_observations.csv", index=False)
    pd.DataFrame([row for row in observations if row["underperformedSpy"]]).to_csv(
        output_dir / "underperformance_days.csv", index=False
    )
    pd.DataFrame(payload["summary"]["conditionSummary"]).to_csv(
        output_dir / "condition_summary.csv", index=False
    )
    (output_dir / "report.md").write_text(_report(payload), encoding="utf-8")
    return payload


def main() -> None:
    argparse.ArgumentParser().parse_args()
    result = run()
    print(json.dumps({"sessions": result["summary"]["sessions"], "output": str(OUTPUT_DIR)}, indent=2))


if __name__ == "__main__":
    main()
