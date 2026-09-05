"""Freeze the sp500_pit_free bundle manifest and register it as a universe
(commit sp500_pit_bundle_v1) -- the final step, run only after membership,
symbol identity, price ingestion, and the coverage audit have all already
run.

Two things this deliberately does NOT do:

1. It does not touch ``data/universe_membership.json`` or any existing file
   under ``universes/`` (``dow_pit``, ``sp500_pit``, etc. stay byte-identical).
   ``universes/sp500_pit_free_v1.json`` is a new, separate file.

2. It does not register the universe as ``runnable: true``, regardless of
   what the coverage audit found. Reading ``engine/universe_registry.py:
   runnable_symbols()`` shows there is currently NO live code path that
   resolves ANY point-in-time ticker-interval ledger into a per-rebalance-
   date roster for the backtest engine to trade -- ``membershipLedgerPath``
   on a universe definition is descriptive metadata only; the function only
   ever branches on ``membershipMode == "dynamic_pit_security_master"``
   (a completely different loader, ``engine.pit_all_stocks``) or falls back
   to a static ``symbols`` list. This is already true of the existing
   ``sp500_pit`` universe (``symbols: []``, ``runnable: false``) -- it is not
   a defect specific to this dataset, but a real engine capability gap this
   commit cannot close as a side effect of registering one universe. Closing
   it (teaching ``engine/runner.py`` to resolve
   ``engine.universe_ledger.PointInTimeSchedule.membership_at(date)`` -- or
   an equivalent ticker-interval reader for this dataset's own schema -- per
   rebalance date) is real, separate engine work belonging to its own
   change, not something to rush through here.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data" / "sp500_pit_free"
MEMBERSHIP_AUDIT_PATH = DATA_DIR / "audits" / "membership_audit.json"
SYMBOL_MAP_AUDIT_PATH = DATA_DIR / "audits" / "symbol_map_audit.json"
PRICE_COVERAGE_PATH = DATA_DIR / "audits" / "price_coverage.json"
COVERAGE_AUDIT_PATH = DATA_DIR / "audits" / "coverage_audit.json"
INTERVALS_PATH = DATA_DIR / "membership" / "intervals.csv"
PRICES_DIR = DATA_DIR / "prices"
MANIFEST_PATH = DATA_DIR / "audits" / "dataset_manifest.json"
UNIVERSE_DEFINITION_PATH = ROOT / "universes" / "sp500_pit_free_v1.json"

PRICE_PROVIDER = "yfinance (auto_adjust=True daily bars)"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else f"missing:{path.name}"


def _sha256_price_bundle(prices_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(prices_dir.glob("*.parquet")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def build_manifest() -> dict:
    membership_audit = json.loads(MEMBERSHIP_AUDIT_PATH.read_text(encoding="utf-8"))
    symbol_map_audit = json.loads(SYMBOL_MAP_AUDIT_PATH.read_text(encoding="utf-8"))
    price_coverage = json.loads(PRICE_COVERAGE_PATH.read_text(encoding="utf-8"))
    coverage_audit = json.loads(COVERAGE_AUDIT_PATH.read_text(encoding="utf-8"))

    unresolved_tickers = sorted({
        *(
            symbol for symbol, row in price_coverage["symbols"].items()
            if row["status"] == "MISSING_DELISTED_PRICE_HISTORY"
        ),
        *symbol_map_audit["identityAmbiguousMultiTenureTickers"],
    })

    return {
        "datasetName": "sp500_pit_free_v1",
        "membershipSource1": {
            "role": "primary",
            **membership_audit["primarySource"],
        },
        "membershipSource2": {
            "role": "secondary (cross-check + post-primary-coverage single-source extension)",
            **membership_audit["secondarySource"],
        },
        "sharedAncestryCaveat": membership_audit["sharedAncestryCaveat"],
        "priceProvider": PRICE_PROVIDER,
        "buildDate": date.today().isoformat(),
        "usableStartDate": coverage_audit["usableWindow"].get("usableStartDate"),
        "usableEndDate": coverage_audit["usableWindow"].get("usableEndDate"),
        "usableWindowVerdict": coverage_audit["usableWindow"]["verdict"],
        "coverageThreshold": {
            "minCoveragePct": coverage_audit["minCoveragePct"],
            "minShareOfDatesClearingIt": coverage_audit["minShareOfDatesClearingIt"],
            "preregistration": coverage_audit["preregistration"],
        },
        "membershipHashSha256": _sha256_file(INTERVALS_PATH),
        "priceHashSha256": _sha256_price_bundle(PRICES_DIR),
        "unresolvedTickers": unresolved_tickers,
        "unresolvedTickerCount": len(unresolved_tickers),
        "priceStatusCounts": price_coverage["statusCounts"],
        "engineWiringStatus": (
            "NOT WIRED: no runner/universe_registry code path resolves this (or any) "
            "point-in-time ticker-interval ledger into a runnable per-rebalance-date "
            "roster yet -- see this module's docstring. runnable is therefore false "
            "regardless of coverage outcome."
        ),
    }


def build(
    *, manifest_path: Path = MANIFEST_PATH, universe_path: Path = UNIVERSE_DEFINITION_PATH,
) -> dict:
    manifest = build_manifest()
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps({"generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"), **manifest}, indent=2) + "\n",
        encoding="utf-8",
    )

    usable_note = (
        f"Coverage-audited usable window: {manifest['usableStartDate']} to "
        f"{manifest['usableEndDate']} ({manifest['usableWindowVerdict']})."
        if manifest["usableWindowVerdict"] == "USABLE_WINDOW_DETERMINED"
        else f"Coverage audit found no window clearing the preregistered threshold "
             f"({manifest['coverageThreshold']['minCoveragePct']}% coverage at "
             f"{manifest['coverageThreshold']['minShareOfDatesClearingIt']}% of dates)."
    )
    definition = {
        "id": "sp500_pit_free_v1",
        "label": "S&P 500 -- free PIT reconstruction v1 (research-only)",
        "category": "Research-only",
        "description": (
            "Survivorship-free S&P 500 reconstruction from two independent open "
            "GitHub membership trackers (qzzcl primary, leandroloi secondary "
            "cross-check), with free-provider (yfinance) daily prices. "
            f"{usable_note} See data/sp500_pit_free/audits/dataset_manifest.json "
            "for full provenance, hashes, and unresolved tickers. NOT the same "
            "dataset as sp500_pit (different membership sources, independently "
            "audited); dow_pit and sp500_pit are unmodified by this dataset."
        ),
        "assetClass": "equity",
        "symbols": [],
        "membershipLedgerPath": "data/sp500_pit_free/membership/intervals.csv",
        "membershipMode": "pit_ledger_required",
        "dataCoverage": {},
        "costModel": {
            "type": "equity_spread",
            "commissionBps": 0.0,
            "estimator": "engine.execution_calibration.spread_for",
            "note": "Per-symbol equity spreads; never valid for futures roll/slippage costs.",
        },
        "primaryBenchmark": "SPY",
        "equalWeightBenchmark": "self_equal_weight",
        "applicableGates": {
            "beats_equal_weight": {
                "applicable": True,
                "reason": "This is a multi-instrument equity basket.",
            },
            "beats_spy": {
                "applicable": True,
                "reason": "The registered primary benchmark is SPY for this US-equity universe.",
            },
            "pit_membership": {
                "applicable": True,
                "reason": (
                    "Membership replay and cross-source reconciliation are complete and "
                    "audited (data/sp500_pit_free/audits/membership_audit.json), but no "
                    "engine code path resolves this ledger into a runnable roster yet -- "
                    "the gate stays blocked on engine wiring, independent of data quality."
                ),
            },
        },
        "runnable": False,
        "selectable": False,
        "unavailableReason": (
            "Not wired into the backtest engine: engine/universe_registry.py's "
            "runnable_symbols() has no code path that resolves a point-in-time "
            "ticker-interval ledger (this dataset's schema, or engine/universe_ledger.py's "
            "date-effective-roster schema) into a tradeable per-rebalance-date universe -- "
            "true of the existing sp500_pit universe too, not a defect introduced by this "
            "dataset. " + usable_note
        ),
    }
    universe_path.parent.mkdir(parents=True, exist_ok=True)
    universe_path.write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    result = build()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
