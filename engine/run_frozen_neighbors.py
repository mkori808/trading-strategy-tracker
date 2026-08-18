"""Execute only the robustness arms registered before canonical V1 results.

Canonical rows must already exist.  Arms are persisted as research evidence,
not ordinary run-history rows, and can never replace a canonical result.
The script is resumable: an experiment is registered before its result is
computed, while completed arm rows are skipped on subsequent invocations.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from itertools import product
import json
import math
from typing import Any, Iterable

import pandas as pd

from engine import data as data_module, logging_db
from engine.cross_sectional import run_cross_sectional_backtest
from engine.execution_calibration import spread_for_universe
from engine.frozen_event import (
    earnings_reaction_dates, precompute_signal_features,
    run_frozen_event_backtest, signals_from_features,
)
from engine.frozen_protocol import family_record, load_protocol
from engine.pit_analysis import dynamic_random_benchmarks
from engine.portfolio import run_portfolio_backtest
from engine.runner import ALPACA_COMMISSION_BPS
from engine.sanity import check_return, check_sharpe
from engine.universe import SECTOR_BENCHMARK
from engine.universe_ledger import resolve_schedule
from strategies.params import apply_params
from strategies.registry import build_cross_sectional_strategy, build_frozen_event_strategy


IMPLEMENTED_FAMILIES = (
    "52-Week-High Momentum",
    "Negative Return + Volume Shock Reversal",
    "Market-Residual Momentum",
    "Volume-Shock Continuation",
    "MAX Lottery-Return Reversal",
    "Volatility-Conditioned Pullback",
)

IMPLEMENTATION_REVISIONS = {
    "Market-Residual Momentum": "benchmark_cutoff_before_execution_v2",
}

DISPLAY_NAMES = {
    "52-Week-High Momentum": ("52-Week-High Momentum",),
    "Negative Return + Volume Shock Reversal": (
        "Negative Return + Volume Shock Reversal",
    ),
    "Market-Residual Momentum": ("Market-Residual Momentum",),
    "Volume-Shock Continuation": (
        "Volume-Shock Continuation (Long)",
        "Volume-Shock Continuation (Short)",
    ),
    "MAX Lottery-Return Reversal": ("MAX Lottery-Return Reversal (Short)",),
    "Volatility-Conditioned Pullback": ("Volatility-Conditioned Pullback",),
}


def _grid(neighbors: dict[str, list[Any]]) -> Iterable[dict[str, Any]]:
    keys = list(neighbors)
    for values in product(*(neighbors[key] for key in keys)):
        yield dict(zip(keys, values))


def _engine_params(family: str, raw: dict[str, Any]) -> dict[str, Any]:
    params = dict(raw)
    params.pop("factorModel", None)  # market-only is structural in this build
    if family == "MAX Lottery-Return Reversal":
        params["earnings_exclusion"] = params.pop("earningsExclusion")
    if family == "Volatility-Conditioned Pullback":
        regime = params.pop("atrRegime")
        params["atr_percentile_low"], params["atr_percentile_high"] = {
            "bottom_50": (0.0, 0.5),
            "middle_60": (0.2, 0.8),
            "top_50": (0.5, 1.0),
        }[regime]
    return params


def _normalize_canonical_families() -> None:
    """Move preexisting V1 rows onto stable, rename-proof family keys."""
    conn = logging_db.get_connection()
    with conn:
        for family in IMPLEMENTED_FAMILIES:
            stable = family_record(family)["searchFamily"]
            for number, name in enumerate(DISPLAY_NAMES[family], start=1):
                rows = conn.execute(
                    "SELECT id, search_family FROM research_experiments "
                    "WHERE strategy_name = ? AND status = 'completed' ORDER BY id",
                    (name,),
                ).fetchall()
                if not rows:
                    raise RuntimeError(
                        f"Canonical V1 for {name} is not stored; neighbors are forbidden"
                    )
                canonical_id, current_family = rows[0]
                if current_family != stable:
                    conn.execute(
                        "UPDATE research_experiments SET search_family = ?, "
                        "family_search_number = ? WHERE id = ?",
                        (stable, number, canonical_id),
                    )
        # Early resumable runs keyed continuation arms only by numerical
        # parameters.  Long and short intentionally share one multiplicity
        # family, so the side must be part of experiment identity too.
        volume_family = family_record("Volume-Shock Continuation")["searchFamily"]
        rows = conn.execute(
            "SELECT id, config_json FROM research_experiments "
            "WHERE search_family = ? AND config_json LIKE '%frozenNeighbor%'",
            (volume_family,),
        ).fetchall()
        for experiment_id, encoded in rows:
            config = json.loads(encoded)
            if "variant" not in config:
                config["variant"] = "Volume-Shock Continuation (Long)"
                conn.execute(
                    "UPDATE research_experiments SET config_json = ? WHERE id = ?",
                    (json.dumps(config, sort_keys=True), experiment_id),
                )
    conn.close()


def _existing_experiment(search_family: str, config: dict[str, Any]):
    encoded = json.dumps(config, sort_keys=True)
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    row = conn.execute(
        "SELECT e.*, n.status AS neighbor_status FROM research_experiments e "
        "LEFT JOIN frozen_neighbor_results n ON n.experiment_id=e.id "
        "WHERE e.search_family=? AND e.config_json=? ORDER BY e.id LIMIT 1",
        (search_family, encoded),
    ).fetchone()
    conn.close()
    return row


def _experiment_config(
    family: str, display_name: str, raw: dict[str, Any]
) -> dict[str, Any]:
    config = {"frozenNeighbor": True, "params": raw, "universeId": "dow_pit"}
    if len(DISPLAY_NAMES[family]) > 1:
        config["variant"] = display_name
    if family in IMPLEMENTATION_REVISIONS:
        config["implementationRevision"] = IMPLEMENTATION_REVISIONS[family]
        config["window"] = load_protocol()["window"]
    return config


def _register(family: str, display_name: str, raw: dict[str, Any]) -> tuple[int, bool]:
    record = family_record(family)
    search_family = record["searchFamily"]
    config = _experiment_config(family, display_name, raw)
    existing = _existing_experiment(search_family, config)
    if existing is not None:
        return int(existing["id"]), existing["neighbor_status"] == "completed"
    experiment_id, _ = logging_db.register_experiment(
        strategy_name=display_name,
        engine=record["engine"],
        hypothesis=(
            f"Pre-registered robustness diagnostic for {family}; canonical V1 remains fixed"
        ),
        config=config,
        primary_benchmark=(
            "PIT equal-weight eligible universe"
            if record["engine"] == "cross_sectional" else "exposure-matched SPY"
        ),
        primary_criterion="same sign and general economic behavior as canonical V1",
        planned_universes=["dow_pit"],
        search_family=search_family,
        is_preregistered=True,
        universe_id="dow_pit",
    )
    logging_db.set_family_search_count(
        search_family, logging_db.family_search_count(search_family)
    )
    return experiment_id, False


def _return_pct(series: pd.Series) -> float | None:
    clean = series.dropna()
    if len(clean) < 2 or float(clean.iloc[0]) <= 0:
        return None
    return float((clean.iloc[-1] / clean.iloc[0] - 1.0) * 100.0)


def _event_summary(result, portfolio) -> dict[str, Any]:
    metrics = result.metrics
    summary = {
        "returnPct": portfolio.return_pct,
        "cagrPct": portfolio.cagr_pct,
        "sharpe": portfolio.sharpe,
        "sortino": portfolio.sortino,
        "maxDrawdownPct": portfolio.max_drawdown_pct,
        "trades": metrics.trades_taken,
        "winRatePct": metrics.win_rate * 100.0,
        "expectancyR": metrics.expectancy_r,
        "profitFactor": metrics.profit_factor,
        "averageExposurePct": metrics.average_gross_exposure_pct,
        "timeInMarketPct": metrics.time_in_market_pct,
        "benchmarkExcessPct": metrics.matched_spy_excess_pct,
        "matchedSpyReturnPct": metrics.matched_spy_return_pct,
        "alphaAnnualPct": metrics.matched_alpha_annual_pct,
        "modeledCosts": metrics.modeled_costs,
        "skippedForCapacity": portfolio.skipped_for_capacity,
        "supportsHypothesis": bool(
            metrics.trades_taken > 0
            and metrics.expectancy_r > 0
            and metrics.matched_spy_excess_pct is not None
            and metrics.matched_spy_excess_pct > 0
        ),
    }
    years = max(0.0, (result.end - result.start).days / 365.25)
    check_return(portfolio.return_pct, label=result.strategy_name, years=years)
    check_sharpe(portfolio.sharpe, label=result.strategy_name)
    return summary


def _cross_summary(result, ew_return: float | None) -> dict[str, Any]:
    exposure = None
    if not result.rebalances.empty:
        exposure = float(
            result.rebalances["holdings"].map(
                lambda holdings: sum(abs(float(weight)) for weight in holdings.values())
            ).mean() * 100.0
        )
    excess = None if ew_return is None else result.return_pct - ew_return
    summary = {
        "returnPct": result.return_pct,
        "cagrPct": result.cagr_pct,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "maxDrawdownPct": abs(result.max_drawdown_pct),
        "trades": None,
        "rebalances": len(result.rebalances),
        "averageExposurePct": exposure,
        "benchmarkExcessPct": excess,
        "equalWeightReturnPct": ew_return,
        "modeledCosts": result.total_costs,
        "supportsHypothesis": bool(excess is not None and excess > 0),
    }
    years = max(0.0, (result.end - result.start).days / 365.25)
    check_return(result.return_pct, label=result.strategy_name, years=years)
    check_sharpe(result.sharpe, label=result.strategy_name)
    return summary


def _record_success(
    experiment_id: int, family: str, display_name: str,
    raw: dict[str, Any], result: dict[str, Any],
) -> None:
    search_family = family_record(family)["searchFamily"]
    stored_config = dict(raw)
    if family in IMPLEMENTATION_REVISIONS:
        stored_config["_implementationRevision"] = IMPLEMENTATION_REVISIONS[family]
        stored_config["_window"] = load_protocol()["window"]
    logging_db.record_frozen_neighbor_result(
        experiment_id=experiment_id, strategy_name=display_name,
        search_family=search_family, config=stored_config, status="completed", result=result,
    )
    logging_db.complete_experiment(
        experiment_id, "completed",
        "supports frozen hypothesis" if result["supportsHypothesis"] else "does not support frozen hypothesis",
    )


def _record_failure(
    experiment_id: int, family: str, display_name: str,
    raw: dict[str, Any], exc: Exception,
) -> None:
    search_family = family_record(family)["searchFamily"]
    message = f"{type(exc).__name__}: {exc}"
    stored_config = dict(raw)
    if family in IMPLEMENTATION_REVISIONS:
        stored_config["_implementationRevision"] = IMPLEMENTATION_REVISIONS[family]
        stored_config["_window"] = load_protocol()["window"]
    logging_db.record_frozen_neighbor_result(
        experiment_id=experiment_id, strategy_name=display_name,
        search_family=search_family, config=stored_config, status="failed", error=message,
    )
    logging_db.complete_experiment(experiment_id, "failed", message)


def run_neighbors(selected_family: str | None = None, limit: int | None = None) -> dict:
    _normalize_canonical_families()
    frozen_window = load_protocol()["window"]
    start = date.fromisoformat(frozen_window["start"])
    end = date.fromisoformat(frozen_window["end"])
    schedule = resolve_schedule(
        "dow_jones_industrial_average", start, end, require_complete=False
    )
    if schedule is None:
        raise RuntimeError("Dow PIT schedule unavailable")
    symbols = schedule.symbols
    fetch_start = start - timedelta(days=430)
    bars_by_symbol = {
        symbol: bars for symbol in symbols
        if not (bars := data_module.get_bars(symbol, "1d", fetch_start, end)).empty
    }
    market_bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", fetch_start, end)
    risk_free_rate = data_module.risk_free_rate(start, end)
    spreads = {
        symbol: spread_for_universe(symbol, start, end, "dow_pit")
        for symbol in bars_by_symbol
    }
    earnings_cache = {
        symbol: earnings_reaction_dates(symbol, bars)
        for symbol, bars in bars_by_symbol.items()
    }
    feature_cache = {
        symbol: precompute_signal_features(bars, market_bars)
        for symbol, bars in bars_by_symbol.items()
    }
    risk_cache = {
        symbol: {
            pd.Timestamp(stamp).date(): float(value)
            for stamp, value in features["atr14"].dropna().items()
        }
        for symbol, features in feature_cache.items()
    }
    signal_exit_cache = {
        symbol: {
            pd.Timestamp(stamp).date()
            for stamp in features.index[
                (features["Close"] > features["sma5"]).fillna(False)
            ]
        }
        for symbol, features in feature_cache.items()
    }
    attempted = completed = skipped = failed = 0
    family_summaries: dict[str, dict[str, int]] = {}

    families = [selected_family] if selected_family else list(IMPLEMENTED_FAMILIES)
    for family in families:
        if family not in IMPLEMENTED_FAMILIES:
            raise ValueError(f"Unknown implemented frozen family: {family}")
        record = family_record(family)
        canonical = record["canonical"]
        counts = {"completed": 0, "skipped": 0, "failed": 0}
        for display_name in DISPLAY_NAMES[family]:
            for raw in _grid(record["neighbors"]):
                # Each side has its own stored canonical V1 but shares one
                # multiple-testing family, as preregistered.
                canonical_on_grid = {
                    key: canonical[key] for key in record["neighbors"]
                }
                if raw == canonical_on_grid:
                    continue
                if limit is not None and attempted >= limit:
                    family_summaries[family] = counts
                    return {
                        "attempted": attempted, "completed": completed,
                        "skipped": skipped, "failed": failed,
                        "families": family_summaries, "limited": True,
                    }
                experiment_id, done = _register(family, display_name, raw)
                if done:
                    skipped += 1; counts["skipped"] += 1
                    continue
                attempted += 1
                try:
                    params = _engine_params(family, raw)
                    if record["engine"] == "cross_sectional":
                        strategy = apply_params(
                            build_cross_sectional_strategy(
                                display_name, risk_free_rate,
                                benchmark_bars=market_bars,
                            ),
                            params,
                        )
                        result = run_cross_sectional_backtest(
                            display_name, strategy, symbols, start, end,
                            risk_free_rate=risk_free_rate,
                            rebalance_frequency=getattr(
                                strategy, "rebalance_frequency", "monthly"
                            ),
                            spread_by_symbol=spreads,
                            commission_bps=ALPACA_COMMISSION_BPS,
                            bars_by_symbol=bars_by_symbol,
                            membership_at=schedule.membership_at,
                            universe_key=schedule.universe_key,
                        )
                        ew_curve, _ = dynamic_random_benchmarks(
                            bars_by_symbol, schedule.membership_at,
                            result.equity_curve.index,
                            rebalance_frequency=getattr(
                                strategy, "rebalance_frequency", "monthly"
                            ),
                            top_n=int(getattr(strategy, "top_n", 5)),
                            initial_equity=float(result.equity_curve.iloc[0]),
                            simulations=1,
                        )
                        summary = _cross_summary(result, _return_pct(ew_curve))
                    else:
                        strategy = apply_params(
                            build_frozen_event_strategy(display_name), params
                        )
                        result = run_frozen_event_backtest(
                            display_name, strategy, symbols, bars_by_symbol,
                            market_bars, start, end, risk_free_rate,
                            schedule.membership_at, earnings_cache=earnings_cache,
                            signals_by_symbol={
                                symbol: signals_from_features(strategy, features)
                                for symbol, features in feature_cache.items()
                            },
                            risk_by_symbol=risk_cache,
                            signal_exits_by_symbol=(
                                signal_exit_cache
                                if family == "Volatility-Conditioned Pullback"
                                else {symbol: set() for symbol in feature_cache}
                            ),
                        )
                        portfolio = run_portfolio_backtest(
                            result, risk_free_rate=risk_free_rate
                        )
                        summary = _event_summary(result, portfolio)
                    _record_success(
                        experiment_id, family, display_name, raw, summary
                    )
                    completed += 1; counts["completed"] += 1
                except Exception as exc:  # persist the arm failure; never hide it
                    _record_failure(experiment_id, family, display_name, raw, exc)
                    failed += 1; counts["failed"] += 1
                if attempted % 10 == 0:
                    print(
                        json.dumps({
                            "progress": attempted, "completed": completed,
                            "failed": failed, "family": family,
                        }), flush=True,
                    )
        family_summaries[family] = counts
        logging_db.set_family_search_count(
            record["searchFamily"],
            logging_db.family_search_count(record["searchFamily"]),
        )
    return {
        "attempted": attempted, "completed": completed, "skipped": skipped,
        "failed": failed, "families": family_summaries, "limited": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--family", choices=IMPLEMENTED_FAMILIES)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    print(json.dumps(run_neighbors(args.family, args.limit), indent=2))


if __name__ == "__main__":
    main()
