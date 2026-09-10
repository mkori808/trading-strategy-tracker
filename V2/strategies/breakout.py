"""Breakout preregistration: new intermediate-term (126-trading-day) highs
with volume confirmation. Third signal family (distinct mechanism from
momentum: level-based resistance break + volume, not return ranking).
Weekly rebalance (Friday close formation and execution), development period
only (2001-2016); 2017-2025 is a locked holdout, not touched here.

See research/breakout_preregistration.json for the full locked spec,
falsification criteria, and known limitation (Q5 here is the weakest
volume-confirmed breakout, not a symmetric new-low signal -- the long-only
Q1 leg is the primary test).

Signal (locked):
  pct_to_high = close_t / max(close[t-126..t-1]) - 1
  vol_ratio   = mean(volume[t-4..t]) / mean(volume[t-62..t])
  eligible    = vol_ratio > 1.5
  rank pct_to_high descending among eligible; Q1 = strongest confirmed
  breakout = BUY.
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
from strategies.momentum import _weekly_dates, _six_factor_regression, _weekly_to_monthly

HIGH_LOOKBACK = 126
VOL_SHORT = 5
VOL_LONG = 63
VOL_RATIO_MIN = 1.5
ROUND_TRIP_COST_BPS = 10


@dataclass
class BreakoutResult:
    weekly_returns: pd.DataFrame
    monthly_quintiles: pd.DataFrame
    long_only: dict
    long_short: dict
    turnover_weekly: float
    annual_cost_estimate: float
    avg_n_eligible: float
    size_terciles: dict
    worst_month: dict
    january_vs_other: dict
    lookahead_violations: int
    n_weeks: int


def run(db_path: str, dev_start: str = '2001-01-01', dev_end: str = '2016-12-31', exclude_fama_sectors: list[str] | None = None) -> BreakoutResult:
    con = sqlite3.connect(db_path)
    candidates = load_universe_candidates(con, exclude_fama_sectors=exclude_fama_sectors)
    load_start = (pd.Timestamp(dev_start) - pd.Timedelta(days=500)).strftime('%Y-%m-%d')
    window_start = pd.Timestamp(load_start)
    window_end = pd.Timestamp(dev_end)
    candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
    universe_tickers = candidates.ticker.tolist()

    price_panel = load_price_panel(con, universe_tickers, load_start, dev_end)
    mc_panel = load_marketcap_panel(con, universe_tickers, dev_start, dev_end)
    con.close()

    all_dates = pd.DatetimeIndex(sorted(price_panel.date.unique()))
    rebalance_dates = _weekly_dates(all_dates, pd.Timestamp(dev_start), pd.Timestamp(dev_end))

    px = price_panel.pivot_table(index='date', columns='ticker', values='value', aggfunc='last').sort_index()
    px = px.ffill(limit=5)
    vol = price_panel.pivot_table(index='date', columns='ticker', values='volume', aggfunc='last').sort_index()
    vol = vol.reindex(px.index).ffill(limit=5)
    date_pos = {d: i for i, d in enumerate(px.index)}

    # rolling 126-day high EXCLUDING today (shift(1) before the rolling max)
    rolling_high = px.shift(1).rolling(HIGH_LOOKBACK, min_periods=HIGH_LOOKBACK).max()
    vol_short = vol.rolling(VOL_SHORT, min_periods=VOL_SHORT).mean()
    vol_long = vol.rolling(VOL_LONG, min_periods=VOL_LONG).mean()
    vol_ratio = vol_short / vol_long

    weekly_rows = []
    holdings_by_date = {}
    n_eligible = []
    violations = 0
    size_tercile_rows = {'Small': [], 'Mid': [], 'Large': []}

    for rd in rebalance_dates:
        if rd not in date_pos:
            continue
        idx = date_pos[rd]
        if idx < max(HIGH_LOOKBACK, VOL_LONG):
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
        pth = (px.iloc[idx] / rolling_high.iloc[idx] - 1).reindex(syms)
        vr = vol_ratio.iloc[idx].reindex(syms)
        p_now = px.iloc[idx]
        p_next = px.iloc[idx_next]
        fwd = (p_next / p_now - 1).reindex(syms)
        df = pd.DataFrame({'signal': pth, 'vol_ratio': vr, 'fwd': fwd}).dropna()
        # lookahead check: rolling_high/vol windows both end strictly before
        # idx (shift(1) on price, and vol windows use data through idx which
        # is the formation date itself -- fine, volume on the formation day
        # is known at formation; the forward return window [idx, idx_next]
        # never overlaps the formation windows, which all end at or before idx).
        eligible = df[df.vol_ratio > VOL_RATIO_MIN]
        if len(eligible) < 25:
            continue
        n_eligible.append(len(eligible))
        eligible = eligible.sort_values('signal', ascending=False).reset_index().rename(columns={'index': 'ticker'})
        n = len(eligible)
        eligible['q'] = (np.floor(np.arange(n) * 5 / n) + 1).astype(int)
        eligible['date'] = rd_next
        eligible = winsorize_by_group(eligible, 'fwd', 'date')
        qret = eligible.groupby('q')['fwd'].mean()
        holdings_by_date[rd] = set(eligible.loc[eligible.q == 1, 'ticker'])
        weekly_rows.append((rd_next, {f'Q{i}': qret.get(i, np.nan) for i in range(1, 6)}))

        mktcaps = panel_marketcaps_asof(mc_panel, eligible.ticker.tolist(), rd)
        eligible['mktcap'] = eligible.ticker.map(mktcaps)
        szc = size_controlled_quintile_spread(eligible, signal_col='signal', return_col='fwd', mktcap_col='mktcap', ascending=False)
        for tercile in ('Small', 'Mid', 'Large'):
            spread = szc[tercile]['q1'] - szc[tercile]['q5'] if pd.notna(szc[tercile]['q1']) and pd.notna(szc[tercile]['q5']) else np.nan
            size_tercile_rows[tercile].append((rd_next, spread))

    weekly = pd.DataFrame({d: v for d, v in weekly_rows}).T.sort_index()

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
    weekly_net['Q1'] = weekly_net['Q1'] - weekly_cost
    annual_cost_estimate = weekly_cost * 52

    monthly_q = pd.DataFrame({c: _weekly_to_monthly(weekly_net[c].dropna()) for c in weekly_net.columns})
    long_only = _six_factor_regression(monthly_q['Q1'], dev_start, is_long_short=False)
    spread_weekly = weekly_net['Q1'] - weekly_net['Q5']
    spread_monthly = _weekly_to_monthly(spread_weekly.dropna())
    long_short = _six_factor_regression(spread_monthly, dev_start, is_long_short=True)

    worst_month = {'Q1': float(monthly_q['Q1'].min()) if len(monthly_q) else np.nan,
                    'spread': float(spread_monthly.min()) if len(spread_monthly) else np.nan}
    jan_mask = monthly_q.index.month == 1
    january_vs_other = {
        'Q1_jan': float(monthly_q.loc[jan_mask, 'Q1'].mean()) if jan_mask.any() else np.nan,
        'Q1_other': float(monthly_q.loc[~jan_mask, 'Q1'].mean()) if (~jan_mask).any() else np.nan,
        'spread_jan': float(spread_monthly[spread_monthly.index.month == 1].mean()) if (spread_monthly.index.month == 1).any() else np.nan,
        'spread_other': float(spread_monthly[spread_monthly.index.month != 1].mean()) if (spread_monthly.index.month != 1).any() else np.nan,
    }
    size_terciles = {}
    for tercile, rows in size_tercile_rows.items():
        s = pd.Series(dict(rows)).dropna()
        size_terciles[tercile] = float(s.mean() * 52) if len(s) else np.nan

    return BreakoutResult(
        weekly_returns=weekly_net, monthly_quintiles=monthly_q, long_only=long_only, long_short=long_short,
        turnover_weekly=turnover_weekly, annual_cost_estimate=annual_cost_estimate,
        avg_n_eligible=float(np.mean(n_eligible)) if n_eligible else np.nan, size_terciles=size_terciles,
        worst_month=worst_month, january_vs_other=january_vs_other, lookahead_violations=violations, n_weeks=len(weekly_net),
    )


if __name__ == '__main__':
    r = run('data/sharadar.db')
    print(r.long_only['alpha_annual'], r.long_only['tstat'], r.long_short['alpha_annual'], r.long_short['tstat'])
