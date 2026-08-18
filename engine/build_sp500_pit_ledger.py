"""Build and audit the S&P 500 point-in-time membership ledger.

The input is the dated-roster CSV maintained by ``fja05680/sp500``.  It is
an open, approximate research source, not an official S&P DJI constituent
feed.  The builder therefore records the exact source URL, git commit and
SHA-256 digest and keeps price coverage false until a separate all-ticker
fetchability audit has completed.

This script never runs a strategy and never inspects a strategy result.
It only turns dated rosters into date-effective intervals and verifies that
forward replay of every derived add/delete transition reproduces the source.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Iterable

import pandas as pd

from engine.sanity import check_window


ROOT = Path(__file__).resolve().parent.parent
LEDGER_PATH = ROOT / "data" / "universe_membership.json"
DEFAULT_SOURCE_PATH = (
    ROOT / "data" / "sources" / "sp500" / "historical_components.csv"
)
DEFAULT_PROVENANCE_PATH = (
    ROOT / "data" / "sources" / "sp500" / "provenance.json"
)
DEFAULT_TAIL_EVENTS_PATH = ROOT / "data" / "sources" / "sp500" / "tail_events.json"
UNIVERSE_KEY = "sp500"
SOURCE_REPOSITORY = "https://github.com/fja05680/sp500"
SOURCE_FILE = "S&P 500 Historical Components & Changes (Updated).csv"
MIN_REASONABLE_ROSTER = 450
MAX_REASONABLE_ROSTER = 550


@dataclass(frozen=True)
class MembershipEvent:
    effective_date: date
    additions: tuple[str, ...]
    deletions: tuple[str, ...]


def normalize_ticker(value: str) -> str:
    """Use the quote-provider convention for share-class separators."""
    return str(value).strip().upper().replace(".", "-")


def _symbols(value: object) -> frozenset[str]:
    if pd.isna(value):
        return frozenset()
    return frozenset(
        normalize_ticker(item)
        for item in str(value).split(",")
        if str(item).strip()
    )


def load_snapshots(path: Path) -> list[tuple[date, frozenset[str]]]:
    frame = pd.read_csv(path)
    if list(frame.columns) != ["date", "tickers"]:
        raise ValueError(f"{path}: expected exactly date,tickers columns")
    frame["date"] = pd.to_datetime(frame["date"], errors="raise")
    if frame["date"].duplicated().any():
        duplicated = frame.loc[frame["date"].duplicated(), "date"].dt.date.tolist()
        raise ValueError(f"{path}: duplicate snapshot dates: {duplicated[:5]}")
    frame = frame.sort_values("date").reset_index(drop=True)
    # The shared analysis-script floor catches a source/cache accidentally
    # returning rows beyond the range this builder believes it is processing.
    check_window(
        pd.DataFrame(index=pd.DatetimeIndex(frame["date"])),
        frame["date"].iloc[0].date(),
        frame["date"].iloc[-1].date(),
        label="S&P 500 membership source",
    )
    snapshots = [(stamp.date(), _symbols(raw)) for stamp, raw in zip(frame["date"], frame["tickers"])]
    for stamp, roster in snapshots:
        if not MIN_REASONABLE_ROSTER <= len(roster) <= MAX_REASONABLE_ROSTER:
            raise ValueError(
                f"{stamp}: roster contains {len(roster)} securities; expected "
                f"{MIN_REASONABLE_ROSTER}-{MAX_REASONABLE_ROSTER}"
            )
    return snapshots


def changed_snapshots(
    snapshots: Iterable[tuple[date, frozenset[str]]],
) -> list[tuple[date, frozenset[str]]]:
    """Discard repeated daily snapshots without discarding any transition."""
    changed: list[tuple[date, frozenset[str]]] = []
    for stamp, roster in snapshots:
        if not changed or roster != changed[-1][1]:
            changed.append((stamp, roster))
    return changed


def derive_events(
    snapshots: Iterable[tuple[date, frozenset[str]]],
) -> list[MembershipEvent]:
    changed = changed_snapshots(snapshots)
    return [
        MembershipEvent(
            effective_date=stamp,
            additions=tuple(sorted(roster - previous)),
            deletions=tuple(sorted(previous - roster)),
        )
        for (_previous_stamp, previous), (stamp, roster) in zip(changed, changed[1:])
    ]


def validate_forward_replay(
    snapshots: Iterable[tuple[date, frozenset[str]]],
) -> dict[str, int | str]:
    materialized = list(snapshots)
    changed = changed_snapshots(materialized)
    if not changed:
        raise ValueError("membership source contains no snapshots")
    replay = set(changed[0][1])
    events = derive_events(changed)
    for event, (stamp, expected) in zip(events, changed[1:]):
        if event.effective_date != stamp:
            raise AssertionError("derived event date diverged from source snapshot")
        replay.difference_update(event.deletions)
        replay.update(event.additions)
        if replay != set(expected):
            raise AssertionError(f"forward replay failed at {stamp}")
    return {
        "snapshotRows": len(materialized),
        "membershipIntervals": len(changed),
        "transitionDates": len(events),
        "additions": sum(len(item.additions) for item in events),
        "deletions": sum(len(item.deletions) for item in events),
        "eventLegs": sum(len(item.additions) + len(item.deletions) for item in events),
        "finalRosterSize": len(replay),
        "finalSnapshotDate": changed[-1][0].isoformat(),
    }


def validate_reverse_replay(
    snapshots: Iterable[tuple[date, frozenset[str]]],
) -> dict[str, int | bool]:
    """Walk today's roster backward through every derived transition."""
    changed = changed_snapshots(list(snapshots))
    if not changed:
        raise ValueError("membership source contains no snapshots")
    events = derive_events(changed)
    replay = set(changed[-1][1])
    for event, (_stamp, expected_previous) in zip(reversed(events), reversed(changed[:-1])):
        replay.difference_update(event.additions)
        replay.update(event.deletions)
        if replay != set(expected_previous):
            raise AssertionError(f"reverse replay failed before {event.effective_date}")
    return {"passed": True, "transitionDates": len(events), "initialRosterSize": len(replay)}


def apply_tail_events(
    snapshots: Iterable[tuple[date, frozenset[str]]],
    events: Iterable[dict],
) -> list[tuple[date, frozenset[str]]]:
    """Extend a stale snapshot file with individually sourced later events."""
    extended = list(snapshots)
    if not extended:
        raise ValueError("cannot append events to an empty membership history")
    roster = set(extended[-1][1])
    previous_date = extended[-1][0]
    for raw in sorted(events, key=lambda item: item["effectiveDate"]):
        stamp = date.fromisoformat(raw["effectiveDate"])
        additions = {normalize_ticker(item) for item in raw.get("additions", [])}
        deletions = {normalize_ticker(item) for item in raw.get("deletions", [])}
        if stamp <= previous_date:
            raise ValueError(f"tail event {stamp} is not after final source snapshot {previous_date}")
        if not raw.get("source"):
            raise ValueError(f"tail event {stamp} has no source")
        absent_deletions = deletions - roster
        duplicate_additions = additions & roster
        if absent_deletions or duplicate_additions:
            raise ValueError(
                f"tail event {stamp} does not reconcile: absent deletions="
                f"{sorted(absent_deletions)}, duplicate additions={sorted(duplicate_additions)}"
            )
        roster.difference_update(deletions)
        roster.update(additions)
        extended.append((stamp, frozenset(roster)))
        previous_date = stamp
    return extended


def build_records(
    snapshots: Iterable[tuple[date, frozenset[str]]],
    *,
    coverage_end: date,
    source_description: str,
) -> list[dict]:
    changed = changed_snapshots(snapshots)
    if coverage_end < changed[-1][0]:
        raise ValueError("coverage end predates the final source snapshot")
    records: list[dict] = []
    for index, (stamp, roster) in enumerate(changed):
        end = (
            changed[index + 1][0] - timedelta(days=1)
            if index + 1 < len(changed)
            else coverage_end
        )
        records.append({
            "effectiveStart": stamp.isoformat(),
            "effectiveEnd": end.isoformat(),
            "symbols": sorted(roster),
            "unfetchableOrDelisted": [],
            "source": source_description,
            # Membership truth and price availability are separate claims.
            # The latter stays false until every distinct historical ticker
            # is checked over its actual membership intervals.
            "priceCoverageComplete": False,
        })
    return records


def source_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_ledger(
    *,
    source_path: Path,
    source_commit: str,
    coverage_end: date,
    tail_events_path: Path | None = DEFAULT_TAIL_EVENTS_PATH,
    ledger_path: Path = LEDGER_PATH,
    provenance_path: Path = DEFAULT_PROVENANCE_PATH,
) -> dict:
    snapshots = load_snapshots(source_path)
    tail_events: list[dict] = []
    if tail_events_path is not None and tail_events_path.exists():
        raw_tail = json.loads(tail_events_path.read_text(encoding="utf-8"))
        tail_events = raw_tail.get("events", [])
        if not isinstance(tail_events, list):
            raise ValueError("tail_events.json must contain an events list")
        snapshots = apply_tail_events(snapshots, tail_events)
    audit = validate_forward_replay(snapshots)
    audit["reverseReplay"] = validate_reverse_replay(snapshots)
    digest = source_digest(source_path)
    description = (
        f"{SOURCE_REPOSITORY}@{source_commit}/{SOURCE_FILE}; sha256={digest}; "
        "open approximate research dataset, not an official S&P DJI feed"
    )
    records = build_records(
        snapshots, coverage_end=coverage_end, source_description=description,
    )
    final_roster = set(records[-1]["symbols"])
    local_current = {
        normalize_ticker(symbol)
        for symbol in json.loads((ROOT / "universes" / "sp500_current.json").read_text(encoding="utf-8"))["symbols"]
    }
    if final_roster != local_current:
        raise ValueError(
            "final historical roster does not reconcile to registered current roster; "
            f"historyOnly={sorted(final_roster-local_current)}, "
            f"registryOnly={sorted(local_current-final_roster)}"
        )

    payload = (
        json.loads(ledger_path.read_text(encoding="utf-8"))
        if ledger_path.exists()
        else {"version": 1, "universes": {}}
    )
    payload.setdefault("universes", {})[UNIVERSE_KEY] = records
    payload.setdefault("sources", {})[UNIVERSE_KEY] = {
        "repository": SOURCE_REPOSITORY,
        "file": SOURCE_FILE,
        "commit": source_commit,
        "sha256": digest,
        "license": "MIT",
        "official": False,
        "coverageStart": records[0]["effectiveStart"],
        "coverageEnd": records[-1]["effectiveEnd"],
        "priceCoverageComplete": False,
        "tailEvents": tail_events,
        "audit": audit,
    }
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    provenance_path.parent.mkdir(parents=True, exist_ok=True)
    provenance_path.write_text(
        json.dumps(payload["sources"][UNIVERSE_KEY], indent=2) + "\n",
        encoding="utf-8",
    )
    return payload["sources"][UNIVERSE_KEY]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE_PATH)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--coverage-end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    parser.add_argument("--provenance", type=Path, default=DEFAULT_PROVENANCE_PATH)
    parser.add_argument("--tail-events", type=Path, default=DEFAULT_TAIL_EVENTS_PATH)
    args = parser.parse_args()
    result = write_ledger(
        source_path=args.source,
        source_commit=args.source_commit,
        coverage_end=args.coverage_end,
        tail_events_path=args.tail_events,
        ledger_path=args.ledger,
        provenance_path=args.provenance,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
