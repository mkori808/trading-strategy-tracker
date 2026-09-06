from datetime import date

from engine.triage_sp500_pit_free_missing_prices import (
    aggregate_coverage_by_year,
    classify_ticker,
    concentration_ranking,
    same_day_removal_partners,
    smallest_set_for_share,
    survivorship_bias_check,
)


MEMBERSHIP_AUDIT = {
    "primarySource": {"coverageEnd": "2023-03-20"},
    "secondarySource": {"coverageEnd": "2025-05-17"},
}
LEDGER_END = date(2025, 5, 17)


def _price_row(status="MISSING_DELISTED_PRICE_HISTORY"):
    return {
        "status": status,
        "requested_start": "1996-01-02",
        "requested_end": "2025-05-17",
    }


def test_a_tenure_ending_exactly_at_ledger_end_is_still_open_not_removed():
    """Regression test: engine.build_sp500_pit_free_membership writes a
    concrete end_date (never None) for a tenure that survives the
    primary/secondary source handoff and is still current as of the
    ledger's own coverage end. Treating that concrete date as a real
    removal mislabeled BK, HES, IPG, K, MMC (all real, currently-active
    securities) as delisted/bankrupt in an earlier version of this module.
    """
    entry = {"reasons": set(), "tenures": [(date(1996, 1, 2), LEDGER_END)]}
    category, evidence = classify_ticker(
        "BK", _price_row(), entry, {}, {}, MEMBERSHIP_AUDIT, None,
    )
    assert category == "provider_coverage_gap"
    assert "currently-held" in evidence


def test_a_tenure_that_actually_ended_before_ledger_end_is_not_still_open():
    entry = {"reasons": set(), "tenures": [(date(1996, 1, 2), date(2008, 9, 17))]}
    category, _ = classify_ticker(
        "LEHMQ", _price_row(), entry, {}, {}, MEMBERSHIP_AUDIT, None,
    )
    assert category != "provider_coverage_gap"


def test_a_genuinely_open_none_ended_tenure_is_also_still_open():
    entry = {"reasons": set(), "tenures": [(date(1996, 1, 2), None)]}
    category, _ = classify_ticker(
        "AAPL", _price_row(), entry, {}, {}, MEMBERSHIP_AUDIT, None,
    )
    assert category == "provider_coverage_gap"


def test_legacy_suffix_candidate_with_data_is_never_treated_as_resolved():
    """Regression test: a stripped-Q candidate having fetchable data must
    never be classified as resolved -- data existing does not prove company
    identity (the CPQ -> Canadian Pacific Railway counterexample)."""
    entry = {"reasons": {"legacy_suffix_needs_verification"}, "tenures": [(date(1996, 1, 2), date(2002, 5, 5))]}
    resolution = {
        "candidate": "CP", "dataFound": True, "identityVerified": False,
        "availableStart": "1995-03-08", "availableEnd": "2002-05-03",
        "reason": "candidate identity is NOT confirmed",
    }
    category, evidence = classify_ticker(
        "CPQ", _price_row(), entry, {}, {}, MEMBERSHIP_AUDIT, resolution,
    )
    assert category == "symbol_mapping_candidate_found_identity_unverifiable"
    assert "NOT confirmed" in evidence


def test_legacy_suffix_candidate_without_data_is_reported_distinctly():
    entry = {"reasons": {"legacy_suffix_needs_verification"}, "tenures": [(date(1996, 1, 2), date(2008, 9, 17))]}
    resolution = {"candidate": "LEHM", "dataFound": False, "identityVerified": False, "reason": "no data"}
    category, _ = classify_ticker(
        "LEHMQ", _price_row(), entry, {}, {}, MEMBERSHIP_AUDIT, resolution,
    )
    assert category == "symbol_mapping_attempted_no_candidate_data"


def test_membership_defect_candidate_flags_primary_boundary_exactly():
    entry = {"reasons": set(), "tenures": [(date(2010, 1, 1), date(2023, 3, 20))]}
    category, evidence = classify_ticker(
        "XYZ", _price_row(), entry, {}, {}, MEMBERSHIP_AUDIT, None,
    )
    assert category == "membership_defect_candidate"
    assert "2023-03-20" in evidence


def test_same_day_swap_is_only_detected_for_a_clean_one_for_one_substitution():
    rows = [
        {"effective_date": "2020-06-09", "ticker": "OLD", "action": "REMOVE"},
        {"effective_date": "2020-06-09", "ticker": "NEW", "action": "ADD"},
        {"effective_date": "2021-01-01", "ticker": "A", "action": "REMOVE"},
        {"effective_date": "2021-01-01", "ticker": "B", "action": "REMOVE"},
        {"effective_date": "2021-01-01", "ticker": "C", "action": "ADD"},
    ]
    partners = same_day_removal_partners(rows)
    assert partners[("OLD", "2020-06-09")] == "NEW"
    # Two removals against one addition on the same date is not a clean 1:1
    # swap and must not be reported as one.
    assert ("A", "2021-01-01") not in partners
    assert ("B", "2021-01-01") not in partners


def test_merger_classification_requires_partner_to_have_fetchable_price_history():
    entry = {"reasons": set(), "tenures": [(date(2010, 1, 1), date(2020, 6, 9))]}
    swap_partners = {("OLD", "2020-06-09"): "NEW"}
    price_coverage = {"NEW": {"status": "OK"}}
    category, evidence = classify_ticker(
        "OLD", _price_row(), entry, swap_partners, price_coverage, MEMBERSHIP_AUDIT, None,
    )
    assert category == "ticker_change_merger_or_acquisition_likely"
    assert "NEW" in evidence

    price_coverage_unfetchable = {"NEW": {"status": "MISSING_DELISTED_PRICE_HISTORY"}}
    category2, _ = classify_ticker(
        "OLD", _price_row(), entry, swap_partners, price_coverage_unfetchable, MEMBERSHIP_AUDIT, None,
    )
    assert category2 != "ticker_change_merger_or_acquisition_likely"


def test_concentration_ranking_and_smallest_set_for_share():
    required = {
        "A": [date(2020, 1, 1), date(2020, 2, 1), date(2020, 3, 1)],
        "B": [date(2020, 1, 1)],
        "C": [date(2020, 1, 1)],
    }
    ranking = concentration_ranking(required)
    assert ranking[0]["ticker"] == "A"
    assert ranking[0]["missingRebalanceDates"] == 3
    assert ranking[0]["cumulativeSharePct"] == 60.0

    smallest = smallest_set_for_share(ranking, 60.0)
    assert smallest == ["A"]


def test_survivorship_bias_check_flags_disproportionate_removed_share():
    price_coverage = {
        "OK1": {"status": "OK"}, "OK2": {"status": "OK"},
        "MISS_REMOVED": {"status": "MISSING_DELISTED_PRICE_HISTORY"},
        "MISS_ACTIVE": {"status": "MISSING_DELISTED_PRICE_HISTORY"},
    }
    symbol_map = {
        "OK1": {"tenures": [(date(1996, 1, 1), None)]},
        "OK2": {"tenures": [(date(1996, 1, 1), date(2010, 1, 1))]},
        "MISS_REMOVED": {"tenures": [(date(1996, 1, 1), date(2005, 1, 1))]},
        "MISS_ACTIVE": {"tenures": [(date(1996, 1, 1), LEDGER_END)]},
    }
    result = survivorship_bias_check(price_coverage, symbol_map, LEDGER_END)
    assert result["missingTickers"] == {"removed": 1, "active": 1}
    assert result["coveredTickers"] == {"removed": 1, "active": 1}
    assert result["percentagePointGap"] == 0.0
    assert result["verdict"].startswith("NO_STRONG_DIRECTIONAL_SKEW")


def test_aggregate_coverage_by_year_weights_by_member_observations():
    rows = [
        {"date": "2020-01-01", "pit_members": 100, "with_valid_prices": 90, "coverage_pct": 90.0},
        {"date": "2020-02-01", "pit_members": 200, "with_valid_prices": 100, "coverage_pct": 50.0},
    ]
    annual = aggregate_coverage_by_year(rows)
    assert len(annual) == 1
    assert annual[0]["year"] == 2020
    assert annual[0]["pit_member_observations"] == 300
    assert annual[0]["with_valid_price_observations"] == 190
    assert annual[0]["coverage_pct"] == round(190 / 300 * 100, 2)
    assert annual[0]["minimum_rebalance_date_coverage_pct"] == 50.0
    assert annual[0]["maximum_rebalance_date_coverage_pct"] == 90.0
