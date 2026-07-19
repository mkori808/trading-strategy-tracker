from datetime import date

import pandas as pd

from engine.overnight import run_overnight_backtest
from strategies.swing.overnight_hold import OvernightHold

NY = "America/New_York"


def _bars(closes, opens):
    idx = pd.bdate_range("2023-01-02", periods=len(closes), tz=NY)
    closes = pd.Series(closes, index=idx, dtype=float)
    opens = pd.Series(opens, index=idx, dtype=float)
    highs = pd.concat([opens, closes], axis=1).max(axis=1) + 1
    lows = pd.concat([opens, closes], axis=1).min(axis=1) - 1
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": 1e6}, index=idx
    )


def test_positive_overnight_gaps_are_all_wins(monkeypatch):
    # 205 rising sessions (so Close > 200-SMA in the tradeable region), each
    # night gapping up +0.3 (Open[t+1] = Close[t] + 0.3).
    n = 205
    closes = [100 + 0.5 * i for i in range(n)]
    opens = [closes[0]] + [closes[i - 1] + 0.3 for i in range(1, n)]
    bars = _bars(closes, opens)
    monkeypatch.setattr("engine.overnight.data_module.get_bars", lambda *a, **k: bars)

    result = run_overnight_backtest(
        "Overnight Hold", OvernightHold(), ["SPY"], date(2023, 1, 1), date(2024, 1, 1)
    )
    m = result.metrics
    assert m.trades_taken > 0
    assert m.win_rate == 1.0          # every overnight gap was positive
    assert m.expectancy_r > 0


def test_downtrend_blocks_entries(monkeypatch):
    # Falling the whole way -> price never above its 200-SMA -> no overnight
    # positions are ever taken.
    n = 205
    closes = [200 - 0.5 * i for i in range(n)]
    opens = [closes[0]] + [closes[i - 1] for i in range(1, n)]
    bars = _bars(closes, opens)
    monkeypatch.setattr("engine.overnight.data_module.get_bars", lambda *a, **k: bars)

    result = run_overnight_backtest(
        "Overnight Hold", OvernightHold(), ["SPY"], date(2023, 1, 1), date(2024, 1, 1)
    )
    assert result.metrics.trades_taken == 0
