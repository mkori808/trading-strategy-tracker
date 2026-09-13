"""Run PEAD sub-hypothesis B (pead_B_v1). See strategies/pead_b.py and
research/preregistrations/pead_B_v1.json for the locked spec."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategies.pead_b import (
    HORIZONS, PRIMARY_THRESHOLD, THRESHOLDS,
    factor_regressions, leave_one_crisis_out, market_cap_subgroups,
    regime_check_2020, run_pead_b, summarize_threshold,
)
from strategies.earnings_continuation import load_factors


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
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "sharadar.db"))
    parser.add_argument("--factors-csv", default=str(ROOT / "data" / "factors_ff5_mom_daily_2001_2024.csv"))
    parser.add_argument("--start", default="2001-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    print("Loading universe and events...")
    bundle = run_pead_b(args.db, args.start, args.end)
    events, coverage = bundle["events"], bundle["coverage"]
    print("Coverage:", coverage)

    print("2020+ regime check...")
    regime = regime_check_2020(events, PRIMARY_THRESHOLD)
    for h in HORIZONS:
        r = regime[h]
        print(f"  {h}: full={r['full_sample'].get('annualized')} t={r['full_sample'].get('tstat')} | "
              f"pre2020 t={r['pre_2020'].get('tstat')} | post2020 t={r['post_2020'].get('tstat')}")

    fatal = all(
        (regime[h]["post_2020"].get("tstat") is not None and np.isfinite(regime[h]["post_2020"]["tstat"])
         and regime[h]["post_2020"]["tstat"] < 1.0)
        or regime[h]["post_2020"].get("tstat") is None
        for h in HORIZONS
    )
    print("FATAL (all horizons post-2020 t<1.0 or no data):", fatal)

    result = {"coverage": coverage, "regime_check_2020": regime, "fatal_stop": bool(fatal)}

    if not fatal:
        print("Full characterization (3 thresholds x 3 horizons)...")
        result["thresholds"] = {str(t): summarize_threshold(events, t) for t in THRESHOLDS}

        print("Factor regressions (FF5+UMD)...")
        factors = load_factors(args.factors_csv)
        result["factor_regression_primary"] = factor_regressions(events, PRIMARY_THRESHOLD, factors)

        print("Leave-one-crisis-out...")
        result["leave_one_crisis_out"] = leave_one_crisis_out(events, PRIMARY_THRESHOLD, horizon="H2")

        print("Market-cap subgroups...")
        result["market_cap_subgroups"] = market_cap_subgroups(events, PRIMARY_THRESHOLD)

    out_dir = Path(args.out) if args.out else ROOT / "reports" / "pead_b"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / stamp
    out_path.mkdir(parents=True, exist_ok=True)
    (out_path / "results.json").write_text(json.dumps(_json_safe(result), indent=2), encoding="utf-8")
    events.drop(columns=[c for c in events.columns if c.startswith("close_fwd_")]).to_csv(
        out_path / "events.csv", index=False
    )
    print(f"\nResults written to: {out_path}")
    return out_path


if __name__ == "__main__":
    main()
