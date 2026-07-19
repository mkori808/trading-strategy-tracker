from strategies.swing.breakout_consolidation import BreakoutFromConsolidation


def test_no_signal_with_too_little_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 100.1, 100.2], volumes=[1e5] * 3)
    strat = BreakoutFromConsolidation()
    assert not (strat.entry_signal(bars))


def test_close_above_consolidation_high_on_volume_triggers_long(daily_bars_factory):
    closes = [100] * 24 + [110]
    highs = [101] * 4 + [105] + [101] * 19 + [110.5]
    lows = [99] * 4 + [95] + [99] * 19 + [109.5]
    opens = [100] * 25
    volumes = [1e5] * 24 + [2e5]
    bars = daily_bars_factory(closes=closes, opens=opens, highs=highs, lows=lows, volumes=volumes)
    strat = BreakoutFromConsolidation()
    assert strat.entry_signal(bars)
    stop = strat.stop_price(bars, entry_price=110.0)
    assert stop == 100.0  # midpoint of the 95-105 consolidation range
