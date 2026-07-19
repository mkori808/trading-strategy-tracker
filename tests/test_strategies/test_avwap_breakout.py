"""strategies/swing/avwap_breakout.py: entry (first-cross-after-N-bars-below
+ volume confirmation), stop, and exit (signal cross-below / time stop)
rules, tested in isolation with synthetic bars -- see
strategies/swing/avwap_breakout.py's module docstring for the locked
interpretation of the entry rule and the entry_bar/exit_bar logging
convention this test also checks."""

from __future__ import annotations

import pandas as pd
import pytest

from strategies.swing.avwap_breakout import AvwapBreakout


def _entry_ready_bars(daily_bars_factory, tail_closes: list[float] | None = None):
    """Anchor at bar 0 (tp=100 via H=C+1/L=C-1, so typical price == close);
    22 bars at C=99 (always below the ~99.x AVWAP, so "below" holds for way
    more than the required 5-bar run); a cross bar at position 23 (C=115,
    volume=2000, well above 1.5x the 1000-volume baseline) that should fire
    entry. `tail_closes` appends additional bars after the cross, all at
    volume=1000, for exit-rule tests."""
    closes = [100.0] + [99.0] * 22 + [115.0] + list(tail_closes or [])
    n = len(closes)
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    volumes = [1000.0] * 23 + [2000.0] + [1000.0] * len(tail_closes or [])
    bars = daily_bars_factory(closes=closes, highs=highs, lows=lows, volumes=volumes)
    anchor_date = bars.index[0].date()
    return bars, anchor_date


def test_no_entry_without_a_qualifying_anchor():
    strategy = AvwapBreakout(anchor_dates=[])
    bars = pd.DataFrame({
        "Open": [100] * 10, "High": [101] * 10, "Low": [99] * 10,
        "Close": [100] * 10, "Volume": [1000] * 10,
    }, index=pd.bdate_range("2024-01-02", periods=10, tz="America/New_York"))
    assert not strategy.entry_signal(bars)


def test_no_entry_on_anchor_bar_or_bar_immediately_after(daily_bars_factory):
    bars, anchor_date = _entry_ready_bars(daily_bars_factory)
    strategy = AvwapBreakout(anchor_dates=[anchor_date])
    assert not strategy.entry_signal(bars.iloc[:1])  # anchor bar itself
    assert not strategy.entry_signal(bars.iloc[:2])  # one bar after


def test_entry_fires_on_genuine_cross_with_volume_confirmation(daily_bars_factory):
    bars, anchor_date = _entry_ready_bars(daily_bars_factory)
    strategy = AvwapBreakout(anchor_dates=[anchor_date])
    signal_bars = bars.iloc[:24]  # through the cross bar, position 23
    assert strategy.entry_signal(signal_bars)
    assert strategy.entry_log == [{
        "entry_bar": 24,
        "anchor": pd.Timestamp(anchor_date, tz=bars.index.tz),
        "avwap_at_signal": pytest.approx(strategy.entry_log[0]["avwap_at_signal"]),
    }]


def test_no_entry_without_volume_confirmation(daily_bars_factory):
    closes = [100.0] + [99.0] * 22 + [115.0]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    volumes = [1000.0] * 23 + [1400.0]  # only 1.4x -- below the 1.5x bar
    bars = daily_bars_factory(closes=closes, highs=highs, lows=lows, volumes=volumes)
    strategy = AvwapBreakout(anchor_dates=[bars.index[0].date()])
    assert not strategy.entry_signal(bars)


def test_no_entry_without_five_consecutive_bars_below_avwap(daily_bars_factory):
    # Only 2 bars below AVWAP before the "cross" -- shouldn't qualify.
    closes = [100.0, 99.0, 99.0, 115.0]
    highs = [c + 1 for c in closes]
    lows = [c - 1 for c in closes]
    volumes = [1000.0, 1000.0, 1000.0, 2000.0]
    bars = daily_bars_factory(closes=closes, highs=highs, lows=lows, volumes=volumes)
    strategy = AvwapBreakout(anchor_dates=[bars.index[0].date()], min_bars_below_avwap=5)
    assert not strategy.entry_signal(bars)


def test_stop_price_is_hard_stop_pct_below_entry():
    strategy = AvwapBreakout(anchor_dates=[], hard_stop_pct=0.08)
    assert strategy.stop_price(pd.DataFrame(), 100.0) == pytest.approx(92.0)


def test_exit_signal_fires_on_avwap_cross_below(daily_bars_factory):
    # tail bar at position 24 (the fill bar) stays at 115 -- still above
    # AVWAP, shouldn't exit; position 25 drops to 90, well below AVWAP.
    bars, anchor_date = _entry_ready_bars(daily_bars_factory, tail_closes=[115.0, 90.0])
    strategy = AvwapBreakout(anchor_dates=[anchor_date])
    assert strategy.entry_signal(bars.iloc[:24])
    assert not strategy.exit_signal(bars.iloc[:25])
    assert strategy.exit_signal(bars.iloc[:26])
    assert strategy.exit_log == [{"entry_bar": 24, "exit_bar": 26, "reason": "signal_exit"}]


def test_exit_signal_fires_on_time_stop(daily_bars_factory):
    # Price stays well above AVWAP indefinitely -- only the time stop can
    # close this position. 90 business days is comfortably past 60 calendar
    # days (business days undercounts weekends, so this is a safe margin).
    tail = [115.0] * 90
    bars, anchor_date = _entry_ready_bars(daily_bars_factory, tail_closes=tail)
    strategy = AvwapBreakout(anchor_dates=[anchor_date], time_stop_days=60)
    assert strategy.entry_signal(bars.iloc[:24])
    assert not strategy.exit_signal(bars.iloc[:25])  # fill bar, held=0

    fired = False
    for end in range(25, len(bars)):
        window = bars.iloc[:end + 1]
        if strategy.exit_signal(window):
            fired = True
            assert strategy.exit_log[-1]["reason"] == "time_stop"
            held_days = (window.index[-1] - bars.index[24]).days
            assert held_days >= 60
            break
    assert fired, "time stop never fired within the test window"
