"""POST /api/research/forward/propose -- the Strategies page's 'Propose for
forward experiment' action, replacing a direct promotion into live paper
execution. Must create a real forward_experiments row via the existing
engine/forward_experiments.py:start() and must never touch execution_db, so
it can never change which strategy Alpaca is currently executing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(main.logging_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(main.logging_db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(main.execution_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(main.execution_db, "DB_PATH", tmp_path / "execution.db")


@pytest.fixture
def client():
    return TestClient(main.app)


def _report(*, forward_test_worthy: bool, blockers=None) -> dict:
    return {
        "version": main.VALIDATION_REPORT_VERSION,
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
            "manifest": {"runFingerprint": "fp-propose-test", "config": {"lookback": 189}},
            "validationSpec": {"primaryBenchmark": "SPY", "primaryCriterion": "positive forward contribution"},
        },
    }


def _log_run(*, forward_test_worthy: bool, is_canonical: bool = True, blockers=None) -> int:
    run_id = main.logging_db.log_portfolio_run(
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
        is_canonical=is_canonical,
    )
    main.logging_db.attach_validation(
        "portfolio_runs", run_id, _report(forward_test_worthy=forward_test_worthy, blockers=blockers),
    )
    return run_id


def test_propose_requires_acknowledgement(client):
    run_id = _log_run(forward_test_worthy=True)
    response = client.post("/api/research/forward/propose", json={
        "strategyName": "Dual Momentum", "validationRunId": run_id, "acknowledgeSelection": False,
    })
    assert response.status_code == 400
    assert "acknowledg" in response.json()["detail"].lower()


def test_propose_creates_a_real_forward_experiment_row(client):
    run_id = _log_run(forward_test_worthy=True)
    response = client.post("/api/research/forward/propose", json={
        "strategyName": "Dual Momentum", "validationRunId": run_id, "acknowledgeSelection": True,
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["validationRunId"] == run_id
    assert payload["status"] == "running"

    listed = client.get("/api/research/forward/Dual Momentum").json()
    assert any(row["id"] == payload["id"] for row in listed)


def test_propose_never_touches_execution_config(client):
    """The core safety property: proposing a forward experiment must not
    change /api/live/execution/config's state, unlike the coupled
    set_execution_config(enabled=True, ...) path it replaces."""
    run_id = _log_run(forward_test_worthy=True)
    before = client.get("/api/live/execution/config").json()

    response = client.post("/api/research/forward/propose", json={
        "strategyName": "Dual Momentum", "validationRunId": run_id, "acknowledgeSelection": True,
    })
    assert response.status_code == 200

    after = client.get("/api/live/execution/config").json()
    assert after == before


def test_propose_without_override_rejects_a_failing_run(client):
    run_id = _log_run(forward_test_worthy=False)
    response = client.post("/api/research/forward/propose", json={
        "strategyName": "Dual Momentum", "validationRunId": run_id, "acknowledgeSelection": True,
    })
    assert response.status_code == 409


def test_propose_with_override_promotes_a_failing_run_and_logs_it(client):
    run_id = _log_run(forward_test_worthy=False, blockers=["Statistical power"])
    response = client.post("/api/research/forward/propose", json={
        "strategyName": "Dual Momentum", "validationRunId": run_id,
        "acknowledgeSelection": True, "overridePassedGates": True,
        "overrideReason": "Watching it anyway",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["overrideUsed"] is True
    assert payload["overrideReason"] == "Watching it anyway"


def test_propose_rejects_a_standard_engine_strategy(client):
    response = client.post("/api/research/forward/propose", json={
        "strategyName": "Pullback to 21 EMA", "validationRunId": 1, "acknowledgeSelection": True,
    })
    assert response.status_code == 400


def test_propose_is_idempotent_for_the_same_run(client):
    run_id = _log_run(forward_test_worthy=True)
    first = client.post("/api/research/forward/propose", json={
        "strategyName": "Dual Momentum", "validationRunId": run_id, "acknowledgeSelection": True,
    }).json()
    second = client.post("/api/research/forward/propose", json={
        "strategyName": "Dual Momentum", "validationRunId": run_id, "acknowledgeSelection": True,
    }).json()
    assert first["id"] == second["id"]
