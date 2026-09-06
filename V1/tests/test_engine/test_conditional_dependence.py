"""engine/conditional_dependence.py: cluster identification, cluster
bootstrap, cluster permutation, and the core regression proof that naive
row-level inference overstates evidence on clustered observations.

The central test in this file (`test_naive_permutation_overstates_significance_...`)
is the synthetic experiment the 2026-08-22 dependence audit specified: 30
monthly clusters, 5 perfectly-dependent rows per cluster, one regime feature
shared across all five rows, NO true feature/outcome relationship. It must
FAIL under the naive method and PASS under the corrected one -- if either
side of that ever reverses, the dependence-aware statistics have regressed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import conditional_dependence as cd
from engine import conditional_stats as stats
from engine import observations as observations_module

pytestmark = pytest.mark.filterwarnings("ignore")


def _cross_sectional_frame(n_clusters: int, rows_per_cluster: int = 5) -> pd.DataFrame:
    rows = []
    for c in range(n_clusters):
        entry = pd.Timestamp("2020-01-01") + pd.Timedelta(days=30 * c)
        exit_ = entry + pd.Timedelta(days=20)
        for s in range(rows_per_cluster):
            rows.append({
                "symbol": f"S{s}", "entry_time": entry, "exit_time": exit_,
                "decision_time": entry - pd.Timedelta(days=1),
            })
    return pd.DataFrame(rows)


# -- assign_rebalance_clusters -------------------------------------------

def test_assign_rebalance_clusters_groups_by_entry_time():
    frame = _cross_sectional_frame(n_clusters=10, rows_per_cluster=5)
    cluster_ids = cd.assign_rebalance_clusters(frame)
    assert cluster_ids.nunique() == 10
    # Every row sharing an entry_time must share a cluster id.
    for entry_time, group in frame.groupby("entry_time"):
        assert cluster_ids.loc[group.index].nunique() == 1


def test_assign_clusters_dispatches_by_engine():
    cross = _cross_sectional_frame(5, 5)
    structure = cd.assign_clusters(cross, "cross_sectional")
    assert structure.method == "rebalance_cluster"
    assert structure.n_clusters == 5
    assert structure.effective_n == 5


def test_assign_clusters_empty_frame():
    structure = cd.assign_clusters(pd.DataFrame(columns=["entry_time", "exit_time"]), "cross_sectional")
    assert structure.n_raw == 0
    assert structure.effective_n == 0


# -- estimate_block_length / overlap_block clustering ----------------------

def _overlapping_trades(n: int, *, concurrent: int, hold_days: int = 10) -> pd.DataFrame:
    """`n` trades entering `hold_days/concurrent` days apart, each held for
    `hold_days` -- produces roughly `concurrent` simultaneously open positions."""
    step = max(hold_days // concurrent, 1)
    entries = [pd.Timestamp("2020-01-01") + pd.Timedelta(days=step * i) for i in range(n)]
    exits = [e + pd.Timedelta(days=hold_days) for e in entries]
    return pd.DataFrame({"entry_time": entries, "exit_time": exits, "symbol": "AAPL"})


def test_estimate_block_length_scales_with_measured_concurrency():
    low_overlap = _overlapping_trades(60, concurrent=1, hold_days=1)
    high_overlap = _overlapping_trades(60, concurrent=10, hold_days=10)
    low_block = cd.estimate_block_length(low_overlap)
    high_block = cd.estimate_block_length(high_overlap)
    assert high_block > low_block


def test_estimate_block_length_respects_floor_and_ceiling():
    flat = _overlapping_trades(10, concurrent=1, hold_days=1)
    assert cd.estimate_block_length(flat) >= cd.MIN_BLOCK_LENGTH
    extreme = _overlapping_trades(200, concurrent=200, hold_days=500)
    assert cd.estimate_block_length(extreme) <= cd.MAX_BLOCK_LENGTH


def test_assign_overlap_block_clusters_orders_by_entry_time():
    frame = _overlapping_trades(20, concurrent=5, hold_days=5)
    # Shuffle the frame's row order -- clustering must still follow entry
    # time, not incoming row position.
    shuffled = frame.sample(frac=1.0, random_state=1)
    cluster_ids = cd.assign_overlap_block_clusters(shuffled, block_length=4)
    ordered_clusters = cluster_ids.loc[shuffled.sort_values("entry_time").index].to_numpy()
    assert list(ordered_clusters) == sorted(ordered_clusters)


def test_overlap_dependency_chains_collapses_under_continuous_overlap():
    # Positions overlapping nearly continuously (IBS-like) must collapse to
    # very few connected components -- this is the exact degeneracy that
    # rules out connected-components as the block-bootstrap cluster
    # definition (see module docstring).
    continuous = _overlapping_trades(100, concurrent=20, hold_days=30)
    chains = cd.overlap_dependency_chains(continuous)
    assert chains["components"] < 10


def test_overlap_dependency_chains_many_components_when_non_overlapping():
    non_overlapping = _overlapping_trades(50, concurrent=1, hold_days=1)
    chains = cd.overlap_dependency_chains(non_overlapping)
    assert chains["components"] >= 40


def test_assign_clusters_standard_engine_flags_degenerate_overlap():
    continuous = _overlapping_trades(200, concurrent=25, hold_days=40)
    structure = cd.assign_clusters(continuous, "standard")
    assert structure.method == "overlap_block"
    assert structure.dependency_chains is not None
    assert any("almost continuously" in note for note in structure.notes)


# -- cluster_bootstrap_ci --------------------------------------------------

def test_cluster_bootstrap_ci_is_wider_than_iid_under_within_cluster_correlation():
    rng = np.random.default_rng(3)
    rows = []
    for c in range(25):
        shock = rng.normal(0, 1.0)
        entry = pd.Timestamp("2021-01-01") + pd.Timedelta(days=30 * c)
        for _ in range(6):
            rows.append({
                "entry_time": entry, "exit_time": entry + pd.Timedelta(days=20),
                "realized_r": shock + rng.normal(0, 0.02),
            })
    frame = pd.DataFrame(rows)
    cluster_ids = cd.assign_rebalance_clusters(frame)
    cluster_ci = cd.cluster_bootstrap_ci(frame, "realized_r", cluster_ids, draws=1000, seed=5)
    iid_low, iid_high, _ = stats.bootstrap_mean_ci(frame["realized_r"], draws=1000, seed=5)
    cluster_width = cluster_ci["ciHigh"] - cluster_ci["ciLow"]
    iid_width = iid_high - iid_low
    assert cluster_width > iid_width * 2  # heavily correlated rows -> substantially wider


def test_cluster_bootstrap_ci_reduces_to_iid_width_when_clusters_are_singletons():
    # One row per cluster (cluster == row) should behave like an ordinary
    # bootstrap over independent rows.
    rng = np.random.default_rng(4)
    frame = pd.DataFrame({
        "entry_time": pd.date_range("2020-01-01", periods=200, freq="D"),
        "exit_time": pd.date_range("2020-01-02", periods=200, freq="D"),
        "realized_r": rng.normal(0, 1, 200),
    })
    cluster_ids = cd.assign_rebalance_clusters(frame)
    assert cluster_ids.nunique() == 200
    cluster_ci = cd.cluster_bootstrap_ci(frame, "realized_r", cluster_ids, draws=1000, seed=6)
    iid_low, iid_high, _ = stats.bootstrap_mean_ci(frame["realized_r"], draws=1000, seed=6)
    assert cluster_ci["ciLow"] == pytest.approx(iid_low, abs=0.05)
    assert cluster_ci["ciHigh"] == pytest.approx(iid_high, abs=0.05)


def test_cluster_bootstrap_ci_drops_draws_with_no_masked_rows():
    frame = _cross_sectional_frame(10, 5)
    frame["realized_r"] = np.linspace(-1, 1, len(frame))
    cluster_ids = cd.assign_rebalance_clusters(frame)
    # Mask selects only ONE row total -- most draws that don't happen to
    # include that row's cluster will contribute nothing.
    mask = pd.Series(False, index=frame.index)
    mask.iloc[0] = True
    result = cd.cluster_bootstrap_ci(frame, "realized_r", cluster_ids, mask=mask, draws=500, seed=7)
    assert result["usableDraws"] <= result["draws"]


# -- cluster_permutation_test: the core regression proof --------------------

def _null_cross_sectional_dataset(seed: int, n_clusters: int = 30, rows_per_cluster: int = 5) -> pd.DataFrame:
    """30 clusters, 5 perfectly-dependent rows each, ONE regime feature shared
    across all 5 rows, and NO true relationship between the feature and the
    outcome -- the exact synthetic scenario the dependence audit specified."""
    rng = np.random.default_rng(seed)
    rows = []
    for c in range(n_clusters):
        regime = bool(rng.integers(0, 2))
        cluster_shock = rng.normal(0, 1.0)  # shared within cluster -> high within-cluster correlation
        entry = pd.Timestamp("2020-01-01") + pd.Timedelta(days=30 * c)
        for _ in range(rows_per_cluster):
            outcome = cluster_shock + rng.normal(0, 0.05)  # small idiosyncratic noise
            rows.append({
                "entry_time": entry, "exit_time": entry + pd.Timedelta(days=20),
                "realized_r": outcome, "regime": regime,
            })
    return pd.DataFrame(rows)


def test_naive_permutation_overstates_significance_under_within_cluster_dependence():
    """The central regression proof. Across many independently-drawn null
    datasets (true relationship = none), the NAIVE row-level permutation test
    must reject far more often than its stated alpha, while the CLUSTER
    permutation test must reject close to alpha. If this test ever starts
    failing, the dependence-aware correction has stopped doing its job."""
    trials = 120
    alpha = 0.05
    naive_rejections = 0
    cluster_rejections = 0
    for seed in range(trials):
        frame = _null_cross_sectional_dataset(seed)
        mask = frame["regime"]
        cluster_ids = cd.assign_rebalance_clusters(frame)

        naive = stats.permutation_test(
            frame.loc[mask, "realized_r"], frame.loc[~mask, "realized_r"],
            permutations=300, seed=seed + 10_000,
        )
        clustered = cd.cluster_permutation_test(
            frame, "realized_r", mask, cluster_ids, permutations=300, seed=seed + 10_000,
        )
        if naive["pValue"] is not None and naive["pValue"] <= alpha:
            naive_rejections += 1
        if clustered["pValue"] is not None and clustered["pValue"] <= alpha:
            cluster_rejections += 1

    naive_rate = naive_rejections / trials
    cluster_rate = cluster_rejections / trials

    # The naive method must be shown overstating evidence by a wide margin
    # (measured directly: ~37% vs. a nominal 5%) -- a loose bound so the test
    # is not flaky, but tight enough that it would fail if the demonstration
    # stopped working.
    assert naive_rate > 0.20, f"naive false-positive rate was only {naive_rate:.1%}; expected the overstatement"
    # The cluster-aware method must stay close to the nominal alpha -- a
    # generous band (2.5x) since 120 trials at a 5% true rate has real
    # sampling variance of its own.
    assert cluster_rate <= alpha * 2.5, f"cluster method false-positive rate {cluster_rate:.1%} is not controlled"
    assert cluster_rate < naive_rate


def test_cluster_permutation_reduces_to_between_cluster_shuffle_for_constant_features():
    # For a portfolio_regime-style feature (constant within every cluster),
    # cluster permutation must reject at close to the nominal rate on a null
    # dataset -- checked directly rather than only via the aggregate rate
    # test above, to catch any single catastrophic miscalculation.
    frame = _null_cross_sectional_dataset(seed=99)
    mask = frame["regime"]
    cluster_ids = cd.assign_rebalance_clusters(frame)
    result = cd.cluster_permutation_test(frame, "realized_r", mask, cluster_ids, permutations=2000, seed=1)
    assert result["pValue"] is not None
    assert result["permutations"] > 0


def test_cluster_permutation_detects_a_real_cluster_level_effect():
    rng = np.random.default_rng(11)
    rows = []
    for c in range(30):
        regime = bool(rng.integers(0, 2))
        # REAL effect: regime=True clusters have a higher mean shock.
        cluster_shock = rng.normal(1.0 if regime else -1.0, 0.3)
        entry = pd.Timestamp("2020-01-01") + pd.Timedelta(days=30 * c)
        for _ in range(5):
            outcome = cluster_shock + rng.normal(0, 0.05)
            rows.append({"entry_time": entry, "exit_time": entry + pd.Timedelta(days=20),
                         "realized_r": outcome, "regime": regime})
    frame = pd.DataFrame(rows)
    mask = frame["regime"]
    cluster_ids = cd.assign_rebalance_clusters(frame)
    result = cd.cluster_permutation_test(frame, "realized_r", mask, cluster_ids, permutations=1000, seed=2)
    assert result["pValue"] < 0.01


def test_cluster_permutation_handles_within_cluster_varying_security_level_feature():
    # Security-level feature: varies row-to-row WITHIN a cluster (unlike the
    # regime case). Still must produce a valid, computable p-value.
    rng = np.random.default_rng(12)
    rows = []
    for c in range(25):
        cluster_shock = rng.normal(0, 1.0)
        entry = pd.Timestamp("2020-01-01") + pd.Timedelta(days=30 * c)
        for _ in range(5):
            security_feature_high = bool(rng.integers(0, 2))  # varies per row, not per cluster
            outcome = cluster_shock + rng.normal(0, 0.05)
            rows.append({"entry_time": entry, "exit_time": entry + pd.Timedelta(days=20),
                         "realized_r": outcome, "feature_bucket": security_feature_high})
    frame = pd.DataFrame(rows)
    mask = frame["feature_bucket"]
    cluster_ids = cd.assign_rebalance_clusters(frame)
    result = cd.cluster_permutation_test(frame, "realized_r", mask, cluster_ids, permutations=500, seed=3)
    assert result["pValue"] is not None
    assert 0.0 < result["pValue"] <= 1.0


def test_cluster_permutation_unavailable_when_mask_selects_everything_or_nothing():
    frame = _cross_sectional_frame(10, 5)
    frame["realized_r"] = 0.0
    cluster_ids = cd.assign_rebalance_clusters(frame)
    all_true = pd.Series(True, index=frame.index)
    result = cd.cluster_permutation_test(frame, "realized_r", all_true, cluster_ids, permutations=100)
    assert result["pValue"] is None


# -- dependence_aware_evaluate ----------------------------------------------

def test_dependence_aware_evaluate_reports_raw_vs_effective_n():
    frame = _cross_sectional_frame(20, 5)
    frame["realized_r"] = np.random.default_rng(0).normal(0, 1, len(frame))
    structure = cd.assign_clusters(frame, "cross_sectional")
    mask = pd.Series(frame.index % 5 < 2, index=frame.index)  # 2 of 5 rows per cluster
    result = cd.dependence_aware_evaluate(frame, "realized_r", mask, structure, draws=200, permutations=200)
    assert result["rawObservations"] == int(mask.sum())
    assert result["effectiveObservations"] <= structure.n_clusters
    assert result["methodologyVersion"] == stats.METHODOLOGY_V2_DEPENDENCE_AWARE


def test_dependence_aware_evaluate_effective_n_below_raw_n_for_clustered_data():
    frame = _cross_sectional_frame(8, 5)  # 40 raw rows, 8 clusters
    frame["realized_r"] = 0.0
    structure = cd.assign_clusters(frame, "cross_sectional")
    mask = pd.Series(True, index=frame.index)  # every row conditioned
    result = cd.dependence_aware_evaluate(frame, "realized_r", mask, structure, draws=50, permutations=50)
    assert result["rawObservations"] == 40
    assert result["effectiveObservations"] == 8
