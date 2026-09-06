"""engine/pit_features.py: point-in-time correctness, feature metadata, and
the earnings/gap/market-context builders.

The look-ahead check follows the same convention as
tests/test_engine/test_regime.py:test_no_lookahead_regime_label_is_stable_under_truncation --
recompute on truncated data and require the last row to match the full-series
value at that position. That is the causality contract every feature in this
module claims, and it is asserted by recomputation rather than by inspection.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import pit_features
from tests.test_engine._conditional_helpers import monkeypatch_bars, synthetic_bars

pytestmark = pytest.mark.filterwarnings("ignore")


def _context(monkeypatch, bars_by_symbol, universe):
    monkeypatch_bars(monkeypatch, pit_features, bars_by_symbol)
    return pit_features.build_market_context(universe, bars_by_symbol["SPY"].index[300].date(), bars_by_symbol["SPY"].index[-1].date())


@pytest.fixture
def universe_bars():
    return {
        "SPY": synthetic_bars(500, seed=1),
        "AAPL": synthetic_bars(500, seed=2, drift=0.0005),
        "MSFT": synthetic_bars(500, seed=3, drift=0.0002),
    }


def test_feature_registry_is_internally_consistent():
    for key, definition in pit_features.FEATURES.items():
        assert definition.key == key
        if not definition.available:
            assert definition.unavailable_reason
    assert "days_until_earnings" not in pit_features.AVAILABLE_FEATURES
    assert set(pit_features.DISCOVERY_FEATURES) <= set(pit_features.AVAILABLE_FEATURES)


def test_calendar_and_sector_features_are_not_discovery_eligible():
    for key in ("day_of_week", "month", "days_to_month_end", "days_to_quarter_end"):
        assert pit_features.FEATURES[key].discovery_eligible is False
    for key in ("sector_ret_60d", "sector_rank_60d", "rs_vs_sector_60d", "residual_ret_20d_sector"):
        assert pit_features.FEATURES[key].discovery_eligible is False
        assert pit_features.FEATURES[key].pit_safe is False


def test_availability_report_declares_unavailable_feature_with_reason():
    report = pit_features.availability_report("standard")
    row = next(f for f in report["features"] if f["key"] == "days_until_earnings")
    assert row["available"] is False
    assert "scheduled" in row["unavailableReason"].lower() or "announce" in row["unavailableReason"].lower()
    assert report["availableCount"] == len(pit_features.AVAILABLE_FEATURES)


def test_symbol_price_features_are_stable_under_truncation(monkeypatch, universe_bars):
    """Bar i's feature row must depend only on bars <= i for every symbol-level feature."""
    context = _context(monkeypatch, universe_bars, ["AAPL", "MSFT"])
    bars = universe_bars["AAPL"]
    full = pit_features.features_from_bars(bars, "AAPL", context)
    checked = ("ret_20d", "dist_sma50", "rsi2", "ibs", "atr_pct", "gap_pct",
               "vol_percentile_252", "trend_slope_60d", "dist_52w_high", "consecutive_days")
    for cut in (260, 320, 400, len(bars) - 1):
        truncated_context = _context(monkeypatch, universe_bars, ["AAPL", "MSFT"])
        truncated = pit_features.features_from_bars(bars.iloc[: cut + 1], "AAPL", truncated_context)
        for key in checked:
            full_value = full[key].iloc[cut]
            truncated_value = truncated[key].iloc[-1]
            if pd.isna(full_value) and pd.isna(truncated_value):
                continue
            assert full_value == pytest.approx(truncated_value, rel=1e-9, abs=1e-9), key


def test_market_regime_feature_matches_regime_module(monkeypatch, universe_bars):
    from engine import regime as regime_module

    context = _context(monkeypatch, universe_bars, ["AAPL"])
    expected = regime_module.regime_series(universe_bars["SPY"])
    assert context.market["mkt_regime"].dropna().isin(
        [regime_module.BULLISH, regime_module.NEUTRAL, regime_module.BEARISH]
    ).all()
    aligned_expected = expected.reindex(context.market.index)
    matched = (context.market["mkt_regime"] == aligned_expected).dropna()
    assert matched.all()


def test_market_above_sma_is_unknown_not_false_during_warmup(monkeypatch, universe_bars):
    context = _context(monkeypatch, universe_bars, ["AAPL"])
    warmup_slice = context.market["mkt_above_sma200"].iloc[:100]
    # Unwarmed rows must be None (unknown), never False -- a bool column has
    # no way to represent "not yet computable" and would silently read as
    # "market below its 200-day average."
    assert warmup_slice.isna().all() or (warmup_slice == None).all()  # noqa: E711


def test_cross_sectional_percentile_is_a_same_date_rank_only(monkeypatch, universe_bars):
    """Percentiles must never rank a date against any OTHER date's data."""
    context = _context(monkeypatch, universe_bars, ["AAPL", "MSFT"])
    frame = context.xs_percentiles["xs_ret_percentile_20d"]
    # Perturbing history before a date must not move that date's percentile,
    # since only that date's cross-section should determine the rank.
    mutated = dict(universe_bars)
    mutated["MSFT"] = mutated["MSFT"].copy()
    mutated["MSFT"].iloc[:200] *= 1.5
    mutated_context = _context(monkeypatch, mutated, ["AAPL", "MSFT"])
    mutated_frame = mutated_context.xs_percentiles["xs_ret_percentile_20d"]
    tail_a = frame["AAPL"].iloc[300:]
    tail_b = mutated_frame["AAPL"].iloc[300:]
    pd.testing.assert_series_equal(tail_a, tail_b, check_names=False)


def test_earnings_features_sentinel_and_gap(monkeypatch, universe_bars):
    context = _context(monkeypatch, universe_bars, ["AAPL"])
    bars = universe_bars["AAPL"]
    report_date = bars.index[350]

    def _earnings_dates(symbol, force_refresh=False):
        return pd.DataFrame(
            {"Reported EPS": [1.0], "EPS Estimate": [0.5], "Surprise(%)": [100.0]},
            index=pd.DatetimeIndex([report_date]),
        )

    monkeypatch.setattr(pit_features.data_module, "earnings_dates", _earnings_dates)
    frame = pit_features.features_from_bars(bars, "AAPL", context)

    before = frame["days_since_earnings"].iloc[340]
    assert before == pit_features.NO_EARNINGS_SENTINEL

    after = frame["days_since_earnings"].iloc[360]
    assert 0 < after < pit_features.NO_EARNINGS_SENTINEL

    assert bool(frame["post_earnings_5d"].iloc[351]) is True
    assert bool(frame["post_earnings_5d"].iloc[400]) is False


def test_earnings_features_degrade_gracefully_with_no_feed(monkeypatch, universe_bars):
    context = _context(monkeypatch, universe_bars, ["AAPL"])
    bars = universe_bars["AAPL"]
    monkeypatch.setattr(pit_features.data_module, "earnings_dates", lambda *a, **k: pd.DataFrame())
    frame = pit_features.features_from_bars(bars, "AAPL", context)
    assert (frame["days_since_earnings"] == pit_features.NO_EARNINGS_SENTINEL).all()
    assert not frame["post_earnings_5d"].any()


def test_snapshot_at_resolves_backwards_never_forwards(monkeypatch, universe_bars):
    context = _context(monkeypatch, universe_bars, ["AAPL"])
    bars = universe_bars["AAPL"]
    frame = pit_features.features_from_bars(bars, "AAPL", context)

    exact = frame.index[400]
    between = exact + pd.Timedelta(hours=1)  # not an exact index match

    snap_exact = pit_features.snapshot_at(frame, exact)
    snap_between = pit_features.snapshot_at(frame, between)
    # A lookup strictly between two bars must resolve to the EARLIER one,
    # never peek at the bar that follows it.
    assert snap_exact["ret_20d"] == snap_between["ret_20d"]

    before_start = frame.index[0] - pd.Timedelta(days=10)
    assert pit_features.snapshot_at(frame, before_start) == {}


def test_snapshot_at_drops_missing_values_as_none(monkeypatch, universe_bars):
    context = _context(monkeypatch, universe_bars, ["AAPL"])
    bars = universe_bars["AAPL"]
    frame = pit_features.features_from_bars(bars, "AAPL", context)
    early = pit_features.snapshot_at(frame, frame.index[5])
    assert early["dist_sma200"] is None  # not yet warm


def test_gap_alignment_categories(monkeypatch, universe_bars):
    context = _context(monkeypatch, universe_bars, ["AAPL"])
    bars = universe_bars["AAPL"].copy()
    frame = pit_features.features_from_bars(bars, "AAPL", context)
    valid = frame["gap_alignment"].dropna()
    assert set(valid.unique()) <= {"aligned", "opposed", "flat"}
