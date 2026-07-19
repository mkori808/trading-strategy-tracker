"""engine/compare_sector_rotation_exits.py: the PeriodicExitStrategy exit-
cadence gate and the shortlist/baseline-reproduction gates, tested in
isolation with a fake inner strategy (no real bars/data needed) -- see
engine/compare_sector_rotation_exits.py's module docstring for why this
test exists (Sector Rotation Play's registered exit is signal-based, not a
top-N rebalance, so what's under test here is purely the cadence wrapper)."""

from __future__ import annotations

import pandas as pd
import pytest

from engine.compare_sector_rotation_exits import (
    BASELINE_EXPECTANCY_R,
    BASELINE_TRADES_TAKEN,
    BASELINE_WIN_RATE,
    PeriodicExitStrategy,
    _baseline_reproduces,
    _clears_shortlist,
)
from strategies.base import Strategy


class _FakeStrategy(Strategy):
    name = "Fake"
    timeframe = "1d"
    direction = "long"

    def __init__(self, entry_at: set[int], exit_at: set[int]):
        self.entry_at = entry_at
        self.exit_at = exit_at

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        return len(bars) in self.entry_at

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return entry_price - 1.0

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        return len(bars) in self.exit_at


def _simulate(strategy: Strategy, n_bars: int) -> list[tuple[int, str]]:
    """Mirrors engine/backtest.py's adapter loop closely enough for this
    test: entry_signal only checked while flat, exit_signal only checked
    while in a position, bars grow by one row per step."""
    in_position = False
    events: list[tuple[int, str]] = []
    for length in range(1, n_bars + 1):
        bars = pd.DataFrame(index=range(length))
        if in_position:
            if strategy.exit_signal(bars):
                events.append((length, "exit"))
                in_position = False
        else:
            if strategy.entry_signal(bars):
                events.append((length, "enter"))
                in_position = True
    return events


def test_check_every_days_1_min_hold_0_is_pass_through():
    inner = _FakeStrategy(entry_at={3}, exit_at={7})
    wrapped = PeriodicExitStrategy(inner, check_every_days=1, min_hold_days=0)
    assert _simulate(wrapped, 10) == [(3, "enter"), (7, "exit")]


def test_min_hold_days_blocks_early_exit():
    inner = _FakeStrategy(entry_at={3}, exit_at=set(range(4, 30)))
    wrapped = PeriodicExitStrategy(inner, check_every_days=1, min_hold_days=5)
    events = _simulate(wrapped, 30)
    # held = length - 3; first length with held >= 5 is length 8.
    assert events == [(3, "enter"), (8, "exit")]


def test_check_every_days_gates_to_cadence_aligned_bars():
    inner = _FakeStrategy(entry_at={3}, exit_at=set(range(4, 60)))
    wrapped = PeriodicExitStrategy(inner, check_every_days=21, min_hold_days=0)
    events = _simulate(wrapped, 60)
    # held = length - 3; first held multiple of 21 (with held >= 1) is 21 -> length 24.
    assert events == [(3, "enter"), (24, "exit")]


def test_second_trade_cadence_resets_from_its_own_entry():
    inner = _FakeStrategy(entry_at={3, 30}, exit_at=set(range(4, 60)))
    wrapped = PeriodicExitStrategy(inner, check_every_days=21, min_hold_days=0)
    events = _simulate(wrapped, 60)
    # trade 1: enter@3, exit at held=21 -> length 24
    # trade 2: enter@30 (first entry_at bar after being flat again), exit at
    # held=21 relative to 30 -> length 51 -- NOT computed relative to trade 1.
    assert events == [(3, "enter"), (24, "exit"), (30, "enter"), (51, "exit")]


def test_entry_signal_that_never_fills_does_not_corrupt_next_real_entry_cadence():
    # entry_signal fires at 3 AND 4 (simulating a bar where the real adapter
    # skipped placing the order after signal=True, e.g. a bracket-price
    # sanity check) -- the wrapper isn't told about that skip, but the LAST
    # True before the real fill is always the right stamp (see docstring).
    inner = _FakeStrategy(entry_at={3, 4}, exit_at={25})
    wrapped = PeriodicExitStrategy(inner, check_every_days=21, min_hold_days=0)
    events = _simulate(wrapped, 30)
    # Only one "enter" event is possible here since _simulate flips
    # in_position on the first True and stops checking entry_signal --
    # confirms the stamp used (len=3) still gates correctly (held=25-3=22,
    # not a multiple of 21) and no exit fires early.
    assert events == [(3, "enter")]


@pytest.fixture
def baseline_row():
    return {
        "trades_taken": BASELINE_TRADES_TAKEN,
        "win_rate": BASELINE_WIN_RATE,
        "expectancy_r": BASELINE_EXPECTANCY_R,
    }


def test_baseline_reproduces_accepts_exact_match(baseline_row):
    assert _baseline_reproduces(baseline_row) == []


def test_baseline_reproduces_flags_trade_count_mismatch(baseline_row):
    baseline_row["trades_taken"] = BASELINE_TRADES_TAKEN + 1
    problems = _baseline_reproduces(baseline_row)
    assert len(problems) == 1
    assert "trades_taken" in problems[0]


def test_baseline_reproduces_flags_expectancy_drift(baseline_row):
    baseline_row["expectancy_r"] = BASELINE_EXPECTANCY_R + 0.01
    problems = _baseline_reproduces(baseline_row)
    assert any("expectancy_r" in p for p in problems)


def _row(**overrides):
    row = {
        "sharpe": 0.6,
        "expectancy_r": 0.15,
        "trades_taken": 40,
        "exit_efficiency_pct": 80.0,
    }
    row.update(overrides)
    return row


def test_clears_shortlist_requires_all_four_conditions():
    assert _clears_shortlist(_row()) is True
    assert _clears_shortlist(_row(sharpe=-0.1)) is False
    assert _clears_shortlist(_row(expectancy_r=0.05)) is False
    assert _clears_shortlist(_row(trades_taken=20)) is False
    assert _clears_shortlist(_row(exit_efficiency_pct=60.0)) is False
    assert _clears_shortlist(_row(exit_efficiency_pct=None)) is False
    assert _clears_shortlist(_row(sharpe=None)) is False
