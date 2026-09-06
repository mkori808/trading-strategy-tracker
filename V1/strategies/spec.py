"""Declarative strategy specs -- the rule language natural-language
authoring compiles into (see engine/strategy_authoring.py).

Why a DSL rather than generated Python: a strategy written from a sentence
still has to obey every rule the hand-written strategies obey -- no
look-ahead, tunable numbers exposed as `param_field()`s, the same
`strategies.base.Strategy` interface the engine already runs. Generated
source code can violate all three silently and cannot be checked before it
runs. A spec can only express operations enumerated here, every one of
which reads bars <= the current one, so "did the model write a look-ahead
bug?" stops being a question that needs asking per strategy.

The two-way contract:

- `parse_spec()` is the ONLY way a dict becomes a StrategySpec. It raises
  ValueError with a specific message on anything it does not recognize --
  an unknown indicator, an unresolvable param reference, a stop rule that
  can never close a trade. Nothing is silently dropped or defaulted into
  something plausible-looking.
- `spec_strategy_class()` turns the parsed spec into a real @dataclass
  Strategy subclass whose tunable numbers are `param_field()`s, so the Lab
  tab's sliders, `strategies.params.apply_params()`'s bounds validation,
  and run logging's params blob all work on a custom strategy exactly as
  they do on a registered one -- no second code path.
- `describe_spec()` renders the parsed spec back to English. The authoring
  UI shows THAT, not the model's own summary of what it wrote: the point of
  the review step is to display the rules that will actually run.

Deliberately not expressible: anything needing data the per-symbol engine
doesn't have (fundamentals, cross-symbol ranking, news), and anything
reading a bar after the current one. Both are refusals in
engine/strategy_authoring.py rather than gaps to fill in later here.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd

from engine.indicators import atr, ema, macd, rsi, sma
from engine.indicators import vwap as session_vwap
from strategies.base import Strategy
from strategies.params import param_field

SPEC_VERSION = 1

TIMEFRAMES = ("1d", "5m")
DIRECTIONS = ("long", "short")
KINDS = ("Day Trading", "Swing Trading")
COMPARISON_OPS = ("<", "<=", ">", ">=", "crosses_above", "crosses_below")

# Bars back an operand may reference. A rule needing more history than this
# to state its condition is almost always a misread of the request rather
# than a real rule; the cap keeps a spec's warmup bounded and reviewable.
MAX_OFFSET = 20


# --------------------------------------------------------------------------
# Indicator vocabulary
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class IndicatorDef:
    """One entry in the vocabulary a spec may reference.

    `compute` returns a full Series aligned to `bars` and must be causal:
    the value at position i may depend only on rows <= i. Every
    implementation below is a right-aligned rolling or recursive op, which
    is what makes the no-look-ahead guarantee a property of this table
    rather than something to re-audit per generated strategy.
    """

    kind: str
    numeric_args: tuple[str, ...]
    compute: Callable[[pd.DataFrame, dict[str, float]], pd.Series]
    warmup: Callable[[dict[str, float]], int]
    render: Callable[[dict[str, Any]], str]
    description: str


def _i(args: dict[str, float], name: str, default: float) -> int:
    return int(args.get(name, default))


def _bollinger(bars: pd.DataFrame, args: dict[str, float], *, upper: bool) -> pd.Series:
    period = _i(args, "period", 20)
    width = float(args.get("stddev", 2.0))
    mid = bars["Close"].rolling(period).mean()
    # ddof=0 -- population stddev, the convention Bollinger bands use.
    sd = bars["Close"].rolling(period).std(ddof=0)
    return mid + width * sd if upper else mid - width * sd


INDICATORS: dict[str, IndicatorDef] = {
    "close": IndicatorDef(
        "close", (), lambda b, a: b["Close"], lambda a: 1,
        lambda a: "close", "The bar's closing price.",
    ),
    "open": IndicatorDef(
        "open", (), lambda b, a: b["Open"], lambda a: 1,
        lambda a: "open", "The bar's opening price.",
    ),
    "high": IndicatorDef(
        "high", (), lambda b, a: b["High"], lambda a: 1,
        lambda a: "high", "The bar's high.",
    ),
    "low": IndicatorDef(
        "low", (), lambda b, a: b["Low"], lambda a: 1,
        lambda a: "low", "The bar's low.",
    ),
    "volume": IndicatorDef(
        "volume", (), lambda b, a: b["Volume"], lambda a: 1,
        lambda a: "volume", "The bar's share volume.",
    ),
    "sma": IndicatorDef(
        "sma", ("period",), lambda b, a: sma(b["Close"], _i(a, "period", 20)),
        lambda a: _i(a, "period", 20),
        lambda a: f"SMA({a.get('period')})", "Simple moving average of close.",
    ),
    "ema": IndicatorDef(
        "ema", ("period",), lambda b, a: ema(b["Close"], _i(a, "period", 21)),
        lambda a: _i(a, "period", 21) * 3,
        lambda a: f"EMA({a.get('period')})", "Exponential moving average of close.",
    ),
    "rsi": IndicatorDef(
        "rsi", ("period",), lambda b, a: rsi(b["Close"], _i(a, "period", 14)),
        lambda a: _i(a, "period", 14) * 3,
        lambda a: f"RSI({a.get('period')})", "Wilder RSI of close, 0-100.",
    ),
    "atr": IndicatorDef(
        "atr", ("period",), lambda b, a: atr(b, _i(a, "period", 14)),
        lambda a: _i(a, "period", 14) * 3,
        lambda a: f"ATR({a.get('period')})", "Average true range, in price units.",
    ),
    "atr_pct": IndicatorDef(
        "atr_pct", ("period",),
        lambda b, a: atr(b, _i(a, "period", 14)) / b["Close"] * 100.0,
        lambda a: _i(a, "period", 14) * 3,
        lambda a: f"ATR({a.get('period')}) as % of close",
        "Average true range as a percentage of close.",
    ),
    "vwap": IndicatorDef(
        "vwap", (), lambda b, a: session_vwap(b), lambda a: 1,
        lambda a: "session VWAP",
        "Session VWAP, resets each day. Intraday timeframes only.",
    ),
    "volume_sma": IndicatorDef(
        "volume_sma", ("period",), lambda b, a: sma(b["Volume"], _i(a, "period", 20)),
        lambda a: _i(a, "period", 20),
        lambda a: f"average volume({a.get('period')})",
        "Simple moving average of volume.",
    ),
    "volume_ratio": IndicatorDef(
        "volume_ratio", ("period",),
        lambda b, a: b["Volume"] / sma(b["Volume"], _i(a, "period", 20)).replace(0, np.nan),
        lambda a: _i(a, "period", 20),
        lambda a: f"volume vs. its {a.get('period')}-bar average",
        "This bar's volume divided by its N-bar average (1.0 = average).",
    ),
    "rolling_high": IndicatorDef(
        "rolling_high", ("period",),
        # Shifted one bar: the highest high of the N bars BEFORE this one.
        # Including the current bar's own high would make "close > N-bar
        # high" a tautology-or-impossibility rather than a breakout test.
        lambda b, a: b["High"].rolling(_i(a, "period", 20)).max().shift(1),
        lambda a: _i(a, "period", 20) + 1,
        lambda a: f"prior {a.get('period')}-bar high",
        "Highest high of the N bars BEFORE the current one (breakout level).",
    ),
    "rolling_low": IndicatorDef(
        "rolling_low", ("period",),
        lambda b, a: b["Low"].rolling(_i(a, "period", 20)).min().shift(1),
        lambda a: _i(a, "period", 20) + 1,
        lambda a: f"prior {a.get('period')}-bar low",
        "Lowest low of the N bars BEFORE the current one (breakdown level).",
    ),
    "pct_change": IndicatorDef(
        "pct_change", ("period",),
        lambda b, a: b["Close"].pct_change(_i(a, "period", 1)) * 100.0,
        lambda a: _i(a, "period", 1) + 1,
        lambda a: f"{a.get('period')}-bar return %",
        "Percent change of close over N bars.",
    ),
    "gap_pct": IndicatorDef(
        "gap_pct", (),
        lambda b, a: (b["Open"] / b["Close"].shift(1) - 1.0) * 100.0,
        lambda a: 2,
        lambda a: "gap %", "This bar's open vs. the prior close, in percent.",
    ),
    "macd": IndicatorDef(
        "macd", ("fast", "slow", "signal"),
        lambda b, a: macd(b["Close"], _i(a, "fast", 12), _i(a, "slow", 26), _i(a, "signal", 9))[0],
        lambda a: _i(a, "slow", 26) * 3,
        lambda a: f"MACD line({a.get('fast')},{a.get('slow')})", "MACD line.",
    ),
    "macd_signal": IndicatorDef(
        "macd_signal", ("fast", "slow", "signal"),
        lambda b, a: macd(b["Close"], _i(a, "fast", 12), _i(a, "slow", 26), _i(a, "signal", 9))[1],
        lambda a: _i(a, "slow", 26) * 3,
        lambda a: f"MACD signal({a.get('signal')})", "MACD signal line.",
    ),
    "macd_hist": IndicatorDef(
        "macd_hist", ("fast", "slow", "signal"),
        lambda b, a: macd(b["Close"], _i(a, "fast", 12), _i(a, "slow", 26), _i(a, "signal", 9))[2],
        lambda a: _i(a, "slow", 26) * 3,
        lambda a: "MACD histogram", "MACD line minus its signal line.",
    ),
    "bollinger_upper": IndicatorDef(
        "bollinger_upper", ("period", "stddev"),
        lambda b, a: _bollinger(b, a, upper=True), lambda a: _i(a, "period", 20),
        lambda a: f"upper Bollinger({a.get('period')}, {a.get('stddev')}sd)",
        "Upper Bollinger band on close.",
    ),
    "bollinger_lower": IndicatorDef(
        "bollinger_lower", ("period", "stddev"),
        lambda b, a: _bollinger(b, a, upper=False), lambda a: _i(a, "period", 20),
        lambda a: f"lower Bollinger({a.get('period')}, {a.get('stddev')}sd)",
        "Lower Bollinger band on close.",
    ),
    "constant": IndicatorDef(
        "constant", ("value",),
        lambda b, a: pd.Series(float(a.get("value", 0.0)), index=b.index), lambda a: 1,
        lambda a: f"{a.get('value')}", "A fixed number.",
    ),
}

# Session VWAP resets per calendar day, so on daily bars it collapses to that
# one day's typical price -- a real trap for a rule written from a sentence
# like "buy the VWAP bounce" with no timeframe in mind. Rejected at parse
# time rather than producing a rule that silently means something else.
INTRADAY_ONLY_INDICATORS = {"vwap"}


# --------------------------------------------------------------------------
# Parsed spec dataclasses
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SpecParam:
    """A tunable rule number, declared with bounds so it becomes a real
    `param_field()` on the compiled class (Lab-tab slider + apply_params
    validation) instead of a literal baked into the rule."""

    name: str
    label: str
    default: float
    minimum: float
    maximum: float
    step: float | None = None
    kind: Literal["int", "float"] = "float"
    help: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class Operand:
    kind: str
    args: dict[str, Any] = field(default_factory=dict)
    offset: int = 0        # bars ago; 0 = the current (last) bar
    scale: Any = 1.0       # multiplier, so "1.02 x SMA(50)" needs no extra node

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind}
        if self.args:
            out["args"] = dict(self.args)
        if self.offset:
            out["offset"] = self.offset
        if self.scale != 1.0:
            out["scale"] = self.scale
        return out


@dataclass(frozen=True)
class Comparison:
    left: Operand
    op: str
    right: Operand

    def to_dict(self) -> dict[str, Any]:
        return {"left": self.left.to_dict(), "op": self.op, "right": self.right.to_dict()}


@dataclass(frozen=True)
class AnyOf:
    """An OR group: satisfied when any one of its comparisons holds. A
    top-level condition list is AND; this is the only nesting allowed, which
    keeps every rule renderable as a flat, reviewable list."""

    options: tuple[Comparison, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"any": [c.to_dict() for c in self.options]}


Condition = Comparison | AnyOf

STOP_KINDS = ("atr_multiple", "percent", "swing_extreme", "prior_bar_extreme")
TARGET_KINDS = ("risk_multiple", "percent", "atr_multiple", "none")


@dataclass(frozen=True)
class StopRule:
    kind: str
    args: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "args": dict(self.args)}


@dataclass(frozen=True)
class TargetRule:
    kind: str
    args: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "args": dict(self.args)}


@dataclass(frozen=True)
class StrategySpec:
    name: str
    kind: str            # "Day Trading" | "Swing Trading"
    timeframe: str       # "1d" | "5m"
    direction: str       # "long" | "short"
    description: str
    entry: tuple[Condition, ...]
    stop: StopRule
    target: TargetRule
    exit: tuple[Condition, ...] = ()
    params: tuple[SpecParam, ...] = ()
    version: int = SPEC_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "name": self.name,
            "kind": self.kind,
            "timeframe": self.timeframe,
            "direction": self.direction,
            "description": self.description,
            "params": [p.to_dict() for p in self.params],
            "entry": [c.to_dict() for c in self.entry],
            "stop": self.stop.to_dict(),
            "target": self.target.to_dict(),
            "exit": [c.to_dict() for c in self.exit],
        }


# --------------------------------------------------------------------------
# Parsing / validation
# --------------------------------------------------------------------------

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 /()+&.,'-]{2,59}$")
PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _parse_params(raw: Any) -> tuple[SpecParam, ...]:
    _require(isinstance(raw, list), "params must be a list")
    params: list[SpecParam] = []
    seen: set[str] = set()
    for item in raw:
        _require(isinstance(item, dict), "each param must be an object")
        name = item.get("name")
        _require(
            isinstance(name, str) and bool(PARAM_NAME_RE.match(name)),
            f"param name {name!r} must be lower_snake_case, 2-40 chars",
        )
        _require(name not in seen, f"duplicate param {name!r}")
        seen.add(name)
        kind = item.get("kind", "float")
        _require(kind in ("int", "float"), f"param {name!r}: kind must be 'int' or 'float'")
        for key in ("default", "minimum", "maximum"):
            _require(
                isinstance(item.get(key), (int, float)) and not isinstance(item.get(key), bool),
                f"param {name!r}: {key} must be a number",
            )
        default, minimum, maximum = item["default"], item["minimum"], item["maximum"]
        _require(minimum < maximum, f"param {name!r}: minimum must be below maximum")
        _require(
            minimum <= default <= maximum,
            f"param {name!r}: default {default} is outside [{minimum}, {maximum}]",
        )
        step = item.get("step")
        _require(
            step is None or (isinstance(step, (int, float)) and step > 0),
            f"param {name!r}: step must be a positive number or omitted",
        )
        label = item.get("label")
        _require(
            isinstance(label, str) and label.strip() != "",
            f"param {name!r}: label is required (it is what the Lab tab's slider is called)",
        )
        params.append(
            SpecParam(
                name=name, label=label.strip(),
                default=int(default) if kind == "int" else float(default),
                minimum=float(minimum), maximum=float(maximum),
                step=None if step is None else float(step), kind=kind,
                help=item.get("help") if isinstance(item.get("help"), str) else None,
            )
        )
    return tuple(params)


def _parse_number(value: Any, param_names: set[str], where: str) -> Any:
    """A numeric slot: a literal, or {"param": name} referring to one of the
    spec's declared params. An unresolvable reference is an error, never a
    silently-substituted default."""
    if isinstance(value, bool):
        raise ValueError(f"{where}: expected a number, got a boolean")
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, dict) and set(value) == {"param"}:
        ref = value["param"]
        _require(
            isinstance(ref, str) and ref in param_names,
            f"{where}: references param {ref!r}, which is not declared in params",
        )
        return {"param": ref}
    raise ValueError(f"{where}: expected a number or {{'param': name}}, got {value!r}")


def _parse_operand(raw: Any, param_names: set[str], timeframe: str, where: str) -> Operand:
    _require(isinstance(raw, dict), f"{where}: operand must be an object")
    kind = raw.get("kind")
    _require(
        isinstance(kind, str) and kind in INDICATORS,
        f"{where}: unknown indicator {kind!r}. Available: {', '.join(sorted(INDICATORS))}",
    )
    _require(
        not (kind in INTRADAY_ONLY_INDICATORS and timeframe == "1d"),
        f"{where}: {kind!r} is an intraday indicator and means nothing on daily bars",
    )
    definition = INDICATORS[kind]
    raw_args = raw.get("args", {})
    _require(isinstance(raw_args, dict), f"{where}: args must be an object")
    unknown = set(raw_args) - set(definition.numeric_args)
    if unknown:
        raise ValueError(f"{where}: {kind!r} takes no argument named {sorted(unknown)[0]!r}")
    args = {
        key: _parse_number(value, param_names, f"{where}.args.{key}")
        for key, value in raw_args.items()
    }
    if kind == "constant":
        _require("value" in args, f"{where}: constant requires args.value")
    offset = raw.get("offset", 0)
    _require(
        isinstance(offset, int) and not isinstance(offset, bool) and 0 <= offset <= MAX_OFFSET,
        f"{where}: offset must be an integer between 0 and {MAX_OFFSET} (bars ago)",
    )
    scale = _parse_number(raw.get("scale", 1.0), param_names, f"{where}.scale")
    return Operand(kind=kind, args=args, offset=offset, scale=scale)


def _parse_comparison(raw: Any, param_names: set[str], timeframe: str, where: str) -> Comparison:
    _require(isinstance(raw, dict), f"{where}: condition must be an object")
    op = raw.get("op")
    _require(
        isinstance(op, str) and op in COMPARISON_OPS,
        f"{where}: op must be one of {', '.join(COMPARISON_OPS)}",
    )
    left = _parse_operand(raw.get("left"), param_names, timeframe, f"{where}.left")
    right = _parse_operand(raw.get("right"), param_names, timeframe, f"{where}.right")
    return Comparison(left=left, op=op, right=right)


def _parse_condition(raw: Any, param_names: set[str], timeframe: str, where: str) -> Condition:
    if isinstance(raw, dict) and "any" in raw:
        options = raw["any"]
        _require(
            isinstance(options, list) and len(options) >= 2,
            f"{where}: an 'any' group needs at least two conditions",
        )
        return AnyOf(
            tuple(
                _parse_comparison(item, param_names, timeframe, f"{where}.any[{i}]")
                for i, item in enumerate(options)
            )
        )
    return _parse_comparison(raw, param_names, timeframe, where)


def _parse_conditions(
    raw: Any, param_names: set[str], timeframe: str, where: str
) -> tuple[Condition, ...]:
    _require(isinstance(raw, list), f"{where} must be a list of conditions")
    return tuple(
        _parse_condition(item, param_names, timeframe, f"{where}[{i}]")
        for i, item in enumerate(raw)
    )


_STOP_ARGS: dict[str, tuple[str, ...]] = {
    "atr_multiple": ("period", "multiple"),
    "percent": ("pct",),
    "swing_extreme": ("lookback", "buffer_pct"),
    "prior_bar_extreme": ("buffer_pct",),
}
_TARGET_ARGS: dict[str, tuple[str, ...]] = {
    "risk_multiple": ("multiple",),
    "percent": ("pct",),
    "atr_multiple": ("period", "multiple"),
    "none": (),
}


def _parse_rule_args(
    raw: Any, allowed: tuple[str, ...], required: tuple[str, ...], param_names: set[str], where: str
) -> dict[str, Any]:
    raw_args = raw if isinstance(raw, dict) else {}
    unknown = set(raw_args) - set(allowed)
    if unknown:
        raise ValueError(f"{where}: unexpected argument {sorted(unknown)[0]!r}")
    missing = [key for key in required if key not in raw_args]
    if missing:
        raise ValueError(f"{where}: missing required argument {missing[0]!r}")
    return {
        key: _parse_number(value, param_names, f"{where}.{key}")
        for key, value in raw_args.items()
    }


def _parse_stop(raw: Any, param_names: set[str]) -> StopRule:
    _require(isinstance(raw, dict), "stop must be an object")
    kind = raw.get("kind")
    _require(
        isinstance(kind, str) and kind in STOP_KINDS,
        f"stop.kind must be one of {', '.join(STOP_KINDS)}",
    )
    required = {
        "atr_multiple": ("multiple",), "percent": ("pct",),
        "swing_extreme": ("lookback",), "prior_bar_extreme": (),
    }[kind]
    args = _parse_rule_args(raw.get("args"), _STOP_ARGS[kind], required, param_names, "stop.args")
    return StopRule(kind=kind, args=args)


def _parse_target(raw: Any, param_names: set[str]) -> TargetRule:
    if raw is None:
        return TargetRule(kind="none")
    _require(isinstance(raw, dict), "target must be an object")
    kind = raw.get("kind")
    _require(
        isinstance(kind, str) and kind in TARGET_KINDS,
        f"target.kind must be one of {', '.join(TARGET_KINDS)}",
    )
    required = {
        "risk_multiple": ("multiple",), "percent": ("pct",),
        "atr_multiple": ("multiple",), "none": (),
    }[kind]
    args = _parse_rule_args(raw.get("args"), _TARGET_ARGS[kind], required, param_names, "target.args")
    return TargetRule(kind=kind, args=args)


def parse_spec(raw: dict[str, Any]) -> StrategySpec:
    """Validate an untrusted spec dict (from the authoring model, or from a
    stored file written by an older version of this module) into a
    StrategySpec. Raises ValueError naming the exact offending path."""
    _require(isinstance(raw, dict), "spec must be an object")

    name = raw.get("name")
    _require(
        isinstance(name, str) and bool(NAME_RE.match(name.strip())),
        "name must be 3-60 characters of letters, digits, spaces or ()/+&.,'-",
    )
    name = name.strip()

    kind = raw.get("kind")
    _require(kind in KINDS, f"kind must be one of {', '.join(KINDS)}")
    timeframe = raw.get("timeframe")
    _require(timeframe in TIMEFRAMES, f"timeframe must be one of {', '.join(TIMEFRAMES)}")
    _require(
        (kind == "Day Trading") == (timeframe == "5m"),
        "kind and timeframe disagree: 'Day Trading' runs on 5m bars, "
        "'Swing Trading' on 1d bars",
    )
    direction = raw.get("direction")
    _require(
        direction in DIRECTIONS,
        "direction must be 'long' or 'short'. A rule that trades both sides has to be "
        "saved as two strategies so each side's expectancy is measured separately",
    )
    description = raw.get("description")
    _require(
        isinstance(description, str) and description.strip() != "",
        "description is required -- one sentence saying what the rule is",
    )

    params = _parse_params(raw.get("params", []))
    param_names = {p.name for p in params}

    entry = _parse_conditions(raw.get("entry"), param_names, timeframe, "entry")
    _require(len(entry) >= 1, "entry needs at least one condition")
    exit_conditions = _parse_conditions(raw.get("exit", []), param_names, timeframe, "exit")

    stop = _parse_stop(raw.get("stop"), param_names)
    target = _parse_target(raw.get("target"), param_names)

    # A trade has to have a way to close in profit. With no target and no
    # exit rule the only exit is the stop, which is not a strategy -- it is
    # a guaranteed loser, and would post a 0% win rate that reads as a
    # measured result rather than a construction artifact.
    _require(
        target.kind != "none" or exit_conditions,
        "with target.kind 'none' the strategy needs at least one exit condition -- "
        "otherwise the only way a trade ever closes is the stop",
    )

    unused = param_names - _referenced_params(entry, exit_conditions, stop, target)
    if unused:
        raise ValueError(f"param {sorted(unused)[0]!r} is declared but never used by any rule")
    return StrategySpec(
        name=name, kind=kind, timeframe=timeframe, direction=direction,
        description=description.strip(), entry=entry, stop=stop, target=target,
        exit=exit_conditions, params=params, version=SPEC_VERSION,
    )


def _numbers_in(value: Any) -> list[Any]:
    if isinstance(value, dict) and set(value) == {"param"}:
        return [value]
    return []


def _operand_params(operand: Operand) -> set[str]:
    refs: set[str] = set()
    for value in [*operand.args.values(), operand.scale]:
        for ref in _numbers_in(value):
            refs.add(ref["param"])
    return refs


def _condition_params(condition: Condition) -> set[str]:
    if isinstance(condition, AnyOf):
        return set().union(*(_condition_params(c) for c in condition.options))
    return _operand_params(condition.left) | _operand_params(condition.right)


def _referenced_params(
    entry: tuple[Condition, ...],
    exit_conditions: tuple[Condition, ...],
    stop: StopRule,
    target: TargetRule,
) -> set[str]:
    refs: set[str] = set()
    for condition in (*entry, *exit_conditions):
        refs |= _condition_params(condition)
    for rule in (stop, target):
        for value in rule.args.values():
            for ref in _numbers_in(value):
                refs.add(ref["param"])
    return refs


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


def _resolve(value: Any, params: dict[str, float]) -> float:
    if isinstance(value, dict):
        return float(params[value["param"]])
    return float(value)


def _resolved_args(args: dict[str, Any], params: dict[str, float]) -> dict[str, float]:
    return {key: _resolve(value, params) for key, value in args.items()}


def _series(operand: Operand, bars: pd.DataFrame, params: dict[str, float]) -> pd.Series:
    definition = INDICATORS[operand.kind]
    args = _resolved_args(operand.args, params)
    series = definition.compute(bars, args)
    scale = _resolve(operand.scale, params)
    return series * scale if scale != 1.0 else series


def _at(series: pd.Series, offset: int) -> float:
    position = len(series) - 1 - offset
    if position < 0:
        return float("nan")
    return float(series.iloc[position])


def _evaluate_comparison(
    comparison: Comparison, bars: pd.DataFrame, params: dict[str, float]
) -> bool:
    left = _series(comparison.left, bars, params)
    right = _series(comparison.right, bars, params)
    op = comparison.op
    if op in ("crosses_above", "crosses_below"):
        # A cross is defined against the bar immediately before each
        # operand's own offset, so "EMA9 crossed above EMA21 one bar ago"
        # still compares that bar with the one before it.
        now_left, now_right = _at(left, comparison.left.offset), _at(right, comparison.right.offset)
        prev_left = _at(left, comparison.left.offset + 1)
        prev_right = _at(right, comparison.right.offset + 1)
        values = (now_left, now_right, prev_left, prev_right)
        if any(pd.isna(v) for v in values):
            return False
        if op == "crosses_above":
            return prev_left <= prev_right and now_left > now_right
        return prev_left >= prev_right and now_left < now_right

    left_value, right_value = _at(left, comparison.left.offset), _at(right, comparison.right.offset)
    if pd.isna(left_value) or pd.isna(right_value):
        return False
    if op == ">":
        return left_value > right_value
    if op == ">=":
        return left_value >= right_value
    if op == "<":
        return left_value < right_value
    return left_value <= right_value


def _evaluate(conditions: tuple[Condition, ...], bars: pd.DataFrame, params: dict[str, float]) -> bool:
    for condition in conditions:
        if isinstance(condition, AnyOf):
            if not any(_evaluate_comparison(c, bars, params) for c in condition.options):
                return False
        elif not _evaluate_comparison(condition, bars, params):
            return False
    return True


def _condition_warmup(condition: Condition, params: dict[str, float]) -> int:
    if isinstance(condition, AnyOf):
        return max(_condition_warmup(c, params) for c in condition.options)
    return max(
        INDICATORS[operand.kind].warmup(_resolved_args(operand.args, params)) + operand.offset + 1
        for operand in (condition.left, condition.right)
    )


def required_bars(spec: StrategySpec, params: dict[str, float]) -> int:
    """Bars of history the spec's own rules need before any value it reads
    is defined. Below this `entry_signal` returns False rather than acting
    on a NaN-seeded indicator."""
    warmups = [
        _condition_warmup(condition, params) for condition in (*spec.entry, *spec.exit)
    ]
    if spec.stop.kind == "atr_multiple":
        warmups.append(int(_resolve(spec.stop.args.get("period", 14), params)) * 3)
    if spec.stop.kind == "swing_extreme":
        warmups.append(int(_resolve(spec.stop.args.get("lookback", 20), params)) + 1)
    if spec.target.kind == "atr_multiple":
        warmups.append(int(_resolve(spec.target.args.get("period", 14), params)) * 3)
    return max([*warmups, 2])


# --------------------------------------------------------------------------
# Compiled Strategy
# --------------------------------------------------------------------------


class SpecStrategy(Strategy):
    """Base of every compiled spec strategy. `spec` is a CLASS attribute set
    by `spec_strategy_class()`, never a dataclass field, so
    `dataclasses.replace()` (how the Lab tab applies a param override) only
    ever has to carry the tunable numbers."""

    spec: StrategySpec

    def _params(self) -> dict[str, float]:
        return {p.name: getattr(self, p.name) for p in self.spec.params}

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        params = self._params()
        if len(bars) < required_bars(self.spec, params):
            return False
        return _evaluate(self.spec.entry, bars, params)

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        if not self.spec.exit:
            return False
        params = self._params()
        if len(bars) < required_bars(self.spec, params):
            return False
        return _evaluate(self.spec.exit, bars, params)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        params = self._params()
        rule = self.spec.stop
        args = _resolved_args(rule.args, params)
        long_side = self.spec.direction == "long"
        if rule.kind == "atr_multiple":
            distance = float(atr(bars, int(args.get("period", 14))).iloc[-1]) * args["multiple"]
            return entry_price - distance if long_side else entry_price + distance
        if rule.kind == "percent":
            fraction = args["pct"] / 100.0
            return entry_price * (1 - fraction) if long_side else entry_price * (1 + fraction)
        buffer_fraction = args.get("buffer_pct", 0.0) / 100.0
        if rule.kind == "swing_extreme":
            lookback = int(args["lookback"])
            level = (
                float(bars["Low"].tail(lookback).min()) if long_side
                else float(bars["High"].tail(lookback).max())
            )
        else:  # prior_bar_extreme
            level = float(bars["Low"].iloc[-1]) if long_side else float(bars["High"].iloc[-1])
        return level * (1 - buffer_fraction) if long_side else level * (1 + buffer_fraction)

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        params = self._params()
        rule = self.spec.target
        if rule.kind == "none":
            return None
        args = _resolved_args(rule.args, params)
        long_side = self.spec.direction == "long"
        if rule.kind == "risk_multiple":
            risk = abs(entry_price - self.stop_price(bars, entry_price))
            distance = risk * args["multiple"]
        elif rule.kind == "percent":
            distance = entry_price * args["pct"] / 100.0
        else:  # atr_multiple
            distance = float(atr(bars, int(args.get("period", 14))).iloc[-1]) * args["multiple"]
        return entry_price + distance if long_side else entry_price - distance


def _class_name(spec: StrategySpec) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", " ", spec.name).title().replace(" ", "")
    return f"Custom{slug or 'Strategy'}"


def _field_type(param: SpecParam) -> type:
    return int if param.kind == "int" else float


@lru_cache(maxsize=64)
def _compile(spec_json: str) -> type[SpecStrategy]:
    import json

    spec = parse_spec(json.loads(spec_json))
    fields = [
        (
            p.name,
            _field_type(p),
            param_field(
                p.default, label=p.label, minimum=p.minimum, maximum=p.maximum,
                step=p.step, help=p.help,
            ),
        )
        for p in spec.params
    ]
    return dataclasses.make_dataclass(
        _class_name(spec),
        fields,
        bases=(SpecStrategy,),
        # name/timeframe/direction are UNANNOTATED class attributes, exactly
        # as in the hand-written strategies -- @dataclass only turns
        # annotated attributes into fields, so these stay out of __init__
        # and out of dataclasses.replace().
        namespace={
            "spec": spec,
            "name": spec.name,
            "timeframe": spec.timeframe,
            "direction": spec.direction,
            "__doc__": spec.description,
        },
    )


def spec_strategy_class(spec: StrategySpec) -> type[SpecStrategy]:
    """The @dataclass Strategy subclass for `spec` -- tunable numbers as
    real `param_field()`s, so describe_params()/apply_params() work on it
    with no custom-strategy branch anywhere."""
    import json

    return _compile(json.dumps(spec.to_dict(), sort_keys=True))


def build_spec_strategy(spec: StrategySpec) -> SpecStrategy:
    return spec_strategy_class(spec)()


# --------------------------------------------------------------------------
# English rendering (what the review step displays)
# --------------------------------------------------------------------------


def _render_number(value: Any, spec: StrategySpec) -> str:
    if isinstance(value, dict):
        param = next((p for p in spec.params if p.name == value["param"]), None)
        return f"{param.label} ({_fmt(param.default)})" if param else value["param"]
    return _fmt(value)


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _render_operand(operand: Operand, spec: StrategySpec) -> str:
    args = {key: _render_number(value, spec) for key, value in operand.args.items()}
    text = INDICATORS[operand.kind].render(args)
    scale = operand.scale
    if not (isinstance(scale, (int, float)) and float(scale) == 1.0):
        text = f"{_render_number(scale, spec)} x {text}"
    if operand.offset == 1:
        text = f"{text} (1 bar ago)"
    elif operand.offset > 1:
        text = f"{text} ({operand.offset} bars ago)"
    return text


_OP_WORDS = {
    ">": "is above", ">=": "is at or above", "<": "is below", "<=": "is at or below",
    "crosses_above": "crosses above", "crosses_below": "crosses below",
}


def _render_condition(condition: Condition, spec: StrategySpec) -> str:
    if isinstance(condition, AnyOf):
        return "either " + " OR ".join(_render_condition(c, spec) for c in condition.options)
    return (
        f"{_render_operand(condition.left, spec)} {_OP_WORDS[condition.op]} "
        f"{_render_operand(condition.right, spec)}"
    )


def _render_stop(spec: StrategySpec) -> str:
    rule, side = spec.stop, ("below" if spec.direction == "long" else "above")
    args = {key: _render_number(value, spec) for key, value in rule.args.items()}
    if rule.kind == "atr_multiple":
        return f"{args['multiple']} x ATR({args.get('period', '14')}) {side} the entry price"
    if rule.kind == "percent":
        return f"{args['pct']}% {side} the entry price"
    buffer_text = f", plus a {args['buffer_pct']}% cushion" if "buffer_pct" in args else ""
    extreme = "low" if spec.direction == "long" else "high"
    if rule.kind == "swing_extreme":
        return f"the {extreme} of the last {args['lookback']} bars{buffer_text}"
    return f"the entry bar's {extreme}{buffer_text}"


def _render_target(spec: StrategySpec) -> str:
    rule = spec.target
    args = {key: _render_number(value, spec) for key, value in rule.args.items()}
    if rule.kind == "none":
        return "no fixed target -- the position closes on the exit rule or the stop"
    direction_word = "above" if spec.direction == "long" else "below"
    if rule.kind == "risk_multiple":
        return f"{args['multiple']} x the initial risk (stop distance) {direction_word} entry"
    if rule.kind == "percent":
        return f"{args['pct']}% {direction_word} the entry price"
    return f"{args['multiple']} x ATR({args.get('period', '14')}) {direction_word} entry"


def describe_spec(spec: StrategySpec) -> dict[str, Any]:
    """The compiled rules in English -- rendered FROM the parsed spec, so
    the authoring UI's review step shows what will actually run rather than
    the model's own account of what it wrote."""
    timeframe_word = "5-minute" if spec.timeframe == "5m" else "daily"
    return {
        "summary": (
            f"{spec.direction.capitalize()}-only {spec.kind.lower()} strategy on "
            f"{timeframe_word} bars."
        ),
        "entry": [_render_condition(c, spec) for c in spec.entry],
        "exit": [_render_condition(c, spec) for c in spec.exit],
        "stop": _render_stop(spec),
        "target": _render_target(spec),
        "warmupBars": required_bars(spec, {p.name: p.default for p in spec.params}),
    }


def vocabulary() -> list[dict[str, Any]]:
    """The indicator table as data -- used to build the authoring prompt and
    to show the user what a description can refer to, from one source."""
    return [
        {
            "kind": definition.kind,
            "args": list(definition.numeric_args),
            "description": definition.description,
            "intradayOnly": definition.kind in INTRADAY_ONLY_INDICATORS,
        }
        for definition in INDICATORS.values()
    ]
