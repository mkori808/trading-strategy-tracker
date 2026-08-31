import json

import pytest

from engine import execution_db, execution_ownership as ownership


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(execution_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(execution_db, "DB_PATH", tmp_path / "execution.db")
    execution_db.set_config("Strategy A", True, json.dumps({"lookback_trading_days": 10}),
                            "2026-08-17T10:00:00-04:00", symbols=json.dumps(["AAA"]))
    return tmp_path


def test_one_fingerprinted_owner_and_second_strategy_fails_closed(isolated):
    status = ownership.bootstrap_current_owner("PAPER-1")
    assert status["owner"]["strategyName"] == "Strategy A"
    assert status["multiStrategyExecution"] is False
    fingerprint, payload = ownership.configured_identity("Strategy A")
    ownership.assert_can_enable("PAPER-1", "Strategy A", fingerprint, payload["identity"]["key"])
    with pytest.raises(ownership.OwnershipConflict, match="shadow forward test or assign it"):
        ownership.assert_strategy_slot("PAPER-1", "Strategy B")


def test_fingerprint_change_revokes_authority_without_reassigning_owner(isolated):
    before = ownership.bootstrap_current_owner("PAPER-1")
    execution_db.set_config("Strategy A", True, json.dumps({"lookback_trading_days": 11}),
                            "2026-08-18T10:00:00-04:00", symbols=json.dumps(["AAA"]))
    changed_fingerprint, changed_payload = ownership.configured_identity("Strategy A")
    with pytest.raises(ownership.OwnershipConflict, match="Display-name equality cannot authorize"):
        ownership.assert_can_enable(
            "PAPER-1", "Strategy A", changed_fingerprint, changed_payload["identity"]["key"],
        )
    allowed, reason = ownership.verify_execution_authority("PAPER-1", "Strategy A")
    after = ownership.ownership_status("PAPER-1")
    assert allowed is False
    assert "fingerprint" in reason.lower()
    assert after["owner"]["strategyFingerprint"] == before["owner"]["strategyFingerprint"]
    assert after["actionRequired"] is True


def test_unknown_fill_and_inconsistent_position_are_append_only_action_required(isolated):
    ownership.bootstrap_current_owner("PAPER-1")
    conn = execution_db.get_connection()
    with conn:
        conn.execute(
            "INSERT INTO rebalance_runs (strategy_name,rebalance_date,trigger_source,triggered_at,status,target_weights) VALUES (?,?,?,?,?,?)",
            ("Strategy A", "2026-08-18", "scheduled", "2026-08-18T10:00:00-04:00", "completed", json.dumps({"AAA": 1.0})),
        )
    conn.close()
    status = ownership.monitor_account(
        "PAPER-1",
        [{"id": "manual-1", "symbol": "ZZZ", "submittedAt": "2026-08-18T14:00:00+00:00",
          "filledAt": "2026-08-18T14:01:00+00:00", "filledQty": 2, "attributed": False}],
        [{"symbol": "ZZZ"}], detected_at="2026-08-18T14:02:00+00:00",
    )
    assert status["actionRequired"] is True
    assert {row["type"] for row in status["integrityIssues"]} == {
        "unknown_fill_attribution", "position_inconsistent_with_owner",
    }
    ownership.monitor_account(
        "PAPER-1",
        [{"id": "manual-1", "symbol": "ZZZ", "submittedAt": "2026-08-18T14:00:00+00:00",
          "filledAt": "2026-08-18T14:01:00+00:00", "filledQty": 2, "attributed": False}],
        [{"symbol": "ZZZ"}], detected_at="2026-08-18T14:03:00+00:00",
    )
    assert len(ownership.ownership_status("PAPER-1")["integrityIssues"]) == 2


def test_multiple_accounts_are_independent_and_existing_records_are_preserved(isolated):
    ownership.bootstrap_current_owner("PAPER-1")
    conn = execution_db.get_connection()
    before = conn.execute("SELECT COUNT(*) FROM rebalance_runs").fetchone()[0]
    conn.close()
    execution_db.set_enabled("Strategy A", False, "2026-08-19T09:00:00-04:00")
    execution_db.set_config("Strategy B", True, "{}", "2026-08-19T09:01:00-04:00", symbols="[]")
    second = ownership.bootstrap_current_owner("PAPER-2")
    assert second["owner"]["strategyName"] == "Strategy B"
    assert ownership.ownership_status("PAPER-1")["owner"]["strategyName"] == "Strategy A"
    conn = execution_db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM brokerage_accounts").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM rebalance_runs").fetchone()[0] == before
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
