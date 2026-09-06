from strategies.day.scalping import Scalping


def test_no_signal_with_too_little_history(intraday_bars_factory):
    bars = intraday_bars_factory(closes=[100, 100.1, 100.2], volumes=[1e5] * 3)
    strat = Scalping()
    assert not (strat.entry_signal(bars))


def test_sustained_uptrend_gives_bullish_confluence(intraday_bars_factory):
    closes = [100 + 0.5 * i for i in range(30)]
    bars = intraday_bars_factory(closes=closes, volumes=[1e5] * 30)
    strat = Scalping()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "long"
    entry_price = closes[-1]
    stop = strat.stop_price(bars, entry_price)
    assert stop < entry_price


def test_sustained_downtrend_gives_bearish_confluence(intraday_bars_factory):
    closes = [130 - 0.5 * i for i in range(30)]
    bars = intraday_bars_factory(closes=closes, volumes=[1e5] * 30)
    strat = Scalping()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "short"
    entry_price = closes[-1]
    stop = strat.stop_price(bars, entry_price)
    assert stop > entry_price
