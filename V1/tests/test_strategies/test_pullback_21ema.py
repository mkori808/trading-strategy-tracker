import pandas as pd

from engine.indicators import ema
from strategies.swing.pullback_21ema import PullbackTo21Ema


def test_no_signal_with_too_little_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 101, 102], volumes=[1e6] * 3)
    strat = PullbackTo21Ema()
    assert not (strat.entry_signal(bars))


def test_pullback_to_rising_ema_with_bullish_candle_triggers_long(daily_bars_factory):
    closes = [100 + i for i in range(34)]
    base_bars = daily_bars_factory(closes=closes, volumes=[1e6] * 34)
    ema21_value = float(ema(base_bars["Close"], 21).iloc[-1])

    next_day = pd.bdate_range(start=base_bars.index[-1], periods=2, tz=base_bars.index.tz)[1:2]
    last_bar = pd.DataFrame(
        {
            "Open": [ema21_value - 0.3],
            "High": [ema21_value + 1.0],
            "Low": [ema21_value - 0.3],
            "Close": [ema21_value + 0.5],
            "Volume": [1e6],
        },
        index=next_day,
    )
    bars = pd.concat([base_bars, last_bar])

    strat = PullbackTo21Ema()
    assert strat.entry_signal(bars)
    entry_price = float(bars.iloc[-1]["Close"])
    stop = strat.stop_price(bars, entry_price)
    assert stop < entry_price
