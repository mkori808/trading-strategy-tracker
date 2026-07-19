"""Anchored VWAP (AVWAP) calculation and anchor selection, for the
Anchored VWAP Breakout strategy (strategies/swing/avwap_breakout.py).

AVWAP starts from a specific meaningful price event and accumulates
volume-weighted price forward from that date continuously, unlike session
VWAP (engine/indicators.py:vwap) which resets every day. Implemented
directly from OHLCV -- no external VWAP library, per CLAUDE.md and the
task spec, so the calculation stays auditable.

Anchor selection is pre-registered and must not change after seeing
backtest results (see strategies/swing/avwap_breakout.py and LESSONS.md):

  - Primary: earnings gap anchors -- a >3% open-vs-prior-close gap on a
    real earnings announcement date, confirmed by volume > 1.5x the
    trailing 20-day average volume computed strictly BEFORE the gap bar
    (no look-ahead). Gap-up only for this (long-only) implementation.
  - Fallback: swing low anchors -- a bar whose low is below the N bars on
    either side (N=10) that also marks a >=15% decline from the prior
    swing high. Used only if earnings date coverage is too thin (see
    engine/run_avwap_breakout.py's data-availability check).

Both anchor functions are computed vectorized over a symbol's whole
history in one pass (same convention as engine/trend_template.py), not
recomputed bar-by-bar.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

EARNINGS_GAP_PCT = 0.03
EARNINGS_GAP_VOLUME_MULTIPLE = 1.5
EARNINGS_GAP_VOLUME_LOOKBACK = 20

SWING_LOW_N = 10
SWING_LOW_MIN_DECLINE_PCT = 0.15


def compute_avwap(bars: pd.DataFrame, anchor: pd.Timestamp) -> pd.DataFrame:
    """AVWAP plus 1-and-2-std bands, cumulative from `anchor` (inclusive)
    through the last row of `bars`. `anchor` must be a timestamp present in
    `bars.index` -- callers resolve "most recent qualifying anchor as of
    today" before calling this (see AvwapBreakout._avwap_series).

    typical_price = (High + Low + Close) / 3, standard VWAP math, applied
    cumulatively instead of resetting daily:
        avwap    = cumsum(typical_price * volume) / cumsum(volume)
        variance = cumsum(typical_price^2 * volume) / cumsum(volume) - avwap^2
    (the volume-weighted analogue of Var(X) = E[X^2] - E[X]^2). Bands are
    diagnostic only for this first implementation -- not used in the entry
    rule, logged per bar so they can be analyzed later.
    """
    columns = ["avwap", "std", "upper_1", "lower_1", "upper_2", "lower_2"]
    if anchor not in bars.index:
        return pd.DataFrame(columns=columns)

    window = bars.loc[anchor:]
    typical_price = (window["High"] + window["Low"] + window["Close"]) / 3
    volume = window["Volume"]

    cum_volume = volume.cumsum()
    cum_pv = (typical_price * volume).cumsum()
    avwap = cum_pv / cum_volume

    cum_pv2 = ((typical_price ** 2) * volume).cumsum()
    variance = (cum_pv2 / cum_volume) - avwap ** 2
    # Float round-off can push a true-zero variance (e.g. the anchor bar
    # itself, a single point) marginally negative -- clip rather than let
    # a NaN std leak out of a sqrt of a negative number.
    variance = variance.clip(lower=0)
    std = np.sqrt(variance)

    return pd.DataFrame(
        {
            "avwap": avwap,
            "std": std,
            "upper_1": avwap + std,
            "lower_1": avwap - std,
            "upper_2": avwap + 2 * std,
            "lower_2": avwap - 2 * std,
        },
        index=window.index,
    )


def earnings_gap_anchors(
    bars: pd.DataFrame,
    earnings_dates: list[date],
    gap_pct: float = EARNINGS_GAP_PCT,
    volume_multiple: float = EARNINGS_GAP_VOLUME_MULTIPLE,
    volume_lookback: int = EARNINGS_GAP_VOLUME_LOOKBACK,
) -> list[pd.Timestamp]:
    """Gap-UP earnings anchors only (long side, per spec). For each real
    earnings date, find the first trading bar on/after it (the reaction
    session) and test: (open / prior_close - 1) > gap_pct AND
    volume > volume_multiple * the volume_lookback-day average volume of
    the bars strictly BEFORE the reaction session (shift(1) before the
    rolling mean so the gap bar's own volume never leaks into its own
    threshold -- no look-ahead)."""
    if bars.empty or not earnings_dates:
        return []

    prior_close = bars["Close"].shift(1)
    gap_pct_series = bars["Open"] / prior_close - 1
    avg_volume = bars["Volume"].shift(1).rolling(volume_lookback).mean()
    volume_ok = bars["Volume"] > volume_multiple * avg_volume
    gap_up_ok = gap_pct_series > gap_pct
    qualifies = gap_up_ok & volume_ok

    anchors: list[pd.Timestamp] = []
    index_dates = bars.index.normalize()
    for ed in sorted(set(earnings_dates)):
        ed_ts = pd.Timestamp(ed)
        if ed_ts.tz is None and bars.index.tz is not None:
            ed_ts = ed_ts.tz_localize(bars.index.tz)
        # First trading bar on/after the announcement date -- the reaction
        # session (announcements after the close land on the next session;
        # before-the-open announcements react the same day).
        candidates = bars.index[index_dates >= ed_ts.normalize()]
        if len(candidates) == 0:
            continue
        reaction_bar = candidates[0]
        if bool(qualifies.get(reaction_bar, False)):
            anchors.append(reaction_bar)

    return sorted(set(anchors))


def swing_low_anchors(
    bars: pd.DataFrame,
    n: int = SWING_LOW_N,
    min_decline_pct: float = SWING_LOW_MIN_DECLINE_PCT,
) -> list[pd.Timestamp]:
    """Fallback anchor type: a bar whose Low is strictly below the N bars
    immediately before AND after it, that also marks at least
    `min_decline_pct` decline from the highest High in the N bars
    immediately before it (the "prior swing high"). Only uses bars up to
    and including the swing low bar plus the N bars after it to CONFIRM the
    swing (a swing low isn't knowable until N bars later) -- the anchor
    DATE used is the swing low bar's own date, but the anchor only enters
    the qualifying list once confirmed, same causality the earnings-gap
    path gets from using only prior data for its volume average."""
    if len(bars) < 2 * n + 1:
        return []

    low = bars["Low"]
    high = bars["High"]
    anchors: list[pd.Timestamp] = []
    for i in range(n, len(bars) - n):
        window_low = low.iloc[i]
        before = low.iloc[i - n : i]
        after = low.iloc[i + 1 : i + n + 1]
        if not (window_low < before.min() and window_low < after.min()):
            continue
        prior_high = high.iloc[i - n : i].max()
        if prior_high <= 0:
            continue
        decline = 1 - (window_low / prior_high)
        if decline >= min_decline_pct:
            anchors.append(bars.index[i])

    return anchors
