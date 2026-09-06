from strategies.swing.connors_rsi2 import ConnorsMeanReversion


def test_no_signal_with_too_little_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 101, 102], volumes=[1e6] * 3)
    strat = ConnorsMeanReversion()
    assert not strat.entry_signal(bars)


def test_uptrend_with_rsi2_oversold_triggers_long(daily_bars_factory):
    # Long, steady uptrend keeps price well above the 200-day SMA, then a
    # sharp two-day pullback drives 2-period RSI into oversold territory
    # while price is still above the SMA -- exactly the setup this rule fades.
    closes = [100 + i * 0.5 for i in range(230)]
    closes += [closes[-1] * 0.94, closes[-1] * 0.94 * 0.94]
    bars = daily_bars_factory(closes=closes, volumes=[1e6] * len(closes))

    strat = ConnorsMeanReversion()
    assert strat.entry_signal(bars)
    entry_price = float(bars.iloc[-1]["Close"])
    stop = strat.stop_price(bars, entry_price)
    assert stop < entry_price


def test_no_signal_when_price_below_200_sma(daily_bars_factory):
    # A steady downtrend: price stays below its own 200-day SMA throughout,
    # so the trend filter should block entry even during a sharp dip.
    closes = [300 - i * 0.5 for i in range(230)]
    bars = daily_bars_factory(closes=closes, volumes=[1e6] * len(closes))

    strat = ConnorsMeanReversion()
    assert not strat.entry_signal(bars)


def test_exit_when_close_reclaims_5day_sma(daily_bars_factory):
    closes = [100] * 10 + [105, 106, 107, 108, 110]
    bars = daily_bars_factory(closes=closes, volumes=[1e6] * len(closes))
    strat = ConnorsMeanReversion()
    assert strat.exit_signal(bars)
