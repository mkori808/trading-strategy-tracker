import pandas as pd

from strategies.day.momentum_gap_go import MomentumGapAndGo

NY = "America/New_York"


def _two_session_bars():
    prior_idx = pd.date_range("2024-01-02 09:30", periods=5, freq="5min", tz=NY)
    prior = pd.DataFrame(
        {
            "Open": [99.8, 100.0, 100.1, 99.9, 100.0],
            "High": [100.2, 100.3, 100.3, 100.2, 100.2],
            "Low": [99.6, 99.8, 99.9, 99.7, 99.8],
            "Close": [100.0, 100.1, 99.9, 100.0, 100.1],
            "Volume": [1e5] * 5,
        },
        index=prior_idx,
    )
    gap_idx = pd.date_range("2024-01-03 09:30", periods=6, freq="5min", tz=NY)
    gap = pd.DataFrame(
        {
            "Open": [104.0, 104.5, 105.8, 102.5, 102.8, 103.2],
            "High": [105.0, 106.0, 106.0, 103.0, 103.5, 108.0],
            "Low": [103.5, 104.0, 102.0, 101.5, 102.0, 103.0],
            "Close": [104.5, 105.8, 102.5, 102.8, 103.2, 107.5],
            "Volume": [1e5, 1e5, 1e5, 1e5, 1e5, 5e5],
        },
        index=gap_idx,
    )
    return pd.concat([prior, gap])


def test_no_signal_without_a_gap(intraday_bars_factory):
    bars = intraday_bars_factory(closes=[100, 100.1, 100.2, 100.1, 100.3, 100.2], volumes=[1e5] * 6)
    strat = MomentumGapAndGo()
    assert not (strat.entry_signal(bars))


def test_gap_up_with_pullback_and_breakout_triggers_long():
    bars = _two_session_bars()
    strat = MomentumGapAndGo()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "long"
    stop = strat.stop_price(bars, entry_price=107.5)
    assert stop == 101.5  # the pullback low
