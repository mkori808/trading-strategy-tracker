from __future__ import annotations

import json

import pytest

from engine.execution_calibration import spread_for_universe
from engine.universe_registry import gate_applicability, registered_universe, universe_registry


def test_required_universes_are_registered_with_per_symbol_coverage() -> None:
    registry = universe_registry()
    assert {
        "dow_pit", "sp500_pit", "sp400_pit", "sp600_pit",
        "sp500_proxy", "crypto_majors", "futures_market_proxies",
        "international_markets", "sp500_current", "sp400_current", "sp600_current",
        "us_all_stocks_pit",
    } <= set(registry)
    for definition in registry.values():
        if definition.symbols:
            assert set(definition.data_coverage) == set(definition.symbols)
            assert all(row["coverageStart"] for row in definition.data_coverage.values())


def test_cross_asset_dropdown_universes_have_honest_cost_and_gate_metadata() -> None:
    crypto = registered_universe("crypto_majors")
    futures = registered_universe("futures_market_proxies")
    international = registered_universe("international_markets")

    assert crypto.asset_class == "crypto"
    assert crypto.cost_model["type"] == "fixed_all_in_spread"
    assert crypto.applicable_gates["beats_spy"]["applicable"] is False
    assert "proxy" in futures.description.lower()
    assert futures.cost_model["type"] == "equity_spread"
    assert international.primary_benchmark == "EFA"


def test_crypto_cost_does_not_call_equity_spread_estimator(monkeypatch) -> None:
    monkeypatch.setattr(
        "engine.execution_calibration.spread_for",
        lambda *_args, **_kwargs: pytest.fail("equity spread estimator must not run"),
    )
    assert spread_for_universe(
        "BTC-USD", "2021-01-01", "2022-01-01", "crypto_majors",
    ) == pytest.approx(0.0015)


def test_unavailable_pit_ledger_is_not_silently_runnable() -> None:
    definition = registered_universe("sp500_pit")
    assert definition.runnable is False
    assert definition.membership_ledger_path
    assert "delisted-price" in (definition.unavailable_reason or "")


def test_all_stocks_pit_is_visible_but_fail_closed_without_licensed_bundle() -> None:
    definition = registered_universe("us_all_stocks_pit")

    assert definition.selectable is True
    assert definition.runnable is False
    assert definition.membership_mode == "dynamic_pit_security_master"
    assert definition.pit_status is not None
    assert definition.pit_status["ready"] is False
    assert "manifest.json" in definition.pit_status["missingArtifacts"]


def test_inapplicable_gate_has_a_reason(tmp_path, monkeypatch) -> None:
    payload = registered_universe("dow_pit").to_dict()
    payload["id"] = "single"
    payload["assetClass"] = "single-instrument"
    payload["symbols"] = ["SPY"]
    payload["dataCoverage"] = {"SPY": payload["dataCoverage"]["AAPL"]}
    payload["applicableGates"] = {
        "beats_equal_weight": {"applicable": False, "reason": "A single instrument has no equal-weight basket."}
    }
    (tmp_path / "single.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr("engine.universe_registry.REGISTRY_DIR", tmp_path)

    applicable, reason = gate_applicability("single", "beats_equal_weight")
    assert applicable is False
    assert reason == "A single instrument has no equal-weight basket."
