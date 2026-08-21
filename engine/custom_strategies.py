"""On-disk store for user-authored strategies (see strategies/spec.py for
the rule language, engine/strategy_authoring.py for the natural-language
front end).

One JSON file per strategy in `data/custom_strategies/`, holding the spec
AND the prompt it was written from. Keeping the prompt is the point: a rule
that came out of a sentence is only auditable if the sentence is still
there next to it, so "why does this strategy use a 2x ATR stop?" has an
answer that doesn't depend on anyone's memory.

These are deliberately NOT added to `strategies/registry.py`'s
ALL_STRATEGY_NAMES. That list is checked 1:1 against
strategy_tracker.xlsx's Day Trading / Swing Trading tabs
(tests/test_engine/test_registry.py), and the tracker stays the source of
truth for the established catalogue -- a strategy typed into the app at
runtime has not been through that decision. Callers that want both sets ask
for them explicitly (api/main.py's `_known_strategy_names`), so a custom
strategy can never quietly become indistinguishable from a tracker entry.

Every custom strategy still runs on the standard per-symbol engine, logs to
the same `runs` table, and is scored by the same `derive_status()` bar as
everything else. Being user-authored buys it no leniency anywhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from strategies.registry import ALL_STRATEGY_NAMES
from strategies.spec import StrategySpec, describe_spec, parse_spec

STORE_DIR = Path(__file__).resolve().parent.parent / "data" / "custom_strategies"

# Small enough that reading the whole directory per request is cheaper than
# any cache invalidation scheme worth trusting; large enough that a runaway
# authoring loop can't fill the disk.
MAX_CUSTOM_STRATEGIES = 100


@dataclass(frozen=True)
class CustomStrategy:
    spec: StrategySpec
    prompt: str
    created_at: str
    path: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "kind": self.spec.kind,
            "timeframe": self.spec.timeframe,
            "direction": self.spec.direction,
            "description": self.spec.description,
            "createdAt": self.created_at,
            "prompt": self.prompt,
            "spec": self.spec.to_dict(),
            "rules": describe_spec(self.spec),
        }


@dataclass(frozen=True)
class LoadError:
    """A stored file that no longer parses -- surfaced, never swallowed. A
    spec written by an older version of strategies/spec.py whose rule the
    parser has since tightened must show up as a broken strategy the user
    can see and delete, not vanish from the list as if it never existed."""

    filename: str
    error: str


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "strategy"


def _path_for(name: str) -> Path:
    return STORE_DIR / f"{_slug(name)}.json"


def load_all() -> tuple[dict[str, CustomStrategy], list[LoadError]]:
    """Every stored strategy by name, plus every file that failed to load."""
    if not STORE_DIR.exists():
        return {}, []
    strategies: dict[str, CustomStrategy] = {}
    errors: list[LoadError] = []
    for path in sorted(STORE_DIR.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            spec = parse_spec(payload["spec"])
        except Exception as exc:  # noqa: BLE001 -- any failure is reported, not raised
            errors.append(LoadError(filename=path.name, error=str(exc)))
            continue
        strategies[spec.name] = CustomStrategy(
            spec=spec,
            prompt=payload.get("prompt", ""),
            created_at=payload.get("createdAt", ""),
            path=path,
        )
    return strategies, errors


def custom_strategies() -> dict[str, CustomStrategy]:
    return load_all()[0]


def custom_strategy_names() -> list[str]:
    return list(custom_strategies())


def custom_spec(name: str) -> StrategySpec | None:
    entry = custom_strategies().get(name)
    return entry.spec if entry else None


def is_custom(name: str) -> bool:
    return name in custom_strategies()


def name_conflict(name: str, existing: dict[str, CustomStrategy] | None = None) -> str | None:
    """Why `name` can't be used, or None if it's free. Slug collisions count
    as conflicts too -- two names differing only in punctuation would map to
    one file and the second save would overwrite the first."""
    stored = custom_strategies() if existing is None else existing
    if name in ALL_STRATEGY_NAMES:
        return f"{name!r} is already a registered strategy from strategy_tracker.xlsx."
    if name in stored:
        return f"A custom strategy named {name!r} already exists."
    slug = _slug(name)
    clash = next((other for other in stored if _slug(other) == slug), None)
    if clash is not None:
        return f"{name!r} is too close to the existing custom strategy {clash!r}."
    return None


def save(spec: StrategySpec, prompt: str) -> CustomStrategy:
    """Write a new custom strategy. Raises ValueError on a name conflict or
    a full store -- never overwrites an existing strategy, since a silent
    overwrite would orphan that strategy's logged run history under a name
    whose rules had changed underneath it."""
    stored, _errors = load_all()
    conflict = name_conflict(spec.name, stored)
    if conflict:
        raise ValueError(conflict)
    if len(stored) >= MAX_CUSTOM_STRATEGIES:
        raise ValueError(
            f"The custom strategy store already holds {MAX_CUSTOM_STRATEGIES} strategies. "
            "Delete one before adding another."
        )
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(spec.name)
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(
        json.dumps(
            {"spec": spec.to_dict(), "prompt": prompt, "createdAt": created_at},
            indent=2,
        ),
        encoding="utf-8",
    )
    return CustomStrategy(spec=spec, prompt=prompt, created_at=created_at, path=path)


def delete(name: str) -> None:
    """Remove a custom strategy's definition. Its logged runs stay in
    logs/runs.db -- deleting a definition is not a licence to erase the
    measurements it produced (same principle as ARCHIVED_STRATEGY_NAMES
    hiding a strategy rather than deleting it)."""
    stored, _errors = load_all()
    entry = stored.get(name)
    if entry is None:
        raise ValueError(f"No custom strategy named {name!r}")
    entry.path.unlink()


def delete_broken(filename: str) -> None:
    """Remove a file that failed to load (reported by load_all's errors).
    Path-joined by basename only so a filename from an HTTP request can't
    reach outside the store directory."""
    path = STORE_DIR / Path(filename).name
    if not path.exists() or path.suffix != ".json":
        raise ValueError(f"No stored custom-strategy file named {filename!r}")
    path.unlink()
