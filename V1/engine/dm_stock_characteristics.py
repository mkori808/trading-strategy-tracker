"""Preregistered stock-characteristics audit for canonical Dual Momentum.

The selected holdings and exact-open outcomes come from the already-reconciled
rank-depth ledger.  This module only attaches information available strictly
before each rebalance and performs dependence-aware diagnostics.  It cannot
construct an alternative portfolio or place orders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engine import data as data_module

ROOT = Path(__file__).resolve().parent.parent
PREREGISTRATION = ROOT / "research" / "dm_stock_characteristics_v1_preregistration.json"
SOURCE_LEDGER = ROOT / "reports" / "dm_rank_depth_audit" / "dm_rank_ledger.csv"
OUTPUT_DIR = ROOT / "reports" / "dm_stock_characteristics_v1"
BOOTSTRAP_DRAWS = 2_000
NULL_DRAWS = 2_000
SEED = 20260830
MIN_COVERAGE = 0.95
MIN_EFFECT = 0.01

FEATURES: tuple[tuple[str, str], ...] = (
    ("volatility_63d", "63-session annualized volatility"),
    ("beta_spy_126d", "126-session SPY beta"),
    ("log_dollar_volume_20d", "20-session log average dollar volume"),
    ("distance_from_252d_high", "Distance from 252-session high"),
    ("momentum_acceleration_63_189", "63-vs-189-session momentum acceleration"),
)


def _safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if isinstance(value, (np.integer,)): return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)): return bool(value)
    if isinstance(value, pd.Timestamp): return value.isoformat()
    return value


def _before(frame: pd.DataFrame, rebalance: pd.Timestamp) -> pd.DataFrame:
    """Bars observable before the rebalance open; never include that session."""
    return frame.loc[frame.index.date < rebalance.date()].sort_index()


def point_in_time_features(
    bars: pd.DataFrame, spy_bars: pd.DataFrame, rebalance: pd.Timestamp,
) -> dict[str, float | None]:
    history = _before(bars, rebalance)
    spy_history = _before(spy_bars, rebalance)
    close = history.Close.astype(float)
    returns = close.pct_change(fill_method=None)
    values: dict[str, float | None] = {key: None for key, _ in FEATURES}
    if len(returns.dropna()) >= 63:
        values["volatility_63d"] = float(returns.dropna().tail(63).std(ddof=1) * math.sqrt(252))
    aligned = pd.concat(
        [returns.rename("stock"), spy_history.Close.astype(float).pct_change(fill_method=None).rename("spy")],
        axis=1, join="inner",
    ).dropna().tail(126)
    if len(aligned) == 126 and float(aligned.spy.var(ddof=1)) > 0:
        values["beta_spy_126d"] = float(aligned.stock.cov(aligned.spy) / aligned.spy.var(ddof=1))
    if len(history) >= 20:
        dollar_volume = (history.Close.astype(float) * history.Volume.astype(float)).tail(20).mean()
        if np.isfinite(dollar_volume) and dollar_volume > 0:
            values["log_dollar_volume_20d"] = float(math.log(dollar_volume))
    if len(close) >= 252:
        high = float(close.tail(252).max())
        if high > 0:
            values["distance_from_252d_high"] = float(close.iloc[-1] / high - 1.0)
    if len(close) >= 190 and close.iloc[-190] > 0 and close.iloc[-64] > 0:
        ret_63 = float(close.iloc[-1] / close.iloc[-64] - 1.0)
        ret_189 = float(close.iloc[-1] / close.iloc[-190] - 1.0)
        values["momentum_acceleration_63_189"] = ret_63 - ret_189
    return values


def load_selected_ledger(path: Path = SOURCE_LEDGER) -> pd.DataFrame:
    frame = pd.read_csv(path)
    selected = frame.loc[frame.selected_by_canonical.astype(str).str.lower().eq("true")].copy()
    selected["rebalance_date"] = pd.to_datetime(selected.rebalance_date, utc=True).dt.tz_convert("America/New_York")
    selected["next_rebalance_date"] = pd.to_datetime(selected.next_rebalance_date, utc=True).dt.tz_convert("America/New_York")
    selected["active_return"] = selected.forward_return.astype(float) - selected.spy_forward_return.astype(float)
    selected = selected.sort_values(["rebalance_date", "rank", "symbol"]).reset_index(drop=True)
    return selected


def build_feature_ledger(
    selected: pd.DataFrame, bars: dict[str, pd.DataFrame], spy_bars: pd.DataFrame,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    prior_holdings: set[str] | None = None
    for rebalance, group in selected.groupby("rebalance_date", sort=True):
        current = set(group.symbol)
        for source in group.to_dict("records"):
            symbol = str(source["symbol"])
            features = (
                point_in_time_features(bars[symbol], spy_bars, pd.Timestamp(rebalance))
                if symbol in bars else {key: None for key, _ in FEATURES}
            )
            rows.append({
                "rebalance_date": pd.Timestamp(rebalance),
                "next_rebalance_date": pd.Timestamp(source["next_rebalance_date"]),
                "symbol": symbol, "rank": int(source["rank"]),
                "momentum_score": float(source["score"]),
                "incumbent": None if prior_holdings is None else symbol in prior_holdings,
                "forward_return": float(source["forward_return"]),
                "spy_forward_return": float(source["spy_forward_return"]),
                "active_return": float(source["active_return"]),
                **features,
            })
        prior_holdings = current
    ledger = pd.DataFrame(rows)
    for key, _ in FEATURES:
        ledger[f"{key}_z"] = ledger.groupby("rebalance_date")[key].transform(
            lambda values: (values - values.mean()) / values.std(ddof=1)
            if values.notna().sum() >= 2 and values.std(ddof=1) > 0 else np.nan
        )
    return ledger


def _effect(frame: pd.DataFrame, feature: str, outcome: str = "active_return") -> dict[str, float | int | None]:
    z = f"{feature}_z"
    sample = frame[["rebalance_date", z, outcome]].dropna().copy()
    if sample.empty:
        return {"effect": None, "clusterSe": None, "t": None, "n": 0, "clusters": 0}
    sample["y"] = sample[outcome] - sample.groupby("rebalance_date")[outcome].transform("mean")
    denominator = float(np.square(sample[z]).sum())
    if denominator <= 0:
        return {"effect": None, "clusterSe": None, "t": None, "n": len(sample), "clusters": sample.rebalance_date.nunique()}
    effect = float((sample[z] * sample.y).sum() / denominator)
    sample["score"] = sample[z] * (sample.y - effect * sample[z])
    cluster_scores = sample.groupby("rebalance_date").score.sum().to_numpy(float)
    clusters = len(cluster_scores)
    se = (
        float(math.sqrt(clusters / (clusters - 1) * np.square(cluster_scores).sum() / denominator**2))
        if clusters > 1 else None
    )
    return {
        "effect": effect, "clusterSe": se,
        "t": None if not se or se <= 0 else effect / se,
        "n": len(sample), "clusters": clusters,
    }


def _bootstrap_ci(frame: pd.DataFrame, feature: str, rng: np.random.Generator) -> tuple[float, float] | None:
    z = f"{feature}_z"
    sample = frame[["rebalance_date", z, "active_return"]].dropna().copy()
    sample["y"] = sample.active_return - sample.groupby("rebalance_date").active_return.transform("mean")
    sufficient = sample.assign(xy=sample[z] * sample.y, xx=np.square(sample[z])).groupby("rebalance_date")[["xy", "xx"]].sum()
    if sufficient.empty:
        return None
    xy, xx = sufficient.xy.to_numpy(float), sufficient.xx.to_numpy(float)
    draws = np.empty(BOOTSTRAP_DRAWS)
    for i in range(BOOTSTRAP_DRAWS):
        chosen = rng.integers(0, len(sufficient), len(sufficient))
        denominator = float(xx[chosen].sum())
        draws[i] = float(xy[chosen].sum() / denominator) if denominator > 0 else np.nan
    clean = draws[np.isfinite(draws)]
    return None if not len(clean) else (float(np.quantile(clean, .025)), float(np.quantile(clean, .975)))


def _permutation_fwer(frame: pd.DataFrame, observed: dict[str, dict[str, Any]], rng: np.random.Generator) -> dict[str, float | None]:
    # Permute the shared outcome once per date, then let each feature apply its
    # own preregistered missing-row policy inside _effect.  This preserves one
    # joint familywise null without forcing every feature onto the intersection
    # of all other features' available rows.
    complete = frame.dropna(subset=["active_return"]).copy()
    dates = [group.index.to_numpy() for _, group in complete.groupby("rebalance_date", sort=True)]
    maxima = np.empty(NULL_DRAWS)
    base_outcome = complete.active_return.copy()
    for draw in range(NULL_DRAWS):
        permuted = base_outcome.copy()
        for indices in dates:
            permuted.loc[indices] = rng.permutation(base_outcome.loc[indices].to_numpy(float))
        candidate = complete.copy()
        candidate["active_return"] = permuted
        statistics = [abs(float(_effect(candidate, key)["t"] or 0.0)) for key, _ in FEATURES]
        maxima[draw] = max(statistics)
    return {
        key: (
            None if row["t"] is None else
            float((1 + np.sum(maxima >= abs(float(row["t"])))) / (NULL_DRAWS + 1))
        )
        for key, row in observed.items()
    }


def _bucket_summary(frame: pd.DataFrame, feature: str) -> dict[str, Any]:
    z = f"{feature}_z"
    sample = frame.dropna(subset=[z, "active_return"]).copy()
    # Top-two and bottom-two are disjoint only when at least four securities
    # were selected. Canonical DM held cash and selected only two names in one
    # month; omit that month from this descriptive contrast rather than count
    # the same stocks on both sides.
    sizes = sample.groupby("rebalance_date")[z].transform("size")
    sample = sample.loc[sizes >= 4].copy()
    sample["bucket_rank"] = sample.groupby("rebalance_date")[z].rank(method="first")
    sizes = sample.groupby("rebalance_date")[z].transform("size")
    low = sample.loc[sample.bucket_rank <= 2]
    high = sample.loc[sample.bucket_rank > sizes - 2]
    return {
        "lowN": len(low), "lowMeanCharacteristic": low[feature].mean(),
        "lowMeanActiveReturn": low.active_return.mean(),
        "highN": len(high), "highMeanCharacteristic": high[feature].mean(),
        "highMeanActiveReturn": high.active_return.mean(),
        "highMinusLow": high.active_return.mean() - low.active_return.mean(),
    }


def analyze(ledger: pd.DataFrame) -> dict[str, Any]:
    rng = np.random.default_rng(SEED)
    primary: dict[str, dict[str, Any]] = {}
    all_dates = sorted(ledger.rebalance_date.unique())
    midpoint = len(all_dates) // 2
    first_dates, second_dates = set(all_dates[:midpoint]), set(all_dates[midpoint:])
    years = sorted(ledger.rebalance_date.dt.year.unique())
    symbols = sorted(ledger.symbol.unique())
    for key, label in FEATURES:
        result = dict(_effect(ledger, key))
        ci = _bootstrap_ci(ledger, key, rng)
        result.update({
            "key": key, "label": label,
            "coverage": float(ledger[key].notna().mean()),
            "ciLow": None if ci is None else ci[0], "ciHigh": None if ci is None else ci[1],
            "firstHalfEffect": _effect(ledger.loc[ledger.rebalance_date.isin(first_dates)], key)["effect"],
            "secondHalfEffect": _effect(ledger.loc[ledger.rebalance_date.isin(second_dates)], key)["effect"],
            "leaveOneYearOut": {
                str(year): _effect(ledger.loc[ledger.rebalance_date.dt.year != year], key)["effect"]
                for year in years
            },
            "leaveOneSecurityOut": {
                symbol: _effect(ledger.loc[ledger.symbol != symbol], key)["effect"]
                for symbol in symbols
            },
            "buckets": _bucket_summary(ledger, key),
        })
        primary[key] = result
    adjusted = _permutation_fwer(ledger, primary, rng)
    for key, result in primary.items():
        result["familywiseP"] = adjusted[key]
        effect = result["effect"]
        sign = 0 if effect is None else int(np.sign(effect))
        loyo = [value for value in result["leaveOneYearOut"].values() if value is not None]
        agreement = sum(int(np.sign(value)) == sign for value in loyo) / len(loyo) if loyo and sign else 0.0
        result["leaveOneYearDirectionalAgreement"] = agreement
        halves_agree = all(
            value is not None and int(np.sign(value)) == sign
            for value in (result["firstHalfEffect"], result["secondHalfEffect"])
        )
        result["qualifies"] = bool(
            effect is not None and abs(float(effect)) >= MIN_EFFECT
            and result["coverage"] >= MIN_COVERAGE
            and result["ciLow"] is not None and result["ciHigh"] is not None
            and float(result["ciLow"]) * float(result["ciHigh"]) > 0
            and result["familywiseP"] is not None and float(result["familywiseP"]) <= .10
            and halves_agree and agreement >= .80
        )
    incumbent = ledger.dropna(subset=["incumbent"]).copy()
    incumbent_summary = {
        "incumbentN": int(incumbent.incumbent.sum()),
        "newN": int((~incumbent.incumbent.astype(bool)).sum()),
        "incumbentMeanActiveReturn": incumbent.loc[incumbent.incumbent.astype(bool), "active_return"].mean(),
        "newMeanActiveReturn": incumbent.loc[~incumbent.incumbent.astype(bool), "active_return"].mean(),
    }
    incumbent_summary["incumbentMinusNew"] = (
        incumbent_summary["incumbentMeanActiveReturn"] - incumbent_summary["newMeanActiveReturn"]
    )
    qualified = [key for key, row in primary.items() if row["qualifies"]]
    return {
        "rows": len(ledger), "rebalanceClusters": ledger.rebalance_date.nunique(),
        "symbols": ledger.symbol.nunique(), "features": primary,
        "incumbentDescriptive": incumbent_summary, "qualifiedFeatures": qualified,
        "decision": (
            "One separately preregistered validation study is justified."
            if qualified else "No stock-characteristic validation study is justified."
        ),
    }


def _pct(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None or not np.isfinite(value) else f"{value:+.{digits}%}"


def _characteristic_value(key: str, value: float | None) -> str:
    if value is None or not np.isfinite(value): return "n/a"
    if key == "volatility_63d": return f"{value:.1%}"
    if key == "beta_spy_126d": return f"{value:.2f}"
    if key == "log_dollar_volume_20d": return f"${math.exp(value) / 1_000_000:.0f}M"
    return f"{value:+.1%}"


def _report(payload: dict[str, Any]) -> str:
    result = payload["analysis"]
    lines = [
        "# Canonical Dual Momentum Stock-Characteristics Audit v1", "",
        "This audit asks which point-in-time price/liquidity characteristics describe better next-rebalance SPY-relative outcomes among the five stocks canonical DM actually selected. It does not test a new portfolio.", "",
        "## Decision", "", f"**{result['decision']}**", "",
        f"The ledger contains {result['rows']} selected stock-periods, {result['rebalanceClusters']} independent rebalance clusters, and {result['symbols']} securities.", "",
        "## Primary characteristic tests", "",
        "Effect is the active-return change for a one-standard-deviation within-rebalance increase. P-values use the preregistered familywise maximum-statistic null.", "",
        "| Characteristic | Coverage | Effect / SD | 95% cluster CI | Cluster t | FWER p | First half | Second half | LOYO sign | Qualifies |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key, label in FEATURES:
        row = result["features"][key]
        ci = f"[{_pct(row['ciLow'])}, {_pct(row['ciHigh'])}]"
        lines.append(
            f"| {label} | {row['coverage']:.1%} | {_pct(row['effect'])} | {ci} | "
            f"{row['t']:.2f} | {row['familywiseP']:.3f} | {_pct(row['firstHalfEffect'])} | "
            f"{_pct(row['secondHalfEffect'])} | {row['leaveOneYearDirectionalAgreement']:.0%} | "
            f"{'yes' if row['qualifies'] else 'no'} |"
        )
    lines += ["", "## High-versus-low descriptive buckets", "",
              "Within each rebalance, the two highest-characteristic holdings are compared with the two lowest. These are descriptions, not extra hypotheses.", "",
              "| Characteristic | Low group level | Low mean active | High group level | High mean active | High minus low |", "|---|---:|---:|---:|---:|---:|"]
    for key, label in FEATURES:
        row = result["features"][key]["buckets"]
        lines.append(
            f"| {label} | {_characteristic_value(key, row['lowMeanCharacteristic'])} | "
            f"{_pct(row['lowMeanActiveReturn'])} | {_characteristic_value(key, row['highMeanCharacteristic'])} | "
            f"{_pct(row['highMeanActiveReturn'])} | {_pct(row['highMinusLow'])} |"
        )
    incumbent = result["incumbentDescriptive"]
    lines += ["", "## Incumbent status (descriptive)", "",
              f"Previously held names averaged {_pct(incumbent['incumbentMeanActiveReturn'])} active return ({incumbent['incumbentN']} rows), versus {_pct(incumbent['newMeanActiveReturn'])} for new selections ({incumbent['newN']} rows), a {_pct(incumbent['incumbentMinusNew'])} difference.", "",
              "## Scope and blockers", "",
              "- Results apply only inside canonical DM's selected top five in the caveated Dow reconstruction; they do not describe the live optimized small/mid-cap universe.",
              "- Sector, market cap, and fundamentals remain untested because genuine point-in-time histories are not installed.",
              "- Simultaneous holdings are dependent. The analysis treats the rebalance date, not each stock row, as the independent unit.",
              "- No feature, threshold, filter, weight, signal, order, or forward-test configuration changed.", ""]
    return "\n".join(lines)


def run(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    prereg = json.loads(PREREGISTRATION.read_text(encoding="utf-8"))
    selected = load_selected_ledger()
    start = selected.rebalance_date.min().date() - timedelta(days=500)
    end = selected.rebalance_date.max().date() + timedelta(days=2)
    symbols = sorted(selected.symbol.unique())
    bars = {symbol: data_module.get_bars(symbol, "1d", start, end) for symbol in symbols}
    spy = data_module.get_bars("SPY", "1d", start, end)
    ledger = build_feature_ledger(selected, bars, spy)
    analysis = analyze(ledger)
    payload = {
        "study": prereg["study"], "preregistration": str(PREREGISTRATION),
        "sourceLedger": str(SOURCE_LEDGER),
        "preregistrationSha256": hashlib.sha256(PREREGISTRATION.read_bytes()).hexdigest(),
        "sourceLedgerSha256": hashlib.sha256(SOURCE_LEDGER.read_bytes()).hexdigest(),
        "informationBoundary": prereg["population"]["informationBoundary"],
        "analysis": analysis,
        "guards": prereg["prohibited"],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger.to_csv(output_dir / "selected_stock_characteristics.csv", index=False)
    (output_dir / "results.json").write_text(json.dumps(_safe(payload), indent=2, sort_keys=True), encoding="utf-8")
    (output_dir / "report.md").write_text(_report(payload), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    payload = run(args.output_dir)
    print(json.dumps({
        "rows": payload["analysis"]["rows"],
        "clusters": payload["analysis"]["rebalanceClusters"],
        "qualifiedFeatures": payload["analysis"]["qualifiedFeatures"],
        "output": str(args.output_dir),
    }, indent=2))


if __name__ == "__main__":
    main()
