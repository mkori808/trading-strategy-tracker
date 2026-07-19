from strategies.swing.oversold_bounce import OversoldBounce


def test_no_signal_with_too_little_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 99, 98], volumes=[1e6] * 3)
    strat = OversoldBounce()
    assert not (strat.entry_signal(bars))


def test_oversold_bounce_at_support_triggers_long(daily_bars_factory):
    # needs RSI_PERIOD (14) + SUPPORT_LOOKBACK (20) = 34 prior bars minimum
    closes = [130 - i for i in range(40)]  # steady decline -> RSI(14) deeply oversold
    base = daily_bars_factory(closes=closes, volumes=[1e6] * 40)

    import pandas as pd

    next_day = pd.bdate_range(start=base.index[-1], periods=2, tz=base.index.tz)[1:2]
    last_bar = pd.DataFrame(
        {"Open": [90.0], "High": [92.0], "Low": [89.0], "Close": [91.5], "Volume": [1e6]},
        index=next_day,
    )
    bars = pd.concat([base, last_bar])

    strat = OversoldBounce()
    assert strat.entry_signal(bars)
    stop = strat.stop_price(bars, entry_price=91.5)
    assert stop == 89.0 * 0.99
