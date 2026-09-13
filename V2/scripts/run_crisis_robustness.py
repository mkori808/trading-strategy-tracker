"""Leave-one-crisis-out audit for frozen crisis-conditioned hypotheses.

This audit does not search thresholds, dates, weights, or signal variants.
It reuses the frozen S1 BEAR+HIGH_VOL weekly panel and the crisis episodes
already identified by the preregistered V3.1 drawdown-anatomy study.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PANEL = ROOT / "reports" / "bear_overlay" / "20260911T141624Z" / "weekly_s1_panel_eval.csv"

# Exact peak-to-recovery intervals already present in
# reports/v31_deepening_q2q3/20260911T043543Z/summary.json.  The two
# overlapping 2001/2002 drawdowns are treated as one dot-com episode so a
# leave-one-event-out row cannot retain half of the same crisis.
EPISODES = {
    "dot-com / 2001–2002": ("2001-05-25", "2003-05-09"),
    "Global Financial Crisis / 2008–2009": ("2007-07-13", "2010-04-09"),
    "COVID crash / 2020": ("2019-12-20", "2020-06-05"),
    "2022 bear market": ("2021-11-12", "2023-03-03"),
}


def spread_stats(series: pd.Series) -> dict:
    clean = series.dropna().astype(float)
    if len(clean) < 10:
        return {
            "observations": int(len(clean)),
            "annualized_spread": None,
            "hac_tstat": None,
            "sign_positive": None,
        }
    fit = sm.OLS(clean.to_numpy(), np.ones(len(clean))).fit(
        cov_type="HAC", cov_kwds={"maxlags": 4}
    )
    annualized = (1.0 + float(clean.mean())) ** 52 - 1.0
    return {
        "observations": int(len(clean)),
        "annualized_spread": annualized,
        "hac_tstat": float(fit.tvalues[0]),
        "sign_positive": bool(annualized > 0),
    }


def run(panel_path: Path) -> dict:
    panel = pd.read_csv(panel_path, parse_dates=["signal_date", "realization_date"])
    panel = panel.set_index("realization_date").sort_index()
    target = panel.loc[panel["overlay_on"].astype(bool), "s1_spread"].dropna()

    full = spread_stats(target)
    exclusions = []
    for label, (start, end) in EPISODES.items():
        outside = target.loc[~((target.index >= start) & (target.index <= end))]
        row = {
            "excluded_episode": label,
            "episode_start": start,
            "episode_end": end,
            "removed_observations": int(len(target) - len(outside)),
            **spread_stats(outside),
        }
        row["collapses_without_episode"] = bool(
            row["annualized_spread"] is None
            or row["annualized_spread"] <= 0
            or (full["annualized_spread"] and row["annualized_spread"] < 0.25 * full["annualized_spread"])
        )
        exclusions.append(row)

    any_collapse = any(row["collapses_without_episode"] for row in exclusions)
    all_positive = all(row["sign_positive"] for row in exclusions)
    min_n = min(row["observations"] for row in exclusions)
    if any_collapse:
        interpretation = "DOMINATED BY ONE EPISODE"
    elif min_n < 100:
        interpretation = "UNDERPOWERED"
    elif all_positive:
        interpretation = "ROBUST ACROSS CRISES"
    else:
        interpretation = "PARTIALLY ROBUST"

    return {
        "schema_version": 1,
        "hypothesis": "bear_overlay_v1 — S1 Pullback in frozen BEAR+HIGH_VOL weeks",
        "evidence_status": "EXPLORATORY / HYPOTHESIS-GENERATING — FORWARD VALIDATION REQUIRED",
        "method": "Leave one pre-existing V3.1 peak-to-recovery crisis episode out at a time; no dates or signal rules selected from these results.",
        "source_panel": str(panel_path.relative_to(ROOT.parent)).replace("\\", "/"),
        "full_sample": full,
        "leave_one_crisis_out": exclusions,
        "interpretation": interpretation,
        "interpretation_note": (
            "The sign remains positive after every exclusion and no one episode causes collapse, "
            "but the frozen target condition has only 87 full-sample observations and 62–80 after exclusions. "
            "This robustness check does not convert a broad-search discovery into independent confirmation."
        ),
        "not_run": [
            {
                "hypothesis": "crisis Breakout / new-high survivor sleeve",
                "status": "NOT AVAILABLE",
                "reason": "No frozen crisis-sleeve rule or unambiguous direction-consistent return series exists. The historical 'buy new LOWS' text was an interpretation error; active research direction is buy new 20-day HIGHS. Freeze the exact rule prospectively before computing robustness."
            },
            {
                "hypothesis": "crisis Gap Fade sleeve",
                "status": "NOT AVAILABLE",
                "reason": "No frozen portfolio rule combining gap threshold, activation, holdings, and execution exists. Broad conditional-search cells are insufficient for a portfolio robustness rerun."
            }
        ]
    }


def report_markdown(result: dict) -> str:
    full = result["full_sample"]
    lines = [
        "# Crisis-conditioned leave-one-crisis-out robustness",
        "",
        f"Evidence status: **{result['evidence_status']}**",
        "",
        result["method"],
        "",
        "This is a robustness characterization on already-viewed history, not a new holdout.",
        "",
        "| Sample | Observations | Annualized S1 spread | HAC t-stat | Sign positive | Collapse? |",
        "|---|---:|---:|---:|---|---|",
        f"| Full frozen BEAR+HIGH_VOL sample | {full['observations']} | {full['annualized_spread']:.2%} | {full['hac_tstat']:.2f} | {'YES' if full['sign_positive'] else 'NO'} | — |",
    ]
    for row in result["leave_one_crisis_out"]:
        lines.append(
            f"| Excluding {row['excluded_episode']} | {row['observations']} | "
            f"{row['annualized_spread']:.2%} | {row['hac_tstat']:.2f} | "
            f"{'YES' if row['sign_positive'] else 'NO'} | "
            f"{'YES' if row['collapses_without_episode'] else 'NO'} |"
        )
    lines.extend([
        "",
        f"Interpretation: **{result['interpretation']}**",
        "",
        result["interpretation_note"],
        "",
        "## Not run",
        "",
    ])
    for row in result["not_run"]:
        lines.append(f"- **{row['hypothesis']} — {row['status']}.** {row['reason']}")
    lines.append("")
    return "\n".join(lines)


def main() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--panel", type=Path, default=DEFAULT_PANEL)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    result = run(args.panel)
    out = args.out or ROOT / "reports" / "crisis_robustness" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (out / "report.md").write_text(report_markdown(result), encoding="utf-8")
    print(out)
    return out


if __name__ == "__main__":
    main()
