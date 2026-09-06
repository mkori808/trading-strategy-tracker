import numpy as np

from engine.prop_scaling_survival_study import Rules, _simulate_scale


def _sample(close: float, low: float, high: float, paths: int = 20) -> np.ndarray:
    values = np.zeros((paths, 252, 3), dtype=float)
    values[:, :, 0] = close
    values[:, :, 1] = low
    values[:, :, 2] = high
    return values


def test_intraday_low_can_breach_when_close_does_not():
    rows = _sample(close=0.0, low=-0.031, high=0.0)
    result = _simulate_scale(rows, 1.0, Rules(), "static")
    assert result["breachProbability1m"] == 1.0
    assert result["dailyLimitBreachProbability"] == 1.0


def test_reset_fees_are_charged_after_each_breach():
    rows = _sample(close=-0.04, low=-0.04, high=0.0, paths=5)
    result = _simulate_scale(rows, 1.0, Rules(), "static")
    assert result["expectedResets"] == 252.0
    assert result["expectedFees"] == 500.0 + 252 * 500.0
    assert result["p05NetPayout"] < 0


def test_static_utilization_uses_static_floor_anchor_not_profit_peak():
    rows = _sample(close=0.001, low=-0.001, high=0.01)
    result = _simulate_scale(rows, 0.1, Rules(), "static")
    assert result["medianDrawdownUtilization"] < 0.01


def test_trailing_high_then_low_is_more_demanding_than_static():
    rows = _sample(close=0.0, low=-0.02, high=0.05)
    static = _simulate_scale(rows, 1.0, Rules(), "static")
    trailing = _simulate_scale(rows, 1.0, Rules(), "trailing_to_breakeven")
    assert trailing["breachProbability12m"] >= static["breachProbability12m"]
