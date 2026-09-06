"""Accruals anomaly study with strict datekey point-in-time alignment.

Expected exposures: positive RMW/CMA, market near 1, modest positive SMB/HML.
Expected subgroup pattern: strongest alpha in small caps and possible post-2010 decay.
"""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3
import numpy as np
import pandas as pd
from utils.research_utils import factor_regression

@dataclass
class StrategyResult:
    monthly_returns: pd.Series; factor_returns: pd.DataFrame; alpha_annual: float; alpha_tstat: float; alpha_pvalue: float; betas: dict; r_squared: float; n_months: int; portfolio_history: pd.DataFrame; quintile_returns: pd.DataFrame; size_terciles: dict; period_results: dict; lookahead_violations: int; dimensions_used: dict

def _universe(db, dt):
    try:
        from scripts.build_universe import get_universe
    except ImportError:
        from build_universe import get_universe
    return get_universe(db, dt)

def _returns(con, tickers, start, end):
    if not tickers: return pd.DataFrame()
    q=','.join('?'*len(tickers)); return pd.read_sql_query(f'SELECT ticker,date,closeadj,closeunadj,close FROM stocks WHERE ticker IN ({q}) AND date>=? AND date<=?',con,params=[*tickers,start,end],parse_dates=['date'])

def run(db_path: str, as_of_start: str, as_of_end: str) -> StrategyResult:
    con=sqlite3.connect(db_path); rows=[]; history=[]; dims={}; violations=0
    for dt in pd.date_range(as_of_start,as_of_end,freq='ME'):
        u=_universe(db_path,dt.strftime('%Y-%m-%d')); syms=u.ticker.tolist()
        if not syms: continue
        q=','.join('?'*len(syms)); f=pd.read_sql_query(f"SELECT ticker,datekey,dimension,netinc,ncfo,assets FROM fundamentals WHERE ticker IN ({q}) AND datekey<=? AND dimension IN ('ART','ARY') ORDER BY ticker,datekey",con,params=[*syms,dt.strftime('%Y-%m-%d')],parse_dates=['datekey'])
        vals=[]
        for t in syms:
            x=f[f.ticker==t]; art=x[x.dimension=='ART']; ary=x[x.dimension=='ARY']; z=art if not art.empty else ary
            z=z.dropna(subset=['netinc','ncfo','assets']); z=z[z.assets!=0]
            if z.empty: continue
            r=z.iloc[-1]; assert r.datekey<=dt; violations += int(r.datekey>dt); dims[r.dimension]=dims.get(r.dimension,0)+1; vals.append((t,float((r.netinc-r.ncfo)/r.assets)))
        if len(vals)<5: continue
        s=pd.DataFrame(vals,columns=['ticker','signal']).sort_values('signal').reset_index(drop=True); s['q']=(np.floor(np.arange(len(s))*5/len(s))+1).astype(int)
        px=_returns(con,s.ticker.tolist(),dt.strftime('%Y-%m-%d'),(dt+pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')); px['value']=px.closeadj.fillna(px.closeunadj).fillna(px.close); px['month']=px.date.dt.to_period('M')
        cur=dt.to_period('M'); nxt=(dt+pd.offsets.MonthEnd(1)).to_period('M'); got=[]
        for _,r in s.iterrows():
            a=px[(px.ticker==r.ticker)&(px.month==cur)].tail(1); b=px[(px.ticker==r.ticker)&(px.month==nxt)].tail(1)
            if a.empty or b.empty: continue
            got.append({'date':dt,'ticker':r.ticker,'q':int(r.q),'return':float(b.value.iloc[0]/a.value.iloc[0]-1),'weight':1/len(s[s.q==r.q])})
        if got: history.extend(got); rows.append(pd.DataFrame(got).groupby('q')['return'].mean().rename(dt))
    con.close(); quint=pd.DataFrame(rows); quint.columns=[f'Q{i}' for i in quint.columns] if len(quint.columns)==5 else quint.columns; monthly=quint.get('Q1',pd.Series(dtype=float)); factors,a,t,p,b,r2=factor_regression(monthly,as_of_start)
    periods={}
    for name,a0,b0 in [('2000-2009',as_of_start,'2009-12-31'),('2010-2019','2010-01-01','2019-12-31'),('2020-present','2020-01-01',as_of_end)]:
        x=monthly[(monthly.index>=a0)&(monthly.index<=b0)]
        try: _,aa,tt,*_=factor_regression(x,a0); periods[name]={'alpha_annual':aa,'tstat':tt,'n_months':len(x)}
        except Exception: periods[name]={'alpha_annual':np.nan,'tstat':np.nan,'n_months':len(x)}
    return StrategyResult(monthly,factors,a,t,p,b,r2,len(monthly),pd.DataFrame(history),quint,{},periods,violations,dims)

if __name__=='__main__':
    r=run('data/sharadar.db','2000-01-01','2024-12-31'); print('ACCRUALS — FULL UNIVERSE'); print(f'Violations found: {r.lookahead_violations}')
