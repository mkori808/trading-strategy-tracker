"""Task-3 power test: re-run the FROZEN Dual Momentum config (189-day
lookback, monthly rebalance, top-5, unchanged from FROZEN_DUAL_MOMENTUM.md)
over 2000-01-01 -> today using the extended point-in-time Dow ledger
(data/universe_membership.json), instead of the 2021-08-14-start /
~5-year window the canonical config runs by default.

This is a POWER test, not a search: no strategy parameters are touched here
and no parameter sweep is run. The only thing that changes from the frozen
config is the DATE WINDOW, via an explicit RunRequest override -- exactly
the mechanism the Lab tab uses for any one-off experiment, logged as
non-canonical (see engine/runner.py:RunRequest.is_default()).

Mirrors engine/runner.py:run_cross_sectional()'s cross-sectional branch
directly rather than calling it, for one reason: resolve_schedule() must be
called with require_complete=False here (see engine/universe_ledger.py's
docstring) to activate point-in-time membership at all, given the ledger
honestly documents unfetchable names (EK, GM, SBC, UTX, KFT, DWDP, WBA --
see data/universe_membership.json). The production API path intentionally
keeps its own resolve_schedule() call strict (require_complete defaults to
True there), so canonical/live runs are completely unaffected by this
script existing -- this file is the only caller of the relaxed mode.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from engine import data as data_module
from engine.advanced_validation import factor_attribution_evidence, statistical_power_evidence
from engine.cross_sectional import run_cross_sectional_backtest
from engine.execution_calibration import spread_for_universe
from engine.metrics import plausible_return_bounds
from engine.research_governance import MINIMUM_TRADABLE_ALPHA_PCT
from engine.universe import EQUITY_UNIVERSE
from engine.universe_ledger import resolve_schedule
from strategies.registry import build_cross_sectional_strategy

ALPACA_COMMISSION_BPS = 0.0
STRATEGY_NAME = "Dual Momentum"

# ---------------------------------------------------------------------------
# PRE-REGISTRATION -- written before the run executes, per the task's
# explicit constraint. Do not edit after the run below has produced a result.
# ---------------------------------------------------------------------------
PRE_REGISTERED_PREDICTION = {
    "recordedAt": "2026-08-13",
    "priorMdaPct": 11.15,  # 5-year window, decision-frequency-corrected (FROZEN_DUAL_MOMENTUM.md, 2026-08-12 amendment)
    "predictedMdaRangePct": [5.0, 7.0],
    "reasoning": (
        "MDA scales roughly with 1/sqrt(T) for a fixed decision-frequency "
        "design (same 5 positions, same monthly cadence, same ~29-name "
        "correlated large-cap universe -- breadth is unchanged, only history "
        "length is). 5y -> ~25y is a ~5x increase in decisions (60 -> ~300 "
        "monthly rebalances), so MDA should fall by roughly sqrt(5) ~= 2.2x: "
        "11.15%/yr / 2.2 ~= 5.1%/yr, landing inside the pre-registered "
        "5-7%/yr range."
    ),
    "decisionRule": (
        "If MDA lands in 5-7%/yr: the design still fails the 2%/yr gate "
        "(MINIMUM_TRADABLE_ALPHA_PCT) and the conclusion is that monthly "
        "rebalancing on ~30 correlated large caps cannot clear it at any "
        "realistic history length -- breadth, not sample size, is the "
        "binding constraint (consistent with engine/power_curve.py's earlier "
        "finding). If MDA clears 2%/yr: the returns become interpretable for "
        "the first time and are worth reading."
    ),
}


def run() -> dict:
    start = date(2000, 1, 1)
    end = date.today()

    schedule = resolve_schedule(
        "dow_jones_industrial_average", start, end, require_complete=False,
    )
    if schedule is None:
        raise RuntimeError(
            "resolve_schedule returned None even with require_complete=False -- "
            "the ledger has a real defect (gap, missing source, or malformed "
            "record) beyond the disclosed unresolved prices. Fix the ledger, "
            "don't loosen this further."
        )
    symbols = schedule.symbols

    rf = data_module.risk_free_rate(start, end)
    strategy = build_cross_sectional_strategy(STRATEGY_NAME, risk_free_rate=rf)
    rebalance_frequency = getattr(strategy, "rebalance_frequency", "monthly")

    spread_by_symbol = {
        s: spread_for_universe(s, start, end, None) for s in symbols
    }

    result = run_cross_sectional_backtest(
        STRATEGY_NAME, strategy, symbols, start, end, risk_free_rate=rf,
        rebalance_frequency=rebalance_frequency,
        spread_by_symbol=spread_by_symbol,
        commission_bps=ALPACA_COMMISSION_BPS,
        membership_at=schedule.membership_at,
        universe_key="dow_jones_industrial_average",
        allow_incomplete_warmup=False,
    )

    years = (end - start).days / 365.25
    lo, hi = plausible_return_bounds(years)
    if not (lo <= result.return_pct <= hi):
        raise RuntimeError(
            f"Result outside plausible cumulative-return bounds ({lo:.0f}%, "
            f"{hi:.0f}%) for a {years:.1f}-year window: {result.return_pct:.1f}%. "
            "This is the exact guard added for Task 1 -- refusing to report a "
            "number it would also refuse to let the app log."
        )

    # MDA first, before returns -- same decision-frequency-corrected
    # methodology as every other MDA figure in FROZEN_DUAL_MOMENTUM.md
    # (engine/advanced_validation.py, fixed 2026-08-12 for exactly this
    # strategy). Monthly rebalance -> decisions_per_year=12, matching the
    # cadence map engine/validation.py's _generic_research_dimensions uses.
    decisions_per_year = 12.0
    factor_pass, factor_details = factor_attribution_evidence(
        result.equity_curve, start, end, decisions_per_year=decisions_per_year,
    )
    power_pass, power_details = statistical_power_evidence(
        result.equity_curve,
        minimum_tradable_alpha_pct=MINIMUM_TRADABLE_ALPHA_PCT,
        factor_details=factor_details,
        assumed_pairwise_correlation=0.5,
        decisions_per_year=decisions_per_year,
    )

    return {
        "result": result,
        "years": years,
        "startingRoster": sorted(schedule.membership_at(start)),
        "totalDistinctSymbols": len(symbols),
        "factorDetails": factor_details,
        "powerDetails": power_details,
    }


if __name__ == "__main__":
    print("PRE-REGISTERED PREDICTION (recorded before this run executes):")
    print(json.dumps(PRE_REGISTERED_PREDICTION, indent=2))
    print()
    print(f"Running frozen Dual Momentum (189-day lookback, monthly, top-5) "
          f"over 2000-01-01 -> {date.today().isoformat()} against the "
          f"point-in-time Dow ledger...")
    print()
    outcome = run()
    result = outcome["result"]
    print(f"years measured: {outcome['years']:.2f}")
    print(f"starting (2000-01-01) roster ({len(outcome['startingRoster'])}): "
          f"{outcome['startingRoster']}")
    print(f"distinct symbols ever eligible: {outcome['totalDistinctSymbols']}")

    print()
    print("=" * 70)
    print("MDA -- reported BEFORE returns, per the pre-registered decision rule")
    print("=" * 70)
    print(json.dumps(outcome["powerDetails"], indent=2, default=str))
    mda = outcome["powerDetails"].get("selectedMdaPct")
    print()
    if mda is not None:
        lo, hi = PRE_REGISTERED_PREDICTION["predictedMdaRangePct"]
        in_range = lo <= mda <= hi
        print(f"MDA = {mda:.2f}%/yr  (pre-registered prediction: {lo}-{hi}%/yr, "
              f"{'CONFIRMED within range' if in_range else 'OUTSIDE the predicted range'})")
        print(f"Clears the {MINIMUM_TRADABLE_ALPHA_PCT}%/yr actionable-alpha gate: "
              f"{mda <= MINIMUM_TRADABLE_ALPHA_PCT}")
    else:
        print("MDA could not be computed -- see powerDetails reason above.")

    print()
    print("=" * 70)
    print("Factor regression (context for the MDA above)")
    print("=" * 70)
    print(json.dumps(outcome["factorDetails"], indent=2, default=str))

    print()
    print("=" * 70)
    print("Returns -- reported AFTER MDA")
    print("=" * 70)
    print(f"return_pct: {result.return_pct:.2f}%")
    print(f"cagr_pct: {result.cagr_pct}")
    print(f"sharpe: {result.sharpe}")
    print(f"sortino: {result.sortino}")
    print(f"rebalances: {len(result.rebalances)}")
    print(f"incomplete_warmup: {result.incomplete_warmup}")
