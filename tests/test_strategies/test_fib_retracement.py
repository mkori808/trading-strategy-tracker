from strategies.swing.fib_retracement import FibonacciRetracementEntry


def test_no_signal_with_too_little_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 101, 102], volumes=[1e6] * 3)
    strat = FibonacciRetracementEntry()
    assert not (strat.entry_signal(bars))


def test_pullback_into_fib_zone_with_bullish_candle_triggers_long(daily_bars_factory):
    rally = [100 + i * (50 / 19) for i in range(20)]  # 100 -> 150
    pullback = [145, 140, 133, 127, 123]
    hist = rally + pullback  # 25 zero-range historical bars (O=H=L=C)

    closes = hist + [123]
    opens = hist + [121]
    highs = hist + [124]
    lows = hist + [120]

    bars = daily_bars_factory(closes=closes, opens=opens, highs=highs, lows=lows, volumes=[1e6] * len(closes))
    strat = FibonacciRetracementEntry()
    assert strat.entry_signal(bars)
    stop = strat.stop_price(bars, entry_price=123.0)
    assert stop < 120.0
