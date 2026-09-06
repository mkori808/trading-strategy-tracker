"""Coverage-by-date audit and usable-window determination (commit
sp500_coverage_audit_v1), executing the procedure pre-registered in
``research/sp500_pit_free_coverage_preregistration.json`` exactly -- read
that file first. This script must run, and its output must be committed,
BEFORE any canonical strategy is run against the sp500_pit_free universe;
the thresholds below are fixed by that preregistration and must never be
adjusted after looking at the table this script produces.

For every rebalance date (the first calendar day of each month, spanning the
membership ledger's own coverage window), computes:
  - pit_members: tickers with a membership interval covering that date
  - with_valid_prices: of those, how many have a price bar within 7 calendar
    days of the date (OK or PARTIAL_COVERAGE status; MISSING_DELISTED_PRICE_HISTORY
    or a bar too far away never counts)
  - coverage_pct

Then determines the usable window: the longest contiguous trailing run of
rebalance dates, ending at the ledger's coverage end, for which at least
95% of dates individually clear 98% coverage.
"""

from __future__ import annotations

import argparse
import bisect
import csv
from collections import defaultdict
from datetime import date, datetime, timezone
import json
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "sp500_pit_free"
INTERVALS_PATH = DATA_DIR / "membership" / "intervals.csv"
PRICE_COVERAGE_PATH = DATA_DIR / "audits" / "price_coverage.json"
PRICES_DIR = DATA_DIR / "prices"
COVERAGE_BY_DATE_PATH = DATA_DIR / "audits" / "coverage_by_date.csv"
AUDIT_PATH = DATA_DIR / "audits" / "coverage_audit.json"

NEAR_BAR_TOLERANCE_DAYS = 7
MIN_COVERAGE_PCT = 98.0
MIN_SHARE_OF_DATES_CLEARING_IT = 95.0


def load_intervals(path: Path = INTERVALS_PATH) -> list[tuple[str, date, date | None]]:
    rows = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            end = date.fromisoformat(row["effective_end"]) if row["effective_end"] else None
            rows.append((row["ticker"], date.fromisoformat(row["effective_start"]), end))
    return rows


def rebalance_dates(intervals: list[tuple[str, date, date | None]]) -> list[date]:
    starts = [start for _, start, _ in intervals]
    ends = [end for _, _, end in intervals if end is not None]
    coverage_start, coverage_end = min(starts), max(ends)
    dates = []
    cursor = date(coverage_start.year, coverage_start.month, 1)
    while cursor <= coverage_end:
        dates.append(cursor)
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    return dates


def roster_by_date(
    intervals: list[tuple[str, date, date | None]], dates: list[date],
) -> dict[date, set[str]]:
    rosters: dict[date, set[str]] = {d: set() for d in dates}
    for ticker, start, end in intervals:
        effective_end = end if end is not None else dates[-1]
        for d in dates:
            if start <= d <= effective_end:
                rosters[d].add(ticker)
    return rosters


def _bar_dates(ticker: str, prices_dir: Path = PRICES_DIR) -> list[date]:
    path = prices_dir / f"{ticker}.parquet"
    if not path.exists():
        return []
    try:
        frame = pd.read_parquet(path)
    except Exception:  # noqa: BLE001 - a corrupt cache has no usable bars
        return []
    if frame.empty:
        return []
    return sorted(frame.index.normalize().date.tolist())


def _has_bar_near(sorted_bar_dates: list[date], target: date, tolerance_days: int) -> bool:
    if not sorted_bar_dates:
        return False
    idx = bisect.bisect_left(sorted_bar_dates, target)
    candidates = [
        sorted_bar_dates[i] for i in (idx - 1, idx) if 0 <= i < len(sorted_bar_dates)
    ]
    return any(abs((c - target).days) <= tolerance_days for c in candidates)


def compute_coverage_by_date(
    intervals: list[tuple[str, date, date | None]],
    dates: list[date],
    *,
    prices_dir: Path = PRICES_DIR,
) -> list[dict]:
    tickers = sorted({ticker for ticker, _, _ in intervals})
    bar_index = {ticker: _bar_dates(ticker, prices_dir) for ticker in tickers}
    rosters = roster_by_date(intervals, dates)

    rows = []
    for d in dates:
        roster = rosters[d]
        covered = sum(
            1 for ticker in roster if _has_bar_near(bar_index[ticker], d, NEAR_BAR_TOLERANCE_DAYS)
        )
        pct = round(100.0 * covered / len(roster), 2) if roster else 0.0
        rows.append({
            "date": d.isoformat(),
            "pit_members": len(roster),
            "with_valid_prices": covered,
            "coverage_pct": pct,
        })
    return rows


def determine_usable_window(rows: list[dict]) -> dict:
    """Earliest start date whose trailing suffix (through the ledger's own
    coverage end) has at least MIN_SHARE_OF_DATES_CLEARING_IT percent of its
    dates individually at or above MIN_COVERAGE_PCT -- i.e. the longest
    qualifying contiguous trailing run, computed directly rather than by a
    stateful backward scan that could stop early on a non-monotonic table.
    """
    n = len(rows)
    passes = [row["coverage_pct"] >= MIN_COVERAGE_PCT for row in rows]
    usable_start_index = None
    for start_index in range(n):
        window = passes[start_index:]
        share_passing = 100.0 * sum(window) / len(window)
        if share_passing >= MIN_SHARE_OF_DATES_CLEARING_IT:
            usable_start_index = start_index
            break
    if usable_start_index is None:
        return {
            "usableStartDate": None,
            "usableEndDate": None,
            "verdict": "NO_WINDOW_CLEARS_THE_PREREGISTERED_THRESHOLD",
        }
    window = rows[usable_start_index:]
    return {
        "usableStartDate": window[0]["date"],
        "usableEndDate": window[-1]["date"],
        "rebalanceDatesInWindow": len(window),
        "shareOfDatesClearingThreshold": round(
            100.0 * sum(1 for row in window if row["coverage_pct"] >= MIN_COVERAGE_PCT) / len(window), 2,
        ),
        "verdict": "USABLE_WINDOW_DETERMINED",
    }


def build(
    *,
    intervals_path: Path = INTERVALS_PATH,
    prices_dir: Path = PRICES_DIR,
    coverage_by_date_path: Path = COVERAGE_BY_DATE_PATH,
    audit_path: Path = AUDIT_PATH,
) -> dict:
    intervals = load_intervals(intervals_path)
    dates = rebalance_dates(intervals)
    rows = compute_coverage_by_date(intervals, dates, prices_dir=prices_dir)
    window = determine_usable_window(rows)

    coverage_by_date_path.parent.mkdir(parents=True, exist_ok=True)
    with coverage_by_date_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "pit_members", "with_valid_prices", "coverage_pct"])
        writer.writeheader()
        writer.writerows(rows)

    audit = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "buildStep": "sp500_coverage_audit_v1",
        "preregistration": "research/sp500_pit_free_coverage_preregistration.json",
        "minCoveragePct": MIN_COVERAGE_PCT,
        "minShareOfDatesClearingIt": MIN_SHARE_OF_DATES_CLEARING_IT,
        "rebalanceDatesTotal": len(dates),
        "usableWindow": window,
        "worstDates": sorted(rows, key=lambda row: row["coverage_pct"])[:10],
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intervals", type=Path, default=INTERVALS_PATH)
    parser.add_argument("--prices-dir", type=Path, default=PRICES_DIR)
    args = parser.parse_args()
    result = build(intervals_path=args.intervals, prices_dir=args.prices_dir)
    print(json.dumps({k: v for k, v in result.items() if k != "worstDates"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
