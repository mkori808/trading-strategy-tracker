"""engine/research_status.py: the Research Status + Data Blocker dashboard.

This is aggregation-only -- every test here checks that a status is
correctly DERIVED from ledger/registry state, never that a new verdict was
invented. Uses the same isolated-tmp-DB fixture pattern as
test_conditional_ledger.py so seeding a specific ledger state is
deterministic and does not touch the real logs/runs.db.
"""

from __future__ import annotations

import json

import pytest

from engine import conditional_ledger as ledger
from engine import logging_db
from engine import research_registry as registry
from engine import research_status as rs
from engine import conditional_stats as stats

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(logging_db, "LOGS_DIR", tmp_path)
    return logging_db


CONDITIONS = [{"feature": "rsi2", "op": "<", "value": 30.0, "rawEdge": 27.4, "source": "quantile", "description": "RSI(2) below 30"}]


def _freeze(strategy_name: str = "Test Strategy", **overrides):
    payload = dict(
        strategy_name=strategy_name, engine="standard", outcome_column="realized_r",
        conditions=CONDITIONS, expected_direction="higher", primary_metric="Expectancy (R)",
        minimum_effect=0.05, minimum_observations=30,
        validation_start="2024-01-01", validation_end="2024-06-01",
        holdout_start="2024-06-01", holdout_end="2025-01-01",
    )
    payload.update(overrides)
    return ledger.freeze(**payload)


def _record_session(strategy_name: str, session_id: str, **overrides) -> None:
    payload = {
        "sessionId": session_id, "createdAt": "2026-08-22T00:00:00+00:00",
        "strategyName": strategy_name, "engine": "standard", "seed": 1, "observations": 100,
        "discoveryStart": "2022-01-01", "discoveryEnd": "2023-01-01",
        "fdr": {"hypothesesTested": 50, "rawSignificant": 5, "fdrSignificant": 0, "alpha": 0.1},
        "accounting": {"totalExamined": 50},
        "candidates": [], "univariate": [], "interactions": [],
        "clusterStructure": None, "methodologyVersion": None,
    }
    payload.update(overrides)

    class _FakeSession:
        def to_dict(self) -> dict:
            return payload

    ledger.record_session(_FakeSession())


# -- next_action mapping ----------------------------------------------------

def test_next_action_covers_every_standardized_status():
    for status in rs.StatusLabel.__args__:
        action = rs.next_action(status)
        assert action and "no mapped action" not in action.lower()


def test_next_action_unknown_status_falls_back_safely():
    assert "no mapped action" in rs.next_action("Some Made Up Status").lower()


def test_next_action_never_suggests_optimization_after_failure():
    for status in ("Validation Failed", "Rejected"):
        action = rs.next_action(status).lower()
        assert "tun" not in action and "optimiz" not in action


# -- status normalization: hypothesis-backed rows ---------------------------

def test_validation_failed_hypothesis_maps_to_validation_failed_status(db):
    frozen = _freeze()
    ledger.record_validation(
        frozen, stage="validation", passed=False,
        result={"observations": 40, "conditionalMean": 0.01, "baselineMean": 0.04, "improvement": -0.03},
        conclusion="Did not clear the bar.", new_status=ledger.STATUS_VALIDATION_FAILED,
    )
    row = rs.derive_conditional_status("Test Strategy")
    assert row.status == "Validation Failed"
    assert "remains rejected" in " ".join(row.notes).lower()


def test_rejected_hypothesis_stays_rejected_and_is_never_rediscovered_as_novel(db):
    frozen = _freeze(strategy_name="Rejected Strategy")
    ledger.record_validation(
        frozen, stage="validation", passed=False, result={"observations": 40},
        conclusion="failed", new_status=ledger.STATUS_REJECTED,
    )
    row = rs.derive_conditional_status("Rejected Strategy")
    assert row.status == "Rejected"
    # The ledger's own previously_tested() must independently agree this is rejected.
    prior = ledger.previously_tested("Rejected Strategy", "realized_r", CONDITIONS)
    assert prior is not None and prior["previouslyRejected"] is True


def test_holdout_passed_hypothesis_maps_to_validated(db):
    frozen = _freeze(strategy_name="Winner Strategy")
    ledger.record_validation(
        frozen, stage="validation", passed=True, result={"observations": 40},
        conclusion="passed", new_status=ledger.STATUS_VALIDATION_PASSED,
    )
    reloaded = ledger.get(frozen.row_id)
    ledger.record_validation(
        reloaded, stage="holdout", passed=True, result={"observations": 60},
        conclusion="holdout passed", new_status=ledger.STATUS_HOLDOUT_PASSED,
    )
    row = rs.derive_conditional_status("Winner Strategy")
    assert row.status == "Validated"


def test_no_historical_candidate_is_ever_rendered_as_validated(db):
    # A session with real, in-sample-only candidates and NO frozen hypothesis
    # must never be classified "Validated" -- discovery-stage evidence alone,
    # however strong, is not validation.
    _record_session(
        "In-Sample Only", "sess1",
        candidates=[{"hypothesisId": "h1", "effectiveN": 500, "nConditioned": 500}],
        fdr={"hypothesesTested": 10, "rawSignificant": 10, "fdrSignificant": 10, "alpha": 0.1},
    )
    row = rs.derive_conditional_status("In-Sample Only")
    assert row.status != "Validated"


# -- effective-N blocking classification ------------------------------------

def test_session_with_all_candidates_below_effective_n_is_blocked(db):
    _record_session(
        "Underpowered Strategy", "sess2",
        candidates=[
            {"hypothesisId": "h1", "effectiveN": 20, "nConditioned": 50},
            {"hypothesisId": "h2", "effectiveN": 15, "nConditioned": 40},
        ],
        clusterStructure={"nRaw": 300, "effectiveN": 40, "method": "overlap_block"},
    )
    row = rs.derive_conditional_status("Underpowered Strategy")
    assert row.status == "Blocked - Insufficient Effective N"
    assert row.causal_chain  # the causal chain must be populated for a blocked row
    assert "PIT" in " ".join(row.causal_chain)


@pytest.mark.parametrize("strategy_name", tuple(registry.CONDITIONAL_FINAL_INTERPRETATIONS))
def test_completed_batch2_null_result_takes_priority_over_effective_n_block(db, strategy_name):
    _record_session(
        strategy_name, f"batch2-{strategy_name}",
        candidates=[{"hypothesisId": "h1", "effectiveN": 9, "nConditioned": 40}],
    )

    row = rs.derive_conditional_status(strategy_name)

    assert row.status == "No Conditional Structure Detected"
    assert row.evidence_stage == "Discovery + Null Calibration"
    assert any("null behavior" in note for note in row.notes)
    assert any("effective-N freeze requirements" in note for note in row.notes)
    assert row.primary_blocker is None


@pytest.mark.parametrize("strategy_name", ("Dual Momentum", "Market-Residual Momentum"))
def test_dm_mrm_remain_effective_n_blocked_when_candidates_are_underpowered(db, strategy_name):
    _record_session(
        strategy_name, f"underpowered-{strategy_name}",
        candidates=[{"hypothesisId": "h1", "effectiveN": 9, "nConditioned": 40}],
    )

    row = rs.derive_conditional_status(strategy_name)

    assert row.status == "Blocked - Insufficient Effective N"


def test_session_with_one_candidate_clearing_effective_n_is_not_blocked(db):
    # A candidate whose discovery-side effective N is comfortably above the
    # floor even after the disclosed validation-ratio estimate should NOT be
    # classified as effective-N-blocked.
    high_n = int(stats.HYPOTHESIS_MIN_N / rs._ESTIMATED_VALIDATION_RATIO) + 50
    _record_session(
        "Adequately Powered Strategy", "sess3",
        candidates=[{"hypothesisId": "h1", "effectiveN": high_n, "nConditioned": 500}],
    )
    row = rs.derive_conditional_status("Adequately Powered Strategy")
    assert row.status != "Blocked - Insufficient Effective N"


def test_session_with_zero_candidates_is_discovery_only_not_blocked(db):
    _record_session("No Candidates Strategy", "sess4", candidates=[])
    row = rs.derive_conditional_status("No Candidates Strategy")
    assert row.status == "Discovery Only"


def test_effective_n_blocking_falls_back_to_methodology_audit_when_session_predates_field(db):
    # Simulates the real Dual Momentum/Market-Residual-Momentum situation:
    # the ORIGINAL session predates the effectiveN field entirely (None on
    # every candidate), but a methodology-audit record supplies the
    # dependence-aware corrected effective N.
    _record_session(
        "Pre-V2 Strategy", "sess5",
        candidates=[{"hypothesisId": "audited-h1", "effectiveN": None, "nConditioned": 45}],
        methodologyVersion=None,
    )
    ledger.record_methodology_audit(
        strategy_name="Pre-V2 Strategy", target_type="candidate", target_key="audited-h1",
        session_id="sess5", corrected_methodology_version=stats.METHODOLOGY_V2_DEPENDENCE_AWARE,
        corrected_inference_method="rebalance_cluster", corrected_effective_n=9,
        reason="test audit",
    )
    row = rs.derive_conditional_status("Pre-V2 Strategy")
    assert row.status == "Blocked - Insufficient Effective N"


# -- methodology-version superseded flag ------------------------------------

def test_v1_session_with_matching_audit_is_flagged_superseded(db):
    _record_session("Superseded Strategy", "sess6", candidates=[], methodologyVersion=None)
    ledger.record_methodology_audit(
        strategy_name="Superseded Strategy", target_type="session", target_key="Superseded Strategy",
        session_id="sess6", corrected_methodology_version=stats.METHODOLOGY_V2_DEPENDENCE_AWARE,
        corrected_inference_method="rebalance_cluster", reason="test audit",
    )
    row = rs.derive_conditional_status("Superseded Strategy")
    assert row.methodology_superseded is True
    assert row.methodology_version == stats.METHODOLOGY_V1_ROW_LEVEL
    assert any("supersed" in n.lower() for n in row.notes)


def test_v2_native_session_is_not_flagged_superseded(db):
    _record_session(
        "Native V2 Strategy", "sess7", candidates=[],
        methodologyVersion=stats.METHODOLOGY_V2_DEPENDENCE_AWARE,
    )
    row = rs.derive_conditional_status("Native V2 Strategy")
    assert row.methodology_superseded is False
    assert row.methodology_version == stats.METHODOLOGY_V2_DEPENDENCE_AWARE


# -- no-session / in-progress detection --------------------------------------

def test_strategy_with_no_session_and_not_in_batch_shows_plain_absence(db):
    row = rs.derive_conditional_status("Never Touched Strategy")
    assert row.status == "Discovery Only"
    assert "no conditional edge discovery session" in row.last_completed_action.lower()


def test_strategy_in_current_batch_with_real_preregistration_shows_in_progress(db, tmp_path, monkeypatch):
    prereg_file = tmp_path / "prereg.json"
    prereg_file.write_text(json.dumps({
        "registeredAt": "2026-08-22",
        "methodologyVersion": stats.METHODOLOGY_V2_DEPENDENCE_AWARE,
        "strategies": {"Batch Strategy": {}},
    }))
    monkeypatch.setattr(registry, "CURRENT_BATCH_PREREGISTRATION_PATH", str(prereg_file))
    monkeypatch.setattr(registry, "CURRENT_BATCH_STRATEGIES", ("Batch Strategy",))

    row = rs.derive_conditional_status("Batch Strategy")
    assert row.status == "Research Run In Progress"
    assert row.methodology_version == stats.METHODOLOGY_V2_DEPENDENCE_AWARE


def test_batch_strategy_without_a_real_preregistration_file_is_not_fabricated_in_progress(db, monkeypatch):
    monkeypatch.setattr(registry, "CURRENT_BATCH_PREREGISTRATION_PATH", "research/does_not_exist.json")
    monkeypatch.setattr(registry, "CURRENT_BATCH_STRATEGIES", ("Ghost Strategy",))
    row = rs.derive_conditional_status("Ghost Strategy")
    assert row.status != "Research Run In Progress"


def test_in_progress_becomes_real_status_once_session_is_persisted(db, tmp_path, monkeypatch):
    # The self-updating property: once a session exists, "in progress" must
    # never be shown even if the strategy is still named in an old
    # preregistration file.
    prereg_file = tmp_path / "prereg.json"
    prereg_file.write_text(json.dumps({"strategies": {"Now Complete Strategy": {}}}))
    monkeypatch.setattr(registry, "CURRENT_BATCH_PREREGISTRATION_PATH", str(prereg_file))
    monkeypatch.setattr(registry, "CURRENT_BATCH_STRATEGIES", ("Now Complete Strategy",))
    _record_session("Now Complete Strategy", "sess8", candidates=[])
    row = rs.derive_conditional_status("Now Complete Strategy")
    assert row.status != "Research Run In Progress"
    assert row.status == "Discovery Only"


# -- DM/MRM vol-scaled forward-test row ---------------------------------------

def test_dm_mrm_vol_scaled_row_shows_correct_development_cutoff():
    row = rs.derive_dm_mrm_vol_scaled_status()
    assert row.name == "DM/MRM Volatility-Scaled Portfolio"
    # Either the real frozen module is present (assert its real cutoff) or it
    # degrades gracefully -- never crashes the dashboard either way.
    if row.status != "Data Required":
        assert row.development_period is not None
        assert row.evidence_stage == "Frozen"
        assert row.status in ("Frozen — Forward Testing", "Too Early to Evaluate")


# -- infrastructure row -------------------------------------------------------

def test_infrastructure_row_reports_v2_methodology_version():
    row = rs.derive_conditional_edge_infrastructure_status()
    assert row.status == "Methodology Complete"
    assert row.methodology_version == stats.METHODOLOGY_V2_DEPENDENCE_AWARE


# -- data blockers ------------------------------------------------------------

def test_missing_pit_bundle_is_detected_with_missing_artifacts():
    blockers = {b["dataset"]: b for b in rs.data_blockers()}
    us_all = blockers["U.S. All Stocks PIT bundle"]
    assert us_all["status"] == "Blocked"
    assert us_all["pitSafe"] is False
    assert us_all["survivorshipFree"] is False
    assert len(us_all["missingArtifacts"]) > 0


def test_declared_coverage_is_not_misrepresented_as_installed():
    blockers = {b["dataset"]: b for b in rs.data_blockers()}
    us_all = blockers["U.S. All Stocks PIT bundle"]
    # The declared date range must be shown (it exists in the universe
    # definition) AND the dataset must simultaneously read as blocked --
    # never silently implying installed coverage because a date range exists.
    assert us_all["intendedCoverage"] is not None
    assert us_all["status"] == "Blocked"
    assert "not installed" in us_all["actualAvailability"].lower() or "not runnable" in us_all["actualAvailability"].lower()


def test_dow_pit_shows_partial_reconstruction_caveat():
    blockers = {b["dataset"]: b for b in rs.data_blockers()}
    dow = blockers["Dow PIT"]
    assert "partial" in dow["actualAvailability"].lower()
    assert "not equivalent to a licensed pit" in dow["actualAvailability"].lower()


def test_sp500_pit_blocker_present_and_blocked():
    blockers = {b["dataset"]: b for b in rs.data_blockers()}
    sp500 = blockers["S&P 500 PIT"]
    assert sp500["status"] == "Blocked"
    assert sp500["survivorshipFree"] is False


# -- dashboard assembly / summary consistency --------------------------------

def test_summary_counts_match_actual_rows():
    dashboard = rs.build_status_dashboard()
    rows = dashboard["rows"]
    summary = dashboard["summary"]
    assert summary["rejectedHypotheses"] == sum(
        1 for r in rows if r["status"] in ("Validation Failed", "Rejected")
    )
    assert summary["methodologyCompleteSystems"] == sum(
        1 for r in rows if r["status"] == "Methodology Complete"
    )


def test_dashboard_never_crashes_and_returns_camelcase_keys():
    dashboard = rs.build_status_dashboard()
    assert "statusLabels" in dashboard
    for row in dashboard["rows"]:
        assert "nextAction" in row
        assert "methodologySuperseded" in row
        assert "evidence_stage" not in row  # must be camelCase, not snake_case
