import numpy as np
import pandas as pd
import pytest

from engine import dm_stock_characteristics as study


def _bars(periods=320, future_jump=False):
    index = pd.bdate_range("2024-01-02", periods=periods, tz="America/New_York")
    close = np.linspace(100.0, 180.0, periods)
    if future_jump:
        close[-1] = 10_000.0
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * .99,
        "Close": close, "Volume": np.linspace(1_000_000, 2_000_000, periods),
    }, index=index)


def test_features_use_only_bars_strictly_before_rebalance():
    bars = _bars()
    spy = _bars()
    decision = bars.index[-1]
    baseline = study.point_in_time_features(bars, spy, decision)
    changed = bars.copy()
    changed.loc[decision, ["Close", "Volume"]] = [50_000.0, 900_000_000]

    assert study.point_in_time_features(changed, spy, decision) == baseline
    assert baseline["volatility_63d"] is not None
    assert baseline["distance_from_252d_high"] <= 0


def test_future_rows_cannot_change_a_past_feature_row():
    bars = _bars()
    spy = _bars()
    decision = bars.index[-5]
    baseline = study.point_in_time_features(bars.loc[:decision], spy.loc[:decision], decision)
    extended = pd.concat([
        bars,
        pd.DataFrame({
            "Open": [50_000.0], "High": [60_000.0], "Low": [40_000.0],
            "Close": [55_000.0], "Volume": [999_000_000],
        }, index=[bars.index[-1] + pd.offsets.BDay()]),
    ])

    assert study.point_in_time_features(extended, spy, decision) == baseline


def test_effect_demeans_each_rebalance_and_uses_cluster_count():
    rows = []
    for day, level in (("2026-01-02", 0.20), ("2026-02-02", -0.10), ("2026-03-02", 0.05)):
        for z in (-1.0, 0.0, 1.0):
            rows.append({
                "rebalance_date": pd.Timestamp(day),
                "active_return": level + 0.02 * z,
                "volatility_63d_z": z,
            })
    frame = pd.DataFrame(rows)

    result = study._effect(frame, "volatility_63d")

    assert result["effect"] == pytest.approx(0.02)
    assert result["clusters"] == 3
    assert result["n"] == 9


def test_top_two_bottom_two_buckets_exclude_months_with_overlap():
    frame = pd.DataFrame([
        *({"rebalance_date": "2026-01-02", "volatility_63d": value,
           "volatility_63d_z": z, "active_return": outcome}
          for value, z, outcome in ((.1, -1.2, -.01), (.2, -.4, 0), (.3, .4, .01), (.4, 1.2, .02))),
        {"rebalance_date": "2026-02-02", "volatility_63d": .1,
         "volatility_63d_z": -1, "active_return": -1.0},
        {"rebalance_date": "2026-02-02", "volatility_63d": .4,
         "volatility_63d_z": 1, "active_return": 1.0},
    ])

    result = study._bucket_summary(frame, "volatility_63d")

    assert result["lowN"] == 2 and result["highN"] == 2
    assert result["highMinusLow"] == pytest.approx(.02)


def test_feature_ledger_marks_incumbents_without_looking_at_outcomes():
    dates = pd.to_datetime(["2025-03-03", "2025-04-01"], utc=True).tz_convert("America/New_York")
    selected = pd.DataFrame([
        {"rebalance_date": dates[0], "next_rebalance_date": dates[1], "symbol": "A", "rank": 1,
         "score": .4, "forward_return": .02, "spy_forward_return": .01, "active_return": .01},
        {"rebalance_date": dates[0], "next_rebalance_date": dates[1], "symbol": "B", "rank": 2,
         "score": .3, "forward_return": .00, "spy_forward_return": .01, "active_return": -.01},
        {"rebalance_date": dates[1], "next_rebalance_date": dates[1], "symbol": "A", "rank": 1,
         "score": .5, "forward_return": .03, "spy_forward_return": .01, "active_return": .02},
        {"rebalance_date": dates[1], "next_rebalance_date": dates[1], "symbol": "C", "rank": 2,
         "score": .2, "forward_return": -.01, "spy_forward_return": .01, "active_return": -.02},
    ])
    bars = {symbol: _bars() for symbol in ("A", "B", "C")}

    ledger = study.build_feature_ledger(selected, bars, _bars())

    first = ledger.loc[ledger.rebalance_date == dates[0]]
    second = ledger.loc[ledger.rebalance_date == dates[1]].set_index("symbol")
    assert first.incumbent.isna().all()
    assert bool(second.loc["A", "incumbent"]) is True
    assert bool(second.loc["C", "incumbent"]) is False
