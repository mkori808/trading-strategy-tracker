"""Preregistered prop scaling/survival study for five frozen momentum candidates.

This module consumes frozen strategy outputs.  It cannot tune a signal and it
does not choose a leverage winner.  The full scale frontier is always emitted.
See research/prop_scaling_survival_v1_preregistration.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from engine import execution_db, logging_db
from engine.dm_mrm_vol_scaled import compute_weight_path
from engine.runner import RunRequest, run_cross_sectional

PREREGISTRATION = Path("research/prop_scaling_survival_v1_preregistration.json")
OUTPUT_DIR = Path("reports/prop_scaling_survival_v1")
SCALES = tuple(round(x, 2) for x in np.arange(0.10, 1.001, 0.05))
N_PATHS = 5_000
HORIZON = 252
BLOCK = 21
SEED = 20_260_822


@dataclass(frozen=True)
class Rules:
    account_size: float = 100_000.0
    daily_loss_pct: float = 0.03
    max_drawdown_pct: float = 0.06
    target_pct: float = 0.08
    payout_split: float = 0.80
    challenge_fee: float = 500.0
    reset_fee: float = 500.0
    payout_cadence: int = 21


@dataclass
class CandidatePath:
    key: str
    label: str
    frame: pd.DataFrame
    provenance: dict[str, Any]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row_33() -> dict[str, Any]:
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    row = conn.execute(
        "SELECT id, strategy_name, start_date, end_date, params, symbols, "
        "slippage_bps, commission_bps FROM portfolio_runs WHERE id=33"
    ).fetchone()
    conn.close()
    if row is None:
        raise RuntimeError("Frozen DM Optimized source portfolio_runs.id=33 is missing.")
    return dict(row)


def _turnover(result: Any) -> pd.Series:
    values: dict[pd.Timestamp, float] = {}
    previous: dict[str, float] = {}
    for row in result.rebalances.sort_values("date").itertuples():
        current = dict(row.holdings)
        symbols = set(previous) | set(current)
        values[pd.Timestamp(row.date)] = sum(
            abs(current.get(symbol, 0.0) - previous.get(symbol, 0.0))
            for symbol in symbols
        )
        previous = current
    return pd.Series(values, dtype=float)


def _ohlc_envelope(result: Any) -> pd.DataFrame:
    """Daily close return plus a disclosed OHLC portfolio envelope.

    The held names' lows/highs are combined as if simultaneous.  That is a
    conservative low envelope, not a claim about the true intraday account
    path; daily bars cannot reveal cross-name timing or high/low ordering.
    """
    equity = pd.to_numeric(result.equity_curve, errors="coerce").dropna().sort_index()
    close_return = equity.pct_change().dropna()
    bars = result.validation_bars or {}
    decisions = {
        pd.Timestamp(row.date): dict(row.holdings)
        for row in result.rebalances.sort_values("date").itertuples()
    }
    holdings: dict[str, float] = {}
    low_rows: list[float] = []
    high_rows: list[float] = []
    for day in close_return.index:
        if day in decisions:
            holdings = decisions[day]
        lows, highs = 0.0, 0.0
        used = 0.0
        prior = equity.index[equity.index < day]
        for symbol, weight in holdings.items():
            frame = bars.get(symbol)
            if frame is None or day not in frame.index or len(prior) == 0:
                continue
            prior_rows = frame.index[frame.index < day]
            if len(prior_rows) == 0:
                continue
            ref = float(frame.loc[prior_rows[-1], "Close"])
            if ref <= 0:
                continue
            lows += weight * (float(frame.loc[day, "Low"]) / ref - 1.0)
            highs += weight * (float(frame.loc[day, "High"]) / ref - 1.0)
            used += weight
        close = float(close_return.loc[day])
        if used == 0:
            lows = highs = close
        low_rows.append(min(lows, close))
        high_rows.append(max(highs, close))
    return pd.DataFrame(
        {"close_return": close_return.to_numpy(), "low_return": low_rows, "high_return": high_rows},
        index=close_return.index,
    )


def _optimized_candidate(*, apply_execution_calibration: bool = True) -> CandidatePath:
    source = _row_33()
    params = json.loads(source["params"] or "{}")
    params.setdefault("top_n", 5)
    symbols = json.loads(source["symbols"])
    result = run_cross_sectional(
        "Dual Momentum",
        RunRequest(
            symbols=symbols,
            start=date.fromisoformat(source["start_date"]),
            end=date.fromisoformat(source["end_date"]),
            params=params,
        ),
        persist=False,
    )
    frame = _ohlc_envelope(result)
    calibration = execution_db.fill_calibration()
    adjustment = {
        "applied": False,
        "reason": "aggregate broker calibration did not meet its minimum sample",
        **calibration,
    }
    if calibration.get("calibrated") and apply_execution_calibration:
        fill_ratio = float(calibration.get("meanFillRatio") or 1.0)
        observed_bps = max(0.0, float(calibration.get("medianAdverseSlippageBps") or 0.0))
        baseline_bps = float(source.get("slippage_bps") or 0.0)
        extra = max(0.0, observed_bps - baseline_bps) / 10_000.0
        turnover = _turnover(result).reindex(frame.index).fillna(0.0)
        for column in frame:
            frame[column] = frame[column] * fill_ratio - extra * turnover
        frame["low_return"] = np.minimum(frame.low_return, frame.close_return)
        frame["high_return"] = np.maximum(frame.high_return, frame.close_return)
        adjustment.update({
            "applied": True,
            "reason": "observed aggregate execution overlay applied to the candidate that generated it",
            "historicalBacktestSlippageBps": baseline_bps,
            "incrementalObservedSlippageBps": extra * 10_000.0,
            "partialFillApproximation": "market exposure multiplied by observed aggregate mean fill ratio",
        })
    elif calibration.get("calibrated"):
        adjustment.update({
            "applied": False,
            "reason": "registered-cost-only sensitivity; observed execution overlay intentionally omitted",
            "historicalBacktestSlippageBps": float(source.get("slippage_bps") or 0.0),
        })
    return CandidatePath(
        "dm_optimized_63d_daily", "DM Optimized 63D/Daily", frame,
        {
            "sourceRunId": 33,
            "parameters": params,
            "symbols": symbols,
            "historicalSelectionContaminated": True,
            "executionCalibration": adjustment,
            "totalCosts": float(result.total_costs),
            "totalTradedNotional": float(result.total_traded_notional),
        },
    )


def _registered_candidate(name: str, key: str, label: str) -> tuple[CandidatePath, Any]:
    result = run_cross_sectional(name, persist=False)
    frame = _ohlc_envelope(result)
    candidate = CandidatePath(
        key, label, frame,
        {
            "registeredDefaults": True,
            "start": result.start.isoformat(),
            "end": result.end.isoformat(),
            "observations": len(frame),
            "totalCosts": float(result.total_costs),
            "totalTradedNotional": float(result.total_traded_notional),
            "executionCalibration": {
                "candidateSpecificObservedFills": False,
                "treatment": "registered per-symbol spread estimator; full fills",
            },
        },
    )
    return candidate, result


def _blend(
    dm: CandidatePath, mrm: CandidatePath, *, vol_scaled: bool
) -> CandidatePath:
    idx = dm.frame.index.intersection(mrm.frame.index).sort_values()
    left, right = dm.frame.loc[idx], mrm.frame.loc[idx]
    decisions = compute_weight_path(left.close_return, right.close_return) if vol_scaled else []
    targets = {
        pd.Timestamp(item.date): (float(item.dm_weight), float(item.mrm_weight))
        for item in decisions if item.dm_weight is not None and item.mrm_weight is not None
    }
    monthly = set(pd.Series(idx, index=idx).groupby([idx.year, idx.month]).first())
    dm_value = mrm_value = 0.5
    rows: list[dict[str, float]] = []
    for day in idx:
        total = dm_value + mrm_value
        w_dm = dm_value / total
        w_mrm = 1.0 - w_dm
        low = w_dm * float(left.loc[day, "low_return"]) + w_mrm * float(right.loc[day, "low_return"])
        high = w_dm * float(left.loc[day, "high_return"]) + w_mrm * float(right.loc[day, "high_return"])
        dm_value *= 1.0 + float(left.loc[day, "close_return"])
        mrm_value *= 1.0 + float(right.loc[day, "close_return"])
        close = (dm_value + mrm_value) / total - 1.0
        rows.append({"close_return": close, "low_return": min(low, close), "high_return": max(high, close)})
        if day in monthly:
            target = targets.get(day, (0.5, 0.5)) if vol_scaled else (0.5, 0.5)
            total = dm_value + mrm_value
            dm_value, mrm_value = total * target[0], total * target[1]
    key = "vol_scaled" if vol_scaled else "fixed_50_50"
    label = "Frozen Vol-Scaled DM/MRM" if vol_scaled else "Fixed 50/50 DM/MRM"
    return CandidatePath(
        key, label, pd.DataFrame(rows, index=idx),
        {
            "dmSource": dm.key,
            "mrmSource": mrm.key,
            "monthlyReset": True,
            "weightRule": "frozen 60-session inverse volatility" if vol_scaled else "fixed 50/50",
            "overlayTransactionCost": "zero per frozen specifications",
            "observations": len(idx),
        },
    )


def build_candidates() -> list[CandidatePath]:
    optimized = _optimized_candidate()
    canonical_dm, _ = _registered_candidate("Dual Momentum", "canonical_dm", "Canonical DM")
    mrm, _ = _registered_candidate("Market-Residual Momentum", "mrm", "Canonical MRM")
    fixed = _blend(canonical_dm, mrm, vol_scaled=False)
    vol = _blend(canonical_dm, mrm, vol_scaled=True)
    return [optimized, mrm, vol, fixed, canonical_dm]


def _bootstrap_indices(n: int, paths: int, rng: np.random.Generator) -> np.ndarray:
    blocks = int(np.ceil(HORIZON / BLOCK))
    starts = rng.integers(0, n, size=(paths, blocks))
    offsets = np.arange(BLOCK)
    return ((starts[:, :, None] + offsets) % n).reshape(paths, -1)[:, :HORIZON]


def _simulate_scale(
    sampled: np.ndarray,
    scale: float,
    rules: Rules,
    drawdown_rule: Literal["static", "trailing_to_breakeven"],
) -> dict[str, Any]:
    n = sampled.shape[0]
    account = rules.account_size
    equity = np.full(n, account)
    peak = np.full(n, account)
    stage = np.zeros(n, dtype=np.int8)  # 0 evaluation, 1 funded
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
        start_equity = equity.copy()
        close_pnl = sampled[:, day, 0] * account * scale
        low_pnl = sampled[:, day, 1] * account * scale
        high_pnl = sampled[:, day, 2] * account * scale
        high_equity = start_equity + high_pnl
        low_equity = start_equity + low_pnl
        close_equity = start_equity + close_pnl
        intraday_peak = np.maximum(peak, high_equity)
        utilization_anchor = account if drawdown_rule == "static" else intraday_peak
        max_util = np.maximum(max_util, np.maximum(0.0, utilization_anchor - low_equity) / (account * rules.max_drawdown_pct))
        daily_breach = low_pnl <= -(account * rules.daily_loss_pct)
        if drawdown_rule == "static":
            floor = account * (1.0 - rules.max_drawdown_pct)
        else:
            floor = np.minimum(intraday_peak - account * rules.max_drawdown_pct, account)
        drawdown_breach = (~daily_breach) & (low_equity <= floor)
        breached = daily_breach | drawdown_breach
        first_breach[(first_breach == 0) & breached] = day + 1
        daily_breach_ever |= daily_breach
        drawdown_breach_ever |= drawdown_breach

        alive = ~breached
        equity[alive] = close_equity[alive]
        peak[alive] = intraday_peak[alive]
        funded_days[alive & (stage == 1)] += 1

        pass_eval = alive & (stage == 0) & (equity >= account * (1.0 + rules.target_pct))
        stage[pass_eval] = 1
        equity[pass_eval] = account
        peak[pass_eval] = account
        funded_days[pass_eval] = 0

        due = alive & (stage == 1) & (funded_days > 0) & (funded_days % rules.payout_cadence == 0)
        gross_withdrawable = np.where(due, np.maximum(equity - account, 0.0), 0.0)
        paid = gross_withdrawable * rules.payout_split
        payout += paid
        first_payout[(first_payout == 0) & (paid > 0)] = day + 1
        withdrew = due & (gross_withdrawable > 0)
        equity[withdrew] = account
        peak[withdrew] = account

        # A breach terminates that account.  The preregistered economics then
        # buys a fresh challenge for the next session, charging the reset fee.
        resets[breached] += 1
        fees[breached] += rules.reset_fee
        equity[breached] = account
        peak[breached] = account
        stage[breached] = 0
        funded_days[breached] = 0

    net = payout - fees
    has_payout = first_payout > 0
    return {
        "scale": scale,
        "paths": n,
        "breachProbability1m": float(np.mean((first_breach > 0) & (first_breach <= 21))),
        "breachProbability3m": float(np.mean((first_breach > 0) & (first_breach <= 63))),
        "breachProbability6m": float(np.mean((first_breach > 0) & (first_breach <= 126))),
        "breachProbability12m": float(np.mean(first_breach > 0)),
        "dailyLimitBreachProbability": float(daily_breach_ever.mean()),
        "drawdownBreachProbability": float(drawdown_breach_ever.mean()),
        "expectedPayout": float(payout.mean()),
        "medianPayout": float(np.median(payout)),
        "expectedNetPayout": float(net.mean()),
        "medianNetPayout": float(np.median(net)),
        "p05NetPayout": float(np.percentile(net, 5)),
        "probabilityAnyPayout": float(has_payout.mean()),
        "medianDaysToFirstPayout": float(np.median(first_payout[has_payout])) if has_payout.any() else None,
        "medianDrawdownUtilization": float(np.median(max_util)),
        "p95DrawdownUtilization": float(np.percentile(max_util, 95)),
        "expectedResets": float(resets.mean()),
        "expectedFees": float(fees.mean()),
    }


def simulate_candidate(candidate: CandidatePath, rules: Rules, paths: int = N_PATHS) -> list[dict[str, Any]]:
    values = candidate.frame[["close_return", "low_return", "high_return"]].replace([np.inf, -np.inf], np.nan).dropna().to_numpy(float)
    if len(values) < 240:
        raise RuntimeError(f"{candidate.label} has only {len(values)} usable daily observations.")
    seed_offset = sum(candidate.key.encode("utf-8"))
    indices = _bootstrap_indices(len(values), paths, np.random.default_rng(SEED + seed_offset))
    sampled = values[indices]
    rows = []
    for rule in ("static", "trailing_to_breakeven"):
        for scale in SCALES:
            row = _simulate_scale(sampled, scale, rules, rule)
            row.update({"candidate": candidate.key, "label": candidate.label, "drawdownRule": rule})
            rows.append(row)
    return rows


def _summary(rows: pd.DataFrame) -> list[dict[str, Any]]:
    """Descriptive operating points, never an optimization recommendation."""
    out = []
    for (candidate, label, rule), group in rows.groupby(["candidate", "label", "drawdownRule"], sort=False):
        ordered = group.sort_values("scale")
        safe = ordered.loc[ordered.breachProbability12m <= 0.05]
        payout = ordered.loc[ordered.expectedNetPayout.idxmax()]
        out.append({
            "candidate": candidate,
            "label": label,
            "drawdownRule": rule,
            "largestScaleAtOrBelow5PctBreach": None if safe.empty else float(safe.iloc[-1].scale),
            "historicalMaxExpectedNetPayoutScale": float(payout.scale),
            "historicalMaxExpectedNetPayout": float(payout.expectedNetPayout),
            "guard": "The payout-maximizing historical scale is descriptive and is not selected or recommended.",
        })
    return out


def _report(payload: dict[str, Any], rows: pd.DataFrame, output_dir: Path) -> None:
    primary = rows.loc[rows.drawdownRule == "static"]
    lines = [
        "# Frozen Prop Candidate Scaling and Survival Study v1", "",
        "The complete preregistered frontier is reported. No signal changed and no payout-maximizing scale is promoted as optimal.", "",
        "## Account and simulation", "",
        "Primary rules: $100,000 nominal account, 3% daily loss limit, 6% static maximum loss, 8% evaluation target, 80% payout split, $500 initial/reset fee, and monthly funded payouts. A trailing-to-breakeven sensitivity is included.",
        "Moving-block bootstrap: 5,000 paths, 21-session blocks, 252-session horizon. A breached account buys a fresh challenge on the next session; expected fees include those resets.", "",
        "## Descriptive 5% annual-breach reference", "",
        "This is a common-risk reference point, not a selected operating scale and not a promotion rule.", "",
        "| Candidate | Largest tested scale at or below 5% breach | 12m breach | Expected net payout | Median net payout | P(any payout) | Median days to first payout |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for label, group in primary.groupby("label", sort=False):
        eligible = group.loc[group.breachProbability12m <= 0.05].sort_values("scale")
        if eligible.empty:
            lines.append(f"| {label} | none | — | — | — | — | — |")
            continue
        row = eligible.iloc[-1]
        days = "—" if pd.isna(row.medianDaysToFirstPayout) else f"{row.medianDaysToFirstPayout:.0f}"
        lines.append(
            f"| {label} | {row.scale:.2f}x | {row.breachProbability12m:.1%} | "
            f"${row.expectedNetPayout:,.0f} | ${row.medianNetPayout:,.0f} | "
            f"{row.probabilityAnyPayout:.1%} | {days} |"
        )
    lines += ["",
        "## Primary static-rule frontier", "",
        "| Candidate | Scale | Breach 1m | 3m | 6m | 12m | Expected payout | Median payout | Expected net | P05 net | P(any payout) | Median days to first payout | Median DD use | P95 DD use |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in primary.itertuples():
        days = "—" if pd.isna(row.medianDaysToFirstPayout) else f"{row.medianDaysToFirstPayout:.0f}"
        lines.append(
            f"| {row.label} | {row.scale:.2f}x | {row.breachProbability1m:.1%} | {row.breachProbability3m:.1%} | "
            f"{row.breachProbability6m:.1%} | {row.breachProbability12m:.1%} | ${row.expectedPayout:,.0f} | "
            f"${row.medianPayout:,.0f} | ${row.expectedNetPayout:,.0f} | ${row.p05NetPayout:,.0f} | "
            f"{row.probabilityAnyPayout:.1%} | {days} | {row.medianDrawdownUtilization:.0%} | {row.p95DrawdownUtilization:.0%} |"
        )
    trailing = rows.loc[rows.drawdownRule == "trailing_to_breakeven"]
    lines += ["", "## Trailing-to-breakeven sensitivity", "",
              "The high-then-low daily-bar ordering is deliberately conservative. This table uses the same descriptive 5% annual-breach reference.", "",
              "| Candidate | Largest tested scale at or below 5% breach | 12m breach | Expected net payout | Median net payout | P(any payout) |",
              "|---|---:|---:|---:|---:|---:|"]
    for label, group in trailing.groupby("label", sort=False):
        eligible = group.loc[group.breachProbability12m <= 0.05].sort_values("scale")
        if eligible.empty:
            lines.append(f"| {label} | none | — | — | — | — |")
            continue
        row = eligible.iloc[-1]
        lines.append(
            f"| {label} | {row.scale:.2f}x | {row.breachProbability12m:.1%} | "
            f"${row.expectedNetPayout:,.0f} | ${row.medianNetPayout:,.0f} | {row.probabilityAnyPayout:.1%} |"
        )
    lines += ["", "## Interpretation limits", "",
        "- Historical daily OHLC does not reveal the true account-equity path, whether different securities hit lows simultaneously, or whether a session high preceded its low. Static-rule results use a conservative simultaneous-low envelope; trailing results additionally use conservative high-then-low ordering. Both are approximate, not intraday-verified.",
        "- DM Optimized 63D/Daily was selected from a historical search. Its one-year historical series is development evidence only; the live paper ledger is the authoritative forward test.",
        "- The optimized candidate alone receives the aggregate observed-fill overlay. Those small-cap fills are not transferred to the Dow-based controls. Partial fills are approximated through observed mean exposure because order-level counterfactual replay is unavailable.",
        "- Registered spreads and zero equity commission are charged by the underlying backtests. SEC/TAF pass-through fees are not represented. Challenge/reset fees and payout split are represented.",
        "- Five years of Dow history and one year of optimized-candidate history are not long-history evidence. WRDS/CRSP remains the blocker for stronger regime and survivorship claims.",
        "- The historical payout-maximizing scale is shown only as a diagnostic. Choosing it would violate the preregistered interpretation rule.",
        "", "## Artifacts", "",
        "`frontier.csv` contains every candidate, scale, and drawdown rule. `results.json` contains the same frontier, source provenance, execution calibration, and preregistration hash."
    ]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_study(output_dir: Path = OUTPUT_DIR, paths: int = N_PATHS) -> dict[str, Any]:
    if tuple(json.loads(PREREGISTRATION.read_text(encoding="utf-8"))["scalingGrid"]) != SCALES:
        raise RuntimeError("Code scaling grid differs from the preregistration.")
    output_dir.mkdir(parents=True, exist_ok=True)
    rules = Rules()
    candidates = build_candidates()
    all_rows = [row for candidate in candidates for row in simulate_candidate(candidate, rules, paths)]
    frame = pd.DataFrame(all_rows)
    frame.to_csv(output_dir / "frontier.csv", index=False)
    payload = {
        "study": "Frozen Prop Candidate Scaling and Survival Study v1",
        "preregistration": str(PREREGISTRATION),
        "preregistrationSha256": _sha256(PREREGISTRATION),
        "rules": asdict(rules),
        "simulation": {"paths": paths, "horizonDays": HORIZON, "blockSize": BLOCK, "seed": SEED},
        "intradayClassification": "daily-OHLC conservative envelope; approximate, not intraday-verified",
        "candidates": {candidate.key: {**candidate.provenance, "observations": len(candidate.frame), "start": str(candidate.frame.index.min().date()), "end": str(candidate.frame.index.max().date())} for candidate in candidates},
        "descriptiveOperatingPoints": _summary(frame),
        "frontier": all_rows,
        "noOptimizationGuard": "No scale is selected or recommended from historical expected payout.",
    }
    (output_dir / "results.json").write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    _report(payload, frame, output_dir)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--paths", type=int, default=N_PATHS, help="Verification override; published study uses preregistered 5000.")
    args = parser.parse_args()
    result = run_study(args.output_dir, args.paths)
    print(json.dumps({"output": str(args.output_dir), "candidates": list(result["candidates"]), "rows": len(result["frontier"])}, indent=2))


if __name__ == "__main__":
    main()
