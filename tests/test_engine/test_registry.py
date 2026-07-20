import openpyxl

from strategies.registry import ALL_STRATEGY_NAMES

TRACKER_PATH = "strategy_tracker.xlsx"


def _tracker_strategy_names(sheet: str) -> list[str]:
    wb = openpyxl.load_workbook(TRACKER_PATH, data_only=True)
    ws = wb[sheet]
    names = []
    for row in ws.iter_rows(min_row=2, max_col=1, values_only=True):
        if row[0]:
            names.append(row[0])
    return names


def test_registry_matches_tracker_day_trading_names():
    tracker_names = _tracker_strategy_names("Day Trading")
    from strategies.registry import DAY_TRADING_STRATEGIES

    assert set(DAY_TRADING_STRATEGIES) == set(tracker_names)


def test_registry_matches_tracker_swing_trading_names():
    tracker_names = _tracker_strategy_names("Swing Trading")
    from strategies.registry import (
        AVWAP_BREAKOUT_NAME,
        CROSS_SECTIONAL_STRATEGY_NAMES,
        OVERNIGHT_NAME,
        PAIRS_STRATEGY_NAMES,
        PEAD_NAME,
        SECTOR_ROTATION_NAME,
        SWING_TRADING_STRATEGIES_NO_BENCHMARK,
    )

    # Not every tracker entry is a strategies.base.Strategy instance run
    # through the per-symbol engine -- cross-sectional, pairs, PEAD (real
    # earnings seeding), Overnight Hold (close->open engine), and Anchored
    # VWAP Breakout (real per-symbol earnings-gap anchors, same reason as
    # PEAD) live in separate name lists because they need different
    # construction/engines (see strategies/registry.py and LESSONS.md). The
    # tracker is still the single source of truth for the full set of
    # names; this unions all of them before comparing.
    registry_names = (
        set(SWING_TRADING_STRATEGIES_NO_BENCHMARK)
        | {SECTOR_ROTATION_NAME}
        | set(CROSS_SECTIONAL_STRATEGY_NAMES)
        | set(PAIRS_STRATEGY_NAMES)
        | {PEAD_NAME, OVERNIGHT_NAME, AVWAP_BREAKOUT_NAME}
    )
    assert registry_names == set(tracker_names)


def test_all_strategy_names_has_twenty_four_entries():
    assert len(ALL_STRATEGY_NAMES) == 24
    assert len(set(ALL_STRATEGY_NAMES)) == 24


def test_archived_strategy_names_are_a_subset_of_all_strategy_names():
    from strategies.registry import ARCHIVED_STRATEGY_NAMES

    assert set(ARCHIVED_STRATEGY_NAMES).issubset(set(ALL_STRATEGY_NAMES))
    # Every archived entry has a real (non-empty) reason -- this dict is a
    # user-facing record ("what we tried and why we stopped"), not a bare
    # exclusion list.
    assert all(a.reason.strip() for a in ARCHIVED_STRATEGY_NAMES.values())
