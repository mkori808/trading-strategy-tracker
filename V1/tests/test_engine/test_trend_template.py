"""Minervini Trend Template: one synthetic series that passes all 8 criteria,
plus a targeted series per criterion that breaks that criterion specifically.

The per-criterion tests assert on the individual boolean column, not just the
combined verdict. The 8 criteria are geometrically linked -- you cannot drop a
stock below its 150-day SMA without also disturbing its distance from the
52-week high -- so "construct a series that fails ONLY criterion N" is not
generally constructible. Asserting the named column flipped proves that
criterion is the one responding, and asserting `passes` is False proves any
single failure is disqualifying.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.trend_template import (
    CRITERIA,
    MIN_SLOPE_LOOKBACK,
    failed_criteria,
    format_scan_summary,
    passes_trend_template,
    rolling_slope,
    scan_summary,
    trend_template_frame,
)

BARS = 500  # > 200-SMA + 105-bar slope window, and > 252 for the 52-week legs


def _series(closes, daily_bars_factory):
    return daily_bars_factory(list(np.asarray(closes, dtype=float)))


def _flat_benchmark(n, daily_bars_factory):
    """A benchmark that goes nowhere, so criterion 8 turns on the stock alone."""
    return _series(np.full(n, 100.0), daily_bars_factory)


def _uptrend(n=BARS):
    """Steady 100 -> 300 advance: passes all 8 by construction."""
    return np.linspace(100.0, 300.0, n)


def _frame(closes, daily_bars_factory, benchmark=None):
    bars = _series(closes, daily_bars_factory)
    bench = benchmark if benchmark is not None else _flat_benchmark(len(closes), daily_bars_factory)
    return trend_template_frame(bars, bench)


# --- the passing case -------------------------------------------------------


def test_textbook_uptrend_passes_all_eight(daily_bars_factory):
    frame = _frame(_uptrend(), daily_bars_factory)
    last = frame.iloc[-1]
    for name in CRITERIA:
        assert bool(last[name]), f"criterion {name} unexpectedly failed"
    assert bool(last["passes"])

    bars = _series(_uptrend(), daily_bars_factory)
    assert passes_trend_template(bars, _flat_benchmark(BARS, daily_bars_factory))
    assert failed_criteria(bars, _flat_benchmark(BARS, daily_bars_factory)) == []


# --- one targeted failure per criterion -------------------------------------


def test_1_fails_when_price_drops_below_150_and_200_smas(daily_bars_factory):
    # Long advance, then a crash that puts price under both long averages.
    closes = np.concatenate([_uptrend(), np.linspace(300, 150, 40)])
    last = _frame(closes, daily_bars_factory).iloc[-1]
    assert not bool(last["above_150_and_200"])
    assert not bool(last["passes"])


def test_2_fails_when_150_sma_sits_below_200_sma(daily_bars_factory):
    # A sustained decline drags the faster average under the slower one.
    closes = np.concatenate([_uptrend(200), np.linspace(180, 60, 400)])
    last = _frame(closes, daily_bars_factory).iloc[-1]
    assert not bool(last["sma150_above_sma200"])
    assert not bool(last["passes"])


def test_3_fails_when_200_sma_slope_is_not_positive(daily_bars_factory):
    closes = np.concatenate([_uptrend(250), np.linspace(200, 60, 400)])
    last = _frame(closes, daily_bars_factory).iloc[-1]
    assert not bool(last["sma200_rising"])
    assert not bool(last["passes"])


def test_4_fails_when_50_sma_falls_below_the_longer_smas(daily_bars_factory):
    # Advance, then a pullback long enough to pull the 50-SMA under the 150.
    closes = np.concatenate([_uptrend(), np.linspace(300, 205, 90)])
    last = _frame(closes, daily_bars_factory).iloc[-1]
    assert not bool(last["sma50_above_150_and_200"])
    assert not bool(last["passes"])


def test_5_fails_when_price_dips_below_the_50_sma(daily_bars_factory):
    # A dip shallow and short enough that the 150/200 legs still hold.
    closes = np.concatenate([_uptrend(), np.linspace(300, 286, 10)])
    frame = _frame(closes, daily_bars_factory)
    last = frame.iloc[-1]
    assert not bool(last["above_sma50"])
    assert bool(last["above_150_and_200"])  # the longer-term legs are undisturbed
    assert not bool(last["passes"])


def test_6_fails_when_price_is_less_than_25_percent_above_52_week_low(daily_bars_factory):
    # Rises, then spends the whole trailing year flat: the 52-week low is
    # right underneath price, so the 25% cushion never opens up.
    closes = np.concatenate([np.linspace(100, 300, 250), np.full(300, 300.0)])
    last = _frame(closes, daily_bars_factory).iloc[-1]
    assert not bool(last["above_52w_low"])
    assert not bool(last["passes"])


def test_7_fails_when_price_is_more_than_25_percent_below_52_week_high(daily_bars_factory):
    # A 40% drawdown from a high set inside the trailing 52 weeks.
    closes = np.concatenate([_uptrend(), np.linspace(300, 180, 60)])
    last = _frame(closes, daily_bars_factory).iloc[-1]
    assert not bool(last["near_52w_high"])
    assert not bool(last["passes"])


def test_8_fails_when_benchmark_outruns_the_stock_over_12_months(daily_bars_factory):
    # Identical stock series; only the benchmark changes. Isolates criterion 8
    # cleanly, which is the one criterion that CAN be isolated.
    closes = _uptrend()
    strong_benchmark = _series(np.linspace(100, 900, BARS), daily_bars_factory)
    last = _frame(closes, daily_bars_factory, benchmark=strong_benchmark).iloc[-1]
    assert not bool(last["rs_beats_benchmark"])
    assert not bool(last["passes"])
    # ...and the same stock passes against a flat benchmark, proving the
    # benchmark is what moved the verdict.
    assert bool(_frame(closes, daily_bars_factory).iloc[-1]["passes"])


def test_missing_benchmark_fails_rs_rather_than_silently_passing(daily_bars_factory):
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    last = trend_template_frame(_series(_uptrend(), daily_bars_factory), empty).iloc[-1]
    assert not bool(last["rs_beats_benchmark"])
    assert not bool(last["passes"])


# --- look-ahead ------------------------------------------------------------


def test_no_lookahead_verdict_is_stable_under_truncation(daily_bars_factory):
    """Row i must be a function of rows <= i only.

    Recomputes the whole template on data truncated at bar i and compares
    every criterion against the full-history result. A centered rolling
    window, a full-series 52-week max/min, or a backward-filled benchmark
    would each make an earlier row change once later bars exist -- and each
    would fail here.
    """
    rng = np.random.default_rng(11)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0008, 0.012, BARS)))
    bars = _series(closes, daily_bars_factory)
    benchmark = _flat_benchmark(BARS, daily_bars_factory)
    full = trend_template_frame(bars, benchmark)

    for i in (320, 400, 460, BARS - 1):
        truncated = trend_template_frame(bars.iloc[: i + 1], benchmark.iloc[: i + 1])
        for name in [*CRITERIA, "passes"]:
            assert bool(truncated.iloc[-1][name]) == bool(full.iloc[i][name]), (
                f"{name} at bar {i} changed when future bars were added"
            )


def test_52_week_window_is_trailing_not_whole_series(daily_bars_factory):
    """A high set more than 52 weeks ago must not count against criterion 7.

    Guards the specific bug where `.max()` over the full series lets every bar
    see the entire backtest's extremes.
    """
    # Spike to 500 early, then a long clean advance that never revisits it.
    closes = np.concatenate([np.linspace(100, 500, 30), np.linspace(150, 400, 470)])
    last = _frame(closes, daily_bars_factory).iloc[-1]
    # 400 is 20% below the all-time high of 500 but at its trailing-52w high.
    assert bool(last["near_52w_high"])


def test_benchmark_gap_is_forward_filled_never_backward(daily_bars_factory):
    """On a date the benchmark has no bar for, the template must reuse the
    last PRIOR benchmark value, not reach forward to the next one."""
    closes = _uptrend()
    bars = _series(closes, daily_bars_factory)
    benchmark = _flat_benchmark(BARS, daily_bars_factory)

    # Benchmark jumps hugely on the final bar, and that final bar is removed
    # from the benchmark's own index. A backward fill has nothing to reach to;
    # the real tell is a gap in the MIDDLE, so drop an interior bar instead and
    # spike the bar right after it.
    spiked = benchmark.copy()
    spiked.iloc[-1] = spiked.iloc[-1] * 50
    gapped = spiked.drop(spiked.index[-2])

    frame = trend_template_frame(bars, gapped)
    # The second-to-last stock bar has no benchmark bar of its own. Forward
    # fill gives it the flat pre-spike value -> RS still passes. A backward
    # fill would hand it the 50x spike from the future -> RS would fail.
    assert bool(frame.iloc[-2]["rs_beats_benchmark"])


def test_slope_lookback_below_the_21_day_minimum_is_rejected(daily_bars_factory):
    bars = _series(_uptrend(), daily_bars_factory)
    with pytest.raises(ValueError, match="minimum"):
        trend_template_frame(bars, _flat_benchmark(BARS, daily_bars_factory), slope_lookback=5)
    # The documented minimum itself is allowed.
    trend_template_frame(
        bars, _flat_benchmark(BARS, daily_bars_factory), slope_lookback=MIN_SLOPE_LOOKBACK
    )


def test_rolling_slope_matches_least_squares(daily_bars_factory):
    series = pd.Series(np.linspace(0, 100, 200))
    slopes = rolling_slope(series, 50)
    expected = np.polyfit(np.arange(50), series.iloc[-50:].values, 1)[0]
    assert abs(float(slopes.iloc[-1]) - expected) < 1e-9
    assert pd.isna(slopes.iloc[48])  # not warm before a full window exists


# --- selectivity logging ----------------------------------------------------


def test_scan_summary_counts_passes_and_failures_per_date(daily_bars_factory):
    winner = _frame(_uptrend(), daily_bars_factory)
    loser = _frame(np.linspace(300, 100, BARS), daily_bars_factory)
    summary = scan_summary({"WIN": winner, "LOSE": loser})

    assert len(summary) == BARS
    final = summary.iloc[-1]
    assert final["passed"] == 1
    assert final["failed"] == 1
    assert final["pass_rate"] == 0.5
    assert final["top_failure"] in CRITERIA
    assert "mean pass rate" in format_scan_summary(summary)


def test_scan_summary_of_nothing_is_empty_not_an_error():
    assert scan_summary({}).empty
    assert "no scan dates" in format_scan_summary(pd.DataFrame())
