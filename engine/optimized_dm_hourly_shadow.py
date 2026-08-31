"""Hourly, observation-only shadow of the locked Optimized Dual Momentum parent.

The parent ranks once per completed daily bar.  This variant preserves its
63-session lookback, top-five selection, universe, and positive absolute-
momentum filter, but re-ranks after every completed 60-minute bar and applies
the target at the next hourly bar's open.  It contains no broker/order code.

Rows before ``activatedAt`` are retained as a blind pre-activation backfill:
the user requested the rule before inspecting those results, but the ledger
did not exist when the hours occurred, so they are not prospective evidence.
Only later rows are counted as prospective shadow observations.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from engine import data as data_module, execution_db
from engine.strategy_identity import execution_fingerprint

NY = ZoneInfo("America/New_York")
LEDGER_PATH = Path(__file__).resolve().parent.parent / "logs" / "dm_optimized_63d_hourly_shadow.json"
STARTING_EQUITY = 100_000.0
SOURCE_INTERVAL = "30m"
ENGINE_VERSION = 2


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, allow_nan=False)
            handle.flush(); os.fsync(handle.fileno())
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read(path: Path = LEDGER_PATH) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _parent_config() -> dict[str, Any]:
    row = execution_db.automation_config().get("Dual Momentum")
    if row is None:
        raise RuntimeError("The Optimized Dual Momentum parent configuration is missing")
    params = json.loads(row["params"] or "{}")
    symbols = json.loads(row["symbols"] or "[]")
    lookback = int(params.get("lookback_trading_days", 189))
    top_n = int(params.get("top_n", 5))
    if lookback != 63 or str(params.get("rebalance_frequency")) != "daily":
        raise RuntimeError("The parent is no longer the locked Optimized 63D/Daily configuration")
    if not symbols:
        raise RuntimeError("The Optimized Dual Momentum parent universe is empty")
    fingerprint, payload = execution_fingerprint(
        "Dual Momentum", params, symbols, row["validation_run_id"],
    )
    if not payload["identity"].get("fingerprintMatches"):
        raise RuntimeError("The parent strategy fingerprint no longer matches validation run 33")
    inception = execution_db.inception_for("Dual Momentum")
    if inception.get("status") != "initialized" or not inception.get("inceptionAt"):
        raise RuntimeError("The parent paper-forward inception has not been initialized")
    return {
        "lookbackSessions": lookback, "topN": top_n, "symbols": symbols,
        "parentFingerprint": fingerprint, "validationRunId": row["validation_run_id"],
        "parentInceptionAt": inception["inceptionAt"],
    }


def _timestamp(value: str | datetime) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tzinfo is None:
        return stamp.tz_localize(NY)
    return stamp.tz_convert(NY)


def simulate(
    bars_by_symbol: dict[str, pd.DataFrame], *, start_at: str | datetime,
    lookback_sessions: int = 63, top_n: int = 5,
    starting_equity: float = STARTING_EQUITY,
) -> list[dict[str, Any]]:
    """Replay hourly decisions with a strict prior-bar information boundary."""
    usable = {
        symbol: bars.sort_index()
        for symbol, bars in bars_by_symbol.items()
        if bars is not None and not bars.empty
    }
    if not usable:
        return []
    calendar = pd.DatetimeIndex(sorted(set().union(*(frame.index for frame in usable.values()))))
    if calendar.tz is None:
        calendar = calendar.tz_localize(NY)
    else:
        calendar = calendar.tz_convert(NY)
    close_df = pd.DataFrame({symbol: frame["Close"] for symbol, frame in usable.items()}).reindex(calendar).ffill()
    open_df = pd.DataFrame({symbol: frame["Open"] for symbol, frame in usable.items()}).reindex(calendar)
    start = _timestamp(start_at)
    shares: dict[str, float] = {}
    cash = float(starting_equity)
    rows: list[dict[str, Any]] = []

    for index, execution_bar in enumerate(calendar):
        # 30-minute source bars let the hourly clock anchor to the 09:30
        # market open. Rebalance at 10:30, 11:30, ..., 15:30; the execution
        # bar then marks the new holdings through its close, including the
        # final 15:30-16:00 half-hour without pretending it is a full hour.
        if (index == 0 or execution_bar < start or execution_bar.minute != 30
                or execution_bar.time() < time(10, 30)):
            continue
        decision_bar = calendar[index - 1]
        sessions = list(dict.fromkeys(stamp.date() for stamp in calendar[:index]))
        if len(sessions) < lookback_sessions + 1:
            continue
        base_session = sessions[-lookback_sessions - 1]
        base_rows = calendar[:index][[stamp.date() == base_session for stamp in calendar[:index]]]
        if len(base_rows) == 0:
            continue
        base_bar = base_rows[-1]
        scores: dict[str, float] = {}
        for symbol in usable:
            now_px = close_df.at[decision_bar, symbol]
            base_px = close_df.at[base_bar, symbol]
            if pd.notna(now_px) and pd.notna(base_px) and float(base_px) > 0:
                score = float(now_px) / float(base_px) - 1.0
                if score > 0:
                    scores[symbol] = score
        selected = sorted(scores, key=scores.get, reverse=True)[:top_n]
        targets = ({symbol: 1.0 / len(selected) for symbol in selected} if selected else {})

        portfolio_at_open = cash
        for symbol, qty in shares.items():
            px = open_df.at[execution_bar, symbol] if symbol in open_df else float("nan")
            if pd.notna(px):
                portfolio_at_open += qty * float(px)
        prior_values = {
            symbol: qty * float(open_df.at[execution_bar, symbol])
            for symbol, qty in shares.items()
            if symbol in open_df and pd.notna(open_df.at[execution_bar, symbol])
        }
        traded_notional = 0.0
        for symbol in list(shares):
            if symbol not in targets:
                px = open_df.at[execution_bar, symbol]
                if pd.notna(px):
                    value = shares.pop(symbol) * float(px)
                    cash += value; traded_notional += abs(value)
        for symbol, weight in targets.items():
            px = open_df.at[execution_bar, symbol] if symbol in open_df else float("nan")
            if pd.isna(px) or float(px) <= 0:
                continue
            target_value = portfolio_at_open * weight
            current_value = prior_values.get(symbol, 0.0)
            delta_value = target_value - current_value
            shares[symbol] = shares.get(symbol, 0.0) + delta_value / float(px)
            cash -= delta_value; traded_notional += abs(delta_value)

        close_equity = cash
        position_values: dict[str, float] = {}
        for symbol, qty in shares.items():
            px = close_df.at[execution_bar, symbol]
            if pd.notna(px):
                value = qty * float(px)
                close_equity += value; position_values[symbol] = value
        holdings = {
            symbol: value / close_equity for symbol, value in position_values.items()
        } if close_equity else {}
        rows.append({
            "timestamp": execution_bar.isoformat(), "sessionDate": execution_bar.date().isoformat(),
            "decisionBar": decision_bar.isoformat(), "equity": float(close_equity),
            "cash": float(cash), "holdings": holdings, "scores": {s: scores[s] for s in selected},
            "turnover": traded_notional / portfolio_at_open if portfolio_at_open else 0.0,
        })
    return rows


def _bars(config: dict[str, Any], now: datetime) -> dict[str, pd.DataFrame]:
    inception_day = _timestamp(config["parentInceptionAt"]).date()
    # 63 trading sessions plus generous holiday/weekend slack.
    fetch_start = inception_day - timedelta(days=120)
    fetch_end = now.astimezone(NY).date() + timedelta(days=1)
    return {
        symbol: data_module.get_bars(symbol, SOURCE_INTERVAL, fetch_start, fetch_end)
        for symbol in config["symbols"]
    }


def advance(
    *, now: datetime | None = None, path: Path = LEDGER_PATH,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create the blind backfill once, then append new prospective hourly marks."""
    now = now or datetime.now(NY)
    config = config or _parent_config()
    existing = _read(path)
    if existing is None:
        existing = {
            "strategyKey": "dm_optimized_63d_hourly", "strategyName": "Dual Momentum · Optimized 63D/Hourly",
            "activatedAt": now.astimezone(NY).isoformat(timespec="seconds"),
            "backfillIntent": "Specified before inspecting backfilled results",
            "backfillStart": _timestamp(config["parentInceptionAt"]).date().isoformat(),
            "startingEquity": STARTING_EQUITY, "config": config, "rows": [], "lastError": None,
            "engineVersion": ENGINE_VERSION,
        }
        _atomic_write(path, existing)  # activation boundary exists before results are calculated
    elif existing["config"]["parentFingerprint"] != config["parentFingerprint"]:
        raise RuntimeError("Hourly shadow parent fingerprint changed; refusing to splice evidence")

    try:
        completed_cutoff = _timestamp(now) - pd.Timedelta(minutes=30)
        invalid = [row for row in existing["rows"] if _timestamp(row["timestamp"]) > completed_cutoff]
        if invalid:
            existing["rows"] = [row for row in existing["rows"] if _timestamp(row["timestamp"]) <= completed_cutoff]
            existing.setdefault("corrections", []).append({
                "recordedAt": now.astimezone(NY).isoformat(timespec="seconds"),
                "reason": "Removed a provider-supplied hourly bar before its full 60-minute interval had elapsed",
                "timestamps": [row["timestamp"] for row in invalid],
            })
        source_bars = bars_by_symbol or _bars(config, now)
        source_bars = {
            symbol: frame.loc[frame.index <= completed_cutoff]
            for symbol, frame in source_bars.items()
        }
        replay = simulate(
            source_bars, start_at=config["parentInceptionAt"],
            lookback_sessions=int(config["lookbackSessions"]), top_n=int(config["topN"]),
            starting_equity=float(existing["startingEquity"]),
        )
        if int(existing.get("engineVersion", 1)) < ENGINE_VERSION:
            if any(row.get("evidenceClass") == "prospective_shadow" for row in existing["rows"]):
                raise RuntimeError("Hourly engine correction cannot rewrite prospective observations")
            existing.setdefault("corrections", []).append({
                "recordedAt": now.astimezone(NY).isoformat(timespec="seconds"),
                "reason": "Rebuilt the blind backfill from 30-minute bars so hourly decisions anchor to the 09:30 market open and include the closing half-hour",
                "priorRows": len(existing["rows"]),
            })
            existing["rows"] = []
            existing["engineVersion"] = ENGINE_VERSION
        prior = {row["timestamp"]: row for row in existing["rows"]}
        for row in replay:
            recorded = prior.get(row["timestamp"])
            if recorded and abs(float(recorded["equity"]) - float(row["equity"])) > 0.01:
                raise RuntimeError(f"Historical hourly replay mutation at {row['timestamp']}")
        last_timestamp = existing["rows"][-1]["timestamp"] if existing["rows"] else None
        additions = [row for row in replay if last_timestamp is None or row["timestamp"] > last_timestamp]
        activation = _timestamp(existing["activatedAt"])
        for row in additions:
            row["evidenceClass"] = (
                "prospective_shadow" if _timestamp(row["timestamp"]) > activation
                else "blind_pre_activation_backfill"
            )
        existing["rows"].extend(additions)
        existing["lastAttemptAt"] = now.astimezone(NY).isoformat(timespec="seconds")
        existing["lastSuccessAt"] = existing["lastAttemptAt"]
        existing["lastError"] = None
        _atomic_write(path, existing)
        return {"status": "advanced" if additions else "up_to_date", "appendedMarks": len(additions)}
    except Exception as exc:
        existing["lastAttemptAt"] = now.astimezone(NY).isoformat(timespec="seconds")
        existing["lastError"] = f"{type(exc).__name__}: {exc}"
        _atomic_write(path, existing)
        raise


def status(path: Path = LEDGER_PATH) -> dict[str, Any]:
    ledger = _read(path)
    if ledger is None:
        return {"available": False, "reason": "Hourly shadow has not been activated", "key": "dm_optimized_63d_hourly"}
    rows = ledger.get("rows") or []
    latest = rows[-1] if rows else None
    daily: list[dict[str, Any]] = []
    in_progress = None
    today = datetime.now(NY).date()
    for session in dict.fromkeys(row["sessionDate"] for row in rows):
        marks = [row for row in rows if row["sessionDate"] == session]
        close = marks[-1]
        session_day = date.fromisoformat(session)
        settled = session_day < today or (
            session_day == today and _timestamp(close["timestamp"]).time() >= time(15, 0)
            and datetime.now(NY).time() >= time(16, 0)
        )
        point = {"date": session, "equity": close["equity"], "evidenceClass": close["evidenceClass"]}
        if settled:
            daily.append(point)
        else:
            in_progress = {**point, "timestamp": close["timestamp"]}
    prospective = [row for row in rows if row["evidenceClass"] == "prospective_shadow"]
    backfill = [row for row in rows if row["evidenceClass"] == "blind_pre_activation_backfill"]
    start = float(ledger["startingEquity"])
    current = float(latest["equity"]) if latest else start
    return {
        "available": True, "key": ledger["strategyKey"], "strategy": ledger["strategyName"],
        "activatedAt": ledger["activatedAt"], "backfillStart": ledger["backfillStart"],
        "backfillIntent": ledger["backfillIntent"], "startingEquity": start,
        "currentEquity": current, "returnPct": (current / start - 1) * 100,
        "lastUpdate": latest["timestamp"] if latest else None,
        "currentHoldings": latest["holdings"] if latest else {}, "dailyHistory": daily,
        "inProgress": in_progress,
        "positionHistory": [
            {"date": row["timestamp"], "holdings": row["holdings"],
             "source": row["evidenceClass"]}
            for index, row in enumerate(rows)
            if index == 0 or row["holdings"] != rows[index - 1]["holdings"]
        ],
        "backfillMarks": len(backfill), "prospectiveMarks": len(prospective),
        "backfillSessions": sum(row["evidenceClass"] == "blind_pre_activation_backfill" for row in daily),
        "prospectiveSessions": sum(row["evidenceClass"] == "prospective_shadow" for row in daily),
        "totalTurnoverPct": sum(float(row["turnover"]) for row in rows) * 100,
        "lastAttemptAt": ledger.get("lastAttemptAt"), "lastSuccessAt": ledger.get("lastSuccessAt"),
        "lastError": ledger.get("lastError"),
        "corrections": ledger.get("corrections") or [],
        "methodology": {
            "ranking": "63 trading-session momentum ending at the prior completed hourly bar",
            "selection": "Top 5 positive-momentum names from the locked 26-name parent universe",
            "execution": "Re-rank on market-open-anchored hourly intervals; apply at 10:30, 11:30, ..., 15:30 using 30-minute source bars",
            "orders": "None — observation-only synthetic shadow",
            "costs": "Gross simulation; turnover reported separately; no slippage/fees deducted",
        },
    }
