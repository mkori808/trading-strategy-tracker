"""Unit tests for engine/timing_filters.py -- the FOMC-day and volatility-
regime entry gates. Synthetic series only; no network."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from engine.timing_filters import (
    FOMC_DECISION_DATES,
    EntryGate,
    GateDiagnostics,
    VolTargetedCrossSectional,
    fomc_blocked_dates,
    vol_allowed_series,
)
from strategies.base import Strategy


class FixedWeights:
    name = "Fixed Weights"
    timeframe = "1mo"

    def __init__(self, weights: dict[str, float]) -> None:
        self._weights = weights

    def rebalance(self, universe_bars, as_of):
        return dict(self._weights)


class AlwaysEnter(Strategy):
    name = "Always Enter"
    timeframe = "1d"
    direction = "long"

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        return True

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return entry_price * 0.95


def _bars(day: date) -> pd.DataFrame:
    ts = pd.Timestamp(day, tz="America/New_York")
    return pd.DataFrame({"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0]}, index=[ts])


def test_fomc_blocked_dates_include_decision_and_prior_day() -> None:
    blocked = fomc_blocked_dates(date(2024, 1, 1), date(2024, 12, 31))
    assert date(2024, 6, 12) in blocked  # decision day
    assert date(2024, 6, 11) in blocked  # meeting day 1 (default buffer_before=1)
    assert date(2024, 6, 13) not in blocked  # default buffer_after=0


def test_fomc_blocked_dates_rejects_uncovered_year() -> None:
    with pytest.raises(ValueError, match="Extend FOMC_DECISION_DATES"):
        fomc_blocked_dates(date(2030, 1, 1), date(2030, 12, 31))


def test_fomc_calendar_has_eight_meetings_per_year() -> None:
    per_year: dict[int, int] = {}
    for d in FOMC_DECISION_DATES:
        per_year[d.year] = per_year.get(d.year, 0) + 1
    assert all(count == 8 for count in per_year.values()), per_year


def test_entry_gate_blocks_and_counts() -> None:
    blocked = fomc_blocked_dates(date(2024, 1, 1), date(2024, 12, 31))
    diagnostics = GateDiagnostics("test gate")
    gate = EntryGate(AlwaysEnter(), lambda ts: ts.date() not in blocked, diagnostics)

    assert gate.entry_signal(_bars(date(2024, 6, 12))) is False  # FOMC day
    assert gate.entry_signal(_bars(date(2024, 6, 20))) is True  # ordinary day
    assert diagnostics.blocked_entries == 1
    assert diagnostics.passed_entries == 1


def test_entry_gate_delegates_position_management() -> None:
    inner = AlwaysEnter()
    gate = EntryGate(inner, lambda ts: True, GateDiagnostics("test gate"))
    bars = _bars(date(2024, 6, 20))
    assert gate.stop_price(bars, 100.0) == inner.stop_price(bars, 100.0)
    assert gate.target_price(bars, 100.0) is None
    assert gate.exit_signal(bars) is False


def test_vol_allowed_series_partitions_and_blocks_warmup() -> None:
    idx = pd.date_range("2024-01-01", periods=5, tz="America/New_York")
    percentiles = pd.Series([np.nan, 0.2, 0.7, 0.9, 0.5], index=idx)
    calm = vol_allowed_series(percentiles, "calm", threshold=0.7)
    storm = vol_allowed_series(percentiles, "storm", threshold=0.7)

    # NaN warmup days are allowed in neither mode.
    assert not calm.iloc[0] and not storm.iloc[0]
    # Every real-valued day is allowed in exactly one of the two modes.
    real = percentiles.notna()
    assert ((calm ^ storm) | ~real).all()
    assert list(calm[real]) == [True, True, False, True]


def test_vol_targeted_overlay_scales_down_in_high_vol() -> None:
    idx = pd.date_range("2024-01-01", periods=3, tz="America/New_York")
    realized_vol = pd.Series([0.12, 0.24, 0.06], index=idx)  # target=0.12
    inner = FixedWeights({"AAA": 0.5, "BBB": 0.5})
    overlay = VolTargetedCrossSectional(inner, realized_vol, target_annual_vol=0.12)

    at_target = overlay.rebalance({}, idx[0])
    assert at_target == {"AAA": 0.5, "BBB": 0.5}  # scale = 1.0, unchanged

    double_vol = overlay.rebalance({}, idx[1])
    assert double_vol == {"AAA": 0.25, "BBB": 0.25}  # scale = 0.5

    half_vol = overlay.rebalance({}, idx[2])
    assert half_vol == {"AAA": 0.5, "BBB": 0.5}  # scale capped at 1.0, not levered up


def test_vol_targeted_overlay_passes_through_empty_and_missing_vol() -> None:
    idx = pd.date_range("2024-01-01", periods=2, tz="America/New_York")
    realized_vol = pd.Series([np.nan, 0.12], index=idx)
    overlay = VolTargetedCrossSectional(
        FixedWeights({"AAA": 1.0}), realized_vol, target_annual_vol=0.12
    )
    assert overlay.rebalance({}, idx[0]) == {"AAA": 1.0}  # no vol reading -- unscaled

    empty_inner = VolTargetedCrossSectional(
        FixedWeights({}), realized_vol, target_annual_vol=0.12
    )
    assert empty_inner.rebalance({}, idx[1]) == {}  # already fully in cash -- no-op


def test_vol_percentile_is_causal_on_synthetic_series() -> None:
    """Recompute on truncated data and compare -- the value at bar i must
    not change when later bars are removed (right-aligned windows only).
    Uses the same rolling recipe spy_vol_percentile applies to SPY, on a
    synthetic close series so no network is involved."""
    rng = np.random.default_rng(7)
    idx = pd.date_range("2020-01-01", periods=400, freq="B", tz="America/New_York")
    closes = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx)))), index=idx)

    def percentile(series: pd.Series) -> pd.Series:
        log_returns = np.log(series / series.shift(1))
        vol = log_returns.rolling(21).std() * np.sqrt(252)
        return vol.rolling(100).rank(pct=True)

    full = percentile(closes)
    truncated = percentile(closes.iloc[:300])
    pd.testing.assert_series_equal(full.iloc[:300], truncated)
