"""Separate virtual-fill engine for prospective daily IBS overnight tracks.

Tracks 4A/4B are deliberately isolated from the frozen weekly state engine.
They virtually enter at a recorded close and mark/exit at the next session's
opening bar, so their ledger measures close-to-open only.  They never submit
orders and they are launch-gated until the user approves the dry run.
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone, time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import rebalance_engine as weekly_engine
import signals


HERE = Path(__file__).resolve().parent
STATE_DIR = HERE / "state"
LEDGER_DIR = HERE / "ledger"
TRACKS = ("ibs_daily_overnight_full", "ibs_daily_overnight_liquid")
ET = ZoneInfo("America/New_York")
ROUND_TRIP_COST_BPS = 10.0
DAILY_LEDGER_COLUMNS = [
    "record_date", "fill_date", "day_of_week", "nav_start", "nav_end",
    "gross_return", "net_return", "n_holdings", "target_replacement_pct",
    "overnight_turnover_pct", "modeled_cost_bps", "modeled_cost_usd",
    "annualized_cost_estimate_pct", "cost_flag", "fill_quality_avg",
    "fill_quality_median", "fill_data_unavailable", "overnight_gap_avg",
    "volume_ratio_avg", "universe_size", "post_exclusion_size",
    "exclusions_nsi", "exclusions_quality", "earnings_neutral_count",
]


def _atomic_write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def state_path(track: str) -> Path:
    _validate_track(track)
    return STATE_DIR / f"{track}_state.json"


def ledger_path(track: str) -> Path:
    _validate_track(track)
    return LEDGER_DIR / f"{track}.csv"


def _validate_track(track: str) -> None:
    if track not in TRACKS:
        raise ValueError(f"unknown daily overnight track: {track}")


def _track_config(cfg: dict, track: str) -> dict:
    _validate_track(track)
    return cfg["daily_overnight"]["tracks"][track]


def execution_audit(cfg: dict) -> dict:
    """Return the frozen execution timeline and its launch-validity verdict."""
    return dict(cfg["daily_overnight"]["execution_audit"])


def cost_sensitivities(cfg: dict) -> list[dict]:
    """Predeclared diagnostic costs; 10bp remains the primary assumption."""
    return [
        {"round_trip_bps": float(bps), "annualized_cost_pct": float(bps) / 10_000 * 252 * 100}
        for bps in cfg["daily_overnight"]["cost_sensitivity_bps_round_trip"]
    ]


def init_state(track: str, notional: float, inception: pd.Timestamp) -> dict:
    """Create an inactive state file; refuse to overwrite an existing run."""
    path = state_path(track)
    if path.exists():
        raise RuntimeError(f"refusing to overwrite existing prospective state: {path}")
    state = {
        "track": track,
        "inception_date": inception.date().isoformat(),
        "notional": float(notional),
        "cash": float(notional),
        "active_overnight": None,
        "last_target_symbols": [],
        "nav_history": [],
        "launch_authorized": False,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_write_json(path, state)
    return state


def load_state(track: str) -> dict:
    return json.loads(state_path(track).read_text(encoding="utf-8"))


def save_state(track: str, state: dict) -> None:
    _atomic_write_json(state_path(track), state)


def latest_available_session(cfg: dict) -> pd.Timestamp:
    """Latest close available in Sharadar, avoiding a dry run on a future date."""
    import sqlite3
    with sqlite3.connect(cfg["sharadar_db"]) as con:
        value = con.execute("SELECT MAX(date) FROM stocks").fetchone()[0]
    if value is None:
        raise RuntimeError("Sharadar stocks table has no dates")
    return pd.Timestamp(value)


def _as_of_panel(cfg: dict, as_of: pd.Timestamp) -> dict:
    return signals.load_live_panel(
        cfg["sharadar_db"], as_of, cfg["universe"]["exclude_fama_sectors"]
    )


def dry_run(track: str, cfg: dict, as_of: pd.Timestamp | None = None) -> dict:
    track_cfg = _track_config(cfg, track)
    as_of = as_of or latest_available_session(cfg)
    panel = _as_of_panel(cfg, as_of)
    result = signals.daily_overnight_ibs(panel, as_of, track_cfg["liquid_only"])
    held = result["held"].copy()
    if len(held):
        held["notional"] = cfg["portfolio"]["notional_usd"] / len(held)
    checks = []
    audit = execution_audit(cfg)
    checks.append(("execution timing is PIT-safe", audit["status"] != "LOOKAHEAD_INVALID",
                   f"{audit['status']}: {audit['reason']}"))
    expected = track_cfg["expected_pre_exclusion_universe"]
    checks.append(("pre-exclusion universe in registered range", expected[0] <= result["universe_size"] <= expected[1], f"n={result['universe_size']}, expected {expected[0]}-{expected[1]}"))
    # The preregistered selection is the top quintile *after* exclusions and
    # removal of earnings-event names.  A fixed 200/50 minimum would be
    # wrong on dates where those registered filters reduce the candidate
    # count; validate the actual quintile construction instead.
    tradable_count = int(result["scores"].tradable.sum())
    expected_holdings = max(1, tradable_count // 5)
    checks.append(("post-filter top-quintile count is exact", len(held) == expected_holdings,
                   f"held={len(held)}, expected={expected_holdings} of {tradable_count} tradable names"))
    if track_cfg["liquid_only"]:
        original_minimum = cfg["daily_overnight"]["liquid_count_amendment"]["original_minimum_holdings"]
        amended_minimum = cfg["daily_overnight"]["liquid_count_amendment"]["amended_minimum_holdings"]
        checks.append(("original preregistration 4B >=50 holding rule", len(held) >= original_minimum,
                       f"held={len(held)}, original floor={original_minimum} [SUPERSEDED_BY_AMENDMENT]"))
        checks.append(("amended 4B concentration floor", len(held) >= amended_minimum,
                       f"held={len(held)}, amended floor={amended_minimum}"))
    earnings = result["scores"].loc[result["scores"].earnings_event, "ibs"]
    checks.append(("earnings names are neutral at 0.50", bool((earnings == .50).all()), f"n={len(earnings)}"))
    excluded = result["excluded_nsi_q5"] | result["excluded_quality_q5"]
    checks.append(("no excluded name in portfolio", not bool(set(held.index) & excluded), f"overlap={sorted(set(held.index) & excluded)[:5]}"))
    checks.append(("no earnings name in overnight portfolio", not bool(held.earnings_event.any()), f"earnings={int(held.earnings_event.sum())}"))
    checks.append(("IBS p10 < 0.15", result["scores"].ibs.quantile(.1) < .15, f"p10={result['scores'].ibs.quantile(.1):.3f}"))
    checks.append(("IBS p90 > 0.85", result["scores"].ibs.quantile(.9) > .85, f"p90={result['scores'].ibs.quantile(.9):.3f}"))
    return {"track": track, "as_of": as_of, "result": result, "held": held, "checks": checks}


def _target_replacement(old: set[str], new: set[str]) -> float:
    if not old:
        return 1.0 if new else 0.0
    return len(old.symmetric_difference(new)) / (2 * max(len(old), len(new), 1))


def record_close_target(track: str, cfg: dict, as_of: pd.Timestamp) -> dict:
    """Create a close-entry virtual overnight position after launch approval."""
    state = load_state(track)
    if not state.get("launch_authorized", False) or not launch_authorized(cfg):
        raise RuntimeError("daily launch is not authorized; complete dry-run review and explicitly authorize first")
    if state.get("active_overnight"):
        raise RuntimeError(f"{track}: prior overnight has not been marked at the next open")
    report = dry_run(track, cfg, as_of)
    failures = [name for name, passed, _ in report["checks"] if not passed]
    if failures:
        raise RuntimeError(f"{track}: dry-run guards failed at record time: {failures}")
    held = report["held"]
    panel = _as_of_panel(cfg, as_of)
    closes = panel["C"].loc[as_of].reindex(held.index)
    if closes.isna().any() or (closes <= 0).any():
        raise RuntimeError(f"{track}: missing/non-positive close for {list(closes[closes.isna() | closes.le(0)].index)}")
    nav_start = float(state["cash"])
    target_value = nav_start
    shares = {symbol: float(target_value / len(held) / closes[symbol]) for symbol in held.index}
    cost = target_value * ROUND_TRIP_COST_BPS / 10_000
    old_target = set(state.get("last_target_symbols", []))
    state["cash"] = nav_start - target_value - cost
    state["active_overnight"] = {
        "record_date": as_of.date().isoformat(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "nav_start": nav_start,
        "target_value": target_value,
        "shares": shares,
        "close_prices": {symbol: float(closes[symbol]) for symbol in held.index},
        "target_replacement_pct": _target_replacement(old_target, set(held.index)),
        "volume_ratio_avg": float(held.volume_ratio.mean()),
        "universe_size": report["result"]["universe_size"],
        "post_exclusion_size": report["result"]["post_exclusion_size"],
        "exclusions_nsi": len(report["result"]["excluded_nsi_q5"]),
        "exclusions_quality": len(report["result"]["excluded_quality_q5"]),
        "earnings_neutral_count": report["result"]["earnings_neutral_count"],
    }
    state["last_target_symbols"] = sorted(held.index)
    save_state(track, state)
    return report | {"nav_start": nav_start, "modeled_cost_usd": cost, "state_path": state_path(track)}


def _opening_price(client, ticker: str, session: pd.Timestamp) -> tuple[float | None, str]:
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import Adjustment, DataFeed
        start = pd.Timestamp.combine(session.date(), clock_time(9, 30)).tz_localize(ET)
        request = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute, start=start, end=start + pd.Timedelta(minutes=10), feed=DataFeed.IEX, adjustment=Adjustment.RAW)
        bars = sorted(client.get_stock_bars(request).data.get(ticker, []), key=lambda bar: bar.timestamp)
        bars = [bar for bar in bars if bar.timestamp.astimezone(ET).time() >= clock_time(9, 30)]
        if bars:
            return float(bars[0].open), "OK"
    except Exception:
        pass
    return None, "DATA_UNAVAILABLE"


def _append_ledger(track: str, row: dict) -> None:
    path = ledger_path(track)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size:
        existing = pd.read_csv(path, usecols=["record_date"])
        if row["record_date"] in set(existing.record_date.astype(str)):
            raise RuntimeError(f"immutable ledger already has record_date {row['record_date']}")
    is_new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=DAILY_LEDGER_COLUMNS)
        if is_new:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in DAILY_LEDGER_COLUMNS})
        handle.flush()
        os.fsync(handle.fileno())


def apply_open_mark(track: str, client, session: pd.Timestamp) -> dict:
    """Mark and liquidate the prior close virtual position at the next open."""
    state = load_state(track)
    active = state.get("active_overnight")
    if not active:
        raise RuntimeError(f"{track}: no active overnight; run --record-daily after a close first")
    record_date = pd.Timestamp(active["record_date"])
    if session.normalize() <= record_date.normalize():
        raise RuntimeError("daily fill session must be after its close-entry session")
    values, quality, unavailable = [], [], 0
    for ticker, shares in active["shares"].items():
        opening, status = _opening_price(client, ticker, session)
        close = active["close_prices"][ticker]
        if opening is None:
            opening = close
            unavailable += 1
        values.append(shares * opening)
        quality.append(opening / close)
    nav_end = float(state["cash"] + sum(values))
    gross_nav_end = nav_end + active["target_value"] * ROUND_TRIP_COST_BPS / 10_000
    gross_return = gross_nav_end / active["nav_start"] - 1
    net_return = nav_end / active["nav_start"] - 1
    cost_pct = ROUND_TRIP_COST_BPS / 10_000 * 252 * 100
    cost_flag = "COST_DOMINATES" if cost_pct > 10 else ("COST_FLAG" if cost_pct > 5 else "")
    row = {
        "record_date": active["record_date"], "fill_date": session.date().isoformat(),
        "day_of_week": record_date.dayofweek, "nav_start": active["nav_start"], "nav_end": nav_end,
        "gross_return": gross_return, "net_return": net_return,
        "n_holdings": len(active["shares"]), "target_replacement_pct": active["target_replacement_pct"],
        "overnight_turnover_pct": 1.0, "modeled_cost_bps": ROUND_TRIP_COST_BPS,
        "modeled_cost_usd": active["target_value"] * ROUND_TRIP_COST_BPS / 10_000,
        "annualized_cost_estimate_pct": cost_pct, "cost_flag": cost_flag,
        "fill_quality_avg": sum(quality) / len(quality), "fill_quality_median": float(pd.Series(quality).median()),
        "fill_data_unavailable": unavailable, "overnight_gap_avg": sum(value - 1 for value in quality) / len(quality),
        "volume_ratio_avg": active["volume_ratio_avg"], "universe_size": active["universe_size"],
        "post_exclusion_size": active["post_exclusion_size"], "exclusions_nsi": active["exclusions_nsi"],
        "exclusions_quality": active["exclusions_quality"], "earnings_neutral_count": active["earnings_neutral_count"],
    }
    _append_ledger(track, row)
    state["cash"] = nav_end
    state["active_overnight"] = None
    state["nav_history"].append({"date": session.date().isoformat(), "nav": nav_end, "net_return": net_return})
    save_state(track, state)
    return {"track": track, "session": session, "row": row, "nav": nav_end}


def launch_authorized(cfg: dict) -> bool:
    return bool(cfg.get("daily_overnight", {}).get("launch_authorized", False)) and execution_audit(cfg)["status"] != "LOOKAHEAD_INVALID"
