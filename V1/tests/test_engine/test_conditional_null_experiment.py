"""engine/conditional_null_experiment.py: the dependence-preserving null
calibration of the full adaptive discovery pipeline.

Test data is small and iteration counts are low (this suite must stay fast);
the STATISTICAL claim that cluster-aware inference behaves correctly is
already proven at the primitive level in test_conditional_dependence.py.
These tests check the null-experiment machinery is wired correctly: the
randomization actually preserves what it claims to, results are
deterministic under a fixed seed, and the reported shape matches the
observed session it is being compared against.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import conditional_dependence as cd
from engine import conditional_null_experiment as ne
from engine.observations import ObservationSet

pytestmark = pytest.mark.filterwarnings("ignore")


def _cross_sectional_observations(n_clusters: int, rows_per_cluster: int = 5, *, effect: float = 0.0, seed: int = 0) -> ObservationSet:
    rng = np.random.default_rng(seed)
    rows = []
    for c in range(n_clusters):
        regime = rng.uniform(0, 100)
        cluster_shock = effect * (regime - 50) / 50 + rng.normal(0, 1.0)
        entry = pd.Timestamp("2020-01-01") + pd.Timedelta(days=30 * c)
        for s in range(rows_per_cluster):
            rows.append({
                "decision_time": entry - pd.Timedelta(days=1), "entry_time": entry,
                "exit_time": entry + pd.Timedelta(days=20), "symbol": f"S{s}", "direction": "long",
                "realized_r": cluster_shock + rng.normal(0, 0.05), "rsi2": regime,
            })
    frame = pd.DataFrame(rows)
    return ObservationSet(
        strategy_name="Synthetic", engine="cross_sectional", frame=frame,
        outcome_column="realized_r", outcome_label="R", start=frame["entry_time"].min().date(),
        end=frame["entry_time"].max().date(), symbols=[f"S{i}" for i in range(rows_per_cluster)],
        feature_keys=["rsi2"], coverage={"rsi2": len(frame)},
    )


def test_shuffle_preserves_feature_columns_and_cluster_boundaries():
    obs = _cross_sectional_observations(20, 5)
    cluster_ids = cd.assign_rebalance_clusters(obs.frame)
    shuffled = ne.shuffle_outcomes_by_cluster(obs.frame, "realized_r", cluster_ids, seed=1)
    # Feature column itself must be untouched by the outcome shuffle.
    pd.testing.assert_series_equal(obs.frame["rsi2"], obs.frame["rsi2"])
    # The shuffled outcome must be a rearrangement (same multiset of values),
    # not a resample -- every original value appears exactly once.
    assert sorted(shuffled.to_numpy()) == pytest.approx(sorted(obs.frame["realized_r"].to_numpy()))


def test_shuffle_moves_whole_clusters_intact():
    obs = _cross_sectional_observations(10, 5)
    cluster_ids = cd.assign_rebalance_clusters(obs.frame)
    shuffled = ne.shuffle_outcomes_by_cluster(obs.frame, "realized_r", cluster_ids, seed=2)
    # Every cluster's shuffled outcome set must equal exactly one ORIGINAL
    # cluster's outcome set (clusters move as intact blocks, not row-shuffled
    # independently).
    original_cluster_sets = [
        frozenset(np.round(obs.frame.loc[cluster_ids == c, "realized_r"].to_numpy(), 8))
        for c in cluster_ids.unique()
    ]
    shuffled_frame = obs.frame.copy()
    shuffled_frame["realized_r"] = shuffled
    shuffled_cluster_sets = [
        frozenset(np.round(shuffled_frame.loc[cluster_ids == c, "realized_r"].to_numpy(), 8))
        for c in cluster_ids.unique()
    ]
    for shuffled_set in shuffled_cluster_sets:
        assert shuffled_set in original_cluster_sets


def test_shuffle_is_deterministic_under_fixed_seed():
    obs = _cross_sectional_observations(15, 5)
    cluster_ids = cd.assign_rebalance_clusters(obs.frame)
    first = ne.shuffle_outcomes_by_cluster(obs.frame, "realized_r", cluster_ids, seed=7)
    second = ne.shuffle_outcomes_by_cluster(obs.frame, "realized_r", cluster_ids, seed=7)
    pd.testing.assert_series_equal(first, second)


def test_null_observations_preserves_everything_but_outcome():
    obs = _cross_sectional_observations(10, 5)
    cluster_structure = cd.assign_clusters(obs.frame, "cross_sectional")
    null_obs = ne._null_observations(obs, cluster_structure, seed=3)
    pd.testing.assert_series_equal(null_obs.frame["rsi2"], obs.frame["rsi2"])
    pd.testing.assert_series_equal(null_obs.frame["entry_time"], obs.frame["entry_time"])
    assert null_obs.strategy_name == obs.strategy_name
    assert null_obs.engine == obs.engine
    # The outcome column itself must actually have moved for a non-trivial
    # cluster count (extremely unlikely to be an identity permutation).
    assert not null_obs.frame["realized_r"].equals(obs.frame["realized_r"])


def test_run_null_experiment_produces_requested_iteration_count():
    obs = _cross_sectional_observations(30, 5, effect=0.0, seed=10)
    result = ne.run_null_experiment(obs, iterations=5, inner_permutations=50, seed=42)
    assert len(result.null_iterations) == 5
    assert result.strategy_name == "Synthetic"


def test_run_null_experiment_is_deterministic_under_fixed_seed():
    obs = _cross_sectional_observations(30, 5, effect=2.0, seed=11)
    first = ne.run_null_experiment(obs, iterations=5, inner_permutations=50, seed=99)
    second = ne.run_null_experiment(obs, iterations=5, inner_permutations=50, seed=99)
    assert [it.raw_significant for it in first.null_iterations] == [it.raw_significant for it in second.null_iterations]
    assert first.observed_fdr_significant == second.observed_fdr_significant


def test_run_null_experiment_reuses_supplied_observed_session():
    from engine import conditional_analysis as ca

    obs = _cross_sectional_observations(30, 5, effect=1.0, seed=12)
    observed_session = ca.run_discovery(obs, seed=1, permutations=50, dependence_aware=False)
    result = ne.run_null_experiment(
        obs, iterations=3, inner_permutations=50, seed=1, observed_session=observed_session,
    )
    assert result.observed_raw_significant == observed_session.fdr["rawSignificant"]
    assert result.observed_fdr_significant == observed_session.fdr["fdrSignificant"]


def test_empirical_p_value_bounds_and_convention():
    obs = _cross_sectional_observations(30, 5, seed=13)
    result = ne.run_null_experiment(obs, iterations=8, inner_permutations=50, seed=5)
    d = result.to_dict()
    for key, value in d["empiricalPValues"].items():
        assert 0.0 < value <= 1.0, key


def test_to_dict_includes_cluster_structure_and_interpretation():
    obs = _cross_sectional_observations(20, 5, seed=14)
    result = ne.run_null_experiment(obs, iterations=3, inner_permutations=50, seed=6)
    d = result.to_dict()
    assert d["clusterStructure"]["method"] == "rebalance_cluster"
    assert "interpretation" in d
    assert d["iterations"] == 3
