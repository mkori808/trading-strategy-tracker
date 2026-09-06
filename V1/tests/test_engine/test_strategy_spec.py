"""strategies/spec.py: the rule DSL natural-language authoring compiles
into, and the guarantees that make a generated strategy as trustworthy as a
hand-written one -- validation refuses rather than defaults, tunable numbers
become real param_field()s, and no rule can read a bar it shouldn't."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from strategies.params import apply_params, describe_params
from strategies.spec import (
    describe_spec,
    parse_spec,
    required_bars,
    spec_strategy_class,
    vocabulary,
)


def _bars(n: int = 400, seed: int = 0) -> pd.DataFrame:
    index = pd.date_range("2022-01-03", periods=n, freq="B", tz="America/New_York")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1.5, n)), index=index).abs() + 40
    return pd.DataFrame(
        {
            "Open": close.shift(1).fillna(close.iloc[0]),
            "High": close * 1.012,
            "Low": close * 0.988,
            "Close": close,
            "Volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
        },
        index=index,
    )


RSI_DIP = {
    "name": "RSI Dip Above Trend",
    "kind": "Swing Trading",
    "timeframe": "1d",
    "direction": "long",
    "description": "Buy an oversold pullback while price holds above its long average.",
    "params": [
        {
            "name": "rsi_threshold", "label": "RSI oversold threshold", "kind": "int",
            "default": 35, "minimum": 10, "maximum": 45, "step": 1,
        },
        {
            "name": "target_multiple", "label": "Target (x risk)", "kind": "float",
            "default": 2.0, "minimum": 0.5, "maximum": 5.0, "step": 0.5,
        },
    ],
    "entry": [
        {
            "left": {"kind": "rsi", "args": {"period": 14}}, "op": "<",
            "right": {"kind": "constant", "args": {"value": {"param": "rsi_threshold"}}},
        },
        {"left": {"kind": "close"}, "op": ">", "right": {"kind": "sma", "args": {"period": 100}}},
    ],
    "stop": {"kind": "atr_multiple", "args": {"period": 14, "multiple": 2.0}},
    "target": {"kind": "risk_multiple", "args": {"multiple": {"param": "target_multiple"}}},
    "exit": [],
}


def test_a_spec_round_trips_through_its_own_serialization():
    spec = parse_spec(RSI_DIP)
    assert parse_spec(spec.to_dict()).to_dict() == spec.to_dict()


def test_tunable_numbers_become_real_param_fields():
    """The compiled class is an ordinary @dataclass strategy, so the Lab
    tab's slider schema and apply_params' bounds checking work on it with no
    custom-strategy branch anywhere."""
    cls = spec_strategy_class(parse_spec(RSI_DIP))
    specs = {s.name: s for s in describe_params(cls)}

    assert set(specs) == {"rsi_threshold", "target_multiple"}
    assert specs["rsi_threshold"].kind == "int"
    assert (specs["rsi_threshold"].minimum, specs["rsi_threshold"].maximum) == (10, 45)

    tuned = apply_params(cls(), {"rsi_threshold": 25})
    assert tuned.rsi_threshold == 25
    with pytest.raises(ValueError):
        apply_params(cls(), {"rsi_threshold": 90})  # above its declared maximum
    with pytest.raises(ValueError):
        apply_params(cls(), {"not_a_param": 1})


def test_name_timeframe_and_direction_are_plain_class_attributes():
    """Same rule the hand-written strategies follow: @dataclass only turns
    ANNOTATED attributes into fields, so these stay out of __init__ and out
    of dataclasses.replace()."""
    cls = spec_strategy_class(parse_spec(RSI_DIP))
    field_names = {f.name for f in cls.__dataclass_fields__.values()}
    assert field_names == {"rsi_threshold", "target_multiple"}
    assert cls.name == "RSI Dip Above Trend"
    assert cls.timeframe == "1d"
    assert cls.direction == "long"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ({"entry": [{"left": {"kind": "telepathy"}, "op": ">", "right": {"kind": "close"}}]},
         "unknown indicator"),
        ({"target": {"kind": "none"}, "exit": []}, "exit condition"),
        ({"timeframe": "5m"}, "kind and timeframe disagree"),
        ({"entry": [{"left": {"kind": "close"}, "op": "≈", "right": {"kind": "close"}}]}, "op must be"),
        ({"entry": [{"left": {"kind": "close", "offset": 999}, "op": ">", "right": {"kind": "close"}}]},
         "offset must be"),
        ({"stop": {"kind": "atr_multiple", "args": {"period": 14}}}, "missing required argument"),
        ({"stop": {"kind": "hope", "args": {}}}, "stop.kind must be one of"),
        ({"direction": "both"}, "direction must be"),
    ],
)
def test_validation_refuses_rather_than_defaulting(mutation, message):
    """Every rejection names the offending path. Nothing is silently
    dropped or replaced with a plausible-looking default -- a spec that
    can't be trusted must not become a runnable strategy."""
    with pytest.raises(ValueError, match=message):
        parse_spec({**RSI_DIP, **mutation})


def test_a_param_reference_must_resolve_and_a_declared_param_must_be_used():
    undeclared = {**RSI_DIP, "params": []}
    with pytest.raises(ValueError, match="not declared in params"):
        parse_spec(undeclared)

    unused = {
        **RSI_DIP,
        "params": [*RSI_DIP["params"], {
            "name": "spare", "label": "Spare", "kind": "float",
            "default": 1.0, "minimum": 0.0, "maximum": 2.0,
        }],
    }
    with pytest.raises(ValueError, match="never used"):
        parse_spec(unused)


def test_session_vwap_is_rejected_on_daily_bars():
    """It resets per calendar day, so on daily bars it collapses to that
    one day's typical price -- a rule that silently means something else."""
    with pytest.raises(ValueError, match="intraday indicator"):
        parse_spec({
            **RSI_DIP,
            "entry": [{"left": {"kind": "close"}, "op": ">", "right": {"kind": "vwap"}}],
        })


def test_entry_signal_ignores_bars_that_have_not_happened_yet():
    """The look-ahead guarantee, asserted by recomputation rather than by
    inspection: a signal decided at bar i must not change when later bars
    are appended, no matter how extreme they are."""
    spec = parse_spec(RSI_DIP)
    strategy = spec_strategy_class(spec)()
    bars = _bars()

    for i in range(250, 300):
        truncated = bars.iloc[: i + 1]
        # A wildly different future: a spec that peeked would flip on it.
        distorted = bars.copy()
        distorted.iloc[i + 1 :] *= 5.0
        assert strategy.entry_signal(truncated) == strategy.entry_signal(distorted.iloc[: i + 1])
        assert strategy.entry_signal(truncated) == strategy.entry_signal(
            pd.concat([truncated, distorted.iloc[i + 1 : i + 20]]).iloc[: i + 1]
        )


def test_a_breakout_level_excludes_the_current_bar():
    """rolling_high is the highest high of the N bars BEFORE this one --
    including the current bar's own high would make "close above the N-bar
    high" a tautology-or-impossibility rather than a breakout test."""
    spec = parse_spec({
        "name": "Twenty Day Breakout", "kind": "Swing Trading", "timeframe": "1d",
        "direction": "long", "description": "Buy a 20-day breakout.",
        "params": [],
        "entry": [{
            "left": {"kind": "close"}, "op": ">",
            "right": {"kind": "rolling_high", "args": {"period": 20}},
        }],
        "stop": {"kind": "percent", "args": {"pct": 5.0}},
        "target": {"kind": "risk_multiple", "args": {"multiple": 2.0}},
        "exit": [],
    })
    strategy = spec_strategy_class(spec)()
    bars = _bars()
    fired = [i for i in range(100, 400) if strategy.entry_signal(bars.iloc[: i + 1])]
    assert fired, "a 20-day breakout rule should fire at least once over 400 bars"
    for i in fired:
        prior_high = bars["High"].iloc[i - 20 : i].max()
        assert bars["Close"].iloc[i] > prior_high


def test_stop_and_target_sit_on_the_correct_side_for_each_direction():
    bars = _bars()
    long_strategy = spec_strategy_class(parse_spec(RSI_DIP))()
    short_spec = parse_spec({**RSI_DIP, "name": "RSI Pop Short", "direction": "short"})
    short_strategy = spec_strategy_class(short_spec)()

    assert long_strategy.stop_price(bars, 100.0) < 100.0
    assert long_strategy.target_price(bars, 100.0) > 100.0
    assert short_strategy.stop_price(bars, 100.0) > 100.0
    assert short_strategy.target_price(bars, 100.0) < 100.0


def test_a_signal_exit_strategy_may_omit_a_target():
    spec = parse_spec({
        "name": "EMA Cross Ride", "kind": "Swing Trading", "timeframe": "1d",
        "direction": "long", "description": "Ride an EMA crossover until it reverses.",
        "params": [],
        "entry": [{
            "left": {"kind": "ema", "args": {"period": 9}}, "op": "crosses_above",
            "right": {"kind": "ema", "args": {"period": 21}},
        }],
        "exit": [{
            "left": {"kind": "ema", "args": {"period": 9}}, "op": "crosses_below",
            "right": {"kind": "ema", "args": {"period": 21}},
        }],
        "stop": {"kind": "swing_extreme", "args": {"lookback": 20, "buffer_pct": 1.0}},
        "target": {"kind": "none", "args": {}},
    })
    strategy = spec_strategy_class(spec)()
    bars = _bars()
    assert strategy.target_price(bars, 100.0) is None
    assert any(strategy.entry_signal(bars.iloc[: i + 1]) for i in range(100, 400))
    assert any(strategy.exit_signal(bars.iloc[: i + 1]) for i in range(100, 400))


def test_an_or_group_fires_when_either_side_holds():
    spec = parse_spec({
        **RSI_DIP,
        "name": "RSI Or Band Dip",
        "entry": [{
            "any": [
                {
                    "left": {"kind": "rsi", "args": {"period": 14}}, "op": "<",
                    "right": {"kind": "constant", "args": {"value": {"param": "rsi_threshold"}}},
                },
                {
                    "left": {"kind": "close"}, "op": "<",
                    "right": {"kind": "bollinger_lower", "args": {"period": 20, "stddev": 2.0}},
                },
            ]
        }],
        "target": {"kind": "risk_multiple", "args": {"multiple": {"param": "target_multiple"}}},
    })
    strategy = spec_strategy_class(spec)()
    bars = _bars()
    assert any(strategy.entry_signal(bars.iloc[: i + 1]) for i in range(100, 400))


def test_no_signal_fires_before_the_spec_has_enough_history():
    spec = parse_spec(RSI_DIP)
    strategy = spec_strategy_class(spec)()
    bars = _bars()
    warmup = required_bars(spec, {p.name: p.default for p in spec.params})
    assert warmup >= 100  # the SMA(100) leg dominates
    for i in range(warmup):
        assert strategy.entry_signal(bars.iloc[: i + 1]) is False


def test_describe_spec_renders_the_parsed_rules_not_the_raw_input():
    """The authoring UI shows this rendering, so it has to describe the
    COMPILED rule -- including a param's label and default in place of the
    reference, so a reader sees the number that will actually be used."""
    rendered = describe_spec(parse_spec(RSI_DIP))
    assert "RSI(14) is below RSI oversold threshold (35)" in rendered["entry"]
    assert "close is above SMA(100)" in rendered["entry"]
    assert "ATR(14) below the entry price" in rendered["stop"]
    assert "initial risk" in rendered["target"]
    assert rendered["warmupBars"] >= 100


def test_vocabulary_covers_every_indicator_the_parser_accepts():
    """One source for the authoring prompt and the UI's "what you can
    reference" note -- a new indicator can't be addable-but-undocumented."""
    from strategies.spec import INDICATORS

    assert {item["kind"] for item in vocabulary()} == set(INDICATORS)
    assert all(item["description"] for item in vocabulary())
