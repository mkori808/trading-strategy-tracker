"""Build the fail-closed ``dow_pit_extended_v1`` membership ledger.

This is membership work only. It copies the existing sourced 2000-2020 Dow
reconstruction without altering it, restores each disclosed unavailable member
to the full 30-name roster, and appends official S&P DJI changes through the
target dataset date. It never runs a strategy or declares prices complete.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LEGACY_LEDGER_PATH = ROOT / "data" / "universe_membership.json"
OUT_PATH = ROOT / "data" / "dow_pit_extended" / "membership" / "intervals.csv"
AUDIT_PATH = ROOT / "data" / "dow_pit_extended" / "audits" / "membership_audit.json"
TARGET_END = date(2026, 8, 31)

OFFICIAL_TAIL_CHANGES = [
    {
        "announced_at": date(2024, 2, 20),
        "effective_start": date(2024, 2, 26),
        "adds": {"AMZN"},
        "removes": {"WBA"},
        "source": "https://press.spglobal.com/2024-02-20-Amazon-com-Set-to-Join-Dow-Jones-Industrial-Average-Uber-to-Join-Dow-Jones-Transportation-Average",
    },
    {
        "announced_at": date(2024, 11, 1),
        "effective_start": date(2024, 11, 8),
        "adds": {"NVDA", "SHW"},
        "removes": {"INTC", "DOW"},
        "source": "https://press.spglobal.com/2024-11-01-NVIDIA-and-Sherwin-Williams-Set-to-Join-Dow-Jones-Industrial-Average-Vistra-to-Join-Dow-Jones-Utility-Average",
    },
    {
        "announced_at": date(2026, 6, 23),
        "effective_start": date(2026, 6, 29),
        "adds": {"GOOGL"},
        "removes": {"VZ"},
        "source": "https://press.spglobal.com/2026-06-23-Alphabet-Set-to-Join-and-Honeywell-International-to-Remain-in-Dow-Jones-Industrial-Average",
    },
]


def _full_roster(row: dict) -> set[str]:
    return {str(item) for item in [*row["symbols"], *row.get("unfetchableOrDelisted", [])]}


def build_intervals(legacy_rows: list[dict], target_end: date = TARGET_END) -> list[dict]:
    if not legacy_rows:
        raise ValueError("legacy Dow ledger is empty")
    intervals = []
    for row in legacy_rows[:-1]:
        roster = _full_roster(row)
        if len(roster) != 30:
            raise ValueError(f"{row['effectiveStart']}: expected 30 members, found {len(roster)}")
        intervals.append({
            "effective_start": date.fromisoformat(row["effectiveStart"]),
            "effective_end": date.fromisoformat(row["effectiveEnd"]),
            "announced_at": None,
            "symbols": roster,
            "source": row["source"],
        })

    last = legacy_rows[-1]
    roster = _full_roster(last)
    cursor = date.fromisoformat(last["effectiveStart"])
    current_source = last["source"]
    current_announced = None
    for change in OFFICIAL_TAIL_CHANGES:
        if change["effective_start"] > target_end:
            break
        intervals.append({
            "effective_start": cursor,
            "effective_end": change["effective_start"] - timedelta(days=1),
            "announced_at": current_announced,
            "symbols": set(roster),
            "source": current_source,
        })
        if not change["removes"] <= roster:
            raise ValueError(f"cannot remove absent members {sorted(change['removes'] - roster)}")
        roster = (roster - change["removes"]) | change["adds"]
        if len(roster) != 30:
            raise ValueError(f"{change['effective_start']}: expected 30 members, found {len(roster)}")
        cursor = change["effective_start"]
        current_source = change["source"]
        current_announced = change["announced_at"]

    intervals.append({
        "effective_start": cursor,
        "effective_end": target_end,
        "announced_at": current_announced,
        "symbols": set(roster),
        "source": current_source,
    })
    return intervals


def build(
    legacy_path: Path = LEGACY_LEDGER_PATH,
    out_path: Path = OUT_PATH,
    audit_path: Path = AUDIT_PATH,
    target_end: date = TARGET_END,
) -> dict:
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    legacy_rows = payload["universes"]["dow_jones_industrial_average"]
    intervals = build_intervals(legacy_rows, target_end)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["effective_start", "effective_end", "announced_at", "ticker", "source"])
        for interval in intervals:
            for ticker in sorted(interval["symbols"]):
                writer.writerow([
                    interval["effective_start"].isoformat(), interval["effective_end"].isoformat(),
                    interval["announced_at"].isoformat() if interval["announced_at"] else "",
                    ticker, interval["source"],
                ])

    gaps = []
    for previous, current in zip(intervals, intervals[1:]):
        if current["effective_start"] != previous["effective_end"] + timedelta(days=1):
            gaps.append([previous["effective_end"].isoformat(), current["effective_start"].isoformat()])
    audit = {
        "dataset": "dow_pit_extended_v1",
        "targetStart": intervals[0]["effective_start"].isoformat(),
        "targetEnd": target_end.isoformat(),
        "intervalCount": len(intervals),
        "distinctTickerCount": len({ticker for row in intervals for ticker in row["symbols"]}),
        "allIntervalsContainExactly30Members": all(len(row["symbols"]) == 30 for row in intervals),
        "membershipGaps": gaps,
        "officialTailChanges": [
            {
                "announcedAt": row["announced_at"].isoformat(),
                "effectiveStart": row["effective_start"].isoformat(),
                "adds": sorted(row["adds"]), "removes": sorted(row["removes"]),
                "source": row["source"],
            }
            for row in OFFICIAL_TAIL_CHANGES if row["effective_start"] <= target_end
        ],
        "passed": all(len(row["symbols"]) == 30 for row in intervals) and not gaps,
        "note": "Membership completeness only; price and identity coverage are separate fail-closed gates.",
    }
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    return audit


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))
