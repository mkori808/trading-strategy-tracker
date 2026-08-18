"""Universal research governance for every backtest engine.

Strategy-specific validation asks whether a particular rule survived the
right domain tests.  This module supplies the evidence that must not vary by
engine: a complete research contract, chronological holdout/walk-forward
measurement, leakage invariants, block-bootstrap uncertainty, execution-cost
stress, dependency diagnostics, experiment accounting, and a reproducible run
manifest.

None of these checks is a composite score.  Each returns its own status and
evidence so one failure can never be averaged away by unrelated strengths.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import platform
from dataclasses import asdict, dataclass
from datetime import date
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

from engine import data as data_module
from engine.execution_calibration import snapshot as execution_calibration_snapshot
from engine.sanity import calendar_daily_series, check_return
from engine.universe import (
    EQUITY_UNIVERSE,
    MIDCAP_UNIVERSE,
    SECTOR_BENCHMARK,
    SECTOR_UNIVERSE,
    SMALL_CAP_UNIVERSE,
)
from strategies.params import ParamSpec, describe_params


RESEARCH_SUITE_VERSION = 1
VALIDATION_REPORT_VERSION = 5
EngineKind = Literal["standard", "cross_sectional", "pairs"]

# Smallest annual benchmark-relative advantage that would justify continuing
# the research.  This is part of the pre-run contract, not a number inferred
# after seeing a backtest.  A design whose MDA exceeds this threshold cannot
# resolve the claim the application is asking it to evaluate.
#
# Do not raise this to make an MDA gate pass. See FROZEN_DUAL_MOMENTUM.md's
# 2026-08-13 amendment for the full reasoning: MDA is a property of the
# design, this threshold is a property of the decision, and moving the
# second to fit the first asserts a return claim (here, ~11%/yr sustained
# over equal-weight Dow) the evidence does not support and that has no
# documented precedent in liquid US equities.
MINIMUM_TRADABLE_ALPHA_PCT = 2.0


@dataclass(frozen=True)
class StrategyValidationSpec:
    strategy_name: str
    engine: EngineKind
    hypothesis: str
    primary_benchmark: str
    primary_criterion: str
    minimum_tradable_alpha_pct: float
    universe_policy: str
    pit_membership_required: bool
    warmup_bars: int
    execution_timing: str
    cost_model: str
    holdout_fraction: float
    walk_forward_folds: int
    parameter_neighborhoods: dict[str, list[Any]]
    alternative_universes: list[str]
    search_family: str
    random_seed: int
    external_data_release_policy: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "strategyName": payload.pop("strategy_name"),
            "engine": payload.pop("engine"),
            "hypothesis": payload.pop("hypothesis"),
            "primaryBenchmark": payload.pop("primary_benchmark"),
            "primaryCriterion": payload.pop("primary_criterion"),
            "minimumTradableAlphaPct": payload.pop("minimum_tradable_alpha_pct"),
            "universePolicy": payload.pop("universe_policy"),
            "pitMembershipRequired": payload.pop("pit_membership_required"),
            "warmupBars": payload.pop("warmup_bars"),
            "executionTiming": payload.pop("execution_timing"),
            "costModel": payload.pop("cost_model"),
            "holdoutFraction": payload.pop("holdout_fraction"),
            "walkForwardFolds": payload.pop("walk_forward_folds"),
            "parameterNeighborhoods": payload.pop("parameter_neighborhoods"),
            "alternativeUniverses": payload.pop("alternative_universes"),
            "searchFamily": payload.pop("search_family"),
            "randomSeed": payload.pop("random_seed"),
            "externalDataReleasePolicy": payload.pop("external_data_release_policy"),
        }


def _neighbor_values(spec: ParamSpec) -> list[Any]:
    default = spec.default
    if spec.kind == "bool":
        return [not bool(default)]
    if spec.kind == "str":
        if not spec.choices or default not in spec.choices:
            return []
        index = spec.choices.index(default)
        values = []
        if index > 0:
            values.append(spec.choices[index - 1])
        if index + 1 < len(spec.choices):
            values.append(spec.choices[index + 1])
        return values
    if not isinstance(default, (int, float)):
        return []
    step = spec.step or max(abs(float(default)) * 0.10, 1.0 if spec.kind == "int" else 0.1)
    values: list[Any] = []
    for candidate in (float(default) - step, float(default) + step):
        if spec.minimum is not None and candidate < spec.minimum:
            continue
        if spec.maximum is not None and candidate > spec.maximum:
            continue
        values.append(int(round(candidate)) if spec.kind == "int" else float(candidate))
    return list(dict.fromkeys(values))


def parameter_neighborhoods(strategy_class: type) -> dict[str, list[Any]]:
    return {
        spec.name: values
        for spec in describe_params(strategy_class)
        if (values := _neighbor_values(spec))
    }


def _inferred_warmup(specs: list[ParamSpec], engine: EngineKind) -> int:
    numeric = [
        int(spec.default)
        for spec in specs
        if spec.kind == "int"
        and isinstance(spec.default, int)
        and any(token in spec.name.lower() for token in ("lookback", "period", "sma", "window"))
    ]
    floor = 252 if engine == "cross_sectional" else 30
    return max([floor, *numeric])


def build_validation_spec(
    strategy_name: str,
    engine: EngineKind,
    symbols: list[str],
    strategy_class: type,
    universe_id: str | None = None,
) -> StrategyValidationSpec:
    specs = describe_params(strategy_class)
    is_sector = set(symbols) == set(SECTOR_UNIVERSE)
    is_dow_snapshot = set(symbols) == set(EQUITY_UNIVERSE)
    registered = None
    if universe_id:
        from engine.universe_registry import registered_universe
        registered = registered_universe(universe_id)
    if registered:
        universe_policy = f"registered {registered.membership_mode}: {registered.label}"
    elif is_sector:
        universe_policy = "externally defined sector ETFs"
    elif is_dow_snapshot:
        universe_policy = "frozen July-2021 Dow snapshot"
    else:
        universe_policy = "explicit fixed symbol list supplied before execution"
    pit_required = bool(
        registered and "pit" in registered.universe_id
    ) if registered else engine in {"cross_sectional", "pairs"} and is_dow_snapshot
    benchmark = (
        registered.primary_benchmark
        if registered and registered.primary_benchmark
        else "Equal-weight selected universe" if engine == "cross_sectional" else SECTOR_BENCHMARK
    )
    execution_timing = {
        "standard": "signal on completed bar; market order on next available bar",
        "cross_sectional": "rank on completed bars through the prior session; execute at the next session open",
        "pairs": "select on training half; signal on completed trading-half bars and execute at the next session open",
    }[engine]
    alternatives = (
        ["S&P 400 mid-cap sample", "S&P 600 small-cap sample"]
        if engine == "cross_sectional"
        else ["deterministic disjoint symbol halves"] if len(symbols) >= 4
        else []
    )
    # Frozen research families use a stable preregistered identifier so a UI
    # rename cannot reset the multiple-testing count. Ordinary strategies keep
    # the historical name:engine convention.
    from engine.frozen_protocol import family_record
    frozen_family = family_record(strategy_name)
    search_family = (
        str(frozen_family["searchFamily"])
        if frozen_family is not None else f"{strategy_name}:{engine}"
    )
    return StrategyValidationSpec(
        strategy_name=strategy_name,
        engine=engine,
        hypothesis=(
            f"The frozen {strategy_name} rule adds net value relative to {benchmark} "
            "outside the observations used to define it."
        ),
        primary_benchmark=benchmark,
        primary_criterion="positive net benchmark-relative return on the chronological holdout",
        minimum_tradable_alpha_pct=MINIMUM_TRADABLE_ALPHA_PCT,
        universe_policy=universe_policy,
        pit_membership_required=pit_required,
        warmup_bars=_inferred_warmup(specs, engine),
        execution_timing=execution_timing,
        cost_model=(
            json.dumps(registered.cost_model, sort_keys=True)
            if registered else "per-symbol spread/slippage plus broker commission; stressed at 2x and 3x"
        ),
        holdout_fraction=0.20,
        walk_forward_folds=5,
        parameter_neighborhoods=parameter_neighborhoods(strategy_class),
        alternative_universes=alternatives,
        search_family=search_family,
        random_seed=20260812,
        external_data_release_policy=(
            "event/fundamental inputs must be timestamped by public availability; price inputs are completed bars only"
        ),
    )


def pre_result_power_design(
    strategy_name: str,
    engine: EngineKind,
    symbols: list[str],
    strategy_class: type,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Freeze the design-level MDA before any result is observed.

    Cross-sectional strategies have a declared position count and cadence, so
    the calibrated breadth screen can use both. Signal-timed standard/pairs
    engines do not know their realized event frequency before execution;
    their pre-result screen therefore uses one portfolio return stream and a
    disclosed 20% annual-volatility planning assumption. Adding more correlated
    names does not manufacture more time observations under either method.
    """
    years = max(0.0, (end - start).days / 365.25)
    specs = {item.name: item.default for item in describe_params(strategy_class)}
    if engine == "cross_sectional":
        from engine.power_curve import screen_design

        cadence = str(specs.get("rebalance_frequency", "monthly"))
        rebalances = {
            "daily": 252, "weekly": 52, "semimonthly": 24,
            "monthly": 12, "quarterly": 4,
        }.get(cadence, 12)
        screen = screen_design(
            f"{strategy_name} × {len(symbols)} symbols",
            positions=min(len(symbols), int(specs.get("top_n", 1))),
            rebalances_per_year=rebalances,
            years=years,
            tradable_alpha_pct=MINIMUM_TRADABLE_ALPHA_PCT,
        )
        result = {
            "method": "calibrated correlated-breadth design screen",
            "mdaPct": float(screen["mda_pct"]),
            "viable": bool(screen["viable"]),
            **screen,
        }
    else:
        assumed_volatility_pct = 20.0
        mda = float("inf") if years <= 0 else 2.0 * assumed_volatility_pct / math.sqrt(years)
        result = {
            "method": "single portfolio return stream; 20% annual-volatility planning assumption at t=2",
            "mdaPct": mda,
            "viable": bool(mda <= MINIMUM_TRADABLE_ALPHA_PCT),
            "years": years,
            "symbols": len(symbols),
            "assumedAnnualVolatilityPct": assumed_volatility_pct,
            "tradableAlphaPct": MINIMUM_TRADABLE_ALPHA_PCT,
            "universeBreadthCredit": 0,
        }
    if math.isfinite(float(result["mdaPct"])):
        check_return(float(result["mdaPct"]), label=f"pre-result MDA {strategy_name}")
    return result


def spec_completeness(spec: StrategyValidationSpec) -> tuple[bool, dict[str, Any]]:
    payload = spec.to_dict()
    required = [
        "hypothesis", "primaryBenchmark", "primaryCriterion", "minimumTradableAlphaPct", "universePolicy",
        "warmupBars", "executionTiming", "costModel", "holdoutFraction",
        "walkForwardFolds", "searchFamily", "externalDataReleasePolicy",
    ]
    missing = [key for key in required if payload.get(key) in (None, "", [], {})]
    bounds_ok = 0.10 <= spec.holdout_fraction <= 0.40 and spec.walk_forward_folds >= 3
    if not bounds_ok:
        missing.append("valid holdout/walk-forward policy")
    if spec.minimum_tradable_alpha_pct <= 0:
        missing.append("positive minimum tradable alpha")
    return not missing, {"spec": payload, "missingFields": missing}


def _daily_equity(equity: pd.Series | None) -> pd.Series:
    if equity is None or len(equity) < 2 or not isinstance(equity.index, pd.DatetimeIndex):
        return pd.Series(dtype=float)
    # calendar_daily_series -- see engine/sanity.py's docstring. The previous
    # body here only collapsed intraday duplicates to one point per observed
    # DATE, without reindexing onto the calendar the curve actually spans. On
    # a SPARSE, entry/exit-only equity curve (every per-symbol strategy's
    # shared-capital simulation -- see engine.portfolio.run_portfolio_backtest)
    # that left multi-week gaps between adjacent "daily" observations, so
    # chronological_evidence/leakage_evidence/bootstrap_evidence all treated a
    # three-week price move as a single trading day's worth of data.
    return calendar_daily_series(equity)


def _benchmark_curve(start: date, end: date, index: pd.DatetimeIndex) -> pd.Series:
    bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", start, end)
    if bars.empty or len(bars) < 2:
        return pd.Series(dtype=float)
    close = pd.to_numeric(bars["Close"], errors="coerce").dropna()
    if close.empty:
        return pd.Series(dtype=float)
    if index.tz is not None and close.index.tz is None:
        close.index = close.index.tz_localize(index.tz)
    elif index.tz is None and close.index.tz is not None:
        close.index = close.index.tz_localize(None)
    return close.reindex(index, method="ffill").dropna()


def _window_return(series: pd.Series) -> float | None:
    if len(series) < 2 or not np.isfinite(series.iloc[[0, -1]]).all() or series.iloc[0] == 0:
        return None
    return float((series.iloc[-1] / series.iloc[0] - 1.0) * 100.0)


def chronological_evidence(
    equity: pd.Series,
    start: date,
    end: date,
    spec: StrategyValidationSpec,
    benchmark_equity: pd.Series | None = None,
) -> tuple[bool, dict[str, Any]]:
    strategy = _daily_equity(equity)
    if len(strategy) < max(60, spec.walk_forward_folds * 10):
        return False, {"reason": "insufficient daily equity observations", "observations": len(strategy)}
    benchmark = (
        _daily_equity(benchmark_equity).reindex(strategy.index, method="ffill").dropna()
        if benchmark_equity is not None
        else _benchmark_curve(start, end, strategy.index)
    )
    common = strategy.index.intersection(benchmark.index)
    strategy, benchmark = strategy.loc[common], benchmark.loc[common]
    if len(common) < 60:
        return False, {"reason": "benchmark alignment is incomplete", "observations": len(common)}

    split = max(1, min(len(common) - 2, int(len(common) * (1.0 - spec.holdout_fraction))))
    holdout_strategy = _window_return(strategy.iloc[split:])
    holdout_benchmark = _window_return(benchmark.iloc[split:])
    holdout_contribution = (
        None if holdout_strategy is None or holdout_benchmark is None
        else holdout_strategy - holdout_benchmark
    )

    windows: list[dict[str, Any]] = []
    chunks = np.array_split(np.arange(len(common)), spec.walk_forward_folds)
    for chunk in chunks:
        if len(chunk) < 2:
            continue
        s_ret = _window_return(strategy.iloc[chunk])
        b_ret = _window_return(benchmark.iloc[chunk])
        windows.append({
            "start": common[chunk[0]].isoformat(),
            "end": common[chunk[-1]].isoformat(),
            "strategyReturnPct": s_ret,
            "benchmarkReturnPct": b_ret,
            "contributionPct": None if s_ret is None or b_ret is None else s_ret - b_ret,
        })
    resolved = [row for row in windows if row["contributionPct"] is not None]
    positive_fraction = (
        sum(row["contributionPct"] > 0 for row in resolved) / len(resolved)
        if resolved else None
    )
    passed = bool(
        holdout_contribution is not None
        and holdout_contribution > 0
        and positive_fraction is not None
        and positive_fraction >= 0.60
    )
    return passed, {
        "selectionPolicy": "configuration frozen before the job; no holdout optimization",
        "developmentEnd": common[split - 1].isoformat(),
        "holdoutStart": common[split].isoformat(),
        "holdoutStrategyReturnPct": holdout_strategy,
        "holdoutBenchmarkReturnPct": holdout_benchmark,
        "holdoutContributionPct": holdout_contribution,
        "walkForwardWindows": windows,
        "fractionPositiveWindows": positive_fraction,
    }


def leakage_evidence(
    equity: pd.Series,
    trades: pd.DataFrame | None,
    spec: StrategyValidationSpec,
) -> tuple[bool, dict[str, Any]]:
    issues: list[str] = []
    daily = _daily_equity(equity)
    if daily.empty or not np.isfinite(daily.to_numpy(dtype=float)).all():
        issues.append("equity curve is empty or non-finite")
    if trades is not None and not trades.empty:
        entry_col = "EntryTime" if "EntryTime" in trades.columns else None
        exit_col = "ExitTime" if "ExitTime" in trades.columns else None
        if entry_col and exit_col:
            entry = pd.to_datetime(trades[entry_col], errors="coerce")
            exit_ = pd.to_datetime(trades[exit_col], errors="coerce")
            if entry.isna().any() or exit_.isna().any():
                issues.append("trade timestamps contain missing values")
            if (exit_ < entry).any():
                issues.append("one or more trades exit before entry")
        for column in ("EntryPrice", "ExitPrice", "PnL"):
            if column in trades and not np.isfinite(pd.to_numeric(trades[column], errors="coerce")).all():
                issues.append(f"{column} contains non-finite values")
    contract_ok = "completed" in spec.execution_timing or "training half" in spec.execution_timing
    if not contract_ok:
        issues.append("execution timing does not declare a completed-information boundary")
    return not issues, {
        "issues": issues,
        "executionTiming": spec.execution_timing,
        "externalDataReleasePolicy": spec.external_data_release_policy,
        "engineInvariant": "standard strategies receive only the current bar prefix; portfolio selectors receive data sliced through the rebalance timestamp",
    }


def bootstrap_evidence(
    equity: pd.Series,
    start: date,
    end: date,
    *,
    seed: int,
    benchmark_equity: pd.Series | None = None,
    simulations: int = 400,
    block_length: int = 5,
) -> tuple[bool, dict[str, Any]]:
    strategy = _daily_equity(equity)
    benchmark = (
        _daily_equity(benchmark_equity).reindex(strategy.index, method="ffill").dropna()
        if benchmark_equity is not None and not strategy.empty
        else _benchmark_curve(start, end, strategy.index) if not strategy.empty
        else pd.Series(dtype=float)
    )
    common = strategy.index.intersection(benchmark.index)
    if len(common) < 60:
        return False, {"reason": "insufficient aligned daily observations", "observations": len(common)}
    s = strategy.loc[common].pct_change().dropna().to_numpy(dtype=float)
    b = benchmark.loc[common].pct_change().dropna().to_numpy(dtype=float)
    length = min(len(s), len(b))
    s, b = s[-length:], b[-length:]
    if length < block_length * 3:
        return False, {"reason": "insufficient returns for block bootstrap", "observations": length}
    rng = np.random.default_rng(seed)
    starts = np.arange(0, max(1, length - block_length + 1))
    strategy_returns: list[float] = []
    contributions: list[float] = []
    sharpes: list[float] = []
    drawdowns: list[float] = []
    blocks_needed = math.ceil(length / block_length)
    for _ in range(simulations):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        indices = np.concatenate([np.arange(i, i + block_length) for i in chosen])[:length]
        s_draw, b_draw = s[indices], b[indices]
        s_total = float((np.prod(1.0 + s_draw) - 1.0) * 100.0)
        b_total = float((np.prod(1.0 + b_draw) - 1.0) * 100.0)
        wealth = np.cumprod(1.0 + s_draw)
        peak = np.maximum.accumulate(wealth)
        dd = float(np.min((wealth - peak) / peak) * 100.0)
        std = float(np.std(s_draw, ddof=1))
        strategy_returns.append(s_total)
        contributions.append(s_total - b_total)
        sharpes.append(float(np.mean(s_draw) / std * np.sqrt(252)) if std > 0 else 0.0)
        drawdowns.append(abs(dd))
    sr = np.asarray(strategy_returns)
    cr = np.asarray(contributions)
    sh = np.asarray(sharpes)
    dd = np.asarray(drawdowns)
    probability_outperform = float((cr > 0).mean())
    probability_profit = float((sr > 0).mean())
    passed = probability_outperform >= 0.80 and float(np.quantile(cr, 0.05)) > 0
    return passed, {
        "method": f"moving-block bootstrap, block={block_length} observations",
        "simulations": simulations,
        "probabilityProfit": probability_profit,
        "probabilityOutperform": probability_outperform,
        "returnP05Pct": float(np.quantile(sr, 0.05)),
        "returnMedianPct": float(np.median(sr)),
        "returnP95Pct": float(np.quantile(sr, 0.95)),
        "contributionP05Pct": float(np.quantile(cr, 0.05)),
        "contributionMedianPct": float(np.median(cr)),
        "contributionP95Pct": float(np.quantile(cr, 0.95)),
        "sharpeP05": float(np.quantile(sh, 0.05)),
        "sharpeMedian": float(np.median(sh)),
        "maxDrawdownP95Pct": float(np.quantile(dd, 0.95)),
    }


def standard_dependency_evidence(
    per_symbol: dict[str, Any],
    portfolio_trades: pd.DataFrame,
) -> tuple[bool, dict[str, Any]]:
    symbol_pnl: dict[str, float] = {}
    yearly_pnl: dict[str, float] = {}
    all_pnl: list[float] = []
    for symbol, result in per_symbol.items():
        trades = result.trades
        pnl = float(pd.to_numeric(trades.get("PnL", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
        symbol_pnl[symbol] = pnl
        if not trades.empty and "ExitTime" in trades and "PnL" in trades:
            years = pd.to_datetime(trades["ExitTime"], errors="coerce").dt.year
            for year, value in pd.to_numeric(trades["PnL"], errors="coerce").fillna(0).groupby(years).sum().items():
                if pd.notna(year):
                    yearly_pnl[str(int(year))] = yearly_pnl.get(str(int(year)), 0.0) + float(value)
        all_pnl.extend(pd.to_numeric(trades.get("PnL", pd.Series(dtype=float)), errors="coerce").dropna().tolist())
    positive_total = sum(max(value, 0.0) for value in symbol_pnl.values())
    largest_symbol_share = (
        max((max(value, 0.0) for value in symbol_pnl.values()), default=0.0) / positive_total
        if positive_total > 0 else None
    )
    ordered = sorted(all_pnl, reverse=True)
    pnl_ex_best_five = float(sum(ordered[5:])) if len(ordered) > 5 else None
    positive_year_fraction = (
        sum(value > 0 for value in yearly_pnl.values()) / len(yearly_pnl)
        if yearly_pnl else None
    )
    passed = bool(
        largest_symbol_share is not None and largest_symbol_share <= 0.50
        and pnl_ex_best_five is not None and pnl_ex_best_five > 0
        and positive_year_fraction is not None and positive_year_fraction >= 0.50
    )
    return passed, {
        "symbolPnl": symbol_pnl,
        "yearlyPnl": yearly_pnl,
        "largestPositiveSymbolShare": largest_symbol_share,
        "pnlExcludingBestFiveTrades": pnl_ex_best_five,
        "positiveYearFraction": positive_year_fraction,
        "portfolioTrades": len(portfolio_trades),
    }


def standard_cost_stress_evidence(
    per_symbol: dict[str, Any],
    portfolio_trades: pd.DataFrame,
    symbols: list[str],
    interval: str,
    start: date,
    end: date,
    base_return_pct: float,
    benchmark_return_pct: float | None,
    starting_equity: float = 10_000.0,
    universe_id: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    traded_notional = 0.0
    for result in per_symbol.values():
        trades = result.trades
        if trades.empty or not {"EntryPrice", "ExitPrice", "Size"}.issubset(trades.columns):
            continue
        size = pd.to_numeric(trades["Size"], errors="coerce").abs().fillna(0)
        entry = pd.to_numeric(trades["EntryPrice"], errors="coerce").fillna(0)
        exit_ = pd.to_numeric(trades["ExitPrice"], errors="coerce").fillna(0)
        traded_notional += float((size * (entry + exit_)).sum())
    from engine.execution_calibration import spread_for_universe

    spreads = [spread_for_universe(symbol, start, end, universe_id) for symbol in symbols]
    mean_spread = float(np.mean(spreads)) if spreads else 0.0
    estimated_base_cost = traded_notional * mean_spread
    stressed = {
        f"{multiple}x": base_return_pct - ((multiple - 1) * estimated_base_cost / starting_equity * 100.0)
        for multiple in (1, 2, 3)
    }
    threshold = benchmark_return_pct if benchmark_return_pct is not None else 0.0
    delayed_pnl = 0.0
    original_selected_pnl = 0.0
    borrow_cost = 0.0
    participation_rates: list[float] = []
    bars_cache: dict[str, pd.DataFrame] = {}
    if not portfolio_trades.empty:
        for _, trade in portfolio_trades.iterrows():
            symbol = str(trade.get("Symbol", ""))
            if not symbol:
                continue
            if symbol not in bars_cache:
                bars_cache[symbol] = data_module.get_bars(symbol, interval, start, end)
            bars = bars_cache[symbol]
            if bars.empty:
                continue
            entry_time = pd.Timestamp(trade["EntryTime"])
            exit_time = pd.Timestamp(trade["ExitTime"])
            if bars.index.tz is not None and entry_time.tzinfo is None:
                entry_time = entry_time.tz_localize(bars.index.tz)
                exit_time = exit_time.tz_localize(bars.index.tz)
            elif bars.index.tz is None and entry_time.tzinfo is not None:
                entry_time = entry_time.tz_localize(None)
                exit_time = exit_time.tz_localize(None)
            entry_pos = int(bars.index.searchsorted(entry_time, side="left")) + 1
            exit_pos = int(bars.index.searchsorted(exit_time, side="left")) + 1
            if entry_pos >= len(bars) or exit_pos >= len(bars):
                continue
            size = float(trade.get("Size", 0.0))
            direction = 1.0 if size >= 0 else -1.0
            quantity = abs(size)
            delayed_entry = float(bars["Open"].iloc[entry_pos])
            delayed_exit = float(bars["Open"].iloc[exit_pos])
            delayed_pnl += quantity * direction * (delayed_exit - delayed_entry)
            original_selected_pnl += float(trade.get("PnL", 0.0))
            dollar_volume = float(bars["Close"].iloc[entry_pos] * bars["Volume"].iloc[entry_pos])
            if dollar_volume > 0:
                participation_rates.append(quantity * delayed_entry / dollar_volume)
            if direction < 0:
                holding_days = max(0.0, (exit_time - entry_time).total_seconds() / 86_400.0)
                borrow_cost += quantity * delayed_entry * 0.03 * holding_days / 365.25
    delayed_return = (
        base_return_pct + (delayed_pnl - original_selected_pnl - borrow_cost) / starting_equity * 100.0
    )
    partial_fill_return = base_return_pct * 0.80
    max_participation = max(participation_rates, default=None)
    capacity_ok = max_participation is None or max_participation <= 0.01
    passed = (
        stressed["2x"] > threshold
        and stressed["3x"] > 0
        and delayed_return > threshold
        and partial_fill_return > 0
        and capacity_ok
    )
    return passed, {
        "tradedNotional": traded_notional,
        "meanSpreadBps": mean_spread * 10_000.0,
        "estimatedBaseCost": estimated_base_cost,
        "baseReturnPct": base_return_pct,
        "benchmarkReturnPct": benchmark_return_pct,
        "stressedReturnPct": stressed,
        "oneBarDelayedReturnPct": delayed_return,
        "partialFill80ReturnPct": partial_fill_return,
        "shortBorrowCostAt3Pct": borrow_cost,
        "maxBarParticipationRate": max_participation,
        "capacityLimit": 0.01,
        "limitations": "historical locate availability and order-book market impact are unavailable; short borrow is stressed at 3% annualized",
    }


def disjoint_replication_evidence(per_symbol: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    names = sorted(per_symbol)
    halves = [names[::2], names[1::2]]
    rows = []
    for index, half in enumerate(halves, start=1):
        pnl = 0.0
        trades = 0
        for symbol in half:
            frame = per_symbol[symbol].trades
            trades += len(frame)
            if "PnL" in frame:
                pnl += float(pd.to_numeric(frame["PnL"], errors="coerce").fillna(0).sum())
        rows.append({"universe": f"deterministic half {index}", "symbols": half, "trades": trades, "pnl": pnl})
    passed = len(rows) == 2 and all(row["trades"] > 0 and row["pnl"] > 0 for row in rows)
    return passed, {"universes": rows, "splitRule": "alphabetical alternating assignment fixed before measurement"}


def _hash_file(path: Path, digest: "hashlib._Hash") -> None:
    if not path.exists() or not path.is_file():
        digest.update(f"missing:{path.name}".encode())
        return
    digest.update(path.name.encode())
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)


def build_run_manifest(
    *,
    strategy_name: str,
    engine: EngineKind,
    symbols: list[str],
    interval: str,
    start: date,
    end: date,
    params: dict[str, Any] | None,
    strategy_class: type,
    experiment_id: int | None,
    spec: StrategyValidationSpec,
    equity: pd.Series,
) -> dict[str, Any]:
    code_digest = hashlib.sha256()
    source_paths: list[Path] = []
    try:
        source_paths.append(Path(inspect.getsourcefile(strategy_class) or ""))
    except (TypeError, OSError):
        pass
    root = Path(__file__).resolve().parent.parent
    source_paths.extend([
        root / "engine" / "validation.py",
        root / "engine" / "research_governance.py",
        root / "engine" / "runner.py",
        root / "engine" / ("backtest.py" if engine == "standard" else "cross_sectional.py" if engine == "cross_sectional" else "pairs.py"),
    ])
    for path in sorted(set(source_paths), key=str):
        if str(path):
            _hash_file(path, code_digest)

    data_digest = hashlib.sha256()
    validation_symbols = set(symbols) | {"SPY", "IWM", "IWD", "IWF", "MTUM"}
    if engine in {"cross_sectional", "pairs"}:
        validation_symbols.update(MIDCAP_UNIVERSE)
        validation_symbols.update(SMALL_CAP_UNIVERSE)
    data_files = sorted({
        *(data_module.DATA_DIR / f"{symbol}_{interval}.parquet" for symbol in symbols),
        *(data_module.DATA_DIR / f"{symbol}_1d.parquet" for symbol in validation_symbols),
    }, key=str)
    missing_data_files = [path.name for path in data_files if not path.is_file()]
    for path in data_files:
        _hash_file(path, data_digest)

    result_digest = hashlib.sha256()
    daily = _daily_equity(equity)
    result_digest.update(pd.util.hash_pandas_object(daily, index=True).values.tobytes())
    execution_calibration = execution_calibration_snapshot(symbols, start, end)
    config = {
        "strategyName": strategy_name,
        "engine": engine,
        "symbols": sorted(symbols),
        "interval": interval,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "params": params or {},
        "executionCalibration": execution_calibration,
    }
    fingerprint = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    dependency_versions = {}
    for package in ("pandas", "numpy", "backtesting", "yfinance", "statsmodels"):
        try:
            dependency_versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            dependency_versions[package] = None
    return {
        "suiteVersion": RESEARCH_SUITE_VERSION,
        "experimentId": experiment_id,
        "runFingerprint": fingerprint,
        "codeHashSha256": code_digest.hexdigest(),
        "dataHashSha256": data_digest.hexdigest(),
        "resultHashSha256": result_digest.hexdigest(),
        "hashedDataFiles": [path.name for path in data_files],
        "missingDataFiles": missing_data_files,
        "config": config,
        "validationSpec": spec.to_dict(),
        "randomSeeds": {"researchSuite": spec.random_seed},
        "dependencies": {"python": platform.python_version(), **dependency_versions},
        "executionCalibration": execution_calibration,
    }


def lifecycle_stage(
    *,
    is_preregistered: bool,
    holdout_passed: bool,
    forward_test_worthy: bool,
    production_capital_worthy: bool,
    signal_edge: str,
) -> str:
    if production_capital_worthy:
        return "production_eligible"
    if forward_test_worthy:
        return "paper_eligible"
    if holdout_passed:
        return "holdout_passed"
    if is_preregistered:
        return "preregistered"
    if signal_edge == "Not established":
        return "edge_not_established"
    return "exploratory"
