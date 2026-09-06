from __future__ import annotations

import pytest

from engine.edge_candidate_triage import (
    COST_SENSITIVE,
    EdgeCandidate,
    annual_cost_from_holding_period,
    build_report,
    classify_breadth,
    classify_cost,
)


def test_holding_period_cost_does_not_multiply_by_event_count() -> None:
    # 20 bps paid once every half year is 40 bps/yr, regardless of whether
    # the market produces ten or ten thousand events for parallel slots.
    assert annual_cost_from_holding_period(20.0, 0.5) == pytest.approx(0.4)


def test_exact_zero_spread_is_below_resolution_not_literal_cost_ok() -> None:
    assert classify_cost(2.8, 0.0, effect_ceiling_pct=3.12) == COST_SENSITIVE


def test_breadth_status_uses_mda_not_nominal_cross_section_label() -> None:
    assert classify_breadth(3.99) == "VIABLE"
    assert classify_breadth(5.0) == "MARGINAL"
    assert classify_breadth(9.0) == "SCREENED_OUT"


def test_partial_observability_fails_closed() -> None:
    candidate = EdgeCandidate(
        id="partial",
        name="Partial candidate",
        family="test",
        origin="test",
        mechanism_tier=1,
        mechanism="mechanism",
        constrained_actor="actor",
        constraint="constraint",
        persistence_reason="reason",
        trigger="trigger",
        expected_distortion="distortion",
        required_data=["missing PIT field"],
        pit_observability="PARTIAL",
        observability_notes="not complete",
        final_triage_status="NEEDS_HUMAN_REVIEW",
    )
    with pytest.raises(ValueError, match="fail closed"):
        candidate.validate()


def test_report_never_promotes_or_consumes_research_evidence() -> None:
    report = build_report()
    assert report["backtestsRun"] is False
    assert report["holdoutConsumed"] is False
    assert report["preregistrationsCreated"] is False
    assert report["strongCandidates"] == []
    assert all(row["final_triage_status"] != "APPROVED_FOR_PREREGISTRATION" for row in report["candidates"])


def test_new_behavioral_candidate_carries_factor_warning() -> None:
    report = build_report()
    ex_dividend = next(row for row in report["candidates"] if row["id"] == "ex_dividend_clientele")
    assert ex_dividend["factor_explanation_risk"] is True
    assert ex_dividend["effective_bets_per_year"] < ex_dividend["nominal_events_per_year"]
    assert ex_dividend["cost_status"] == COST_SENSITIVE


def test_common_batch_rows_do_not_count_as_independent_events() -> None:
    report = build_report()
    equal_weight = next(row for row in report["candidates"] if row["id"] == "sp500_equal_weight_rebalance")
    assert equal_weight["nominal_events_per_year"] == 2000
    assert equal_weight["effective_bets_per_year"] == 4
    assert equal_weight["final_triage_status"] == "SCREENED_OUT_BREADTH"
