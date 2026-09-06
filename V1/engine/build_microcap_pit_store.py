"""Builds the point-in-time micro-cap universe store for the preregistered
Sub-$300M-Cap Cross-Sectional Momentum v1 study
(research/microcap_momentum_v1_preregistration.json).

Populates data/pit_us_all_stocks/ against engine/pit_all_stocks.py's
existing contract -- reused unchanged, not redefined, per this session's
Phase 1 REUSE finding.

IDENTITY SAFETY MODEL (fail closed, confirmed necessary this session, not
assumed): ticker symbols are reused across unrelated companies (confirmed
empirically: `FB` is held by a different, currently-active entity after
Meta's 2022 rename -- Sharadar's per-ticker lookup returns whoever holds a
ticker NOW, with no way to retrieve a vacated ticker's prior holder) and
`tickerchangefrom`/`tickerchangeto` action rows are not always reliable
(confirmed: PNST's recorded 2024-01-02 change to BYN never took effect in
the real price feed -- PNST kept trading under its own ticker until its
actual 2025-03-05 delisting; BYN has zero price rows, ever). This module
NEVER stitches a security's history across a ticker change. Every
ticker-scoped price fetch is clamped to that exact ticker's own
[tickers.firstpricedate, tickers.lastpricedate] tenure. A security whose
ticker changes mid-formation-window is excluded from that month's ranking
(missing data, a conservative failure) rather than risking a silent splice
of two different companies' price series (confirmed as the correct
tradeoff with the user before this module was written).

`security_id` in this store is therefore `f"{ticker}@{tenure_start}"`, NOT
bare `permaticker` -- Sharadar's `stocks`/`daily` endpoints do not accept a
`permaticker` query parameter (confirmed: HTTP 400), so permaticker cannot
be used to fetch a cross-ticker price history even though the field exists
on `tickers` rows. This store does NOT claim the "permanent identifier,
never ticker" guarantee data/pit_us_all_stocks/README.md aspirationally
describes -- it is a disclosed, deliberate deviation, not an oversight.

PHASE 1 (this module, first pass): market-wide month-end marketcap
snapshots -> the eligible (bottom-quintile) ticker set at every historical
rebalance date, and a stability check on whether that population's size is
comparable across the full study period (the exact "not measured" caveat
`reports/hypothesis_backlog_screen/report.md` flagged and left open).
PHASE 2 (not yet built): per-ticker price/volume ingestion for the UNION of
every ticker that is ever eligible, clamped to tenure, for the 12-1 month
momentum signal and the 63-day liquidity floor.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import pandas as pd

from engine.sharadar_client import SharadarClient


ROOT = Path(__file__).resolve().parent.parent
STORE_DIR = ROOT / "data" / "pit_us_all_stocks"
UNIVERSE_SNAPSHOT_PATH = STORE_DIR / "monthly_universe_snapshots.parquet"
BUILD_LOG_PATH = STORE_DIR / "build_log_phase1.json"

BOTTOM_QUINTILE = 0.20


@dataclass(frozen=True)
class MonthEndSnapshot:
    date: str
    total_with_marketcap: int
    eligible_count: int
    eligible_marketcap_ceiling: float
    tickers: list[str]


def month_end_trading_dates(client: SharadarClient, start: str, end: str) -> list[str]:
    """Real month-end trading dates from an always-listed reference security's own calendar.

    Never assumes calendar-month-end -- e.g. the last calendar day of a
    month is frequently a weekend/holiday. AAPL is used only as a trading-
    calendar reference; its own price/marketcap data plays no other role.
    """
    result = client.query_all(
        "stocks", ticker="AAPL", **{"from": start, "to": end}, fields="ticker,date", sort="date.asc"
    )
    dates = pd.to_datetime([row["date"] for row in result.rows])
    frame = pd.Series(dates, index=dates)
    month_ends = frame.groupby([dates.year, dates.month]).max()
    return sorted(d.date().isoformat() for d in month_ends)


def fetch_month_end_snapshot(
    client: SharadarClient, date_str: str, *, force_refresh: bool = False
) -> MonthEndSnapshot:
    """One market-wide (ticker-less) daily.marketcap pull for a single date.

    Percentile-based, not a fixed nominal threshold -- recomputed fresh at
    every date from that date's own cross-section, per the preregistration's
    explicit rejection of a fixed $300M cutoff held constant since 1998.
    """
    result = client.query_all(
        "daily", date=date_str, fields="ticker,date,marketcap", force_refresh=force_refresh
    )
    have_cap = [
        row for row in result.rows if row.get("marketcap") not in (None, "N/A")
        and float(row["marketcap"]) > 0
    ]
    if not have_cap:
        return MonthEndSnapshot(date_str, 0, 0, 0.0, [])
    caps = sorted(have_cap, key=lambda row: float(row["marketcap"]))
    cutoff_index = max(1, int(len(caps) * BOTTOM_QUINTILE))
    eligible = caps[:cutoff_index]
    ceiling = float(eligible[-1]["marketcap"])
    return MonthEndSnapshot(
        date=date_str,
        total_with_marketcap=len(have_cap),
        eligible_count=len(eligible),
        eligible_marketcap_ceiling=ceiling,
        tickers=[row["ticker"] for row in eligible],
    )


def build_monthly_universe_history(
    start: str = "1998-01-01", end: str = "2026-09-05", *, force_refresh: bool = False
) -> pd.DataFrame:
    client = SharadarClient()
    trading_dates = month_end_trading_dates(client, start, end)
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for date_str in trading_dates:
        try:
            snapshot = fetch_month_end_snapshot(client, date_str, force_refresh=force_refresh)
        except Exception as exc:  # noqa: BLE001 -- one bad month must not abort the whole build
            errors.append(f"{date_str}: {type(exc).__name__}: {exc}")
            continue
        rows.append({
            "date": snapshot.date,
            "totalWithMarketcap": snapshot.total_with_marketcap,
            "eligibleCount": snapshot.eligible_count,
            "eligibleMarketcapCeiling": snapshot.eligible_marketcap_ceiling,
            "tickers": snapshot.tickers,
        })
    frame = pd.DataFrame(rows)
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    if not frame.empty:
        # tickers is a list column; parquet needs it serialized for the
        # compact on-disk form, but the returned in-memory frame keeps it
        # as a real list for immediate downstream use (Phase 2).
        to_write = frame.copy()
        to_write["tickers"] = to_write["tickers"].apply(json.dumps)
        to_write.to_parquet(UNIVERSE_SNAPSHOT_PATH, index=False)
    log = {
        "schemaVersion": 1,
        "generatedAt": pd.Timestamp.now(tz="UTC").isoformat(),
        "requestedWindow": [start, end],
        "monthsRequested": len(trading_dates),
        "monthsSucceeded": len(rows),
        "errors": errors,
        "bottomQuintileFraction": BOTTOM_QUINTILE,
    }
    BUILD_LOG_PATH.write_text(json.dumps(log, indent=2), encoding="utf-8")
    return frame


def stability_report(frame: pd.DataFrame) -> dict[str, Any]:
    """Answers the open caveat: is the eligible population's size stable across history?

    A percentile-based universe is stable BY CONSTRUCTION in eligibleCount
    relative to totalWithMarketcap (always ~20%), so the question that
    actually matters is whether totalWithMarketcap itself (the base the
    percentile is taken over) is stable, and whether the resulting absolute
    eligibleCount ever drops low enough to threaten the portfolio sizes
    (30/50) preregistered.
    """
    if frame.empty:
        return {"status": "no data"}
    years = pd.to_datetime(frame["date"]).dt.year
    by_year = frame.groupby(years).agg(
        meanTotal=("totalWithMarketcap", "mean"),
        meanEligible=("eligibleCount", "mean"),
        minEligible=("eligibleCount", "min"),
    )
    return {
        "overallMeanTotalWithMarketcap": float(frame["totalWithMarketcap"].mean()),
        "overallMeanEligibleCount": float(frame["eligibleCount"].mean()),
        "minEligibleCountAnyMonth": int(frame["eligibleCount"].min()),
        "minEligibleCountDate": frame.loc[frame["eligibleCount"].idxmin(), "date"],
        "belowPortfolioSizeThresholdMonths": {
            "below30": int((frame["eligibleCount"] < 30).sum()),
            "below50": int((frame["eligibleCount"] < 50).sum()),
        },
        "byYear": by_year.round(1).to_dict(orient="index"),
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="1998-01-01")
    parser.add_argument("--end", default="2026-09-05")
    parser.add_argument("--force-refresh", action="store_true")
    args = parser.parse_args()
    frame = build_monthly_universe_history(args.start, args.end, force_refresh=args.force_refresh)
    report = stability_report(frame)
    print(json.dumps({
        "monthsBuilt": len(frame),
        "storePath": str(UNIVERSE_SNAPSHOT_PATH),
        "logPath": str(BUILD_LOG_PATH),
        "stability": {k: v for k, v in report.items() if k != "byYear"},
    }, indent=2, default=str))


if __name__ == "__main__":
    main()
