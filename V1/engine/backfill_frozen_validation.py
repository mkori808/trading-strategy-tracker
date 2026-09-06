"""Attach completed frozen-neighbor evidence to canonical validation reports."""

from __future__ import annotations

import json
import math
from statistics import median
from typing import Any

from datetime import date

from engine import data_quality, logging_db
from engine.frozen_protocol import family_record, load_protocol
from engine.run_frozen_neighbors import IMPLEMENTATION_REVISIONS
from engine.validation import (
    ValidationCheck, ValidationDimension, _finalize,
)


CANONICAL_ROWS = (
    ("portfolio_runs", "52-Week-High Momentum"),
    ("portfolio_runs", "Market-Residual Momentum"),
    ("runs", "Negative Return + Volume Shock Reversal"),
    ("runs", "Volume-Shock Continuation (Long)"),
    ("runs", "Volume-Shock Continuation (Short)"),
    ("runs", "MAX Lottery-Return Reversal (Short)"),
    ("runs", "Volatility-Conditioned Pullback"),
)


def _protocol_name(display_name: str) -> str:
    if display_name.startswith("Volume-Shock Continuation"):
        return "Volume-Shock Continuation"
    if display_name.startswith("MAX Lottery-Return Reversal"):
        return "MAX Lottery-Return Reversal"
    return display_name


def _finite(values) -> list[float]:
    return [float(value) for value in values if value is not None and math.isfinite(float(value))]


def summarize_neighbors(
    display_name: str, *, canonical_supports: bool,
    canonical_sign: int | None,
) -> dict[str, Any]:
    protocol_name = _protocol_name(display_name)
    record = family_record(protocol_name)
    rows = [
        row for row in logging_db.frozen_neighbor_results(record["searchFamily"])
        if row["strategy_name"] == display_name
        and (
            protocol_name not in IMPLEMENTATION_REVISIONS
            or (
                json.loads(row["config_json"]).get("_implementationRevision")
                == IMPLEMENTATION_REVISIONS[protocol_name]
                and json.loads(row["config_json"]).get("_window")
                == load_protocol()["window"]
            )
        )
    ]
    expected = (
        int(record["materialConfigurationsPerSide"]) - 1
        if "materialConfigurationsPerSide" in record
        else int(record["materialConfigurations"]) - 1
    )
    completed = [row for row in rows if row["status"] == "completed"]
    supports = [bool(row["supports_hypothesis"]) for row in completed]
    benchmark = _finite(row["benchmark_excess_pct"] for row in completed)
    expectancy = _finite(row["expectancy_r"] for row in completed)
    same_sign_values = benchmark if not expectancy else expectancy
    same_sign = []
    if canonical_sign is not None:
        same_sign = [
            (1 if value > 0 else -1 if value < 0 else 0) == canonical_sign
            for value in same_sign_values
        ]
    support_fraction = sum(supports) / len(supports) if supports else 0.0
    complete = len(completed) == expected and len(rows) == expected
    ridge_pass = bool(complete and canonical_supports and support_fraction >= 0.60)
    return {
        "status": "pass" if ridge_pass else "fail",
        "value": support_fraction,
        "summary": (
            f"{sum(supports)}/{len(supports)} pre-registered neighbors retain positive "
            "benchmark-relative economics; canonical V1 remains unchanged"
        ),
        "details": {
            "searchFamily": record["searchFamily"],
            "expectedNeighbors": expected,
            "completedNeighbors": len(completed),
            "failedNeighbors": len(rows) - len(completed),
            "coverageComplete": complete,
            "canonicalSupportsHypothesis": canonical_supports,
            "positiveEconomicNeighbors": sum(supports),
            "positiveEconomicFraction": support_fraction,
            "sameSignFraction": (
                sum(same_sign) / len(same_sign) if same_sign else None
            ),
            "positiveExpectancyFraction": (
                sum(value > 0 for value in expectancy) / len(expectancy)
                if expectancy else None
            ),
            "benchmarkExcessMedianPct": median(benchmark) if benchmark else None,
            "benchmarkExcessMinimumPct": min(benchmark) if benchmark else None,
            "benchmarkExcessMaximumPct": max(benchmark) if benchmark else None,
            "zeroTradeNeighbors": sum((row["trades"] or 0) == 0 for row in completed)
            if expectancy else None,
            "passRule": (
                "all registered arms completed, canonical V1 supports the hypothesis, "
                "and at least 60% of neighbors have positive expectancy and matched-"
                "benchmark excess (ranking arms require positive PIT-EW contribution)"
            ),
            "selectionRule": (
                "Diagnostic only; no neighbor may replace canonical V1"
            ),
        },
    }


def _find_check(report: dict, key: str):
    for dimension in report.get("dimensions", []):
        for check in dimension.get("checks", []):
            if check.get("key") == key:
                yield dimension, check


def _canonical_economics(report: dict, row: Any, table: str) -> tuple[bool, int | None]:
    if table == "portfolio_runs":
        check = next((item for _, item in _find_check(report, "beats_equal_weight")), None)
        value = None if check is None else check.get("value")
        return bool(check and check.get("status") == "pass"), (
            None if value is None else 1 if float(value) > 0 else -1 if float(value) < 0 else 0
        )
    matched = next((item for _, item in _find_check(report, "beats_matched_spy")), None)
    matched_value = None if matched is None else matched.get("value")
    expectancy = row["expectancy_r"]
    supports = bool(
        matched and matched.get("status") == "pass"
        and expectancy is not None and float(expectancy) > 0
    )
    sign_value = expectancy if expectancy is not None else matched_value
    return supports, (
        None if sign_value is None else 1 if float(sign_value) > 0 else -1 if float(sign_value) < 0 else 0
    )


def _update_multiple_testing(check: dict, family: str, count: int) -> None:
    details = dict(check.get("details") or {})
    naive = details.get("naiveBootstrapP")
    if naive is None:
        naive = details.get("naiveEmpiricalP")
    if naive is None and isinstance(check.get("value"), (int, float)):
        # Canonical V1 was corrected at width one, so its old corrected value
        # is the disclosed naive p before the neighbor sweep widened the family.
        naive = float(check["value"])
    corrected = None if naive is None else min(1.0, float(naive) * count)
    details.update({
        "searchFamily": family,
        "familySearchCount": count,
        "actualConfigurationsEvaluated": count,
        "correctedP": corrected,
        "correction": "Bonferroni over every actually executed family configuration",
    })
    check["details"] = details
    check["value"] = corrected
    check["status"] = (
        "unresolved" if corrected is None else "pass" if corrected <= 0.05 else "fail"
    )
    check["summary"] = (
        f"Multiple-testing burden: {count} materially distinct configurations evaluated"
    )


def _to_dimensions(report: dict) -> list[ValidationDimension]:
    return [
        ValidationDimension(
            dimension["key"], dimension["label"],
            [ValidationCheck(**check) for check in dimension["checks"]],
        )
        for dimension in report["dimensions"]
    ]


def backfill() -> list[dict[str, Any]]:
    output = []
    conn = logging_db.get_connection()
    conn.row_factory = __import__("sqlite3").Row
    for table, display_name in CANONICAL_ROWS:
        row = conn.execute(
            f"SELECT * FROM {table} WHERE strategy_name=? AND is_canonical=1 "
            "ORDER BY id DESC LIMIT 1", (display_name,),
        ).fetchone()
        if row is None or not row["validation_json"]:
            raise RuntimeError(f"Canonical validation missing for {display_name}")
        report = json.loads(row["validation_json"])
        protocol = family_record(_protocol_name(display_name))
        family = protocol["searchFamily"]
        count = logging_db.family_search_count(family)
        canonical_supports, canonical_sign = _canonical_economics(report, row, table)
        ridge = summarize_neighbors(
            display_name, canonical_supports=canonical_supports,
            canonical_sign=canonical_sign,
        )
        ridge_checks = list(_find_check(report, "parameter_ridge"))
        if not ridge_checks:
            raise RuntimeError(f"Parameter-ridge gate missing for {display_name}")
        for _, check in ridge_checks:
            check.update({
                "label": "Pre-registered parameter ridge",
                "status": ridge["status"], "summary": ridge["summary"],
                "required": True, "value": ridge["value"],
                "details": ridge["details"],
            })
        for _, check in _find_check(report, "multiple_testing"):
            _update_multiple_testing(check, family, count)
        for _, check in _find_check(report, "data_quality"):
            if check.get("status") in {"unresolved", "warning"}:
                symbols = json.loads(row["symbols"] or "[]")
                quality = data_quality.audit_universe(
                    symbols, "1d", date.fromisoformat(row["start_date"]),
                    date.fromisoformat(row["end_date"]),
                ).to_dict()
                check.update({
                    "status": "pass" if quality["passed"] else "fail",
                    "summary": (
                        "Pre-run OHLCV audit passed for every represented symbol"
                        if quality["passed"] else
                        "Pre-run OHLCV audit found critical data-integrity issues"
                    ),
                    "required": True,
                    "details": quality,
                })
        research = dict(report.get("research") or {})
        if table == "portfolio_runs" and not research.get("canonicalPortfolioMetrics"):
            measurement = next(
                (item for _, item in _find_check(report, "measurement_integrity")), {}
            ).get("details", {})
            ew = next(
                (item for _, item in _find_check(report, "beats_equal_weight")), {}
            )
            research["canonicalPortfolioMetrics"] = {
                "returnPct": row["return_pct"], "cagrPct": row["cagr_pct"],
                "sharpe": row["sharpe"], "sortino": row["sortino"],
                "maxDrawdownPct": row["max_drawdown_pct"],
                "rebalances": next(
                    (item for _, item in _find_check(report, "sample_coverage")), {}
                ).get("value"),
                "averageGrossExposurePct": None,
                "turnoverPct": measurement.get("turnoverPct"),
                "modeledCosts": measurement.get("modeledCosts"),
                "pitEqualWeightExcessPct": ew.get("value"),
            }
        research.update({
            "searchFamily": family,
            "familySearchCount": count,
            "multipleTestingBurden": (
                f"{count} materially distinct configurations evaluated"
            ),
            "frozenNeighborEvidence": ridge["details"],
        })
        manifest = research.get("manifest") or {}
        if manifest.get("validationSpec"):
            manifest["validationSpec"]["searchFamily"] = family
        research["manifest"] = manifest
        updated = _finalize(_to_dimensions(report), research).to_dict()
        logging_db.attach_validation(table, int(row["id"]), updated)
        output.append({
            "strategy": display_name, "runId": int(row["id"]),
            "familySearchCount": count, "ridgeStatus": ridge["status"],
            "positiveNeighborFraction": ridge["value"],
            "headline": updated["verdict"]["headline"],
        })
    conn.close()
    return output


def main() -> None:
    print(json.dumps(backfill(), indent=2))


if __name__ == "__main__":
    main()
