from strategies.day.range_trading import RangeTrading


def test_no_signal_with_too_little_range_history(intraday_bars_factory):
    bars = intraday_bars_factory(closes=[100, 100.1, 100.2], volumes=[1e5] * 3)
    strat = RangeTrading()
    assert not (strat.entry_signal(bars))


def test_rejection_at_range_low_triggers_long(intraday_bars_factory):
    closes = [102, 97] + [100] * 18 + [97]
    opens = [100, 100] + [100] * 18 + [95.1]
    highs = [105, 101] + [101] * 18 + [97.2]
    lows = [100, 95] + [99] * 18 + [95.05]
    bars = intraday_bars_factory(closes=closes, opens=opens, highs=highs, lows=lows, volumes=[1e5] * 21)
    strat = RangeTrading()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "long"
    stop = strat.stop_price(bars, entry_price=97.0)
    assert stop < 95
    target = strat.target_price(bars, entry_price=97.0)
    assert target == 105
