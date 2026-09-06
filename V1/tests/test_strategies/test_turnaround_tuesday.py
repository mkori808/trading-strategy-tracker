from strategies.swing.turnaround_tuesday import TurnaroundTuesday


def _uptrend_then_dip(factory):
    # 200 bars rising 60 -> ~120 (so price sits well above its 200-SMA),
    # then a multi-session decline (drives 2-period RSI < 10).
    rise = [60 + i * 60 / 200 for i in range(200)]
    decline = [118, 116, 114, 112, 110, 108, 106, 104]
    return factory(closes=rise + decline)


def test_no_signal_with_too_little_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100.0] * 50)
    assert not TurnaroundTuesday().entry_signal(bars)


def test_entry_only_on_monday(daily_bars_factory):
    bars = _uptrend_then_dip(daily_bars_factory)
    strat = TurnaroundTuesday()

    monday = bars
    while monday.index[-1].weekday() != 0:
        monday = monday.iloc[:-1]
    assert monday.index[-1].weekday() == 0
    assert strat.entry_signal(monday)  # Monday + oversold + uptrend

    non_monday = bars
    while non_monday.index[-1].weekday() == 0:
        non_monday = non_monday.iloc[:-1]
    # Same oversold-uptrend state, only the weekday differs -> no entry.
    assert not strat.entry_signal(non_monday)


def test_exit_on_first_up_close(daily_bars_factory):
    strat = TurnaroundTuesday()
    up = daily_bars_factory(closes=[100.0, 99.0, 101.0])   # last close > prior
    assert strat.exit_signal(up)
    down = daily_bars_factory(closes=[100.0, 99.0, 98.0])  # last close < prior
    assert not strat.exit_signal(down)


def test_stop_is_below_entry(daily_bars_factory):
    bars = _uptrend_then_dip(daily_bars_factory)
    strat = TurnaroundTuesday()
    entry = float(bars["Close"].iloc[-1])
    assert strat.stop_price(bars, entry) < entry
