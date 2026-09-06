from __future__ import annotations

from engine.hypothesis_backlog_screen import (
    MAX_TRADABLE_ALPHA_PCT,
    MIN_USABLE_EVENTS,
    ScreenResult,
    _classify_sp500_reason,
)


def test_classify_sp500_reason_matches_the_index_deletion_screens_taxonomy() -> None:
    # Same classification rule as engine/index_deletion_screen.py, applied
    # here to BOTH added and removed rows -- must stay consistent so an
    # addition and its paired removal are never classified differently.
    assert _classify_sp500_reason("Safeway Inc was acquired by AB Acquisition LLC.") == "acquired_or_merged"
    assert _classify_sp500_reason("Market capitalization change.") == "market_cap_change"
    assert _classify_sp500_reason("Honeywell completed the corporate spin-off of Honeywell Aerospace.") == "spinoff"
    assert _classify_sp500_reason(None) == "other"


def test_verdict_thresholds_match_the_documented_ceiling() -> None:
    viable = ScreenResult(
        candidate="t", barrier="MANDATE", observation_definition="Event: x",
        raw_count="200", usable_count="200", events_or_bets_per_year=10.0, years=10.0,
        independence_note="", designs=[{"label": "a", "mda_pct": MAX_TRADABLE_ALPHA_PCT, "independent_bets_per_year": 1.0}],
    )
    assert viable.verdict() == "VIABLE"

    marginal = ScreenResult(
        candidate="t", barrier="MANDATE", observation_definition="Event: x",
        raw_count="200", usable_count="200", events_or_bets_per_year=10.0, years=10.0,
        independence_note="", designs=[{"label": "a", "mda_pct": MAX_TRADABLE_ALPHA_PCT * 1.5, "independent_bets_per_year": 1.0}],
    )
    assert marginal.verdict() == "MARGINAL"

    dead = ScreenResult(
        candidate="t", barrier="MANDATE", observation_definition="Event: x",
        raw_count="200", usable_count="200", events_or_bets_per_year=10.0, years=10.0,
        independence_note="", designs=[{"label": "a", "mda_pct": MAX_TRADABLE_ALPHA_PCT * 3, "independent_bets_per_year": 1.0}],
    )
    assert dead.verdict() == "DEAD"

    blocked = ScreenResult(
        candidate="t", barrier="OPERATIONS", observation_definition="Event: x",
        raw_count="0", usable_count="0", events_or_bets_per_year=0.0, years=10.0,
        independence_note="", data_blocked=True, data_blocked_reason="no data",
    )
    assert blocked.verdict() == "DATA_BLOCKED"


def test_best_case_mda_takes_the_minimum_across_reported_designs() -> None:
    result = ScreenResult(
        candidate="t", barrier="MANDATE", observation_definition="Event: x",
        raw_count="200", usable_count="200", events_or_bets_per_year=10.0, years=10.0,
        independence_note="",
        designs=[
            {"label": "a", "mda_pct": 9.0, "independent_bets_per_year": 1.0},
            {"label": "b", "mda_pct": 3.0, "independent_bets_per_year": 1.0},
        ],
    )
    assert result.best_case_mda_pct == 3.0


def test_dead_cause_distinguishes_event_count_floor_from_mda_only_failure() -> None:
    fails_count = ScreenResult(
        candidate="t", barrier="MANDATE", observation_definition="Event: x",
        raw_count="50", usable_count="50", events_or_bets_per_year=5.0, years=10.0,
        independence_note="", designs=[{"label": "a", "mda_pct": 20.0, "independent_bets_per_year": 1.0}],
    )
    assert fails_count.usable_count_floor_applicable is True
    assert "event-count floor" in fails_count.dead_cause()

    clears_count = ScreenResult(
        candidate="t", barrier="MANDATE", observation_definition="Event: x",
        raw_count=str(MIN_USABLE_EVENTS + 1), usable_count=str(MIN_USABLE_EVENTS + 1),
        events_or_bets_per_year=5.0, years=10.0,
        independence_note="", designs=[{"label": "a", "mda_pct": 20.0, "independent_bets_per_year": 1.0}],
    )
    assert "clears the event-count floor" in clears_count.dead_cause()

    cross_sectional = ScreenResult(
        candidate="t", barrier="CAPACITY", observation_definition="Cross-sectional: x",
        raw_count="50", usable_count="50", events_or_bets_per_year=5.0, years=10.0,
        independence_note="", designs=[{"label": "a", "mda_pct": 20.0, "independent_bets_per_year": 1.0}],
    )
    assert cross_sectional.usable_count_floor_applicable is False
