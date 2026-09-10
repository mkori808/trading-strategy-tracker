"""Overnight-hold signal characterization (not an edge-finding exercise).

Close-to-open return: buy at close[t-1], sell at open[t]. Captures
information arrival between sessions (news, earnings, pre-market order
flow) rather than intraday exhaustion (IBS/RSI2's mechanism).

CRITICAL STRUCTURAL FACT, verified before computing anything: every signal
here is only knowable once day t's open has printed (it needs open[t] and
close[t-1]). None of them can be computed at a Friday close for Monday
execution -- by the time you know Monday's overnight return, Monday's open
has already happened. This makes all six signals below untradeable in a
Friday-close/Monday-open weekly rebalance discipline as used by
composite_v1/v2. They are characterized here as same-day-of-open signals:
ON[t] becomes known at open[t], and is tested against day t's own
open-to-close return plus close[t]-forward horizons.

Adjustment convention verified: `open` and `close` in the `stocks` table
share the same split-adjusted-only convention (confirmed against
closeunadj around AAPL's 2020-08-31 4:1 split -- open continues smoothly
from the prior close, closeunadj shows the real ~4x jump). No correction
needed for close-to-open returns.

Distinct-signal note: O1 (raw overnight return), O2 (percentile rank of
the same), and O4 (framed as "buy overnight winners") are the SAME score
under quintile formation -- percentile-ranking and framing don't change
which names land in Q1 vs Q5 for a monotonic transform of the same
underlying value. Reported separately as requested, but the numbers are
identical by construction; O3 is exactly the sign-flipped mirror of O1.
"""
from __future__ import annotations
import sqlite3
import numpy as np
import pandas as pd
from strategies.signal_library import (
    load_full_ohlc_panel, _wide, build_eligibility, _winsorize_wide,
    _quintile_masks, _hac_regress, _factor_h_returns,
)
from utils.research_utils import load_universe_candidates, load_marketcap_panel

MIN_NAMES = 100


def run(db_path: str, start: str = '2001-01-01', end: str = '2024-12-31') -> dict:
    con = sqlite3.connect(db_path)
    candidates = load_universe_candidates(con, exclude_sectors=[], exclude_fama_sectors=['Financial Services', 'Real Estate', 'Healthcare'])
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=500)).strftime('%Y-%m-%d')
    window_start = pd.Timestamp(load_start); window_end = pd.Timestamp(end)
    candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
    tickers = candidates.ticker.tolist()

    px = load_full_ohlc_panel(con, tickers, load_start, end)
    mc_panel = load_marketcap_panel(con, tickers, start, end)
    con.close()

    O, H, L, C, V = (_wide(px, c) for c in ('open', 'high', 'low', 'close', 'volume'))
    eligible = build_eligibility(O, H, L, C, V, candidates)

    ON = O / C.shift(1) - 1                       # overnight return, indexed at day t (known at open[t])
    prior_day_ret = C.shift(1) / C.shift(2) - 1    # day t-1's own close-to-close return
    ibs_raw = -((C - L) / (H - L).replace(0, np.nan))  # signal_library's S5 convention, day t-1
    intraday_fwd = C / O - 1                       # same-day open-to-close realized return

    scores = {
        'O1_overnight_raw': ON,
        'O2_overnight_rank': ON,   # identical under quintile formation -- see module docstring
        'O3_overnight_reversal': -ON,
        'O4_overnight_momentum': ON,  # identical to O1 -- see module docstring
        'O5_overnight_vs_prior_day': ON * np.sign(prior_day_ret),
        'O6_ibs_overnight_interaction': ibs_raw.shift(1) * ON,
    }

    mc_wide = mc_panel.pivot_table(index='date', columns='ticker', values='marketcap', aggfunc='last').sort_index()
    mc_wide = mc_wide.reindex(C.index).ffill(limit=5)
    mc_pct = mc_wide.rank(axis=1, pct=True)
    size_tercile_mask = {'Small': mc_pct <= (1 / 3), 'Mid': (mc_pct > 1 / 3) & (mc_pct <= 2 / 3), 'Large': mc_pct > (2 / 3)}

    import pandas_datareader.data as web
    ff5_daily = web.DataReader('F-F_Research_Data_5_Factors_2x3_daily', 'famafrench', start=start)[0]
    ff5_daily.index = ff5_daily.index.to_timestamp()
    rf_daily = ff5_daily['RF']
    ff5_only = ff5_daily[['Mkt-RF', 'SMB', 'HML', 'RMW', 'CMA']]

    try:
        import yfinance as yf
        spy_hist = yf.download('SPY', start=load_start, end=end, interval='1d', progress=False, auto_adjust=True)['Close']['SPY']
        spy_hist.index = pd.to_datetime(spy_hist.index)
    except Exception:
        spy_hist = None

    HDEFS = {'open_to_close': ('special', intraday_fwd), 1: ('cc', 1), 5: ('cc', 5), 20: ('cc', 20)}
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    def _bound(s):
        return s[(s.index >= start_ts) & (s.index <= end_ts)]

    fwd_cc = {h: _winsorize_wide(C.shift(-h) / C - 1) for h in (1, 5, 20)}
    fwd_otc = _winsorize_wide(intraday_fwd)
    factor_h_cc = {h: _factor_h_returns(ff5_only, h) for h in (1, 5, 20)}
    rf_h_cc = {h: _factor_h_returns(rf_daily.to_frame('RF'), h)['RF'] for h in (1, 5, 20)}
    factor_h_otc = _factor_h_returns(ff5_only, 1)  # same-day: use 1-day factor compounding as the nearest available daily factor proxy
    rf_h_otc = _factor_h_returns(rf_daily.to_frame('RF'), 1)['RF']

    results = {}
    for sname, score in scores.items():
        elig_s = eligible & score.notna()
        q1, q5, valid_day = _quintile_masks(score, elig_s, min_names=MIN_NAMES)

        horizon_rows = {}
        for hlabel in ('open_to_close', 1, 5, 20):
            if hlabel == 'open_to_close':
                r = fwd_otc
                h_for_factor = 1
                fac_al_full, rf_full = factor_h_otc, rf_h_otc
            else:
                r = fwd_cc[hlabel]
                h_for_factor = hlabel
                fac_al_full, rf_full = factor_h_cc[hlabel], rf_h_cc[hlabel]

            q1_ret = r.where(q1).mean(axis=1)
            q5_ret = r.where(q5).mean(axis=1)
            spread = _bound((q1_ret - q5_ret).dropna())
            spread_test = _hac_regress(spread, None, h_for_factor)

            fac_al = fac_al_full.reindex(spread.index)
            q1_excess = q1_ret.reindex(spread.index) - rf_full.reindex(spread.index)
            q1_reg = _hac_regress(q1_excess, fac_al, h_for_factor)
            spread_reg = _hac_regress(spread, fac_al, h_for_factor)

            def ann(x, h=h_for_factor):
                return (1 + x) ** (252 / h) - 1 if pd.notna(x) else np.nan

            size_spreads = {}
            for tercile, mask in size_tercile_mask.items():
                elig_t = elig_s & mask
                q1t, q5t, _ = _quintile_masks(score, elig_t, min_names=max(10, MIN_NAMES // 5))
                st = _bound((r.where(q1t).mean(axis=1) - r.where(q5t).mean(axis=1)).dropna())
                size_spreads[tercile] = ann(st.mean()) if len(st) else np.nan

            regime_spreads = {}
            if spy_hist is not None:
                spy_c = spy_hist.reindex(C.index).ffill()
                spy_ma200 = spy_c.rolling(200, min_periods=200).mean()
                bull = (spy_c > spy_ma200)
                bear_thresh = spy_c < spy_ma200 * 0.95
                for label, mask in (('bull', bull.reindex(spread.index).fillna(False)),
                                     ('bear', bear_thresh.reindex(spread.index).fillna(False))):
                    sub = spread[mask]
                    regime_spreads[label] = ann(sub.mean()) if len(sub) >= 30 else np.nan
                other_mask = ~(bull.reindex(spread.index).fillna(False) | bear_thresh.reindex(spread.index).fillna(False))
                sub = spread[other_mask]
                regime_spreads['sideways'] = ann(sub.mean()) if len(sub) >= 30 else np.nan

            horizon_rows[hlabel] = {
                'spread_ann': ann(spread.mean()), 'spread_tstat': spread_test['tstat'], 'spread_n': spread_test['n'],
                'q1_alpha_ann': ann(q1_reg['alpha_daily']), 'q1_tstat': q1_reg['tstat'], 'q1_r2': q1_reg['r_squared'], 'q1_betas': q1_reg['betas'],
                'spread_alpha_ann': ann(spread_reg['alpha_daily']), 'spread_reg_tstat': spread_reg['tstat'], 'spread_r2': spread_reg['r_squared'], 'spread_betas': spread_reg['betas'],
                'size_spreads': size_spreads, 'regime_spreads': regime_spreads,
                'avg_valid_names_per_day': float((score.where(elig_s).notna().sum(axis=1))[valid_day].mean()) if valid_day.any() else np.nan,
            }

        results[sname] = {'horizons': horizon_rows, 'spread_series_5d': _bound((fwd_cc[5].where(q1).mean(axis=1) - fwd_cc[5].where(q5).mean(axis=1)).dropna())}

    # ---------------- asymmetry analysis ----------------
    overnight_daily_mean = ON.where(eligible).mean(axis=1)
    intraday_daily_mean = intraday_fwd.where(eligible).mean(axis=1)
    close_daily_mean = (C / C.shift(1) - 1).where(eligible).mean(axis=1)
    overnight_daily_mean = _bound(overnight_daily_mean.dropna())
    intraday_daily_mean = _bound(intraday_daily_mean.dropna())
    close_daily_mean = _bound(close_daily_mean.dropna())

    asymmetry = {
        'overnight_ann': (1 + overnight_daily_mean.mean()) ** 252 - 1,
        'intraday_ann': (1 + intraday_daily_mean.mean()) ** 252 - 1,
        'close_to_close_ann': (1 + close_daily_mean.mean()) ** 252 - 1,
        'overnight_mean_daily': overnight_daily_mean.mean(),
        'intraday_mean_daily': intraday_daily_mean.mean(),
        'sum_check_daily': overnight_daily_mean.mean() + intraday_daily_mean.mean(),
        'actual_close_to_close_daily': close_daily_mean.mean(),
    }
    size_asym = {}
    for tercile, mask in size_tercile_mask.items():
        elig_t = eligible & mask
        on_t = _bound(ON.where(elig_t).mean(axis=1).dropna())
        ic_t = _bound(intraday_fwd.where(elig_t).mean(axis=1).dropna())
        size_asym[tercile] = {'overnight_ann': (1 + on_t.mean()) ** 252 - 1, 'intraday_ann': (1 + ic_t.mean()) ** 252 - 1}
    asymmetry['by_size'] = size_asym

    return {'signals': results, 'asymmetry': asymmetry}


if __name__ == '__main__':
    res = run('data/sharadar.db', '2020-01-01', '2021-12-31')
    for s, d in res['signals'].items():
        h1 = d['horizons'][1]
        print(s, h1['spread_ann'], h1['spread_tstat'])
    print(res['asymmetry'])
