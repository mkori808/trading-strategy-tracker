"""Shared research helpers for clean V2 factor studies."""
from __future__ import annotations
import bisect
import numpy as np
import pandas as pd

def load_pit_universe_snapshots(con):
    """Reconstruct point-in-time S&P 500 membership from the `sp500` table's
    quarterly full snapshots (action in ('historical','current') -- each one
    is a complete constituent list on that date, not an incremental delta).

    This replaces the bug found in all three V2 strategy scripts, which used
    a hardcoded, present-day 29-name Dow roster (including names like CRM,
    V, and DOW that didn't exist as public companies for large parts of the
    2000-2024 backtest) as a static universe for the entire sample. That is
    survivorship/hindsight bias in the universe itself: today's winners,
    projected backwards. Returns a sorted list of (date, frozenset(tickers))
    checkpoints; pick the latest one <= a given rebalance date."""
    snap = pd.read_sql_query(
        "SELECT date, ticker FROM sp500 WHERE action IN ('historical','current') ORDER BY date",
        con, parse_dates=['date'],
    )
    return [(dt, frozenset(g.ticker)) for dt, g in snap.groupby('date')]

def universe_as_of(snapshots, as_of) -> list[str]:
    """Latest PIT snapshot <= as_of (falls back to the earliest snapshot for
    dates before the first one available, since sp500 history starts 2000-03)."""
    if not snapshots:
        return []
    dates = [s[0] for s in snapshots]
    idx = bisect.bisect_right(dates, pd.Timestamp(as_of)) - 1
    idx = max(idx, 0)
    return list(snapshots[idx][1])

_UNIVERSE_CACHE: dict[tuple, list[str]] = {}

def get_full_pit_universe(
    con,
    as_of: str,
    min_price: float = 1.0,
    min_dollar_volume: float = 500_000,
    min_history_days: int = 252,
    exclude_sectors: list[str] | None = None,
) -> list[str]:
    """Point-in-time tradeable universe from the full Sharadar stock database
    (not S&P 500 membership) -- the capacity-barrier thesis needs testing in
    smaller, less-covered names institutional capital can't reach, not in the
    most efficiently-priced large caps. Uses only data available on or before
    as_of. Fails closed: any ambiguity (missing price, short history, no
    volume data) excludes the ticker rather than including it.

    Real schema differs slightly from a naive spec: `exchange` values are
    'NASDAQ'/'NYSE'/'NYSEMKT'/'NYSEARCA'/'BATS' (no spaces), and Sharadar
    combines Finance and Real Estate into one sicsector label, 'Finance
    Insurance And Real Estate' -- not two separate ones.

    Cached per (as_of, filter params): the DB is static within a research
    run and all three strategies (plus quality's internal sensitivity
    sweep) share the same monthly rebalance dates, so recomputing this from
    scratch every call would be pure waste.
    """
    if exclude_sectors is None:
        exclude_sectors = ['Finance Insurance And Real Estate']
    cache_key = (as_of, min_price, min_dollar_volume, min_history_days, tuple(sorted(exclude_sectors)))
    if cache_key in _UNIVERSE_CACHE:
        return _UNIVERSE_CACHE[cache_key]

    as_of_ts = pd.Timestamp(as_of)
    exchanges = ('NASDAQ', 'NYSE', 'NYSEMKT', 'NYSEARCA', 'BATS')
    ph = ','.join('?' * len(exchanges))
    cand = pd.read_sql_query(
        f"""SELECT ticker, sicsector FROM tickers
            WHERE category = 'Domestic Common Stock'
              AND exchange IN ({ph})
              AND firstpricedate IS NOT NULL AND firstpricedate <= ?
              AND (lastpricedate IS NULL OR lastpricedate >= ?)""",
        con, params=[*exchanges, as_of, as_of],
    )
    if exclude_sectors:
        cand = cand[~cand.sicsector.isin(exclude_sectors)]
    if cand.empty:
        _UNIVERSE_CACHE[cache_key] = []
        return []

    candidate_tickers = cand.ticker.tolist()
    # 400 calendar days comfortably contains >=252 trading days if they
    # exist (a full trading year is ~252 of ~365 calendar days), and is far
    # more than the 63-day liquidity window -- bounding the pull keeps this
    # a fast, fixed-size query regardless of how long a ticker has traded.
    lookback_start = (as_of_ts - pd.Timedelta(days=400)).strftime('%Y-%m-%d')
    ph2 = ','.join('?' * len(candidate_tickers))
    px = pd.read_sql_query(
        f"SELECT ticker, date, close, volume FROM stocks WHERE ticker IN ({ph2}) AND date<=? AND date>=?",
        con, params=[*candidate_tickers, as_of, lookback_start], parse_dates=['date'],
    )
    if px.empty:
        _UNIVERSE_CACHE[cache_key] = []
        return []

    px['close'] = pd.to_numeric(px['close'], errors='coerce')
    px['volume'] = pd.to_numeric(px['volume'], errors='coerce')
    out = []
    px = px.sort_values('date')
    for t, g in px.groupby('ticker'):
        if len(g) < min_history_days:
            continue
        last_close = g['close'].iloc[-1]
        if pd.isna(last_close) or last_close < min_price:
            continue
        recent = g.tail(63)
        if len(recent) < 63:
            continue
        dollar_vol = (recent['close'] * recent['volume']).median()
        if pd.isna(dollar_vol) or dollar_vol < min_dollar_volume:
            continue
        out.append(t)

    _UNIVERSE_CACHE[cache_key] = out
    return out

def winsorize_by_group(df: pd.DataFrame, value_col: str, group_col: str, lower: float = 0.01, upper: float = 0.99) -> pd.DataFrame:
    """Clip value_col to the [lower, upper] cross-sectional percentile within
    each group_col bucket (e.g. clip each month's stock returns at its own
    1st/99th percentile) so a handful of extreme individual observations
    can't mechanically dominate an equal-weighted quintile average. Returns
    a copy; does not mutate df."""
    df = df.copy()
    bounds = df.groupby(group_col)[value_col].transform(lambda s: s.clip(s.quantile(lower), s.quantile(upper)))
    df[value_col] = bounds
    return df

def get_market_caps(con, tickers: list[str], as_of: str, lookback_days: int = 45) -> dict[str, float]:
    """Latest available marketcap (from the `daily` table) per ticker on or
    before as_of, within a bounded lookback window. Missing -> excluded from
    the returned dict (fail closed), not imputed."""
    if not tickers:
        return {}
    q = ','.join('?' * len(tickers))
    lookback_start = (pd.Timestamp(as_of) - pd.Timedelta(days=lookback_days)).strftime('%Y-%m-%d')
    mc = pd.read_sql_query(
        f"SELECT ticker,date,marketcap FROM daily WHERE ticker IN ({q}) AND date<=? AND date>=?",
        con, params=[*tickers, as_of, lookback_start], parse_dates=['date'],
    )
    if mc.empty:
        return {}
    mc['marketcap'] = pd.to_numeric(mc['marketcap'], errors='coerce')
    mc = mc.dropna(subset=['marketcap']).sort_values('date')
    latest = mc.groupby('ticker').tail(1)
    return dict(zip(latest.ticker, latest.marketcap))

def load_universe_candidates(con, exclude_sectors: list[str] | None = None, exclude_fama_sectors: list[str] | None = None) -> pd.DataFrame:
    """One-time load of the static ticker metadata needed for universe
    eligibility (category/exchange/listing dates/sector). ~20k rows -- loads
    in well under a second, unlike the per-month version's repeated queries.

    Two independent sector fields exist on `tickers`: `sicsector` (Sharadar's
    broad SIC-derived grouping, where Sharadar merges Finance and Real Estate
    into one label and has no separate Healthcare bucket -- pharma/biotech
    fall under 'Manufacturing' or 'Services' there) and `sector` (a Fama-
    style classification that does have a clean 'Healthcare' value). Use
    `exclude_sectors` to filter on `sicsector` (existing behavior, default
    excludes Finance/Real Estate) and `exclude_fama_sectors` to filter on
    `sector` (e.g. ['Healthcare']) when a SIC-code-level exclusion doesn't
    exist for the desired sector."""
    if exclude_sectors is None:
        exclude_sectors = ['Finance Insurance And Real Estate']
    exchanges = ('NASDAQ', 'NYSE', 'NYSEMKT', 'NYSEARCA', 'BATS')
    ph = ','.join('?' * len(exchanges))
    cand = pd.read_sql_query(
        f"""SELECT ticker, sicsector, sector, firstpricedate, lastpricedate FROM tickers
            WHERE category = 'Domestic Common Stock' AND exchange IN ({ph})""",
        con, params=list(exchanges), parse_dates=['firstpricedate', 'lastpricedate'],
    )
    if exclude_sectors:
        cand = cand[~cand.sicsector.isin(exclude_sectors)]
    if exclude_fama_sectors:
        cand = cand[~cand.sector.isin(exclude_fama_sectors)]
    return cand.reset_index(drop=True)

def load_price_panel(con, tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """One bulk load of daily prices for `tickers` over [start,end], instead
    of one query per rebalance month. Adds a coerced `value` column
    (closeadj falling back to closeunadj/close) and a `month` Period column
    so downstream per-month filtering is pure in-memory pandas."""
    if not tickers:
        return pd.DataFrame(columns=['ticker', 'date', 'close', 'closeadj', 'closeunadj', 'volume', 'value', 'month'])
    q = ','.join('?' * len(tickers))
    # At full universe scope this is 20M+ rows (all candidate tickers, most
    # of an entire ~25yr price history). pandas' default read_sql_query
    # builds one big object-dtype intermediate array before casting to typed
    # columns -- that intermediate allocation itself can exceed available
    # RAM even when the final typed DataFrame would comfortably fit.
    # chunksize reads and types each piece incrementally instead.
    chunks = pd.read_sql_query(
        f"SELECT ticker,date,close,closeadj,closeunadj,volume FROM stocks WHERE ticker IN ({q}) AND date>=? AND date<=?",
        con, params=[*tickers, start, end], parse_dates=['date'], chunksize=500_000,
    )
    typed_chunks = []
    for chunk in chunks:
        for c in ('close', 'closeadj', 'closeunadj', 'volume'):
            chunk[c] = pd.to_numeric(chunk[c], errors='coerce')
        typed_chunks.append(chunk)
    px = pd.concat(typed_chunks, ignore_index=True) if typed_chunks else pd.DataFrame(columns=['ticker', 'date', 'close', 'closeadj', 'closeunadj', 'volume'])
    px['value'] = px['closeadj'].fillna(px['closeunadj']).fillna(px['close'])
    px['month'] = px['date'].dt.to_period('M')
    return px.sort_values(['ticker', 'date']).reset_index(drop=True)

def load_fundamentals_panel(con, tickers: list[str], start: str, end: str, columns: list[str], dimensions=('ART', 'ARY')) -> pd.DataFrame:
    """One bulk load of fundamentals for `tickers` over [start,end], instead
    of one query per rebalance month re-scanning that ticker's whole history."""
    if not tickers:
        return pd.DataFrame(columns=['ticker', 'datekey', 'dimension', *columns])
    q = ','.join('?' * len(tickers))
    dq = ','.join('?' * len(dimensions))
    cols = ','.join(columns)
    f = pd.read_sql_query(
        f"SELECT ticker,date AS datekey,dimension,{cols} FROM fundamentals WHERE ticker IN ({q}) AND date>=? AND date<=? AND dimension IN ({dq})",
        con, params=[*tickers, start, end, *dimensions], parse_dates=['datekey'],
    )
    f[list(columns)] = f[list(columns)].apply(pd.to_numeric, errors='coerce')
    return f.sort_values(['ticker', 'datekey']).reset_index(drop=True)

def load_marketcap_panel(con, tickers: list[str], start: str, end: str) -> pd.DataFrame:
    """One bulk load of daily marketcap for `tickers` over [start,end]."""
    if not tickers:
        return pd.DataFrame(columns=['ticker', 'date', 'marketcap'])
    q = ','.join('?' * len(tickers))
    mc = pd.read_sql_query(
        f"SELECT ticker,date,marketcap FROM daily WHERE ticker IN ({q}) AND date>=? AND date<=?",
        con, params=[*tickers, start, end], parse_dates=['date'],
    )
    mc['marketcap'] = pd.to_numeric(mc['marketcap'], errors='coerce')
    return mc.sort_values(['ticker', 'date']).reset_index(drop=True)

def panel_universe_as_of(price_panel: pd.DataFrame, candidates: pd.DataFrame, as_of, min_price: float = 1.0, min_dollar_volume: float = 500_000, min_history_days: int = 252) -> list[str]:
    """In-memory equivalent of get_full_pit_universe's steps 2-5 (listing
    status was already applied once in load_universe_candidates via sector/
    category/exchange -- only the as_of-dependent listing-date and price/
    liquidity/history filters happen here, per month, against the preloaded
    panel). Same eligibility semantics, no per-call SQL."""
    as_of_ts = pd.Timestamp(as_of)
    live = candidates[(candidates.firstpricedate <= as_of_ts) & (candidates.lastpricedate.isna() | (candidates.lastpricedate >= as_of_ts))]
    if live.empty:
        return []
    cand_tickers = set(live.ticker)
    lookback_start = as_of_ts - pd.Timedelta(days=400)
    w = price_panel[(price_panel.date <= as_of_ts) & (price_panel.date >= lookback_start) & (price_panel.ticker.isin(cand_tickers))]
    if w.empty:
        return []
    counts = w.groupby('ticker').size()
    last_close = w.groupby('ticker')['close'].last()
    recent63 = w.groupby('ticker').tail(63)
    dollar_vol = (recent63.close * recent63.volume).groupby(recent63.ticker).median()
    ok = counts.index[
        (counts >= min_history_days)
        & (last_close.reindex(counts.index) >= min_price)
        & (dollar_vol.reindex(counts.index).fillna(-1) >= min_dollar_volume)
        & (recent63.groupby('ticker').size().reindex(counts.index).fillna(0) >= 63)
    ]
    return list(ok)

def panel_latest_asof(panel: pd.DataFrame, as_of, dimension_priority=('ART', 'ARY')) -> pd.DataFrame:
    """Vectorized 'latest row per ticker with datekey<=as_of' lookup over an
    already (ticker,datekey)-sorted panel -- replaces the per-ticker Python
    loop over a per-month SQL fetch. Prefers the first dimension in
    dimension_priority that has data for a given ticker (matching each
    strategy's original 'ART if available else ARY' rule), computed for
    every ticker in one vectorized pass instead of a per-ticker branch."""
    sub = panel[panel.datekey <= pd.Timestamp(as_of)]
    if sub.empty:
        return sub
    parts = []
    seen: set = set()
    for dim in dimension_priority:
        d = sub[sub.dimension == dim]
        if d.empty:
            continue
        last = d.groupby('ticker').tail(1)
        last = last[~last.ticker.isin(seen)]
        seen.update(last.ticker)
        parts.append(last)
    return pd.concat(parts, ignore_index=True) if parts else sub.iloc[0:0]

def panel_marketcaps_asof(mc_panel: pd.DataFrame, tickers, as_of, lookback_days: int = 45) -> dict[str, float]:
    as_of_ts = pd.Timestamp(as_of)
    lookback_start = as_of_ts - pd.Timedelta(days=lookback_days)
    w = mc_panel[(mc_panel.date <= as_of_ts) & (mc_panel.date >= lookback_start) & (mc_panel.ticker.isin(set(tickers)))]
    if w.empty:
        return {}
    last = w.dropna(subset=['marketcap']).groupby('ticker').tail(1)
    return dict(zip(last.ticker, last.marketcap))

def panel_month_values(price_panel: pd.DataFrame, period) -> pd.Series:
    """Last `value` per ticker within a given calendar-month Period, from
    the preloaded price panel -- vectorized month-end price lookup."""
    w = price_panel[price_panel.month == period]
    if w.empty:
        return pd.Series(dtype=float)
    return w.groupby('ticker')['value'].last()

def size_tercile_top_returns(df: pd.DataFrame, signal_col: str = 'signal', return_col: str = 'return', mktcap_col: str = 'mktcap', ascending: bool = True) -> dict[str, float]:
    """Split df (one rebalance month's stocks, with signal/return/mktcap
    columns already populated) into 3 equal-count size terciles by mktcap,
    then within each tercile take the top signal quintile (ascending=True:
    lowest signal is best, e.g. accruals/net-share-issuance; ascending=False:
    highest is best, e.g. quality) and return its equal-weighted mean return
    per tercile. This tests the capacity-barrier prediction directly: is the
    effect concentrated in Small, where institutional capital can't crowd
    it away, or is it uniform/inverted across size?"""
    d = df.dropna(subset=[mktcap_col, signal_col, return_col])
    if len(d) < 15:
        return {'Small': np.nan, 'Mid': np.nan, 'Large': np.nan}
    d = d.sort_values(mktcap_col).reset_index(drop=True)
    d['size_tercile'] = pd.qcut(d.index, 3, labels=['Small', 'Mid', 'Large'])
    out = {}
    for name in ['Small', 'Mid', 'Large']:
        g = d[d.size_tercile == name]
        if g.empty:
            out[name] = np.nan
            continue
        gs = g.sort_values(signal_col, ascending=ascending)
        top_n = max(1, len(gs) // 5)
        out[name] = gs.head(top_n)[return_col].mean()
    return out

def size_controlled_quintile_spread(df: pd.DataFrame, signal_col: str = 'signal', return_col: str = 'return', mktcap_col: str = 'mktcap', ascending: bool = True, min_per_quintile: int = 10) -> dict[str, dict]:
    """Within each of 3 equal-count size terciles (by mktcap), independently
    re-rank by signal and split into 5 quintiles, returning that tercile's
    own Q1 and Q5 mean returns plus its stock count. This is the size-
    controlled test: the raw cross-sectional Q1-Q5 spread can partly just
    be a size tilt (both net_share_issuance and quality's overall spreads
    carried material SMB exposure, -0.47 and -0.62 respectively) --
    re-ranking *within* a fixed size bracket removes that channel and asks
    whether the signal still discriminates once size is held constant.

    Requires >= min_per_quintile*5 stocks in a tercile (not just
    min_per_quintile total) so every quintile, not just the tercile as a
    whole, clears the minimum count -- a tercile with only 12 names split
    5 ways would give quintiles of 2-3 stocks each, not a valid portfolio."""
    d = df.dropna(subset=[mktcap_col, signal_col, return_col])
    empty = {'q1': np.nan, 'q5': np.nan, 'n': 0}
    if len(d) < min_per_quintile * 5 * 3:
        return {'Small': dict(empty), 'Mid': dict(empty), 'Large': dict(empty)}
    d = d.sort_values(mktcap_col).reset_index(drop=True)
    d['size_tercile'] = pd.qcut(d.index, 3, labels=['Small', 'Mid', 'Large'])
    out = {}
    for name in ['Small', 'Mid', 'Large']:
        g = d[d.size_tercile == name].sort_values(signal_col, ascending=ascending).reset_index(drop=True)
        if len(g) < min_per_quintile * 5:
            out[name] = dict(empty); out[name]['n'] = len(g)
            continue
        g['q'] = (np.floor(np.arange(len(g)) * 5 / len(g)) + 1).astype(int)
        out[name] = {'q1': g[g.q == 1][return_col].mean(), 'q5': g[g.q == 5][return_col].mean(), 'n': len(g)}
    return out

def within_size_analysis(q1_by_tercile: dict[str, pd.Series], q5_by_tercile: dict[str, pd.Series], n_by_tercile: dict[str, pd.Series], start: str) -> dict:
    """For each size tercile, regress (a) the tercile's own Q1 long-only
    leg and (b) its Q1-Q5 spread against the 5 factors. This is the
    decisive version of the size question: not 'is the top bucket's
    return higher in small caps' but 'does the signal still separate
    winners from losers once size is held fixed, and is any of that
    long-only-implementable.'

    Sanity flags (not hard assertions -- these are per-tercile slices,
    smaller and noisier than the full-universe regression, so they're
    reported rather than allowed to crash the whole analysis):
      long-only sane:  0.5 < market beta < 1.5, R-squared > 0.4
      long-short sane: |market beta| < 0.5, |alpha| < 30%/yr
    """
    out = {}
    for name in ['Small', 'Mid', 'Large']:
        q1 = q1_by_tercile.get(name, pd.Series(dtype=float)).dropna()
        q5 = q5_by_tercile.get(name, pd.Series(dtype=float)).dropna()
        n_series = n_by_tercile.get(name, pd.Series(dtype=float))
        avg_n = float(n_series[n_series > 0].mean()) if (n_series > 0).any() else float('nan')
        months_with_data = int((n_series > 0).sum())
        months_total = len(n_series)

        try:
            _, lo_alpha, lo_t, _, lo_betas, lo_r2 = factor_regression(q1, start)
            lo_beta = lo_betas.get('Mkt-RF', float('nan'))
            lo_sane = bool(pd.notna(lo_beta) and 0.5 < lo_beta < 1.5 and pd.notna(lo_r2) and lo_r2 > 0.4)
        except Exception:
            lo_alpha, lo_t, lo_r2, lo_betas, lo_sane = np.nan, np.nan, np.nan, {}, False
        lo_raw = (1 + q1.mean()) ** 12 - 1 if len(q1) else float('nan')

        spread = (q1 - q5).dropna()
        try:
            _, _, ls_alpha, ls_t, _, ls_betas, ls_r2 = long_short_spread_regression(pd.DataFrame({'Q1': q1, 'Q5': q5}), start)
            ls_beta = ls_betas.get('Mkt-RF', float('nan'))
            ls_sane = bool(pd.notna(ls_beta) and abs(ls_beta) < 0.5 and pd.notna(ls_alpha) and abs(ls_alpha) < 0.30)
        except Exception:
            ls_alpha, ls_t, ls_r2, ls_betas, ls_sane = np.nan, np.nan, np.nan, {}, False
        ls_raw = (1 + spread.mean()) ** 12 - 1 if len(spread) else float('nan')

        out[name] = {
            'avg_n': avg_n, 'months_with_data': months_with_data, 'months_total': months_total,
            'q1_raw': lo_raw, 'q1_alpha': lo_alpha, 'q1_tstat': lo_t, 'q1_r2': lo_r2, 'q1_sane': lo_sane,
            'spread_raw': ls_raw, 'spread_alpha': ls_alpha, 'spread_tstat': ls_t, 'spread_r2': ls_r2,
            'spread_betas': ls_betas, 'spread_sane': ls_sane,
        }
    return out

def format_within_size_report(name: str, expectations: str, within_size: dict) -> str:
    """Format a within_size_analysis() result in the exact layout used for
    the small/mid/large capacity-controlled diagnostic."""
    def pct(x): return f"{x:.1%}" if pd.notna(x) else "N/A"
    def num(x): return f"{x:.2f}" if pd.notna(x) else "N/A"
    L = [f"{name} -- Within-Size Analysis", '=' * 60, "", "Preregistered expectations:", expectations, ""]
    for tercile in ['Small', 'Mid', 'Large']:
        r = within_size[tercile]
        L.append(f"{tercile.upper()} CAP TERCILE")
        L.append('-' * 45)
        L.append(f"Avg securities/month:    {r['avg_n']:.0f} (Q1: ~{r['avg_n']/5:.0f})" if pd.notna(r['avg_n']) else "Avg securities/month:    N/A")
        L.append(f"Months with data:        {r['months_with_data']} / {r['months_total']}" + ("  [FLAG: <60 months]" if r['months_with_data'] < 60 else ""))
        L.append("")
        L.append("Q1 long-only:")
        L.append(f"  Raw return:            {pct(r['q1_raw'])}/yr")
        L.append(f"  Alpha:                 {pct(r['q1_alpha'])}/yr")
        L.append(f"  t-stat:                {num(r['q1_tstat'])}")
        L.append(f"  R-squared:             {num(r['q1_r2'])}" + ("" if r['q1_sane'] else "  [FLAG: fails sanity check]"))
        L.append("")
        L.append("Q1-Q5 spread:")
        L.append(f"  Raw spread:            {pct(r['spread_raw'])}/yr")
        L.append(f"  Alpha:                 {pct(r['spread_alpha'])}/yr")
        L.append(f"  t-stat:                {num(r['spread_tstat'])}")
        L.append(f"  R-squared:             {num(r['spread_r2'])}" + ("" if r['spread_sane'] else "  [FLAG: fails sanity check]"))
        b = r['spread_betas'] or {}
        L.append(f"  RMW loading:           {num(b.get('RMW', float('nan')))}")
        L.append(f"  SMB loading:           {num(b.get('SMB', float('nan')))}")
        L.append("")
    small_a, mid_a, large_a = within_size['Small']['spread_alpha'], within_size['Mid']['spread_alpha'], within_size['Large']['spread_alpha']
    small_lo_t = within_size['Small']['q1_tstat']
    L.append("SUMMARY")
    L.append('-' * 45)
    if any(pd.isna(x) for x in (small_a, mid_a, large_a)):
        monotonic_str = "N/A (missing data)"
    else:
        monotonic_str = 'YES' if small_a > mid_a > large_a else 'NO'
    L.append(f"Small alpha > Mid alpha > Large alpha: {monotonic_str}")
    lo_sig_str = 'N/A' if pd.isna(small_lo_t) else ('YES' if small_lo_t > 2.0 else 'NO')
    L.append(f"Long-only Q1 alpha significant in small caps: {lo_sig_str}")
    capacity_str = 'N/A' if pd.isna(small_a) else ('YES' if (small_a > 0.02 and within_size['Small']['spread_tstat'] > 2.0) else 'NO')
    L.append(f"Capacity story supported: {capacity_str}")
    return '\n'.join(L)

def size_tercile_breakdown(monthly_by_tercile: dict[str, pd.Series], start: str) -> dict:
    """Run the factor regression separately on each size tercile's monthly
    return series and report whether the pattern matches the capacity-
    barrier hypothesis (alpha strongest in Small, weakest in Large)."""
    out = {}
    for name in ['Small', 'Mid', 'Large']:
        s = monthly_by_tercile.get(name, pd.Series(dtype=float)).dropna()
        try:
            _, aa, tt, *_ = factor_regression(s, start)
        except Exception:
            aa, tt = np.nan, np.nan
        out[name] = {'alpha_annual': aa, 'tstat': tt, 'n_months': len(s)}
    small, mid, large = out['Small']['alpha_annual'], out['Mid']['alpha_annual'], out['Large']['alpha_annual']
    if any(pd.isna(x) for x in (small, mid, large)):
        out['pattern_matches_hypothesis'] = None
    else:
        out['pattern_matches_hypothesis'] = bool(small > mid > large)
    return out

def format_report(name: str, monthly_returns: pd.Series, quintile_returns: pd.DataFrame, betas: dict, alpha_annual: float, alpha_tstat: float, r_squared: float, factor_returns: pd.DataFrame, portfolio_history: pd.DataFrame, size_terciles: dict, avg_universe_size: float, winsorize_applied: bool, long_short_alpha: float = float('nan'), long_short_tstat: float = float('nan'), long_short_rsquared: float = float('nan'), long_short_betas: dict = None) -> str:
    monthly_returns = monthly_returns.dropna()
    raw_q1_annual = (1 + monthly_returns.mean()) ** 12 - 1 if len(monthly_returns) else float('nan')
    ew_monthly = portfolio_history.groupby('date')['return'].mean() if len(portfolio_history) else pd.Series(dtype=float)
    ew_annual = (1 + ew_monthly.mean()) ** 12 - 1 if len(ew_monthly) else float('nan')
    aligned = factor_returns.reindex(monthly_returns.index).mean() if len(factor_returns) else pd.Series(dtype=float)
    factor_contrib = {k: betas.get(k, 0.0) * aligned.get(k, 0.0) * 12 for k in ['Mkt-RF', 'SMB', 'HML', 'RMW', 'CMA']}
    factor_explained = sum(factor_contrib.values()) if len(aligned) else float('nan')
    quint_annual = {}
    for q in ['Q1', 'Q2', 'Q3', 'Q4', 'Q5']:
        s = quintile_returns[q].dropna() if q in quintile_returns.columns else pd.Series(dtype=float)
        quint_annual[q] = (1 + s.mean()) ** 12 - 1 if len(s) else float('nan')
    monotonic = bool(quint_annual['Q1'] > quint_annual['Q5']) if not (pd.isna(quint_annual['Q1']) or pd.isna(quint_annual['Q5'])) else None
    max_stock_month = portfolio_history.groupby(['date', 'ticker'])['return'].max().max() if len(portfolio_history) else float('nan')

    def pct(x): return f"{x:.1%}" if pd.notna(x) else "N/A"
    def yn(x): return 'YES' if x is True else ('NO' if x is False else 'N/A')

    L = []
    L.append(f"{name} -- Full Sharadar universe")
    L.append('-' * 60)
    L.append(f"Period:           {monthly_returns.index.min():%Y-%m} to {monthly_returns.index.max():%Y-%m} ({len(monthly_returns)} months)" if len(monthly_returns) else "Period:           N/A (0 months)")
    L.append(f"Universe (avg):   {avg_universe_size:.0f} securities/month")
    L.append(f"Outlier check:    max single-stock monthly return {pct(max_stock_month)}")
    L.append(f"                  winsorization applied: {'YES' if winsorize_applied else 'NO'}")
    L.append("")
    L.append(f"Raw Q1 return:    {pct(raw_q1_annual)}/yr")
    L.append(f"Benchmark (EW):   {pct(ew_annual)}/yr")
    L.append("")
    L.append("Factor decomposition:")
    L.append(f"  Raw annual return:     {pct(raw_q1_annual)}")
    L.append(f"  Factor-explained:      {pct(factor_explained)}")
    for k, label in [('Mkt-RF', 'MKT'), ('SMB', 'SMB'), ('HML', 'HML'), ('RMW', 'RMW'), ('CMA', 'CMA')]:
        L.append(f"    {label}:                 {pct(factor_contrib.get(k, float('nan')))}")
    L.append(f"  Residual alpha:        {pct(alpha_annual)}/yr")
    L.append(f"  Alpha t-stat:          {alpha_tstat:.2f}" if pd.notna(alpha_tstat) else "  Alpha t-stat:          N/A")
    L.append(f"  R-squared:             {r_squared:.2f}" if pd.notna(r_squared) else "  R-squared:             N/A")
    L.append("")
    L.append("Quintile returns (annualized):")
    for q in ['Q1', 'Q2', 'Q3', 'Q4', 'Q5']:
        L.append(f"  {q}:   {pct(quint_annual[q])}")
    L.append(f"Monotonic Q1>Q5: {yn(monotonic)}")
    L.append("")
    L.append("SIZE TERCILE BREAKDOWN")
    L.append('-' * 60)
    for k in ['Small', 'Mid', 'Large']:
        t = size_terciles.get(k, {}) if size_terciles else {}
        L.append(f"{k+':':8s}alpha {pct(t.get('alpha_annual', float('nan')))}/yr  t={t.get('tstat', float('nan')):.2f}" if size_terciles and pd.notna(t.get('tstat')) else f"{k+':':8s}alpha N/A  t=N/A")
    pm = size_terciles.get('pattern_matches_hypothesis') if size_terciles else None
    L.append(f"Pattern matches hypothesis (strongest small): {yn(pm)}")
    L.append("")
    L.append("LONG-SHORT SPREAD (Q1 minus Q5)")
    L.append('-' * 60)
    L.append(f"Alpha:      {pct(long_short_alpha)}/yr")
    L.append(f"t-stat:     {long_short_tstat:.2f}" if pd.notna(long_short_tstat) else "t-stat:     N/A")
    L.append(f"R-squared:  {long_short_rsquared:.2f}" if pd.notna(long_short_rsquared) else "R-squared:  N/A")
    if long_short_betas:
        for k, label in [('Mkt-RF', 'MKT'), ('SMB', 'SMB'), ('HML', 'HML'), ('RMW', 'RMW'), ('CMA', 'CMA')]:
            v = long_short_betas.get(k)
            L.append(f"  {label} beta: {v:.2f}" if v is not None and pd.notna(v) else f"  {label} beta: N/A")
    return '\n'.join(L)

def validate_factor_alignment(portfolio_returns: pd.Series, factors: pd.DataFrame) -> None:
    """Sanity check that portfolio and factor return series are on the same
    monthly index before trusting an OLS run on them -- misaligned dates or
    silent NaNs would make the regression's t-stat/R-squared meaningless
    regardless of what number comes out."""
    overlap = portfolio_returns.index.intersection(factors.index)
    coverage = len(overlap) / len(portfolio_returns) if len(portfolio_returns) else float('nan')
    print(f"Portfolio months: {len(portfolio_returns)}")
    print(f"Factor months: {len(factors)}")
    print(f"Overlapping months: {len(overlap)}")
    print(f"Coverage: {coverage:.1%}")
    print(f"NaN in portfolio: {portfolio_returns.isna().sum()}")
    print(f"NaN in factors:\n{factors.isna().sum()}")
    print(f"Portfolio: {portfolio_returns.index[0]} to {portfolio_returns.index[-1]}")
    print(f"Factors:   {factors.index[0]} to {factors.index[-1]}")

def assert_regression_sanity(portfolio_returns: pd.Series, factors: pd.DataFrame, model) -> None:
    """Fail loudly if a factor regression's outputs are implausible for a
    diversified long-only US equity portfolio. This is exactly the check
    that would have caught, on the very first run, a real bug found this
    session: a one-month date-labeling error that regressed month t+1's
    realized returns against month t's factor returns, producing a market
    beta of ~0.06 and R-squared of 0.02 instead of the ~1.0 beta / 0.85-0.95
    R-squared a real long-only book should show. An implausible beta or R-
    squared here is always worth stopping for -- it means either the
    regression is misaligned/broken, or the portfolio has a genuinely
    extraordinary property that needs to be understood before trusting any
    alpha estimate built on top of it."""
    mkt_beta = model.params['Mkt-RF']
    r_squared = model.rsquared
    assert 0.5 < mkt_beta < 1.5, f"Market beta {mkt_beta:.2f} implausible for long-only equity"
    assert r_squared > 0.3, f"R-squared {r_squared:.2f} implausible for long-only equity"
    mkt_corr = portfolio_returns.corr(factors['Mkt-RF'])
    assert mkt_corr > 0.5, f"Portfolio-market correlation {mkt_corr:.2f} implausible"

def long_short_spread_regression(quintile_returns: pd.DataFrame, start: str):
    """Q1-minus-Q5 zero-cost spread, factor-regressed the same way as the
    long-only Q1 leg. This is the standard academic test for these
    anomalies -- a long-only leg's alpha can reflect nothing more than
    average factor exposure (as net_share_issuance turned out to: 0.6%/yr,
    t=0.67 once the regression was correctly aligned). If a signal carries
    genuine cross-sectional information, the long-short spread should show
    positive alpha net of factor exposures even when the long leg alone
    doesn't -- and if the spread's alpha is *also* indistinguishable from
    factor exposure, that's the more decisive verdict on whether anything
    is left after controlling for known risk factors."""
    if 'Q1' not in quintile_returns.columns or 'Q5' not in quintile_returns.columns:
        return pd.Series(dtype=float), None, np.nan, np.nan, np.nan, {}, np.nan
    spread = (quintile_returns['Q1'] - quintile_returns['Q5']).dropna()
    factors, alpha_annual, tstat, pvalue, betas, r2 = factor_regression(spread, start, is_long_short=True)
    return spread, factors, alpha_annual, tstat, pvalue, betas, r2

def factor_regression(returns: pd.Series, start: str, is_long_short: bool = False):
    import pandas_datareader.data as web
    import statsmodels.api as sm
    factors = web.DataReader('F-F_Research_Data_5_Factors_2x3', 'famafrench', start=start)[0] / 100
    factors.index = pd.to_datetime(factors.index.to_timestamp(how='end')).to_period('M').to_timestamp('M')
    aligned = pd.concat([returns.rename('portfolio'), factors], axis=1, join='inner').dropna()
    if len(aligned) < 3:
        return factors, np.nan, np.nan, np.nan, {}, np.nan
    # A zero-cost long-short spread ties up no net capital, so there's no
    # risk-free hurdle to net out the way there is for a long-only holding
    # -- regress the raw spread return, not spread-minus-RF.
    dependent = aligned.portfolio if is_long_short else aligned.portfolio - aligned.RF
    model = sm.OLS(dependent, sm.add_constant(aligned[['Mkt-RF','SMB','HML','RMW','CMA']])).fit()
    # Runs on every long-only call (is_long_short=False). Sub-analyses
    # (period splits, sensitivity sweeps, size terciles) already wrap their
    # factor_regression calls in try/except Exception and degrade to NaN on
    # failure -- appropriate, since a smaller/noisier slice can legitimately
    # have an odd beta
    # without that meaning anything is broken. The *primary* Q1 regression
    # call in each strategy's run() is NOT wrapped, so a failure there
    # correctly propagates and stops the run -- exactly where an
    # implausible result is most likely to mean something is actually wrong.
    # A long-short spread is expected to have near-zero market beta (the
    # long and short legs' market exposure should largely cancel), so the
    # long-only sanity assertion doesn't apply -- skip it there.
    if not is_long_short:
        assert_regression_sanity(aligned.portfolio, aligned, model)
    return factors, (1 + float(model.params['const'])) ** 12 - 1, float(model.tvalues['const']), float(model.pvalues['const']), model.params.drop('const').to_dict(), float(model.rsquared)
