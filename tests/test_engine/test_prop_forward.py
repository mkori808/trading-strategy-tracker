import json
from datetime import date, datetime, timedelta, timezone

import pytest

from engine import prop_forward as pf


@pytest.fixture
def locked(monkeypatch):
    prereg = json.loads(pf.PREREG.read_text(encoding="utf-8"))
    config = {"validationRunId": 33,
              "params": {"lookback_trading_days": 63, "rebalance_frequency": "daily"},
              "symbols": prereg["strategy"]["universe"],
              "identity": {"fingerprintMatches": True}}
    monkeypatch.setattr(pf, "_fingerprint", lambda: ("locked-fingerprint", config))


def snap(at, equity=100_000.0, positions=None):
    return {"observedAt": at, "sessionDate": at[:10], "equity": equity,
            "priorCloseEquity": 100_000.0, "cash": 10_000.0,
            "totalSessionPl": equity - 100_000.0, "positions": positions or [], "orders": []}


def test_snapshot_is_append_only_and_idempotent(tmp_path, locked):
    path = tmp_path / "forward.db"
    first = pf.append_snapshot(snap("2026-08-24T09:35:00-04:00"), path)
    second = pf.append_snapshot(snap("2026-08-24T09:35:00-04:00"), path)
    assert first == (1, True)
    assert second == (1, False)
    conn = pf.get_connection(path)
    assert conn.execute("select count(*) from raw_equity_snapshots").fetchone()[0] == 1
    assert conn.execute("select count(*) from shadow_marks").fetchone()[0] == 2
    assert conn.execute("select count(*) from self_funded_marks").fetchone()[0] == 2


def test_self_funded_shadows_share_marks_but_have_no_prop_lifecycle(tmp_path, locked):
    path = tmp_path / "forward.db"
    pf.append_snapshot(snap("2026-08-24T09:35:00-04:00", 100_000), path)
    pf.append_snapshot(snap("2026-08-24T09:40:00-04:00", 110_000), path)
    conn = pf.get_connection(path)
    for key, spec in pf.SELF_FUNDED_SHADOWS.items():
        state = json.loads(conn.execute("select state_json from self_funded_state where shadow_key=?", (key,)).fetchone()[0])
        assert state["equity"] == pytest.approx(spec["startingCapital"] + spec["fixedExposure"] * 0.10)
        assert "phase" not in state
        assert "fees" not in state
    result = pf.status(path, datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc))
    assert len(result["selfFundedShadows"]) == 2
    assert all(row["stopoutRule"] is None for row in result["selfFundedShadows"])


def test_account_level_exposure_contribution_and_session_extrema(tmp_path, locked):
    path = tmp_path / "forward.db"
    positions = [
        {"symbol": "GAIN", "side": "long", "marketValue": 60_000, "unrealizedIntradayPl": 5_000},
        {"symbol": "LOSS", "side": "long", "marketValue": 40_000, "unrealizedIntradayPl": -4_000},
    ]
    pf.append_snapshot(snap("2026-08-24T09:35:00-04:00", 101_000, positions), path)
    pf.append_snapshot(snap("2026-08-24T10:00:00-04:00", 99_000, positions), path)
    pf.append_snapshot(snap("2026-08-24T15:55:00-04:00", 102_000, positions), path)
    pf.append_snapshot(snap("2026-08-25T09:35:00-04:00", 102_500, positions), path)
    conn = pf.get_connection(path)
    raw = conn.execute("select * from raw_equity_snapshots order by id limit 1").fetchone()
    assert raw["gross_exposure"] == 100_000
    assert raw["net_exposure"] == 100_000
    assert json.loads(raw["contributions_json"]) == {"GAIN": 5000.0, "LOSS": -4000.0}
    session = conn.execute("select * from session_outcomes where session_date='2026-08-24'").fetchone()
    assert session["opening_equity"] == 101_000
    assert session["minimum_equity"] == 99_000
    assert session["maximum_equity"] == 102_000
    assert session["closing_equity"] == 102_000
    assert session["maximum_sampling_gap_seconds"] > pf.FRESH_SECONDS


def test_closed_market_finalizes_only_prior_unsealed_sessions(tmp_path, locked):
    path = tmp_path / "forward.db"
    pf.append_snapshot(snap("2026-08-24T15:55:00-04:00", 101_000), path)
    pf.append_snapshot(snap("2026-08-25T15:55:00-04:00", 102_000), path)
    conn = pf.get_connection(path)
    conn.execute("DELETE FROM session_outcomes")
    conn.commit()
    conn.close()

    finalized = pf.finalize_prior_sessions(
        "2026-08-25T20:00:00-04:00", path=path,
    )

    assert finalized == ["2026-08-24"]
    conn = pf.get_connection(path)
    assert [row[0] for row in conn.execute(
        "SELECT session_date FROM session_outcomes ORDER BY session_date"
    )] == ["2026-08-24"]
    conn.close()
    assert pf.finalize_prior_sessions(
        "2026-08-25T20:05:00-04:00", path=path,
    ) == []


def test_closed_market_collection_runs_session_finalizer(monkeypatch):
    monkeypatch.setattr(pf.alpaca_trading, "get_clock", lambda: {
        "available": True, "isOpen": False,
        "timestamp": "2026-08-30T12:00:00-04:00",
    })
    monkeypatch.setattr(
        pf, "finalize_prior_sessions", lambda completed_at: ["2026-08-28"],
    )

    assert pf.collect_once() == {
        "collected": False, "reason": "market_closed",
        "finalizedSessions": ["2026-08-28"],
    }


def test_interval_contributions_adjust_for_fills_and_reconcile_to_equity(tmp_path, locked):
    path = tmp_path / "forward.db"
    first_positions = [{"symbol": "A", "side": "long", "marketValue": 10_000}]
    pf.append_snapshot(snap("2026-08-24T09:35:00-04:00", 100_000, first_positions), path)
    second = snap(
        "2026-08-24T09:40:00-04:00", 100_600,
        [{"symbol": "A", "side": "long", "marketValue": 10_500},
         {"symbol": "B", "side": "long", "marketValue": 2_050}],
    )
    second["orders"] = [{
        "symbol": "B", "side": "buy", "filledQty": 20, "filledAvgPrice": 100,
        "filledAt": "2026-08-24T09:38:00-04:00",
    }]
    pf.append_snapshot(second, path)
    conn = pf.get_connection(path)
    raw = conn.execute("select * from raw_equity_snapshots order by id desc limit 1").fetchone()
    contribution = json.loads(raw["contributions_json"])
    assert contribution["A"] == 500
    assert contribution["B"] == 50
    assert contribution["__cash_fees_and_unattributed__"] == 50
    assert sum(contribution.values()) == 600


def test_breach_is_permanent_and_new_attempt_starts(tmp_path, locked):
    path = tmp_path / "forward.db"
    pf.append_snapshot(snap("2026-08-24T09:35:00-04:00", 100_000), path)
    pf.append_snapshot(snap("2026-08-24T10:00:00-04:00", 80_000), path)
    conn = pf.get_connection(path)
    for key in pf.SHADOWS:
        state = json.loads(conn.execute("select state_json from shadow_state where shadow_key=?", (key,)).fetchone()[0])
        assert state["failures"] == 1
        assert state["attempts"] == 2
        assert state["fees"] == 1000
        breach = conn.execute("select * from shadow_events where shadow_key=? and event_type='breach'", (key,)).fetchone()
        assert breach is not None
        assert breach["amount_beyond"] > 0


def test_challenge_transitions_to_funded(tmp_path, locked):
    path = tmp_path / "forward.db"
    pf.append_snapshot(snap("2026-08-24T09:35:00-04:00", 100_000), path)
    # +40% underlying -> +8% at the 0.20x shadow and +10% at 0.25x.
    pf.append_snapshot(snap("2026-08-24T10:00:00-04:00", 140_000), path)
    conn = pf.get_connection(path)
    for key in pf.SHADOWS:
        state = json.loads(conn.execute("select state_json from shadow_state where shadow_key=?", (key,)).fetchone()[0])
        assert state["phase"] == "funded"
        assert state["fundedTransitions"] == 1
        assert conn.execute("select count(*) from shadow_events where shadow_key=? and event_type='funded_transition'", (key,)).fetchone()[0] == 1


def test_static_and_trailing_drawdown_breaches_persist(tmp_path, locked):
    path = tmp_path / "forward.db"
    equity = 100_000.0
    start = date(2026, 8, 24)
    pf.append_snapshot(snap(f"{start.isoformat()}T09:35:00-04:00", equity), path)
    for offset in range(1, 5):
        equity *= 0.92  # -8% underlying: -2,000 at .25x / -1,600 at .20x per session
        day = start + timedelta(days=offset)
        pf.append_snapshot(snap(f"{day.isoformat()}T09:35:00-04:00", equity), path)
    conn = pf.get_connection(path)
    for key in pf.SHADOWS:
        breach = conn.execute("select * from shadow_events where shadow_key=? and event_type='breach'", (key,)).fetchone()
        assert breach is not None
        assert json.loads(breach["payload_json"])["rule"] == "drawdown"


def test_monthly_payout_uses_split_and_retains_fee_accounting(tmp_path, locked):
    path = tmp_path / "forward.db"
    start = date(2026, 8, 24)
    pf.append_snapshot(snap(f"{start.isoformat()}T09:35:00-04:00", 100_000), path)
    pf.append_snapshot(snap(f"{start.isoformat()}T10:00:00-04:00", 140_000), path)
    equity = 140_000.0
    for offset in range(1, 23):
        equity *= 1.01
        day = start + timedelta(days=offset)
        pf.append_snapshot(snap(f"{day.isoformat()}T09:35:00-04:00", equity), path)
    conn = pf.get_connection(path)
    for key in pf.SHADOWS:
        event = conn.execute("select * from shadow_events where shadow_key=? and event_type='payout'", (key,)).fetchone()
        assert event is not None
        payload = json.loads(event["payload_json"])
        assert payload["traderPayout"] == pytest.approx(payload["grossProfit"] * 0.8)
        state = json.loads(conn.execute("select state_json from shadow_state where shadow_key=?", (key,)).fetchone()[0])
        assert state["fees"] == 500
        assert state["grossPayout"] > 0


def test_status_separates_evidence_and_preserves_controls(tmp_path, locked):
    path = tmp_path / "forward.db"
    pf.append_snapshot(snap("2026-08-24T09:35:00-04:00"), path)
    result = pf.status(path, datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc))
    assert result["evidenceClass"] == "prospective_prop_shadow"
    assert result["historicalEvidenceKeptSeparate"] is True
    assert result["parentStrategyId"] == "dm_optimized_63d_daily"
    assert result["parentStrategyFingerprint"] == "locked-fingerprint"
    assert len(result["shadows"]) == 2
    assert "Canonical DM" in result["controlsPreserved"]


def test_status_exposes_current_parent_stocks_for_scaled_shadow_positions(tmp_path, locked):
    path = tmp_path / "forward.db"
    positions = [{"symbol": "AAA", "side": "long", "marketValue": 20_000,
                  "currentPrice": 50, "unrealizedIntradayPl": 100}]
    pf.append_snapshot(snap("2026-08-24T09:35:00-04:00", 100_000, positions), path)

    result = pf.status(path, datetime(2026, 8, 24, 14, 0, tzinfo=timezone.utc))

    assert result["currentParentPositions"][0]["symbol"] == "AAA"
    assert result["currentParentPositions"][0]["weight"] == pytest.approx(.20)


def test_status_exposes_only_finalized_session_closes_for_account_views(tmp_path, locked):
    path = tmp_path / "forward.db"
    pf.append_snapshot(snap("2026-08-24T09:35:00-04:00", 100_000), path)
    pf.append_snapshot(snap("2026-08-24T15:55:00-04:00", 102_000), path)
    pf.append_snapshot(snap("2026-08-25T09:35:00-04:00", 103_000), path)

    result = pf.status(path, datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc))

    assert result["shadows"][0]["history"] == [{
        "date": "2026-08-24", "equity": pytest.approx(100_400),
    }]
    assert result["selfFundedShadows"][0]["history"] == [{
        "date": "2026-08-24", "equity": pytest.approx(20_400),
    }]


def test_fingerprint_change_blocks_new_observations(tmp_path, locked, monkeypatch):
    path = tmp_path / "forward.db"
    pf.append_snapshot(snap("2026-08-24T09:35:00-04:00"), path)
    monkeypatch.setattr(pf, "_fingerprint", lambda: ("changed", {}))
    with pytest.raises(RuntimeError, match="fingerprint"):
        pf.append_snapshot(snap("2026-08-24T09:40:00-04:00"), path)


def test_account_contamination_blocks_prop_observation(monkeypatch):
    monkeypatch.setattr(pf.alpaca_trading, "get_clock", lambda: {
        "available": True, "isOpen": True, "timestamp": "2026-08-24T10:00:00-04:00",
    })
    monkeypatch.setattr(pf.alpaca_trading, "account_snapshot", lambda: {
        "account": {"available": True, "accountNumber": "PAPER-1", "equity": 100_000,
                    "lastEquity": 100_000, "cash": 10_000},
        "positions": [], "orders": [],
    })
    monkeypatch.setattr(pf.execution_ownership, "monitor_account", lambda *a, **k: {
        "actionRequired": True, "integrityIssues": [{"type": "unknown_fill_attribution"}],
    })
    monkeypatch.setattr(pf, "append_snapshot", lambda *a, **k: pytest.fail("contaminated equity must not be appended"))
    result = pf.collect_once()
    assert result["collected"] is False
    assert result["reason"] == "account_contamination"
