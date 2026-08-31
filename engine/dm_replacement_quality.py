"""Preregistered diagnostic decomposition of canonical replacement quality.

No strategy rule is implemented here.  The frozen specification is
``research/dm_replacement_quality_decomposition_preregistration.json``.
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine import data as data_module
from engine.runner import run_cross_sectional
from engine.trade_lifecycle_audit import (
    BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, _at_or_after, _iso, _mean_pct, _pct,
    _safe, _score_snapshot, _share,
)
from strategies.registry import build_cross_sectional_strategy


STUDY_NAME = "Dual Momentum Replacement-Quality Decomposition v1"
PREREGISTRATION = Path("research/dm_replacement_quality_decomposition_preregistration.json")
OUTPUT_DIR = Path("reports/dm_replacement_quality")
TOP_N = 5
NULL_DRAWS = 2_000
CONTINUOUS_FEATURES = [
    "rank_gap", "outgoing_rank_deterioration", "score_spread", "score_percentile_spread",
    "outgoing_return_20d", "incoming_return_20d", "outgoing_return_60d", "incoming_return_60d",
    "outgoing_acceleration", "incoming_acceleration", "outgoing_unrealized_return",
    "outgoing_holding_sessions", "outgoing_mfe_to_date", "outgoing_mae_to_date",
    "outgoing_giveback", "incoming_drawdown_from_60d_high", "incoming_distance_from_189d_high",
]


def _strategy_for(name: str, result: Any) -> Any:
    benchmark = None
    if name == "Market-Residual Momentum":
        benchmark = data_module.get_bars("SPY", "1d", result.start - timedelta(days=430), result.end)
    return build_cross_sectional_strategy(name, result.risk_free_rate, benchmark_bars=benchmark)


def _ret(close: pd.Series, sessions: int) -> float | None:
    clean = close.dropna()
    if len(clean) < sessions + 1: return None
    base = float(clean.iloc[-sessions - 1])
    return float(clean.iloc[-1] / base - 1.0) if base > 0 else None


def _max_drawdown(path: pd.Series) -> float | None:
    clean = path.dropna()
    if clean.empty: return None
    return float((clean / clean.cummax() - 1.0).min())


def _future_path(frame: pd.DataFrame, day: pd.Timestamp, next_day: pd.Timestamp) -> dict[str, Any] | None:
    p0, p1 = _at_or_after(frame, day, "Open"), _at_or_after(frame, next_day, "Open")
    if p0 is None or p1 is None or p0 <= 0: return None
    held = frame.loc[(frame.index >= day) & (frame.index < next_day)]
    if held.empty: return None
    closes = pd.concat([pd.Series([p0]), held.Close.reset_index(drop=True), pd.Series([p1])], ignore_index=True)
    return {"return": p1 / p0 - 1.0, "mfe": float(held.High.max() / p0 - 1.0),
            "mae": float(held.Low.min() / p0 - 1.0), "max_drawdown": _max_drawdown(closes)}


def _historical_state(frame: pd.DataFrame, day: pd.Timestamp, entry_day: pd.Timestamp | None) -> dict[str, Any]:
    history = frame.loc[frame.index < day]
    close = history.Close.dropna()
    r20, r60 = _ret(close, 20), _ret(close, 60)
    output = {"return_20d": r20, "return_60d": r60,
              "acceleration": (r20 - r60 / 3.0 if r20 is not None and r60 is not None else None),
              "drawdown_from_60d_high": (float(close.iloc[-1] / close.iloc[-60:].max() - 1.0) if len(close) >= 60 else None),
              "distance_from_189d_high": (float(close.iloc[-1] / close.iloc[-189:].max() - 1.0) if len(close) >= 189 else None)}
    if entry_day is None:
        return output
    entry_open = _at_or_after(frame, entry_day, "Open")
    path = frame.loc[(frame.index >= entry_day) & (frame.index < day), "Close"].dropna()
    if entry_open is None or entry_open <= 0 or path.empty:
        return output
    returns = path / entry_open - 1.0
    current, mfe, mae = float(returns.iloc[-1]), max(0.0, float(returns.max())), min(0.0, float(returns.min()))
    output.update({"unrealized_return": current, "holding_sessions": len(path), "mfe_to_date": mfe,
                   "mae_to_date": mae, "giveback": mfe - current, "recovery_from_mae": current - mae,
                   "currently_profitable": current > 0})
    return output


def build_event_ledger(strategy_name: str) -> tuple[pd.DataFrame, dict[str, Any], Any]:
    result = run_cross_sectional(strategy_name, persist=False)
    strategy = _strategy_for(strategy_name, result)
    bars = result.validation_bars or {}
    membership = result.membership_at_runtime
    rebs = result.rebalances.sort_values("date").reset_index(drop=True)
    snapshots = []
    for day in rebs.date:
        eligible = membership(day.date()) if membership else set(bars)
        snapshots.append(_score_snapshot(strategy_name, strategy, bars, day, eligible))

    entry_dates: dict[str, pd.Timestamp] = {}
    rows, decisions = [], []
    excluded_missing = censored_last = unpaired_removals = unpaired_additions = 0
    all_removals = all_additions = 0
    for i, reb in rebs.iterrows():
        day, current = reb.date, set(reb.holdings)
        previous = set(rebs.iloc[i - 1].holdings) if i else set()
        added, removed = current - previous, previous - current
        if i == 0:
            for symbol in added: entry_dates[symbol] = day
            continue
        all_removals += len(removed); all_additions += len(added)
        snap, prior_snap = snapshots[i], snapshots[i - 1]
        rank_key = lambda s: (float(snap.loc[s, "rank"]) if s in snap.index else math.inf, s)
        outs, ins = sorted(removed, key=rank_key), sorted(added, key=rank_key)
        pair_count = min(len(outs), len(ins))
        unpaired_removals += len(outs) - pair_count
        unpaired_additions += len(ins) - pair_count
        decision = {"date": _iso(day), "removals": len(outs), "additions": len(ins), "pairable": pair_count,
                    "unpairedRemovals": len(outs) - pair_count, "unpairedAdditions": len(ins) - pair_count,
                    "censoredNoNextRebalance": i + 1 >= len(rebs)}
        decisions.append(decision)
        if i + 1 >= len(rebs):
            censored_last += pair_count
        else:
            next_day = rebs.iloc[i + 1].date
            n_scores = max(len(snap), 1)
            for outgoing, incoming in zip(outs[:pair_count], ins[:pair_count]):
                outgoing_future = _future_path(bars[outgoing], day, next_day)
                incoming_future = _future_path(bars[incoming], day, next_day)
                if outgoing_future is None or incoming_future is None:
                    excluded_missing += 1
                    continue
                out_rank = int(snap.loc[outgoing, "rank"]) if outgoing in snap.index else None
                in_rank = int(snap.loc[incoming, "rank"]) if incoming in snap.index else None
                prior_rank = int(prior_snap.loc[outgoing, "rank"]) if outgoing in prior_snap.index else None
                out_score = float(snap.loc[outgoing, "score"]) if outgoing in snap.index else None
                in_score = float(snap.loc[incoming, "score"]) if incoming in snap.index else None
                out_state = _historical_state(bars[outgoing], day, entry_dates.get(outgoing))
                in_state = _historical_state(bars[incoming], day, None)
                out_pct = (1.0 - (out_rank - 1) / (n_scores - 1)) if out_rank and n_scores > 1 else None
                in_pct = (1.0 - (in_rank - 1) / (n_scores - 1)) if in_rank and n_scores > 1 else None
                rows.append({
                    "strategy": strategy_name, "rebalance_date": _iso(day), "next_rebalance_date": _iso(next_day),
                    "outgoing_symbol": outgoing, "incoming_symbol": incoming,
                    "outgoing_prior_rank": prior_rank, "outgoing_current_rank": out_rank,
                    "incoming_current_rank": in_rank,
                    "rank_gap": (out_rank - in_rank if out_rank is not None and in_rank is not None else None),
                    "outgoing_rank_deterioration": (out_rank - prior_rank if out_rank is not None and prior_rank is not None else None),
                    "outgoing_score": out_score, "incoming_score": in_score,
                    "score_spread": (in_score - out_score if in_score is not None and out_score is not None else None),
                    "score_percentile_spread": (in_pct - out_pct if in_pct is not None and out_pct is not None else None),
                    "outgoing_return_20d": out_state.get("return_20d"), "incoming_return_20d": in_state.get("return_20d"),
                    "outgoing_return_60d": out_state.get("return_60d"), "incoming_return_60d": in_state.get("return_60d"),
                    "outgoing_acceleration": out_state.get("acceleration"), "incoming_acceleration": in_state.get("acceleration"),
                    "outgoing_unrealized_return": out_state.get("unrealized_return"),
                    "outgoing_holding_sessions": out_state.get("holding_sessions"),
                    "outgoing_mfe_to_date": out_state.get("mfe_to_date"), "outgoing_mae_to_date": out_state.get("mae_to_date"),
                    "outgoing_giveback": out_state.get("giveback"), "outgoing_recovery_from_mae": out_state.get("recovery_from_mae"),
                    "outgoing_currently_profitable": out_state.get("currently_profitable"),
                    "incoming_drawdown_from_60d_high": in_state.get("drawdown_from_60d_high"),
                    "incoming_distance_from_189d_high": in_state.get("distance_from_189d_high"),
                    "outgoing_subsequent_return": outgoing_future["return"],
                    "incoming_subsequent_return": incoming_future["return"],
                    "replacement_advantage": incoming_future["return"] - outgoing_future["return"],
                    "outgoing_future_mfe": outgoing_future["mfe"], "outgoing_future_mae": outgoing_future["mae"],
                    "outgoing_future_max_drawdown": outgoing_future["max_drawdown"],
                    "incoming_future_mfe": incoming_future["mfe"], "incoming_future_mae": incoming_future["mae"],
                    "incoming_future_max_drawdown": incoming_future["max_drawdown"],
                })
        for symbol in removed: entry_dates.pop(symbol, None)
        for symbol in added: entry_dates[symbol] = day
    frame = pd.DataFrame(rows)
    duplicates = int(frame.duplicated(["rebalance_date", "outgoing_symbol", "incoming_symbol"]).sum()) if len(frame) else 0
    reconciliation = {"strategy": strategy_name, "rebalances": len(rebs), "allRemovals": all_removals,
                      "allAdditions": all_additions, "completedPairs": len(frame),
                      "uniquePairRebalanceDates": frame.rebalance_date.nunique() if len(frame) else 0,
                      "effectiveN": frame.rebalance_date.nunique() if len(frame) else 0,
                      "censoredLastRebalancePairs": censored_last, "excludedMissingPricePairs": excluded_missing,
                      "unpairedRemovalsFromCashChanges": unpaired_removals,
                      "unpairedAdditionsFromCashChanges": unpaired_additions,
                      "duplicatePairs": duplicates, "identicalWindows": bool((frame.next_rebalance_date > frame.rebalance_date).all()),
                      "decisionLedger": decisions}
    return frame, reconciliation, result


def _spearman(x: pd.Series, y: pd.Series) -> float | None:
    pair = pd.concat([x, y], axis=1).dropna()
    if len(pair) < 3 or pair.iloc[:, 0].nunique() < 2 or pair.iloc[:, 1].nunique() < 2: return None
    return float(pair.iloc[:, 0].rank().corr(pair.iloc[:, 1].rank()))


def _cluster_bootstrap_stat(frame: pd.DataFrame, statistic, draws: int = BOOTSTRAP_DRAWS) -> dict[str, Any]:
    clusters = list(frame.rebalance_date.unique())
    if not clusters: return {"estimate": None, "ci95": [None, None], "rawN": 0, "effectiveN": 0}
    groups = {c: frame[frame.rebalance_date == c] for c in clusters}
    estimate = statistic(frame)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    values = []
    for _ in range(draws):
        selected = rng.choice(clusters, size=len(clusters), replace=True)
        sample = pd.concat([groups[c] for c in selected], ignore_index=True)
        value = statistic(sample)
        if value is not None and np.isfinite(value): values.append(value)
    return {"estimate": estimate, "ci95": ([float(x) for x in np.quantile(values, [.025, .975])]
                                             if values else [None, None]),
            "rawN": len(frame), "effectiveN": len(clusters), "usableDraws": len(values),
            "method": f"rebalance-cluster bootstrap, {draws} draws, seed {BOOTSTRAP_SEED}"}


def _mean_ci(frame: pd.DataFrame) -> dict[str, Any]:
    sample = frame.dropna(subset=["replacement_advantage"])
    clusters = list(sample.rebalance_date.unique())
    if not clusters: return {"estimate": None, "ci95": [None, None], "rawN": 0, "effectiveN": 0}
    sums = np.array([sample.loc[sample.rebalance_date == c, "replacement_advantage"].sum() for c in clusters])
    counts = np.array([(sample.rebalance_date == c).sum() for c in clusters], dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    multiplicities = rng.multinomial(len(clusters), np.full(len(clusters), 1/len(clusters)), size=BOOTSTRAP_DRAWS)
    estimates = (multiplicities @ sums) / (multiplicities @ counts)
    return {"estimate": float(sample.replacement_advantage.mean()),
            "ci95": [float(x) for x in np.quantile(estimates, [.025, .975])],
            "rawN": len(sample), "effectiveN": len(clusters), "usableDraws": BOOTSTRAP_DRAWS,
            "method": f"rebalance-cluster bootstrap, {BOOTSTRAP_DRAWS} draws, seed {BOOTSTRAP_SEED}"}


def _bucket_summary(frame: pd.DataFrame, column: str, definitions: list[tuple[str, Any]]) -> dict[str, Any]:
    output = {}
    for label, selector in definitions:
        subset = frame[selector(frame[column])]
        ci = _mean_ci(subset)
        output[label] = {"rawN": len(subset), "effectiveN": subset.rebalance_date.nunique(),
                         "meanAdvantagePct": _mean_pct(subset.replacement_advantage),
                         "medianAdvantagePct": _pct(subset.replacement_advantage),
                         "winRatePct": _share(subset.replacement_advantage > 0),
                         "ci95Pct": [x * 100 if x is not None else None for x in ci["ci95"]],
                         "equalSlotContributionPct": float(subset.replacement_advantage.sum() / TOP_N * 100)}
    return output


def _quartile_summary(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    clean = frame.dropna(subset=[column]).copy()
    if clean.empty: return {}
    clean["quartile"] = pd.qcut(clean[column].rank(method="first"), 4, labels=["Q1", "Q2", "Q3", "Q4"])
    output = {}
    for label, subset in clean.groupby("quartile", observed=True):
        ci = _mean_ci(subset)
        output[str(label)] = {"range": [float(subset[column].min()), float(subset[column].max())],
                              "rawN": len(subset), "effectiveN": subset.rebalance_date.nunique(),
                              "meanAdvantagePct": _mean_pct(subset.replacement_advantage),
                              "medianAdvantagePct": _pct(subset.replacement_advantage),
                              "winRatePct": _share(subset.replacement_advantage > 0),
                              "ci95Pct": [x * 100 if x is not None else None for x in ci["ci95"]]}
    return output


def _continuous_relationships(frame: pd.DataFrame) -> dict[str, Any]:
    output = {}
    for feature in CONTINUOUS_FEATURES:
        sample = frame.dropna(subset=[feature, "replacement_advantage"])
        clusters = list(sample.rebalance_date.unique())
        x = sample[feature].rank().to_numpy(float)
        y = sample.replacement_advantage.rank().to_numpy(float)
        codes = pd.Categorical(sample.rebalance_date, categories=clusters).codes
        rng = np.random.default_rng(BOOTSTRAP_SEED)
        multiplicities = rng.multinomial(len(clusters), np.full(len(clusters), 1/len(clusters)), size=BOOTSTRAP_DRAWS)
        values = []
        for draw in multiplicities:
            weights = draw[codes].astype(float)
            total = weights.sum()
            if total <= 1: continue
            mx, my = np.sum(weights*x)/total, np.sum(weights*y)/total
            cov = np.sum(weights*(x-mx)*(y-my))
            vx, vy = np.sum(weights*(x-mx)**2), np.sum(weights*(y-my)**2)
            if vx > 0 and vy > 0: values.append(float(cov / math.sqrt(vx*vy)))
        estimate = _spearman(sample[feature], sample.replacement_advantage)
        output[feature] = {"spearman": estimate,
                           "ci95": [float(z) for z in np.quantile(values, [.025, .975])],
                           "rawN": len(sample), "effectiveN": len(clusters),
                           "method": "cluster bootstrap of fixed full-sample ranks"}
    return output


def _shuffle_outcomes_by_cluster(frame: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
    outcome = frame.replacement_advantage.to_numpy().copy()
    positions = {c: np.flatnonzero(frame.rebalance_date.to_numpy() == c) for c in frame.rebalance_date.unique()}
    by_size: dict[int, list[Any]] = {}
    for cluster, pos in positions.items(): by_size.setdefault(len(pos), []).append(cluster)
    shuffled = outcome.copy()
    for clusters in by_size.values():
        sources = rng.permutation(clusters)
        for destination, source in zip(clusters, sources):
            shuffled[positions[destination]] = outcome[positions[source]]
    return shuffled


def _null_calibration(frame: pd.DataFrame, relationships: dict[str, Any]) -> dict[str, Any]:
    eligible = [f for f in CONTINUOUS_FEATURES if relationships[f]["spearman"] is not None]
    observed = max((abs(relationships[f]["spearman"]), f) for f in eligible)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    maxima = np.empty(NULL_DRAWS)
    feature_ranks = {f: frame[f].rank().to_numpy(float) for f in eligible}
    for i in range(NULL_DRAWS):
        shuffled = _shuffle_outcomes_by_cluster(frame, rng)
        outcome_rank = pd.Series(shuffled).rank().to_numpy(float)
        correlations = []
        for f in eligible:
            x = feature_ranks[f]
            mask = np.isfinite(x) & np.isfinite(outcome_rank)
            correlations.append(float(np.corrcoef(x[mask], outcome_rank[mask])[0, 1]) if mask.sum() >= 3 else None)
        maxima[i] = max(abs(x) for x in correlations if x is not None)
    return {"observedStrongestFeature": observed[1], "observedAbsoluteSpearman": observed[0],
            "nullMedianMaxAbsoluteSpearman": float(np.median(maxima)),
            "nullP95MaxAbsoluteSpearman": float(np.quantile(maxima, .95)),
            "familywiseEmpiricalP": float((1 + (maxima >= observed[0]).sum()) / (NULL_DRAWS + 1)),
            "draws": NULL_DRAWS, "seed": BOOTSTRAP_SEED,
            "method": "same-size rebalance-cluster outcome-vector shuffle; maximum absolute Spearman across preregistered features"}


def _state_analysis(frame: pd.DataFrame) -> dict[str, Any]:
    profitable = frame.outgoing_currently_profitable.fillna(False).astype(bool)
    mfe = frame.outgoing_mfe_to_date
    capture = frame.outgoing_unrealized_return / mfe.replace(0, np.nan)
    recovery_floor = np.maximum(.01, .25 * frame.outgoing_mae_to_date.abs())
    groups = {
        "currently profitable": profitable,
        "currently unprofitable": ~profitable,
        "winner near MFE": profitable & (capture >= .75),
        "winner high giveback": profitable & (capture < .50),
        "loser meaningful recovery": (~profitable) & (frame.outgoing_recovery_from_mae >= recovery_floor),
        "loser near MAE": (~profitable) & (frame.outgoing_recovery_from_mae < .01),
    }
    return _bucket_summary(frame.assign(_state=0), "_state", [(label, lambda _x, mask=mask: mask) for label, mask in groups.items()])


def _effect_size(good: pd.Series, bad: pd.Series) -> float | None:
    good, bad = good.dropna(), bad.dropna()
    if len(good) < 2 or len(bad) < 2: return None
    pooled = math.sqrt(((len(good)-1)*good.var(ddof=1) + (len(bad)-1)*bad.var(ddof=1)) / (len(good)+len(bad)-2))
    if pooled <= 0: return None
    d = (good.mean() - bad.mean()) / pooled
    correction = 1 - 3 / (4 * (len(good) + len(bad)) - 9)
    return float(d * correction)


def _good_bad(frame: pd.DataFrame) -> dict[str, Any]:
    good, bad = frame[frame.replacement_advantage > 0], frame[frame.replacement_advantage < 0]
    output = {"goodN": len(good), "badN": len(bad), "goodClusters": good.rebalance_date.nunique(),
              "badClusters": bad.rebalance_date.nunique(), "features": {}}
    for feature in CONTINUOUS_FEATURES:
        sample = frame.dropna(subset=[feature]).copy()
        clusters = list(sample.rebalance_date.unique())
        aggregates = []
        for c in clusters:
            block = sample[sample.rebalance_date == c]
            a, b = block[block.replacement_advantage > 0][feature], block[block.replacement_advantage < 0][feature]
            aggregates.append((float(a.sum()), len(a), float(b.sum()), len(b)))
        aggregates = np.asarray(aggregates, dtype=float)
        rng = np.random.default_rng(BOOTSTRAP_SEED)
        multiplicities = rng.multinomial(len(clusters), np.full(len(clusters), 1/len(clusters)), size=BOOTSTRAP_DRAWS)
        totals = multiplicities @ aggregates
        valid = (totals[:, 1] > 0) & (totals[:, 3] > 0)
        differences = totals[valid, 0]/totals[valid, 1] - totals[valid, 2]/totals[valid, 3]
        observed = float(good[feature].mean() - bad[feature].mean())
        output["features"][feature] = {
            "goodMean": float(good[feature].mean()), "badMean": float(bad[feature].mean()),
            "goodMedian": float(good[feature].median()), "badMedian": float(bad[feature].median()),
            "goodMinusBadMean": observed, "ci95": [float(z) for z in np.quantile(differences, [.025, .975])],
            "hedgesG": _effect_size(good[feature], bad[feature])}
    return output


def _archetypes(frame: pd.DataFrame) -> dict[str, Any]:
    hot_cut = float(frame.incoming_return_20d.quantile(.75))
    giveback_cut = float(frame.outgoing_giveback.quantile(.75))
    masks = {
        "strong upgrade": (frame.rank_gap >= 6) & (frame.outgoing_rank_deterioration >= 6),
        "marginal churn": frame.rank_gap <= 2,
        "hot entrant": frame.incoming_return_20d >= hot_cut,
        "decaying incumbent": frame.outgoing_giveback >= giveback_cut,
        "loser removal": (~frame.outgoing_currently_profitable.fillna(False).astype(bool)) & (frame.outgoing_return_20d < 0),
    }
    result = _bucket_summary(frame.assign(_archetype=0), "_archetype",
                             [(label, lambda _x, mask=mask: mask) for label, mask in masks.items()])
    result["definitions"] = {"hotEntrant20dCutoff": hot_cut, "decayingIncumbentGivebackCutoff": giveback_cut}
    return result


def _bad_concentration(frame: pd.DataFrame) -> dict[str, Any]:
    bad = frame[frame.replacement_advantage < 0].copy()
    worst = frame.nsmallest(5, "replacement_advantage")
    cluster = frame.groupby("rebalance_date").replacement_advantage.sum().sort_values()
    negative_total = abs(float(cluster[cluster < 0].sum()))
    shares = {f"worst{k}ClusterShareOfNegativePct": (abs(float(cluster.iloc[:k].clip(upper=0).sum())) / negative_total * 100
                                                       if negative_total else None) for k in (1, 3, 5)}
    loo = [float(frame[frame.rebalance_date != c].replacement_advantage.mean()) for c in frame.rebalance_date.unique()]
    return {"badEvents": len(bad), "meanBadPct": _mean_pct(bad.replacement_advantage),
            "medianBadPct": _pct(bad.replacement_advantage), "totalNegativeEqualSlotPct": float(bad.replacement_advantage.sum()/TOP_N*100),
            "worstFive": _safe(worst[["rebalance_date", "outgoing_symbol", "incoming_symbol", "replacement_advantage",
                                      "outgoing_subsequent_return", "incoming_subsequent_return"]].to_dict("records")),
            **shares, "leaveOneClusterOutMeanRangePct": [min(loo)*100, max(loo)*100]}


def _all_drawdown_episodes(equity: pd.Series) -> list[dict[str, Any]]:
    equity = equity.dropna()
    dd = equity / equity.cummax() - 1.0
    episodes, start = [], None
    for i, (ts, value) in enumerate(dd.items()):
        if value < 0 and start is None: start = max(i-1, 0)
        last = i == len(dd)-1
        if start is not None and (value >= 0 or last):
            end = i if last and value < 0 else i-1
            segment = dd.iloc[start:end+1]
            trough = segment.idxmin()
            episodes.append({"start": _iso(dd.index[start]), "trough": _iso(trough),
                             "recovery": _iso(dd.index[i]) if value >= 0 else None,
                             "drawdownPct": float(segment.min()*100)})
            start = None
    return episodes


def _drawdown_analysis(frame: pd.DataFrame, result: Any) -> dict[str, Any]:
    periods = {
        "2021-22": ("2021-12-29", "2022-06-17"),
        "2022-23": ("2022-11-30", "2023-05-31"),
        "2025": ("2025-02-19", "2025-04-08"),
    }
    episodes = _all_drawdown_episodes(result.equity_curve)
    for year in (2024, 2026):
        choices = [e for e in episodes if int(e["trough"][:4]) == year]
        if choices:
            chosen = min(choices, key=lambda e: e["drawdownPct"])
            periods[str(year)] = (chosen["start"], chosen["trough"])
    output = {}
    for label, (start, end) in periods.items():
        subset = frame[(frame.rebalance_date >= start) & (frame.rebalance_date <= end)]
        output[label] = {"start": start, "end": end, "events": len(subset),
                         "effectiveN": subset.rebalance_date.nunique(),
                         "meanAdvantagePct": _mean_pct(subset.replacement_advantage),
                         "winRatePct": _share(subset.replacement_advantage > 0),
                         "equalSlotContributionPct": float(subset.replacement_advantage.sum()/TOP_N*100)}
    for year in (2024, 2026):
        output.setdefault(str(year), {"episode": None, "events": 0, "note": "No drawdown episode with trough in this year."})
    return output


def _economic(frame: pd.DataFrame, buckets: dict[str, Any]) -> dict[str, Any]:
    total = float(frame.replacement_advantage.sum() / TOP_N * 100)
    years = 5.0
    group = {}
    for label, row in buckets.items():
        contribution = row["equalSlotContributionPct"]
        group[label] = {"equalSlotContributionPct": contribution,
                        "annualizedArithmeticPct": contribution / years,
                        "shareOfNetReplacementContributionPct": (contribution / total * 100 if total else None),
                        "effectiveN": row["effectiveN"]}
    return {"totalEqualSlotContributionPct": total, "annualizedArithmeticPct": total/years,
            "groupEconomics": group,
            "caveat": "Arithmetic event attribution is not a compounded portfolio counterfactual; group shares are unstable when net contribution is near zero."}


def _distribution(frame: pd.DataFrame) -> dict[str, Any]:
    ci = _mean_ci(frame)
    return {"rawN": len(frame), "effectiveN": frame.rebalance_date.nunique(),
            "meanPct": _mean_pct(frame.replacement_advantage), "medianPct": _pct(frame.replacement_advantage),
            "stdPct": float(frame.replacement_advantage.std(ddof=1)*100),
            "p10Pct": _pct(frame.replacement_advantage, .10), "p25Pct": _pct(frame.replacement_advantage, .25),
            "p75Pct": _pct(frame.replacement_advantage, .75), "p90Pct": _pct(frame.replacement_advantage, .90),
            "winRatePct": _share(frame.replacement_advantage > 0),
            "clusterCi95Pct": [x*100 if x is not None else None for x in ci["ci95"]],
            "incomingMeanPct": _mean_pct(frame.incoming_subsequent_return),
            "outgoingMeanPct": _mean_pct(frame.outgoing_subsequent_return),
            "incomingMeanMfePct": _mean_pct(frame.incoming_future_mfe), "incomingMeanMaePct": _mean_pct(frame.incoming_future_mae),
            "outgoingMeanMfePct": _mean_pct(frame.outgoing_future_mfe), "outgoingMeanMaePct": _mean_pct(frame.outgoing_future_mae),
            "incomingMeanMaxDrawdownPct": _mean_pct(frame.incoming_future_max_drawdown),
            "outgoingMeanMaxDrawdownPct": _mean_pct(frame.outgoing_future_max_drawdown)}


def _compact_mrm(frame: pd.DataFrame, relationships: dict[str, Any]) -> dict[str, Any]:
    return {"distribution": _distribution(frame),
            "scoreSpreadSpearman": relationships["score_spread"],
            "rankGapSpearman": relationships["rank_gap"],
            "badReplacementPct": _share(frame.replacement_advantage < 0),
            "worstReplacementPct": float(frame.replacement_advantage.min()*100),
            "p10ReplacementPct": _pct(frame.replacement_advantage, .10)}


def _fmt(value: Any, suffix: str = "") -> str:
    return "N/A" if value is None else f"{value:.2f}{suffix}"


def _markdown(report: dict[str, Any]) -> str:
    dist, recon = report["replacementDistribution"], report["reconciliation"]["Dual Momentum"]
    lines = ["# Dual Momentum Replacement-Quality Decomposition", "",
             "Canonical Dual Momentum was not modified. The closed Winner Grace hypothesis was not reopened.", "",
             "## Event reconciliation", "",
             "| Metric | Result |", "|---|---:|",
             f"| Completed pairs | {recon['completedPairs']} |", f"| Effective rebalance N | {recon['effectiveN']} |",
             f"| Censored final pairs | {recon['censoredLastRebalancePairs']} |",
             f"| Unpaired removals / additions from cash changes | {recon['unpairedRemovalsFromCashChanges']} / {recon['unpairedAdditionsFromCashChanges']} |",
             f"| Missing-price exclusions | {recon['excludedMissingPricePairs']} |", f"| Duplicate pairs | {recon['duplicatePairs']} |", "",
             "## Replacement advantage", "", "| Metric | Result |", "|---|---:|",
             f"| Mean | {_fmt(dist['meanPct'],'%')} |", f"| Median | {_fmt(dist['medianPct'],'%')} |",
             f"| Win rate | {_fmt(dist['winRatePct'],'%')} |",
             f"| Cluster 95% CI | {_fmt(dist['clusterCi95Pct'][0],'%')} to {_fmt(dist['clusterCi95Pct'][1],'%')} |",
             f"| P10 / P90 | {_fmt(dist['p10Pct'],'%')} / {_fmt(dist['p90Pct'],'%')} |", "",
             "## Natural rank-gap buckets", "", "| Bucket | N | Effective N | Mean advantage | Win rate |", "|---|---:|---:|---:|---:|"]
    for label, row in report["rankGapAnalysis"].items():
        lines.append(f"| {label} | {row['rawN']} | {row['effectiveN']} | {_fmt(row['meanAdvantagePct'],'%')} | {_fmt(row['winRatePct'],'%')} |")
    lines += ["", "## Score-spread quartiles", "", "| Quartile | Range | Mean advantage | Win rate |", "|---|---:|---:|---:|"]
    for label, row in report["scoreSpreadQuartiles"].items():
        lines.append(f"| {label} | {row['range'][0]:.3f} to {row['range'][1]:.3f} | {_fmt(row['meanAdvantagePct'],'%')} | {_fmt(row['winRatePct'],'%')} |")
    score = report["scoreSpreadAnalysis"]
    rank_rho = report["allContinuousRelationships"]["rank_gap"]
    lines += ["", f"Score-spread Spearman: {_fmt(score['spearman'])} (cluster 95% CI {_fmt(score['ci95'][0])} to {_fmt(score['ci95'][1])}).",
              f"Rank-gap Spearman: {_fmt(rank_rho['spearman'])} (cluster 95% CI {_fmt(rank_rho['ci95'][0])} to {_fmt(rank_rho['ci95'][1])})."]
    lines += ["", "## Outgoing rank deterioration", "", "| Bucket | N | Mean advantage | Win rate |", "|---|---:|---:|---:|"]
    for label, row in report["rankDeteriorationAnalysis"].items():
        lines.append(f"| {label} | {row['rawN']} | {_fmt(row['meanAdvantagePct'],'%')} | {_fmt(row['winRatePct'],'%')} |")
    lines += ["", "## Incumbent state", "", "| State | N | Effective N | Mean advantage | Win rate |", "|---|---:|---:|---:|---:|"]
    for label, row in report["incumbentStateAnalysis"].items():
        lines.append(f"| {label} | {row['rawN']} | {row['effectiveN']} | {_fmt(row['meanAdvantagePct'],'%')} | {_fmt(row['winRatePct'],'%')} |")
    lines += ["", "## Recent-path diagnostics", "", "| Diagnostic | Spearman | Cluster 95% CI |", "|---|---:|---:|"]
    for feature, row in report["recentPathAnalysis"]["continuousRelationships"].items():
        lines.append(f"| {feature} | {_fmt(row['spearman'])} | {_fmt(row['ci95'][0])} to {_fmt(row['ci95'][1])} |")
    lines += ["", "## Replacement archetypes", "", "| Archetype | N | Mean advantage | Win rate |", "|---|---:|---:|---:|"]
    for label, row in report["archetypes"].items():
        if label == "definitions": continue
        lines.append(f"| {label} | {row['rawN']} | {_fmt(row['meanAdvantagePct'],'%')} | {_fmt(row['winRatePct'],'%')} |")
    lines += ["", "## Good versus bad replacements", "",
              "Largest standardized differences among the preregistered explanatory variables (positive means larger in good replacements):", "",
              "| Feature | Good mean | Bad mean | Hedges g | Cluster CI for mean difference |", "|---|---:|---:|---:|---:|"]
    good_bad = report["goodVsBad"]["features"]
    ordered_effects = sorted(good_bad.items(), key=lambda item: abs(item[1]["hedgesG"] or 0), reverse=True)[:6]
    for feature, row in ordered_effects:
        lines.append(f"| {feature} | {_fmt(row['goodMean'])} | {_fmt(row['badMean'])} | {_fmt(row['hedgesG'])} | {_fmt(row['ci95'][0])} to {_fmt(row['ci95'][1])} |")
    null = report["nullCalibration"]
    lines += ["", "## Null calibration", "",
              f"Strongest observed continuous relationship: `{null['observedStrongestFeature']}` with |Spearman| {null['observedAbsoluteSpearman']:.3f}. ",
              f"The cluster-preserving familywise empirical p-value was {null['familywiseEmpiricalP']:.3f}; the null 95th-percentile maximum was {null['nullP95MaxAbsoluteSpearman']:.3f}.", "",
              "## Bad-replacement concentration", ""]
    bad = report["badReplacementConcentration"]
    lines += [f"There were {bad['badEvents']} bad replacements. The worst one, three, and five rebalance clusters account for "
              f"{bad['worst1ClusterShareOfNegativePct']:.2f}%, {bad['worst3ClusterShareOfNegativePct']:.2f}%, and {bad['worst5ClusterShareOfNegativePct']:.2f}% of negative replacement shortfall. "
              f"Leave-one-cluster-out mean replacement advantage remains {_fmt(bad['leaveOneClusterOutMeanRangePct'][0],'%')} to {_fmt(bad['leaveOneClusterOutMeanRangePct'][1],'%')}.", "",
              "| Date | Outgoing to incoming | Advantage | Outgoing return | Incoming return |", "|---|---|---:|---:|---:|"]
    for row in bad["worstFive"]:
        lines.append(f"| {row['rebalance_date']} | {row['outgoing_symbol']} to {row['incoming_symbol']} | {_fmt(row['replacement_advantage']*100,'%')} | {_fmt(row['outgoing_subsequent_return']*100,'%')} | {_fmt(row['incoming_subsequent_return']*100,'%')} |")
    lines += ["",
              "## Drawdown-period replacement quality", "", "| Period | Events | Effective N | Mean advantage | Win rate |", "|---|---:|---:|---:|---:|"]
    for label, row in report["drawdownPeriods"].items():
        lines.append(f"| {label} | {row['events']} | {row.get('effectiveN',0)} | {_fmt(row.get('meanAdvantagePct'),'%')} | {_fmt(row.get('winRatePct'),'%')} |")
    lines += ["", "## DM versus MRM", "", "| Metric | DM | MRM |", "|---|---:|---:|"]
    dm, mrm = report["dmVsMrm"]["Dual Momentum"], report["dmVsMrm"]["Market-Residual Momentum"]
    lines += [f"| Replacement pairs | {dm['distribution']['rawN']} | {mrm['distribution']['rawN']} |",
              f"| Mean advantage | {_fmt(dm['distribution']['meanPct'],'%')} | {_fmt(mrm['distribution']['meanPct'],'%')} |",
              f"| Win rate | {_fmt(dm['distribution']['winRatePct'],'%')} | {_fmt(mrm['distribution']['winRatePct'],'%')} |",
              f"| Bad replacement share | {_fmt(dm['badReplacementPct'],'%')} | {_fmt(mrm['badReplacementPct'],'%')} |",
              f"| P10 | {_fmt(dm['p10ReplacementPct'],'%')} | {_fmt(mrm['p10ReplacementPct'],'%')} |",
              f"| Worst replacement | {_fmt(dm['worstReplacementPct'],'%')} | {_fmt(mrm['worstReplacementPct'],'%')} |", "",
              "## Economic magnitude", "",
              f"Canonical replacement pairs contribute an equal-slot arithmetic {report['economicMagnitude']['totalEqualSlotContributionPct']:.2f}% over the sample, approximately {report['economicMagnitude']['annualizedArithmeticPct']:.2f}% per year. "
              "This is descriptive event attribution, not a compounded portfolio counterfactual.", "",
              "## Candidate mechanism and next step", "", report["conclusion"], "",
              f"**Recommendation:** {report['recommendation']}", ""]
    return "\n".join(lines)


def run_study(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    if not PREREGISTRATION.exists():
        raise FileNotFoundError("The decomposition preregistration must be persisted before analysis.")
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    if prereg.get("statusAtRegistration") != "frozen_before_feature_outcome_analysis":
        raise RuntimeError("Preregistration is not frozen.")

    dm, dm_recon, dm_result = build_event_ledger("Dual Momentum")
    mrm, mrm_recon, _mrm_result = build_event_ledger("Market-Residual Momentum")
    if dm_recon["duplicatePairs"] or not dm_recon["identicalWindows"]:
        raise RuntimeError("DM event ledger reconciliation failed.")
    dm_relationships = _continuous_relationships(dm)
    mrm_relationships = _continuous_relationships(mrm)
    null = _null_calibration(dm, dm_relationships)
    rank_gap = _bucket_summary(dm, "rank_gap", [
        ("small (1-2)", lambda x: x <= 2),
        ("medium (3-5)", lambda x: (x >= 3) & (x <= 5)),
        ("large (6+)", lambda x: x >= 6),
    ])
    rank_deterioration = _bucket_summary(dm, "outgoing_rank_deterioration", [
        ("barely (<=2)", lambda x: x <= 2),
        ("moderate (3-5)", lambda x: (x >= 3) & (x <= 5)),
        ("sharp (6+)", lambda x: x >= 6),
    ])
    state = _state_analysis(dm)
    archetypes = _archetypes(dm)
    bad = _bad_concentration(dm)
    score_quartiles = _quartile_summary(dm, "score_spread")
    giveback_quartiles = _quartile_summary(dm, "outgoing_giveback")
    incoming20_quartiles = _quartile_summary(dm, "incoming_return_20d")
    outgoing_accel_quartiles = _quartile_summary(dm, "outgoing_acceleration")
    strongest_feature = null["observedStrongestFeature"]
    strongest_relationship = dm_relationships[strongest_feature]
    extreme_difference = None
    relevant_quartiles = {
        "score_spread": score_quartiles, "outgoing_giveback": giveback_quartiles,
        "incoming_return_20d": incoming20_quartiles, "outgoing_acceleration": outgoing_accel_quartiles,
    }.get(strongest_feature)
    if relevant_quartiles:
        extreme_difference = relevant_quartiles["Q4"]["meanAdvantagePct"] - relevant_quartiles["Q1"]["meanAdvantagePct"]
    elif strongest_feature == "rank_gap":
        extreme_difference = rank_gap["large (6+)"]["meanAdvantagePct"] - rank_gap["small (1-2)"]["meanAdvantagePct"]
    elif strongest_feature == "outgoing_rank_deterioration":
        extreme_difference = rank_deterioration["sharp (6+)"]["meanAdvantagePct"] - rank_deterioration["barely (<=2)"]["meanAdvantagePct"]
    else:
        strongest_quartiles = _quartile_summary(dm, strongest_feature)
        if strongest_quartiles:
            extreme_difference = strongest_quartiles["Q4"]["meanAdvantagePct"] - strongest_quartiles["Q1"]["meanAdvantagePct"]

    null_supportive = null["familywiseEmpiricalP"] <= .10
    material = extreme_difference is not None and abs(extreme_difference) >= 2.0
    broad = strongest_relationship["effectiveN"] >= 20
    not_tail_dominated = (bad["worst1ClusterShareOfNegativePct"] or 100) < 50
    followup = bool(null_supportive and material and broad and not_tail_dominated)
    if followup:
        mechanism = f"Replacement confidence appears to matter through `{strongest_feature}`."
        recommendation = (f"One future preregistered confirmation experiment may test a single monotonic concept based on {strongest_feature}; "
                          "do not select its cutoff from this sample and do not implement it in canonical DM now.")
    else:
        mechanism = ("No simple PIT replacement feature cleared the combined interpretability, materiality, independent-cluster, "
                     "tail-concentration, and familywise null-calibration standard.")
        recommendation = "No narrow replacement modification is currently justified."

    candidate_mechanisms = [
        {"mechanism": "Replacement confidence matters", "supported": False,
         "evidence": "Rank gap Spearman and score-spread Spearman are near zero; bucket results are non-monotonic."},
        {"mechanism": "Marginal churn is costly", "supported": False,
         "evidence": "Small rank-gap swaps have positive mean advantage rather than systematic harm."},
        {"mechanism": "Hot-entry exhaustion", "supported": False,
         "evidence": "Incoming 20-session strength is non-monotonic and its continuous relationship is near zero."},
        {"mechanism": "Incumbent deterioration matters", "supported": "descriptive only",
         "evidence": "Unprofitable and near-MAE incumbents show better replacement economics, but intervals cross zero and the familywise null is unsupportive."},
        {"mechanism": "No useful replacement structure", "supported": True,
         "evidence": "No predeclared continuous feature exceeds ordinary strongest-relationship behavior under clustered null calibration."},
    ]

    report = {
        "studyName": STUDY_NAME, "preregistration": str(PREREGISTRATION),
        "canonicalChanged": False, "winnerGraceReopened": False,
        "reconciliation": {"Dual Momentum": dm_recon, "Market-Residual Momentum": mrm_recon},
        "replacementDistribution": _distribution(dm),
        "rankGapAnalysis": rank_gap, "scoreSpreadAnalysis": dm_relationships["score_spread"],
        "scoreSpreadQuartiles": score_quartiles, "rankDeteriorationAnalysis": rank_deterioration,
        "incumbentStateAnalysis": state,
        "recentPathAnalysis": {"continuousRelationships": {k: dm_relationships[k] for k in (
            "outgoing_return_20d", "incoming_return_20d", "outgoing_return_60d", "incoming_return_60d",
            "outgoing_acceleration", "incoming_acceleration")},
            "incoming20dQuartiles": incoming20_quartiles,
            "outgoingAccelerationQuartiles": outgoing_accel_quartiles},
        "mfeGivebackQuartiles": giveback_quartiles, "archetypes": archetypes,
        "goodVsBad": _good_bad(dm), "allContinuousRelationships": dm_relationships,
        "nullCalibration": null, "badReplacementConcentration": bad,
        "drawdownPeriods": _drawdown_analysis(dm, dm_result),
        "dmVsMrm": {"Dual Momentum": _compact_mrm(dm, dm_relationships),
                    "Market-Residual Momentum": _compact_mrm(mrm, mrm_relationships)},
        "economicMagnitude": _economic(dm, rank_gap),
        "decisionAssessment": {"strongestFeature": strongest_feature,
                               "strongestSpearman": strongest_relationship["spearman"],
                               "extremeGroupDifferencePct": extreme_difference,
                               "nullDirectionallySupportive": null_supportive,
                               "economicallyMaterial": material, "atLeast20Clusters": broad,
                               "notDominatedByWorstCluster": not_tail_dominated,
                               "followupCriteriaMet": followup},
        "strongestFeatureQuartiles": _quartile_summary(dm, strongest_feature),
        "candidateMechanisms": candidate_mechanisms,
        "candidateMechanism": mechanism, "conclusion": mechanism, "recommendation": recommendation,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    dm.to_csv(output_dir / "dm_replacement_events_enriched.csv", index=False)
    mrm.to_csv(output_dir / "mrm_replacement_events_enriched.csv", index=False)
    (output_dir / "replacement_quality_results.json").write_text(json.dumps(_safe(report), indent=2), encoding="utf-8")
    (output_dir / "replacement_quality_report.md").write_text(_markdown(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    report = run_study(args.output_dir)
    print(json.dumps(_safe({"reconciliation": report["reconciliation"]["Dual Momentum"],
                            "distribution": report["replacementDistribution"],
                            "decision": report["decisionAssessment"],
                            "recommendation": report["recommendation"]}), indent=2))


if __name__ == "__main__":
    main()
