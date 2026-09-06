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


# --- live intraday mark (engine/shadow_live_mark.py wiring) -----------------


def test_simulate_captures_the_exact_price_each_held_symbol_was_marked_at():
    a = bars(1.001)
    b = bars(.999)
    start = a.index[-5].isoformat()

    rows = hourly.simulate({"A": a, "B": b}, start_at=start, top_n=1)

    assert rows
    for row in rows:
        assert set(row["prices"]) == set(row["holdings"])
        for symbol in row["holdings"]:
            assert row["prices"][symbol] > 0


def test_live_mark_uses_the_captured_baseline_prices_not_a_daily_close(tmp_path, monkeypatch):
    a = bars(1.001)
    b = bars(.999)
    start = a.index[-6].isoformat()
    path = tmp_path / "hourly.json"
    hourly.advance(
        now=datetime(2026, 8, 5, 17, 0, tzinfo=NY), path=path,
        bars_by_symbol={"A": a, "B": b}, config=config(start),
    )
    latest = hourly._read(path)["rows"][-1]
    held_symbol = next(iter(latest["holdings"]))
    baseline_price = latest["prices"][held_symbol]

    # A live quote up 10% from the captured baseline price -- if the daily
    # close were used instead (never called here) the test data has no
    # daily bars at all, so this would raise or return unavailable.
    monkeypatch.setattr(
        "engine.shadow_live_mark.quotes_module.get_quotes",
        lambda symbols: {s: {"symbol": s, "price": baseline_price * 1.10} for s in symbols},
    )

    mark = hourly.live_mark(path)

    assert mark["available"] is True
    assert mark["equity"] == pytest.approx(float(latest["equity"]) * 1.10)


def test_live_mark_degrades_gracefully_without_captured_prices(tmp_path):
    path = tmp_path / "hourly.json"
    path.write_text(
        '{"strategyKey": "dm_optimized_63d_hourly", "strategyName": "x", '
        '"activatedAt": "2026-08-05T17:00:00-04:00", "backfillStart": "2026-08-01", '
        '"backfillIntent": "x", "startingEquity": 100000.0, "config": {}, '
        '"rows": [{"timestamp": "2026-08-05T15:30:00-04:00", "sessionDate": "2026-08-05", '
        '"decisionBar": "2026-08-05T15:00:00-04:00", "equity": 100500.0, "cash": 0.0, '
        '"holdings": {"A": 1.0}, "scores": {"A": 0.01}, "turnover": 0.0, '
        '"evidenceClass": "prospective_shadow"}], "lastError": null}',
        encoding="utf-8",
    )

    mark = hourly.live_mark(path)

    assert mark["available"] is False
    assert "predates" in mark["reason"]


def test_live_mark_unavailable_with_no_ledger(tmp_path):
    mark = hourly.live_mark(tmp_path / "does-not-exist.json")
    assert mark["available"] is False


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
