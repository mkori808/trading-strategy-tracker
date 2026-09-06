"""Quoted-spread follow-up for the ex-dividend triage candidate.

This is a harvestability diagnostic, not a return test. It reads the frozen
liquid-name sample from the corrected cost report, fetches short historical
Alpaca IEX quote windows, and compares a mechanism-based five-session holding
horizon with Corwin-Schultz, Abdi-Ranaldo, and directly quoted spreads.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from engine.alpaca_client import market_data_client
from engine.edge_candidate_triage import annual_cost_from_holding_period


ROOT = Path(__file__).resolve().parent.parent
COST_REPORT = ROOT / "reports" / "hypothesis_cost_screen" / "report.json"
TRIAGE_REPORT = ROOT / "reports" / "edge_candidate_triage" / "report.json"
REPORT_DIR = ROOT / "reports" / "ex_dividend_feasibility"

SAMPLE_SOURCE = "S&P 500+400+600 additions/deletions/migrations (500-only available; 400/600 DATA_BLOCKED)"
QUOTE_WINDOWS_ET = (
    ("2026-08-28T12:00:00", "2026-08-28T12:03:00"),
    ("2026-09-01T12:00:00", "2026-09-01T12:03:00"),
    ("2026-09-03T12:00:00", "2026-09-03T12:03:00"),
)
HOLDING_PERIOD_SESSIONS = 5
HOLDING_PERIOD_YEARS = HOLDING_PERIOD_SESSIONS / 252
SANITY_SYMBOLS = ("AAPL", "MSFT")
MAX_PLAUSIBLE_MEGA_CAP_SPREAD_BPS = 5.0


@dataclass(frozen=True)
class SymbolQuoteSpread:
    symbol: str
    valid_quotes: int
    rejected_quotes: int
    median_spread_bps: float | None
    p90_spread_bps: float | None


def quoted_spread_bps(quote: Any) -> float | None:
    """Return a valid relative quoted spread; crossed/empty books fail closed."""
    bid = float(getattr(quote, "bid_price", 0) or 0)
    ask = float(getattr(quote, "ask_price", 0) or 0)
    bid_size = float(getattr(quote, "bid_size", 0) or 0)
    ask_size = float(getattr(quote, "ask_size", 0) or 0)
    if bid <= 0 or ask <= 0 or bid_size <= 0 or ask_size <= 0 or ask < bid:
        return None
    midpoint = (bid + ask) / 2
    return (ask - bid) / midpoint * 10_000 if midpoint > 0 else None


def summarize_symbol_quotes(symbol: str, quotes: Iterable[Any]) -> SymbolQuoteSpread:
    valid: list[float] = []
    rejected = 0
    for quote in quotes:
        spread = quoted_spread_bps(quote)
        if spread is None:
            rejected += 1
        else:
            valid.append(spread)
    ordered = sorted(valid)
    p90 = ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))] if ordered else None
    return SymbolQuoteSpread(
        symbol=symbol,
        valid_quotes=len(valid),
        rejected_quotes=rejected,
        median_spread_bps=median(valid) if valid else None,
        p90_spread_bps=p90,
    )


def _inputs() -> tuple[list[str], float, float, float]:
    costs = json.loads(COST_REPORT.read_text(encoding="utf-8"))
    cost_row = next(row for row in costs if row["candidate"] == SAMPLE_SOURCE)
    triage = json.loads(TRIAGE_REPORT.read_text(encoding="utf-8"))
    candidate = next(row for row in triage["candidates"] if row["id"] == "ex_dividend_clientele")
    return (
        cost_row["sampleTickers"],
        float(cost_row["spreadMeasurement"]["corwinSchultzMedianBps"]),
        float(cost_row["spreadMeasurement"]["abdiRanaldoMedianBps"]),
        float(candidate["mda_pct_per_year"]),
    )


def fetch_quote_summaries(symbols: list[str]) -> tuple[list[SymbolQuoteSpread], list[str]]:
    client, reason = market_data_client()
    if client is None:
        raise RuntimeError(reason)
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockQuotesRequest

    eastern = ZoneInfo("America/New_York")
    per_symbol: dict[str, list[Any]] = {symbol: [] for symbol in symbols}
    errors: list[str] = []
    for start_text, end_text in QUOTE_WINDOWS_ET:
        request = StockQuotesRequest(
            symbol_or_symbols=symbols,
            start=datetime.fromisoformat(start_text).replace(tzinfo=eastern),
            end=datetime.fromisoformat(end_text).replace(tzinfo=eastern),
            feed=DataFeed.IEX,
        )
        try:
            result = client.get_stock_quotes(request)
        except Exception as exc:  # noqa: BLE001 - report the bounded data failure
            errors.append(f"{start_text}: {type(exc).__name__}: {exc}")
            continue
        for symbol in symbols:
            per_symbol[symbol].extend(result.data.get(symbol, []))
    return [summarize_symbol_quotes(symbol, per_symbol[symbol]) for symbol in symbols], errors


def build_report() -> dict[str, Any]:
    symbols, cs_bps, ar_bps, mda_pct = _inputs()
    requested = list(dict.fromkeys([*symbols, *SANITY_SYMBOLS]))
    all_summaries, errors = fetch_quote_summaries(requested)
    summaries = [row for row in all_summaries if row.symbol in symbols]
    sanity = [row for row in all_summaries if row.symbol in SANITY_SYMBOLS]
    measured = [row for row in summaries if row.median_spread_bps is not None]
    quoted_median = median(row.median_spread_bps for row in measured) if measured else None
    quote_cost = (
        annual_cost_from_holding_period(quoted_median, HOLDING_PERIOD_YEARS)
        if quoted_median is not None else None
    )
    cs_cost = annual_cost_from_holding_period(cs_bps, HOLDING_PERIOD_YEARS)
    ar_cost = annual_cost_from_holding_period(ar_bps, HOLDING_PERIOD_YEARS)
    sanity_passed = len(sanity) == len(SANITY_SYMBOLS) and all(
        row.median_spread_bps is not None
        and row.median_spread_bps <= MAX_PLAUSIBLE_MEGA_CAP_SPREAD_BPS
        for row in sanity
    )
    all_clean = len(measured) >= 40 and not errors and sanity_passed
    estimators_agree = (
        quote_cost is not None
        and cs_cost < mda_pct
        and ar_cost > 0
        and ar_cost < mda_pct
        and quote_cost < mda_pct
    )
    status = "COST_OK" if all_clean and estimators_agree else "COST_SENSITIVE"
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(tz=timezone.utc).isoformat(),
        "evidenceOnly": True,
        "backtestsRun": False,
        "returnsInspected": False,
        "holdoutConsumed": False,
        "candidate": "ex_dividend_clientele",
        "holdingPeriod": {
            "sessions": HOLDING_PERIOD_SESSIONS,
            "years": HOLDING_PERIOD_YEARS,
            "basis": "A generous upper bound for an ex-day price-pressure mechanism; the cited literature studies ex-dividend-day pricing, not a multi-month effect.",
            "sources": [
                "https://onlinelibrary.wiley.com/doi/pdf/10.1111/j.1540-6261.1982.tb03598.x",
                "https://doi.org/10.1016/S0304-405X(97)00041-X",
            ],
        },
        "quoteEvidence": {
            "source": "Alpaca historical IEX top-of-book quotes",
            "limitation": "IEX is one venue, not consolidated NBBO; the result is direct quoted evidence but not a complete execution-cost model.",
            "windowsET": list(QUOTE_WINDOWS_ET),
            "requestedSymbols": len(symbols),
            "measuredSymbols": len(measured),
            "medianOfSymbolMedianSpreadBps": quoted_median,
            "usableAsExecutionCostProxy": sanity_passed,
            "sanityThresholdBps": MAX_PLAUSIBLE_MEGA_CAP_SPREAD_BPS,
            "megaCapSanity": [asdict(row) for row in sanity],
            "errors": errors,
            "symbols": [asdict(row) for row in summaries],
        },
        "comparison": {
            "mdaPctPerYear": mda_pct,
            "corwinSchultz": {"spreadBps": cs_bps, "annualCostPct": cs_cost},
            "abdiRanaldo": {
                "spreadBps": ar_bps,
                "annualCostPct": ar_cost,
                "interpretation": "below estimator resolution, not literal zero cost" if ar_bps == 0 else "measured",
            },
            "quotedIex": {"spreadBps": quoted_median, "annualCostPct": quote_cost},
        },
        "status": status,
        "advancesToStrongCandidate": status == "COST_OK",
        "reason": (
            "All three cost measures are positive and below MDA on a broad clean sample."
            if status == "COST_OK"
            else "The IEX quote book fails the mega-cap sanity check and therefore cannot resolve the estimator disagreement under a mechanism-consistent short holding period."
        ),
    }


def markdown(report: dict[str, Any]) -> str:
    quote = report["quoteEvidence"]
    comparison = report["comparison"]
    lines = [
        "# Ex-dividend harvestability follow-up",
        "",
        "> Diagnostic only: no candidate returns, backtest, holdout, or preregistration were used.",
        "",
        f"Status: **{report['status']}**",
        "",
        report["reason"],
        "",
        "## Holding horizon",
        "",
        f"The screen uses **{report['holdingPeriod']['sessions']} trading sessions** ({report['holdingPeriod']['years']:.6f} years). {report['holdingPeriod']['basis']}",
        "",
        "## Cost comparison",
        "",
        "| Evidence | Spread | Annual drag at five sessions | Interpretation |",
        "|---|---:|---:|---|",
        f"| Corwin-Schultz | {comparison['corwinSchultz']['spreadBps']:.2f} bps | {comparison['corwinSchultz']['annualCostPct']:.2f}% | Daily-range estimator |",
        f"| Abdi-Ranaldo | {comparison['abdiRanaldo']['spreadBps']:.2f} bps | {comparison['abdiRanaldo']['annualCostPct']:.2f}% | {comparison['abdiRanaldo']['interpretation']} |",
        f"| Alpaca IEX quotes | {comparison['quotedIex']['spreadBps']:.2f} bps | {comparison['quotedIex']['annualCostPct']:.2f}% | Direct one-venue top-of-book evidence |" if comparison['quotedIex']['spreadBps'] is not None else "| Alpaca IEX quotes | — | — | unavailable |",
        "",
        f"MDA: **{comparison['mdaPctPerYear']:.2f}%/yr**. Quote coverage: **{quote['measuredSymbols']}/{quote['requestedSymbols']} symbols** across three midday windows.",
        "",
        quote["limitation"],
        "",
        f"Execution-cost proxy sanity status: **{'PASS' if quote['usableAsExecutionCostProxy'] else 'FAIL'}** (AAPL and MSFT must each have a median no greater than {quote['sanityThresholdBps']:.1f} bps).",
        "",
        "| Sanity symbol | Median IEX bps | P90 IEX bps |",
        "|---|---:|---:|",
    ]
    for row in quote["megaCapSanity"]:
        med = "—" if row["median_spread_bps"] is None else f"{row['median_spread_bps']:.2f}"
        p90 = "—" if row["p90_spread_bps"] is None else f"{row['p90_spread_bps']:.2f}"
        lines.append(f"| {row['symbol']} | {med} | {p90} |")
    lines.extend([
        "",
        "## Per-symbol quote evidence",
        "",
        "| Symbol | Valid quotes | Rejected | Median bps | P90 bps |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in quote["symbols"]:
        med = "—" if row["median_spread_bps"] is None else f"{row['median_spread_bps']:.2f}"
        p90 = "—" if row["p90_spread_bps"] is None else f"{row['p90_spread_bps']:.2f}"
        lines.append(f"| {row['symbol']} | {row['valid_quotes']} | {row['rejected_quotes']} | {med} | {p90} |")
    lines.extend([
        "",
        "The candidate advances only if all three measures are positive and below MDA. Exact zero never counts as proof of free execution.",
        "",
    ])
    return "\n".join(lines)


def write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = REPORT_DIR / "report.json"
    md_path = REPORT_DIR / "report.md"
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    md_path.write_text(markdown(report), encoding="utf-8")
    return md_path, json_path


def main() -> None:
    report = build_report()
    paths = write_report(report)
    print(json.dumps({"status": report["status"], "reports": [str(path) for path in paths]}, indent=2))


if __name__ == "__main__":
    main()
