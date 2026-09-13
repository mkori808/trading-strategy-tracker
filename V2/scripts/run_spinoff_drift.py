"""Run spin-off drift (spinoff_drift_v1). See strategies/spinoff_drift.py and
research/preregistrations/spinoff_drift_v1.json for the locked spec."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.spinoff_drift import (
    HORIZONS, PRIMARY_HORIZON,
    attach_spy_and_excess, build_leg_events, build_master_calendar,
    distribution_ratio_conditioning, eligible_events, factor_regression_event_time,
    leave_one_crisis_out, load_events, load_marketcap_at, load_price_panel,
    load_sic_codes, size_tercile_analysis, summarize_all_horizons, time_period_analysis,
)
from strategies.earnings_continuation import load_factors, load_spy_close


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (np.floating, float)):
        v = float(value)
        return None if not np.isfinite(v) else v
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    return value


def build_leg(
    con: sqlite3.Connection, events: pd.DataFrame, master_calendar: np.ndarray,
    start: str, end: str, spy_close: pd.Series,
) -> tuple[pd.DataFrame, dict]:
    tickers = events.ticker.unique().tolist()
    prices = load_price_panel(con, tickers)
    sic_codes = load_sic_codes(con, tickers)
    frame = build_leg_events(events, prices, master_calendar, sic_codes, start, end)

    coverage = {
        "total_events": int(len(frame)),
        "no_price_data": int((frame.exclude_reason == "NO_PRICE_DATA").sum()),
        "no_entry_price_within_5d": int((frame.exclude_reason == "NO_ENTRY_PRICE_WITHIN_5_DAYS").sum()),
        "data_quality_gate_excluded": int((frame.exclude_reason == "DATA_QUALITY_GATE").sum()),
    }

    priced = frame[frame.entry_price.notna()].copy()
    if not priced.empty:
        mcap = load_marketcap_at(con, priced[["ticker", "entry_date"]].assign(entry_date=priced.entry_date))
        priced["marketcap_entry"] = mcap.reindex(priced.index)
    else:
        priced["marketcap_entry"] = np.nan

    priced = attach_spy_and_excess(priced, spy_close)
    priced = priced[priced.entry_date.between(start, end)]
    coverage["in_development_window"] = int(len(priced))
    coverage["financial_sic_excluded"] = int(priced.is_financial.fillna(False).sum())

    elig = eligible_events(priced)
    coverage["eligible_after_all_filters"] = int(len(elig))
    span_years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    coverage["events_per_year_avg"] = float(len(elig) / span_years)
    return elig, coverage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "sharadar.db"))
    parser.add_argument("--factors-csv", default=str(ROOT / "data" / "factors_ff5_mom_daily_2001_2024.csv"))
    parser.add_argument("--spy-csv", default=str(ROOT / "data" / "spy_total_return_daily_2001_2024.csv"))
    parser.add_argument("--start", default="2000-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    con = sqlite3.connect(args.db)
    print("Loading events...")
    child_events, parent_events = load_events(con)
    print(f"  child (spunofffrom): {len(child_events)}  parent (spinoff): {len(parent_events)}")

    print("Loading factor/benchmark data...")
    factors = load_factors(args.factors_csv)
    spy_close = load_spy_close(args.spy_csv)

    print("Building master trading calendar...")
    all_tickers = pd.concat([child_events.ticker, parent_events.ticker]).unique().tolist()
    calendar_prices = load_price_panel(con, all_tickers)
    master_calendar = build_master_calendar(calendar_prices, args.start, args.end)
    print(f"  master calendar sessions: {len(master_calendar)}")

    print("Building child leg (primary)...")
    child, child_coverage = build_leg(con, child_events, master_calendar, args.start, args.end, spy_close)
    print("  child coverage:", child_coverage)

    print("Building parent leg (secondary)...")
    parent, parent_coverage = build_leg(con, parent_events, master_calendar, args.start, args.end, spy_close)
    print("  parent coverage:", parent_coverage)

    con.close()

    result = {
        "coverage": {"child": child_coverage, "parent": parent_coverage},
    }

    print("Primary results (child, H1-H4)...")
    result["primary_child_horizons"] = summarize_all_horizons(child)

    print("Secondary results (parent, H1-H4)...")
    result["secondary_parent_horizons"] = summarize_all_horizons(parent)

    print("Factor regression (FF5+UMD), child, primary horizon...")
    result["factor_regression_child_h2"] = factor_regression_event_time(child, PRIMARY_HORIZON, factors)
    print("Factor regression (FF5+UMD), child, all horizons (for reference)...")
    result["factor_regression_child_all"] = {
        h: factor_regression_event_time(child, h, factors) for h in HORIZONS
    }

    print("Size tercile analysis (child, H2)...")
    result["size_tercile_child_h2"] = size_tercile_analysis(child, PRIMARY_HORIZON)

    print("Time period analysis (child, H2)...")
    result["time_period_child_h2"] = time_period_analysis(child, PRIMARY_HORIZON)

    print("Leave-one-crisis-out (child, H2)...")
    result["leave_one_crisis_out_child_h2"] = leave_one_crisis_out(child, PRIMARY_HORIZON)

    print("Distribution ratio conditioning (child, H2)...")
    result["distribution_ratio_child_h2"] = distribution_ratio_conditioning(child, PRIMARY_HORIZON)

    out_dir = Path(args.out) if args.out else ROOT / "reports" / "spinoff_drift"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / stamp
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "results.json").write_text(json.dumps(_json_safe(result), indent=2), encoding="utf-8")
    drop_cols = [c for c in child.columns if c.startswith("spy_exit_") or c.startswith("exit_date_")]
    child.drop(columns=drop_cols, errors="ignore").to_csv(out_path / "child_events.csv", index=False)
    parent.drop(columns=drop_cols, errors="ignore").to_csv(out_path / "parent_events.csv", index=False)
    print(f"\nResults written to: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
