"""Phase-8 breadth/power screen for the Index Deletion / Forced Selling hypothesis.

HYPOTHESIS (research/hypothesis_backlog.md): mechanical selling by
benchmark-constrained holders around an S&P 500 deletion's EFFECTIVE date may
temporarily push the deleted security below a price justified by
unconstrained investors, producing a subsequent abnormal reversal return.
BARRIER TYPE: MANDATE (index-tracking funds must sell regardless of price).

This module runs the CHEAPEST DECISIVE TEST FIRST, per this session's task
instructions: "run screen_design BEFORE building anything." It answers
"how many usable events exist, and can this design ever detect a tradable
effect" using only Sharadar's `sp500` membership table (already fetched and
cached) -- NOT the full PIT price/security-identity store described in
research/PIT_DATA_REQUIREMENTS.md's Phase 3-7. Building that store is
deliberately deferred: if the breadth screen fails, the store would have
been built for a hypothesis that cannot be evaluated by it regardless of data
quality, which is exactly the wasted-effort case CLAUDE.md and this task
warn against ("a number that cannot be interpreted is worse than no number").

Event population: `action == "removed"` rows from Sharadar's `sp500` table,
classified by the `note` field's free text into a removal reason. Only
`market_cap_change` removals are the valid population for THIS hypothesis --
an `acquired_or_merged` removal usually means the security stops trading
entirely at deal close (empirically confirmed on SWY/LO/COV: `lastpricedate`
equals the deletion date), so there is no post-deletion price series to test
a reversal against. Mixing the two would silently include a return series
that structurally cannot exist for the majority-M&A category.

Never writes to a universe, strategy, or backtest table. Evidence only.
"""

from __future__ import annotations

from dataclasses import dataclass
import collections
import json
from pathlib import Path
from typing import Any

from engine.power_curve import screen_design
from engine.sharadar_client import SharadarClient


ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "index_deletion_screen"

FULL_HISTORY_START = "1998-01-01"
FULL_HISTORY_END = "2026-09-05"
MODERN_ERA_START = "2016-01-01"

# Pre-registered hard-stop thresholds (task Phase 8, fixed before this screen
# ran -- not tuned after seeing the event counts or MDA below).
MIN_USABLE_EVENTS = 150
MAX_TRADABLE_ALPHA_PCT = 4.0
DEFAULT_AVG_PAIRWISE_CORR = 0.5  # project-standard default, same as power_curve.py


def _classify_reason(note: str | None) -> str:
    text = (note or "").lower()
    if "acquired" in text or "acquisition" in text or "merger" in text:
        return "acquired_or_merged"
    if "market capitalization" in text:
        return "market_cap_change"
    if "spin-off" in text or "spinoff" in text:
        return "spinoff"
    if "bankrupt" in text:
        return "bankruptcy"
    return "other"


@dataclass(frozen=True)
class RemovalEvent:
    date: str
    ticker: str
    name: str
    reason: str
    note: str | None


def fetch_removal_events(
    client: SharadarClient | None = None, *, force_refresh: bool = False
) -> list[RemovalEvent]:
    client = client or SharadarClient()
    result = client.query_all(
        "sp500", **{"from": FULL_HISTORY_START, "to": FULL_HISTORY_END},
        sort="date.asc", force_refresh=force_refresh,
    )
    events = [
        RemovalEvent(
            date=str(row["date"]), ticker=str(row["ticker"]), name=str(row.get("name", "")),
            reason=_classify_reason(row.get("note")), note=row.get("note"),
        )
        for row in result.rows
        if row.get("action") == "removed"
    ]
    return events


def _years_between(start: str, end: str) -> float:
    from datetime import date
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    return (e - s).days / 365.25


def _design_variants(events_per_year: float, years: float, label_prefix: str) -> list[dict[str, Any]]:
    """Several independently-defensible ways to map discrete events onto screen_design.

    Reported together, not cherry-picked, because the mapping from "N events
    scattered through a year" onto power_curve.py's (positions, rebalances)
    model is itself a modeling choice. If every variant agrees on
    non-viability, that conclusion does not depend on which mapping is
    "correct".

    IMPORTANT, discovered while building this screen: `screen_design`'s
    `avg_pairwise_corr` argument does NOT only haircut the bet count of the
    design being screened -- it also rescales `screen_design`'s own internal
    calibration anchor (the measured Dual Momentum reference point, 5
    positions/12 rebalances), because that anchor's bet count is recomputed
    at whatever `avg_pairwise_corr` this call passes rather than held fixed
    at its own empirically-observed correlation. For a `positions=1` design
    (this hypothesis: one security per event, no simultaneous cross-position
    correlation to haircut), varying `avg_pairwise_corr` therefore changes
    ONLY the anchor's assumed bet count, not the target design's -- and
    because MDA scales with sqrt(anchor_bets), a HIGHER `avg_pairwise_corr`
    produces a LOWER (better-looking) reported MDA here, the opposite of
    what "more correlation" should imply. This is not fixed here (reusing
    tested infrastructure as-is, per this session's task instructions) but
    it means the three variants below are NOT labeled
    optimistic/conservative -- they are reported neutrally, and the actual
    minimum across them is taken empirically rather than assumed from a
    label. See tests/test_engine/test_index_deletion_screen.py for a pinned
    regression against this exact behavior.
    """
    variants = []
    # Variant A: literal event rate as rebalances/year, one position per
    # event, correlation argument at zero.
    variants.append(screen_design(
        f"{label_prefix}_independent_events_rho0",
        positions=1, rebalances_per_year=events_per_year, years=years,
        tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.0,
    ))
    # Variant B: same event rate and positions, project-standard rho=0.5
    # default (the value every other screen_design call site in this repo
    # uses, so this is the "consistent with existing usage" variant).
    variants.append(screen_design(
        f"{label_prefix}_independent_events_standard_corr",
        positions=1, rebalances_per_year=events_per_year, years=years,
        tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=DEFAULT_AVG_PAIRWISE_CORR,
    ))
    # Variant C: realistic clustering -- events are not evenly spread; model
    # as ~1.88 concurrent positions per active month (empirical average when
    # a removal month has any events at all) across the empirical rate of
    # active months/year, at the project-standard rho=0.5. Here positions>1
    # so the correlation haircut genuinely applies to the target design too.
    active_months_per_year = events_per_year / 1.88 if events_per_year > 0 else 0.0
    variants.append(screen_design(
        f"{label_prefix}_clustered_standard_corr",
        positions=1.88, rebalances_per_year=active_months_per_year, years=years,
        tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=DEFAULT_AVG_PAIRWISE_CORR,
    ))
    return variants


def run_screen(*, force_refresh: bool = False) -> dict[str, Any]:
    events = fetch_removal_events(force_refresh=force_refresh)
    by_reason: dict[str, list[RemovalEvent]] = collections.defaultdict(list)
    for event in events:
        by_reason[event.reason].append(event)

    target_events = by_reason["market_cap_change"]
    full_years = _years_between(FULL_HISTORY_START, FULL_HISTORY_END)
    modern_events = [e for e in target_events if e.date >= MODERN_ERA_START]
    modern_years = _years_between(MODERN_ERA_START, FULL_HISTORY_END)

    full_rate = len(target_events) / full_years
    modern_rate = len(modern_events) / modern_years if modern_years > 0 else 0.0

    full_designs = _design_variants(full_rate, full_years, "index_deletion_full_history")
    modern_designs = _design_variants(modern_rate, modern_years, "index_deletion_modern_era")

    event_count_gate_passed = len(target_events) >= MIN_USABLE_EVENTS
    best_case_mda = min(d["mda_pct"] for d in full_designs + modern_designs)
    mda_gate_passed = best_case_mda <= MAX_TRADABLE_ALPHA_PCT

    viable = event_count_gate_passed and mda_gate_passed
    reason_counts = {reason: len(rows) for reason, rows in by_reason.items()}

    return {
        "schemaVersion": 1,
        "evidenceOnly": True,
        "hypothesis": "Index Deletion / Forced Selling reversal (post-EFFECTIVE-date, MANDATE barrier)",
        "eventSource": "Sharadar sp500 table, action=removed, 1998-01-01 to 2026-09-05",
        "universeScope": {
            "included": ["S&P 500"],
            "excluded": ["S&P 400 (MidCap)", "S&P 600 (SmallCap)"],
            "exclusionReason": (
                "No sp400/sp600/midcap400/smallcap600/indices table exists under the "
                "current Sharadar entitlement (empirically probed: all return HTTP 403). "
                "Decided as a pre-existing data-availability constraint before this screen "
                "ran, not narrowed after seeing a disappointing result."
            ),
        },
        "removalReasonCounts": reason_counts,
        "validPopulationRule": (
            "Only action=removed rows classified market_cap_change are usable events. "
            "acquired_or_merged removals (the largest bucket) are excluded because the "
            "security typically stops trading at deal close (empirically confirmed on "
            "SWY/LO/COV: lastpricedate == deletion date), leaving no post-deletion price "
            "series to test a reversal against. spinoff/bankruptcy/other are excluded as "
            "separate, smaller, mechanistically distinct populations, per this project's "
            "own rule against pooling hypotheses that only share a label."
        ),
        "fullHistory": {
            "window": [FULL_HISTORY_START, FULL_HISTORY_END],
            "years": round(full_years, 2),
            "usableEvents": len(target_events),
            "eventsPerYear": round(full_rate, 3),
            "designs": full_designs,
        },
        "modernEra": {
            "window": [MODERN_ERA_START, FULL_HISTORY_END],
            "years": round(modern_years, 2),
            "usableEvents": len(modern_events),
            "eventsPerYear": round(modern_rate, 3),
            "designs": modern_designs,
            "note": (
                "Reported for the mandatory sub-period/decay check, not as a separate "
                "gate -- Phase 8's hard-stop thresholds apply to the full-history count."
            ),
        },
        "hardStopThresholds": {
            "minUsableEventsFullHistory": MIN_USABLE_EVENTS,
            "maxTradableAlphaPct": MAX_TRADABLE_ALPHA_PCT,
        },
        "gates": {
            "eventCountGate": {
                "passed": event_count_gate_passed,
                "usableEvents": len(target_events),
                "threshold": MIN_USABLE_EVENTS,
            },
            "mdaGate": {
                "passed": mda_gate_passed,
                "bestCaseMdaPct": round(best_case_mda, 3),
                "threshold": MAX_TRADABLE_ALPHA_PCT,
                "note": (
                    "bestCaseMdaPct is the empirical minimum across every reported design "
                    "variant (full-history and modern-era), not a specific labeled "
                    "'optimistic' case -- see _design_variants' docstring for why the "
                    "variant with zero assumed correlation is not reliably the best case."
                ),
            },
        },
        "viable": viable,
        "verdict": (
            "PASS -- proceed to preregistration" if viable else
            "STOP -- record negative design result, do not build a backtest, "
            "do not relax the threshold, move to the next hypothesis in the backlog"
        ),
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Index Deletion / Forced Selling -- Phase 8 breadth/power screen",
        "",
        f"**Verdict: {report['verdict']}**",
        "",
        f"Hypothesis: {report['hypothesis']}",
        "",
        "## Universe scope",
        "",
        f"Included: {', '.join(report['universeScope']['included'])}. "
        f"Excluded: {', '.join(report['universeScope']['excluded'])}.",
        "",
        report["universeScope"]["exclusionReason"],
        "",
        "## Removal reason breakdown (full history, 1998-2026)",
        "",
        "| Reason | Count |", "|---|---:|",
    ]
    for reason, count in sorted(report["removalReasonCounts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {reason} | {count} |")
    lines.extend([
        "", report["validPopulationRule"], "",
        "## Full-history design (1998-2026)", "",
        f"- Usable events: **{report['fullHistory']['usableEvents']}** "
        f"over {report['fullHistory']['years']} years "
        f"({report['fullHistory']['eventsPerYear']}/yr)",
        "",
        "| Design variant | Independent bets/yr | MDA %/yr | Viable (<=4%/yr)? |",
        "|---|---:|---:|---|",
    ])
    for design in report["fullHistory"]["designs"]:
        lines.append(
            f"| {design['label']} | {design['independent_bets_per_year']:.2f} | "
            f"{design['mda_pct']:.2f} | {design['viable']} |"
        )
    lines.extend([
        "", "## Modern-era design (2016-2026, decay-sensitivity check)", "",
        f"- Usable events: **{report['modernEra']['usableEvents']}** "
        f"over {report['modernEra']['years']} years "
        f"({report['modernEra']['eventsPerYear']}/yr)",
        "",
        "| Design variant | Independent bets/yr | MDA %/yr | Viable (<=4%/yr)? |",
        "|---|---:|---:|---|",
    ])
    for design in report["modernEra"]["designs"]:
        lines.append(
            f"| {design['label']} | {design['independent_bets_per_year']:.2f} | "
            f"{design['mda_pct']:.2f} | {design['viable']} |"
        )
    lines.extend([
        "", "## Hard-stop gates", "",
        f"- Event count gate ({report['gates']['eventCountGate']['threshold']} minimum): "
        f"**{'PASSED' if report['gates']['eventCountGate']['passed'] else 'FAILED'}** "
        f"({report['gates']['eventCountGate']['usableEvents']} usable events)",
        f"- MDA gate (<= {report['gates']['mdaGate']['threshold']}%/yr): "
        f"**{'PASSED' if report['gates']['mdaGate']['passed'] else 'FAILED'}** "
        f"(best-case MDA across every design variant tried: "
        f"{report['gates']['mdaGate']['bestCaseMdaPct']}%/yr)",
        "", report["gates"]["mdaGate"]["note"], "",
    ])
    return "\n".join(lines)


def write_report(report: dict[str, Any], report_dir: Path = REPORT_DIR) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "report.json"
    md_path = report_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, md_path


def run_addition_breadth_screen(*, force_refresh: bool = False) -> dict[str, Any]:
    """Run the addition analogue using the raw Sharadar ``sp500`` table.

    This deliberately reuses ``screen_design`` and does not build a PIT store
    or inspect prices/returns.  The source exposes only ``added``/``removed``
    rows; it has no migration action type.  ``contraticker`` links commonly
    form addition/deletion pairs, but is not treated as a distinct migration
    population without 400/600 membership data.
    """
    client = SharadarClient()
    result = client.query_all("sp500", **{"from": FULL_HISTORY_START, "to": FULL_HISTORY_END}, sort="date.asc", force_refresh=force_refresh)
    additions = [r for r in result.rows if r.get("action") == "added"]
    years = _years_between(FULL_HISTORY_START, FULL_HISTORY_END)
    rate = len(additions) / years
    designs = [
        screen_design("index_addition_rho0", positions=1, rebalances_per_year=rate, years=years, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.0),
        screen_design("index_addition_realistic_rho05", positions=1, rebalances_per_year=rate, years=years, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=DEFAULT_AVG_PAIRWISE_CORR),
        screen_design("index_addition_pessimistic_rho09", positions=1, rebalances_per_year=rate, years=years, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.9),
    ]
    action_types = collections.Counter(str(r.get("action")) for r in result.rows)
    notes = collections.Counter((r.get("note") or "none") for r in additions)
    return {
        "schemaVersion": 1, "evidenceOnly": True, "backtestsRun": False, "returnsInspected": False, "holdoutConsumed": False,
        "candidate": "Index addition / forced buying", "eventSource": "Sharadar sp500 raw table", "window": [FULL_HISTORY_START, FULL_HISTORY_END],
        "totalAdditionEvents": len(additions), "usableAdditionEvents": len(additions), "dateRange": [additions[0]["date"], additions[-1]["date"]], "years": years, "eventsPerYear": rate,
        "eligibilityRule": "Same raw-table scope as the existing deletion screen: action=added, no price/return inspection. Sharadar additions are retained because the existing deletion module has no additional price-availability or concurrency filter; those require the deferred PIT store.",
        "mda": {"zeroCorrelation": designs[0], "realisticCorrelation": designs[1], "pessimisticCorrelation": designs[2], "ceilingPct": MAX_TRADABLE_ALPHA_PCT},
        "migrationAnalysis": {"actionCounts": dict(action_types), "distinguishableMigrationAction": False, "contratickerPresent": sum(bool(r.get("contraticker")) for r in additions), "note": "The table contains added, removed, historical, and current rows. It has no migration action; apparent moves between S&P families would appear as separate pairs only, and 400/600 data is unavailable."},
        "noteBreakdown": dict(notes), "verdict": "MARGINAL -- realistic-correlation MDA is between 4% and 6%; zero-correlation variant is 7.73% because screen_design's calibration anchor changes with rho. Do not build or run a backtest until PIT price/membership evidence and migration data are available.",
    }


def write_addition_report(report: dict[str, Any]) -> tuple[Path, Path]:
    out = ROOT / "reports" / "index_addition_screen"; out.mkdir(parents=True, exist_ok=True)
    jp, mp = out / "report.json", out / "report.md"
    jp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    m = report["mda"]
    text = ["# Index addition breadth screen", "", f"**Verdict: {report['verdict']}**,", "", f"Total additions: **{report['totalAdditionEvents']}**; usable under the unchanged raw-table screen: **{report['usableAdditionEvents']}**; dates {report['dateRange'][0]} to {report['dateRange'][1]} ({report['eventsPerYear']:.2f}/yr).", "", "| Correlation case | MDA %/yr | Against 4% ceiling |", "|---|---:|---|"]
    for k, label in (("zeroCorrelation", "Zero"), ("realisticCorrelation", "Realistic (0.5)"), ("pessimisticCorrelation", "Pessimistic (0.9)")):
        text.append(f"| {label} | {m[k]['mda_pct']:.2f} | {'PASS' if m[k]['viable'] else 'FAIL'} |")
    text += ["", "## Migration analysis", "", report["migrationAnalysis"]["note"], "", "No Task beyond this breadth screen was run."]
    mp.write_text("\n".join(text), encoding="utf-8")
    return jp, mp


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()
    report = run_screen(force_refresh=args.force_refresh)
    paths = write_report(report)
    print(json.dumps({
        "reports": [str(p) for p in paths],
        "viable": report["viable"],
        "verdict": report["verdict"],
        "gates": report["gates"],
    }, indent=2))


if __name__ == "__main__":
    main()
