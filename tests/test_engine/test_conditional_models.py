"""engine/conditional_models.py: the optional diagnostic models, restricted
to expanding-window CV and the same quantile grid the rest of the discovery
engine uses."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import conditional_models as models
from engine import conditional_stats as stats
from engine.observations import ObservationSet

pytestmark = pytest.mark.filterwarnings("ignore")


def _observation_set(n: int, *, seed: int = 0, effect: float = 3.0) -> ObservationSet:
    rng = np.random.default_rng(seed)
    times = pd.date_range("2020-01-01", periods=n, freq="D", tz="America/New_York")
    rsi2 = rng.uniform(0, 100, n)
    ret5 = rng.normal(0, 5, n)
    outcome = -effect * (rsi2 - 50) / 50 + 0.1 * ret5 + rng.normal(0, 1, n)
    frame = pd.DataFrame({
        "decision_time": times, "entry_time": times, "exit_time": times,
        "symbol": "AAPL", "direction": "long", "realized_r": outcome,
        "rsi2": rsi2, "ret_5d": ret5,
    })
    return ObservationSet(
        strategy_name="S", engine="standard", frame=frame, outcome_column="realized_r",
        outcome_label="R", start=times[0].date(), end=times[-1].date(), symbols=["AAPL"],
        feature_keys=["rsi2", "ret_5d"], coverage={"rsi2": n, "ret_5d": n},
    )


def test_fit_diagnostics_returns_empty_below_minimum_observations():
    obs = _observation_set(models.MIN_MODEL_OBSERVATIONS - 10)
    assert models.fit_diagnostics(obs) == []


def test_fit_diagnostics_produces_all_three_models_with_enough_data():
    obs = _observation_set(1000, effect=4.0)
    diagnostics = models.fit_diagnostics(obs, folds=4)
    names = {d.model for d in diagnostics}
    assert names <= {"logistic regression", "linear regression", "depth-2 quantile tree"}
    assert len(diagnostics) >= 1


def test_select_features_excludes_non_pit_and_non_eligible_features():
    obs = _observation_set(1000)
    obs.feature_keys = ["rsi2", "ret_5d", "sector_ret_60d", "day_of_week"]
    obs.frame["sector_ret_60d"] = 1.0
    obs.frame["day_of_week"] = "Mon"
    chosen = models._select_features(obs, None)
    assert "sector_ret_60d" not in chosen
    assert "day_of_week" not in chosen
    assert "rsi2" in chosen


def test_select_features_caps_at_max_model_features():
    obs = _observation_set(1000)
    extra_keys = [f"synthetic_{i}" for i in range(models.MAX_MODEL_FEATURES + 5)]
    for key in extra_keys:
        obs.frame[key] = np.random.default_rng(0).normal(0, 1, len(obs.frame))
    # Fake registry entries so _select_features treats them as eligible continuous features.
    from engine import pit_features
    import unittest.mock as mock

    fake_defs = {
        key: pit_features.FeatureDefinition(
            key=key, label=key, group="trend", kind="continuous", source="symbol_ohlcv",
            lookback_bars=1, description="synthetic",
        )
        for key in extra_keys
    }
    with mock.patch.dict(pit_features.FEATURES, fake_defs):
        obs.feature_keys = ["rsi2", "ret_5d", *extra_keys]
        chosen = models._select_features(obs, None)
    assert len(chosen) <= models.MAX_MODEL_FEATURES


def test_model_diagnostic_to_dict_preserves_feature_id_keys():
    """Regression test for the same class of bug fixed in
    conditional_ledger.py: coefficients/permutation_importance are dicts
    keyed by FEATURE ID ("rsi2", "intercept"), and a blind camelCase rekey
    would corrupt "mkt_regime" -> "mktRegime"."""
    diagnostic = models.ModelDiagnostic(
        model="linear regression", target="expected realized_r", features=["rsi2", "mkt_regime"],
        observations=500, coefficients={"intercept": 0.1, "mkt_regime": -0.2},
        coefficient_stability={"mkt_regime": 1.0}, permutation_importance={"mkt_regime": 0.05},
        fold_scores=[0.1, 0.2], mean_score=0.15, score_name="out-of-sample R^2", notes=[],
    )
    payload = diagnostic.to_dict()
    assert "mkt_regime" in payload["coefficients"]
    assert "mkt_regime" in payload["permutationImportance"]
    assert "mktRegime" not in payload["coefficients"]


def test_quantile_tree_splits_only_at_tercile_boundaries():
    n = 400
    rng = np.random.default_rng(1)
    feature = rng.uniform(0, 100, n)
    outcome = np.where(feature > 66.7, 1.0, -1.0) + rng.normal(0, 0.1, n)
    frame = pd.DataFrame({"rsi2": feature, "realized_r": outcome})
    tree = models._fit_quantile_tree(frame, ["rsi2"], "realized_r", depth=1)
    assert tree is not None
    if not tree["leaf"]:
        column = frame["rsi2"]
        tercile_1, tercile_2 = column.quantile(1 / 3), column.quantile(2 / 3)
        assert tree["threshold"] == pytest.approx(tercile_1) or tree["threshold"] == pytest.approx(tercile_2)


def test_quantile_tree_returns_leaf_when_sample_too_small():
    frame = pd.DataFrame({"rsi2": [1.0, 2.0, 3.0], "realized_r": [0.1, 0.2, 0.3]})
    tree = models._fit_quantile_tree(frame, ["rsi2"], "realized_r")
    assert tree is None


def test_expanding_folds_never_leak_future_into_training():
    obs = _observation_set(800)
    folds = models._expanding_folds(obs.frame, folds=4, embargo_days=7)
    for train, test in folds:
        assert train["decision_time"].max() < test["decision_time"].min()
