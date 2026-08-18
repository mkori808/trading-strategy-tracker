"""First-class, immutable backtest-universe registry.

Universe ids are persisted on new experiments/runs.  Historical rows remain
NULL: inferring an id from a symbol list after the fact would invent provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any


REGISTRY_DIR = Path(__file__).resolve().parent.parent / "universes"


@dataclass(frozen=True)
class UniverseDefinition:
    universe_id: str
    label: str
    category: str
    description: str
    asset_class: str
    symbols: tuple[str, ...]
    membership_ledger_path: str | None
    membership_mode: str
    data_coverage: dict[str, dict[str, Any]]
    cost_model: dict[str, Any]
    primary_benchmark: str | None
    equal_weight_benchmark: str | None
    applicable_gates: dict[str, dict[str, Any]]
    runnable: bool
    selectable: bool
    unavailable_reason: str | None
    pit_status: dict[str, Any] | None = None
    coverage_start: str | None = None
    coverage_end: str | None = None
    approximate_security_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.universe_id,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "assetClass": self.asset_class,
            "symbols": list(self.symbols),
            "membershipLedgerPath": self.membership_ledger_path,
            "membershipMode": self.membership_mode,
            "dataCoverage": self.data_coverage,
            "costModel": self.cost_model,
            "primaryBenchmark": self.primary_benchmark,
            "equalWeightBenchmark": self.equal_weight_benchmark,
            "applicableGates": self.applicable_gates,
            "runnable": self.runnable,
            "selectable": self.selectable,
            "unavailableReason": self.unavailable_reason,
            "pitStatus": self.pit_status,
            "coverageStart": self.coverage_start,
            "coverageEnd": self.coverage_end,
            "approximateSecurityCount": self.approximate_security_count,
        }


def _definition(path: Path) -> UniverseDefinition:
    payload = json.loads(path.read_text(encoding="utf-8"))
    universe_id = str(payload.get("id") or "")
    if path.stem != universe_id:
        raise ValueError(f"{path}: id must match filename")
    asset_class = str(payload.get("assetClass") or "")
    if asset_class not in {"equity", "crypto", "futures", "single-instrument"}:
        raise ValueError(f"{path}: invalid assetClass {asset_class!r}")
    symbols = tuple(dict.fromkeys(str(s).upper() for s in payload.get("symbols", [])))
    ledger = payload.get("membershipLedgerPath")
    if not symbols and not ledger:
        raise ValueError(f"{path}: symbols or membershipLedgerPath is required")
    coverage = payload.get("dataCoverage") or {}
    if symbols and set(coverage) != set(symbols):
        missing = sorted(set(symbols) - set(coverage))
        extra = sorted(set(coverage) - set(symbols))
        raise ValueError(f"{path}: per-symbol dataCoverage mismatch; missing={missing}, extra={extra}")
    for symbol, item in coverage.items():
        for key in ("dataSource", "coverageStart", "coverageEnd"):
            if not item.get(key):
                raise ValueError(f"{path}: dataCoverage.{symbol}.{key} is required")
        if date.fromisoformat(item["coverageEnd"]) < date.fromisoformat(item["coverageStart"]):
            raise ValueError(f"{path}: reversed coverage for {symbol}")
    cost_model = payload.get("costModel") or {}
    if not cost_model.get("type"):
        raise ValueError(f"{path}: costModel.type is required")
    if asset_class == "futures" and cost_model.get("type") == "equity_spread":
        raise ValueError(f"{path}: futures cannot use the equity spread estimator")
    membership_mode = str(payload.get("membershipMode") or "fixed_symbols")
    pit_status = None
    runnable = bool(payload.get("runnable", True))
    unavailable_reason = payload.get("unavailableReason")
    coverage_start = payload.get("coverageStart")
    coverage_end = payload.get("coverageEnd")
    approximate_security_count = payload.get("approximateSecurityCount")
    if membership_mode == "dynamic_pit_security_master":
        from engine.pit_all_stocks import inspect_dataset

        status = inspect_dataset()
        pit_status = status.to_dict()
        runnable = status.ready
        unavailable_reason = None if status.ready else status.summary + (
            ": " + "; ".join([*status.missing_artifacts, *status.invalid_reasons])
            if status.missing_artifacts or status.invalid_reasons else ""
        )
        coverage_start = status.coverage_start or coverage_start
        coverage_end = status.coverage_end or coverage_end
        approximate_security_count = status.security_count or approximate_security_count
    return UniverseDefinition(
        universe_id=universe_id,
        label=str(payload.get("label") or universe_id),
        category=str(payload.get("category") or "Other"),
        description=str(payload.get("description") or ""),
        asset_class=asset_class,
        symbols=symbols,
        membership_ledger_path=str(ledger) if ledger else None,
        membership_mode=membership_mode,
        data_coverage=coverage,
        cost_model=cost_model,
        primary_benchmark=payload.get("primaryBenchmark"),
        equal_weight_benchmark=payload.get("equalWeightBenchmark"),
        applicable_gates=payload.get("applicableGates") or {},
        runnable=runnable,
        selectable=bool(payload.get("selectable", True)),
        unavailable_reason=unavailable_reason,
        pit_status=pit_status,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        approximate_security_count=(
            int(approximate_security_count) if approximate_security_count is not None else None
        ),
    )


def universe_registry(directory: Path | None = None) -> dict[str, UniverseDefinition]:
    directory = directory or REGISTRY_DIR
    definitions = [_definition(path) for path in sorted(directory.glob("*.json"))]
    if not definitions:
        raise RuntimeError(f"no universe definitions found in {directory}")
    ids = [item.universe_id for item in definitions]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate universe id")
    return {item.universe_id: item for item in definitions}


def registered_universe(universe_id: str) -> UniverseDefinition:
    try:
        return universe_registry()[universe_id]
    except KeyError as exc:
        raise ValueError(f"unknown registered universe {universe_id!r}") from exc


def runnable_symbols(universe_id: str) -> list[str]:
    definition = registered_universe(universe_id)
    if not definition.runnable:
        raise ValueError(definition.unavailable_reason or f"{universe_id} is not runnable")
    if definition.membership_mode == "dynamic_pit_security_master":
        from engine.pit_all_stocks import security_ids

        return security_ids()
    if not definition.symbols:
        raise ValueError(f"{universe_id} requires a membership-ledger loader before it can run")
    return list(definition.symbols)


def gate_applicability(
    universe_id: str | None, gate_key: str,
) -> tuple[bool, str | None]:
    if not universe_id:
        return True, None
    definition = registered_universe(universe_id)
    declaration = definition.applicable_gates.get(gate_key)
    if declaration is None:
        return True, None
    return bool(declaration.get("applicable")), declaration.get("reason")
