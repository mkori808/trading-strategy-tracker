import numpy as np

from engine.dm_regime_dependence import (
    BLOCK,
    _bh,
    _block_bootstrap_difference,
    _classification,
)


def test_block_bootstrap_is_deterministic_and_skips_single_state_resamples():
    values = np.linspace(-0.01, 0.01, 84)
    states = np.array([False] * 42 + [True] * 42)
    first = _block_bootstrap_difference(values, states, np.random.default_rng(7))
    second = _block_bootstrap_difference(values, states, np.random.default_rng(7))
    assert first == second
    assert 0 < first["replications"] <= 5000
    assert BLOCK == 21


def test_bh_correction_uses_the_complete_six_test_family():
    rows = [{"bootstrap": {"twoSidedP": p}} for p in (0.01, 0.02, 0.03, 0.4, 0.5, 0.9)]
    _bh(rows)
    assert [row["qValue"] for row in rows[:3]] == [0.06, 0.06, 0.06]
    assert all(0 <= row["qValue"] <= 1 for row in rows)


def test_optimized_result_is_always_capped_at_exploratory():
    row = {
        "stateOne": {"n": 200}, "stateZero": {"n": 200},
        "calendarYears": [2023, 2024, 2025],
        "bootstrap": {"ciLow": 0.001, "ciHigh": 0.002},
        "qValue": 0.01,
        "leaveOneYearOut": {"directionalAgreement": 1.0},
    }
    classification, reasons = _classification(row, optimized=True)
    assert classification == "exploratory_historical"
    assert any("selection-contaminated" in reason for reason in reasons)
