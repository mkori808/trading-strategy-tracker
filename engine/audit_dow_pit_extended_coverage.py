"""Audit ``dow_pit_extended_v1`` without fetching data or running strategies."""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = ROOT / "data" / "dow_pit_extended"
INTERVALS_PATH = DATASET_DIR / "membership" / "intervals.csv"
AUDIT_PATH = DATASET_DIR / "audits" / "coverage_audit.json"
BY_TICKER_PATH = DATASET_DIR / "audits" / "coverage_by_ticker.csv"
BY_DATE_PATH = DATASET_DIR / "audits" / "coverage_by_rebalance_date.csv"
TARGET_START = date(2000, 1, 1)
TARGET_END = date(2026, 8, 31)
TOLERANCE_DAYS = 7
WARMUP_CALENDAR_DAYS = 400

# These are carried from the existing Dow ledger's explicit disclosures. A
# price file under a matching string does not clear identity by itself --
# only a passing, recorded independentPriceCrossCheck in
# data/dow_pit_extended/metadata/price_provenance/<ticker>.json does. WBA,
# UTX, KFT, and DWDP were removed from this dict after engine.recover_dow_pit_extended_prices
# produced exactly that for each: a fresh raw fetch (never sliced from this
# project's own auto_adjust=True cache, which was verified to silently bake
# in later out-of-window corporate actions), correctly reversed for any
# post-window split/merger-ratio and adjusted for in-window dividends only,
# checked against independently sourced reference closes. See each ticker's
# provenance sidecar for the checked dates and observed-vs-expected values.
IDENTITY_BLOCKERS = {
    "EK": "Eastman Kodak; historical/delisted identity and terminal economics unresolved",
    "GM": "pre-bankruptcy General Motors; current GM ticker is a different security",
    "SBC": "SBC Communications to AT&T rename/acquisition continuity unresolved",
}


def month_starts(start: date, end: date) -> list[date]:
    out = []
    cursor = start.replace(day=1)
    while cursor <= end:
        out.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), 1 if cursor.month == 12 else cursor.month + 1, 1)
    return out


def load_intervals(path: Path = INTERVALS_PATH) -> list[dict]:
    grouped: dict[tuple[str, str], dict] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            key = (row["effective_start"], row["effective_end"])
            grouped.setdefault(key, {
                "start": date.fromisoformat(row["effective_start"]),
                "end": date.fromisoformat(row["effective_end"]), "symbols": set(),
            })["symbols"].add(row["ticker"])
    return sorted(grouped.values(), key=lambda row: row["start"])


def membership_at(intervals: list[dict], as_of: date) -> set[str]:
    matches = [row["symbols"] for row in intervals if row["start"] <= as_of <= row["end"]]
    if len(matches) != 1:
        raise ValueError(f"expected one membership interval on {as_of}, found {len(matches)}")
    return set(matches[0])


def _read_dates(path: Path) -> set[date]:
    if not path.exists():
        return set()
    try:
        frame = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - corrupt/unreadable input is an audit miss
        return set()
    if frame.empty:
        return set()
    index = pd.DatetimeIndex(frame.index)
    return set(index.normalize().date)


def available_dates(ticker: str) -> tuple[set[date], list[str]]:
    candidates = [
        DATASET_DIR / "prices" / f"{ticker}.parquet",
        ROOT / "data" / "sp500_pit_free" / "prices" / f"{ticker}.parquet",
        ROOT / "data" / f"{ticker}_1d.parquet",
    ]
    dates: set[date] = set()
    used = []
    for path in candidates:
        found = _read_dates(path)
        if found:
            dates.update(found)
            used.append(str(path.relative_to(ROOT)).replace("\\", "/"))
    return dates, used


def has_near(dates: set[date], target: date, tolerance: int = TOLERANCE_DAYS) -> bool:
    return any(target - timedelta(days=tolerance) <= item <= target + timedelta(days=tolerance) for item in dates)


def build(
    intervals_path: Path = INTERVALS_PATH,
    audit_path: Path = AUDIT_PATH,
    by_ticker_path: Path = BY_TICKER_PATH,
    by_date_path: Path = BY_DATE_PATH,
) -> dict:
    intervals = load_intervals(intervals_path)
    rebalances = month_starts(TARGET_START, TARGET_END)
    rosters = {d: membership_at(intervals, d) for d in rebalances}
    required: dict[str, list[date]] = defaultdict(list)
    for d, roster in rosters.items():
        for ticker in roster:
            required[ticker].append(d)

    ticker_rows = []
    missing_by_date: dict[date, list[str]] = defaultdict(list)
    for ticker in sorted(required):
        bars, sources = available_dates(ticker)
        missing = [d for d in required[ticker] if not has_near(bars, d)]
        for d in missing:
            missing_by_date[d].append(ticker)
        warmup_target = required[ticker][0] - timedelta(days=WARMUP_CALENDAR_DAYS)
        warmup_ok = bool(bars) and min(bars) <= warmup_target
        identity_status = "unresolved" if ticker in IDENTITY_BLOCKERS else "no_known_blocker_in_existing_ledger"
        ticker_rows.append({
            "ticker": ticker,
            "required_rebalance_dates": len(required[ticker]),
            "missing_rebalance_dates": len(missing),
            "first_required": required[ticker][0].isoformat(),
            "last_required": required[ticker][-1].isoformat(),
            "warmup_required_by": warmup_target.isoformat(),
            "warmup_available": warmup_ok,
            "identity_status": identity_status,
            "identity_blocker": IDENTITY_BLOCKERS.get(ticker, ""),
            "artifact_sources": ";".join(sources),
            "first_available_bar": min(bars).isoformat() if bars else "",
            "last_available_bar": max(bars).isoformat() if bars else "",
            "missing_dates": ";".join(d.isoformat() for d in missing),
        })

    by_ticker_path.parent.mkdir(parents=True, exist_ok=True)
    with by_ticker_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(ticker_rows[0]))
        writer.writeheader()
        writer.writerows(ticker_rows)

    date_rows = []
    with by_date_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "pit_members", "with_valid_prices", "coverage_pct", "missing_tickers"])
        for d in rebalances:
            missing = sorted(missing_by_date[d])
            members = len(rosters[d])
            covered = members - len(missing)
            row = [d.isoformat(), members, covered, round(100.0 * covered / members, 2), ";".join(missing)]
            writer.writerow(row)
            date_rows.append(row)

    price_complete = all(not row["missing_rebalance_dates"] and row["warmup_available"] for row in ticker_rows)
    unresolved_identity = sorted(ticker for ticker in required if ticker in IDENTITY_BLOCKERS)
    audit = {
        "dataset": "dow_pit_extended_v1",
        "scope": "diagnostic-only; no network fetch and no strategy run",
        "targetWindow": {"start": TARGET_START.isoformat(), "end": TARGET_END.isoformat()},
        "rebalanceDateCount": len(rebalances),
        "distinctHistoricalTickerCount": len(required),
        "allRebalanceRostersExactly30": all(len(roster) == 30 for roster in rosters.values()),
        "priceCoverageComplete": price_complete,
        "identityCoverageComplete": not unresolved_identity,
        "unresolvedIdentityTickers": unresolved_identity,
        "tickersWithMissingRebalancePrices": [row["ticker"] for row in ticker_rows if row["missing_rebalance_dates"]],
        "tickersWithoutRequiredWarmup": [row["ticker"] for row in ticker_rows if not row["warmup_available"]],
        "minimumRebalanceDateCoveragePct": min(row[3] for row in date_rows),
        "activationPassed": price_complete and not unresolved_identity,
        "nextAcquisitionQueue": sorted(set(unresolved_identity) | {
            row["ticker"] for row in ticker_rows
            if row["missing_rebalance_dates"] or not row["warmup_available"]
        }),
        "note": "A matching ticker file never clears an explicit identity blocker. Terminal economics and successor continuity must be sourced before activation.",
    }
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return audit


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
