"""engine/avwap.py: AVWAP calculation verified against hand-computed
values (per CLAUDE.md's sanity-check requirement -- "compute AVWAP
manually... confirm the engine output matches, stop and fix if not"),
plus the earnings-gap and swing-low anchor selection rules."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from engine.avwap import compute_avwap, earnings_gap_anchors, swing_low_anchors


def test_avwap_matches_hand_calculation_equal_volume(daily_bars_factory):
    # H+L+C chosen so typical price is a clean integer each bar; equal
    # volume every bar makes AVWAP a plain running mean of typical price,
    # computed by hand below and compared to the engine's output.
    bars = daily_bars_factory(
        closes=[102, 105, 108, 111, 114],
        highs=[105, 108, 111, 114, 117],
        lows=[99, 102, 105, 108, 111],
        volumes=[1000, 1000, 1000, 1000, 1000],
    )
    anchor = bars.index[0]
    result = compute_avwap(bars, anchor)

    typical_prices = [102, 105, 108, 111, 114]
    expected_avwap = [
        sum(typical_prices[: i + 1]) / (i + 1) for i in range(len(typical_prices))
    ]
    for i, expected in enumerate(expected_avwap):
        assert result["avwap"].iloc[i] == pytest.approx(expected, abs=1e-9)

    # Hand variance at the last bar: mean(tp^2) - mean(tp)^2 = 11682 - 108^2 = 18
    assert result["std"].iloc[-1] == pytest.approx(math.sqrt(18), abs=1e-9)
    assert result["upper_1"].iloc[-1] == pytest.approx(108 + math.sqrt(18), abs=1e-9)
    assert result["lower_2"].iloc[-1] == pytest.approx(108 - 2 * math.sqrt(18), abs=1e-9)


def test_avwap_matches_hand_calculation_unequal_volume(daily_bars_factory):
    # tp=100 (V=100), tp=200 (V=100), tp=100 (V=300):
    # avwap = [100, 150, 120] by hand (volume-weighted, not a plain mean).
    bars = daily_bars_factory(
        closes=[100, 200, 100],
        highs=[110, 220, 110],
        lows=[90, 180, 90],
        volumes=[100, 100, 300],
    )
    anchor = bars.index[0]
    result = compute_avwap(bars, anchor)
    assert result["avwap"].tolist() == pytest.approx([100.0, 150.0, 120.0], abs=1e-9)


def test_avwap_anchor_not_in_bars_returns_empty(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 101, 102])
    result = compute_avwap(bars, pd.Timestamp("2099-01-01", tz=bars.index.tz))
    assert result.empty


def test_avwap_starts_only_from_anchor_forward(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 200, 300, 400], volumes=[1, 1, 1, 1])
    anchor = bars.index[2]
    result = compute_avwap(bars, anchor)
    assert len(result) == 2
    assert result.index[0] == anchor


def test_earnings_gap_anchor_qualifies_on_gap_and_volume(daily_bars_factory):
    n = 25
    closes = [100.0] * n
    opens = [100.0] * n
    volumes = [1000.0] * n
    # Bar `n-1` (the reaction session) gaps up >3% on >1.5x the trailing
    # 20-day average volume computed from bars strictly before it.
    opens[-1] = 104.0  # prior close 100 -> +4% gap
    volumes[-1] = 2000.0
    bars = daily_bars_factory(closes=closes, opens=opens, volumes=volumes)
    earnings_date = bars.index[-1].date()

    anchors = earnings_gap_anchors(bars, [earnings_date])
    assert anchors == [bars.index[-1]]


def test_earnings_gap_anchor_rejected_below_gap_threshold(daily_bars_factory):
    n = 25
    closes = [100.0] * n
    opens = [100.0] * n
    volumes = [1000.0] * n
    opens[-1] = 101.5  # +1.5% gap, below the 3% threshold
    volumes[-1] = 2000.0
    bars = daily_bars_factory(closes=closes, opens=opens, volumes=volumes)
    earnings_date = bars.index[-1].date()

    assert earnings_gap_anchors(bars, [earnings_date]) == []


def test_earnings_gap_anchor_rejected_without_volume_confirmation(daily_bars_factory):
    n = 25
    closes = [100.0] * n
    opens = [100.0] * n
    volumes = [1000.0] * n
    opens[-1] = 104.0  # gap qualifies
    volumes[-1] = 1100.0  # but volume doesn't clear 1.5x avg
    bars = daily_bars_factory(closes=closes, opens=opens, volumes=volumes)
    earnings_date = bars.index[-1].date()

    assert earnings_gap_anchors(bars, [earnings_date]) == []


def test_earnings_gap_anchor_excludes_gap_down(daily_bars_factory):
    n = 25
    closes = [100.0] * n
    opens = [100.0] * n
    volumes = [1000.0] * n
    opens[-1] = 96.0  # -4% gap DOWN -- long-only implementation excludes this
    volumes[-1] = 2000.0
    bars = daily_bars_factory(closes=closes, opens=opens, volumes=volumes)
    earnings_date = bars.index[-1].date()

    assert earnings_gap_anchors(bars, [earnings_date]) == []


def test_swing_low_anchor_requires_confirmed_local_low_and_decline(daily_bars_factory):
    n = 10
    # Prior high of 100 (bars 0..9), decline to a low of 80 (>=15%) at bar
    # 10, confirmed by 10 bars of higher lows on both sides.
    lows = list(range(90, 100)) + [80] + list(range(85, 95))
    highs = [low + 20 for low in lows]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    bars = daily_bars_factory(closes=closes, highs=highs, lows=lows)

    anchors = swing_low_anchors(bars, n=10, min_decline_pct=0.15)
    assert bars.index[10] in anchors


def test_swing_low_anchor_rejected_below_decline_threshold(daily_bars_factory):
    n = 10
    # Only a shallow ~7% dip (prior high ~102 vs. low of 95) -- a confirmed
    # local low, but doesn't clear the 15% decline requirement.
    lows = [100] * n + [95] + [100] * n
    highs = [low + 2 for low in lows]
    closes = [(h + l) / 2 for h, l in zip(highs, lows)]
    bars = daily_bars_factory(closes=closes, highs=highs, lows=lows)

    anchors = swing_low_anchors(bars, n=10, min_decline_pct=0.15)
    assert bars.index[10] not in anchors
