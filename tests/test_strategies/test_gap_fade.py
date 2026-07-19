import pandas as pd

from strategies.swing.gap_fade import GapFade


def test_no_signal_with_too_little_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 101, 102], volumes=[1e6] * 3)
    strat = GapFade()
    assert not strat.entry_signal(bars)


def test_large_gap_up_triggers_short(daily_bars_factory):
    # 21 calm bars (tight range -> small ATR), then a bar that gaps way up
    # at the open relative to the prior close.
    calm_closes = [100.0] * 21
    calm = daily_bars_factory(
        closes=calm_closes,
        highs=[100.5] * 21,
        lows=[99.5] * 21,
        opens=[100.0] * 21,
        volumes=[1e6] * 21,
    )

    next_day = pd.bdate_range(start=calm.index[-1], periods=2, tz=calm.index.tz)[1:2]
    gap_bar = pd.DataFrame(
        {"Open": [110.0], "High": [111.0], "Low": [109.0], "Close": [110.5], "Volume": [1e6]},
        index=next_day,
    )
    bars = pd.concat([calm, gap_bar])

    strat = GapFade()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "short"
    entry_price = float(bars.iloc[-1]["Close"])
    stop = strat.stop_price(bars, entry_price)
    assert stop == 111.0  # beyond the gap extreme (the bar's high)
    target = strat.target_price(bars, entry_price)
    assert target < 110.0  # partial reversion back toward the prior close of 100


def test_small_gap_does_not_trigger(daily_bars_factory):
    calm_closes = [100.0] * 21
    calm = daily_bars_factory(
        closes=calm_closes, highs=[100.5] * 21, lows=[99.5] * 21, opens=[100.0] * 21, volumes=[1e6] * 21,
    )

    next_day = pd.bdate_range(start=calm.index[-1], periods=2, tz=calm.index.tz)[1:2]
    small_gap_bar = pd.DataFrame(
        {"Open": [100.3], "High": [100.6], "Low": [100.1], "Close": [100.4], "Volume": [1e6]},
        index=next_day,
    )
    bars = pd.concat([calm, small_gap_bar])

    strat = GapFade()
    assert not strat.entry_signal(bars)
