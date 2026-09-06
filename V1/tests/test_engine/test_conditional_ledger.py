"""engine/conditional_ledger.py: freeze/version immutability, contract
integrity, and the permanent record of rejected hypotheses and consumed
holdouts.

Uses a real temporary SQLite file (same pattern as test_logging_db.py) so
the schema migration and constraint behaviour (the UNIQUE(hypothesis_key,
version) index in particular) is exercised for real."""

from __future__ import annotations

import pytest

from engine import conditional_ledger as ledger
from engine import logging_db
from engine import pit_features

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(logging_db, "LOGS_DIR", tmp_path)
    return logging_db


CONDITIONS = [{"feature": "rsi2", "op": "<", "value": 30.0, "rawEdge": 27.4, "source": "quantile", "description": "RSI(2) below 30"}]


def _freeze(**overrides):
    payload = dict(
        strategy_name="Connors Mean Reversion (RSI2)", engine="standard", outcome_column="realized_r",
        conditions=CONDITIONS, expected_direction="higher", primary_metric="Expectancy (R)",
        minimum_effect=0.05, minimum_observations=30,
        validation_start="2024-01-01", validation_end="2024-06-01",
        holdout_start="2024-06-01", holdout_end="2025-01-01",
    )
    payload.update(overrides)
    return ledger.freeze(**payload)


def test_freeze_creates_version_one(db):
    frozen = _freeze()
    assert frozen.version == 1
    assert frozen.status == ledger.STATUS_FROZEN
    assert frozen.row_id is not None


def test_freeze_rejects_exact_duplicate(db):
    _freeze()
    with pytest.raises(ledger.HypothesisFrozenError):
        _freeze()


def test_freeze_rejects_empty_conditions(db):
    with pytest.raises(ValueError):
        _freeze(conditions=[])


def test_freeze_rejects_missing_direction(db):
    with pytest.raises(ValueError):
        _freeze(expected_direction="sideways")


def test_freeze_same_strategy_different_conditions_is_a_different_hypothesis(db):
    first = _freeze()
    second = _freeze(conditions=[{"feature": "ibs", "op": "<", "value": 0.2, "rawEdge": None, "source": "quantile", "description": "IBS below 0.2"}])
    assert first.hypothesis_key != second.hypothesis_key


def test_amend_creates_new_version_and_supersedes_the_old_one(db):
    first = _freeze()
    second = ledger.amend(first.row_id, minimum_effect=0.10)
    assert second.version == 2
    assert second.hypothesis_key == first.hypothesis_key
    assert second.supersedes == first.row_id

    reloaded_first = ledger.get(first.row_id)
    assert reloaded_first.status == ledger.STATUS_SUPERSEDED
    assert reloaded_first.superseded_by == second.row_id


def test_verify_integrity_intact_immediately_after_freeze(db):
    frozen = _freeze()
    integrity = ledger.verify_integrity(frozen)
    assert integrity["intact"] is True
    assert integrity["featureDriftDetected"] is False


def test_verify_integrity_detects_tampered_contract(db):
    frozen = _freeze()
    tampered = frozen.__class__(**{**frozen.__dict__, "minimum_effect": 999.0})
    integrity = ledger.verify_integrity(tampered)
    assert integrity["intact"] is False


def test_verify_integrity_detects_feature_drift(db, monkeypatch):
    frozen = _freeze()
    original = pit_features.FEATURES["rsi2"]
    drifted = original.__class__(**{**original.__dict__, "description": "a completely different definition"})
    monkeypatch.setitem(pit_features.FEATURES, "rsi2", drifted)
    integrity = ledger.verify_integrity(frozen)
    assert integrity["featureDriftDetected"] is True
    assert any("rsi2" in note for note in integrity["featureDrift"])


def test_to_dict_preserves_feature_id_keys_in_feature_contract(db):
    """Regression test: feature_contract is keyed by FEATURE ID
    ("mkt_above_sma200", ...), and a blind snake_case->camelCase rekey would
    corrupt those keys. See engine/conditional_stats.py:camel_keys and the
    fix in FrozenHypothesis.to_dict."""
    frozen = _freeze()
    payload = frozen.to_dict()
    assert "rsi2" in payload["featureContract"]
    assert "lookbackBars" in payload["featureContract"]["rsi2"]
    assert payload["conditions"][0]["feature"] == "rsi2"


def test_previously_tested_is_none_before_freezing(db):
    assert ledger.previously_tested("Connors Mean Reversion (RSI2)", "realized_r", CONDITIONS) is None


def test_previously_tested_reports_rejection_after_failed_validation(db):
    frozen = _freeze()
    ledger.record_validation(
        frozen, stage="validation", passed=False,
        result={"observations": 40, "conditionalMean": 0.01, "baselineMean": 0.04, "improvement": -0.03},
        conclusion="Did not clear the bar.", new_status=ledger.STATUS_VALIDATION_FAILED,
    )
    record = ledger.previously_tested("Connors Mean Reversion (RSI2)", "realized_r", CONDITIONS)
    assert record["previouslyRejected"] is True
    assert record["status"] == ledger.STATUS_VALIDATION_FAILED


def test_rejected_hypotheses_only_includes_terminal_failures(db):
    frozen = _freeze()
    assert ledger.rejected_hypotheses("Connors Mean Reversion (RSI2)") == []
    ledger.record_validation(
        frozen, stage="validation", passed=False,
        result={"observations": 40}, conclusion="failed",
        new_status=ledger.STATUS_VALIDATION_FAILED,
    )
    rejected = ledger.rejected_hypotheses("Connors Mean Reversion (RSI2)")
    assert len(rejected) == 1
    assert rejected[0]["status"] == ledger.STATUS_VALIDATION_FAILED


def test_record_validation_appends_never_overwrites(db):
    frozen = _freeze()
    ledger.record_validation(frozen, stage="validation", passed=True, result={"observations": 40}, conclusion="passed")
    ledger.record_validation(frozen, stage="validation", passed=True, result={"observations": 40}, conclusion="passed again")
    results = ledger.validation_results(frozen.row_id)
    assert len(results) == 2


def test_mark_holdout_consumed_is_permanent_and_reported(db):
    status_before = ledger.holdout_consumption("Connors Mean Reversion (RSI2)")
    assert status_before["consumed"] is False

    ledger.mark_holdout_consumed("Connors Mean Reversion (RSI2)", "testing")
    status_after = ledger.holdout_consumption("Connors Mean Reversion (RSI2)")
    assert status_after["consumed"] is True
    assert status_after["consumptionCount"] == 1
    assert status_after["entries"][0]["reason"] == "testing"


def test_ledger_lists_hypotheses_with_results_and_integrity(db):
    frozen = _freeze()
    ledger.record_validation(frozen, stage="validation", passed=True, result={"observations": 40}, conclusion="ok")
    rows = ledger.ledger("Connors Mean Reversion (RSI2)")
    assert len(rows) == 1
    assert len(rows[0]["results"]) == 1
    assert rows[0]["integrity"]["intact"] is True


def test_hypothesis_key_is_stable_regardless_of_condition_order(db):
    conditions_a = [
        {"feature": "rsi2", "op": "<", "value": 30.0},
        {"feature": "ibs", "op": "<", "value": 0.2},
    ]
    conditions_b = list(reversed(conditions_a))
    key_a = ledger.hypothesis_key("S", "realized_r", conditions_a)
    key_b = ledger.hypothesis_key("S", "realized_r", conditions_b)
    assert key_a == key_b
