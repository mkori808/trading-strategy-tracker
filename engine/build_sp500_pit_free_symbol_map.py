"""Build the sp500_pit_free ticker-identity crosswalk (commit sp500_symbol_identity_v1).

Turns ``data/sp500_pit_free/membership/intervals.csv`` (one row per ticker
tenure, built by ``engine.build_sp500_pit_free_membership``) into
``data/sp500_pit_free/metadata/symbol_map.csv``: one row per tenure, with a
``vendor_symbol`` (the string that will actually be requested from the price
provider in the next build step) and a ``reason`` disclosing anything that
needs scrutiny before that fetch is trusted.

This module does NOT independently verify ticker identity against a
company-name or CUSIP/FIGI crosswalk -- neither source repository carries
company names, only ticker strings (see both READMEs), so there is nothing
to cross-reference against at this stage. ``vendor_symbol`` is the literal,
already-normalized (dot/dash-unified by
``engine.build_sp500_pit_ledger.normalize_ticker`` when the membership
ledger was built) historical ticker string; this module's job is to flag
which of those strings need scrutiny, not to guess corrections for them.
Two flags, both resolved empirically (fetch success + tenure-coverage
plausibility) by the next build step
(``engine.fetch_sp500_pit_free_prices``), never by pattern-matching alone:

- ``identity_ambiguous_multi_tenure``: this ticker has more than one
  disjoint membership tenure in the ledger. That can mean a real re-entry
  (Transocean/RIG left and later rejoined) or ticker reuse by a completely
  different, unrelated company (a known, disclosed risk class -- the
  existing ``engine/audit_sp500_price_coverage.py`` flags the same pattern
  as ``identityAmbiguous`` for the fja05680-sourced ledger). Neither source
  here carries a company name to settle which case applies.
- ``legacy_suffix_needs_verification``: the ticker ends in "Q", the old
  Nasdaq convention for a security in bankruptcy proceedings. Some of these
  are real: qzzcl labels AMR Corp's entire 1996-2003 tenure as ``AAMRQ``,
  the ticker it only actually carried during 2011-2013 Chapter 11
  proceedings -- a retroactive mislabeling artifact, not evidence the
  company traded under that symbol throughout. Others are NOT bankruptcy
  artifacts at all: ``CPQ`` (Compaq Computer) is a real, ordinary ticker
  that happens to end in Q. A blind "strip the Q" heuristic would corrupt
  CPQ while a blind "trust it literally" pass would silently fetch
  bankruptcy-era prices (if fetchable at all) for most of AAMRQ's claimed
  1996-2003 tenure. This is why the flag exists instead of a correction:
  the actual answer depends on what the price provider returns for the
  ticker's claimed date range, which only the next build step can check.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "sp500_pit_free"
INTERVALS_PATH = DATA_DIR / "membership" / "intervals.csv"
SYMBOL_MAP_PATH = DATA_DIR / "metadata" / "symbol_map.csv"
AUDIT_PATH = DATA_DIR / "audits" / "symbol_map_audit.json"

LEGACY_BANKRUPTCY_SUFFIX = "Q"


@dataclass(frozen=True)
class IntervalRow:
    ticker: str
    start: date
    end: date | None
    source: str


def load_intervals(path: Path = INTERVALS_PATH) -> list[IntervalRow]:
    rows: list[IntervalRow] = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(IntervalRow(
                ticker=row["ticker"],
                start=date.fromisoformat(row["effective_start"]),
                end=date.fromisoformat(row["effective_end"]) if row["effective_end"] else None,
                source=row["source"],
            ))
    return rows


def build_symbol_map(intervals: list[IntervalRow]) -> list[dict]:
    by_ticker: dict[str, list[IntervalRow]] = defaultdict(list)
    for row in intervals:
        by_ticker[row.ticker].append(row)

    rows: list[dict] = []
    for ticker in sorted(by_ticker):
        tenures = sorted(by_ticker[ticker], key=lambda item: item.start)
        multi_tenure = len(tenures) > 1
        legacy_suffix = ticker.endswith(LEGACY_BANKRUPTCY_SUFFIX) and len(ticker) >= 3
        reasons = []
        if multi_tenure:
            reasons.append("identity_ambiguous_multi_tenure")
        if legacy_suffix:
            reasons.append("legacy_suffix_needs_verification")
        reason = ";".join(reasons) if reasons else "standard"
        for tenure in tenures:
            rows.append({
                "historical_symbol": ticker,
                "vendor_symbol": ticker,
                "start_date": tenure.start.isoformat(),
                "end_date": tenure.end.isoformat() if tenure.end else "",
                "reason": reason,
            })
    return rows


def build(
    *,
    intervals_path: Path = INTERVALS_PATH,
    symbol_map_path: Path = SYMBOL_MAP_PATH,
    audit_path: Path = AUDIT_PATH,
) -> dict:
    intervals = load_intervals(intervals_path)
    rows = build_symbol_map(intervals)

    symbol_map_path.parent.mkdir(parents=True, exist_ok=True)
    with symbol_map_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["historical_symbol", "vendor_symbol", "start_date", "end_date", "reason"],
        )
        writer.writeheader()
        writer.writerows(rows)

    distinct_tickers = sorted({row["historical_symbol"] for row in rows})
    ambiguous = sorted({
        row["historical_symbol"] for row in rows
        if "identity_ambiguous_multi_tenure" in row["reason"]
    })
    legacy_suffix = sorted({
        row["historical_symbol"] for row in rows
        if "legacy_suffix_needs_verification" in row["reason"]
    })
    audit = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "buildStep": "sp500_symbol_identity_v1",
        "distinctTickers": len(distinct_tickers),
        "tenureRows": len(rows),
        "identityAmbiguousMultiTenureTickers": ambiguous,
        "identityAmbiguousMultiTenureCount": len(ambiguous),
        "legacySuffixNeedsVerificationTickers": legacy_suffix,
        "legacySuffixNeedsVerificationCount": len(legacy_suffix),
        "note": (
            "vendor_symbol is the literal normalized historical ticker string, "
            "not an independently verified identity -- neither source repository "
            "carries a company name to crosswalk against. Flags here are resolved "
            "empirically by engine.fetch_sp500_pit_free_prices (fetch success and "
            "tenure-coverage plausibility), never by pattern-matching alone."
        ),
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intervals", type=Path, default=INTERVALS_PATH)
    parser.add_argument("--symbol-map", type=Path, default=SYMBOL_MAP_PATH)
    parser.add_argument("--audit", type=Path, default=AUDIT_PATH)
    args = parser.parse_args()
    result = build(
        intervals_path=args.intervals, symbol_map_path=args.symbol_map, audit_path=args.audit,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
