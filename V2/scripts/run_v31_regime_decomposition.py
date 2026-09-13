"""V3.1 Regime Decomposition (v31_regime_decomposition_v1).

Characterizes the LIVE V3.1 composite (not a stripped base) across bull /
bear / sideways SPY regimes: composite alpha by regime, standalone
single-signal comparison, a signal-contribution decomposition, conditioning-
variable regime means, and sub-period stability. Characterization only --
no regime-switching composite is built here.

See research/preregistrations/v31_regime_decomposition_v1.json for the
locked spec, definitions, and documented deviations. Read that file first.
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

ROOT = Path(__file__).resolve().parents[1]
ROUND_TRIP_COST_BPS = 10
WEIGHTS = {"ibs": 0.62, "rsi2": 0.22, "sector_rs": 0.10, "turnaround_tue": 0.06}
EXCLUDE_FAMA_SECTORS = ["Financial Services", "Real Estate", "Healthcare"]
SUBPERIODS = [("2001-2008", "2001-01-01", "2008-12-31"), ("2009-2016", "2009-01-01", "2016-12-31"), ("2017-2024", "2017-01-01", "2024-12-31")]


def build_regime_calendar(spy_daily: pd.Series, ref_index: pd.DatetimeIndex) -> pd.Series:
    price = (1 + spy_daily.fillna(0)).cumprod()
    price = price.reindex(ref_index.union(price.index)).sort_index().ffill().reindex(ref_index)
    ma200 = price.rolling(200, min_periods=200).mean()
    ret252 = price / price.shift(252) - 1
    bull = (price > ma200) & (ret252 > 0)
    bear = (price < ma200) & (ret252 < 0)
    regime = pd.Series("SIDEWAYS", index=ref_index)
    regime[bull.fillna(False)] = "BULL"
    regime[bear.fillna(False)] = "BEAR"
    regime[ma200.isna()] = np.nan
    return regime


def build_earnings_flag(con, tickers, load_start, end, price_index, columns) -> pd.DataFrame:
    q = ",".join("?" * len(tickers)) if tickers else ""
    earn = pd.read_sql_query(
        f"SELECT DISTINCT ticker, date AS datekey FROM fundamentals WHERE ticker IN ({q}) AND dimension='ARQ' AND date>=? AND date<=?",
        con, params=[*tickers, load_start, end], parse_dates=["datekey"],
    ) if tickers else pd.DataFrame(columns=["ticker", "datekey"])
    col_idx = {c: i for i, c in enumerate(columns)}
    idx_vals = price_index.values
    flag_arr = np.zeros((len(price_index), len(columns)), dtype=bool)
    one_day = np.timedelta64(1, "D")
    for t, g in earn.groupby("ticker"):
        ci = col_idx.get(t)
        if ci is None:
            continue
        dks = g["datekey"].values
        lefts = np.searchsorted(idx_vals, dks - one_day, side="left")
        rights = np.searchsorted(idx_vals, dks + one_day, side="right")
        for l, r in zip(lefts, rights):
            if r > l:
                flag_arr[l:r, ci] = True
    return pd.DataFrame(flag_arr, index=price_index, columns=columns)


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
    print("Panels loaded. Loading earnings dates...")

    O, H, L, C, V = (_wide(px, c) for c in ("open", "high", "low", "close", "volume"))
    eligible = build_eligibility(O, H, L, C, V, candidates)
    sector_of = candidates.set_index("ticker")["sector"]
    earnings_flag = build_earnings_flag(con, universe_tickers, load_start, end, C.index, C.columns)
    con.close()
    print("Building conditioning-variable panels...")

    ibs_avg5 = ((C - L) / (H - L).replace(0, np.nan)).rolling(5, min_periods=5).mean()
    raw_ibs = 1 - ibs_avg5
    rsi2 = _rsi(C, 2)
    raw_rsi2 = 100 - rsi2
    ret63 = C / C.shift(63) - 1
    friday_ret = C / C.shift(1) - 1

    daily_ret = C.pct_change(fill_method=None)
    market_ret = daily_ret.where(eligible).mean(axis=1)
    total_vol20_pre = daily_ret.shift(1).rolling(20, min_periods=15).std()
    idio_ret = daily_ret.sub(market_ret, axis=0)
    idio_vol20_pre = idio_ret.shift(1).rolling(20, min_periods=15).std()
    vol20_pre_mean = V.shift(1).rolling(20, min_periods=15).mean()
    abnormal_volume = V / vol20_pre_mean.replace(0, np.nan)
    prev_c = C.shift(1)
    abs_gap = (O - prev_c).abs() / prev_c.replace(0, np.nan)
    abs_intraday = (C - O).abs() / O.replace(0, np.nan)
    overnight_gap_share = abs_gap / (abs_gap + abs_intraday).replace(0, np.nan)
    dollar_volume = C * V
    mc_wide = mc_panel.pivot_table(index="date", columns="ticker", values="marketcap", aggfunc="last").reindex(C.index).ffill(limit=5)
    turnover = dollar_volume / mc_wide.replace(0, np.nan)

    spy_daily = load_local_spy_daily()
    regime_by_date = build_regime_calendar(spy_daily, C.index)

    all_dates = pd.DatetimeIndex(sorted(px.date.unique()))
    date_pos = {d: i for i, d in enumerate(C.index)}
    rebalance_dates = _weekly_dates(all_dates, start_ts, end_ts)

    factors_daily = load_local_ff5_umd_daily()

    rows = []
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
        c63 = ret63.loc[rd].reindex(syms)
        d = (friday_ret.loc[rd].reindex(syms) < -0.01).astype(float)
        earn_flag = earnings_flag.loc[rd].reindex(syms).fillna(False)

        df = pd.DataFrame({"ibs_raw": a, "rsi2_raw": b, "sector_rs_raw": c63, "tue": d, "sector": sector_of.reindex(syms), "earn": earn_flag}).dropna(subset=["ibs_raw", "rsi2_raw", "sector_rs_raw"])
        if len(df) < 25:
            continue

        df["ibs_pct"] = df["ibs_raw"].rank(pct=True, ascending=True)
        df.loc[df["earn"], "ibs_pct"] = 0.50
        df["rsi2_pct"] = df["rsi2_raw"].rank(pct=True, ascending=True)
        df["sector_rs_pct"] = df.groupby("sector")["sector_rs_raw"].rank(pct=True, ascending=True)
        df["sector_rs_pct"] = df["sector_rs_pct"].fillna(0.5)
        df["composite"] = (WEIGHTS["ibs"] * df["ibs_pct"] + WEIGHTS["rsi2"] * df["rsi2_pct"]
                            + WEIGHTS["sector_rs"] * df["sector_rs_pct"] + WEIGHTS["turnaround_tue"] * df["tue"])

        n = len(df)
        top_n = max(1, n // 5)

        entry_all = O.loc[entry_date, df.index]
        exit_all = O.loc[exit_date, df.index]
        raw_ret_all = (exit_all / entry_all - 1)
        lo, hi = raw_ret_all.quantile(0.01), raw_ret_all.quantile(0.99)
        raw_ret_all = raw_ret_all.clip(lo, hi)
        df["realized_return"] = raw_ret_all.reindex(df.index)

        composite_held = df.sort_values("composite", ascending=False).index[:top_n]
        composite_ret = float(df.loc[composite_held, "realized_return"].mean())

        ibs_held = df.sort_values("ibs_pct", ascending=False).index[:top_n]
        ibs_ret = float(df.loc[ibs_held, "realized_return"].mean())
        rsi2_held = df.sort_values("rsi2_pct", ascending=False).index[:top_n]
        rsi2_ret = float(df.loc[rsi2_held, "realized_return"].mean())
        srs_held = df.sort_values("sector_rs_pct", ascending=False).index[:top_n]
        srs_ret = float(df.loc[srs_held, "realized_return"].mean())
        tue_names = df.index[df["tue"] == 1]
        tue_ret = float(df.loc[tue_names, "realized_return"].mean()) if len(tue_names) >= 5 else np.nan

        held_df = df.loc[composite_held]
        contrib_ibs = float((held_df["ibs_pct"] * WEIGHTS["ibs"] * held_df["realized_return"]).mean())
        contrib_rsi2 = float((held_df["rsi2_pct"] * WEIGHTS["rsi2"] * held_df["realized_return"]).mean())
        contrib_srs = float((held_df["sector_rs_pct"] * WEIGHTS["sector_rs"] * held_df["realized_return"]).mean())
        contrib_tue = float((held_df["tue"] * WEIGHTS["turnaround_tue"] * held_df["realized_return"]).mean())

        cond_overnight = float(overnight_gap_share.loc[rd].reindex(syms).mean())
        cond_abvol = float(abnormal_volume.loc[rd].reindex(syms).mean())
        cond_turnover = float(turnover.loc[rd].reindex(syms).mean())
        cond_totvol = float(total_vol20_pre.loc[rd].reindex(syms).mean())
        cond_idiovol = float(idio_vol20_pre.loc[rd].reindex(syms).mean())

        fac_window = factors_daily[(factors_daily.index > entry_date) & (factors_daily.index <= exit_date)]
        if len(fac_window):
            fac_week = (1 + fac_window[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD", "RF"]]).prod() - 1
        else:
            fac_week = pd.Series({c: np.nan for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD", "RF"]})

        regime = regime_by_date.get(rd, np.nan)

        rows.append({
            "signal_date": rd, "realization_date": rd_next, "regime": regime,
            "composite_ret": composite_ret, "ibs_ret": ibs_ret, "rsi2_ret": rsi2_ret, "srs_ret": srs_ret, "tue_ret": tue_ret,
            "contrib_ibs": contrib_ibs, "contrib_rsi2": contrib_rsi2, "contrib_srs": contrib_srs, "contrib_tue": contrib_tue,
            "cond_overnight_gap_share": cond_overnight, "cond_abnormal_volume": cond_abvol, "cond_turnover": cond_turnover,
            "cond_total_vol": cond_totvol, "cond_idio_vol": cond_idiovol,
            "n_held": len(composite_held), "n_universe": len(syms),
            **{f"fac_{c}": fac_week[c] for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD", "RF"]},
        })

        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(rebalance_dates)} rebalance dates processed ({rd.date()})")

    print("Weekly loop complete.")
    out = pd.DataFrame(rows).set_index("realization_date").sort_index()
    return {"weekly": out, "regime_by_date": regime_by_date}


def regress_regime(weekly_ret: pd.Series, factors: pd.DataFrame, mask: pd.Series) -> dict:
    r = weekly_ret[mask].dropna()
    f = factors.loc[r.index].dropna()
    aligned = pd.concat([r.rename("r"), f], axis=1, join="inner").dropna()
    if len(aligned) < 15:
        return {"alpha_annual": np.nan, "tstat": np.nan, "r_squared": np.nan, "n_weeks": len(aligned)}
    cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD"]
    dependent = aligned["r"] - aligned["RF"]
    model = sm.OLS(dependent, sm.add_constant(aligned[cols])).fit()
    alpha_week = float(model.params["const"])
    alpha_annual = (1 + alpha_week) ** 52 - 1
    return {"alpha_annual": alpha_annual, "tstat": float(model.tvalues["const"]), "r_squared": float(model.rsquared), "n_weeks": len(aligned)}


def sharpe_weekly(r: pd.Series) -> float:
    r = r.dropna()
    if len(r) < 5 or r.std(ddof=1) == 0:
        return float("nan")
    return float(np.sqrt(52) * r.mean() / r.std(ddof=1))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "sharadar.db"))
    parser.add_argument("--start", default="2001-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    prereg_path = ROOT / "research" / "preregistrations" / "v31_regime_decomposition_v1.json"
    if not prereg_path.exists():
        raise SystemExit(f"Missing preregistration: {prereg_path}")

    bundle = run(args.db, args.start, args.end)
    weekly = bundle["weekly"]

    factors = weekly[["fac_Mkt-RF", "fac_SMB", "fac_HML", "fac_RMW", "fac_CMA", "fac_UMD", "fac_RF"]].rename(
        columns={"fac_Mkt-RF": "Mkt-RF", "fac_SMB": "SMB", "fac_HML": "HML", "fac_RMW": "RMW", "fac_CMA": "CMA", "fac_UMD": "UMD", "fac_RF": "RF"})

    out_dir = Path(args.out) if args.out else ROOT / "reports" / "v31_regime_decomposition"
    from datetime import datetime, timezone
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / tag
    out_path.mkdir(parents=True, exist_ok=True)
    weekly.to_csv(out_path / "weekly_panel.csv")

    regime_counts = weekly["regime"].value_counts()
    print("\nREGIME CALENDAR")
    print(regime_counts)

    portfolios = {"composite": "composite_ret", "ibs": "ibs_ret", "rsi2": "rsi2_ret", "sector_rs": "srs_ret", "turnaround_tue": "tue_ret"}
    by_regime = {}
    for pname, col in portfolios.items():
        by_regime[pname] = {}
        for regime in ("BULL", "BEAR", "SIDEWAYS", "FULL"):
            mask = pd.Series(True, index=weekly.index) if regime == "FULL" else (weekly["regime"] == regime)
            reg = regress_regime(weekly[col], factors, mask)
            sharpe = sharpe_weekly(weekly.loc[mask, col])
            by_regime[pname][regime] = {**reg, "sharpe": sharpe}

    print("\nCOMPOSITE V3.1 BY REGIME")
    for regime, r in by_regime["composite"].items():
        print(f"  {regime}: alpha={r['alpha_annual']:.4f} t={r['tstat']:.2f} sharpe={r['sharpe']:.2f} n={r['n_weeks']}")

    contrib_cols = {"ibs": "contrib_ibs", "rsi2": "contrib_rsi2", "sector_rs": "contrib_srs", "turnaround_tue": "contrib_tue"}
    contrib_by_regime = {}
    for sig, col in contrib_cols.items():
        contrib_by_regime[sig] = {}
        for regime in ("BULL", "BEAR", "SIDEWAYS"):
            mask = weekly["regime"] == regime
            contrib_by_regime[sig][regime] = float(weekly.loc[mask, col].mean())

    cond_cols = {"overnight_gap_share": "cond_overnight_gap_share", "abnormal_volume": "cond_abnormal_volume", "turnover": "cond_turnover", "total_volatility": "cond_total_vol", "idiosyncratic_volatility": "cond_idio_vol"}
    cond_by_regime = {}
    for var, col in cond_cols.items():
        cond_by_regime[var] = {}
        for regime in ("BULL", "BEAR", "SIDEWAYS"):
            mask = weekly["regime"] == regime
            cond_by_regime[var][regime] = float(weekly.loc[mask, col].mean())

    subperiod_regime = {}
    for label, s, e in SUBPERIODS:
        sub = weekly[(weekly.index >= s) & (weekly.index <= e)]
        subperiod_regime[label] = {}
        for regime in ("BULL", "BEAR", "SIDEWAYS"):
            mask = sub["regime"] == regime
            if mask.sum() < 10:
                subperiod_regime[label][regime] = {"alpha_annual": np.nan, "tstat": np.nan, "n_weeks": int(mask.sum())}
                continue
            sub_factors = factors.loc[sub.index]
            reg = regress_regime(sub["composite_ret"], sub_factors, mask)
            subperiod_regime[label][regime] = reg

    summary = {
        "regime_counts": regime_counts.to_dict(),
        "by_regime": by_regime,
        "contribution_by_regime": contrib_by_regime,
        "conditioning_by_regime": cond_by_regime,
        "subperiod_regime": subperiod_regime,
    }
    (out_path / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nResults written to: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
