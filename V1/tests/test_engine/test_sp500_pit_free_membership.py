from datetime import date

import pandas as pd
import pytest

from engine.build_sp500_pit_free_membership import (
    build,
    implausible_snapshot_dates,
    intervals_from_snapshots,
    load_source_snapshots,
    reconcile,
)
from engine.build_sp500_pit_ledger import derive_events


def _write(path, rows):
    pd.DataFrame(rows, columns=["date", "tickers"]).to_csv(path, index=False)
    return path


def test_duplicate_dated_rows_keep_the_last_row_and_are_disclosed(tmp_path):
    path = _write(tmp_path / "dup.csv", [
        ("2024-01-02", ",".join(f"S{i}" for i in range(495))),
        ("2024-01-03", ",".join([*(f"S{i}" for i in range(495)), "KDP"])),
        ("2024-01-03", ",".join([*(f"S{i}" for i in range(1, 495)), "KDP"])),
    ])
    snapshots, duplicate_rows = load_source_snapshots(path)

    assert duplicate_rows == 1
    assert len(snapshots) == 2
    assert "S0" not in snapshots[-1][1]
    assert "KDP" in snapshots[-1][1]


def test_strict_mode_rejects_an_implausible_roster_size(tmp_path):
    path = _write(tmp_path / "small.csv", [
        ("2024-01-02", ",".join(f"S{i}" for i in range(300))),
    ])
    with pytest.raises(ValueError, match="expected"):
        load_source_snapshots(path, strict_roster_size=True)


def test_non_strict_mode_tolerates_a_small_but_plausible_roster(tmp_path):
    path = _write(tmp_path / "small.csv", [
        ("2024-01-02", ",".join(f"S{i}" for i in range(300))),
    ])
    snapshots, _ = load_source_snapshots(path, strict_roster_size=False)
    assert len(snapshots[0][1]) == 300


def test_non_strict_mode_still_rejects_a_parse_catastrophe(tmp_path):
    path = _write(tmp_path / "tiny.csv", [
        ("2024-01-02", "S0"),
    ])
    with pytest.raises(ValueError, match="parse failure"):
        load_source_snapshots(path, strict_roster_size=False)


def test_intervals_from_snapshots_tracks_per_ticker_tenure():
    snapshots = [
        (date(2024, 1, 2), frozenset({"A", "B", "C"})),
        (date(2024, 2, 1), frozenset({"A", "C", "NEW"})),
        (date(2024, 3, 1), frozenset({"A", "NEW"})),
    ]
    intervals = intervals_from_snapshots(
        snapshots, coverage_end=date(2024, 4, 1), source="test",
        still_open_as_of_coverage_end=True,
    )
    by_ticker = {item.ticker: item for item in intervals}

    assert by_ticker["B"].start_date == date(2024, 1, 2)
    assert by_ticker["B"].end_date == date(2024, 1, 31)
    assert by_ticker["C"].start_date == date(2024, 1, 2)
    assert by_ticker["C"].end_date == date(2024, 2, 29)
    assert by_ticker["NEW"].start_date == date(2024, 2, 1)
    assert by_ticker["NEW"].end_date == date(2024, 4, 1)
    assert by_ticker["A"].end_date == date(2024, 4, 1)


def test_implausible_windows_merge_across_a_long_gap_between_changes():
    """Regression test for a real bug: two flagged snapshots more than the
    old 120-day calendar tolerance apart must still merge into one window
    when nothing plausible happened in between -- a long gap between
    recorded CHANGES is not evidence the roster became plausible meanwhile.
    """
    snapshots = [
        (date(2000, 1, 1), frozenset(f"S{i}" for i in range(440))),
        (date(2000, 3, 1), frozenset([*(f"S{i}" for i in range(440)), "X"])),
        (date(2001, 6, 1), frozenset([*(f"S{i}" for i in range(440)), "Y"])),
        (date(2005, 1, 1), frozenset(f"S{i}" for i in range(495))),
    ]
    windows = implausible_snapshot_dates(snapshots, minimum=485)

    assert len(windows) == 1
    start, end, worst = windows[0]
    assert start == date(2000, 1, 1)
    assert end == date(2001, 6, 1)
    assert worst == 440


def test_implausible_window_closes_once_a_plausible_snapshot_occurs():
    snapshots = [
        (date(2000, 1, 1), frozenset(f"S{i}" for i in range(440))),
        (date(2000, 6, 1), frozenset(f"S{i}" for i in range(500))),
        (date(2001, 1, 1), frozenset(f"S{i}" for i in range(440))),
    ]
    windows = implausible_snapshot_dates(snapshots, minimum=485)

    assert len(windows) == 2
    assert windows[0] == (date(2000, 1, 1), date(2000, 1, 1), 440)
    assert windows[1] == (date(2001, 1, 1), date(2001, 1, 1), 440)


def test_reconcile_confirms_matching_events_within_tolerance():
    primary = [
        (date(2024, 1, 2), frozenset({"A", "B"})),
        (date(2024, 2, 1), frozenset({"A", "NEW"})),
    ]
    secondary = [
        (date(2024, 1, 2), frozenset({"A", "B"})),
        (date(2024, 2, 3), frozenset({"A", "NEW"})),  # recorded 2 days later
    ]
    rows = reconcile(
        derive_events(primary), derive_events(secondary),
        overlap_start=date(2024, 1, 1), overlap_end=date(2024, 3, 1),
    )
    by_ticker_action = {(row["ticker"], row["action"]): row for row in rows}

    # Both legs of one diff share the later snapshot's date, so B's removal
    # picks up the same 2-day offset as NEW's addition.
    assert by_ticker_action[("B", "REMOVE")]["confidence"] == "CONFIRMED_WITH_DATE_OFFSET"
    assert by_ticker_action[("NEW", "ADD")]["confidence"] == "CONFIRMED_WITH_DATE_OFFSET"
    assert by_ticker_action[("NEW", "ADD")]["agreement"] is True


def test_reconcile_flags_a_real_disagreement_as_unresolved():
    primary = [
        (date(2024, 1, 2), frozenset({"A", "B"})),
        (date(2024, 2, 1), frozenset({"A"})),
    ]
    secondary = [
        (date(2024, 1, 2), frozenset({"A", "B"})),
        (date(2024, 2, 1), frozenset({"A", "B"})),  # never recorded the removal
    ]
    rows = reconcile(
        derive_events(primary), derive_events(secondary),
        overlap_start=date(2024, 1, 1), overlap_end=date(2024, 3, 1),
    )

    assert len(rows) == 1
    assert rows[0]["ticker"] == "B"
    assert rows[0]["confidence"] == "UNRESOLVED"
    assert rows[0]["agreement"] is False


def test_reconcile_does_not_count_a_secondary_defect_window_as_a_real_disagreement():
    primary = [
        (date(2024, 1, 2), frozenset({"A", "B"})),
        (date(2024, 2, 1), frozenset({"A"})),
    ]
    secondary = [
        (date(2024, 1, 2), frozenset({"A", "B"})),
        (date(2024, 2, 1), frozenset({"A", "B"})),
    ]
    rows = reconcile(
        derive_events(primary), derive_events(secondary),
        overlap_start=date(2024, 1, 1), overlap_end=date(2024, 3, 1),
        secondary_implausible_ranges=[(date(2024, 1, 1), date(2024, 3, 1), 400)],
    )

    assert rows[0]["confidence"] == "NOT_CROSS_CHECKED"
    assert rows[0]["agreement"] is False


def test_build_end_to_end_writes_disclosed_gap_and_intervals(tmp_path):
    primary_path = _write(tmp_path / "primary.csv", [
        ("2024-01-02", ",".join(f"S{i}" for i in range(500))),
        ("2024-02-01", ",".join([*(f"S{i}" for i in range(1, 500)), "NEW"])),
    ])
    secondary_path = _write(tmp_path / "secondary.csv", [
        ("2024-01-02", ",".join(f"S{i}" for i in range(500))),
        ("2024-02-01", ",".join([*(f"S{i}" for i in range(1, 500)), "NEW"])),
        ("2024-03-01", ",".join([*(f"S{i}" for i in range(2, 500)), "NEW", "NEXT"])),
    ])
    result = build(
        primary_path=primary_path,
        secondary_path=secondary_path,
        intervals_path=tmp_path / "intervals.csv",
        reconciliation_path=tmp_path / "reconciliation.csv",
        provenance_path=tmp_path / "provenance.json",
        audit_path=tmp_path / "audit.json",
    )

    assert result["crossCheckWindow"]["agreementRate"] == 1.0
    assert result["singleSourceExtension"]["start"] == "2024-02-02"
    assert result["singleSourceExtension"]["end"] == "2024-03-01"
    assert result["uncoveredGap"] is not None
    assert (tmp_path / "intervals.csv").exists()
    assert (tmp_path / "provenance.json").exists()
    intervals_text = (tmp_path / "intervals.csv").read_text(encoding="utf-8")
    assert "NEXT,2024-03-01" in intervals_text
