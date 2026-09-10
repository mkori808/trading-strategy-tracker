"""Signal library characterization exercise (not an edge-finding exercise).

Nine daily cross-sectional technical signals, characterized -- not
validated -- across 4 forward-return horizons (1/5/10/20 days) on the full
2001-2024 history. No holdout, no standalone evidence gates: the question
is what each signal measures and what factor it loads on, for a future
signal-library reference.

Universe: full Sharadar PIT-eligible common stocks, price>=$1, trailing
63-day median dollar volume>=$500K, >=252 days history, excluding Finance,
Real Estate, and Healthcare by the Fama `sector` field (distinct from the
sicsector-merged Finance/RE convention used elsewhere in this codebase --
this exercise's spec calls for the three Fama-sector exclusions
separately, so `exclude_fama_sectors` carries all three here).

Everything is computed on wide (date x ticker) matrices with vectorized
pandas/numpy operations -- no per-stock Python loops. `open`/`high`/`low`/
`close` in the `stocks` table are already split-adjusted and mutually
consistent (confirmed against closeadj/closeunadj around a known 2020
AAPL split), so they're used directly with no extra adjustment step --
standard practice for technical signals, which are conventionally computed
on split-adjusted-only (not dividend-adjusted) prices.
"""
from __future__ import annotations
import sqlite3
import numpy as np
import pandas as pd
import statsmodels.api as sm
from utils.research_utils import load_universe_candidates, load_marketcap_panel

HORIZONS = (1, 5, 10, 20)
MIN_PER_QUINTILE_STOCKS = 20  # -> min 100 total names/day (20 * 5 quintiles) for a valid day; see _quintile_masks


def load_full_ohlc_panel(con, tickers: list[str], start: str, end: str) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame(columns=['ticker', 'date', 'open', 'high', 'low', 'close', 'volume'])
    q = ','.join('?' * len(tickers))
    chunks = pd.read_sql_query(
        f"SELECT ticker,date,open,high,low,close,volume FROM stocks WHERE ticker IN ({q}) AND date>=? AND date<=?",
        con, params=[*tickers, start, end], parse_dates=['date'], chunksize=500_000,
    )
    typed = []
    for c in chunks:
        for col in ('open', 'high', 'low', 'close', 'volume'):
            c[col] = pd.to_numeric(c[col], errors='coerce')
        typed.append(c)
    px = pd.concat(typed, ignore_index=True) if typed else pd.DataFrame(columns=['ticker', 'date', 'open', 'high', 'low', 'close', 'volume'])
    return px.sort_values(['ticker', 'date']).reset_index(drop=True)


def _wide(px: pd.DataFrame, col: str) -> pd.DataFrame:
    return px.pivot_table(index='date', columns='ticker', values=col, aggfunc='last').sort_index()


def build_eligibility(O, H, L, C, V, candidates: pd.DataFrame) -> pd.DataFrame:
    """Vectorized daily eligibility mask (date x ticker bool): price>=1,
    trailing-63d median dollar volume>=$500K, >=252 days of price history.
    Sector exclusion is applied earlier (candidates is already pre-filtered
    when passed in), so this only re-applies the as-of-date-dependent
    price/liquidity/history filters."""
    price_ok = C >= 1.0
    dollar_vol = C * V
    dv_med = dollar_vol.rolling(63, min_periods=63).median()
    liquidity_ok = dv_med >= 500_000
    history_ok = C.notna().cumsum() >= 252
    return price_ok.fillna(False) & liquidity_ok.fillna(False) & history_ok.fillna(False)


def _rsi(C: pd.DataFrame, period: int) -> pd.DataFrame:
    delta = C.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi.where(avg_loss != 0, 100.0)  # no losses in window -> RSI=100 by convention


def compute_signals(O, H, L, C, V, candidates: pd.DataFrame) -> dict[str, pd.DataFrame]:
    signals = {}

    ema21 = C.ewm(span=21, adjust=False).mean()
    signals['S1_pullback_ema21'] = -(C - ema21) / ema21

    ema9 = C.ewm(span=9, adjust=False).mean()
    signals['S2_ema_crossover'] = (ema9 - ema21) / C

    rsi14 = _rsi(C, 14)
    signals['S3_rsi14_oversold'] = -rsi14

    rsi2 = _rsi(C, 2)
    signals['S4_rsi2_connors'] = -rsi2

    ibs = (C - L) / (H - L).replace(0, np.nan)
    signals['S5_ibs'] = -ibs

    prev_c = C.shift(1)
    tr = np.maximum(np.maximum((H - L).to_numpy(), (H - prev_c).abs().to_numpy()), (L - prev_c).abs().to_numpy())
    tr = pd.DataFrame(tr, index=C.index, columns=C.columns)
    atr20 = tr.rolling(20, min_periods=20).mean()
    high20 = H.rolling(20, min_periods=20).max()
    signals['S6_breakout'] = (C - high20) / atr20.replace(0, np.nan)

    dow = pd.Series(C.index.dayofweek, index=C.index)
    is_monday = (dow == 0)
    down_monday = (C < C.shift(1))
    s7 = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    s7.loc[is_monday] = down_monday.loc[is_monday].astype(float)
    signals['S7_turnaround_tuesday'] = s7

    signals['S8_gap_fade'] = -(O - prev_c) / prev_c

    ret63 = C / C.shift(63) - 1
    sector_median = pd.DataFrame(np.nan, index=C.index, columns=C.columns)
    sector_of = candidates.set_index('ticker')['sector']
    for sec, grp in sector_of.groupby(sector_of):
        cols = [c for c in grp.index if c in ret63.columns]
        if len(cols) < 3:
            continue
        med = ret63[cols].median(axis=1)
        sector_median[cols] = np.broadcast_to(med.values.reshape(-1, 1), (len(med), len(cols)))
    signals['S9_sector_rotation'] = ret63 / sector_median.replace(0, np.nan)

    return signals


def _winsorize_wide(returns: pd.DataFrame, lower=0.01, upper=0.99) -> pd.DataFrame:
    arr = returns.to_numpy(dtype=float)
    lo = np.nanpercentile(arr, lower * 100, axis=1, keepdims=True)
    hi = np.nanpercentile(arr, upper * 100, axis=1, keepdims=True)
    clipped = np.clip(arr, lo, hi)
    return pd.DataFrame(clipped, index=returns.index, columns=returns.columns)


def _binary_group_masks(score: pd.DataFrame, eligible: pd.DataFrame, min_names: int = 20):
    """For a binary 0/1 score (S7): Q1 = score==1 (flagged), Q5 = score==0
    (not flagged), restricted to days the score is even defined (non-NaN,
    i.e. Mondays for S7). Ties within a binary field break the generic
    percentile-rank quintile logic (an entire tied group shares one average
    rank, so it either all clears a threshold or none of it does, which
    made Q1 and Q5 populate on almost entirely disjoint days when tested)
    -- a direct group split avoids that."""
    s = score.where(eligible)
    q1 = (s == 1)
    q5 = (s == 0)
    valid_day = (q1.sum(axis=1) >= min_names) & (q5.sum(axis=1) >= min_names)
    q1 = q1 & valid_day.to_numpy()[:, None]
    q5 = q5 & valid_day.to_numpy()[:, None]
    return q1, q5, valid_day


def _quintile_masks(score: pd.DataFrame, eligible: pd.DataFrame, min_names: int = 100):
    """Vectorized daily Q1 (top 20%, most favorable) / Q5 (bottom 20%) masks
    via cross-sectional percentile rank. A day with fewer than min_names
    eligible+scored names is entirely masked out (NaN rank -> False)."""
    s = score.where(eligible)
    n_valid = s.notna().sum(axis=1)
    valid_day = n_valid >= min_names
    pct = s.rank(axis=1, pct=True)
    q1 = pct.ge(0.8) & valid_day.to_numpy()[:, None]
    q5 = pct.le(0.2) & valid_day.to_numpy()[:, None]
    return q1, q5, valid_day


def _fwd_returns(C: pd.DataFrame, h: int) -> pd.DataFrame:
    return C.shift(-h) / C - 1


def _factor_h_returns(factors_daily_pct: pd.DataFrame, h: int) -> pd.DataFrame:
    """h-day-ahead compounded factor return aligned to formation date t
    (i.e. the return over [t+1, t+h]), for every column, via cumulative
    log returns -- exact compounding, fully vectorized (no loops)."""
    gross = 1 + factors_daily_pct / 100
    logg = np.log(gross)
    cumlog = logg.cumsum()
    fwd_log = cumlog.shift(-h) - cumlog
    return np.exp(fwd_log) - 1


def _hac_regress(y: pd.Series, X: pd.DataFrame | None, h: int):
    """OLS with HAC (Newey-West) standard errors, maxlags=h. X=None -> a
    simple mean test (constant only), used for the raw spread t-stat."""
    df = pd.concat([y.rename('y')] + ([X] if X is not None else []), axis=1).dropna()
    if len(df) < 30:
        return {'alpha_daily': np.nan, 'tstat': np.nan, 'r_squared': np.nan, 'betas': {}, 'n': len(df)}
    exog = sm.add_constant(df.drop(columns='y')) if X is not None else sm.add_constant(pd.DataFrame(index=df.index))
    model = sm.OLS(df['y'], exog).fit(cov_type='HAC', cov_kwds={'maxlags': h})
    betas = model.params.drop('const').to_dict() if X is not None else {}
    return {'alpha_daily': float(model.params['const']), 'tstat': float(model.tvalues['const']),
            'r_squared': float(model.rsquared) if X is not None else np.nan, 'betas': betas, 'n': len(df)}


def run(db_path: str, start: str = '2001-01-01', end: str = '2024-12-31') -> dict:
    con = sqlite3.connect(db_path)
    candidates = load_universe_candidates(con, exclude_sectors=[], exclude_fama_sectors=['Financial Services', 'Real Estate', 'Healthcare'])
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=500)).strftime('%Y-%m-%d')
    window_start = pd.Timestamp(load_start)
    window_end = pd.Timestamp(end)
    candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
    universe_tickers = candidates.ticker.tolist()

    px = load_full_ohlc_panel(con, universe_tickers, load_start, end)
    mc_panel = load_marketcap_panel(con, universe_tickers, start, end)
    con.close()

    O, H, L, C, V = (_wide(px, c) for c in ('open', 'high', 'low', 'close', 'volume'))
    eligible = build_eligibility(O, H, L, C, V, candidates)
    signals = compute_signals(O, H, L, C, V, candidates)

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

    # Sample-boundary fix: C/eligible/signals/fwd all span [load_start, end]
    # (load_start is ~500 days before `start`, needed to warm up rolling
    # windows like the 252-day history filter and 63-day liquidity median).
    # Nothing bounded the STATS to [start, end] though -- spread_test used
    # X=None (no factor merge to implicitly drop pre-start rows via dropna
    # the way q1_reg/spread_reg did), so warmup-buffer dates silently
    # entered every signal's headline spread_tstat/spread_ann. Confirmed via
    # the IBS look-ahead verification's Check 2 spot check, which surfaced a
    # 2000-12-29 date in the "2001-2024" numbers. Fixed by explicitly
    # bounding every date-indexed series used for stats below.
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)

    def _bound(s: pd.Series) -> pd.Series:
        return s[(s.index >= start_ts) & (s.index <= end_ts)]

    fwd = {h: _winsorize_wide(_fwd_returns(C, h)) for h in HORIZONS}
    factor_h = {h: _factor_h_returns(ff5_only, h) for h in HORIZONS}
    rf_h = {h: _factor_h_returns(rf_daily.to_frame('RF'), h)['RF'] for h in HORIZONS}

    results = {}
    for sname, score in signals.items():
        is_binary = (sname == 'S7_turnaround_tuesday')
        if is_binary:
            elig_s = eligible & score.notna()
            min_names = 20
            q1, q5, valid_day = _binary_group_masks(score, elig_s, min_names=min_names)
        else:
            elig_s = eligible
            min_names = MIN_PER_QUINTILE_STOCKS * 5
            q1, q5, valid_day = _quintile_masks(score, elig_s, min_names=min_names)

        horizon_rows = {}
        for h in HORIZONS:
            r = fwd[h]
            q1_ret = r.where(q1).mean(axis=1)
            q5_ret = r.where(q5).mean(axis=1)
            spread = _bound((q1_ret - q5_ret).dropna())

            spread_test = _hac_regress(spread, None, h)
            fac_al = factor_h[h].reindex(spread.index)
            q1_excess = (q1_ret.reindex(spread.index) - rf_h[h].reindex(spread.index))
            q1_reg = _hac_regress(q1_excess, fac_al, h)
            spread_reg = _hac_regress(spread, fac_al, h)

            def ann(daily_h_return):
                return (1 + daily_h_return) ** (252 / h) - 1 if pd.notna(daily_h_return) else np.nan

            spread_ann = ann(spread.mean())
            q1_alpha_ann = ann(q1_reg['alpha_daily'])
            spread_alpha_ann = ann(spread_reg['alpha_daily'])

            size_spreads = {}
            for tercile, mask in size_tercile_mask.items():
                elig_t = elig_s & mask
                if is_binary:
                    q1t, q5t, _ = _binary_group_masks(score, elig_t, min_names=max(5, min_names // 4))
                else:
                    q1t, q5t, _ = _quintile_masks(score, elig_t, min_names=max(10, min_names // 5))
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

            horizon_rows[h] = {
                'spread_ann': spread_ann, 'spread_tstat': spread_test['tstat'], 'spread_n': spread_test['n'],
                'q1_alpha_ann': q1_alpha_ann, 'q1_tstat': q1_reg['tstat'], 'q1_r2': q1_reg['r_squared'], 'q1_betas': q1_reg['betas'],
                'spread_alpha_ann': spread_alpha_ann, 'spread_reg_tstat': spread_reg['tstat'], 'spread_r2': spread_reg['r_squared'], 'spread_betas': spread_reg['betas'],
                'size_spreads': size_spreads, 'regime_spreads': regime_spreads,
                'avg_valid_names_per_day': float((score.where(elig_s).notna().sum(axis=1))[valid_day].mean()) if valid_day.any() else np.nan,
            }

        results[sname] = {'horizons': horizon_rows, 'spread_series_5d': _bound((fwd[5].where(q1).mean(axis=1) - fwd[5].where(q5).mean(axis=1)).dropna())}

    return results


if __name__ == '__main__':
    res = run('data/sharadar.db', '2020-01-01', '2021-12-31')
    for s, d in res.items():
        h5 = d['horizons'][5]
        print(s, h5['spread_ann'], h5['spread_tstat'], h5['q1_r2'])
