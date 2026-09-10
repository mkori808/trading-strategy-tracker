"""Point-in-time net-share-issuance portfolios (standalone V2 module)."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import sqlite3
import numpy as np
import pandas as pd
from pathlib import Path

import sys
sys.path.append(str(Path(__file__).parent.parent / 'scripts'))
sys.path.append(str(Path(__file__).parent.parent))
from utils.research_utils import (
    load_pit_universe_snapshots, universe_as_of, get_full_pit_universe,
    load_universe_candidates, load_price_panel, load_fundamentals_panel, load_marketcap_panel,
    panel_universe_as_of, panel_latest_asof, panel_marketcaps_asof, panel_month_values,
    size_tercile_top_returns, size_tercile_breakdown, winsorize_by_group, long_short_spread_regression,
    factor_regression, size_controlled_quintile_spread, within_size_analysis,
)

# Legacy fixed roster -- kept only so callers can still request it explicitly
# via symbols=DOW_ROSTER. No longer the default: using today's Dow-30 list as
# a static universe across 2000-2024 is survivorship/hindsight bias (several
# of these names, e.g. CRM, V, DOW, did not exist as public companies for
# large parts of that period). The default universe is the full point-in-time
# Sharadar tradeable universe -- the S&P 500 was also tried and rejected as a
# default: it's the most efficiently-priced, most heavily-covered slice of
# the market, which is the wrong place to test a capacity-barrier thesis that
# predicts the effect lives where institutional capital structurally can't
# follow.
DOW_ROSTER = [
    "MMM", "GS", "NKE", "AXP", "HD", "PG", "AMGN", "HON", "CRM", "AAPL",
    "INTC", "TRV", "BA", "IBM", "UNH", "CAT", "JNJ", "VZ", "CVX", "JPM",
    "V", "CSCO", "MCD", "KO", "MRK", "WMT", "DOW", "MSFT", "DIS",
]

@dataclass
class StrategyResult:
    monthly_returns: pd.Series
    factor_returns: pd.DataFrame
    alpha_annual: float
    alpha_tstat: float
    alpha_pvalue: float
    betas: dict
    r_squared: float
    n_months: int
    portfolio_history: pd.DataFrame
    quintile_returns: pd.DataFrame
    size_terciles: dict | None = None
    avg_universe_size: float = 0.0
    long_short_alpha: float = float('nan')
    long_short_tstat: float = float('nan')
    long_short_rsquared: float = float('nan')
    long_short_betas: dict | None = None
    within_size: dict | None = None

def _months(start, end):
    return pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="ME")

def run(db_path: str, as_of_start: str, as_of_end: str, symbols=None, winsorize: bool = True) -> StrategyResult:
    con=sqlite3.connect(db_path)
    months=_months(as_of_start,as_of_end)

    # Panel loading: a handful of bulk queries covering the whole date range,
    # instead of one query per rebalance month re-scanning that ticker's
    # whole accumulated history every time (the actual bottleneck -- SQLite
    # was already using its (ticker,date) indexes correctly; the cost was
    # 300 separate round-trips per strategy with heavily overlapping data
    # re-fetched every month, not missing indexes or an unbounded scan).
    if symbols is not None:
        universe_tickers = list(symbols); candidates = None
    else:
        candidates = load_universe_candidates(con)
        # Pre-filter to tickers that could plausibly be eligible somewhere in
        # [as_of_start, as_of_end] before the panel load, not just per-month:
        # a ticker's IN-clause cost in SQLite is paid per name regardless of
        # whether it has any rows in the date window, so the full ~11k-name
        # candidate list (most never listed/already delisted for this range)
        # was the dominant cost of the one-time load, not the date span.
        window_start = pd.Timestamp(as_of_start) - pd.Timedelta(days=400)
        window_end = pd.Timestamp(as_of_end)
        candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
        universe_tickers = candidates.ticker.tolist()
    load_start = (months.min() - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    load_end = (months.max() + pd.offsets.MonthEnd(1)).strftime("%Y-%m-%d")
    price_panel = load_price_panel(con, universe_tickers, load_start, load_end)
    # Fundamentals panel must reach back further than as_of_start: the
    # signal needs the sharesbas value from ~1 year before *each* rebalance,
    # including the very first one in the range.
    fund_panel = load_fundamentals_panel(con, universe_tickers, load_start, load_end, ["sharesbas"], dimensions=("MRQ",))
    mc_panel = load_marketcap_panel(con, universe_tickers, as_of_start, load_end) if symbols is None else pd.DataFrame(columns=["ticker","date","marketcap"])
    con.close()

    qrows=[]; history=[]; universe_sizes=[]; small_rows=[]; mid_rows=[]; large_rows=[]
    sc_small_q1=[]; sc_small_q5=[]; sc_small_n=[]; sc_mid_q1=[]; sc_mid_q5=[]; sc_mid_n=[]; sc_large_q1=[]; sc_large_q5=[]; sc_large_n=[]
    for rebalance in months:
        syms = list(symbols) if symbols is not None else panel_universe_as_of(price_panel, candidates, rebalance)
        if not syms: continue
        universe_sizes.append(len(syms))
        cutoff = rebalance - pd.Timedelta(days=365)
        now_df = panel_latest_asof(fund_panel, rebalance, dimension_priority=("MRQ",))
        old_df = panel_latest_asof(fund_panel, cutoff, dimension_priority=("MRQ",))
        if now_df.empty or old_df.empty: continue
        now_s = now_df.set_index("ticker")["sharesbas"]; old_s = old_df.set_index("ticker")["sharesbas"]
        idx = pd.Index(syms).intersection(now_s.index).intersection(old_s.index)
        now_v = now_s.reindex(idx); old_v = old_s.reindex(idx)
        valid = idx[old_v.notna() & (old_v != 0) & now_v.notna()]
        if len(valid) < 5: continue
        signal = (now_v.reindex(valid) - old_v.reindex(valid)) / old_v.reindex(valid)
        s = signal.rename("signal").rename_axis("ticker").reset_index().sort_values("signal").reset_index(drop=True)
        s["q"] = (np.floor(np.arange(len(s))*5/len(s))+1).astype(int)
        # Widen the lower bound by a week: rebalance is a calendar month-end
        # from freq='ME' and is often a weekend/holiday, not a trading day.
        # Using the panel's own month-Period bucketing (already computed
        # once at load time) sidesteps the issue entirely: .last() within
        # the correct calendar month finds the true last trading day
        # regardless of whether `rebalance` itself was one.
        cur_vals = panel_month_values(price_panel, rebalance.to_period("M"))
        next_vals = panel_month_values(price_panel, (rebalance+pd.offsets.MonthEnd(1)).to_period("M"))
        s["a"] = s.ticker.map(cur_vals); s["b"] = s.ticker.map(next_vals)
        s = s.dropna(subset=["a","b"])
        if s.empty: continue
        s["return"] = s.b/s.a - 1
        s["weight"] = 1/s.groupby("q")["ticker"].transform("count")
        # `return` here is the price change from rebalance to rebalance+1mo
        # (formation month to realization month) -- it belongs to the
        # REALIZATION month for any comparison against calendar-dated
        # factor returns. Labeling it with `rebalance` (the formation
        # month) instead was a real bug: it shifted the whole portfolio
        # series one month early relative to the Fama-French factors,
        # which regresses month t+1's stock returns against month t's
        # market return. Confirmed empirically -- shifting the computed
        # series forward one month took its correlation with the market
        # from 0.05 to 0.91, exactly what a diversified long-only equity
        # portfolio should show. This affected every factor-regression
        # output (alpha, t-stat, R-squared, all factor loadings) computed
        # this session; it did NOT affect the quintile spread itself,
        # which is a same-month relative comparison.
        realization_month = rebalance + pd.offsets.MonthEnd(1)
        gdf = s[["ticker","q","signal","return","weight"]].copy(); gdf["date"] = realization_month
        if winsorize:
            gdf = winsorize_by_group(gdf, "return", "date")
        history.extend(gdf.to_dict("records"))
        qrows.append(gdf.groupby("q")["return"].mean().rename(realization_month))
        if symbols is None:
            # Size tercile breakdown: top signal quintile (lowest net share
            # issuance, i.e. repurchasers, since ascending=True) within each
            # of Small/Mid/Large market-cap terciles -- tests whether the
            # effect concentrates where institutional capital can't crowd
            # it away.
            mktcaps = panel_marketcaps_asof(mc_panel, gdf.ticker.tolist(), rebalance)
            gdf["mktcap"] = gdf.ticker.map(mktcaps)
            tercile_rets = size_tercile_top_returns(gdf, signal_col="signal", return_col="return", mktcap_col="mktcap", ascending=True)
            small_rows.append((realization_month, tercile_rets["Small"])); mid_rows.append((realization_month, tercile_rets["Mid"])); large_rows.append((realization_month, tercile_rets["Large"]))
            # Size-controlled test: re-rank into quintiles *within* each
            # size tercile rather than just taking the top bucket. The raw
            # cross-sectional spread carries real SMB exposure (-0.47), so
            # part of its long-short alpha could just be a size tilt --
            # this asks whether the signal still separates winners from
            # losers once size is held fixed.
            sc = size_controlled_quintile_spread(gdf, signal_col="signal", return_col="return", mktcap_col="mktcap", ascending=True, min_per_quintile=10)
            sc_small_q1.append((realization_month, sc["Small"]["q1"])); sc_small_q5.append((realization_month, sc["Small"]["q5"])); sc_small_n.append((realization_month, sc["Small"]["n"]))
            sc_mid_q1.append((realization_month, sc["Mid"]["q1"])); sc_mid_q5.append((realization_month, sc["Mid"]["q5"])); sc_mid_n.append((realization_month, sc["Mid"]["n"]))
            sc_large_q1.append((realization_month, sc["Large"]["q1"])); sc_large_q5.append((realization_month, sc["Large"]["q5"])); sc_large_n.append((realization_month, sc["Large"]["n"]))
    quint=pd.DataFrame(qrows); quint.columns=[f"Q{i}" for i in range(1,6)] if len(quint.columns)==5 else quint.columns
    monthly=quint.get("Q1",pd.Series(dtype=float)); factors,aa,ts,pv,betas,rsq=factor_regression(monthly,as_of_start)
    small_s = pd.Series(dict(small_rows)); mid_s = pd.Series(dict(mid_rows)); large_s = pd.Series(dict(large_rows))
    size_terciles = size_tercile_breakdown({"Small": small_s, "Mid": mid_s, "Large": large_s}, as_of_start)
    avg_universe_size = float(np.mean(universe_sizes)) if universe_sizes else 0.0
    # Q1-minus-Q5 long-short spread: the standard academic test for these
    # anomalies. A long-only leg's alpha can just reflect average factor
    # exposure -- as this signal's long-only leg turned out to (0.6%/yr,
    # t=0.67 once the date-labeling bug was fixed). The spread, which
    # cancels common exposure, is the more decisive test of whether the
    # signal carries genuine information: it does (10.5%/yr, t=5.26).
    try:
        _, _, ls_alpha, ls_t, _, ls_betas, ls_r2 = long_short_spread_regression(quint, as_of_start)
    except Exception:
        ls_alpha, ls_t, ls_r2, ls_betas = np.nan, np.nan, np.nan, {}
    within_size = within_size_analysis(
        {"Small": pd.Series(dict(sc_small_q1)), "Mid": pd.Series(dict(sc_mid_q1)), "Large": pd.Series(dict(sc_large_q1))},
        {"Small": pd.Series(dict(sc_small_q5)), "Mid": pd.Series(dict(sc_mid_q5)), "Large": pd.Series(dict(sc_large_q5))},
        {"Small": pd.Series(dict(sc_small_n)), "Mid": pd.Series(dict(sc_mid_n)), "Large": pd.Series(dict(sc_large_n))},
        as_of_start,
    ) if symbols is None else None
    return StrategyResult(monthly,factors,aa,ts,pv,betas,rsq,len(monthly),pd.DataFrame(history),quint,size_terciles,avg_universe_size,ls_alpha,ls_t,ls_r2,ls_betas,within_size)

if __name__=="__main__":
    r=run("data/sharadar.db","2000-01-01","2024-12-31"); print("NET SHARE ISSUANCE — FULL UNIVERSE"); print(f"Period: {r.monthly_returns.index.min()} to {r.monthly_returns.index.max()} ({r.n_months} months)"); print(f"Residual alpha: {r.alpha_annual:.2%}/yr  t={r.alpha_tstat:.2f}  p={r.alpha_pvalue:.3f}")
