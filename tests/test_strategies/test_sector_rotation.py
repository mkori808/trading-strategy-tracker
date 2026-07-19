from engine.indicators import ema
from strategies.swing.sector_rotation import SectorRotationPlay, _weekly

RS_FAST = SectorRotationPlay.rs_fast
RS_SLOW = SectorRotationPlay.rs_slow


def _build_bars(daily_bars_factory):
    decline = [120 - i * 0.5 for i in range(60)]  # ~12 weeks underperforming
    rise = [90 + i * 1.0 for i in range(140)]  # ~28 weeks outperforming
    sector_closes = decline + rise
    n = len(sector_closes)
    sector_bars = daily_bars_factory(closes=sector_closes, volumes=[1e6] * n)
    spy_bars = daily_bars_factory(closes=[100.0] * n, volumes=[1e6] * n)  # flat benchmark
    return sector_bars, spy_bars


def test_no_signal_with_too_little_history(daily_bars_factory):
    sector_bars, spy_bars = _build_bars(daily_bars_factory)
    strat = SectorRotationPlay(benchmark_bars=spy_bars.iloc[:5])
    assert not (strat.entry_signal(sector_bars.iloc[:5]))


def test_relative_strength_turning_up_triggers_long(daily_bars_factory):
    sector_bars, spy_bars = _build_bars(daily_bars_factory)

    sector_w, spy_w = _weekly(sector_bars), _weekly(spy_bars)
    common = sector_w.index.intersection(spy_w.index)
    rs = sector_w.loc[common, "Close"] / spy_w.loc[common, "Close"]
    rs_fast, rs_slow = ema(rs, RS_FAST), ema(rs, RS_SLOW)
    crossed = (rs_fast.shift(1) <= rs_slow.shift(1)) & (rs_fast > rs_slow)
    candidates = crossed.iloc[12:]  # skip the decline phase
    cross_week = candidates[candidates].index[0]

    sector_slice = sector_bars.loc[:cross_week]
    spy_slice = spy_bars.loc[:cross_week]

    strat = SectorRotationPlay(benchmark_bars=spy_slice)
    assert strat.entry_signal(sector_slice)
    entry_price = float(sector_slice.iloc[-1]["Close"])
    stop = strat.stop_price(sector_slice, entry_price)
    assert stop < entry_price
