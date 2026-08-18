"""Convert observed broker fills into assumptions for future runs."""

from __future__ import annotations

from engine import execution_db


def effective_spread(symbol: str, baseline_decimal: float) -> float:
    evidence = execution_db.fill_calibration(symbol)
    calibrated_bps = evidence.get("medianAdverseSlippageBps")
    if not evidence["calibrated"] or calibrated_bps is None:
        return baseline_decimal
    return max(float(baseline_decimal), max(0.0, float(calibrated_bps)) / 10_000.0)


def spread_for(symbol: str, start, end) -> float:
    from engine import data as data_module

    return effective_spread(symbol, data_module.estimate_spread(symbol, start, end))


def spread_for_universe(symbol: str, start, end, universe_id: str | None = None) -> float:
    """Resolve execution cost without applying the equity estimator to crypto.

    Futures choices currently use listed ETF proxies and therefore retain the
    equity model. An actual futures-contract registry entry must declare its
    own non-equity model before it can pass registry validation.
    """
    if universe_id:
        from engine.universe_registry import registered_universe

        model = registered_universe(universe_id).cost_model
        if model.get("type") == "fixed_all_in_spread":
            return max(0.0, float(model.get("spreadBps", 0.0))) / 10_000.0
    return spread_for(symbol, start, end)


def snapshot(symbols: list[str], start, end) -> dict:
    from engine import data as data_module

    return {
        symbol: {
            "baselineSpreadBps": data_module.estimate_spread(symbol, start, end) * 10_000.0,
            **execution_db.fill_calibration(symbol),
            "effectiveSpreadBps": spread_for(symbol, start, end) * 10_000.0,
        }
        for symbol in sorted(symbols)
    }
