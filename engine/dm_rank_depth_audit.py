"""Preregistered diagnostic of forward return by canonical DM/MRM rank.

This module reconstructs the full eligible ranking at each unchanged canonical
rebalance.  It never constructs or executes an alternative top-N portfolio.
Definitions live in research/dm_rank_depth_audit_preregistration.json.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from engine import data as data_module
from engine.runner import run_cross_sectional
from engine.trade_lifecycle_audit import _safe, _score_snapshot
from strategies.registry import build_cross_sectional_strategy


OUTPUT_DIR = Path("reports/dm_rank_depth_audit")
PREREGISTRATION = Path("research/dm_rank_depth_audit_preregistration.json")
PRIMARY_RANKS = tuple(range(1, 11))
GROUPS: dict[str, tuple[int, ...]] = {
    "elite": (1, 2),
    "upper": (3,),
    "lowerSelected": (4, 5),
    "nearMisses": (6, 7, 8, 9, 10),
    "top3": (1, 2, 3),
    "ranks3To5": (3, 4, 5),
    "top5": (1, 2, 3, 4, 5),
}
COMPARISONS = {
    "top3VsRanks4To5": (GROUPS["top3"], GROUPS["lowerSelected"]),
    "ranks1To2VsRanks3To5": (GROUPS["elite"], GROUPS["ranks3To5"]),
    "top5VsRanks6To10": (GROUPS["top5"], GROUPS["nearMisses"]),
}
CUMULATIVE_DEPTHS = (1, 2, 3, 4, 5, 6, 10)
BOOTSTRAP_DRAWS = 2_000
NULL_DRAWS = 2_000
SEED = 20260822
MATERIAL_H1 = 0.01
MAJOR_DM_DRAWDOWNS = (
    ("2021-12-29", "2022-06-17"),
    ("2022-11-30", "2023-05-31"),
    ("2025-02-19", "2025-04-08"),
)


def _json(value: Any) -> Any:
    value = _safe(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _strategy(name: str, result: Any) -> Any:
    benchmark = None
    if name == "Market-Residual Momentum":
        benchmark = data_module.get_bars(
            "SPY", "1d", result.start - timedelta(days=430), result.end,
        )
    return build_cross_sectional_strategy(
        name, risk_free_rate=result.risk_free_rate, benchmark_bars=benchmark,
    )


def _row_on(frame: pd.DataFrame, day: pd.Timestamp) -> pd.Series | None:
    """Return the exact session row; never substitute a later price."""
    rows = frame.loc[frame.index.date == day.date()]
    return rows.iloc[0] if len(rows) else None


def _forward_path(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, float, float] | None:
    entry, exit_ = _row_on(frame, start), _row_on(frame, end)
    if entry is None or exit_ is None or not np.isfinite(entry.Open) or not np.isfinite(exit_.Open) or entry.Open <= 0:
        return None
    window = frame.loc[(frame.index.date >= start.date()) & (frame.index.date < end.date())]
    if window.empty:
        return None
    base = float(entry.Open)
    forward = float(exit_.Open / base - 1.0)
    mfe = float(max(window.High.max(), exit_.Open) / base - 1.0)
    mae = float(min(window.Low.min(), exit_.Open) / base - 1.0)
    return forward, mfe, mae


def build_rank_ledger(name: str, result: Any, spy_bars: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Reconstruct every score/rank and attach only next-rebalance outcomes."""
    bars = result.validation_bars or {}
    membership = result.membership_at_runtime
    strategy = _strategy(name, result)
    rebalances = result.rebalances.sort_values("date").reset_index(drop=True)
    rows: list[dict[str, Any]] = []
    selected_matches = 0
    complete_periods = 0
    missing_prices: list[dict[str, str]] = []
    for i in range(len(rebalances) - 1):
        day = pd.Timestamp(rebalances.iloc[i].date)
        next_day = pd.Timestamp(rebalances.iloc[i + 1].date)
        eligible = membership(day.date()) if membership else set(bars)
        scores = _score_snapshot(name, strategy, bars, day, eligible)
        if scores.empty:
            continue
        actual = set(dict(rebalances.iloc[i].holdings))
        if name == "Dual Momentum":
            predicted = set(scores.loc[scores.qualifies].head(strategy.top_n).index)
        else:
            predicted = set(scores.head(strategy.top_n).index)
        selected_matches += int(actual == predicted)
        complete_periods += 1
        spy_path = _forward_path(spy_bars, day, next_day)
        spy_return = spy_path[0] if spy_path else np.nan
        period_rows: list[dict[str, Any]] = []
        universe_size = len(scores)
        for symbol, score_row in scores.iterrows():
            path = _forward_path(bars[symbol], day, next_day) if symbol in bars else None
            if path is None:
                missing_prices.append({"rebalanceDate": day.date().isoformat(), "symbol": symbol})
                continue
            rank = int(score_row["rank"])
            forward, mfe, mae = path
            period_rows.append({
                "strategy": name,
                "rebalance_date": day,
                "next_rebalance_date": next_day,
                "symbol": symbol,
                "rank": rank,
                "score": float(score_row["score"]),
                "qualifies_absolute_filter": bool(score_row["qualifies"]),
                "selected_by_canonical": symbol in actual,
                "universe_size": universe_size,
                "rank_percentile_from_top": (rank - 1) / max(universe_size - 1, 1),
                "forward_return": forward,
                "forward_mfe": mfe,
                "forward_mae": mae,
                "spy_forward_return": spy_return,
            })
        if not period_rows:
            continue
        returns = pd.Series([x["forward_return"] for x in period_rows])
        median = float(returns.median())
        q75 = float(returns.quantile(.75))
        for row in period_rows:
            row["universe_median_forward_return"] = median
            row["universe_top_quartile_threshold"] = q75
            row["beat_spy"] = bool(row["forward_return"] > spy_return) if np.isfinite(spy_return) else None
            row["beat_universe_median"] = row["forward_return"] > median
            row["future_top_quartile"] = row["forward_return"] >= q75
        rows.extend(period_rows)
    reconciliation = {
        "canonicalRebalances": len(rebalances),
        "completeForwardPeriods": complete_periods,
        "selectedSetMatches": selected_matches,
        "selectedSetsMatchCanonical": selected_matches == complete_periods,
        "missingOutcomePrices": missing_prices,
        "informationBoundary": "scores use closes strictly before decision; outcomes use exact rebalance opens",
    }
    return pd.DataFrame(rows), reconciliation


def _downside_deviation(values: pd.Series) -> float | None:
    clean = values.dropna().to_numpy(float)
    downside = clean[clean < 0]
    return float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0 if len(clean) else None


def _performance(values: pd.DataFrame) -> dict[str, Any]:
    r = values.forward_return.dropna()
    winners, losers = r[r > 0], r[r <= 0]
    return {
        "n": len(r),
        "meanReturn": float(r.mean()) if len(r) else None,
        "medianReturn": float(r.median()) if len(r) else None,
        "winRate": float((r > 0).mean()) if len(r) else None,
        "volatility": float(r.std(ddof=1)) if len(r) > 1 else None,
        "downsideDeviation": _downside_deviation(r),
        "worstReturn": float(r.min()) if len(r) else None,
        "bestReturn": float(r.max()) if len(r) else None,
        "p10": float(r.quantile(.10)) if len(r) else None,
        "p90": float(r.quantile(.90)) if len(r) else None,
        "beatSpy": float(values.beat_spy.dropna().mean()) if values.beat_spy.notna().any() else None,
        "beatUniverseMedian": float(values.beat_universe_median.mean()) if len(r) else None,
        "futureTopQuartile": float(values.future_top_quartile.mean()) if len(r) else None,
        "averageWinner": float(winners.mean()) if len(winners) else None,
        "averageLoser": float(losers.mean()) if len(losers) else None,
        "expectancy": float(r.mean()) if len(r) else None,
        "averageMfe": float(values.forward_mfe.mean()) if len(r) else None,
        "averageMae": float(values.forward_mae.mean()) if len(r) else None,
    }


def rank_table(ledger: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {"rank": rank, **_performance(ledger.loc[ledger["rank"] == rank])}
        for rank in PRIMARY_RANKS
    ]


def group_table(ledger: pd.DataFrame) -> dict[str, dict[str, Any]]:
    return {
        name: _performance(ledger.loc[ledger["rank"].isin(ranks)])
        for name, ranks in GROUPS.items()
    }


def _period_group_means(ledger: pd.DataFrame, ranks: Iterable[int]) -> pd.Series:
    return ledger.loc[ledger["rank"].isin(tuple(ranks))].groupby("rebalance_date").forward_return.mean()


def compare_groups(
    ledger: pd.DataFrame,
    left_ranks: Iterable[int],
    right_ranks: Iterable[int],
    *,
    draws: int = BOOTSTRAP_DRAWS,
    seed: int = SEED,
) -> dict[str, Any]:
    left = ledger.loc[ledger["rank"].isin(tuple(left_ranks))]
    right = ledger.loc[ledger["rank"].isin(tuple(right_ranks))]
    paired = pd.concat(
        [_period_group_means(ledger, left_ranks).rename("left"),
         _period_group_means(ledger, right_ranks).rename("right")], axis=1,
    ).dropna()
    differences = paired.left - paired.right
    rng = np.random.default_rng(seed)
    boot = np.array([
        float(rng.choice(differences, len(differences), replace=True).mean())
        for _ in range(draws)
    ]) if len(differences) else np.array([])
    effect = float(differences.mean() / differences.std(ddof=1)) if len(differences) > 1 and differences.std(ddof=1) else None
    return {
        "leftRanks": list(left_ranks),
        "rightRanks": list(right_ranks),
        "effectiveN": len(differences),
        "meanReturnDifference": float(left.forward_return.mean() - right.forward_return.mean()),
        "medianReturnDifference": float(left.forward_return.median() - right.forward_return.median()),
        "winRateDifference": float((left.forward_return > 0).mean() - (right.forward_return > 0).mean()),
        "volatilityDifference": float(left.forward_return.std() - right.forward_return.std()),
        "downsideDeviationDifference": float(_downside_deviation(left.forward_return) - _downside_deviation(right.forward_return)),
        "p10Difference": float(left.forward_return.quantile(.10) - right.forward_return.quantile(.10)),
        "beatSpyDifference": float(left.beat_spy.dropna().mean() - right.beat_spy.dropna().mean()),
        "pairedMonthlyMeanDifference": float(differences.mean()) if len(differences) else None,
        "clusterCi95": [float(x) for x in np.quantile(boot, [.025, .975])] if len(boot) else [None, None],
        "pairedStandardizedEffect": effect,
        "method": f"rebalance-cluster bootstrap, {draws} draws, seed {seed}",
    }


def _rank_corr(frame: pd.DataFrame) -> float:
    sample = frame[["rank", "forward_return"]].dropna()
    return float(sample["rank"].corr(sample["forward_return"], method="spearman")) if len(sample) > 2 else np.nan


def rank_inference(
    ledger: pd.DataFrame, *, draws: int = BOOTSTRAP_DRAWS, seed: int = SEED,
) -> dict[str, Any]:
    sample = ledger.loc[ledger["rank"].isin(PRIMARY_RANKS)]
    dates = list(sample.rebalance_date.unique())
    grouped = {d: sample.loc[sample.rebalance_date == d] for d in dates}
    rng = np.random.default_rng(seed)
    boot = []
    for _ in range(draws):
        chosen = rng.choice(dates, len(dates), replace=True)
        draw = pd.concat([grouped[d] for d in chosen], ignore_index=True)
        boot.append(_rank_corr(draw))
    means = sample.groupby("rank").forward_return.mean().reindex(PRIMARY_RANKS)
    monotone_steps = int((means.diff().dropna() < 0).sum())
    return {
        "spearman": _rank_corr(sample),
        "clusterCi95": [float(x) for x in np.nanquantile(boot, [.025, .975])],
        "effectiveN": len(dates),
        "rawN": len(sample),
        "decreasingAdjacentSteps": monotone_steps,
        "totalAdjacentSteps": len(PRIMARY_RANKS) - 1,
        "strictlyMonotonicMeanCurve": monotone_steps == len(PRIMARY_RANKS) - 1,
        "method": f"rebalance-cluster bootstrap, {draws} draws, seed {seed}",
    }


def shuffled_rank_null(
    ledger: pd.DataFrame, *, draws: int = NULL_DRAWS, seed: int = SEED,
) -> dict[str, Any]:
    sample = ledger.loc[ledger["rank"].isin(PRIMARY_RANKS), ["rebalance_date", "rank", "forward_return"]].copy()
    clusters = [g.copy() for _, g in sample.groupby("rebalance_date")]
    observed_h1 = compare_groups(sample.assign(
        beat_spy=False, forward_mfe=np.nan, forward_mae=np.nan,
    ), GROUPS["top3"], GROUPS["lowerSelected"], draws=100, seed=seed)["meanReturnDifference"]
    observed_h2 = compare_groups(sample.assign(
        beat_spy=False, forward_mfe=np.nan, forward_mae=np.nan,
    ), GROUPS["top5"], GROUPS["nearMisses"], draws=100, seed=seed)["meanReturnDifference"]
    observed_rho = _rank_corr(sample)
    rng = np.random.default_rng(seed)
    null_h1, null_h2, null_rho = np.empty(draws), np.empty(draws), np.empty(draws)
    for i in range(draws):
        pieces = []
        for cluster in clusters:
            shuffled = cluster.copy()
            shuffled["rank"] = rng.permutation(cluster["rank"].to_numpy())
            pieces.append(shuffled)
        permuted = pd.concat(pieces, ignore_index=True)
        null_h1[i] = permuted.loc[permuted["rank"].isin(GROUPS["top3"]), "forward_return"].mean() - permuted.loc[permuted["rank"].isin(GROUPS["lowerSelected"]), "forward_return"].mean()
        null_h2[i] = permuted.loc[permuted["rank"].isin(GROUPS["top5"]), "forward_return"].mean() - permuted.loc[permuted["rank"].isin(GROUPS["nearMisses"]), "forward_return"].mean()
        null_rho[i] = _rank_corr(permuted)

    def result(observed: float, null: np.ndarray, direction: str) -> dict[str, Any]:
        directional = (
            (1 + int((null >= observed).sum())) / (len(null) + 1)
            if direction == "greater" else
            (1 + int((null <= observed).sum())) / (len(null) + 1)
        )
        return {
            "observed": observed,
            "nullMean": float(null.mean()),
            "nullCi95": [float(x) for x in np.quantile(null, [.025, .975])],
            "directionalEmpiricalP": directional,
            "twoSidedEmpiricalP": (1 + int((np.abs(null) >= abs(observed)).sum())) / (len(null) + 1),
        }
    return {
        "H1Top3MinusRanks4To5": result(observed_h1, null_h1, "greater"),
        "H2Top5MinusRanks6To10": result(observed_h2, null_h2, "greater"),
        "H3RankReturnSpearman": result(observed_rho, null_rho, "less"),
        "draws": draws,
        "seed": seed,
        "method": "rank labels shuffled within each rebalance; future-return set preserved",
    }


def cumulative_depth(ledger: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for depth in CUMULATIVE_DEPTHS:
        basket = ledger.loc[ledger["rank"] <= depth, "forward_return"]
        added = ledger.loc[ledger["rank"] == depth, "forward_return"]
        prior = ledger.loc[ledger["rank"] < depth, "forward_return"]
        rows.append({
            "depth": depth,
            "n": len(basket),
            "meanReturn": float(basket.mean()),
            "newRankMeanReturn": float(added.mean()),
            "marginalVsHigherRanks": None if depth == 1 else float(added.mean() - prior.mean()),
        })
    return rows


def score_spacing(ledger: pd.DataFrame) -> list[dict[str, Any]]:
    top = ledger.loc[ledger["rank"] <= 6]
    pivot_score = top.pivot(index="rebalance_date", columns="rank", values="score")
    pivot_return = top.pivot(index="rebalance_date", columns="rank", values="forward_return")
    output = []
    for rank in range(1, 6):
        frame = pd.DataFrame({
            "gap": pivot_score[rank] - pivot_score[rank + 1],
            "returnDifference": pivot_return[rank] - pivot_return[rank + 1],
        }).dropna()
        median_gap = float(frame.gap.median())
        output.append({
            "boundary": f"{rank}-{rank + 1}",
            "n": len(frame),
            "meanScoreGap": float(frame.gap.mean()),
            "medianScoreGap": median_gap,
            "gapReturnDifferenceSpearman": float(frame.gap.corr(frame.returnDifference, method="spearman")),
            "meanReturnDifferenceSmallGap": float(frame.loc[frame.gap <= median_gap, "returnDifference"].mean()),
            "meanReturnDifferenceLargeGap": float(frame.loc[frame.gap > median_gap, "returnDifference"].mean()),
        })
    return output


def percentile_table(ledger: pd.DataFrame) -> list[dict[str, Any]]:
    definitions = (
        ("top10pct", 0.0, 0.10),
        ("pct10To25", 0.10, 0.25),
        ("pct25To50", 0.25, 0.50),
        ("bottom50pct", 0.50, 1.0000001),
    )
    return [
        {"group": name, "lower": low, "upper": high,
         **_performance(ledger.loc[(ledger.rank_percentile_from_top >= low) & (ledger.rank_percentile_from_top < high)])}
        for name, low, high in definitions
    ]


def stability_analysis(ledger: pd.DataFrame) -> dict[str, Any]:
    dates = sorted(ledger.rebalance_date.unique())
    half = dates[len(dates) // 2]

    def h1(frame: pd.DataFrame) -> float:
        paired = pd.concat([
            _period_group_means(frame, GROUPS["top3"]).rename("top3"),
            _period_group_means(frame, GROUPS["lowerSelected"]).rename("lower"),
        ], axis=1).dropna()
        return float((paired.top3 - paired.lower).mean())

    years = sorted(ledger.rebalance_date.dt.year.unique())
    full = h1(ledger)
    loso = {symbol: h1(ledger.loc[ledger.symbol != symbol]) for symbol in sorted(ledger.symbol.unique())}
    largest_influence = max(loso, key=lambda s: abs(loso[s] - full)) if loso else None
    return {
        "splitDate": pd.Timestamp(half).date().isoformat(),
        "firstHalfTop3MinusRanks4To5": h1(ledger.loc[ledger.rebalance_date < half]),
        "secondHalfTop3MinusRanks4To5": h1(ledger.loc[ledger.rebalance_date >= half]),
        "yearlyTop3MinusRanks4To5": {str(y): h1(ledger.loc[ledger.rebalance_date.dt.year == y]) for y in years},
        "leaveOneYearOutTop3MinusRanks4To5": {str(y): h1(ledger.loc[ledger.rebalance_date.dt.year != y]) for y in years},
        "leaveOneSecurityOutTop3MinusRanks4To5": loso,
        "largestInfluenceSecurity": largest_influence,
        "largestInfluenceResult": loso.get(largest_influence) if largest_influence else None,
        "allLeaveOneYearOutPositive": all(h1(ledger.loc[ledger.rebalance_date.dt.year != y]) > 0 for y in years),
        "allLeaveOneSecurityOutPositive": all(x > 0 for x in loso.values()),
    }


def security_concentration(ledger: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    primary = ledger.loc[ledger["rank"] <= 10].copy()
    primary["rankGroup"] = np.select(
        [primary["rank"].isin(GROUPS["top3"]), primary["rank"].isin(GROUPS["lowerSelected"]), primary["rank"].isin(GROUPS["nearMisses"])],
        ["top3", "ranks4To5", "ranks6To10"], default="other",
    )
    grouped = primary.groupby(["rankGroup", "symbol"]).forward_return.agg(["size", "mean", "sum"]).reset_index()
    grouped.columns = ["rankGroup", "symbol", "n", "meanReturn", "sumReturn"]
    by_rank = primary.groupby(["rank", "symbol"]).forward_return.agg(["size", "mean", "sum"]).reset_index()
    by_rank.columns = ["rank", "symbol", "n", "meanReturn", "sumReturn"]
    return {
        "byGroup": grouped.sort_values(["rankGroup", "sumReturn"], ascending=[True, False]).to_dict("records"),
        "byRank": by_rank.sort_values(["rank", "sumReturn"], ascending=[True, False]).to_dict("records"),
    }


def extreme_winner_analysis(ledger: pd.DataFrame) -> dict[str, Any]:
    output = {}
    trimmed_means = {}
    for name, ranks in {"top3": GROUPS["top3"], "ranks4To5": GROUPS["lowerSelected"]}.items():
        r = ledger.loc[ledger["rank"].isin(ranks), "forward_return"].sort_values(ascending=False)
        positive_total = float(r[r > 0].sum())
        top1n = max(1, math.ceil(.01 * len(r)))
        output[name] = {
            "n": len(r),
            "top1PctObservationCount": top1n,
            "top1PctSharePositiveReturnSum": float(r.head(top1n).sum() / positive_total) if positive_total else None,
            "top5SharePositiveReturnSum": float(r.head(5).sum() / positive_total) if positive_total else None,
            "top10SharePositiveReturnSum": float(r.head(10).sum() / positive_total) if positive_total else None,
            "meanAfterRemovingTop1Pct": float(r.iloc[top1n:].mean()),
            "meanAfterRemovingTop5": float(r.iloc[5:].mean()),
            "largestObservations": [float(x) for x in r.head(10)],
        }
        trimmed_means[name] = output[name]["meanAfterRemovingTop1Pct"]
    output["top1PctTrimmedTop3MinusRanks4To5"] = trimmed_means["top3"] - trimmed_means["ranks4To5"]
    return output


def drawdown_behavior(ledger: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    timezone = getattr(ledger.rebalance_date.dt, "tz", None)
    for start, end in MAJOR_DM_DRAWDOWNS:
        start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
        if timezone is not None:
            start_ts = start_ts.tz_localize(timezone)
            end_ts = end_ts.tz_localize(timezone)
        sample = ledger.loc[(ledger.next_rebalance_date > start_ts) & (ledger.rebalance_date <= end_ts)]
        rows.append({
            "start": start,
            "end": end,
            "rebalancePeriods": int(sample.rebalance_date.nunique()),
            "top3MeanReturn": float(sample.loc[sample["rank"].isin(GROUPS["top3"]), "forward_return"].mean()),
            "ranks4To5MeanReturn": float(sample.loc[sample["rank"].isin(GROUPS["lowerSelected"]), "forward_return"].mean()),
            "ranks6To10MeanReturn": float(sample.loc[sample["rank"].isin(GROUPS["nearMisses"]), "forward_return"].mean()),
        })
    return rows


def concentration_implication(ledger: pd.DataFrame) -> dict[str, Any]:
    pivot = ledger.loc[ledger["rank"] <= 5].pivot(index="rebalance_date", columns="rank", values="forward_return")
    top3 = pivot[[1, 2, 3]].mean(axis=1)
    lower = pivot[[4, 5]].mean(axis=1)
    corr = pivot[[1, 2, 3]].corr().to_numpy()
    triangle = corr[np.triu_indices(3, 1)]
    return {
        "descriptiveOnlyNotPortfolioBacktest": True,
        "top3PeriodReturnVolatilityAnnualizedSqrt12": float(top3.std() * np.sqrt(12)),
        "ranks4To5PeriodReturnVolatilityAnnualizedSqrt12": float(lower.std() * np.sqrt(12)),
        "top3AverageRankOutcomeCorrelation": float(triangle.mean()),
        "top3WorstSingleNamePeriod": float(ledger.loc[ledger["rank"] <= 3, "forward_return"].min()),
        "top3P10SingleNamePeriod": float(ledger.loc[ledger["rank"] <= 3, "forward_return"].quantile(.10)),
        "interpretation": "Concentration is justified only if the return advantage is stable enough to compensate for fewer names and larger single-name exposure.",
    }


def _classify(summary: dict[str, Any]) -> dict[str, Any]:
    h1 = summary["comparisons"]["top3VsRanks4To5"]
    stability = summary["stability"]
    extreme = summary["extremeWinnerConcentration"]
    rank_inf = summary["rankInference"]
    strong = bool(
        h1["meanReturnDifference"] >= MATERIAL_H1
        and h1["clusterCi95"][0] > 0
        and stability["firstHalfTop3MinusRanks4To5"] > 0
        and stability["secondHalfTop3MinusRanks4To5"] > 0
        and stability["allLeaveOneYearOutPositive"]
        and stability["allLeaveOneSecurityOutPositive"]
        and extreme["top1PctTrimmedTop3MinusRanks4To5"] > 0
    )
    h2 = summary["comparisons"]["top5VsRanks6To10"]
    labels = []
    if strong:
        labels.append("Strong evidence of top-rank edge concentration")
    elif h1["meanReturnDifference"] > 0:
        labels.append("Weak/descriptive edge concentration")
    else:
        labels.append("No rank-depth edge")
    if h2["meanReturnDifference"] <= 0 or h2["clusterCi95"][0] <= 0 <= h2["clusterCi95"][1]:
        labels.append("Top-5 boundary unsupported")
    if not rank_inf["strictlyMonotonicMeanCurve"] and not (
        rank_inf["spearman"] < 0 and rank_inf["clusterCi95"][1] < 0
    ):
        labels.append("Non-monotonic ranking signal")
    return {
        "classifications": labels,
        "separateTopNExperimentJustified": strong,
        "recommendation": (
            "Preregister exactly one canonical top-5 versus top-3 portfolio comparison; do not search other cutoffs."
            if strong else
            "Do not run a top-N portfolio experiment from this evidence. Keep canonical top-N=5 unchanged."
        ),
    }


def summarize_strategy(name: str, ledger: pd.DataFrame, reconciliation: dict[str, Any], canonical: Any) -> dict[str, Any]:
    primary = ledger.loc[ledger["rank"] <= 10].copy()
    strategy = _strategy(name, canonical)
    comparisons = {
        key: compare_groups(primary, left, right)
        for key, (left, right) in COMPARISONS.items()
    }
    summary = {
        "canonical": {
            "strategy": name,
            "start": canonical.start.isoformat(),
            "end": canonical.end.isoformat(),
            "lookback": getattr(strategy, "lookback_trading_days", getattr(strategy, "lookback", None)),
            "topN": getattr(strategy, "top_n", None),
            "rebalanceFrequency": getattr(strategy, "rebalance_frequency", None),
        },
        "reconciliation": reconciliation,
        "rankTable": rank_table(primary),
        "groups": group_table(primary),
        "comparisons": comparisons,
        "rankInference": rank_inference(primary),
        "rankLabelNull": shuffled_rank_null(primary),
        "cumulativeDepth": cumulative_depth(primary),
        "scoreSpacing": score_spacing(primary),
        "percentileGroups": percentile_table(ledger),
        "stability": stability_analysis(primary),
        "securityContributors": security_concentration(primary),
        "extremeWinnerConcentration": extreme_winner_analysis(primary),
        "concentrationImplication": concentration_implication(primary),
    }
    if name == "Dual Momentum":
        summary["drawdownBehavior"] = drawdown_behavior(primary)
        summary["decision"] = _classify(summary)
    return summary


def _write_svg(rank_rows: list[dict[str, Any]], path: Path) -> None:
    width, height, margin = 760, 380, 55
    values = [x["meanReturn"] for x in rank_rows]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * .15, .005)
    lo, hi = lo - pad, hi + pad
    x = lambda rank: margin + (rank - 1) * (width - 2 * margin) / 9
    y = lambda value: margin + (hi - value) * (height - 2 * margin) / (hi - lo)
    points = " ".join(f"{x(r['rank']):.1f},{y(r['meanReturn']):.1f}" for r in rank_rows)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<line x1="{margin}" y1="{height-margin}" x2="{width-margin}" y2="{height-margin}" stroke="#777"/>',
             f'<line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height-margin}" stroke="#777"/>',
             f'<polyline points="{points}" fill="none" stroke="#2563eb" stroke-width="3"/>']
    for row in rank_rows:
        xx, yy = x(row["rank"]), y(row["meanReturn"])
        parts += [f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="4" fill="#2563eb"/>',
                  f'<text x="{xx:.1f}" y="{height-margin+20}" text-anchor="middle" font-size="12">{row["rank"]}</text>',
                  f'<text x="{xx:.1f}" y="{yy-9:.1f}" text-anchor="middle" font-size="10">{row["meanReturn"]:.1%}</text>']
    parts += [f'<text x="{width/2}" y="{height-8}" text-anchor="middle" font-size="13">Canonical rank</text>',
              '<text x="15" y="190" transform="rotate(-90 15 190)" text-anchor="middle" font-size="13">Mean next-rebalance return</text>',
              '</svg>']
    path.write_text("\n".join(parts), encoding="utf-8")


def _write_report(payload: dict[str, Any], path: Path) -> None:
    dm, mrm = payload["strategies"]["Dual Momentum"], payload["strategies"]["Market-Residual Momentum"]
    decision = dm["decision"]
    lines = [
        "# Dual Momentum Rank-Depth / Edge-Decay Audit", "",
        "Canonical DM and MRM were reconstructed unchanged. No alternative top-N portfolio was built or tested.", "",
        "## Final classification", "",
        "**" + "; ".join(decision["classifications"]) + ".**", "", decision["recommendation"], "",
        "## Ranking and rebalance reconciliation", "",
        "| Strategy | Complete periods | Selected-set matches | Missing outcome prices |", "|---|---:|---:|---:|",
    ]
    for name, summary in (("DM", dm), ("MRM", mrm)):
        r = summary["reconciliation"]
        lines.append(f"| {name} | {r['completeForwardPeriods']} | {r['selectedSetMatches']} | {len(r['missingOutcomePrices'])} |")
    lines += ["", "Scores use closes strictly before each rebalance; returns use exact rebalance-open to next-rebalance-open prices.", "",
              "## DM exact rank 1–10 forward performance", "",
              "| Rank | N | Mean | Median | Win rate | Beat SPY | Beat universe median | Top quartile | P10 | P90 |", "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in dm["rankTable"]:
        lines.append(f"| {row['rank']} | {row['n']} | {row['meanReturn']:.2%} | {row['medianReturn']:.2%} | {row['winRate']:.1%} | {row['beatSpy']:.1%} | {row['beatUniverseMedian']:.1%} | {row['futureTopQuartile']:.1%} | {row['p10']:.2%} | {row['p90']:.2%} |")
    ri = dm["rankInference"]
    lines += ["", "![Rank-return curve](rank_return_curve.svg)", "",
              f"Spearman(rank, return) = {ri['spearman']:.3f}, rebalance-cluster 95% CI [{ri['clusterCi95'][0]:.3f}, {ri['clusterCi95'][1]:.3f}], effective N={ri['effectiveN']}. The mean curve decreases on {ri['decreasingAdjacentSteps']} of {ri['totalAdjacentSteps']} adjacent steps.", "",
              "## Preregistered comparisons", "", "| Comparison | Mean difference | Median difference | Win-rate difference | P10 difference | Cluster 95% CI | Effect size |", "|---|---:|---:|---:|---:|---:|---:|"]
    for key, label in (("top3VsRanks4To5", "Top 3 vs ranks 4–5"), ("ranks1To2VsRanks3To5", "Ranks 1–2 vs ranks 3–5"), ("top5VsRanks6To10", "Top 5 vs ranks 6–10")):
        x = dm["comparisons"][key]
        lines.append(f"| {label} | {x['meanReturnDifference']:.2%} | {x['medianReturnDifference']:.2%} | {x['winRateDifference']:.1%} | {x['p10Difference']:.2%} | [{x['clusterCi95'][0]:.2%}, {x['clusterCi95'][1]:.2%}] | {x['pairedStandardizedEffect']:.2f} |")
    lines += ["", "## Cumulative depth / edge decay", "", "| Depth | Mean return | Added-rank mean | Added rank vs higher ranks |", "|---:|---:|---:|---:|"]
    for x in dm["cumulativeDepth"]:
        marginal = "—" if x["marginalVsHigherRanks"] is None else f"{x['marginalVsHigherRanks']:.2%}"
        lines.append(f"| {x['depth']} | {x['meanReturn']:.2%} | {x['newRankMeanReturn']:.2%} | {marginal} |")
    lines += ["", "## Accuracy, magnitude, and tails", "", "| Group | Win | Beat SPY | Beat universe | Top quartile | Avg winner | Avg loser | Expectancy |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for key, label in (("elite", "Ranks 1–2"), ("top3", "Ranks 1–3"), ("lowerSelected", "Ranks 4–5"), ("nearMisses", "Ranks 6–10")):
        x = dm["groups"][key]
        lines.append(f"| {label} | {x['winRate']:.1%} | {x['beatSpy']:.1%} | {x['beatUniverseMedian']:.1%} | {x['futureTopQuartile']:.1%} | {x['averageWinner']:.2%} | {x['averageLoser']:.2%} | {x['expectancy']:.2%} |")
    ext = dm["extremeWinnerConcentration"]
    lines += ["", f"Removing each group's top 1% outcomes leaves a top-3 minus ranks-4–5 difference of {ext['top1PctTrimmedTop3MinusRanks4To5']:.2%}. Top-3's top 1% / top 5 / top 10 observations contribute {ext['top3']['top1PctSharePositiveReturnSum']:.1%} / {ext['top3']['top5SharePositiveReturnSum']:.1%} / {ext['top3']['top10SharePositiveReturnSum']:.1%} of its positive-return sum.", "",
              "## Score spacing", "", "| Boundary | Mean score gap | Gap/return-difference rho | Small-gap return diff | Large-gap return diff |", "|---|---:|---:|---:|---:|"]
    for x in dm["scoreSpacing"]:
        lines.append(f"| {x['boundary']} | {x['meanScoreGap']:.4f} | {x['gapReturnDifferenceSpearman']:.3f} | {x['meanReturnDifferenceSmallGap']:.2%} | {x['meanReturnDifferenceLargeGap']:.2%} |")
    st = dm["stability"]
    lines += ["", "## Chronological and concentration stability", "",
              f"Top-3 minus ranks-4–5 is {st['firstHalfTop3MinusRanks4To5']:.2%} in the first half and {st['secondHalfTop3MinusRanks4To5']:.2%} in the second. Leave-one-year-out estimates: " + ", ".join(f"{k}: {v:.2%}" for k, v in st['leaveOneYearOutTop3MinusRanks4To5'].items()) + ".",
              f"Largest leave-one-security influence: {st['largestInfluenceSecurity']} (remaining difference {st['largestInfluenceResult']:.2%}). Full security/group attribution is in results.json.", "",
              "## Drawdown behavior", "", "| Episode | Periods | Top 3 | Ranks 4–5 | Ranks 6–10 |", "|---|---:|---:|---:|---:|"]
    for x in dm["drawdownBehavior"]:
        lines.append(f"| {x['start']} to {x['end']} | {x['rebalancePeriods']} | {x['top3MeanReturn']:.2%} | {x['ranks4To5MeanReturn']:.2%} | {x['ranks6To10MeanReturn']:.2%} |")
    null = dm["rankLabelNull"]
    lines += ["", "## Clustered inference and rank-label null", "",
              f"Within-rebalance shuffled-rank empirical directional p-values are H1={null['H1Top3MinusRanks4To5']['directionalEmpiricalP']:.3f}, H2={null['H2Top5MinusRanks6To10']['directionalEmpiricalP']:.3f}, and H3={null['H3RankReturnSpearman']['directionalEmpiricalP']:.3f}. These are lightweight diagnostic calibrations, not proof of a deployable variant.", "",
              "## Diversification implication", ""]
    ci = dm["concentrationImplication"]
    lines += [f"The descriptive top-3 period-return volatility is {ci['top3PeriodReturnVolatilityAnnualizedSqrt12']:.1%} annualized (sqrt-12), with average correlation {ci['top3AverageRankOutcomeCorrelation']:.2f} across rank slots and a worst single-name period of {ci['top3WorstSingleNamePeriod']:.1%}. This is not a top-3 portfolio backtest.", "",
              "## Compact MRM comparison", "", "| Strategy | Rank rho | Top3 minus 4–5 | Top5 minus 6–10 |", "|---|---:|---:|---:|"]
    for label, summary in (("DM", dm), ("MRM", mrm)):
        lines.append(f"| {label} | {summary['rankInference']['spearman']:.3f} | {summary['comparisons']['top3VsRanks4To5']['meanReturnDifference']:.2%} | {summary['comparisons']['top5VsRanks6To10']['meanReturnDifference']:.2%} |")
    lines += ["", "## Artifacts", "", "`results.json` is the machine-readable result; `dm_rank_ledger.csv` and `mrm_rank_ledger.csv` contain every ranked eligible security and forward outcome. Canonical DM, MRM, frozen forward tests, and Alpaca automation were not modified."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    if not PREREGISTRATION.exists():
        raise FileNotFoundError(f"Missing preregistration: {PREREGISTRATION}")
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries: dict[str, Any] = {}
    for name in ("Dual Momentum", "Market-Residual Momentum"):
        canonical = run_cross_sectional(name, persist=False)
        spy = data_module.get_bars("SPY", "1d", canonical.start, canonical.end)
        ledger, reconciliation = build_rank_ledger(name, canonical, spy)
        if ledger.empty:
            raise RuntimeError(f"{name}: rank ledger is empty")
        slug = "dm" if name == "Dual Momentum" else "mrm"
        ledger.to_csv(output_dir / f"{slug}_rank_ledger.csv", index=False)
        summaries[name] = summarize_strategy(name, ledger, reconciliation, canonical)
    payload = {
        "study": "Dual Momentum Rank-Depth / Edge-Decay Audit v1",
        "preregistration": str(PREREGISTRATION),
        "diagnosticOnly": True,
        "alternativeTopNPortfoliosTested": False,
        "strategies": summaries,
    }
    (output_dir / "results.json").write_text(
        json.dumps(_json(payload), indent=2, allow_nan=False), encoding="utf-8",
    )
    _write_svg(summaries["Dual Momentum"]["rankTable"], output_dir / "rank_return_curve.svg")
    _write_report(payload, output_dir / "report.md")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    result = run_audit(args.output_dir)
    print(json.dumps(_json(result["strategies"]["Dual Momentum"]["decision"]), indent=2))


if __name__ == "__main__":
    main()
