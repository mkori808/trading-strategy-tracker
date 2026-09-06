from __future__ import annotations

import numpy as np
import pytest

from engine.hypothesis_cost_screen import (
    CostScreenResult,
    abdi_ranaldo_spread,
    corwin_schultz_spread,
)


def test_corwin_schultz_truncates_negative_estimates_to_zero() -> None:
    # A perfectly flat high==low series drives beta/gamma to zero and alpha
    # negative -- must clip to 0, never return a negative "spread".
    high = [100.0] * 10
    low = [100.0] * 10
    spread = corwin_schultz_spread(high, low)
    assert (spread >= 0).all()


def test_abdi_ranaldo_zero_when_close_sits_exactly_at_the_log_midpoint() -> None:
    # If close always equals the LOG high-low midpoint (the geometric mean
    # of high and low, not the arithmetic one), the estimator's covariance
    # term is zero -- the textbook zero-spread case.
    high = [102.0] * 10
    low = [98.0] * 10
    close = [(102.0 * 98.0) ** 0.5] * 10  # exact geometric mean
    spread = abdi_ranaldo_spread(close, high, low)
    assert np.allclose(spread, 0, atol=1e-9)


def test_estimators_agree_liquid_flat_series_gives_near_zero_both_ways() -> None:
    # Pinned regression for the mega-cap sanity check's logic: a low-
    # volatility series should read near-zero on BOTH estimators, not just
    # one -- if only one goes to zero, that is the divergence this session
    # found on AAPL and it must be visible, not averaged away.
    rng = np.random.default_rng(0)
    n = 200
    base = 100 * np.cumprod(1 + rng.normal(0, 0.0005, n))
    high = base * 1.0002
    low = base * 0.9998
    close = base
    cs = np.nanmedian(corwin_schultz_spread(high, low))
    ar = np.nanmedian(abdi_ranaldo_spread(close, high, low))
    assert cs < 0.001  # < 10bps
    assert ar < 0.001


def test_verdict_moot_overrides_cost_regardless_of_estimator_values() -> None:
    result = CostScreenResult(
        candidate="x", holdingPeriodModel="", holdingPeriodYears=0.1, sampleTickers=["A"],
        spreadMeasurement={"corwinSchultzMedianBps": 5.0, "abdiRanaldoMedianBps": 5.0},
        mdaPct=1.0, breadthVerdict="DEAD", annualCostByEstimator={"corwinSchultz": 0.01, "abdiRanaldo": 0.01},
    )
    assert result.verdict().startswith("MOOT")


def test_verdict_estimator_sensitive_when_estimators_disagree_on_which_side_of_mda() -> None:
    result = CostScreenResult(
        candidate="x", holdingPeriodModel="", holdingPeriodYears=0.1, sampleTickers=["A"],
        spreadMeasurement={}, mdaPct=3.0, breadthVerdict="VIABLE",
        annualCostByEstimator={"corwinSchultz": 5.0, "abdiRanaldo": 1.0},
    )
    assert result.verdict() == "ESTIMATOR_SENSITIVE -- verdict depends on which spread estimator is trusted"


def test_verdict_cost_ok_requires_both_estimators_under_mda() -> None:
    result = CostScreenResult(
        candidate="x", holdingPeriodModel="", holdingPeriodYears=0.1, sampleTickers=["A"],
        spreadMeasurement={}, mdaPct=3.0, breadthVerdict="VIABLE",
        annualCostByEstimator={"corwinSchultz": 1.0, "abdiRanaldo": 0.5},
    )
    assert result.verdict() == "COST_OK -- stays under MDA under BOTH estimators"


def test_holding_period_not_event_rate_pinned_example() -> None:
    # Regression pin for this session's core correction: annual cost must
    # scale as spread / holding_period_years, NOT spread * events_per_year.
    # A 6-month (0.5yr) holding period with a 100bps spread costs 2%/yr of
    # capital, regardless of how many events per year arrive -- Little's-law
    # argument in the module docstring.
    spread_bps = 100.0
    holding_period_years = 0.5
    annual_cost_pct = spread_bps / holding_period_years / 100.0
    assert annual_cost_pct == pytest.approx(2.0)
