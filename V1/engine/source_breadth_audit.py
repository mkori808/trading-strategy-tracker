"""Bounded source audit for family-index membership and leveraged ETF inputs.

This module only evaluates whether public source coverage is sufficient to
justify infrastructure. It does not inspect strategy returns or run a
backtest. The breadth projection is a transparent sqrt(T) approximation based
on the existing 500-only MDA and an assumed three-fold event family.
"""

from __future__ import annotations

from datetime import date
import json
from math import sqrt
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "source_breadth_audit"
BASE_MDA_PCT = 4.157313997
BASE_YEARS = 28.5
FAMILY_MULTIPLIER = 3.0
TARGET_MDA_PCT = 4.0
AS_OF = date(2026, 9, 5)


def projected_mda(start: date, *, base_mda: float = BASE_MDA_PCT) -> float:
    years = (AS_OF - start).days / 365.2425
    if years <= 0:
        raise ValueError("source start must precede audit date")
    return base_mda * sqrt(BASE_YEARS / (years * FAMILY_MULTIPLIER))


def years_required(*, base_mda: float = BASE_MDA_PCT) -> float:
    return BASE_YEARS * (base_mda / TARGET_MDA_PCT) ** 2 / FAMILY_MULTIPLIER


def build_report() -> dict[str, Any]:
    required = years_required()
    sources = [
        {
            "source": "pitindex",
            "url": "https://github.com/arielNacamulli/pitindex",
            "familyCoverage": {"sp500": "2005-01-03", "sp400": "2011-11-20", "sp600": "2021-03-26"},
            "estimatedYears": (AS_OF - date(2021, 3, 26)).days / 365.2425,
            "projectedMdaPct": projected_mda(date(2021, 3, 26)),
            "status": "PARTIAL_INSUFFICIENT_HISTORY",
            "notes": "The composite 1500 floor is the S&P 600 start; project is community-maintained and documents incomplete historical changes.",
        },
        {
            "source": "indexkit",
            "url": "https://github.com/kovagent/indexkit",
            "familyCoverage": {"sp600": "2019-11-01"},
            "estimatedYears": (AS_OF - date(2019, 11, 1)).days / 365.2425,
            "projectedMdaPct": projected_mda(date(2019, 11, 1)),
            "status": "PARTIAL_INSUFFICIENT_HISTORY",
            "notes": "Community reconstruction using N-PORT, sponsor files, and Wayback; does not establish a complete 2000-era family ledger.",
        },
        {
            "source": "S&P DJI press releases",
            "url": "https://press.spglobal.com/2026-03-06-Vertiv-Holdings%2C-Lumentum-Holdings%2C-Coherent%2C-and-EchoStar-Set-to-Join-S-P-500-Others-to-Join-S-P-100%2C-S-P-MidCap-400-and-S-P-SmallCap-600",
            "familyCoverage": "individual announcements",
            "estimatedYears": None,
            "projectedMdaPct": None,
            "status": "NOT_MACHINE_BOUNDED",
            "notes": "Primary announcements confirm individual changes but no exhaustive, normalized historical archive was established in this audit.",
        },
    ]
    return {
        "schemaVersion": 1,
        "evidenceOnly": True,
        "backtestsRun": False,
        "returnsInspected": False,
        "holdoutConsumed": False,
        "preregistrationsCreated": False,
        "baseMdaPct": BASE_MDA_PCT,
        "baseYears": BASE_YEARS,
        "familyMultiplier": FAMILY_MULTIPLIER,
        "yearsRequiredForFourPctMda": required,
        "sources": sources,
        "verdict": "STOP_SCREENED_OUT_BREADTH",
        "reason": "The bounded free S&P 400/600 sources do not provide enough complete independent history to clear a 4% MDA target; official releases remain unbounded for automated reconstruction.",
    }


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# S&P 400/600 source breadth audit",
        "",
        "> Diagnostic only: no returns, backtest, holdout, or preregistration was used.",
        "",
        f"Verdict: **{report['verdict']}**",
        "",
        report["reason"],
        "",
        f"Using the existing 500-only MDA of {report['baseMdaPct']:.2f}% over {report['baseYears']:.1f} years and a conservative three-fold event-family breadth multiplier, a complete history needs about **{report['yearsRequiredForFourPctMda']:.2f} years** to reach the 4% MDA ceiling.",
        "",
        "| Source | Coverage | Projected MDA | Status |",
        "|---|---|---:|---|",
    ]
    for row in report["sources"]:
        coverage = row["familyCoverage"] if isinstance(row["familyCoverage"], str) else ", ".join(f"{k}: {v}" for k, v in row["familyCoverage"].items())
        mda = "—" if row["projectedMdaPct"] is None else f"{row['projectedMdaPct']:.2f}%"
        lines.append(f"| [{row['source']}]({row['url']}) | {coverage} | {mda} | {row['status']} |")
        lines.append(f"|  | {row['notes']} |  |  |")
    lines.extend(["", "Do not build a family-index strategy pipeline unless an identity-safe, exhaustive change ledger covering at least the required horizon is obtained.", ""])
    return "\n".join(lines)


def write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "report.json"
    md_path = REPORT_DIR / "report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    return md_path, json_path


if __name__ == "__main__":
    report = build_report()
    write_report(report)
    print(json.dumps({"verdict": report["verdict"], "yearsRequired": report["yearsRequiredForFourPctMda"]}, indent=2))
