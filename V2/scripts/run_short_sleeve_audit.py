"""Short Sleeve Audit (short_sleeve_v1).

Cost-screened feasibility test of shorting NSI Q5 / Quality Q5 names within
a liquid, S&P-500-restricted, borrow-plausible universe. NOT a new signal
search -- the long-short spread is already validated at the signal level;
this asks only whether it survives realistic borrow + margin costs.

See research/preregistrations/short_sleeve_v1.json for the locked spec,
falsification criteria, and constraints. Read that file before this one.
Cost screen runs first and gates everything downstream, per the prereg.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
if str(_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_ROOT_FOR_IMPORT))

from strategies.composite_v2 import _nsi_signal, _quality_score, _quintile
from strategies.quality import _FUND_COLS
from strategies.signal_library import load_full_ohlc_panel, _wide
from utils.research_utils import (
    load_fundamentals_panel, load_marketcap_panel, load_pit_universe_snapshots,
    load_price_panel, load_universe_candidates, panel_marketcaps_asof,
    panel_month_values, universe_as_of, winsorize_by_group,
)
from scripts.run_negative_selection_audit import load_local_ff5_umd_daily, daily_to_monthly, six_factor_regression_local

ROOT = Path(__file__).resolve().parents[1]
MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 5_000_000
MIN_HISTORY_DAYS = 252
EXCLUDE_FAMA_SECTORS = ["Financial Services", "Real Estate"]
BORROW_TIERS = {"BASE": 0.01, "STRESS": 0.03, "SEVERE": 0.05}
MARGIN_DRAG_ANNUAL = 0.005


def _custom_eligibility(O, H, L, C, V, min_price, min_dollar_volume, min_history_days) -> pd.DataFrame:
    price_ok = C >= min_price
    dollar_vol = C * V
    dv_med = dollar_vol.rolling(63, min_periods=63).median()
    liquidity_ok = dv_med >= min_dollar_volume
    history_ok = C.notna().cumsum() >= min_history_days
    return price_ok.fillna(False) & liquidity_ok.fillna(False) & history_ok.fillna(False)


def run(db_path: str, start: str, end: str) -> dict:
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    con = sqlite3.connect(db_path)
    candidates = load_universe_candidates(con, exclude_sectors=[], exclude_fama_sectors=EXCLUDE_FAMA_SECTORS)
    load_start = (start_ts - pd.Timedelta(days=500)).strftime("%Y-%m-%d")
    load_end = (end_ts + pd.Timedelta(days=45)).strftime("%Y-%m-%d")
    window_start, window_end = pd.Timestamp(load_start), end_ts
    candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
    universe_tickers = candidates.ticker.tolist()
    print(f"Universe candidates (sector-filtered, pre-S&P500/liquidity restriction): {len(universe_tickers)}")

    px = load_full_ohlc_panel(con, universe_tickers, load_start, end)
    price_panel = load_price_panel(con, universe_tickers, load_start, load_end)
    shares_panel = load_fundamentals_panel(con, universe_tickers, load_start, end, ["sharesbas"], dimensions=("MRQ",))
    qual_panel = load_fundamentals_panel(con, universe_tickers, load_start, end, _FUND_COLS)
    mc_panel = load_marketcap_panel(con, universe_tickers, start, load_end)
    sp500_snapshots = load_pit_universe_snapshots(con)
    con.close()
    print(f"Panels loaded. S&P 500 PIT snapshots: {len(sp500_snapshots)}")

    O, H, L, C, V = (_wide(px, c) for c in ("open", "high", "low", "close", "volume"))
    liquidity_eligible = _custom_eligibility(O, H, L, C, V, MIN_PRICE, MIN_DOLLAR_VOLUME, MIN_HISTORY_DAYS)

    months = pd.date_range(start, end, freq="ME")
    trading_days = C.index

    def nearest_trading_day_leq(dt):
        idx = trading_days[trading_days <= dt]
        return idx[-1] if len(idx) else None

    rows_gross = {"both": [], "nsi_only": [], "quality_only": []}
    coverage_rows = []
    holdings_meta = {"both": [], "nsi_only": [], "quality_only": []}

    for dt in months:
        rd = nearest_trading_day_leq(dt)
        if rd is None or rd not in liquidity_eligible.index:
            continue
        sp500_members = set(universe_as_of(sp500_snapshots, rd))
        liquid_today = set(liquidity_eligible.columns[liquidity_eligible.loc[rd]])
        sector_ok = set(candidates.ticker)
        syms = sorted(sp500_members & liquid_today & sector_ok)
        coverage_rows.append({"date": rd, "sp500_members": len(sp500_members), "restricted_universe": len(syms)})
        if len(syms) < 25:
            continue

        nsi = _nsi_signal(shares_panel, rd, syms)
        nsi_q5 = set()
        if len(nsi) >= 5:
            q = _quintile(nsi, ascending=True)
            nsi_q5 = set(q.index[q == 5])

        qual = _quality_score(qual_panel, rd, syms)
        qual_q5 = set()
        if len(qual) >= 5:
            q = _quintile(qual, ascending=False)
            qual_q5 = set(q.index[q == 5])

        both = nsi_q5 & qual_q5
        realization_month = dt + pd.offsets.MonthEnd(1)
        cur_vals = panel_month_values(price_panel, dt.to_period("M"))
        next_vals = panel_month_values(price_panel, realization_month.to_period("M"))
        mktcaps = panel_marketcaps_asof(mc_panel, syms, rd)
        median_mktcap = float(np.nanmedian([mktcaps.get(s, np.nan) for s in syms])) if syms else np.nan

        for label, group in (("both", both), ("nsi_only", nsi_q5), ("quality_only", qual_q5)):
            names = sorted(group)
            holdings_meta[label].append({"date": rd, "realization_month": realization_month, "n": len(names)})
            if not names:
                continue
            a = pd.Series({s: cur_vals.get(s, np.nan) for s in names})
            b = pd.Series({s: next_vals.get(s, np.nan) for s in names})
            gdf = pd.DataFrame({"ticker": names, "a": a.values, "b": b.values})
            gdf = gdf.dropna(subset=["a", "b"])
            if gdf.empty:
                continue
            gdf["return"] = gdf.b / gdf.a - 1
            gdf["date"] = realization_month
            gdf = winsorize_by_group(gdf, "return", "date")
            gdf["mktcap"] = gdf.ticker.map(mktcaps)
            gdf["size_bucket"] = np.where(gdf["mktcap"] >= median_mktcap, "Large", "Mid")
            for _, r in gdf.iterrows():
                rows_gross[label].append({"realization_month": realization_month, "ticker": r.ticker, "return": r["return"], "size_bucket": r.size_bucket, "formation_year": rd.year})

    print("Monthly signal loop complete. Building return series.")
    long_q5_monthly = {}
    for label in rows_gross:
        df = pd.DataFrame(rows_gross[label])
        long_q5_monthly[label] = df

    return {
        "long_q5_rows": long_q5_monthly,
        "coverage": pd.DataFrame(coverage_rows),
        "holdings_meta": {k: pd.DataFrame(v) for k, v in holdings_meta.items()},
    }


def _monthly_series(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype=float)
    s = df.groupby("realization_month")["return"].mean().sort_index()
    return s


def _net_tiers(gross_short_monthly: pd.Series) -> dict:
    out = {}
    for tier, rate in BORROW_TIERS.items():
        drag = (rate + MARGIN_DRAG_ANNUAL) / 12
        out[tier] = gross_short_monthly - drag
    return out


def _cagr(monthly: pd.Series) -> float:
    m = monthly.dropna()
    if m.empty or (1 + m).le(0).any():
        return float("nan")
    growth = float((1 + m).prod())
    years = len(m) / 12
    return growth ** (1 / years) - 1 if years > 0 and growth > 0 else float("nan")


def _sharpe_monthly(monthly: pd.Series) -> float:
    m = monthly.dropna()
    if len(m) < 2 or m.std(ddof=1) == 0:
        return float("nan")
    return float(np.sqrt(12) * m.mean() / m.std(ddof=1))


def _max_dd(monthly: pd.Series) -> float:
    m = monthly.dropna()
    if m.empty:
        return float("nan")
    wealth = (1 + m).cumprod()
    return float((wealth / wealth.cummax() - 1).min())


def build_factors_monthly() -> pd.DataFrame:
    factors_daily = load_local_ff5_umd_daily()
    factors_monthly = daily_to_monthly(factors_daily[["Mkt-RF", "SMB", "HML", "RMW", "CMA", "UMD"]])
    factors_monthly["RF"] = factors_daily["RF"].groupby(factors_daily.index.to_period("M")).apply(lambda s: (1 + s).prod() - 1).set_axis(factors_monthly.index)
    return factors_monthly


def primary_cost_screen(bundle: dict, factors_monthly: pd.DataFrame) -> tuple[str, dict]:
    """Minimal computation needed for the cost-screen gate: primary (both-Q5)
    gross short return and its gross factor alpha only. Everything else
    (net tiers, sub-groups, time/size splits) is gated behind this and must
    not be computed first -- per the preregistration's explicit ordering."""
    long_ret = _monthly_series(bundle["long_q5_rows"]["both"])
    short_gross = -long_ret
    hm = bundle["holdings_meta"]["both"]
    avg_n = float(hm["n"].mean()) if len(hm) else np.nan
    reg_gross = six_factor_regression_local(short_gross, factors_monthly, is_long_short=True)
    primary = {
        "avg_n_per_month": avg_n, "n_months": len(short_gross.dropna()),
        "gross_alpha_annual": reg_gross["alpha_annual"], "gross_alpha_tstat": reg_gross["tstat"],
        "gross_r2": reg_gross["r_squared"], "gross_betas": reg_gross["betas"],
        "short_gross_monthly": short_gross,
    }
    return cost_screen(primary), primary


def analyze(bundle: dict, factors_monthly: pd.DataFrame) -> dict:
    results = {}
    for label in ("both", "nsi_only", "quality_only"):
        long_ret = _monthly_series(bundle["long_q5_rows"][label])
        short_gross = -long_ret
        net_tiers = _net_tiers(short_gross)

        hm = bundle["holdings_meta"][label]
        avg_n = float(hm["n"].mean()) if len(hm) else np.nan

        reg_gross = six_factor_regression_local(short_gross, factors_monthly, is_long_short=True)
        reg_net = {tier: six_factor_regression_local(s, factors_monthly, is_long_short=True) for tier, s in net_tiers.items()}

        results[label] = {
            "avg_n_per_month": avg_n,
            "n_months": len(long_ret.dropna()),
            "gross_cagr": _cagr(short_gross),
            "net_cagr": {tier: _cagr(s) for tier, s in net_tiers.items()},
            "sharpe_net_base": _sharpe_monthly(net_tiers["BASE"]),
            "max_dd_short_side_net_base": _max_dd(net_tiers["BASE"]),
            "gross_alpha_annual": reg_gross["alpha_annual"], "gross_alpha_tstat": reg_gross["tstat"], "gross_r2": reg_gross["r_squared"], "gross_betas": reg_gross["betas"],
            "net_alpha": {tier: r["alpha_annual"] for tier, r in reg_net.items()},
            "net_alpha_tstat": {tier: r["tstat"] for tier, r in reg_net.items()},
            "net_betas": {tier: r["betas"] for tier, r in reg_net.items()},
            "short_gross_monthly": short_gross,
            "net_tiers_monthly": net_tiers,
        }

    coverage = bundle["coverage"]
    both_hm = bundle["holdings_meta"]["both"]
    time_splits = {}
    for label_p, (s_yr, e_yr) in (("2001-2009", (2001, 2009)), ("2010-2018", (2010, 2018))):
        sub = bundle["long_q5_rows"]["both"]
        sub = sub[(sub.realization_month.dt.year >= s_yr) & (sub.realization_month.dt.year <= e_yr)] if len(sub) else sub
        m = _monthly_series(sub)
        short = -m
        try:
            reg = six_factor_regression_local(short, factors_monthly, is_long_short=True)
            time_splits[label_p] = {"gross_alpha_annual": reg["alpha_annual"], "tstat": reg["tstat"], "n_months": len(short.dropna())}
        except Exception:
            time_splits[label_p] = {"gross_alpha_annual": np.nan, "tstat": np.nan, "n_months": len(short.dropna())}

    size_splits = {}
    for bucket in ("Large", "Mid"):
        sub = bundle["long_q5_rows"]["both"]
        sub = sub[sub.size_bucket == bucket] if len(sub) else sub
        m = _monthly_series(sub)
        short = -m
        try:
            reg = six_factor_regression_local(short, factors_monthly, is_long_short=True)
            size_splits[bucket] = {"gross_alpha_annual": reg["alpha_annual"], "tstat": reg["tstat"], "n_months": len(short.dropna())}
        except Exception:
            size_splits[bucket] = {"gross_alpha_annual": np.nan, "tstat": np.nan, "n_months": len(short.dropna())}

    return {
        "groups": results,
        "coverage": {
            "avg_sp500_members": float(coverage.sp500_members.mean()) if len(coverage) else np.nan,
            "avg_restricted_universe": float(coverage.restricted_universe.mean()) if len(coverage) else np.nan,
        },
        "time_splits": time_splits,
        "size_splits": size_splits,
        "factors_monthly": factors_monthly,
    }


def cost_screen(primary: dict) -> str:
    alpha = primary["gross_alpha_annual"]
    if pd.isna(alpha):
        return "INSUFFICIENT_DATA"
    if alpha < BORROW_TIERS["BASE"]:
        return "COST_DOMINATES"
    if alpha < BORROW_TIERS["STRESS"]:
        return "MARGINAL"
    return "VIABLE_TIER"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "sharadar.db"))
    parser.add_argument("--start", default="2001-01-01")
    parser.add_argument("--end", default="2018-12-31")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    prereg_path = ROOT / "research" / "preregistrations" / "short_sleeve_v1.json"
    if not prereg_path.exists():
        raise SystemExit(f"Missing preregistration: {prereg_path}")

    bundle = run(args.db, args.start, args.end)
    factors_monthly = build_factors_monthly()

    out_dir = Path(args.out) if args.out else ROOT / "reports" / "short_sleeve"
    from datetime import datetime, timezone
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / tag
    out_path.mkdir(parents=True, exist_ok=True)

    coverage = bundle["coverage"]
    coverage_summary = {
        "avg_sp500_members": float(coverage.sp500_members.mean()) if len(coverage) else np.nan,
        "avg_restricted_universe": float(coverage.restricted_universe.mean()) if len(coverage) else np.nan,
    }

    screen, primary_minimal = primary_cost_screen(bundle, factors_monthly)
    print(f"\nCOST SCREEN VERDICT: {screen}")
    print(f"Gross short alpha (both Q5): {primary_minimal['gross_alpha_annual']:.4f}  t={primary_minimal['gross_alpha_tstat']:.2f}")
    print(f"Avg names/month (both Q5): {primary_minimal['avg_n_per_month']:.1f}")
    for tier, rate in BORROW_TIERS.items():
        net_alpha_approx = primary_minimal["gross_alpha_annual"] - rate - MARGIN_DRAG_ANNUAL
        print(f"  cost-screen net alpha {tier} (alpha - borrow - margin, approx): {net_alpha_approx:.4f}")

    if screen == "COST_DOMINATES":
        print("\nCOST_DOMINATES -- stopping per preregistration. No factor regression, sub-groups, or combined analysis computed.")
        summary = {"cost_screen": screen, "coverage": coverage_summary, "primary_gross_alpha_annual": primary_minimal["gross_alpha_annual"], "primary_gross_alpha_tstat": primary_minimal["gross_alpha_tstat"], "primary_avg_n_per_month": primary_minimal["avg_n_per_month"]}
        (out_path / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        primary_minimal["short_gross_monthly"].to_csv(out_path / "both_short_gross_monthly.csv", header=["short_gross_return"])
        print(f"\nResults written to: {out_path}")
        return out_path

    analysis = analyze(bundle, factors_monthly)
    analysis["coverage"] = coverage_summary
    primary = analysis["groups"]["both"]
    for tier in BORROW_TIERS:
        print(f"  net alpha {tier} (regressed): {primary['net_alpha'][tier]:.4f}  t={primary['net_alpha_tstat'][tier]:.2f}")

    serializable = {"coverage": analysis["coverage"], "time_splits": analysis["time_splits"], "size_splits": analysis["size_splits"], "cost_screen": screen, "groups": {}}
    for label, g in analysis["groups"].items():
        serializable["groups"][label] = {k: v for k, v in g.items() if k not in ("short_gross_monthly", "net_tiers_monthly")}
        g["short_gross_monthly"].to_csv(out_path / f"{label}_short_gross_monthly.csv", header=["short_gross_return"])
        for tier, s in g["net_tiers_monthly"].items():
            s.to_csv(out_path / f"{label}_short_net_{tier}_monthly.csv", header=[f"short_net_{tier}_return"])

    (out_path / "summary.json").write_text(json.dumps(serializable, indent=2, default=str), encoding="utf-8")
    print(f"\nResults written to: {out_path}")

    if screen == "VIABLE_TIER" and primary["net_alpha_tstat"]["BASE"] > 2.0 and primary["avg_n_per_month"] >= 20 and primary["gross_betas"].get("Mkt-RF", 0) < 0:
        v31_path = ROOT / "reports" / "negative_selection"
        print(f"Primary VIABLE by falsification criteria -- combined long-short analysis requires the live V3.1 weekly series (see {v31_path}); run as a follow-up given this is gated as secondary.")

    return out_path


if __name__ == "__main__":
    main()
