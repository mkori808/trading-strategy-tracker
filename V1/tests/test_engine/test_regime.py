"""Market regime classifier: one synthetic SPY series per regime state."""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine.regime import (
    BEARISH,
    BULLISH,
    NEUTRAL,
    classify,
    format_distribution,
    regime_distribution,
    regime_log,
    regime_series,
)

BARS = 400  # enough to warm the 200-day SMA with room to spare


def _bars(closes, daily_bars_factory):
    return daily_bars_factory(list(closes))


def test_bullish_when_price_above_both_smas_and_50_above_200(daily_bars_factory):
    # A steady uptrend puts price above both averages and the fast above the slow.
    closes = np.linspace(100, 300, BARS)
    assert classify(_bars(closes, daily_bars_factory)) == BULLISH


def test_bearish_when_price_below_200_sma_and_50_below_200(daily_bars_factory):
    # A steady downtrend is the mirror image.
    closes = np.linspace(300, 100, BARS)
    assert classify(_bars(closes, daily_bars_factory)) == BEARISH


def test_neutral_when_price_below_200_sma_but_50_still_above_it(daily_bars_factory):
    # Long uptrend, then a sharp pullback: price drops under the 200-SMA while
    # the 50-SMA hasn't crossed down yet. Neither definition matches -> Neutral.
    closes = list(np.linspace(100, 300, BARS)) + list(np.linspace(300, 180, 30))
    labels = regime_series(_bars(closes, daily_bars_factory))
    fifty = pd.Series(closes).rolling(50).mean().iloc[-1]
    two_hundred = pd.Series(closes).rolling(200).mean().iloc[-1]
    assert closes[-1] < two_hundred and fifty > two_hundred  # the setup we intended
    assert labels.iloc[-1] == NEUTRAL


def test_neutral_during_warmup_before_200_sma_exists(daily_bars_factory):
    # An unknown regime must gate entries OFF, not on.
    closes = np.linspace(100, 200, 50)
    labels = regime_series(_bars(closes, daily_bars_factory))
    assert (labels == NEUTRAL).all()


def test_empty_bars_classify_neutral():
    assert classify(pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])) == NEUTRAL


def test_no_lookahead_regime_label_is_stable_under_truncation(daily_bars_factory):
    """Bar i's label must depend only on bars <= i.

    Recompute on data truncated at i and assert it matches the label from the
    full series. If any window were centered or forward-looking, appending
    future bars would change an earlier label and this would fail.
    """
    rng = np.random.default_rng(7)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, BARS)))
    bars = _bars(closes, daily_bars_factory)
    full = regime_series(bars)
    for i in (250, 300, 355, BARS - 1):
        truncated = regime_series(bars.iloc[: i + 1])
        assert truncated.iloc[-1] == full.iloc[i]


def test_regime_distribution_sums_to_one_and_counts_each_state(daily_bars_factory):
    closes = list(np.linspace(100, 300, BARS)) + list(np.linspace(300, 80, BARS))
    labels = regime_series(_bars(closes, daily_bars_factory))
    dist = regime_distribution(labels)
    assert abs(sum(dist.values()) - 1.0) < 1e-9
    assert dist[BULLISH] > 0 and dist[BEARISH] > 0
    assert "Bullish" in format_distribution(labels)


def test_regime_log_flags_transitions(daily_bars_factory):
    closes = list(np.linspace(100, 300, BARS)) + list(np.linspace(300, 80, BARS))
    log = regime_log(regime_series(_bars(closes, daily_bars_factory)))
    assert len(log) == 2 * BARS
    assert set(log.columns) == {"date", "regime", "changed"}
    assert log["changed"].sum() >= 2  # at least the initial state plus one flip
