"""Capability-based Sharadar data registry and hypothesis readiness gate.

This module separates provider capability from hypothesis suitability. It is a
data-readiness diagnostic only: it never runs a strategy, computes returns, or
changes universe enablement.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import StrEnum
import json
from pathlib import Path
from typing import Iterable


class CapabilityStatus(StrEnum):
    SUPPORTED = "SUPPORTED"
    SUPPORTED_WITH_LIMITATIONS = "SUPPORTED_WITH_LIMITATIONS"
    PARTIAL = "PARTIAL"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class LimitationPolicy(StrEnum):
    EXCLUDE_AMBIGUOUS_CASES = "EXCLUDE_AMBIGUOUS_CASES"
    FAIL_CLOSED_ON_MISSING = "FAIL_CLOSED_ON_MISSING"
    USE_VENDOR_AS_AUTHORITATIVE = "USE_VENDOR_AS_AUTHORITATIVE"
    REQUIRE_SUPPLEMENTAL_EVIDENCE = "REQUIRE_SUPPLEMENTAL_EVIDENCE"
    NOT_ALLOWED = "NOT_ALLOWED"


class Readiness(StrEnum):
    DATA_READY = "DATA_READY"
    DATA_READY_WITH_LIMITATIONS = "DATA_READY_WITH_LIMITATIONS"
    DATA_MATERIAL_LIMITATION = "DATA_MATERIAL_LIMITATION"
    DATA_BLOCKED = "DATA_BLOCKED"


@dataclass(frozen=True)
class DataCapability:
    capability_id: str
    name: str
    status: CapabilityStatus
    provider: str
    source_tables: list[str]
    validated_scope: str
    known_limitations: list[str]
    safe_use_cases: list[str]
    unsafe_use_cases: list[str]
    evidence_refs: list[str]
    last_validated_at: datetime | None
    limitation_policy: LimitationPolicy = LimitationPolicy.FAIL_CLOSED_ON_MISSING

    def to_dict(self) -> dict:
        value = asdict(self)
        value["status"] = self.status.value
        value["limitation_policy"] = self.limitation_policy.value
        value["last_validated_at"] = self.last_validated_at.isoformat() if self.last_validated_at else None
        return value


@dataclass(frozen=True)
class DataRequirement:
    capability_id: str
    required_statuses: set[str]
    critical: bool = True
    notes: str | None = None
    affected_security_period_pct: float | None = None
    affected_trade_pct: float | None = None


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    name: str
    requirements: tuple[DataRequirement, ...]


@dataclass(frozen=True)
class DataReadinessResult:
    hypothesis_id: str
    hypothesis_name: str
    readiness: Readiness
    blockers: tuple[str, ...]
    limitations: tuple[str, ...]
    capabilities: tuple[dict, ...]


MATERIAL_SECURITY_PERIOD_PCT = 5.0
MATERIAL_TRADE_PCT = 10.0


def _cap(
    capability_id: str,
    name: str,
    status: CapabilityStatus,
    tables: list[str],
    scope: str,
    limitations: list[str],
    safe: list[str],
    unsafe: list[str],
    refs: list[str],
    policy: LimitationPolicy = LimitationPolicy.FAIL_CLOSED_ON_MISSING,
) -> DataCapability:
    return DataCapability(
        capability_id, name, status, "Sharadar", tables, scope, limitations,
        safe, unsafe, refs, datetime(2026, 9, 6, tzinfo=timezone.utc), policy,
    )


def default_registry() -> dict[str, DataCapability]:
    """Return the evidence-backed registry; statuses are deliberately conservative."""
    base = "reports/sharadar_contract_validation/report.md"
    recon = "reports/sharadar_contract_validation/reconstruction/panel.json"
    return {
        "daily_prices": _cap("daily_prices", "Daily historical prices", CapabilityStatus.SUPPORTED, ["stocks"], "Deep history to 1998; broad active/delisted coverage measured", ["Prices are ticker-keyed"], ["Daily price studies with identity-safe ticker windows"], ["Blind ticker stitching"], [base]),
        "adjusted_prices": _cap("adjusted_prices", "Total-return adjusted prices", CapabilityStatus.SUPPORTED_WITH_LIMITATIONS, ["stocks", "actions"], "Canonical reconstruction from closeunadj plus dated actions", ["HON vendor closeadj diverged 3.25%; vendor closeadj is cross-check only"], ["Reconstructed adjusted close with action audit"], ["Using vendor closeadj as unquestioned canonical series"], [base, recon], LimitationPolicy.EXCLUDE_AMBIGUOUS_CASES),
        "unadjusted_prices": _cap("unadjusted_prices", "Unadjusted prices", CapabilityStatus.SUPPORTED, ["stocks"], "closeunadj observed and empirically raw", [], ["Raw execution-price and terminal-price comparison"], ["Treating raw prices as total return"], [base]),
        "active_security_universe": _cap("active_security_universe", "Active security coverage", CapabilityStatus.SUPPORTED_WITH_LIMITATIONS, ["tickers", "stocks"], "Active securities in Sharadar population", ["Historical listing/type status is not fully date-effective"], ["Current and date-bounded active security research"], ["Assuming current category/exchange is historical PIT"], [base], LimitationPolicy.EXCLUDE_AMBIGUOUS_CASES),
        "delisted_price_history": _cap("delisted_price_history", "Delisted price history", CapabilityStatus.SUPPORTED_WITH_LIMITATIONS, ["tickers", "stocks", "actions"], "Delisted records and prices through last observed date", ["Terminal economic disposition is not universal"], ["Studies using final valid observation with incomplete-episode disclosure"], ["Silently dropping delisted positions"], [base], LimitationPolicy.FAIL_CLOSED_ON_MISSING),
        "basic_corporate_actions": _cap("basic_corporate_actions", "Basic corporate actions", CapabilityStatus.SUPPORTED, ["actions"], "Dated typed splits, dividends, spinoffs, listings/delistings", ["Some value semantics require reconstruction validation"], ["Split/dividend adjustment and action diagnostics"], ["Assuming every action encodes terminal consideration"], [base]),
        "ticker_history": _cap("ticker_history", "Ticker history", CapabilityStatus.PARTIAL, ["actions", "tickers"], "Ticker-change records available but not universally reliable", ["PNST action mismatch; ticker reuse exists"], ["Ticker-scoped windows with independent checks"], ["Ticker similarity or action pair as proof of continuity"], [base, "LESSONS.md"], LimitationPolicy.EXCLUDE_AMBIGUOUS_CASES),
        "permanent_identity": _cap("permanent_identity", "Permanent security identity", CapabilityStatus.PARTIAL, ["tickers"], "permaticker present; continuity verified only in bounded examples", ["Does not prove same economic security across cancellation/reissue"], ["Within-security joins after edge-case audit"], ["Successor or reused-ticker stitching"], [base, "reports/pit_acceptance/report.md"], LimitationPolicy.REQUIRE_SUPPLEMENTAL_EVIDENCE),
        "terminal_delisting_economics": _cap("terminal_delisting_economics", "Terminal delisting economics", CapabilityStatus.PARTIAL, ["actions", "stocks"], "Some action context and last prices; no universal terminal disposition", ["Bankruptcy, mixed consideration, and successor equity remain unresolved"], ["Ordinary studies that exclude ambiguous episodes"], ["Assuming last price equals shareholder terminal value"], ["reports/pit_acceptance/primary_evidence_notes.json"], LimitationPolicy.REQUIRE_SUPPLEMENTAL_EVIDENCE),
        "successor_security_linkage": _cap("successor_security_linkage", "Successor security linkage", CapabilityStatus.UNSUPPORTED, ["actions", "tickers"], "CIK can verify hand-identified links but no complete successor graph", ["Ticker/name/relatedticker linkage creates false positives"], ["None without external lineage evidence"], ["Post-reorg continuity or successor returns"], ["LESSONS.md", "reports/pit_acceptance/report.md"], LimitationPolicy.NOT_ALLOWED),
        "complex_reorganization_chain": _cap("complex_reorganization_chain", "Complex reorganization chains", CapabilityStatus.UNSUPPORTED, ["actions", "tickers"], "No discoverable complete chain across cancellation, reissue, and successor entities", ["Ground-truth screen found discovery gap"], ["None"], ["Distressed/post-reorg portfolio accounting"], ["LESSONS.md"], LimitationPolicy.NOT_ALLOWED),
        "pit_fundamentals": _cap("pit_fundamentals", "Point-in-time fundamentals", CapabilityStatus.SUPPORTED, ["fundamentals"], "AR filing-date semantics validated", ["MR snapshots are not PIT facts"], ["As-reported filing-date fundamentals"], ["Back-projecting current snapshots"], ["research/PIT_DATA_REQUIREMENTS.md"]),
        "market_cap_daily": _cap("market_cap_daily", "Daily market capitalization", CapabilityStatus.SUPPORTED, ["daily"], "Daily marketcap observed from 1998-12-01 onward", ["No pre-1998 daily marketcap in measured table"], ["Percentile size universes within coverage"], ["Fixed threshold before coverage start"], ["data/pit_us_all_stocks/phase1_build_manifest.json"]),
        "sp500_membership": _cap("sp500_membership", "Historical S&P 500 membership", CapabilityStatus.SUPPORTED, ["sp500"], "Machine-readable additions/removals and reasons to 1998", ["Identity still required for ticker reuse"], ["S&P 500 membership event studies"], ["S&P 400/600 inference"], ["research/PIT_DATA_REQUIREMENTS.md"]),
        "sp400_membership": _cap("sp400_membership", "Historical S&P 400 membership", CapabilityStatus.UNSUPPORTED, [], "403 under current entitlement", ["No product-scope table"], ["None"], ["S&P 400 PIT studies"], ["research/PIT_DATA_REQUIREMENTS.md"], LimitationPolicy.NOT_ALLOWED),
        "sp600_membership": _cap("sp600_membership", "Historical S&P 600 membership", CapabilityStatus.UNSUPPORTED, [], "403 under current entitlement", ["No product-scope table"], ["None"], ["S&P 600 PIT studies"], ["research/PIT_DATA_REQUIREMENTS.md"], LimitationPolicy.NOT_ALLOWED),
        "security_type_classification": _cap("security_type_classification", "PIT security type classification", CapabilityStatus.PARTIAL, ["tickers"], "Current category only", ["No date-effective security-type intervals"], ["Current snapshot screens"], ["Historical common-stock eligibility without supplements"], [base], LimitationPolicy.EXCLUDE_AMBIGUOUS_CASES),
        "exchange_listing_history": _cap("exchange_listing_history", "Historical exchange listing", CapabilityStatus.PARTIAL, ["tickers", "actions"], "Current exchange plus event context", ["Historical primary listing venue changes not complete"], ["Date-bounded exchange studies with exclusions"], ["Assuming current exchange applies historically"], [base], LimitationPolicy.EXCLUDE_AMBIGUOUS_CASES),
    }


def _material(requirement: DataRequirement) -> bool:
    return (
        requirement.affected_security_period_pct is not None
        and requirement.affected_security_period_pct > MATERIAL_SECURITY_PERIOD_PCT
    ) or (
        requirement.affected_trade_pct is not None
        and requirement.affected_trade_pct > MATERIAL_TRADE_PCT
    )


def evaluate_data_readiness(
    hypothesis: Hypothesis,
    registry: dict[str, DataCapability] | None = None,
) -> DataReadinessResult:
    registry = registry or default_registry()
    blockers: list[str] = []
    limitations: list[str] = []
    material_blocked = False
    details: list[dict] = []
    for requirement in hypothesis.requirements:
        capability = registry.get(requirement.capability_id)
        if capability is None:
            status = CapabilityStatus.UNKNOWN
            details.append({"capabilityId": requirement.capability_id, "status": status.value})
            if requirement.critical:
                blockers.append(f"{requirement.capability_id}: UNKNOWN")
            continue
        status = capability.status.value
        details.append({"capabilityId": capability.capability_id, "status": status, "critical": requirement.critical, "policy": capability.limitation_policy.value})
        allowed = status in requirement.required_statuses
        if allowed:
            continue
        if requirement.critical and status in {CapabilityStatus.UNSUPPORTED.value, CapabilityStatus.UNKNOWN.value}:
            blockers.append(f"{capability.capability_id}: {status}")
        elif requirement.critical and _material(requirement):
            blockers.append(f"{capability.capability_id}: MATERIAL_{status}")
            material_blocked = True
        else:
            limitations.append(f"{capability.capability_id}: {status}")
    if blockers and material_blocked and not any(": UNSUPPORTED" in item or ": UNKNOWN" in item for item in blockers):
        readiness = Readiness.DATA_MATERIAL_LIMITATION
    elif blockers:
        readiness = Readiness.DATA_BLOCKED
    elif limitations:
        readiness = Readiness.DATA_READY_WITH_LIMITATIONS
    else:
        readiness = Readiness.DATA_READY
    return DataReadinessResult(hypothesis.hypothesis_id, hypothesis.name, readiness, tuple(blockers), tuple(limitations), tuple(details))


def backlog_hypotheses() -> list[Hypothesis]:
    supported = {CapabilityStatus.SUPPORTED.value, CapabilityStatus.SUPPORTED_WITH_LIMITATIONS.value}
    return [
        Hypothesis("sp500_index_deletion", "S&P 500 index deletion", (DataRequirement("sp500_membership", supported), DataRequirement("daily_prices", supported), DataRequirement("adjusted_prices", supported))),
        Hypothesis("microcap_momentum", "Sub-$300M cap cross-sectional momentum", (DataRequirement("market_cap_daily", supported), DataRequirement("pit_fundamentals", supported), DataRequirement("daily_prices", supported), DataRequirement("adjusted_prices", supported), DataRequirement("active_security_universe", supported))),
        Hypothesis("distressed_post_reorg", "Distressed / post-reorg equity", (DataRequirement("successor_security_linkage", supported, True), DataRequirement("terminal_delisting_economics", supported, True), DataRequirement("complex_reorganization_chain", supported, True))),
        Hypothesis("sp400_migrations", "S&P 400 migrations", (DataRequirement("sp400_membership", supported), DataRequirement("daily_prices", supported))),
        Hypothesis("sp600_migrations", "S&P 600 migrations", (DataRequirement("sp600_membership", supported), DataRequirement("daily_prices", supported))),
        Hypothesis("ordinary_delisted_momentum", "Momentum with ordinary delisted episodes excluded", (DataRequirement("daily_prices", supported), DataRequirement("delisted_price_history", supported, False, "Ambiguous terminal episodes are excluded"))),
        Hypothesis("ticker_change_momentum", "Momentum across ticker changes", (DataRequirement("ticker_history", supported), DataRequirement("permanent_identity", supported))),
    ]


def build_backlog_report() -> dict:
    registry = default_registry()
    results = [evaluate_data_readiness(hypothesis, registry) for hypothesis in backlog_hypotheses()]
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "evidenceOnly": True,
        "backtestsRun": False,
        "returnsInspected": False,
        "holdoutConsumed": False,
        "registry": [cap.to_dict() for cap in registry.values()],
        "hypotheses": [asdict(result) | {"readiness": result.readiness.value, "blockers": list(result.blockers), "limitations": list(result.limitations), "capabilities": list(result.capabilities)} for result in results],
    }


def write_backlog_report(report_dir: Path | None = None) -> tuple[Path, Path]:
    report_dir = report_dir or Path(__file__).resolve().parent.parent / "reports" / "data_readiness"
    report_dir.mkdir(parents=True, exist_ok=True)
    report = build_backlog_report()
    json_path = report_dir / "report.json"
    md_path = report_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    lines = [
        "# Capability registry and hypothesis data readiness", "",
        "> Capability-only diagnostic: no strategies, returns, MDA, or holdouts were inspected.", "",
        "## Capability registry", "",
        "| Capability | Status | Safe use | Unsafe use |", "|---|---|---|---|",
    ]
    for cap in report["registry"]:
        lines.append(f"| `{cap['capability_id']}` | **{cap['status']}** | {'; '.join(cap['safe_use_cases'])} | {'; '.join(cap['unsafe_use_cases'])} |")
    lines.extend(["", "## Backlog readiness", "", "| Hypothesis | Readiness | Blockers | Limitations |", "|---|---|---|---|"])
    for row in report["hypotheses"]:
        lines.append(f"| `{row['hypothesis_id']}` | **{row['readiness']}** | {'; '.join(row['blockers']) or '—'} | {'; '.join(row['limitations']) or '—'} |")
    lines.extend(["", "Adjusted prices retain the explicit carve-out: `reconstruct_adjustment.py` is canonical and vendor `closeadj` is cross-check only.", ""])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


if __name__ == "__main__":
    paths = write_backlog_report()
    print(json.dumps({"reports": [str(path) for path in paths]}, indent=2))
