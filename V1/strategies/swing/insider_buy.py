"""Insider Buying -- Variant A (Significant Single Buy) and Variant B
(Cluster Buy), built entirely on the SEC EDGAR Form 4 purchase feed
(engine/data_edgar.py). Signal generation only; engine/insider_buy.py owns
entry/exit/sizing/simulation -- same split as strategies/swing/dividend_hybrid.py
and engine/dividend_hybrid.py.

Not a `strategies.base.Strategy`: the entry signal comes from an external
per-symbol event feed (Form 4 filings), not from `entry_signal(bars)`
computed off OHLCV, and the exit is a genuinely FIXED N-day hold, which
`exit_signal(bars)` has no way to express (it can't see how long a position
has been open -- see LESSONS.md's Turnaround Tuesday / PEAD entries for the
same wall). See engine/insider_buy.py's docstring for the full reasoning.

Variant A -- Significant Single Buy: one insider bought more than
`min_single_buy_value` in open-market shares in a single Form 4 FILING
(summed across every lot in that filing, not a single lot), OR increased
their position by more than `min_position_increase_pct` (computed from the
EARLIEST lot's pre-transaction holdings to the filing's total shares
bought -- the correct "before" baseline for a multi-lot filing).

Variant B -- Cluster Buy: `cluster_min_insiders` or more DISTINCT filer CIKs
each filed a qualifying purchase (any size) for the same issuer within a
`cluster_window_days`-calendar-day trailing window. Signal date is the
filed-at date of the filing that completes the cluster. Clusters are
resolved greedily and non-overlapping: once a cluster fires, its
constituent filings are cleared before scanning for the next one, rather
than letting one filing complete multiple overlapping clusters -- a
disclosed simplification the spec doesn't itself resolve.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import pandas as pd

from strategies.params import param_field

# --- sector pre-registration -------------------------------------------------
#
# TASK REQUIREMENT NOT YET SATISFIABLE. Both variants are specified to apply
# a pre-registered sector filter built from a Reddit post's binomial-
# significance results, chosen BEFORE running and never touched after seeing
# results (same anti-survivorship-bias discipline as engine/universe.py's
# fixed symbol lists, applied to sectors instead of symbols). That post's
# content was never provided in this session -- there is nothing real to
# pre-register from, and fabricating a plausible-looking sector list would
# be exactly the kind of "backfilled result" CLAUDE.md prohibits outright.
#
# PRE_REGISTERED_SECTORS is therefore explicitly None. `passes_sector_filter`
# below is a documented no-op (passes everything) until a real list is
# supplied -- not a silent stand-in for "no filter was ever specified" and
# not applied as a disguised pass-through in the report output (see
# engine/insider_buy.py, which prints a warning banner on every run while
# this is None).
#
# There is also no GICS sub-industry data source anywhere in this codebase
# yet (checked: engine/ has none) -- even with a real list, this can't be
# evaluated without one. Per the task's own instruction ("if GICS
# sub-industry data is not available for a symbol, exclude it rather than
# guessing"), that becomes this function's job once BOTH the list and a
# GICS data source exist.
PRE_REGISTERED_SECTORS: tuple[str, ...] | None = None


def passes_sector_filter(
    symbol: str, sectors: tuple[str, ...] | None = PRE_REGISTERED_SECTORS
) -> bool:
    return True


@dataclass
class InsiderBuy:
    name = "Insider Buying"
    timeframe = "1d"
    direction = "long"

    min_single_buy_value: float = param_field(
        1_000_000.0, label="Min single-filing purchase value ($)",
        minimum=100_000.0, maximum=10_000_000.0, step=100_000.0,
        help="Variant A: total $ value across every lot in one Form 4 filing.",
    )
    min_position_increase_pct: float = param_field(
        10.0, label="Min position increase (%)", minimum=1.0, maximum=50.0, step=1.0,
        help="Variant A: filing's total shares bought vs. shares held before the filing's earliest lot.",
    )
    cluster_min_insiders: int = param_field(
        3, label="Cluster: min distinct insiders", minimum=2, maximum=10, step=1,
    )
    cluster_window_days: int = param_field(
        5, label="Cluster window (calendar days)", minimum=1, maximum=30, step=1,
    )
    hold_days: int = param_field(
        5, label="Fixed holding period (trading days)", minimum=1, maximum=30, step=1,
    )
    stop_pct: float = param_field(
        8.0, label="Hard stop (% below entry)", minimum=1.0, maximum=30.0, step=1.0,
    )
    risk_pct_per_trade: float = param_field(
        1.0, label="Risk per trade (% of equity)", minimum=0.1, maximum=5.0, step=0.1,
    )
    min_dollar_volume: float = param_field(
        5_000_000.0, label="Min avg daily dollar volume ($)",
        minimum=0.0, maximum=50_000_000.0, step=1_000_000.0,
    )
    min_price: float = param_field(
        5.0, label="Min price ($)", minimum=1.0, maximum=50.0, step=1.0,
    )


_FILING_COLUMNS = [
    "accession_no", "issuer_ticker", "filer_cik", "filer_name", "filed_at",
    "signal_date", "earliest_transaction_date", "total_value", "total_shares",
    "pct_change_holdings",
]


def filing_level_purchases(
    conn: sqlite3.Connection, symbols: list[str], start, end
) -> pd.DataFrame:
    """One row per qualifying Form 4 FILING (not per transaction lot),
    aggregated from engine.data_edgar's per-lot rows -- see module docstring
    for why this must aggregate rather than treat each lot as its own
    filing-level event."""
    if not symbols:
        return pd.DataFrame(columns=_FILING_COLUMNS)
    placeholders = ",".join("?" for _ in symbols)
    query = f"""
        SELECT accession_no, issuer_ticker, filer_cik, filer_name, filed_at,
               signal_date, transaction_date, shares_transacted, price_per_share,
               transaction_value, shares_owned_before
        FROM form4_transactions
        WHERE issuer_ticker IN ({placeholders}) AND signal_date BETWEEN ? AND ?
        ORDER BY issuer_ticker, accession_no, transaction_date
    """
    df = pd.read_sql_query(
        query, conn, params=[*symbols, start.isoformat(), end.isoformat()]
    )
    if df.empty:
        return pd.DataFrame(columns=_FILING_COLUMNS)

    df["transaction_date"] = pd.to_datetime(df["transaction_date"])
    rows = []
    for accession_no, g in df.sort_values("transaction_date").groupby("accession_no"):
        first = g.iloc[0]
        total_value = float(g["transaction_value"].sum())
        total_shares = float(g["shares_transacted"].sum())
        before = first["shares_owned_before"]
        pct_change = (total_shares / before * 100) if before and before > 0 else None
        rows.append({
            "accession_no": accession_no,
            "issuer_ticker": first["issuer_ticker"],
            "filer_cik": first["filer_cik"],
            "filer_name": first["filer_name"],
            "filed_at": first["filed_at"],
            "signal_date": first["signal_date"],
            "earliest_transaction_date": first["transaction_date"].date(),
            "total_value": total_value,
            "total_shares": total_shares,
            "pct_change_holdings": pct_change,
        })
    return pd.DataFrame(rows, columns=_FILING_COLUMNS)


def variant_a_signals(filing_df: pd.DataFrame, config: InsiderBuy) -> pd.DataFrame:
    if filing_df.empty:
        return filing_df
    qualifies = (filing_df["total_value"] >= config.min_single_buy_value) | (
        filing_df["pct_change_holdings"].fillna(0) >= config.min_position_increase_pct
    )
    return filing_df[qualifies].copy()


def variant_b_signals(filing_df: pd.DataFrame, config: InsiderBuy) -> pd.DataFrame:
    """See module docstring for the greedy, non-overlapping cluster
    resolution. Every qualifying filing (any size -- no $/% threshold for
    Variant B) counts toward a cluster regardless of whether it would also
    qualify for Variant A."""
    columns = [
        "issuer_ticker", "signal_date", "completing_accession_no",
        "cluster_size", "cluster_filer_ciks", "filed_at", "earliest_transaction_date",
    ]
    if filing_df.empty:
        return pd.DataFrame(columns=columns)

    working = filing_df.copy()
    working["signal_date"] = pd.to_datetime(working["signal_date"])

    out = []
    for ticker, g in working.sort_values("signal_date").groupby("issuer_ticker"):
        pending: list[pd.Series] = []
        for _, row in g.iterrows():
            pending.append(row)
            window_floor = row["signal_date"] - pd.Timedelta(days=config.cluster_window_days - 1)
            pending = [r for r in pending if r["signal_date"] >= window_floor]
            distinct_filers = sorted({r["filer_cik"] for r in pending})
            if len(distinct_filers) >= config.cluster_min_insiders:
                out.append({
                    "issuer_ticker": ticker,
                    "signal_date": row["signal_date"].date(),
                    "completing_accession_no": row["accession_no"],
                    "cluster_size": len(distinct_filers),
                    "cluster_filer_ciks": distinct_filers,
                    # The COMPLETING filing's own filed_at/transaction_date --
                    # the signal is dated to this filing, so its filing lag
                    # (not the earlier cluster members') is the relevant one.
                    "filed_at": row["filed_at"],
                    "earliest_transaction_date": row["earliest_transaction_date"],
                })
                pending = []  # greedy reset -- see module docstring
    return pd.DataFrame(out, columns=columns)
