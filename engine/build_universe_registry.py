"""Generate registered universe JSON from frozen lists and local coverage.

This script changes metadata only; it does not run or inspect strategy results.
All date/return inputs pass engine.sanity floors before being recorded.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from engine.sanity import check_window
from engine.universe import EQUITY_UNIVERSE, MIDCAP_UNIVERSE, SMALL_CAP_UNIVERSE


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "universes"


def coverage(symbols: list[str]) -> dict:
    rows = {}
    for symbol in symbols:
        path = ROOT / "data" / f"{symbol}_1d.parquet"
        bars = pd.read_parquet(path)
        if bars.empty:
            raise RuntimeError(f"{symbol}: cached daily data is empty")
        start, end = bars.index[0].date(), bars.index[-1].date()
        check_window(bars, start, end, label=f"universe coverage {symbol}")
        rows[symbol] = {
            "dataSource": "local adjusted daily cache (provider recorded in run manifest)",
            "coverageStart": start.isoformat(),
            "coverageEnd": end.isoformat(),
        }
    return rows


def gates(*, pit_reason: str | None = None) -> dict:
    return {
        "pit_membership": {
            "applicable": True,
            "reason": pit_reason or "Historical index membership changes the eligible basket.",
        },
        "beats_equal_weight": {
            "applicable": True,
            "reason": "This is a multi-instrument equity basket.",
        },
        "beats_spy": {
            "applicable": True,
            "reason": "The registered primary benchmark is SPY for this US-equity universe.",
        },
    }


def definition(
    universe_id: str, label: str, category: str, description: str,
    symbols: list[str], mode: str, *, runnable: bool, reason: str,
    selectable: bool = True,
) -> dict:
    return {
        "id": universe_id,
        "label": label,
        "category": category,
        "description": description,
        "assetClass": "equity",
        "symbols": symbols,
        "membershipLedgerPath": None,
        "membershipMode": mode,
        "dataCoverage": coverage(symbols),
        "costModel": {
            "type": "equity_spread",
            "estimator": "engine.execution_calibration.spread_for",
            "commissionBps": 0.0,
            "note": "Per-symbol equity spreads; never valid for futures roll/slippage costs.",
        },
        "primaryBenchmark": "SPY",
        "equalWeightBenchmark": "self_equal_weight",
        "applicableGates": gates(pit_reason=reason),
        "runnable": runnable,
        "selectable": selectable,
        "unavailableReason": None if runnable else reason,
    }


def main() -> int:
    OUT.mkdir(exist_ok=True)
    entries = [
        definition(
            "dow_pit", "Dow 30", "US markets",
            "Dow large-cap stock universe. Uses the existing 29-symbol executable roster.",
            EQUITY_UNIVERSE,
            "partial_reconstruction_static_execution_roster", runnable=True,
            reason="The prior reconstruction is evidence, but the executable ledger and four delisted-price histories are incomplete.",
        ),
        {
            **definition(
                "sp500_pit", "S&P 500 — PIT data required", "Research-only",
                "Reserved for a future licensed S&P 500 point-in-time constituent ledger.",
                [], "pit_ledger_required",
                runnable=False,
                reason="Licensed historical constituents and delisted-price coverage are not installed; running a current roster would be survivorship-biased.",
                selectable=False,
            ),
            "symbols": [],
            "membershipLedgerPath": "data/universe_ledgers/sp500_pit.json",
            "dataCoverage": {},
        },
        definition(
            "sp400_pit", "S&P MidCap 400 sample", "S&P indexes",
            "Mechanical 27-stock S&P MidCap 400 sample; point-in-time membership remains unresolved.",
            MIDCAP_UNIVERSE,
            "mechanical_current_constituent_sample", runnable=True,
            reason="The registered symbols are a mechanical current-constituent sample, not a complete PIT ledger; PIT remains unresolved.",
            selectable=False,
        ),
        definition(
            "sp600_pit", "S&P SmallCap 600 sample", "S&P indexes",
            "Mechanical 26-stock S&P SmallCap 600 sample; point-in-time membership remains unresolved.",
            SMALL_CAP_UNIVERSE,
            "mechanical_current_constituent_sample", runnable=True,
            reason="The registered symbols are a truncated mechanical current-constituent sample, not a complete PIT ledger; PIT remains unresolved.",
            selectable=False,
        ),
    ]
    for item in entries:
        (OUT / f"{item['id']}.json").write_text(
            json.dumps(item, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
