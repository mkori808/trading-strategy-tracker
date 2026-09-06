"""Shared metadata for tunable strategy parameters.

Every strategy is a @dataclass (see strategies/*/*.py). Fields that are real
rule parameters -- the kind a user should be able to tune from the webapp's
Lab tab -- are declared with `param_field()` instead of a bare default, so
`describe_params()` can turn any strategy class into a UI-renderable schema
without per-strategy frontend code.

Fields declared as plain dataclass fields (no `param_field()`) are treated
as structural/injected -- data the ENGINE supplies at construction time
(e.g. `benchmark_bars`, `positive_earnings`, `risk_free_rate`), not a rule
a user tunes. `describe_params` excludes them via one rule (does the field's
metadata carry a "label"?) rather than a separate allow/deny list that could
drift out of sync with the dataclass fields themselves.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Literal

ParamKind = Literal["int", "float", "bool", "str"]


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    kind: ParamKind
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    help: str | None = None
    # Fixed set of valid values for a "str" kind field (e.g. a rebalance
    # frequency) -- the UI renders a dropdown instead of free text, and
    # apply_params() rejects anything outside this set the same way it
    # rejects an out-of-bounds number. None for a str field means free
    # text (no current strategy does this, but the option stays open).
    choices: list[str] | None = None


def param_field(
    default: Any,
    *,
    label: str,
    minimum: float | None = None,
    maximum: float | None = None,
    step: float | None = None,
    help: str | None = None,
    choices: list[str] | None = None,
) -> Any:
    """A dataclass field that is also a tunable, UI-exposed rule parameter."""
    return dataclasses.field(
        default=default,
        metadata={
            "label": label,
            "minimum": minimum,
            "maximum": maximum,
            "step": step,
            "help": help,
            "choices": choices,
        },
    )


def _kind_of(field_type: Any) -> ParamKind:
    # Every strategy module has `from __future__ import annotations` (PEP
    # 563), so dataclass field annotations are unevaluated strings ("bool",
    # "int", ...) at this point, not the type objects themselves -- compare
    # by name, not identity. Order matters: check "bool" before "int" since
    # `issubclass(bool, int)` is true the other way around, were this ever
    # switched to runtime type objects.
    name = field_type if isinstance(field_type, str) else getattr(field_type, "__name__", "")
    if name == "bool":
        return "bool"
    if name == "int":
        return "int"
    if name == "float":
        return "float"
    return "str"


def describe_params(cls: type) -> list[ParamSpec]:
    """The tunable-parameter schema for a strategy class (or instance's
    class). Field order matches declaration order in the source file.
    Strategies with no tunable parameters (e.g. Pivot-Level ETF Reversal)
    are never converted to a @dataclass in the first place -- returns an
    empty schema for those rather than raising."""
    target = cls if isinstance(cls, type) else type(cls)
    if not dataclasses.is_dataclass(target):
        return []
    specs = []
    for f in dataclasses.fields(target):
        label = f.metadata.get("label")
        if label is None:
            continue  # structural/injected field -- not user-tunable
        specs.append(
            ParamSpec(
                name=f.name,
                label=label,
                kind=_kind_of(f.type),
                default=f.default,
                minimum=f.metadata.get("minimum"),
                maximum=f.metadata.get("maximum"),
                step=f.metadata.get("step"),
                help=f.metadata.get("help"),
                choices=f.metadata.get("choices"),
            )
        )
    return specs


def tunable_field_names(cls: type) -> set[str]:
    """Just the names -- used to validate that an incoming params dict from
    the API doesn't reference a structural/nonexistent field."""
    return {spec.name for spec in describe_params(cls)}


def apply_params(strategy: Any, params: dict[str, Any] | None) -> Any:
    """Validated `dataclasses.replace` -- the one place a params dict from
    an untrusted caller (the API) turns into an actual strategy instance.
    Raises ValueError (never silently clamps or drops a bad value) if a key
    isn't a real tunable field or a value falls outside its declared bounds.

    Lives here (not engine/runner.py, which re-exports it for backward
    compatibility) so a module that needs per-symbol strategy construction
    with param overrides -- e.g. engine/run_avwap_breakout.py, which builds
    one AvwapBreakout per symbol from per-symbol anchor dates -- can use it
    without importing engine.runner and creating a cycle back here."""
    if not params:
        return strategy
    specs = {spec.name: spec for spec in describe_params(type(strategy))}
    for key, value in params.items():
        spec = specs.get(key)
        if spec is None:
            raise ValueError(f"{key!r} is not a tunable parameter of {strategy.name!r}")
        if spec.kind in ("int", "float") and not isinstance(value, (int, float)):
            raise ValueError(f"{key!r} must be a number, got {value!r}")
        if spec.kind == "bool" and not isinstance(value, bool):
            raise ValueError(f"{key!r} must be a boolean, got {value!r}")
        if spec.kind == "str" and not isinstance(value, str):
            raise ValueError(f"{key!r} must be a string, got {value!r}")
        if spec.choices is not None and value not in spec.choices:
            raise ValueError(f"{key!r}={value!r} must be one of {spec.choices}")
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"{key!r}={value} is below its minimum of {spec.minimum}")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"{key!r}={value} is above its maximum of {spec.maximum}")
    return dataclasses.replace(strategy, **params)
