"""engine/live_risk.py: the hard position/risk limits enforced independent
of any strategy's own logic."""

from __future__ import annotations

from engine.live_risk import RiskLimits, clip_target_weights, daily_loss_halted


def test_clip_target_weights_caps_a_single_position():
    limits = RiskLimits(max_position_pct=0.30, max_concurrent_positions=10)
    weights = {"AAPL": 0.60, "MSFT": 0.20}
    clipped = clip_target_weights(weights, limits)
    assert clipped == {"AAPL": 0.30, "MSFT": 0.20}


def test_clip_target_weights_truncates_to_top_n_by_weight():
    limits = RiskLimits(max_position_pct=1.0, max_concurrent_positions=2)
    weights = {"A": 0.10, "B": 0.50, "C": 0.30}
    clipped = clip_target_weights(weights, limits)
    assert clipped == {"B": 0.50, "C": 0.30}


def test_clip_target_weights_applies_cap_before_truncation():
    # B and C both get capped to 0.30 first; the truncation to top-2 must
    # then pick among the CAPPED values, not the original ones, so a name
    # that was originally largest but gets capped the same as another
    # doesn't win a tie it wouldn't otherwise have.
    limits = RiskLimits(max_position_pct=0.30, max_concurrent_positions=2)
    weights = {"A": 0.10, "B": 0.90, "C": 0.35}
    clipped = clip_target_weights(weights, limits)
    assert set(clipped) == {"B", "C"}
    assert clipped["B"] == 0.30
    assert clipped["C"] == 0.30


def test_clip_target_weights_excess_becomes_cash_not_redistributed():
    limits = RiskLimits(max_position_pct=0.20, max_concurrent_positions=10)
    weights = {"AAPL": 1.0}
    clipped = clip_target_weights(weights, limits)
    # 0.80 of the portfolio value is simply not represented -- it's cash,
    # never handed to another symbol that wasn't in the original weights.
    assert clipped == {"AAPL": 0.20}


def test_clip_target_weights_empty_input():
    assert clip_target_weights({}, RiskLimits()) == {}


def test_daily_loss_halted_true_when_threshold_breached():
    limits = RiskLimits(daily_loss_halt_pct=0.05)
    assert daily_loss_halted(equity=94_000, last_equity=100_000, limits=limits) is True


def test_daily_loss_halted_false_when_under_threshold():
    limits = RiskLimits(daily_loss_halt_pct=0.05)
    assert daily_loss_halted(equity=96_000, last_equity=100_000, limits=limits) is False


def test_daily_loss_halted_false_on_a_gain():
    limits = RiskLimits(daily_loss_halt_pct=0.05)
    assert daily_loss_halted(equity=105_000, last_equity=100_000, limits=limits) is False


def test_daily_loss_halted_false_when_no_baseline():
    limits = RiskLimits(daily_loss_halt_pct=0.05)
    assert daily_loss_halted(equity=50_000, last_equity=None, limits=limits) is False
    assert daily_loss_halted(equity=50_000, last_equity=0, limits=limits) is False
