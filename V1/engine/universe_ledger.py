"""Point-in-time universe-membership ledger and integrity audit.

The app never treats today's index members as historical members. A licensed
constituent/delisted-price feed can populate ``data/universe_membership.json``
using the documented schema; until coverage is complete the audit returns an
honest unresolved result and blocks an identified-edge verdict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


LEDGER_PATH = Path(__file__).resolve().parent.parent / "data" / "universe_membership.json"
MANUAL_EVIDENCE_PATH = Path(__file__).resolve().parent.parent / "data" / "manual_validation_evidence.json"


@dataclass(frozen=True)
class MembershipAudit:
    passed: bool | None
    summary: str
    details: dict[str, Any]


@dataclass(frozen=True)
class PointInTimeSchedule:
    universe_key: str
    records: list[dict[str, Any]]

    @property
    def symbols(self) -> list[str]:
        return sorted({str(symbol) for row in self.records for symbol in row["symbols"]})

    def membership_at(self, as_of: date) -> set[str]:
        matches = {
            str(symbol)
            for row in self.records
            if date.fromisoformat(row["effectiveStart"]) <= as_of <= date.fromisoformat(row["effectiveEnd"])
            for symbol in row["symbols"]
        }
        return matches


def load_ledger(path: Path = LEDGER_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "universes": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("universes", {}), dict):
        raise ValueError("universe membership ledger must contain an object-valued 'universes' field")
    return payload


def load_manual_membership_evidence(
    strategy_name: str,
    path: Path = MANUAL_EVIDENCE_PATH,
) -> dict[str, Any] | None:
    """Load a structured prior PIT test without pretending it is a ledger.

    The earlier Dow reconstruction produced a real result but did not persist
    the date-effective roster in the schedule format used by this module.
    Keeping that result visible is provenance; it still cannot satisfy the
    gate while favorable delisted-price exclusions remain unresolved.
    """
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    strategies = payload.get("strategies", {}) if isinstance(payload, dict) else {}
    evidence = strategies.get(strategy_name) if isinstance(strategies, dict) else None
    return evidence if isinstance(evidence, dict) else None


def audit_membership(
    *,
    universe_key: str | None,
    symbols: list[str],
    start: date,
    end: date,
    membership_required: bool,
    point_in_time_applied: bool = False,
    path: Path = LEDGER_PATH,
    require_complete: bool = True,
) -> MembershipAudit:
    """`require_complete=False` is a narrow, explicit, opt-in relaxation: it
    stops UNRESOLVED PRICES (documented-but-unfetchable/delisted names) from
    blocking `ledger_complete`, while every OTHER completeness requirement
    (full window coverage, no gaps, every record sourced, every record's
    price coverage flagged complete, no malformed records) still applies
    exactly as before.

    This exists because those two failure classes are not the same kind of
    problem. A gap, a missing source, or a malformed record is a LEDGER
    QUALITY defect -- fixable by better data entry, and correctly treated as
    disqualifying. A name that genuinely has no fetchable price history
    anywhere (e.g. an entity absorbed into a successor whose ticker was
    reused, or a pre-merger identity a free data provider never carried) is a
    DATA AVAILABILITY fact -- irreducible by better record-keeping, and
    conflating it with a quality defect means a ledger honest enough to
    document real gaps can never activate point-in-time resolution at all,
    permanently falling back to the exact static-roster application PIT
    membership exists to replace. Default is unchanged (True, strict) so
    every existing caller's behavior is untouched; only a caller that opts in
    explicitly -- see engine/dow_pit_power_test.py -- gets the relaxed rule,
    and `unresolvedPrices` is still reported in full either way.
    """
    if not membership_required:
        return MembershipAudit(
            True,
            "Explicit fixed symbols were registered before execution; no historical index-membership claim is made",
            {"policy": "fixed_list", "symbols": symbols},
        )
    ledger = load_ledger(path)
    records = (ledger.get("universes") or {}).get(universe_key or "", [])
    if not records:
        return MembershipAudit(
            None,
            "No point-in-time membership ledger covers this index universe",
            {
                "universeKey": universe_key,
                "requestedStart": start.isoformat(),
                "requestedEnd": end.isoformat(),
                "ledgerPath": str(path),
                "requiredSchema": {
                    "effectiveStart": "YYYY-MM-DD",
                    "effectiveEnd": "YYYY-MM-DD",
                    "symbols": ["..."],
                    "unfetchableOrDelisted": ["..."],
                    "source": "licensed source and snapshot identifier",
                },
                "survivorshipRisk": True,
            },
        )
    malformed = []
    parsed = []
    for index, row in enumerate(records):
        try:
            row_start = date.fromisoformat(row["effectiveStart"])
            row_end = date.fromisoformat(row["effectiveEnd"])
            row_symbols = row["symbols"]
            if row_end < row_start or not isinstance(row_symbols, list) or not row_symbols:
                raise ValueError("invalid date range or empty symbols")
            parsed.append((row_start, row_end, row))
        except (KeyError, TypeError, ValueError) as exc:
            malformed.append({"record": index, "error": str(exc)})
    relevant = [item for item in parsed if item[1] >= start and item[0] <= end]
    if not relevant:
        return MembershipAudit(
            False,
            "PIT ledger has no records overlapping the requested window",
            {"universeKey": universe_key, "survivorshipRisk": True, "malformedRecords": malformed},
        )
    covered_start = min(item[0] for item in relevant)
    covered_end = max(item[1] for item in relevant)
    gaps = []
    cursor = start
    for row_start, row_end, _ in sorted(relevant, key=lambda item: item[0]):
        effective_start = max(start, row_start)
        effective_end = min(end, row_end)
        if effective_start > cursor:
            gaps.append({"after": cursor.isoformat(), "before": effective_start.isoformat()})
        if effective_end >= cursor:
            cursor = date.fromordinal(effective_end.toordinal() + 1)
    if cursor <= end:
        gaps.append({"after": cursor.isoformat(), "before": end.isoformat()})
    unresolved_prices = sorted({
        symbol
        for _, _, row in relevant
        for symbol in row.get("unfetchableOrDelisted", [])
    })
    sources_missing = [row.get("effectiveStart") for _, _, row in relevant if not row.get("source")]
    price_coverage_missing = [
        row.get("effectiveStart") for _, _, row in relevant
        if row.get("priceCoverageComplete") is not True
    ]
    ledger_symbols = {
        str(symbol)
        for _, _, row in relevant
        for symbol in row.get("symbols", [])
    }
    ledger_complete = (
        covered_start <= start and covered_end >= end
        and not gaps and (require_complete is False or not unresolved_prices)
        and not sources_missing and not price_coverage_missing and not malformed
    )
    passed = ledger_complete and point_in_time_applied
    if passed and unresolved_prices:
        summary = (
            "PIT membership was applied; date-effective roster is complete but "
            f"{len(unresolved_prices)} historical member(s) have no fetchable price "
            "history and were excluded from the tradeable universe -- see "
            "unresolvedPrices for names and survivorship-direction disclosure"
        )
    elif passed:
        summary = "Complete PIT membership was applied and delisted-price coverage is resolved"
    elif ledger_complete:
        summary = "PIT ledger is complete, but this run used a static symbol list instead of date-effective membership"
    else:
        summary = "PIT ledger exists but has coverage, source, or delisted-price gaps"
    return MembershipAudit(
        passed,
        summary,
        {
            "universeKey": universe_key,
            "coveredStart": covered_start.isoformat(),
            "coveredEnd": covered_end.isoformat(),
            "membershipGaps": gaps,
            "unresolvedPrices": unresolved_prices,
            "recordsWithoutSource": sources_missing,
            "recordsWithoutCompletePriceCoverage": price_coverage_missing,
            "malformedRecords": malformed,
            "pointInTimeMembershipApplied": point_in_time_applied,
            "inputSymbolsAbsentFromEverySnapshot": sorted(set(symbols) - ledger_symbols),
            "historicalMembersExcludedFromStaticInput": sorted(ledger_symbols - set(symbols)),
            "survivorshipRisk": not passed,
        },
    )


def resolve_schedule(
    universe_key: str,
    start: date,
    end: date,
    *,
    path: Path = LEDGER_PATH,
    require_complete: bool = True,
) -> PointInTimeSchedule | None:
    """`require_complete=False` -- see audit_membership's docstring. Every
    record's own `symbols` list must still only ever contain names that ARE
    fetchable; `unfetchableOrDelisted` is disclosure, not a filter applied
    here, so a genuinely unfetchable name must never appear in `symbols`."""
    ledger = load_ledger(path)
    records = (ledger.get("universes") or {}).get(universe_key, [])
    symbols = sorted({str(symbol) for row in records for symbol in row.get("symbols", [])})
    audit = audit_membership(
        universe_key=universe_key,
        symbols=symbols,
        start=start,
        end=end,
        membership_required=True,
        point_in_time_applied=True,
        path=path,
        require_complete=require_complete,
    )
    if audit.passed is not True:
        return None
    relevant = [
        row for row in records
        if date.fromisoformat(row["effectiveEnd"]) >= start
        and date.fromisoformat(row["effectiveStart"]) <= end
    ]
    return PointInTimeSchedule(universe_key, relevant)
