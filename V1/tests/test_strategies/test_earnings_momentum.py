from strategies.swing.earnings_momentum import EarningsMomentumGapHold


def test_no_signal_with_too_little_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 100.1, 100.2], volumes=[1e5] * 3)
    strat = EarningsMomentumGapHold()
    assert not (strat.entry_signal(bars))


def test_gap_then_hold_then_continuation_triggers_long(daily_bars_factory):
    closes = [100, 100, 100, 100, 100, 100, 104, 103, 105, 104, 106, 105, 104, 105, 106, 110]
    opens = [100, 100, 100, 100, 100, 100, 105, 104, 103, 105, 104, 106, 105, 104, 105, 106]
    volumes = [1e5] * 6 + [3e5] + [1e5] * 8 + [1e5]

    bars = daily_bars_factory(closes=closes, opens=opens, volumes=volumes)
    strat = EarningsMomentumGapHold()
    assert strat.entry_signal(bars)
    stop = strat.stop_price(bars, entry_price=110.0)
    assert stop == 103.0 * 0.99  # 0.99x the post-gap consolidation low
