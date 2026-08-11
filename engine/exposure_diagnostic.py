"""Is Sharpe measuring strategy quality, or just measuring low exposure?

`engine/metrics.py:SHARPE_THRESHOLD` is 0.5, and it was chosen when nothing in
the project could reach it -- the best per-symbol Sharpe was -0.16, because
idle cash was charged a risk-free rate it was never credited. The threshold has
therefore never been calibrated against a metric that works. This prints the
four columns needed to decide what, if anything, it should become.

    Sharpe                   -- corrected (metrics_version 1)
    Exposure %               -- fraction of the CALENDAR spent at risk
    Invested days            -- absolute sample the Sharpe estimate rests on
    Exposure-weighted excess -- excess CAGR scaled to full deployment

Why exposure % and invested days are BOTH needed: they answer different
questions and are not interchangeable. Exposure says what fraction of the
window was risked; invested days says how much the estimate is worth. Over five
years, 10.9% exposure is ~137 invested days (a usable sample) while 1.6% is
~20 (not one). A minimum-invested-days rule -- the same shape as the existing
<30 trades rule, applied to the metric rather than the sample -- needs the
second, and having both makes the threshold fall out of the table instead of
being asserted.

Why the fourth column: once the risk-free rate nets out, the cash portion
contributes to neither the numerator nor the denominator of Sharpe, so Sharpe
becomes a statement about entry quality with NO reference to how often the
strategy enters. Connors at 1.49 says its ~11% of exposure was well chosen; it
says nothing about the other ~89% spent in T-bills, and structurally cannot.
Exposure-weighted excess return asks the question the shortlist actually cares
about -- what would this return if the idle fraction were deployed into more of
the same signal -- and is directly comparable to the benchmark's own excess.

Naive scaling, disclosed rather than hidden: it assumes the signal is available
at that scale and that returns are linear in deployment. Neither is true in
general (more capital chasing the same entries meets capacity limits, and
levering a mean-reversion book changes its risk profile). It is a screening
number for "could this possibly matter", not a projection.

Usage:
    python -m engine.exposure_diagnostic
"""

from __future__ import annotations

import sqlite3

from engine import data as data_module
from engine.logging_db import DB_PATH
from engine.metrics import SHARPE_THRESHOLD, UNRANKABLE_STATUSES
from engine.universe import SECTOR_BENCHMARK, daily_date_range

TRADING_DAYS_PER_YEAR = 252


def _benchmark_excess_cagr() -> tuple[float, float]:
    """(SPY excess CAGR %, rf %) over the canonical daily window."""
    start, end = daily_date_range()
    rf = data_module.risk_free_rate(start, end)
    bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", start, end)
    years = (bars.index[-1] - bars.index[0]).days / 365.25
    cagr = (bars["Close"].iloc[-1] / bars["Close"].iloc[0]) ** (1 / years) - 1
    return (cagr - rf) * 100.0, rf * 100.0


def main() -> int:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=8000")
    rows = conn.execute(
        """
        SELECT r.* FROM runs r
        INNER JOIN (
            SELECT strategy_name, MAX(run_at) AS latest
            FROM runs WHERE metrics_version = 1 AND is_canonical = 1
            GROUP BY strategy_name
        ) m ON r.strategy_name = m.strategy_name AND r.run_at = m.latest
        WHERE r.metrics_version = 1 AND r.is_canonical = 1
        """
    ).fetchall()
    conn.close()

    if not rows:
        print("No metrics_version=1 canonical rows yet -- run engine.backfill_metrics first.")
        return 1

    bench_excess, rf_pct = _benchmark_excess_cagr()
    print(f"rf = {rf_pct:.2f}%   SPY excess CAGR = {bench_excess:+.2f}%   "
          f"SHARPE_THRESHOLD = {SHARPE_THRESHOLD}\n")
    header = (
        f"{'strategy':32} {'Sharpe':>7} {'expo%':>7} {'inv.days':>9} "
        f"{'excess%':>8} {'wtd excess%':>12} {'trades':>7}"
    )
    print(header)
    print("-" * len(header))

    table = []
    for row in rows:
        if row["status"] in UNRANKABLE_STATUSES:
            continue
        expo = row["exposure_pct"]
        cagr = row["cagr_pct"]
        if expo is None or cagr is None:
            continue
        # Window length in trading days, from the row's own dates.
        span_days = None
        if row["start_date"] and row["end_date"]:
            from datetime import date

            d0 = date.fromisoformat(row["start_date"])
            d1 = date.fromisoformat(row["end_date"])
            span_days = (d1 - d0).days / 365.25 * TRADING_DAYS_PER_YEAR
        invested_days = (expo / 100.0) * span_days if span_days else float("nan")
        excess = cagr - rf_pct
        # Scaled to full deployment. Guarded: a near-zero exposure divides into
        # a meaningless multiple, and that row's Sharpe is untrustworthy anyway.
        weighted = excess / (expo / 100.0) if expo > 0.5 else float("nan")
        table.append((row["strategy_name"], row["sharpe"], expo, invested_days,
                      excess, weighted, row["trades_taken"]))

    for name, sharpe, expo, inv, excess, wtd, trades in sorted(table, key=lambda r: -(r[1] or -99)):
        flag = " <- clears gate" if sharpe is not None and sharpe > SHARPE_THRESHOLD else ""
        print(f"{name[:32]:32} {sharpe if sharpe is not None else float('nan'):7.2f} "
              f"{expo:7.1f} {inv:9.0f} {excess:+8.2f} {wtd:+12.2f} {trades:7d}{flag}")

    print(f"\nSPY excess CAGR for comparison: {bench_excess:+.2f}%")
    print("A weighted-excess column uniformly below that means the shortlist "
          "criterion needs replacing, not reshaping -- no gate geometry rescues "
          "a signal too weak to matter at full deployment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
