"""Append-only prospective regime labels for the live Optimized DM parent.

Observation only: this module reads completed account sessions and daily bars.
It has no brokerage/order imports and cannot affect execution.

**RETIRED 2026-09-06.** Surplus forward shadow from the Dual Momentum
research line, which closed 2026-08-11 (LESSONS.md: "Dual Momentum is
momentum, not an edge"). `api/main.py` no longer calls
`record_completed_sessions`, so this ledger stops advancing here; its
historical observations were not deleted. See
`logs/dm_regime_forward_v1.RETIRED.json` for the machine-readable marker
(a sidecar file, not a schema change to `DB_PATH`).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pandas as pd

from engine import data as data_module, prop_forward
from engine.dm_regime_dependence import _condition_frame, _normalize_series

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "logs" / "dm_regime_forward_v1.db"
PREREGISTRATION = ROOT / "research" / "dm_regime_dependence_v1_preregistration.json"
RELATIVE_OUTCOME_PREREGISTRATION = ROOT / "research" / "dm_spy_underperformance_v1_preregistration.json"
CUTOFF = date(2026, 8, 26)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS program_metadata (
 key TEXT PRIMARY KEY, preregistration_sha256 TEXT NOT NULL,
 strategy_fingerprint TEXT, created_at TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS daily_observations (
 session_date TEXT PRIMARY KEY, recorded_at TEXT NOT NULL,
 parent_strategy_fingerprint TEXT NOT NULL, account_return REAL,
 return_source TEXT NOT NULL, spy_trend INTEGER NOT NULL,
 market_volatility INTEGER NOT NULL, cross_sectional_dispersion INTEGER NOT NULL,
 conditions_json TEXT NOT NULL, evidence_class TEXT NOT NULL
);
"""


def get_connection(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(_SCHEMA)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(daily_observations)")}
    additions = {
        "spy_return": "REAL", "active_return": "REAL", "absolute_loss": "INTEGER",
        "underperformed_spy": "INTEGER", "joint_damaging": "INTEGER",
    }
    for column, sql_type in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE daily_observations ADD COLUMN {column} {sql_type}")
    conn.commit()
    conn.row_factory = sqlite3.Row
    return conn


def _prereg() -> dict[str, Any]:
    return json.loads(PREREGISTRATION.read_text(encoding="utf-8"))


def _symbols() -> list[str]:
    return list(_prereg()["strategies"][0].get("universe") or [
        "AAMI", "ACIW", "AEO", "ALG", "AROC", "AWI", "BCPC", "BLFS", "BXMT", "CBRL",
        "CFFN", "CRC", "CXW", "DFIN", "EAT", "ENPH", "EVTC", "FCPT", "FORM", "GEO",
        "GRBK", "HCC", "HOPE", "HZO", "INVA", "JBSS",
    ])


def _labels(dates: list[date]) -> dict[str, dict[str, bool]]:
    start, end = min(dates) - timedelta(days=180), max(dates)
    bars = {symbol: data_module.get_bars(symbol, "1d", start, end) for symbol in _symbols()}
    dummy = SimpleNamespace(validation_bars=bars, membership_at_runtime=None)
    index = pd.DatetimeIndex(pd.to_datetime(dates))
    conditions, _ = _condition_frame(dummy, index)
    if conditions.isna().any().any():
        missing = conditions.columns[conditions.isna().any()].tolist()
        raise RuntimeError(f"Prospective regime labels lack required warmup: {missing}")
    return {
        day.date().isoformat(): {column: bool(conditions.loc[day, column]) for column in conditions}
        for day in conditions.index
    }


def _spy_returns(dates: list[date]) -> dict[str, float]:
    bars = data_module.get_bars("SPY", "1d", min(dates) - timedelta(days=10), max(dates))
    if bars.empty:
        raise RuntimeError("SPY bars unavailable for prospective active-return calculation.")
    returns = _normalize_series(bars["Close"]).pct_change(fill_method=None)
    result = {}
    for day in dates:
        stamp = pd.Timestamp(day)
        if stamp not in returns.index or pd.isna(returns.loc[stamp]):
            raise RuntimeError(f"Aligned SPY return unavailable for completed session {day.isoformat()}.")
        result[day.isoformat()] = float(returns.loc[stamp])
    return result


def record_completed_sessions(
    *, source_path: Path = prop_forward.DB_PATH, output_path: Path = DB_PATH,
    label_builder: Callable[[list[date]], dict[str, dict[str, bool]]] = _labels,
    spy_return_builder: Callable[[list[date]], dict[str, float]] = _spy_returns,
) -> dict[str, Any]:
    """Append eligible completed sessions once; never backfill pre-cutoff days."""
    if not source_path.exists():
        return {"status": "source_not_started", "appendedSessions": 0}
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    try:
        sessions = source.execute(
            "SELECT * FROM session_outcomes WHERE session_date>? ORDER BY session_date", (CUTOFF.isoformat(),)
        ).fetchall()
    except sqlite3.Error:
        source.close()
        return {"status": "source_not_started", "appendedSessions": 0}
    target = get_connection(output_path)
    existing = {row[0] for row in target.execute("SELECT session_date FROM daily_observations")}
    pending = [row for row in sessions if row["session_date"] not in existing]
    fingerprint_row = source.execute(
        "SELECT strategy_fingerprint FROM raw_equity_snapshots ORDER BY id DESC LIMIT 1"
    ).fetchone()
    source.close()
    if fingerprint_row is None:
        target.close()
        return {"status": "fingerprint_unavailable", "appendedSessions": 0}
    fingerprint = str(fingerprint_row["strategy_fingerprint"])
    digest = hashlib.sha256(PREREGISTRATION.read_bytes()).hexdigest()
    relative_digest = hashlib.sha256(RELATIVE_OUTCOME_PREREGISTRATION.read_bytes()).hexdigest()
    metadata = target.execute("SELECT * FROM program_metadata WHERE key='dm_regime_forward_v1'").fetchone()
    if metadata is not None and (
        metadata["preregistration_sha256"] != digest or metadata["strategy_fingerprint"] != fingerprint
    ):
        target.close()
        raise RuntimeError("Regime-forward preregistration or parent fingerprint changed.")
    relative_metadata = target.execute(
        "SELECT * FROM program_metadata WHERE key='dm_spy_underperformance_forward_v1'"
    ).fetchone()
    if relative_metadata is not None and (
        relative_metadata["preregistration_sha256"] != relative_digest
        or relative_metadata["strategy_fingerprint"] != fingerprint
    ):
        target.close()
        raise RuntimeError("SPY-relative forward preregistration or parent fingerprint changed.")
    now = datetime.now(timezone.utc).isoformat()
    with target:
        if metadata is None:
            target.execute(
                "INSERT INTO program_metadata VALUES (?,?,?,?,?)",
                ("dm_regime_forward_v1", digest, fingerprint, now,
                json.dumps({"cutoff": CUTOFF.isoformat(), "orders": False}, sort_keys=True)),
            )
        if relative_metadata is None:
            target.execute(
                "INSERT INTO program_metadata VALUES (?,?,?,?,?)",
                ("dm_spy_underperformance_forward_v1", relative_digest, fingerprint, now,
                 json.dumps({"cutoff": CUTOFF.isoformat(), "orders": False,
                             "outcome": "account_return_minus_aligned_spy_return"}, sort_keys=True)),
            )
    if not pending:
        target.close()
        return {"status": "up_to_date", "appendedSessions": 0}
    labels = label_builder([date.fromisoformat(row["session_date"]) for row in pending])
    spy_returns = spy_return_builder([date.fromisoformat(row["session_date"]) for row in pending])
    with target:
        for row in pending:
            session = row["session_date"]
            conditions = labels[session]
            total_pl = row["total_pl"]
            closing = float(row["closing_equity"])
            if total_pl is not None and closing - float(total_pl) != 0:
                account_return = float(total_pl) / (closing - float(total_pl))
                return_source = "alpaca_session_pl_over_inferred_prior_close_equity"
            else:
                opening = float(row["opening_equity"])
                account_return = closing / opening - 1.0 if opening else None
                return_source = "first_to_last_intraday_snapshot_approximation"
            spy_return = spy_returns[session]
            active_return = None if account_return is None else account_return - spy_return
            absolute_loss = None if account_return is None else int(account_return < 0)
            underperformed = None if active_return is None else int(active_return < 0)
            joint_damaging = None if absolute_loss is None else int(bool(absolute_loss) and bool(underperformed))
            target.execute(
                """INSERT INTO daily_observations
                (session_date, recorded_at, parent_strategy_fingerprint, account_return,
                 return_source, spy_trend, market_volatility, cross_sectional_dispersion,
                 conditions_json, evidence_class, spy_return, active_return, absolute_loss,
                 underperformed_spy, joint_damaging)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (session, now, fingerprint, account_return, return_source,
                 int(conditions["spy_trend"]), int(conditions["market_volatility"]),
                 int(conditions["cross_sectional_dispersion"]), json.dumps(conditions, sort_keys=True),
                 "prospective_execution_observed_parent", spy_return, active_return, absolute_loss,
                 underperformed, joint_damaging),
            )
    target.close()
    return {"status": "appended", "appendedSessions": len(pending), "lastSession": pending[-1]["session_date"]}


def status(path: Path = DB_PATH) -> dict[str, Any]:
    conn = get_connection(path)
    count = int(conn.execute("SELECT COUNT(*) FROM daily_observations").fetchone()[0])
    latest = conn.execute("SELECT * FROM daily_observations ORDER BY session_date DESC LIMIT 1").fetchone()
    metadata = conn.execute("SELECT * FROM program_metadata WHERE key='dm_regime_forward_v1'").fetchone()
    relative_metadata = conn.execute(
        "SELECT * FROM program_metadata WHERE key='dm_spy_underperformance_forward_v1'"
    ).fetchone()
    conn.close()
    return {
        "program": "Optimized DM Prospective Regime Labels v1",
        "evidenceClass": "prospective_only",
        "cutoff": CUTOFF.isoformat(),
        "observations": count,
        "lastSession": None if latest is None else latest["session_date"],
        "lastObservation": None if latest is None else dict(latest),
        "fingerprintLocked": metadata is not None,
        "relativeOutcomePreregistrationLocked": relative_metadata is not None,
        "ordersAllowed": False,
    }
