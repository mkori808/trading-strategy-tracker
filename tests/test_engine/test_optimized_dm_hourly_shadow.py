from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from engine import optimized_dm_hourly_shadow as hourly


NY = ZoneInfo("America/New_York")


def bars(multiplier: float, periods: int = 66) -> pd.DataFrame:
    stamps = []
    values = []
    value = 100.0
    for day in pd.bdate_range("2026-05-01", periods=periods):
        for hour, minute in ((9, 30), (10, 0), (10, 30), (11, 0)):
            stamps.append(pd.Timestamp(day.date(), tz=NY) + pd.Timedelta(hours=hour, minutes=minute))
            values.append(value)
            value *= multiplier
    return pd.DataFrame({"Open": values, "High": values, "Low": values,
                         "Close": values, "Volume": 1000}, index=stamps)


def config(start: str) -> dict:
    return {"lookbackSessions": 63, "topN": 1, "symbols": ["A", "B"],
            "parentFingerprint": "locked", "validationRunId": 33,
            "parentInceptionAt": start}


def test_hourly_simulation_uses_prior_bar_and_selects_positive_top_name():
    a = bars(1.001)
    b = bars(.999)
    start = a.index[-5].isoformat()

    rows = hourly.simulate({"A": a, "B": b}, start_at=start, top_n=1)

    assert rows
    assert rows[0]["decisionBar"] < rows[0]["timestamp"]
    assert pd.Timestamp(rows[0]["decisionBar"]).minute == 0
    assert pd.Timestamp(rows[0]["timestamp"]).minute == 30
    assert list(rows[0]["scores"]) == ["A"]
    assert rows[0]["holdings"]["A"] == pytest.approx(1.0)


def test_activation_keeps_blind_backfill_separate_from_prospective_marks(tmp_path):
    a = bars(1.001)
    b = bars(.999)
    start = a.index[-6].isoformat()
    activation = datetime(2026, 8, 5, 17, 0, tzinfo=NY)
    path = tmp_path / "hourly.json"

    result = hourly.advance(
        now=activation, path=path, bars_by_symbol={"A": a, "B": b},
        config=config(start),
    )
    status = hourly.status(path)

    assert result["status"] == "advanced"
    assert status["backfillMarks"] > 0
    assert status["prospectiveMarks"] == 0
    assert status["backfillIntent"] == "Specified before inspecting backfilled results"
    assert {row["evidenceClass"] for row in status["dailyHistory"]} == {
        "blind_pre_activation_backfill"
    }


def test_parent_fingerprint_change_fails_closed(tmp_path):
    a = bars(1.001)
    b = bars(.999)
    start = a.index[-6].isoformat()
    path = tmp_path / "hourly.json"
    hourly.advance(
        now=datetime(2026, 8, 5, 17, 0, tzinfo=NY), path=path,
        bars_by_symbol={"A": a, "B": b}, config=config(start),
    )
    changed = {**config(start), "parentFingerprint": "changed"}

    with pytest.raises(RuntimeError, match="fingerprint changed"):
        hourly.advance(
            now=datetime(2026, 8, 6, 17, 0, tzinfo=NY), path=path,
            bars_by_symbol={"A": a, "B": b}, config=changed,
        )
