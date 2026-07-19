from strategies.swing.pead import PostEarningsDrift


def test_no_entry_without_earnings(daily_bars_factory):
    bars = daily_bars_factory(closes=[100 + i for i in range(30)])
    assert not PostEarningsDrift([]).entry_signal(bars)


def test_entry_after_positive_surprise_drift(daily_bars_factory):
    # Steadily rising: reaction session (index 27) closes up, price drifts
    # further up and sits above its 20-EMA -> drift trade fires.
    bars = daily_bars_factory(closes=[100 + i for i in range(30)])
    events = [bars.index[27].date()]
    assert PostEarningsDrift(events).entry_signal(bars)


def test_no_entry_when_reaction_session_closed_down(daily_bars_factory):
    closes = [100 + i for i in range(30)]
    closes[27] = closes[26] - 5  # the reaction session itself closes DOWN
    bars = daily_bars_factory(closes=closes)
    events = [bars.index[27].date()]
    assert not PostEarningsDrift(events).entry_signal(bars)


def test_no_entry_when_event_is_stale(daily_bars_factory):
    # An earnings date far outside the entry window (session 5, current 29)
    # should not trigger a fresh entry.
    bars = daily_bars_factory(closes=[100 + i for i in range(30)])
    events = [bars.index[5].date()]
    assert not PostEarningsDrift(events).entry_signal(bars)


def test_exit_when_close_breaks_below_ema20(daily_bars_factory):
    closes = [100 + i for i in range(28)] + [90.0]  # last bar dumps below the EMA
    bars = daily_bars_factory(closes=closes)
    assert PostEarningsDrift([]).exit_signal(bars)
