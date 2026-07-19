import pandas as pd

from strategies.day.news_fade import NewsFade


def test_no_signal_on_calm_market(intraday_bars_factory):
    closes = [100 + 0.05 * (i % 3) for i in range(21)]
    bars = intraday_bars_factory(closes=closes, volumes=[1e5] * 21)
    strat = NewsFade()
    assert not (strat.entry_signal(bars))


def test_spike_up_that_fails_to_hold_triggers_short(intraday_bars_factory):
    closes = [100.0] * 20 + [101.0]
    opens = [100.0] * 20 + [100.0]
    highs = [100.5] * 20 + [110.0]
    lows = [99.5] * 20 + [99.5]
    volumes = [1e5] * 20 + [5e5]
    bars = intraday_bars_factory(closes=closes, opens=opens, highs=highs, lows=lows, volumes=volumes)
    strat = NewsFade()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "short"
    stop = strat.stop_price(bars, entry_price=101.0)
    assert stop == 110.0


def test_spike_down_that_fails_to_hold_triggers_long(intraday_bars_factory):
    closes = [100.0] * 20 + [99.0]
    opens = [100.0] * 20 + [100.0]
    highs = [100.5] * 20 + [100.5]
    lows = [99.5] * 20 + [90.0]
    volumes = [1e5] * 20 + [5e5]
    bars = intraday_bars_factory(closes=closes, opens=opens, highs=highs, lows=lows, volumes=volumes)
    strat = NewsFade()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "long"
    stop = strat.stop_price(bars, entry_price=99.0)
    assert stop == 90.0


def test_first_bar_of_a_new_session_is_not_a_false_spike():
    # 20 calm bars finishing out yesterday's session, then a single bar
    # opening today's session with a big range/volume jump -- this is a
    # completely ordinary overnight gap, not an intraday spike-and-fade, and
    # must not fire just because it clears the ATR/volume bar of whatever
    # happened to trade at the end of the prior session.
    NY = "America/New_York"
    yesterday = pd.date_range("2024-01-02 15:15", periods=20, freq="5min", tz=NY)
    today = pd.date_range("2024-01-03 09:30", periods=1, freq="5min", tz=NY)
    index = yesterday.append(today)

    opens = [100.0] * 20 + [100.0]
    closes = [100.0] * 20 + [101.0]
    highs = [100.5] * 20 + [110.0]
    lows = [99.5] * 20 + [99.5]
    volumes = [1e5] * 20 + [5e5]

    bars = pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=index,
    )
    strat = NewsFade()
    assert not strat.entry_signal(bars)
