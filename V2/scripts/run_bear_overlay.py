"""Bear overlay (bear_overlay_v1): adds S1 Pullback-to-21EMA as a fifth
signal during BEAR+HIGH_VOL weeks on top of V3.2 (V3.1 minus Sector RS).

See research/preregistrations/bear_overlay_v1.json for the locked spec.
Diagnostic checks run first; full evaluation only proceeds if they pass.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
if str(_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORT))

from strategies.composite_v2 import _nsi_signal, _quality_score, _quintile
from strategies.momentum import _weekly_dates
from strategies.quality import _FUND_COLS
from strategies.signal_library import build_eligibility, load_full_ohlc_panel, _rsi, _wide
from utils.research_utils import (
    load_fundamentals_panel, load_marketcap_panel, load_universe_candidates,
)
from scripts.run_negative_selection_audit import load_local_ff5_umd_daily, load_local_spy_daily
from scripts.run_v31_regime_decomposition import build_regime_calendar, build_earnings_flag

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_FAMA_SECTORS = ["Financial Services", "Real Estate", "Healthcare"]
SUBPERIODS = [("2001-2008", "2001-01-01", "2008-12-31"), ("2009-2016", "2009-01-01", "2016-12-31"), ("2017-2024", "2017-01-01", "2024-12-31")]

W_OFF = {"ibs": 0.69, "rsi2": 0.25, "tue": 0.06, "s1": 0.00}
W_ON = {"ibs": 0.55, "rsi2": 0.20, "tue": 0.05, "s1": 0.20}


def build_high_vol_calendar(spy_daily: pd.Series, ref_index: pd.DatetimeIndex) -> pd.Series:
    """Same 20d/252w PIT-safe methodology as v33_vol_filter_v1, returns bool HIGH_VOL flag."""
    price = (1 + spy_daily.fillna(0)).cumprod()
    price = price.reindex(ref_index.union(price.index)).sort_index().ffill().reindex(ref_index)
    daily_all = spy_daily.reindex(price.index)
    vol20_daily = daily_all.rolling(20, min_periods=20).std() * np.sqrt(252)
    vol20_weekly = vol20_daily.reindex(ref_index)
    vol_75th = vol20_weekly.rolling(252, min_periods=252).apply(lambda x: np.percentile(x, 75), raw=True)
    high_vol = vol20_weekly > vol_75th
    high_vol[vol_75th.isna()] = False
    return high_vol, vol20_weekly, vol_75th


def run(db_path: str, start: str, end: str) -> dict:
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    con = sqlite3.connect(db_path)
    candidates = load_universe_candidates(con, exclude_sectors=[], exclude_fama_sectors=EXCLUDE_FAMA_SECTORS)
    load_start = (start_ts - pd.Timedelta(days=500)).strftime("%Y-%m-%d")
    window_start, window_end = pd.Timestamp(load_start), end_ts
    candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
    universe_tickers = candidates.ticker.tolist()
    print(f"Universe candidates: {len(universe_tickers)}")

    px = load_full_ohlc_panel(con, universe_tickers, load_start, end)
    shares_panel = load_fundamentals_panel(con, universe_tickers, load_start, end, ["sharesbas"], dimensions=("MRQ",))
    qual_panel = load_fundamentals_panel(con, universe_tickers, load_start, end, _FUND_COLS)
    mc_panel = load_marketcap_panel(con, universe_tickers, start, end)
    print("Panels loaded.")

    O, H, L, C, V = (_wide(px, c) for c in ("open", "high", "low", "close", "volume"))
    eligible = build_eligibility(O, H, L, C, V, candidates)
    sector_of = candidates.set_index("ticker")["sector"]
    earnings_flag = build_earnings_flag(con, universe_tickers, load_start, end, C.index, C.columns)
    con.close()
    print("Building signal panels...")

    ibs_avg5 = ((C - L) / (H - L).replace(0, np.nan)).rolling(5, min_periods=5).mean()
    raw_ibs = 1 - ibs_avg5
    rsi2 = _rsi(C, 2)
    raw_rsi2 = 100 - rsi2
    friday_ret = C / C.shift(1) - 1
    ema21 = C.ewm(span=21, adjust=False).mean()
    raw_s1 = -(C - ema21) / ema21

    spy_daily = load_local_spy_daily()
    regime_by_date = build_regime_calendar(spy_daily, C.index)
    high_vol_by_date, vol20_by_date, vol75_by_date = build_high_vol_calendar(spy_daily, C.index)

    all_dates = pd.DatetimeIndex(sorted(px.date.unique()))
    date_pos = {d: i for i, d in enumerate(C.index)}
    rebalance_dates = _weekly_dates(all_dates, start_ts, end_ts)
    factors_daily = load_local_ff5_umd_daily()

    rows = []
    weekly_corr_rows = []
    cached_month = None
    cached_excluded = set()

    for i, rd in enumerate(rebalance_dates):
        if rd not in date_pos or date_pos[rd] < 200:
            continue
        fut_r = [d for d in rebalance_dates if d > rd]
        if not fut_r:
            continue
        rd_next = fut_r[0]
        after_rd = [d for d in C.index if d > rd]
        after_rd_next = [d for d in C.index if d > rd_next]
        if not after_rd or not after_rd_next:
            continue
        entry_date, exit_date = after_rd[0], after_rd_next[0]

        month_key = (rd.year, rd.month)
        if month_key != cached_month:
            syms_month = list(eligible.columns[eligible.loc[rd]]) if rd in eligible.index else []
            nsi = _nsi_signal(shares_panel, rd, syms_month)
            nsi_excl = set(_quintile(nsi, ascending=True).pipe(lambda q: q.index[q == 5])) if len(nsi) >= 5 else set()
            qual = _quality_score(qual_panel, rd, syms_month)
            qual_excl = set(_quintile(qual, ascending=False).pipe(lambda q: q.index[q == 5])) if len(qual) >= 5 else set()
            cached_excluded = nsi_excl | qual_excl
            cached_month = month_key

        syms_today = list(eligible.columns[eligible.loc[rd]]) if rd in eligible.index else []
        syms = [s for s in syms_today if s not in cached_excluded]
        if len(syms) < 25:
            continue

        a = raw_ibs.loc[rd].reindex(syms)
        b = raw_rsi2.loc[rd].reindex(syms)
        s1raw = raw_s1.loc[rd].reindex(syms)
        d = (friday_ret.loc[rd].reindex(syms) < -0.01).astype(float)
        earn_flag = earnings_flag.loc[rd].reindex(syms).fillna(False)

        df = pd.DataFrame({"ibs_raw": a, "rsi2_raw": b, "s1_raw": s1raw, "tue": d, "earn": earn_flag}).dropna(subset=["ibs_raw", "rsi2_raw", "s1_raw"])
        if len(df) < 25:
            continue

        df["ibs_pct"] = df["ibs_raw"].rank(pct=True, ascending=True)
        df.loc[df["earn"], "ibs_pct"] = 0.50
        df["rsi2_pct"] = df["rsi2_raw"].rank(pct=True, ascending=True)
        df["s1_pct"] = df["s1_raw"].rank(pct=True, ascending=True)

        n = len(df)
        top_n = max(1, n // 5)

        entry_all = O.loc[entry_date, df.index]
        exit_all = O.loc[exit_date, df.index]
        raw_ret_all = (exit_all / entry_all - 1)
        lo, hi = raw_ret_all.quantile(0.01), raw_ret_all.quantile(0.99)
        raw_ret_all = raw_ret_all.clip(lo, hi)
        df["realized_return"] = raw_ret_all.reindex(df.index)

        ibs_held = set(df.sort_values("ibs_pct", ascending=False).index[:top_n])
        ibs_ret = float(df.loc[list(ibs_held), "realized_return"].mean())
        rsi2_held = set(df.sort_values("rsi2_pct", ascending=False).index[:top_n])
        rsi2_ret = float(df.loc[list(rsi2_held), "realized_return"].mean())
        tue_names = df.index[df["tue"] == 1]
        tue_ret = float(df.loc[tue_names, "realized_return"].mean()) if len(tue_names) >= 5 else np.nan
        s1_held = set(df.sort_values("s1_pct", ascending=False).index[:top_n])
        s1_ret = float(df.loc[list(s1_held), "realized_return"].mean())
        s1_bottom = set(df.sort_values("s1_pct", ascending=True).index[:top_n])
        s1_bottom_ret = float(df.loc[list(s1_bottom), "realized_return"].mean())
        s1_spread = s1_ret - s1_bottom_ret

        turnover_ibs_vs_s1 = len(ibs_held.symmetric_difference(s1_held)) / max(1, top_n)

        regime = regime_by_date.get(rd, np.nan)
        high_vol = bool(high_vol_by_date.get(rd, False))
        vol20 = float(vol20_by_date.get(rd, np.nan)) if pd.notna(vol20_by_date.get(rd, np.nan)) else np.nan

        cs_corr = float(df["s1_pct"].corr(df["ibs_pct"]))

        fac_window = factors_daily[(factors_daily.index > entry_date) & (factors_daily.index <= exit_date)]
        if len(fac_window):
            fac_week = (1 + fac_window[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD", "RF"]]).prod() - 1
        else:
            fac_week = pd.Series({c: np.nan for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD", "RF"]})

        rows.append({
            "signal_date": rd, "realization_date": rd_next, "regime": regime, "high_vol": high_vol, "vol20": vol20,
            "ibs_ret": ibs_ret, "rsi2_ret": rsi2_ret, "tue_ret": tue_ret, "s1_ret": s1_ret, "s1_bottom_ret": s1_bottom_ret,
            "s1_spread": s1_spread, "cs_corr_s1_ibs": cs_corr, "turnover_ibs_vs_s1_frac": turnover_ibs_vs_s1,
            "n_held": top_n, "n_universe": len(syms),
            **{f"fac_{c}": fac_week[c] for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD", "RF"]},
        })

        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(rebalance_dates)} rebalance dates processed ({rd.date()})")

    print("Weekly loop complete.")
    out = pd.DataFrame(rows).set_index("realization_date").sort_index()
    return {"weekly": out}


def hac_stats(r: pd.Series, factors: pd.DataFrame) -> dict:
    r = r.dropna()
    f = factors.loc[r.index].dropna()
    aligned = pd.concat([r.rename("r"), f], axis=1, join="inner").dropna()
    if len(aligned) < 15:
        return dict(alpha_annual=np.nan, tstat=np.nan, sharpe=np.nan, n=len(aligned))
    cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD"]
    dep = aligned["r"] - aligned["RF"]
    m = sm.OLS(dep, sm.add_constant(aligned[cols])).fit(cov_type="HAC", cov_kwds={"maxlags": 4})
    alpha_annual = (1 + m.params["const"]) ** 52 - 1
    rr = r
    sharpe = (rr.mean() / rr.std()) * np.sqrt(52) if rr.std() > 0 else np.nan
    return dict(alpha_annual=float(alpha_annual), tstat=float(m.tvalues["const"]), sharpe=float(sharpe), n=int(len(aligned)))


def spread_stat(spread: pd.Series) -> dict:
    s = spread.dropna()
    if len(s) < 10:
        return dict(spread_ann=np.nan, tstat=np.nan, n=len(s))
    ann = (1 + s.mean()) ** 52 - 1
    m = sm.OLS(s, np.ones(len(s))).fit(cov_type="HAC", cov_kwds={"maxlags": 4})
    return dict(spread_ann=float(ann), tstat=float(m.tvalues[0]), n=int(len(s)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "sharadar.db"))
    parser.add_argument("--start", default="2001-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    bundle = run(args.db, args.start, args.end)
    weekly = bundle["weekly"]

    from datetime import datetime, timezone
    out_dir = Path(args.out) if args.out else ROOT / "reports" / "bear_overlay"
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / tag
    out_path.mkdir(parents=True, exist_ok=True)
    weekly.to_csv(out_path / "weekly_s1_panel.csv")
    print(f"Weekly S1 panel written: {out_path / 'weekly_s1_panel.csv'} ({len(weekly)} weeks)")
    return out_path, weekly


if __name__ == "__main__":
    main()
