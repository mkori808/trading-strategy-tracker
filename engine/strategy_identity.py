"""Read-only identity/fingerprint plumbing for paper strategy presentation.

This module does not decide what may trade.  It gives a persisted execution
configuration an honest display identity and verifies it against the exact
historical validation run from which execution already loads its parameters
and universe.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


CANONICAL_DM_FINGERPRINT = {
    "lookback_trading_days": 189,
    "top_n": 5,
    "rebalance_frequency": "monthly",
}


def _core(params: dict[str, Any]) -> dict[str, Any]:
    return {
        "lookback_trading_days": params.get("lookback_trading_days", 189),
        "top_n": params.get("top_n", 5),
        "rebalance_frequency": params.get("rebalance_frequency", "monthly"),
    }


def identify_execution_config(
    strategy_name: str,
    params: dict[str, Any],
    symbols: list[str],
    validation_run_id: int | None,
) -> dict[str, Any]:
    """Return display identity plus a fingerprint check against its source run."""
    actual = _core(params)
    if strategy_name != "Dual Momentum":
        return {
            "key": strategy_name,
            "displayName": strategy_name,
            "shortName": strategy_name,
            "variant": "registered",
            "provenance": "Registered strategy",
            "expectedConfig": actual,
            "actualConfig": actual,
            "fingerprintMatches": True,
            "fingerprintReason": None,
        }

    if actual == CANONICAL_DM_FINGERPRINT and validation_run_id is None:
        return {
            "key": "dm_canonical_189d_monthly",
            "displayName": "Canonical Dual Momentum · 189D/Monthly",
            "shortName": "Canonical DM 189D/Monthly",
            "variant": "canonical",
            "provenance": "Registered canonical strategy",
            "expectedConfig": CANONICAL_DM_FINGERPRINT,
            "actualConfig": actual,
            "fingerprintMatches": True,
            "fingerprintReason": None,
        }

    expected_params: dict[str, Any] | None = None
    expected_symbols: list[str] | None = None
    if validation_run_id is not None:
        try:
            from engine import logging_db

            selected, _ = logging_db.canonical_portfolio_validation(
                strategy_name, validation_run_id
            )
            if selected is not None:
                expected_params = json.loads(selected["params"] or "{}")
                expected_symbols = json.loads(selected["symbols"] or "[]")
        except Exception:  # status must degrade without affecting execution
            expected_params = None
            expected_symbols = None

    expected = _core(expected_params or params)
    params_match = actual == expected
    symbols_match = expected_symbols is None or symbols == expected_symbols
    fingerprint_matches = bool(expected_params is not None and params_match and symbols_match)
    lookback = actual["lookback_trading_days"]
    cadence = str(actual["rebalance_frequency"]).capitalize()
    return {
        "key": f"dm_optimized_{lookback}d_{str(actual['rebalance_frequency']).lower()}",
        "displayName": f"Dual Momentum · Optimized {lookback}D/{cadence}",
        "shortName": f"DM Optimized {lookback}D/{cadence}",
        "variant": "optimized",
        "provenance": "Historically optimized · requires OOS confirmation",
        "selectionContext": "Selected from config search",
        "selectionBiasNote": (
            "This configuration was selected after comparing multiple historical Dual Momentum "
            "configurations, so historical performance is development evidence rather than "
            "independent validation."
        ),
        "expectedConfig": expected,
        "actualConfig": actual,
        "expectedSymbolCount": len(expected_symbols) if expected_symbols is not None else None,
        "actualSymbolCount": len(symbols),
        "fingerprintMatches": fingerprint_matches,
        "fingerprintReason": (
            None
            if fingerprint_matches
            else "Persisted automation no longer matches its promoted validation-run fingerprint."
        ),
    }


def execution_fingerprint(
    strategy_name: str,
    params: dict[str, Any],
    symbols: list[str],
    validation_run_id: int | None,
) -> tuple[str, dict[str, Any]]:
    """Stable identity used by account ownership and prospective ledgers.

    Display names are deliberately excluded as authority; the hash binds the
    strategy ID to its promoted run, exact parameters, universe, and verified
    configuration identity.
    """
    identity = identify_execution_config(strategy_name, params, symbols, validation_run_id)
    payload = {
        "validationRunId": validation_run_id,
        "params": params,
        "symbols": symbols,
        "identity": identity,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), payload
