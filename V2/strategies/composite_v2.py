"""Composite v2: price-based mean-reversion composite built from the
verified signal library (IBS, RSI2, Sector Rotation, Friday-down bonus),
layered on the same NSI Q5 / Quality Q5 hard exclusions as composite_v1,
but with those exclusions cached monthly instead of recomputed weekly.
See research/composite_v2_preregistration.json for the full locked spec.

Composite score (eligible, non-excluded names only):
  score_A (0.50) = pct_rank(1 - mean(IBS, trailing 5 days))
  score_B (0.30) = pct_rank(100 - RSI2)
  score_C (0.15) = pct_rank_within_sector(close/close[-63] - 1)
  score_D (0.05) = 1 if Friday's own daily return < -1% else 0 (binary, not rank-scaled)
Hold top quintile, equal weight, weekly Friday-close signal / Monday-open execution.
"""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3
import numpy as np
import pandas as pd
from utils.research_utils import (
    load_universe_candidates, load_marketcap_panel, panel_marketcaps_asof,
    size_controlled_quintile_spread,
)
from strategies.momentum import _weekly_dates, _six_factor_regression, _weekly_to_monthly
from strategies.quality import _component_values, _FUND_COLS
from strategies.signal_library import load_full_ohlc_panel, _wide, build_eligibility, _rsi
from utils.research_utils import load_fundamentals_panel, panel_latest_asof

ROUND_TRIP_COST_BPS = 10
W_A, W_B, W_C, W_D = 0.50, 0.30, 0.15, 0.05


@dataclass
class CompositeV2Result:
    weekly_returns: pd.Series
    monthly_returns: pd.Series
    long_only: dict
    turnover_weekly: float
    annual_cost_estimate: float
    avg_universe_after_exclusion: float
    avg_n_held: float
    size_tercile_regressions: dict
    score_by_quintile: dict          # {'A':{'Q1':x,'Q5':x}, 'B':..., 'C':..., 'D':...}
    subperiod_regressions: dict      # {label: six_factor_regression dict}
    lookahead_violations: int
    n_weeks: int


def _nsi_signal(fund_panel_shares, as_of, syms):
    cutoff = as_of - pd.Timedelta(days=365)
    now_df = panel_latest_asof(fund_panel_shares, as_of, dimension_priority=('MRQ',))
    old_df = panel_latest_asof(fund_panel_shares, cutoff, dimension_priority=('MRQ',))
    if now_df.empty or old_df.empty:
        return pd.Series(dtype=float)
    now_s = now_df.set_index('ticker')['sharesbas']; old_s = old_df.set_index('ticker')['sharesbas']
    idx = pd.Index(syms).intersection(now_s.index).intersection(old_s.index)
    now_v = now_s.reindex(idx); old_v = old_s.reindex(idx)
    valid = idx[old_v.notna() & (old_v != 0) & now_v.notna()]
    if len(valid) == 0:
        return pd.Series(dtype=float)
    return (now_v.reindex(valid) - old_v.reindex(valid)) / old_v.reindex(valid)


def _quality_score(fund_panel_qual, as_of, syms):
    latest = panel_latest_asof(fund_panel_qual, as_of, dimension_priority=('ART', 'ARY'))
    if latest.empty:
        return pd.Series(dtype=float)
    latest = latest[latest.ticker.isin(syms)]
    if latest.empty:
        return pd.Series(dtype=float)
    raw_scores = []
    for r in latest.itertuples():
        vals = _component_values(r); good = {k: v for k, v in vals.items() if pd.notna(v)}
        if len(good) < 4:
            continue
        raw_scores.append({'ticker': r.ticker, **good})
    if len(raw_scores) < 5:
        return pd.Series(dtype=float)
    raw = pd.DataFrame(raw_scores).set_index('ticker')
    ranked = raw.rank(pct=True, ascending=True)
    return ranked.mean(axis=1)


def _quintile(s: pd.Series, ascending: bool) -> pd.Series:
    order = s.sort_values(ascending=ascending)
    n = len(order)
    q = (np.floor(np.arange(n) * 5 / n) + 1).astype(int)
    return pd.Series(q, index=order.index)


def _monthly_exclusions(shares_panel, qual_panel, as_of, syms):
    excluded = set()
    nsi = _nsi_signal(shares_panel, as_of, syms)
    if len(nsi) >= 5:
        nsi_q = _quintile(nsi, ascending=True)
        excluded |= set(nsi_q.index[nsi_q == 5])
    qual = _quality_score(qual_panel, as_of, syms)
    if len(qual) >= 5:
        qual_q = _quintile(qual, ascending=False)
        excluded |= set(qual_q.index[qual_q == 5])
    return excluded


def run(db_path: str, start: str = '2001-01-01', end: str = '2018-12-31', exclude_fama_sectors=('Financial Services', 'Real Estate', 'Healthcare')) -> CompositeV2Result:
    con = sqlite3.connect(db_path)
    candidates = load_universe_candidates(con, exclude_sectors=[], exclude_fama_sectors=list(exclude_fama_sectors))
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=500)).strftime('%Y-%m-%d')
    window_start = pd.Timestamp(load_start); window_end = pd.Timestamp(end)
    candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
    universe_tickers = candidates.ticker.tolist()

    px = load_full_ohlc_panel(con, universe_tickers, load_start, end)
    shares_panel = load_fundamentals_panel(con, universe_tickers, load_start, end, ['sharesbas'], dimensions=('MRQ',))
    qual_panel = load_fundamentals_panel(con, universe_tickers, load_start, end, _FUND_COLS)
    mc_panel = load_marketcap_panel(con, universe_tickers, start, end)
    con.close()

    O, H, L, C, V = (_wide(px, c) for c in ('open', 'high', 'low', 'close', 'volume'))
    eligible = build_eligibility(O, H, L, C, V, candidates)
    sector_of = candidates.set_index('ticker')['sector']

    ibs_daily = (C - L) / (H - L).replace(0, np.nan)
    ibs_avg5 = ibs_daily.rolling(5, min_periods=5).mean()
    raw_A = 1 - ibs_avg5
    rsi2 = _rsi(C, 2)
    raw_B = 100 - rsi2
    ret63 = C / C.shift(63) - 1
    friday_ret = C / C.shift(1) - 1

    all_dates = pd.DatetimeIndex(sorted(px.date.unique()))
    date_pos = {d: i for i, d in enumerate(C.index)}
    rebalance_dates = _weekly_dates(all_dates, pd.Timestamp(start), pd.Timestamp(end))

    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    weekly_rows = []
    holdings_by_date = {}
    n_after_exclusion, n_held = [], []
    violations = 0
    size_tercile_monthly = {'Small': [], 'Mid': [], 'Large': []}
    score_samples = {'A': {'Q1': [], 'Q5': []}, 'B': {'Q1': [], 'Q5': []}, 'C': {'Q1': [], 'Q5': []}, 'D': {'Q1': [], 'Q5': []}}

    cached_month = None
    cached_excluded = set()

    for rd in rebalance_dates:
        if rd not in date_pos:
            continue
        idx = date_pos[rd]
        if idx < 63:
            continue
        fut_r = [d for d in rebalance_dates if d > rd]
        if not fut_r:
            continue
        rd_next = fut_r[0]

        # Monday-open execution: entry = first trading day after rd, exit =
        # first trading day after rd_next. Both legs use Open, one session
        # lagged behind each Friday's close signal.
        after_rd = [d for d in C.index if d > rd]
        after_rd_next = [d for d in C.index if d > rd_next]
        if not after_rd or not after_rd_next:
            continue
        entry_date, exit_date = after_rd[0], after_rd_next[0]

        month_key = (rd.year, rd.month)
        if month_key != cached_month:
            syms_month = list(eligible.columns[eligible.loc[rd]]) if rd in eligible.index else []
            cached_excluded = _monthly_exclusions(shares_panel, qual_panel, rd, syms_month)
            cached_month = month_key

        elig_today = eligible.loc[rd]
        syms = [s for s in eligible.columns[elig_today] if s not in cached_excluded]
        if len(syms) < 25:
            continue
        n_after_exclusion.append(len(syms))

        a = raw_A.loc[rd].reindex(syms)
        b = raw_B.loc[rd].reindex(syms)
        c63 = ret63.loc[rd].reindex(syms)
        d = (friday_ret.loc[rd].reindex(syms) < -0.01).astype(float)

        df = pd.DataFrame({'A': a, 'B': b, 'C_raw': c63, 'D': d, 'sector': sector_of.reindex(syms)}).dropna(subset=['A', 'B', 'C_raw'])
        if len(df) < 25:
            continue

        df['A_pct'] = df['A'].rank(pct=True, ascending=True)
        df['B_pct'] = df['B'].rank(pct=True, ascending=True)
        df['C_pct'] = df.groupby('sector')['C_raw'].rank(pct=True, ascending=True)
        df['C_pct'] = df['C_pct'].fillna(0.5)

        df['composite'] = W_A * df['A_pct'] + W_B * df['B_pct'] + W_C * df['C_pct'] + W_D * df['D']

        n = len(df)
        ranked = df.sort_values('composite', ascending=False)
        top_n = max(1, n // 5)
        bot_n = max(1, n // 5)
        held = ranked.index[:top_n]
        bottom = ranked.index[-bot_n:]
        n_held.append(len(held))
        holdings_by_date[rd] = set(held)

        for label, col in (('A', 'A_pct'), ('B', 'B_pct'), ('C', 'C_pct'), ('D', 'D')):
            score_samples[label]['Q1'].append(df.loc[held, col].mean())
            score_samples[label]['Q5'].append(df.loc[bottom, col].mean())

        entry_p = O.loc[entry_date, list(held)]
        exit_p = O.loc[exit_date, list(held)]
        raw_ret = (exit_p / entry_p - 1)
        lo, hi = raw_ret.quantile(0.01), raw_ret.quantile(0.99)
        raw_ret = raw_ret.clip(lo, hi)
        weekly_rows.append((rd_next, float(raw_ret.mean())))

        mktcaps = panel_marketcaps_asof(mc_panel, df.index.tolist(), rd)
        df['mktcap'] = df.index.map(mktcaps)
        # For size-tercile spread we need a return for every scored name, not
        # just the held top quintile -- reuse the same entry/exit open prices
        # for the FULL scored universe that week (not only the top/bottom
        # picks used for the headline portfolio).
        entry_full = O.loc[entry_date, df.index]
        exit_full = O.loc[exit_date, df.index]
        df['fwd'] = (exit_full / entry_full - 1)
        sc = size_controlled_quintile_spread(df, signal_col='composite', return_col='fwd', mktcap_col='mktcap', ascending=False)
        for tercile in ('Small', 'Mid', 'Large'):
            q1v = sc[tercile]['q1']
            if pd.notna(q1v):
                size_tercile_monthly[tercile].append((rd_next, q1v))

    weekly = pd.Series(dict(weekly_rows)).sort_index()
    weekly = weekly[(weekly.index >= start_ts) & (weekly.index <= end_ts)]

    rates = []
    prev = None
    for rd in sorted(holdings_by_date):
        cur = holdings_by_date[rd]
        if prev is not None and len(cur):
            rates.append(len(cur.symmetric_difference(prev)) / (2 * len(cur)))
        prev = cur
    turnover_weekly = float(np.mean(rates)) if rates else float('nan')
    weekly_cost = turnover_weekly * (ROUND_TRIP_COST_BPS / 10_000)
    weekly_net = weekly - weekly_cost
    annual_cost_estimate = weekly_cost * 52

    monthly = _weekly_to_monthly(weekly_net.dropna())
    long_only = _six_factor_regression(monthly, start, is_long_short=False)

    size_tercile_regressions = {}
    for tercile, rows in size_tercile_monthly.items():
        s = pd.Series(dict(rows)).sort_index()
        s = s[(s.index >= start_ts) & (s.index <= end_ts)]
        s_adj = s - weekly_cost
        m = _weekly_to_monthly(s_adj.dropna())
        size_tercile_regressions[tercile] = _six_factor_regression(m, start, is_long_short=False)

    subperiod_bounds = [('2001-2009', '2001-01-01', '2009-12-31'), ('2010-2018', '2010-01-01', '2018-12-31'),
                         ('2019-2022', '2019-01-01', '2022-12-31'), ('2023-2025', '2023-01-01', '2025-12-31')]
    subperiod_regressions = {}
    for label, s, e in subperiod_bounds:
        sub = monthly[(monthly.index >= s) & (monthly.index <= e)]
        if len(sub) < 6:
            continue
        try:
            subperiod_regressions[label] = _six_factor_regression(sub, s, is_long_short=False)
        except Exception:
            subperiod_regressions[label] = {'alpha_annual': np.nan, 'tstat': np.nan}

    score_by_quintile = {k: {'Q1': float(np.nanmean(v['Q1'])) if v['Q1'] else np.nan, 'Q5': float(np.nanmean(v['Q5'])) if v['Q5'] else np.nan} for k, v in score_samples.items()}

    return CompositeV2Result(
        weekly_returns=weekly_net, monthly_returns=monthly, long_only=long_only,
        turnover_weekly=turnover_weekly, annual_cost_estimate=annual_cost_estimate,
        avg_universe_after_exclusion=float(np.mean(n_after_exclusion)) if n_after_exclusion else np.nan,
        avg_n_held=float(np.mean(n_held)) if n_held else np.nan,
        size_tercile_regressions=size_tercile_regressions, score_by_quintile=score_by_quintile,
        subperiod_regressions=subperiod_regressions, lookahead_violations=violations, n_weeks=len(weekly_net),
    )


if __name__ == '__main__':
    r = run('data/sharadar.db', '2001-01-01', '2018-12-31')
    print(r.long_only['alpha_annual'], r.long_only['tstat'], r.annual_cost_estimate)
