"""Natural language -> a validated strategy spec (strategies/spec.py).

The Strategies tab's "Describe a strategy" box sends a sentence here; this
module turns it into a `StrategySpec` the existing per-symbol engine can run.
It is the app's second LLM feature after engine/chat_assistant.py, and it
follows the same rules: `ANTHROPIC_API_KEY` from .env, a clear "not
configured" reply rather than a raised exception, and a per-use cost the
rest of the app doesn't have.

Three design decisions worth stating, because each one is what keeps a
strategy typed in English as honest as one written in Python:

1. **The model emits a spec, never code.** Its entire output surface is the
   JSON schema below, which can only name indicators from
   `strategies/spec.py:INDICATORS`. It cannot reach a bar after the current
   one, read a fundamental, or import anything -- not because it was told
   not to, but because the vocabulary has no way to say it. Generated
   Python would have all three risks and no way to check for them before
   running.
2. **`parse_spec()` is the gate, and the model gets its errors back.** A
   spec that fails validation is returned to the model with the exact
   message and re-attempted (up to `MAX_ATTEMPTS`); a spec that still fails
   is reported as a failure, never partially applied. The UI's review step
   renders `describe_spec()` -- the compiled rules -- not the model's own
   summary of what it wrote.
3. **Refusing is a first-class outcome.** "Buy when the CEO tweets" and
   "screen for P/E under 15" are both unexpressible here, and the schema
   has an explicit `decline` branch so the model says so instead of
   inventing the nearest rule it CAN express. A wrong-but-runnable strategy
   is worse than no strategy: it would log real metrics under the user's
   sentence and look like it had been tested.

A saved strategy is exploratory by construction -- it has no tracker entry
and no prior sample. It runs on the same engine, logs to the same `runs`
table, and is scored by the same `derive_status()` bar as everything else
(see engine/custom_strategies.py's module docstring).
"""

from __future__ import annotations

import json
from typing import Any

from engine.alpaca_client import first_env
from strategies.spec import (
    COMPARISON_OPS,
    INDICATORS,
    MAX_OFFSET,
    STOP_KINDS,
    TARGET_KINDS,
    StrategySpec,
    describe_spec,
    parse_spec,
    vocabulary,
)

MODEL = "claude-opus-5"
MAX_TOKENS = 8000

# One retry per validation failure. Two attempts fixes the ordinary case (a
# mistyped indicator argument); a spec still failing on the third is a
# request the vocabulary can't express, which is a decline, not a bug to
# grind on at the user's expense.
MAX_ATTEMPTS = 3


class AuthoringError(RuntimeError):
    """The request could not be turned into a runnable spec. Carries a
    message meant to be shown to the user verbatim (an API failure, a
    refusal reason, or the validator's last complaint)."""


def _vocabulary_text() -> str:
    lines = []
    for item in vocabulary():
        args = f" args: {', '.join(item['args'])}" if item["args"] else ""
        intraday = " (intraday timeframes only)" if item["intradayOnly"] else ""
        lines.append(f"- {item['kind']}{args} -- {item['description']}{intraday}")
    return "\n".join(lines)


def _system_prompt() -> str:
    return f"""You translate a plain-English description of a trading strategy into a strict JSON rule spec for the Trading Strategy Lab, a local backtesting app.

The spec you produce is compiled into a real strategy and run against five years of daily bars (or the ~50 trading days of 5-minute bars the data provider serves, for day-trading strategies). It is not a sketch -- whatever you write is exactly what gets measured.

INDICATOR VOCABULARY (the only values `kind` may take):
{_vocabulary_text()}

Comparison operators: {', '.join(COMPARISON_OPS)}. "crosses_above"/"crosses_below" compare the current bar against the previous one, so use them for genuine crossovers rather than a bare ">".

Structure:
- `entry` is a list of condition groups, ALL of which must hold for an entry. Each group is a list of comparisons, ANY of which satisfies that group. A group with one comparison is a plain AND-ed condition -- use that unless the description really says "or".
- `exit` is the same shape and closes an open position. Leave it empty if the strategy exits only at its stop or target.
- `stop` is required. Kinds: {', '.join(STOP_KINDS)}. `atr_multiple` (args: period, multiple), `percent` (pct), `swing_extreme` (lookback, buffer_pct), `prior_bar_extreme` (buffer_pct). It is always placed on the losing side for the strategy's direction -- state the distance, not the direction.
- `target` kinds: {', '.join(TARGET_KINDS)}. `risk_multiple` (multiple) is the usual choice -- N times the stop distance. Use `none` ONLY when there is a real exit rule, since with neither a target nor an exit the only way a trade closes is at a loss.
- `offset` on an operand means "this many bars ago", 0..{MAX_OFFSET}. `scale` multiplies the operand, so "price 2% above the 50-day average" is close > sma(50) with scale 1.02.

PARAMETERS -- this matters:
Every number a user might reasonably want to tune (a threshold, a period, a stop multiple, a target multiple) must be declared in `params` with a label, a default, and sensible minimum/maximum bounds, then referenced from the rule by name. A number written inline is frozen forever; a declared param becomes a slider the user can sweep. Use inline numbers only for values that are part of the rule's identity (e.g. the 0 in "MACD histogram is above 0"). Every declared param must actually be referenced, and every reference must name a declared param.

DIRECTION:
Pick "long" or "short" -- one spec is one side. If the description clearly trades both ways, produce the side it emphasizes and say so in `notes`.

WHEN TO DECLINE (set outcome to "decline" and explain in decline_reason):
- The rule needs data this engine does not have: fundamentals (P/E, earnings, revenue), news, sentiment, insider or options data, cross-symbol ranking or pair spreads, or anything about a specific company's business.
- The rule needs information from the future, or from a bar after the one being evaluated.
- The description is too vague to pin to a specific, testable condition ("buy strong stocks", "trade the trend"), and you would have to invent the actual rule.
Declining is the correct answer in all three cases. Do not substitute the nearest rule you CAN express and present it as what was asked for -- a runnable strategy that does not match the user's sentence is worse than none, because it produces real-looking measurements of the wrong idea.

NOTES:
Use `notes` for judgment calls the user should check: an approximation you made, a number you chose that the description left open, a side you picked, a part of the request you dropped. If you translated the description literally with nothing left over, say so briefly. Never use `notes` to claim the strategy is good, profitable, or worth trading -- you have measured nothing.

Name the strategy descriptively (3-60 characters) after what the rule DOES, not after an outcome ("20-Day Breakout with ATR Stop", never "Reliable Profit Setup")."""


_NUMERIC = {
    "type": "object",
    "description": (
        "A number: set `value` for a literal, or `param` to reference a declared "
        "parameter by name. Exactly one of the two is non-null."
    ),
    "properties": {
        "value": {"anyOf": [{"type": "number"}, {"type": "null"}]},
        "param": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": ["value", "param"],
    "additionalProperties": False,
}

_OPERAND = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": sorted(INDICATORS)},
        "args": {
            "type": "array",
            "description": "Arguments for this indicator; omit for indicators that take none.",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": ["period", "fast", "slow", "signal", "stddev", "value"],
                    },
                    "number": {"$ref": "#/$defs/numeric"},
                },
                "required": ["name", "number"],
                "additionalProperties": False,
            },
        },
        "offset": {"type": "integer", "description": f"Bars ago; 0 = current bar, max {MAX_OFFSET}."},
        "scale": {"$ref": "#/$defs/numeric"},
    },
    "required": ["kind", "args", "offset", "scale"],
    "additionalProperties": False,
}

_COMPARISON = {
    "type": "object",
    "properties": {
        "left": {"$ref": "#/$defs/operand"},
        "op": {"type": "string", "enum": list(COMPARISON_OPS)},
        "right": {"$ref": "#/$defs/operand"},
    },
    "required": ["left", "op", "right"],
    "additionalProperties": False,
}

_CONDITION_GROUP = {
    "type": "object",
    "description": (
        "One AND-ed condition. A single comparison is the normal case; two or "
        "more means any of them satisfies this condition."
    ),
    "properties": {"any_of": {"type": "array", "items": {"$ref": "#/$defs/comparison"}}},
    "required": ["any_of"],
    "additionalProperties": False,
}

_RULE = {
    "type": "object",
    "properties": {
        "kind": {"type": "string"},
        "args": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": ["period", "multiple", "pct", "lookback", "buffer_pct"],
                    },
                    "number": {"$ref": "#/$defs/numeric"},
                },
                "required": ["name", "number"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["kind", "args"],
    "additionalProperties": False,
}

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "$defs": {
        "numeric": _NUMERIC,
        "operand": _OPERAND,
        "comparison": _COMPARISON,
        "conditionGroup": _CONDITION_GROUP,
        "rule": _RULE,
    },
    "properties": {
        "outcome": {"type": "string", "enum": ["strategy", "decline"]},
        "decline_reason": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "notes": {"type": "string"},
        "strategy": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": ["Day Trading", "Swing Trading"]},
                        "timeframe": {"type": "string", "enum": ["1d", "5m"]},
                        "direction": {"type": "string", "enum": ["long", "short"]},
                        "description": {"type": "string"},
                        "params": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "label": {"type": "string"},
                                    "kind": {"type": "string", "enum": ["int", "float"]},
                                    "default": {"type": "number"},
                                    "minimum": {"type": "number"},
                                    "maximum": {"type": "number"},
                                    "step": {"anyOf": [{"type": "number"}, {"type": "null"}]},
                                    "help": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                },
                                "required": [
                                    "name", "label", "kind", "default",
                                    "minimum", "maximum", "step", "help",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "entry": {"type": "array", "items": {"$ref": "#/$defs/conditionGroup"}},
                        "exit": {"type": "array", "items": {"$ref": "#/$defs/conditionGroup"}},
                        "stop": {"$ref": "#/$defs/rule"},
                        "target": {"$ref": "#/$defs/rule"},
                    },
                    "required": [
                        "name", "kind", "timeframe", "direction", "description",
                        "params", "entry", "exit", "stop", "target",
                    ],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
    },
    "required": ["outcome", "decline_reason", "notes", "strategy"],
    "additionalProperties": False,
}


def available() -> tuple[bool, str]:
    """Whether authoring can run, and why not if it can't. Same graceful
    degradation as engine/chat_assistant.py:available()."""
    if not first_env("ANTHROPIC_API_KEY"):
        return False, "ANTHROPIC_API_KEY missing from .env. Add a key line and restart the API."
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "anthropic is not installed (pip install anthropic)."
    return True, "ok"


def _client() -> Any:
    from anthropic import Anthropic

    return Anthropic(api_key=first_env("ANTHROPIC_API_KEY"))


# --------------------------------------------------------------------------
# Wire format -> spec dict
# --------------------------------------------------------------------------
#
# The schema above uses arrays of {name, number} rather than free-form
# objects because a strict JSON schema requires additionalProperties:false,
# which rules out a dict with arbitrary keys. These helpers flatten that
# wire shape back into the natural spec dict parse_spec() expects. They do
# no validating of their own -- an unusable value survives into parse_spec()
# and comes back as a specific error the model can act on, rather than
# being silently dropped here.


def _number(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw
    if raw.get("param"):
        return {"param": raw["param"]}
    return raw.get("value")


def _args(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list):
        return {}
    out: dict[str, Any] = {}
    for item in raw:
        if not isinstance(item, dict) or "name" not in item:
            continue
        value = _number(item.get("number"))
        if value is not None:
            out[item["name"]] = value
    return out


def _operand(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {"kind": raw.get("kind"), "args": _args(raw.get("args"))}
    if raw.get("offset"):
        out["offset"] = raw["offset"]
    scale = _number(raw.get("scale"))
    if scale is not None:
        out["scale"] = scale
    return out


def _comparison(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {"left": _operand(raw.get("left")), "op": raw.get("op"), "right": _operand(raw.get("right"))}


def _conditions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    conditions = []
    for group in raw:
        options = group.get("any_of") if isinstance(group, dict) else None
        if not isinstance(options, list) or not options:
            continue
        if len(options) == 1:
            conditions.append(_comparison(options[0]))
        else:
            conditions.append({"any": [_comparison(item) for item in options]})
    return conditions


def _rule(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {"kind": raw.get("kind"), "args": _args(raw.get("args"))}


def to_spec_dict(strategy: dict[str, Any]) -> dict[str, Any]:
    """The model's wire payload as a spec dict for `parse_spec()`."""
    return {
        "name": strategy.get("name"),
        "kind": strategy.get("kind"),
        "timeframe": strategy.get("timeframe"),
        "direction": strategy.get("direction"),
        "description": strategy.get("description"),
        "params": strategy.get("params", []),
        "entry": _conditions(strategy.get("entry")),
        "exit": _conditions(strategy.get("exit")),
        "stop": _rule(strategy.get("stop")),
        "target": _rule(strategy.get("target")),
    }


# --------------------------------------------------------------------------
# Drafting
# --------------------------------------------------------------------------


def draft(description: str, existing_names: list[str] | None = None) -> dict[str, Any]:
    """Turn `description` into a validated spec.

    Returns `{"spec": StrategySpec, "notes": str, "attempts": int}`. Raises
    `AuthoringError` when the model declines, when every attempt fails
    validation, or when the API call itself fails -- the message is written
    to be shown to the user as-is.
    """
    text = (description or "").strip()
    if not text:
        raise AuthoringError("Describe the strategy you want in a sentence or two.")

    ok, reason = available()
    if not ok:
        raise AuthoringError(f"Strategy authoring isn't configured: {reason}")

    client = _client()
    taken = ", ".join(existing_names or []) or "(none yet)"
    conversation: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Strategy description:\n{text}\n\n"
                f"Names already in use (pick a different one): {taken}"
            ),
        }
    ]

    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=_system_prompt(),
                output_config={"format": {"type": "json_schema", "schema": RESPONSE_SCHEMA}},
                messages=conversation,
            )
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user, never a 500
            raise AuthoringError(f"Strategy authoring request failed: {exc}") from exc

        # Checked before reading content: a refusal returns HTTP 200 with an
        # empty or partial content list, so indexing straight into it would
        # raise an opaque IndexError instead of saying what happened.
        if response.stop_reason == "refusal":
            raise AuthoringError(
                "The model declined to answer this request. Rephrase the strategy "
                "in terms of price, volume, and indicator rules."
            )

        raw_text = "".join(block.text for block in response.content if block.type == "text")
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise AuthoringError(f"The model's reply wasn't valid JSON: {exc}") from exc

        if payload.get("outcome") == "decline" or not payload.get("strategy"):
            raise AuthoringError(
                payload.get("decline_reason")
                or "That strategy can't be expressed with the data this engine has."
            )

        try:
            spec = parse_spec(to_spec_dict(payload["strategy"]))
        except ValueError as exc:
            last_error = str(exc)
            if attempt == MAX_ATTEMPTS:
                break
            conversation.append({"role": "assistant", "content": raw_text})
            conversation.append({
                "role": "user",
                "content": (
                    f"That spec failed validation: {last_error}\n\n"
                    "Fix exactly that problem and return the whole spec again. If the "
                    "rule can't be expressed within the vocabulary, decline instead."
                ),
            })
            continue

        return {
            "spec": spec,
            "notes": payload.get("notes", ""),
            "attempts": attempt,
        }

    raise AuthoringError(
        f"Couldn't produce a valid strategy after {MAX_ATTEMPTS} attempts. "
        f"Last validation error: {last_error}"
    )


def draft_payload(description: str, existing_names: list[str] | None = None) -> dict[str, Any]:
    """`draft()` plus the compiled English rendering -- the exact shape the
    API returns and the review step displays. `rules` comes from
    `describe_spec()`, i.e. from the parsed spec, so what the user approves
    is what will run."""
    result = draft(description, existing_names)
    spec: StrategySpec = result["spec"]
    return {
        "name": spec.name,
        "kind": spec.kind,
        "timeframe": spec.timeframe,
        "direction": spec.direction,
        "description": spec.description,
        "notes": result["notes"],
        "attempts": result["attempts"],
        "spec": spec.to_dict(),
        "rules": describe_spec(spec),
        "params": [p.to_dict() for p in spec.params],
    }
