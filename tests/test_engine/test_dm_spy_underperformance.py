import numpy as np
import pandas as pd

from engine import dm_spy_underperformance as audit


def _frame() -> pd.DataFrame:
    index = pd.date_range("2020-01-01", periods=10, freq="YS")
    strategy = pd.Series([-0.02, -0.01, 0.01, 0.03, -0.03, 0.02, 0.01, -0.01, 0.02, 0.00], index=index)
    spy = pd.Series([-0.01, -0.02, 0.02, 0.01, -0.01, 0.03, 0.00, -0.02, 0.01, 0.00], index=index)
    frame = pd.DataFrame({"return": strategy, "spy_return": spy})
    frame["active_return"] = frame["return"] - frame["spy_return"]
    frame["underperformed_spy"] = frame["active_return"] < 0
    frame["absolute_loss"] = frame["return"] < 0
    frame["joint_damaging"] = frame["underperformed_spy"] & frame["absolute_loss"]
    frame["qualification"] = [True] * 5 + [False] * 5
    frame["beta_adjusted_residual"] = frame["active_return"]
    frame["residual_underperformed"] = frame["beta_adjusted_residual"] < 0
    return frame


def test_event_summary_partitions_all_days_and_freezes_severe_cutoff_on_first_60_percent():
    frame = _frame()
    summary = audit._event_summary(frame)
    quadrants = summary["quadrants"]
    assert sum(item["n"] for item in quadrants.values()) == len(frame)
    expected_cutoff = frame.iloc[:6]["active_return"].quantile(0.10)
    assert summary["severeRelativeMiss"]["trainingObservations"] == 6
    assert summary["severeRelativeMiss"]["frozenTrainingP10Cutoff"] == expected_cutoff


def test_effect_compares_predictive_event_rates_to_false_state_base_rate():
    row = audit._effect(_frame(), "qualification")
    assert row["qualified"]["n"] == 5
    assert row["notQualified"]["n"] == 5
    assert row["riskDifference"] == row["qualified"]["eventRate"] - row["notQualified"]["eventRate"]
    assert row["qualificationBaseRate"] == 0.5


def test_moving_block_bootstrap_is_seed_deterministic(monkeypatch):
    monkeypatch.setattr(audit, "BOOTSTRAPS", 100)
    event = np.array([True, False, True, False] * 20)
    state = np.array([True, True, False, False] * 20)
    first = audit._bootstrap(event, state, np.random.default_rng(123))
    second = audit._bootstrap(event, state, np.random.default_rng(123))
    assert first == second
    assert first["replications"] == 100


def test_optimized_result_cannot_be_promoted_even_when_all_statistical_gates_pass():
    row = {
        "qualified": {"n": 100}, "notQualified": {"n": 100},
        "calendarYears": [2022, 2023, 2024],
        "bootstrap": {"ciLow": 0.01, "ciHigh": 0.08}, "qValue": 0.01,
        "leaveOneYearOut": {"directionalAgreement": 1.0},
    }
    classification, reasons = audit._classify(row, optimized=True, total_events=80)
    assert classification == "exploratory_historical"
    assert any("selection-contaminated" in reason for reason in reasons)
