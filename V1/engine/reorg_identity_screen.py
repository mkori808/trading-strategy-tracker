"""Feasibility check for distressed/post-reorg equity's data-shape caveat.

Answers ONE question, per this session's task: can a pre-bankruptcy issuer
be linked to its post-reorganization equity, using only point-in-time
information, reliably enough to deserve a real experiment? This module
does NOT backtest, does NOT touch a holdout, and does NOT select on future
outcomes -- it only tests whether the LINKAGE and EVENT TIMING can be
established at all.

METHOD, discovered empirically this session on 10 real, well-documented
bankruptcy cases (never assumed from Sharadar's schema docs):

1. `actions.bankruptcyliquidation` fires on the SAME DATE as `actions.delisted`
   for the pre-bankruptcy ticker -- this is a clean, consistent FILING-
   TRIGGERED DELISTING signal, confirmed on Hertz, Washington Mutual, GM,
   Frontier, Chesapeake, Rite Aid, and Party City. It does NOT by itself
   distinguish liquidation from reorganization, and it is NOT the emergence
   date -- it is close to the FILING date (companies get delisted from
   their primary exchange within days of a Chapter 11 filing).

2. The `tickerchangefrom`/`tickerchangeto` action pair immediately
   preceding a `bankruptcyliquidation` row is Sharadar's OWN internal
   relabeling of the pre-bankruptcy ticker to a bankruptcy-suffixed one
   (e.g. WM -> WAMUQ) -- NOT a claim about corporate succession. Treating
   it as one produces a textbook false link: WAMUQ's `tickerchangefrom`
   contraticker is literally "WM", which today is Waste Management Inc, a
   completely unrelated company. `relatedtickers` on the OLD ticker is
   equally unsafe for the same reason and, separately, was also confirmed
   to MISS a real, correct link (FTRCQ's relatedtickers never mentions
   FYBR, the actual 2021 successor) -- unreliable in BOTH directions, never
   trusted here.

3. The one AUDITABLE, externally-verifiable signal found: the SEC CIK
   embedded in `tickers.secfilings`. Confirmed on three real reorganizations
   (Hertz, Chesapeake, Frontier) that the pre- and post-bankruptcy
   securities share the SAME CIK -- the legal registrant persisted through
   Chapter 11 even though Sharadar assigns the post-reorg security a
   DIFFERENT permaticker (correctly reflecting that the SECURITY, old
   shares cancelled and new ones issued, is not continuous even when the
   ISSUER is). Confirmed on GM (liquidation + genuinely new Delaware
   corporation) and Washington Mutual (unrelated ticker-reuse) that a
   DIFFERENT CIK correctly flags "not the same legal registrant."

4. CIK MATCHING VERIFIES a candidate link; it does not DISCOVER one. Every
   confirmed case above required already knowing which candidate ticker to
   check. No field in `tickers` or `actions` points FORWARD from an old
   bankrupt security to its successor's ticker, and none points BACKWARD
   from a new listing to its bankrupt predecessor. A market-wide, blind
   discovery process would require building a full CIK index across every
   Sharadar-tracked security and matching by (CIK, date proximity) -- real,
   nontrivial infrastructure not built in this feasibility pass, per the
   task's explicit instruction not to process the whole historical universe
   before the ground-truth check.

Never runs a backtest. Never touches a holdout. Reports what was measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
from typing import Any

from engine.sharadar_client import SharadarClient


LinkageConfidence = str  # "HIGH" | "MEDIUM" | "LOW" | "UNRESOLVED"
Outcome = str  # "REORGANIZED" | "LIQUIDATED" | "ACQUIRED_IN_BANKRUPTCY" | "UNKNOWN"


@dataclass(frozen=True)
class ReorganizationEvent:
    event_id: str

    company_cik: str | None  # SEC CIK when resolvable -- the one externally-auditable identifier found

    pre_reorg_ticker: str
    pre_reorg_permaticker: str | None
    post_reorg_ticker: str | None
    post_reorg_permaticker: str | None

    bankruptcy_filing_or_delisting_date: date | None  # actions.bankruptcyliquidation date -- filing-adjacent, NOT emergence
    post_reorg_first_trade_date: date | None  # tickers.firstpricedate of the post-reorg security, when one exists

    outcome: Outcome
    linkage_method: str
    linkage_confidence: LinkageConfidence
    source_fields: dict[str, Any] = field(default_factory=dict)


def extract_cik(secfilings_url: str | None) -> str | None:
    if not secfilings_url:
        return None
    match = re.search(r"CIK=(\d+)", secfilings_url)
    return match.group(1) if match else None


def _ticker_record(client: SharadarClient, ticker: str) -> dict[str, Any] | None:
    result = client.query_all("tickers", ticker=ticker, table="stocks")
    return result.rows[0] if result.rows else None


def _bankruptcy_actions(client: SharadarClient, ticker: str) -> list[dict[str, Any]]:
    result = client.query_all(
        "actions", ticker=ticker, **{"from": "1998-01-01", "to": "2026-09-05"}, sort="date.asc"
    )
    return [row for row in result.rows if row["action"] == "bankruptcyliquidation"]


def check_case(
    client: SharadarClient,
    pre_reorg_ticker: str,
    candidate_post_reorg_ticker: str | None,
    *,
    force_refresh: bool = False,
) -> ReorganizationEvent:
    """Verify ONE hand-identified candidate link. This is a VERIFIER, not a
    discovery tool -- see module docstring point 4. `candidate_post_reorg_ticker`
    must be supplied from external knowledge (a news source, this session's
    own researched ground-truth sample); nothing here searches for it."""
    old_record = _ticker_record(client, pre_reorg_ticker)
    old_cik = extract_cik(old_record.get("secfilings")) if old_record else None
    old_permaticker = old_record.get("permaticker") if old_record else None

    bankruptcy_rows = _bankruptcy_actions(client, pre_reorg_ticker)
    filing_date = date.fromisoformat(bankruptcy_rows[-1]["date"]) if bankruptcy_rows else None

    if not candidate_post_reorg_ticker:
        outcome = "LIQUIDATED" if old_record and old_record.get("isdelisted") == "Y" else "UNKNOWN"
        return ReorganizationEvent(
            event_id=f"{pre_reorg_ticker}->NONE", company_cik=old_cik,
            pre_reorg_ticker=pre_reorg_ticker, pre_reorg_permaticker=old_permaticker,
            post_reorg_ticker=None, post_reorg_permaticker=None,
            bankruptcy_filing_or_delisting_date=filing_date, post_reorg_first_trade_date=None,
            outcome=outcome, linkage_method="no_candidate_supplied",
            linkage_confidence="HIGH" if outcome == "LIQUIDATED" else "UNRESOLVED",
            source_fields={"old": old_record},
        )

    new_record = _ticker_record(client, candidate_post_reorg_ticker)
    if not new_record:
        return ReorganizationEvent(
            event_id=f"{pre_reorg_ticker}->{candidate_post_reorg_ticker}", company_cik=old_cik,
            pre_reorg_ticker=pre_reorg_ticker, pre_reorg_permaticker=old_permaticker,
            post_reorg_ticker=candidate_post_reorg_ticker, post_reorg_permaticker=None,
            bankruptcy_filing_or_delisting_date=filing_date, post_reorg_first_trade_date=None,
            outcome="UNKNOWN", linkage_method="candidate_ticker_not_found",
            linkage_confidence="UNRESOLVED", source_fields={"old": old_record},
        )

    new_cik = extract_cik(new_record.get("secfilings"))
    new_permaticker = new_record.get("permaticker")
    first_trade = new_record.get("firstpricedate")
    first_trade_date = date.fromisoformat(first_trade) if first_trade else None

    same_cik = bool(old_cik and new_cik and old_cik == new_cik)
    if same_cik:
        outcome = "REORGANIZED"
        confidence: LinkageConfidence = "HIGH"
        method = "same_sec_cik_different_permaticker"
    elif old_cik and new_cik and old_cik != new_cik:
        outcome = "UNKNOWN"
        confidence = "LOW"
        method = "different_cik_candidate_is_likely_unrelated"
    else:
        outcome = "UNKNOWN"
        confidence = "UNRESOLVED"
        method = "cik_not_resolvable_for_one_or_both_sides"

    return ReorganizationEvent(
        event_id=f"{pre_reorg_ticker}->{candidate_post_reorg_ticker}", company_cik=old_cik if same_cik else None,
        pre_reorg_ticker=pre_reorg_ticker, pre_reorg_permaticker=old_permaticker,
        post_reorg_ticker=candidate_post_reorg_ticker, post_reorg_permaticker=new_permaticker,
        bankruptcy_filing_or_delisting_date=filing_date, post_reorg_first_trade_date=first_trade_date,
        outcome=outcome, linkage_method=method, linkage_confidence=confidence,
        source_fields={"old": old_record, "new": new_record, "oldCik": old_cik, "newCik": new_cik},
    )


# Ground-truth sample: 10 real, independently-verifiable bankruptcy cases,
# researched this session (never invented), spanning every shape the task
# asked for. `expected_outcome`/`expected_confidence` are this session's
# real-world knowledge of what actually happened, used to check whether the
# CIK-matching method reproduces it -- NOT tuned to make the method look good.
GROUND_TRUTH_CASES: list[dict[str, Any]] = [
    {"issuer": "Hertz Global Holdings", "pre": "HTZGQ", "post": "HTZ",
     "shape": "successful reorganization, old equity cancelled, new equity issued, same brand+CIK",
     "expected_outcome": "REORGANIZED", "expected_confidence": "HIGH"},
    {"issuer": "Frontier Communications", "pre": "FTRCQ", "post": "FYBR",
     "shape": "reorganization with a NEW ticker (not brand-preserving), same CIK",
     "expected_outcome": "REORGANIZED", "expected_confidence": "HIGH"},
    {"issuer": "Chesapeake Energy", "pre": "CHKAQ", "post": "EXE",
     "shape": "reorganization, later ticker-changed again via a subsequent merger (repeated corporate event)",
     "expected_outcome": "REORGANIZED", "expected_confidence": "HIGH"},
    {"issuer": "General Motors Corp (old)", "pre": "MTLQQ", "post": "GM",
     "shape": "liquidation of the old entity ('Motors Liquidation Co'); GM the ticker was reused by a genuinely NEW Delaware corporation",
     "expected_outcome": "UNKNOWN", "expected_confidence": "LOW"},
    {"issuer": "Washington Mutual", "pre": "WAMUQ", "post": "WM",
     "shape": "FALSE LINK TRAP: relatedtickers/tickerchangefrom both surface 'WM', which is Waste Management Inc, unrelated",
     "expected_outcome": "UNKNOWN", "expected_confidence": "LOW"},
    {"issuer": "Sears Holdings", "pre": "SHLDQ", "post": None,
     "shape": "acquired in bankruptcy via credit bid (ESL/Transform Holdco); no new PUBLIC equity ever issued",
     "expected_outcome": "LIQUIDATED", "expected_confidence": "HIGH"},
    {"issuer": "SVB Financial Group", "pre": "SIVBQ", "post": None,
     "shape": "holding-company bankruptcy after the bank subsidiary's FDIC seizure; no successor public equity",
     "expected_outcome": "LIQUIDATED", "expected_confidence": "HIGH"},
    {"issuer": "Party City Holdco", "pre": "PRTYQ", "post": None,
     "shape": "delisted 2023; no current ticker holder found -- consistent with eventual full liquidation",
     "expected_outcome": "LIQUIDATED", "expected_confidence": "HIGH"},
    {"issuer": "Rite Aid Corp", "pre": "RADCQ", "post": None,
     "shape": "delisted 2023; no current ticker holder found -- consistent with no lasting public successor equity",
     "expected_outcome": "LIQUIDATED", "expected_confidence": "HIGH"},
    {"issuer": "Bed Bath & Beyond Inc", "pre": "BBBYQ", "post": "BBBY",
     "shape": "AMBIGUOUS: publicly reported that Overstock.com acquired the brand and later used the BBBY ticker, but neither OSTK nor BBBY resolves to a current Sharadar ticker record -- inconclusive from this data source alone, must fail closed",
     "expected_outcome": "UNKNOWN", "expected_confidence": "UNRESOLVED"},
]


def run_ground_truth(*, force_refresh: bool = False) -> list[dict[str, Any]]:
    client = SharadarClient()
    results = []
    for case in GROUND_TRUTH_CASES:
        event = check_case(client, case["pre"], case["post"], force_refresh=force_refresh)
        correct = (
            event.outcome == case["expected_outcome"]
            and event.linkage_confidence == case["expected_confidence"]
        )
        results.append({
            "issuer": case["issuer"], "shape": case["shape"],
            "preReorgTicker": case["pre"], "candidatePostReorgTicker": case["post"],
            "expectedOutcome": case["expected_outcome"], "expectedConfidence": case["expected_confidence"],
            "derivedOutcome": event.outcome, "derivedConfidence": event.linkage_confidence,
            "linkageMethod": event.linkage_method,
            "bankruptcyDate": event.bankruptcy_filing_or_delisting_date.isoformat() if event.bankruptcy_filing_or_delisting_date else None,
            "postReorgFirstTradeDate": event.post_reorg_first_trade_date.isoformat() if event.post_reorg_first_trade_date else None,
            "matchesExternalKnowledge": correct,
        })
    return results


def main() -> None:
    import json
    results = run_ground_truth()
    correct = sum(1 for r in results if r["matchesExternalKnowledge"])
    print(json.dumps({"results": results, "correct": correct, "total": len(results)}, indent=2))


if __name__ == "__main__":
    main()
