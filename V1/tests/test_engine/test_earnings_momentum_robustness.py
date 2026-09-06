import json

from engine.earnings_momentum_robustness import REQUIRED_FIELDS, audit_input_gate, run_audit


def test_missing_pit_earnings_data_fails_closed(tmp_path):
    gate = audit_input_gate()
    assert gate["passed"] is False
    assert set(gate["missingFields"]) == set(REQUIRED_FIELDS)


def test_incomplete_timestamp_ledger_fails_closed(tmp_path):
    ledger = tmp_path / "earnings.json"
    ledger.write_text(json.dumps([{"symbol": "ABC", "announcement_timestamp": "2024-01-01T08:00:00"}]))
    gate = audit_input_gate(ledger)
    assert gate["passed"] is False
    assert "known_at" in gate["missingFields"]
    assert "release_session" in gate["missingFields"]


def test_data_blocked_report_withholds_outcome_statistics(tmp_path):
    result = run_audit(tmp_path / "report")
    assert result["classification"] == "DATA BLOCKED"
    assert result["testsRun"] == []
    assert "leave-one-year-out" in result["testsWithheld"]
