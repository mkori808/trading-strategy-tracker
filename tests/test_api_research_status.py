"""API response stability for the Research Status + Data Blocker dashboard.

Uses the real app against an isolated tmp DB (same pattern as
test_api_validation_jobs.py) -- no backtest is triggered by either endpoint,
matching the dashboard brief's "this should load quickly" requirement.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api import main


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setattr(main.logging_db, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(main.logging_db, "DB_PATH", tmp_path / "runs.db")


@pytest.fixture
def client():
    return TestClient(main.app)


def test_research_status_endpoint_returns_expected_shape(client):
    response = client.get("/api/research/status")
    assert response.status_code == 200
    payload = response.json()
    assert "rows" in payload and "summary" in payload and "statusLabels" in payload
    for row in payload["rows"]:
        assert set(row) >= {
            "name", "researchType", "status", "evidenceStage", "lastCompletedAction",
            "nextAction", "methodologyVersion", "methodologySuperseded", "notes", "causalChain",
        }


def test_research_status_summary_counts_are_non_negative_integers(client):
    payload = client.get("/api/research/status").json()
    for value in payload["summary"].values():
        assert isinstance(value, int) and value >= 0


def test_research_data_blockers_endpoint_returns_expected_shape(client):
    response = client.get("/api/research/data-blockers")
    assert response.status_code == 200
    payload = response.json()
    assert "blockers" in payload
    names = {b["dataset"] for b in payload["blockers"]}
    assert {"U.S. All Stocks PIT bundle", "S&P 500 PIT", "Dow PIT"} <= names
    for blocker in payload["blockers"]:
        assert set(blocker) >= {
            "dataset", "status", "actualAvailability", "pitSafe", "survivorshipFree",
            "missingArtifacts", "unlocks", "blocksResearch", "severity",
        }


def test_research_endpoints_do_not_trigger_a_backtest(client, monkeypatch):
    """Neither endpoint may call the backtest runner -- the dashboard must
    stay fast regardless of how many strategies exist."""
    def _fail(*args, **kwargs):
        raise AssertionError("Research status endpoints must never run a backtest.")

    monkeypatch.setattr(main, "run_backtest", _fail)
    r1 = client.get("/api/research/status")
    r2 = client.get("/api/research/data-blockers")
    r3 = client.get("/api/research/forward-stack")
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 200


def test_capital_efficiency_endpoint_is_artifact_backed(client):
    payload = client.get("/api/research/capital-efficiency").json()
    assert payload["available"] is True
    assert payload["classification"]["result"] == "depends_on_available_capital"
    assert set(payload["operatingPoints"]) == {"020_trailing", "025_static"}
    assert all(row["passed"] for row in payload["reproductionChecks"])


def test_dm_regime_forward_endpoint_preserves_prospective_boundary(client, monkeypatch):
    monkeypatch.setattr(main.dm_regime_forward, "status", lambda: {
        "program": "Optimized DM Prospective Regime Labels v1",
        "evidenceClass": "prospective_only", "cutoff": "2026-08-26",
        "observations": 0, "lastSession": None, "fingerprintLocked": True,
        "ordersAllowed": False,
    })
    payload = client.get("/api/research/dm-regime-forward").json()
    assert payload["evidenceClass"] == "prospective_only"
    assert payload["cutoff"] == "2026-08-26"
    assert payload["ordersAllowed"] is False


def test_forward_stack_endpoint_has_five_separate_normalized_series(client, monkeypatch):
    monkeypatch.setattr(main.dm_mrm_forward, "forward_stack_status", lambda: {
        "series": [{"key": key, "series": key, "type": "shadow", "status": "Too Early to Evaluate",
                    "sessions": 0, "nav": None, "returnPct": None, "drawdownPct": None, "lastUpdate": None}
                   for key in ("dm", "mrm", "fiftyFifty", "volScaled", "spy")],
        "currentBlend": None, "maturity": {"label": "Too Early to Evaluate", "reached": [], "next": {"sessions": 20}},
        "alerts": [], "separation": {"alpacaEquityLabel": "Alpaca paper account equity",
                                        "researchNavLabel": "Normalized strategy forward NAV"},
    })
    payload = client.get("/api/research/forward-stack").json()
    assert [row["key"] for row in payload["series"]] == ["dm", "mrm", "fiftyFifty", "volScaled", "spy"]
    assert payload["separation"]["alpacaEquityLabel"] != payload["separation"]["researchNavLabel"]


def test_research_registry_keeps_winner_grace_closed(client):
    rows = {row["name"]: row for row in client.get("/api/research/status").json()["rows"]}
    assert rows["Dual Momentum - One-Rebalance Winner Grace v1"]["status"] == "Modification Unsupported / Closed"
    winner_notes = rows["Dual Momentum - One-Rebalance Winner Grace v1"]["notes"]
    assert any(note.startswith("Preregistration:") for note in winner_notes)
    assert any(note.startswith("Result ledger:") for note in winner_notes)
    assert any("Final result: Modification unsupported" in note for note in winner_notes)
    assert rows["Canonical Dual Momentum · 189D/Monthly"]["status"] == "Shadow Forward Testing"
    assert rows["Market-Residual Momentum"]["status"] == "Shadow Forward Testing"
    assert rows["Fixed 50/50 DM/MRM"]["status"] == "Shadow Forward Testing"


def test_research_status_keeps_optimized_paper_identity_separate_from_canonical(client):
    rows = client.get("/api/research/status").json()["rows"]
    paper = next(row for row in rows if row["researchType"] == "Operational paper strategy")
    canonical = next(row for row in rows if row["name"] == "Canonical Dual Momentum · 189D/Monthly")
    assert "Optimized" in paper["name"]
    assert paper["name"] != canonical["name"]
    assert canonical["researchType"] == "Research shadow strategy"


def test_research_status_responds_quickly(client):
    import time

    start = time.time()
    response = client.get("/api/research/status")
    elapsed = time.time() - start
    assert response.status_code == 200
    assert elapsed < 5.0, f"Research status took {elapsed:.1f}s -- expected a fast, backtest-free read."


def test_frozen_dm_mrm_is_a_shadow_live_test_promotion_candidate(client):
    response = client.get("/api/live/execution/strategies")
    assert response.status_code == 200
    candidates = {row["strategyName"]: row for row in response.json()}

    frozen = candidates["DM/MRM Volatility-Scaled Portfolio"]
    assert frozen["testMode"] == "research_shadow"
    assert frozen["canPlaceOrders"] is False
    assert candidates["Dual Momentum"]["canPlaceOrders"] is True
    assert "Dual Momentum — One-Rebalance Winner Grace v1" not in candidates


def test_shadow_dm_mrm_cannot_be_enabled_for_alpaca_orders(client):
    response = client.post(
        "/api/live/execution/config",
        json={
            "strategyName": "DM/MRM Volatility-Scaled Portfolio",
            "enabled": True,
            "inceptionPolicy": "adopt",
        },
    )
    assert response.status_code == 400
    assert "not an automatable" in response.json()["detail"]


def test_mrm_shadow_cannot_be_enabled_for_alpaca_orders(client):
    candidates = {row["strategyName"]: row for row in client.get("/api/live/execution/strategies").json()}
    assert candidates["Market-Residual Momentum"]["testMode"] == "research_shadow"
    assert candidates["Market-Residual Momentum"]["canPlaceOrders"] is False
    response = client.post("/api/live/execution/config", json={
        "strategyName": "Market-Residual Momentum", "enabled": True, "inceptionPolicy": "adopt"})
    assert response.status_code == 400


def test_second_order_capable_strategy_is_locked_before_validation_override(client, monkeypatch):
    monkeypatch.setattr(main.alpaca_trading, "get_account", lambda: {
        "available": True, "accountNumber": "PAPER-1",
    })
    monkeypatch.setattr(
        main.execution_ownership, "assert_strategy_slot",
        lambda account_id, strategy_name: (_ for _ in ()).throw(
            main.execution_ownership.OwnershipConflict(
                "This brokerage account is currently assigned to Dual Momentum · Optimized 63D/Daily. "
                "Enable the new strategy as a shadow forward test or assign it to a separate brokerage account."
            )
        ),
    )
    response = client.post("/api/live/execution/config", json={
        "strategyName": "52-Week-High Momentum", "enabled": True,
        "inceptionPolicy": "adopt", "overridePassedGates": True, "overrideReason": "try",
    })
    assert response.status_code == 423
    assert "shadow forward test" in response.json()["detail"]


def test_conditional_status_endpoint_distinguishes_never_evaluated(client):
    response = client.get("/api/research/conditional-status/Opening%20Range%20Breakout%20%28ORB%29")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Opening Range Breakout (ORB) Conditional Edge"
    assert payload["evidenceStage"] is None
    assert payload["lastCompletedAction"].startswith("No Conditional Edge Discovery session")


def test_conditional_status_endpoint_unknown_strategy_is_404(client):
    assert client.get("/api/research/conditional-status/Definitely%20Unknown").status_code == 404


def test_prop_shadow_endpoint_keeps_prospective_evidence_separate(client, monkeypatch):
    from api import main
    monkeypatch.setattr(main.prop_forward, "status", lambda: {
        "program": "Optimized DM Prop Shadows", "evidenceClass": "prospective_prop_shadow",
        "historicalEvidenceKeptSeparate": True, "samplingIntervalSeconds": 300,
        "lastSnapshot": None, "samplingAgeSeconds": None, "completedSessions": 0,
        "maturity": "Not started", "warnings": [], "shadows": [],
        "controlsPreserved": ["Canonical DM", "Canonical MRM", "Fixed 50/50 DM/MRM", "Vol-Scaled DM/MRM"],
        "fingerprintLocked": True,
    })
    response = client.get("/api/research/prop-shadows")
    assert response.status_code == 200
    assert response.json()["evidenceClass"] == "prospective_prop_shadow"
    assert response.json()["historicalEvidenceKeptSeparate"] is True
    assert "Canonical MRM" in response.json()["controlsPreserved"]


def test_hourly_shadow_endpoint_preserves_backfill_boundary(client, monkeypatch):
    monkeypatch.setattr(main.optimized_dm_hourly_shadow, "status", lambda: {
        "available": True, "key": "dm_optimized_63d_hourly",
        "strategy": "Dual Momentum · Optimized 63D/Hourly",
        "backfillMarks": 62, "prospectiveMarks": 0,
        "backfillIntent": "Specified before inspecting backfilled results",
    })

    response = client.get("/api/research/optimized-dm-hourly-shadow")

    assert response.status_code == 200
    assert response.json()["backfillMarks"] == 62
    assert response.json()["prospectiveMarks"] == 0


def test_execution_account_endpoint_separates_actual_shadow_and_prop_evidence(client, monkeypatch):
    monkeypatch.setattr(main.alpaca_trading, "account_snapshot", lambda: {
        "account": {"available": True, "accountNumber": "PAPER-1"},
        "positions": [{"symbol": "AAA", "qty": 1}], "orders": [],
        "clock": {"timestamp": "2026-08-24T10:00:00-04:00"},
    })
    owner = {"strategyId": "dm_optimized_63d_daily", "strategyName": "Dual Momentum",
             "displayName": "Dual Momentum · Optimized 63D/Daily", "strategyFingerprint": "locked",
             "ownershipStartedAt": "2026-08-17", "active": True, "fingerprintMatches": True}
    monkeypatch.setattr(main.execution_ownership, "monitor_account", lambda *a, **k: {
        "accountKey": "alpaca:paper:PAPER-1", "brokerage": "alpaca", "brokerageLabel": "Alpaca Paper",
        "accountId": "PAPER-1", "environment": "paper", "active": True,
        "multiStrategyExecution": False, "executionIsolation": "Single-strategy locked",
        "owner": owner, "actionRequired": False, "integrityIssues": [], "principle": "one account, one owner",
    })
    monkeypatch.setattr(main.dm_mrm_forward, "forward_stack_status", lambda: {"series": [
        {"key": key, "series": name, "sessions": 3}
        for key, name in (("dm", "Canonical DM"), ("mrm", "Canonical MRM"),
                          ("fiftyFifty", "Fixed 50/50 DM/MRM"), ("volScaled", "Vol-Scaled DM/MRM"),
                          ("spy", "SPY"))]})
    monkeypatch.setattr(main.prop_forward, "status", lambda: {
        "completedSessions": 2, "parentStrategyId": "dm_optimized_63d_daily",
        "parentStrategyFingerprint": "locked", "shadows": [
            {"key": "p20", "scale": .20, "sessions": 2}, {"key": "p25", "scale": .25, "sessions": 2}],
        "selfFundedShadows": [
            {"key": "s20", "label": "Self-funded 0.20x", "observations": 2},
            {"key": "s25", "label": "Self-funded 0.25x", "observations": 2}],
    })
    monkeypatch.setattr(main.optimized_dm_hourly_shadow, "status", lambda: {
        "available": False, "key": "dm_optimized_63d_hourly",
    })
    payload = client.get("/api/live/execution/account-ownership").json()
    assert payload["executionIsolation"] == "Single-strategy locked"
    assert payload["counts"] == {"BROKERAGE EXECUTION": 1, "SHADOW": 6, "PROP SHADOW": 2}
    qualities = {(row["mode"], row["evidenceQuality"]) for row in payload["forwardStrategies"]}
    assert ("BROKERAGE EXECUTION", "Execution-observed") in qualities
    assert ("SHADOW", "Prospective synthetic") in qualities
    assert ("SHADOW", "Self-funded synthetic on observed parent stream") in qualities
    assert ("PROP SHADOW", "Prop synthetic on observed parent stream") in qualities
    assert next(row for row in payload["forwardStrategies"] if row["key"] == "p20")["parentFingerprint"] == "locked"
    assert payload["positionSeparation"] == {"brokerage": "Brokerage positions", "shadow": "Shadow holdings"}
