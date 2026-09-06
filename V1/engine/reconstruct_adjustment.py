"""Reusable, per-symbol total-return reconstruction from Sharadar closeunadj + actions.

Generalized from the original HON-only diagnostic
(``reports/sharadar_contract_validation/hon_reconstruction.json``), which
found HON's vendor ``closeadj`` diverging from the existing
``data/HON_1d.parquet`` (yfinance) cache by a 3.24% median / 3.70% max
absolute percentage difference. This module does not trust EITHER vendor's
own adjusted series for ANY symbol. It recomputes a symbol's total-return-
adjusted close independently from ``closeunadj`` (confirmed genuinely raw --
never split-adjusted, see the WMT split test in
``validate_sharadar_contract.py``) plus the ``actions`` table's own dated
``split``/``dividend``/``spinoffdividend`` events, using the same backward-
cascading method this project already validated for Yahoo data in
``engine/recover_dow_pit_extended_prices.py``. That turns a two-way "which
vendor do we believe" guess into a three-way check, for any symbol, not just
HON.

``run_panel`` is the permanent detector for this defect class: it reconstructs
every symbol in a panel and flags any security where the reconstruction and
the vendor's own ``closeadj`` disagree beyond a threshold, so a future
Dow-close cross-check discrepancy is never left as an unresolved "vintage
difference" guess when this cheaper, more precise check can resolve it.

Never writes to ``data/*.parquet`` or ``engine/data.py``'s cache. Evidence
only; does not touch a universe, strategy, or backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
from typing import Any

import pandas as pd

from engine.sharadar_client import SharadarClient


ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / "reports" / "sharadar_contract_validation" / "reconstruction"
DEFAULT_DISCREPANCY_THRESHOLD_PCT = 0.5


@dataclass(frozen=True)
class ReconstructionInputs:
    stocks_rows: list[dict[str, Any]]
    action_rows: list[dict[str, Any]]


def fetch_inputs(
    symbol: str, client: SharadarClient, *, force_refresh: bool = False
) -> ReconstructionInputs:
    end = date.today()
    start = end - timedelta(days=365 * 5 + 15)
    stocks = client.query_all(
        "stocks", ticker=symbol, **{"from": start.isoformat(), "to": end.isoformat()},
        fields="ticker,date,close,closeadj,closeunadj", sort="date.asc",
        force_refresh=force_refresh,
    )
    actions = client.query_all(
        "actions", ticker=symbol, **{"from": start.isoformat(), "to": end.isoformat()},
        sort="date.asc", force_refresh=force_refresh,
    )
    return ReconstructionInputs(stocks_rows=stocks.rows, action_rows=actions.rows)


def _extract_events(
    action_rows: list[dict[str, Any]],
) -> tuple[list[tuple[pd.Timestamp, float]], list[tuple[pd.Timestamp, float, str]]]:
    split_events: list[tuple[pd.Timestamp, float]] = []
    distribution_events: list[tuple[pd.Timestamp, float, str]] = []
    for row in action_rows:
        action = str(row.get("action", "")).lower()
        stamp = pd.Timestamp(row["date"])
        value = row.get("value")
        if value in (None, "N/A"):
            continue
        value = float(value)
        if action == "split":
            split_events.append((stamp, value))
        elif action in ("dividend", "spinoffdividend"):
            distribution_events.append((stamp, value, action))
    return split_events, distribution_events


def event_diagnostics(inputs: ReconstructionInputs) -> list[dict[str, Any]]:
    """Per-event predicted vs. ACTUAL closeadj/close jump, straight from Sharadar's own columns.

    Isolates which individual corporate action, if any, Sharadar's own
    ``closeadj`` computes differently from the split-adjusted-basis formula
    that matches every other event -- rather than only reporting an
    aggregate divergence that could be (wrongly) read as "many small
    disagreements spread evenly across five years."
    """
    frame = pd.DataFrame(inputs.stocks_rows)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.set_index("date").sort_index()
    for column in ("close", "closeadj", "closeunadj"):
        frame[column] = frame[column].astype(float)
    idx = frame.index

    split_events, distribution_events = _extract_events(inputs.action_rows)
    cumulative_split = pd.Series(1.0, index=idx)
    running_split = 1.0
    for event_date, ratio in sorted(split_events, key=lambda item: item[0], reverse=True):
        running_split *= ratio
        cumulative_split.loc[idx < event_date] = running_split
    split_adjusted_close = frame["closeunadj"] / cumulative_split
    adjustment_ratio = frame["closeadj"] / frame["close"]

    rows = []
    for event_date, amount, kind in sorted(distribution_events, key=lambda item: item[0]):
        before = frame.loc[idx < event_date]
        on_or_after = frame.loc[idx >= event_date]
        if before.empty or on_or_after.empty:
            continue
        prior_split_adjusted = float(split_adjusted_close.loc[idx < event_date].iloc[-1])
        predicted_jump = 1.0 / max(1e-9, 1.0 - amount / prior_split_adjusted)
        actual_jump = float(adjustment_ratio.loc[idx >= event_date].iloc[0]) / float(
            adjustment_ratio.loc[idx < event_date].iloc[-1]
        )
        rows.append({
            "date": event_date.date().isoformat(),
            "action": kind,
            "value": amount,
            "priorSplitAdjustedClose": round(prior_split_adjusted, 4),
            "predictedJump": round(predicted_jump, 6),
            "actualJump": round(actual_jump, 6),
            "residualPct": round((actual_jump / predicted_jump - 1.0) * 100.0, 4),
        })
    return rows


def reconstruct_adjusted_close(inputs: ReconstructionInputs) -> pd.DataFrame:
    """Back-adjust ``closeunadj`` using only dated ``actions`` rows.

    Two independent multiplicative cascades, both anchored to the RAW
    (never-adjusted) ``closeunadj`` price actually traded at each event's
    own date -- not to a partially-adjusted series -- because a split's
    share-count change and a dividend's/spinoff's declared dollar value are
    each economically invariant to the OTHER kind of future event:

    - SPLIT: ``value`` is the new-shares-per-old-share ratio (verified
      empirically against WMT's real 3-for-1: raw closeunadj fell to ~1/3,
      Sharadar's own split-adjusted `close` stayed continuous at
      raw/value). Every split dated after a given bar divides that bar's
      raw price by ``value`` to restate it on the current share basis.
    - DIVIDEND / SPINOFFDIVIDEND: ``value`` is a per-share dollar amount,
      applied against the SPLIT-ADJUSTED price on the prior session
      (``closeunadj`` divided by the cumulative split factor in force at
      that point), NOT the raw price. Verified empirically against every
      one of HON's 20 real distribution events (see
      ``tests/test_engine/test_sharadar_contract.py``): predicting each
      event's closeadj/close jump as 1 / (1 - value / split_adjusted_prior_close)
      matches the actual observed jump to within ~0.02% for ordinary
      dividends; the same prediction using raw closeunadj as the basis is
      off by roughly the full pre-split ratio for events dated before a
      later split. A bare ``spinoff`` action (share-ratio of the
      DISTRIBUTED security) is deliberately excluded here -- it says
      nothing about this security's own back-adjustment factor.
    """
    frame = pd.DataFrame(inputs.stocks_rows)
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.set_index("date").sort_index()
    frame["closeunadj"] = frame["closeunadj"].astype(float)
    idx = frame.index

    split_events, distribution_events = _extract_events(inputs.action_rows)

    cumulative_split = pd.Series(1.0, index=idx)
    running_split = 1.0
    split_log: list[dict[str, Any]] = []
    for event_date, ratio in sorted(split_events, key=lambda item: item[0], reverse=True):
        running_split *= ratio
        cumulative_split.loc[idx < event_date] = running_split
        split_log.append({"date": event_date.date().isoformat(), "ratio": ratio, "cumulativeAfter": running_split})

    # Basis for the dividend/spinoffdividend ratio: the split-adjusted price
    # (raw / cumulative split factor in force at that date), not the raw
    # price -- see the docstring's empirical verification against HON.
    split_adjusted_close = frame["closeunadj"] / cumulative_split

    cumulative_dividend = pd.Series(1.0, index=idx)
    running_dividend = 1.0
    dividend_log: list[dict[str, Any]] = []
    for event_date, amount, kind in sorted(distribution_events, key=lambda item: item[0], reverse=True):
        prior_mask = idx < event_date
        if not prior_mask.any():
            continue
        prior_close = float(split_adjusted_close.loc[prior_mask].iloc[-1])
        if prior_close <= 0:
            continue
        local_ratio = max(0.0, 1.0 - amount / prior_close)
        running_dividend *= local_ratio
        cumulative_dividend.loc[prior_mask] = running_dividend
        dividend_log.append({
            "date": event_date.date().isoformat(), "kind": kind, "value": amount,
            "priorSplitAdjustedClose": prior_close, "localRatio": local_ratio,
            "cumulativeAfter": running_dividend,
        })

    frame["reconstructedClose"] = split_adjusted_close * cumulative_dividend
    frame["cumulativeSplitFactor"] = cumulative_split
    frame["cumulativeDividendFactor"] = cumulative_dividend
    frame.attrs["splitLog"] = list(reversed(split_log))
    frame.attrs["dividendLog"] = list(reversed(dividend_log))
    return frame


def _pct_diff(a: pd.Series, b: pd.Series) -> pd.Series:
    return ((a - b).abs() / b.abs()) * 100.0


def compare_series(symbol: str, reconstructed: pd.DataFrame) -> dict[str, Any]:
    local_path = ROOT / "data" / f"{symbol}_1d.parquet"
    if not local_path.exists():
        return {"error": f"no local cache at {local_path.relative_to(ROOT)}"}
    local_frame = pd.read_parquet(local_path, columns=["Close"])
    local_frame.index = pd.to_datetime(local_frame.index, utc=True).tz_convert(None).normalize()
    local_close = local_frame["Close"].astype(float).rename("yfinanceLocal")

    sharadar_closeadj = reconstructed["closeadj"].astype(float).rename("sharadarCloseadj")
    reconstructed_close = reconstructed["reconstructedClose"].rename("reconstructed")

    joined = pd.concat([reconstructed_close, sharadar_closeadj, local_close], axis=1, join="inner").dropna()
    if joined.empty:
        return {"error": "no overlapping rows between reconstruction and local cache"}

    def summarize(a: str, b: str) -> dict[str, float]:
        diff = _pct_diff(joined[a], joined[b])
        return {
            "medianAbsPctDiff": float(diff.median()),
            "maxAbsPctDiff": float(diff.max()),
            "maxAbsPctDiffDate": diff.idxmax().date().isoformat(),
            "latestAbsPctDiff": float(diff.iloc[-1]),
        }

    # Windows around every split this reconstruction actually found for this
    # symbol -- evidence-derived, never a hardcoded per-symbol date list, so
    # this generalizes to any security without per-symbol maintenance.
    event_dates = [entry["date"] for entry in reconstructed.attrs.get("splitLog", [])]

    def around_events(dates: list[str]) -> dict[str, Any]:
        windows = {}
        for event_date in dates:
            center = pd.Timestamp(event_date)
            window = joined.loc[center - pd.Timedelta(days=5): center + pd.Timedelta(days=5)]
            windows[event_date] = {
                d.date().isoformat(): {
                    "reconstructed": round(float(row["reconstructed"]), 4),
                    "sharadarCloseadj": round(float(row["sharadarCloseadj"]), 4),
                    "yfinanceLocal": round(float(row["yfinanceLocal"]), 4),
                }
                for d, row in window.iterrows()
            }
        return windows

    return {
        "overlapRows": len(joined),
        "measuredStart": joined.index.min().date().isoformat(),
        "measuredEnd": joined.index.max().date().isoformat(),
        "reconstructedVsSharadarCloseadj": summarize("reconstructed", "sharadarCloseadj"),
        "reconstructedVsYfinanceLocal": summarize("reconstructed", "yfinanceLocal"),
        "sharadarCloseadjVsYfinanceLocal": summarize("sharadarCloseadj", "yfinanceLocal"),
        "aroundSplitEvents": around_events(event_dates),
    }


def build_report(symbol: str, *, client: SharadarClient | None = None, force_refresh: bool = False) -> dict[str, Any]:
    client = client or SharadarClient()
    inputs = fetch_inputs(symbol, client, force_refresh=force_refresh)
    if not inputs.stocks_rows:
        return {
            "schemaVersion": 1,
            "generatedAt": pd.Timestamp.now(tz="UTC").isoformat(),
            "evidenceOnly": True,
            "symbol": symbol,
            "error": "no stocks rows returned for this symbol",
        }
    reconstructed = reconstruct_adjusted_close(inputs)
    comparison = compare_series(symbol, reconstructed)
    diagnostics = event_diagnostics(inputs)
    worst = max(diagnostics, key=lambda row: abs(row["residualPct"])) if diagnostics else None
    reconstructed_vs_vendor = comparison.get("reconstructedVsSharadarCloseadj", {})
    vendor_agrees_with_reconstruction = (
        reconstructed_vs_vendor.get("medianAbsPctDiff", 0.0) <= DEFAULT_DISCREPANCY_THRESHOLD_PCT
    )
    report = {
        "schemaVersion": 1,
        "generatedAt": pd.Timestamp.now(tz="UTC").isoformat(),
        "evidenceOnly": True,
        "symbol": symbol,
        "method": (
            "splitAdjustedClose = closeunadj / cumulative_split_factor; reconstructedClose = "
            "splitAdjustedClose * cumulative_dividend_factor. Both factors are backward-cascaded from "
            "actions rows: split ratio directly; dividend/spinoffdividend dollar value divided by "
            "splitAdjustedClose (not raw closeunadj) on the prior trading session. "
            "Independent of Sharadar's own closeadj column throughout."
        ),
        "actionsUsed": {
            "splits": reconstructed.attrs["splitLog"],
            "distributions": reconstructed.attrs["dividendLog"],
        },
        "comparison": comparison,
        "eventDiagnostics": diagnostics,
        "worstEvent": worst,
        "vendorAgreesWithReconstruction": vendor_agrees_with_reconstruction,
        "discrepancyThresholdPct": DEFAULT_DISCREPANCY_THRESHOLD_PCT,
    }
    return report


def write_report(symbol: str, report: dict[str, Any], report_dir: Path = REPORT_DIR) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"{symbol}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def run_panel(
    symbols: list[str], *, force_refresh: bool = False, threshold: float = DEFAULT_DISCREPANCY_THRESHOLD_PCT
) -> dict[str, Any]:
    """The permanent detector: reconstruct every symbol in ``symbols`` and flag divergence.

    Not a re-implementation of ``validate_sharadar_contract.compare_dow_closes``
    -- that check compares vendor closeadj to the existing yfinance cache and
    names a discrepancy without explaining it. This function additionally
    computes a from-scratch reconstruction for each flagged symbol so the
    output states WHICH of vendor-closeadj or reconstructed-close matches the
    independent yfinance series, rather than leaving two disagreeing series
    with no resolution.
    """
    client = SharadarClient()
    results: dict[str, Any] = {}
    flagged: list[str] = []
    errors: list[str] = []
    for symbol in symbols:
        report = build_report(symbol, client=client, force_refresh=force_refresh)
        results[symbol] = report
        if report.get("error"):
            errors.append(f"{symbol}: {report['error']}")
            continue
        vendor_diff = report["comparison"].get("reconstructedVsSharadarCloseadj", {}).get("medianAbsPctDiff")
        if vendor_diff is not None and vendor_diff > threshold:
            flagged.append(symbol)
    return {
        "schemaVersion": 1,
        "generatedAt": pd.Timestamp.now(tz="UTC").isoformat(),
        "evidenceOnly": True,
        "symbols": symbols,
        "thresholdPct": threshold,
        "flaggedVendorCloseadjDivergesFromReconstruction": flagged,
        "errors": errors,
        "perSymbol": results,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append", dest="symbols", help="Symbol to reconstruct (repeatable)")
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument(
        "--threshold", type=float, default=DEFAULT_DISCREPANCY_THRESHOLD_PCT,
        help="Median abs pct diff above which vendor closeadj is flagged against the reconstruction",
    )
    args = parser.parse_args()
    symbols = args.symbols or ["HON"]
    panel = run_panel(symbols, force_refresh=args.force_refresh, threshold=args.threshold)
    paths = [str(write_report(symbol, panel["perSymbol"][symbol])) for symbol in symbols if not panel["perSymbol"][symbol].get("error")]
    panel_path = REPORT_DIR / "panel.json"
    panel_path.write_text(json.dumps(panel, indent=2), encoding="utf-8")
    print(json.dumps({
        "reports": paths,
        "panel": str(panel_path),
        "flagged": panel["flaggedVendorCloseadjDivergesFromReconstruction"],
        "errors": panel["errors"],
    }, indent=2))


if __name__ == "__main__":
    main()
