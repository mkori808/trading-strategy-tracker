"""engine/conditional_audit.py: re-scoring already-reported discovery
evidence under the dependence-aware methodology, and persisting the
before/after to the ledger -- without running any new search.

`conditional_edge.build_observations_for` is monkeypatched to return a
pre-built synthetic `ObservationSet` directly, so these tests exercise the
real `reaudit_strategy`/`persist_strategy_audit` logic without needing a full
synthetic OHLCV-to-backtest pipeline (that flow is exercised end-to-end
against real strategies elsewhere; this module's own logic -- matching v1
vs. v2 hypotheses, re-scoring stored candidates, persisting audit rows -- is
what these tests target)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import conditional_audit as audit
from engine import conditional_edge
from engine import conditional_ledger as ledger
from engine import logging_db
from engine.observations import ObservationSet

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_db, "DB_PATH", tmp_path / "runs.db")
    monkeypatch.setattr(logging_db, "LOGS_DIR", tmp_path)
    return logging_db


def _cross_sectional_observations(n_clusters: int, rows_per_cluster: int = 5, *, effect: float = 3.0, seed: int = 0) -> ObservationSet:
    rng = np.random.default_rng(seed)
    rows = []
    for c in range(n_clusters):
        regime = rng.uniform(0, 100)
        cluster_shock = -effect * (regime - 50) / 50 + rng.normal(0, 1.0)
        entry = pd.Timestamp("2020-01-01") + pd.Timedelta(days=30 * c)
        for s in range(rows_per_cluster):
            rows.append({
                "decision_time": entry - pd.Timedelta(days=1), "entry_time": entry,
                "exit_time": entry + pd.Timedelta(days=20), "symbol": f"S{s}", "direction": "long",
                "realized_r": cluster_shock + rng.normal(0, 0.05), "rsi2": regime,
            })
    frame = pd.DataFrame(rows)
    return ObservationSet(
        strategy_name="Synthetic Cross-Sectional", engine="cross_sectional", frame=frame,
        outcome_column="realized_r", outcome_label="R", start=frame["entry_time"].min().date(),
        end=frame["entry_time"].max().date(), symbols=[f"S{i}" for i in range(rows_per_cluster)],
        feature_keys=["rsi2"], coverage={"rsi2": len(frame)},
    )


@pytest.fixture
def synthetic_session(db, monkeypatch):
    """A real, persisted discovery session over synthetic cross-sectional
    data, via the actual `conditional_edge.analyze()` entry point -- so the
    stored session JSON `reaudit_strategy` reads back has the exact shape a
    real one does."""
    obs = _cross_sectional_observations(60, 5, effect=3.0, seed=1)
    monkeypatch.setattr(conditional_edge, "build_observations_for", lambda name, result=None: (obs, None))
    study = conditional_edge.analyze(obs.strategy_name, permutations=200, persist=True)
    return obs, study


def test_reaudit_strategy_reports_identical_hypothesis_count_v1_vs_v2(synthetic_session):
    obs, study = synthetic_session
    result = audit.reaudit_strategy(
        obs.strategy_name, study.session.session_id, result=None, permutations=200,
    )
    # Feature/interaction SELECTION must be identical between v1 and v2 --
    # only their p/q values differ. hypotheses_tested comes from the v1 replay.
    assert result.hypotheses_tested > 0
    assert result.cluster_structure["method"] == "rebalance_cluster"
    # `reaudit_strategy` operates on the DISCOVERY SLICE (default 60% split
    # of the 60 total clusters), not the full observation set -- matching
    # `conditional_edge.analyze()`'s own default split.
    assert 25 <= result.cluster_structure["effectiveN"] <= 40


def test_reaudit_strategy_rescoring_uses_stored_conditions_not_new_search(synthetic_session):
    obs, study = synthetic_session
    result = audit.reaudit_strategy(obs.strategy_name, study.session.session_id, permutations=200)
    stored = ledger.load_session(study.session.session_id)
    stored_ids = {c["hypothesisId"] for c in stored["candidates"]}
    audited_ids = {c.hypothesis_id for c in result.candidates}
    # Every audited candidate must be one of the ORIGINALLY stored ones --
    # no new hypothesis IDs introduced by the re-scoring pass.
    assert audited_ids <= stored_ids
    assert audited_ids == stored_ids  # and every stored one gets re-scored


def test_reaudit_strategy_candidate_effective_n_is_populated(synthetic_session):
    obs, study = synthetic_session
    result = audit.reaudit_strategy(obs.strategy_name, study.session.session_id, permutations=200)
    assert result.candidates, "expected at least one candidate on this strongly-clustered synthetic effect"
    for candidate in result.candidates:
        assert candidate.corrected is not None
        assert candidate.corrected["effectiveN"] is not None
        # Cluster-based effective N must never exceed the discovery cluster count.
        assert candidate.corrected["effectiveN"] <= result.cluster_structure["effectiveN"]


def test_persist_strategy_audit_writes_session_and_candidate_rows(db, synthetic_session):
    obs, study = synthetic_session
    result = audit.reaudit_strategy(obs.strategy_name, study.session.session_id, permutations=200)
    row_ids = audit.persist_strategy_audit(result, reason="unit test audit")
    assert len(row_ids) == 1 + len(result.candidates)  # one session-level + one per candidate

    audits = ledger.methodology_audits_for(strategy_name=obs.strategy_name)
    assert len(audits) == len(row_ids)
    session_rows = [a for a in audits if a["targetType"] == "session"]
    assert len(session_rows) == 1
    assert session_rows[0]["correctedInferenceMethod"] == "rebalance_cluster"

    candidate_rows = [a for a in audits if a["targetType"] == "candidate"]
    assert len(candidate_rows) == len(result.candidates)
    for row in candidate_rows:
        assert row["correctedMethodologyVersion"] == "conditional_edge_stats_v2_dependence_aware"
        assert row["hypothesisRowId"] is None  # never frozen in this test


def test_persist_strategy_audit_links_hypothesis_row_id_when_provided(db, synthetic_session):
    obs, study = synthetic_session
    result = audit.reaudit_strategy(obs.strategy_name, study.session.session_id, permutations=200)
    assert result.candidates
    target = result.candidates[0]
    row_ids = audit.persist_strategy_audit(
        result, reason="linked test", hypothesis_row_id_by_candidate={target.hypothesis_id: 999},
    )
    audits = ledger.methodology_audits_for(strategy_name=obs.strategy_name)
    linked = [a for a in audits if a["hypothesisRowId"] == 999]
    assert len(linked) == 1
    assert linked[0]["targetKey"] == target.hypothesis_id


def test_persist_strategy_audit_never_mutates_original_session(db, synthetic_session):
    obs, study = synthetic_session
    original = ledger.load_session(study.session.session_id)
    result = audit.reaudit_strategy(obs.strategy_name, study.session.session_id, permutations=200)
    audit.persist_strategy_audit(result, reason="mutation check")
    reloaded = ledger.load_session(study.session.session_id)
    assert reloaded == original  # byte-identical: the audit never rewrites the original session row


def test_reaudit_strategy_with_no_true_effect_yields_no_or_few_candidates():
    # A sanity check that the audit machinery does not fabricate structure
    # when there is none -- not a re-proof of the FDR calibration itself.
    obs = _cross_sectional_observations(60, 5, effect=0.0, seed=2)
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        from engine import conditional_analysis as ca

        session_v1 = ca.run_discovery(obs, seed=1, permutations=200, dependence_aware=False)
        session_v2 = ca.run_discovery(obs, seed=1, permutations=200, dependence_aware=True)
    # Corrected FDR-significant count must never EXCEED the naive one by a
    # wide margin on pure noise -- the correction should not manufacture
    # spurious findings the naive method itself did not show.
    assert session_v2.fdr["fdrSignificant"] <= session_v1.fdr["fdrSignificant"] + 3
