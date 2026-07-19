import pandas as pd

from strategies.swing.internal_bar_strength import InternalBarStrength


def test_no_signal_with_too_little_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 101, 102], volumes=[1e6] * 3)
    strat = InternalBarStrength()
    assert not strat.entry_signal(bars)


def test_close_near_low_in_uptrend_triggers_long(daily_bars_factory):
    closes = [100 + i * 0.3 for i in range(260)]
    base = daily_bars_factory(closes=closes, volumes=[1e6] * len(closes))

    next_day = pd.bdate_range(start=base.index[-1], periods=2, tz=base.index.tz)[1:2]
    low = closes[-1]
    last_bar = pd.DataFrame(
        {"Open": [low + 1.8], "High": [low + 2.0], "Low": [low], "Close": [low + 0.1], "Volume": [1e6]},
        index=next_day,
    )
    bars = pd.concat([base, last_bar])

    strat = InternalBarStrength()
    assert strat.entry_signal(bars)
    entry_price = float(bars.iloc[-1]["Close"])
    stop = strat.stop_price(bars, entry_price)
    assert stop < entry_price


def test_close_near_high_triggers_exit(daily_bars_factory):
    bars = daily_bars_factory(
        closes=[99.9], highs=[100], lows=[90], opens=[91], volumes=[1e6],
    )
    strat = InternalBarStrength()
    assert strat.exit_signal(bars)


def test_zero_range_bar_does_not_crash(daily_bars_factory):
    bars = daily_bars_factory(closes=[100], highs=[100], lows=[100], opens=[100], volumes=[1e6])
    strat = InternalBarStrength()
    assert not strat.entry_signal(bars)
    assert not strat.exit_signal(bars)
