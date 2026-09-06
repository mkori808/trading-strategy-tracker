from __future__ import annotations

from engine.index_deletion_screen import (
    MAX_TRADABLE_ALPHA_PCT,
    MIN_USABLE_EVENTS,
    _classify_reason,
    _design_variants,
    _years_between,
)


def test_classify_reason_distinguishes_acquisition_from_market_cap_change() -> None:
    assert _classify_reason("Safeway Inc was acquired by AB Acquisition LLC.") == "acquired_or_merged"
    assert _classify_reason("Market capitalization change.") == "market_cap_change"
    assert _classify_reason("Honeywell completed the corporate spin-off of Honeywell Aerospace.") == "spinoff"
    assert _classify_reason("Company filed for bankruptcy protection.") == "bankruptcy"
    assert _classify_reason(None) == "other"
    assert _classify_reason("") == "other"


def test_years_between_matches_calendar_span() -> None:
    assert _years_between("2020-01-01", "2021-01-01") > 0.99
    assert _years_between("2020-01-01", "2021-01-01") < 1.01


def test_design_variants_every_variant_reports_the_same_tradable_alpha_threshold() -> None:
    variants = _design_variants(events_per_year=6.5, years=28.5, label_prefix="test")
    assert len(variants) == 3
    for variant in variants:
        assert variant["tradable_alpha_pct"] == MAX_TRADABLE_ALPHA_PCT


def test_design_variants_rho_couples_into_screen_designs_own_anchor() -> None:
    # Pinned, surprising behavior discovered while building this screen:
    # screen_design's avg_pairwise_corr also rescales its internal Dual
    # Momentum calibration anchor, not just the target design's own bet
    # count. For a positions=1 target (no cross-position correlation to
    # haircut), this means the rho=0 variant does NOT produce the lowest
    # MDA -- the rho=0.5 variant does, because a smaller anchor bet count
    # shrinks the whole MDA scale. If power_curve.screen_design is ever
    # changed to hold the anchor's correlation fixed independent of the
    # caller's avg_pairwise_corr, this test's ordering will flip and should
    # be updated, not silently deleted.
    variants = _design_variants(events_per_year=6.5, years=28.5, label_prefix="test")
    rho0_mda = variants[0]["mda_pct"]
    standard_corr_mda = variants[1]["mda_pct"]
    assert standard_corr_mda < rho0_mda


def test_low_event_rate_fails_both_hard_stop_thresholds_at_sp500_scale() -> None:
    # Pinned to the actual 2026-09-05 measurement (184 market_cap_change
    # removals over ~28.5 years) so a future refactor can't silently change
    # the screen's arithmetic without a test noticing. If Sharadar's sp500
    # table is re-fetched and this test starts failing because the event
    # count grew, that is real information -- update the pin, don't delete
    # the test.
    usable_events = 184
    years = 28.5
    events_per_year = usable_events / years
    variants = _design_variants(events_per_year, years, "pinned")
    best_case_mda = min(v["mda_pct"] for v in variants)

    assert usable_events >= MIN_USABLE_EVENTS  # event-count gate: PASSES
    assert best_case_mda > MAX_TRADABLE_ALPHA_PCT  # MDA gate: FAILS even optimistically
