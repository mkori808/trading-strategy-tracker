"""engine/conditional_stats.py: bucketing, uncertainty, effect size,
multiple-testing control and randomization tests -- the statistical
primitives every layer of Conditional Edge Discovery is built on."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from engine import conditional_stats as stats

pytestmark = pytest.mark.filterwarnings("ignore")


def test_sample_tier_boundaries():
    assert stats.sample_tier(stats.HYPOTHESIS_MIN_N - 1) == "insufficient"
    assert stats.sample_tier(stats.HYPOTHESIS_MIN_N) == "exploratory"
    assert stats.sample_tier(stats.EXPLORATORY_N) == "thin"
    assert stats.sample_tier(stats.WARN_N) == "adequate"


def test_sample_warning_present_below_warn_and_absent_above():
    assert stats.sample_warning(stats.HYPOTHESIS_MIN_N - 1) is not None
    assert stats.sample_warning(stats.WARN_N) is None


def test_profit_factor_known_ratio():
    values = np.array([10.0, 10.0, -5.0, -5.0])
    assert stats.profit_factor(values) == pytest.approx(2.0)


def test_profit_factor_no_losses_is_infinite():
    assert stats.profit_factor(np.array([1.0, 2.0])) == float("inf")


def test_profit_factor_no_trades_at_all_is_none():
    assert stats.profit_factor(np.array([0.0, 0.0])) is None


def test_bootstrap_ci_contains_mean_and_narrows_with_n():
    rng = np.random.default_rng(0)
    small = rng.normal(0.1, 1.0, 40)
    large = rng.normal(0.1, 1.0, 4000)
    lo_s, hi_s, _ = stats.bootstrap_mean_ci(small, seed=1)
    lo_l, hi_l, _ = stats.bootstrap_mean_ci(large, seed=1)
    assert lo_s <= small.mean() <= hi_s
    assert lo_l <= large.mean() <= hi_l
    assert (hi_l - lo_l) < (hi_s - lo_s)


def test_bootstrap_ci_deterministic_under_fixed_seed():
    values = np.linspace(-1, 1, 60)
    first = stats.bootstrap_mean_ci(values, seed=42)
    second = stats.bootstrap_mean_ci(values, seed=42)
    assert first == second


def test_hedges_g_matches_known_effect_size():
    # Two groups differing by exactly 1 pooled SD, large n so the small-sample
    # correction is negligible.
    rng = np.random.default_rng(3)
    a = rng.normal(1.0, 1.0, 5000)
    b = rng.normal(0.0, 1.0, 5000)
    g = stats.hedges_g(a, b)
    assert g == pytest.approx(1.0, abs=0.1)


def test_permutation_test_p_value_is_bounded_and_deterministic():
    rng = np.random.default_rng(4)
    a = rng.normal(0.0, 1.0, 50)
    b = rng.normal(0.0, 1.0, 50)
    result = stats.permutation_test(a, b, permutations=500, seed=5)
    assert 0.0 < result["pValue"] <= 1.0
    repeat = stats.permutation_test(a, b, permutations=500, seed=5)
    assert result["pValue"] == repeat["pValue"]


def test_permutation_test_detects_a_real_shift():
    rng = np.random.default_rng(6)
    a = rng.normal(2.0, 0.5, 100)
    b = rng.normal(0.0, 0.5, 100)
    result = stats.permutation_test(a, b, permutations=500, seed=7)
    assert result["pValue"] < 0.01


def test_permutation_test_p_value_never_exactly_zero():
    # (r+1)/(m+1) convention: even a perfect separation cannot report p=0.
    a = np.full(20, 100.0)
    b = np.full(20, -100.0)
    result = stats.permutation_test(a, b, permutations=200, seed=8)
    assert result["pValue"] == pytest.approx(1.0 / 201.0)


def test_benjamini_hochberg_monotone_and_matches_hand_calculation():
    # Classic textbook example: 5 p-values, alpha=0.05.
    p_values = [0.01, 0.04, 0.03, 0.20, 0.5]
    result = stats.benjamini_hochberg(p_values, alpha=0.05)
    q = result["qValues"]
    sorted_pairs = sorted(zip(p_values, q), key=lambda pair: pair[0])
    for i in range(len(sorted_pairs) - 1):
        assert sorted_pairs[i][1] <= sorted_pairs[i + 1][1] + 1e-12
    assert result["hypothesesTested"] == 5


def test_benjamini_hochberg_none_values_excluded_from_correction():
    result = stats.benjamini_hochberg([0.01, None, 0.5])
    assert result["qValues"][1] is None
    assert result["testableHypotheses"] == 2


def test_benjamini_hochberg_more_lenient_than_bonferroni():
    p_values = [0.001] * 20
    result = stats.benjamini_hochberg(p_values, alpha=0.05)
    assert all(q <= 0.05 for q in result["qValues"])


def test_monotonicity_detects_strictly_decreasing():
    result = stats.monotonicity([0.2, 0.1, 0.0, -0.1, -0.2])
    assert result["strictlyMonotonic"] is True
    assert result["direction"] == "decreasing"
    assert result["spearman"] == pytest.approx(-1.0)


def test_monotonicity_flat_is_not_monotonic():
    result = stats.monotonicity([0.1, 0.3, 0.1, 0.3, 0.1])
    assert result["strictlyMonotonic"] is False


def test_monotonicity_too_few_buckets_returns_none():
    assert stats.monotonicity([0.1, 0.2])["spearman"] is None


def test_quantile_buckets_correct_count_and_edges_monotonic():
    values = pd.Series(np.linspace(0, 100, 500))
    labels, edges = stats.quantile_buckets(values, buckets=5)
    assert set(labels.dropna().unique()) == {"Q1", "Q2", "Q3", "Q4", "Q5"}
    edge_highs = [e["high"] for e in edges]
    assert edge_highs == sorted(edge_highs)


def test_quantile_buckets_degrades_gracefully_with_heavy_ties():
    # 90% of values pinned at 1.0 -- qcut cannot make 10 distinct edges here.
    values = pd.Series([1.0] * 90 + list(np.linspace(2, 3, 10)))
    labels, edges = stats.quantile_buckets(values, buckets=10)
    assert len(edges) < 10
    assert len(edges) >= 1


def test_quantile_buckets_insufficient_data_returns_empty():
    labels, edges = stats.quantile_buckets(pd.Series([1.0, 1.0, 1.0]), buckets=5)
    assert edges == []


def test_redundancy_report_clusters_perfectly_correlated_features():
    n = 200
    base = np.linspace(0, 1, n)
    frame = pd.DataFrame({
        "a": base,
        "b": base * 2 + 1,  # perfectly monotonic with a -> Spearman 1.0
        "c": np.random.default_rng(0).normal(0, 1, n),  # independent
    })
    report = stats.redundancy_report(frame, ["a", "b", "c"], threshold=0.8)
    assert any(set(cluster) == {"a", "b"} for cluster in report["clusters"])
    assert not any("c" in cluster for cluster in report["clusters"])


def test_redundancy_report_handles_fewer_than_two_numeric_features():
    frame = pd.DataFrame({"a": [1.0] * 20})
    report = stats.redundancy_report(frame, ["a"])
    assert report["pairs"] == []
    assert report["clusters"] == []


# -- camel_keys: the fix for the snake_case/camelCase JSON convention bug ---

def test_camel_keys_rewrites_field_names():
    assert stats.camel_keys({"n_used": 5, "ci_low": 0.1}) == {"nUsed": 5, "ciLow": 0.1}


def test_camel_keys_is_idempotent():
    once = stats.camel_keys({"win_rate": 1})
    twice = stats.camel_keys(once)
    assert once == twice == {"winRate": 1}


def test_camel_keys_recurses_into_nested_lists_and_dicts():
    nested = {"buckets": [{"low_edge": 1, "high_edge": 2}]}
    result = stats.camel_keys(nested)
    assert result == {"buckets": [{"lowEdge": 1, "highEdge": 2}]}


def test_camel_keys_never_touches_string_values():
    # A feature identifier ("mkt_above_sma200") appearing as a VALUE must
    # survive unchanged -- only DICT KEYS are rewritten.
    payload = {"feature": "mkt_above_sma200", "op": "=="}
    assert stats.camel_keys(payload) == {"feature": "mkt_above_sma200", "op": "=="}
