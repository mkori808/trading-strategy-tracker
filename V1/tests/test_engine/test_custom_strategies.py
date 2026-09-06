"""engine/custom_strategies.py and engine/strategy_authoring.py: the store
for user-authored strategies and the wire-format normalization in front of
it. No network here -- the authoring model's response shape is tested
against parse_spec(), which is the gate that decides whether a draft ever
becomes a runnable strategy."""

from __future__ import annotations

import json

import pytest

from engine import custom_strategies, strategy_authoring
from strategies.registry import ALL_STRATEGY_NAMES
from strategies.spec import parse_spec

SPEC = {
    "name": "Volume Surge Breakout",
    "kind": "Swing Trading",
    "timeframe": "1d",
    "direction": "long",
    "description": "Buy a 20-day breakout confirmed by above-average volume.",
    "params": [{
        "name": "volume_ratio_min", "label": "Volume vs. average", "kind": "float",
        "default": 1.5, "minimum": 1.0, "maximum": 4.0, "step": 0.1,
    }],
    "entry": [
        {"left": {"kind": "close"}, "op": ">", "right": {"kind": "rolling_high", "args": {"period": 20}}},
        {
            "left": {"kind": "volume_ratio", "args": {"period": 20}}, "op": ">",
            "right": {"kind": "constant", "args": {"value": {"param": "volume_ratio_min"}}},
        },
    ],
    "stop": {"kind": "atr_multiple", "args": {"period": 14, "multiple": 2.0}},
    "target": {"kind": "risk_multiple", "args": {"multiple": 2.0}},
    "exit": [],
}


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_strategies, "STORE_DIR", tmp_path / "custom_strategies")
    return tmp_path / "custom_strategies"


def test_a_saved_strategy_round_trips_with_the_prompt_it_came_from(store):
    """The description is stored beside the rules on purpose: a strategy
    written from a sentence is only auditable if the sentence is still
    there next to it."""
    prompt = "buy 20-day breakouts when volume is 50% above average"
    saved = custom_strategies.save(parse_spec(SPEC), prompt)

    loaded, errors = custom_strategies.load_all()
    assert errors == []
    entry = loaded["Volume Surge Breakout"]
    assert entry.prompt == prompt
    assert entry.spec.to_dict() == saved.spec.to_dict()
    assert entry.created_at
    assert custom_strategies.custom_strategy_names() == ["Volume Surge Breakout"]
    assert custom_strategies.is_custom("Volume Surge Breakout")


def test_saving_never_overwrites_a_registered_or_existing_name(store):
    custom_strategies.save(parse_spec(SPEC), "prompt")

    with pytest.raises(ValueError, match="already exists"):
        custom_strategies.save(parse_spec(SPEC), "prompt")

    # Punctuation-only differences map to the same file; the second save
    # would silently replace the first and orphan its logged run history.
    near_duplicate = {**SPEC, "name": "Volume-Surge Breakout"}
    with pytest.raises(ValueError, match="too close to"):
        custom_strategies.save(parse_spec(near_duplicate), "prompt")

    registered = {**SPEC, "name": ALL_STRATEGY_NAMES[0]}
    with pytest.raises(ValueError, match="already a registered strategy"):
        custom_strategies.save(parse_spec(registered), "prompt")


def test_a_stored_file_that_no_longer_parses_is_reported_not_skipped(store):
    """A spec whose rule the parser has since tightened must show up as a
    broken strategy the user can see and delete, not vanish as if it never
    existed."""
    custom_strategies.save(parse_spec(SPEC), "prompt")
    broken = store / "broken.json"
    broken.write_text(json.dumps({"spec": {"name": "x"}, "prompt": "p"}), encoding="utf-8")

    loaded, errors = custom_strategies.load_all()
    assert set(loaded) == {"Volume Surge Breakout"}
    assert [e.filename for e in errors] == ["broken.json"]
    assert errors[0].error

    custom_strategies.delete_broken("broken.json")
    assert custom_strategies.load_all()[1] == []


def test_delete_broken_cannot_escape_the_store_directory(store):
    store.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ValueError):
        custom_strategies.delete_broken("../../secrets.json")


def test_deleting_a_strategy_removes_only_its_definition(store):
    custom_strategies.save(parse_spec(SPEC), "prompt")
    custom_strategies.delete("Volume Surge Breakout")
    assert custom_strategies.custom_strategy_names() == []
    with pytest.raises(ValueError, match="No custom strategy"):
        custom_strategies.delete("Volume Surge Breakout")


def test_the_store_is_capped(store, monkeypatch):
    monkeypatch.setattr(custom_strategies, "MAX_CUSTOM_STRATEGIES", 1)
    custom_strategies.save(parse_spec(SPEC), "prompt")
    with pytest.raises(ValueError, match="already holds"):
        custom_strategies.save(parse_spec({**SPEC, "name": "Another Breakout"}), "prompt")


# --------------------------------------------------------------------------
# Authoring wire format
# --------------------------------------------------------------------------


def test_the_models_wire_payload_normalizes_into_a_parseable_spec():
    """The response schema uses arrays of {name, number} because a strict
    JSON schema can't express a dict with arbitrary keys. This is the
    flattening back to the shape parse_spec expects -- if it drifts, every
    draft fails validation."""
    payload = {
        "name": "Wire Format Check",
        "kind": "Swing Trading",
        "timeframe": "1d",
        "direction": "long",
        "description": "Oversold bounce.",
        "params": [{
            "name": "rsi_threshold", "label": "RSI threshold", "kind": "int",
            "default": 30, "minimum": 10, "maximum": 45, "step": 1, "help": None,
        }],
        "entry": [
            {"any_of": [{
                "left": {
                    "kind": "rsi",
                    "args": [{"name": "period", "number": {"value": 14, "param": None}}],
                    "offset": 0,
                    "scale": {"value": None, "param": None},
                },
                "op": "<",
                "right": {
                    "kind": "constant",
                    "args": [{"name": "value", "number": {"value": None, "param": "rsi_threshold"}}],
                    "offset": 0,
                    "scale": {"value": None, "param": None},
                },
            }]},
            {"any_of": [
                {
                    "left": {"kind": "close", "args": [], "offset": 0,
                             "scale": {"value": None, "param": None}},
                    "op": ">",
                    "right": {"kind": "sma", "args": [{"name": "period", "number": {"value": 50, "param": None}}],
                              "offset": 0, "scale": {"value": 1.02, "param": None}},
                },
                {
                    "left": {"kind": "close", "args": [], "offset": 0,
                             "scale": {"value": None, "param": None}},
                    "op": ">",
                    "right": {"kind": "ema", "args": [{"name": "period", "number": {"value": 200, "param": None}}],
                              "offset": 0, "scale": {"value": None, "param": None}},
                },
            ]},
        ],
        "exit": [],
        "stop": {"kind": "percent", "args": [{"name": "pct", "number": {"value": 5.0, "param": None}}]},
        "target": {"kind": "risk_multiple",
                   "args": [{"name": "multiple", "number": {"value": 2.0, "param": None}}]},
    }

    spec = parse_spec(strategy_authoring.to_spec_dict(payload))

    assert spec.name == "Wire Format Check"
    assert len(spec.entry) == 2
    # A single-option group flattens to a plain AND-ed comparison; a
    # multi-option one becomes an OR group.
    assert spec.entry[0].__class__.__name__ == "Comparison"
    assert spec.entry[1].__class__.__name__ == "AnyOf"
    assert spec.entry[0].right.args["value"] == {"param": "rsi_threshold"}
    assert spec.entry[1].options[0].right.scale == 1.02
    # A null scale means "unscaled", not "scale by zero".
    assert spec.entry[0].left.scale == 1.0


def test_drafting_without_a_description_fails_before_any_api_call(monkeypatch):
    monkeypatch.setattr(strategy_authoring, "_client", lambda: pytest.fail("called the API"))
    with pytest.raises(strategy_authoring.AuthoringError, match="Describe the strategy"):
        strategy_authoring.draft("   ")


def test_missing_credentials_degrade_to_a_clear_message(monkeypatch):
    """Same graceful degradation as every other optional integration here
    -- a missing key is a readable reply, not a 500."""
    monkeypatch.setattr(strategy_authoring, "first_env", lambda *_: None)
    ok, reason = strategy_authoring.available()
    assert ok is False
    assert "ANTHROPIC_API_KEY" in reason
    with pytest.raises(strategy_authoring.AuthoringError, match="isn't configured"):
        strategy_authoring.draft("buy the dip")


def test_the_authoring_prompt_lists_the_real_indicator_vocabulary():
    """The prompt is generated from strategies/spec.py:INDICATORS, so the
    model can never be told about an indicator the parser would reject."""
    from strategies.spec import INDICATORS

    prompt = strategy_authoring._system_prompt()
    for kind in INDICATORS:
        assert kind in prompt
    assert "decline" in prompt


# --------------------------------------------------------------------------
# Causality evidence for a strategy with no source file
# --------------------------------------------------------------------------


def test_causality_evidence_reads_the_spec_when_there_is_no_source_file():
    """A compiled-spec strategy has no source to scan (inspect.getsource
    raises), and scanning source text wouldn't be the right evidence anyway
    -- the guarantee comes from the vocabulary. The validation suite must
    record that, not crash and not skip the check."""
    from engine.advanced_validation import causality_contract_evidence
    from strategies.spec import spec_strategy_class

    cls = spec_strategy_class(parse_spec(SPEC))
    passed, evidence = causality_contract_evidence("standard", cls)

    assert passed is True
    assert evidence["strategyDefinition"] == "compiled-spec"
    assert evidence["suspiciousFutureOperators"] == []
    assert set(evidence["specIndicators"]) == {"close", "rolling_high", "volume_ratio", "constant"}
    assert evidence["maxBarsBackReferenced"] == 0
    assert evidence["strategySourceSha256"]


def test_an_uninspectable_strategy_fails_rather_than_passing_by_default():
    """"We couldn't inspect it" must never read as "it passed."""
    from engine.advanced_validation import causality_contract_evidence

    opaque = type("Opaque", (), {})  # no source file, no spec
    passed, evidence = causality_contract_evidence("standard", opaque)

    assert passed is False
    assert evidence["strategyDefinition"] == "unavailable"
    assert evidence["strategySourceSha256"] is None
    assert evidence["suspiciousFutureOperators"]
