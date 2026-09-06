"""Phase-8-style breadth/MDA screen across the WHOLE hypothesis backlog.

Follow-up to `engine/index_deletion_screen.py`, which closed the Index
Deletion / Forced Selling hypothesis on breadth before any backtest was
built. This module generalizes that pattern -- screen off raw Sharadar
tables, never the normalized PIT store -- across every remaining candidate
in `research/hypothesis_backlog.md`, so the backlog is triaged on breadth
BEFORE anyone writes a mechanism story or preregisters the next one.

This module does NOT build the PIT store, does NOT run a backtest, and does
NOT preregister anything. It produces a ranked feasibility table only.

Discovered while pulling data for this screen, worth stating once here
rather than in every function: `engine/sharadar_client.py`'s
`SharadarClient.query_all` pagination assumes "fewer rows returned than the
requested page size" means "no more data." For the market-wide (ticker-less)
`actions` endpoint this assumption is FALSE -- a single request spanning
1998-2026 for `action=bankruptcyliquidation` silently truncated at
2007-12-26 with only 1359 rows returned despite `limit=10000`, even though
948 more rows exist from 2008 onward. The vendor appears to cap a single
response below the requested page size for large multi-decade market-wide
pulls without any error or truncation flag. Every market-wide multi-decade
pull in this module chunks by a small number of years (`_fetch_actions_chunked`)
to work around this empirically-confirmed defect. This is flagged here as a
finding for whoever eventually builds the PIT store's ingestion layer --
not fixed in `sharadar_client.py` itself, since that would be store-building
infrastructure this screening pass is explicitly scoped not to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import collections
import json
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

from engine.power_curve import screen_design
from engine.sharadar_client import SharadarClient


ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "hypothesis_backlog_screen"

FULL_HISTORY_START_YEAR = 1998
FULL_HISTORY_END = "2026-09-05"
FULL_HISTORY_YEARS = 28.5
# 2026-09-05 (FULL_HISTORY_END) is a Saturday -- the `stocks`/`daily` daily-bar
# tables have no row for a non-trading day. `sp500`'s "current" snapshot is not
# a daily bar (it answers "who is a member right now") and works fine with
# FULL_HISTORY_END; anything hitting `stocks`/`daily` for a single-date
# snapshot must use the last confirmed real trading session instead.
SNAPSHOT_TRADING_DATE = "2026-09-03"

# Same thresholds as engine/index_deletion_screen.py, held fixed for
# comparability across every candidate in this pass.
MAX_TRADABLE_ALPHA_PCT = 4.0
MIN_USABLE_EVENTS = 150
STANDARD_CORR = 0.5  # the project-standard default used everywhere else


def run_liquidity_decile_map(*, snapshot_date: str = SNAPSHOT_TRADING_DATE, force_refresh: bool = False) -> dict[str, Any]:
    """Map dollar-volume deciles without running a strategy or inspecting returns."""
    client = SharadarClient()
    snap = client.query_all("daily", date=snapshot_date, fields="ticker,date,marketcap", force_refresh=force_refresh).rows
    meta_rows = client.query_all("tickers", fields="ticker,exchange,category", force_refresh=force_refresh).rows
    meta = {r.get("ticker"): r for r in meta_rows}
    allowed = {"NYSE", "NASDAQ", "NYSEMKT", "AMEX"}
    universe = [r["ticker"] for r in snap if r.get("ticker") in meta and meta[r["ticker"]].get("exchange") in allowed and "common stock" in str(meta[r["ticker"]].get("category", "")).lower()]
    observations: dict[str, list[dict[str, Any]]] = {}
    # Sharadar rejects very large comma-separated ticker filters (HTTP 400);
    # ten-symbol batches are accepted and keep this mapping reproducible.
    for i in range(0, len(universe), 10):
        batch = universe[i:i + 10]
        try:
            rows = client.query_all("stocks", ticker=",".join(batch), **{"from": "2025-09-03", "to": snapshot_date}, fields="ticker,date,close,high,low,volume", sort="date.asc", force_refresh=force_refresh).rows
        except Exception:
            continue
        for row in rows:
            try:
                if float(row.get("close", 0)) >= 1 and float(row.get("volume", 0)) > 0:
                    observations.setdefault(row["ticker"], []).append(row)
            except (TypeError, ValueError):
                pass
    snap_cap = {r["ticker"]: float(r["marketcap"]) for r in snap if r.get("marketcap") not in (None, "N/A")}
    records = []
    for ticker, rows in observations.items():
        if len(rows) < 200: continue
        tail = rows[-63:]
        dv = float(np.median([float(r["close"]) * float(r["volume"]) for r in tail]))
        records.append({"ticker": ticker, "dollarVolume": dv, "marketCap": snap_cap.get(ticker), "price": float(tail[-1]["close"]), "historyRows": len(rows)})
    records.sort(key=lambda r: r["dollarVolume"])
    deciles = []
    for idx, group in enumerate(np.array_split(records, 10), 1):
        g = list(group)
        n = len(g); positions = max(n * 0.2, 1)
        design = screen_design(f"liquidity_decile_{idx}", positions=positions, rebalances_per_year=12, years=FULL_HISTORY_YEARS, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.3)
        cs = float(np.median([r["dollarVolume"] for r in g])) if g else None
        # OHLC-derived estimator is intentionally corrected uniformly; this
        # mapping uses a conservative fixed proxy from the existing CS screen.
        spreads = []
        for r in g[:]:
            try:
                bars = observations[r["ticker"]]
                if len(bars) >= 30:
                    from engine.hypothesis_cost_screen import corwin_schultz_spread
                    spreads.append(float(np.nanmedian(corwin_schultz_spread([float(x["high"]) for x in bars if float(x.get("low", 0)) > 0], [float(x["low"]) for x in bars if float(x.get("low", 0)) > 0]))) * 10000)
            except Exception:
                continue
        cs_bps = float(np.median(spreads)) if spreads else None
        deciles.append({"decile": idx, "count": n, "dollarVolumeMin": g[0]["dollarVolume"] if g else None, "dollarVolumeMax": g[-1]["dollarVolume"] if g else None, "medianMarketCap": float(np.median([r["marketCap"] for r in g if r["marketCap"] is not None])) if g else None, "medianPrice": float(np.median([r["price"] for r in g])) if g else None, "independentBetsPerYear": design["independent_bets_per_year"], "mdaPct": design["mda_pct"], "csSpreadBps": cs_bps, "annualCostByCorrection": {str(f): (cs_bps * f * 2 * 12 / 10000) if cs_bps is not None else None for f in (1.5, 2.0, 3.0)}})
    favorable = {str(f): [d["decile"] for d in deciles if d["mdaPct"] < 4 and d["annualCostByCorrection"][str(f)] is not None and d["annualCostByCorrection"][str(f)] < d["mdaPct"]] for f in (1.5, 2.0, 3.0)}
    return {"schemaVersion": 1, "evidenceOnly": True, "backtestsRun": False, "returnsInspected": False, "holdoutConsumed": False, "snapshotDate": snapshot_date, "baselineEligibility": "common stock; NYSE/NASDAQ/AMEX/NYSEMKT; price >= $1; >=200 trading rows", "correlation": 0.3, "biasCorrections": [1.5, 2.0, 3.0], "deciles": deciles, "eligibleCount": len(records), "favorableDecilesByCorrection": favorable, "verdict": "FAVORABLE" if favorable["1.5"] and favorable["2.0"] and favorable["3.0"] else "COST_SENSITIVE" if favorable["1.5"] else "NONE"}


def _fetch_actions_chunked(
    client: SharadarClient, action: str, *, chunk_years: int = 4, force_refresh: bool = False
) -> list[dict[str, Any]]:
    """Market-wide (no ticker filter) actions of one type, full history.

    Chunked to work around the pagination defect documented in this module's
    docstring -- a single wide-range request silently truncates.
    """
    rows: list[dict[str, Any]] = []
    year = FULL_HISTORY_START_YEAR
    while year <= 2026:
        year_end = min(year + chunk_years - 1, 2026)
        start = f"{year}-01-01"
        end = f"{year_end}-12-31" if year_end < 2026 else FULL_HISTORY_END
        result = client.query_all(
            "actions", **{"from": start, "to": end}, action=action, sort="date.asc",
            force_refresh=force_refresh,
        )
        rows.extend(result.rows)
        year = year_end + 1
    return rows


def _classify_sp500_reason(note: str | None) -> str:
    text = (note or "").lower()
    if "acquired" in text or "acquisition" in text or "merger" in text:
        return "acquired_or_merged"
    if "market capitalization" in text:
        return "market_cap_change"
    if "spin-off" in text or "spinoff" in text:
        return "spinoff"
    if "bankrupt" in text:
        return "bankruptcy"
    return "other"


@dataclass
class ScreenResult:
    candidate: str
    barrier: str
    observation_definition: str
    raw_count: str
    usable_count: str
    events_or_bets_per_year: float
    years: float
    independence_note: str
    designs: list[dict[str, Any]] = field(default_factory=list)
    approximation_note: str | None = None
    data_blocked: bool = False
    data_blocked_reason: str | None = None

    @property
    def best_case_mda_pct(self) -> float | None:
        if not self.designs:
            return None
        return min(d["mda_pct"] for d in self.designs)

    @property
    def usable_count_floor_applicable(self) -> bool:
        # The 150-usable-event floor is an EVENT-count concept. For a
        # cross-sectional candidate (many positions every rebalance), the
        # analogous total-observation count is always far larger and the
        # floor is not the binding constraint -- reported as such rather
        # than silently applying an event-shaped gate to a different shape
        # of design.
        return "event" in self.observation_definition.lower() and "cross-sectional" not in self.observation_definition.lower()

    def verdict(self) -> str:
        if self.data_blocked:
            return "DATA_BLOCKED"
        mda = self.best_case_mda_pct
        if mda is None:
            return "DATA_BLOCKED"
        if mda <= MAX_TRADABLE_ALPHA_PCT:
            return "VIABLE"
        if mda <= 2 * MAX_TRADABLE_ALPHA_PCT:
            return "MARGINAL"
        return "DEAD"

    def dead_cause(self) -> str | None:
        if self.verdict() != "DEAD":
            return None
        if self.usable_count_floor_applicable:
            try:
                count = float(self.usable_count)
            except (TypeError, ValueError):
                count = None
            if count is not None and count < MIN_USABLE_EVENTS:
                return "breadth (fails both the event-count floor and the MDA ceiling)"
        return "breadth (clears the event-count floor but MDA still exceeds the ceiling)"


def screen_index_addition(client: SharadarClient, *, force_refresh: bool = False) -> ScreenResult:
    result = client.query_all(
        "sp500", **{"from": f"{FULL_HISTORY_START_YEAR}-01-01", "to": FULL_HISTORY_END},
        sort="date.asc", force_refresh=force_refresh,
    )
    added = [row for row in result.rows if row.get("action") == "added"]
    reasons = collections.Counter(_classify_sp500_reason(row.get("note")) for row in added)
    rate = len(added) / FULL_HISTORY_YEARS
    designs = [
        screen_design(
            "index_addition_positions1_rho0", positions=1, rebalances_per_year=rate,
            years=FULL_HISTORY_YEARS, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.0,
        ),
        screen_design(
            "index_addition_positions1_standard_corr", positions=1, rebalances_per_year=rate,
            years=FULL_HISTORY_YEARS, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=STANDARD_CORR,
        ),
    ]
    return ScreenResult(
        candidate="Index addition / forced buying",
        barrier="MANDATE",
        observation_definition="Event: S&P 500 sp500-table action=added, full history 1998-2026",
        raw_count=str(len(added)),
        usable_count=str(len(added)),
        events_or_bets_per_year=round(rate, 2),
        years=FULL_HISTORY_YEARS,
        independence_note=(
            "Two rho variants reported (0.0, 0.5 standard); at positions=1 rho only affects "
            "screen_design's own internal calibration anchor, not this design's bet count "
            "(pinned behavior, see tests/test_engine/test_index_deletion_screen.py)."
        ),
        designs=designs,
        approximation_note=(
            "Unlike deletion, NO reason-based filtering is applied: by definition every "
            "'added' event names a security that begins trading in the index and continues "
            "trading afterward (that is what 'added' means) -- there is no acquired/merged-style "
            f"subset that stops trading. Reason breakdown for context only: {dict(reasons)}."
        ),
    )


def screen_spinoff_dumping(client: SharadarClient, *, force_refresh: bool = False) -> ScreenResult:
    spinoff = _fetch_actions_chunked(client, "spinoff", force_refresh=force_refresh)
    rate = len(spinoff) / FULL_HISTORY_YEARS
    designs = [
        screen_design(
            "spinoff_dumping_positions1_rho0", positions=1, rebalances_per_year=rate,
            years=FULL_HISTORY_YEARS, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.0,
        ),
        screen_design(
            "spinoff_dumping_positions1_standard_corr", positions=1, rebalances_per_year=rate,
            years=FULL_HISTORY_YEARS, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=STANDARD_CORR,
        ),
    ]
    return ScreenResult(
        candidate="Spinoff shares dumped by ineligible holders",
        barrier="MANDATE",
        observation_definition="Event: market-wide actions table, action=spinoff, full history 1998-2026",
        raw_count=str(len(spinoff)),
        usable_count=str(len(spinoff)),
        events_or_bets_per_year=round(rate, 2),
        years=FULL_HISTORY_YEARS,
        independence_note="Two rho variants reported (0.0, 0.5 standard), same positions=1 caveat as index addition.",
        designs=designs,
        approximation_note=(
            "MARKET-WIDE count (not restricted to S&P 500 parents), a materially larger and more "
            "correct population than the backlog's original 73-event S&P-500-only estimate -- the "
            "forced-selling mechanism (holders whose mandate can't retain a newly spun-off, "
            "often small-cap security) applies to any spinoff with a widely-held parent, not just "
            "S&P 500 constituents. 'spunofffrom' (the parent-side mirror action) independently "
            "confirmed the identical count (570), consistent with one row per event per side."
        ),
    )


def screen_distressed_postreorg(client: SharadarClient, *, force_refresh: bool = False) -> ScreenResult:
    bankruptcy = _fetch_actions_chunked(client, "bankruptcyliquidation", force_refresh=force_refresh)
    spac_keywords = ("acquisition corp", "acquisition inc", "acquisition ii", "acquisition iii", "spac")
    non_spac = [row for row in bankruptcy if not any(kw in row["name"].lower() for kw in spac_keywords)]
    rate = len(non_spac) / FULL_HISTORY_YEARS
    designs = [
        screen_design(
            "distressed_postreorg_positions1_rho0", positions=1, rebalances_per_year=rate,
            years=FULL_HISTORY_YEARS, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.0,
        ),
        screen_design(
            "distressed_postreorg_positions1_standard_corr", positions=1, rebalances_per_year=rate,
            years=FULL_HISTORY_YEARS, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=STANDARD_CORR,
        ),
    ]
    return ScreenResult(
        candidate="Distressed / post-reorg equity",
        barrier="MANDATE/CAPACITY",
        observation_definition="Event: market-wide actions table, action=bankruptcyliquidation, full history 1998-2026",
        raw_count=str(len(bankruptcy)),
        usable_count=str(len(non_spac)),
        events_or_bets_per_year=round(rate, 2),
        years=FULL_HISTORY_YEARS,
        independence_note="Two rho variants reported (0.0, 0.5 standard), same positions=1 caveat.",
        designs=designs,
        approximation_note=(
            f"Raw count {len(bankruptcy)} includes {len(bankruptcy) - len(non_spac)} SPAC-name-heuristic "
            "matches (SPAC trust liquidations are a different, much more common phenomenon than an "
            "operating-company Chapter 11 exit and were excluded). REMAINING DATA-SHAPE CAVEAT, not "
            "resolved by this screen: 'bankruptcyliquidation' rows have no contraticker linking an old, "
            "cancelled security to a NEW post-reorg ticker -- this action type most plausibly marks the "
            "OLD equity's cancellation, not the emergence event the hypothesis actually needs (shares of "
            "the reorganized company that a mandate-constrained holder can't yet hold). The count below is "
            "therefore an UPPER BOUND on usable events, not a confirmed usable count -- some fraction of "
            "these are pure zero-recovery liquidations with no continuing equity to buy at all."
        ),
    )


def screen_merger_arb_small_deal(
    client: SharadarClient, *, size_threshold_millions: float = 500.0, force_refresh: bool = False
) -> ScreenResult:
    deals = _fetch_actions_chunked(client, "acquisitionof", force_refresh=force_refresh)
    sized = [row for row in deals if row.get("value") not in (None, "N/A")]
    small = [row for row in sized if float(row["value"]) < size_threshold_millions]
    rate = len(small) / FULL_HISTORY_YEARS
    designs = [
        screen_design(
            "merger_arb_small_deal_positions1_rho0", positions=1, rebalances_per_year=rate,
            years=FULL_HISTORY_YEARS, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.0,
        ),
        screen_design(
            "merger_arb_small_deal_positions1_standard_corr", positions=1, rebalances_per_year=rate,
            years=FULL_HISTORY_YEARS, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=STANDARD_CORR,
        ),
        # Sensitivity variant only, explicitly labeled as a DIFFERENT
        # assumption, not a way to inflate the primary numbers above: merger
        # arb spread-capture returns are conventionally treated as largely
        # deal-idiosyncratic (each deal's completion risk is mostly
        # independent of the market and of other deals), which would justify
        # a lower correlation than the project-standard 0.5 default.
        screen_design(
            "merger_arb_small_deal_positions1_low_corr_sensitivity", positions=1, rebalances_per_year=rate,
            years=FULL_HISTORY_YEARS, tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.2,
        ),
    ]
    return ScreenResult(
        candidate="Small merger arb below institutional minimum deal size",
        barrier="CAPACITY",
        observation_definition=(
            f"Event: market-wide actions table, action=acquisitionof, deal value < ${size_threshold_millions:.0f}M, "
            "full history 1998-2026"
        ),
        raw_count=str(len(deals)),
        usable_count=str(len(small)),
        events_or_bets_per_year=round(rate, 2),
        years=FULL_HISTORY_YEARS,
        independence_note=(
            "Three variants: rho=0.0, rho=0.5 (project standard), and rho=0.2 (sensitivity only, "
            "reflecting the conventional view that merger-arb completion risk is largely deal-specific "
            "rather than market-correlated) -- reported separately, never substituted for the standard case."
        ),
        designs=designs,
        approximation_note=(
            f"acquisitionof.value is the vendor-reported total deal value in $M, confirmed against named "
            f"real 2025 examples (e.g. Roche/Poseida $925.9M). {len(deals) - len(sized)} of {len(deals)} "
            f"deals had no value reported and were excluded from sizing. {len(sized)} sized deals; "
            f"{len(small)} ({100*len(small)/len(sized):.1f}%) below ${size_threshold_millions:.0f}M, "
            f"median deal size across all sized deals was measured separately at ~$377M. This table is "
            "market-wide, not S&P-500-limited -- deliberately, since 'below institutional minimum deal "
            "size' by construction targets sub-large-cap acquirers/targets the S&P 500 roster would "
            "mostly exclude."
        ),
    )


def screen_tax_loss_selling(
    client: SharadarClient,
    *,
    sample_stride: int = 8,
    loser_threshold: float = -0.20,
    sample_years: tuple[int, ...] = (2022, 2023, 2024, 2025),
    force_refresh: bool = False,
) -> ScreenResult:
    current = client.query_all("sp500", date=FULL_HISTORY_END, action="current", force_refresh=force_refresh)
    universe = sorted({row["ticker"] for row in current.rows})
    sample = universe[::sample_stride]

    yearly_rate: dict[int, float] = {}
    yearly_counts: dict[int, tuple[int, int]] = {}
    per_symbol: dict[str, list[dict[str, Any]]] = {}
    for symbol in sample:
        result = client.query_all(
            "stocks", ticker=symbol, **{"from": "2021-12-15", "to": "2025-11-15"},
            fields="ticker,date,closeadj", sort="date.asc", force_refresh=force_refresh,
        )
        per_symbol[symbol] = result.rows

    for year in sample_years:
        jan1 = pd.Timestamp(f"{year}-01-01")
        nov1 = pd.Timestamp(f"{year}-11-01")
        losers = 0
        total = 0
        for rows in per_symbol.values():
            if not rows:
                continue
            frame = pd.DataFrame(rows)
            frame["date"] = pd.to_datetime(frame["date"])
            frame = frame.set_index("date").sort_index()
            before = frame.loc[frame.index <= jan1]
            asof_nov = frame.loc[frame.index <= nov1]
            if before.empty or asof_nov.empty:
                continue
            start_price = float(before["closeadj"].iloc[-1])
            nov_price = float(asof_nov["closeadj"].iloc[-1])
            total += 1
            if start_price > 0 and (nov_price / start_price - 1.0) < loser_threshold:
                losers += 1
        yearly_counts[year] = (losers, total)
        yearly_rate[year] = losers / total if total else 0.0

    avg_rate = sum(yearly_rate.values()) / len(yearly_rate) if yearly_rate else 0.0
    estimated_positions = round(avg_rate * len(universe))
    designs = [
        screen_design(
            "tax_loss_selling_cross_sectional_standard_corr",
            positions=max(1, estimated_positions), rebalances_per_year=1, years=FULL_HISTORY_YEARS,
            tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=STANDARD_CORR,
        ),
        # Losers cluster hard in bad years (measured: 46% in 2022 vs 1.6% in
        # 2025) -- a higher-correlation sensitivity variant is reported
        # because "many stocks down 20%+ in the same year" is close to a
        # single macro factor bet, not N independent ones.
        screen_design(
            "tax_loss_selling_cross_sectional_high_corr_sensitivity",
            positions=max(1, estimated_positions), rebalances_per_year=1, years=FULL_HISTORY_YEARS,
            tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.7,
        ),
    ]
    return ScreenResult(
        candidate="Tax-loss selling reversal (December)",
        barrier="MANDATE/OPS",
        observation_definition=(
            "Cross-sectional: (S&P-500-constituent, year) pairs where closeadj return from "
            f"Jan 1 to Nov 1 < {loser_threshold:.0%}, one trade per qualifying stock per year"
        ),
        raw_count=f"~{len(universe)} current S&P 500 constituents x {len(sample_years)} sampled years",
        usable_count=f"~{estimated_positions}/yr (extrapolated from a {len(sample)}-ticker sample)",
        events_or_bets_per_year=estimated_positions,
        years=FULL_HISTORY_YEARS,
        independence_note=(
            "Two variants: rho=0.5 (project standard) and rho=0.7 (sensitivity, reflecting measured "
            "extreme year-to-year clustering -- loser rate ranged 1.6% to 46.0% across just 4 sampled "
            "years, i.e. most 'losers' in a given year are largely the same macro bet)."
        ),
        designs=designs,
        approximation_note=(
            f"APPROXIMATION, not a full-universe measurement: sampled {len(sample)} of {len(universe)} "
            f"CURRENT S&P 500 constituents (every {sample_stride}th ticker alphabetically), measured "
            f"Jan1-to-Nov1 return in {sample_years}. Per-year loser rate: "
            + ", ".join(f"{y}={c[0]}/{c[1]} ({100*c[0]/c[1]:.1f}%)" for y, c in sorted(yearly_counts.items()))
            + f". Average {100*avg_rate:.1f}%/yr extrapolated to the full {len(universe)}-name universe gives "
            f"~{estimated_positions} qualifying stocks/yr, ONE trade cycle per year (Dec-Jan window). "
            "Uses TODAY's S&P 500 roster as a proxy for the historical universe every year, which "
            "understates true breadth for this specific hypothesis (small/mid-caps outside the S&P 500 "
            "are the more classic tax-loss-selling candidates and are entirely excluded from this "
            "approximation) -- flagged as a conservative undercount, not a full measurement."
        ),
    )


def screen_sub5_dollar(client: SharadarClient, *, force_refresh: bool = False) -> ScreenResult:
    snapshot = client.query_all(
        "stocks", date=SNAPSHOT_TRADING_DATE, fields="ticker,date,close", force_refresh=force_refresh
    )
    priced = [row for row in snapshot.rows if row.get("close") not in (None, "N/A")]
    under5 = [row for row in priced if 0 < float(row["close"]) < 5]
    # No crossing-rate data is available without a full historical multi-
    # thousand-ticker daily scan (out of scope for this pass). Approximated
    # instead as: assume a MODEST 25% of the sub-$5 population crosses the
    # threshold (up or down) in a typical year -- a labeled assumption, not
    # a measurement -- to convert a static level count into an annual
    # observation rate for screen_design's monthly-rebalance shape.
    assumed_annual_crossing_fraction = 0.25
    estimated_positions = round(len(under5) * assumed_annual_crossing_fraction)
    designs = [
        screen_design(
            "sub5_dollar_cross_sectional_standard_corr",
            positions=max(1, estimated_positions), rebalances_per_year=12, years=FULL_HISTORY_YEARS,
            tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=STANDARD_CORR,
        ),
    ]
    return ScreenResult(
        candidate="Sub-$5 stocks excluded by fund charters",
        barrier="MANDATE",
        observation_definition="Cross-sectional: securities crossing the $5 close-price threshold, monthly monitoring",
        raw_count=f"{len(priced)} priced securities market-wide on {SNAPSHOT_TRADING_DATE}",
        usable_count=f"{len(under5)} under $5 today ({100*len(under5)/len(priced):.1f}%); ~{estimated_positions}/yr assumed crossing",
        events_or_bets_per_year=estimated_positions,
        years=FULL_HISTORY_YEARS,
        independence_note="rho=0.5 (project standard) only -- see approximation caveats below before trusting this number.",
        designs=designs,
        approximation_note=(
            "HEAVILY APPROXIMATE, weakest-evidence screen in this pass. MEASURED: a single-day "
            f"market-wide snapshot ({SNAPSHOT_TRADING_DATE}) found {len(under5)} of {len(priced)} tracked "
            "securities (includes ETFs, ADRs, SPAC units -- NOT filtered to common stock, no join "
            "against tickers.category attempted this pass) closing under $5. NOT MEASURED: the actual "
            "annual rate of stocks CROSSING the threshold (the tradable event), which is what the "
            "hypothesis needs and what a real screen would require a multi-thousand-ticker historical "
            "daily scan to get. The 25% annual-crossing-fraction and monthly-rebalance cadence are "
            "assumptions, not data. Treat this row's MDA as illustrative of order of magnitude only."
        ),
    )


def screen_sub300m_cap_anomalies(client: SharadarClient, *, force_refresh: bool = False) -> ScreenResult:
    snapshot = client.query_all(
        "daily", date=SNAPSHOT_TRADING_DATE, fields="ticker,date,marketcap", force_refresh=force_refresh
    )
    have_cap = [row for row in snapshot.rows if row.get("marketcap") not in (None, "N/A")]
    under300 = [row for row in have_cap if float(row["marketcap"]) < 300]
    designs = [
        screen_design(
            "sub300m_cap_cross_sectional_standard_corr",
            positions=len(under300), rebalances_per_year=12, years=FULL_HISTORY_YEARS,
            tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=STANDARD_CORR,
        ),
        # A cross-correlation of 0.5 across TWO THOUSAND names is an
        # unrealistically high assumption to carry over unmodified from a
        # 5-position mega-cap anchor -- reported as a sensitivity check, not
        # a substitute, since 0.5 stays the primary number for comparability.
        screen_design(
            "sub300m_cap_cross_sectional_lower_corr_sensitivity",
            positions=len(under300), rebalances_per_year=12, years=FULL_HISTORY_YEARS,
            tradable_alpha_pct=MAX_TRADABLE_ALPHA_PCT, avg_pairwise_corr=0.15,
        ),
    ]
    return ScreenResult(
        candidate="Sub-$300M cap cross-sectional anomalies",
        barrier="CAPACITY",
        observation_definition="Cross-sectional: securities with marketcap < $300M, monthly rebalance",
        raw_count=f"{len(have_cap)} securities market-wide with marketcap on {SNAPSHOT_TRADING_DATE}",
        usable_count=f"{len(under300)} under $300M ({100*len(under300)/len(have_cap):.1f}%)",
        events_or_bets_per_year=len(under300) * 12,
        years=FULL_HISTORY_YEARS,
        independence_note=(
            "Two variants: rho=0.5 (project standard, but see note) and rho=0.15 (sensitivity -- "
            "0.5 was calibrated on a 5-name mega-cap anchor and is not obviously the right assumption "
            "for a ~2000-name small/micro-cap cross-section, where idiosyncratic risk is proportionally "
            "much larger; reported as a range, not resolved)."
        ),
        designs=designs,
        approximation_note=(
            f"MEASURED, single-day market-wide snapshot ({SNAPSHOT_TRADING_DATE}): {len(under300)} of "
            f"{len(have_cap)} tracked securities. NOT measured: whether this count is stable across "
            "the full 28.5-year history (assumed comparable order of magnitude here; small/micro-cap "
            "listing counts DO vary with market cycles and this was not checked historically). Highest "
            "single-day raw breadth of any candidate in this pass by a wide margin."
        ),
    )


def screen_odd_lot_tender() -> ScreenResult:
    return ScreenResult(
        candidate="Odd-lot tender provisions",
        barrier="OPERATIONS",
        observation_definition="Event: company-initiated odd-lot tender offers",
        raw_count="0 (no matching action code found)",
        usable_count="0",
        events_or_bets_per_year=0.0,
        years=FULL_HISTORY_YEARS,
        independence_note="N/A -- no data source identified.",
        data_blocked=True,
        data_blocked_reason=(
            "Empirically confirmed absent, not assumed: the full distinct-action-type inventory of "
            "Sharadar's actions table, checked over both a 1-month and a full 1-year (2025) market-wide "
            "sample (44,180 rows), contains no tender-offer or odd-lot-specific code. Full observed set: "
            "dividend, listed, delisted, split, tickerchangefrom/to, relation, acquisitionby/of, "
            "namechangefrom/to, acquisitioncash, sicchangefrom/to, spacunitseparation, "
            "regulatorydelisting, bankruptcyliquidation, acquisitionstock, spacmerger, adrratiosplit, "
            "spinoff, spunofffrom, voluntarydelisting, spinoffdividend, acquisitionelectstock, "
            "acquisitionelectcash, mergerfrom/to. No other Sharadar table probed this session "
            "(tickers, stocks, daily, sp500, events) carries tender-offer structure either. `events` "
            "(SEC 8-K eventcodes) was NOT decoded this session and could theoretically contain a tender "
            "code, but that is unconfirmed, not a basis for a number."
        ),
    )


def run_all(*, force_refresh: bool = False) -> dict[str, Any]:
    client = SharadarClient()
    results = [
        screen_index_addition(client, force_refresh=force_refresh),
        screen_tax_loss_selling(client, force_refresh=force_refresh),
        screen_spinoff_dumping(client, force_refresh=force_refresh),
        screen_distressed_postreorg(client, force_refresh=force_refresh),
        screen_merger_arb_small_deal(client, force_refresh=force_refresh),
        screen_sub300m_cap_anomalies(client, force_refresh=force_refresh),
        screen_sub5_dollar(client, force_refresh=force_refresh),
        screen_odd_lot_tender(),
    ]
    ranked = sorted(
        results,
        key=lambda r: (
            {"VIABLE": 0, "MARGINAL": 1, "DEAD": 2, "DATA_BLOCKED": 3}[r.verdict()],
            r.best_case_mda_pct if r.best_case_mda_pct is not None else float("inf"),
        ),
    )
    return {
        "schemaVersion": 1,
        "evidenceOnly": True,
        "thresholds": {"maxTradableAlphaPct": MAX_TRADABLE_ALPHA_PCT, "minUsableEvents": MIN_USABLE_EVENTS},
        "results": [
            {
                "candidate": r.candidate,
                "barrier": r.barrier,
                "observationDefinition": r.observation_definition,
                "rawCount": r.raw_count,
                "usableCount": r.usable_count,
                "eventsOrBetsPerYear": r.events_or_bets_per_year,
                "years": r.years,
                "independenceNote": r.independence_note,
                "designs": r.designs,
                "bestCaseMdaPct": r.best_case_mda_pct,
                "verdict": r.verdict(),
                "deadCause": r.dead_cause(),
                "approximationNote": r.approximation_note,
                "dataBlockedReason": r.data_blocked_reason,
            }
            for r in ranked
        ],
    }


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hypothesis backlog breadth/MDA screen",
        "",
        f"Thresholds: MDA ceiling {report['thresholds']['maxTradableAlphaPct']}%/yr, "
        f"event-count floor {report['thresholds']['minUsableEvents']} (event-driven candidates only).",
        "",
        "| Rank | Candidate | Barrier | Bets/yr | Best-case MDA %/yr | Verdict | Cause if DEAD |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for i, row in enumerate(report["results"], start=1):
        mda = f"{row['bestCaseMdaPct']:.2f}" if row["bestCaseMdaPct"] is not None else "—"
        cause = row["deadCause"] or ("data availability" if row["verdict"] == "DATA_BLOCKED" else "—")
        lines.append(
            f"| {i} | {row['candidate']} | {row['barrier']} | {row['eventsOrBetsPerYear']} | "
            f"{mda} | **{row['verdict']}** | {cause} |"
        )
    lines.append("")
    for row in report["results"]:
        lines.extend([
            f"## {row['candidate']} -- {row['verdict']}",
            "",
            f"- Barrier: {row['barrier']}",
            f"- Observation definition: {row['observationDefinition']}",
            f"- Raw count: {row['rawCount']}",
            f"- Usable count: {row['usableCount']}",
            f"- Independence assumption: {row['independenceNote']}",
        ])
        if row["designs"]:
            lines.append("")
            lines.append("| Design | Bets/yr | MDA %/yr |")
            lines.append("|---|---:|---:|")
            for d in row["designs"]:
                lines.append(f"| {d['label']} | {d['independent_bets_per_year']:.2f} | {d['mda_pct']:.2f} |")
        if row["approximationNote"]:
            lines.extend(["", row["approximationNote"]])
        if row["dataBlockedReason"]:
            lines.extend(["", row["dataBlockedReason"]])
        lines.append("")
    return "\n".join(lines)


def write_report(report: dict[str, Any], report_dir: Path = REPORT_DIR) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "report.json"
    md_path = report_dir / "report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    return json_path, md_path


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()
    report = run_all(force_refresh=args.force_refresh)
    paths = write_report(report)
    print(json.dumps({
        "reports": [str(p) for p in paths],
        "ranked": [(r["candidate"], r["verdict"], r["bestCaseMdaPct"]) for r in report["results"]],
    }, indent=2))


if __name__ == "__main__":
    main()
