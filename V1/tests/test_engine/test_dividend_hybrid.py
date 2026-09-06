"""Version A (no stop) vs Version B (8% stop) exit behaviour.

The screen and trigger are stubbed to fire on one known bar so each test
controls exactly one entry and a deliberate price path. What is under test
here is the exit machinery and the bookkeeping the comparison depends on --
still-held accounting, unrealized-drawdown tracking, dividend cuts during a
hold -- not the screen (covered in tests/test_strategies/test_dividend_hybrid.py).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine import dividend_hybrid as dh
from strategies.swing.dividend_hybrid import DividendHybrid

NY = "America/New_York"
SIGNAL_BAR = 230       # entry fills on the next bar's open
TOTAL_BARS = 400
ENTRY_YIELD_PCT = 10.0  # take profit at +10% from entry


def _bars(closes, opens=None, highs=None, lows=None) -> pd.DataFrame:
    index = pd.bdate_range("2022-01-03", periods=len(closes), tz=NY)
    closes = pd.Series(closes, index=index, dtype=float)
    opens = pd.Series(opens, index=index, dtype=float) if opens is not None else closes
    highs = pd.Series(highs, index=index, dtype=float) if highs is not None else pd.concat([opens, closes], axis=1).max(axis=1)
    lows = pd.Series(lows, index=index, dtype=float) if lows is not None else pd.concat([opens, closes], axis=1).min(axis=1)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes,
         "Volume": pd.Series(1e6, index=index)}
    )


@pytest.fixture
def stub(monkeypatch):
    """Screen always passes; trigger fires once, on SIGNAL_BAR."""
    def _install(bars: pd.DataFrame, symbols=("AAA",), cuts_from: int | None = None):
        frames = {s: bars for s in symbols}
        monkeypatch.setattr(
            dh.data_module, "get_bars",
            lambda symbol, interval, start, end, **k: frames.get(symbol, pd.DataFrame()),
        )

        def _fundamentals(symbol, index):
            cut = pd.Series(False, index=index)
            if cuts_from is not None:
                cut.iloc[cuts_from:] = True
            return pd.DataFrame(
                {
                    "trailing_dividend_yield_pct": pd.Series(ENTRY_YIELD_PCT, index=index),
                    "dividend_growth_yoy_pct": pd.Series(5.0, index=index),
                    "dividend_cagr_5y_pct": pd.Series(5.0, index=index),
                    "dividend_cut": cut,
                },
                index=index,
            )

        monkeypatch.setattr(dh.fundamentals_module, "fundamentals_frame", _fundamentals)
        monkeypatch.setattr(
            dh, "technical_screen",
            lambda bars, config: pd.DataFrame({"ok": True}, index=bars.index),
        )
        monkeypatch.setattr(
            dh, "point_in_time_fundamental_screen",
            # `yield_ok` by name: the engine reads that column for the
            # yield-screen selectivity log.
            lambda fundamentals, config: pd.DataFrame(
                {"yield_ok": True, "dividend_growth_ok": True}, index=fundamentals.index
            ),
        )

        def _trigger(bars, config):
            fire = pd.Series(False, index=bars.index)
            fire.iloc[SIGNAL_BAR] = True
            return pd.DataFrame({"fire": fire}, index=bars.index)

        monkeypatch.setattr(dh, "entry_trigger", _trigger)
        return list(symbols)

    return _install


def _run(version, symbols, **kwargs):
    return dh.run_dividend_hybrid(
        "Test", DividendHybrid(), list(symbols),
        pd.Timestamp("2022-01-03").date(), pd.Timestamp("2026-01-01").date(),
        version=version, **kwargs,
    )


def _falling_then_flat() -> pd.DataFrame:
    """Enters at 100, falls 25%, and never recovers."""
    closes = [100.0] * (SIGNAL_BAR + 1) + list(np.linspace(100, 75, 40)) + [75.0] * (
        TOTAL_BARS - SIGNAL_BAR - 41
    )
    return _bars(closes)


def _rising() -> pd.DataFrame:
    """Enters at 100 and climbs past the +10% take profit."""
    closes = [100.0] * (SIGNAL_BAR + 1) + list(np.linspace(100, 130, TOTAL_BARS - SIGNAL_BAR - 1))
    return _bars(closes)


# --- take profit ------------------------------------------------------------


def test_take_profit_is_the_entry_date_dividend_yield(stub):
    symbols = stub(_rising())
    result = _run(dh.VERSION_A, symbols)
    trade = result.trades.iloc[0]
    assert trade["TP"] == pytest.approx(trade["EntryPrice"] * (1 + ENTRY_YIELD_PCT / 100))
    assert trade["ExitReason"] == "target"
    assert trade["PnL"] > 0


# --- Version A: no stop -----------------------------------------------------


def test_version_a_places_no_stop_and_holds_a_loser_to_the_end(stub):
    symbols = stub(_falling_then_flat())
    result = _run(dh.VERSION_A, symbols)
    trade = result.trades.iloc[0]
    assert pd.isna(trade["SL"]), "Version A must not place a stop"
    assert trade["ExitReason"] == "still_held"
    assert result.still_held == 1
    assert result.closed_trades == 0
    assert trade["PnL"] < 0


def test_version_a_tracks_max_unrealized_drawdown_during_the_hold(stub):
    symbols = stub(_falling_then_flat())
    result = _run(dh.VERSION_A, symbols)
    assert result.trades.iloc[0]["MaxUnrealizedDrawdownPct"] == pytest.approx(-25.0, abs=0.5)


def test_version_a_populates_the_drawdown_buckets(stub):
    symbols = stub(_falling_then_flat())
    result = _run(dh.VERSION_A, symbols)
    assert result.drawdown_bucket_counts[10.0] == 1
    assert result.drawdown_bucket_counts[20.0] == 1
    assert result.drawdown_bucket_counts[30.0] == 0
    assert result.drawdown_bucket_counts[40.0] == 0


def test_a_loss_past_forty_percent_raises_the_thesis_breakdown_warning(stub):
    closes = [100.0] * (SIGNAL_BAR + 1) + list(np.linspace(100, 50, 40)) + [50.0] * (
        TOTAL_BARS - SIGNAL_BAR - 41
    )
    symbols = stub(_bars(closes))
    result = _run(dh.VERSION_A, symbols)
    assert result.thesis_breakdown_trades == 1
    assert any("thesis does not survive" in w for w in result.warnings)


# --- Version B: 8% hard stop ------------------------------------------------


def test_version_b_stops_out_at_eight_percent(stub):
    symbols = stub(_falling_then_flat())
    result = _run(dh.VERSION_B, symbols)
    trade = result.trades.iloc[0]
    assert trade["ExitReason"] == "stop"
    assert trade["ExitPrice"] == pytest.approx(trade["EntryPrice"] * 0.92)
    assert result.still_held == 0


def test_version_b_caps_the_loss_version_a_leaves_open(stub):
    bars = _falling_then_flat()
    symbols = stub(bars)
    a = _run(dh.VERSION_A, symbols)
    b = _run(dh.VERSION_B, symbols)
    assert b.trades.iloc[0]["PnL"] > a.trades.iloc[0]["PnL"]


def test_stop_is_assumed_first_when_a_bar_spans_both_stop_and_target(stub):
    """A daily bar can't say which came first, so assume the adverse one."""
    closes = [100.0] * TOTAL_BARS
    highs = [100.0] * TOTAL_BARS
    lows = [100.0] * TOTAL_BARS
    highs[SIGNAL_BAR + 1] = 120.0   # spans the +10% target
    lows[SIGNAL_BAR + 1] = 80.0     # and the -8% stop
    symbols = stub(_bars(closes, highs=highs, lows=lows))
    result = _run(dh.VERSION_B, symbols)
    assert result.trades.iloc[0]["ExitReason"] == "stop"


# --- dividend cuts during a hold -------------------------------------------


def test_a_dividend_cut_during_the_hold_is_recorded_and_warned(stub):
    symbols = stub(_falling_then_flat(), cuts_from=SIGNAL_BAR + 10)
    result = _run(dh.VERSION_A, symbols)
    assert bool(result.trades.iloc[0]["DividendCutDuringHold"])
    assert result.dividend_cuts_during_hold == 1
    assert any("dividend CUT" in w for w in result.warnings)


def test_no_cut_is_recorded_when_the_dividend_holds(stub):
    symbols = stub(_falling_then_flat())
    result = _run(dh.VERSION_A, symbols)
    assert not bool(result.trades.iloc[0]["DividendCutDuringHold"])
    assert result.dividend_cuts_during_hold == 0


# --- reporting honesty ------------------------------------------------------


def test_closed_only_metrics_flatter_version_a_and_are_reported_separately(stub):
    """Version A's closed trades are 100% winners BY CONSTRUCTION -- with no
    stop, the only way to close is at target. The headline metrics must mark
    still-held positions to market; the flattering closed-only view exists
    but has to be asked for."""
    rising = _rising()
    falling = _falling_then_flat()
    frames = {"WIN": rising, "LOSE": falling}

    symbols = stub(rising, symbols=("WIN", "LOSE"))
    # Give each symbol its own path.
    dh.data_module.get_bars = lambda symbol, interval, start, end, **k: frames[symbol]

    result = _run(dh.VERSION_A, symbols)
    assert result.still_held == 1
    assert result.closed_trades == 1
    assert result.closed_only_metrics().win_rate == 1.0     # flattering
    assert result.metrics.win_rate < 1.0                    # honest


def test_concurrency_above_three_positions_warns(stub):
    """More than 3 open positions is 30%+ of the account with undefined
    downside in Version A."""
    rising = _rising()
    symbols = stub(rising, symbols=("A1", "A2", "A3", "A4", "A5"))
    result = _run(dh.VERSION_A, symbols, cash=1_000_000.0)
    assert result.max_concurrent_positions > dh.MAX_CONCURRENT_WARN
    assert any("positions open simultaneously" in w for w in result.warnings)


def test_position_size_is_ten_percent_of_equity(stub):
    symbols = stub(_rising())
    result = _run(dh.VERSION_A, symbols, cash=100_000.0)
    trade = result.trades.iloc[0]
    notional = trade["EntryPrice"] * trade["Size"]
    assert notional == pytest.approx(10_000.0, rel=0.02)


def test_entry_fills_on_the_bar_after_the_signal(stub):
    """No look-ahead: the signal bar's data decides, the NEXT bar's open
    fills."""
    bars = _rising()
    symbols = stub(bars)
    result = _run(dh.VERSION_A, symbols)
    trade = result.trades.iloc[0]
    assert trade["EntryTime"] == bars.index[SIGNAL_BAR + 1]
    assert trade["EntryPrice"] == pytest.approx(float(bars["Open"].iloc[SIGNAL_BAR + 1]))


def test_a_symbol_without_enough_history_is_skipped_not_crashed(stub):
    symbols = stub(_bars([100.0] * 50))
    result = _run(dh.VERSION_A, symbols)
    assert result.metrics.trades_taken == 0
    assert result.warnings
