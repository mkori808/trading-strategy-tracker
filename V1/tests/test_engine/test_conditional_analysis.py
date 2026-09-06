"""engine/conditional_analysis.py: univariate/interaction analysis, threshold
snapping, and candidate-hypothesis generation.

Every synthetic ObservationSet here uses REAL feature keys from
pit_features.FEATURES (e.g. "rsi2", "mkt_regime") so the module's own
metadata-driven branches (categorical vs continuous, discovery_eligible,
pit_safe) exercise their real lookups rather than a test double.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import conditional_analysis as ca
from engine import conditional_stats as stats
from engine import pit_features
from engine.observations import ObservationSet

pytestmark = pytest.mark.filterwarnings("ignore")


def _observation_set(n: int, *, seed: int = 0, feature: str = "rsi2", effect: float = 0.0) -> ObservationSet:
    """`n` observations where `realized_r` is a deterministic decreasing
    function of `feature` plus noise, scaled by `effect` (0 = no relationship)."""
    rng = np.random.default_rng(seed)
    feature_values = rng.uniform(0, 100, n)
    noise = rng.normal(0, 1, n)
    outcome = -effect * (feature_values - 50) / 50 + noise
    times = pd.date_range("2022-01-01", periods=n, freq="D", tz="America/New_York")
    frame = pd.DataFrame({
        "decision_time": times, "entry_time": times, "exit_time": times,
        "symbol": "AAPL", "direction": "long",
        "realized_r": outcome,
        feature: feature_values,
    })
    return ObservationSet(
        strategy_name="Synthetic", engine="standard", frame=frame,
        outcome_column="realized_r", outcome_label="Expectancy (R)",
        start=times[0].date(), end=times[-1].date(), symbols=["AAPL"],
        feature_keys=[feature], coverage={feature: n},
    )


def test_choose_bucket_count_scales_with_sample_size():
    assert ca.choose_bucket_count(stats.HYPOTHESIS_MIN_N - 1) == 0
    assert ca.choose_bucket_count(stats.HYPOTHESIS_MIN_N * 5) == 5
    assert ca.choose_bucket_count(stats.EXPLORATORY_N * 10) == 10


def test_analyze_univariate_detects_a_real_monotonic_relationship():
    obs = _observation_set(600, feature="rsi2", effect=3.0)
    results = ca.analyze_univariate(obs, ["rsi2"], permutations=500)
    assert len(results) == 1
    result = results[0]
    assert result.monotonic["strictlyMonotonic"] is True
    assert result.permutation_p is not None and result.permutation_p < 0.05
    assert result.spread != 0


def test_analyze_univariate_suppresses_features_below_minimum_n():
    obs = _observation_set(10, feature="rsi2", effect=1.0)
    accounting = stats.DiscoveryAccounting(session_id="test")
    results = ca.analyze_univariate(obs, ["rsi2"], permutations=100, accounting=accounting)
    assert results == []
    assert accounting.suppressed_for_sample == 1


def test_analyze_univariate_flags_non_pit_and_exploratory_features():
    obs = _observation_set(600, feature="sector_ret_60d", effect=2.0)
    results = ca.analyze_univariate(obs, ["sector_ret_60d"], permutations=200)
    assert results[0].pit_safe is False
    assert results[0].discovery_eligible is False
    assert any("NOT point-in-time" in w for w in results[0].warnings)


def test_categorical_feature_uses_category_ordering():
    n = 300
    rng = np.random.default_rng(1)
    times = pd.date_range("2022-01-01", periods=n, freq="D", tz="America/New_York")
    regimes = rng.choice(["Bullish", "Neutral", "Bearish"], n)
    outcome = np.where(regimes == "Bullish", 0.3, np.where(regimes == "Bearish", -0.3, 0.0)) + rng.normal(0, 0.2, n)
    frame = pd.DataFrame({
        "decision_time": times, "entry_time": times, "exit_time": times,
        "symbol": "AAPL", "direction": "long", "realized_r": outcome, "mkt_regime": regimes,
    })
    obs = ObservationSet(
        strategy_name="S", engine="standard", frame=frame, outcome_column="realized_r",
        outcome_label="R", start=times[0].date(), end=times[-1].date(), symbols=["AAPL"],
        feature_keys=["mkt_regime"], coverage={"mkt_regime": n},
    )
    results = ca.analyze_univariate(obs, ["mkt_regime"], permutations=300)
    assert results[0].kind == "categorical"
    labels = [b.label for b in results[0].buckets]
    # Bearish/Neutral/Bullish is the declared category order for this feature.
    assert labels == [l for l in ("Bearish", "Neutral", "Bullish") if l in labels]


def test_interaction_minimum_n_enforcement_suppresses_small_cells():
    n = 200
    rng = np.random.default_rng(2)
    times = pd.date_range("2022-01-01", periods=n, freq="D", tz="America/New_York")
    a = rng.uniform(0, 100, n)
    b = rng.choice([True, False], n, p=[0.02, 0.98])  # heavily imbalanced -> tiny cells
    outcome = rng.normal(0, 1, n)
    frame = pd.DataFrame({
        "decision_time": times, "entry_time": times, "exit_time": times,
        "symbol": "AAPL", "direction": "long", "realized_r": outcome,
        "rsi2": a, "mkt_above_sma200": b,
    })
    obs = ObservationSet(
        strategy_name="S", engine="standard", frame=frame, outcome_column="realized_r",
        outcome_label="R", start=times[0].date(), end=times[-1].date(), symbols=["AAPL"],
        feature_keys=["rsi2", "mkt_above_sma200"], coverage={"rsi2": n, "mkt_above_sma200": n},
    )
    results = ca.analyze_interactions(obs, [("rsi2", "mkt_above_sma200")], permutations=200)
    if results:
        assert results[0].cells_suppressed >= 1
        assert all(cell.n >= stats.HYPOTHESIS_MIN_N for cell in results[0].cells)


def test_plan_pairwise_excludes_same_group_pairs():
    univariate = [
        ca.UnivariateResult(
            feature="rsi2", label="RSI(2)", group="mean_reversion", kind="continuous",
            pit_safe=True, discovery_eligible=True, buckets=[], n_used=100, n_missing=0,
            baseline_mean=0, best_label="Q1", best_mean=0.1, worst_label="Q5", worst_mean=-0.1,
            spread=0.2, effect_size=0.5, permutation_p=0.01, welch_p=0.01,
            monotonic={"spearman": -1.0, "strictlyMonotonic": True, "direction": "decreasing"},
            tier="adequate",
        ),
        ca.UnivariateResult(
            feature="cum_ret_3d", label="3-day return", group="mean_reversion", kind="continuous",
            pit_safe=True, discovery_eligible=True, buckets=[], n_used=100, n_missing=0,
            baseline_mean=0, best_label="Q1", best_mean=0.1, worst_label="Q5", worst_mean=-0.1,
            spread=0.2, effect_size=0.4, permutation_p=0.02, welch_p=0.02,
            monotonic={"spearman": -1.0, "strictlyMonotonic": True, "direction": "decreasing"},
            tier="adequate",
        ),
    ]
    pairs = ca.plan_pairwise(univariate)
    # rsi2 and cum_ret_3d are BOTH "mean_reversion" -- must never be paired
    # against each other, only against the fixed regime axis.
    assert ("rsi2", "cum_ret_3d") not in pairs and ("cum_ret_3d", "rsi2") not in pairs
    assert any("rsi2" in pair for pair in pairs)


def test_plan_three_way_empty_below_minimum_total_sample():
    obs = _observation_set(50, feature="rsi2")
    assert ca.plan_three_way([], obs) == []


def test_snap_threshold_rsi_snaps_to_multiples_of_five():
    assert ca.snap_threshold("rsi2", 27.43) == 25.0
    assert ca.snap_threshold("rsi2", 12.6) == 15.0


def test_snap_threshold_zero_band_for_trend_features():
    assert ca.snap_threshold("ret_20d", 1.2) == 0.0
    assert ca.snap_threshold("ret_20d", -2.9) == 0.0


def test_snap_threshold_generic_rounding_uses_half_steps():
    result = ca.snap_threshold("dollar_volume", 1_234_567.0)
    assert result != 1_234_567.0
    # Rounding to a half-step of the leading digit's magnitude.
    assert result % (10 ** (len(str(int(result))) - 2) / 2) == pytest.approx(0, abs=1e-6)


def test_build_candidate_returns_none_below_minimum_n():
    obs = _observation_set(10, feature="rsi2", effect=3.0)
    condition = ca.Condition(feature="rsi2", op="<", value=30.0)
    assert ca.build_candidate(obs, [condition], origin="test") is None


def test_build_candidate_reports_retention_and_direction():
    obs = _observation_set(600, feature="rsi2", effect=3.0)
    condition = ca.Condition(feature="rsi2", op="<", value=30.0)
    candidate = ca.build_candidate(obs, [condition], origin="test")
    assert candidate is not None
    assert candidate.n_conditioned == int((obs.frame["rsi2"] < 30.0).sum())
    assert candidate.retention_pct == pytest.approx(candidate.n_conditioned / len(obs) * 100.0)
    # rsi2 < 30 is the LOW tail, and effect is set up so low feature -> high
    # outcome (outcome = -effect*(x-50)/50), so this condition should IMPROVE
    # the mean relative to baseline.
    assert candidate.improvement > 0


def test_generate_candidates_respects_max_conditions_and_group_diversity():
    obs = _observation_set(700, feature="rsi2", effect=3.0)
    # Add a second, unrelated feature from a DIFFERENT group so a 2-condition
    # composite is possible.
    rng = np.random.default_rng(9)
    obs.frame["mkt_above_sma200"] = rng.choice([True, False], len(obs.frame))
    obs.feature_keys = ["rsi2", "mkt_above_sma200"]
    univariate = ca.analyze_univariate(obs, obs.feature_keys, permutations=300)
    interactions = ca.analyze_interactions(obs, ca.plan_pairwise(univariate), permutations=300)
    candidates = ca.generate_candidates(obs, univariate, interactions, permutations=300)
    for candidate in candidates:
        assert len(candidate.conditions) <= ca.MAX_CONDITIONS
        groups = [c.groups for c in [candidate]][0]
        assert len(groups) == len(set(groups))  # no repeated group in one candidate


def test_run_discovery_is_deterministic_under_fixed_seed():
    obs = _observation_set(500, feature="rsi2", effect=2.0)
    first = ca.run_discovery(obs, seed=123, permutations=300)
    second = ca.run_discovery(obs, seed=123, permutations=300)
    assert first.fdr["hypothesesTested"] == second.fdr["hypothesesTested"]
    first_ps = sorted(u.permutation_p for u in first.univariate if u.permutation_p is not None)
    second_ps = sorted(u.permutation_p for u in second.univariate if u.permutation_p is not None)
    assert first_ps == second_ps


def test_run_discovery_accounting_matches_actual_tests_performed():
    obs = _observation_set(500, feature="rsi2", effect=2.0)
    session = ca.run_discovery(obs, seed=1, permutations=300, include_three_way=False)
    assert session.accounting.univariate_tested == len(session.univariate)
    assert session.accounting.pairwise_tested == len([i for i in session.interactions if i.order == 2])


def test_run_discovery_with_no_relationship_yields_no_significant_hypotheses():
    obs = _observation_set(400, feature="rsi2", effect=0.0)
    session = ca.run_discovery(obs, seed=2, permutations=300)
    # No real effect: FDR-significant should be 0 or very small by chance,
    # never dominant.
    assert session.fdr["fdrSignificant"] <= 1
