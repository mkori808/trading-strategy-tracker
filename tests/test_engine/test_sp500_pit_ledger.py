from datetime import date
import json

import pandas as pd

from engine.build_sp500_pit_ledger import (
    apply_tail_events,
    build_records,
    derive_events,
    load_snapshots,
    validate_forward_replay,
    validate_reverse_replay,
)
from engine.universe_ledger import PointInTimeSchedule


def _source(tmp_path):
    path = tmp_path / "history.csv"
    pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-02-01", "2024-03-01"],
        "tickers": [
            ",".join(f"S{i}" for i in range(500)),
            ",".join(f"S{i}" for i in range(500)),
            ",".join([*(f"S{i}" for i in range(1, 500)), "NEW"]),
            ",".join([*(f"S{i}" for i in range(2, 500)), "NEW", "NEXT"]),
        ],
    }).to_csv(path, index=False)
    return path


def test_builder_compresses_duplicate_snapshots_and_replays_every_change(tmp_path):
    snapshots = load_snapshots(_source(tmp_path))
    events = derive_events(snapshots)
    audit = validate_forward_replay(snapshots)

    assert len(events) == 2
    assert events[0].effective_date == date(2024, 2, 1)
    assert events[0].additions == ("NEW",)
    assert events[0].deletions == ("S0",)
    assert audit["membershipIntervals"] == 3
    assert audit["eventLegs"] == 4
    assert validate_reverse_replay(snapshots)["passed"] is True


def test_real_effective_date_flips_eligibility_without_lookahead(tmp_path):
    snapshots = load_snapshots(_source(tmp_path))
    records = build_records(
        snapshots,
        coverage_end=date(2024, 4, 30),
        source_description="fixture",
    )
    schedule = PointInTimeSchedule("sp500", records)

    assert "S0" in schedule.membership_at(date(2024, 1, 31))
    assert "NEW" not in schedule.membership_at(date(2024, 1, 31))
    assert "S0" not in schedule.membership_at(date(2024, 2, 1))
    assert "NEW" in schedule.membership_at(date(2024, 2, 1))


def test_new_ledger_records_cannot_claim_price_coverage_before_fetchability_audit(tmp_path):
    records = build_records(
        load_snapshots(_source(tmp_path)),
        coverage_end=date(2024, 4, 30),
        source_description="fixture",
    )

    assert all(row["priceCoverageComplete"] is False for row in records)
    assert all(row["source"] == "fixture" for row in records)


def test_sourced_tail_event_extends_stale_snapshot_without_guessing(tmp_path):
    snapshots = load_snapshots(_source(tmp_path))
    extended = apply_tail_events(snapshots, [{
        "effectiveDate": "2024-04-01",
        "additions": ["TAIL"],
        "deletions": ["S2"],
        "source": "official release",
    }])

    assert "S2" not in extended[-1][1]
    assert "TAIL" in extended[-1][1]
    assert validate_forward_replay(extended)["eventLegs"] == 6


def test_real_sp500_add_remove_effective_dates_flip_exactly():
    payload = json.loads((
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "data" / "universe_membership.json"
    ).read_text(encoding="utf-8"))
    schedule = PointInTimeSchedule("sp500", payload["universes"]["sp500"])

    # Tesla replaced Apartment Investment & Management before the open on
    # 2020-12-21. Neither side may flip a day early or late.
    assert "AIV" in schedule.membership_at(date(2020, 12, 20))
    assert "TSLA" not in schedule.membership_at(date(2020, 12, 20))
    assert "AIV" not in schedule.membership_at(date(2020, 12, 21))
    assert "TSLA" in schedule.membership_at(date(2020, 12, 21))

    # The separately sourced tail event must reconcile the stale source file
    # to today's roster at the stated pre-open effective date.
    assert "EA" in schedule.membership_at(date(2026, 8, 4))
    assert "FERG" not in schedule.membership_at(date(2026, 8, 4))
    assert "EA" not in schedule.membership_at(date(2026, 8, 5))
    assert "FERG" in schedule.membership_at(date(2026, 8, 5))
