"""Negative Selection Audit (negative_selection_v1).

Portfolio construction audit -- NOT a signal discovery exercise. Measures
the marginal contribution of the NSI Q5 and Quality Q5 hard exclusions to
the live V3.1 composite spec, independently and jointly, against a
"stripped base" that runs the identical V3.1 signals/weights/universe/
rebalance/cost model with the exclusion step skipped.

See research/preregistrations/negative_selection_v1.json for the locked
spec, falsification criteria, and constraints. Read that file before
reading this one.
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
from strategies.momentum import _weekly_dates, _weekly_to_monthly
from strategies.quality import _FUND_COLS
from strategies.signal_library import build_eligibility, load_full_ohlc_panel, _rsi, _wide
from utils.research_utils import (
    load_fundamentals_panel, load_marketcap_panel, load_universe_candidates,
    panel_latest_asof, panel_marketcaps_asof,
)
from utils.performance_metrics import cagr as m_cagr, max_drawdown as m_max_dd, sharpe_ratio as m_sharpe, sortino_ratio as m_sortino, calmar_ratio as m_calmar

ROOT = Path(__file__).resolve().parents[1]
ROUND_TRIP_COST_BPS = 10
WEIGHTS = {"ibs": 0.62, "rsi2": 0.22, "sector_rs": 0.10, "turnaround_tue": 0.06}
EXCLUDE_FAMA_SECTORS = ["Financial Services", "Real Estate", "Healthcare"]
PORTFOLIOS = {
    "A": set(),
    "B": {"nsi"},
    "C": {"quality"},
    "D": {"nsi", "quality"},
}
CRASH_PERIODS = {
    "2008-09": ("2008-01-01", "2009-12-31"),
    "2020-03": ("2020-02-01", "2020-04-30"),
    "2022": ("2022-01-01", "2022-12-31"),
}


def load_local_ff5_umd_daily() -> pd.DataFrame:
    df = pd.read_csv(ROOT / "data" / "factors_ff5_mom_daily_2001_2024.csv", parse_dates=["Date"]).set_index("Date")
    return df.rename(columns={"Mom": "UMD"})


def load_local_spy_daily() -> pd.Series:
    df = pd.read_csv(ROOT / "data" / "spy_total_return_daily_2001_2024.csv", parse_dates=["Date"]).set_index("Date")
    return df["SPY"]


def daily_to_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    m = daily.groupby(daily.index.to_period("M")).apply(lambda g: (1 + g).prod() - 1)
    m.index = m.index.to_timestamp("M")
    return m


def six_factor_regression_local(monthly_returns: pd.Series, factors_monthly: pd.DataFrame, is_long_short: bool = False) -> dict:
    cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD"]
    aligned = pd.concat([monthly_returns.rename("r"), factors_monthly[[*cols, "RF"]]], axis=1, join="inner").dropna()
    if len(aligned) < 10:
        return {"alpha_annual": np.nan, "tstat": np.nan, "r_squared": np.nan, "betas": {}, "n_months": len(aligned), "residuals": pd.Series(dtype=float)}
    dependent = aligned.r if is_long_short else aligned.r - aligned.RF
    model = sm.OLS(dependent, sm.add_constant(aligned[cols])).fit()
    alpha_period = float(model.params["const"])
    alpha_annual = alpha_period * 12 if is_long_short else (1 + alpha_period) ** 12 - 1
    return {
        "alpha_annual": alpha_annual, "tstat": float(model.tvalues["const"]), "r_squared": float(model.rsquared),
        "betas": model.params.drop("const").to_dict(), "n_months": len(aligned),
        "residuals": model.resid,
    }


def build_earnings_flag(con, tickers: list[str], load_start: str, end: str, price_index: pd.DatetimeIndex, columns: pd.Index) -> pd.DataFrame:
    """Vectorized: for each ticker, searchsorted the +/-1-day window of every
    ARQ datekey into the shared trading-day index and fill a bool array by
    slice -- avoids one pandas .loc call per (ticker, earnings event) pair,
    which is too slow at full-universe x 24yr scale (hundreds of thousands
    of events)."""
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
    print("Price/fundamentals/marketcap panels loaded. Loading earnings dates...")

    O, H, L, C, V = (_wide(px, c) for c in ("open", "high", "low", "close", "volume"))
    eligible = build_eligibility(O, H, L, C, V, candidates)
    sector_of = candidates.set_index("ticker")["sector"]

    earnings_flag = build_earnings_flag(con, universe_tickers, load_start, end, C.index, C.columns)
    con.close()
    print("Panels ready. Starting weekly rebalance loop.")

    ibs_avg5 = ((C - L) / (H - L).replace(0, np.nan)).rolling(5, min_periods=5).mean()
    raw_ibs = 1 - ibs_avg5
    rsi2 = _rsi(C, 2)
    raw_rsi2 = 100 - rsi2
    ret63 = C / C.shift(63) - 1
    friday_ret = C / C.shift(1) - 1

    all_dates = pd.DatetimeIndex(sorted(px.date.unique()))
    date_pos = {d: i for i, d in enumerate(C.index)}
    rebalance_dates = _weekly_dates(all_dates, start_ts, end_ts)

    weekly_rows = {p: [] for p in PORTFOLIOS}
    holdings_by_date = {p: {} for p in PORTFOLIOS}
    universe_sizes = {p: [] for p in PORTFOLIOS}
    n_held_hist = {p: [] for p in PORTFOLIOS}

    cached_month = None
    cached_nsi_excl, cached_qual_excl = set(), set()

    for i, rd in enumerate(rebalance_dates):
        if rd not in date_pos or date_pos[rd] < 63:
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
            cached_nsi_excl = set(_quintile(nsi, ascending=True).pipe(lambda q: q.index[q == 5])) if len(nsi) >= 5 else set()
            qual = _quality_score(qual_panel, rd, syms_month)
            cached_qual_excl = set(_quintile(qual, ascending=False).pipe(lambda q: q.index[q == 5])) if len(qual) >= 5 else set()
            cached_month = month_key

        syms_today = list(eligible.columns[eligible.loc[rd]]) if rd in eligible.index else []
        if len(syms_today) < 25:
            continue

        for pname, excl_set in PORTFOLIOS.items():
            excluded = set()
            if "nsi" in excl_set:
                excluded |= cached_nsi_excl
            if "quality" in excl_set:
                excluded |= cached_qual_excl
            syms = [s for s in syms_today if s not in excluded]
            if len(syms) < 25:
                continue
            universe_sizes[pname].append(len(syms))

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
            held = df.sort_values("composite", ascending=False).index[:top_n]
            n_held_hist[pname].append(len(held))
            holdings_by_date[pname][rd] = set(held)

            entry_p = O.loc[entry_date, list(held)]
            exit_p = O.loc[exit_date, list(held)]
            raw_ret = (exit_p / entry_p - 1)
            lo, hi = raw_ret.quantile(0.01), raw_ret.quantile(0.99)
            raw_ret = raw_ret.clip(lo, hi)
            weekly_rows[pname].append((rd_next, float(raw_ret.mean())))

        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(rebalance_dates)} rebalance dates processed ({rd.date()})")

    print("Rebalance loop complete. Computing metrics.")

    factors_daily = load_local_ff5_umd_daily()
    factors_monthly = daily_to_monthly(factors_daily[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD"]])
    factors_monthly["RF"] = factors_daily["RF"].groupby(factors_daily.index.to_period("M")).apply(lambda s: (1 + s).prod() - 1).set_axis(factors_monthly.index)
    spy_daily = load_local_spy_daily()

    results = {}
    for pname in PORTFOLIOS:
        weekly = pd.Series(dict(weekly_rows[pname])).sort_index()
        weekly = weekly[(weekly.index >= start_ts) & (weekly.index <= end_ts)]

        prev, rates = None, []
        for rd in sorted(holdings_by_date[pname]):
            cur = holdings_by_date[pname][rd]
            if prev is not None and len(cur):
                rates.append(len(cur.symmetric_difference(prev)) / (2 * len(cur)))
            prev = cur
        turnover_weekly = float(np.mean(rates)) if rates else float("nan")
        weekly_cost = turnover_weekly * (ROUND_TRIP_COST_BPS / 10_000)
        weekly_net = weekly - weekly_cost
        annual_cost_estimate = weekly_cost * 52

        monthly_net = _weekly_to_monthly(weekly_net.dropna())
        reg = six_factor_regression_local(monthly_net, factors_monthly, is_long_short=False)

        spy_week_rows = []
        rd_list = sorted(holdings_by_date[pname].keys())
        for j, rd in enumerate(rd_list):
            fut = [x for x in rd_list if x > rd]
            if not fut:
                continue
            rd_next = fut[0]
            after_rd = [dt for dt in C.index if dt > rd]
            after_rd_next = [dt for dt in C.index if dt > rd_next]
            if not after_rd or not after_rd_next:
                continue
            e_date, x_date = after_rd[0], after_rd_next[0]
            window = spy_daily[(spy_daily.index > e_date) & (spy_daily.index <= x_date)]
            spy_week_rows.append((rd_next, float((1 + window).prod() - 1) if len(window) else np.nan))
        spy_weekly_series = pd.Series(dict(spy_week_rows)).sort_index()
        spy_weekly_series = spy_weekly_series[(spy_weekly_series.index >= start_ts) & (spy_weekly_series.index <= end_ts)]

        spy_full = spy_daily[(spy_daily.index >= start_ts) & (spy_daily.index <= end_ts)]
        spy_cagr = float((1 + spy_full).prod() ** (252 / len(spy_full)) - 1) if len(spy_full) else np.nan

        left_tail_5pct = float(monthly_net.quantile(0.05)) if len(monthly_net) else np.nan
        rolling_52w = (1 + weekly_net.fillna(0)).rolling(52).apply(lambda x: x.prod() - 1, raw=True)
        worst_12mo = float(rolling_52w.min()) if rolling_52w.notna().any() else np.nan
        downside = weekly_net[weekly_net < 0]
        semidev_weekly = float(np.sqrt((downside ** 2).mean())) if len(downside) else np.nan

        wealth = (1 + weekly_net.fillna(0)).cumprod()
        dd = wealth / wealth.cummax() - 1
        in_dd = dd < 0
        dd_durations, run_len = [], 0
        for flag in in_dd:
            if flag:
                run_len += 1
            else:
                if run_len:
                    dd_durations.append(run_len)
                run_len = 0
        if run_len:
            dd_durations.append(run_len)
        max_dd_duration_weeks = max(dd_durations) if dd_durations else 0
        max_dd_duration_months = max_dd_duration_weeks / 4.33

        crash = {}
        for label, (cs, ce) in CRASH_PERIODS.items():
            w = weekly_net[(weekly_net.index >= cs) & (weekly_net.index <= ce)]
            crash[label] = float((1 + w).prod() - 1) if len(w) else np.nan

        avg_n_held = float(np.mean(n_held_hist[pname])) if n_held_hist[pname] else np.nan
        avg_universe = float(np.mean(universe_sizes[pname])) if universe_sizes[pname] else np.nan
        hhi = float(np.mean([1.0 / n for n in n_held_hist[pname]])) if n_held_hist[pname] else np.nan
        avg_position_size = 100_000.0 / avg_n_held if avg_n_held else np.nan

        results[pname] = {
            "weekly_net": weekly_net, "monthly_net": monthly_net, "holdings": holdings_by_date[pname],
            "spy_weekly": spy_weekly_series, "residuals": reg["residuals"],
            "cagr": m_cagr(weekly_net, 52), "excess_cagr_vs_spy": m_cagr(weekly_net, 52) - spy_cagr if pd.notna(m_cagr(weekly_net, 52)) else np.nan,
            "spy_cagr": spy_cagr,
            "alpha_annual": reg["alpha_annual"], "alpha_tstat": reg["tstat"], "r_squared": reg["r_squared"], "factor_n_months": reg["n_months"],
            "sharpe": m_sharpe(weekly_net, 52), "sortino_weekly_equiv": m_sortino(weekly_net, 52),
            "max_drawdown": m_max_dd(weekly_net), "max_dd_duration_months": max_dd_duration_months, "calmar": m_calmar(weekly_net, 52),
            "semi_deviation_weekly": semidev_weekly, "left_tail_5pct_monthly": left_tail_5pct, "worst_12mo_rolling": worst_12mo,
            "crash": crash,
            "turnover_weekly": turnover_weekly, "turnover_monthly_equiv": turnover_weekly * 4.33,
            "annual_cost_pct": annual_cost_estimate, "net_cagr_after_costs": m_cagr(weekly_net, 52),
            "avg_n_held": avg_n_held, "avg_universe_after_exclusion": avg_universe,
            "avg_position_size_usd": avg_position_size, "hhi": hhi,
            "n_weeks": len(weekly_net),
        }

    for pname in PORTFOLIOS:
        if pname == "D":
            continue
        r, rd_ = results[pname], results["D"]
        active_p = (r["weekly_net"] - r["spy_weekly"]).dropna()
        active_d = (rd_["weekly_net"] - rd_["spy_weekly"]).dropna()
        aligned = pd.concat([active_p.rename("p"), active_d.rename("d")], axis=1, join="inner").dropna()
        results[pname]["active_return_corr_vs_D"] = float(aligned["p"].corr(aligned["d"])) if len(aligned) > 5 else np.nan

        res_aligned = pd.concat([r["residuals"].rename("p"), rd_["residuals"].rename("d")], axis=1, join="inner").dropna()
        results[pname]["residual_corr_vs_D"] = float(res_aligned["p"].corr(res_aligned["d"])) if len(res_aligned) > 5 else np.nan

        common_dates = sorted(set(r["holdings"].keys()) & set(rd_["holdings"].keys()))
        jaccards = []
        for rd in common_dates:
            hp, hd = r["holdings"][rd], rd_["holdings"][rd]
            union = hp | hd
            if union:
                jaccards.append(len(hp & hd) / len(union))
        results[pname]["holdings_jaccard_vs_D"] = float(np.mean(jaccards)) if jaccards else np.nan

    active_p = (results["D"]["weekly_net"] - results["D"]["spy_weekly"]).dropna()
    results["D"]["active_return_corr_vs_D"] = 1.0
    results["D"]["residual_corr_vs_D"] = 1.0
    results["D"]["holdings_jaccard_vs_D"] = 1.0

    return results


def verdict(row_a: dict, row_x: dict) -> tuple[str, dict]:
    sharpe_d = row_x["sharpe"] - row_a["sharpe"]
    dd_d = row_x["max_drawdown"] - row_a["max_drawdown"]  # both negative numbers; less negative = improvement
    tail_d = row_x["left_tail_5pct_monthly"] - row_a["left_tail_5pct_monthly"]
    alpha_d = row_x["alpha_annual"] - row_a["alpha_annual"]
    cagr_d = row_x["cagr"] - row_a["cagr"]

    improved = 0
    if pd.notna(sharpe_d) and sharpe_d >= 0.05:
        improved += 1
    if pd.notna(dd_d) and dd_d >= 0.02:
        improved += 1
    if pd.notna(tail_d) and tail_d >= 0.005:
        improved += 1
    if pd.notna(alpha_d) and alpha_d >= 0.005:
        improved += 1

    worsened = 0
    if pd.notna(cagr_d) and cagr_d < -0.01:
        worsened += 1
    if pd.notna(dd_d) and dd_d < -0.02:
        worsened += 1

    deltas = {"sharpe_delta": sharpe_d, "maxdd_delta": dd_d, "tail_delta": tail_d, "alpha_delta": alpha_d, "cagr_delta": cagr_d}
    if worsened > 2:
        return "HARMFUL", deltas
    if improved >= 2:
        return "VALUABLE", deltas
    return "NEUTRAL", deltas


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "sharadar.db"))
    parser.add_argument("--start", default="2001-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    prereg_path = ROOT / "research" / "preregistrations" / "negative_selection_v1.json"
    if not prereg_path.exists():
        raise SystemExit(f"Missing preregistration: {prereg_path}")

    results = run(args.db, args.start, args.end)

    out_dir = Path(args.out) if args.out else ROOT / "reports" / "negative_selection"
    from datetime import datetime, timezone
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / tag
    out_path.mkdir(parents=True, exist_ok=True)

    summary = {}
    for pname, r in results.items():
        summary[pname] = {k: v for k, v in r.items() if k not in ("weekly_net", "monthly_net", "holdings", "spy_weekly", "residuals")}
    (out_path / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    for pname, r in results.items():
        r["weekly_net"].to_csv(out_path / f"{pname}_weekly_net_returns.csv", header=["weekly_net_return"])

    verdicts = {}
    for pname in ("B", "C", "D"):
        v, deltas = verdict(results["A"], results[pname])
        verdicts[pname] = {"verdict": v, **deltas}
    (out_path / "verdicts.json").write_text(json.dumps(verdicts, indent=2, default=str), encoding="utf-8")

    print(f"\nResults written to: {out_path}")
    print(json.dumps(verdicts, indent=2, default=str))
    return out_path


if __name__ == "__main__":
    main()
