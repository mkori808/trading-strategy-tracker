"""strategies/params.py: the schema every strategy's tunable rule
parameters are turned into for the Lab tab's config form."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from strategies.params import ParamSpec, apply_params, describe_params, param_field, tunable_field_names


@dataclass
class _Sample:
    name = "Sample"
    injected: str = field(default_factory=lambda: "not tunable")
    count: int = param_field(5, label="Count", minimum=1, maximum=10, step=1, help="how many")
    ratio: float = param_field(0.5, label="Ratio", minimum=0.0, maximum=1.0)
    enabled: bool = param_field(True, label="Enabled")
    mode: str = param_field("fast", label="Mode")
    frequency: str = param_field("monthly", label="Frequency", choices=["monthly", "weekly", "daily"])


class _NotADataclass:
    pass


def test_tunable_fields_are_described_with_full_metadata():
    specs = describe_params(_Sample)
    count = next(s for s in specs if s.name == "count")
    assert count == ParamSpec(
        name="count", label="Count", kind="int", default=5,
        minimum=1, maximum=10, step=1, help="how many",
    )


def test_structural_field_without_param_field_is_excluded():
    names = {s.name for s in describe_params(_Sample)}
    assert "injected" not in names
    assert names == {"count", "ratio", "enabled", "mode", "frequency"}


def test_field_order_matches_declaration_order():
    assert [s.name for s in describe_params(_Sample)] == [
        "count", "ratio", "enabled", "mode", "frequency",
    ]


@pytest.mark.parametrize(
    "field_name, expected_kind",
    [("count", "int"), ("ratio", "float"), ("enabled", "bool"), ("mode", "str")],
)
def test_kind_is_inferred_from_the_annotation(field_name, expected_kind):
    spec = next(s for s in describe_params(_Sample) if s.name == field_name)
    assert spec.kind == expected_kind


def test_bool_is_not_misclassified_as_int():
    # bool is a subclass of int in Python -- a naive isinstance/issubclass
    # check would misfire here.
    spec = next(s for s in describe_params(_Sample) if s.name == "enabled")
    assert spec.kind == "bool"


def test_describe_params_accepts_an_instance_too():
    assert describe_params(_Sample()) == describe_params(_Sample)


def test_non_dataclass_strategy_returns_empty_schema_not_an_error():
    # Pivot-Level ETF Reversal has no tunable constants and was never
    # converted to a @dataclass -- describe_params must degrade gracefully.
    assert describe_params(_NotADataclass) == []


def test_tunable_field_names_matches_describe_params():
    assert tunable_field_names(_Sample) == {"count", "ratio", "enabled", "mode", "frequency"}


# --- spot-check a handful of real strategies -------------------------------


def test_pullback_21ema_schema():
    from strategies.swing.pullback_21ema import PullbackTo21Ema

    names = {s.name for s in describe_params(PullbackTo21Ema)}
    assert names == {
        "pullback_atr_tolerance", "trend_lookback", "stop_swing_lookback", "stop_buffer_pct",
    }


def test_sector_rotation_excludes_benchmark_bars():
    from strategies.swing.sector_rotation import SectorRotationPlay

    names = {s.name for s in describe_params(SectorRotationPlay)}
    assert "benchmark_bars" not in names
    assert names == {"rs_fast", "rs_slow", "support_lookback_weeks", "stop_buffer_pct"}


def test_pead_excludes_positive_earnings():
    from strategies.swing.pead import PostEarningsDrift

    names = {s.name for s in describe_params(PostEarningsDrift)}
    assert "positive_earnings" not in names
    assert names == {"entry_window_bars", "ema_period", "stop_atr_multiple", "atr_period"}


def test_dual_momentum_excludes_risk_free_rate():
    from strategies.swing.dual_momentum import DualMomentum

    names = {s.name for s in describe_params(DualMomentum)}
    assert "risk_free_rate" not in names
    assert names == {"lookback_trading_days", "top_n", "rebalance_frequency"}


def test_dual_momentum_rebalance_frequency_defaults_to_monthly():
    from strategies.swing.dual_momentum import DualMomentum

    spec = next(s for s in describe_params(DualMomentum) if s.name == "rebalance_frequency")
    assert spec.default == "monthly"
    assert spec.choices == ["monthly", "weekly", "daily"]
    assert spec.kind == "str"


def test_choices_field_is_exposed_on_the_spec():
    spec = next(s for s in describe_params(_Sample) if s.name == "frequency")
    assert spec.choices == ["monthly", "weekly", "daily"]


def test_str_field_without_choices_has_none():
    spec = next(s for s in describe_params(_Sample) if s.name == "mode")
    assert spec.choices is None


def test_apply_params_accepts_a_valid_choice():
    updated = apply_params(_Sample(), {"frequency": "weekly"})
    assert updated.frequency == "weekly"


def test_apply_params_rejects_a_value_outside_choices():
    with pytest.raises(ValueError, match="must be one of"):
        apply_params(_Sample(), {"frequency": "hourly"})


def test_apply_params_rejects_a_non_string_for_a_str_field():
    with pytest.raises(ValueError, match="must be a string"):
        apply_params(_Sample(), {"mode": 123})


def test_pivot_reversal_has_no_tunable_params():
    from strategies.day.pivot_reversal import PivotLevelEtfReversal

    assert describe_params(PivotLevelEtfReversal) == []


def test_every_registered_strategy_class_is_describable_without_raising():
    """Every name in strategies.registry must resolve to something
    describe_params can introspect without an instance -- the exact contract
    api/main.py's /api/params endpoint relies on."""
    from engine.runner import strategy_class
    from strategies.registry import ALL_STRATEGY_NAMES, CROSS_SECTIONAL_STRATEGY_NAMES, PAIRS_STRATEGY_NAMES

    excluded = set(CROSS_SECTIONAL_STRATEGY_NAMES) | set(PAIRS_STRATEGY_NAMES)
    for name in ALL_STRATEGY_NAMES:
        if name in excluded:
            continue
        describe_params(strategy_class(name))  # must not raise
