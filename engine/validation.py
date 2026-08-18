"""Machine-enforced evidence checks run after every backtest.

The old ``metrics.status`` answers a narrow question about one measured run.
This module answers the research question the UI actually asks: has an edge
survived enough hostile tests to be identified?  Results remain dimensional;
there is deliberately no composite score whose average can hide a failed gate.

Cross-sectional ranking strategies receive the full concentration, parameter,
history, and replication batteries.  Other engine shapes still receive a
report, but unsupported evidence is explicit and blocks ``identified_edge``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from itertools import product
import hashlib
import math
import random
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd

from engine import data as data_module
from engine import logging_db
from engine.advanced_validation import (
    causality_contract_evidence,
    factor_attribution_evidence,
    portfolio_interaction_evidence,
    probability_backtest_overfitting,
    purged_cv_evidence,
    regime_stress_evidence,
    statistical_power_evidence,
)
from engine.backtest import StrategyBacktestResult
from engine.cross_sectional import CrossSectionalResult, run_cross_sectional_backtest
from engine.execution_calibration import spread_for, spread_for_universe
from engine.metrics import (
    ASSUMED_PAIRWISE_CORRELATION,
    MIN_INVESTED_DAYS,
    MIN_RELIABLE_TRADES,
    SHARPE_THRESHOLD,
    independent_bets_per_year,
    invested_days,
)
from engine.pairs import PairsResult
from engine.pit_analysis import (
    analyze_pit_result,
    dynamic_random_benchmarks,
    normalized_benchmark,
)
from engine.portfolio import PortfolioResult
from engine.research_governance import (
    VALIDATION_REPORT_VERSION,
    bootstrap_evidence,
    build_run_manifest,
    build_validation_spec,
    chronological_evidence,
    disjoint_replication_evidence,
    leakage_evidence,
    lifecycle_stage,
    spec_completeness,
    standard_cost_stress_evidence,
    standard_dependency_evidence,
)
from engine.universe import EQUITY_UNIVERSE, MIDCAP_UNIVERSE, SECTOR_BENCHMARK, SMALL_CAP_UNIVERSE
from engine.universe_ledger import (
    audit_membership,
    load_manual_membership_evidence,
    resolve_schedule,
)
from engine.universe_registry import gate_applicability, registered_universe
from strategies.params import apply_params
from strategies.swing.dual_momentum import DualMomentum


CheckStatus = Literal["pass", "fail", "warning", "unresolved", "not_applicable"]


def _json_native(value: Any) -> Any:
    """Normalize dataframe/NumPy scalars at the validation API boundary."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_native(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_native(item) for item in value]
    return value


@dataclass
class ValidationCheck:
    key: str
    label: str
    status: CheckStatus
    summary: str
    required: bool = False
    value: float | int | str | bool | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationDimension:
    key: str
    label: str
    checks: list[ValidationCheck]


@dataclass
class EdgeVerdict:
    identified_edge: bool
    headline: str
    signal_edge: str
    universe_specific: str
    beats_buy_and_hold: str
    forward_test_worthy: bool
    production_capital_worthy: bool
    lifecycle_stage: str
    blockers: list[str]
    blocking_checks: list[dict[str, str]]


@dataclass
class ValidationReport:
    version: int
    generated_at: str
    dimensions: list[ValidationDimension]
    verdict: EdgeVerdict
    research: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        verdict = payload["verdict"]
        payload["generatedAt"] = payload.pop("generated_at")
        verdict["identifiedEdge"] = verdict.pop("identified_edge")
        verdict["signalEdge"] = verdict.pop("signal_edge")
        verdict["universeSpecific"] = verdict.pop("universe_specific")
        verdict["beatsBuyAndHold"] = verdict.pop("beats_buy_and_hold")
        verdict["forwardTestWorthy"] = verdict.pop("forward_test_worthy")
        verdict["productionCapitalWorthy"] = verdict.pop("production_capital_worthy")
        verdict["lifecycleStage"] = verdict.pop("lifecycle_stage")
        verdict["blockingChecks"] = verdict.pop("blocking_checks")
        return _json_native(payload)


def _check(
    key: str,
    label: str,
    passed: bool | None,
    summary: str,
    *,
    required: bool = False,
    value: float | int | str | bool | None = None,
    details: dict[str, Any] | None = None,
    unavailable_status: CheckStatus = "unresolved",
) -> ValidationCheck:
    status: CheckStatus = unavailable_status if passed is None else ("pass" if passed else "fail")
    return ValidationCheck(key, label, status, summary, required, value, details or {})


def _not_applicable(key: str, label: str, summary: str) -> ValidationCheck:
    return ValidationCheck(key, label, "not_applicable", summary)


def _finalize(
    dimensions: list[ValidationDimension],
    research: dict[str, Any] | None = None,
) -> ValidationReport:
    research_payload = research or {}
    universe_id = research_payload.get("universeId")
    universe_definition = registered_universe(universe_id) if universe_id else None
    for dimension in dimensions:
        for check in dimension.checks:
            applicable, reason = gate_applicability(universe_id, check.key)
            if not applicable:
                check.status = "not_applicable"
                check.required = False
                check.value = None
                check.summary = reason or "This gate is not defined for the registered universe."
            elif (
                check.key == "pit_membership"
                and check.status == "pass"
                and universe_definition is not None
                and universe_definition.membership_mode not in {
                    "pit_ledger_complete", "dynamic_pit_security_master",
                }
            ):
                declaration = universe_definition.applicable_gates.get("pit_membership") or {}
                check.status = "warning"
                check.required = True
                check.value = None
                check.summary = declaration.get("reason") or (
                    "Point-in-time membership is applicable but the registered ledger is incomplete."
                )
                check.details = {
                    **check.details,
                    "universeId": universe_id,
                    "membershipMode": universe_definition.membership_mode,
                    "registryIntegrityOverride": True,
                }
    # warmup_validity exists specifically for the "a constituent has zero
    # bars during the required lookback" case (the DOW zero-bar bug this
    # gate was built to catch). chronological_evidence (the
    # chronological_oos/"holdout" check) computes its fold-level pass/fail
    # from the SAME equity curve independently, with no knowledge of
    # whether that curve rests on complete warmup data -- so it can, and
    # measurably did (two runs: Dual Momentum x sp500_current, x
    # sp600_current), report "holdout passed" on a curve built from
    # incomplete data. identified_edge/forward_test_worthy were already
    # correctly blocked elsewhere (warmup_validity is itself a required
    # gate), but the individual chronological_oos line read as an
    # unqualified pass to anyone looking at just that check -- gate it
    # explicitly rather than leaving it advisory.
    all_checks_pre = [check for dimension in dimensions for check in dimension.checks]
    warmup_check = next((c for c in all_checks_pre if c.key == "warmup_validity"), None)
    holdout_check = next((c for c in all_checks_pre if c.key == "chronological_oos"), None)
    if (
        warmup_check is not None
        and warmup_check.status not in {"pass", "not_applicable"}
        and holdout_check is not None
        and holdout_check.status == "pass"
    ):
        holdout_check.status = "unresolved"
        holdout_check.summary = (
            f"Blocked by failed warmup validity ({warmup_check.summary}) -- the "
            "chronological split was computed on data with incomplete required "
            "lookback, so this result is not usable evidence even though the "
            "fold-level arithmetic itself passed."
        )
        holdout_check.details = {**holdout_check.details, "blockedByWarmupValidity": True}

    if not any(
        check.key == "statistical_power"
        for dimension in dimensions
        for check in dimension.checks
    ):
        dimensions.insert(0, ValidationDimension(
            "power",
            "Statistical power and detectability",
            [ValidationCheck(
                "statistical_power",
                "Can this design resolve the claimed edge?",
                "unresolved",
                "Minimum detectable alpha was not computed for this run",
                required=True,
                details={"reason": "power evidence missing"},
            )],
        ))
    # Statistical power is question zero. Keep it first in the report even
    # though it is produced by the generic evidence layer after the
    # strategy-specific backtest calculations.
    dimensions = [d for d in dimensions if d.key == "power"] + [
        d for d in dimensions if d.key != "power"
    ]
    checks = [check for dimension in dimensions for check in dimension.checks]
    required = [check for check in checks if check.required]
    blocked = [check for check in required if check.status != "pass"]
    blockers = [check.label for check in blocked]
    blocking_checks = [
        {
            "key": check.key,
            "label": check.label,
            "status": check.status,
            "summary": check.summary,
        }
        for check in blocked
    ]
    identified = bool(required) and not blockers

    # No pass-rate or weighted score. One unresolved required gate remains
    # unresolved; one failed required gate prevents establishment. Power gets
    # its own state because a design that cannot resolve its claim is not
    # negative evidence about the signal itself.
    power = next((c for c in checks if c.key == "statistical_power"), None)
    if identified:
        signal_edge = "Established"
    elif power is None or power.status in {"unresolved", "warning"}:
        signal_edge = "Unresolved"
    elif power.status == "fail":
        signal_edge = "Underpowered"
    elif any(check.status in {"unresolved", "warning"} for check in blocked):
        signal_edge = "Unresolved"
    else:
        signal_edge = "Not established"

    replication = next((c for c in checks if c.key == "cross_universe_replication"), None)
    universe_specific = (
        "Yes" if replication and replication.status == "fail"
        else "No evidence of it" if replication and replication.status == "pass"
        else "Unknown"
    )
    buy_hold = next((c for c in checks if c.key == "beats_spy"), None)
    beats_buy_hold = (
        "Pass - SPY, same window" if buy_hold and buy_hold.status == "pass"
        else "Not established vs SPY"
    )
    # A forward test exists precisely to gather new out-of-sample evidence.
    # Historical PIT membership uncertainty still blocks "identified edge",
    # but does not make a broad, measurable signal ineligible for paper-only
    # observation. Coverage/warmup/cost defects do: those would make the
    # forward experiment itself uninterpretable.
    measurement_blockers = {
        "sample_coverage", "warmup_validity", "measurement_integrity",
        "research_contract", "leakage_audit", "chronological_oos",
    }
    forward_exclusions = {
        "pit_membership", "multiple_testing", "cross_universe_replication",
        "selection_adjusted_significance", "pair_selection_multiplicity",
        "locate_availability",
    }
    forward_required = [
        c for c in required if c.key not in forward_exclusions
    ]
    measurement_clean = all(
        c.status == "pass" for c in checks if c.key in measurement_blockers and c.required
    )
    forward = (
        measurement_clean
        and bool(forward_required)
        and all(c.status == "pass" for c in forward_required)
    )
    # Historical evidence may authorize a locked paper experiment, never
    # production capital by itself. Only engine.forward_experiments can promote
    # the persisted lifecycle after new observations clear the frozen horizon.
    production = False
    holdout = next((c for c in checks if c.key == "chronological_oos"), None)
    stage = lifecycle_stage(
        is_preregistered=bool(research_payload.get("isPreregistered", False)),
        holdout_passed=bool(holdout and holdout.status == "pass"),
        forward_test_worthy=forward,
        production_capital_worthy=production,
        signal_edge=signal_edge,
    )
    research_payload["lifecycleStage"] = stage
    if identified:
        headline = "Identified edge"
    elif power and power.status == "fail":
        selected_mda = power.details.get("selectedMdaPct")
        threshold = power.details.get("minimumTradableAlphaPct")
        if isinstance(selected_mda, (int, float)) and isinstance(threshold, (int, float)):
            headline = (
                f"Underpowered - MDA {selected_mda:.2f}%/yr exceeds "
                f"{threshold:.2f}%/yr"
            )
        else:
            headline = "Underpowered - MDA exceeds the actionable-alpha threshold"
    elif power is None or power.status in {"unresolved", "warning"}:
        headline = "Power unresolved - MDA not computable"
    elif signal_edge == "Unresolved":
        headline = "Evidence unresolved - required gates remain"
    else:
        headline = "Edge not established"
    return ValidationReport(
        version=VALIDATION_REPORT_VERSION,
        generated_at=pd.Timestamp.now(tz="UTC").isoformat(),
        dimensions=dimensions,
        verdict=EdgeVerdict(
            identified_edge=identified,
            headline=headline,
            signal_edge=signal_edge,
            universe_specific=universe_specific,
            beats_buy_and_hold=beats_buy_hold,
            forward_test_worthy=forward,
            production_capital_worthy=production,
            lifecycle_stage=stage,
            blockers=blockers,
            blocking_checks=blocking_checks,
        ),
        research=research_payload,
    )


def _return_pct(equity: pd.Series | None) -> float | None:
    if equity is None or len(equity) < 2 or equity.iloc[0] == 0:
        return None
    return float((equity.iloc[-1] / equity.iloc[0] - 1.0) * 100.0)


def _turnover_pct(rebalances: pd.DataFrame) -> float:
    """One-way turnover summed across rebalances, as percent of capital."""
    previous: dict[str, float] = {}
    turnover = 0.0
    for _, row in rebalances.iterrows():
        current = row["holdings"]
        names = set(previous) | set(current)
        turnover += 0.5 * sum(abs(current.get(s, 0.0) - previous.get(s, 0.0)) for s in names)
        previous = current
    return turnover * 100.0


def _benchmark_return(symbol: str, start: date, end: date) -> float | None:
    bars = data_module.get_bars(symbol, "1d", start, end)
    if bars.empty or len(bars) < 2:
        return None
    return float((bars["Close"].iloc[-1] / bars["Close"].iloc[0] - 1.0) * 100.0)


def _close_frame(symbols: list[str], start: date, end: date) -> tuple[pd.DataFrame, list[str]]:
    series: dict[str, pd.Series] = {}
    missing: list[str] = []
    for symbol in symbols:
        bars = data_module.get_bars(symbol, "1d", start, end)
        if bars.empty or len(bars) < 2:
            missing.append(symbol)
        else:
            series[symbol] = bars["Close"]
    if not series:
        return pd.DataFrame(), missing
    return pd.DataFrame(series).sort_index().ffill(), missing


def _preload_bars(
    symbols: list[str], start: date, end: date, warmup_days: int
) -> dict[str, pd.DataFrame]:
    fetch_start = start - timedelta(days=int(warmup_days * 1.45) + 7) if warmup_days else start
    return {symbol: data_module.get_bars(symbol, "1d", fetch_start, end) for symbol in symbols}


def _close_from_bars(
    bars_by_symbol: dict[str, pd.DataFrame], symbols: list[str], start: date
) -> tuple[pd.DataFrame, list[str]]:
    series: dict[str, pd.Series] = {}
    missing: list[str] = []
    for symbol in symbols:
        bars = bars_by_symbol.get(symbol, pd.DataFrame())
        if bars.empty:
            missing.append(symbol)
            continue
        boundary = pd.Timestamp(start, tz=bars.index.tz)
        window = bars.loc[bars.index >= boundary, "Close"]
        if len(window) < 2:
            missing.append(symbol)
        else:
            series[symbol] = window
    return (pd.DataFrame(series).sort_index().ffill() if series else pd.DataFrame()), missing


def _equal_weight_curve(close: pd.DataFrame) -> pd.Series | None:
    if close.empty:
        return None
    normalized = close.apply(lambda s: s / s.dropna().iloc[0] if not s.dropna().empty else np.nan)
    curve = normalized.mean(axis=1, skipna=True).dropna()
    return curve if len(curve) >= 2 else None


def _random_portfolios(
    close: pd.DataFrame,
    top_n: int,
    strategy_return_pct: float,
    *,
    simulations: int = 400,
    seed_material: str,
) -> dict[str, Any] | None:
    if close.empty or top_n < 1 or close.shape[1] < top_n:
        return None
    symbols = list(close.columns)
    seed = int(hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)
    returns: list[float] = []
    for _ in range(simulations):
        chosen = rng.sample(symbols, top_n)
        value = _return_pct(_equal_weight_curve(close[chosen]))
        if value is not None:
            returns.append(value)
    if not returns:
        return None
    arr = np.asarray(returns, dtype=float)
    exceedances = int((arr >= strategy_return_pct).sum())
    p_value = (exceedances + 1) / (len(arr) + 1)
    return {
        "simulations": len(arr),
        "meanPct": float(arr.mean()),
        "medianPct": float(np.median(arr)),
        "p05Pct": float(np.quantile(arr, 0.05)),
        "p95Pct": float(np.quantile(arr, 0.95)),
        "maxPct": float(arr.max()),
        "percentile": float((arr < strategy_return_pct).mean() * 100.0),
        "empiricalP": float(p_value),
        # Named explicitly because this project has now confused this figure
        # with `multiple_testing`'s bootstrap-based p-value twice: they are
        # DIFFERENT NULLS. This one asks "how often does a RANDOM top-N
        # selection from the same universe beat this return" -- it isolates
        # SELECTION SKILL from concentration. See `multiple_testing`'s
        # nullDefinition for the other one.
        "nullDefinition": "concentration-matched random top-N portfolio selection",
    }


def _slice_return(equity: pd.Series, start: date, end: date) -> float | None:
    if equity.empty:
        return None
    tz = equity.index.tz
    lo = pd.Timestamp(start, tz=tz)
    hi = pd.Timestamp(end, tz=tz)
    return _return_pct(equity.loc[lo:hi])


def _rolling_stability(
    strategy_curve: pd.Series,
    ew_curve: pd.Series,
    start_year: int,
    end_year: int,
) -> dict[str, Any] | None:
    contributions: list[dict[str, Any]] = []
    for year in range(start_year, end_year - 2):
        window_start = date(year, 1, 1)
        window_end = date(year + 3, 1, 1) - timedelta(days=1)
        strategy_ret = _slice_return(strategy_curve, window_start, window_end)
        ew_ret = _slice_return(ew_curve, window_start, window_end)
        if strategy_ret is None or ew_ret is None:
            continue
        contributions.append({
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "contributionPct": strategy_ret - ew_ret,
        })
    if len(contributions) < 3:
        return None
    values = np.asarray([row["contributionPct"] for row in contributions], dtype=float)
    best_index = int(values.argmax())
    without_best = np.delete(values, best_index)
    return {
        "windows": contributions,
        "count": len(values),
        "fractionPositive": float((values > 0).mean()),
        "meanContributionPct": float(values.mean()),
        "medianContributionPct": float(np.median(values)),
        "worstContributionPct": float(values.min()),
        "bestContributionPct": float(values.max()),
        "meanExcludingBestPct": float(without_best.mean()) if len(without_best) else None,
    }


# Rolling stability uses OVERLAPPING 3-year windows stepped one year at a
# time (see _rolling_stability above), so a run spanning ~16 calendar years
# produces "windows": 14 that are NOT 14 independent draws -- consecutive
# windows share up to 2 of their 3 years. Dual Momentum's own recorded
# evidence (data/manual_validation_evidence.json) estimates the effective
# independent sample at 5-6 windows for exactly this reason
# (effectiveIndependentWindowsLow/High). Requiring >=70% of the RAW 14
# windows positive is therefore, in effective-sample terms, requiring
# roughly 4 of ~5-6 independent windows positive -- a real bar, not a
# rubber stamp, but deliberately not stiffened further given how few
# independent windows a multi-decade daily-bar backtest actually contains.
# This value is pinned by test_rolling_stability_gate_matches_recorded_dual_momentum_evidence
# in tests/test_engine/test_validation.py against the exact recorded numbers
# above -- change the threshold there first if it ever needs to move, so the
# change is visible as a diff rather than a silent verdict flip (see
# LESSONS.md's "gate silently changes verdict on fixed evidence" entry).
STABILITY_MIN_FRACTION_POSITIVE = 0.70


def _stability_gate_passes(stability: dict[str, Any] | None) -> bool:
    return (
        stability is not None
        and stability["fractionPositive"] >= STABILITY_MIN_FRACTION_POSITIVE
        and stability["medianContributionPct"] > 0
        and stability["meanExcludingBestPct"] > 0
    )


def _rolling_matched_trade_stability(
    result: StrategyBacktestResult, start: date, end: date,
) -> dict[str, Any] | None:
    """Rolling three-year mean trade excess for sparse event strategies.

    Each trade is compared with SPY over its exact entry/exit interval first;
    only then are those matched excesses grouped into historical windows.
    This avoids turning idle cash into a full-window benchmark position.
    """
    frames = []
    for symbol_result in result.per_symbol.values():
        trades = symbol_result.trades
        if trades.empty or "ExcessVsSPY" not in trades:
            continue
        frame = trades[["ExitTime", "ExcessVsSPY"]].copy()
        frame["ExitTime"] = pd.to_datetime(frame["ExitTime"])
        frame["ExcessVsSPY"] = pd.to_numeric(
            frame["ExcessVsSPY"], errors="coerce"
        )
        frames.append(frame.dropna())
    if not frames:
        return None
    pooled = pd.concat(frames, ignore_index=True)
    windows = []
    for year in range(start.year, end.year - 2):
        window_start = date(year, 1, 1)
        window_end = date(year + 3, 1, 1) - timedelta(days=1)
        mask = (
            (pooled["ExitTime"].dt.date >= window_start)
            & (pooled["ExitTime"].dt.date <= window_end)
        )
        values = pooled.loc[mask, "ExcessVsSPY"]
        if len(values) < 10:
            continue
        windows.append({
            "start": window_start.isoformat(), "end": window_end.isoformat(),
            "matchedTrades": int(len(values)),
            "meanTradeExcessPct": float(values.mean() * 100.0),
        })
    if len(windows) < 3:
        return None
    values = np.asarray([row["meanTradeExcessPct"] for row in windows], dtype=float)
    without_best = np.delete(values, int(values.argmax()))
    return {
        "method": "equal-weight mean of exact-interval trade excess vs SPY",
        "windows": windows, "count": len(windows),
        "fractionPositive": float((values > 0).mean()),
        "meanContributionPct": float(values.mean()),
        "medianContributionPct": float(np.median(values)),
        "worstContributionPct": float(values.min()),
        "bestContributionPct": float(values.max()),
        "meanExcludingBestPct": float(without_best.mean()),
    }


def _research_context(context: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "experimentId": None,
        "familySearchNumber": 1,
        "isPreregistered": False,
        **(context or {}),
    }


def _standard_parameter_robustness(
    result: StrategyBacktestResult,
    applied_params: dict[str, Any] | None,
    progress: Callable[[int, str], None] | None,
    universe_id: str | None = None,
) -> tuple[bool | None, dict[str, Any]]:
    """Replay immediate declared neighbors without logging sweep arms."""
    from engine.runner import RunRequest, run_backtest, strategy_class
    from strategies.params import describe_params

    specs = describe_params(strategy_class(result.strategy_name))
    if not specs:
        return None, {"reason": "strategy declares no tunable parameters"}
    current = {spec.name: (applied_params or {}).get(spec.name, spec.default) for spec in specs}
    arms: list[dict[str, Any]] = []
    candidates: list[tuple[str, Any]] = []
    for spec in specs:
        value = current[spec.name]
        if spec.kind == "bool":
            candidates.append((spec.name, not bool(value)))
        elif spec.kind == "str" and spec.choices and value in spec.choices:
            index = spec.choices.index(value)
            if index > 0:
                candidates.append((spec.name, spec.choices[index - 1]))
            if index + 1 < len(spec.choices):
                candidates.append((spec.name, spec.choices[index + 1]))
        elif spec.kind in {"int", "float"} and isinstance(value, (int, float)):
            step = spec.step or max(abs(float(value)) * 0.10, 1.0 if spec.kind == "int" else 0.1)
            for raw in (float(value) - step, float(value) + step):
                if spec.minimum is not None and raw < spec.minimum:
                    continue
                if spec.maximum is not None and raw > spec.maximum:
                    continue
                candidates.append((spec.name, int(round(raw)) if spec.kind == "int" else float(raw)))
    # Bound latency while covering as many distinct parameter directions as
    # possible. The selected configuration itself is represented by the base
    # result and is not rerun.
    candidates = candidates[:8]
    for index, (name, value) in enumerate(candidates):
        if progress:
            progress(45 + int(15 * index / max(1, len(candidates))),
                     f"Parameter robustness: {name}={value}")
        params = {**current, name: value}
        request = RunRequest(
            symbols=list(result.symbols), start=result.start, end=result.end, params=params,
            universe_id=universe_id,
        )
        try:
            arm_result = run_backtest(result.strategy_name, request, persist=False)
            from engine.portfolio import run_portfolio_backtest
            arm_portfolio = run_portfolio_backtest(
                arm_result, risk_free_rate=arm_result.metrics.risk_free_rate or 0.0,
            )
            spy = _benchmark_return(SECTOR_BENCHMARK, result.start, result.end)
            contribution = None if spy is None else arm_portfolio.return_pct - spy
            arms.append({
                "parameter": name,
                "value": value,
                "returnPct": arm_portfolio.return_pct,
                "benchmarkReturnPct": spy,
                "contributionPct": contribution,
            })
        except (ValueError, RuntimeError) as exc:
            arms.append({"parameter": name, "value": value, "error": str(exc), "contributionPct": None})
    resolved = [arm for arm in arms if arm.get("contributionPct") is not None]
    fraction_positive = (
        sum(arm["contributionPct"] > 0 for arm in resolved) / len(resolved)
        if resolved else None
    )
    passed = None if fraction_positive is None else fraction_positive >= 0.60
    return passed, {
        "arms": arms,
        "fractionBeatingBenchmark": fraction_positive,
        "candidateArms": len(candidates),
        "policy": "immediate declared neighbors; maximum eight non-persisted arms",
    }


def _generic_research_dimensions(
    *,
    strategy_name: str,
    engine: Literal["standard", "cross_sectional", "pairs"],
    symbols: list[str],
    start: date,
    end: date,
    equity: pd.Series,
    benchmark_equity: pd.Series | None,
    trades: pd.DataFrame | None,
    applied_params: dict[str, Any] | None,
    context: dict[str, Any] | None,
    extra_checks: list[ValidationCheck],
) -> tuple[list[ValidationDimension], dict[str, Any]]:
    from engine.runner import run_config, strategy_class

    ctx = _research_context(context)
    interval, _, _, _ = run_config(strategy_name)
    cls = strategy_class(strategy_name)
    spec = build_validation_spec(
        strategy_name, engine, symbols, cls, universe_id=ctx.get("universeId"),
    )
    contract_pass, contract_details = spec_completeness(spec)
    holdout_pass, holdout_details = chronological_evidence(
        equity, start, end, spec, benchmark_equity=benchmark_equity,
    )
    leakage_pass, leakage_details = leakage_evidence(equity, trades, spec)
    bootstrap_pass, bootstrap_details = bootstrap_evidence(
        equity, start, end, seed=spec.random_seed, benchmark_equity=benchmark_equity,
    )
    family_searches = max(
        1,
        int(ctx.get("familySearchCount") or ctx.get("familySearchNumber") or 1),
    )
    family_search_number = max(1, int(ctx.get("familySearchNumber") or 1))
    # DISTINCT null from `beats_random`/`selection_adjusted_significance`'s
    # empiricalP (concentration-matched random portfolio selection): this one
    # is 1 - P(block-bootstrap resample of the strategy's OWN realized daily
    # excess returns outperforms the benchmark), i.e. "how often would a
    # bootstrap resample of what actually happened still beat the benchmark."
    # Naming it distinctly here so it never gets read as a second copy of the
    # concentration p-value -- see nullDefinition in the check details below.
    naive_bootstrap_p = (
        None if "probabilityOutperform" not in bootstrap_details
        else 1.0 - float(bootstrap_details["probabilityOutperform"])
    )
    corrected_p = None if naive_bootstrap_p is None else min(1.0, naive_bootstrap_p * family_searches)
    resolved_benchmark = benchmark_equity
    if resolved_benchmark is None:
        benchmark_bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", start, end)
        if not benchmark_bars.empty:
            close = pd.to_numeric(benchmark_bars["Close"], errors="coerce").dropna()
            if len(close) >= 2:
                resolved_benchmark = close / float(close.iloc[0]) * float(equity.iloc[0])
    causality_pass, causality_details = causality_contract_evidence(engine, cls)
    purged_pass, purged_details = purged_cv_evidence(
        equity, resolved_benchmark, family_searches=family_searches,
    )
    years = max(0.0, (end - start).days / 365.25)
    # Decisions per year DRIVES the regression basis in both
    # factor_attribution_evidence and statistical_power_evidence -- computed
    # once, here, so the two evidence functions cannot silently disagree about
    # what a "decision" was for this run.
    #
    #   cross_sectional -- a real calendar cadence exists (the strategy's own
    #     rebalance_frequency), so this is exact.
    #   standard / pairs -- no calendar cadence; entries fire on irregular,
    #     signal-driven dates. Estimated from REALIZED trade frequency
    #     (trades_taken / years) instead. This is an estimate of the implied
    #     holding period, not a claim of a fixed cadence -- see
    #     _decision_periodicity's docstring for how it is used (a daily
    #     regression with the HAC lag spanning that implied period), which
    #     tolerates the estimate being approximate.
    rebalances_per_year: float | None = None
    effective_bets = None
    if engine == "cross_sectional":
        from strategies.params import describe_params

        resolved_params = {item.name: item.default for item in describe_params(cls)}
        resolved_params.update(applied_params or {})
        cadence = str(resolved_params.get("rebalance_frequency", "monthly"))
        rebalances_per_year = {
            "daily": 252.0,
            "weekly": 52.0,
            "semimonthly": 24.0,
            "monthly": 12.0,
            "quarterly": 4.0,
        }.get(cadence)
        bets_per_year = independent_bets_per_year(
            int(resolved_params.get("top_n", 1)), rebalances_per_year,
        )
        effective_bets = None if bets_per_year is None else bets_per_year * years
        decisions_per_year = rebalances_per_year
    elif trades is not None and not trades.empty and years > 0:
        decisions_per_year = len(trades) / years
    else:
        decisions_per_year = None
    factor_pass, factor_details = factor_attribution_evidence(
        equity, start, end, decisions_per_year=decisions_per_year,
    )
    power_pass, power_details = statistical_power_evidence(
        equity,
        minimum_tradable_alpha_pct=spec.minimum_tradable_alpha_pct,
        factor_details=factor_details,
        effective_independent_bets=effective_bets,
        assumed_pairwise_correlation=(
            ASSUMED_PAIRWISE_CORRELATION if engine == "cross_sectional" else None
        ),
        decisions_per_year=decisions_per_year,
    )
    if ctx.get("preResultPower"):
        planned = dict(ctx["preResultPower"])
        power_pass = bool(planned.get("viable"))
        power_details = {
            **planned,
            "selectedMdaPct": planned.get("mdaPct"),
            "minimumTradableAlphaPct": spec.minimum_tradable_alpha_pct,
            "selectedBasis": planned.get("method"),
            "computedBeforeResults": True,
        }
    regime_pass, regime_details = regime_stress_evidence(
        equity, resolved_benchmark, start, end,
    )
    peer_curves = logging_db.peer_equity_curves(strategy_name)
    allocation_pass, allocation_details = portfolio_interaction_evidence(equity, peer_curves)
    manifest = build_run_manifest(
        strategy_name=strategy_name,
        engine=engine,
        symbols=symbols,
        interval=interval,
        start=start,
        end=end,
        params=applied_params,
        strategy_class=cls,
        experiment_id=ctx.get("experimentId"),
        spec=spec,
        equity=equity,
    )
    family_curves = logging_db.strategy_equity_curves(strategy_name)
    family_curves[manifest["runFingerprint"]] = equity
    pbo_pass, pbo_details = probability_backtest_overfitting(family_curves)
    dimensions = [
        ValidationDimension("power", "Statistical power and detectability", [
            _check(
                "statistical_power", "Can this design resolve the claimed edge?", power_pass,
                "Minimum detectable annual benchmark-relative alpha must not exceed the pre-registered actionable effect",
                required=True,
                value=power_details.get("selectedMdaPct"),
                details=power_details,
            ),
        ]),
        ValidationDimension("research_contract", "Mandatory validation specification", [
            _check(
                "research_contract", "Complete pre-run validation contract", contract_pass,
                "Hypothesis, benchmark, universe policy, warmup, fills, costs, holdout, search family, and release timing are mandatory",
                required=True, details=contract_details,
            ),
            _check(
                "preregistration", "Configuration registered before execution",
                bool(ctx.get("isPreregistered")),
                "The exact symbols, dates, parameters, hypothesis, and primary criterion must be stored before results are computed",
                required=True,
                details={"experimentId": ctx.get("experimentId"),
                         "familySearchNumber": family_search_number,
                         "familySearchCount": family_searches},
            ),
            _check(
                "data_quality", "Pre-run market-data quality", (
                    None if ctx.get("dataQuality") is None
                    else bool(ctx["dataQuality"].get("passed"))
                ),
                "OHLCV schema, timestamps, finite values, price bounds, volume, gaps, and corporate-action outliers are audited before statistics",
                required=True, details=ctx.get("dataQuality") or {"reason": "pre-run audit was not registered"},
            ),
        ]),
        ValidationDimension("out_of_sample", "Chronological out-of-sample discipline", [
            _check(
                "chronological_oos", "Untouched holdout and walk-forward windows", holdout_pass,
                "The final 20% must beat the benchmark and at least 60% of fixed-rule chronological folds must contribute positively",
                required=True, details=holdout_details,
            ),
        ]),
        ValidationDimension("leakage", "Look-ahead and leakage audit", [
            _check(
                "leakage_audit", "Causal timestamps and execution boundary", leakage_pass,
                "Trade chronology, finite values, completed-bar inputs, and externally timestamped information are enforced",
                required=True, details=leakage_details,
            ),
            _check(
                "causality_contract", "Engine-bound causality contract", causality_pass,
                "The tested engine source must enforce completed-information prefixes and next-open execution, with no obvious future-looking strategy operator",
                required=True, details=causality_details,
            ),
        ]),
        ValidationDimension("purged_validation", "Purged time-series validation", [
            _check(
                "purged_cv", "Purged folds and deflated Sharpe", purged_pass,
                "At least two-thirds of embargoed folds must contribute positively and deflated excess Sharpe must remain significant",
                required=True, details=purged_details,
            ),
            _check(
                "probability_backtest_overfitting", "Probability of backtest overfitting",
                pbo_pass if family_searches > 1 else None,
                "CSCV compares every archived configuration in the declared search family; PBO must not exceed 20%",
                required=family_searches > 1,
                details=pbo_details,
                unavailable_status="not_applicable" if family_searches <= 1 else "unresolved",
            ),
        ]),
        ValidationDimension("uncertainty", "Block-bootstrap uncertainty", [
            _check(
                "bootstrap_confidence", "Benchmark-relative confidence interval", bootstrap_pass,
                "Requires at least 80% bootstrap probability of outperforming and a positive 5th-percentile contribution",
                required=True, value=bootstrap_details.get("probabilityOutperform"), details=bootstrap_details,
            ),
        ]),
        ValidationDimension("research_controls", "Cost, capacity, dependency, and replication controls", extra_checks),
        ValidationDimension("attribution", "Factor, regime, and allocation attribution", [
            _check(
                "factor_residual_alpha", "Residual alpha after tradable factors", factor_pass,
                "Residual annual alpha must be positive with t-stat at least 2 after market, size, value, and momentum proxy exposure",
                required=True, details=factor_details,
            ),
            _check(
                "regime_stress", "Cross-regime contribution", regime_pass,
                "Benchmark-relative contribution must remain positive in at least two sufficiently sampled market regimes",
                required=regime_pass is not None, details=regime_details,
                unavailable_status="unresolved",
            ),
            _check(
                "portfolio_interaction", "Marginal portfolio allocation value", allocation_pass,
                "A 50/50 combination must improve at least one archived peer strategy's Sharpe",
                required=False, details=allocation_details, unavailable_status="not_applicable",
            ),
        ]),
        ValidationDimension("experiment_accounting", "Experiment accounting and reproducibility", [
            _check(
                "multiple_testing", "Actual search-family correction",
                corrected_p is not None and corrected_p <= 0.05,
                "Bonferroni-corrects the block-bootstrap outperformance p-value "
                "(naiveBootstrapP: how often a resample of this strategy's OWN "
                "realized returns still beats the benchmark) for the persisted "
                "number of experiments in this strategy family. This is a "
                "DIFFERENT null from the concentration/`beats_random` check's "
                "empiricalP, which instead asks how often a RANDOM top-N "
                "selection from the same universe beats this return -- see "
                "that check's nullDefinition. Do not treat the two p-values "
                "as duplicates or average them.",
                required=True,
                value=corrected_p,
                details={
                    "familySearchNumber": family_search_number,
                    "familySearchCount": family_searches,
                    "naiveBootstrapP": naive_bootstrap_p,
                    "correctedP": corrected_p,
                    "searchFamily": spec.search_family,
                    "nullDefinition": "block-bootstrap resample of the strategy's own realized daily excess returns vs benchmark",
                },
            ),
            _check(
                "reproducible_manifest", "Reproducible run manifest", not manifest["missingDataFiles"],
                "Code, cached data files, result, configuration, dependencies, and random seeds are fingerprinted",
                required=True,
                details=manifest,
            ),
        ]),
    ]
    research = {
        "experimentId": ctx.get("experimentId"),
        "familySearchNumber": family_search_number,
        "familySearchCount": family_searches,
        "universeId": ctx.get("universeId"),
        "preResultPower": ctx.get("preResultPower"),
        "isPreregistered": bool(ctx.get("isPreregistered")),
        "validationSpec": spec.to_dict(),
        "manifest": manifest,
        "dataQuality": ctx.get("dataQuality"),
        "canonicalPortfolioMetrics": ctx.get("canonicalPortfolioMetrics"),
        "canonicalReplayDriftPct": ctx.get("canonicalReplayDriftPct"),
        "canonicalReplayTolerancePct": ctx.get("canonicalReplayTolerancePct"),
    }
    logging_db.archive_equity_curve(
        strategy_name=strategy_name,
        experiment_id=ctx.get("experimentId"),
        run_fingerprint=manifest["runFingerprint"],
        equity=equity,
    )
    return dimensions, research


def validate_standard(
    result: StrategyBacktestResult,
    portfolio: PortfolioResult,
    *,
    applied_params: dict[str, Any] | None = None,
    research_context: dict[str, Any] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> ValidationReport:
    """Evidence report for per-symbol strategies without inventing ranking tests."""
    m = result.metrics
    measured_start = m.measured_start or result.start
    measured_end = m.measured_end or result.end
    invested = invested_days(m.exposure_pct, measured_start, measured_end)
    coverage_ok = (
        m.trades_taken >= MIN_RELIABLE_TRADES
        and invested is not None
        and invested >= MIN_INVESTED_DAYS
    )
    measured_days = max(0, (measured_end - measured_start).days)
    requested_days = max(1, (result.end - result.start).days)
    coverage_ratio = measured_days / requested_days
    spy_return = _benchmark_return(SECTOR_BENCHMARK, measured_start, measured_end)
    if progress:
        progress(35, "Auditing chronological holdout, leakage, and uncertainty")
    frozen_only = bool((research_context or {}).get("frozenConfigOnly"))
    if frozen_only:
        parameter_pass, parameter_details = None, {
            "reason": "Frozen multi-universe sweep forbids parameter-neighbor replays."
        }
    else:
        parameter_pass, parameter_details = _standard_parameter_robustness(
            result, applied_params, progress, (research_context or {}).get("universeId"),
        )
    dependency_pass, dependency_details = standard_dependency_evidence(
        result.per_symbol, portfolio.trades,
    )
    from engine.runner import run_config
    interval = run_config(result.strategy_name)[0]
    cost_pass, cost_details = standard_cost_stress_evidence(
        result.per_symbol,
        portfolio.trades,
        result.symbols,
        interval,
        measured_start,
        measured_end,
        portfolio.return_pct,
        spy_return,
        universe_id=(research_context or {}).get("universeId"),
    )
    replication_pass, replication_details = disjoint_replication_evidence(result.per_symbol)
    frozen_pit = result.research_metadata.get("pointInTimeUniverse") == "dow_jones_industrial_average"
    membership = audit_membership(
        universe_key="dow_jones_industrial_average" if frozen_pit else None,
        symbols=result.symbols,
        start=measured_start,
        end=measured_end,
        membership_required=frozen_pit,
        point_in_time_applied=frozen_pit,
    )
    equal_weight_gap = (
        None if m.buy_hold_return_pct is None
        else portfolio.return_pct - m.buy_hold_return_pct
    )
    matched_stability = (
        _rolling_matched_trade_stability(result, measured_start, measured_end)
        if frozen_pit else None
    )
    matched_stability_pass = _stability_gate_passes(matched_stability)

    dimensions = [
        ValidationDimension("performance", "Raw strategy performance", [
            _check("positive_return", "Positive absolute return", portfolio.return_pct > 0,
                   f"Shared-capital return {portfolio.return_pct:+.1f}%", value=portfolio.return_pct),
            _check("beats_cash", "Beats cash / risk-free", m.sharpe is not None and m.sharpe > SHARPE_THRESHOLD,
                   "Sharpe must exceed 0.50 against the measured risk-free rate", required=True, value=m.sharpe),
            _check("sample_coverage", "Valid trade and exposure coverage", coverage_ok,
                   f"{m.trades_taken} trades; {invested or 0:.1f} invested days", required=True,
                   details={"trades": m.trades_taken, "investedDays": invested}),
            _check("warmup_validity", "Measured-window coverage", coverage_ratio >= 0.9,
                   f"Measured {coverage_ratio:.0%} of the requested window", required=True, value=coverage_ratio),
            _check("measurement_integrity", "Costs and portfolio allocation modeled", True,
                   "Per-symbol spread, shared-capital capacity, turnover, and modeled costs are included", required=True,
                   details={
                       "turnoverPct": m.turnover_pct,
                       "modeledCosts": m.modeled_costs,
                       "averageGrossExposurePct": m.average_gross_exposure_pct,
                       "averageNetExposurePct": m.average_net_exposure_pct,
                       "timeInMarketPct": m.time_in_market_pct,
                   }),
        ]),
        ValidationDimension("benchmarks", "Benchmark-relative checks", [
            _check("beats_matched_spy", "Beats exposure-matched SPY",
                   m.matched_spy_excess_pct is not None and m.matched_spy_excess_pct > 0,
                   "SPY is held only over each trade's exact represented entry/exit interval and deployed notional",
                   required=True, value=m.matched_spy_excess_pct,
                   details={
                       **result.matched_benchmark,
                       "averageGrossExposurePct": m.average_gross_exposure_pct,
                       "timeInMarketPct": m.time_in_market_pct,
                   }),
            _check("beats_spy", "Buy-and-hold SPY gap (descriptive)",
                   spy_return is not None and portfolio.return_pct > spy_return,
                   "Full-window buy-and-hold context; not the primary edge gate for a sparse strategy",
                   required=False, value=None if spy_return is None else portfolio.return_pct - spy_return,
                   details={
                       "strategyReturnPct": portfolio.return_pct, "spyReturnPct": spy_return,
                       # The window that actually produced spyReturnPct -- pinned
                       # explicitly because measured_start/measured_end (data
                       # coverage) can differ from the requested start/end, and a
                       # canonical run's requested end defaults to "today", so this
                       # window silently extends on every re-run. See
                       # logging_db.py:_STANDARD_BENCHMARK_COLUMNS.
                       "measuredStart": measured_start.isoformat(),
                       "measuredEnd": measured_end.isoformat(),
                   }),
            _check("beats_equal_weight", "Beats equal-weight universe buy-and-hold",
                   equal_weight_gap is not None and equal_weight_gap > 0,
                   "Shared-capital strategy return minus equal-weight buy-and-hold on the same symbols",
                   required=True, value=equal_weight_gap,
                   details={"strategyReturnPct": portfolio.return_pct,
                            "equalWeightReturnPct": m.buy_hold_return_pct}),
            _not_applicable("beats_random", "Concentration-matched random portfolios",
                            "This engine generates timed signals rather than selecting a top-N basket"),
        ]),
        ValidationDimension("robustness", "Robustness and stability", [
            _check("parameter_ridge", "Broad parameter ridge", parameter_pass,
                   "Immediate declared parameter neighbors are replayed without writing sweep arms to run history",
                   required=parameter_pass is not None,
                   value=parameter_details.get("fractionBeatingBenchmark"), details=parameter_details,
                   unavailable_status="not_applicable"),
            *(
                [_check(
                    "historical_stability",
                    "Rolling 3-year matched-SPY trade stability",
                    matched_stability_pass,
                    "At least 70% positive with positive median and mean excluding the best window",
                    required=True, details=matched_stability or {
                        "reason": "fewer than three adequately sampled 3-year windows"
                    },
                )]
                if frozen_pit else []
            ),
        ]),
        ValidationDimension("integrity", "Universe and research integrity", [
            _check("pit_membership", "Point-in-time universe integrity", membership.passed,
                   membership.summary, required=True, details=membership.details),
            (
                _not_applicable(
                    "cross_universe_replication", "Cross-universe replication",
                    "S&P PIT price coverage is incomplete; static current rosters are prohibited",
                )
                if frozen_pit else
                _check("cross_universe_replication", "Disjoint-universe replication", replication_pass,
                       "The frozen rule must remain profitable in both deterministic symbol halves",
                       required=True, details=replication_details)
            ),
        ]),
    ]
    generic, research = _generic_research_dimensions(
        strategy_name=result.strategy_name,
        engine="standard",
        symbols=result.symbols,
        start=measured_start,
        end=measured_end,
        equity=portfolio.equity_curve,
        benchmark_equity=None,
        trades=portfolio.trades,
        applied_params=applied_params,
        context=research_context,
        extra_checks=[
            _check("execution_cost_stress", "Execution-cost and capacity stress", cost_pass,
                   "The edge must survive doubled costs, remain positive at tripled costs, and disclose unmodeled impact/borrow limits",
                   required=True, details=cost_details),
            _check("dependency_concentration", "Symbol, trade, and regime dependency", dependency_pass,
                   "No single symbol may dominate positive P&L; results must survive removing the best five trades and span profitable years",
                   required=True, details=dependency_details),
        ],
    )
    if progress:
        progress(94, "Applying lifecycle and promotion gates")
    return _finalize(dimensions + generic, research)


def _pairs_parameter_robustness(
    result: PairsResult,
    applied_params: dict[str, Any] | None,
    progress: Callable[[int, str], None] | None,
    universe_id: str | None = None,
) -> tuple[bool | None, dict[str, Any]]:
    from engine.runner import RunRequest, run_pairs, strategy_class
    from strategies.params import describe_params

    specs = describe_params(strategy_class(result.strategy_name))
    current = {spec.name: (applied_params or {}).get(spec.name, spec.default) for spec in specs}
    candidates: list[tuple[str, Any]] = []
    for spec in specs:
        value = current[spec.name]
        if spec.kind in {"int", "float"} and isinstance(value, (int, float)):
            step = spec.step or max(abs(float(value)) * 0.10, 1.0 if spec.kind == "int" else 0.1)
            for raw in (float(value) - step, float(value) + step):
                if spec.minimum is not None and raw < spec.minimum:
                    continue
                if spec.maximum is not None and raw > spec.maximum:
                    continue
                candidates.append((spec.name, int(round(raw)) if spec.kind == "int" else float(raw)))
    candidates = candidates[:6]
    arms = []
    start = result.training_window[0].date()
    end = result.trading_window[1].date()
    for index, (name, value) in enumerate(candidates):
        if progress:
            progress(45 + int(15 * index / max(1, len(candidates))),
                     f"Pairs robustness: {name}={value}")
        try:
            arm = run_pairs(
                result.strategy_name,
                RunRequest(
                    symbols=result.symbols, start=start, end=end,
                    params={**current, name: value}, universe_id=universe_id,
                ),
                persist=False,
            )
            spy = _benchmark_return(SECTOR_BENCHMARK, arm.trading_window[0].date(), arm.trading_window[1].date())
            arms.append({
                "parameter": name,
                "value": value,
                "pair": None if arm.pair is None else f"{arm.pair.symbol_a}/{arm.pair.symbol_b}",
                "returnPct": arm.return_pct,
                "benchmarkReturnPct": spy,
                "contributionPct": None if spy is None else arm.return_pct - spy,
            })
        except (ValueError, RuntimeError) as exc:
            arms.append({"parameter": name, "value": value, "error": str(exc), "contributionPct": None})
    resolved = [arm for arm in arms if arm.get("contributionPct") is not None]
    fraction = sum(arm["contributionPct"] > 0 for arm in resolved) / len(resolved) if resolved else None
    return (None if fraction is None else fraction >= 0.60), {
        "arms": arms, "fractionBeatingBenchmark": fraction,
        "policy": "immediate declared neighbors; maximum six non-persisted arms",
    }


def validate_pairs(
    result: PairsResult,
    *,
    applied_params: dict[str, Any] | None = None,
    research_context: dict[str, Any] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> ValidationReport:
    benchmark = _benchmark_return(
        SECTOR_BENCHMARK, result.trading_window[0].date(), result.trading_window[1].date()
    )
    pair_found = result.pair is not None
    frozen_only = bool((research_context or {}).get("frozenConfigOnly"))
    if frozen_only:
        parameter_pass, parameter_details = None, {
            "reason": "Frozen multi-universe sweep forbids parameter-neighbor replays."
        }
    else:
        parameter_pass, parameter_details = _pairs_parameter_robustness(
            result, applied_params, progress, (research_context or {}).get("universeId"),
        )
    replication_rows = []
    from engine.runner import RunRequest, run_pairs
    replication_start = result.training_window[0].date()
    replication_end = result.trading_window[1].date()
    for label, universe in (("S&P 400 mid-cap sample", MIDCAP_UNIVERSE), ("S&P 600 small-cap sample", SMALL_CAP_UNIVERSE)):
        try:
            arm = run_pairs(
                result.strategy_name,
                RunRequest(symbols=list(universe), start=replication_start, end=replication_end, params=applied_params),
                persist=False,
            )
            arm_spy = _benchmark_return(
                SECTOR_BENCHMARK, arm.trading_window[0].date(), arm.trading_window[1].date(),
            )
            replication_rows.append({
                "universe": label,
                "pair": None if arm.pair is None else f"{arm.pair.symbol_a}/{arm.pair.symbol_b}",
                "returnPct": arm.return_pct,
                "benchmarkReturnPct": arm_spy,
                "contributionPct": None if arm_spy is None else arm.return_pct - arm_spy,
            })
        except (ValueError, RuntimeError) as exc:
            replication_rows.append({"universe": label, "error": str(exc), "contributionPct": None})
    replication_resolved = [row for row in replication_rows if row.get("contributionPct") is not None]
    replication_pass = bool(
        len(replication_resolved) == len(replication_rows)
        and sum(row["contributionPct"] > 0 for row in replication_resolved) >= 1
    )
    pair_membership = audit_membership(
        universe_key="dow_jones_industrial_average" if set(result.symbols) == set(EQUITY_UNIVERSE) else None,
        symbols=result.symbols,
        start=result.training_window[0].date(),
        end=result.trading_window[1].date(),
        membership_required=set(result.symbols) == set(EQUITY_UNIVERSE),
    )
    starting_equity = 10_000.0
    doubled_cost_return = result.return_pct - result.total_costs / starting_equity * 100.0
    tripled_cost_return = result.return_pct - 2.0 * result.total_costs / starting_equity * 100.0
    positive_trade_pnl = (
        float(pd.to_numeric(result.trades.get("PnL", pd.Series(dtype=float)), errors="coerce").clip(lower=0).sum())
        if not result.trades.empty else 0.0
    )
    locate_rejection_return = result.return_pct - 0.10 * positive_trade_pnl / starting_equity * 100.0
    partial_fill_return = min(result.return_pct, result.return_pct * 0.80)
    cost_threshold = benchmark if benchmark is not None else 0.0
    pair_cost_pass = bool(
        doubled_cost_return > cost_threshold
        and tripled_cost_return > 0
        and locate_rejection_return > 0
        and partial_fill_return > 0
    )
    selected_pairs = {
        row["pair"] for row in replication_rows if row.get("pair")
    }
    if result.pair is not None:
        selected_pairs.add(f"{result.pair.symbol_a}/{result.pair.symbol_b}")
    pair_dependency_pass = replication_pass and len(selected_pairs) >= 2
    dimensions = [
        ValidationDimension("performance", "Raw strategy performance", [
            _check("positive_return", "Positive absolute return", result.return_pct > 0,
                   f"Trading-window return {result.return_pct:+.1f}%", value=result.return_pct),
            _check("beats_cash", "Beats cash / risk-free", result.sharpe is not None and result.sharpe > SHARPE_THRESHOLD,
                   "Sharpe must exceed 0.50", required=True, value=result.sharpe),
            _check("sample_coverage", "Pair and trade coverage", pair_found and len(result.trades) >= MIN_RELIABLE_TRADES,
                   f"{'Pair selected' if pair_found else 'No pair selected'}; {len(result.trades)} trades",
                   required=True),
            _check("warmup_validity", "Out-of-sample split", pair_found,
                   "Pair selection uses the training half; performance uses the trading half", required=True),
            _check("measurement_integrity", "Measurement integrity", pair_found,
                   "No performance verdict is possible without a selected pair", required=True),
        ]),
        ValidationDimension("benchmarks", "Benchmark-relative checks", [
            _check("beats_spy", "Beats SPY", benchmark is not None and result.return_pct > benchmark,
                   "Trading-window return compared with SPY", required=True,
                   value=None if benchmark is None else result.return_pct - benchmark,
                   details={"benchmarkReturnPct": benchmark}),
            _not_applicable("beats_equal_weight", "Ranking contribution vs equal-weight",
                            "Pairs trading is not a cross-sectional ranking strategy"),
            _not_applicable("beats_random", "Concentration-matched random portfolios",
                            "The relevant null is randomized pair selection, not top-N concentration"),
        ]),
        ValidationDimension("robustness", "Robustness and stability", [
            _check("parameter_ridge", "Broad parameter ridge", parameter_pass,
                   "Immediate pair-selection and z-score neighbors are replayed without persisting sweep arms",
                   required=parameter_pass is not None, value=parameter_details.get("fractionBeatingBenchmark"),
                   details=parameter_details, unavailable_status="not_applicable"),
        ]),
        ValidationDimension("integrity", "Universe and research integrity", [
            _check("pit_membership", "Point-in-time universe integrity", pair_membership.passed,
                   pair_membership.summary, required=True, details=pair_membership.details),
            _check("cross_universe_replication", "Cross-universe replication", replication_pass,
                   "The frozen pair-selection rule must add value in at least one alternative universe",
                   required=True, details={"universes": replication_rows}),
            _check("pair_selection_multiplicity", "Candidate-pair selection multiplicity", False if pair_found else None,
                   "Selecting the lowest p-value from many candidate pairs requires family-wise correction",
                   value=result.pair.p_value if result.pair else None, unavailable_status="warning"),
        ]),
    ]
    generic, research = _generic_research_dimensions(
        strategy_name=result.strategy_name,
        engine="pairs",
        symbols=result.symbols,
        start=result.trading_window[0].date(),
        end=result.trading_window[1].date(),
        equity=result.equity_curve,
        benchmark_equity=None,
        trades=result.trades,
        applied_params=applied_params,
        context=research_context,
        extra_checks=[
            _check(
                "execution_cost_stress", "Spread, borrow, delay, and fill stress", pair_cost_pass,
                "Both legs fill one bar later with spread, commission, and 3% borrow; doubled/tripled costs, 10% locate rejection, and 80% fills are stressed",
                required=True,
                details={
                    "modeledTransactionCosts": result.total_costs,
                    "modeledBorrowCosts": result.total_borrow_cost,
                    "doubledCostReturnPct": doubled_cost_return,
                    "tripledCostReturnPct": tripled_cost_return,
                    "tenPctLocateRejectionReturnPct": locate_rejection_return,
                    "partialFill80ReturnPct": partial_fill_return,
                    "benchmarkReturnPct": benchmark,
                },
            ),
            _check(
                "dependency_concentration", "Pair dependency", pair_dependency_pass,
                "At least two distinct selected pairs must contribute across the primary and replication universes",
                required=True,
                details={"selectedPairs": sorted(selected_pairs), "replicationPassed": replication_pass},
            ),
            _check(
                "locate_availability", "Historical short-locate availability", None,
                "Borrow cost is modeled, but a historical locate feed is unavailable; this blocks an identified-edge claim while allowing paper observation",
                required=True,
                details={"borrowRateAnnual": 0.03, "historicalLocateFeed": False},
            ),
        ],
    )
    return _finalize(dimensions + generic, research)


def _run_cross_arm(
    strategy: DualMomentum,
    symbols: list[str],
    start: date,
    end: date,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    spread_by_symbol: dict[str, float] | None = None,
    membership_at: Callable[[date], set[str]] | None = None,
    universe_key: str | None = None,
) -> CrossSectionalResult:
    spreads = spread_by_symbol or {s: spread_for(s, start, end) for s in symbols}
    return run_cross_sectional_backtest(
        strategy.name,
        strategy,
        symbols,
        start,
        end,
        risk_free_rate=strategy.risk_free_rate,
        rebalance_frequency=strategy.rebalance_frequency,
        spread_by_symbol=spreads,
        commission_bps=0.0,
        bars_by_symbol=bars_by_symbol,
        membership_at=membership_at,
        universe_key=universe_key,
    )


def _validate_pit_all_stocks(
    result: CrossSectionalResult,
    *,
    applied_params: dict[str, Any] | None,
    research_context: dict[str, Any] | None,
) -> ValidationReport:
    """Fail-closed evidence report for a permanent-ID all-stocks run.

    This path never calls Yahoo with permanent security IDs. Evidence that is
    not yet computed from the installed bundle remains an explicit required
    unresolved gate rather than inheriting a pass from the static-roster
    validator.
    """
    start = result.equity_curve.index[0].date()
    end = result.equity_curve.index[-1].date()
    spy_bars = data_module.get_bars("SPY", "1d", start, end)
    spy_equity = (
        normalized_benchmark(
            spy_bars["Close"], result.equity_curve.index,
            float(result.equity_curve.iloc[0]),
        )
        if not spy_bars.empty else pd.Series(dtype=float)
    )
    if spy_equity.empty:
        analysis: dict[str, Any] = {
            "mda": {"mdaPct": None, "reason": "SPY total-return data unavailable"},
            "pitIntegrity": result.pit_diagnostics,
        }
    else:
        analysis = analyze_pit_result(
            result.equity_curve, spy_equity,
            total_costs=result.total_costs,
            pit_diagnostics=result.pit_diagnostics,
            actionable_alpha_pct=2.0,
        )
    params = applied_params or {}
    primary_strategy = apply_params(DualMomentum(risk_free_rate=result.risk_free_rate), params)
    ew_equity = pd.Series(dtype=float)
    random_stats: dict[str, Any] = {"simulations": 0, "reason": "runtime PIT data unavailable"}
    if result.validation_bars and result.membership_at_runtime:
        ew_equity, random_stats = dynamic_random_benchmarks(
            result.validation_bars, result.membership_at_runtime,
            result.equity_curve.index,
            rebalance_frequency=primary_strategy.rebalance_frequency,
            top_n=primary_strategy.top_n,
            initial_equity=float(result.equity_curve.iloc[0]),
        )
    ew_return = _return_pct(ew_equity)
    ew_gap = None if ew_return is None else result.return_pct - ew_return
    random_returns = random_stats.get("returnsPct") or []
    random_empirical_p = (
        (1 + sum(float(value) >= result.return_pct for value in random_returns))
        / (len(random_returns) + 1)
        if random_returns else None
    )
    random_stats["empiricalP"] = random_empirical_p
    random_stats["strategyPercentile"] = (
        sum(float(value) <= result.return_pct for value in random_returns) / len(random_returns) * 100.0
        if random_returns else None
    )
    random_stats.pop("returnsPct", None)
    analysis["equalWeightEligibleReturnPct"] = ew_return
    analysis["rankingContributionPct"] = ew_gap
    analysis["randomControl"] = random_stats

    robustness_arms: list[dict[str, Any]] = []
    if result.validation_bars and result.membership_at_runtime:
        configs = list(product(
            (126, 189, 252), (3, 5, 10, 20), ("monthly", "semimonthly", "weekly"),
        ))
        for lookback, positions, frequency in configs:
            if (
                lookback == primary_strategy.lookback_trading_days
                and positions == primary_strategy.top_n
                and frequency == primary_strategy.rebalance_frequency
            ):
                arm = result
            else:
                arm_strategy = DualMomentum(
                    risk_free_rate=result.risk_free_rate,
                    lookback_trading_days=lookback,
                    top_n=positions,
                    rebalance_frequency=frequency,
                    pit_minimum_price=primary_strategy.pit_minimum_price,
                    pit_minimum_average_dollar_volume=primary_strategy.pit_minimum_average_dollar_volume,
                    pit_liquidity_lookback_days=primary_strategy.pit_liquidity_lookback_days,
                    pit_minimum_market_cap=primary_strategy.pit_minimum_market_cap,
                    pit_max_adv_participation_pct=primary_strategy.pit_max_adv_participation_pct,
                )
                arm = run_cross_sectional_backtest(
                    result.strategy_name, arm_strategy, result.symbols,
                    result.start, result.end, risk_free_rate=result.risk_free_rate,
                    rebalance_frequency=frequency,
                    spread_by_symbol={symbol: 0.0010 for symbol in result.symbols},
                    commission_bps=result.commission_bps,
                    bars_by_symbol=result.validation_bars,
                    membership_at=result.membership_at_runtime,
                    universe_key=result.universe_key,
                    allow_incomplete_warmup=True,
                )
            arm_spy = (
                normalized_benchmark(
                    spy_bars["Close"], arm.equity_curve.index,
                    float(arm.equity_curve.iloc[0]),
                ) if not spy_bars.empty else pd.Series(dtype=float)
            )
            arm_spy_return = _return_pct(arm_spy)
            robustness_arms.append({
                "lookback": lookback, "topN": positions, "frequency": frequency,
                "primary": (
                    lookback == primary_strategy.lookback_trading_days
                    and positions == primary_strategy.top_n
                    and frequency == primary_strategy.rebalance_frequency
                ),
                "returnPct": arm.return_pct,
                "beatsSpy": arm_spy_return is not None and arm.return_pct > arm_spy_return,
                "beatsPitEqualWeight": ew_return is not None and arm.return_pct > ew_return,
            })
    robustness_fraction_ew = (
        sum(bool(row["beatsPitEqualWeight"]) for row in robustness_arms) / len(robustness_arms)
        if robustness_arms else None
    )
    robustness_fraction_spy = (
        sum(bool(row["beatsSpy"]) for row in robustness_arms) / len(robustness_arms)
        if robustness_arms else None
    )
    ridge_pass = bool(
        robustness_fraction_ew is not None and robustness_fraction_ew >= 0.60
        and robustness_fraction_spy is not None and robustness_fraction_spy >= 0.40
    )
    family_searches = max(
        36,
        int((research_context or {}).get("familySearchCount") or 1),
    )
    corrected_p = min(1.0, random_empirical_p * family_searches) if random_empirical_p is not None else None
    analysis["robustness"] = {
        "primaryPreregisteredConfig": {
            "lookback": primary_strategy.lookback_trading_days,
            "topN": primary_strategy.top_n,
            "frequency": primary_strategy.rebalance_frequency,
        },
        "arms": robustness_arms,
        "fractionBeatingPitEqualWeight": robustness_fraction_ew,
        "fractionBeatingSpy": robustness_fraction_spy,
        "interpretation": "Neighboring arms are robustness checks, not candidates for retrospective selection.",
    }
    result.pit_analysis = analysis
    mda = analysis.get("mda") or {}
    mda_pct = mda.get("mdaPct")
    power_pass = mda_pct is not None and float(mda_pct) <= 2.0
    pit = result.pit_diagnostics
    dataset = pit.get("dataset") or {}
    pit_pass = bool(
        dataset.get("ready")
        and pit.get("historicallyDelistedSecuritiesUsed", 0) > 0
        and pit.get("completePitCoveragePct", 0) == 100.0
        and result.pit_membership_applied
    )
    rolling_three = ((analysis.get("rollingExcess") or {}).get("3Year") or {})
    stability_fraction = rolling_three.get("fractionBeatingSpy")
    stability_pass = bool(
        stability_fraction is not None
        and stability_fraction >= 0.70
        and (rolling_three.get("medianExcessPct") or 0) > 0
    )
    gap = analysis.get("cumulativeGapPct")
    holdout = analysis.get("holdout") or {}
    holdout_gap = holdout.get("holdoutExcessPct")
    capacity = pit.get("capacity") or {}
    capacity_pass = capacity.get("breachCount") == 0 if capacity else None
    spy_return = analysis.get("spyReturnPct")
    stress = analysis.get("costStressReturnPct") or {}
    cost_pass = (
        stress.get("3x") is not None and spy_return is not None
        and float(stress["3x"]) > float(spy_return)
    )
    dimensions = [
        ValidationDimension("power", "Statistical power and detectability", [
            _check(
                "statistical_power", "Can this design resolve the claimed edge?", power_pass,
                "HAC MDA must not exceed the preregistered 2% annual actionable edge",
                required=True, value=mda_pct,
                details={**mda, "selectedMdaPct": mda_pct, "minimumTradableAlphaPct": 2.0},
            ),
        ]),
        ValidationDimension("performance", "Raw strategy performance", [
            _check("positive_return", "Positive absolute return", result.return_pct > 0,
                   f"Net return {result.return_pct:+.1f}%", value=result.return_pct),
            _check("sample_coverage", "Rebalance/sample coverage", len(result.rebalances) >= 120,
                   f"{len(result.rebalances)} rebalance periods", required=True,
                   value=len(result.rebalances)),
            _check("measurement_integrity", "Capacity and historical-liquidity integrity", capacity_pass,
                   "No target allocation may exceed the configured historical ADV participation limit",
                   required=True, details=capacity),
        ]),
        ValidationDimension("benchmarks", "Benchmark-relative checks", [
            _check("beats_spy", "Beats SPY on identical dates", gap is not None and gap > 0,
                   "Strategy and SPY total return use exactly aligned observations",
                   required=True, value=gap,
                   details={"strategyReturnPct": analysis.get("strategyReturnPct"), "spyReturnPct": spy_return}),
            ValidationCheck(
                "beats_equal_weight", "Beats PIT equal-weight eligible universe",
                "pass" if ew_gap is not None and ew_gap > 0 else "fail" if ew_gap is not None else "unresolved",
                "Ranking contribution = strategy minus dynamic PIT-eligible equal-weight universe",
                required=True, value=ew_gap,
                details={"strategyReturnPct": result.return_pct, "equalWeightReturnPct": ew_return},
            ),
        ]),
        ValidationDimension("integrity", "Point-in-time universe integrity", [
            _check("pit_membership", "Historical entrants, exits, and delisting returns", pit_pass,
                   "Requires complete PIT coverage and at least one historically delisted security in the run",
                   required=True, details=pit),
        ]),
        ValidationDimension("stability", "Historical stability", [
            _check("historical_stability", "Rolling 3-year excess-return stability", stability_pass,
                   "At least 70% of rolling 3-year windows must beat SPY with positive median excess",
                   required=True, value=stability_fraction, details=rolling_three),
            _check("chronological_oos", "Final 20% chronological holdout", holdout_gap is not None and holdout_gap > 0,
                   "The untouched final 20% must beat SPY", required=True,
                   value=holdout_gap, details=holdout),
        ]),
        ValidationDimension("robustness", "Robustness and multiplicity", [
            ValidationCheck(
                "parameter_ridge", "Preregistered 36-arm neighboring grid",
                "pass" if ridge_pass else "fail" if robustness_arms else "unresolved",
                "126/189/252 × 3/5/10/20 × monthly/semimonthly/weekly; primary remains frozen",
                required=True, value=robustness_fraction_ew,
                details=analysis["robustness"],
            ),
            ValidationCheck(
                "beats_random", "Concentration-matched random top-N control",
                "pass" if random_empirical_p is not None and random_empirical_p <= 0.05 else "fail" if random_empirical_p is not None else "unresolved",
                "400 dynamic PIT-eligible random portfolios form the empirical null",
                required=True, value=random_empirical_p, details=random_stats,
            ),
            ValidationCheck(
                "multiple_testing", "Selection-adjusted significance",
                "pass" if corrected_p is not None and corrected_p <= 0.05 else "fail" if corrected_p is not None else "unresolved",
                "Bonferroni correction includes the 36-arm robustness family and recorded prior family searches",
                required=True, value=corrected_p,
                details={"naiveEmpiricalP": random_empirical_p, "familySearchCount": family_searches, "correctedP": corrected_p},
            ),
        ]),
        ValidationDimension("costs", "Costs and implementability", [
            _check("cost_stress", "Survives 3× transaction costs", cost_pass,
                   "The 3×-cost strategy return must still beat same-window SPY",
                   required=True, value=stress.get("3x"), details=stress),
        ]),
    ]
    research = {**(research_context or {}), "universeId": "us_all_stocks_pit"}
    return _finalize(dimensions, research)


def _validate_frozen_cross_sectional(
    result: CrossSectionalResult,
    *,
    applied_params: dict[str, Any] | None,
    research_context: dict[str, Any] | None,
) -> ValidationReport:
    """Fail-closed V1 report without substituting Dual Momentum parameters."""
    from engine.frozen_protocol import family_record
    from strategies.registry import build_cross_sectional_strategy

    start = result.equity_curve.index[0].date()
    end = result.equity_curve.index[-1].date()
    benchmark_bars = data_module.get_bars("SPY", "1d", start - timedelta(days=430), end)
    strategy = build_cross_sectional_strategy(
        result.strategy_name,
        result.risk_free_rate,
        benchmark_bars=benchmark_bars,
    )
    strategy = apply_params(strategy, applied_params)
    top_n = int(getattr(strategy, "top_n", 5))
    spy_equity = normalized_benchmark(
        benchmark_bars["Close"], result.equity_curve.index,
        float(result.equity_curve.iloc[0]),
    )
    spy_return = _return_pct(spy_equity)

    ew_equity = pd.Series(dtype=float)
    random_stats: dict[str, Any] = {"simulations": 0, "reason": "PIT runtime bars unavailable"}
    if result.validation_bars and result.membership_at_runtime:
        ew_equity, random_stats = dynamic_random_benchmarks(
            result.validation_bars,
            result.membership_at_runtime,
            result.equity_curve.index,
            rebalance_frequency=getattr(strategy, "rebalance_frequency", "monthly"),
            top_n=top_n,
            initial_equity=float(result.equity_curve.iloc[0]),
        )
    ew_return = _return_pct(ew_equity)
    ranking_contribution = None if ew_return is None else result.return_pct - ew_return
    random_returns = random_stats.get("returnsPct") or []
    random_p = (
        (1 + sum(float(value) >= result.return_pct for value in random_returns))
        / (len(random_returns) + 1)
        if random_returns else None
    )
    random_stats = {key: value for key, value in random_stats.items() if key != "returnsPct"}
    random_stats["empiricalP"] = random_p

    stability = (
        _rolling_stability(result.equity_curve, ew_equity, start.year, end.year)
        if not ew_equity.empty else None
    )
    stability_pass = _stability_gate_passes(stability)
    membership = audit_membership(
        universe_key=result.universe_key,
        symbols=result.symbols,
        start=start,
        end=end,
        membership_required=True,
        point_in_time_applied=result.pit_membership_applied,
    )
    protocol = family_record(result.strategy_name) or {}
    declared = int(protocol.get("materialConfigurations") or 1)
    family_search_count = max(
        1, int((research_context or {}).get("familySearchCount") or 1)
    )
    corrected_p = min(1.0, random_p * family_search_count) if random_p is not None else None
    turnover_pct = _turnover_pct(result.rebalances)
    active_rebalances = (
        int(result.rebalances["holdings"].map(bool).sum())
        if not result.rebalances.empty and "holdings" in result.rebalances else 0
    )
    active_fraction = active_rebalances / len(result.rebalances) if len(result.rebalances) else 0.0
    dimensions = [
        ValidationDimension("performance", "Raw strategy performance", [
            _check("positive_return", "Positive absolute return", result.return_pct > 0,
                   f"Net return {result.return_pct:+.1f}%", value=result.return_pct),
            _check("sample_coverage", "Rebalance/sample coverage",
                   len(result.rebalances) >= 36 and active_fraction >= 0.80,
                   f"{active_rebalances}/{len(result.rebalances)} rebalances held positions",
                   required=True, value=active_rebalances,
                   details={"totalRebalances": len(result.rebalances),
                            "activeRebalances": active_rebalances,
                            "activeFraction": active_fraction}),
            _check("measurement_integrity", "Costs and turnover modeled",
                   result.total_costs >= 0 and result.total_traded_notional > 0,
                   "Per-symbol spread is charged on traded deltas", required=True,
                   details={"turnoverPct": turnover_pct, "modeledCosts": result.total_costs,
                            "grossTradedNotional": result.total_traded_notional}),
        ]),
        ValidationDimension("benchmarks", "Benchmark-relative checks", [
            _check("beats_spy", "Beats SPY on identical dates",
                   spy_return is not None and result.return_pct > spy_return,
                   "Continuously invested ranker compared over the identical window",
                   required=True, value=None if spy_return is None else result.return_pct - spy_return,
                   details={"strategyReturnPct": result.return_pct, "spyReturnPct": spy_return}),
            _check("beats_equal_weight", "Beats PIT equal-weight eligible universe",
                   ranking_contribution is not None and ranking_contribution > 0,
                   "Ranking contribution = strategy minus dynamic PIT equal-weight",
                   required=True, value=ranking_contribution,
                   details={"strategyReturnPct": result.return_pct, "equalWeightReturnPct": ew_return}),
        ]),
        ValidationDimension("integrity", "Point-in-time universe integrity", [
            _check("pit_membership", "Date-effective Dow membership", membership.passed,
                   membership.summary, required=True, details=membership.details),
            _not_applicable("cross_universe_replication", "Cross-universe replication",
                            "S&P PIT price coverage is incomplete; static present-day rosters are prohibited"),
        ]),
        ValidationDimension("stability", "Historical stability", [
            _check("historical_stability", "Rolling 3-year ranking contribution", stability_pass,
                   "At least 70% positive with positive median and mean excluding the best window",
                   required=True, details=stability or {}),
        ]),
        ValidationDimension("robustness", "Robustness and multiplicity", [
            _check("parameter_ridge", "Pre-registered neighbor grid", None,
                   "V1 is frozen; neighbors must run only after this canonical result is stored",
                   required=True, details={"declaredConfigurations": declared, "protocol": protocol}),
            _check("beats_random", "Concentration-matched random top-N control",
                   random_p is not None and random_p <= 0.05,
                   "400 dynamic PIT portfolios use identical dates and concentration",
                   required=True, value=random_p, details=random_stats),
            _check("multiple_testing", "Selection-adjusted significance",
                   corrected_p is not None and corrected_p <= 0.05,
                   "Correction uses experiments actually executed; the full registered grid is disclosed separately",
                   required=True, value=corrected_p,
                   details={"familySearchCount": family_search_count,
                            "registeredConfigurations": declared, "correctedP": corrected_p}),
        ]),
    ]
    generic, research = _generic_research_dimensions(
        strategy_name=result.strategy_name,
        engine="cross_sectional",
        symbols=result.symbols,
        start=start,
        end=end,
        equity=result.equity_curve,
        benchmark_equity=spy_equity,
        trades=pd.DataFrame(),
        applied_params=applied_params,
        context=research_context,
        extra_checks=[],
    )
    return _finalize(dimensions + generic, {**research, "frozenProtocol": protocol})


def validate_cross_sectional(
    result: CrossSectionalResult,
    applied_params: dict[str, Any] | None = None,
    *,
    configs_searched: int | None = None,
    progress: Callable[[int, str], None] | None = None,
    research_context: dict[str, Any] | None = None,
) -> ValidationReport:
    """Run the full falsification battery for Dual Momentum-like rankers."""
    if result.strategy_name in {"52-Week-High Momentum", "Market-Residual Momentum"}:
        return _validate_frozen_cross_sectional(
            result,
            applied_params=applied_params,
            research_context=research_context,
        )
    if result.pit_diagnostics:
        return _validate_pit_all_stocks(
            result, applied_params=applied_params, research_context=research_context,
        )
    report_progress = progress or (lambda _percent, _stage: None)
    frozen_only = bool((research_context or {}).get("frozenConfigOnly"))
    report_progress(32, "Loading benchmark and concentration controls")
    params = applied_params or {}
    strategy = apply_params(DualMomentum(risk_free_rate=result.risk_free_rate), params)
    top_n = strategy.top_n
    analysis_start = result.equity_curve.index[0].date()
    analysis_end = result.equity_curve.index[-1].date()
    primary_schedule = (
        resolve_schedule(result.universe_key, analysis_start, analysis_end)
        if result.universe_key and result.pit_membership_applied else None
    )
    # One data load for the whole local surface.  The old implementation
    # fetched 29 symbols again for every one of ~27 arms, turning a click into
    # a multi-minute wait even when the data was cached.
    max_surface_warmup = max(lookback for lookback in (
        max(63, strategy.lookback_trading_days - 21),
        strategy.lookback_trading_days,
        min(378, strategy.lookback_trading_days + 21),
    )) + 1
    surface_bars = _preload_bars(result.symbols, analysis_start, analysis_end, max_surface_warmup)
    selected_universe_id = (research_context or {}).get("universeId")
    surface_spreads = {
        symbol: spread_for_universe(symbol, analysis_start, analysis_end, selected_universe_id)
        for symbol in result.symbols
    }
    close, missing = _close_from_bars(surface_bars, result.symbols, analysis_start)
    ew_curve = _equal_weight_curve(close)
    ew_return = _return_pct(ew_curve)
    spy_return = _benchmark_return(SECTOR_BENCHMARK, analysis_start, analysis_end)
    contribution = None if ew_return is None else result.return_pct - ew_return
    random_stats = _random_portfolios(
        close,
        top_n,
        result.return_pct,
        seed_material=f"{result.strategy_name}|{analysis_start}|{analysis_end}|{sorted(result.symbols)}|{top_n}",
    )
    report_progress(43, "Replaying neighboring parameter configurations")

    # Immediate neighboring surface, not a new optimizer: center +/- one
    # declared step for numeric parameters and adjacent cadence choices.
    lookbacks = sorted({
        max(63, strategy.lookback_trading_days - 21),
        strategy.lookback_trading_days,
        min(378, strategy.lookback_trading_days + 21),
    })
    top_ns = sorted({max(1, top_n - 1), top_n, min(15, top_n + 1)})
    cadence_order = ["quarterly", "monthly", "semimonthly", "weekly", "daily"]
    cadence_index = cadence_order.index(strategy.rebalance_frequency)
    frequencies = cadence_order[max(0, cadence_index - 1): cadence_index + 2]
    # A local 3x3 lookback/top-N plane at the selected cadence, plus adjacent
    # cadences at the selected lookback/top-N. This detects an isolated needle
    # in every requested direction with 11 arms instead of a 27-arm Cartesian
    # product whose higher-order interactions tripled click latency.
    arm_configs = (
        {(strategy.lookback_trading_days, top_n, strategy.rebalance_frequency)}
        if frozen_only else set(product(lookbacks, top_ns, [strategy.rebalance_frequency]))
    )
    if not frozen_only:
        arm_configs.update(
            (strategy.lookback_trading_days, top_n, frequency)
            for frequency in frequencies
        )
    surface: list[dict[str, Any]] = []
    sorted_arm_configs = sorted(arm_configs)
    for arm_index, (lookback, arm_top_n, frequency) in enumerate(sorted_arm_configs):
        report_progress(
            43 + int(17 * arm_index / max(1, len(sorted_arm_configs))),
            f"Parameter robustness: arm {arm_index + 1} of {len(sorted_arm_configs)}",
        )
        if (
            lookback == strategy.lookback_trading_days
            and arm_top_n == top_n
            and frequency == strategy.rebalance_frequency
        ):
            arm = result
        else:
            arm_strategy = DualMomentum(
                risk_free_rate=result.risk_free_rate,
                lookback_trading_days=lookback,
                top_n=arm_top_n,
                rebalance_frequency=frequency,
            )
            try:
                arm = _run_cross_arm(
                    arm_strategy, result.symbols, analysis_start, analysis_end,
                    bars_by_symbol=surface_bars,
                    spread_by_symbol=surface_spreads,
                    membership_at=primary_schedule.membership_at if primary_schedule else None,
                    universe_key=result.universe_key,
                )
            except (ValueError, RuntimeError) as exc:
                surface.append({
                    "lookback": lookback,
                    "topN": arm_top_n,
                    "frequency": frequency,
                    "returnPct": None,
                    "contributionPct": None,
                    "beatsSpy": False,
                    "error": str(exc),
                })
                continue
        arm_contribution = None if ew_return is None else arm.return_pct - ew_return
        surface.append({
            "lookback": lookback,
            "topN": arm_top_n,
            "frequency": frequency,
            "returnPct": arm.return_pct,
            "contributionPct": arm_contribution,
            "beatsSpy": spy_return is not None and arm.return_pct > spy_return,
        })
    valid_surface = [row for row in surface if row["contributionPct"] is not None]
    contribution_fraction = (
        sum(row["contributionPct"] > 0 for row in valid_surface) / len(valid_surface)
        if valid_surface else None
    )
    spy_fraction = (
        sum(row["beatsSpy"] for row in valid_surface) / len(valid_surface)
        if valid_surface and spy_return is not None else None
    )
    ridge_pass = None if frozen_only else (
        contribution_fraction is not None
        and contribution_fraction >= 0.60
        and (spy_fraction is None or spy_fraction >= 0.40)
    )
    report_progress(62, "Measuring rolling three-year historical stability")

    # One continuous long run, then fixed overlapping windows. A missing name
    # is excluded transparently and is also carried into the PIT integrity gate.
    history_start = date(2010, 1, 1)
    warmup_start = history_start - timedelta(days=int(strategy.required_history_days() * 1.45) + 7)
    history_schedule = (
        resolve_schedule(result.universe_key, history_start, analysis_end)
        if result.universe_key else None
    )
    history_candidates = history_schedule.symbols if history_schedule else result.symbols
    history_symbols: list[str] = []
    history_exclusions: list[str] = []
    history_bars = {
        symbol: data_module.get_bars(symbol, "1d", warmup_start, analysis_end)
        for symbol in history_candidates
    }
    for symbol in history_candidates:
        bars = history_bars[symbol]
        if int((bars.index < pd.Timestamp(history_start, tz=bars.index.tz)).sum()) >= strategy.required_history_days() if not bars.empty else False:
            history_symbols.append(symbol)
        else:
            history_exclusions.append(symbol)
    manual_evidence = load_manual_membership_evidence(result.strategy_name) or {}
    stability = None
    if result.universe_key and history_schedule is None:
        history_exclusions.append("PIT membership ledger does not cover the full stability window")
    if len(history_symbols) >= max(top_n, 2) and (not result.universe_key or history_schedule is not None):
        long_strategy = DualMomentum(
            risk_free_rate=data_module.risk_free_rate(history_start, analysis_end),
            lookback_trading_days=strategy.lookback_trading_days,
            top_n=strategy.top_n,
            rebalance_frequency=strategy.rebalance_frequency,
        )
        long_result = _run_cross_arm(
            long_strategy, history_symbols, history_start, analysis_end,
            bars_by_symbol=history_bars,
            spread_by_symbol={
                symbol: spread_for_universe(symbol, history_start, analysis_end, selected_universe_id)
                for symbol in history_symbols
            },
            membership_at=history_schedule.membership_at if history_schedule else None,
            universe_key=result.universe_key,
        )
        long_close, _ = _close_from_bars(history_bars, history_symbols, history_start)
        long_ew = _equal_weight_curve(long_close)
        if long_ew is not None:
            stability = _rolling_stability(
                long_result.equity_curve, long_ew, history_start.year, analysis_end.year
            )
    # The original hostile-validation run predated the structured report but
    # its exact frozen-rule rolling-window results were documented.  Preserve
    # that completed work as provenance instead of saying it never ran.  It is
    # explicitly labelled fixed-roster evidence and does not satisfy PIT.
    manual_stability = manual_evidence.get("historicalStability")
    if stability is None and isinstance(manual_stability, dict):
        stability = {
            **manual_stability,
            "evidenceSource": manual_evidence.get("sourceDocument"),
            "evidenceRecordedAt": manual_evidence.get("recordedAt"),
        }
    stability_pass = _stability_gate_passes(stability)

    replications: list[dict[str, Any]] = []
    replication_universes = (
        ("S&P 400 mid-cap sample", "sp_400_midcap", MIDCAP_UNIVERSE),
        ("S&P 600 small-cap sample", "sp_600_smallcap", SMALL_CAP_UNIVERSE),
    )
    for replication_index, (label, universe_key, static_universe) in enumerate(
        () if frozen_only else replication_universes
    ):
        report_progress(
            76 + replication_index * 9,
            f"Cross-universe replication: {label}",
        )
        try:
            replication_schedule = resolve_schedule(universe_key, analysis_start, analysis_end)
            universe = replication_schedule.symbols if replication_schedule else static_universe
            arm_bars = _preload_bars(universe, analysis_start, analysis_end, strategy.required_history_days())
            arm_spreads = {
                symbol: spread_for(symbol, analysis_start, analysis_end)
                for symbol in universe
            }
            arm = _run_cross_arm(
                strategy, universe, analysis_start, analysis_end,
                bars_by_symbol=arm_bars,
                spread_by_symbol=arm_spreads,
                membership_at=replication_schedule.membership_at if replication_schedule else None,
                universe_key=universe_key,
            )
            arm_close, arm_missing = _close_from_bars(arm_bars, universe, analysis_start)
            arm_ew = _return_pct(_equal_weight_curve(arm_close))
            replications.append({
                "universe": label,
                "returnPct": arm.return_pct,
                "equalWeightReturnPct": arm_ew,
                "contributionPct": None if arm_ew is None else arm.return_pct - arm_ew,
                "missingSymbols": arm_missing,
                "pitMembershipApplied": replication_schedule is not None,
            })
        except (ValueError, RuntimeError) as exc:
            replications.append({"universe": label, "error": str(exc), "contributionPct": None})
    resolved_replications = [r for r in replications if r.get("contributionPct") is not None]
    replication_pass = None if frozen_only else (
        len(resolved_replications) == len(replications)
        and all(r.get("pitMembershipApplied") for r in resolved_replications)
        and sum(r["contributionPct"] > 0 for r in resolved_replications) >= math.ceil(len(replications) / 2)
    )

    is_known_dow_snapshot = result.universe_key == "dow_jones_industrial_average" or set(result.symbols) == set(EQUITY_UNIVERSE)
    membership = audit_membership(
        universe_key="dow_jones_industrial_average" if is_known_dow_snapshot else None,
        symbols=result.symbols,
        start=history_start if is_known_dow_snapshot else analysis_start,
        end=analysis_end,
        membership_required=is_known_dow_snapshot,
        point_in_time_applied=bool(result.pit_membership_applied and history_schedule is not None),
    )
    manual_pit = manual_evidence.get("pointInTimeMembership")
    if membership.passed is None and isinstance(manual_pit, dict) and manual_pit.get("testRan"):
        pit_status: CheckStatus = "warning"
        pit_summary = (
            f"Prior PIT reconstruction survived ({float(manual_pit.get('annualContributionPct', 0.0)):+.2f}%/yr), "
            "but favorable/unfetchable exclusions leave survivorship risk unresolved"
        )
        pit_details = {
            **membership.details,
            **manual_pit,
            "evidenceSource": manual_evidence.get("sourceDocument"),
            "evidenceRecordedAt": manual_evidence.get("recordedAt"),
            "historicalExclusions": history_exclusions,
        }
    else:
        pit_status = "pass" if membership.passed is True else "fail" if membership.passed is False else "unresolved"
        pit_summary = membership.summary
        pit_details = {**membership.details, "historicalExclusions": history_exclusions}
    effective_low, effective_high = (5, 10) if result.strategy_name == "Dual Momentum" else (1, 1)
    # This is the CONCENTRATION-matched randomization p-value (from
    # `_random_portfolios`, same source as the `beats_random` check above),
    # Sidak-corrected for an assumed 5-10 effectively independent parameter
    # configurations searched. It is NOT the same null as `multiple_testing`'s
    # naiveBootstrapP (a block-bootstrap of realized returns, corrected
    # Bonferroni-style for the full declared search family) -- the two
    # checks answer different questions and must not be reconciled into one.
    naive_concentration_p = random_stats["empiricalP"] if random_stats else None
    corrected_low = 1 - (1 - naive_concentration_p) ** effective_low if naive_concentration_p is not None else None
    corrected_high = 1 - (1 - naive_concentration_p) ** effective_high if naive_concentration_p is not None else None
    declared_searches = configs_searched or (54 if result.strategy_name == "Dual Momentum" else len(surface))
    multiple_pass = corrected_high is not None and corrected_high <= 0.05
    turnover_pct = _turnover_pct(result.rebalances)
    starting_equity = float(result.equity_curve.iloc[0]) if len(result.equity_curve) else 10_000.0
    gross_traded_notional_pct = (
        float(result.total_traded_notional / starting_equity * 100.0)
        if starting_equity > 0 else None
    )
    effective_cost_bps = (
        float(result.total_costs / result.total_traded_notional * 10_000.0)
        if result.total_traded_notional > 0 else (0.0 if result.total_costs == 0 else None)
    )
    configured_cost_bps = [
        spread * 10_000.0 + result.commission_bps
        for spread in surface_spreads.values()
    ]
    configured_cost_low = min(configured_cost_bps, default=result.commission_bps)
    configured_cost_high = max(configured_cost_bps, default=result.commission_bps)
    cost_reconciles = bool(
        effective_cost_bps is not None
        and configured_cost_low - 0.01 <= effective_cost_bps <= configured_cost_high + 0.01
    )
    cost_summary = (
        f"{turnover_pct:.0f}% target-weight one-way turnover; "
        f"{gross_traded_notional_pct:.0f}% gross traded notional; "
        f"${result.total_costs:,.2f} costs = {effective_cost_bps:.2f} bps "
        f"(configured {configured_cost_low:.2f}-{configured_cost_high:.2f} bps)"
        if gross_traded_notional_pct is not None and effective_cost_bps is not None
        else "Costs could not be reconciled to recorded traded notional"
    )
    warmup_missing = sorted(set(missing) | set(result.incomplete_warmup))
    warmup_summary = (
        "Every requested symbol supplied the required lookback"
        if not warmup_missing
        else (
            f"{len(warmup_missing)} constituent(s) lacked full starting-date warmup: "
            + ", ".join(warmup_missing[:12])
            + (" …" if len(warmup_missing) > 12 else "")
        )
    )

    dimensions = [
        ValidationDimension("performance", "Raw strategy performance", [
            _check("positive_return", "Positive absolute return", result.return_pct > 0,
                   f"Net return {result.return_pct:+.1f}%", value=result.return_pct),
            _check("beats_cash", "Beats cash / risk-free", result.sharpe is not None and result.sharpe > SHARPE_THRESHOLD,
                   "Sharpe must exceed 0.50 against the measured risk-free rate", required=True, value=result.sharpe),
            _check("sample_coverage", "Trade/sample coverage", len(result.rebalances) >= 24,
                   f"{len(result.rebalances)} rebalances across the measured window", required=True,
                   value=len(result.rebalances)),
            _check("warmup_validity", "Warmup validity", not warmup_missing,
                   warmup_summary, required=True,
                   details={
                       "missingSymbols": missing,
                       "incompleteWarmupBars": result.incomplete_warmup,
                   }),
            _check("measurement_integrity", "Turnover and cost reconciliation", cost_reconciles,
                   cost_summary,
                   required=True, value=result.total_costs,
                   details={
                       "targetWeightOneWayTurnoverPct": turnover_pct,
                       "grossTradedNotionalPct": gross_traded_notional_pct,
                       "totalTradedNotional": float(result.total_traded_notional),
                       "totalCosts": float(result.total_costs),
                       "effectiveCostBps": effective_cost_bps,
                       "configuredCostMinBps": configured_cost_low,
                       "configuredCostMaxBps": configured_cost_high,
                       "commissionBps": result.commission_bps,
                       "reconciliationPass": cost_reconciles,
                       "turnoverDefinition": (
                           "Target-weight turnover is half-L1 between target weights; gross traded "
                           "notional is the absolute dollar delta charged slippage/commission."
                       ),
                   }),
        ]),
        ValidationDimension("benchmarks", "Benchmark-relative checks", [
            _check("beats_spy", "Beats SPY", spy_return is not None and result.return_pct > spy_return,
                   "Net strategy return compared over identical dates", required=True,
                   value=None if spy_return is None else result.return_pct - spy_return,
                   details={"strategyReturnPct": result.return_pct, "spyReturnPct": spy_return}),
            _check("beats_equal_weight", "Beats equal-weight universe", contribution is not None and contribution > 0,
                   "Ranking contribution = strategy minus equal-weight universe", required=True,
                   value=contribution,
                   details={"strategyReturnPct": result.return_pct, "equalWeightReturnPct": ew_return}),
        ]),
        ValidationDimension("concentration", "Concentration control", [
            _check("beats_random", "Beats concentration-matched random portfolios",
                   random_stats is not None and random_stats["empiricalP"] <= 0.05,
                   "400 deterministic random top-N buy-and-hold portfolios form the empirical null "
                   "(see details.nullDefinition). Distinct from `multiple_testing`'s block-bootstrap "
                   "p-value elsewhere in this report -- do not conflate the two.",
                   required=True, value=None if random_stats is None else random_stats["empiricalP"],
                   details=random_stats or {}),
        ]),
        ValidationDimension("robustness", "Parameter robustness", [
            _check("parameter_ridge", "Broad parameter ridge", ridge_pass,
                   "Immediate lookback, top-N, and rebalance neighbors must win broadly",
                   required=not frozen_only, value=contribution_fraction,
                   details={"arms": surface, "fractionBeatingEqualWeight": contribution_fraction,
                            "fractionBeatingSpy": spy_fraction,
                            "reason": "Frozen multi-universe sweep forbids parameter-neighbor replays." if frozen_only else None},
                   unavailable_status="not_applicable" if frozen_only else "unresolved"),
        ]),
        ValidationDimension("stability", "Historical stability", [
            _check("historical_stability", "Rolling 3-year stability", stability_pass,
                   "At least 70% positive, positive median, and positive mean excluding the best window",
                   required=True, value=None if stability is None else stability["fractionPositive"],
                   details={**(stability or {}), "excludedSymbols": history_exclusions}),
        ]),
        ValidationDimension("integrity", "Point-in-time universe integrity", [
            ValidationCheck(
                "pit_membership", "PIT membership robustness",
                pit_status, pit_summary, required=True, details=pit_details,
            ),
        ]),
        ValidationDimension("replication", "Cross-universe replication", [
            _check("cross_universe_replication", "Frozen-rule replication", replication_pass,
                   "The unchanged rule must add value in at least half of pre-registered alternative universes",
                   details={"universes": replications,
                            "reason": "The registered sweep matrix itself is the replication test." if frozen_only else None},
                   unavailable_status="not_applicable" if frozen_only else "unresolved"),
        ]),
        ValidationDimension("multiple_testing", "Multiple-testing correction", [
            ValidationCheck(
                "selection_adjusted_significance", "Selection-adjusted significance",
                "pass" if multiple_pass else ("warning" if corrected_high is not None else "unresolved"),
                "Sidak-corrects the CONCENTRATION-matched randomization p-value "
                "(same source as the `beats_random` check, naiveEmpiricalP) for "
                "a disclosed 5-10 effectively independent configuration range. "
                "This is a different null and a different correction from the "
                "`multiple_testing` check's block-bootstrap naiveBootstrapP -- "
                "the two are not interchangeable and should not be averaged or "
                "treated as a single reconciled p-value.",
                value=corrected_high,
                details={"configsSearched": declared_searches, "effectiveConfigsLow": effective_low,
                         "effectiveConfigsHigh": effective_high, "naiveEmpiricalP": naive_concentration_p,
                         "correctedPLow": corrected_low, "correctedPHigh": corrected_high,
                         "nullDefinition": "concentration-matched random top-N portfolio selection (see beats_random)"},
            ),
        ]),
    ]
    stressed_returns = {
        f"{multiple}x": result.return_pct - ((multiple - 1) * result.total_costs / starting_equity * 100.0)
        for multiple in (1, 2, 3)
    }
    cost_stress_pass = bool(
        ew_return is not None
        and stressed_returns["2x"] > ew_return
        and stressed_returns["3x"] > 0
    )
    selection_counts: dict[str, int] = {}
    for _, row in result.rebalances.iterrows():
        for symbol, weight in row["holdings"].items():
            if float(weight) > 0:
                selection_counts[symbol] = selection_counts.get(symbol, 0) + 1
    total_selections = sum(selection_counts.values())
    max_selection_share = (
        max(selection_counts.values(), default=0) / total_selections
        if total_selections else None
    )
    yearly_returns = []
    if len(result.equity_curve) >= 2:
        for year, values in result.equity_curve.groupby(result.equity_curve.index.year):
            value = _return_pct(values)
            if value is not None:
                yearly_returns.append({"year": int(year), "returnPct": value})
    positive_year_fraction = (
        sum(row["returnPct"] > 0 for row in yearly_returns) / len(yearly_returns)
        if yearly_returns else None
    )
    dependency_pass = bool(
        max_selection_share is not None and max_selection_share <= 0.35
        and positive_year_fraction is not None and positive_year_fraction >= 0.50
    )
    generic, research = _generic_research_dimensions(
        strategy_name=result.strategy_name,
        engine="cross_sectional",
        symbols=result.symbols,
        start=analysis_start,
        end=analysis_end,
        equity=result.equity_curve,
        benchmark_equity=ew_curve,
        trades=None,
        applied_params=applied_params,
        context=research_context,
        extra_checks=[
            _check(
                "execution_cost_stress", "Execution-cost and capacity stress", cost_stress_pass,
                "Ranking contribution must survive doubled costs and remain positive at tripled costs",
                required=True,
                details={
                    "modeledBaseCosts": result.total_costs,
                    "startingEquity": starting_equity,
                    "equalWeightReturnPct": ew_return,
                    "stressedReturnPct": stressed_returns,
                    "limitations": "market impact is not identifiable without historical quote depth",
                },
            ),
            _check(
                "dependency_concentration", "Selection and regime dependency", dependency_pass,
                "No symbol may dominate more than 35% of selections and at least half of calendar years must be positive",
                required=True,
                details={
                    "selectionCounts": selection_counts,
                    "largestSelectionShare": max_selection_share,
                    "yearlyReturns": yearly_returns,
                    "positiveYearFraction": positive_year_fraction,
                },
            ),
        ],
    )
    report_progress(94, "Applying hard evidence and lifecycle gates")
    return _finalize(dimensions + generic, research)
