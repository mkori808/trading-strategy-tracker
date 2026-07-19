from strategies.day.orb import OpeningRangeBreakout


def test_no_signal_before_opening_range_completes(intraday_bars_factory):
    bars = intraday_bars_factory(closes=[100, 100.2, 100.4], volumes=[1e5] * 3)
    strat = OpeningRangeBreakout()
    assert not (strat.entry_signal(bars))


def test_breakout_above_range_high_on_volume_triggers_long(intraday_bars_factory):
    bars = intraday_bars_factory(
        closes=[100.2, 100.5, 100.8, 100.9, 105],
        highs=[100.5, 100.8, 101, 101, 105.5],
        lows=[99.5, 99.8, 100, 100, 101],
        opens=[100, 100.2, 100.5, 100.8, 101.2],
        volumes=[1e5, 1e5, 1e5, 1e5, 3e5],
    )
    strat = OpeningRangeBreakout()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "long"
    stop = strat.stop_price(bars, entry_price=105.0)
    assert stop == 99.5  # opposite side of the opening range


def test_breakdown_below_range_low_triggers_short(intraday_bars_factory):
    bars = intraday_bars_factory(
        closes=[100.2, 100.5, 100.8, 100.9, 95],
        highs=[100.5, 100.8, 101, 101, 100.9],
        lows=[99.5, 99.8, 100, 100, 94.5],
        opens=[100, 100.2, 100.5, 100.8, 100.9],
        volumes=[1e5, 1e5, 1e5, 1e5, 3e5],
    )
    strat = OpeningRangeBreakout()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "short"
    stop = strat.stop_price(bars, entry_price=95.0)
    assert stop == 101  # opposite side of the opening range


def test_breakdown_rejected_when_close_still_above_session_vwap(intraday_bars_factory):
    # Opening range settles around 100-105 (bars 0-3). The session then
    # craters on heavy volume toward 70, pulling session VWAP down with it.
    # The final bar is a bounce back to 98 -- numerically still below the
    # opening-range low of 100, but no longer below the now-lower session
    # VWAP: a stalled/failed breakdown, not a trend day, and the trend-day
    # filter should reject it even though the raw range/volume rule would fire.
    bars = intraday_bars_factory(
        opens=[102, 104, 103, 101, 95, 85, 75, 92],
        highs=[105, 105, 104, 102, 96, 86, 76, 99],
        lows=[101, 102, 100, 94, 84, 74, 70, 91],
        closes=[104, 103, 101, 95, 85, 75, 71, 98],
        volumes=[1e5, 1e5, 1e5, 1e5, 3e5, 3e5, 3e5, 4e5],
    )
    strat = OpeningRangeBreakout()
    assert not strat.entry_signal(bars)
