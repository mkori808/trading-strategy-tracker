"""Momentum preregistration (Track B). Four horizons tested in parallel:
20-day, 60-day, 126-day, and 12-1 month (skip most recent month). Weekly
rebalance (Friday close formation, Friday close execution -- same-day, no
Monday-open gap modeled), full Sharadar PIT universe excluding Finance/Real
Estate (matching the existing strategies). Development period only
(2001-01-01 to 2016-12-31); 2017-2025 is a locked holdout, not touched here.

See research/momentum_preregistration.json for the full locked spec,
falsification criteria, and multiple-testing correction (this module
implements the signals/backtest; it does not decide pass/fail itself).

Signal definitions (locked, no skip unless noted):
  H1  20-day:  price[t] / price[t-20]  - 1
  H2  60-day:  price[t] / price[t-60]  - 1
  H3 126-day:  price[t] / price[t-126] - 1
  H4  12-1mo:  price[t-21] / price[t-252] - 1   (skip most recent 21 trading days)
All ranked descending: high momentum = Q1 = BUY, low momentum = Q5 = AVOID.
"""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3
import numpy as np
import pandas as pd
from utils.research_utils import (
    load_universe_candidates, load_price_panel, load_marketcap_panel,
    panel_universe_as_of, panel_marketcaps_asof, winsorize_by_group,
    size_controlled_quintile_spread,
)

HORIZONS = {
    'momentum_20d':   {'lookback': 20,  'skip': 0},
    'momentum_60d':   {'lookback': 60,  'skip': 0},
    'momentum_126d':  {'lookback': 126, 'skip': 0},
    'momentum_12_1m': {'lookback': 252, 'skip': 21},
}

ROUND_TRIP_COST_BPS = 10  # 10bps round trip, applied to one-way weekly turnover


@dataclass
class MomentumResult:
    horizon: str
    weekly_returns: pd.DataFrame          # columns Q1..Q5, weekly net-of-cost returns
    monthly_quintiles: pd.DataFrame        # columns Q1..Q5, compounded monthly
    long_only: dict                        # alpha/tstat/r2/betas for the long leg (Q1, or Q5 if long_quintile=5)
    long_short: dict                       # alpha/tstat/r2/betas for long-minus-short spread
    turnover_weekly: float
    annual_cost_estimate: float
    avg_n_per_quintile: float
    size_terciles: dict
    worst_month: dict                      # {'long':..., 'spread':...}
    january_vs_other: dict                 # {'long_jan':..,'long_other':..,'spread_jan':..,'spread_other':..}
    lookahead_violations: int
    n_weeks: int


def _weekly_dates(all_dates: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """Last trading day of each ISO week (Friday, or nearest prior trading
    day if Friday itself isn't a session) within [start, end]."""
    s = pd.Series(all_dates, name='date').to_frame()
    s['week'] = s.date.dt.to_period('W-FRI')
    last = s.groupby('week')['date'].max()
    return sorted(d for d in last if start <= d <= end)


def _six_factor_regression(returns: pd.Series, start: str, is_long_short: bool):
    """FF5 + UMD (momentum factor), monthly. Same RF-netting convention as
    factor_regression in research_utils: long-only nets RF, long-short (a
    zero-cost spread) regresses the raw return."""
    import pandas_datareader.data as web
    import statsmodels.api as sm
    ff5 = web.DataReader('F-F_Research_Data_5_Factors_2x3', 'famafrench', start=start)[0] / 100
    mom = web.DataReader('F-F_Momentum_Factor', 'famafrench', start=start)[0] / 100
    mom.columns = ['UMD']
    factors = ff5.join(mom, how='inner')
    factors.index = pd.to_datetime(factors.index.to_timestamp(how='end')).to_period('M').to_timestamp('M')
    aligned = pd.concat([returns.rename('portfolio'), factors], axis=1, join='inner').dropna()
    if len(aligned) < 6:
        return {'alpha_annual': np.nan, 'tstat': np.nan, 'r_squared': np.nan, 'betas': {}, 'n_months': len(aligned), 'sanity_pass': False, 'sanity_notes': ['too few overlapping months']}
    dependent = aligned.portfolio if is_long_short else aligned.portfolio - aligned.RF
    X = sm.add_constant(aligned[['Mkt-RF', 'SMB', 'HML', 'RMW', 'CMA', 'UMD']])
    model = sm.OLS(dependent, X).fit()
    alpha_annual = (1 + float(model.params['const'])) ** 12 - 1
    tstat = float(model.tvalues['const'])
    r2 = float(model.rsquared)
    betas = model.params.drop('const').to_dict()
    notes = []
    if is_long_short:
        ok_beta = abs(betas['Mkt-RF']) < 0.5
        ok_alpha = abs(alpha_annual) < 0.30
        if not ok_beta:
            notes.append(f"FAIL: |market beta| {betas['Mkt-RF']:.2f} >= 0.5 for long-short spread")
        if not ok_alpha:
            notes.append(f"FAIL: |alpha| {alpha_annual:.1%} >= 30% -- implausible, investigate before trusting")
        sanity_pass = ok_beta and ok_alpha
    else:
        ok_r2 = r2 > 0.4
        ok_beta = 0.5 < betas['Mkt-RF'] < 1.5
        if not ok_r2:
            notes.append(f"FAIL: R2 {r2:.2f} <= 0.4 for long-only portfolio")
        if not ok_beta:
            notes.append(f"FAIL: market beta {betas['Mkt-RF']:.2f} outside [0.5, 1.5]")
        sanity_pass = ok_r2 and ok_beta
    return {'alpha_annual': alpha_annual, 'tstat': tstat, 'r_squared': r2, 'betas': betas, 'n_months': len(aligned), 'sanity_pass': sanity_pass, 'sanity_notes': notes}


def _weekly_to_monthly(weekly: pd.Series) -> pd.Series:
    m = weekly.groupby(weekly.index.to_period('M')).apply(lambda s: (1 + s).prod() - 1)
    m.index = m.index.to_timestamp('M')
    return m


def run(db_path: str, dev_start: str = '2001-01-01', dev_end: str = '2016-12-31', exclude_fama_sectors: list[str] | None = None,
        horizons: dict | None = None, long_quintile: int = 1) -> dict[str, MomentumResult]:
    """long_quintile lets a caller flip which quintile is treated as the
    'long' leg for turnover/cost/long-only-regression purposes (5 = buy the
    laggards instead of the leaders -- used by the short-horizon reversal
    test, which reuses this exact backtest engine on the same signals since
    the reversal hypothesis IS the momentum signal, just held long on the
    other end). The long-short spread is always long_quintile minus its
    mirror (6 - long_quintile), so it flips sign consistently with which
    leg is being called 'long'. horizons lets a caller run a subset (e.g.
    only 20d/60d) instead of all four."""
    horizons = horizons if horizons is not None else HORIZONS
    short_quintile = 6 - long_quintile
    con = sqlite3.connect(db_path)
    candidates = load_universe_candidates(con, exclude_fama_sectors=exclude_fama_sectors)
    load_start = (pd.Timestamp(dev_start) - pd.Timedelta(days=500)).strftime('%Y-%m-%d')
    load_end = dev_end
    window_start = pd.Timestamp(load_start)
    window_end = pd.Timestamp(dev_end)
    candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
    universe_tickers = candidates.ticker.tolist()

    price_panel = load_price_panel(con, universe_tickers, load_start, load_end)
    mc_panel = load_marketcap_panel(con, universe_tickers, dev_start, load_end)
    con.close()

    all_dates = pd.DatetimeIndex(sorted(price_panel.date.unique()))
    rebalance_dates = _weekly_dates(all_dates, pd.Timestamp(dev_start), pd.Timestamp(dev_end))

    wide = price_panel.pivot_table(index='date', columns='ticker', values='value', aggfunc='last').sort_index()
    wide = wide.ffill(limit=5)
    date_pos = {d: i for i, d in enumerate(wide.index)}
    max_lookback = max(h['lookback'] for h in HORIZONS.values())

    results: dict[str, MomentumResult] = {}
    for hname, spec in horizons.items():
        lookback, skip = spec['lookback'], spec['skip']
        weekly_rows = []      # (date, {'Q1':ret,...,'Q5':ret})
        holdings_by_date = {}
        n_per_quintile = []
        violations = 0
        size_tercile_rows = {'Small': [], 'Mid': [], 'Large': []}
        for rd in rebalance_dates:
            if rd not in date_pos:
                continue
            idx = date_pos[rd]
            if idx < lookback:
                continue
            fut = [d for d in rebalance_dates if d > rd]
            if not fut:
                continue
            rd_next = fut[0]
            if rd_next not in date_pos:
                continue
            idx_next = date_pos[rd_next]

            syms = panel_universe_as_of(price_panel, candidates, rd)
            if not syms:
                continue
            end_idx = idx - skip
            start_idx = idx - lookback
            if start_idx < 0 or end_idx < 0:
                continue
            p_end = wide.iloc[end_idx]
            p_start = wide.iloc[start_idx]
            p_now = wide.iloc[idx]
            p_next = wide.iloc[idx_next]
            sig = (p_end / p_start - 1).reindex(syms)
            fwd = (p_next / p_now - 1).reindex(syms)
            df = pd.DataFrame({'signal': sig, 'fwd': fwd}).dropna()
            # lookahead check: the formation window [start_idx, end_idx] and
            # the forward window [idx, idx_next] must not overlap.
            if not (end_idx <= idx):
                violations += 1
            if len(df) < 25:
                continue
            df = df.sort_values('signal', ascending=False).reset_index().rename(columns={'index': 'ticker'})
            n = len(df)
            df['q'] = (np.floor(np.arange(n) * 5 / n) + 1).astype(int)
            df['date'] = rd_next
            df = winsorize_by_group(df, 'fwd', 'date')
            n_per_quintile.append(n / 5)
            qret = df.groupby('q')['fwd'].mean()
            holdings_by_date[rd] = set(df.loc[df.q == long_quintile, 'ticker'])
            weekly_rows.append((rd_next, {f'Q{i}': qret.get(i, np.nan) for i in range(1, 6)}))

            mktcaps = panel_marketcaps_asof(mc_panel, df.ticker.tolist(), rd)
            df['mktcap'] = df.ticker.map(mktcaps)
            # ascending=False picks the highest-signal name as "top" within
            # a tercile; ascending=True picks the lowest. Match whichever
            # quintile is being treated as the long leg here.
            szc = size_controlled_quintile_spread(df, signal_col='signal', return_col='fwd', mktcap_col='mktcap', ascending=(long_quintile == 5))
            for tercile in ('Small', 'Mid', 'Large'):
                spread = szc[tercile]['q1'] - szc[tercile]['q5'] if pd.notna(szc[tercile]['q1']) and pd.notna(szc[tercile]['q5']) else np.nan
                size_tercile_rows[tercile].append((rd_next, spread))

        if not weekly_rows:
            continue
        weekly = pd.DataFrame({d: v for d, v in weekly_rows}).T.sort_index()

        # turnover on the long leg (long_quintile) -- symmetric difference of
        # holdings between consecutive rebalances, one-way convention
        # (matches Test 1).
        long_col, short_col = f'Q{long_quintile}', f'Q{short_quintile}'
        rates = []
        prev = None
        for rd in sorted(holdings_by_date):
            cur = holdings_by_date[rd]
            if prev is not None and len(cur):
                rates.append(len(cur.symmetric_difference(prev)) / (2 * len(cur)))
            prev = cur
        turnover_weekly = float(np.mean(rates)) if rates else float('nan')
        weekly_cost = turnover_weekly * (ROUND_TRIP_COST_BPS / 10_000)
        weekly_net = weekly.copy()
        weekly_net[long_col] = weekly_net[long_col] - weekly_cost
        annual_cost_estimate = weekly_cost * 52

        monthly_q = pd.DataFrame({c: _weekly_to_monthly(weekly_net[c].dropna()) for c in weekly_net.columns})
        long_only = _six_factor_regression(monthly_q[long_col], dev_start, is_long_short=False)
        spread_weekly = weekly_net[long_col] - weekly_net[short_col]
        spread_monthly = _weekly_to_monthly(spread_weekly.dropna())
        long_short = _six_factor_regression(spread_monthly, dev_start, is_long_short=True)

        worst_month = {'long': float(monthly_q[long_col].min()) if len(monthly_q) else np.nan,
                        'spread': float(spread_monthly.min()) if len(spread_monthly) else np.nan}
        jan_mask = monthly_q.index.month == 1
        january_vs_other = {
            'long_jan': float(monthly_q.loc[jan_mask, long_col].mean()) if jan_mask.any() else np.nan,
            'long_other': float(monthly_q.loc[~jan_mask, long_col].mean()) if (~jan_mask).any() else np.nan,
            'spread_jan': float(spread_monthly[spread_monthly.index.month == 1].mean()) if (spread_monthly.index.month == 1).any() else np.nan,
            'spread_other': float(spread_monthly[spread_monthly.index.month != 1].mean()) if (spread_monthly.index.month != 1).any() else np.nan,
        }

        # size-controlled spread (Q1-Q5 re-ranked WITHIN each size tercile,
        # so this isolates the signal from a raw size tilt): mean weekly
        # spread annualized (*52, simple, not compounded) per tercile.
        size_terciles = {}
        for tercile, rows in size_tercile_rows.items():
            s = pd.Series(dict(rows)).dropna()
            size_terciles[tercile] = float(s.mean() * 52) if len(s) else np.nan

        results[hname] = MomentumResult(
            horizon=hname, weekly_returns=weekly_net, monthly_quintiles=monthly_q,
            long_only=long_only, long_short=long_short, turnover_weekly=turnover_weekly,
            annual_cost_estimate=annual_cost_estimate, avg_n_per_quintile=float(np.mean(n_per_quintile)) if n_per_quintile else np.nan,
            size_terciles=size_terciles, worst_month=worst_month, january_vs_other=january_vs_other,
            lookahead_violations=violations, n_weeks=len(weekly_net),
        )
    return results


if __name__ == '__main__':
    res = run('data/sharadar.db')
    for h, r in res.items():
        print(h, r.long_short['alpha_annual'], r.long_short['tstat'])
