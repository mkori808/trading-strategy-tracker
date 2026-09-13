"""PEAD sub-hypothesis B (pead_B_v1): multi-day post-earnings-announcement
drift measured from the announcement day's CLOSE (not its open), so there is
zero overlap with the already-falsified same-day hypotheses A/A' in
strategies/earnings_continuation.py.

Reuses that module's universe construction, event anchoring, and gap
definition verbatim. Adds: a 10-day forward-return horizon (A/A' only had
5-day and 20-day), the sequential-earnings exclusion, the 2020+ regime
check, and leave-one-crisis-out robustness -- none of which earnings_continuation.py
computes.

See research/preregistrations/pead_B_v1.json for the locked spec.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from strategies.earnings_continuation import (
    Settings as ECSettings,
    _bucket,
    horizon_spread_series,
    load_universe_and_events,
    winsorize,
)
from strategies.ibs_conditioning import (
    _candidate_metadata,
    _factor_regression,
    _hac_mean_test,
)

THRESHOLDS = (0.01, 0.02, 0.03)
PRIMARY_THRESHOLD = 0.02
HORIZONS = ("H1", "H2", "H3")  # 5, 10, 20 trading days, all from close[t]
HORIZON_DAYS = {"H1": 5, "H2": 10, "H3": 20}
MIN_SUBGROUP_EVENTS = 20


def _annualized_mean_for_horizon(series: pd.Series, horizon_trading_days: int) -> float:
    """Correctly annualize a MULTI-DAY cumulative return series.

    ibs_conditioning._annualized_mean() does (1+mean)**252-1, which is only
    valid when each observation is itself a 1-trading-day return. Every
    series here is a 5/10/20-trading-day CUMULATIVE return per event, so the
    correct compounding exponent is 252/horizon_trading_days (the number of
    non-overlapping horizon-length windows per year), not 252. Using the
    wrong exponent overstates H2 (10d) by ~10x and H3 (20d) by ~20x -- this
    function exists specifically to avoid reproducing that error for PEAD.
    """
    value = float(series.dropna().mean()) if series.notna().any() else np.nan
    if not np.isfinite(value) or value <= -1:
        return np.nan
    periods_per_year = 252.0 / horizon_trading_days
    return float((1 + value) ** periods_per_year - 1)

CRISIS_WINDOWS = {
    "dot-com / 2001-2002": ("2001-05-25", "2003-05-09"),
    "GFC / 2008-2009": ("2007-07-13", "2010-04-09"),
    "COVID / 2020": ("2019-12-20", "2020-06-05"),
    "2022 bear": ("2021-11-12", "2023-03-03"),
}


def build_pead_features(
    prices: pd.DataFrame,
    events: pd.DataFrame,
    core_start: pd.Timestamp,
    core_end: pd.Timestamp,
    settings: ECSettings,
) -> pd.DataFrame:
    """Same anchoring/eligibility logic as earnings_continuation.build_features,
    with an added 10-day close-to-close horizon and the sequential-earnings
    exclusion. Re-implemented locally (not monkey-patched) so the extra
    horizon column exists before the eligibility filter is applied."""
    p = prices.sort_values(["ticker", "date"]).copy().reset_index(drop=True)
    if p.empty:
        return p
    g = p.groupby("ticker", sort=False)
    p["history_n"] = g.cumcount() + 1
    p["prev_close"] = g.close.shift(1)
    p["earnings_gap"] = p.open / p.prev_close - 1
    p["dollar_volume"] = p.close * p.volume
    p["adv20_pre"] = (
        g.dollar_volume.shift(1).groupby(p.ticker, sort=False)
        .rolling(20, min_periods=15).median()
        .reset_index(level=0, drop=True).sort_index()
    )
    p["marketcap_pre"] = g.marketcap.shift(1)

    for horizon, n in (("H1", 5), ("H2", 10), ("H3", 20)):
        p[f"close_fwd_{horizon}"] = g.close.shift(-n)
        p[f"return_{horizon}"] = p[f"close_fwd_{horizon}"] / p.close - 1

    if events.empty:
        p["is_anchor_day"] = False
        return p

    anchors: list[dict] = []
    sessions_by_ticker = {t: d.date.reset_index(drop=True) for t, d in p.groupby("ticker", sort=False)}
    for ticker, ev in events.groupby("ticker", sort=False):
        sessions = sessions_by_ticker.get(ticker)
        if sessions is None or sessions.empty:
            continue
        session_array = sessions.to_numpy()
        for datekey in ev.date:
            idx = int(np.searchsorted(session_array, np.datetime64(datekey), side="left"))
            if idx >= len(session_array):
                continue
            anchors.append({"ticker": ticker, "date": pd.Timestamp(session_array[idx])})

    anchor_frame = pd.DataFrame(anchors).drop_duplicates() if anchors else pd.DataFrame(columns=["ticker", "date"])
    anchor_index = pd.MultiIndex.from_frame(anchor_frame) if len(anchor_frame) else pd.MultiIndex.from_arrays([[], []])
    row_index = pd.MultiIndex.from_frame(p[["ticker", "date"]])
    p["is_anchor_day"] = row_index.isin(anchor_index)

    listing_ok = p.lastpricedate.isna() | (p.lastpricedate >= p.date)
    eligible = (
        p.date.between(core_start, core_end)
        & (p.firstpricedate <= p.date)
        & listing_ok
        & p.history_n.ge(252)
        & p.close.ge(settings.min_price)
        & p.adv20_pre.ge(settings.min_adv)
        & p.is_anchor_day
        & p.earnings_gap.notna()
    )
    out = p[eligible].copy()

    # Sequential-earnings exclusion: drop an event if it is within 5 trading
    # days (by this ticker's own history_n index) of the SAME ticker's
    # immediately prior anchor event in this eligible set. First event per
    # ticker is never excluded (no prior event exists).
    out = out.sort_values(["ticker", "date"])
    prior_history_n = out.groupby("ticker", sort=False).history_n.shift(1)
    gap_trading_days = out.history_n - prior_history_n
    out["sequential_overlap_excluded"] = gap_trading_days.notna() & (gap_trading_days < 5)
    return out[~out["sequential_overlap_excluded"]].copy()


def run_pead_b(db_path: str, start: str, end: str) -> dict:
    settings = ECSettings(start=start, end=end)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    con = sqlite3.connect(db_path)
    metadata = _candidate_metadata(con, start, end)
    all_events: list[pd.DataFrame] = []
    total_raw_events = 0
    try:
        for year in range(start_ts.year, end_ts.year + 1):
            lo = max(start_ts, pd.Timestamp(year=year, month=1, day=1))
            hi = min(end_ts, pd.Timestamp(year=year, month=12, day=31))
            prices, raw_events = load_universe_and_events(con, metadata, lo, hi)
            total_raw_events += len(raw_events)
            feats = build_pead_features(prices, raw_events, lo, hi, settings)
            if not feats.empty:
                all_events.append(feats)
    finally:
        con.close()

    events = pd.concat(all_events, ignore_index=True) if all_events else pd.DataFrame()
    if events.empty:
        raise RuntimeError("No valid PEAD events; check data coverage and eligibility settings")

    n_before_seq_excl = int(events.shape[0])  # already post-exclusion per-year; recompute raw count separately
    for col in ("return_H1", "return_H2", "return_H3", "earnings_gap"):
        events[col] = winsorize(events[col])

    span_years = (end_ts - start_ts).days / 365.25
    coverage = {
        "total_earnings_filings_seen": int(total_raw_events),
        "valid_gap_measurements_post_exclusions": int(len(events)),
        "events_per_year_avg": float(len(events) / span_years),
        "unique_tickers": int(events.ticker.nunique()),
    }
    bucket_primary = _bucket(events.earnings_gap, PRIMARY_THRESHOLD)
    coverage["long_bucket_primary"] = int((bucket_primary == "long").sum())
    coverage["avoid_bucket_primary"] = int((bucket_primary == "short").sum())
    coverage["long_bucket_primary_pct"] = float((bucket_primary == "long").mean())
    coverage["avoid_bucket_primary_pct"] = float((bucket_primary == "short").mean())

    return {"events": events, "coverage": coverage}


def summarize_threshold(events: pd.DataFrame, threshold: float) -> dict:
    d = events.copy()
    d["bucket"] = _bucket(d.earnings_gap, threshold)
    result = {
        "threshold": threshold,
        "n_long_events": int((d.bucket == "long").sum()),
        "n_short_events": int((d.bucket == "short").sum()),
        "horizons": {},
    }
    for horizon in HORIZONS:
        series = horizon_spread_series(d, horizon, threshold)
        if series.empty:
            result["horizons"][horizon] = {"status": "NO_DATA"}
            continue
        long_test = _hac_mean_test(series.long_return)
        short_test = _hac_mean_test(series.short_return)
        spread_test = _hac_mean_test(series.spread)
        days = HORIZON_DAYS[horizon]
        result["horizons"][horizon] = {
            "n_event_days": int(len(series)),
            "long_annualized": _annualized_mean_for_horizon(series.long_return, days),
            "long_tstat": long_test["tstat"], "long_n": long_test["n"],
            "avoid_annualized": _annualized_mean_for_horizon(series.short_return, days),
            "avoid_tstat": short_test["tstat"], "avoid_n": short_test["n"],
            "spread_annualized": _annualized_mean_for_horizon(series.spread, days),
            "spread_tstat": spread_test["tstat"], "spread_n": spread_test["n"],
        }
    return result


def regime_check_2020(events: pd.DataFrame, threshold: float) -> dict:
    windows = {"full_sample": ("2001-01-01", "2024-12-31"),
               "pre_2020": ("2001-01-01", "2019-12-31"),
               "post_2020": ("2020-01-01", "2024-12-31")}
    out = {}
    for horizon in HORIZONS:
        out[horizon] = {}
        for label, (lo, hi) in windows.items():
            subset = events[events.date.between(lo, hi)]
            series = horizon_spread_series(subset, horizon, threshold)
            if series.empty:
                out[horizon][label] = {"status": "NO_DATA"}
                continue
            long_test = _hac_mean_test(series.long_return)
            out[horizon][label] = {
                "n_events": int((_bucket(subset.earnings_gap, threshold) == "long").sum()),
                "annualized": _annualized_mean_for_horizon(series.long_return, HORIZON_DAYS[horizon]),
                "tstat": long_test["tstat"], "n": long_test["n"],
            }
    return out


def leave_one_crisis_out(events: pd.DataFrame, threshold: float, horizon: str = "H2") -> dict:
    days = HORIZON_DAYS[horizon]
    full_series = horizon_spread_series(events, horizon, threshold)
    full_test = _hac_mean_test(full_series.spread)
    out = {
        "full_sample": {
            "n_events": int(len(events)),
            "spread_annualized": _annualized_mean_for_horizon(full_series.spread, days),
            "tstat": full_test["tstat"], "n": full_test["n"],
            "sign_positive": bool(full_test["mean"] > 0) if np.isfinite(full_test["mean"]) else None,
        }
    }
    for label, (lo, hi) in CRISIS_WINDOWS.items():
        mask = events.date.between(lo, hi)
        ex = events[~mask]
        series = horizon_spread_series(ex, horizon, threshold)
        test = _hac_mean_test(series.spread) if not series.empty else {"mean": np.nan, "tstat": np.nan, "n": 0}
        out[f"excluding_{label}"] = {
            "n_events": int(len(ex)),
            "removed_events": int(mask.sum()),
            "spread_annualized": _annualized_mean_for_horizon(series.spread, days) if not series.empty else np.nan,
            "tstat": test["tstat"], "n": test["n"],
            "sign_positive": bool(test["mean"] > 0) if np.isfinite(test.get("mean", np.nan)) else None,
        }
    return out


def factor_regressions(events: pd.DataFrame, threshold: float, factors: pd.DataFrame) -> dict:
    out = {}
    for horizon in ("H1", "H2"):
        series = horizon_spread_series(events, horizon, threshold)
        if series.empty:
            out[horizon] = {"status": "NO_DATA"}
            continue
        series = series.set_index("date")
        out[horizon] = {
            "long_leg": _factor_regression(series.long_return, factors),
            "spread": _factor_regression(series.spread, factors),
        }
    return out


def market_cap_subgroups(events: pd.DataFrame, threshold: float, min_events: int = MIN_SUBGROUP_EVENTS) -> dict:
    valid = events.dropna(subset=["marketcap_pre"])
    if len(valid) < 30:
        return {"status": "INSUFFICIENT_DATA"}
    terciles = valid.marketcap_pre.quantile([1 / 3, 2 / 3]).to_numpy()
    labels = pd.cut(valid.marketcap_pre, [-np.inf, terciles[0], terciles[1], np.inf], labels=["small", "mid", "large"])
    out = {}
    for label in ("small", "mid", "large"):
        subset = valid[labels == label]
        bucket = _bucket(subset.earnings_gap, threshold)
        long_subset = subset[bucket == "long"]
        if len(long_subset) < min_events:
            out[label] = {"status": "INSUFFICIENT_DATA", "n": int(len(long_subset))}
            continue
        h1 = _hac_mean_test(long_subset.return_H1)
        h2 = _hac_mean_test(long_subset.return_H2)
        out[label] = {
            "n": int(len(long_subset)),
            "h1_annualized": _annualized_mean_for_horizon(long_subset.return_H1, HORIZON_DAYS["H1"]), "h1_tstat": h1["tstat"],
            "h2_annualized": _annualized_mean_for_horizon(long_subset.return_H2, HORIZON_DAYS["H2"]), "h2_tstat": h2["tstat"],
        }
    return out
