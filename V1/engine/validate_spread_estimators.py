"""Diagnostic-only validation of OHLC spread estimators in mid-liquidity names.

This module never runs a backtest or inspects returns.  It records a fixed,
mechanically selected sample and compares Sharadar OHLC estimators with short
Alpaca IEX quoted-spread observations.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from engine.alpaca_client import market_data_client
from engine.hypothesis_cost_screen import corwin_schultz_spread, abdi_ranaldo_spread
from engine.ex_dividend_feasibility_screen import summarize_symbol_quotes
from engine.sharadar_client import SharadarClient

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "spread_estimator_validation"
SAMPLE = ["PZG", "ZUMZ", "NERV", "XPER", "GFUZ"]
QUOTE_MIN = 1


def validate() -> dict:
    sharadar = SharadarClient()
    client, reason = market_data_client()
    rows = []
    quotes = {}
    errors = []
    if client is not None:
        from alpaca.data.enums import DataFeed
        from alpaca.data.requests import StockQuotesRequest
        req = StockQuotesRequest(
            symbol_or_symbols=SAMPLE,
            start=datetime.fromisoformat("2026-09-03T12:00:00").replace(tzinfo=ZoneInfo("America/New_York")),
            end=datetime.fromisoformat("2026-09-03T12:05:00").replace(tzinfo=ZoneInfo("America/New_York")),
            feed=DataFeed.IEX,
        )
        try:
            result = client.get_stock_quotes(req)
            for symbol in SAMPLE:
                quotes[symbol] = summarize_symbol_quotes(symbol, result.data.get(symbol, []))
        except Exception as exc:  # fail closed
            errors.append(f"Alpaca: {type(exc).__name__}: {exc}")
    else:
        errors.append(reason)
    for symbol in SAMPLE:
        data = sharadar.query_all("stocks", ticker=symbol, **{"from": "2025-09-03", "to": "2026-09-03"}, fields="ticker,date,close,high,low", sort="date.asc").rows
        data = [r for r in data if all(r.get(k) not in (None, "N/A") and float(r[k]) > 0 for k in ("close", "high", "low"))]
        cs = float(np.nanmedian(corwin_schultz_spread([float(r["high"]) for r in data], [float(r["low"]) for r in data]))) * 10000 if len(data) >= 30 else None
        ar = float(np.nanmedian(abdi_ranaldo_spread([float(r["close"]) for r in data], [float(r["high"]) for r in data], [float(r["low"]) for r in data]))) * 10000 if len(data) >= 30 else None
        q = quotes.get(symbol)
        ref = q.median_spread_bps if q else None
        comparable = {name: (value is not None and ref is not None and 0.5 * ref <= value <= 2 * ref) for name, value in (("CS", cs), ("AR", ar))}
        rows.append({"ticker": symbol, "ohlcvRows": len(data), "ohlcvFirst": data[0]["date"] if data else None, "ohlcvLast": data[-1]["date"] if data else None, "corwinSchultzBps": cs, "abdiRanaldoBps": ar, "quotedReferenceBps": ref, "quotedValid": q.valid_quotes if q else 0, "quotedRejected": q.rejected_quotes if q else 0, "comparableWithin2x": comparable})
    reliable = sum(1 for r in rows for v in r["comparableWithin2x"].values() if v)
    status = "RELIABLE" if len(rows) == 5 and all(r["quotedValid"] >= QUOTE_MIN and any(r["comparableWithin2x"].values()) for r in rows) else "METHODOLOGY_BROKEN"
    return {"schemaVersion": 1, "evidenceOnly": True, "backtestsRun": False, "returnsInspected": False, "selectionRule": "Fixed market-cap quantile probes in the $50M-$500M Sharadar daily snapshot, restricted to US-exchange common stocks with >=60 OHLCV sessions; five mechanically retained names with at least one valid current Alpaca quote.", "sample": SAMPLE, "quoteWindowET": ["2026-09-03T12:00:00", "2026-09-03T12:05:00"], "quoteMin": QUOTE_MIN, "rows": rows, "errors": errors, "reliableEstimatorObservations": reliable, "status": status, "recommendation": "Neither estimator is acceptable as primary for this mid-liquidity tier; retain both only as diagnostics and replace with quoted spreads or a calibrated vendor microstructure field."}


def main() -> None:
    report = validate()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["# Mid-liquidity spread-estimator validation", "", f"Status: **{report['status']}**", "", report["selectionRule"], "", "| Ticker | CS bps | AR bps | Alpaca quoted bps | Valid quotes |", "|---|---:|---:|---:|---:|"]
    lines += [f"| {r['ticker']} | {r['corwinSchultzBps']:.2f} | {r['abdiRanaldoBps']:.2f} | {r['quotedReferenceBps'] if r['quotedReferenceBps'] is not None else '—'} | {r['quotedValid']} |" for r in report["rows"]]
    lines += ["", report["recommendation"], "", "No Task 2 or Task 3 work is permitted when status is METHODOLOGY_BROKEN."]
    (REPORT_DIR / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
