from datetime import date

from engine.build_dow_pit_extended_membership import build_intervals
from engine.audit_dow_pit_extended_coverage import month_starts


def test_tail_changes_preserve_exactly_thirty_members():
    legacy = [{
        "effectiveStart": "2020-08-31",
        "effectiveEnd": "2035-01-01",
        "symbols": [f"S{i}" for i in range(29)],
        "unfetchableOrDelisted": ["WBA"],
        "source": "legacy source",
    }]
    legacy[0]["symbols"][0:4] = ["INTC", "DOW", "VZ", "HON"]

    rows = build_intervals(legacy, date(2026, 8, 31))

    assert all(len(row["symbols"]) == 30 for row in rows)
    assert rows[0]["effective_start"] == date(2020, 8, 31)
    assert rows[-1]["effective_end"] == date(2026, 8, 31)
    assert {"AMZN", "NVDA", "SHW", "GOOGL"} <= rows[-1]["symbols"]
    assert not ({"WBA", "INTC", "DOW", "VZ"} & rows[-1]["symbols"])


def test_tail_intervals_are_contiguous():
    legacy = [{
        "effectiveStart": "2020-08-31",
        "effectiveEnd": "2035-01-01",
        "symbols": ["INTC", "DOW", "VZ", *[f"S{i}" for i in range(26)]],
        "unfetchableOrDelisted": ["WBA"],
        "source": "legacy source",
    }]

    rows = build_intervals(legacy, date(2026, 8, 31))

    assert all(current["effective_start"].toordinal() == previous["effective_end"].toordinal() + 1
               for previous, current in zip(rows, rows[1:]))


def test_month_starts_are_inclusive_and_calendar_correct():
    assert month_starts(date(2024, 11, 1), date(2025, 2, 1)) == [
        date(2024, 11, 1), date(2024, 12, 1), date(2025, 1, 1), date(2025, 2, 1),
    ]
