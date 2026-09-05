"""Forward-shadow ledger for Plain Momentum (C) vs Market-Residual Momentum
(D). These tests exercise the append-only/atomic/checkpoint infrastructure
in isolation from engine.mrm_attribution_ladder.compute_rungs() (a real
backtest) -- they never call it, only the pure ledger/scorecard functions
this module adds on top.
"""
from __future__ import annotations

from datetime import date

import pytest

from engine import plain_vs_residual_momentum_forward as fwd


# --- NAV append-only guards --------------------------------------------------


def test_nav_row_rejects_on_or_before_development_cutoff(tmp_path):
    path = tmp_path / "nav.json"
    with pytest.raises(ValueError, match="development cutoff"):
        fwd.append_nav_level_row(fwd.DEVELOPMENT_CUTOFF, {"c": 100, "d": 100, "spy": 100}, path)


def test_nav_row_rejects_duplicate_date(tmp_path):
    path = tmp_path / "nav.json"
    day = date(2026, 9, 2)
    fwd.append_nav_level_row(day, {"c": 100, "d": 100, "spy": 100}, path)
    with pytest.raises(ValueError, match="already recorded"):
        fwd.append_nav_level_row(day, {"c": 101, "d": 101, "spy": 101}, path)


def test_nav_row_rejects_out_of_order_append(tmp_path):
    path = tmp_path / "nav.json"
    fwd.append_nav_level_row(date(2026, 9, 3), {"c": 100, "d": 100, "spy": 100}, path)
    with pytest.raises(ValueError, match="chronologically"):
        fwd.append_nav_level_row(date(2026, 9, 2), {"c": 99, "d": 99, "spy": 99}, path)


def test_load_forward_nav_round_trips(tmp_path):
    path = tmp_path / "nav.json"
    fwd.append_nav_level_row(date(2026, 9, 2), {"c": 100.0, "d": 100.0, "spy": 100.0}, path)
    fwd.append_nav_level_row(date(2026, 9, 3), {"c": 101.0, "d": 99.5, "spy": 100.2}, path)
    df = fwd.load_forward_nav(path)
    assert len(df) == 2
    assert list(df["c"]) == [100.0, 101.0]


# --- rebalance-record append-only guards ------------------------------------


def _record(rebalance_date: str, c_return=1.0, d_return=0.5, regime="Bullish") -> fwd.RebalanceRecord:
    return fwd.RebalanceRecord(
        rebalanceDate=rebalance_date, regime=regime,
        cHoldings={"AAPL": 1.0}, dHoldings={"MSFT": 1.0},
        cPeriodReturnPct=c_return, dPeriodReturnPct=d_return,
        recordedAt="2026-09-02T12:00:00",
    )


def test_rebalance_record_rejects_on_or_before_cutoff(tmp_path):
    path = tmp_path / "rebalances.json"
    with pytest.raises(ValueError, match="development cutoff"):
        fwd.record_rebalance(_record("2026-08-31"), path)


def test_rebalance_record_rejects_duplicate_date(tmp_path):
    path = tmp_path / "rebalances.json"
    fwd.record_rebalance(_record("2026-09-02"), path)
    with pytest.raises(ValueError, match="already recorded"):
        fwd.record_rebalance(_record("2026-09-02"), path)


# --- amendments ---------------------------------------------------------------


def test_amendment_rejects_unconfirmed_status(tmp_path):
    path = tmp_path / "amendments.json"
    amendment = fwd.ForwardAmendment(
        amendmentId="a1", recordedAt="2026-09-02T00:00:00", reasonCode="test",
        explanation="test", evidence={}, replacements=[], status="pending",
    )
    with pytest.raises(ValueError, match="confirmed"):
        fwd.record_amendment(amendment, path)


def test_amendment_rejects_development_period_target(tmp_path):
    path = tmp_path / "amendments.json"
    amendment = fwd.ForwardAmendment(
        amendmentId="a1", recordedAt="2026-09-02T00:00:00", reasonCode="test",
        explanation="test", evidence={},
        replacements=[{"date": "2026-08-31", "series": "c", "priorValue": 100.0, "replacementValue": 101.0}],
    )
    with pytest.raises(ValueError, match="development-period"):
        fwd.record_amendment(amendment, path)


def test_amendment_rejects_unknown_series(tmp_path):
    path = tmp_path / "amendments.json"
    amendment = fwd.ForwardAmendment(
        amendmentId="a1", recordedAt="2026-09-02T00:00:00", reasonCode="test",
        explanation="test", evidence={},
        replacements=[{"date": "2026-09-02", "series": "bogus", "priorValue": 100.0, "replacementValue": 101.0}],
    )
    with pytest.raises(ValueError, match="Unknown NAV series"):
        fwd.record_amendment(amendment, path)


def test_effective_nav_applies_confirmed_amendment_without_touching_raw(tmp_path):
    nav_path, amend_path = tmp_path / "nav.json", tmp_path / "amend.json"
    fwd.append_nav_level_row(date(2026, 9, 2), {"c": 100.0, "d": 100.0, "spy": 100.0}, nav_path)
    amendment = fwd.ForwardAmendment(
        amendmentId="a1", recordedAt="2026-09-03T00:00:00", reasonCode="dividend_correction",
        explanation="test", evidence={},
        replacements=[{"date": "2026-09-02", "series": "c", "priorValue": 100.0, "replacementValue": 100.5}],
    )
    fwd.record_amendment(amendment, amend_path)
    raw = fwd.load_forward_nav(nav_path)
    effective = fwd.load_effective_forward_nav(nav_path, amend_path)
    assert raw.loc["2026-09-02", "c"] == 100.0  # raw untouched
    assert effective.loc["2026-09-02", "c"] == 100.5  # effective view corrected


def test_amendment_prior_value_guard_rejects_stale_replacement(tmp_path):
    nav_path, amend_path = tmp_path / "nav.json", tmp_path / "amend.json"
    fwd.append_nav_level_row(date(2026, 9, 2), {"c": 100.0, "d": 100.0, "spy": 100.0}, nav_path)
    amendment = fwd.ForwardAmendment(
        amendmentId="a1", recordedAt="2026-09-03T00:00:00", reasonCode="test",
        explanation="test", evidence={},
        # priorValue does not match what's actually stored (100.0)
        replacements=[{"date": "2026-09-02", "series": "c", "priorValue": 999.0, "replacementValue": 100.5}],
    )
    fwd.record_amendment(amendment, amend_path)
    with pytest.raises(RuntimeError, match="prior-value guard"):
        fwd.load_effective_forward_nav(nav_path, amend_path)


# --- checkpoint gating --------------------------------------------------------


def test_no_forward_observations_yet_is_pre_checkpoint():
    status = fwd.checkpoint_status(None)
    assert status["reached"] == []
    assert "No forward observations" in status["interpretationStatus"]


def test_before_six_months_is_observational_only():
    status = fwd.checkpoint_status(date(2026, 9, 1), today=date(2026, 11, 1))
    assert status["reached"] == []
    assert "OBSERVATIONAL ONLY" in status["interpretationStatus"]


def test_between_six_and_twelve_months_is_still_not_a_verdict():
    status = fwd.checkpoint_status(date(2026, 9, 1), today=date(2027, 4, 1))
    assert status["reached"] == [fwd.CHECKPOINTS_MONTHS[0][1]]
    assert "still observational only" in status["interpretationStatus"]


def test_at_twelve_months_is_eligible_for_formal_comparison():
    status = fwd.checkpoint_status(date(2026, 9, 1), today=date(2027, 9, 5))
    assert fwd.CHECKPOINTS_MONTHS[1][1] in status["reached"]
    assert "Eligible for formal comparison" in status["interpretationStatus"]


def test_at_twenty_four_months_all_checkpoints_reached():
    status = fwd.checkpoint_status(date(2026, 9, 1), today=date(2028, 9, 10))
    assert len(status["reached"]) == 3
    assert status["next"] is None


def test_checkpoint_dates_use_real_calendar_month_arithmetic():
    """Regression guard: the checkpoint DATE must land on the actual
    calendar day (first_session + N months), not an averaged-days
    approximation that could drift."""
    status = fwd.checkpoint_status(date(2026, 9, 1), today=date(2026, 9, 1))
    schedule = {row["months"]: row["date"] for row in status["schedule"]}
    assert schedule[6] == "2027-03-01"
    assert schedule[12] == "2027-09-01"
    assert schedule[24] == "2028-09-01"
    assert status["next"] == {"months": 6, "label": fwd.CHECKPOINTS_MONTHS[0][1], "date": "2027-03-01", "reached": False}


def test_schedule_marks_each_checkpoint_reached_independently():
    status = fwd.checkpoint_status(date(2026, 9, 1), today=date(2027, 9, 1))
    reached_by_months = {row["months"]: row["reached"] for row in status["schedule"]}
    assert reached_by_months == {6: True, 12: True, 24: False}


def test_no_observations_next_checkpoint_still_has_no_date():
    status = fwd.checkpoint_status(None)
    assert status["next"]["date"] is None


def test_no_observations_still_shows_the_preregistered_schedule():
    """Regression test: a viewer opening the card before the first forward
    session must still see all three preregistered checkpoints (dates
    unknown until there's a first session to anchor them), not an empty
    schedule that reads as if none were ever defined -- caught via a
    real browser screenshot showing no checkpoint boxes at zero
    observations."""
    status = fwd.checkpoint_status(None)
    assert len(status["schedule"]) == 3
    assert [row["months"] for row in status["schedule"]] == [6, 12, 24]
    assert all(row["date"] is None and row["reached"] is False for row in status["schedule"])


# --- scorecard: hit rate, regime breakdown, checkpoint gating --------------


def test_scorecard_with_no_observations_is_honest_about_it():
    import pandas as pd
    empty = pd.DataFrame(columns=["c", "d", "spy"])
    score = fwd.build_scorecard(empty, [])
    assert score["observations"] == 0
    assert score["status"] == "No Forward Observations Yet"


def test_hit_rate_and_paired_hit_rate_computed_from_rebalance_rows():
    rows = [
        {"cPeriodReturnPct": 1.0, "dPeriodReturnPct": 2.0, "regime": "Bullish"},   # d hit, c hit, paired: d wins
        {"cPeriodReturnPct": 1.0, "dPeriodReturnPct": -1.0, "regime": "Bearish"},  # c hit, d miss, paired: c wins
        {"cPeriodReturnPct": -2.0, "dPeriodReturnPct": -0.5, "regime": "Bearish"}, # both miss, paired: d wins
    ]
    import pandas as pd
    nav = pd.DataFrame(
        {"c": [100, 101, 102], "d": [100, 99, 98], "spy": [100, 100.5, 101]},
        index=pd.to_datetime(["2026-09-02", "2026-10-02", "2026-11-02"]),
    )
    score = fwd.build_scorecard(nav, rows)
    # Scorecard values are intentionally rounded to 1 decimal place.
    assert score["hitRates"]["cHitRatePct"] == pytest.approx(66.7, abs=0.05)  # 2 of 3 positive
    assert score["hitRates"]["dHitRatePct"] == pytest.approx(33.3, abs=0.05)  # 1 of 3 positive
    assert score["hitRates"]["pairedHitRatePct"] == pytest.approx(66.7, abs=0.05)  # d beats c 2 of 3
    assert score["regimeBreakdown"]["Bearish"]["periods"] == 2
    assert score["regimeBreakdown"]["Bullish"]["periods"] == 1


def test_scorecard_benchmark_relative_return_uses_spy_as_reference():
    import pandas as pd
    nav = pd.DataFrame(
        {"c": [100, 110], "d": [100, 105], "spy": [100, 103]},
        index=pd.to_datetime(["2026-09-02", "2026-10-02"]),
    )
    score = fwd.build_scorecard(nav, [])
    assert score["benchmarkRelative"]["cVsSpyReturnDiffPp"] == pytest.approx(10.0 - 3.0, rel=1e-6)
    assert score["benchmarkRelative"]["dVsSpyReturnDiffPp"] == pytest.approx(5.0 - 3.0, rel=1e-6)


def test_scorecard_paired_relative_is_d_minus_c():
    """Direct forward analogue of the historical ladder's
    incrementalCMinusD sign convention (D minus C)."""
    import pandas as pd
    nav = pd.DataFrame(
        {"c": [100, 110], "d": [100, 105], "spy": [100, 103]},
        index=pd.to_datetime(["2026-09-02", "2026-10-02"]),
    )
    score = fwd.build_scorecard(nav, [])
    assert score["pairedRelative"]["dMinusCReturnDiffPp"] == pytest.approx(5.0 - 10.0, rel=1e-6)
    assert score["pairedRelative"]["dMinusCMaxDrawdownDiffPp"] == pytest.approx(0.0, abs=1e-9)


def test_scorecard_withholds_sharpe_before_minimum_sessions():
    import pandas as pd
    import numpy as np
    idx = pd.bdate_range("2026-09-01", periods=50)
    rng = np.random.default_rng(0)
    nav = pd.DataFrame({
        "c": 100 * (1 + rng.normal(0.0003, 0.01, 50)).cumprod(),
        "d": 100 * (1 + rng.normal(0.0002, 0.009, 50)).cumprod(),
        "spy": 100 * (1 + rng.normal(0.0002, 0.008, 50)).cumprod(),
    }, index=idx)
    score = fwd.build_scorecard(nav, [])
    assert score["series"]["c"]["annualizedBlock"] == "Too early to evaluate"
    assert "sharpe" not in score["series"]["c"]


# --- forward_stack_status: amendment exposure, end to end -------------------


def test_forward_stack_status_exposes_confirmed_amendments(tmp_path):
    nav_path = tmp_path / "nav.json"
    rebalances_path = tmp_path / "rebalances.json"
    amendments_path = tmp_path / "amendments.json"
    operations_path = tmp_path / "operations.json"

    fwd.append_nav_level_row(date(2026, 9, 2), {"c": 100.0, "d": 100.0, "spy": 100.0}, nav_path)
    amendment = fwd.ForwardAmendment(
        amendmentId="a1", recordedAt="2026-09-03T00:00:00", reasonCode="dividend_correction",
        explanation="test correction", evidence={"source": "test"},
        replacements=[{"date": "2026-09-02", "series": "c", "priorValue": 100.0, "replacementValue": 100.3}],
    )
    fwd.record_amendment(amendment, amendments_path)

    status = fwd.forward_stack_status(
        nav_path=nav_path, rebalances_path=rebalances_path,
        amendments_path=amendments_path, operations_path=operations_path,
    )
    assert status["reconciliation"]["confirmedAmendments"] == 1
    assert status["reconciliation"]["amendments"][0]["amendmentId"] == "a1"
    assert status["reconciliation"]["rawNavPreserved"] is True


def test_forward_stack_status_with_no_amendments_reports_zero(tmp_path):
    status = fwd.forward_stack_status(
        nav_path=tmp_path / "nav.json", rebalances_path=tmp_path / "rebalances.json",
        amendments_path=tmp_path / "amendments.json", operations_path=tmp_path / "operations.json",
    )
    assert status["reconciliation"]["confirmedAmendments"] == 0
    assert status["reconciliation"]["amendments"] == []
