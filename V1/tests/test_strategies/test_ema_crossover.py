from engine.indicators import ema
from strategies.swing.ema_crossover import Ema9_21Crossover


def _crossed_up_bars(daily_bars_factory):
    closes = [130 - i for i in range(20)] + [110 + 2 * i for i in range(30)]
    bars = daily_bars_factory(closes=closes, volumes=[1e6] * len(closes))
    ema9, ema21 = ema(bars["Close"], 9), ema(bars["Close"], 21)
    crossed = (ema9.shift(1) <= ema21.shift(1)) & (ema9 > ema21)
    # skip the trivial artifact crossing near bar 0, where both EMAs start equal
    candidates = crossed.iloc[20:]
    cross_idx = candidates[candidates].index[0]
    pos = bars.index.get_loc(cross_idx)
    return bars.iloc[: pos + 1]


def test_no_signal_with_too_little_history(daily_bars_factory):
    bars = daily_bars_factory(closes=[100, 101, 102], volumes=[1e6] * 3)
    strat = Ema9_21Crossover()
    assert not (strat.entry_signal(bars))


def test_crossover_up_with_both_sloping_up_triggers_long(daily_bars_factory):
    bars = _crossed_up_bars(daily_bars_factory)
    strat = Ema9_21Crossover()
    assert strat.entry_signal(bars)
    entry_price = float(bars.iloc[-1]["Close"])
    stop = strat.stop_price(bars, entry_price)
    assert stop < entry_price


def test_exit_signal_fires_on_crossunder(daily_bars_factory):
    up_closes = [130 - i for i in range(20)] + [110 + 2 * i for i in range(30)]
    down_closes = [200 - 3 * i for i in range(30)]
    bars = daily_bars_factory(closes=up_closes + down_closes, volumes=[1e6] * (len(up_closes) + len(down_closes)))
    ema9, ema21 = ema(bars["Close"], 9), ema(bars["Close"], 21)
    crossed_down = (ema9.shift(1) >= ema21.shift(1)) & (ema9 < ema21)
    candidates = crossed_down.iloc[len(up_closes):]
    cross_idx = candidates[candidates].index[0]
    pos = bars.index.get_loc(cross_idx)
    trimmed = bars.iloc[: pos + 1]

    strat = Ema9_21Crossover()
    assert strat.exit_signal(trimmed)
