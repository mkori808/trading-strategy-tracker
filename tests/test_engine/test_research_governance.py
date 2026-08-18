from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from engine import research_governance as governance
from engine.research_governance import (
    bootstrap_evidence,
    build_run_manifest,
    build_validation_spec,
    chronological_evidence,
    leakage_evidence,
    lifecycle_stage,
    spec_completeness,
)
from engine.universe_ledger import audit_membership
from strategies.params import param_field


@dataclass
class _GovernedStrategy:
    lookback: int = param_field(
        20, label="Lookback", minimum=10, maximum=30, step=5,
    )


def _spec():
    return build_validation_spec(
        "Governed", "standard", ["AAA", "BBB", "CCC", "DDD"], _GovernedStrategy,
    )


def _curves() -> tuple[pd.Series, pd.Series]:
    index = pd.bdate_range("2020-01-01", periods=320)
    strategy = pd.Series(10_000 * np.power(1.0010, np.arange(len(index))), index=index)
    benchmark = pd.Series(10_000 * np.power(1.0002, np.arange(len(index))), index=index)
    return strategy, benchmark


def test_validation_spec_is_complete_and_declares_neighbors() -> None:
    spec = _spec()
    complete, details = spec_completeness(spec)

    assert complete is True
    assert details["missingFields"] == []
    assert spec.parameter_neighborhoods["lookback"] == [15, 25]
    assert spec.holdout_fraction == 0.20
    assert spec.walk_forward_folds == 5


def test_governance_daily_equity_uses_observed_market_dates_only() -> None:
    index = pd.to_datetime([
        "2026-08-07 10:00", "2026-08-07 16:00",
        "2026-08-10 16:00", "2026-08-11 16:00",
    ])
    equity = pd.Series([100.0, 101.0, 103.0, 104.0], index=index)

    daily = governance._daily_equity(equity)

    assert list(daily.index) == [
        pd.Timestamp("2026-08-07"), pd.Timestamp("2026-08-10"), pd.Timestamp("2026-08-11"),
    ]


def test_chronological_holdout_and_bootstrap_are_deterministic() -> None:
    strategy, benchmark = _curves()
    spec = _spec()

    holdout_passed, holdout = chronological_evidence(
        strategy, date(2020, 1, 1), date(2022, 1, 1), spec,
        benchmark_equity=benchmark,
    )
    first = bootstrap_evidence(
        strategy, date(2020, 1, 1), date(2022, 1, 1), seed=123,
        benchmark_equity=benchmark, simulations=80,
    )
    second = bootstrap_evidence(
        strategy, date(2020, 1, 1), date(2022, 1, 1), seed=123,
        benchmark_equity=benchmark, simulations=80,
    )

    assert holdout_passed is True
    assert holdout["holdoutContributionPct"] > 0
    assert holdout["fractionPositiveWindows"] == 1.0
    assert first == second
    assert first[0] is True
    assert first[1]["contributionP05Pct"] > 0


def test_leakage_audit_rejects_impossible_trade_chronology() -> None:
    strategy, _ = _curves()
    trades = pd.DataFrame({
        "EntryTime": [pd.Timestamp("2024-02-02")],
        "ExitTime": [pd.Timestamp("2024-02-01")],
        "PnL": [1.0],
    })

    passed, details = leakage_evidence(strategy, trades, _spec())

    assert passed is False
    assert "one or more trades exit before entry" in details["issues"]


def test_complete_ledger_still_requires_the_run_to_apply_date_effective_membership(tmp_path) -> None:
    ledger = tmp_path / "membership.json"
    ledger.write_text(json.dumps({
        "version": 1,
        "universes": {
            "sample_index": [{
                "effectiveStart": "2020-01-01",
                "effectiveEnd": "2024-12-31",
                "symbols": ["AAA", "BBB", "OLD"],
                "unfetchableOrDelisted": [],
                "source": "licensed snapshot 1",
                "priceCoverageComplete": True,
            }],
        },
    }), encoding="utf-8")

    static = audit_membership(
        universe_key="sample_index", symbols=["AAA", "BBB"],
        start=date(2021, 1, 1), end=date(2024, 1, 1),
        membership_required=True, point_in_time_applied=False, path=ledger,
    )
    applied = audit_membership(
        universe_key="sample_index", symbols=["AAA", "BBB"],
        start=date(2021, 1, 1), end=date(2024, 1, 1),
        membership_required=True, point_in_time_applied=True, path=ledger,
    )

    assert static.passed is False
    assert static.details["historicalMembersExcludedFromStaticInput"] == ["OLD"]
    assert applied.passed is True


def test_missing_index_ledger_is_unresolved_but_fixed_list_is_valid(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    index_audit = audit_membership(
        universe_key="missing", symbols=["AAA"], start=date(2020, 1, 1),
        end=date(2021, 1, 1), membership_required=True, path=missing,
    )
    fixed_audit = audit_membership(
        universe_key=None, symbols=["AAA"], start=date(2020, 1, 1),
        end=date(2021, 1, 1), membership_required=False, path=missing,
    )

    assert index_audit.passed is None
    assert index_audit.details["survivorshipRisk"] is True
    assert fixed_audit.passed is True


def test_manifest_fingerprints_code_data_result_and_config(tmp_path, monkeypatch) -> None:
    strategy, _ = _curves()
    monkeypatch.setattr(governance.data_module, "DATA_DIR", tmp_path)
    (tmp_path / "AAA_1d.parquet").write_bytes(b"frozen-data")
    spec = build_validation_spec("Governed", "standard", ["AAA"], _GovernedStrategy)

    first = build_run_manifest(
        strategy_name="Governed", engine="standard", symbols=["AAA"], interval="1d",
        start=date(2020, 1, 1), end=date(2022, 1, 1), params={"lookback": 20},
        strategy_class=_GovernedStrategy, experiment_id=7, spec=spec, equity=strategy,
    )
    second = build_run_manifest(
        strategy_name="Governed", engine="standard", symbols=["AAA"], interval="1d",
        start=date(2020, 1, 1), end=date(2022, 1, 1), params={"lookback": 25},
        strategy_class=_GovernedStrategy, experiment_id=8, spec=spec, equity=strategy,
    )

    assert len(first["codeHashSha256"]) == 64
    assert len(first["dataHashSha256"]) == 64
    assert len(first["resultHashSha256"]) == 64
    assert first["runFingerprint"] != second["runFingerprint"]


def test_lifecycle_never_skips_evidence_stages() -> None:
    assert lifecycle_stage(
        is_preregistered=True, holdout_passed=False, forward_test_worthy=False,
        production_capital_worthy=False, signal_edge="Not established",
    ) == "preregistered"
    assert lifecycle_stage(
        is_preregistered=True, holdout_passed=True, forward_test_worthy=False,
        production_capital_worthy=False, signal_edge="Possible",
    ) == "holdout_passed"
    assert lifecycle_stage(
        is_preregistered=True, holdout_passed=True, forward_test_worthy=True,
        production_capital_worthy=False, signal_edge="Likely",
    ) == "paper_eligible"
    assert lifecycle_stage(
        is_preregistered=True, holdout_passed=True, forward_test_worthy=True,
        production_capital_worthy=True, signal_edge="Likely",
    ) == "production_eligible"
