"""Preregistered residual / idiosyncratic intermediate-horizon momentum.

This is deliberately three fixed economic hypotheses, not a parameter sweep.
It uses daily PIT eligibility and decomposes a stock's return into the eligible
universe's contemporaneous equal-weight market component plus its eligible
sector's return in excess of that market component.  The remainder is the
stock-specific return accumulated by each registered formation window.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm

from strategies.ibs_conditioning import load_factors, load_spy_returns
from strategies.momentum import _six_factor_regression, _weekly_to_monthly
from strategies.signal_library import _rsi, _wide, build_eligibility, load_full_ohlc_panel
from utils.research_utils import load_marketcap_panel, load_universe_candidates


ROOT = Path(__file__).resolve().parents[1]
PREREG_DIR = ROOT / "research" / "preregistrations"
SPECS = {
    "residual_momentum_60d": {"lookback": 60, "skip": 0, "file": "residual_momentum_60d_v1.json", "sha256": "8c94b3d6396f4aaf59acb70647819c223d7ea0c80e91da90ff7550b2a0892f7e"},
    "residual_momentum_126d": {"lookback": 126, "skip": 0, "file": "residual_momentum_126d_v1.json", "sha256": "689a84286998ab4752842177f008d065d66f0de7020c30f5c6ccdcc8b9145bec"},
    # The frozen specification is inclusive t-251 through t-20: 232 returns.
    "residual_momentum_252d_ex20": {"lookback": 232, "skip": 20, "file": "residual_momentum_252d_ex20_v1.json", "sha256": "44f7d016e1555e50d68808f424e2bda94e8459ed9447d82ab7f7663cc0e705c7"},
}
FORWARD_HORIZONS = (1, 5, 10, 20)
FAMILYWISE_T = 2.39
EXCLUDED_SECTORS = ["Financial Services", "Real Estate", "Healthcare"]
COST_BPS = 10.0


def _hac_mean(series: pd.Series, lags: int) -> dict:
    series = series.dropna()
    if len(series) < 30:
        return {"mean": np.nan, "tstat": np.nan, "n": len(series)}
    model = sm.OLS(series, np.ones((len(series), 1))).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return {"mean": float(model.params.iloc[0]), "tstat": float(model.tvalues.iloc[0]), "n": len(series)}


def _ann(mean_return: float, horizon: int) -> float:
    return (1 + mean_return) ** (252 / horizon) - 1 if pd.notna(mean_return) and mean_return > -1 else np.nan


def _max_drawdown(returns: pd.Series) -> float:
    wealth = (1 + returns.fillna(0)).cumprod()
    return float((wealth / wealth.cummax() - 1).min()) if len(wealth) else np.nan


def _residual_returns(C: pd.DataFrame, eligible: pd.DataFrame, sectors: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """PIT day-by-day market + sector decomposition; no forward membership."""
    stock = C.pct_change(fill_method=None)
    usable = stock.where(eligible & stock.notna())
    market = usable.mean(axis=1)
    sector_component = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    for sector, names in sectors.groupby(sectors).groups.items():
        cols = [c for c in names if c in C.columns]
        if cols:
            sector_return = usable[cols].mean(axis=1)
            sector_component.loc[:, cols] = np.broadcast_to((sector_return - market).to_numpy()[:, None], (len(C.index), len(cols)))
    residual = stock.sub(market, axis=0) - sector_component
    return residual.where(eligible), sector_component, market


def _scores(residual: pd.DataFrame, C: pd.DataFrame, H: pd.DataFrame, L: pd.DataFrame, eligible: pd.DataFrame, sectors: pd.Series) -> tuple[dict[str, pd.DataFrame], dict[str, pd.DataFrame]]:
    raw = {}
    for name, spec in SPECS.items():
        accumulated = residual.rolling(spec["lookback"], min_periods=spec["lookback"]).sum()
        if spec["skip"]:
            accumulated = accumulated.shift(spec["skip"])
        raw[name] = accumulated.where(eligible)
    ibs = (C - L) / (H - L).replace(0, np.nan)
    ibs_score = (1 - ibs.rolling(5, min_periods=5).mean()).where(eligible).rank(axis=1, pct=True)
    rsi_score = (100 - _rsi(C, 2)).where(eligible).rank(axis=1, pct=True)
    ret63 = C / C.shift(63) - 1
    sector_rs = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    for _, names in sectors.groupby(sectors).groups.items():
        cols = [c for c in names if c in C.columns]
        if cols:
            sector_rs.loc[:, cols] = ret63[cols].where(eligible[cols]).rank(axis=1, pct=True)
    tue = pd.DataFrame(0.0, index=C.index, columns=C.columns)
    monday = C.index.dayofweek == 0
    tue.loc[monday] = (C.loc[monday] < C.shift(1).loc[monday]).astype(float)
    v31 = .62 * ibs_score + .22 * rsi_score + .10 * sector_rs + .06 * tue
    return raw, {"IBS": ibs_score, "RSI2": rsi_score, "Sector_RS": sector_rs, "Turnaround_Tuesday": tue, "Composite_V3_1_raw": v31}


def _quintile_day(score: pd.Series, forward: pd.Series) -> tuple[dict, float]:
    frame = pd.DataFrame({"score": score, "forward": forward}).dropna()
    if len(frame) < 100:
        return ({f"Q{i}": np.nan for i in range(1, 6)}, np.nan)
    frame = frame.sort_values("score", ascending=True)
    frame["q"] = np.floor(np.arange(len(frame)) * 5 / len(frame)).astype(int) + 1
    q = frame.groupby("q").forward.mean().to_dict()
    return ({f"Q{i}": q.get(i, np.nan) for i in range(1, 6)}, float(frame.score.corr(frame.forward, method="spearman")))


def _characterize(score: pd.DataFrame, C: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    qrows, icrows, summary = [], [], {}
    dates = C.index[(C.index >= start) & (C.index <= end)]
    for h in FORWARD_HORIZONS:
        fwd = C.shift(-h).div(C).sub(1)
        rows, ics = [], []
        for d in dates:
            q, ic = _quintile_day(score.loc[d], fwd.loc[d])
            q["date"] = d; q["horizon"] = h
            rows.append(q)
            ics.append({"date": d, "horizon": h, "ic": ic})
        qdf = pd.DataFrame(rows).set_index("date")
        idf = pd.DataFrame(ics).set_index("date")
        qrows.append(qdf.reset_index()); icrows.append(idf.reset_index())
        spread = qdf.Q5 - qdf.Q1
        test, ict = _hac_mean(spread, h), _hac_mean(idf.ic, h)
        summary[h] = {
            **{f"Q{i}_mean": float(qdf[f"Q{i}"].mean()) for i in range(1, 6)},
            "spread_mean": test["mean"], "spread_annualized": _ann(test["mean"], h), "spread_hac_tstat": test["tstat"], "spread_n": test["n"],
            "ic_mean": ict["mean"], "ic_hac_tstat": ict["tstat"], "ic_n": ict["n"],
            "monotonic_q_means": bool(all(qdf[f"Q{i}"].mean() <= qdf[f"Q{i+1}"].mean() for i in range(1, 5))),
        }
    return pd.concat(qrows, ignore_index=True), pd.concat(icrows, ignore_index=True), summary


def _rebalance_dates(index: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp, step: int) -> list[pd.Timestamp]:
    valid = index[(index >= start) & (index <= end)]
    return list(valid[::step])


def _implementation(score: pd.DataFrame, C: pd.DataFrame, O: pd.DataFrame, marketcaps: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, step: int = 20, compute_factors: bool = True) -> tuple[pd.Series, dict, pd.DataFrame]:
    dates = _rebalance_dates(C.index, start, end, step)
    returns, spread_returns, rows = [], [], []
    prior_long, prior_short = set(), set()
    pos = {d: i for i, d in enumerate(C.index)}
    for d in dates:
        i = pos[d]
        if i + step + 1 >= len(C.index):
            continue
        signal = score.loc[d].dropna().sort_values(ascending=False)
        n = len(signal) // 5
        if n < 20:
            continue
        held = set(signal.head(n).index)
        short = set(signal.tail(n).index)
        entry, exit_ = C.index[i + 1], C.index[i + 1 + step]
        individual = O.loc[exit_, list(held)].div(O.loc[entry, list(held)]).sub(1).dropna()
        short_individual = O.loc[exit_, list(short)].div(O.loc[entry, list(short)]).sub(1).dropna()
        if len(individual) < 20 or len(short_individual) < 20:
            continue
        turnover = 1.0 if not prior_long else len(held.symmetric_difference(prior_long)) / (2 * max(len(held), len(prior_long)))
        short_turnover = 1.0 if not prior_short else len(short.symmetric_difference(prior_short)) / (2 * max(len(short), len(prior_short)))
        cost = turnover * COST_BPS / 10_000
        gross = float(individual.mean())
        short_gross = float(short_individual.mean())
        spread_gross = gross - short_gross
        returns.append((exit_, gross - cost))
        spread_returns.append((exit_, spread_gross))
        cap = marketcaps.loc[d].reindex(list(held)) if d in marketcaps.index else pd.Series(dtype=float)
        rows.append({"formation_date": d, "entry_date": entry, "exit_date": exit_, "n_holdings": len(individual), "gross_return": gross, "net_return": gross-cost, "turnover": turnover, "cost": cost, "short_gross_return": short_gross, "spread_gross_return": spread_gross, "short_turnover": short_turnover, "median_marketcap": cap.median(), "median_adv_proxy": np.nan})
        prior_long, prior_short = held, short
    series = pd.Series(dict(returns), name="net_return").sort_index()
    spread_series = pd.Series(dict(spread_returns), name="spread_gross_return").sort_index()
    periods = pd.DataFrame(rows)
    months = _weekly_to_monthly(series) if len(series) else pd.Series(dtype=float)
    spread_months = _weekly_to_monthly(spread_series) if len(spread_series) else pd.Series(dtype=float)
    empty_factor = {"alpha_annual": np.nan, "tstat": np.nan, "betas": {}, "r_squared": np.nan}
    factor = _six_factor_regression(months, str(start.date()), is_long_short=False) if compute_factors else empty_factor
    spread_factor = _six_factor_regression(spread_months, str(start.date()), is_long_short=True) if compute_factors else empty_factor
    years = max((series.index.max() - series.index.min()).days / 365.25, 1) if len(series) > 1 else np.nan
    gross_cagr = (1 + periods.gross_return).prod() ** (1 / years) - 1 if len(periods) > 1 else np.nan
    net_cagr = (1 + series).prod() ** (1 / years) - 1 if len(series) > 1 else np.nan
    metrics = {"gross_cagr": gross_cagr, "net_cagr": net_cagr, "turnover_annual": float(periods.turnover.mean() * 252 / step) if len(periods) else np.nan, "cost_drag_annual_simple": float(periods.cost.mean() * 252 / step) if len(periods) else np.nan, "sharpe": float(series.mean() / series.std(ddof=1) * np.sqrt(252 / step)) if len(series) > 2 and series.std() else np.nan, "max_drawdown": _max_drawdown(series), "factor_alpha_annual": factor["alpha_annual"], "factor_alpha_tstat": factor["tstat"], "factor_betas": factor["betas"], "factor_r2": factor["r_squared"], "spread_factor_alpha_annual": spread_factor["alpha_annual"], "spread_factor_alpha_tstat": spread_factor["tstat"], "spread_factor_betas": spread_factor["betas"], "spread_factor_r2": spread_factor["r_squared"], "n_rebalances": len(series)}
    return series, metrics, periods


def _classify(primary: dict, implementation: dict) -> str:
    """Apply the frozen three-hypothesis family classification literally."""
    alpha = implementation["spread_factor_alpha_annual"]
    alpha_t = implementation["spread_factor_alpha_tstat"]
    if ((primary["spread_mean"] <= 0 and primary["spread_hac_tstat"] <= -1)
            or (alpha <= 0 and alpha_t <= -1)):
        return "FALSIFIED"
    if primary["spread_hac_tstat"] >= FAMILYWISE_T and primary["ic_mean"] > 0 and alpha > 0:
        return "SUPPORTED"
    return "UNRESOLVED / UNDERPOWERED"


def _regimes(score: pd.DataFrame, C: pd.DataFrame, marketcaps: pd.DataFrame, eligible: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    fwd = C.shift(-20).div(C).sub(1); rows = []
    market = C.pct_change(fill_method=None).where(eligible).mean(axis=1)
    high_vol = market.rolling(20).std() >= market.rolling(20).std().median()
    dates = C.index[(C.index >= start) & (C.index <= end)]
    for d in dates:
        s, r = score.loc[d], fwd.loc[d]
        frame = pd.DataFrame({"s": s, "r": r, "cap": marketcaps.loc[d] if d in marketcaps.index else np.nan}).dropna(subset=["s", "r"])
        if len(frame) < 100: continue
        def spread(x):
            x=x.sort_values("s"); n=len(x)//5; return x.r.iloc[-n:].mean()-x.r.iloc[:n].mean() if n else np.nan
        for label, subset in (("all", frame), ("small", frame[frame.cap.rank(pct=True)<=1/3]), ("large", frame[frame.cap.rank(pct=True)>2/3])):
            rows.append({"date":d,"bucket":label,"spread":spread(subset),"high_vol":bool(high_vol.loc[d]),"bull":bool(market.rolling(200).mean().loc[d] > 0)})
    out=[]
    data=pd.DataFrame(rows)
    for key, sub in [("early", data[data.date.dt.year<=2009]), ("late", data[data.date.dt.year>=2010]), ("bull",data[data.bull]),("bear",data[~data.bull]),("high_vol",data[data.high_vol]),("low_vol",data[~data.high_vol])]:
        for bucket, g in sub.groupby("bucket"):
            out.append({"regime":key,"bucket":bucket,"mean_spread":g.spread.mean(),"n":g.spread.notna().sum()})
    return pd.DataFrame(out)


def run(db_path: str, start: str = "2001-01-01", end: str = "2018-12-31") -> dict:
    start_ts,end_ts=pd.Timestamp(start),pd.Timestamp(end)
    con=sqlite3.connect(db_path)
    candidates=load_universe_candidates(con, exclude_sectors=[], exclude_fama_sectors=EXCLUDED_SECTORS)
    load_start=(start_ts-pd.Timedelta(days=600)).strftime("%Y-%m-%d")
    candidates=candidates[(candidates.firstpricedate<=end_ts)&(candidates.lastpricedate.isna()|(candidates.lastpricedate>=pd.Timestamp(load_start)))].reset_index(drop=True)
    px=load_full_ohlc_panel(con,candidates.ticker.tolist(),load_start,(end_ts+pd.Timedelta(days=45)).strftime("%Y-%m-%d"))
    mc=load_marketcap_panel(con,candidates.ticker.tolist(),load_start,end)
    con.close()
    O,H,L,C,V=(_wide(px,x) for x in ("open","high","low","close","volume"))
    eligible=build_eligibility(O,H,L,C,V,candidates); sectors=candidates.set_index("ticker").sector
    residual,sector_component,market=_residual_returns(C,eligible,sectors)
    raw,existing=_scores(residual,C,H,L,eligible,sectors)
    mcwide=mc.pivot_table(index="date",columns="ticker",values="marketcap",aggfunc="last").reindex(C.index).ffill(limit=45)
    results={}; correlations=[]; all_strat={}
    vret,_,_=_implementation(existing["Composite_V3_1_raw"],C,O,mcwide,start_ts,end_ts,compute_factors=False)
    for name, score in raw.items():
        q,ic,summary=_characterize(score,C,start_ts,end_ts)
        ret,impl,periods=_implementation(score,C,O,mcwide,start_ts,end_ts)
        all_strat[name]=ret
        primary=summary[20]
        cls = _classify(primary, impl)
        for peer, peer_score in existing.items():
            vals=[]
            for d in score.index[(score.index>=start_ts)&(score.index<=end_ts)]:
                vals.append(score.loc[d].corr(peer_score.loc[d], method="spearman"))
            correlations.append({"signal":name,"comparison":peer,"cross_sectional_spearman_mean":pd.Series(vals).mean()})
        results[name]={"quintiles":q,"ic":ic,"summary":summary,"implementation_returns":ret,"implementation":impl,"periods":periods,"regimes":_regimes(score,C,mcwide,eligible,start_ts,end_ts),"classification":cls,"universe":pd.DataFrame({"date":eligible.index,"n_stocks":eligible.sum(axis=1).values,"median_marketcap":mcwide.where(eligible).median(axis=1).values,"median_dollar_volume":(C*V).where(eligible).median(axis=1).values})}
    corr=pd.DataFrame(correlations)
    for name, ret in all_strat.items():
        # Return correlation uses same 20-session, next-open implementation dates.
        corr.loc[corr.signal==name,"strategy_return_correlation_v31"] = ret.corr(vret)
    return {"results":results,"correlations":corr,"market_component":market,"sector_component":sector_component}


def _write_report(bundle: dict, out: Path, start: str, end: str) -> None:
    lines=["# Residual / idiosyncratic intermediate-horizon momentum", "", f"Development-only characterization: {start} through {end}. The consumed 2019+ holdout was not evaluated.", "", "| Hypothesis | Classification | 20d spread annualized | HAC t | IC | Spread factor alpha | Long-only net CAGR | Turnover | Max DD |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name,r in bundle["results"].items():
        s=r["summary"][20]; m=r["implementation"]
        lines.append(f"| {name} | {r['classification']} | {s['spread_annualized']:.2%} | {s['spread_hac_tstat']:.2f} | {s['ic_mean']:.4f} | {m['spread_factor_alpha_annual']:.2%} | {m['net_cagr']:.2%} | {m['turnover_annual']:.2%} | {m['max_drawdown']:.2%} |")
    corr = bundle["correlations"]
    header = "| " + " | ".join(corr.columns) + " |"
    separator = "|" + "|".join("---" for _ in corr.columns) + "|"
    rows = ["| " + " | ".join("" if pd.isna(v) else f"{v:.6f}" if isinstance(v, (float, np.floating)) else str(v) for v in row) + " |" for row in corr.itertuples(index=False, name=None)]
    lines += ["", "## Orthogonality", "", header, separator, *rows, "", "## Integrity", "", "Residual return is computed only from same-date PIT eligible stock returns and same-date sector grouping. Factor regression reuses the established monthly FF5+UMD alignment helper; the daily signal-library's warm-up boundary defect is not reused. Static Sharadar sector labels remain a data limitation: historical classification changes are unavailable in this bundle."]
    (out/"report.md").write_text("\n".join(lines),encoding="utf-8")


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("--db",default=str(ROOT/"data"/"sharadar.db")); parser.add_argument("--start",default="2001-01-01"); parser.add_argument("--end",default="2018-12-31"); parser.add_argument("--acknowledge-used-historical-data",action="store_true")
    args=parser.parse_args()
    if not args.acknowledge_used_historical_data: raise SystemExit("Refusing historical run: pass --acknowledge-used-historical-data")
    for spec in SPECS.values():
        p=PREREG_DIR/spec["file"]
        if not p.exists(): raise SystemExit(f"Missing preregistration: {p}")
        actual_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        if actual_hash != spec["sha256"]: raise SystemExit(f"Preregistration drift: {p} has SHA256 {actual_hash}, expected {spec['sha256']}")
    bundle=run(args.db,args.start,args.end)
    tag=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out=ROOT/"reports"/"residual_momentum"/tag; out.mkdir(parents=True,exist_ok=False)
    records=[]
    for name,r in bundle["results"].items():
        r["quintiles"].to_csv(out/f"{name}_quintile_returns.csv",index=False); r["ic"].to_csv(out/f"{name}_ic.csv",index=False); r["periods"].to_csv(out/f"{name}_implementation_periods.csv",index=False); r["regimes"].to_csv(out/f"{name}_regimes.csv",index=False); r["universe"].to_csv(out/f"{name}_universe.csv",index=False)
        records.append({"hypothesis":name,"classification":r["classification"],**{f"h{h}_{k}":v for h,d in r["summary"].items() for k,v in d.items()},**r["implementation"]})
    pd.DataFrame(records).to_csv(out/"hypothesis_summary.csv",index=False); bundle["correlations"].to_csv(out/"orthogonality.csv",index=False)
    _write_report(bundle,out,args.start,args.end)
    db = Path(args.db).resolve()
    manifest={"run_utc":datetime.now(timezone.utc).isoformat(),"evidence_stage":"historical development; NOT fresh validation","development_period":[args.start,args.end],"holdout":"2019+ not evaluated","cost_bps":COST_BPS,"familywise_t":FAMILYWISE_T,"correction_note":"Supersedes incomplete 20260910T163536Z artifact: corrected frozen t-251..t-20 window, uses Q5-Q1 factor alpha for classification, and removes optional report dependency.","preregistrations":{x["file"]:x["sha256"] for x in SPECS.values()},"source_database":str(db),"source_database_size":db.stat().st_size if db.exists() else None,"source_database_mtime_utc":datetime.fromtimestamp(db.stat().st_mtime,timezone.utc).isoformat() if db.exists() else None,"result_files_sha256":{}}
    for result_file in sorted(out.iterdir()):
        if result_file.is_file(): manifest["result_files_sha256"][result_file.name]=hashlib.sha256(result_file.read_bytes()).hexdigest()
    (out/"run_manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print(out)


if __name__ == "__main__": main()
