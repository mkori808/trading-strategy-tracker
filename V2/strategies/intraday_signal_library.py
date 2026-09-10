"""Intraday signal library characterization exercise (not an edge-finding
exercise). Six cross-sectional intraday/gap signals, characterized -- not
validated -- across 4 forward-return horizons on the full 2020-2026 history
available from the Alpaca IEX 15-minute bar feed.

Universe: PIT S&P 500 membership (2020-2026), intersected with whatever
tickers actually have Alpaca bars_15m coverage. This is a different, much
narrower universe than the daily library's full Sharadar PIT-eligible
universe -- see the correlation-matrix step, which restricts to the
overlapping universe/date range before comparing against daily-library
signals.

Step 0 (build_daily_summary / join_sharadar / apply_baseline_filters) is
computed once and its output reused for all six signals, per the exercise
spec -- no recomputation from raw 15-minute bars per signal.
"""
from __future__ import annotations
import sqlite3
import numpy as np
import pandas as pd
import statsmodels.api as sm

HORIZONS_CC = (1, 5, 20)  # close-to-close horizons (H2/H3/H4); H1 (intraday) handled separately
MIN_PER_QUINTILE = 20  # -> min 100 total scored+eligible names/day


# ---------------------------------------------------------------------------
# Step 0A -- daily summary from 15-minute bars (single SQL pass, no per-signal
# recomputation). rn = intraday bar sequence number within ticker/day.
# ---------------------------------------------------------------------------
DAILY_SUMMARY_SQL = """
WITH numbered AS (
  SELECT ticker, DATE(timestamp) AS d, timestamp, open, high, low, close, volume,
    ROW_NUMBER() OVER (PARTITION BY ticker, DATE(timestamp) ORDER BY timestamp) AS rn,
    COUNT(*) OVER (PARTITION BY ticker, DATE(timestamp)) AS n_bars
  FROM bars_15m
)
SELECT
  ticker, d AS date,
  MAX(CASE WHEN rn = 1 THEN open END) AS open_price,
  MAX(CASE WHEN rn = n_bars THEN close END) AS close_price,
  MAX(high) AS high_price,
  MIN(low) AS low_price,
  SUM(volume) AS total_volume,
  CASE WHEN SUM(volume) > 0 THEN SUM(close * volume) * 1.0 / SUM(volume) END AS vwap_close_conv,
  CASE WHEN SUM(volume) > 0 THEN SUM((high + low) / 2.0 * volume) * 1.0 / SUM(volume) END AS vwap_mid_conv,
  MAX(CASE WHEN rn <= 2 THEN high END) AS or_high_30,
  MIN(CASE WHEN rn <= 2 THEN low END) AS or_low_30,
  MAX(CASE WHEN rn <= 4 THEN high END) AS or_high_60,
  MIN(CASE WHEN rn <= 4 THEN low END) AS or_low_60,
  MAX(CASE WHEN rn = 2 THEN close END) AS close_30,
  MAX(CASE WHEN rn = 4 THEN close END) AS close_60,
  MAX(CASE WHEN rn >= n_bars - 1 THEN high END) AS last_30_high,
  MIN(CASE WHEN rn >= n_bars - 1 THEN low END) AS last_30_low,
  MAX(n_bars) AS n_bars
FROM numbered
GROUP BY ticker, d
"""


def build_daily_summary(intraday_con: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query(DAILY_SUMMARY_SQL, intraday_con, parse_dates=['date'])
    return df.sort_values(['ticker', 'date']).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Step 0B -- Sharadar joins: prior close/high/low/volume, trailing 20d $vol,
# ATR20, earnings/ex-div/split flags.
# ---------------------------------------------------------------------------
def load_sharadar_daily_panel(sharadar_con: sqlite3.Connection, tickers: list[str], start: str, end: str) -> pd.DataFrame:
    q = ','.join('?' * len(tickers))
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=90)).strftime('%Y-%m-%d')
    px = pd.read_sql_query(
        f"SELECT ticker,date,open,high,low,close,closeadj,volume FROM stocks WHERE ticker IN ({q}) AND date>=? AND date<=?",
        sharadar_con, params=[*tickers, load_start, end], parse_dates=['date'],
    )
    for c in ('open', 'high', 'low', 'close', 'closeadj', 'volume'):
        px[c] = pd.to_numeric(px[c], errors='coerce')
    return px.sort_values(['ticker', 'date']).reset_index(drop=True)


def add_dollar_volume(px: pd.DataFrame) -> pd.DataFrame:
    """Sharadar-side liquidity filter only. Sharadar's `close`/`volume` are
    continuously split-adjusted (retroactively rescaled for ANY split in the
    ticker's history, including ones after the date in question) -- their
    product still approximates true dollar volume (adjustment rescales price
    down and volume up in offsetting proportion), so it's fine for a
    liquidity floor. It is NOT fine as a price *level* to mix with Alpaca's
    raw (unadjusted) intraday prices -- see add_prior_day_and_atr_intraday,
    which derives prior_close/prior_high/prior_low/atr_20 from Alpaca's own
    raw daily aggregates instead, to keep signal construction in one
    consistent price scale. (Confirmed empirically: CMG's 2024 50:1 split
    makes Sharadar's adjusted 2022 close ~$29 vs Alpaca's raw 2022 close
    ~$1500 -- an 830x mismatch that produced a nonsensical 52x "overnight
    return" until this was caught.)"""
    px = px.sort_values(['ticker', 'date']).copy()
    dollar_vol = px['close'] * px['volume']
    px['dollar_volume'] = dollar_vol.groupby(px['ticker']).transform(lambda s: s.rolling(20, min_periods=20).mean())
    return px


def add_prior_day_and_atr_intraday(daily: pd.DataFrame) -> pd.DataFrame:
    """prior_close/prior_high/prior_low/atr_20 derived from Alpaca's own raw
    daily aggregates (open_price/high_price/low_price/close_price), so gap/
    pivot/breakout signals never mix Alpaca-raw and Sharadar-adjusted price
    scales. See add_dollar_volume's docstring for why that mixing is wrong."""
    daily = daily.sort_values(['ticker', 'date']).copy()
    g = daily.groupby('ticker', group_keys=False)
    daily['prior_close'] = g['close_price'].shift(1)
    daily['prior_high'] = g['high_price'].shift(1)
    daily['prior_low'] = g['low_price'].shift(1)
    prev_c = g['close_price'].shift(1)
    tr = pd.concat([
        daily['high_price'] - daily['low_price'],
        (daily['high_price'] - prev_c).abs(),
        (daily['low_price'] - prev_c).abs(),
    ], axis=1).max(axis=1)
    daily['atr_20'] = tr.groupby(daily['ticker']).transform(lambda s: s.rolling(20, min_periods=20).mean())
    return daily


def load_earnings_dates(sharadar_con: sqlite3.Connection, tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """`fundamentals.date` is Sharadar's filing/report date (datekey-equivalent
    field), the closest PIT-safe proxy to earnings-announcement date available
    in this DB (there is no separate SF1/events table)."""
    q = ','.join('?' * len(tickers))
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=10)).strftime('%Y-%m-%d')
    df = pd.read_sql_query(
        f"SELECT DISTINCT ticker, date FROM fundamentals WHERE ticker IN ({q}) AND date>=? AND date<=?",
        sharadar_con, params=[*tickers, load_start, end], parse_dates=['date'],
    )
    return df


def load_actions(sharadar_con: sqlite3.Connection, tickers: list[str], start: str, end: str) -> pd.DataFrame:
    q = ','.join('?' * len(tickers))
    df = pd.read_sql_query(
        f"SELECT ticker, date, action FROM actions WHERE ticker IN ({q}) AND date>=? AND date<=?",
        sharadar_con, params=[*tickers, start, end], parse_dates=['date'],
    )
    return df


def join_sharadar(daily_unbounded: pd.DataFrame, sharadar_con: sqlite3.Connection, start: str, end: str) -> pd.DataFrame:
    """`daily_unbounded` must span some warmup history before `start` (>=20
    trading days) so atr_20/prior_close have no artificial warmup gap at the
    start of the analysis window; the result is truncated to [start,end]
    at the end, after that warmup is consumed."""
    tickers = daily_unbounded.ticker.unique().tolist()
    daily = add_prior_day_and_atr_intraday(daily_unbounded)

    px = load_sharadar_daily_panel(sharadar_con, tickers, start, end)
    px = add_dollar_volume(px)

    out = daily.merge(
        px[['ticker', 'date', 'dollar_volume', 'closeadj']],
        on=['ticker', 'date'], how='left',
    )
    out = out[(out.date >= pd.Timestamp(start)) & (out.date <= pd.Timestamp(end))].reset_index(drop=True)

    earn = load_earnings_dates(sharadar_con, tickers, start, end)
    earn_dates = set(zip(earn.ticker, earn.date))
    all_dates = sorted(out.date.unique())
    date_idx = pd.Series(range(len(all_dates)), index=all_dates)
    is_earn = np.zeros(len(out), dtype=bool)
    if len(earn):
        # within +-1 trading day of any filing date, per ticker
        earn_by_ticker = earn.groupby('ticker')['date'].apply(set).to_dict()
        for i, (tkr, d) in enumerate(zip(out.ticker.values, out.date.values)):
            fset = earn_by_ticker.get(tkr)
            if not fset:
                continue
            di = date_idx.get(d)
            if di is None:
                continue
            neighbor_dates = {all_dates[j] for j in (di - 1, di, di + 1) if 0 <= j < len(all_dates)}
            if fset & neighbor_dates:
                is_earn[i] = True
    out['is_earnings_day'] = is_earn

    actions = load_actions(sharadar_con, tickers, start, end)
    exdiv = set(zip(actions[actions.action == 'dividend'].ticker, actions[actions.action == 'dividend'].date))
    split = set(zip(actions[actions.action.isin(['split', 'adrratiosplit'])].ticker, actions[actions.action.isin(['split', 'adrratiosplit'])].date))
    out['is_exdiv_day'] = [(t, d) in exdiv for t, d in zip(out.ticker, out.date)]
    out['is_split_day'] = [(t, d) in split for t, d in zip(out.ticker, out.date)]

    return out


# ---------------------------------------------------------------------------
# Step 0C -- baseline filters
# ---------------------------------------------------------------------------
def apply_baseline_filters(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    n0 = len(df)
    report = {'total': n0}
    mask = pd.Series(True, index=df.index)

    for name, cond in [
        ('is_earnings_day', df['is_earnings_day']),
        ('is_exdiv_day', df['is_exdiv_day']),
        ('is_split_day', df['is_split_day']),
        ('prior_close_null', df['prior_close'].isna()),
        ('dollar_volume_lt_1m', df['dollar_volume'].fillna(0) < 1_000_000),
        ('total_volume_zero', df['total_volume'].fillna(0) == 0),
    ]:
        excluded_here = cond & mask
        report[name] = int(excluded_here.sum())
        mask &= ~cond

    report['remaining'] = int(mask.sum())
    report['pct_remaining'] = 100.0 * mask.sum() / n0 if n0 else float('nan')
    return df[mask].reset_index(drop=True), report


# ---------------------------------------------------------------------------
# Signal construction (S1-S6), computed on the wide date x ticker layout.
# ---------------------------------------------------------------------------
def _wide(df: pd.DataFrame, col: str) -> pd.DataFrame:
    return df.pivot_table(index='date', columns='ticker', values=col, aggfunc='last').sort_index()


def compute_signals(panel: pd.DataFrame) -> dict[str, pd.DataFrame]:
    close = _wide(panel, 'close_price')
    open_ = _wide(panel, 'open_price')
    or_high_30 = _wide(panel, 'or_high_30')
    or_high_60 = _wide(panel, 'or_high_60')
    vwap = _wide(panel, 'vwap_close_conv')
    prior_close = _wide(panel, 'prior_close')
    prior_high = _wide(panel, 'prior_high')
    prior_low = _wide(panel, 'prior_low')
    atr20 = _wide(panel, 'atr_20')
    close_30 = _wide(panel, 'close_30')

    signals = {}

    breakout_score = (close - or_high_30) / atr20.replace(0, np.nan)
    signals['S1_orb_30'] = breakout_score.rank(axis=1, pct=True)

    breakout_score_60 = (close - or_high_60) / atr20.replace(0, np.nan)
    signals['S1b_orb_60'] = breakout_score_60.rank(axis=1, pct=True)

    vwap_score = (close - vwap) / vwap.replace(0, np.nan)
    signals['S2_vwap_position'] = (-vwap_score).rank(axis=1, pct=True)

    gap = (open_ - prior_close) / prior_close.replace(0, np.nan)
    signals['S3_gap_fade'] = (-gap).rank(axis=1, pct=True)
    signals['S4_gap_and_go'] = gap.rank(axis=1, pct=True)
    signals['_gap_raw'] = gap  # kept for the earnings sub-group step

    pivot = (prior_high + prior_low + prior_close) / 3.0
    support_1 = 2 * pivot - prior_high
    price_vs_support = (open_ - support_1) / support_1.replace(0, np.nan)
    signals['S5_pivot_reversal'] = (-price_vs_support).rank(axis=1, pct=True)

    morning_return = close_30 / open_.replace(0, np.nan) - 1
    signals['S6_intraday_reversal'] = (-morning_return).rank(axis=1, pct=True)

    return signals


# ---------------------------------------------------------------------------
# Quintiles, forward returns, HAC regression -- mirrors the daily library's
# conventions (see V2/strategies/signal_library.py) for consistency.
# ---------------------------------------------------------------------------
def _quintile_masks(score: pd.DataFrame, eligible: pd.DataFrame, min_names: int = MIN_PER_QUINTILE * 5):
    s = score.where(eligible)
    n_valid = s.notna().sum(axis=1)
    valid_day = n_valid >= min_names
    q1 = s.ge(0.8) & valid_day.to_numpy()[:, None]
    q5 = s.le(0.2) & valid_day.to_numpy()[:, None]
    return q1, q5, valid_day


def _winsorize_wide(returns: pd.DataFrame, lower=0.01, upper=0.99) -> pd.DataFrame:
    arr = returns.to_numpy(dtype=float)
    lo = np.nanpercentile(arr, lower * 100, axis=1, keepdims=True)
    hi = np.nanpercentile(arr, upper * 100, axis=1, keepdims=True)
    return pd.DataFrame(np.clip(arr, lo, hi), index=returns.index, columns=returns.columns)


def _hac_regress(y: pd.Series, X: pd.DataFrame | None, maxlags: int):
    df = pd.concat([y.rename('y')] + ([X] if X is not None else []), axis=1).dropna()
    if len(df) < 30:
        return {'alpha_daily': np.nan, 'tstat': np.nan, 'r_squared': np.nan, 'betas': {}, 'n': len(df)}
    exog = sm.add_constant(df.drop(columns='y')) if X is not None else sm.add_constant(pd.DataFrame(index=df.index))
    model = sm.OLS(df['y'], exog).fit(cov_type='HAC', cov_kwds={'maxlags': maxlags})
    betas = model.params.drop('const').to_dict() if X is not None else {}
    return {'alpha_daily': float(model.params['const']), 'tstat': float(model.tvalues['const']),
            'r_squared': float(model.rsquared) if X is not None else np.nan, 'betas': betas, 'n': len(df)}


def _factor_h_returns(factors_daily_pct: pd.DataFrame, h: int) -> pd.DataFrame:
    gross = 1 + factors_daily_pct / 100
    cumlog = np.log(gross).cumsum()
    return np.exp(cumlog.shift(-h) - cumlog) - 1


def run(intraday_db_path: str, sharadar_db_path: str, start: str = '2020-07-27', end: str = '2026-09-05') -> dict:
    icon = sqlite3.connect(intraday_db_path)
    daily = build_daily_summary(icon)
    icon.close()

    scon = sqlite3.connect(sharadar_db_path)
    panel = join_sharadar(daily, scon, start, end)

    filtered, filter_report = apply_baseline_filters(panel)
    unfiltered = panel.copy()  # kept for the earnings sub-group (Step 6), which needs earnings days back

    signals = compute_signals(filtered)
    eligible = _wide(filtered, 'close_price').notna()

    closeadj = _wide(filtered, 'closeadj')
    fwd_cc = {h: _winsorize_wide(closeadj.shift(-h) / closeadj - 1) for h in HORIZONS_CC}
    close_intraday = _wide(filtered, 'close_price')
    open_intraday = _wide(filtered, 'open_price')
    fwd_intraday = _winsorize_wide(close_intraday / open_intraday - 1)

    import pandas_datareader.data as web
    ff5_daily = web.DataReader('F-F_Research_Data_5_Factors_2x3_daily', 'famafrench', start=start)[0]
    ff5_daily.index = ff5_daily.index.to_timestamp()
    ff5_only = ff5_daily[['Mkt-RF', 'SMB', 'HML', 'RMW', 'CMA']]
    factor_h = {h: _factor_h_returns(ff5_only, h) for h in HORIZONS_CC}

    scon.close()

    results = {}
    for sname, score in signals.items():
        if sname == '_gap_raw':
            continue
        q1, q5, valid_day = _quintile_masks(score, eligible)

        horizon_rows = {}

        intra_spread = (fwd_intraday.where(q1).mean(axis=1) - fwd_intraday.where(q5).mean(axis=1)).dropna()
        intra_test = _hac_regress(intra_spread, None, 1)
        horizon_rows['intraday'] = {
            'spread_daily_mean': float(intra_spread.mean()) if len(intra_spread) else np.nan,
            'spread_ann': (1 + intra_spread.mean()) ** 252 - 1 if len(intra_spread) else np.nan,
            'spread_tstat': intra_test['tstat'], 'spread_n': intra_test['n'],
        }

        for h in HORIZONS_CC:
            r = fwd_cc[h]
            q1_ret = r.where(q1).mean(axis=1)
            q5_ret = r.where(q5).mean(axis=1)
            spread = (q1_ret - q5_ret).dropna()
            spread_test = _hac_regress(spread, None, h)

            fac_al = factor_h[h].reindex(spread.index)
            spread_reg = _hac_regress(spread, fac_al, h)

            def ann(daily_h_return):
                return (1 + daily_h_return) ** (252 / h) - 1 if pd.notna(daily_h_return) else np.nan

            horizon_rows[h] = {
                'spread_ann': ann(spread.mean()), 'spread_tstat': spread_test['tstat'], 'spread_n': spread_test['n'],
                'spread_alpha_ann': ann(spread_reg['alpha_daily']), 'spread_reg_tstat': spread_reg['tstat'],
                'spread_r2': spread_reg['r_squared'], 'spread_betas': spread_reg['betas'],
            }

        # single FF5 regression per signal on the 1-day spread series (used to
        # represent the signal's systematic risk exposure across H2-H4)
        one_day_spread = (fwd_cc[1].where(q1).mean(axis=1) - fwd_cc[1].where(q5).mean(axis=1)).dropna()
        ff5_reg = _hac_regress(one_day_spread, factor_h[1].reindex(one_day_spread.index), 1)

        results[sname] = {'horizons': horizon_rows, 'ff5_regression': ff5_reg,
                           'spread_series_1d': one_day_spread, 'avg_names_per_day': float(score.where(eligible).notna().sum(axis=1)[valid_day].mean()) if valid_day.any() else np.nan}

    return {
        'results': results,
        'filter_report': filter_report,
        'signals_raw': signals,
        'eligible': eligible,
        'daily_summary': daily,
        'panel_filtered': filtered,
        'panel_unfiltered': unfiltered,
        'fwd_cc': fwd_cc,
        'fwd_intraday': fwd_intraday,
    }


# ---------------------------------------------------------------------------
# Step 6 -- earnings-day sub-group for the gap signals (S3/S4). Applies every
# baseline filter EXCEPT the earnings exclusion, then splits the resulting
# Q1/Q5 gap portfolios by whether that stock-day is an earnings day.
# ---------------------------------------------------------------------------
def earnings_subgroup(panel_unfiltered: pd.DataFrame, start: str, end: str) -> dict:
    df = panel_unfiltered.copy()
    mask = ~(df.is_exdiv_day | df.is_split_day | df.prior_close.isna()
              | (df.dollar_volume.fillna(0) < 1_000_000) | (df.total_volume.fillna(0) == 0))
    df = df[mask & (df.date >= pd.Timestamp(start)) & (df.date <= pd.Timestamp(end))].reset_index(drop=True)

    open_ = _wide(df, 'open_price')
    close_ = _wide(df, 'close_price')
    prior_close = _wide(df, 'prior_close')
    closeadj = _wide(df, 'closeadj')
    earn_wide = _wide(df, 'is_earnings_day').fillna(0).astype(bool)
    eligible = close_.notna()

    gap = (open_ - prior_close) / prior_close.replace(0, np.nan)
    gap_fade_score = (-gap).rank(axis=1, pct=True)
    gap_go_score = gap.rank(axis=1, pct=True)

    fwd_intraday = _winsorize_wide(close_ / open_ - 1)
    fwd_1d = _winsorize_wide(closeadj.shift(-1) / closeadj - 1)

    out = {}
    for sname, score in [('S3_gap_fade', gap_fade_score), ('S4_gap_and_go', gap_go_score)]:
        q1, q5, valid_day = _quintile_masks(score, eligible, min_names=MIN_PER_QUINTILE * 5)
        row = {}
        for label, fwd in [('intraday', fwd_intraday), ('1day', fwd_1d)]:
            for grp_label, grp_mask in [('earnings', earn_wide), ('non_earnings', ~earn_wide)]:
                q1g = q1 & grp_mask
                q5g = q5 & grp_mask
                spread = (fwd.where(q1g).mean(axis=1) - fwd.where(q5g).mean(axis=1)).dropna()
                test = _hac_regress(spread, None, 1)
                ann = (1 + spread.mean()) ** 252 - 1 if label == 'intraday' else (1 + spread.mean()) ** 252 - 1
                row[f'{label}_{grp_label}'] = {'spread_ann': ann if len(spread) else np.nan, 'tstat': test['tstat'], 'n': test['n']}
        out[sname] = row
    return out


# ---------------------------------------------------------------------------
# VWAP convention comparison: close*volume vs (high+low)/2*volume.
# ---------------------------------------------------------------------------
def vwap_convention_compare(panel_filtered: pd.DataFrame, fwd_cc: dict) -> dict:
    close_ = _wide(panel_filtered, 'close_price')
    eligible = close_.notna()
    out = {}
    for label, col in [('close_x_volume', 'vwap_close_conv'), ('midpoint_x_volume', 'vwap_mid_conv')]:
        vwap = _wide(panel_filtered, col)
        vwap_score = (close_ - vwap) / vwap.replace(0, np.nan)
        score = (-vwap_score).rank(axis=1, pct=True)
        q1, q5, _ = _quintile_masks(score, eligible)
        spread = (fwd_cc[1].where(q1).mean(axis=1) - fwd_cc[1].where(q5).mean(axis=1)).dropna()
        test = _hac_regress(spread, None, 1)
        out[label] = {'spread_ann': (1 + spread.mean()) ** 252 - 1 if len(spread) else np.nan, 'tstat': test['tstat'], 'n': test['n']}
    return out


# ---------------------------------------------------------------------------
# Overnight vs intraday return decomposition, computed directly on this
# intraday universe/period (independent of the daily-library's Task A run,
# which covers a different, broader universe -- see report notes).
# ---------------------------------------------------------------------------
def overnight_intraday_check(panel_filtered: pd.DataFrame) -> dict:
    """Equal-weighted daily average, winsorized cross-sectionally at 1/99pct
    per day like every other return series here -- a handful of bad/extreme
    prior_close or open_price values otherwise dominate the raw mean."""
    open_ = _wide(panel_filtered, 'open_price')
    close_ = _wide(panel_filtered, 'close_price')
    prior_close = _wide(panel_filtered, 'prior_close')

    overnight_w = _winsorize_wide(open_ / prior_close.replace(0, np.nan) - 1)
    intraday_w = _winsorize_wide(close_ / open_.replace(0, np.nan) - 1)
    cc_w = _winsorize_wide(close_ / prior_close.replace(0, np.nan) - 1)

    overnight_daily = overnight_w.mean(axis=1)
    intraday_daily = intraday_w.mean(axis=1)
    cc_daily = cc_w.mean(axis=1)

    return {
        'overnight_mean_daily': float(overnight_daily.mean()),
        'intraday_mean_daily': float(intraday_daily.mean()),
        'close_to_close_mean_daily': float(cc_daily.mean()),
        'overnight_ann': float((1 + overnight_daily.mean()) ** 252 - 1),
        'intraday_ann': float((1 + intraday_daily.mean()) ** 252 - 1),
        'close_to_close_ann': float((1 + cc_daily.mean()) ** 252 - 1),
        'sum_check_daily': float(overnight_daily.mean() + intraday_daily.mean()),
        'n': int(overnight_daily.notna().sum()),
    }


# ---------------------------------------------------------------------------
# Cross-signal correlation: intraday signals (best horizon 1-day spread
# series) vs daily-library IBS(1d)/RSI2(1d)/Sector RS(20d), computed on the
# overlapping universe and date range only.
# ---------------------------------------------------------------------------
def correlation_with_daily_library(sharadar_db_path: str, tickers: list[str], start: str, end: str, intraday_spread_series: dict) -> pd.DataFrame:
    import sys
    sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
    from strategies.signal_library import load_full_ohlc_panel, build_eligibility, compute_signals as daily_compute_signals, _quintile_masks as daily_quintile_masks, _fwd_returns, _winsorize_wide as daily_winsorize
    from utils.research_utils import load_universe_candidates

    con = sqlite3.connect(sharadar_db_path)
    candidates = load_universe_candidates(con, exclude_sectors=[], exclude_fama_sectors=[])
    candidates = candidates[candidates.ticker.isin(tickers)].reset_index(drop=True)
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=200)).strftime('%Y-%m-%d')
    px = load_full_ohlc_panel(con, candidates.ticker.tolist(), load_start, end)
    con.close()

    O, H, L, C, V = (px.pivot_table(index='date', columns='ticker', values=c, aggfunc='last').sort_index() for c in ('open', 'high', 'low', 'close', 'volume'))
    eligible = build_eligibility(O, H, L, C, V, candidates)
    daily_signals = daily_compute_signals(O, H, L, C, V, candidates)

    wanted = {'IBS_1d': ('S5_ibs', 1), 'RSI2_1d': ('S4_rsi2_connors', 1), 'SectorRS_20d': ('S9_sector_rotation', 20)}
    daily_series = {}
    for label, (sname, h) in wanted.items():
        score = daily_signals[sname]
        q1, q5, valid_day = daily_quintile_masks(score, eligible, min_names=100)
        fwd = daily_winsorize(_fwd_returns(C, h))
        spread = (fwd.where(q1).mean(axis=1) - fwd.where(q5).mean(axis=1)).dropna()
        spread = spread[(spread.index >= pd.Timestamp(start)) & (spread.index <= pd.Timestamp(end))]
        daily_series[label] = spread

    all_series = {**{f'intraday_{k}': v for k, v in intraday_spread_series.items()}, **daily_series}
    df = pd.concat(all_series, axis=1)
    return df.corr()


if __name__ == '__main__':
    out = run('data/alpaca_intraday.db', 'data/sharadar.db')
    print(out['filter_report'])
    for s, d in out['results'].items():
        h5 = d['horizons'][5]
        print(s, h5['spread_ann'], h5['spread_tstat'])
