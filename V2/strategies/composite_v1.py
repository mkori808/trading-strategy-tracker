"""Composite v1: two validated negative screens (NSI Q5, Quality Q5 hard
exclusions) plus two suggestive positive tilts (inverse-20D momentum,
2-week-persistent 126-day breakout) combined into one weekly long-only
portfolio. See research/composite_v1_preregistration.json for the full
locked spec and falsification criteria -- this module implements exactly
that spec; it does not decide pass/fail.

Composite score (non-excluded names only):
  score_A = -1 * percentile_rank(trailing 20-day return)
  score_B = 1 if new 126-day high at BOTH this and the prior weekly close, else 0
  composite = 0.7*score_A + 0.3*score_B
Hold the top quintile of the composite score, equal weight, weekly rebalance.
"""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3
import numpy as np
import pandas as pd
from utils.research_utils import (
    load_universe_candidates, load_price_panel, load_fundamentals_panel, load_marketcap_panel,
    panel_universe_as_of, panel_latest_asof, panel_marketcaps_asof, winsorize_by_group,
    size_controlled_quintile_spread,
)
from strategies.momentum import _weekly_dates, _six_factor_regression, _weekly_to_monthly
from strategies.quality import _component_values, _FUND_COLS

HIGH_LOOKBACK = 126
MOM_LOOKBACK = 20
ROUND_TRIP_COST_BPS = 10
WEIGHT_A, WEIGHT_B = 0.7, 0.3


@dataclass
class CompositeResult:
    weekly_returns: pd.Series
    monthly_returns: pd.Series
    long_only: dict
    turnover_weekly: float
    annual_cost_estimate: float
    avg_universe_after_exclusion: float
    avg_n_breakout_confirmed: float
    avg_n_held: float
    size_tercile_regressions: dict          # {'Small':{...six_factor...}, 'Mid':..., 'Large':...}
    lookahead_violations: int
    n_weeks: int


def _nsi_signal(fund_panel_shares: pd.DataFrame, rd: pd.Timestamp, syms: list[str]) -> pd.Series:
    """Trailing-365-day sharesbas percent change, same as net_share_issuance.py."""
    cutoff = rd - pd.Timedelta(days=365)
    now_df = panel_latest_asof(fund_panel_shares, rd, dimension_priority=('MRQ',))
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


def _quality_score(fund_panel_qual: pd.DataFrame, rd: pd.Timestamp, syms: list[str]) -> pd.Series:
    """Mean cross-sectional percentile rank of the 6 quality components, same as quality.py."""
    latest = panel_latest_asof(fund_panel_qual, rd, dimension_priority=('ART', 'ARY'))
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


def run(db_path: str, dev_start: str = '2001-01-01', dev_end: str = '2016-12-31', exclude_fama_sectors: list[str] | None = None) -> CompositeResult:
    con = sqlite3.connect(db_path)
    candidates = load_universe_candidates(con, exclude_fama_sectors=exclude_fama_sectors)
    load_start = (pd.Timestamp(dev_start) - pd.Timedelta(days=500)).strftime('%Y-%m-%d')
    window_start = pd.Timestamp(load_start)
    window_end = pd.Timestamp(dev_end)
    candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
    universe_tickers = candidates.ticker.tolist()

    price_panel = load_price_panel(con, universe_tickers, load_start, dev_end)
    shares_panel = load_fundamentals_panel(con, universe_tickers, load_start, dev_end, ['sharesbas'], dimensions=('MRQ',))
    qual_panel = load_fundamentals_panel(con, universe_tickers, load_start, dev_end, _FUND_COLS)
    mc_panel = load_marketcap_panel(con, universe_tickers, dev_start, dev_end)
    con.close()

    all_dates = pd.DatetimeIndex(sorted(price_panel.date.unique()))
    rebalance_dates = _weekly_dates(all_dates, pd.Timestamp(dev_start), pd.Timestamp(dev_end))

    px = price_panel.pivot_table(index='date', columns='ticker', values='value', aggfunc='last').sort_index()
    px = px.ffill(limit=5)
    date_pos = {d: i for i, d in enumerate(px.index)}
    rolling_high = px.shift(1).rolling(HIGH_LOOKBACK, min_periods=HIGH_LOOKBACK).max()
    new_high_flag = px > rolling_high  # bool frame, True where that day is a new 126d high

    weekly_rows = []
    holdings_by_date = {}
    n_after_exclusion, n_breakout_confirmed, n_held = [], [], []
    violations = 0
    size_tercile_monthly = {'Small': [], 'Mid': [], 'Large': []}
    prev_new_high_flag_at_rd = {}  # rd -> Series of bool, for the 2-consecutive-week check

    for rd in rebalance_dates:
        if rd not in date_pos:
            continue
        idx = date_pos[rd]
        if idx < max(HIGH_LOOKBACK, MOM_LOOKBACK):
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

        nsi = _nsi_signal(shares_panel, rd, syms)
        qual = _quality_score(qual_panel, rd, syms)
        excluded = set()
        if len(nsi) >= 5:
            nsi_q = _quintile(nsi, ascending=True)  # Q5 = highest issuance
            excluded |= set(nsi_q.index[nsi_q == 5])
        if len(qual) >= 5:
            qual_q = _quintile(qual, ascending=False)  # Q5 = lowest quality
            excluded |= set(qual_q.index[qual_q == 5])

        remaining = [s for s in syms if s not in excluded]
        if len(remaining) < 25:
            continue
        n_after_exclusion.append(len(remaining))

        p_now = px.iloc[idx]; p_next = px.iloc[idx_next]
        p_mom_start = px.iloc[idx - MOM_LOOKBACK]
        mom20 = (p_now / p_mom_start - 1).reindex(remaining)
        fwd = (p_next / p_now - 1).reindex(remaining)

        this_high = new_high_flag.iloc[idx].reindex(remaining).fillna(False)
        prev_rebalances = [d for d in rebalance_dates if d < rd]
        score_b = pd.Series(0, index=remaining, dtype=int)
        if prev_rebalances:
            rd_prev = prev_rebalances[-1]
            if rd_prev in date_pos:
                prev_high = new_high_flag.iloc[date_pos[rd_prev]].reindex(remaining).fillna(False)
                score_b = ((this_high & prev_high)).astype(int)
        n_breakout_confirmed.append(int(score_b.sum()))

        df = pd.DataFrame({'mom20': mom20, 'score_b': score_b, 'fwd': fwd}).dropna(subset=['mom20', 'fwd'])
        if len(df) < 25:
            continue
        pct_rank = df['mom20'].rank(pct=True, ascending=True)
        df['score_a'] = -1.0 * pct_rank
        df['composite'] = WEIGHT_A * df['score_a'] + WEIGHT_B * df['score_b']

        n = len(df)
        ranked = df.sort_values('composite', ascending=False)
        top_n = max(1, n // 5)
        held = ranked.index[:top_n]
        n_held.append(len(held))
        holdings_by_date[rd] = set(held)

        hold_df = df.loc[held, ['fwd']].copy()
        hold_df['date'] = rd_next
        hold_df = winsorize_by_group(hold_df, 'fwd', 'date')
        weekly_rows.append((rd_next, float(hold_df['fwd'].mean())))

        # size terciles: re-rank composite WITHIN each size tercile, take
        # that tercile's own top quintile.
        mktcaps = panel_marketcaps_asof(mc_panel, df.index.tolist(), rd)
        df['mktcap'] = df.index.map(mktcaps)
        sc = size_controlled_quintile_spread(df, signal_col='composite', return_col='fwd', mktcap_col='mktcap', ascending=False)
        for tercile in ('Small', 'Mid', 'Large'):
            q1 = sc[tercile]['q1']
            if pd.notna(q1):
                size_tercile_monthly[tercile].append((rd_next, q1))

    weekly = pd.Series(dict(weekly_rows)).sort_index()

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
    long_only = _six_factor_regression(monthly, dev_start, is_long_short=False)

    size_tercile_regressions = {}
    for tercile, rows in size_tercile_monthly.items():
        s = pd.Series(dict(rows)).sort_index()
        s_weekly_cost_adj = s - weekly_cost  # same cost assumption applied for consistency
        m = _weekly_to_monthly(s_weekly_cost_adj.dropna())
        size_tercile_regressions[tercile] = _six_factor_regression(m, dev_start, is_long_short=False)

    return CompositeResult(
        weekly_returns=weekly_net, monthly_returns=monthly, long_only=long_only,
        turnover_weekly=turnover_weekly, annual_cost_estimate=annual_cost_estimate,
        avg_universe_after_exclusion=float(np.mean(n_after_exclusion)) if n_after_exclusion else np.nan,
        avg_n_breakout_confirmed=float(np.mean(n_breakout_confirmed)) if n_breakout_confirmed else np.nan,
        avg_n_held=float(np.mean(n_held)) if n_held else np.nan,
        size_tercile_regressions=size_tercile_regressions, lookahead_violations=violations, n_weeks=len(weekly_net),
    )


if __name__ == '__main__':
    r = run('data/sharadar.db')
    print(r.long_only['alpha_annual'], r.long_only['tstat'], r.annual_cost_estimate)
