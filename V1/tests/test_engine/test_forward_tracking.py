"""The frozen stop rule must be mechanical, not a judgement call.

Every test here asserts a decision the frozen file already made. If one fails,
either the code drifted from FROZEN_DUAL_MOMENTUM.md or someone edited the
frozen file after committing it -- both invalidate the forward test.
"""

from __future__ import annotations

from engine.forward_tracking import (
    CONTINUE_HORIZON_MONTHS,
    STOP_HORIZON_MONTHS,
    STOP_SHORTFALL_PP,
    evaluate_stop,
)


def test_thresholds_match_the_frozen_file():
    """Constants are mirrored from the frozen file and must not drift."""
    assert STOP_SHORTFALL_PP == 15.0
    assert STOP_HORIZON_MONTHS == 24
    assert CONTINUE_HORIZON_MONTHS == 48


def test_early_breach_is_not_a_stop():
    """The stop is evaluated AT 24 months, not continuously.

    Worst observed 3-year dispersion is -12.2pp, so an -18pp reading at month 9
    is inside behaviour this research already measured. Stopping there would be
    stopping on noise the frozen file explicitly names as normal.
    """
    d = evaluate_stop(9.0, -18.0)
    assert not d.triggered
    assert d.verdict == "running"


def test_breach_at_horizon_stops_and_forbids_tuning():
    d = evaluate_stop(24.0, -16.2)
    assert d.triggered
    assert "STOP" in d.verdict
    assert "not tune" in d.reasoning.lower()


def test_within_threshold_at_horizon_is_not_confirmation():
    """The failure mode this encodes: reading survival as vindication.

    Being ahead at 24 months must NOT read as confirmed -- ~24 monthly
    observations cannot separate a +2%/yr effect from noise with 3-year swings
    to -12pp.
    """
    d = evaluate_stop(24.0, +5.0)
    assert not d.triggered
    assert "NOT confirmed" in d.verdict
    assert str(CONTINUE_HORIZON_MONTHS) in d.reasoning


def test_ambiguous_result_continues_unchanged():
    """Between the two outcomes is EXPECTED, not a reason to act."""
    d = evaluate_stop(24.0, -8.0)
    assert not d.triggered
    assert "NOT confirmed" in d.verdict


def test_missing_benchmark_never_triggers_a_stop():
    d = evaluate_stop(30.0, None)
    assert not d.triggered
    assert d.verdict == "insufficient data"
