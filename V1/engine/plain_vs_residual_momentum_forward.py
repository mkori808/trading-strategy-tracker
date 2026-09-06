"""Forward-shadow tracker for Plain Momentum (C) vs Market-Residual Momentum (D).

See research/plain_vs_residual_momentum_forward_preregistration.json -- read
that first. This is the prospective counterpart to the historical
research/mrm_attribution_ladder_preregistration.json study: same C and D,
same universe, same cost model, but observed forward in real time instead
of replayed once over a fixed historical window.

Every C/D return this module records comes from calling
`engine.mrm_attribution_ladder.compute_rungs()` UNCHANGED -- the exact same
computation the historical ladder study already uses. This module never
redefines `PlainMomentumControl` or touches `MarketResidualMomentum`; it only
adds provenance, persistence, checkpoint-gating, and reporting on top,
mirroring the shape (append-only, atomic, no-lookahead, reconciled-not-
rewritten) already proven necessary for `engine/dm_mrm_forward.py`'s DM/MRM
ledger. Utilities with no DM/MRM-specific logic (`_atomic_json_write`,
`_read_json`, `_plain_daily`, `latest_completed_session`) are imported from
that module rather than duplicated, so a fix to the shared Windows/OneDrive
write-retry path can never silently diverge between the two ledgers.

**Paper/research only.** Nothing here places an order or touches live
automation -- Plain Momentum is not even a registered, tradable strategy
(see `PlainMomentumControl`'s own docstring).

**RETIRED 2026-09-06.** Surplus forward shadow from the Dual Momentum /
Market-Residual Momentum research line, which closed 2026-08-11
(LESSONS.md: "Dual Momentum is momentum, not an edge"). `api/main.py` no
longer calls `advance_completed_sessions`, so this ledger stops advancing
here; its historical observations were not deleted. See
``logs/plain_vs_residual_forward_operations.json``'s top-level ``retired``
key.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from engine.dm_mrm_forward import (
    _atomic_json_write,
    _plain_daily,
    _read_json,
    latest_completed_session,
)
from engine.mrm_attribution_ladder import compute_rungs
from engine.regime import classify as regime_classify

DEVELOPMENT_CUTOFF = date(2026, 8, 31)
NY = ZoneInfo("America/New_York")

NAV_SERIES_KEYS = ("c", "d", "spy")
NAV_BASE = 100.0
NAV_SERIES_TOLERANCE: dict[str, float] = {"c": 1e-8, "d": 1e-8, "spy": 1e-8}

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
NAV_PATH = LOGS_DIR / "plain_vs_residual_forward_nav.json"
REBALANCES_PATH = LOGS_DIR / "plain_vs_residual_forward_rebalances.json"
AMENDMENTS_PATH = LOGS_DIR / "plain_vs_residual_forward_amendments.json"
OPERATIONS_PATH = LOGS_DIR / "plain_vs_residual_forward_operations.json"
PREREGISTRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "research" / "plain_vs_residual_momentum_forward_preregistration.json"
)

_ADVANCE_LOCK = Lock()

#: Calendar-month checkpoints, per the preregistration -- deliberately NOT
#: session-count-based (that's engine/dm_mrm_forward.py's 20/63/126/252
#: ladder for a different pair). Kept as a distinct constant on purpose: the
#: two forward ledgers are allowed to use different review cadences without
#: either one silently borrowing the other's.
CHECKPOINTS_MONTHS: tuple[tuple[int, str], ...] = (
    (6, "First descriptive checkpoint"),
    (12, "First checkpoint eligible for a real comparison against preregistered expectations"),
    (24, "Second full comparison checkpoint"),
)

#: Minimum PAIRED rebalance periods required before a checkpoint's
#: comparison is treated as more than "inconclusive" -- matches the
#: preregistration's interpretationRule exactly.
MIN_PAIRED_PERIODS_AT_CHECKPOINT: dict[int, int] = {6: 6, 12: 10, 24: 20}

#: Sessions needed before Sharpe/Sortino are computed at all -- same
#: "no unstable annualized number without a loud withheld label" discipline
#: as engine/dm_mrm_forward.py:MIN_SESSIONS_FOR_ANNUALIZED, independently
#: set here since this ledger's session cadence is not guaranteed identical.
MIN_SESSIONS_FOR_ANNUALIZED = 252


# --- realized rebalance record ----------------------------------------------


@dataclass(frozen=True)
class RebalanceRecord:
    """One completed rebalance period's realized outcome for both C and D,
    immutable once recorded. `*PeriodReturnPct` is the REALIZED return from
    this rebalance date to the next (a still-open final period is never
    backfilled here -- see `advance_completed_sessions`).

    Per-rebalance cost/turnover is NOT recorded on this row: `engine.
    cross_sectional.run_cross_sectional_backtest` only returns a single
    cumulative `total_costs` for the whole replayed window, not a
    per-rebalance breakdown, and fabricating a per-period split from that
    would be exactly the kind of invented number this project's conventions
    prohibit. Cumulative cost since this ledger's setup is tracked
    instead, at the operations-state level (`cumulativeCostsSinceSetup`
    in operations.json, surfaced via the scorecard's
    `cumulativeCostsSinceSetup`) -- real, computed, just not attributable
    to one period without a change to the shared backtest engine this
    module does not make."""

    rebalanceDate: str
    regime: str | None
    cHoldings: dict
    dHoldings: dict
    cPeriodReturnPct: float
    dPeriodReturnPct: float
    recordedAt: str

    def to_dict(self) -> dict:
        return asdict(self)


def record_rebalance(record: RebalanceRecord, path: Path = REBALANCES_PATH) -> None:
    """Append-only. Refuses a duplicate date and refuses anything on or
    before the development cutoff."""
    day = date.fromisoformat(record.rebalanceDate)
    if day <= DEVELOPMENT_CUTOFF:
        raise ValueError(
            f"{record.rebalanceDate} is on or before the development cutoff "
            f"({DEVELOPMENT_CUTOFF.isoformat()}) and cannot enter the forward ledger."
        )
    existing = _read_json(path, [])
    if any(row["rebalanceDate"] == record.rebalanceDate for row in existing):
        raise ValueError(
            f"A rebalance record for {record.rebalanceDate} is already recorded. "
            "Rebalance records are append-only and cannot be revised."
        )
    existing.append(record.to_dict())
    _atomic_json_write(path, existing)


def load_rebalances(path: Path = REBALANCES_PATH) -> list[dict]:
    return _read_json(path, [])


# --- amendments (disclosed, append-only corrections) ------------------------


@dataclass(frozen=True)
class ForwardAmendment:
    amendmentId: str
    recordedAt: str
    reasonCode: str
    explanation: str
    evidence: dict
    replacements: list[dict]
    status: str = "confirmed"

    def to_dict(self) -> dict:
        return asdict(self)


def record_amendment(amendment: ForwardAmendment, path: Path = AMENDMENTS_PATH) -> None:
    if amendment.status != "confirmed":
        raise ValueError("Only confirmed amendments may alter the effective reporting view.")
    existing = _read_json(path, [])
    if any(row["amendmentId"] == amendment.amendmentId for row in existing):
        raise ValueError(f"Amendment {amendment.amendmentId} is already recorded.")
    for replacement in amendment.replacements:
        if date.fromisoformat(replacement["date"]) <= DEVELOPMENT_CUTOFF:
            raise ValueError("An amendment cannot target development-period data.")
        if replacement["series"] not in NAV_SERIES_KEYS:
            raise ValueError(f"Unknown NAV series {replacement['series']!r}.")
    existing.append(amendment.to_dict())
    _atomic_json_write(path, existing)


def load_amendments(path: Path = AMENDMENTS_PATH) -> list[dict]:
    return _read_json(path, [])


# --- NAV ledger (levels, base 100 on the first accepted forward session) ---


def append_nav_level_row(session_date: date, levels: dict[str, float], path: Path = NAV_PATH) -> dict:
    if session_date <= DEVELOPMENT_CUTOFF:
        raise ValueError("A forward NAV level must be strictly after the development cutoff.")
    existing = _read_json(path, [])
    key = session_date.isoformat()
    if any(row["date"] == key for row in existing):
        raise ValueError(f"A forward NAV row for {session_date} is already recorded.")
    if existing and key <= existing[-1]["date"]:
        raise ValueError("Forward NAV level rows must append chronologically.")
    row = {"date": key, **{series: float(levels[series]) for series in NAV_SERIES_KEYS}}
    existing.append(row)
    _atomic_json_write(path, existing)
    return row


def load_forward_nav(path: Path = NAV_PATH) -> pd.DataFrame:
    rows = _read_json(path, [])
    if not rows:
        return pd.DataFrame(columns=["date", *NAV_SERIES_KEYS])
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date").sort_index()


def load_effective_forward_nav(nav_path: Path = NAV_PATH, amendments_path: Path = AMENDMENTS_PATH) -> pd.DataFrame:
    nav = load_forward_nav(nav_path)
    for amendment in load_amendments(amendments_path):
        if amendment.get("status") != "confirmed":
            continue
        for replacement in amendment.get("replacements", []):
            day = pd.Timestamp(replacement["date"])
            series = replacement["series"]
            if day not in nav.index:
                raise RuntimeError(f"Amendment targets missing session {replacement['date']}")
            stored = float(nav.loc[day, series])
            expected_prior = float(replacement["priorValue"])
            if abs(stored - expected_prior) > 1e-10:
                raise RuntimeError(f"Amendment prior-value guard failed for {replacement['date']} {series}")
            nav.loc[day, series] = float(replacement["replacementValue"])
    return nav


# --- replay: reuse compute_rungs() unchanged, clip to completed sessions ---


def _source_paths(latest_completed: date) -> dict[str, Any]:
    """Replay the unchanged C/D computation and derive forward-only,
    completed-session-only levels.

    `compute_rungs()` always fetches through `date.today()` internally (it
    has no `end` override -- see its module for why: it is a diagnostic,
    not a forward tracker, and was never meant to need one). Rather than
    modify that shared, already-frozen-adjacent function, this clips its
    output to `latest_completed` itself, discarding anything from an
    in-progress session the same way `engine.dm_mrm_forward.latest_completed_session`
    already decides what counts as safely complete.
    """
    computed = compute_rungs()
    result_c, result_d = computed["result_c"], computed["result_d"]
    spy_equity = computed["spy_equity"]

    c_equity = _plain_daily(result_c.equity_curve)
    d_equity = _plain_daily(result_d.equity_curve)
    spy_close = _plain_daily(spy_equity)
    idx = c_equity.index.intersection(d_equity.index).intersection(spy_close.index)
    idx = idx[idx.date <= latest_completed].sort_values()
    if not len(idx):
        raise RuntimeError("No completed sessions available in the replayed window")
    c_equity, d_equity, spy_close = c_equity.loc[idx], d_equity.loc[idx], spy_close.loc[idx]

    forward_idx = idx[idx.date > DEVELOPMENT_CUTOFF]
    levels = pd.DataFrame(index=forward_idx)
    raw = {"c": c_equity, "d": d_equity, "spy": spy_close}
    for key, series in raw.items():
        values = series.loc[forward_idx]
        if len(values):
            levels[key] = values / float(values.iloc[0]) * NAV_BASE

    rebalances_c = computed["result_c"].rebalances
    rebalances_d = computed["result_d"].rebalances
    forward_rebalance_dates = sorted({
        d.date() for d in pd.to_datetime(rebalances_c["date"])
        if d.date() > DEVELOPMENT_CUTOFF and d.date() <= latest_completed
    })
    return {
        "levels": levels, "cEquity": c_equity, "dEquity": d_equity, "spyClose": spy_close,
        "rebalancesC": rebalances_c, "rebalancesD": rebalances_d,
        "forwardRebalanceDates": forward_rebalance_dates,
        "cTotalCosts": float(result_c.total_costs), "dTotalCosts": float(result_d.total_costs),
        "latestDate": idx[-1] if len(idx) else None,
    }


def _holdings_at(rebalances: pd.DataFrame, day: date) -> dict[str, float]:
    matches = rebalances.loc[pd.to_datetime(rebalances["date"]).dt.date == day]
    if matches.empty:
        return {}
    return dict(matches.iloc[-1]["holdings"])


def _period_return_pct(equity: pd.Series, start_day: date, end_day: date) -> float | None:
    idx = equity.index[(equity.index.date >= start_day) & (equity.index.date <= end_day)]
    if len(idx) < 2:
        return None
    start_value, end_value = float(equity.loc[idx[0]]), float(equity.loc[idx[-1]])
    if start_value <= 0:
        return None
    return (end_value / start_value - 1.0) * 100.0


def advance_completed_sessions(
    now: datetime | None = None, *,
    latest_session: date | None = None,
    nav_path: Path = NAV_PATH, rebalances_path: Path = REBALANCES_PATH,
    amendments_path: Path = AMENDMENTS_PATH, operations_path: Path = OPERATIONS_PATH,
) -> dict[str, Any]:
    """Append every newly available completed session and any newly closed
    rebalance period exactly once. Existing NAV is verified against a fresh
    replay but never rewritten; a mismatch beyond tolerance raises instead
    of silently mutating forward evidence."""
    if not _ADVANCE_LOCK.acquire(blocking=False):
        return {"status": "already_running", "appendedSessions": 0}
    try:
        completed = latest_session or latest_completed_session(now)
        if completed is None:
            raise RuntimeError("No completed session is available")
        paths = _source_paths(completed)
        levels: pd.DataFrame = paths["levels"]

        existing = load_effective_forward_nav(nav_path, amendments_path)
        if len(existing):
            expected = levels.reindex(existing.index)
            if expected.isna().any().any():
                raise RuntimeError("Previously recorded forward session is missing from the current replay")
            errors = (expected[list(NAV_SERIES_KEYS)] - existing[list(NAV_SERIES_KEYS)]).abs().max()
            breaches = {s: float(errors[s]) for s in NAV_SERIES_KEYS if errors[s] > NAV_SERIES_TOLERANCE[s]}
            if breaches:
                worst = max(breaches, key=breaches.get)
                raise RuntimeError(
                    f"Unexpected historical NAV mutation detected in {worst!r} "
                    f"(max error {breaches[worst]:.3g}, tolerance {NAV_SERIES_TOLERANCE[worst]:.3g})"
                )

        existing_dates = set(existing.index.date) if len(existing) else set()
        appended_sessions = 0
        for day, values in levels.iterrows():
            session = day.date()
            if session in existing_dates:
                continue
            append_nav_level_row(session, {k: float(values[k]) for k in NAV_SERIES_KEYS}, nav_path)
            existing_dates.add(session)
            appended_sessions += 1

        prior_ops = _read_json(operations_path, {})
        # `paths["cTotalCosts"]`/`dTotalCosts` are cumulative over dow_pit's
        # ENTIRE backtest window (compute_rungs() always replays from that
        # strategy's own start, not from the forward cutoff -- see
        # _source_paths's docstring). The only honest "cost since the
        # forward test began" figure is the delta against a baseline
        # captured once, on the first successful advance -- captured here
        # rather than assumed to be zero, since setup may happen a few
        # sessions after the cutoff itself.
        cost_baseline = prior_ops.get("costsBaselineAtSetup")
        if cost_baseline is None:
            cost_baseline = {"c": paths["cTotalCosts"], "d": paths["dTotalCosts"]}
        recorded_rebalance_dates = {row["rebalanceDate"] for row in load_rebalances(rebalances_path)}
        forward_rebalance_dates = paths["forwardRebalanceDates"]
        appended_rebalances = 0
        for i, rebalance_day in enumerate(forward_rebalance_dates):
            key = rebalance_day.isoformat()
            if key in recorded_rebalance_dates:
                continue
            period_end = (
                forward_rebalance_dates[i + 1] if i + 1 < len(forward_rebalance_dates) else completed
            )
            # Only record a period once it has actually closed (the next
            # rebalance happened, or -- for the still-open final period --
            # never here; the open period is reported separately as
            # "in progress", not backfilled as if it had closed).
            if i + 1 >= len(forward_rebalance_dates):
                continue
            c_return = _period_return_pct(paths["cEquity"], rebalance_day, period_end)
            d_return = _period_return_pct(paths["dEquity"], rebalance_day, period_end)
            if c_return is None or d_return is None:
                continue
            regime = None
            try:
                from engine import data as data_module
                spy_bars = data_module.get_bars("SPY", "1d", rebalance_day - timedelta(days=430), rebalance_day)
                regime = regime_classify(spy_bars) if spy_bars is not None and not spy_bars.empty else None
            except Exception:  # noqa: BLE001 - regime tagging is descriptive, never blocks the ledger
                regime = None
            record = RebalanceRecord(
                rebalanceDate=key, regime=regime,
                cHoldings=_holdings_at(paths["rebalancesC"], rebalance_day),
                dHoldings=_holdings_at(paths["rebalancesD"], rebalance_day),
                cPeriodReturnPct=c_return, dPeriodReturnPct=d_return,
                recordedAt=datetime.now(NY).isoformat(timespec="seconds"),
            )
            record_rebalance(record, rebalances_path)
            recorded_rebalance_dates.add(key)
            appended_rebalances += 1

        result = {
            "status": "advanced" if (appended_sessions or appended_rebalances) else "up_to_date",
            "appendedSessions": appended_sessions, "appendedRebalances": appended_rebalances,
            "lastSession": completed.isoformat(),
        }
        operations_state = {
            "lastAttemptAt": datetime.now(NY).isoformat(timespec="seconds"),
            "lastSuccessAt": datetime.now(NY).isoformat(timespec="seconds"),
            "lastError": None,
            "latestCompletedSession": completed.isoformat(),
            "costsBaselineAtSetup": cost_baseline,
            "cumulativeCostsSinceSetup": {
                "c": paths["cTotalCosts"] - cost_baseline["c"],
                "d": paths["dTotalCosts"] - cost_baseline["d"],
            },
            "advanceResult": result,
        }
        _atomic_json_write(operations_path, operations_state)
        return result
    except Exception as exc:
        previous = _read_json(operations_path, {})
        previous.update({
            "lastAttemptAt": datetime.now(NY).isoformat(timespec="seconds"),
            "lastError": f"{type(exc).__name__}: {exc}",
        })
        _atomic_json_write(operations_path, previous)
        raise
    finally:
        _ADVANCE_LOCK.release()


# --- checkpoint gating -------------------------------------------------------


def checkpoint_status(first_session: date | None, today: date | None = None) -> dict[str, Any]:
    """Calendar-month checkpoint status, per the preregistration. Never
    returns anything stronger than 'observational only' before the first
    (6-month) checkpoint -- promotion to a real comparison requires a human
    reading the preregistered hypothesis and the scorecard together, not
    this function."""
    today = today or date.today()
    if first_session is None:
        first_checkpoint_months = CHECKPOINTS_MONTHS[0][0]
        # The preregistered schedule is defined regardless of whether
        # tracking has started -- a viewer opening this before the first
        # forward session should still see all three checkpoints (dates
        # unknown until there's a first session to anchor them to), not an
        # empty schedule that reads as if none were ever defined.
        no_data_schedule = [
            {"months": months, "label": label, "date": None, "reached": False}
            for months, label in CHECKPOINTS_MONTHS
        ]
        return {
            "monthsElapsed": 0.0, "reached": [], "schedule": no_data_schedule,
            "next": {"months": first_checkpoint_months, "label": CHECKPOINTS_MONTHS[0][1], "date": None, "reached": False},
            "interpretationStatus": "No forward observations yet.",
        }
    months_elapsed = (today - first_session).days / 30.4375  # average calendar month, for the elapsed-progress figure ONLY
    # "reached" uses the SAME exact calendar-date arithmetic as the displayed
    # checkpoint date, not the averaged-days figure above -- they disagreed
    # at exactly the 12-month boundary in a non-leap year (365 days is 0.25
    # days short of the 30.4375-day average's "12 months"), which would have
    # shown a checkpoint date of literally today marked as not yet reached.
    schedule = []
    for months, label in CHECKPOINTS_MONTHS:
        checkpoint_date = (pd.Timestamp(first_session) + pd.DateOffset(months=months)).date()
        schedule.append({
            "months": months, "label": label, "date": checkpoint_date.isoformat(),
            "reached": today >= checkpoint_date,
        })
    reached = [row["label"] for row in schedule if row["reached"]]
    upcoming = next((row for row in schedule if not row["reached"]), None)
    six_reached, twelve_reached = schedule[0]["reached"], schedule[1]["reached"]
    if not six_reached:
        interpretation = "OBSERVATIONAL ONLY -- pre-checkpoint. Numbers are reported for transparency; not grounds for redesign."
    elif not twelve_reached:
        interpretation = "First descriptive checkpoint reached -- still observational only; formal comparison begins at 12 months."
    else:
        interpretation = "Eligible for formal comparison against the preregistered hypothesis (human-written verdict required; not auto-concluded)."
    return {
        "monthsElapsed": round(months_elapsed, 2), "firstSession": first_session.isoformat(),
        "reached": reached, "next": upcoming, "schedule": schedule,
        "interpretationStatus": interpretation,
    }


# --- scorecard ---------------------------------------------------------------


def _perf_block(nav: pd.Series) -> dict:
    r = nav.pct_change().dropna()
    dd = nav / nav.cummax() - 1.0
    return {
        "cumulativeReturnPct": float(nav.iloc[-1] / nav.iloc[0] - 1) * 100 if len(nav) > 1 else 0.0,
        "volatilityPct": float(r.std(ddof=1) * np.sqrt(252) * 100) if len(r) > 1 else None,
        "maxDrawdownPct": float(dd.min() * 100) if len(dd) else 0.0,
        "worstDayPct": float(r.min() * 100) if len(r) else None,
    }


def build_scorecard(nav_df: pd.DataFrame, rebalance_rows: list[dict]) -> dict[str, Any]:
    n = len(nav_df)
    first_session = nav_df.index[0].date() if n else None
    checkpoint = checkpoint_status(first_session)

    if n == 0:
        return {
            "observations": 0, "status": "No Forward Observations Yet",
            "checkpoint": checkpoint, "series": {}, "hitRates": {}, "regimeBreakdown": {},
        }

    series: dict[str, dict] = {}
    for key in NAV_SERIES_KEYS:
        block = _perf_block(nav_df[key])
        if n >= MIN_SESSIONS_FOR_ANNUALIZED:
            r = nav_df[key].pct_change().dropna()
            years = n / 252.0
            block["annualizedReturnPct"] = float((nav_df[key].iloc[-1] / nav_df[key].iloc[0]) ** (1 / years) - 1) * 100
            vol = r.std(ddof=1) * np.sqrt(252)
            block["sharpe"] = float(r.mean() * 252 / vol) if vol else None
            downside = r.clip(upper=0)
            down_dev = np.sqrt((downside ** 2).mean()) * np.sqrt(252)
            block["sortino"] = float(r.mean() * 252 / down_dev) if down_dev else None
        else:
            block["annualizedBlock"] = "Too early to evaluate"
        series[key] = block

    benchmark_relative = {
        "cVsSpyReturnDiffPp": series["c"]["cumulativeReturnPct"] - series["spy"]["cumulativeReturnPct"],
        "dVsSpyReturnDiffPp": series["d"]["cumulativeReturnPct"] - series["spy"]["cumulativeReturnPct"],
    }
    # The direct forward analogue of the historical ladder study's
    # "incrementalCMinusD" (engine/mrm_attribution_ladder.py) -- D minus C,
    # so a positive number means residualization is AHEAD on raw return
    # (the opposite of what the preregistered hypothesis expects), matching
    # that module's own sign convention so the two can be read side by side.
    paired_relative = {
        "dMinusCReturnDiffPp": series["d"]["cumulativeReturnPct"] - series["c"]["cumulativeReturnPct"],
        "dMinusCMaxDrawdownDiffPp": series["d"]["maxDrawdownPct"] - series["c"]["maxDrawdownPct"],
    }

    c_hits = [row["cPeriodReturnPct"] > 0 for row in rebalance_rows]
    d_hits = [row["dPeriodReturnPct"] > 0 for row in rebalance_rows]
    paired_hits = [row["dPeriodReturnPct"] > row["cPeriodReturnPct"] for row in rebalance_rows]
    hit_rates = {
        "cHitRatePct": round(100.0 * sum(c_hits) / len(c_hits), 1) if c_hits else None,
        "dHitRatePct": round(100.0 * sum(d_hits) / len(d_hits), 1) if d_hits else None,
        "pairedHitRatePct": round(100.0 * sum(paired_hits) / len(paired_hits), 1) if paired_hits else None,
        "pairedRebalancePeriods": len(rebalance_rows),
    }

    regime_breakdown: dict[str, dict[str, Any]] = {}
    for row in rebalance_rows:
        label = row.get("regime") or "Unknown"
        bucket = regime_breakdown.setdefault(label, {"periods": 0, "cReturns": [], "dReturns": []})
        bucket["periods"] += 1
        bucket["cReturns"].append(row["cPeriodReturnPct"])
        bucket["dReturns"].append(row["dPeriodReturnPct"])
    for label, bucket in regime_breakdown.items():
        bucket["cMeanReturnPct"] = round(float(np.mean(bucket.pop("cReturns"))), 3) if bucket["periods"] else None
        bucket["dMeanReturnPct"] = round(float(np.mean(bucket.pop("dReturns"))), 3) if bucket["periods"] else None

    cost_ops = _read_json(OPERATIONS_PATH, {})
    cumulative_costs = cost_ops.get("cumulativeCostsSinceSetup") or {}

    return {
        "observations": n,
        "developmentCutoff": DEVELOPMENT_CUTOFF.isoformat(),
        "forwardPeriod": {"start": nav_df.index[0].date().isoformat(), "end": nav_df.index[-1].date().isoformat()},
        "status": checkpoint["interpretationStatus"],
        "checkpoint": checkpoint,
        "series": series,
        "benchmarkRelative": benchmark_relative,
        "pairedRelative": paired_relative,
        "maxDrawdownDiffPp": series["d"]["maxDrawdownPct"] - series["c"]["maxDrawdownPct"],
        "sharpeDiff": (
            series["d"].get("sharpe") - series["c"].get("sharpe")
            if series["d"].get("sharpe") is not None and series["c"].get("sharpe") is not None else "Too early to evaluate"
        ),
        "hitRates": hit_rates,
        "regimeBreakdown": regime_breakdown,
        "cumulativeCostsSinceSetup": cumulative_costs,
        "hypothesisNote": (
            "Preregistered hypothesis: residualization may sacrifice raw return but improve "
            "downside/risk quality relative to plain momentum. Not evaluated against this "
            "scorecard except at a reached checkpoint -- see checkpoint.interpretationStatus."
        ),
    }


def forward_stack_status(
    nav_path: Path = NAV_PATH, rebalances_path: Path = REBALANCES_PATH,
    amendments_path: Path = AMENDMENTS_PATH, operations_path: Path = OPERATIONS_PATH,
) -> dict[str, Any]:
    """Fast persisted-state read; never runs a backtest."""
    nav = load_effective_forward_nav(nav_path, amendments_path)
    rebalances = load_rebalances(rebalances_path)
    score = build_scorecard(nav, rebalances)
    operations = _read_json(operations_path, {})
    amendments = load_amendments(amendments_path)
    alerts = []
    if operations.get("lastError"):
        alerts.append({"code": "forward_shadow_failure", "severity": "error", "message": operations["lastError"]})
    dates = list(nav.index.date) if len(nav) else []
    if dates and min(dates) <= DEVELOPMENT_CUTOFF:
        alerts.append({"code": "cutoff_violation", "severity": "error", "message": "Forward NAV contains development data"})
    return {
        "scorecard": score, "operations": operations, "alerts": alerts,
        "preregistration": str(PREREGISTRATION_PATH.relative_to(PREREGISTRATION_PATH.parents[1])),
        "reconciliation": {
            "confirmedAmendments": len(amendments),
            "amendments": amendments,
            "rawNavPreserved": True,
        },
    }
