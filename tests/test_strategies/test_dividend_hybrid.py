"""Dividend Hybrid screen and entry-trigger rules, in isolation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.fundamentals import FundamentalSnapshot
from strategies.swing.dividend_hybrid import (
    TRIGGER_INTRADAY_PROXY,
    TRIGGER_SPEC,
    DividendHybrid,
    adr_pct,
    entry_trigger,
    point_in_time_fundamental_screen,
    snapshot_fundamental_screen,
    technical_screen,
)

CONFIG = DividendHybrid()


def _snapshot(**overrides) -> FundamentalSnapshot:
    """A snapshot that passes every criterion unless overridden."""
    base = dict(
        symbol="X", market_cap=50e9, payout_ratio_pct=50.0, eps_growth_yoy_pct=5.0,
        revenue_growth_yoy_pct=3.0, trailing_pe=12.0, analyst_rating=1.8,
        analyst_target_price=200.0, current_price=100.0, fetched_at="2026-07-18T00:00:00",
    )
    base.update(overrides)
    return FundamentalSnapshot(**base)


def _fundamentals(index, yield_pct=6.0, growth_pct=5.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "trailing_dividend_yield_pct": pd.Series(yield_pct, index=index, dtype=float),
            "dividend_growth_yoy_pct": pd.Series(growth_pct, index=index, dtype=float),
        },
        index=index,
    )


# --- technical screen -------------------------------------------------------


def test_sma_stack_passes_in_a_steady_uptrend(daily_bars_factory):
    bars = daily_bars_factory(list(np.linspace(100, 300, 300)))
    assert bool(technical_screen(bars, CONFIG)["sma_stack"].iloc[-1])


def test_sma_stack_fails_in_a_downtrend(daily_bars_factory):
    bars = daily_bars_factory(list(np.linspace(300, 100, 300)))
    assert not bool(technical_screen(bars, CONFIG)["sma_stack"].iloc[-1])


def test_adr_measures_average_daily_range_as_pct_of_close(daily_bars_factory):
    closes = [100.0] * 40
    bars = daily_bars_factory(closes, highs=[103.0] * 40, lows=[97.0] * 40)
    assert adr_pct(bars).iloc[-1] == pytest.approx(6.0, abs=1e-6)


def test_adr_screen_rejects_a_quiet_stock(daily_bars_factory):
    closes = [100.0] * 40
    quiet = daily_bars_factory(closes, highs=[100.5] * 40, lows=[99.5] * 40)
    assert not bool(technical_screen(quiet, CONFIG)["adr_ok"].iloc[-1])


# --- point-in-time fundamental screen ---------------------------------------


def test_yield_above_threshold_passes(daily_bars_factory):
    bars = daily_bars_factory([100.0] * 30)
    screen = point_in_time_fundamental_screen(_fundamentals(bars.index, yield_pct=6.0), CONFIG)
    assert bool(screen["yield_ok"].iloc[-1])


def test_yield_below_threshold_fails(daily_bars_factory):
    bars = daily_bars_factory([100.0] * 30)
    screen = point_in_time_fundamental_screen(_fundamentals(bars.index, yield_pct=3.9), CONFIG)
    assert not bool(screen["yield_ok"].iloc[-1])


def test_shrinking_dividend_fails(daily_bars_factory):
    bars = daily_bars_factory([100.0] * 30)
    screen = point_in_time_fundamental_screen(_fundamentals(bars.index, growth_pct=-1.0), CONFIG)
    assert not bool(screen["dividend_growth_ok"].iloc[-1])


def test_nan_fundamentals_fail_rather_than_pass(daily_bars_factory):
    bars = daily_bars_factory([100.0] * 30)
    frame = _fundamentals(bars.index)
    frame["trailing_dividend_yield_pct"] = np.nan
    frame["dividend_growth_yoy_pct"] = np.nan
    screen = point_in_time_fundamental_screen(frame, CONFIG)
    assert not screen["yield_ok"].any()
    assert not screen["dividend_growth_ok"].any()


# --- snapshot fundamental screen -------------------------------------------


def test_snapshot_screen_passes_a_healthy_company(daily_bars_factory):
    price = daily_bars_factory([100.0] * 30)["Close"]
    screen = snapshot_fundamental_screen(_snapshot(), price, CONFIG)
    assert screen.all(axis=1).iloc[-1]


@pytest.mark.parametrize(
    "override, failing_column",
    [
        ({"market_cap": 500e6}, "market_cap_ok"),
        ({"analyst_rating": 3.1}, "analyst_rating_ok"),
        ({"payout_ratio_pct": 95.0}, "payout_ratio_ok"),
        ({"payout_ratio_pct": 0.0}, "payout_ratio_ok"),
        ({"eps_growth_yoy_pct": -5.0}, "eps_growth_ok"),
        ({"analyst_target_price": 90.0}, "below_analyst_target"),
    ],
)
def test_each_snapshot_criterion_fails_independently(
    daily_bars_factory, override, failing_column
):
    price = daily_bars_factory([100.0] * 30)["Close"]
    screen = snapshot_fundamental_screen(_snapshot(**override), price, CONFIG)
    assert not bool(screen[failing_column].iloc[-1])
    assert not screen.all(axis=1).iloc[-1]


@pytest.mark.parametrize(
    "field, column",
    [
        ("market_cap", "market_cap_ok"),
        ("analyst_rating", "analyst_rating_ok"),
        ("payout_ratio_pct", "payout_ratio_ok"),
        ("eps_growth_yoy_pct", "eps_growth_ok"),
        ("analyst_target_price", "below_analyst_target"),
    ],
)
def test_a_missing_snapshot_field_fails_its_criterion(daily_bars_factory, field, column):
    """Absent data must never be read as a pass."""
    price = daily_bars_factory([100.0] * 30)["Close"]
    screen = snapshot_fundamental_screen(_snapshot(**{field: None}), price, CONFIG)
    assert not bool(screen[column].iloc[-1])


# --- entry trigger ----------------------------------------------------------


def _gap_up_bars(daily_bars_factory, closes, opens, volumes=None):
    n = len(closes)
    return daily_bars_factory(
        closes,
        opens=opens,
        highs=[max(c, o) + 1 for c, o in zip(closes, opens)],
        lows=[min(c, o) - 1 for c, o in zip(closes, opens)],
        volumes=volumes if volumes is not None else [1_000_000] * n,
    )


def test_gap_up_requires_more_than_one_percent(daily_bars_factory):
    closes = [100.0] * 40
    opens = [100.0] * 39 + [100.5]  # only +0.5% vs prior close
    bars = _gap_up_bars(daily_bars_factory, closes, opens)
    assert not bool(entry_trigger(bars, CONFIG)["gap_up"].iloc[-1])

    opens_big = [100.0] * 39 + [102.0]  # +2%
    bars_big = _gap_up_bars(daily_bars_factory, closes, opens_big)
    assert bool(entry_trigger(bars_big, CONFIG)["gap_up"].iloc[-1])


def test_volume_confirmation_needs_the_top_30_percent_of_the_trailing_window(
    daily_bars_factory
):
    closes = [100.0] * 40
    opens = [100.0] * 40
    quiet = [1_000_000] * 39 + [500_000]
    loud = [1_000_000] * 39 + [5_000_000]
    assert not bool(
        entry_trigger(_gap_up_bars(daily_bars_factory, closes, opens, quiet), CONFIG)
        ["volume_ok"].iloc[-1]
    )
    assert bool(
        entry_trigger(_gap_up_bars(daily_bars_factory, closes, opens, loud), CONFIG)
        ["volume_ok"].iloc[-1]
    )


def test_spec_trigger_requires_the_close_near_the_daily_sma20(daily_bars_factory):
    """The literal spec rule: close within 0.5% of SMA20."""
    flat = [100.0] * 40
    bars = _gap_up_bars(daily_bars_factory, flat, flat)
    assert bool(entry_trigger(bars, CONFIG)["pullback"].iloc[-1])

    stretched = [100.0] * 39 + [110.0]  # 10% above a ~100 SMA20
    bars_far = _gap_up_bars(daily_bars_factory, stretched, [100.0] * 40)
    assert not bool(entry_trigger(bars_far, CONFIG)["pullback"].iloc[-1])


def test_intraday_proxy_wants_a_fade_from_the_open_that_holds_the_gap(daily_bars_factory):
    """Gapped up, pulled back during the session, but did not fill the gap."""
    config = DividendHybrid(trigger_mode=TRIGGER_INTRADAY_PROXY)
    closes = [100.0] * 39 + [101.5]   # below the open, above the prior close
    opens = [100.0] * 39 + [103.0]
    bars = _gap_up_bars(daily_bars_factory, closes, opens)
    assert bool(entry_trigger(bars, config)["pullback"].iloc[-1])

    filled = [100.0] * 39 + [99.0]    # gap fully filled -> not a valid pullback
    bars_filled = _gap_up_bars(daily_bars_factory, filled, opens)
    assert not bool(entry_trigger(bars_filled, config)["pullback"].iloc[-1])

    no_fade = [100.0] * 39 + [104.0]  # closed above the open -> no pullback
    bars_no_fade = _gap_up_bars(daily_bars_factory, no_fade, opens)
    assert not bool(entry_trigger(bars_no_fade, config)["pullback"].iloc[-1])


def test_the_two_trigger_modes_are_genuinely_different(daily_bars_factory):
    """Regression guard for the finding that motivated having two: a >1% gap
    up leaves price far from the DAILY SMA20 (median 3.55% across the Dow),
    so the spec rule and the intraday proxy must not agree here."""
    closes = [100.0] * 39 + [101.5]
    opens = [100.0] * 39 + [103.0]
    bars = _gap_up_bars(daily_bars_factory, closes, opens)
    spec = entry_trigger(bars, DividendHybrid(trigger_mode=TRIGGER_SPEC))
    proxy = entry_trigger(bars, DividendHybrid(trigger_mode=TRIGGER_INTRADAY_PROXY))
    assert bool(spec["gap_up"].iloc[-1]) and bool(proxy["gap_up"].iloc[-1])
    assert not bool(spec["pullback"].iloc[-1])
    assert bool(proxy["pullback"].iloc[-1])


def test_unknown_trigger_mode_is_rejected(daily_bars_factory):
    bars = _gap_up_bars(daily_bars_factory, [100.0] * 40, [100.0] * 40)
    with pytest.raises(ValueError, match="trigger_mode"):
        entry_trigger(bars, DividendHybrid(trigger_mode="nonsense"))


def test_trigger_does_not_look_ahead(daily_bars_factory):
    """Appending future bars must not change an earlier bar's trigger."""
    rng = np.random.default_rng(3)
    closes = list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 120))))
    opens = [c * 1.005 for c in closes]
    bars = _gap_up_bars(daily_bars_factory, closes, opens)
    full = entry_trigger(bars, CONFIG)
    for i in (60, 90, 119):
        truncated = entry_trigger(bars.iloc[: i + 1], CONFIG)
        assert truncated.iloc[-1].equals(full.iloc[i])
