"""engine/conditional_validation.py: chronological splitting with purge/
embargo, walk-forward, the conditioned-strategy gate, effect decomposition,
and the conditional-edge verdict gate."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import conditional_stats as stats
from engine import conditional_validation as cv
from engine.conditional_analysis import Condition
from engine.observations import ObservationSet

pytestmark = pytest.mark.filterwarnings("ignore")


def _observation_set(n: int, *, seed: int = 0, feature: str = "rsi2", effect: float = 0.0, hold_days: int = 3) -> ObservationSet:
    rng = np.random.default_rng(seed)
    feature_values = rng.uniform(0, 100, n)
    noise = rng.normal(0, 1, n)
    outcome = -effect * (feature_values - 50) / 50 + noise
    entry_times = pd.date_range("2022-01-01", periods=n, freq="D", tz="America/New_York")
    exit_times = entry_times + pd.Timedelta(days=hold_days)
    frame = pd.DataFrame({
        "decision_time": entry_times - pd.Timedelta(days=1), "entry_time": entry_times,
        "exit_time": exit_times, "symbol": "AAPL", "direction": "long",
        "realized_r": outcome, feature: feature_values,
    })
    return ObservationSet(
        strategy_name="Synthetic", engine="standard", frame=frame,
        outcome_column="realized_r", outcome_label="Expectancy (R)",
        start=entry_times[0].date(), end=entry_times[-1].date(), symbols=["AAPL"],
        feature_keys=[feature], coverage={feature: n},
    )


# -- split_observations -------------------------------------------------

def test_split_is_chronological_and_non_overlapping():
    obs = _observation_set(900)
    split = cv.split_observations(obs)
    d_max = split.discovery.frame["decision_time"].max()
    v_min = split.validation.frame["decision_time"].min()
    v_max = split.validation.frame["decision_time"].max()
    h_min = split._final_holdout.frame["decision_time"].min()
    assert d_max < v_min
    assert v_max < h_min


def test_split_final_holdout_is_sealed_until_revealed():
    obs = _observation_set(900)
    split = cv.split_observations(obs)
    with pytest.raises(cv.FinalHoldoutSealed):
        _ = split.final_holdout


def test_reveal_final_holdout_requires_a_reason(monkeypatch):
    from engine import conditional_ledger

    monkeypatch.setattr(conditional_ledger, "mark_holdout_consumed", lambda *a, **k: None)
    obs = _observation_set(900)
    split = cv.split_observations(obs)
    with pytest.raises(ValueError):
        split.reveal_final_holdout(reason="")
    revealed = split.reveal_final_holdout(reason="testing")
    assert revealed is split._final_holdout


def test_purge_and_embargo_drops_trades_whose_exit_crosses_the_boundary():
    # 3-day holds with a 7-day embargo: any trade exiting within 7 days of
    # the boundary must be purged from the training side.
    obs = _observation_set(200, hold_days=3)
    boundary = obs.frame["entry_time"].iloc[100]
    purged, count = cv._purge_and_embargo(obs.frame, boundary, embargo_days=7)
    still_present_exits = pd.to_datetime(purged["exit_time"], utc=True)
    limit = pd.Timestamp(boundary).tz_convert("UTC") - pd.Timedelta(days=7) if pd.Timestamp(boundary).tzinfo else pd.Timestamp(boundary, tz="UTC") - pd.Timedelta(days=7)
    assert (still_present_exits < limit).all()
    assert count > 0


def test_purge_and_embargo_drops_rows_with_unknown_exit():
    frame = pd.DataFrame({"exit_time": [pd.NaT, pd.Timestamp("2022-01-01", tz="UTC")]})
    purged, count = cv._purge_and_embargo(frame, pd.Timestamp("2022-06-01", tz="UTC"))
    assert len(purged) == 1
    assert count == 1


def test_split_fractions_must_sum_to_one():
    obs = _observation_set(100)
    with pytest.raises(ValueError):
        cv.split_observations(obs, fractions=(0.5, 0.5, 0.5))


# -- evaluate -------------------------------------------------------------

def test_evaluate_passes_when_effect_and_sample_clear_the_bar():
    obs = _observation_set(600, feature="rsi2", effect=3.0)
    condition = Condition(feature="rsi2", op="<", value=30.0)
    result = cv.evaluate(obs, [condition], minimum_effect=0.05, minimum_observations=30, expected_direction="higher")
    assert result["passed"] is True
    assert result["observations"] > 0


def test_evaluate_fails_on_direction_mismatch():
    obs = _observation_set(600, feature="rsi2", effect=3.0)
    condition = Condition(feature="rsi2", op="<", value=30.0)
    # The real effect is positive for this condition; declaring "lower" must fail.
    result = cv.evaluate(obs, [condition], minimum_effect=0.01, expected_direction="lower")
    assert result["passed"] is False
    assert result["directionMatched"] is False


def test_evaluate_fails_below_minimum_observations():
    obs = _observation_set(600, feature="rsi2", effect=3.0)
    condition = Condition(feature="rsi2", op="<", value=30.0)
    result = cv.evaluate(obs, [condition], minimum_effect=0.01, minimum_observations=10_000)
    assert result["passed"] is False


def test_evaluate_empty_frame_reports_no_observations():
    obs = _observation_set(600, feature="rsi2", effect=3.0)
    empty = obs.slice_between(pd.Timestamp("2099-01-01", tz="America/New_York"), None)
    condition = Condition(feature="rsi2", op="<", value=30.0)
    result = cv.evaluate(empty, [condition])
    assert result["observations"] == 0
    assert result["passed"] is False


# -- walk_forward -----------------------------------------------------------

def test_walk_forward_produces_expected_fold_count():
    obs = _observation_set(1200, feature="rsi2", effect=2.0)
    condition = Condition(feature="rsi2", op="<", value=30.0)
    result = cv.walk_forward(obs, [condition], folds=5)
    assert result["foldCount"] == 4  # folds - 1 test windows


def test_walk_forward_is_deterministic():
    obs = _observation_set(1200, feature="rsi2", effect=2.0)
    condition = Condition(feature="rsi2", op="<", value=30.0)
    first = cv.walk_forward(obs, [condition], folds=5, seed=11)
    second = cv.walk_forward(obs, [condition], folds=5, seed=11)
    assert first["meanImprovement"] == second["meanImprovement"]


def test_walk_forward_detects_a_stable_real_effect():
    obs = _observation_set(1500, feature="rsi2", effect=4.0)
    condition = Condition(feature="rsi2", op="<", value=30.0)
    result = cv.walk_forward(obs, [condition], folds=5, minimum_effect=0.05, expected_direction="higher")
    assert result["positiveFoldPct"] is not None and result["positiveFoldPct"] >= 50.0


# -- ConditionedStrategy ------------------------------------------------

class _AlwaysEntersBase:
    name = "Fake"
    timeframe = "1d"
    direction = "long"

    def entry_signal(self, bars):
        return True

    def entry_direction(self, bars):
        return "long"

    def stop_price(self, bars, entry_price):
        return entry_price * 0.95

    def target_price(self, bars, entry_price):
        return entry_price * 1.05

    def exit_signal(self, bars):
        return False


def _fake_bars(n=10):
    index = pd.bdate_range("2024-01-01", periods=n, tz="America/New_York")
    return pd.DataFrame({"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.0, "Volume": 1_000_000.0}, index=index)


def test_conditioned_strategy_blocks_entry_when_condition_fails():
    bars = _fake_bars()
    feature_frame = pd.DataFrame({"rsi2": [50.0] * len(bars)}, index=bars.index)
    condition = Condition(feature="rsi2", op="<", value=10.0)  # never true at rsi2=50
    strategy = cv.ConditionedStrategy(_AlwaysEntersBase(), "AAPL", [condition], feature_frame)
    assert strategy.entry_signal(bars) is False
    assert strategy.blocked == 1 and strategy.allowed == 0


def test_conditioned_strategy_allows_entry_when_condition_holds():
    bars = _fake_bars()
    feature_frame = pd.DataFrame({"rsi2": [5.0] * len(bars)}, index=bars.index)
    condition = Condition(feature="rsi2", op="<", value=10.0)
    strategy = cv.ConditionedStrategy(_AlwaysEntersBase(), "AAPL", [condition], feature_frame)
    assert strategy.entry_signal(bars) is True
    assert strategy.allowed == 1 and strategy.blocked == 0


def test_conditioned_strategy_blocks_on_unknown_feature_value():
    bars = _fake_bars()
    feature_frame = pd.DataFrame({"rsi2": [np.nan] * len(bars)}, index=bars.index)
    condition = Condition(feature="rsi2", op="<", value=10.0)
    strategy = cv.ConditionedStrategy(_AlwaysEntersBase(), "AAPL", [condition], feature_frame)
    assert strategy.entry_signal(bars) is False


def test_conditioned_strategy_delegates_stop_target_exit_untouched():
    bars = _fake_bars()
    feature_frame = pd.DataFrame({"rsi2": [5.0] * len(bars)}, index=bars.index)
    condition = Condition(feature="rsi2", op="<", value=10.0)
    strategy = cv.ConditionedStrategy(_AlwaysEntersBase(), "AAPL", [condition], feature_frame)
    assert strategy.stop_price(bars, 100.0) == 95.0
    assert strategy.target_price(bars, 100.0) == 105.0
    assert strategy.exit_signal(bars) is False


# -- decompose_filter_effect --------------------------------------------

def test_decompose_identifies_bad_trade_avoidance():
    n = 400
    rng = np.random.default_rng(5)
    times = pd.date_range("2022-01-01", periods=n, freq="D", tz="America/New_York")
    feature = rng.uniform(0, 100, n)
    # Losers cluster where feature > 70; winners are unaffected by feature.
    outcome = np.where(feature > 70, -abs(rng.normal(1, 0.3, n)), rng.normal(0.2, 0.3, n))
    frame = pd.DataFrame({
        "decision_time": times, "entry_time": times, "exit_time": times,
        "symbol": "AAPL", "direction": "long", "realized_r": outcome, "rsi2": feature,
    })
    obs = ObservationSet(
        strategy_name="S", engine="standard", frame=frame, outcome_column="realized_r",
        outcome_label="R", start=times[0].date(), end=times[-1].date(), symbols=["AAPL"],
        feature_keys=["rsi2"], coverage={"rsi2": n},
    )
    condition = Condition(feature="rsi2", op="<", value=70.0)
    result = cv.decompose_filter_effect(obs, [condition])
    assert result["available"] is True
    # Every removed observation here is a loser and none is a winner -- the
    # bad-trade-avoidance mechanism must reflect that directly, whichever
    # mechanism the fuzzy "primary" heuristic (mechanisms legitimately
    # overlap) ends up naming.
    mechanisms = result["mechanisms"]
    assert mechanisms["badTradeAvoidance"]["share"] == pytest.approx(1.0)
    assert mechanisms["badTradeAvoidance"]["lossRateChange"] < 0
    assert mechanisms["winnerConcentration"]["removedWinners"] == 0
    assert result["primaryMechanism"] in {"bad-trade avoidance", "risk reduction"}


def test_decompose_unavailable_when_rule_keeps_everything():
    obs = _observation_set(200, feature="rsi2")
    condition = Condition(feature="rsi2", op="<", value=1000.0)  # always true
    result = cv.decompose_filter_effect(obs, [condition])
    assert result["available"] is False


# -- conditional_verdict ---------------------------------------------------

def _passing_validation(n=100):
    return {"passed": True, "observations": n, "conclusion": "ok"}


def test_verdict_validated_requires_every_gate():
    verdict = cv.conditional_verdict(
        discovery={}, validation=_passing_validation(), holdout=_passing_validation(),
        walk_forward_result={"usableFolds": 4, "positiveFoldPct": 75.0, "positiveFolds": 3},
        fdr_significant=True, hypotheses_examined=50,
        integrity={"intact": True, "featureDriftDetected": False},
    )
    assert verdict.verdict == cv.VERDICT_VALIDATED


def test_verdict_promising_when_holdout_missing_but_validation_and_stability_hold():
    verdict = cv.conditional_verdict(
        discovery={}, validation=_passing_validation(), holdout=None,
        walk_forward_result={"usableFolds": 4, "positiveFoldPct": 75.0, "positiveFolds": 3},
        fdr_significant=False, hypotheses_examined=200,
        integrity={"intact": True, "featureDriftDetected": False},
    )
    assert verdict.verdict == cv.VERDICT_PROMISING


def test_verdict_weak_when_validation_passes_but_nothing_else_does():
    verdict = cv.conditional_verdict(
        discovery={}, validation=_passing_validation(), holdout={"passed": False, "observations": 40, "conclusion": "no"},
        walk_forward_result={"usableFolds": 4, "positiveFoldPct": 25.0, "positiveFolds": 1},
        fdr_significant=False, hypotheses_examined=200,
        integrity={"intact": True, "featureDriftDetected": False},
    )
    assert verdict.verdict == cv.VERDICT_WEAK


def test_verdict_none_when_validation_fails():
    verdict = cv.conditional_verdict(
        discovery={}, validation={"passed": False, "observations": 100, "conclusion": "no improvement"},
        holdout=None, walk_forward_result=None, fdr_significant=False,
        hypotheses_examined=50, integrity={"intact": True, "featureDriftDetected": False},
    )
    assert verdict.verdict == cv.VERDICT_NONE


def test_verdict_insufficient_when_contract_hash_mismatches():
    verdict = cv.conditional_verdict(
        discovery={}, validation=_passing_validation(), holdout=_passing_validation(),
        walk_forward_result={"usableFolds": 4, "positiveFoldPct": 100.0, "positiveFolds": 4},
        fdr_significant=True, hypotheses_examined=10,
        integrity={"intact": False, "featureDriftDetected": False},
    )
    assert verdict.verdict == cv.VERDICT_INSUFFICIENT
    assert any("contract hash" in b.lower() for b in verdict.blockers)


def test_verdict_insufficient_when_validation_never_ran():
    verdict = cv.conditional_verdict(
        discovery={}, validation=None, holdout=None, walk_forward_result=None,
        fdr_significant=None, hypotheses_examined=None, integrity=None,
    )
    assert verdict.verdict == cv.VERDICT_INSUFFICIENT


def test_verdict_insufficient_when_exploratory_feature_used():
    verdict = cv.conditional_verdict(
        discovery={}, validation=_passing_validation(), holdout=_passing_validation(),
        walk_forward_result={"usableFolds": 4, "positiveFoldPct": 100.0, "positiveFolds": 4},
        fdr_significant=True, hypotheses_examined=10,
        integrity={"intact": True, "featureDriftDetected": False},
        exploratory_features_used=True,
    )
    assert verdict.verdict == cv.VERDICT_INSUFFICIENT
