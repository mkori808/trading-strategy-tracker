"""Spin-off drift (spinoff_drift_v1): post-spin-off forced-selling recovery,
tested separately on the child (primary) and parent (secondary) legs.

Mechanism is forced institutional selling of the child security, not
information diffusion -- distinct from the earnings-based studies already
closed in this project (earnings_continuation.py, pead_b.py). Event universe,
filters, horizons, and falsification bands are locked in
research/preregistrations/spinoff_drift_v1.json.

Does not read or modify any paper-trading state file, V3.2, or Tracks 1-3.
"""
from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from strategies.ibs_conditioning import FACTOR_COLUMNS, TRADING_DAYS, _factor_regression, _hac_mean_test

HORIZONS = {"H1": 21, "H2": 63, "H3": 126, "H4": 252}
PRIMARY_HORIZON = "H2"
MIN_SUBGROUP_EVENTS = 15

CRISIS_WINDOWS = {
    "dot-com / 2001-2002": ("2001-05-25", "2003-05-09"),
    "GFC / 2008-2009": ("2007-07-13", "2010-04-09"),
    "COVID / 2020": ("2019-12-20", "2020-06-05"),
    "2022 bear": ("2021-11-12", "2023-03-03"),
}

DECADES = {
    "2000-2009": ("2000-01-01", "2009-12-31"),
    "2010-2019": ("2010-01-01", "2019-12-31"),
    "2020-2026": ("2020-01-01", "2026-12-31"),
}


def load_events(con: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Child (spunofffrom, primary) and parent (spinoff, secondary) event rows."""
    child = pd.read_sql_query(
        "SELECT date, ticker, value AS distribution_ratio, name FROM actions "
        "WHERE action='spunofffrom' ORDER BY date",
        con, parse_dates=["date"],
    )
    parent = pd.read_sql_query(
        "SELECT date, ticker, value AS distribution_ratio, name, contraticker, contraname "
        "FROM actions WHERE action='spinoff' ORDER BY date",
        con, parse_dates=["date"],
    )
    return child, parent


def load_price_panel(con: sqlite3.Connection, tickers: list[str]) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame(columns=["ticker", "date", "close", "closeadj"])
    placeholders = ",".join("?" * len(tickers))
    frame = pd.read_sql_query(
        f"SELECT ticker, date, close, closeadj FROM stocks WHERE ticker IN ({placeholders}) ORDER BY ticker, date",
        con, params=tickers, parse_dates=["date"],
    )
    frame["close"] = pd.to_numeric(frame.close, errors="coerce")
    frame["closeadj"] = pd.to_numeric(frame.closeadj, errors="coerce")
    return frame


def load_sic_codes(con: sqlite3.Connection, tickers: list[str]) -> pd.Series:
    if not tickers:
        return pd.Series(dtype=float)
    placeholders = ",".join("?" * len(tickers))
    frame = pd.read_sql_query(
        f"SELECT ticker, siccode FROM tickers WHERE ticker IN ({placeholders})",
        con, params=tickers,
    )
    frame["siccode"] = pd.to_numeric(frame.siccode, errors="coerce")
    return frame.drop_duplicates("ticker").set_index("ticker").siccode


def load_marketcap_at(con: sqlite3.Connection, pairs: pd.DataFrame) -> pd.Series:
    """Last available daily.marketcap on or before each (ticker, date) pair."""
    tickers = pairs.ticker.unique().tolist()
    if not tickers:
        return pd.Series(dtype=float)
    placeholders = ",".join("?" * len(tickers))
    frame = pd.read_sql_query(
        f"SELECT ticker, date, marketcap FROM daily WHERE ticker IN ({placeholders}) ORDER BY ticker, date",
        con, params=tickers, parse_dates=["date"],
    )
    frame["marketcap"] = pd.to_numeric(frame.marketcap, errors="coerce")
    out = {}
    for ticker, sub in frame.groupby("ticker", sort=False):
        dates = sub.date.to_numpy()
        caps = sub.marketcap.to_numpy()
        for idx, row in pairs[pairs.ticker == ticker].iterrows():
            pos = np.searchsorted(dates, np.datetime64(row.entry_date), side="right") - 1
            out[idx] = caps[pos] if pos >= 0 else np.nan
    return pd.Series(out)


def build_master_calendar(prices: pd.DataFrame, start: str, end: str) -> np.ndarray:
    """Union of all tickers' trading dates in the source DB, used as the
    reference calendar for the data-quality gate (independent of any one
    ticker's own -- possibly truncated by delisting -- trading history)."""
    dates = prices.loc[prices.date.between(start, end), "date"].unique()
    return np.sort(dates)


def _entry_price(dates: np.ndarray, closeadj: np.ndarray, event_date: np.datetime64) -> tuple[int, float] | None:
    """Locate entry index/price: exact date, else the first available
    session within the next 5 trading days. Returns None if no valid price
    is found in that window."""
    idx = int(np.searchsorted(dates, event_date, side="left"))
    for offset in range(6):
        j = idx + offset
        if j < len(dates) and np.isfinite(closeadj[j]):
            return j, closeadj[j]
    return None


def build_leg_events(
    events: pd.DataFrame,
    prices: pd.DataFrame,
    master_calendar: np.ndarray,
    sic_codes: pd.Series,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Attach entry price/index, forward closes for all horizons, sic
    exclusion, and the data-quality gate. One row per event; NaN forward
    returns where a horizon's window isn't resolvable (still kept, not
    dropped, for the coverage report)."""
    rows = []
    by_ticker = {t: g.sort_values("date") for t, g in prices.groupby("ticker", sort=False)}
    for _, ev in events.iterrows():
        ticker = ev.ticker
        sub = by_ticker.get(ticker)
        record = {
            "date": ev.date, "ticker": ticker, "distribution_ratio": ev.distribution_ratio,
            "name": ev.name if "name" in ev else None,
        }
        if sub is None or sub.empty:
            record["exclude_reason"] = "NO_PRICE_DATA"
            rows.append(record)
            continue
        dates = sub.date.to_numpy()
        closeadj = sub.closeadj.to_numpy()
        found = _entry_price(dates, closeadj, np.datetime64(ev.date))
        if found is None or not np.isfinite(found[1]):
            record["exclude_reason"] = "NO_ENTRY_PRICE_WITHIN_5_DAYS"
            rows.append(record)
            continue
        entry_idx, entry_price = found
        entry_date = dates[entry_idx]
        record["entry_date"] = pd.Timestamp(entry_date)
        record["entry_price"] = float(entry_price)

        sic = sic_codes.get(ticker, np.nan)
        record["siccode"] = sic
        record["is_financial"] = bool(np.isfinite(sic) and 6000 <= sic <= 6999)

        # Data-quality gate: master-calendar sessions in [entry, entry+21) vs
        # how many of those sessions this ticker actually has a price row for.
        cal_pos = int(np.searchsorted(master_calendar, entry_date, side="left"))
        window_cal = master_calendar[cal_pos:cal_pos + 21]
        present = np.isin(window_cal, dates)
        record["missing_days_first_21"] = int((~present).sum())
        record["data_quality_fail"] = bool(record["missing_days_first_21"] > 5)

        for horizon, n in HORIZONS.items():
            exit_idx = entry_idx + n
            if exit_idx < len(dates):
                exit_price = closeadj[exit_idx]
                exit_date = dates[exit_idx]
                survivorship = False
            else:
                exit_price = closeadj[-1]
                exit_date = dates[-1]
                survivorship = True
            record[f"exit_date_{horizon}"] = pd.Timestamp(exit_date)
            record[f"survivorship_exit_{horizon}"] = survivorship
            if np.isfinite(exit_price) and np.isfinite(entry_price) and entry_price > 0:
                record[f"raw_return_{horizon}"] = float(exit_price / entry_price - 1)
            else:
                record[f"raw_return_{horizon}"] = np.nan
        rows.append(record)
    frame = pd.DataFrame(rows)
    if "exclude_reason" not in frame.columns:
        frame["exclude_reason"] = None
    if "data_quality_fail" in frame.columns:
        fail_mask = frame["data_quality_fail"].fillna(False) & frame["exclude_reason"].isna()
        frame.loc[fail_mask, "exclude_reason"] = "DATA_QUALITY_GATE"
    return frame


def attach_spy_and_excess(frame: pd.DataFrame, spy_close: pd.Series) -> pd.DataFrame:
    frame = frame.copy()
    spy_dates = spy_close.index.to_numpy()
    spy_values = spy_close.to_numpy()

    def spy_at(date):
        if pd.isna(date):
            return np.nan
        pos = np.searchsorted(spy_dates, np.datetime64(date), side="left")
        if pos < len(spy_dates) and spy_dates[pos] == np.datetime64(date):
            return spy_values[pos]
        return np.nan

    frame["spy_entry"] = frame.entry_date.map(spy_at)
    for horizon in HORIZONS:
        frame[f"spy_exit_{horizon}"] = frame[f"exit_date_{horizon}"].map(spy_at)
        spy_ret = frame[f"spy_exit_{horizon}"] / frame["spy_entry"] - 1
        frame[f"spy_return_{horizon}"] = spy_ret
        frame[f"excess_return_{horizon}"] = frame[f"raw_return_{horizon}"] - spy_ret
    return frame


def winsorize(series: pd.Series, lower: float = 0.01, upper: float = 0.99) -> pd.Series:
    values = series.dropna()
    if len(values) < 20:
        return series
    lo, hi = values.quantile([lower, upper])
    return series.clip(lo, hi)


def eligible_events(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply the preregistered universe filters: min price, sector exclusion,
    data-quality gate. Missing entry price / no data are already excluded
    upstream by construction (NaN entry_price rows)."""
    ok = (
        frame.entry_price.notna()
        & (frame.entry_price >= 1.00)
        & (~frame.is_financial.fillna(False))
        & (~frame.data_quality_fail.fillna(True))
    )
    return frame[ok].copy()


def summarize_horizon(events: pd.DataFrame, horizon: str) -> dict:
    col = f"excess_return_{horizon}"
    series = winsorize(events[col])
    test = _hac_mean_test(series)
    valid = series.dropna()
    if valid.empty:
        return {"status": "NO_DATA"}
    return {
        "n_events": int(len(valid)),
        "excess_return_mean": test["mean"],
        "tstat": test["tstat"],
        "median": float(valid.median()),
        "pct_positive": float((valid > 0).mean()),
    }


def summarize_all_horizons(events: pd.DataFrame) -> dict:
    return {h: summarize_horizon(events, h) for h in HORIZONS}


def factor_regression_event_time(
    events: pd.DataFrame, horizon: str, factors: pd.DataFrame
) -> dict:
    """Event-time regression: dependent = event's RF-adjusted raw return over
    its own [entry, entry+N] window; independent = each FF5+UMD factor
    compounded (buy-and-hold) over that SAME window. HAC(5) standard errors."""
    n = HORIZONS[horizon]
    columns = [c for c in FACTOR_COLUMNS if c in factors.columns]
    factor_dates = factors.index.to_numpy()
    y_rows, x_rows = [], []
    for _, ev in events.iterrows():
        entry_date = ev.entry_date
        exit_date = ev[f"exit_date_{horizon}"]
        raw_ret = ev[f"raw_return_{horizon}"]
        if pd.isna(entry_date) or pd.isna(exit_date) or pd.isna(raw_ret):
            continue
        lo = int(np.searchsorted(factor_dates, np.datetime64(entry_date), side="left"))
        hi = int(np.searchsorted(factor_dates, np.datetime64(exit_date), side="right"))
        window = factors.iloc[lo:hi]
        if len(window) < max(5, n // 4):
            continue
        rf_compound = float((1 + window["RF"]).prod() - 1)
        factor_compound = {c: float((1 + window[c]).prod() - 1) for c in columns}
        y_rows.append(raw_ret - rf_compound)
        x_rows.append(factor_compound)
    if len(y_rows) < 30:
        return {"status": "INSUFFICIENT_OVERLAP", "n": len(y_rows)}
    y = pd.Series(y_rows)
    x = pd.DataFrame(x_rows)
    import statsmodels.api as sm
    model = sm.OLS(y, sm.add_constant(x)).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
    periods_per_year = TRADING_DAYS / n
    alpha_period = float(model.params["const"])
    return {
        "n": len(y_rows),
        "alpha_per_event_window": alpha_period,
        "alpha_annualized": float((1 + alpha_period) ** periods_per_year - 1) if alpha_period > -1 else np.nan,
        "alpha_tstat": float(model.tvalues["const"]),
        "r_squared": float(model.rsquared),
        "betas": {c: float(model.params[c]) for c in columns},
    }


def size_tercile_analysis(events: pd.DataFrame, horizon: str = PRIMARY_HORIZON) -> dict:
    valid = events.dropna(subset=["marketcap_entry", f"excess_return_{horizon}"])
    if len(valid) < 30:
        return {"status": "INSUFFICIENT_DATA"}
    terciles = valid.marketcap_entry.quantile([1 / 3, 2 / 3]).to_numpy()
    labels = pd.cut(valid.marketcap_entry, [-np.inf, terciles[0], terciles[1], np.inf], labels=["small", "mid", "large"])
    out = {}
    for label in ("small", "mid", "large"):
        subset = valid[labels == label]
        if len(subset) < MIN_SUBGROUP_EVENTS:
            out[label] = {"status": "INSUFFICIENT_DATA", "n": int(len(subset))}
            continue
        series = winsorize(subset[f"excess_return_{horizon}"])
        test = _hac_mean_test(series)
        out[label] = {"n": int(len(subset)), "excess_return_mean": test["mean"], "tstat": test["tstat"]}
    return out


def time_period_analysis(events: pd.DataFrame, horizon: str = PRIMARY_HORIZON) -> dict:
    out = {}
    for label, (lo, hi) in DECADES.items():
        subset = events[events.entry_date.between(lo, hi)]
        if len(subset) < MIN_SUBGROUP_EVENTS:
            out[label] = {"status": "INSUFFICIENT_DATA", "n": int(len(subset))}
            continue
        series = winsorize(subset[f"excess_return_{horizon}"])
        test = _hac_mean_test(series)
        out[label] = {"n": int(len(subset)), "excess_return_mean": test["mean"], "tstat": test["tstat"]}
    return out


def leave_one_crisis_out(events: pd.DataFrame, horizon: str = PRIMARY_HORIZON) -> dict:
    full_series = winsorize(events[f"excess_return_{horizon}"])
    full_test = _hac_mean_test(full_series)
    out = {
        "full_sample": {
            "n_events": int(full_series.notna().sum()),
            "excess_return_mean": full_test["mean"], "tstat": full_test["tstat"],
            "sign_positive": bool(full_test["mean"] > 0) if np.isfinite(full_test["mean"]) else None,
        }
    }
    for label, (lo, hi) in CRISIS_WINDOWS.items():
        mask = events.entry_date.between(lo, hi)
        ex = events[~mask]
        series = winsorize(ex[f"excess_return_{horizon}"])
        test = _hac_mean_test(series)
        out[f"excluding_{label}"] = {
            "n_events": int(series.notna().sum()),
            "removed_events": int(mask.sum()),
            "excess_return_mean": test["mean"], "tstat": test["tstat"],
            "sign_positive": bool(test["mean"] > 0) if np.isfinite(test.get("mean", np.nan)) else None,
        }
    return out


def distribution_ratio_conditioning(events: pd.DataFrame, horizon: str = PRIMARY_HORIZON) -> dict:
    valid = events.dropna(subset=["distribution_ratio", f"excess_return_{horizon}"])
    if len(valid) < 30:
        return {"status": "INSUFFICIENT_DATA"}
    median_ratio = valid.distribution_ratio.median()
    out = {"median_distribution_ratio": float(median_ratio)}
    for label, mask in (("high_ratio", valid.distribution_ratio >= median_ratio),
                         ("low_ratio", valid.distribution_ratio < median_ratio)):
        subset = valid[mask]
        series = winsorize(subset[f"excess_return_{horizon}"])
        test = _hac_mean_test(series)
        out[label] = {"n": int(len(subset)), "excess_return_mean": test["mean"], "tstat": test["tstat"]}
    return out
