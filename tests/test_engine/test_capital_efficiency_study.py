import numpy as np

from engine.capital_efficiency_study import (
    _frontier_rows,
    _prop_paths,
    _self_funded_paths,
)
from engine.prop_scaling_survival_study import HORIZON, Rules


def test_self_funded_uses_fixed_matched_exposure_without_stopout():
    sampled = np.zeros((2, HORIZON, 3))
    sampled[0, :, :] = 0.001
    sampled[1, :, :] = -0.001
    result = _self_funded_paths(sampled, 20_000.0)
    assert result["profit"][0] == 20_000.0 * 0.001 * HORIZON
    assert result["profit"][1] == -20_000.0 * 0.001 * HORIZON
    assert result["endingEquity"][1] < 20_000.0


def test_identical_account_frontier_scales_paths_not_summary_statistics():
    sampled = np.zeros((4, HORIZON, 3))
    sampled[:, :, :] = 0.001
    prop = _prop_paths(sampled, 0.20, Rules(), "trailing_to_breakeven")
    own = _self_funded_paths(sampled, 20_000.0)
    point = {"key": "020_trailing", "scale": 0.20, "drawdownRule": "trailing_to_breakeven"}
    rows = _frontier_rows(point, prop, own)
    one = next(row for row in rows if row["structure"] == "prop" and row["accounts"] == 1)
    ten = next(row for row in rows if row["structure"] == "prop" and row["accounts"] == 10)
    assert ten["expectedNetProfit"] == one["expectedNetProfit"] * 10
    assert ten["breachProbabilityAtLeastOne"] == one["breachProbabilityAtLeastOne"]
    assert ten["dependence"] == "perfectly_correlated_identical_paths"


def test_prop_and_self_funded_have_distinct_capital_bases():
    sampled = np.zeros((4, HORIZON, 3))
    prop = _prop_paths(sampled, 0.25, Rules(), "static")
    own = _self_funded_paths(sampled, 25_000.0)
    rows = _frontier_rows({"key": "025_static", "scale": 0.25, "drawdownRule": "static"}, prop, own)
    prop_one = next(row for row in rows if row["structure"] == "prop" and row["accounts"] == 1)
    own_one = next(row for row in rows if row["structure"] == "self_funded_a" and row["accounts"] == 1)
    assert prop_one["upfrontCapitalCommitted"] == 500.0
    assert own_one["upfrontCapitalCommitted"] == 25_000.0
    assert prop_one["aggregateEffectiveExposure"] == own_one["aggregateEffectiveExposure"]
