"""Composite quality factor. Expected: positive RMW/CMA, modest SMB/HML, market beta near one."""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3
import numpy as np
import pandas as pd
from utils.research_utils import (
    factor_regression, load_universe_candidates, load_price_panel, load_fundamentals_panel,
    load_marketcap_panel, panel_universe_as_of, panel_latest_asof, panel_marketcaps_asof,
    panel_month_values, size_tercile_top_returns, size_tercile_breakdown, winsorize_by_group,
    long_short_spread_regression,
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
    monthly_returns: pd.Series; factor_returns: pd.DataFrame; alpha_annual: float; alpha_tstat: float; alpha_pvalue: float; betas: dict; r_squared: float; n_months: int; portfolio_history: pd.DataFrame; quintile_returns: pd.DataFrame; size_terciles: dict; period_results: dict; lookahead_violations: int; sensitivity: dict; sector_breakdown: dict; dimensions_used: dict; avg_universe_size: float; long_short_alpha: float = float('nan'); long_short_tstat: float = float('nan'); long_short_rsquared: float = float('nan'); long_short_betas: dict = None

COMPONENTS=('roe','roa','gm','dte','icr','cfe')
_FUND_COLS = ['netinc','equity','assets','revenue','cor','opinc','debt','intexp','ebit','ncfo']

def _run_core(price_panel, fund_panel, mc_panel, candidates, months, drop=None, symbols=None, winsorize=True, compute_size_terciles=True):
    out=[]; hist=[]; violations=0; dims={}
    universe_sizes=[]; small_rows=[]; mid_rows=[]; large_rows=[]
    for dt in months:
        syms = list(symbols) if symbols is not None else panel_universe_as_of(price_panel, candidates, dt)
        if not syms: continue
        universe_sizes.append(len(syms))
        latest = panel_latest_asof(fund_panel, dt, dimension_priority=('ART', 'ARY'))
        if latest.empty: continue
        latest = latest[latest.ticker.isin(syms)]
        if latest.empty: continue
        for dim, n in latest.dimension.value_counts().items(): dims[dim] = dims.get(dim, 0) + int(n)
        violations += int((latest.datekey > dt).sum())
        raw_scores=[]
        for r in latest.itertuples():
            vals={'roe':r.netinc/r.equity if r.equity>0 else np.nan,'roa':r.netinc/r.assets if r.assets!=0 else np.nan,'gm':(r.revenue-r.cor)/r.revenue if r.revenue>0 else np.nan,'dte':-r.debt/r.equity if r.equity>0 else np.nan,'icr':r.ebit/r.intexp if pd.notna(r.intexp) and r.intexp>0 else np.nan,'cfe':r.ncfo/abs(r.netinc) if r.netinc!=0 else np.nan}
            vals={k:v for k,v in vals.items() if k!=drop}; good={k:v for k,v in vals.items() if pd.notna(v)}
            if len(good)<4: continue
            raw_scores.append({'ticker':r.ticker, **good})
        if len(raw_scores)<5: continue
        # Rank each quality component across securities, then average the
        # cross-sectional percentile ranks. Ranking within one security would
        # measure only its internal metric ordering, not quality relative to peers.
        raw=pd.DataFrame(raw_scores).set_index('ticker')
        ranked=raw.rank(pct=True, ascending=True)
        s=ranked.mean(axis=1).sort_values(ascending=False).rename('score').reset_index(); s['q']=(np.floor(np.arange(len(s))*5/len(s))+1).astype(int)
        cur_vals = panel_month_values(price_panel, dt.to_period('M'))
        next_vals = panel_month_values(price_panel, (dt+pd.offsets.MonthEnd(1)).to_period('M'))
        s['a']=s.ticker.map(cur_vals); s['b']=s.ticker.map(next_vals)
        s=s.dropna(subset=['a','b'])
        if s.empty: continue
        s['return']=s.b/s.a-1
        s['weight']=1/s.groupby('q')['ticker'].transform('count')
        # `return` here is the price change from dt to dt+1mo (formation
        # month to realization month) -- it belongs to the REALIZATION
        # month for comparison against calendar-dated factor returns.
        # Labeling it with `dt` (the formation month) instead was a real
        # bug: it shifted the whole portfolio series one month early
        # relative to the Fama-French factors, regressing month t+1's
        # stock returns against month t's market return. Confirmed
        # empirically on net_share_issuance -- shifting the series forward
        # one month took its market correlation from 0.05 to 0.91. This
        # affected every factor-regression output (alpha, t-stat,
        # R-squared, all factor loadings) computed this session; it did
        # NOT affect the quintile spread itself (a same-month comparison).
        realization_month = dt + pd.offsets.MonthEnd(1)
        gdf=s[['ticker','q','score','return','weight']].copy(); gdf['date']=realization_month
        if winsorize:
            gdf = winsorize_by_group(gdf, 'return', 'date')
        hist.extend(gdf.to_dict('records'))
        out.append(gdf.groupby('q')['return'].mean().rename(realization_month))
        if compute_size_terciles:
            # Top score quintile (highest composite score, since
            # ascending=False) within each of Small/Mid/Large market-cap
            # terciles -- tests whether the effect concentrates where
            # institutional capital can't crowd it away.
            mktcaps = panel_marketcaps_asof(mc_panel, gdf.ticker.tolist(), dt)
            gdf['mktcap'] = gdf.ticker.map(mktcaps)
            tercile_rets = size_tercile_top_returns(gdf, signal_col='score', return_col='return', mktcap_col='mktcap', ascending=False)
            small_rows.append((realization_month, tercile_rets['Small'])); mid_rows.append((realization_month, tercile_rets['Mid'])); large_rows.append((realization_month, tercile_rets['Large']))
    quint=pd.DataFrame(out); quint.columns=[f'Q{i}' for i in quint.columns] if len(quint.columns)==5 else quint.columns
    avg_universe_size=float(np.mean(universe_sizes)) if universe_sizes else 0.0
    size_series={'Small':pd.Series(dict(small_rows)),'Mid':pd.Series(dict(mid_rows)),'Large':pd.Series(dict(large_rows))} if compute_size_terciles else None
    return quint,hist,violations,dims,avg_universe_size,size_series

def run(db_path:str,as_of_start:str,as_of_end:str,symbols=None,winsorize=True)->StrategyResult:
    con = sqlite3.connect(db_path)
    months = pd.date_range(as_of_start, as_of_end, freq='ME')
    # Panel loading: a handful of bulk queries covering the whole date
    # range and shared across all 7 _run_core calls below (main + 6
    # leave-one-component-out sensitivity passes), instead of one query per
    # rebalance month re-scanning that ticker's whole accumulated history
    # every time, times 7. This is what the old per-date _FUND_CACHE/
    # _PRICE_CACHE dicts were working around; passing preloaded panels down
    # makes that caching layer unnecessary.
    if symbols is not None:
        universe_tickers = list(symbols); candidates = None
    else:
        candidates = load_universe_candidates(con)
        window_start = pd.Timestamp(as_of_start) - pd.Timedelta(days=400)
        window_end = pd.Timestamp(as_of_end)
        candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
        universe_tickers = candidates.ticker.tolist()
    load_start = (months.min() - pd.Timedelta(days=400)).strftime('%Y-%m-%d')
    load_end = (months.max() + pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')
    price_panel = load_price_panel(con, universe_tickers, load_start, load_end)
    fund_panel = load_fundamentals_panel(con, universe_tickers, load_start, load_end, _FUND_COLS)
    mc_panel = load_marketcap_panel(con, universe_tickers, as_of_start, load_end) if symbols is None else pd.DataFrame(columns=['ticker','date','marketcap'])
    con.close()

    quint,hist,v,dims,avg_universe_size,size_series=_run_core(price_panel,fund_panel,mc_panel,candidates,months,symbols=symbols,winsorize=winsorize,compute_size_terciles=True)
    monthly=quint.get('Q1',pd.Series(dtype=float)); factors,a,t,p,b,r2=factor_regression(monthly,as_of_start); sensitivity={}
    for c in COMPONENTS:
        q,_,_,_,_,_= _run_core(price_panel,fund_panel,mc_panel,candidates,months,drop=c,symbols=symbols,winsorize=winsorize,compute_size_terciles=False); x=q.get('Q1',pd.Series(dtype=float));
        try: _,aa,tt,*_=factor_regression(x,as_of_start); sensitivity[c]={'alpha_annual':aa,'tstat':tt}
        except Exception: sensitivity[c]={'alpha_annual':np.nan,'tstat':np.nan}
    periods={}
    for name,s,e in [('2000-2009',as_of_start,'2009-12-31'),('2010-2019','2010-01-01','2019-12-31'),('2020-present','2020-01-01',as_of_end)]:
        x=monthly[(monthly.index>=s)&(monthly.index<=e)]
        try: _,aa,tt,*_=factor_regression(x,s); periods[name]={'alpha_annual':aa,'tstat':tt,'n_months':len(x)}
        except Exception: periods[name]={'alpha_annual':np.nan,'tstat':np.nan,'n_months':len(x)}
    size_terciles = size_tercile_breakdown(size_series, as_of_start)
    # Q1-minus-Q5 long-short spread: the standard academic test for these
    # anomalies. A long-only leg's alpha can just reflect average factor
    # exposure; the spread, which cancels common exposure, is the more
    # decisive test of whether the signal carries genuine information.
    try:
        _, _, ls_alpha, ls_t, _, ls_betas, ls_r2 = long_short_spread_regression(quint, as_of_start)
    except Exception:
        ls_alpha, ls_t, ls_r2, ls_betas = np.nan, np.nan, np.nan, {}
    return StrategyResult(monthly,factors,a,t,p,b,r2,len(monthly),pd.DataFrame(hist),quint,size_terciles,periods,v,sensitivity,{},dims,avg_universe_size,ls_alpha,ls_t,ls_r2,ls_betas)

if __name__=='__main__':
    r=run('data/sharadar.db','2000-01-01','2024-12-31'); print('QUALITY — FULL UNIVERSE'); print('Lookahead violations:',r.lookahead_violations)
