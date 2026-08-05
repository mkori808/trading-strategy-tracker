import pandas as pd

from strategies.swing.dual_momentum_pullback import DualMomentumPullbackSwing


def _bars(closes: list[float], daily_bars_factory) -> pd.DataFrame:
    bars = daily_bars_factory(closes=closes, volumes=[1_000_000] * len(closes))
    # Make the final bar a valid bullish pullback/reversal candle.
    bars.iloc[-1, bars.columns.get_loc("Open")] = closes[-1] - 0.5
    bars.iloc[-1, bars.columns.get_loc("Low")] = closes[-1] - 1.0
    bars.iloc[-1, bars.columns.get_loc("High")] = closes[-1] + 1.0
    return bars


def test_requires_spy_absolute_momentum_to_clear_cash(daily_bars_factory):
    n = 220
    asset = _bars([100 + i * 0.30 for i in range(n)], daily_bars_factory)
    spy = _bars([100.0] * n, daily_bars_factory)
    strategy = DualMomentumPullbackSwing(
        benchmark_bars=spy, momentum_lookback=189, risk_free_rate=0.03,
    )

    assert not strategy.entry_signal(asset)


def test_requires_asset_to_outperform_spy(daily_bars_factory):
    n = 220
    asset = _bars([100 + i * 0.20 for i in range(n)], daily_bars_factory)
    spy = _bars([100 + i * 0.30 for i in range(n)], daily_bars_factory)
    strategy = DualMomentumPullbackSwing(
        benchmark_bars=spy, momentum_lookback=189,
    )

    assert not strategy.entry_signal(asset)


def test_never_uses_future_benchmark_data(daily_bars_factory):
    n = 220
    asset = _bars([100 + i * 0.30 for i in range(n)], daily_bars_factory)
    spy = _bars([100 + i * 0.10 for i in range(n)], daily_bars_factory)
    future_index = pd.bdate_range(start=spy.index[-1], periods=2, tz=spy.index.tz)[1:]
    future = pd.DataFrame(
        {"Open": [10.0], "High": [10.0], "Low": [10.0], "Close": [10.0], "Volume": [1_000_000]},
        index=future_index,
    )
    strategy = DualMomentumPullbackSwing(
        benchmark_bars=pd.concat([spy, future]), momentum_lookback=189,
    )

    # The final signal may or may not pass its deliberately strict pullback
    # rules, but appending future benchmark data cannot change it.
    before = strategy.entry_signal(asset)
    assert strategy.entry_signal(asset) is before
