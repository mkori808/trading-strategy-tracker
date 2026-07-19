from strategies.day.vwap_bounce import VwapBounce


def _flat_then_spike_down(intraday_bars_factory):
    n_flat = 19
    closes = [100.0] * n_flat + [95.0]
    highs = [100.5] * n_flat + [100.2]
    lows = [99.5] * n_flat + [90.0]
    opens = [100.0] * n_flat + [90.0]  # last bar bullish: close 95 > open 90
    volumes = [1e5] * (n_flat + 1)
    return intraday_bars_factory(closes=closes, highs=highs, lows=lows, opens=opens, volumes=volumes)


def test_no_signal_with_too_little_history(intraday_bars_factory):
    bars = intraday_bars_factory(closes=[100, 100.1, 100.2], volumes=[1e5] * 3)
    strat = VwapBounce()
    assert not (strat.entry_signal(bars))


def test_deep_dip_with_bullish_reversal_triggers_long(intraday_bars_factory):
    bars = _flat_then_spike_down(intraday_bars_factory)
    strat = VwapBounce()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "long"
    stop = strat.stop_price(bars, entry_price=95.0)
    assert stop < bars.iloc[-1]["Low"]
