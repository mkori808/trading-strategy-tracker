import pandas as pd

from strategies.day.pivot_reversal import PivotLevelEtfReversal

NY = "America/New_York"

# Prior session H/L/C = 110 / 90 / 100 -> P=100, S1=90, R1=110, S2=80, R2=120.
_PRIOR_DAY = {
    "Open": [100, 102, 108, 95, 98],
    "High": [105, 110, 109, 100, 102],
    "Low": [98, 100, 90, 92, 96],
    "Close": [102, 108, 95, 98, 100],
    "Volume": [1e6] * 5,
}


def _prior_session() -> pd.DataFrame:
    idx = pd.date_range("2024-01-02 09:30", periods=5, freq="5min", tz=NY)
    return pd.DataFrame(_PRIOR_DAY, index=idx)


def _with_today(today: dict) -> pd.DataFrame:
    idx = pd.date_range("2024-01-03 09:30", periods=len(today["Close"]), freq="5min", tz=NY)
    return pd.concat([_prior_session(), pd.DataFrame(today, index=idx)])


def test_no_signal_without_a_prior_session():
    # Only one session present -> no pivots to trade against.
    assert not PivotLevelEtfReversal().entry_signal(_prior_session())


def test_long_on_s1_reclaim():
    # Dip touches S1=90 (low 88), prior bar sits at S1, last bar reclaims above
    # it while still below the pivot P=100.
    today = {
        "Open": [99, 96, 90],
        "High": [100, 97, 94],
        "Low": [95, 88, 89],
        "Close": [96, 90, 93],
        "Volume": [1e6] * 3,
    }
    bars = _with_today(today)
    strat = PivotLevelEtfReversal()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "long"
    assert strat.stop_price(bars, 93.0) == 80.0   # S2
    assert strat.target_price(bars, 93.0) == 100.0  # P


def test_short_on_r1_rejection():
    # Spike touches R1=110 (high 112), prior bar sits at R1, last bar rejects
    # back below it while still above the pivot P=100.
    today = {
        "Open": [105, 110, 110],
        "High": [106, 112, 108],
        "Low": [101, 108, 105],
        "Close": [105, 110, 107],
        "Volume": [1e6] * 3,
    }
    bars = _with_today(today)
    strat = PivotLevelEtfReversal()
    assert strat.entry_signal(bars)
    assert strat.entry_direction(bars) == "short"
    assert strat.stop_price(bars, 107.0) == 120.0  # R2
    assert strat.target_price(bars, 107.0) == 100.0  # P


def test_no_signal_when_level_untouched():
    # Price stays mid-range between S1 and R1 all session -> nothing to fade.
    today = {
        "Open": [100, 101, 100],
        "High": [102, 102, 101],
        "Low": [99, 100, 99],
        "Close": [101, 100, 100],
        "Volume": [1e6] * 3,
    }
    assert not PivotLevelEtfReversal().entry_signal(_with_today(today))
