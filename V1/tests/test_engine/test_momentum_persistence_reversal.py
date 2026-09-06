import numpy as np
import pandas as pd

from engine import momentum_persistence_reversal as audit


def test_state_for_session_uses_only_information_through_prior_close(monkeypatch):
    index = pd.bdate_range("2023-01-02", periods=260)
    close = pd.Series(np.linspace(80.0, 120.0, len(index)), index=index)

    def bars(*_args, **_kwargs):
        return pd.DataFrame({"Close": close})

    monkeypatch.setattr(audit.data_module, "get_bars", bars)
    target = index[-1]
    baseline, diagnostics = audit._state_frame(pd.DatetimeIndex([target]))

    close.loc[target] = 1.0
    changed, _ = audit._state_frame(pd.DatetimeIndex([target]))

    assert baseline.loc[target, "state"] == "bullish_persistence"
    assert changed.loc[target, "state"] == baseline.loc[target, "state"]
    assert changed.loc[target, "prior_spy_close"] == baseline.loc[target, "prior_spy_close"]
    assert diagnostics["informationCutoff"] == "T-1 close for both state inputs"


def test_empty_state_metrics_are_explicitly_unavailable():
    sample = pd.DataFrame(columns=["return", "active_return"])
    metrics = audit._state_metrics(sample, 100)

    assert metrics["n"] == 0
    assert metrics["observationShare"] == 0.0
    assert metrics["meanDailyReturn"] is None
    assert metrics["stateAttributedMaximumDrawdown"] is None


def _classification_row(**overrides):
    row = {
        "persistence": {"n": 100, "meanDailyActiveReturn": 0.001},
        "transition": {"n": 100, "meanDailyActiveReturn": -0.001},
        "calendarYears": [2022, 2023, 2024],
        "minimumDisplayedStateN": 30,
        "differenceMeanDailyActiveReturn": 0.002,
        "bootstrap": {"ciLow": 0.0001, "ciHigh": 0.003},
        "qValue": 0.05,
        "leaveOneYearOut": {"directionalAgreement": 1.0},
    }
    row.update(overrides)
    return row


def _gates():
    return {
        "minimumObservationsPerPrimarySide": 63,
        "minimumObservationsPerDisplayedState": 21,
        "minimumContributingCalendarYears": 3,
        "maximumBhQ": 0.10,
        "minimumLeaveOneYearOutDirectionalAgreement": 0.80,
    }


def test_negative_primary_direction_cannot_pass_even_with_excluding_interval():
    row = _classification_row(
        differenceMeanDailyActiveReturn=-0.002,
        bootstrap={"ciLow": -0.003, "ciHigh": -0.0001},
    )
    classification, reasons = audit._classify(
        row, "candidate_for_independent_validation_only", _gates()
    )

    assert classification == "unsupported_or_unstable"
    assert any("not positive" in reason for reason in reasons)


def test_exploratory_evidence_cap_applies_even_when_all_gates_pass():
    classification, reasons = audit._classify(
        _classification_row(), "exploratory_historical_selection_contaminated", _gates()
    )

    assert classification == "exploratory_historical"
    assert any("caps" in reason for reason in reasons)
