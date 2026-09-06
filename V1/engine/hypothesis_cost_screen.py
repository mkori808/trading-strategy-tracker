"""Round-trip spread cost screen across the hypothesis backlog.

Companion to `engine/hypothesis_backlog_screen.py` (breadth) and
`engine/index_deletion_screen.py` (the pattern both extend). This session's
sub-$300M momentum work found that a candidate can clear its own breadth
screen and still be economically dead once real transaction costs are
measured. This module generalizes that check across every candidate in
`research/hypothesis_backlog.md`, using real price data, not invented
spread figures -- and, after the first pass, two real problems with that
first pass, fixed here rather than carried forward silently.

FIX 1 -- estimator validation. Corwin-Schultz (2012) alone was trusted the
first time. Validated here against a second, independently-derived
estimator (Abdi & Ranaldo 2017, close/high/low based) and against a
real-world sanity check on ultra-liquid mega-caps (true spread ~0.5-1bp).
Result: on AAPL, Corwin-Schultz's MEDIAN over a volatile year came back at
18.5bps while Abdi-Ranaldo came back at 0.0bps -- Corwin-Schultz's 2-day
range comparison misattributes genuine large-range trading days (real
volatility, not spread) as spread, a known limitation more severe on
higher-volatility names. Both estimators are now reported for every
candidate; where they diverge sharply, that divergence IS the finding, not
resolved by picking one.

FIX 2 -- holding period, not raw event rate. The first pass used each
event-driven candidate's own events/year figure (built for MDA/breadth
purposes) as if it were also the annual portfolio turnover rate -- correct
only in the special case where exactly one capital slot exists and its
holding period exactly equals 1/events-per-year. In general a strategy
holds a NUMBER of concurrent positions scaling with the event rate, and
annual cost per dollar of capital is spread / holding_period_years,
independent of the event rate (Little's-law argument: N slots x (1/T)
turns/slot/year x (1/N) capital/slot = (1/T) turns/year of total capital).
Each candidate's holding period is now a separate, disclosed assumption
tied to its own mechanism, not borrowed from the breadth calculation.

Never runs a backtest. Never builds a PIT store. Reports what was measured.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import numpy as np

from engine.sharadar_client import SharadarClient


ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "hypothesis_cost_screen"
SAMPLE_WINDOW = ("2025-09-01", "2026-09-03")  # trailing ~1 year, used when no event-relative date applies
CROSS_SECTIONAL_TURNOVERS = (0.25, 0.50, 0.75, 1.00)
TARGET_SAMPLE_SIZE = 45  # up from 8-15 in the first pass


def corwin_schultz_spread(high: list[float], low: list[float]) -> np.ndarray:
    """Corwin & Schultz (2012) high-low spread estimator, 2-day rolling windows.

    Negative estimates are truncated to zero, standard practice for this
    estimator. KNOWN LIMITATION, confirmed this session on real data: large
    genuine-volatility trading days can be misattributed as spread, biasing
    the estimate upward on higher-volatility names -- see module docstring.
    """
    h, l = np.asarray(high, dtype=float), np.asarray(low, dtype=float)
    beta = (np.log(h[:-1] / l[:-1])) ** 2 + (np.log(h[1:] / l[1:])) ** 2
    h2 = np.maximum(h[:-1], h[1:])
    l2 = np.minimum(l[:-1], l[1:])
    gamma = (np.log(h2 / l2)) ** 2
    k = 3 - 2 * np.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    spread = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    return np.clip(spread, 0, None)


def abdi_ranaldo_spread(close: list[float], high: list[float], low: list[float]) -> np.ndarray:
    """Abdi & Ranaldo (2017) close/high/low spread estimator, 2-day pairs.

    Independent derivation from Corwin-Schultz: uses each day's CLOSE
    positioned against the log high-low MIDPOINT, compared across
    adjacent days, rather than comparing 1-day vs. 2-day RANGES directly.
    Chosen as the cross-check specifically because it is methodologically
    different, not a variant of the same idea. Negative estimates
    truncated to zero, same convention as Corwin-Schultz.
    """
    c = np.log(np.asarray(close, dtype=float))
    h = np.log(np.asarray(high, dtype=float))
    l = np.log(np.asarray(low, dtype=float))
    mid = (h + l) / 2
    product = (c[:-1] - mid[:-1]) * (c[:-1] - mid[1:])
    return np.sqrt(np.maximum(4 * product, 0))


def _mega_cap_sanity_check(client: SharadarClient) -> dict[str, Any]:
    """Ultra-liquid mega-caps have a well-known real spread of ~0.5-2bps.
    Neither estimator should report far more than that here -- if it does,
    that estimator's numbers elsewhere in this run need the same skepticism.
    """
    out = {}
    for ticker in ("AAPL", "MSFT"):
        result = client.query_all(
            "stocks", ticker=ticker, **{"from": SAMPLE_WINDOW[0], "to": SAMPLE_WINDOW[1]},
            fields="ticker,date,close,high,low", sort="date.asc",
        )
        rows = result.rows
        if len(rows) < 30:
            continue
        close = [float(r["close"]) for r in rows]
        high = [float(r["high"]) for r in rows]
        low = [float(r["low"]) for r in rows]
        out[ticker] = {
            "n": len(rows),
            "corwinSchultzMedianBps": float(np.nanmedian(corwin_schultz_spread(high, low))) * 10_000,
            "abdiRanaldoMedianBps": float(np.nanmedian(abdi_ranaldo_spread(close, high, low))) * 10_000,
        }
    return out


def dual_estimator_spread_for_sample(
    client: SharadarClient,
    tickers: list[str],
    *,
    window: tuple[str, str] = SAMPLE_WINDOW,
    end_dates: dict[str, str] | None = None,
) -> dict[str, Any]:
    """`end_dates`, when given, fetches each ticker's own trailing ~1-year
    window ENDING at its own event date -- required for tickers that
    stopped trading (merger close, delisting) before a fixed recent window
    would start. Falls back to `window` for any ticker not in `end_dates`.
    """
    per_ticker_cs: dict[str, float] = {}
    per_ticker_ar: dict[str, float] = {}
    for ticker in tickers:
        if end_dates and ticker in end_dates:
            end = date.fromisoformat(end_dates[ticker])
            start = (end - timedelta(days=365)).isoformat()
            fetch_window = (start, end.isoformat())
        else:
            fetch_window = window
        result = client.query_all(
            "stocks", ticker=ticker, **{"from": fetch_window[0], "to": fetch_window[1]},
            fields="ticker,date,close,high,low", sort="date.asc",
        )
        rows = [r for r in result.rows if r.get("high") and r.get("low") and r.get("close") and float(r["low"]) > 0]
        if len(rows) < 30:
            continue
        high = [float(r["high"]) for r in rows]
        low = [float(r["low"]) for r in rows]
        close = [float(r["close"]) for r in rows]
        per_ticker_cs[ticker] = float(np.nanmedian(corwin_schultz_spread(high, low))) * 10_000
        per_ticker_ar[ticker] = float(np.nanmedian(abdi_ranaldo_spread(close, high, low))) * 10_000

    def _median(d: dict[str, float]) -> float | None:
        if not d:
            return None
        v = sorted(d.values())
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2

    return {
        "sampleSize": len(per_ticker_cs),
        "corwinSchultzMedianBps": _median(per_ticker_cs),
        "abdiRanaldoMedianBps": _median(per_ticker_ar),
        "perTickerCorwinSchultz": per_ticker_cs,
        "perTickerAbdiRanaldo": per_ticker_ar,
    }


@dataclass
class CostScreenResult:
    candidate: str
    holdingPeriodModel: str
    holdingPeriodYears: float | None
    sampleTickers: list[str]
    spreadMeasurement: dict[str, Any]
    mdaPct: float | None
    breadthVerdict: str
    annualCostByEstimator: dict[str, float] = field(default_factory=dict)

    def verdict(self) -> str:
        if self.mdaPct is None or not self.annualCostByEstimator:
            return "NOT_MEASURED"
        if self.breadthVerdict in ("DEAD", "SCREENED_OUT"):
            return "MOOT -- already dead on breadth"
        costs = [c for c in self.annualCostByEstimator.values() if c == c]  # drop NaN
        if not costs:
            return "NOT_MEASURED"
        if all(c >= self.mdaPct for c in costs):
            return "COST_DOMINATES -- exceeds MDA under BOTH estimators"
        if all(c < self.mdaPct for c in costs):
            return "COST_OK -- stays under MDA under BOTH estimators"
        return "ESTIMATOR_SENSITIVE -- verdict depends on which spread estimator is trusted"


def _event_driven_result(
    candidate: str, tickers: list[str], holding_period_years: float, holding_period_note: str,
    mda_pct: float, breadth_verdict: str, client: SharadarClient, end_dates: dict[str, str] | None = None,
) -> CostScreenResult:
    """Annual cost = spread / holding_period_years -- see module docstring's
    Little's-law argument for why this replaces the raw events/year figure."""
    measurement = dual_estimator_spread_for_sample(client, tickers, end_dates=end_dates)
    cs, ar = measurement.get("corwinSchultzMedianBps"), measurement.get("abdiRanaldoMedianBps")
    annual_cost = {
        "corwinSchultz": (cs / holding_period_years / 100.0) if cs is not None else float("nan"),
        "abdiRanaldo": (ar / holding_period_years / 100.0) if ar is not None else float("nan"),
    }
    return CostScreenResult(
        candidate=candidate,
        holdingPeriodModel=f"event-driven, ASSUMED holding period {holding_period_years:.3f}yr ({holding_period_note}) -- NOT the raw events/year figure used for breadth",
        holdingPeriodYears=holding_period_years, sampleTickers=tickers, spreadMeasurement=measurement,
        mdaPct=mda_pct, breadthVerdict=breadth_verdict, annualCostByEstimator=annual_cost,
    )


def _cross_sectional_result(
    candidate: str, tickers: list[str], rebalances_per_year: float, mda_pct: float,
    breadth_verdict: str, client: SharadarClient, turnover: float,
) -> CostScreenResult:
    measurement = dual_estimator_spread_for_sample(client, tickers)
    cs, ar = measurement.get("corwinSchultzMedianBps"), measurement.get("abdiRanaldoMedianBps")
    annual_cost = {
        "corwinSchultz": (cs * turnover * rebalances_per_year / 100.0) if cs is not None else float("nan"),
        "abdiRanaldo": (ar * turnover * rebalances_per_year / 100.0) if ar is not None else float("nan"),
    }
    return CostScreenResult(
        candidate=candidate,
        holdingPeriodModel=f"cross-sectional, {rebalances_per_year:.0f} rebalances/yr @ {turnover:.0%} turnover (holding period ~= {1/(rebalances_per_year*turnover):.2f}yr)",
        holdingPeriodYears=1 / (rebalances_per_year * turnover), sampleTickers=tickers,
        spreadMeasurement=measurement, mdaPct=mda_pct, breadthVerdict=breadth_verdict,
        annualCostByEstimator=annual_cost,
    )


def _sp500_family_tickers(client: SharadarClient, *, force_refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
    r = client.query_all("sp500", **{"from": "1998-01-01", "to": "2026-09-05"}, sort="date.asc", force_refresh=force_refresh)
    removed = [row for row in r.rows if row["action"] == "removed"]
    added = [row for row in r.rows if row["action"] == "added"]
    return {"removed": removed, "added": added}


def run_all(*, force_refresh: bool = False) -> list[CostScreenResult]:
    client = SharadarClient()
    results: list[CostScreenResult] = []

    sanity = _mega_cap_sanity_check(client)

    sp500 = _sp500_family_tickers(client, force_refresh=force_refresh)
    mcc_removed = [row["ticker"] for row in sp500["removed"] if "market capitalization" in (row.get("note") or "").lower()]
    mcc_added = [row["ticker"] for row in sp500["added"] if "market capitalization" in (row.get("note") or "").lower()]

    # --- Index deletion (already SCREENED OUT on breadth) ---
    # Holding period: the hypothesis's own design (research/hypothesis_backlog.md)
    # was a short POST-EFFECTIVE-DATE reversal, never preregistered to an exact
    # number since the breadth screen closed it first. 15 trading days (~3
    # weeks) used as a disclosed, reasoned assumption -- not measured, not fitted.
    results.append(_event_driven_result(
        "Index deletion / forced selling", mcc_removed[-TARGET_SAMPLE_SIZE:], 15 / 252,
        "15-trading-day post-effective-date reversal, per the hypothesis's own short-horizon design",
        11.45, "SCREENED_OUT", client,
    ))

    # --- Index addition (MARGINAL on breadth) ---
    results.append(_event_driven_result(
        "Index addition / forced buying", mcc_added[-TARGET_SAMPLE_SIZE:], 15 / 252,
        "mirrors deletion's short-horizon reversal design",
        5.87, "MARGINAL", client,
    ))

    # --- Spinoff dumping (MARGINAL on breadth); target is the CHILD (contraticker) ---
    r2 = client.query_all("actions", **{"from": "2010-01-01", "to": "2025-06-01"}, action="spinoff", sort="date.asc", force_refresh=force_refresh)
    spinoff_children = [row["contraticker"] for row in r2.rows if row.get("contraticker") not in (None, "N/A")]
    results.append(_event_driven_result(
        "Spinoff shares dumped by ineligible holders", spinoff_children[-TARGET_SAMPLE_SIZE:], 40 / 252,
        "~2 months for forced-selling pressure (multiple holder types, not one reversal) to resolve",
        6.50, "MARGINAL", client,
    ))

    # --- Distressed / post-reorg equity (VIABLE with data-shape caveat) ---
    r3 = client.query_all("actions", **{"from": "2015-01-01", "to": "2025-06-01"}, action="bankruptcyliquidation", sort="date.asc", force_refresh=force_refresh)
    distressed_rows = [row for row in r3.rows if not any(kw in row["name"].lower() for kw in ("acquisition corp", "acquisition inc", "spac"))][-TARGET_SAMPLE_SIZE:]
    results.append(_event_driven_result(
        "Distressed / post-reorg equity", [row["ticker"] for row in distressed_rows], 180 / 252,
        "~9 months for a slower re-rating as mandate-constrained holders become willing/eligible to buy",
        2.82, "VIABLE_WITH_CAVEAT", client, end_dates={row["ticker"]: row["date"] for row in distressed_rows},
    ))

    # --- Small merger arb below institutional minimum deal size (SCREENED OUT) ---
    r4 = client.query_all("actions", **{"from": "2015-01-01", "to": "2025-06-01"}, action="acquisitionof", sort="date.asc", force_refresh=force_refresh)
    merger_rows = [row for row in r4.rows if row.get("value") not in (None, "N/A") and float(row["value"]) < 500 and row.get("contraticker") not in (None, "N/A")][-TARGET_SAMPLE_SIZE:]
    results.append(_event_driven_result(
        "Small merger arb below institutional minimum deal size", [row["contraticker"] for row in merger_rows], 130 / 252,
        "~6 months announcement-to-close, standard small-cap M&A timeline",
        4.10, "SCREENED_OUT", client, end_dates={row["contraticker"]: row["date"] for row in merger_rows},
    ))

    # --- Sub-$5 stocks (VIABLE, weak evidence) ---
    r5 = client.query_all("stocks", date="2026-09-03", fields="ticker,date,close", force_refresh=force_refresh)
    under5 = [row["ticker"] for row in r5.rows if row.get("close") not in (None, "N/A") and 0 < float(row["close"]) < 5]
    sub5_tickers = under5[::max(1, len(under5) // TARGET_SAMPLE_SIZE)][:TARGET_SAMPLE_SIZE]
    results.append(_cross_sectional_result("Sub-$5 stocks excluded by fund charters", sub5_tickers, 12, 1.59, "VIABLE_WEAK_EVIDENCE", client, turnover=0.5))

    # --- Tax-loss selling reversal (already DEAD on breadth) ---
    r6 = client.query_all("sp500", date="2026-09-05", action="current", force_refresh=force_refresh)
    sp500_universe = sorted({row["ticker"] for row in r6.rows})
    sp500_sample = sp500_universe[::max(1, len(sp500_universe) // TARGET_SAMPLE_SIZE)][:TARGET_SAMPLE_SIZE]
    results.append(_cross_sectional_result(
        "Tax-loss selling reversal (December)", sp500_sample, rebalances_per_year=1, mda_pct=8.15,
        breadth_verdict="DEAD", client=client, turnover=1.0,
    ))

    # --- Sub-$300M cap momentum: re-measured with larger sample + dual estimator ---
    r7 = client.query_all("daily", date="2026-09-03", fields="ticker,date,marketcap", force_refresh=force_refresh)
    have_cap = [row for row in r7.rows if row.get("marketcap") not in (None, "N/A") and float(row["marketcap"]) > 0]
    under300 = sorted(have_cap, key=lambda x: float(x["marketcap"]))[: int(len(have_cap) * 0.20)]
    momentum_sample = [row["ticker"] for row in under300[::max(1, len(under300) // TARGET_SAMPLE_SIZE)][:TARGET_SAMPLE_SIZE]]
    results.append(_cross_sectional_result(
        "Sub-$300M cap cross-sectional anomalies (momentum)", momentum_sample, rebalances_per_year=12,
        mda_pct=3.04, breadth_verdict="PAUSED_BEFORE_PHASE_2", client=client, turnover=0.5,
    ))

    # --- NEW: S&P 500+400+600 combined additions/deletions/migrations family ---
    # 400/600 confirmed DATA_BLOCKED this session (Sharadar has no sp400/sp600/
    # midcap/smallcap/russell/djia table under any tried name -- reconfirmed via
    # two direct docs fetches plus HTTP probes; a genuine product-scope gap, not
    # an entitlement/tier problem -- the paid full_history_or_broader tier is
    # confirmed working on every table Sharadar actually offers). Screened here:
    # the AVAILABLE S&P 500 side, ALL reasons combined (not filtered to
    # market-cap-change only, since "migrations" between tiers are exactly the
    # market-cap-driven population already used above, and widening to "all
    # reasons" is the closest available proxy for the requested combined family).
    all_500_events = [row["ticker"] for row in sp500["removed"]] + [row["ticker"] for row in sp500["added"]]
    sample_500_family = all_500_events[-TARGET_SAMPLE_SIZE:]
    events_per_year_500_family = len(sp500["removed"] + sp500["added"]) / 28.5
    from engine.power_curve import screen_design
    design = screen_design(
        "sp500_family_all_reasons", positions=1, rebalances_per_year=events_per_year_500_family,
        years=28.5, tradable_alpha_pct=4.0, avg_pairwise_corr=0.5,
    )
    results.append(_event_driven_result(
        "S&P 500+400+600 additions/deletions/migrations (500-only available; 400/600 DATA_BLOCKED)",
        sample_500_family, 15 / 252, "same short-horizon reversal assumption as deletion/addition alone",
        design["mda_pct"], "VIABLE" if design["viable"] else ("MARGINAL" if design["mda_pct"] <= 8 else "DEAD"),
        client,
    ))

    # --- Odd-lot tender provisions: DATA_BLOCKED, no tickers to sample ---
    results.append(CostScreenResult(
        candidate="Odd-lot tender provisions", holdingPeriodModel="N/A -- DATA_BLOCKED, no event population identified",
        holdingPeriodYears=None, sampleTickers=[], spreadMeasurement={"sampleSize": 0},
        mdaPct=None, breadthVerdict="DATA_BLOCKED",
    ))

    for r in results:
        r.spreadMeasurement["megaCapSanityCheck"] = sanity
    return results


def _markdown(results: list[CostScreenResult]) -> str:
    lines = [
        "# Cost screen across the hypothesis backlog (v2 -- dual estimator, holding-period model)",
        "",
        "## Estimator validation (mega-cap sanity check)",
        "",
        "Real spread on AAPL/MSFT is well-known to be ~0.5-2bps. Corwin-Schultz and "
        "Abdi-Ranaldo (2017, an independently-derived close/high/low estimator) are compared "
        "against this and against each other for every candidate below.",
        "",
    ]
    if results:
        sanity = results[0].spreadMeasurement.get("megaCapSanityCheck", {})
        lines.append("| Ticker | n | Corwin-Schultz median (bps) | Abdi-Ranaldo median (bps) |")
        lines.append("|---|---:|---:|---:|")
        for ticker, v in sanity.items():
            lines.append(f"| {ticker} | {v['n']} | {v['corwinSchultzMedianBps']:.2f} | {v['abdiRanaldoMedianBps']:.2f} |")
        lines.append("")
        lines.append(
            "Corwin-Schultz materially overstates spread on higher-volatility names (confirmed on AAPL: "
            "18.5bps vs. Abdi-Ranaldo's 0.0bps over the same year) -- both are reported per candidate below, "
            "never just one."
        )
    lines.extend([
        "",
        "## Backlog table",
        "",
        "| Candidate | Breadth verdict | MDA %/yr | n | CS spread (bps) | AR spread (bps) | Holding period | Annual cost CS/AR %/yr | Verdict |",
        "|---|---|---:|---:|---:|---:|---|---|---|",
    ])
    for r in results:
        mda = f"{r.mdaPct:.2f}" if r.mdaPct is not None else "—"
        cs = r.spreadMeasurement.get("corwinSchultzMedianBps")
        ar = r.spreadMeasurement.get("abdiRanaldoMedianBps")
        n = r.spreadMeasurement.get("sampleSize", 0)
        hp = f"{r.holdingPeriodYears:.2f}yr" if r.holdingPeriodYears else "—"
        cost = r.annualCostByEstimator
        cost_str = (f"{cost.get('corwinSchultz', float('nan')):.2f}/{cost.get('abdiRanaldo', float('nan')):.2f}"
                    if cost else "—")
        cs_str = f"{cs:.1f}" if cs is not None else "—"
        ar_str = f"{ar:.1f}" if ar is not None else "—"
        lines.append(
            f"| {r.candidate} | {r.breadthVerdict} | {mda} | {n} | {cs_str} | {ar_str} | "
            f"{hp} | {cost_str} | {r.verdict()} |"
        )
    lines.append("")
    for r in results:
        lines.extend([
            f"## {r.candidate}",
            "",
            f"- Holding period model: {r.holdingPeriodModel}",
            f"- Sample tickers (n={len(r.sampleTickers)}): {', '.join(r.sampleTickers) if r.sampleTickers else 'none'}",
            f"- Corwin-Schultz median: {r.spreadMeasurement.get('corwinSchultzMedianBps')} bps",
            f"- Abdi-Ranaldo median: {r.spreadMeasurement.get('abdiRanaldoMedianBps')} bps",
            f"- Annual cost by estimator: {r.annualCostByEstimator}",
            f"- Verdict: **{r.verdict()}**",
            "",
        ])
    return "\n".join(lines)


def write_report(results: list[CostScreenResult]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "report.json"
    md_path = REPORT_DIR / "report.md"
    json_path.write_text(json.dumps([
        {
            "candidate": r.candidate, "holdingPeriodModel": r.holdingPeriodModel,
            "holdingPeriodYears": r.holdingPeriodYears, "sampleTickers": r.sampleTickers,
            "spreadMeasurement": r.spreadMeasurement, "mdaPct": r.mdaPct, "breadthVerdict": r.breadthVerdict,
            "annualCostByEstimator": r.annualCostByEstimator, "verdict": r.verdict(),
        }
        for r in results
    ], indent=2), encoding="utf-8")
    md_path.write_text(_markdown(results), encoding="utf-8")
    return json_path, md_path


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()
    results = run_all(force_refresh=args.force_refresh)
    paths = write_report(results)
    print(_markdown(results))
    print(json.dumps({"reports": [str(p) for p in paths]}))


if __name__ == "__main__":
    main()
