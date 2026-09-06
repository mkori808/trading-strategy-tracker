"""Shared research helpers for clean V2 factor studies."""
from __future__ import annotations
import numpy as np
import pandas as pd

def factor_regression(returns: pd.Series, start: str):
    import pandas_datareader.data as web
    import statsmodels.api as sm
    factors = web.DataReader('F-F_Research_Data_5_Factors_2x3', 'famafrench', start=start)[0] / 100
    factors.index = pd.to_datetime(factors.index.to_timestamp(how='end')).to_period('M').to_timestamp('M')
    aligned = pd.concat([returns.rename('portfolio'), factors], axis=1, join='inner').dropna()
    if len(aligned) < 3:
        return factors, np.nan, np.nan, np.nan, {}, np.nan
    model = sm.OLS(aligned.portfolio - aligned.RF, sm.add_constant(aligned[['Mkt-RF','SMB','HML','RMW','CMA']])).fit()
    return factors, (1 + float(model.params['const'])) ** 12 - 1, float(model.tvalues['const']), float(model.pvalues['const']), model.params.drop('const').to_dict(), float(model.rsquared)
