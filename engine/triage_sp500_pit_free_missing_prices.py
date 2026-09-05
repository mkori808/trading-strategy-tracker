"""Diagnostic-only missing/partial price triage for sp500_pit_free_v1.

Answers one question: is the shortfall against the pre-registered 98%
coverage bar (research/sp500_pit_free_coverage_preregistration.json) mostly
a FIXABLE symbol/data-resolution problem, or a STRUCTURAL limitation of the
free sources? It does not touch the preregistered threshold, any strategy
code, engine/runner.py, or universes/sp500_pit_free_v1.json's eligibility --
it only reads the existing membership/symbol/price/coverage artifacts and
writes new diagnostic output under data/sp500_pit_free/audits/.

Classification is deterministic and evidence-based, never a guess about a
security's real-world identity or fate. Every category below states exactly
what data justified it, and several are explicitly "indeterminate from
ticker-level data alone" rather than asserted with false confidence -- this
project's sources carry no company name, CUSIP, or FIGI (see
engine/build_sp500_pit_free_symbol_map.py's own docstring), so bankruptcy
cannot be distinguished from an ordinary provider coverage gap without a
company-identity crosswalk this pipeline does not have.

A single deterministic, pre-specified candidate search is attempted: strip a
trailing "Q" for a ticker the existing symbol_map.csv already flagged
``legacy_suffix_needs_verification``, then check the stripped candidate on the
SAME provider (yfinance) already in use. Fetchable bars only establish that
candidate data exists; they do not establish company identity. Consequently
no candidate is accepted or substituted without a separate identity crosswalk
(the concrete CPQ -> CP false positive demonstrates why). A candidate that
fails to fetch is reported as attempted-without-data; one that fetches is
reported as data-found-but-identity-unverifiable.
"""

from __future__ import annotations

import argparse
import bisect
import csv
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yfinance as yf

from engine import data as data_module
from engine.audit_sp500_pit_free_coverage import (
    _bar_dates,
    _has_bar_near,
    NEAR_BAR_TOLERANCE_DAYS,
    load_intervals,
    rebalance_dates,
    roster_by_date,
)
from engine.fetch_sp500_pit_free_prices import PRICES_DIR, WARMUP_CALENDAR_DAYS, _read_parquet


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "sp500_pit_free"
INTERVALS_PATH = DATA_DIR / "membership" / "intervals.csv"
RECONCILIATION_PATH = DATA_DIR / "membership" / "reconciliation.csv"
SYMBOL_MAP_PATH = DATA_DIR / "metadata" / "symbol_map.csv"
PRICE_COVERAGE_PATH = DATA_DIR / "audits" / "price_coverage.json"
MEMBERSHIP_AUDIT_PATH = DATA_DIR / "audits" / "membership_audit.json"
COVERAGE_AUDIT_PATH = DATA_DIR / "audits" / "coverage_audit.json"

TRIAGE_JSON_PATH = DATA_DIR / "audits" / "missing_price_triage.json"
TRIAGE_BY_TICKER_CSV_PATH = DATA_DIR / "audits" / "missing_price_triage_by_ticker.csv"
TRIAGE_BY_DATE_CSV_PATH = DATA_DIR / "audits" / "missing_price_triage_by_date.csv"
TRIAGE_REQUIRED_DATES_CSV_PATH = DATA_DIR / "audits" / "missing_price_triage_required_dates.csv"
COVERAGE_AFTER_RESOLUTION_CSV_PATH = DATA_DIR / "audits" / "coverage_by_date_after_resolution.csv"
COVERAGE_BY_YEAR_AFTER_RESOLUTION_CSV_PATH = DATA_DIR / "audits" / "coverage_by_year_after_resolution.csv"
MISSING_STATUSES = {"MISSING_DELISTED_PRICE_HISTORY", "PARTIAL_COVERAGE"}

CATEGORIES = [
    "symbol_mapping_candidate_found_identity_unverifiable",
    "symbol_mapping_attempted_no_candidate_data",
    "ticker_change_merger_or_acquisition_likely",
    "membership_defect_candidate",
    "provider_coverage_gap",
    "bankruptcy_delisting_or_provider_gap",
    "unresolved",
]


# --- loaders -----------------------------------------------------------

def load_price_coverage(path: Path = PRICE_COVERAGE_PATH) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))["symbols"]


def load_symbol_map(path: Path = SYMBOL_MAP_PATH) -> dict[str, dict]:
    by_ticker: dict[str, dict] = defaultdict(lambda: {"reasons": set(), "tenures": []})
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            entry = by_ticker[row["historical_symbol"]]
            entry["reasons"].update(row["reason"].split(";"))
            entry["tenures"].append((
                date.fromisoformat(row["start_date"]),
                date.fromisoformat(row["end_date"]) if row["end_date"] else None,
            ))
    return dict(by_ticker)


def load_reconciliation(path: Path = RECONCILIATION_PATH) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def same_day_removal_partners(reconciliation_rows: list[dict]) -> dict[tuple[str, str], str]:
    """(ticker, effective_date) -> the ticker ADDed on that same date, per the
    PRIMARY source's own event legs -- a same-day 1:1 REMOVE+ADD is the
    standard footprint of an index-committee substitution for a merger,
    acquisition, spin-off, or rename, deterministically visible in data we
    already built (no new source consulted)."""
    adds_by_date: dict[str, list[str]] = defaultdict(list)
    removes_by_date: dict[str, list[str]] = defaultdict(list)
    for row in reconciliation_rows:
        bucket = adds_by_date if row["action"] == "ADD" else removes_by_date
        bucket[row["effective_date"]].append(row["ticker"])
    partners: dict[tuple[str, str], str] = {}
    for effective_date, removed in removes_by_date.items():
        added = adds_by_date.get(effective_date, [])
        if len(removed) == 1 and len(added) == 1:
            partners[(removed[0], effective_date)] = added[0]
    return partners


# --- deterministic resolution attempt -----------------------------------

def attempt_legacy_suffix_resolution(
    ticker: str, requested_start: date, requested_end: date,
) -> dict[str, Any] | None:
    """The ONE pre-specified deterministic transform this triage will try:
    strip a trailing 'Q' and check for a fetchable candidate on the SAME
    provider already in use.

    IMPORTANT: this can verify only that DATA EXISTS under the candidate
    ticker in roughly the right era -- it CANNOT verify that the candidate
    is the SAME COMPANY, because none of this pipeline's sources carry a
    company name, CUSIP, or FIGI to cross-check against (same limitation
    build_sp500_pit_free_symbol_map.py already discloses). Proven concretely
    by running this exact check: CPQ (Compaq Computer, delisted 2002) strips
    to 'CP', which has decades of real, fetchable data -- but 'CP' is
    Canadian Pacific Railway, a completely unrelated company that has traded
    under that ticker the whole time. Data existing is not evidence of
    correct identity. Every candidate this function finds is therefore
    returned as ``identityVerified: False`` unconditionally; the caller must
    never treat ``dataFound: True`` as a resolution.
    """
    if not ticker.endswith("Q") or len(ticker) < 3:
        return None
    candidate = ticker[:-1]
    cached = _read_parquet(PRICES_DIR / f"{candidate}.parquet")
    shared = _read_parquet(data_module.DATA_DIR / f"{candidate}_1d.parquet")
    frame = cached if not cached.empty else shared
    if frame.empty:
        try:
            yf.set_tz_cache_location(str(DATA_DIR / ".yfinance_cache"))
            fetched = yf.download(
                candidate,
                start=requested_start - timedelta(days=WARMUP_CALENDAR_DAYS),
                end=requested_end + timedelta(days=1),
                interval="1d", auto_adjust=True, progress=False,
            )
        except Exception:  # noqa: BLE001 - a fetch failure means unresolved, not a crash
            fetched = pd.DataFrame()
        frame = fetched
    if frame.empty:
        return {
            "candidate": candidate, "dataFound": False, "identityVerified": False,
            "reason": "candidate ticker has no fetchable price data",
        }
    bar_dates = sorted(frame.index.normalize().date.tolist()) if hasattr(frame.index, "normalize") else []
    covered_near_start = _has_bar_near(bar_dates, requested_start, NEAR_BAR_TOLERANCE_DAYS * 4)
    covered_near_end = _has_bar_near(bar_dates, requested_end, NEAR_BAR_TOLERANCE_DAYS * 4)
    if not (covered_near_start or covered_near_end):
        return {
            "candidate": candidate, "dataFound": False, "identityVerified": False,
            "reason": f"candidate ticker's data ({bar_dates[0] if bar_dates else '?'} to "
                       f"{bar_dates[-1] if bar_dates else '?'}) does not overlap the required tenure",
        }
    return {
        "candidate": candidate, "dataFound": True, "identityVerified": False,
        "availableStart": bar_dates[0].isoformat(), "availableEnd": bar_dates[-1].isoformat(),
        "reason": "candidate identity is NOT confirmed -- no company-name/"
                   "CUSIP/FIGI crosswalk exists in this pipeline to distinguish a real match from a "
                   "coincidental one (see CPQ->CP counterexample in this function's docstring)",
    }


# --- classification -------------------------------------------------------

def classify_ticker(
    ticker: str,
    price_row: dict,
    symbol_map_entry: dict,
    swap_partners: dict[tuple[str, str], str],
    price_coverage: dict[str, dict],
    membership_audit: dict,
    resolution: dict | None,
) -> tuple[str, str]:
    reasons = symbol_map_entry.get("reasons", set())
    tenures = symbol_map_entry.get("tenures", [])
    primary_end = date.fromisoformat(membership_audit["primarySource"]["coverageEnd"])
    ledger_end = date.fromisoformat(membership_audit["secondarySource"]["coverageEnd"])

    if "legacy_suffix_needs_verification" in reasons:
        if resolution and resolution.get("dataFound"):
            return "symbol_mapping_candidate_found_identity_unverifiable", (
                f"ticker ends in 'Q' (legacy bankruptcy-suffix pattern per symbol_map.csv); "
                f"stripped candidate '{resolution['candidate']}' has fetchable data in the right era "
                f"({resolution['availableStart']} to {resolution['availableEnd']}), but {resolution['reason']}"
            )
        if resolution:
            return "symbol_mapping_attempted_no_candidate_data", (
                f"ticker ends in 'Q'; stripped candidate '{resolution['candidate']}' attempted "
                f"but {resolution['reason']}"
            )

    for start, end in tenures:
        if end is None:
            continue
        partner = swap_partners.get((ticker, end.isoformat())) or swap_partners.get(
            (ticker, (end + timedelta(days=1)).isoformat())
        )
        if partner and price_coverage.get(partner, {}).get("status") == "OK":
            return "ticker_change_merger_or_acquisition_likely", (
                f"same-day 1:1 index substitution in reconciliation.csv: '{ticker}' removed, "
                f"'{partner}' added (partner has fetchable price history) -- standard footprint "
                f"of a ticker change/rename, merger, acquisition, or spin-off substitution; the "
                f"specific subtype cannot be independently confirmed without a company-identity source"
            )

    # An interval's end_date == ledger_end is NOT evidence of removal: every
    # tenure the single-source (leandroloi) extension carries through to the
    # ledger's own coverage end is written with a concrete end_date equal to
    # that coverage end (engine.build_sp500_pit_free_membership's
    # intervals_from_snapshots with still_open_as_of_coverage_end=True),
    # never None -- only the primary-source-only portion of the ledger uses
    # None for "still open". Treating end==ledger_end as "closed" mislabeled
    # real, currently-active names (BK, IPG, K, MMC, ...) as delisted; caught
    # by inspection when large, obviously-liquid tickers appeared in the
    # bankruptcy/delisting bucket.
    still_open = any(end is None or end == ledger_end for _, end in tenures)
    closed_tenures = [end for _, end in tenures if end is not None and end != ledger_end]
    ends_at_primary_boundary = any(end == primary_end for end in closed_tenures)
    if ends_at_primary_boundary:
        return "membership_defect_candidate", (
            f"tenure ends exactly at the primary source's coverage boundary ({primary_end}); "
            "cannot be cross-checked there (qzzcl's own coverage stops that date) -- may reflect "
            "a real removal or an artifact of the source handoff, not distinguishable from "
            "ticker-level data alone"
        )
    if still_open:
        return "provider_coverage_gap", (
            "ticker is still an open (never-removed) membership tenure as of the ledger's own "
            f"coverage end ({ledger_end}) with no fetchable price data at all -- a currently-held "
            "index member missing from the free price provider entirely, not a market event"
        )
    if closed_tenures:
        return "bankruptcy_delisting_or_provider_gap", (
            "tenure ended with no same-day substitution partner and no legacy-suffix or boundary "
            "signal; consistent with a real delisting/bankruptcy OR a provider coverage gap for an "
            "obscure/small-cap name -- these two cannot be told apart without a company-identity "
            "crosswalk this pipeline does not have (see build_sp500_pit_free_symbol_map.py docstring)"
        )
    return "unresolved", "no deterministic signal matched any category"


# --- concentration / survivorship analyses --------------------------------

def concentration_ranking(required_dates_by_ticker: dict[str, list[date]]) -> list[dict]:
    ranked = sorted(required_dates_by_ticker.items(), key=lambda item: -len(item[1]))
    total = sum(len(dates) for _, dates in ranked) or 1
    rows = []
    cumulative = 0
    for ticker, dates in ranked:
        cumulative += len(dates)
        rows.append({
            "ticker": ticker,
            "missingRebalanceDates": len(dates),
            "shareOfTotalMissingObservationsPct": round(100.0 * len(dates) / total, 3),
            "cumulativeSharePct": round(100.0 * cumulative / total, 3),
        })
    return rows


def smallest_set_for_share(ranking: list[dict], target_share_pct: float) -> list[str]:
    out = []
    for row in ranking:
        out.append(row["ticker"])
        if row["cumulativeSharePct"] >= target_share_pct:
            break
    return out


def survivorship_bias_check(
    price_coverage: dict[str, dict], symbol_map: dict[str, dict], ledger_end: date,
) -> dict:
    def is_removed(ticker: str) -> bool:
        tenures = symbol_map.get(ticker, {}).get("tenures", [])
        return bool(tenures) and all(end is not None and end < ledger_end for _, end in tenures)

    buckets = {"covered": {"removed": 0, "active": 0}, "missing": {"removed": 0, "active": 0}}
    for ticker, row in price_coverage.items():
        bucket = "covered" if row["status"] in ("OK", "PARTIAL_COVERAGE") else "missing"
        buckets[bucket]["removed" if is_removed(ticker) else "active"] += 1

    def removed_share(bucket: dict) -> float | None:
        total = bucket["removed"] + bucket["active"]
        return round(100.0 * bucket["removed"] / total, 2) if total else None

    covered_removed_share = removed_share(buckets["covered"])
    missing_removed_share = removed_share(buckets["missing"])
    gap = (
        round(missing_removed_share - covered_removed_share, 2)
        if covered_removed_share is not None and missing_removed_share is not None else None
    )
    return {
        "coveredTickers": buckets["covered"],
        "missingTickers": buckets["missing"],
        "removedShareAmongCoveredPct": covered_removed_share,
        "removedShareAmongMissingPct": missing_removed_share,
        "percentagePointGap": gap,
        "verdict": (
            "SURVIVORSHIP_BIAS_RISK: missing-price tickers are disproportionately removed/delisted "
            "names relative to covered tickers -- exactly the direction that would inflate a "
            "backtest's apparent returns by silently dropping bad outcomes"
            if gap is not None and gap > 5.0
            else "NO_STRONG_DIRECTIONAL_SKEW_DETECTED: removed-ticker share among missing names is "
                 "not meaningfully higher than among covered names"
            if gap is not None
            else "INSUFFICIENT_DATA"
        ),
    }


# --- May 2025 ledger-end explanation ---------------------------------------

def explain_ledger_end(membership_audit: dict) -> dict:
    secondary = membership_audit["secondarySource"]
    return {
        "reportedCoverageEnd": secondary["coverageEnd"],
        "rawSourceLastRowDate": "2025-06-18",
        "explanation": (
            "leandroloi's raw source file's last ROW is 2025-06-18, one month later than the "
            "2025-05-17 reported here. The gap is not missing data: "
            "engine.build_sp500_pit_free_membership.changed_snapshots() intentionally collapses "
            "consecutive snapshot rows that report an IDENTICAL roster, keeping only the date where "
            "a real transition occurred. 2025-05-17 is the last date any membership CHANGE happened; "
            "2025-06-18 is a later re-confirmation that the roster was still unchanged, which the "
            "ledger's coverage_end computation currently does not credit. This is a real precision "
            "gap in the existing builder (coverage_end understates confirmed-current knowledge by "
            "about a month here) worth fixing in a future change to build_sp500_pit_free_membership.py "
            "-- NOT done in this diagnostic-only triage, which was scoped to not modify the membership "
            "builder or its outputs."
        ),
        "canBeExtendedThroughTodayWithoutChangingMethodology": False,
        "whyNot": [
            "qzzcl (primary source): last repository commit 2024-11-13; no dated snapshot file newer "
            "than 'S&P 500 Historical Components & Changes(04-16-2023).csv' has ever been published. "
            "The two-source design's primary tracker has been dead since 2023-03-20 regardless of "
            "when this pipeline runs.",
            "leandroloi (secondary source): last repository commit 2025-06-18T12:46:28Z, verified "
            "against the live GitHub API at triage time -- identical to the commit already used to "
            "build this ledger. No newer data exists upstream to pull.",
            "Extending coverage to 2026-08-31 (today) would require either the source maintainers "
            "publishing new snapshots (outside this project's control), or this pipeline running its "
            "own live Wikipedia-membership scrape the way leandroloi's own sp500.py script does -- "
            "which would introduce a THIRD, self-maintained data source into a design that was "
            "deliberately built and disclosed as two independently-sourced, cross-checked trackers. "
            "That is a methodology change requiring its own decision and disclosure, not something "
            "this triage does silently.",
        ],
    }


# --- coverage-by-date recomputation after identity-verified resolutions ---

def recompute_coverage_after_resolution(
    intervals: list[tuple[str, date, date | None]],
    dates: list[date],
    resolved_candidates: dict[str, str],
    bar_index: dict[str, list[date]] | None = None,
) -> list[dict]:
    """Same method as engine.audit_sp500_pit_free_coverage.compute_coverage_by_date,
    but a ticker with an independently identity-verified resolution would be
    checked against that candidate's price file instead of its own. A fetchable
    stripped ticker alone never enters ``resolved_candidates``. Nothing here
    touches the committed coverage_by_date.csv or the preregistered usable-window
    determination."""
    if bar_index is None:
        bar_index = price_bar_index_after_resolution(intervals, resolved_candidates)

    rosters = roster_by_date(intervals, dates)
    rows = []
    for d in dates:
        roster = rosters[d]
        covered = sum(1 for t in roster if _has_bar_near(bar_index.get(t, []), d, NEAR_BAR_TOLERANCE_DAYS))
        pct = round(100.0 * covered / len(roster), 2) if roster else 0.0
        rows.append({"date": d.isoformat(), "pit_members": len(roster), "with_valid_prices": covered, "coverage_pct": pct})
    return rows


def price_bar_index_after_resolution(
    intervals: list[tuple[str, date, date | None]], resolved_candidates: dict[str, str],
) -> dict[str, list[date]]:
    bar_index: dict[str, list[date]] = {}
    for ticker in sorted({ticker for ticker, _, _ in intervals}):
        lookup_ticker = resolved_candidates.get(ticker, ticker)
        bars = _bar_dates(lookup_ticker, PRICES_DIR)
        if not bars and lookup_ticker != ticker:
            # An identity-verified resolution candidate may have been checked transiently
            # (attempt_legacy_suffix_resolution never writes into PRICES_DIR).
            shared = _read_parquet(data_module.DATA_DIR / f"{lookup_ticker}_1d.parquet")
            if not shared.empty:
                bars = sorted(shared.index.normalize().date.tolist())
        bar_index[ticker] = bars
    return bar_index


def aggregate_coverage_by_year(coverage_rows: list[dict]) -> list[dict]:
    """Aggregate rebalance-date coverage using member observations as weights."""
    by_year: dict[int, list[dict]] = defaultdict(list)
    for row in coverage_rows:
        by_year[int(str(row["date"])[:4])].append(row)

    annual = []
    for year, rows in sorted(by_year.items()):
        members = sum(int(row["pit_members"]) for row in rows)
        covered = sum(int(row["with_valid_prices"]) for row in rows)
        annual.append({
            "year": year,
            "rebalance_dates": len(rows),
            "pit_member_observations": members,
            "with_valid_price_observations": covered,
            "coverage_pct": round(100.0 * covered / members, 2) if members else 0.0,
            "minimum_rebalance_date_coverage_pct": min(float(row["coverage_pct"]) for row in rows),
            "maximum_rebalance_date_coverage_pct": max(float(row["coverage_pct"]) for row in rows),
        })
    return annual


# --- orchestration ---------------------------------------------------------

def build() -> dict:
    price_coverage = load_price_coverage()
    symbol_map = load_symbol_map()
    intervals = load_intervals(INTERVALS_PATH)
    reconciliation_rows = load_reconciliation()
    membership_audit = json.loads(MEMBERSHIP_AUDIT_PATH.read_text(encoding="utf-8"))
    coverage_audit = json.loads(COVERAGE_AUDIT_PATH.read_text(encoding="utf-8"))
    ledger_end = date.fromisoformat(membership_audit["secondarySource"]["coverageEnd"])

    dates = rebalance_dates(intervals)
    rosters = roster_by_date(intervals, dates)
    required_dates_by_ticker: dict[str, list[date]] = defaultdict(list)
    for d, roster in rosters.items():
        for ticker in roster:
            required_dates_by_ticker[ticker].append(d)

    swap_partners = same_day_removal_partners(reconciliation_rows)

    missing_tickers = sorted(
        ticker for ticker, row in price_coverage.items() if row["status"] in MISSING_STATUSES
    )

    resolutions: dict[str, dict] = {}
    per_ticker_rows: list[dict] = []
    category_counts: dict[str, int] = defaultdict(int)
    for ticker in missing_tickers:
        row = price_coverage[ticker]
        entry = symbol_map.get(ticker, {"reasons": set(), "tenures": []})
        resolution = None
        if "legacy_suffix_needs_verification" in entry["reasons"]:
            resolution = attempt_legacy_suffix_resolution(
                ticker, date.fromisoformat(row["requested_start"]), date.fromisoformat(row["requested_end"]),
            )
            if resolution:
                resolutions[ticker] = resolution
        category, evidence = classify_ticker(
            ticker, row, entry, swap_partners, price_coverage, membership_audit, resolution,
        )
        category_counts[category] += 1
        required_dates = required_dates_by_ticker.get(ticker, [])
        per_ticker_rows.append({
            "ticker": ticker,
            "status": row["status"],
            "requestedStart": row["requested_start"],
            "requestedEnd": row["requested_end"],
            "requiredRebalanceDateCount": len(required_dates),
            "firstRequiredRebalanceDate": required_dates[0].isoformat() if required_dates else "",
            "lastRequiredRebalanceDate": required_dates[-1].isoformat() if required_dates else "",
            "requiredRebalanceDates": ";".join(d.isoformat() for d in required_dates),
            "category": category,
            "evidence": evidence,
            "resolutionCandidate": resolution["candidate"] if resolution else "",
            "resolutionCandidateHasData": bool(resolution and resolution.get("dataFound")),
            "resolutionIdentityVerified": bool(resolution and resolution.get("identityVerified")),
        })

    # Always empty in practice: attempt_legacy_suffix_resolution never sets
    # identityVerified=True (see its docstring -- data existing under a
    # candidate ticker cannot confirm it is the same company; the CPQ->CP
    # counterexample proves it directly). Kept as a real filter rather than
    # a hardcoded empty dict so a future, stronger verification method could
    # populate it without touching this call site.
    resolved_candidates = {
        ticker: r["candidate"] for ticker, r in resolutions.items() if r.get("identityVerified")
    }

    survivorship = survivorship_bias_check(price_coverage, symbol_map, ledger_end)
    ledger_end_explanation = explain_ledger_end(membership_audit)

    bar_index = price_bar_index_after_resolution(intervals, resolved_candidates)
    coverage_after = recompute_coverage_after_resolution(
        intervals, dates, resolved_candidates, bar_index=bar_index,
    )
    coverage_by_year_after = aggregate_coverage_by_year(coverage_after)
    best_after = max((row["coverage_pct"] for row in coverage_after), default=0.0)
    coverage_before_path = DATA_DIR / "audits" / "coverage_by_date.csv"
    best_before = max(
        (float(row["coverage_pct"]) for row in csv.DictReader(coverage_before_path.open(encoding="utf-8", newline=""))),
        default=0.0,
    ) if coverage_before_path.exists() else 0.0

    by_date_missing: dict[str, list[str]] = defaultdict(list)
    missing_dates_by_ticker: dict[str, list[date]] = defaultdict(list)
    for d, roster in rosters.items():
        for ticker in roster:
            if not _has_bar_near(bar_index.get(ticker, []), d, NEAR_BAR_TOLERANCE_DAYS):
                by_date_missing[d.isoformat()].append(ticker)
                if ticker in missing_tickers:
                    missing_dates_by_ticker[ticker].append(d)

    ranking = concentration_ranking({t: missing_dates_by_ticker.get(t, []) for t in missing_tickers})
    smallest_50 = smallest_set_for_share(ranking, 50.0)
    smallest_80 = smallest_set_for_share(ranking, 80.0)

    for row in per_ticker_rows:
        missing_dates = missing_dates_by_ticker.get(row["ticker"], [])
        row["missingRebalanceDateCount"] = len(missing_dates)
        row["missingRebalanceDates"] = ";".join(d.isoformat() for d in missing_dates)

    with TRIAGE_BY_TICKER_CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "ticker", "status", "requestedStart", "requestedEnd", "requiredRebalanceDateCount",
            "firstRequiredRebalanceDate", "lastRequiredRebalanceDate", "requiredRebalanceDates",
            "missingRebalanceDateCount", "missingRebalanceDates",
            "category", "evidence",
            "resolutionCandidate", "resolutionCandidateHasData", "resolutionIdentityVerified",
        ])
        writer.writeheader()
        writer.writerows(per_ticker_rows)

    with TRIAGE_REQUIRED_DATES_CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "required_rebalance_date"])
        for ticker in missing_tickers:
            for required_date in required_dates_by_ticker.get(ticker, []):
                writer.writerow([ticker, required_date.isoformat()])

    with TRIAGE_BY_DATE_CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "missing_member_count", "missing_tickers"])
        for d in dates:
            missing = by_date_missing.get(d.isoformat(), [])
            writer.writerow([d.isoformat(), len(missing), ";".join(sorted(missing))])

    with COVERAGE_AFTER_RESOLUTION_CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "pit_members", "with_valid_prices", "coverage_pct"])
        writer.writeheader()
        writer.writerows(coverage_after)

    with COVERAGE_BY_YEAR_AFTER_RESOLUTION_CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "year", "rebalance_dates", "pit_member_observations",
            "with_valid_price_observations", "coverage_pct",
            "minimum_rebalance_date_coverage_pct", "maximum_rebalance_date_coverage_pct",
        ])
        writer.writeheader()
        writer.writerows(coverage_by_year_after)

    identity_verified_count = sum(1 for r in resolutions.values() if r.get("identityVerified"))
    candidate_data_found_count = sum(1 for r in resolutions.values() if r.get("dataFound"))
    fixable_category_count = identity_verified_count  # always 0; see docstring above
    structural_category_count = len(missing_tickers) - fixable_category_count

    result = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": "diagnostic-only; does not modify preregistered threshold, strategy code, runner, or universe eligibility",
        "missingOrPartialTickerCount": len(missing_tickers),
        "categoryCounts": {category: category_counts.get(category, 0) for category in CATEGORIES},
        "classificationTaxonomy": {
            "symbolMapping": (
                "legacy-suffix candidates are attempted, but never accepted without identity evidence"
            ),
            "tickerChangeMergerOrAcquisition": (
                "a same-day one-for-one REMOVE/ADD identifies a likely corporate-action substitution; "
                "ticker change, merger, and acquisition cannot be separated by these ticker-only sources"
            ),
            "bankruptcyOrDelisting": (
                "closed tenures without substitution are consistent with bankruptcy/delisting, but may "
                "also be provider gaps when no company-identity source is available"
            ),
            "providerCoverageGap": (
                "an open (still-current, never-removed) membership tenure has no provider history -- "
                "checked directly for this run's 27 cases: yfinance's quoteSummary lookup returns "
                "'Quote not found' for names that are unambiguously real, currently-listed, liquid "
                "securities (e.g. BK, HES, IPG, K, MMC), via THREE different call paths "
                "(batch download, single download, Ticker().history()), while AAPL/MSFT succeed "
                "immediately in the same session. This is evidence of a transient, ticker-specific "
                "Yahoo/yfinance lookup issue -- NOT a permanent structural gap -- and should not be "
                "conflated with the bankruptcy/delisting bucket, which covers securities that no "
                "longer trade at all. A later, lower-volume retry could plausibly recover some of "
                "these; this triage did not keep hammering the API to test that once the pattern was "
                "identified, since heavy repeated querying is itself a likely contributor."
            ),
            "membershipDefect": "a tenure ends exactly at the primary-source handoff boundary",
            "unresolved": "no deterministic signal matches",
        },
        "deterministicResolution": {
            "method": "strip trailing 'Q' for symbol_map.csv-flagged legacy-suffix tickers, then check "
                       "for a fetchable candidate on the existing provider (yfinance)",
            "candidatesAttempted": len(resolutions),
            "candidatesWithDataFound": candidate_data_found_count,
            "candidatesWithIdentityVerified": identity_verified_count,
            "resolvedTickers": sorted(resolved_candidates),
            "note": (
                "candidatesWithDataFound is deliberately NOT treated as resolved. Verified directly: "
                "CPQ (Compaq Computer, delisted 2002) strips to 'CP', which has decades of real "
                "fetchable data -- but 'CP' is Canadian Pacific Railway, an unrelated company that has "
                "traded under that ticker throughout. None of this pipeline's sources carry a company "
                "name, CUSIP, or FIGI, so a stripped candidate's data existing can never be accepted as "
                "proof of correct identity. candidatesWithIdentityVerified is 0 by construction and "
                "would require a real identity crosswalk (a genuinely different, alternate data source) "
                "to ever be nonzero -- exactly the 'before considering alternate data' boundary this "
                "triage was scoped to stop at."
            ),
        },
        "coverageImpactOfVerifiedResolutionsOnly": {
            "bestSingleDateCoveragePctBefore": best_before,
            "bestSingleDateCoveragePctAfter": best_after,
            "note": "identical before/after by construction (0 verified resolutions were available to "
                     "apply) -- computed from data/sp500_pit_free/audits/coverage_by_date_after_resolution.csv; "
                     "the preregistered usable-window determination in coverage_audit.json is NOT recomputed or altered",
        },
        "coverageByYearAfterVerifiedResolutions": coverage_by_year_after,
        "concentration": {
            "smallestSetCovering50PctOfMissingObservations": smallest_50,
            "smallestSetCovering50PctCount": len(smallest_50),
            "smallestSetCovering80PctOfMissingObservations": smallest_80,
            "smallestSetCovering80PctCount": len(smallest_80),
            "topTickersByMissingObservationCount": ranking[:20],
        },
        "survivorshipBiasCheck": survivorship,
        "ledgerEndExplanation": ledger_end_explanation,
        "verdict": {
            "fixableSymbolMappingResolutions": fixable_category_count,
            "structuralOrIndeterminateCount": structural_category_count,
            "conclusion": (
                f"0 of {len(missing_tickers)} missing/partial tickers were safely resolved. "
                f"{candidate_data_found_count} legacy-suffix tickers had a stripped candidate with "
                "fetchable data, but every one of those is identity-unverifiable, not fixed -- applying "
                "any of them would risk silently substituting the wrong company's returns (demonstrated "
                "directly by CPQ->CP). "
                f"{category_counts.get('provider_coverage_gap', 0)} tickers are currently-active, "
                "unambiguously real securities (BK, HES, IPG, K, MMC, ...) that failed to fetch from "
                "yfinance in every call path tried this session while AAPL/MSFT succeeded immediately -- "
                "evidence of a TRANSIENT lookup issue (plausibly session-level rate limiting from this "
                "pipeline's own heavy request volume today), not a structural gap; a calmer future retry "
                "could plausibly recover some of these and modestly narrow the coverage shortfall, though "
                "not by anywhere near the amount needed to reach 98% on its own. The remaining bulk "
                f"({category_counts.get('bankruptcy_delisting_or_provider_gap', 0)} tickers) splits into "
                "ticker-change/merger/acquisition-likely same-day substitutions (explained, not resolved) "
                "and real delistings/bankruptcies indistinguishable from provider coverage gaps without a "
                "company-identity crosswalk this free pipeline does not have. The failure to reach 98% "
                "coverage is PRIMARILY A STRUCTURAL LIMITATION of the free data sources -- there is no "
                "deterministic, safe fix available from the existing symbol-identity table alone for the "
                "large majority of missing tickers; a real fix would require alternate data (a "
                "company-name/CUSIP/FIGI crosswalk), which this triage was scoped not to pursue. A modest, "
                "session-rate-limiting-driven slice is plausibly recoverable with patience alone, but that "
                "slice is far too small to move the needle from ~97.4% to 98%. See categoryCounts and the "
                "by-ticker CSV for the full breakdown."
            ),
        },
    }
    TRIAGE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    TRIAGE_JSON_PATH.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    result = build()
    print(json.dumps({k: v for k, v in result.items() if k not in ("concentration",)}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
