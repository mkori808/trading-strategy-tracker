"""engine/forward_experiments.py: promoting a strategy to a (paper-only)
forward test, including the explicit, logged override for a strategy that
fails validation gates.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from engine import forward_experiments, logging_db
from engine.metrics import compute_metrics


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(logging_db, "LOGS_DIR", tmp_path)
    return logging_db


def _portfolio_kwargs(**overrides):
    kwargs = dict(
        strategy_name="Dual Momentum",
        symbols=["AAPL", "MSFT"],
        start=None,
        end=None,
        final_equity=11000.0,
        return_pct=10.0,
        cagr_pct=2.0,
        max_drawdown_pct=5.0,
        sharpe=0.5,
        sortino=0.6,
        risk_free_rate=0.03,
    )
    kwargs.update(overrides)
    return kwargs


def _report(*, forward_test_worthy: bool, blockers=None) -> dict:
    return {
        "version": 5,
        "generatedAt": "2026-08-14T00:00:00+00:00",
        "dimensions": [],
        "verdict": {
            "headline": "Identified edge" if forward_test_worthy else "Edge not established",
            "forwardTestWorthy": forward_test_worthy,
            "lifecycleStage": "paper_eligible" if forward_test_worthy else "edge_not_established",
            "blockers": [] if forward_test_worthy else (blockers or ["Statistical power"]),
        },
        "research": {
            "primaryBenchmark": "SPY",
            "primaryCriterion": "positive forward contribution",
            "manifest": {"runFingerprint": "fp-test-123", "config": {"lookback": 189}},
            "validationSpec": {"primaryBenchmark": "SPY", "primaryCriterion": "positive forward contribution"},
        },
    }


def test_start_without_override_rejects_a_failing_report(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs())
    db.attach_validation("portfolio_runs", run_id, _report(forward_test_worthy=False))

    with pytest.raises(ValueError, match="not forward-test worthy"):
        forward_experiments.start("Dual Momentum", run_id)


def test_override_promotes_a_failing_report_and_records_the_override(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs())
    db.attach_validation(
        "portfolio_runs", run_id,
        _report(forward_test_worthy=False, blockers=["Statistical power", "PIT membership"]),
    )

    experiment = forward_experiments.start(
        "Dual Momentum", run_id, override=True, override_reason="Watching it anyway",
    )

    assert experiment["status"] == "running"
    assert experiment["overrideUsed"] is True
    assert experiment["overrideReason"] == "Watching it anyway"
    assert experiment["overrideBlockers"] == ["Statistical power", "PIT membership"]
    assert experiment["overrideAt"] is not None

    # The frozen blockers must survive a fresh read from the DB, not just
    # the in-memory return value of start().
    reloaded = forward_experiments.for_strategy("Dual Momentum")[0]
    assert reloaded["overrideUsed"] is True
    assert reloaded["overrideBlockers"] == ["Statistical power", "PIT membership"]


def test_override_on_an_already_passing_report_is_not_recorded_as_an_override(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs())
    db.attach_validation("portfolio_runs", run_id, _report(forward_test_worthy=True))

    experiment = forward_experiments.start("Dual Momentum", run_id, override=True)

    assert experiment["overrideUsed"] is False
    assert experiment["overrideBlockers"] == []


def test_override_still_requires_a_real_run_fingerprint(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs())
    report = _report(forward_test_worthy=False)
    report["research"]["manifest"]["runFingerprint"] = None
    db.attach_validation("portfolio_runs", run_id, report)

    with pytest.raises(ValueError, match="fingerprint"):
        forward_experiments.start("Dual Momentum", run_id, override=True)


def test_exploratory_run_can_be_promoted_by_exact_run_id(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs(
        params={"top_n": 3}, is_canonical=False, symbols=["AAPL", "MSFT", "NVDA"],
    ))
    db.attach_validation("portfolio_runs", run_id, _report(forward_test_worthy=True))

    experiment = forward_experiments.start("Dual Momentum", run_id)

    assert experiment["validationRunId"] == run_id
    assert experiment["status"] == "running"


def test_logged_override_authorizes_later_paper_execution_checks(db):
    run_id = db.log_portfolio_run(**_portfolio_kwargs(is_canonical=False))
    db.attach_validation(
        "portfolio_runs", run_id,
        _report(forward_test_worthy=False, blockers=["Historical stability"]),
    )
    before = db.paper_execution_eligibility("Dual Momentum", run_id)
    assert before[0] is False

    forward_experiments.start(
        "Dual Momentum", run_id, override=True, override_reason="Paper observation only",
    )
    after = db.paper_execution_eligibility("Dual Momentum", run_id)

    assert after == (True, "Logged paper-execution override is active", run_id)


def test_repeat_start_after_a_successful_override_reuses_the_locked_experiment(db):
    """Reproduces a real reported bug: api/main.py:set_execution_config
    computes override=`overridePassedGates and not eligible`. Once an
    override has already been recorded once, paper_execution_eligibility()
    reports eligible=True forever after -- so that expression evaluates to
    False on every later call, no matter what the caller passes in. A user
    toggling the strategy off and back on (or any other repeat call for the
    same run) must NOT re-hit the forwardTestWorthy gate and raise again;
    it must reuse the already-locked experiment, exactly like a fresh
    override=False call from a caller that has already computed
    eligible=True and therefore never intends to override anything new."""
    run_id = db.log_portfolio_run(**_portfolio_kwargs(is_canonical=False))
    db.attach_validation(
        "portfolio_runs", run_id,
        _report(forward_test_worthy=False, blockers=["Historical stability"]),
    )

    first = forward_experiments.start(
        "Dual Momentum", run_id, override=True, override_reason="Paper observation only",
    )
    eligible_after = db.paper_execution_eligibility("Dual Momentum", run_id)
    assert eligible_after[0] is True

    # Mirrors api/main.py's `override=body.overridePassedGates and not eligible`
    # on a repeat toggle-off/toggle-on: eligible is now True, so override is
    # False here even though the caller still wants the strategy running.
    second = forward_experiments.start("Dual Momentum", run_id, override=False)

    assert second["id"] == first["id"]
    assert second["overrideUsed"] is True
    assert second["overrideReason"] == "Paper observation only"
