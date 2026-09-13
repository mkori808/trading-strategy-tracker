"""V3.1 Deepening -- Questions 2 and 3 (v31_deepening_q2q3_v1).

Q2: Signal marginal-contribution decomposition by regime (ablation: what
would the composite have returned if signal X's weight were zeroed and
redistributed proportionally to the others).

Q3: Drawdown anatomy of the live V3.1 composite -- top-5 peak-to-trough
episodes classified by market-beta contribution, IBS signal-failure test,
FF5+UMD factor exposure, and sector concentration, plus recovery analysis.

See research/preregistrations/v31_deepening_q2q3_v1.json for the locked
spec, weight sets, and method definitions. Read that file first.

Reuses the identical universe/eligibility/exclusion/execution logic as
scripts/run_v31_regime_decomposition.py (same weekly loop), extended to
track per-name percentile data needed for the ablation composites, the
IBS quintile spread, and held-vs-universe sector composition.
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
from scripts.run_v31_regime_decomposition import build_regime_calendar

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_FAMA_SECTORS = ["Financial Services", "Real Estate", "Healthcare"]
SUBPERIODS = [("2001-2008", "2001-01-01", "2008-12-31"), ("2009-2016", "2009-01-01", "2016-12-31"), ("2017-2024", "2017-01-01", "2024-12-31")]

WEIGHT_SETS = {
    "full": {"ibs": 0.62, "rsi2": 0.22, "sector_rs": 0.10, "turnaround_tue": 0.06},
    "minus_ibs": {"ibs": 0.0, "rsi2": 0.297, "sector_rs": 0.135, "turnaround_tue": 0.081},
    "minus_rsi2": {"ibs": 0.775, "rsi2": 0.0, "sector_rs": 0.125, "turnaround_tue": 0.075},
    "minus_tue": {"ibs": 0.659, "rsi2": 0.234, "sector_rs": 0.106, "turnaround_tue": 0.0},
    "minus_srs": {"ibs": 0.689, "rsi2": 0.244, "sector_rs": 0.0, "turnaround_tue": 0.067},
}


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


def score_for_weights(df: pd.DataFrame, w: dict) -> pd.Series:
    return (w["ibs"] * df["ibs_pct"] + w["rsi2"] * df["rsi2_pct"]
            + w["sector_rs"] * df["sector_rs_pct"] + w["turnaround_tue"] * df["tue"])


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
    print("Building signal panels...")

    ibs_avg5 = ((C - L) / (H - L).replace(0, np.nan)).rolling(5, min_periods=5).mean()
    raw_ibs = 1 - ibs_avg5
    rsi2 = _rsi(C, 2)
    raw_rsi2 = 100 - rsi2
    ret63 = C / C.shift(63) - 1
    friday_ret = C / C.shift(1) - 1

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

        n = len(df)
        top_n = max(1, n // 5)

        entry_all = O.loc[entry_date, df.index]
        exit_all = O.loc[exit_date, df.index]
        raw_ret_all = (exit_all / entry_all - 1)
        lo, hi = raw_ret_all.quantile(0.01), raw_ret_all.quantile(0.99)
        raw_ret_all = raw_ret_all.clip(lo, hi)
        df["realized_return"] = raw_ret_all.reindex(df.index)

        # --- Q2: ablation composites -------------------------------------
        week_result = {}
        held_full = None
        for wname, w in WEIGHT_SETS.items():
            score = score_for_weights(df, w)
            held = score.sort_values(ascending=False).index[:top_n]
            week_result[f"{wname}_ret"] = float(df.loc[held, "realized_return"].mean())
            if wname == "full":
                held_full = held

        # --- Q3: IBS quintile spread (signal-failure test) ---------------
        ibs_sorted = df.sort_values("ibs_pct", ascending=False)
        ibs_top = ibs_sorted.index[:top_n]
        ibs_bot = ibs_sorted.index[-top_n:]
        ibs_spread = float(df.loc[ibs_top, "realized_return"].mean() - df.loc[ibs_bot, "realized_return"].mean())

        # --- Q3: sector composition (held vs universe) --------------------
        held_sector_counts = df.loc[held_full, "sector"].value_counts().to_dict()
        universe_sector_counts = df["sector"].value_counts().to_dict()

        fac_window = factors_daily[(factors_daily.index > entry_date) & (factors_daily.index <= exit_date)]
        if len(fac_window):
            fac_week = (1 + fac_window[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD", "RF"]]).prod() - 1
        else:
            fac_week = pd.Series({c: np.nan for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD", "RF"]})

        spy_window = spy_daily[(spy_daily.index > entry_date) & (spy_daily.index <= exit_date)]
        spy_week_ret = float((1 + spy_window).prod() - 1) if len(spy_window) else np.nan

        regime = regime_by_date.get(rd, np.nan)

        rows.append({
            "signal_date": rd, "realization_date": rd_next, "regime": regime,
            **week_result,
            "ibs_spread": ibs_spread, "spy_ret": spy_week_ret,
            "n_held": len(held_full), "n_universe": len(syms),
            "held_sector_counts": json.dumps(held_sector_counts),
            "universe_sector_counts": json.dumps(universe_sector_counts),
            **{f"fac_{c}": fac_week[c] for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD", "RF"]},
        })

        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(rebalance_dates)} rebalance dates processed ({rd.date()})")

    print("Weekly loop complete.")
    out = pd.DataFrame(rows).set_index("realization_date").sort_index()
    return {"weekly": out}


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


def annualize(mean_weekly: float) -> float:
    if pd.isna(mean_weekly):
        return np.nan
    return float((1 + mean_weekly) ** 52 - 1)


def q2_marginal_contributions(weekly: pd.DataFrame) -> dict:
    signal_to_ablation = {"ibs": "minus_ibs", "rsi2": "minus_rsi2", "sector_rs": "minus_srs", "turnaround_tue": "minus_tue"}
    marg = pd.DataFrame(index=weekly.index)
    for sig, abl in signal_to_ablation.items():
        marg[sig] = weekly["full_ret"] - weekly[f"{abl}_ret"]

    by_regime = {}
    for sig in signal_to_ablation:
        by_regime[sig] = {}
        for regime in ("BULL", "BEAR", "SIDEWAYS", "FULL"):
            mask = pd.Series(True, index=weekly.index) if regime == "FULL" else (weekly["regime"] == regime)
            vals = marg.loc[mask, sig].dropna()
            by_regime[sig][regime] = {"marginal_annual": annualize(float(vals.mean())) if len(vals) else np.nan, "n_weeks": int(len(vals))}

    subperiod = {}
    for label, s, e in SUBPERIODS:
        sub_mask = (weekly.index >= s) & (weekly.index <= e)
        subperiod[label] = {}
        for sig in signal_to_ablation:
            subperiod[label][sig] = {}
            for regime in ("BULL", "BEAR", "SIDEWAYS"):
                mask = sub_mask & (weekly["regime"] == regime).values
                vals = marg.loc[mask, sig].dropna()
                subperiod[label][sig][regime] = {"marginal_annual": annualize(float(vals.mean())) if len(vals) else np.nan, "n_weeks": int(len(vals))}
    return {"marginal_series": marg, "by_regime": by_regime, "subperiod": subperiod}


def identify_drawdowns(composite_ret: pd.Series, top_k: int = 5) -> list:
    wealth = (1 + composite_ret.fillna(0)).cumprod()

    episodes = []
    last_peak_date = wealth.index[0]
    last_peak_val = wealth.iloc[0]
    in_dd = False
    trough_date = None
    trough_val = 0.0
    for dt, w in wealth.items():
        if w >= last_peak_val:
            if in_dd:
                episodes.append({"peak_date": last_peak_date, "trough_date": trough_date, "recovery_date": dt, "magnitude": trough_val})
                in_dd = False
            last_peak_date, last_peak_val = dt, w
        else:
            d = w / last_peak_val - 1
            if not in_dd:
                in_dd = True
                trough_date, trough_val = dt, d
            elif d < trough_val:
                trough_date, trough_val = dt, d
    if in_dd:
        episodes.append({"peak_date": last_peak_date, "trough_date": trough_date, "recovery_date": None, "magnitude": trough_val})

    episodes.sort(key=lambda e: e["magnitude"])
    return episodes[:top_k]


def classify_episode(weekly: pd.DataFrame, episode: dict, beta: float, factors_cols: list) -> dict:
    peak, trough = episode["peak_date"], episode["trough_date"]
    ep_weeks = weekly[(weekly.index > peak) & (weekly.index <= trough)]
    n_weeks = len(ep_weeks)

    port_cum = float((1 + ep_weeks["full_ret"]).prod() - 1)
    spy_cum = float((1 + ep_weeks["spy_ret"].fillna(0)).prod() - 1)
    explained_by_beta = beta * spy_cum
    residual = port_cum - explained_by_beta

    ibs_spread_avg = float(ep_weeks["ibs_spread"].mean()) if n_weeks else np.nan
    ibs_spread_annual = annualize(ibs_spread_avg) if pd.notna(ibs_spread_avg) else np.nan

    if abs(residual) < 0.03 or abs(residual) < 0.25 * abs(port_cum):
        classification = "MARKET DRIVEN"
    elif ibs_spread_avg is not None and pd.notna(ibs_spread_avg) and ibs_spread_avg < 0:
        classification = "SIGNAL FAILURE"
    else:
        classification = "MIXED"

    fac = ep_weeks[[f"fac_{c}" for c in factors_cols]].rename(columns={f"fac_{c}": c for c in factors_cols})
    dependent = ep_weeks["full_ret"] - ep_weeks["fac_RF"]
    factor_reg = {}
    if n_weeks >= 5:
        try:
            model = sm.OLS(dependent.values, sm.add_constant(fac[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD"]].values)).fit()
            names = ["const", "Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD"]
            factor_reg = {n: float(v) for n, v in zip(names, model.params)}
            factor_reg["tstats"] = {n: float(v) for n, v in zip(names, model.tvalues)}
            dominant = max((c for c in ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD"]), key=lambda c: abs(factor_reg[c]))
            factor_reg["dominant_factor"] = dominant
        except Exception:
            factor_reg = {"error": "regression failed"}
    factor_reg["n_weeks"] = n_weeks
    factor_reg["noisy_short_window"] = n_weeks < 26

    held_totals, univ_totals = {}, {}
    for _, row in ep_weeks.iterrows():
        for sec, cnt in json.loads(row["held_sector_counts"]).items():
            held_totals[sec] = held_totals.get(sec, 0) + cnt
        for sec, cnt in json.loads(row["universe_sector_counts"]).items():
            univ_totals[sec] = univ_totals.get(sec, 0) + cnt
    held_sum = sum(held_totals.values()) or 1
    univ_sum = sum(univ_totals.values()) or 1
    sector_conc = []
    for sec, cnt in held_totals.items():
        held_w = cnt / held_sum
        univ_w = univ_totals.get(sec, 0) / univ_sum
        if univ_w >= 0.03 and held_w > 1.5 * univ_w:
            sector_conc.append({"sector": sec, "held_weight": held_w, "universe_weight": univ_w})

    # recovery
    recovery_date = episode["recovery_date"]
    recovery_weeks = None
    recovery_ibs_spread_annual = np.nan
    recovery_regime = None
    if recovery_date is not None:
        rec_window = weekly[(weekly.index > trough) & (weekly.index <= recovery_date)]
        recovery_weeks = len(rec_window)
        if len(rec_window):
            recovery_ibs_spread_annual = annualize(float(rec_window["ibs_spread"].mean()))
            recovery_regime = rec_window["regime"].mode().iloc[0] if len(rec_window["regime"].dropna()) else None

    regime_at_start = weekly.loc[peak, "regime"] if peak in weekly.index else None

    return {
        "peak_date": str(peak.date()) if hasattr(peak, "date") else str(peak),
        "trough_date": str(trough.date()),
        "recovery_date": str(recovery_date.date()) if recovery_date is not None else None,
        "magnitude": episode["magnitude"],
        "duration_weeks": n_weeks,
        "regime_at_start": regime_at_start,
        "classification": classification,
        "portfolio_cum_return": port_cum,
        "spy_cum_return": spy_cum,
        "beta_used": beta,
        "explained_by_beta": explained_by_beta,
        "residual": residual,
        "ibs_spread_annual_during_episode": ibs_spread_annual,
        "factor_regression": factor_reg,
        "sector_concentration": sector_conc,
        "recovery_weeks": recovery_weeks,
        "recovery_ibs_spread_annual": recovery_ibs_spread_annual,
        "recovery_regime_mode": recovery_regime,
        "not_yet_recovered": recovery_date is None,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "sharadar.db"))
    parser.add_argument("--start", default="2001-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    prereg_path = ROOT / "research" / "preregistrations" / "v31_deepening_q2q3_v1.json"
    if not prereg_path.exists():
        raise SystemExit(f"Missing preregistration: {prereg_path}")

    bundle = run(args.db, args.start, args.end)
    weekly = bundle["weekly"]

    factor_cols = ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD"]
    factors = weekly[[f"fac_{c}" for c in factor_cols] + ["fac_RF"]].rename(columns={f"fac_{c}": c for c in factor_cols}).rename(columns={"fac_RF": "RF"})

    out_dir = Path(args.out) if args.out else ROOT / "reports" / "v31_deepening_q2q3"
    from datetime import datetime, timezone
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / tag
    out_path.mkdir(parents=True, exist_ok=True)
    weekly.to_csv(out_path / "weekly_panel_extended.csv")

    # ---------------- Q2 ----------------
    q2 = q2_marginal_contributions(weekly)
    print("\nSIGNAL MARGINAL CONTRIBUTION BY REGIME")
    print(f"{'Signal':<10}{'BULL':>12}{'BEAR':>12}{'SIDEWAYS':>12}{'FULL':>12}")
    for sig in ("ibs", "rsi2", "sector_rs", "turnaround_tue"):
        vals = [q2["by_regime"][sig][r]["marginal_annual"] for r in ("BULL", "BEAR", "SIDEWAYS", "FULL")]
        print(f"{sig:<10}" + "".join(f"{v*100:>11.1f}%" if pd.notna(v) else f"{'nan':>12}" for v in vals))

    print("\nFULL-REGIME COMPOSITE CHECK (alpha vs v31_regime_decomposition full composite)")
    for regime in ("BULL", "BEAR", "SIDEWAYS", "FULL"):
        mask = pd.Series(True, index=weekly.index) if regime == "FULL" else (weekly["regime"] == regime)
        reg = regress_regime(weekly["full_ret"], factors, mask)
        print(f"  {regime}: alpha={reg['alpha_annual']:.4f} t={reg['tstat']:.2f} n={reg['n_weeks']}")

    # ---------------- Q3 ----------------
    episodes = identify_drawdowns(weekly["full_ret"], top_k=5)
    print("\nTOP 5 DRAWDOWN EPISODES")
    for i, e in enumerate(episodes, 1):
        print(f"  {i}. peak={e['peak_date']} trough={e['trough_date']} recovery={e['recovery_date']} magnitude={e['magnitude']*100:.1f}%")

    full_mask = pd.Series(True, index=weekly.index)
    full_aligned = pd.concat([weekly["full_ret"].rename("r"), weekly["spy_ret"].rename("spy"), factors["RF"]], axis=1).dropna()
    beta_model = sm.OLS((full_aligned["r"] - full_aligned["RF"]).values, sm.add_constant((full_aligned["spy"] - full_aligned["RF"]).values)).fit()
    full_sample_beta = float(beta_model.params[1])
    print(f"\nFull-sample CAPM beta (weekly, vs SPY): {full_sample_beta:.3f}")

    episode_results = [classify_episode(weekly, e, full_sample_beta, factor_cols) for e in episodes]
    print("\nEPISODE CLASSIFICATION")
    for i, er in enumerate(episode_results, 1):
        print(f"  Episode {i}: {er['peak_date']} -> {er['trough_date']} ({er['classification']})")
        print(f"     port_cum={er['portfolio_cum_return']*100:.1f}% beta_explained={er['explained_by_beta']*100:.1f}% residual={er['residual']*100:.1f}%")
        print(f"     IBS spread during episode: {er['ibs_spread_annual_during_episode']*100:.1f}%/yr")
        print(f"     dominant factor: {er['factor_regression'].get('dominant_factor', 'n/a')} noisy={er['factor_regression']['noisy_short_window']}")
        print(f"     recovery: {er['recovery_weeks']} weeks, regime={er['recovery_regime_mode']}")

    non_dd_mask = pd.Series(True, index=weekly.index)
    for e in episodes:
        non_dd_mask &= ~((weekly.index > e["peak_date"]) & (weekly.index <= e["trough_date"]))
    ibs_spread_in_dd = np.mean([er["ibs_spread_annual_during_episode"] for er in episode_results])
    ibs_spread_out_dd = annualize(float(weekly.loc[non_dd_mask, "ibs_spread"].mean()))

    pattern_summary = {
        "market_driven": sum(1 for er in episode_results if er["classification"] == "MARKET DRIVEN"),
        "signal_failure": sum(1 for er in episode_results if er["classification"] == "SIGNAL FAILURE"),
        "mixed": sum(1 for er in episode_results if er["classification"] == "MIXED"),
        "avg_ibs_spread_during_drawdowns_annual": float(ibs_spread_in_dd),
        "avg_ibs_spread_outside_drawdowns_annual": ibs_spread_out_dd,
        "regimes_at_start": [er["regime_at_start"] for er in episode_results],
        "regimes_at_recovery": [er["recovery_regime_mode"] for er in episode_results],
    }
    print("\nDRAWDOWN PATTERN SUMMARY")
    print(json.dumps(pattern_summary, indent=2, default=str))

    summary = {
        "q2_by_regime": q2["by_regime"],
        "q2_subperiod": q2["subperiod"],
        "q3_full_sample_beta": full_sample_beta,
        "q3_episodes": episode_results,
        "q3_pattern_summary": pattern_summary,
    }
    (out_path / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nResults written to: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
