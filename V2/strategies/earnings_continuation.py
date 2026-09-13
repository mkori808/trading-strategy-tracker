"""Earnings continuation (Sub-hypothesis A) characterization.

Tests whether the direction of the overnight gap on an earnings-announcement
day predicts same-day and multi-day continuation, as opposed to the reversal
seen in non-earnings gaps (Gap Fade). This module only characterizes the
signal against the preregistration in
research/preregistrations/earnings_continuation_A_v1.json. It does not build
a composite or paper track and does not read or modify any paper-trading
state file.

Signal timing
-------------
For each ticker, every SF1 filing date (fundamentals.date, dimension='ARQ')
is mapped to its anchor trading session (that date if it is a session, else
the next session). earnings_gap = open[anchor]/close[anchor-1] - 1 is formed
using information available intraday on the anchor day; H1 additionally
requires an Alpaca intraday open observed at/after 09:30 ET. H2-H4 are
close-to-close returns measured from the anchor day's close, so they do not
double count the H1 move.

Research integrity
-------------------
Development window is the full 2001-01-01 to 2024-12-31 history (the
historical holdout is already consumed; this is a characterization
exercise, not a fresh validation test). Every output records that framing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from strategies.ibs_conditioning import (
    FACTOR_COLUMNS,
    TRADING_DAYS,
    _annualized_mean,
    _cagr,
    _candidate_metadata,
    _factor_regression,
    _git_commit,
    _hac_mean_test,
    _json_safe,
    side_cost_rate,
)

THRESHOLDS = (0.01, 0.02, 0.03)
PRIMARY_THRESHOLD = 0.02
BONFERRONI_T_THRESHOLD = 2.39
HORIZONS = ("H1", "H2", "H3", "H4")


@dataclass(frozen=True)
class Settings:
    start: str = "2001-01-01"
    end: str = "2024-12-31"
    min_price: float = 1.0
    min_adv: float = 500_000.0
    min_names_per_leg: int = 1
    min_subgroup_events: int = 20
    round_trip_bps: float = 15.0


def load_universe_and_events(
    con: sqlite3.Connection,
    metadata: pd.DataFrame,
    core_start: pd.Timestamp,
    core_end: pd.Timestamp,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load one bounded annual window of prices/marketcap plus SF1 datekeys."""
    lo = core_start - pd.Timedelta(days=500)
    hi = core_end + pd.Timedelta(days=40)
    live = metadata[
        (metadata.firstpricedate <= hi)
        & (metadata.lastpricedate.isna() | (metadata.lastpricedate >= lo))
    ]
    tickers = live.ticker.tolist()
    if not tickers:
        return pd.DataFrame(), pd.DataFrame(columns=["ticker", "date"])
    placeholders = ",".join("?" * len(tickers))
    query = f"""SELECT s.ticker,s.date,s.open,s.high,s.low,s.close,s.volume,
                       s.closeunadj,d.marketcap
                FROM stocks s LEFT JOIN daily d
                  ON d.ticker=s.ticker AND d.date=s.date
                WHERE s.ticker IN ({placeholders}) AND s.date>=? AND s.date<=?
                ORDER BY s.ticker,s.date"""
    chunks = pd.read_sql_query(
        query, con,
        params=[*tickers, lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")],
        parse_dates=["date"], chunksize=400_000,
    )
    parts: list[pd.DataFrame] = []
    for chunk in chunks:
        for column in ("open", "high", "low", "close", "volume", "closeunadj", "marketcap"):
            chunk[column] = pd.to_numeric(chunk[column], errors="coerce")
        parts.append(chunk)
    prices = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    if prices.empty:
        return prices, pd.DataFrame(columns=["ticker", "date"])
    prices = prices.merge(live, on="ticker", how="left", validate="many_to_one")

    events = pd.read_sql_query(
        f"""SELECT DISTINCT ticker,date FROM fundamentals
             WHERE ticker IN ({placeholders}) AND dimension='ARQ'
               AND date>=? AND date<=?""",
        con, params=[*tickers, lo.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")],
        parse_dates=["date"],
    )
    return prices, events


def build_features(
    prices: pd.DataFrame,
    events: pd.DataFrame,
    core_start: pd.Timestamp,
    core_end: pd.Timestamp,
    settings: Settings,
) -> pd.DataFrame:
    """Anchor SF1 datekeys to trading sessions and compute forward returns."""
    p = prices.sort_values(["ticker", "date"]).copy().reset_index(drop=True)
    if p.empty:
        return p
    g = p.groupby("ticker", sort=False)
    p["history_n"] = g.cumcount() + 1
    p["prev_close"] = g.close.shift(1)
    p["earnings_gap"] = p.open / p.prev_close - 1
    p["dollar_volume"] = p.close * p.volume
    # Sharadar's daily OHLC is split-adjusted going forward from the download
    # date; Alpaca's historical intraday bars are not retroactively adjusted.
    # This factor rescales an Alpaca price into Sharadar's adjusted scale.
    p["adj_factor"] = p.close / p.closeunadj.replace(0, np.nan)
    p["adv20_pre"] = (
        g.dollar_volume.shift(1).groupby(p.ticker, sort=False)
        .rolling(20, min_periods=15).median()
        .reset_index(level=0, drop=True).sort_index()
    )
    p["marketcap_pre"] = g.marketcap.shift(1)

    for horizon, n in (("H2", 1), ("H3", 5), ("H4", 20)):
        p[f"close_fwd_{horizon}"] = g.close.shift(-n)
        p[f"return_{horizon}"] = p[f"close_fwd_{horizon}"] / p.close - 1

    if events.empty:
        p["is_anchor_day"] = False
        p["is_earnings_day"] = False
        return p

    anchors: list[dict] = []
    flagged: list[dict] = []
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
            anchor_date = session_array[idx]
            anchors.append({"ticker": ticker, "date": pd.Timestamp(anchor_date)})
            for offset in (-1, 0, 1):
                j = idx + offset
                if 0 <= j < len(session_array):
                    flagged.append({"ticker": ticker, "date": pd.Timestamp(session_array[j])})

    anchor_index = (
        pd.MultiIndex.from_frame(pd.DataFrame(anchors).drop_duplicates())
        if anchors else pd.MultiIndex.from_arrays([[], []])
    )
    flagged_index = (
        pd.MultiIndex.from_frame(pd.DataFrame(flagged).drop_duplicates())
        if flagged else pd.MultiIndex.from_arrays([[], []])
    )
    row_index = pd.MultiIndex.from_frame(p[["ticker", "date"]])
    p["is_anchor_day"] = row_index.isin(anchor_index)
    p["is_earnings_day"] = row_index.isin(flagged_index)

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
    return p[eligible].copy()


def load_h1_opens(con: sqlite3.Connection, events: pd.DataFrame) -> pd.Series:
    """First Alpaca 15m bar open at/after 09:30 ET, keyed by (ticker, date)."""
    if events.empty:
        return pd.Series(dtype=float)
    dates = sorted(events.date.dt.strftime("%Y-%m-%d").unique())
    tickers = sorted(events.ticker.unique())
    if not dates or not tickers:
        return pd.Series(dtype=float)
    lo, hi = dates[0], dates[-1]
    t_placeholders = ",".join("?" * len(tickers))
    query = f"""SELECT ticker, substr(timestamp,1,10) AS event_date, timestamp, open
                FROM bars_15m
                WHERE ticker IN ({t_placeholders})
                  AND substr(timestamp,1,10)>=? AND substr(timestamp,1,10)<=?
                  AND substr(timestamp,12,8)>='09:30:00'"""
    frame = pd.read_sql_query(query, con, params=[*tickers, lo, hi])
    if frame.empty:
        return pd.Series(dtype=float)
    frame = frame.sort_values(["ticker", "event_date", "timestamp"])
    first_bar = frame.drop_duplicates(["ticker", "event_date"], keep="first")
    first_bar = first_bar.set_index(["ticker", "event_date"]).open
    first_bar.index = first_bar.index.set_names(["ticker", "date"])
    return first_bar


def winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    values = series.dropna()
    if len(values) < 20:
        return series
    lo, hi = values.quantile([lower, upper])
    return series.clip(lo, hi)


def _bucket(gap: pd.Series, threshold: float) -> pd.Series:
    out = pd.Series("neutral", index=gap.index, dtype="object")
    out[gap > threshold] = "long"
    out[gap < -threshold] = "short"
    return out


def _cost_adjust(gross: pd.Series, adv: pd.Series, round_trip_bps: float) -> pd.Series:
    per_side = side_cost_rate(adv)
    round_trip = np.maximum(2 * per_side, round_trip_bps / 10_000.0)
    return (1 + gross.to_numpy(float)) * (1 - round_trip) - 1


def horizon_spread_series(
    events: pd.DataFrame, horizon: str, threshold: float
) -> pd.DataFrame:
    """Per-event-day long/short bucket means for one horizon and threshold."""
    d = events.dropna(subset=[f"return_{horizon}"]).copy()
    d["bucket"] = _bucket(d.earnings_gap, threshold)
    rows = []
    for date, day in d.groupby("date", sort=True):
        long_ = day[day.bucket == "long"]
        short_ = day[day.bucket == "short"]
        rows.append({
            "date": date,
            "n_long": len(long_), "n_short": len(short_),
            "long_return": float(long_[f"return_{horizon}"].mean()) if len(long_) else np.nan,
            "short_return": float(short_[f"return_{horizon}"].mean()) if len(short_) else np.nan,
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["spread"] = frame.long_return - frame.short_return
    return frame


def summarize_threshold(events: pd.DataFrame, threshold: float, settings: Settings) -> dict:
    d = events.copy()
    d["bucket"] = _bucket(d.earnings_gap, threshold)
    long_events = d[d.bucket == "long"]
    short_events = d[d.bucket == "short"]
    result: dict = {
        "threshold": threshold,
        "n_long_events": int(len(long_events)),
        "n_short_events": int(len(short_events)),
        "avg_long_gap": float(long_events.earnings_gap.mean()) if len(long_events) else np.nan,
        "avg_short_gap": float(short_events.earnings_gap.mean()) if len(short_events) else np.nan,
        "horizons": {},
    }
    for horizon in HORIZONS:
        column = f"return_{horizon}"
        if column not in d.columns:
            result["horizons"][horizon] = {"status": "NO_DATA"}
            continue
        series = horizon_spread_series(d, horizon, threshold)
        if series.empty:
            result["horizons"][horizon] = {"status": "NO_DATA"}
            continue
        long_test = _hac_mean_test(series.long_return)
        short_test = _hac_mean_test(series.short_return)
        spread_test = _hac_mean_test(series.spread)
        result["horizons"][horizon] = {
            "n_event_days": int(len(series)),
            "long_mean": long_test["mean"], "long_tstat": long_test["tstat"], "long_n": long_test["n"],
            "short_mean": short_test["mean"], "short_tstat": short_test["tstat"], "short_n": short_test["n"],
            "spread_mean": spread_test["mean"], "spread_tstat": spread_test["tstat"], "spread_n": spread_test["n"],
            "spread_annualized": _annualized_mean(series.spread),
        }
    return result


def factor_regression_for_threshold(
    events: pd.DataFrame, threshold: float, factors: pd.DataFrame
) -> dict:
    """FF5+UMD regression of the daily long-short spread for H2-H4."""
    out = {}
    for horizon in ("H2", "H3", "H4"):
        series = horizon_spread_series(events, horizon, threshold)
        if series.empty:
            out[horizon] = {"status": "NO_DATA"}
            continue
        spread = series.set_index("date").spread
        out[horizon] = _factor_regression(spread, factors)
    return out


def gap_size_subgroups(events: pd.DataFrame, min_events: int) -> dict:
    bands = {
        "small": (0.01, 0.02), "medium": (0.02, 0.05), "large": (0.05, np.inf),
    }
    out = {}
    for label, (lo, hi) in bands.items():
        long_ = events[events.earnings_gap.between(lo, hi, inclusive="left")]
        short_ = events[(-events.earnings_gap).between(lo, hi, inclusive="left")]
        cell = {}
        for side_label, side in (("long", long_), ("short", short_)):
            if len(side) < min_events:
                cell[side_label] = {"status": "INSUFFICIENT_DATA", "n": int(len(side))}
                continue
            h1 = _hac_mean_test(side.get("return_H1", pd.Series(dtype=float)))
            h2 = _hac_mean_test(side.get("return_H2", pd.Series(dtype=float)))
            cell[side_label] = {
                "n": int(len(side)),
                "h1_mean": h1["mean"], "h1_tstat": h1["tstat"],
                "h2_mean": h2["mean"], "h2_tstat": h2["tstat"],
            }
        out[label] = cell
    return out


def market_cap_subgroups(events: pd.DataFrame, min_events: int) -> dict:
    valid = events.dropna(subset=["marketcap_pre"])
    if len(valid) < 30:
        return {"status": "INSUFFICIENT_DATA"}
    terciles = valid.marketcap_pre.quantile([1 / 3, 2 / 3]).to_numpy()
    labels = pd.cut(valid.marketcap_pre, [-np.inf, terciles[0], terciles[1], np.inf],
                     labels=["small", "mid", "large"])
    out = {}
    for label in ("small", "mid", "large"):
        subset = valid[labels == label]
        if len(subset) < min_events:
            out[label] = {"status": "INSUFFICIENT_DATA", "n": int(len(subset))}
            continue
        h1 = _hac_mean_test(subset.get("return_H1", pd.Series(dtype=float)))
        h2 = _hac_mean_test(subset.get("return_H2", pd.Series(dtype=float)))
        out[label] = {"n": int(len(subset)), "h1_mean": h1["mean"], "h1_tstat": h1["tstat"],
                      "h2_mean": h2["mean"], "h2_tstat": h2["tstat"]}
    return out


def time_period_subgroups(events: pd.DataFrame, min_events: int) -> dict:
    windows = {
        "pre_2010": ("2001-01-01", "2009-12-31"),
        "2010_2020": ("2010-01-01", "2020-12-31"),
        "2020_2024": ("2020-01-01", "2024-12-31"),
    }
    out = {}
    for label, (lo, hi) in windows.items():
        subset = events[events.date.between(lo, hi)]
        if len(subset) < min_events:
            out[label] = {"status": "INSUFFICIENT_DATA", "n": int(len(subset))}
            continue
        h1 = _hac_mean_test(subset.get("return_H1", pd.Series(dtype=float)))
        h2 = _hac_mean_test(subset.get("return_H2", pd.Series(dtype=float)))
        out[label] = {"n": int(len(subset)), "h1_mean": h1["mean"], "h1_tstat": h1["tstat"],
                      "h2_mean": h2["mean"], "h2_tstat": h2["tstat"]}
    return out


def regime_subgroups(events: pd.DataFrame, spy_close: pd.Series, min_events: int) -> dict:
    ma200 = spy_close.rolling(200, min_periods=200).mean()
    regime = pd.Series(np.where(spy_close > ma200, "above_200dma", "below_200dma"), index=spy_close.index)
    regime[ma200.isna()] = np.nan
    events = events.copy()
    events["regime"] = events.date.map(regime)
    out = {}
    for label in ("above_200dma", "below_200dma"):
        subset = events[events.regime == label]
        if len(subset) < min_events:
            out[label] = {"status": "INSUFFICIENT_DATA", "n": int(len(subset))}
            continue
        h1 = _hac_mean_test(subset.get("return_H1", pd.Series(dtype=float)))
        h2 = _hac_mean_test(subset.get("return_H2", pd.Series(dtype=float)))
        out[label] = {"n": int(len(subset)), "h1_mean": h1["mean"], "h1_tstat": h1["tstat"],
                      "h2_mean": h2["mean"], "h2_tstat": h2["tstat"]}
    return out


def run_research(
    db_path: str | Path,
    alpaca_db_path: str | Path,
    settings: Settings,
    factors: pd.DataFrame,
    spy_close: pd.Series,
) -> dict:
    start_ts, end_ts = pd.Timestamp(settings.start), pd.Timestamp(settings.end)
    con = sqlite3.connect(str(db_path))
    metadata = _candidate_metadata(con, settings.start, settings.end)
    all_events: list[pd.DataFrame] = []
    total_raw_events = 0
    try:
        for year in range(start_ts.year, end_ts.year + 1):
            lo = max(start_ts, pd.Timestamp(year=year, month=1, day=1))
            hi = min(end_ts, pd.Timestamp(year=year, month=12, day=31))
            prices, raw_events = load_universe_and_events(con, metadata, lo, hi)
            total_raw_events += len(raw_events)
            features = build_features(prices, raw_events, lo, hi, settings)
            if not features.empty:
                all_events.append(features)
    finally:
        con.close()

    events = pd.concat(all_events, ignore_index=True) if all_events else pd.DataFrame()
    coverage = {
        "total_earnings_filings_seen": int(total_raw_events),
        "valid_gap_measurements": int(len(events)),
    }
    if events.empty:
        raise RuntimeError("No valid earnings-day events; check data coverage and eligibility settings")

    for column in ("return_H2", "return_H3", "return_H4", "earnings_gap"):
        events[column] = winsorize(events[column])

    alpaca_con = sqlite3.connect(str(alpaca_db_path))
    try:
        h1_opens = load_h1_opens(alpaca_con, events[["ticker", "date"]])
    finally:
        alpaca_con.close()
    events["date_str"] = events.date.dt.strftime("%Y-%m-%d")
    key = pd.MultiIndex.from_frame(events[["ticker", "date_str"]])
    events["alpaca_open"] = h1_opens.reindex(key).to_numpy()
    events["alpaca_open_adjusted"] = events.alpaca_open * events.adj_factor
    events["return_H1"] = np.where(
        events.alpaca_open_adjusted.notna() & (events.alpaca_open_adjusted > 0),
        events.close / events.alpaca_open_adjusted - 1,
        np.nan,
    )
    events["return_H1"] = winsorize(pd.Series(events["return_H1"], index=events.index))

    coverage["h1_alpaca_coverage_events"] = int(events.alpaca_open.notna().sum())
    coverage["h1_alpaca_coverage_pct"] = float(events.alpaca_open.notna().mean())
    span_years = (end_ts - start_ts).days / 365.25
    coverage["events_per_year_avg"] = float(len(events) / span_years)
    coverage["unique_tickers"] = int(events.ticker.nunique())

    threshold_results = {t: summarize_threshold(events, t, settings) for t in THRESHOLDS}
    primary_events = events.copy()
    primary_bucket = _bucket(primary_events.earnings_gap, PRIMARY_THRESHOLD)
    primary_events = primary_events[primary_bucket != "neutral"]

    factor_reg = factor_regression_for_threshold(events, PRIMARY_THRESHOLD, factors)

    subgroups = {
        "gap_size": gap_size_subgroups(events, settings.min_subgroup_events),
        "market_cap": market_cap_subgroups(primary_events, settings.min_subgroup_events),
        "time_period": time_period_subgroups(primary_events, settings.min_subgroup_events),
        "market_regime": regime_subgroups(primary_events, spy_close, settings.min_subgroup_events),
    }

    primary = threshold_results[PRIMARY_THRESHOLD]
    h1 = primary["horizons"].get("H1", {})
    h2 = primary["horizons"].get("H2", {})
    h1_tstat = h1.get("spread_tstat", np.nan)
    h2_tstat = h2.get("spread_tstat", np.nan)
    h1_advance = np.isfinite(h1_tstat) and h1_tstat > 2.0
    h1_secondary = np.isfinite(h1_tstat) and 1.5 <= h1_tstat <= 2.0
    h2_advance = np.isfinite(h2_tstat) and h2_tstat > 1.5
    h2_secondary = np.isfinite(h2_tstat) and h2_tstat > 0 and h2_tstat < 1.5

    gap_size_signs = []
    for label, cell in subgroups["gap_size"].items():
        long_cell = cell.get("long", {})
        if isinstance(long_cell, dict) and "h1_mean" in long_cell and np.isfinite(long_cell["h1_mean"]):
            gap_size_signs.append(long_cell["h1_mean"] > 0)
    gap_size_consistent = bool(gap_size_signs) and all(gap_size_signs) if gap_size_signs else False

    bonferroni_pass = np.isfinite(h1_tstat) and abs(h1_tstat) > BONFERRONI_T_THRESHOLD

    if h1_advance and h2_advance and gap_size_consistent:
        verdict = "ADVANCE"
    elif h1_advance or h1_secondary or h2_secondary:
        verdict = "SECONDARY"
    else:
        verdict = "FALSIFIED"

    falsification = {
        "h1_t_gt_2.0": bool(h1_advance),
        "h1_tstat": h1_tstat,
        "h2_t_gt_1.5": bool(h2_advance),
        "h2_tstat": h2_tstat,
        "gap_size_consistent": gap_size_consistent,
        "bonferroni_threshold": BONFERRONI_T_THRESHOLD,
        "bonferroni_pass_primary": bool(bonferroni_pass),
        "verdict": verdict,
    }

    return {
        "coverage": coverage,
        "thresholds": threshold_results,
        "primary_threshold": PRIMARY_THRESHOLD,
        "factor_regression_primary": factor_reg,
        "subgroups": subgroups,
        "falsification": falsification,
        "events_frame": events,
    }


def correlate_with_ibs_proxy(
    events: pd.DataFrame,
    threshold: float,
    ibs_daily_diagnostics_path: str | Path | None,
) -> dict:
    """Correlate the primary-threshold H2 spread series with the IBS engine's
    historical daily net-return series (used as a proxy for V3.1, which has
    no historical NAV overlap with this 2001-2024 development window)."""
    series = horizon_spread_series(events, "H2", threshold).set_index("date").spread
    if ibs_daily_diagnostics_path is None or not Path(ibs_daily_diagnostics_path).exists():
        return {
            "status": "PROXY_UNAVAILABLE",
            "note": "IBS conditioning daily diagnostics file not found; correlation not computed.",
        }
    diagnostics = pd.read_csv(ibs_daily_diagnostics_path, parse_dates=["return_date"])
    ibs_daily = (
        diagnostics[diagnostics.hypothesis_id == "ibs_earnings_event"]
        .groupby("return_date").long_net.mean()
    )
    aligned = pd.concat([series.rename("earnings_continuation"), ibs_daily.rename("ibs_proxy")], axis=1).dropna()
    if len(aligned) < 20:
        return {"status": "INSUFFICIENT_OVERLAP", "n_overlap": int(len(aligned))}
    corr = float(aligned.earnings_continuation.corr(aligned.ibs_proxy))
    return {
        "status": "COMPUTED",
        "proxy_used": "ibs_conditioning daily net returns (ibs_earnings_event hypothesis, non-earnings-day IBS engine)",
        "n_overlap_days": int(len(aligned)),
        "correlation": corr,
        "flag_for_investigation": bool(abs(corr) > 0.30) if np.isfinite(corr) else None,
    }


def load_factors(csv_path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(csv_path, index_col=0, parse_dates=True)
    missing = (set(FACTOR_COLUMNS) | {"RF"}) - set(frame.columns)
    if missing:
        raise ValueError(f"factor CSV is missing columns: {sorted(missing)}")
    return frame.sort_index()


def load_spy_close(csv_path: str | Path) -> pd.Series:
    ret = pd.read_csv(csv_path, index_col=0, parse_dates=True).iloc[:, 0]
    return (1 + ret.fillna(0)).cumprod().rename("SPY")


def write_outputs(
    results: dict,
    correlation: dict,
    settings: Settings,
    output_root: str | Path,
    preregistration_path: str | Path,
    db_path: str | Path,
) -> Path:
    preregistration_path = Path(preregistration_path)
    prereg_bytes = preregistration_path.read_bytes()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    events = results["events_frame"]
    digest = hashlib.sha256(events.to_csv(index=False).encode()).hexdigest()[:10]
    out = Path(output_root) / f"{stamp}_{digest}"
    out.mkdir(parents=True, exist_ok=False)

    events.drop(columns=["events_frame"], errors="ignore").to_csv(out / "events.csv", index=False)
    payload = {k: v for k, v in results.items() if k != "events_frame"}
    payload["correlation_with_v3_1_proxy"] = correlation
    (out / "results.json").write_text(
        json.dumps(_json_safe(payload), indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (out / "preregistration_snapshot.json").write_bytes(prereg_bytes)

    repo_root = Path(__file__).resolve().parents[2]
    db = Path(db_path).resolve()
    manifest = {
        "created_utc": stamp,
        "evidence_stage": "historical development; NOT fresh validation",
        "prospective_confirmation_required": True,
        "holdout_budget_remaining": 0,
        "settings": asdict(settings),
        "preregistration_sha256": hashlib.sha256(prereg_bytes).hexdigest(),
        "source_database": str(db),
        "git_commit": _git_commit(repo_root),
    }
    (out / "run_manifest.json").write_text(
        json.dumps(_json_safe(manifest), indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="V2/data/sharadar.db")
    parser.add_argument("--alpaca-db", default="V2/data/alpaca_intraday.db")
    parser.add_argument("--factors-csv", default="V2/data/factors_ff5_mom_daily_2001_2024.csv")
    parser.add_argument("--spy-csv", default="V2/data/spy_total_return_daily_2001_2024.csv")
    parser.add_argument("--start", default="2001-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--output-root", default="V2/reports/earnings_continuation_A")
    parser.add_argument("--preregistration", default="V2/research/preregistrations/earnings_continuation_A_v1.json")
    parser.add_argument("--ibs-diagnostics-csv", default=None)
    parser.add_argument(
        "--acknowledge-used-historical-data", action="store_true",
        help="Required: confirms this is development/characterization, not fresh validation.",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.acknowledge_used_historical_data:
        parser.error("--acknowledge-used-historical-data is required")
    settings = Settings(start=args.start, end=args.end)
    factors = load_factors(args.factors_csv)
    spy_close = load_spy_close(args.spy_csv)
    results = run_research(args.db, args.alpaca_db, settings, factors, spy_close)
    correlation = correlate_with_ibs_proxy(
        results["events_frame"], PRIMARY_THRESHOLD, args.ibs_diagnostics_csv
    )
    out = write_outputs(results, correlation, settings, args.output_root, args.preregistration, args.db)
    print(out)


if __name__ == "__main__":
    main()
