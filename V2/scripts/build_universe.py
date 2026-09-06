"""Point-in-time US common-equity universe construction for V2."""
from __future__ import annotations

import sqlite3
from typing import Any

import numpy as np
import pandas as pd

EXCHANGES = ("NYSE", "NASDAQ", "NYSEMKT", "NYSEARCA", "BATS")
COLUMNS = ["ticker", "permaticker", "name", "exchange", "firstpricedate", "lastpricedate", "last_close", "median_dollar_volume"]


def _truthy_delisted(value: Any) -> bool:
    return str(value).strip().upper() in {"1", "Y", "YES", "TRUE"}


def get_universe(db_path: str, as_of: str) -> pd.DataFrame:
    """Return legitimately tradeable US common equities known at ``as_of``.

    Every price query is bounded by ``as_of``. Missing or ambiguous data is
    excluded. The returned frame has a ``funnel`` attribute for diagnostics.
    """
    as_of_ts = pd.Timestamp(as_of)
    if as_of_ts.tz is not None:
        raise ValueError("as_of must be a date without timezone")
    as_of_text = as_of_ts.strftime("%Y-%m-%d")
    con = sqlite3.connect(db_path)
    try:
        tickers = pd.read_sql_query(
            """SELECT ticker, permaticker, name, exchange, firstpricedate,
                      lastpricedate, isdelisted, category
               FROM tickers
               WHERE category = 'Domestic Common Stock'
                 AND exchange IN ('NYSE','NASDAQ','NYSEMKT','NYSEARCA','BATS')""", con
        )
        funnel = [{"stage": "started", "count": int(len(tickers)), "excluded": 0}]
        first = pd.to_datetime(tickers["firstpricedate"], errors="coerce")
        last = pd.to_datetime(tickers["lastpricedate"], errors="coerce")
        listing = first.notna() & (first <= as_of_ts) & (last.isna() | (last >= as_of_ts))
        tickers = tickers.loc[listing].copy()
        funnel.append({"stage": "listing_status", "count": int(len(tickers)), "excluded": int(listing.size - listing.sum())})
        if tickers.empty:
            out = pd.DataFrame(columns=COLUMNS); out.attrs["funnel"] = funnel; return out
        symbols = tickers["ticker"].tolist()
        placeholders = ",".join("?" for _ in symbols)
        prices = pd.read_sql_query(
            f"""SELECT ticker, date, closeunadj, volume FROM (
                    SELECT ticker, date, closeunadj, volume,
                           ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                    FROM stocks WHERE date <= ? AND ticker IN ({placeholders})
                ) WHERE rn <= 252""", con, params=[as_of_text, *symbols]
        )
    finally:
        con.close()
    prices["date"] = pd.to_datetime(prices.get("date"), errors="coerce")
    prices["closeunadj"] = pd.to_numeric(prices["closeunadj"], errors="coerce")
    prices["volume"] = pd.to_numeric(prices["volume"], errors="coerce")
    results = []
    price_excluded = history_excluded = 0
    for _, row in tickers.iterrows():
        p = prices[prices["ticker"] == row["ticker"]].sort_values("date")
        p = p.dropna(subset=["closeunadj", "volume"])
        if p.empty or float(p.iloc[-1]["closeunadj"]) < 1:
            price_excluded += 1; continue
        if len(p) < 63:
            history_excluded += 1; continue
        if len(p) < 252:
            history_excluded += 1; continue
        dollar = p.tail(63)["closeunadj"] * p.tail(63)["volume"]
        median_dv = float(np.median(dollar.to_numpy()))
        if median_dv < 500_000:
            price_excluded += 1; continue
        if _truthy_delisted(row["isdelisted"]) and pd.notna(row["lastpricedate"]) and str(row["lastpricedate"]) < as_of_text:
            history_excluded += 1; continue
        results.append({**{k: row[k] for k in COLUMNS[:6]}, "last_close": float(p.iloc[-1]["closeunadj"]), "median_dollar_volume": median_dv})
    funnel.extend([
        {"stage": "price_floor", "count": int(len(tickers) - price_excluded), "excluded": int(price_excluded)},
        {"stage": "liquidity_and_history", "count": int(len(results)), "excluded": int(len(tickers) - price_excluded - len(results))},
        {"stage": "identity", "count": int(len(results)), "excluded": 0},
    ])
    out = pd.DataFrame(results, columns=COLUMNS)
    out.attrs["funnel"] = funnel
    return out
