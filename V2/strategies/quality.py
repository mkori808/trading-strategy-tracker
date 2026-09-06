"""Composite quality factor. Expected: positive RMW/CMA, modest SMB/HML, market beta near one."""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3
import numpy as np
import pandas as pd
from utils.research_utils import factor_regression

@dataclass
class StrategyResult:
    monthly_returns: pd.Series; factor_returns: pd.DataFrame; alpha_annual: float; alpha_tstat: float; alpha_pvalue: float; betas: dict; r_squared: float; n_months: int; portfolio_history: pd.DataFrame; quintile_returns: pd.DataFrame; size_terciles: dict; period_results: dict; lookahead_violations: int; sensitivity: dict; sector_breakdown: dict; dimensions_used: dict

COMPONENTS=('roe','roa','gm','dte','icr','cfe')

def _universe(db,dt):
    try:
        from scripts.build_universe import get_universe
    except ImportError:
        from build_universe import get_universe
    return get_universe(db,dt)

def _prices(con,syms,dt):
    q=','.join('?'*len(syms)); return pd.read_sql_query(f'SELECT ticker,date,closeadj,closeunadj,close FROM stocks WHERE ticker IN ({q}) AND date>=? AND date<=?',con,params=[*syms,dt.strftime('%Y-%m-%d'),(dt+pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')],parse_dates=['date'])

def _run_core(db,start,end,drop=None):
    con=sqlite3.connect(db); out=[]; hist=[]; violations=0; dims={}
    for dt in pd.date_range(start,end,freq='ME'):
        u=_universe(db,dt.strftime('%Y-%m-%d')); syms=u.ticker.tolist();
        if not syms: continue
        q=','.join('?'*len(syms)); f=pd.read_sql_query(f"SELECT ticker,datekey,dimension,netinc,equity,assets,revenue,cor,opinc,debt,intexp,ebit,ncfo FROM fundamentals WHERE ticker IN ({q}) AND datekey<=? AND dimension IN ('ART','ARY') ORDER BY ticker,datekey",con,params=[*syms,dt.strftime('%Y-%m-%d')],parse_dates=['datekey'])
        scores=[]
        for t in syms:
            x=f[f.ticker==t]; a=x[x.dimension=='ART']; y=a if not a.empty else x[x.dimension=='ARY']; y=y.dropna(subset=['netinc','equity','assets','revenue','cor','opinc','debt','ebit','ncfo'])
            if y.empty: continue
            r=y.iloc[-1]; assert r.datekey<=dt; violations+=int(r.datekey>dt); dims[r.dimension]=dims.get(r.dimension,0)+1
            vals={'roe':r.netinc/r.equity if r.equity>0 else np.nan,'roa':r.netinc/r.assets if r.assets!=0 else np.nan,'gm':(r.revenue-r.cor)/r.revenue if r.revenue>0 else np.nan,'dte':-r.debt/r.equity if r.equity>0 else np.nan,'icr':r.ebit/r.intexp if pd.notna(r.intexp) and r.intexp>0 else np.nan,'cfe':r.ncfo/abs(r.netinc) if r.netinc!=0 else np.nan}
            vals={k:v for k,v in vals.items() if k!=drop}; good={k:v for k,v in vals.items() if pd.notna(v)}
            if len(good)<4: continue
            scores.append((t,float(np.mean(pd.Series(good).rank(pct=True)))))
        if len(scores)<5: continue
        s=pd.DataFrame(scores,columns=['ticker','score']).sort_values('score',ascending=False).reset_index(drop=True); s['q']=(np.floor(np.arange(len(s))*5/len(s))+1).astype(int); px=_prices(con,s.ticker.tolist(),dt); px['v']=px.closeadj.fillna(px.closeunadj).fillna(px.close); px['m']=px.date.dt.to_period('M'); cur=dt.to_period('M'); nxt=(dt+pd.offsets.MonthEnd(1)).to_period('M'); got=[]
        for _,z in s.iterrows():
            a=px[(px.ticker==z.ticker)&(px.m==cur)].tail(1); b=px[(px.ticker==z.ticker)&(px.m==nxt)].tail(1)
            if not a.empty and not b.empty: got.append({'date':dt,'ticker':z.ticker,'q':int(z.q),'return':float(b.v.iloc[0]/a.v.iloc[0]-1),'weight':1/len(s[s.q==z.q])})
        if got: hist.extend(got); out.append(pd.DataFrame(got).groupby('q')['return'].mean().rename(dt))
    con.close(); quint=pd.DataFrame(out); quint.columns=[f'Q{i}' for i in quint.columns] if len(quint.columns)==5 else quint.columns; return quint,hist,violations,dims

def run(db_path:str,as_of_start:str,as_of_end:str)->StrategyResult:
    quint,hist,v,dims=_run_core(db_path,as_of_start,as_of_end); monthly=quint.get('Q1',pd.Series(dtype=float)); factors,a,t,p,b,r2=factor_regression(monthly,as_of_start); sensitivity={}
    for c in COMPONENTS:
        q,_,_,_= _run_core(db_path,as_of_start,as_of_end,drop=c); x=q.get('Q1',pd.Series(dtype=float));
        try: _,aa,tt,*_=factor_regression(x,as_of_start); sensitivity[c]={'alpha_annual':aa,'tstat':tt}
        except Exception: sensitivity[c]={'alpha_annual':np.nan,'tstat':np.nan}
    periods={}
    for name,s,e in [('2000-2009',as_of_start,'2009-12-31'),('2010-2019','2010-01-01','2019-12-31'),('2020-present','2020-01-01',as_of_end)]:
        x=monthly[(monthly.index>=s)&(monthly.index<=e)]
        try: _,aa,tt,*_=factor_regression(x,s); periods[name]={'alpha_annual':aa,'tstat':tt,'n_months':len(x)}
        except Exception: periods[name]={'alpha_annual':np.nan,'tstat':np.nan,'n_months':len(x)}
    return StrategyResult(monthly,factors,a,t,p,b,r2,len(monthly),pd.DataFrame(hist),quint,{},periods,v,sensitivity,{},dims)

if __name__=='__main__':
    r=run('data/sharadar.db','2000-01-01','2024-12-31'); print('QUALITY — FULL UNIVERSE'); print('Lookahead violations:',r.lookahead_violations)
