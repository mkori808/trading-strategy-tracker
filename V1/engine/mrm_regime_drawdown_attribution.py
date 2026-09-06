"""Regime and drawdown attribution for Market-Residual Momentum.

See research/mrm_regime_drawdown_attribution_preregistration.json. Follows
directly from engine/mrm_attribution_ladder.py: that diagnostic found D
(canonical Market-Residual Momentum) has a materially better Sharpe and max
drawdown than C (plain momentum) despite a slightly lower raw return. This
module asks whether that risk improvement is broad-based across distinct
market environments, or comes mostly from one or two historical episodes.

Reuses `engine.mrm_attribution_ladder.compute_rungs()` completely unchanged
-- same PlainMomentumControl (C) and canonical MarketResidualMomentum (D)
instances, same universe/window/costs/mechanics. This module adds no new
strategy behavior; it only re-slices the two equity curves that computation
already produces by regime and by drawdown episode.

Two orthogonal cuts:

1. Regime buckets (trend: bull/bear-correction/sideways from
   `engine/regime.py`'s existing SPY classifier; volatility: high-vol vs.
   low/normal-vol from `engine/timing_filters.py`'s existing SPY realized-vol
   percentile). Each rebalance period is labeled by the regime in effect on
   its START date -- the date the period's holdings were already decided --
   never a date inside or after it, so a period can't be labeled using
   information that arrived after the fact.
2. Drawdown episodes, anchored to SPY's OWN equity curve (not C's or D's),
   so the event calendar is an objective, market-wide fact rather than being
   selected after seeing which dates flatter either strategy.

Diagnostic only: writes to reports/, never engine/logging_db.py, and never
changes Market-Residual Momentum's status, parameters, or validation gates.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine import regime as regime_module
from engine import timing_filters
from engine.mrm_attribution_ladder import (
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_SEED,
    _paired_bootstrap,
    _period_returns,
    compute_rungs,
)

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports" / "mrm_regime_drawdown_attribution"
PREREGISTRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "research" / "mrm_regime_drawdown_attribution_preregistration.json"
)

TREND_LABEL_MAP = {
    regime_module.BULLISH: "bull",
    regime_module.BEARISH: "bear/correction",
    regime_module.NEUTRAL: "sideways",
}
VOL_THRESHOLD = 0.70
MATERIALITY_PCT = 5.0
MIN_PERIODS = 6


def _label_periods(
    periods: pd.DataFrame, trend_labels: pd.Series, vol_percentiles: pd.Series,
) -> pd.DataFrame:
    labeled = periods.copy()
    trend, vol = [], []
    for ts in labeled.index:
        raw_trend = trend_labels.asof(ts) if not trend_labels.empty else None
        trend.append(TREND_LABEL_MAP.get(raw_trend, "unknown") if pd.notna(raw_trend) else "unknown")
        raw_vol = vol_percentiles.asof(ts) if not vol_percentiles.empty else None
        if pd.isna(raw_vol):
            vol.append("unknown")
        else:
            vol.append("high vol" if raw_vol > VOL_THRESHOLD else "low/normal vol")
    labeled["trendRegime"] = trend
    labeled["volRegime"] = vol
    return labeled


def _synthetic_drawdown_pct(returns: pd.Series) -> float | None:
    """Max drawdown of a curve built by chaining ONLY the (possibly
    non-contiguous) periods in one bucket, in chronological order -- "how
    much did this cohort of periods draw down while active," which is the
    fair comparison across two strategies sharing the same period labels."""
    if returns.empty:
        return None
    curve = (1.0 + returns.sort_index()).cumprod()
    running_max = curve.cummax()
    dd = curve / running_max - 1.0
    return float(dd.min() * 100.0)


def _bucket_stats(labeled: pd.DataFrame, column: str) -> list[dict[str, Any]]:
    rows = []
    for label, group in labeled.groupby(column):
        diff = group["d"] - group["c"]
        c_contribution = float(np.prod(1.0 + group["c"]) - 1.0) * 100.0
        d_contribution = float(np.prod(1.0 + group["d"]) - 1.0) * 100.0
        c_dd = _synthetic_drawdown_pct(group["c"])
        d_dd = _synthetic_drawdown_pct(group["d"])
        drawdown_improvement = None if c_dd is None or d_dd is None else abs(c_dd) - abs(d_dd)
        rows.append({
            "label": label,
            "periods": int(len(group)),
            "underpowered": len(group) < MIN_PERIODS,
            "cContributionPct": c_contribution,
            "dContributionPct": d_contribution,
            "cMinusDReturnContributionPct": c_contribution - d_contribution,
            "cSyntheticMaxDrawdownPct": c_dd,
            "dSyntheticMaxDrawdownPct": d_dd,
            "drawdownImprovementPct": drawdown_improvement,
            "pairedBootstrap": _paired_bootstrap(diff, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED),
        })
    return sorted(rows, key=lambda r: r["label"])


def _drawdown_episodes(equity: pd.Series, *, materiality_pct: float) -> list[dict[str, Any]]:
    """SPY's own peak-to-trough-to-recovery episodes, same shape as
    engine/dm_replacement_quality.py:_all_drawdown_episodes -- computed on
    SPY, not on C or D, so the episode calendar is fixed before either
    strategy's numbers are inspected."""
    clean = equity.dropna()
    running_max = clean.cummax()
    dd = clean / running_max - 1.0
    episodes: list[dict[str, Any]] = []
    start_idx = None
    n = len(dd)
    for i, value in enumerate(dd.to_numpy()):
        if value < 0 and start_idx is None:
            start_idx = max(i - 1, 0)
        is_last = i == n - 1
        if start_idx is not None and (value >= 0 or is_last):
            end_idx = i if (is_last and value < 0) else i - 1
            segment = dd.iloc[start_idx:end_idx + 1]
            trough_ts = segment.idxmin()
            trough_pct = float(segment.min() * 100.0)
            if abs(trough_pct) >= materiality_pct:
                recovered = value >= 0
                episodes.append({
                    "start": dd.index[start_idx],
                    "trough": trough_ts,
                    "recovery": dd.index[i] if recovered else None,
                    "end": dd.index[i] if recovered else dd.index[end_idx],
                    "spyDrawdownPct": trough_pct,
                })
            start_idx = None
    return episodes


def _local_drawdown_pct(equity: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    window = equity.loc[start:end].dropna()
    if len(window) < 2:
        return None
    running_max = window.cummax()
    dd = window / running_max - 1.0
    return float(dd.min() * 100.0)


def _window_return_pct(equity: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    window = equity.loc[start:end].dropna()
    if len(window) < 2 or float(window.iloc[0]) <= 0:
        return None
    return float((window.iloc[-1] / window.iloc[0] - 1.0) * 100.0)


def _concentration_check(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    improvements = sorted(
        (e["drawdownImprovementPct"] for e in episodes
         if e["drawdownImprovementPct"] is not None and e["drawdownImprovementPct"] > 0),
        reverse=True,
    )
    total = float(sum(improvements))
    if not improvements or total <= 0:
        return {"totalImprovementPct": total, "materialEpisodeCount": 0, "top1SharePct": None, "top2SharePct": None}
    return {
        "totalImprovementPct": total,
        "materialEpisodeCount": len(improvements),
        "top1SharePct": float(improvements[0] / total * 100.0),
        "top2SharePct": float(sum(improvements[:2]) / total * 100.0),
    }


def _verdict(
    trend_buckets: list[dict[str, Any]], vol_buckets: list[dict[str, Any]],
    episodes: list[dict[str, Any]], concentration: dict[str, Any],
) -> dict[str, Any]:
    risk_buckets = (
        [b for b in trend_buckets if b["label"] == "bear/correction"]
        + [b for b in vol_buckets if b["label"] == "high vol"]
    )
    sampled_buckets = [b for b in risk_buckets if not b["underpowered"]]
    bucket_favor_d = sum(1 for b in sampled_buckets if (b["drawdownImprovementPct"] or 0) > 0)

    sampled_episodes = [e for e in episodes if not e["underpowered"]]
    episode_favor_d = sum(1 for e in sampled_episodes if (e["drawdownImprovementPct"] or 0) > 0)

    top1 = concentration.get("top1SharePct")
    material_episode_count = concentration.get("materialEpisodeCount") or 0

    if not sampled_buckets or not sampled_episodes:
        label = "inconclusive"
    else:
        bucket_majority = bucket_favor_d > len(sampled_buckets) / 2
        episode_majority = episode_favor_d > len(sampled_episodes) / 2
        concentrated = top1 is not None and top1 > 60.0
        if concentrated or material_episode_count < 2:
            label = "concentrated / single-event-dominated"
        elif bucket_majority and episode_majority:
            label = "broad-based"
        else:
            label = "inconclusive"

    return {
        "label": label,
        "riskBucketsSampled": len(sampled_buckets),
        "riskBucketsFavoringD": bucket_favor_d,
        "episodesSampled": len(sampled_episodes),
        "episodesFavoringD": episode_favor_d,
        "materialEpisodeCount": material_episode_count,
        "top1EpisodeSharePct": top1,
    }


def run_attribution(cash: float = 10_000.0) -> dict[str, Any]:
    computed = compute_rungs(cash)
    start, end = computed["start"], computed["end"]
    result_c = computed["result_c"]
    result_d = computed["result_d"]
    rebalance_dates = computed["rebalance_dates"]
    spy_equity = computed["spy_equity"]

    periods_c = _period_returns(result_c.equity_curve, rebalance_dates)
    periods_d = _period_returns(result_d.equity_curve, rebalance_dates)
    periods = pd.concat([periods_c.rename("c"), periods_d.rename("d")], axis=1).dropna()

    trend_bars = regime_module.load_spy_bars(start, end)
    trend_series = regime_module.regime_series(trend_bars)
    vol_percentiles = timing_filters.spy_vol_percentile(start, end)
    labeled = _label_periods(periods, trend_series, vol_percentiles)

    trend_buckets = _bucket_stats(labeled, "trendRegime")
    vol_buckets = _bucket_stats(labeled, "volRegime")

    episodes_raw = _drawdown_episodes(spy_equity, materiality_pct=MATERIALITY_PCT)
    episode_rows = []
    for episode in episodes_raw:
        window_end = episode["end"]
        c_dd = _local_drawdown_pct(result_c.equity_curve, episode["start"], window_end)
        d_dd = _local_drawdown_pct(result_d.equity_curve, episode["start"], window_end)
        c_ret = _window_return_pct(result_c.equity_curve, episode["start"], window_end)
        d_ret = _window_return_pct(result_d.equity_curve, episode["start"], window_end)
        window_periods = periods.loc[(periods.index >= episode["start"]) & (periods.index <= window_end)]
        diff = window_periods["d"] - window_periods["c"]
        bootstrap = (
            _paired_bootstrap(diff, draws=BOOTSTRAP_DRAWS, seed=BOOTSTRAP_SEED)
            if len(diff) else {"draws": 0, "reason": "no overlapping rebalance periods"}
        )
        improvement = None if c_dd is None or d_dd is None else abs(c_dd) - abs(d_dd)
        episode_rows.append({
            "start": str(episode["start"].date()),
            "trough": str(episode["trough"].date()),
            "recovery": None if episode["recovery"] is None else str(episode["recovery"].date()),
            "end": str(window_end.date()),
            "spyDrawdownPct": episode["spyDrawdownPct"],
            "cMaxDrawdownPct": c_dd,
            "dMaxDrawdownPct": d_dd,
            "drawdownImprovementPct": improvement,
            "cReturnPct": c_ret,
            "dReturnPct": d_ret,
            "cMinusDReturnContributionPct": None if c_ret is None or d_ret is None else c_ret - d_ret,
            "periods": int(len(window_periods)),
            "underpowered": len(window_periods) < MIN_PERIODS,
            "pairedBootstrap": bootstrap,
        })

    concentration = _concentration_check(episode_rows)
    verdict = _verdict(trend_buckets, vol_buckets, episode_rows, concentration)

    regime_distribution = {
        "trend": {label: int((labeled["trendRegime"] == label).sum()) for label in labeled["trendRegime"].unique()},
        "volatility": {label: int((labeled["volRegime"] == label).sum()) for label in labeled["volRegime"].unique()},
        "totalPeriods": int(len(labeled)),
    }

    return {
        "generatedFrom": str(PREREGISTRATION_PATH.name),
        "window": {"start": str(start), "end": str(end)},
        "minimumPeriodsForPower": MIN_PERIODS,
        "drawdownMaterialityThresholdPct": MATERIALITY_PCT,
        "volatilityThresholdPercentile": VOL_THRESHOLD,
        "regimeDistribution": regime_distribution,
        "trendRegimeBuckets": trend_buckets,
        "volatilityRegimeBuckets": vol_buckets,
        "drawdownEpisodes": episode_rows,
        "concentrationCheck": concentration,
        "verdict": verdict,
    }


def _fmt(value: float | None, suffix: str = "%", signed: bool = True) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}{suffix}" if signed else f"{value:.2f}{suffix}"


def _bucket_table(buckets: list[dict[str, Any]]) -> list[str]:
    lines = [
        "| Regime | Periods | Underpowered | C return | D return | C-D return contribution | C max DD (bucket) | D max DD (bucket) | Drawdown improvement |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in buckets:
        lines.append(
            f"| {row['label']} | {row['periods']} | {'yes' if row['underpowered'] else 'no'} | "
            f"{_fmt(row['cContributionPct'])} | {_fmt(row['dContributionPct'])} | "
            f"{_fmt(row['cMinusDReturnContributionPct'])} | "
            f"{_fmt(row['cSyntheticMaxDrawdownPct'], signed=False)} | "
            f"{_fmt(row['dSyntheticMaxDrawdownPct'], signed=False)} | "
            f"{_fmt(row['drawdownImprovementPct'])} |"
        )
    return lines


def write_report(payload: dict[str, Any]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "results.json").write_text(json.dumps(payload, indent=2, default=str))

    verdict = payload["verdict"]
    concentration = payload["concentrationCheck"]
    sampled_episode_labels = [
        f"{e['start']} to {e['end']}" for e in payload["drawdownEpisodes"] if not e["underpowered"]
    ]
    sampled_episode_text = (
        ", ".join(sampled_episode_labels) if sampled_episode_labels else "none"
    )
    lines = [
        "# Market-Residual Momentum Regime & Drawdown Attribution",
        "",
        f"Window: {payload['window']['start']} to {payload['window']['end']}. "
        f"Minimum periods for a bucket/episode to count toward the verdict: "
        f"{payload['minimumPeriodsForPower']}. Drawdown materiality threshold: "
        f"{payload['drawdownMaterialityThresholdPct']}% SPY peak-to-trough. "
        f"Volatility threshold: top {(1 - payload['volatilityThresholdPercentile']) * 100:.0f}% "
        f"of trailing realized-vol percentile.",
        "",
        "## Verdict",
        "",
        f"**{verdict['label'].upper()}** -- {verdict['riskBucketsFavoringD']}/{verdict['riskBucketsSampled']} "
        f"materially-sampled bear/correction and high-vol buckets favor D on drawdown; "
        f"{verdict['episodesFavoringD']}/{verdict['episodesSampled']} materially-sampled drawdown "
        f"episodes favor D; top episode accounts for "
        f"{_fmt(verdict['top1EpisodeSharePct'], signed=False) if verdict['top1EpisodeSharePct'] is not None else 'n/a'} "
        f"of the total drawdown improvement across {verdict['materialEpisodeCount']} improving episode(s).",
        "",
        f"**Power caveat -- read this before the verdict label above.** "
        f"{len(payload['drawdownEpisodes'])} SPY-anchored episodes were identified in total; only "
        f"{verdict['episodesSampled']} of them clear the {payload['minimumPeriodsForPower']}-period "
        f"minimum this study pre-registered as the floor for an individually reliable result. "
        f"Directionally (ignoring the power floor), "
        f"{sum(1 for e in payload['drawdownEpisodes'] if (e['drawdownImprovementPct'] or 0) > 0)}"
        f"/{len(payload['drawdownEpisodes'])} episodes show D drawing down less than C -- consistent "
        f"with 'broad-based' as a DIRECTIONAL pattern, but the label above is certified by only the "
        f"materially-sampled episode(s) ({sampled_episode_text}) plus the regime-bucket cut, not by "
        f"independently powered results for every episode. Treat the short episodes' own numbers as "
        f"illustrative, not confirmatory.",
        "",
        "## Regime distribution (rebalance periods)",
        "",
        f"Trend: {payload['regimeDistribution']['trend']}. "
        f"Volatility: {payload['regimeDistribution']['volatility']}. "
        f"Total periods: {payload['regimeDistribution']['totalPeriods']}.",
        "",
        "## Trend regime buckets",
        "",
        *_bucket_table(payload["trendRegimeBuckets"]),
        "",
        "## Volatility regime buckets",
        "",
        *_bucket_table(payload["volatilityRegimeBuckets"]),
        "",
        "## Drawdown episodes (SPY-anchored, >=5% peak-to-trough)",
        "",
        "| Start | Trough | Recovery | SPY DD | C max DD | D max DD | Improvement | C-D return contribution | Periods | Underpowered |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for episode in payload["drawdownEpisodes"]:
        lines.append(
            f"| {episode['start']} | {episode['trough']} | {episode['recovery'] or 'not recovered'} | "
            f"{_fmt(episode['spyDrawdownPct'], signed=False)} | {_fmt(episode['cMaxDrawdownPct'], signed=False)} | "
            f"{_fmt(episode['dMaxDrawdownPct'], signed=False)} | {_fmt(episode['drawdownImprovementPct'])} | "
            f"{_fmt(episode['cMinusDReturnContributionPct'])} | {episode['periods']} | "
            f"{'yes' if episode['underpowered'] else 'no'} |"
        )
    lines += [
        "",
        f"Concentration check: {concentration['materialEpisodeCount']} episode(s) improved drawdown at all; "
        f"the single largest accounts for "
        f"{_fmt(concentration['top1SharePct'], signed=False) if concentration['top1SharePct'] is not None else 'n/a'} "
        f"of the total improvement, the largest two for "
        f"{_fmt(concentration['top2SharePct'], signed=False) if concentration['top2SharePct'] is not None else 'n/a'}.",
        "",
        "This is a diagnostic attribution, not a validation gate. It does not change "
        "Market-Residual Momentum's 'Interesting, unresolved' verdict in "
        "research/frozen_research_report.md, its parameters, or its leaderboard status.",
    ]
    (REPORT_DIR / "report.md").write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    payload = run_attribution()
    write_report(payload)
    print(json.dumps(payload, indent=2, default=str))


if __name__ == "__main__":
    main()
