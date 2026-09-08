"""Accruals anomaly study with strict datekey point-in-time alignment.

Two signal formulas are supported:

  formula='income_cfo' (default, original): (netinc - ncfo) / assets.
  Diagnosed this session as conflating earnings QUALITY with earnings
  LEVEL -- netinc appears directly in the numerator, so the signal has a
  0.77 cross-sectional correlation with ROA. Q1 (the "best" quintile) was
  64% loss-making firms that quarter (mean ROA -8.2% vs +5-6% for Q2-Q5),
  mostly non-cash-impairment-driven. That's why RMW came out strongly
  negative regardless of universe/date fixes: sorting on this signal is
  substantially sorting on "did this company just report a loss."

  formula='sloan' (new): the original Sloan (1996) balance-sheet measure,
  (dCA - dCash) - (dCL - dSTD - dTP) - Dep, scaled by average assets. This
  routes through period-over-period balance-sheet changes instead of
  netinc directly, which is the standard fix for the income/CFO measure's
  earnings-level confound.

Expected exposures: positive RMW/CMA, market near 1, modest positive SMB/HML.
Expected subgroup pattern: strongest alpha in small caps and possible post-2010 decay.
"""
from __future__ import annotations
from dataclasses import dataclass
import sqlite3
import numpy as np
import pandas as pd
from utils.research_utils import (
    factor_regression, load_universe_candidates, load_price_panel, load_fundamentals_panel,
    load_marketcap_panel, panel_universe_as_of, panel_latest_asof, panel_marketcaps_asof,
    panel_month_values, size_tercile_top_returns, size_tercile_breakdown, winsorize_by_group,
    long_short_spread_regression,
)

# Legacy fixed roster -- kept only so callers can still request it explicitly
# via symbols=DOW_ROSTER. No longer the default: using today's Dow-30 list as
# a static universe across 2000-2024 is survivorship/hindsight bias (several
# of these names, e.g. CRM, V, DOW, did not exist as public companies for
# large parts of that period). The default universe is the full point-in-time
# Sharadar tradeable universe -- the S&P 500 was also tried and rejected as a
# default: it's the most efficiently-priced, most heavily-covered slice of
# the market, which is the wrong place to test a capacity-barrier thesis that
# predicts the effect lives where institutional capital structurally can't
# follow.
DOW_ROSTER = [
    "MMM", "GS", "NKE", "AXP", "HD", "PG", "AMGN", "HON", "CRM", "AAPL",
    "INTC", "TRV", "BA", "IBM", "UNH", "CAT", "JNJ", "VZ", "CVX", "JPM",
    "V", "CSCO", "MCD", "KO", "MRK", "WMT", "DOW", "MSFT", "DIS",
]

@dataclass
class StrategyResult:
    monthly_returns: pd.Series; factor_returns: pd.DataFrame; alpha_annual: float; alpha_tstat: float; alpha_pvalue: float; betas: dict; r_squared: float; n_months: int; portfolio_history: pd.DataFrame; quintile_returns: pd.DataFrame; size_terciles: dict; period_results: dict; lookahead_violations: int; dimensions_used: dict; avg_universe_size: float; long_short_alpha: float = float('nan'); long_short_tstat: float = float('nan'); long_short_rsquared: float = float('nan'); long_short_betas: dict = None

_INCOME_CFO_COLS = ['netinc', 'ncfo', 'assets']
_SLOAN_COLS = ['assetsc', 'cashneq', 'liabilitiesc', 'debtc', 'taxliabilities', 'depamor', 'assets', 'assetsavg']
_SLOAN_DELTA_COLS = ['assetsc', 'cashneq', 'liabilitiesc', 'debtc', 'taxliabilities']

def _add_prior_period_columns(panel: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Attach prior_<col> = that column's value from the immediately
    preceding filing of the SAME (ticker, dimension) sequence, sorted by
    calendardate. Needed for the Sloan measure's period-over-period
    balance-sheet deltas; computed once for the whole panel, not per month."""
    panel = panel.sort_values(['ticker', 'dimension', 'calendardate']).reset_index(drop=True)
    g = panel.groupby(['ticker', 'dimension'])
    for c in columns:
        panel[f'prior_{c}'] = g[c].shift(1)
    return panel

def _signal_income_cfo(row) -> float | None:
    if pd.isna(row.netinc) or pd.isna(row.ncfo) or pd.isna(row.assets) or row.assets == 0:
        return None
    return float((row.netinc - row.ncfo) / row.assets)

def _signal_sloan(row) -> float | None:
    core = (row.assetsc, row.cashneq, row.liabilitiesc, row.depamor,
            row.prior_assetsc, row.prior_cashneq, row.prior_liabilitiesc)
    if any(pd.isna(x) for x in core):
        return None
    scale = row.assetsavg if pd.notna(row.assetsavg) and row.assetsavg > 0 else row.assets
    if pd.isna(scale) or scale == 0:
        return None
    d_ca = row.assetsc - row.prior_assetsc
    d_cash = row.cashneq - row.prior_cashneq
    d_cl = row.liabilitiesc - row.prior_liabilitiesc
    # Short-term debt and taxes payable are Sloan's refinement terms; many
    # filings don't break them out separately. Missing -> treat the delta as
    # zero (no refinement) rather than dropping the observation, matching
    # common practice when replicating this measure -- the core CA/Cash/CL
    # terms above are still required non-null.
    d_std = (row.debtc - row.prior_debtc) if pd.notna(row.debtc) and pd.notna(row.prior_debtc) else 0.0
    d_tp = (row.taxliabilities - row.prior_taxliabilities) if pd.notna(row.taxliabilities) and pd.notna(row.prior_taxliabilities) else 0.0
    total_accruals = (d_ca - d_cash) - (d_cl - d_std - d_tp) - row.depamor
    return float(total_accruals / scale)

def run(db_path: str, as_of_start: str, as_of_end: str, symbols=None, winsorize: bool = True, formula: str = 'income_cfo') -> StrategyResult:
    if formula not in ('income_cfo', 'sloan'):
        raise ValueError(f"formula must be 'income_cfo' or 'sloan', got {formula!r}")
    con = sqlite3.connect(db_path)
    months = pd.date_range(as_of_start, as_of_end, freq='ME')

    if symbols is not None:
        universe_tickers = list(symbols); candidates = None
    else:
        candidates = load_universe_candidates(con)
        window_start = pd.Timestamp(as_of_start) - pd.Timedelta(days=400)
        window_end = pd.Timestamp(as_of_end)
        candidates = candidates[(candidates.firstpricedate <= window_end) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= window_start))].reset_index(drop=True)
        universe_tickers = candidates.ticker.tolist()
    load_start = (months.min() - pd.Timedelta(days=400)).strftime('%Y-%m-%d')
    load_end = (months.max() + pd.offsets.MonthEnd(1)).strftime('%Y-%m-%d')
    price_panel = load_price_panel(con, universe_tickers, load_start, load_end)
    fund_cols = _INCOME_CFO_COLS if formula == 'income_cfo' else _SLOAN_COLS
    # calendardate is needed to order each ticker's own filing sequence for
    # the Sloan prior-period deltas; harmless extra column for income_cfo.
    fund_panel = load_fundamentals_panel(con, universe_tickers, load_start, load_end, sorted(set(fund_cols)))
    # calendardate isn't in the numeric column list (load_fundamentals_panel
    # only coerces the requested `columns`), so fetch it as-is; the query
    # above already selects it implicitly via the base fundamentals load --
    # re-derive it cleanly here instead of hacking load_fundamentals_panel's
    # column coercion for one non-numeric field.
    if formula == 'sloan':
        q = ','.join('?' * len(universe_tickers)) if universe_tickers else ''
        cal = pd.read_sql_query(
            f"SELECT ticker,date AS datekey,dimension,calendardate FROM fundamentals WHERE ticker IN ({q}) AND date>=? AND date<=? AND dimension IN ('ART','ARY')",
            con, params=[*universe_tickers, load_start, load_end], parse_dates=['datekey', 'calendardate'],
        ) if universe_tickers else pd.DataFrame(columns=['ticker', 'datekey', 'dimension', 'calendardate'])
        fund_panel = fund_panel.merge(cal, on=['ticker', 'datekey', 'dimension'], how='left')
        fund_panel = _add_prior_period_columns(fund_panel, _SLOAN_DELTA_COLS)
    mc_panel = load_marketcap_panel(con, universe_tickers, as_of_start, load_end) if symbols is None else pd.DataFrame(columns=['ticker', 'date', 'marketcap'])
    con.close()

    rows=[]; history=[]; violations=0; dims={}
    universe_sizes=[]; small_rows=[]; mid_rows=[]; large_rows=[]
    for dt in months:
        syms = list(symbols) if symbols is not None else panel_universe_as_of(price_panel, candidates, dt)
        if not syms: continue
        universe_sizes.append(len(syms))
        latest = panel_latest_asof(fund_panel, dt, dimension_priority=('ART', 'ARY'))
        if latest.empty: continue
        latest = latest[latest.ticker.isin(syms)]
        if latest.empty: continue
        for c in latest.dimension.value_counts().items(): dims[c[0]] = dims.get(c[0], 0) + int(c[1])
        violations += int((latest.datekey > dt).sum())
        sig_fn = _signal_income_cfo if formula == 'income_cfo' else _signal_sloan
        vals = [(r.ticker, s) for r in latest.itertuples() if (s := sig_fn(r)) is not None]
        if len(vals) < 5: continue
        s = pd.DataFrame(vals, columns=['ticker', 'signal']).sort_values('signal').reset_index(drop=True)
        s['q'] = (np.floor(np.arange(len(s)) * 5 / len(s)) + 1).astype(int)
        cur_vals = panel_month_values(price_panel, dt.to_period('M'))
        next_vals = panel_month_values(price_panel, (dt + pd.offsets.MonthEnd(1)).to_period('M'))
        s['a'] = s.ticker.map(cur_vals); s['b'] = s.ticker.map(next_vals)
        s = s.dropna(subset=['a', 'b'])
        if s.empty: continue
        s['return'] = s.b / s.a - 1
        s['weight'] = 1 / s.groupby('q')['ticker'].transform('count')
        # `return` here is the price change from dt to dt+1mo (formation
        # month to realization month) -- it belongs to the REALIZATION
        # month for comparison against calendar-dated factor returns.
        # Labeling it with `dt` (the formation month) instead was a real
        # bug: it shifted the whole portfolio series one month early
        # relative to the Fama-French factors, regressing month t+1's
        # stock returns against month t's market return. Confirmed
        # empirically on net_share_issuance -- shifting the series forward
        # one month took its market correlation from 0.05 to 0.91. This
        # affected every factor-regression output (alpha, t-stat,
        # R-squared, all factor loadings) computed this session; it did
        # NOT affect the quintile spread itself (a same-month comparison).
        realization_month = dt + pd.offsets.MonthEnd(1)
        gdf = s[['ticker', 'q', 'signal', 'return', 'weight']].copy(); gdf['date'] = realization_month
        if winsorize:
            gdf = winsorize_by_group(gdf, 'return', 'date')
        history.extend(gdf.to_dict('records'))
        rows.append(gdf.groupby('q')['return'].mean().rename(realization_month))
        if symbols is None:
            mktcaps = panel_marketcaps_asof(mc_panel, gdf.ticker.tolist(), dt)
            gdf['mktcap'] = gdf.ticker.map(mktcaps)
            tercile_rets = size_tercile_top_returns(gdf, signal_col='signal', return_col='return', mktcap_col='mktcap', ascending=True)
            small_rows.append((realization_month, tercile_rets['Small'])); mid_rows.append((realization_month, tercile_rets['Mid'])); large_rows.append((realization_month, tercile_rets['Large']))
    quint = pd.DataFrame(rows); quint.columns = [f'Q{i}' for i in quint.columns] if len(quint.columns) == 5 else quint.columns
    monthly = quint.get('Q1', pd.Series(dtype=float)); factors, a, t, p, b, r2 = factor_regression(monthly, as_of_start)
    periods={}
    for name, a0, b0 in [('2000-2009', as_of_start, '2009-12-31'), ('2010-2019', '2010-01-01', '2019-12-31'), ('2020-present', '2020-01-01', as_of_end)]:
        x = monthly[(monthly.index >= a0) & (monthly.index <= b0)]
        try: _, aa, tt, *_ = factor_regression(x, a0); periods[name] = {'alpha_annual': aa, 'tstat': tt, 'n_months': len(x)}
        except Exception: periods[name] = {'alpha_annual': np.nan, 'tstat': np.nan, 'n_months': len(x)}
    small_s = pd.Series(dict(small_rows)); mid_s = pd.Series(dict(mid_rows)); large_s = pd.Series(dict(large_rows))
    size_terciles = size_tercile_breakdown({'Small': small_s, 'Mid': mid_s, 'Large': large_s}, as_of_start)
    avg_universe_size = float(np.mean(universe_sizes)) if universe_sizes else 0.0
    # Q1-minus-Q5 long-short spread: the standard academic test for these
    # anomalies. A long-only leg's alpha can just reflect average factor
    # exposure; the spread, which cancels common exposure, is the more
    # decisive test of whether the signal carries genuine information.
    try:
        _, _, ls_alpha, ls_t, _, ls_betas, ls_r2 = long_short_spread_regression(quint, as_of_start)
    except Exception:
        ls_alpha, ls_t, ls_r2, ls_betas = np.nan, np.nan, np.nan, {}
    return StrategyResult(monthly, factors, a, t, p, b, r2, len(monthly), pd.DataFrame(history), quint, size_terciles, periods, violations, dims, avg_universe_size, ls_alpha, ls_t, ls_r2, ls_betas)

if __name__ == '__main__':
    r = run('data/sharadar.db', '2000-01-01', '2024-12-31'); print('ACCRUALS — FULL UNIVERSE'); print(f'Violations found: {r.lookahead_violations}')
