from strategies.day.mean_reversion_scalp import MeanReversionScalp


def test_no_signal_with_too_little_history(intraday_bars_factory):
    bars = intraday_bars_factory(closes=[100, 99.8, 99.9], volumes=[1e5] * 3)
    strat = MeanReversionScalp()
    assert not (strat.entry_signal(bars))


def test_sharp_selloff_with_bullish_reversal_triggers_long(intraday_bars_factory):
    closes = [100, 98, 96, 94, 92, 90, 88, 86, 84.5]
    opens = [99, 100, 98, 96, 94, 92, 90, 88, 83]
    highs = [100.5, 100.2, 98.2, 96.2, 94.2, 92.2, 90.2, 88.2, 85]
    lows = [98.8, 97.8, 95.8, 93.8, 91.8, 89.8, 87.8, 85.8, 82]
    bars = intraday_bars_factory(closes=closes, opens=opens, highs=highs, lows=lows, volumes=[1e5] * 9)
    strat = MeanReversionScalp()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "long"
    stop = strat.stop_price(bars, entry_price=84.5)
    assert stop < bars.iloc[-1]["Low"]


def test_sharp_rally_with_bearish_reversal_triggers_short(intraday_bars_factory):
    # one small down-tick keeps RSI's loss average nonzero (an all-gain series
    # makes RSI mathematically undefined and our rsi() defaults it to neutral
    # 50 rather than overbought), then a strong rally pushes RSI >90
    closes = [100, 99, 103, 106, 109, 112, 114, 116, 117.5]
    opens = [101, 100, 99, 103, 106, 109, 112, 114, 119]
    highs = [101.2, 100.2, 103.2, 106.2, 109.2, 112.2, 114.2, 116.2, 120]
    lows = [99.8, 98.8, 98.8, 102.8, 105.8, 108.8, 111.8, 113.8, 117]
    bars = intraday_bars_factory(closes=closes, opens=opens, highs=highs, lows=lows, volumes=[1e5] * 9)
    strat = MeanReversionScalp()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "short"
    stop = strat.stop_price(bars, entry_price=117.5)
    assert stop > bars.iloc[-1]["High"]
