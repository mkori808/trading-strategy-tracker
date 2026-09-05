"""Build the sp500_pit_free membership ledger from two independent open trackers.

This is a SEPARATE dataset from the existing ``sp500_pit`` universe
(``engine/build_sp500_pit_ledger.py``, source ``fja05680/sp500``). It exists
to test whether a differently-sourced free reconstruction, cross-checked
against a second independent tracker, can clear a pre-registered coverage
bar where the existing single-sourced ledger could not. It does not modify
``data/universe_membership.json`` or the ``dow_pit``/``sp500_pit`` universes.

Two sources, by design (see the project's cross-check philosophy already
applied to filters/timing gates elsewhere in this codebase):

- primary:   qzzcl/spx_history_constituents
- secondary: leandroloi/historical_sp500_constituents

Both publish the same ``date,tickers`` snapshot format ``engine.build_sp500_pit_ledger``
already parses, so this module reuses that parser rather than re-implementing
CSV handling -- but it is NOT the same lineage independence the name suggests.
Both repositories' own READMEs trace their base file to the same origin (a
CSV distributed with Andreas Clenow's "Trading Evolved", subsequently
hand-maintained against Wikipedia's S&P 500 change table by each maintainer
independently since ~2019-2021). Agreement between them therefore mostly
catches *maintenance-divergence* errors (a maintainer's transcription
mistake, a missed Wikipedia update), not errors already baked into the
shared ancestor file. This is disclosed in every provenance record this
module writes; it must not be silently treated as two-independent-source
triangulation of the kind CRSP-vs-official-index data would provide.

Coverage is NOT continuous through today. As of this build:
  - qzzcl's processed snapshot file ends 2023-03-20 (repository unmaintained
    since; confirmed via `git log`, not assumed).
  - leandroloi's snapshot file ends 2025-06-18.
  - Nothing in this pipeline covers 2025-06-19 through today.
The membership ledger and every audit below report this as a real gap
rather than silently truncating the dataset or extrapolating a roster.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from engine.build_sp500_pit_ledger import (
    MembershipEvent,
    _symbols,
    changed_snapshots,
    derive_events,
    normalize_ticker,
    source_digest,
    validate_forward_replay,
    validate_reverse_replay,
)
from engine.sanity import check_window


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "sp500_pit_free"
SOURCES_DIR = DATA_DIR / "metadata" / "sources"
MEMBERSHIP_DIR = DATA_DIR / "membership"
AUDITS_DIR = DATA_DIR / "audits"

PRIMARY_PATH = SOURCES_DIR / "qzzcl_historical_components.csv"
SECONDARY_PATH = SOURCES_DIR / "leandroloi_historical_components.csv"
PRIMARY_REPOSITORY = "https://github.com/qzzcl/spx_history_constituents"
SECONDARY_REPOSITORY = "https://github.com/leandroloi/historical_sp500_constituents"
PRIMARY_FILE = "S&P 500 Historical Components & Changes(04-16-2023).csv"
SECONDARY_FILE = "sp_500_historical_components.csv"

# Same shared-ancestor caveat as above: this is a maintenance-divergence
# check, not full source independence.
RECONCILE_TOLERANCE_DAYS = 5

INTERVALS_PATH = MEMBERSHIP_DIR / "intervals.csv"
RECONCILIATION_PATH = MEMBERSHIP_DIR / "reconciliation.csv"
PROVENANCE_PATH = DATA_DIR / "metadata" / "provenance.json"
AUDIT_PATH = AUDITS_DIR / "membership_audit.json"


MIN_REASONABLE_ROSTER = 450
MAX_REASONABLE_ROSTER = 550


def load_source_snapshots(
    path: Path, *, strict_roster_size: bool = True,
) -> tuple[list[tuple[date, frozenset[str]]], int]:
    """Like ``engine.build_sp500_pit_ledger.load_snapshots``, but tolerant of
    same-date duplicate rows recording two sequential same-day roster changes
    (observed in both qzzcl and leandroloi's source files -- e.g. qzzcl's
    2022-06-21 has one row before and one row after a same-day KDP-for-UA/UAA
    swap). The strict loader used for the existing fja05680-sourced ledger
    treats any duplicate date as a source error; here it is a real, disclosed
    data-quality fact about these specific free trackers, so the LAST row per
    date is kept (final state as of that date) and the collapsed-row count is
    returned for the audit rather than silently dropped or raised on.
    """
    frame = pd.read_csv(path)
    if list(frame.columns) != ["date", "tickers"]:
        raise ValueError(f"{path}: expected exactly date,tickers columns")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    duplicate_rows = int(frame["date"].duplicated().sum())
    frame = frame.drop_duplicates(subset="date", keep="last")
    frame = frame.sort_values("date").reset_index(drop=True)
    check_window(
        pd.DataFrame(index=pd.DatetimeIndex(frame["date"])),
        frame["date"].iloc[0].date(),
        frame["date"].iloc[-1].date(),
        label=f"sp500_pit_free source {path.name}",
    )
    snapshots = [(stamp.date(), _symbols(raw)) for stamp, raw in zip(frame["date"], frame["tickers"])]
    for stamp, roster in snapshots:
        if strict_roster_size and not MIN_REASONABLE_ROSTER <= len(roster) <= MAX_REASONABLE_ROSTER:
            raise ValueError(
                f"{path.name} {stamp}: roster contains {len(roster)} securities; "
                f"expected {MIN_REASONABLE_ROSTER}-{MAX_REASONABLE_ROSTER}"
            )
        if not strict_roster_size and not CATASTROPHIC_MIN_ROSTER <= len(roster) <= CATASTROPHIC_MAX_ROSTER:
            # Not a data-quality flag (see implausible_snapshot_dates for that) --
            # this only catches a parse-level catastrophe (wrong column split,
            # empty rows) that would otherwise silently produce a near-empty or
            # near-infinite roster.
            raise ValueError(
                f"{path.name} {stamp}: roster contains {len(roster)} securities; "
                f"this looks like a parse failure, not a real S&P 500 snapshot"
            )
    return snapshots, duplicate_rows


# leandroloi's roster undercounts real S&P 500 membership from its very first
# snapshot (468 members on 1996-01-02, vs. qzzcl's documented 487 floor) and
# degrades further to as low as 442 through 2007-2010, only becoming
# consistently plausible again around 2017-2018 (empirically confirmed by
# comparing yearly min/mean/max roster size against qzzcl's, which stays in
# its documented 487-507 band throughout). This is a real multi-decade defect
# in that source, not sampling noise -- most likely its diff-based update
# script permanently drops a ticker whenever a same-day rename or reuse
# confuses its added/removed-set diff (see leandroloi/sp500.py's `diff_tickers`,
# which has no special handling for a ticker changing format, e.g. BF.B<->BF-B,
# within the same update). Below this threshold a snapshot is excluded from
# contributing to cross-check agreement statistics -- reported as "not
# meaningfully cross-checked", never silently treated as a real disagreement
# or a real agreement.
SECONDARY_PLAUSIBLE_MIN_ROSTER = 485
CATASTROPHIC_MIN_ROSTER = 200
CATASTROPHIC_MAX_ROSTER = 700


def implausible_snapshot_dates(
    snapshots: list[tuple[date, frozenset[str]]], *, minimum: int = SECONDARY_PLAUSIBLE_MIN_ROSTER,
) -> list[tuple[date, date, int]]:
    """Contiguous [start, end] date ranges where a snapshot's roster size fell
    below `minimum`, with the worst (smallest) roster size seen in that range.
    """
    changed = changed_snapshots(snapshots)
    # A gap in calendar days between two flagged CHANGED-snapshot dates does not
    # mean the roster was plausible in between -- it only means nothing changed
    # in that stretch, and the same (small) roster persisted throughout. So a
    # range only ends when an actual intervening snapshot was plausible, never
    # merely because the next flagged snapshot is calendar-distant.
    ranges: list[list] = []
    open_range: list | None = None
    for stamp, roster in changed:
        size = len(roster)
        if size < minimum:
            if open_range is None:
                open_range = [stamp, stamp, size]
            else:
                open_range[1] = stamp
                open_range[2] = min(open_range[2], size)
        elif open_range is not None:
            ranges.append(open_range)
            open_range = None
    if open_range is not None:
        ranges.append(open_range)
    return [(start, end, worst) for start, end, worst in ranges]


@dataclass(frozen=True)
class MembershipInterval:
    ticker: str
    start_date: date
    end_date: date | None  # None means "still a member as of source coverage end"
    source: str


def intervals_from_snapshots(
    snapshots: list[tuple[date, frozenset[str]]],
    *,
    coverage_end: date,
    source: str,
    still_open_as_of_coverage_end: bool,
) -> list[MembershipInterval]:
    """Turn a snapshot roster history into per-ticker contiguous tenures.

    Mirrors ``engine.build_sp500_pit_ledger.derive_events`` (add/delete legs
    between consecutive changed snapshots) but accumulates per-ticker spans
    instead of per-date rosters, which is the shape the engine's backtest
    universes want (ticker | effective_start | effective_end).
    """
    changed = changed_snapshots(snapshots)
    if not changed:
        return []
    open_since: dict[str, date] = {ticker: changed[0][0] for ticker in changed[0][1]}
    intervals: list[MembershipInterval] = []
    for (_prev_date, prev_roster), (stamp, roster) in zip(changed, changed[1:]):
        for ticker in roster - prev_roster:
            open_since[ticker] = stamp
        for ticker in prev_roster - roster:
            start = open_since.pop(ticker)
            intervals.append(
                MembershipInterval(ticker, start, stamp - timedelta(days=1), source)
            )
    for ticker, start in open_since.items():
        intervals.append(
            MembershipInterval(
                ticker,
                start,
                coverage_end if still_open_as_of_coverage_end else None,
                source,
            )
        )
    return sorted(intervals, key=lambda item: (item.ticker, item.start_date))


def _legs(events: Iterable[MembershipEvent]) -> list[tuple[date, str, str]]:
    """Flatten (date, additions, deletions) events into (date, ticker, action) legs."""
    legs: list[tuple[date, str, str]] = []
    for event in events:
        for ticker in event.additions:
            legs.append((event.effective_date, ticker, "ADD"))
        for ticker in event.deletions:
            legs.append((event.effective_date, ticker, "REMOVE"))
    return legs


def _in_implausible_window(stamp: date, implausible_ranges: list[tuple[date, date, int]]) -> bool:
    return any(start <= stamp <= end for start, end, _worst in implausible_ranges)


def reconcile(
    primary_events: list[MembershipEvent],
    secondary_events: list[MembershipEvent],
    *,
    overlap_start: date,
    overlap_end: date,
    tolerance_days: int = RECONCILE_TOLERANCE_DAYS,
    secondary_implausible_ranges: list[tuple[date, date, int]] = (),
) -> list[dict]:
    """Compare every primary-source add/delete leg against the secondary source.

    Only legs inside the window BOTH sources actually cover are compared --
    a leg outside that overlap cannot be cross-checked and must not be
    reported as agreement or disagreement, per the "don't guess" rule.
    Matching allows a small date tolerance because two independently
    maintained trackers commonly record the same real-world change a few
    days apart (effective date vs. announcement date vs. when the
    maintainer next ran their update script) -- an exact-date-only match
    would misreport genuine agreement as UNRESOLVED.

    A leg dated inside `secondary_implausible_ranges` (see
    `implausible_snapshot_dates`) is reported as NOT_CROSS_CHECKED rather
    than agreement or UNRESOLVED: a "disagreement" there is overwhelmingly
    the secondary source's own known roster defect, not a real membership
    dispute, and counting it as UNRESOLVED would drown out the genuine
    disagreements this table exists to surface.
    """
    primary_legs = [
        leg for leg in _legs(primary_events) if overlap_start <= leg[0] <= overlap_end
    ]
    secondary_legs = [
        leg for leg in _legs(secondary_events) if overlap_start <= leg[0] <= overlap_end
    ]
    secondary_by_ticker_action: dict[tuple[str, str], list[date]] = {}
    for leg_date, ticker, action in secondary_legs:
        secondary_by_ticker_action.setdefault((ticker, action), []).append(leg_date)
    secondary_matched: set[tuple[date, str, str]] = set()

    rows: list[dict] = []
    for leg_date, ticker, action in sorted(primary_legs):
        candidates = secondary_by_ticker_action.get((ticker, action), [])
        best = min(candidates, key=lambda d: abs((d - leg_date).days), default=None)
        if best is not None and abs((best - leg_date).days) <= tolerance_days:
            secondary_matched.add((best, ticker, action))
            agreement = True
            confidence = "CONFIRMED" if best == leg_date else "CONFIRMED_WITH_DATE_OFFSET"
            note = "" if best == leg_date else f"secondary recorded {best.isoformat()}"
        elif _in_implausible_window(leg_date, secondary_implausible_ranges):
            agreement = False
            confidence = "NOT_CROSS_CHECKED"
            note = "secondary source roster was implausibly small in this window (see membership_audit.json)"
        else:
            agreement = False
            confidence = "UNRESOLVED"
            note = "no matching secondary event within tolerance window"
        rows.append({
            "effective_date": leg_date.isoformat(),
            "ticker": ticker,
            "action": action,
            "primary_source": "qzzcl",
            "secondary_source": "leandroloi",
            "agreement": agreement,
            "confidence": confidence,
            "note": note,
        })

    # Legs the secondary source has that the primary source never recorded,
    # inside the overlap window, are just as much a disagreement as the
    # reverse -- omitting them would make the primary source look more
    # complete than the comparison actually established.
    for leg_date, ticker, action in sorted(secondary_legs):
        if (leg_date, ticker, action) in secondary_matched:
            continue
        if _in_implausible_window(leg_date, secondary_implausible_ranges):
            confidence, note = (
                "NOT_CROSS_CHECKED",
                "event present only in secondary source, inside its implausible-roster window "
                "(see membership_audit.json) -- likely a secondary-source artifact, not a real event",
            )
        else:
            confidence, note = "UNRESOLVED", "event present only in secondary source"
        rows.append({
            "effective_date": leg_date.isoformat(),
            "ticker": ticker,
            "action": action,
            "primary_source": "qzzcl",
            "secondary_source": "leandroloi",
            "agreement": False,
            "confidence": confidence,
            "note": note,
        })
    return sorted(rows, key=lambda row: (row["effective_date"], row["ticker"]))


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [",".join(columns)]
    for row in rows:
        lines.append(",".join(str(row[column]) for column in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build(
    *,
    primary_path: Path = PRIMARY_PATH,
    secondary_path: Path = SECONDARY_PATH,
    intervals_path: Path = INTERVALS_PATH,
    reconciliation_path: Path = RECONCILIATION_PATH,
    provenance_path: Path = PROVENANCE_PATH,
    audit_path: Path = AUDIT_PATH,
) -> dict:
    primary_snapshots, primary_duplicate_rows = load_source_snapshots(primary_path)
    secondary_snapshots, secondary_duplicate_rows = load_source_snapshots(
        secondary_path, strict_roster_size=False,
    )
    secondary_implausible_ranges = implausible_snapshot_dates(secondary_snapshots)

    primary_changed = changed_snapshots(primary_snapshots)
    secondary_changed = changed_snapshots(secondary_snapshots)
    primary_start, primary_end = primary_changed[0][0], primary_changed[-1][0]
    secondary_start, secondary_end = secondary_changed[0][0], secondary_changed[-1][0]

    primary_audit = validate_forward_replay(primary_snapshots)
    primary_audit["reverseReplay"] = validate_reverse_replay(primary_snapshots)
    secondary_audit = validate_forward_replay(secondary_snapshots)
    secondary_audit["reverseReplay"] = validate_reverse_replay(secondary_snapshots)

    # Canonical intervals: primary source through its own coverage, extended
    # by the secondary source alone past that point (flagged single-source,
    # since only one tracker exists there) up to the secondary's own end.
    # Nothing extends past secondary_end -- that gap is reported, not filled.
    primary_intervals = intervals_from_snapshots(
        primary_snapshots,
        coverage_end=primary_end,
        source="qzzcl",
        still_open_as_of_coverage_end=False,
    )
    extension_snapshots = [
        (stamp, roster) for stamp, roster in secondary_changed if stamp > primary_end
    ]
    single_source_intervals: list[MembershipInterval] = []
    if extension_snapshots:
        # The anchor must be the PRIMARY source's own final roster, not a
        # secondary snapshot "at or before primary_end" -- picking the first
        # such secondary snapshot (a bug caught by inspection: it silently
        # matched secondary_changed's very first, 1996, entry, since the
        # list is ascending and `next()` returns the first match) produced
        # spurious "added" events for every name that was simply a
        # continuing member across the source handoff, e.g. META showing a
        # fabricated 2023-05-07 addition when it had been continuously held
        # since 2022-06-09.
        anchored = [(primary_end, primary_changed[-1][1])] + extension_snapshots
        single_source_intervals = intervals_from_snapshots(
            anchored,
            coverage_end=secondary_end,
            source="leandroloi_single_source",
            still_open_as_of_coverage_end=True,
        )
        single_source_intervals = [
            item for item in single_source_intervals if item.start_date > primary_end
            or item.end_date is None or item.end_date > primary_end
        ]

    # Stitch: a ticker still open at primary_end (qzzcl) that continues
    # unbroken into the extension (its single-source interval starts exactly
    # at primary_end) is ONE real tenure spanning the source handoff, not two
    # -- report it as one row with a combined source label rather than an
    # artificial split at the point where data provenance happens to change.
    open_primary = {item.ticker: item for item in primary_intervals if item.end_date is None}
    stitched: list[MembershipInterval] = []
    stitched_tickers: set[str] = set()
    remaining_single: list[MembershipInterval] = []
    for item in single_source_intervals:
        match = open_primary.get(item.ticker)
        if match is not None and item.start_date == primary_end:
            stitched.append(MembershipInterval(
                item.ticker, match.start_date, item.end_date,
                "qzzcl+leandroloi_single_source",
            ))
            stitched_tickers.add(item.ticker)
        else:
            remaining_single.append(item)
    unstitched_open_primary = [
        item for ticker, item in open_primary.items() if ticker not in stitched_tickers
    ]
    closed_primary = [item for item in primary_intervals if item.end_date is not None]

    all_intervals = sorted(
        closed_primary + unstitched_open_primary + stitched + remaining_single,
        key=lambda item: (item.ticker, item.start_date),
    )
    interval_rows = [
        {
            "ticker": item.ticker,
            "effective_start": item.start_date.isoformat(),
            "effective_end": item.end_date.isoformat() if item.end_date else "",
            "source": item.source,
        }
        for item in all_intervals
    ]
    _write_csv(
        intervals_path, interval_rows,
        ["ticker", "effective_start", "effective_end", "source"],
    )

    overlap_start = max(primary_start, secondary_start)
    overlap_end = min(primary_end, secondary_end)
    primary_events = derive_events(primary_changed)
    secondary_events = derive_events(secondary_changed)
    reconciliation_rows = reconcile(
        primary_events, secondary_events,
        overlap_start=overlap_start, overlap_end=overlap_end,
        secondary_implausible_ranges=secondary_implausible_ranges,
    )
    _write_csv(
        reconciliation_path, reconciliation_rows,
        ["effective_date", "ticker", "action", "primary_source", "secondary_source",
         "agreement", "confidence", "note"],
    )
    agreement_count = sum(1 for row in reconciliation_rows if row["agreement"])
    not_cross_checked_count = sum(
        1 for row in reconciliation_rows if row["confidence"] == "NOT_CROSS_CHECKED"
    )
    unresolved_count = len(reconciliation_rows) - agreement_count - not_cross_checked_count
    cross_checkable_count = len(reconciliation_rows) - not_cross_checked_count

    coverage_gap_start = secondary_end + timedelta(days=1)
    coverage_gap_end = date.today()
    membership_audit = {
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "primarySource": {
            "repository": PRIMARY_REPOSITORY,
            "file": PRIMARY_FILE,
            "sha256": source_digest(primary_path),
            "coverageStart": primary_start.isoformat(),
            "coverageEnd": primary_end.isoformat(),
            "duplicateDateRowsCollapsed": primary_duplicate_rows,
            "replayAudit": primary_audit,
        },
        "secondarySource": {
            "repository": SECONDARY_REPOSITORY,
            "file": SECONDARY_FILE,
            "sha256": source_digest(secondary_path),
            "coverageStart": secondary_start.isoformat(),
            "coverageEnd": secondary_end.isoformat(),
            "duplicateDateRowsCollapsed": secondary_duplicate_rows,
            "replayAudit": secondary_audit,
            "implausibleRosterWindows": [
                {
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "worstRosterSize": worst,
                    "note": f"roster size fell below the {SECONDARY_PLAUSIBLE_MIN_ROSTER}-name "
                             "plausibility floor established by comparing against qzzcl's stable "
                             "487-507 range over the same calendar years",
                }
                for start, end, worst in secondary_implausible_ranges
            ],
        },
        "sharedAncestryCaveat": (
            "Both sources' READMEs trace their base file to the same origin "
            "(Andreas Clenow's 'Trading Evolved' companion CSV, independently "
            "hand-maintained against Wikipedia since ~2019-2021). Agreement "
            "is evidence against maintenance-divergence errors, not proof of "
            "two statistically independent primary sources."
        ),
        "crossCheckWindow": {
            "overlapStart": overlap_start.isoformat(),
            "overlapEnd": overlap_end.isoformat(),
            "legsTotal": len(reconciliation_rows),
            "legsNotCrossCheckedSecondaryImplausible": not_cross_checked_count,
            "legsCrossCheckable": cross_checkable_count,
            "agreementCount": agreement_count,
            "unresolvedCount": unresolved_count,
            "agreementRate": round(agreement_count / cross_checkable_count, 4)
            if cross_checkable_count else None,
            "note": "agreementRate is computed only over legs where the secondary "
                     "source's roster was plausible at that date; see "
                     "secondarySource.implausibleRosterWindows for the excluded span.",
        },
        "singleSourceExtension": {
            "start": (primary_end + timedelta(days=1)).isoformat(),
            "end": secondary_end.isoformat(),
            "source": "leandroloi",
            "note": "qzzcl is unmaintained past its coverage end; this window "
                     "has no cross-check and carries lower confidence.",
        } if extension_snapshots else None,
        "uncoveredGap": {
            "start": coverage_gap_start.isoformat(),
            "end": coverage_gap_end.isoformat(),
            "note": "Neither source has any membership record in this window. "
                     "Not extrapolated, not filled -- reported as a hard gap.",
        } if coverage_gap_start <= coverage_gap_end else None,
        "distinctTickers": len({item.ticker for item in all_intervals}),
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(membership_audit, indent=2) + "\n", encoding="utf-8")

    def _display_path(path: Path) -> str:
        try:
            return str(path.relative_to(ROOT))
        except ValueError:
            return str(path)

    provenance = {
        "datasetName": "sp500_pit_free_v1",
        "buildStep": "sp500_pit_membership_v1",
        "primarySource": membership_audit["primarySource"],
        "secondarySource": membership_audit["secondarySource"],
        "sharedAncestryCaveat": membership_audit["sharedAncestryCaveat"],
        "intervalsPath": _display_path(intervals_path),
        "reconciliationPath": _display_path(reconciliation_path),
        "auditPath": _display_path(audit_path),
    }
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

    return membership_audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, default=PRIMARY_PATH)
    parser.add_argument("--secondary", type=Path, default=SECONDARY_PATH)
    args = parser.parse_args()
    result = build(primary_path=args.primary, secondary_path=args.secondary)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
