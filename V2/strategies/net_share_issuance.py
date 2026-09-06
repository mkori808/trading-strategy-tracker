"""Point-in-time net-share-issuance portfolios (standalone V2 module)."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date
import sqlite3
import numpy as np
import pandas as pd

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

def _months(start, end):
    return pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="ME")

def _load_prices(con, tickers, end):
    if not tickers: 
        return pd.DataFrame()
    q=",".join("?"*len(tickers))
    return pd.read_sql_query(f"SELECT ticker,date,close,closeadj,closeunadj FROM stocks WHERE date<=? AND ticker IN ({q}) ORDER BY date",con,params=[end,*tickers],parse_dates=["date"])

def _factor_model(returns, start):
    try:
        import pandas_datareader.data as web
        import statsmodels.api as sm
    except ImportError as exc:
        raise RuntimeError("Install pandas_datareader and statsmodels to run factor regression") from exc
    factors=web.DataReader("F-F_Research_Data_5_Factors_2x3","famafrench",start=start)[0]/100
    factors.index=pd.to_datetime(factors.index.to_timestamp(how="end")).to_period("M").to_timestamp("M")
    aligned=pd.concat([returns.rename("portfolio"),factors],axis=1,join="inner").dropna()
    if len(aligned)<3: return factors,np.nan,np.nan,np.nan,{},np.nan
    ex=aligned.portfolio-aligned.RF; X=sm.add_constant(aligned[["Mkt-RF","SMB","HML","RMW","CMA"]]); model=sm.OLS(ex,X).fit(); monthly=float(model.params["const"])
    return factors,(1+monthly)**12-1,float(model.tvalues["const"]),float(model.pvalues["const"]),model.params.drop("const").to_dict(),float(model.rsquared)

def run(db_path: str, as_of_start: str, as_of_end: str) -> StrategyResult:
    con=sqlite3.connect(db_path)
    months=_months(as_of_start,as_of_end); qrows=[]; history=[]
    for rebalance in months:
        from scripts.universe import get_universe
        u=get_universe(db_path,rebalance.strftime("%Y-%m-%d"))
        if u.empty: continue
        syms=u.ticker.tolist(); ph=",".join("?"*len(syms)); cutoff=(rebalance-pd.Timedelta(days=365)).strftime("%Y-%m-%d")
        f=pd.read_sql_query(f"SELECT ticker,date,datekey,sharesbas FROM fundamentals WHERE ticker IN ({ph}) AND dimension='MRQ' AND datekey<=? ORDER BY ticker,datekey",con,params=[*syms,rebalance.strftime("%Y-%m-%d")],parse_dates=["datekey"])
        signals=[]
        for t in syms:
            x=f[f.ticker==t].dropna(subset=["sharesbas"]); now=x[x.datekey<=rebalance]; old=x[x.datekey<=pd.Timestamp(cutoff)]
            if now.empty or old.empty or float(old.iloc[-1].sharesbas)==0: continue
            signals.append((t,(float(now.iloc[-1].sharesbas)-float(old.iloc[-1].sharesbas))/float(old.iloc[-1].sharesbas)))
        if len(signals)<5: continue
        s=pd.DataFrame(signals,columns=["ticker","signal"]).sort_values("signal").reset_index(drop=True); s["q"]=(np.floor(np.arange(len(s))*5/len(s))+1).astype(int)
        px=_load_prices(con,s.ticker.tolist(),(rebalance+pd.offsets.MonthEnd(1)).strftime("%Y-%m-%d")); px["value"]=px["closeadj"].fillna(px["closeunadj"]).fillna(px["close"])
        px["month"]=px.date.dt.to_period("M"); next_month=(rebalance+pd.offsets.MonthEnd(1)).to_period("M"); cur_month=rebalance.to_period("M")
        for _,row in s.iterrows():
            a=px[(px.ticker==row.ticker)&(px.month==cur_month)].tail(1); b=px[(px.ticker==row.ticker)&(px.month==next_month)].tail(1)
            if a.empty or b.empty: continue
            history.append({"date":rebalance,"ticker":row.ticker,"weight":1/s[s.q==row.q].shape[0],"return":float(b.value.iloc[0]/a.value.iloc[0]-1),"q":int(row.q)})
        h=pd.DataFrame(history); recent=h[h.date==rebalance] if history else pd.DataFrame()
        if not recent.empty: qrows.append(recent.groupby("q").return.mean().rename(rebalance))
    con.close(); quint=pd.DataFrame(qrows); quint.columns=[f"Q{i}" for i in range(1,6)] if len(quint.columns)==5 else quint.columns
    monthly=quint.get("Q1",pd.Series(dtype=float)); factors,aa,ts,pv,betas,rsq=_factor_model(monthly,as_of_start)
    return StrategyResult(monthly,factors,aa,ts,pv,betas,rsq,len(monthly),pd.DataFrame(history),quint)

if __name__=="__main__":
    r=run("data/sharadar.db","2000-01-01","2024-12-31"); print("NET SHARE ISSUANCE — FULL UNIVERSE"); print(f"Period: {r.monthly_returns.index.min()} to {r.monthly_returns.index.max()} ({r.n_months} months)"); print(f"Residual alpha: {r.alpha_annual:.2%}/yr  t={r.alpha_tstat:.2f}  p={r.alpha_pvalue:.3f}")
