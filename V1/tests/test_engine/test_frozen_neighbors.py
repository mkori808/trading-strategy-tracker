from math import prod

from engine.frozen_protocol import family_record
from engine.run_frozen_neighbors import (
    DISPLAY_NAMES, IMPLEMENTATION_REVISIONS, _engine_params,
    _experiment_config, _grid,
)


def test_each_predeclared_grid_has_exact_registered_width():
    expected = {
        "52-Week-High Momentum": 72,
        "Negative Return + Volume Shock Reversal": 96,
        "Market-Residual Momentum": 27,
        "Volume-Shock Continuation": 54,
        "MAX Lottery-Return Reversal": 72,
        "Volatility-Conditioned Pullback": 54,
    }
    for family, width in expected.items():
        neighbors = family_record(family)["neighbors"]
        assert prod(len(values) for values in neighbors.values()) == width
        assert len(list(_grid(neighbors))) == width


def test_protocol_labels_translate_without_changing_values():
    maximum = _engine_params(
        "MAX Lottery-Return Reversal",
        {"max_threshold": 0.08, "volume_ratio": 1.5,
         "holding_sessions": 5, "earningsExclusion": False},
    )
    assert maximum["earnings_exclusion"] is False
    assert "earningsExclusion" not in maximum

    pullback = _engine_params(
        "Volatility-Conditioned Pullback",
        {"rsi_threshold": 5.0, "atrRegime": "middle_60",
         "market_trend_filter": True, "holding_sessions": 5},
    )
    assert pullback["atr_percentile_low"] == 0.2
    assert pullback["atr_percentile_high"] == 0.8


def test_long_and_short_variants_are_distinct_inside_shared_family():
    names = DISPLAY_NAMES["Volume-Shock Continuation"]
    assert names == (
        "Volume-Shock Continuation (Long)",
        "Volume-Shock Continuation (Short)",
    )
    assert family_record(names[0])["searchFamily"] == family_record(names[1])["searchFamily"]
    params = {"volume_ratio": 2.0}
    assert _experiment_config("Volume-Shock Continuation", names[0], params) != (
        _experiment_config("Volume-Shock Continuation", names[1], params)
    )


def test_corrected_implementation_gets_distinct_experiment_identity():
    config = _experiment_config(
        "Market-Residual Momentum", "Market-Residual Momentum",
        {"lookback": 126},
    )
    assert config["implementationRevision"] == IMPLEMENTATION_REVISIONS[
        "Market-Residual Momentum"
    ]
    assert config["window"] == {"start": "2021-08-14", "end": "2026-08-13"}
