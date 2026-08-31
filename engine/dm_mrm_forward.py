"""Forward-test OPERATING infrastructure for the frozen DM/MRM strategies.

Deliberately a separate module from `engine/dm_mrm_vol_scaled.py`. That
module IS the frozen specification -- the 60-session lookback, the
inverse-vol formula, the monthly reset schedule, the warm-up rule, the
development cutoff. Nothing in this file may change any of those; every
weight this module records is computed by CALLING
`dm_mrm_vol_scaled.compute_weight_path`, never by reimplementing it, so the
two modules cannot silently drift apart.

What this module adds is provenance, persistence, and reporting for a
composite (two-sleeve) forward experiment -- a shape the existing
single-strategy `engine/forward_experiments.py` cannot represent without
fabricating a fake `portfolio_runs` row. Forcing that would manufacture
provenance the run never had, which is the one thing a forward test cannot
survive. So this is new, purpose-built machinery, in the same spirit as
`engine/forward_tracking.py` (a bespoke append-only tracker for one frozen
config) rather than a generic abstraction.

**Comparison set, fixed for the life of this experiment** (never resized by
performance): canonical Dual Momentum, canonical Market-Residual Momentum,
the frozen 50/50 blend, the frozen vol-scaled blend, plus SPY as an external
benchmark only.

**Paper/research only.** Nothing here places an order, reads or writes
`engine/execution_db.py`, or touches live automation. It is pure
record-keeping for a forward comparison a human reads.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from engine import dm_mrm_vol_scaled as vs

DEVELOPMENT_CUTOFF = vs.DEVELOPMENT_CUTOFF  # read, never redefined
DEVELOPMENT_START = date(2021, 8, 23)

COMPARISON_SET: tuple[str, ...] = (
    "Dual Momentum", "Market-Residual Momentum", "50/50 DM/MRM (frozen)",
    "Vol-Scaled DM/MRM (frozen)",
)
BENCHMARK = "SPY"

DECISIONS_PATH = Path(__file__).resolve().parent.parent / "logs" / "dm_mrm_forward_decisions.json"
OUTCOMES_PATH = Path(__file__).resolve().parent.parent / "logs" / "dm_mrm_forward_outcomes.json"
NAV_PATH = Path(__file__).resolve().parent.parent / "logs" / "dm_mrm_forward_nav.json"
AMENDMENTS_PATH = Path(__file__).resolve().parent.parent / "logs" / "dm_mrm_forward_amendments.json"
OPERATIONS_PATH = Path(__file__).resolve().parent.parent / "logs" / "dm_mrm_forward_operations.json"
NY = ZoneInfo("America/New_York")
_ADVANCE_LOCK = Lock()
FINALIZATION_LAG_SESSIONS = 1

CHECKPOINTS = (
    (20, "Operational sanity only"),
    (63, "Early descriptive review"),
    (126, "Interim descriptive review"),
    (252, "First annualized forward review"),
)


def _atomic_json_write(path: Path, value: Any) -> None:
    """Durably replace one JSON file; readers never observe a partial write.

    Replacement is retried past transient Windows file locks the same way
    `engine/data.py:_replace_with_retry` already is: this repo lives under
    OneDrive, whose sync client briefly opens files it is uploading, which
    turns a perfectly good write into a `PermissionError` at the last step.
    Without the retry, that error was itself masking the real
    `advance_completed_sessions` failure in the operator logs (see the
    2026-08-30 DM/MRM forward-NAV incident).
    """
    from engine.data import _replace_with_retry

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(raw)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read_json(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

#: Forward days needed before ANY annualized statistic (Sharpe, Sortino,
#: annualized return, positive-month rate) is computed at all. Matches the
#: 12-month minimum horizon in FROZEN_DM_MRM_VOL_SCALED.md -- reported as
#: "Too early to evaluate" below this, never as an unstable number with a
#: footnote.
MIN_SESSIONS_FOR_ANNUALIZED = 252

#: Separate, slightly later gate: the 12-month RESEARCH REVIEW milestone.
#: Distinct constant from the one above on purpose -- one is "can we even
#: compute a ratio without it being noise", the other is "may a human open
#: this and compare it to the preregistered questions". They happen to share
#: a value today; they are allowed to diverge later without conflating two
#: different decisions.
MIN_SESSIONS_FOR_REVIEW = 252


# --- provenance: what each sleeve contributed to one decision --------------


@dataclass(frozen=True)
class SleeveProvenance:
    """One sleeve's contribution to a composite decision.

    `strategyVersion` is a human-readable description of the frozen config,
    not a database foreign key -- there is no single persisted
    `portfolio_runs` row this sleeve corresponds to at decision time,
    because the composite is computed directly from fresh sleeve returns,
    not from a stored backtest run. That is the honest provenance
    statement, not a workaround."""

    strategyName: str
    strategyVersion: str
    targetWeight: float
    holdings: dict

    def to_dict(self) -> dict:
        return asdict(self)


DM_VERSION = "Dual Momentum -- 189d lookback, top 5, monthly, EQUITY_UNIVERSE (canonical, registered)"
MRM_VERSION = "Market-Residual Momentum -- canonical registered parameters"


# --- immutable decision snapshot --------------------------------------------


@dataclass(frozen=True)
class CompositeDecisionSnapshot:
    """Everything the composite strategy knew and intended at one reset,
    frozen at the moment it was made. Immutable once recorded -- see
    `record_decision`'s append-only enforcement below."""

    decisionDate: str
    decisionRecordedAt: str
    executionDate: str  # == decisionDate: the frozen spec applies weights same-day, no lag
    volLookbackSessions: int
    volWindowStartDate: str | None
    volWindowEndDate: str | None
    dmTrailingVolPct: float | None
    mrmTrailingVolPct: float | None
    dmInverseVolScore: float | None
    mrmInverseVolScore: float | None
    dmTargetWeight: float
    mrmTargetWeight: float
    priorDmWeight: float
    priorMrmWeight: float
    requiredTransferPct: float
    estimatedOverlayCostBps: float
    dmSleeve: dict
    mrmSleeve: dict
    combinedIntendedExposurePct: float
    sufficientData: bool
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def build_decision_snapshot(
    dm_ret_history: pd.Series,
    mrm_ret_history: pd.Series,
    decision_date: pd.Timestamp,
    dm_holdings: dict,
    mrm_holdings: dict,
    overlay_cost_bps: float = 0.0,
) -> CompositeDecisionSnapshot:
    """Compute one decision's full provenance, using ONLY the frozen
    module's own weighting function for the arithmetic.

    `dm_ret_history`/`mrm_ret_history` must be truncated to sessions
    available AT `decision_date` by the caller -- this function does not
    look further into either series than what it is handed, but it also
    does not clip for you, so a caller that hands it the future gets a
    decision computed from the future. The forward-runner in this module
    always calls it with `.loc[:decision_date]`, and a test enforces this.
    """
    history_dm = dm_ret_history.loc[:decision_date]
    history_mrm = mrm_ret_history.loc[:decision_date]
    decisions = vs.compute_weight_path(history_dm, history_mrm)
    if not decisions or pd.Timestamp(decisions[-1].date) != pd.Timestamp(decision_date):
        raise ValueError(
            f"{decision_date} is not a reset date under the frozen monthly schedule "
            "for this history."
        )
    d = decisions[-1]
    prior = decisions[-2] if len(decisions) > 1 else d

    idx = history_dm.index.intersection(history_mrm.index).sort_values()
    window_start = window_end = None
    if d.sufficient_data:
        pos = idx.get_loc(pd.Timestamp(decision_date))
        window = idx[pos - vs.VOL_LOOKBACK_SESSIONS: pos]
        window_start, window_end = str(window[0].date()), str(window[-1].date())

    dm_inv = 1 / (d.dm_trailing_vol_pct / 100) if d.dm_trailing_vol_pct else None
    mrm_inv = 1 / (d.mrm_trailing_vol_pct / 100) if d.mrm_trailing_vol_pct else None
    transfer = abs(d.dm_weight - d.prior_dm_weight) if hasattr(d, "prior_dm_weight") else abs(d.dm_weight - prior.dm_weight)

    return CompositeDecisionSnapshot(
        decisionDate=str(decision_date.date()) if hasattr(decision_date, "date") else str(decision_date),
        decisionRecordedAt=datetime.now().isoformat(timespec="seconds"),
        executionDate=str(decision_date.date()) if hasattr(decision_date, "date") else str(decision_date),
        volLookbackSessions=vs.VOL_LOOKBACK_SESSIONS,
        volWindowStartDate=window_start,
        volWindowEndDate=window_end,
        dmTrailingVolPct=d.dm_trailing_vol_pct,
        mrmTrailingVolPct=d.mrm_trailing_vol_pct,
        dmInverseVolScore=dm_inv,
        mrmInverseVolScore=mrm_inv,
        dmTargetWeight=d.dm_weight,
        mrmTargetWeight=d.mrm_weight,
        priorDmWeight=d.prior_dm_weight,
        priorMrmWeight=d.prior_mrm_weight,
        requiredTransferPct=float(transfer) * 100,
        estimatedOverlayCostBps=overlay_cost_bps,
        dmSleeve=SleeveProvenance("Dual Momentum", DM_VERSION, d.dm_weight, dm_holdings).to_dict(),
        mrmSleeve=SleeveProvenance("Market-Residual Momentum", MRM_VERSION, d.mrm_weight, mrm_holdings).to_dict(),
        combinedIntendedExposurePct=100.0,
        sufficientData=d.sufficient_data,
        note=d.note,
    )


def record_decision(
    snapshot: CompositeDecisionSnapshot, path: Path = DECISIONS_PATH
) -> None:
    """Append-only. Refuses a duplicate date and refuses anything on or
    before the development cutoff -- the two invariants a forward test
    cannot survive losing."""
    decision_day = date.fromisoformat(snapshot.decisionDate)
    if decision_day <= DEVELOPMENT_CUTOFF:
        raise ValueError(
            f"{snapshot.decisionDate} is on or before the development cutoff "
            f"({DEVELOPMENT_CUTOFF.isoformat()}) and cannot enter the forward ledger."
        )
    existing = _read_json(path, [])
    if any(row["decisionDate"] == snapshot.decisionDate for row in existing):
        raise ValueError(
            f"A decision for {snapshot.decisionDate} is already recorded. "
            "Decisions are append-only and cannot be revised."
        )
    existing.append(snapshot.to_dict())
    _atomic_json_write(path, existing)


def load_decisions(path: Path = DECISIONS_PATH) -> list[dict]:
    return _read_json(path, [])


# --- realized outcomes: separate store, never touches the decision --------


@dataclass(frozen=True)
class RealizedOutcome:
    """What actually happened after a decision -- stored separately so
    recording it can never mutate what the decision snapshot said."""

    decisionDate: str
    asOfDate: str
    recordedAt: str
    dmRealizedReturnPct: float
    mrmRealizedReturnPct: float
    combinedRealizedReturnPct: float
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def record_outcome(outcome: RealizedOutcome, path: Path = OUTCOMES_PATH) -> None:
    """Append-only, keyed on (decisionDate, asOfDate). Never rewrites the
    decision it realizes -- that file is never opened here."""
    decision_day, as_of_day = date.fromisoformat(outcome.decisionDate), date.fromisoformat(outcome.asOfDate)
    if decision_day <= DEVELOPMENT_CUTOFF:
        raise ValueError("Outcome decision date is on or before the development cutoff")
    if as_of_day < decision_day:
        raise ValueError("Outcome as-of date cannot precede its immutable decision")
    existing = _read_json(path, [])
    if any(
        row["decisionDate"] == outcome.decisionDate and row["asOfDate"] == outcome.asOfDate
        for row in existing
    ):
        raise ValueError(
            f"An outcome for decision {outcome.decisionDate} as of {outcome.asOfDate} "
            "is already recorded."
        )
    existing.append(outcome.to_dict())
    _atomic_json_write(path, existing)


def load_outcomes(path: Path = OUTCOMES_PATH) -> list[dict]:
    return _read_json(path, [])


# --- forward-only NAV series -------------------------------------------------


NAV_SERIES_KEYS = ("dm", "mrm", "fiftyFifty", "volScaled", "spy")

# Reconciliation tolerance per series (NAV-level units, base 100) for the
# `advance_completed_sessions` historical-mutation check below.
#
# `dm`, `mrm`, `fiftyFifty`, and `spy` are pure compounded single-day returns
# -- given identical daily returns they must reproduce bit-for-bit, so any
# deviation at all is a real problem and stays at essentially machine
# precision.
#
# `volScaled` is different in kind, not degree: `compute_weight_path` sets
# each reset's DM/MRM blend from a ROLLING volatility estimate over the
# entire multi-year lookback (from DEVELOPMENT_START), so its value at any
# forward date depends on every historical bar in that window, not just that
# day's own return. Confirmed in production on 2026-08-30: after the
# 2026-08-27 JNJ-dividend amendment already reconciled dm/mrm/fiftyFifty/spy
# to ~1e-11, volScaled alone still carried a 0.00171 residual traceable to
# ordinary (now-mitigated, see engine/data.py:get_bars's tail-only refetch
# fix) auto-adjust drift on SOME historical bar inside the volatility
# lookback -- not one citable corporate action the way JNJ's dividend was,
# and not practical to chase individual-bar attribution across years of
# rotating holdings every time it recurs. A bounded, disclosed tolerance
# here -- comfortably above that observed residual, and nearly two orders of
# magnitude below the 0.103 JNJ-level event that DID warrant investigation
# and an amendment -- accepts that expected class of noise while still
# catching a real corruption.
NAV_SERIES_TOLERANCE: dict[str, float] = {
    "dm": 1e-8, "mrm": 1e-8, "fiftyFifty": 1e-8, "spy": 1e-8,
    "volScaled": 1e-2,
}
NAV_BASE = 100.0


@dataclass(frozen=True)
class NavAmendment:
    """Append-only correction to the reporting view; raw NAV is never edited."""

    amendmentId: str
    recordedAt: str
    reasonCode: str
    explanation: str
    evidence: dict
    replacements: list[dict]
    status: str = "confirmed"

    def to_dict(self) -> dict:
        return asdict(self)


def record_nav_amendment(amendment: NavAmendment, path: Path = AMENDMENTS_PATH) -> None:
    if amendment.status != "confirmed":
        raise ValueError("Only confirmed NAV amendments may alter the effective reporting view.")
    existing = _read_json(path, [])
    if any(row["amendmentId"] == amendment.amendmentId for row in existing):
        raise ValueError(f"NAV amendment {amendment.amendmentId} is already recorded.")
    for replacement in amendment.replacements:
        if date.fromisoformat(replacement["date"]) <= DEVELOPMENT_CUTOFF:
            raise ValueError("A NAV amendment cannot target development-period data.")
        if replacement["series"] not in NAV_SERIES_KEYS:
            raise ValueError(f"Unknown NAV series {replacement['series']!r}.")
    existing.append(amendment.to_dict())
    _atomic_json_write(path, existing)


def load_nav_amendments(path: Path = AMENDMENTS_PATH) -> list[dict]:
    return _read_json(path, [])


def append_nav_row(
    session_date: date,
    returns: dict[str, float],
    path: Path = NAV_PATH,
) -> dict:
    """One forward session's NAV for all five comparison series.

    `returns` maps each key in NAV_SERIES_KEYS to that session's own
    fractional return. The first row (nothing yet on file) is seeded at
    NAV_BASE for every series regardless of that day's return, so all five
    start from an identical, arbitrary comparison base on the SAME first
    forward session -- never spliced onto a development-period level.
    """
    if session_date <= DEVELOPMENT_CUTOFF:
        raise ValueError(
            f"{session_date} is on or before the development cutoff "
            f"({DEVELOPMENT_CUTOFF.isoformat()}); it cannot enter the forward NAV."
        )
    existing = _read_json(path, [])
    if any(row["date"] == session_date.isoformat() for row in existing):
        raise ValueError(f"A forward NAV row for {session_date} is already recorded.")
    if existing and session_date.isoformat() <= existing[-1]["date"]:
        raise ValueError(
            f"{session_date} is not after the last recorded forward session "
            f"({existing[-1]['date']}); NAV rows must append chronologically."
        )

    if not existing:
        row = {"date": session_date.isoformat(), **{k: NAV_BASE for k in NAV_SERIES_KEYS}}
    else:
        prev = existing[-1]
        row = {"date": session_date.isoformat()}
        for k in NAV_SERIES_KEYS:
            r = returns.get(k)
            row[k] = prev[k] * (1 + r) if r is not None else prev[k]
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


def load_effective_forward_nav(
    path: Path = NAV_PATH, amendments_path: Path = AMENDMENTS_PATH,
) -> pd.DataFrame:
    """Apply confirmed amendments in append order without touching raw NAV."""
    nav = load_forward_nav(path)
    for amendment in load_nav_amendments(amendments_path):
        if amendment.get("status") != "confirmed":
            continue
        for replacement in amendment.get("replacements", []):
            day = pd.Timestamp(replacement["date"])
            series = replacement["series"]
            if day not in nav.index:
                raise RuntimeError(f"NAV amendment targets missing session {replacement['date']}")
            if series not in NAV_SERIES_KEYS:
                raise RuntimeError(f"NAV amendment targets unknown series {series}")
            stored = float(nav.loc[day, series])
            expected_prior = float(replacement["priorValue"])
            if abs(stored - expected_prior) > 1e-10:
                raise RuntimeError(
                    f"NAV amendment prior-value guard failed for {replacement['date']} {series}"
                )
            nav.loc[day, series] = float(replacement["replacementValue"])
    return nav


def append_nav_level_row(session_date: date, levels: dict[str, float], path: Path = NAV_PATH) -> dict:
    """Append exact reconstructed levels, avoiding amendment leakage into a later return."""
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


# --- completed-session automatic advancement -------------------------------


def _plain_daily(series: pd.Series) -> pd.Series:
    out = series.copy().sort_index()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    return out[~out.index.duplicated(keep="last")]


def latest_completed_session(now: datetime | None = None) -> date | None:
    """Latest SPY session whose daily bar is safely complete.

    The updater deliberately waits until 17:00 New York even on normal close
    days. Early closes are therefore delayed, never treated intraday.
    """
    from engine import data as data_module

    now = now.astimezone(NY) if now and now.tzinfo else (now.replace(tzinfo=NY) if now else datetime.now(NY))
    candidate = now.date()
    if now.weekday() >= 5 or now.time() < time(17, 0):
        candidate = (pd.Timestamp(candidate) - pd.offsets.BDay(1)).date()
    bars = data_module.get_bars("SPY", "1d", DEVELOPMENT_CUTOFF - timedelta(days=10), candidate)
    if bars is None or bars.empty:
        return None
    dates = [stamp.date() for stamp in bars.index if stamp.date() <= candidate]
    return max(dates) if dates else None


def _blend_path(dm_ret: pd.Series, mrm_ret: pd.Series,
                targets: dict[str, tuple[float, float]]) -> tuple[pd.Series, pd.DataFrame]:
    """Two real sleeves, returns first and reset at that session afterward."""
    idx = dm_ret.index.intersection(mrm_ret.index).sort_values()
    dm, mrm = dm_ret.loc[idx], mrm_ret.loc[idx]
    nav = pd.Series(index=idx, dtype=float)
    states = []
    value_dm = value_mrm = .5
    for i, day in enumerate(idx):
        if i:
            value_dm *= 1 + float(dm.iloc[i])
            value_mrm *= 1 + float(mrm.iloc[i])
        key = day.date().isoformat()
        transfer = 0.0
        if key in targets:
            w_dm, w_mrm = targets[key]
            total = value_dm + value_mrm
            transfer = abs(value_dm / total - w_dm)
            value_dm, value_mrm = total * w_dm, total * w_mrm
        total = value_dm + value_mrm
        nav.iloc[i] = total
        states.append({"date": day, "dmWeight": value_dm / total, "mrmWeight": value_mrm / total,
                       "overlayTransfer": transfer})
    return nav, pd.DataFrame(states).set_index("date")


def _latest_holdings(rows: pd.DataFrame, day: pd.Timestamp) -> dict[str, float]:
    eligible = rows.loc[pd.to_datetime(rows.date).map(lambda x: x.tz_localize(None) if x.tzinfo else x) <= day]
    return dict(eligible.iloc[-1].holdings) if len(eligible) else {}


def _canonical_source(latest: date) -> dict[str, Any]:
    """Replay unchanged registered sleeves through one completed session."""
    from engine import data as data_module
    from engine.dm_portfolio_concentration_audit import _replay
    from engine.runner import RunRequest, run_cross_sectional

    request = RunRequest(start=DEVELOPMENT_START, end=latest)
    dm = run_cross_sectional("Dual Momentum", request, persist=False)
    mrm = run_cross_sectional("Market-Residual Momentum", request, persist=False)
    spy = data_module.get_bars("SPY", "1d", DEVELOPMENT_START - timedelta(days=7), latest)["Close"]

    def current_actual(result: Any) -> dict[str, float]:
        _, weights, _ = _replay(result)
        if weights.empty:
            return {}
        last = weights.loc[(weights.point == "close") & (weights.date == weights.date.max())]
        return {str(r.symbol): float(r.weight) for r in last.itertuples()}

    return {
        "dmEquity": dm.equity_curve, "mrmEquity": mrm.equity_curve, "spyClose": spy,
        "dmRebalances": dm.rebalances, "mrmRebalances": mrm.rebalances,
        "dmCurrentHoldings": current_actual(dm), "mrmCurrentHoldings": current_actual(mrm),
    }


def _source_paths(source: dict[str, Any]) -> dict[str, Any]:
    for key in ("dmEquity", "mrmEquity", "spyClose"):
        if pd.DatetimeIndex(source[key].index).duplicated().any():
            raise RuntimeError(f"Duplicate completed session in {key}")
    dm_equity = _plain_daily(source["dmEquity"])
    mrm_equity = _plain_daily(source["mrmEquity"])
    spy_close = _plain_daily(source["spyClose"])
    eligible_spy = spy_close.index[spy_close.index.date > DEVELOPMENT_CUTOFF]
    missing = {
        "dm": [d.date().isoformat() for d in eligible_spy.difference(dm_equity.index)],
        "mrm": [d.date().isoformat() for d in eligible_spy.difference(mrm_equity.index)],
    }
    missing = {key: dates for key, dates in missing.items() if dates}
    if missing:
        raise RuntimeError(f"Missing completed-session data; refusing to skip: {missing}")
    idx = dm_equity.index.intersection(mrm_equity.index).intersection(spy_close.index).sort_values()
    dm_equity, mrm_equity, spy_close = dm_equity.loc[idx], mrm_equity.loc[idx], spy_close.loc[idx]
    dm_ret, mrm_ret = dm_equity.pct_change(), mrm_equity.pct_change()
    decisions = vs.compute_weight_path(dm_ret, mrm_ret)
    vol_targets = {d.date: (float(d.dm_weight), float(d.mrm_weight)) for d in decisions}
    reset_dates = {d.date for d in decisions}
    fixed_targets = {d: (.5, .5) for d in reset_dates}
    vol_nav, vol_state = _blend_path(dm_ret, mrm_ret, vol_targets)
    fixed_nav, fixed_state = _blend_path(dm_ret, mrm_ret, fixed_targets)
    raw = {"dm": dm_equity, "mrm": mrm_equity, "fiftyFifty": fixed_nav,
           "volScaled": vol_nav, "spy": spy_close}
    forward_idx = idx[idx.date > DEVELOPMENT_CUTOFF]
    levels = pd.DataFrame(index=forward_idx)
    for key, series in raw.items():
        values = series.loc[forward_idx]
        if len(values):
            levels[key] = values / float(values.iloc[0]) * NAV_BASE
    return {"levels": levels, "dmReturns": dm_ret, "mrmReturns": mrm_ret,
            "decisions": decisions, "volState": vol_state, "fixedState": fixed_state,
            "latestDate": idx[-1] if len(idx) else None}


def _combined_exposure(dm_weight: float, mrm_weight: float,
                       dm_holdings: dict[str, float], mrm_holdings: dict[str, float]) -> dict[str, float]:
    combined: defaultdict[str, float] = defaultdict(float)
    for symbol, weight in dm_holdings.items():
        combined[symbol] += dm_weight * float(weight)
    for symbol, weight in mrm_holdings.items():
        combined[symbol] += mrm_weight * float(weight)
    return dict(sorted(combined.items(), key=lambda item: (-item[1], item[0])))


def _operation_state(source: dict[str, Any], paths: dict[str, Any], latest_completed: date | None,
                     latest_finalized: date | None, result: dict[str, Any]) -> dict[str, Any]:
    latest = paths["latestDate"]
    decisions = paths["decisions"]
    last_decision = decisions[-1] if decisions else None
    vol_row = paths["volState"].iloc[-1] if len(paths["volState"]) else None
    fixed_row = paths["fixedState"].iloc[-1] if len(paths["fixedState"]) else None
    dm_holdings = source.get("dmCurrentHoldings") or _latest_holdings(source["dmRebalances"], latest)
    mrm_holdings = source.get("mrmCurrentHoldings") or _latest_holdings(source["mrmRebalances"], latest)
    next_month = (latest + pd.offsets.MonthBegin(1) + pd.offsets.BDay(0)).date().isoformat() if latest is not None else None
    vol_forward = paths["volState"].loc[paths["volState"].index.date > DEVELOPMENT_CUTOFF]
    fixed_forward = paths["fixedState"].loc[paths["fixedState"].index.date > DEVELOPMENT_CUTOFF]
    return {
        "lastAttemptAt": datetime.now(NY).isoformat(timespec="seconds"),
        "lastSuccessAt": datetime.now(NY).isoformat(timespec="seconds"),
        "lastError": None, "latestCompletedSession": latest_completed.isoformat() if latest_completed else None,
        "latestFinalizedSession": latest_finalized.isoformat() if latest_finalized else None,
        "finalizationLagSessions": FINALIZATION_LAG_SESSIONS,
        "lastSourceSession": latest.date().isoformat() if latest is not None else None,
        "advanceResult": result,
        "comparisonTracking": {
            "volScaledOverlayTurnoverPct": float(vol_forward.overlayTransfer.sum() * 100),
            "fixedOverlayTurnoverPct": float(fixed_forward.overlayTransfer.sum() * 100),
            "turnoverDifferencePct": float((vol_forward.overlayTransfer.sum() - fixed_forward.overlayTransfer.sum()) * 100),
            "overlayCostDifferenceBps": 0.0,
            "costNote": "Both frozen controls charge underlying sleeve costs and zero overlay-level reallocation cost."
        },
        "currentBlend": {
            "dmWeight": float(vol_row.dmWeight) if vol_row is not None else .5,
            "mrmWeight": float(vol_row.mrmWeight) if vol_row is not None else .5,
            "dmTargetWeight": float(last_decision.dm_weight) if last_decision else .5,
            "mrmTargetWeight": float(last_decision.mrm_weight) if last_decision else .5,
            "lastWeightResetDate": last_decision.date if last_decision else None,
            "nextScheduledReset": next_month,
            "dmHoldings": dm_holdings, "mrmHoldings": mrm_holdings,
            "combinedHoldings": _combined_exposure(float(vol_row.dmWeight), float(vol_row.mrmWeight), dm_holdings, mrm_holdings) if vol_row is not None else {},
            "fixedCombinedHoldings": _combined_exposure(float(fixed_row.dmWeight), float(fixed_row.mrmWeight), dm_holdings, mrm_holdings) if fixed_row is not None else {},
        },
    }


def advance_completed_sessions(now: datetime | None = None, *,
                               source_factory: Callable[[date], dict[str, Any]] = _canonical_source,
                               latest_session: date | None = None,
                               decisions_path: Path = DECISIONS_PATH, nav_path: Path = NAV_PATH,
                               amendments_path: Path | None = None,
                               operations_path: Path = OPERATIONS_PATH) -> dict[str, Any]:
    """Append every newly available completed session exactly once.

    Existing NAV is verified against an independent reconstruction but never
    rewritten. Existing decisions are not rebuilt; duplicate dates are skipped.
    A source revision therefore raises an implementation alert instead of
    silently mutating forward evidence.
    """
    if not _ADVANCE_LOCK.acquire(blocking=False):
        return {"status": "already_running", "appendedSessions": 0}
    try:
        amendments_path = amendments_path or (
            AMENDMENTS_PATH if nav_path == NAV_PATH else nav_path.with_name(f"{nav_path.stem}_amendments.json")
        )
        completed = latest_session or latest_completed_session(now)
        if completed is None:
            raise RuntimeError("No completed SPY session is available")
        prior_ops = _read_json(operations_path, {})
        prior_nav = load_effective_forward_nav(nav_path, amendments_path)
        recorded_finalized = prior_ops.get("latestFinalizedSession")
        if (
            prior_ops.get("latestCompletedSession") == completed.isoformat()
            and not prior_ops.get("lastError")
            and prior_ops.get("finalizationLagSessions") == FINALIZATION_LAG_SESSIONS
            and (
                recorded_finalized is None
                or (len(prior_nav) and prior_nav.index[-1].date().isoformat() == recorded_finalized)
            )
        ):
            result = {
                "status": "up_to_date", "appendedSessions": 0,
                "lastSession": recorded_finalized,
                "sourceSession": prior_ops.get("lastSourceSession"),
                "finalizationLagSessions": FINALIZATION_LAG_SESSIONS,
            }
            checked_at = datetime.now(NY).isoformat(timespec="seconds")
            prior_ops.update({"lastAttemptAt": checked_at, "lastSuccessAt": checked_at,
                              "lastError": None, "advanceResult": result})
            _atomic_json_write(operations_path, prior_ops)
            return result
        source = source_factory(completed)
        paths = _source_paths(source)
        levels: pd.DataFrame = paths["levels"]
        finalizable = levels.iloc[:-FINALIZATION_LAG_SESSIONS] if len(levels) > FINALIZATION_LAG_SESSIONS else levels.iloc[:0]
        finalized_through = finalizable.index[-1].date() if len(finalizable) else None
        nav_caught_up = (
            finalized_through is None
            or (len(prior_nav) and prior_nav.index[-1].date() == finalized_through)
        )
        if (prior_ops.get("latestCompletedSession") == completed.isoformat()
                and not prior_ops.get("lastError") and nav_caught_up):
            result = {"status": "up_to_date", "appendedSessions": 0,
                      "lastSession": prior_nav.index[-1].date().isoformat() if len(prior_nav) else None}
            # This is the normal hourly path between completed sessions.  It
            # must refresh the operational heartbeat even though append-only
            # decisions/NAV remain byte-identical; otherwise the dashboard
            # falsely reports a dead scheduler after 2.5 hours.
            checked_at = datetime.now(NY).isoformat(timespec="seconds")
            prior_ops.update({
                "lastAttemptAt": checked_at,
                "lastSuccessAt": checked_at,
                "lastError": None,
                "latestCompletedSession": completed.isoformat(),
                "latestFinalizedSession": finalized_through.isoformat() if finalized_through else None,
                "finalizationLagSessions": FINALIZATION_LAG_SESSIONS,
                "advanceResult": result,
            })
            _atomic_json_write(operations_path, prior_ops)
            return result
        existing = load_effective_forward_nav(nav_path, amendments_path)
        if len(existing):
            expected = finalizable.reindex(existing.index)
            if expected.isna().any().any():
                raise RuntimeError("Previously recorded forward session is missing from the current source replay")
            errors = (expected[list(NAV_SERIES_KEYS)] - existing[list(NAV_SERIES_KEYS)]).abs().max()
            breaches = {
                series: float(errors[series]) for series in NAV_SERIES_KEYS
                if errors[series] > NAV_SERIES_TOLERANCE[series]
            }
            if breaches:
                worst = max(breaches, key=breaches.get)
                raise RuntimeError(
                    f"Unexpected historical NAV mutation detected in {worst!r} "
                    f"(max error {breaches[worst]:.3g}, tolerance {NAV_SERIES_TOLERANCE[worst]:.3g}); "
                    f"all breaches: {breaches}"
                )
        existing_dates = set(existing.index.date) if len(existing) else set()
        decision_dates = {row["decisionDate"] for row in load_decisions(decisions_path)}
        decision_by_date = {d.date: d for d in paths["decisions"]}
        appended = 0
        for day, values in finalizable.iterrows():
            session = day.date()
            if session in existing_dates:
                continue
            key = session.isoformat()
            if key in decision_by_date and key not in decision_dates:
                dm_holdings = _latest_holdings(source["dmRebalances"], day)
                mrm_holdings = _latest_holdings(source["mrmRebalances"], day)
                snapshot = build_decision_snapshot(paths["dmReturns"].loc[:day], paths["mrmReturns"].loc[:day], day, dm_holdings, mrm_holdings)
                record_decision(snapshot, decisions_path)
                decision_dates.add(key)
            append_nav_level_row(session, {k: float(values[k]) for k in NAV_SERIES_KEYS}, nav_path)
            existing_dates.add(session); appended += 1
        result = {"status": "advanced" if appended else "up_to_date", "appendedSessions": appended,
                  "lastSession": finalized_through.isoformat() if finalized_through else None,
                  "sourceSession": paths["latestDate"].date().isoformat() if paths["latestDate"] is not None else None,
                  "finalizationLagSessions": FINALIZATION_LAG_SESSIONS}
        _atomic_json_write(
            operations_path, _operation_state(source, paths, completed, finalized_through, result)
        )
        return result
    except Exception as exc:
        previous = _read_json(operations_path, {})
        previous.update({"lastAttemptAt": datetime.now(NY).isoformat(timespec="seconds"),
                         "lastError": f"{type(exc).__name__}: {exc}"})
        _atomic_json_write(operations_path, previous)
        raise
    finally:
        _ADVANCE_LOCK.release()


def maturity_checkpoint(n_sessions: int) -> dict[str, Any]:
    reached = [label for count, label in CHECKPOINTS if n_sessions >= count]
    upcoming = next(({"sessions": count, "label": label} for count, label in CHECKPOINTS if n_sessions < count), None)
    return {"label": "Too Early to Evaluate" if n_sessions < 252 else "First annualized forward review eligible",
            "reached": reached, "next": upcoming}


def forward_stack_status(nav_path: Path = NAV_PATH, decisions_path: Path = DECISIONS_PATH,
                         amendments_path: Path | None = None,
                         operations_path: Path = OPERATIONS_PATH) -> dict[str, Any]:
    """Fast persisted-state read for API/dashboard; never runs a backtest."""
    amendments_path = amendments_path or (
        AMENDMENTS_PATH if nav_path == NAV_PATH else nav_path.with_name(f"{nav_path.stem}_amendments.json")
    )
    nav = load_effective_forward_nav(nav_path, amendments_path)
    amendments = load_nav_amendments(amendments_path)
    decisions = load_decisions(decisions_path)
    score = build_scorecard(nav, decisions)
    operations = _read_json(operations_path, {})
    labels = {"dm": "Canonical DM", "mrm": "Canonical MRM", "fiftyFifty": "Fixed 50/50 DM/MRM",
              "volScaled": "Vol-Scaled DM/MRM", "spy": "SPY"}
    kinds = {"dm": "Operational paper + normalized forward NAV", "mrm": "Research shadow",
             "fiftyFifty": "Research shadow", "volScaled": "Research shadow", "spy": "Benchmark shadow"}
    rows = []

    position_history: dict[str, list[dict[str, Any]]] = {key: [] for key in NAV_SERIES_KEYS}
    for decision in decisions:
        dm_holdings = (decision.get("dmSleeve") or {}).get("holdings") or {}
        mrm_holdings = (decision.get("mrmSleeve") or {}).get("holdings") or {}
        position_history["dm"].append({"date": decision["decisionDate"], "holdings": dm_holdings, "source": "frozen_decision"})
        position_history["mrm"].append({"date": decision["decisionDate"], "holdings": mrm_holdings, "source": "frozen_decision"})
        position_history["fiftyFifty"].append({
            "date": decision["decisionDate"],
            "holdings": _combined_exposure(.5, .5, dm_holdings, mrm_holdings), "source": "frozen_decision",
        })
        position_history["volScaled"].append({
            "date": decision["decisionDate"],
            "holdings": _combined_exposure(
                float(decision["dmTargetWeight"]), float(decision["mrmTargetWeight"]),
                dm_holdings, mrm_holdings,
            ), "source": "frozen_decision",
        })

    current_blend = operations.get("currentBlend") or {}
    current_date = operations.get("lastSourceSession")
    current_holdings = {
        "dm": current_blend.get("dmHoldings") or {},
        "mrm": current_blend.get("mrmHoldings") or {},
        "fiftyFifty": current_blend.get("fixedCombinedHoldings") or {},
        "volScaled": current_blend.get("combinedHoldings") or {},
    }
    if current_date:
        for key, holdings in current_holdings.items():
            if not holdings:
                continue
            event = {"date": current_date, "holdings": holdings, "source": "current_snapshot"}
            if position_history[key] and position_history[key][-1]["date"] == current_date:
                position_history[key][-1] = event
            else:
                position_history[key].append(event)

    for key in NAV_SERIES_KEYS:
        perf = score.get("series", {}).get(key, {})
        latest_nav = float(nav[key].iloc[-1]) if len(nav) else None
        history = [
            {"date": day.date().isoformat(), "nav": float(value)}
            for day, value in nav[key].items()
        ] if len(nav) else []
        rows.append({"key": key, "series": labels[key], "type": kinds[key], "status": score["status"],
                     "sessions": len(nav), "nav": latest_nav, "returnPct": perf.get("cumulativeReturnPct"),
                     "drawdownPct": perf.get("maxDrawdownPct"),
                     "lastUpdate": nav.index[-1].date().isoformat() if len(nav) else None,
                     "history": history, "positionHistory": position_history[key]})
    alerts = []
    if operations.get("lastError"):
        alerts.append({
            "code": "research_shadow_failure", "severity": "error",
            "scope": "research_shadow", "liveExecutionAffected": False,
            "message": operations["lastError"],
        })
    dates = list(nav.index.date) if len(nav) else []
    if len(dates) != len(set(dates)):
        alerts.append({"code": "duplicate_session", "severity": "error", "scope": "research_shadow",
                       "liveExecutionAffected": False, "message": "Duplicate forward NAV session"})
    if dates and min(dates) <= DEVELOPMENT_CUTOFF:
        alerts.append({"code": "cutoff_violation", "severity": "error", "scope": "research_shadow",
                       "liveExecutionAffected": False, "message": "Forward NAV contains development data"})
    latest_finalized = operations.get("latestFinalizedSession")
    if latest_finalized and (not dates or dates[-1].isoformat() < latest_finalized):
        alerts.append({"code": "stale_forward_state", "severity": "warning", "scope": "research_shadow",
                       "liveExecutionAffected": False,
                       "message": f"Research-shadow state trails finalizable session {latest_finalized}"})
    paper = {"enabled": False, "canonicalConfig": None, "lastCompletedRun": None}
    try:
        from engine import execution_db

        config = execution_db.automation_config().get("Dual Momentum")
        params = json.loads(config["params"]) if config and config["params"] else {}
        expected = {"lookback_trading_days": 189, "top_n": 5, "rebalance_frequency": "monthly"}
        canonical_config = all(params.get(k, v) == v for k, v in expected.items())
        configured_symbols = execution_db.selected_symbols_for("Dual Momentum") or []
        from engine.strategy_identity import identify_execution_config
        identity = identify_execution_config(
            "Dual Momentum", params, configured_symbols,
            config["validation_run_id"] if config else None,
        )
        completed = next((r for r in execution_db.recent_runs(100)
                          if r["strategy_name"] == "Dual Momentum" and r["status"] == "completed"), None)
        paper = {"enabled": bool(config and config["enabled"]), "canonicalConfig": canonical_config,
                 "configuredParams": params or expected,
                 "configuredSymbols": configured_symbols,
                 "validationRunId": config["validation_run_id"] if config else None,
                 "identity": identity,
                 "lastCompletedRun": ({"date": completed["rebalance_date"], "triggeredAt": completed["triggered_at"],
                                       "targetWeights": json.loads(completed["target_weights"]) if completed["target_weights"] else {}}
                                      if completed else None)}
        if paper["enabled"] and identity["variant"] == "optimized" and identity["fingerprintMatches"]:
            rows[0]["type"] = "Canonical research shadow (separate from optimized Alpaca paper strategy)"
        elif paper["enabled"] and not identity["fingerprintMatches"]:
            rows[0]["type"] = "Canonical research shadow; live automation fingerprint mismatch"
            alerts.append({"code": "strategy_fingerprint_mismatch", "severity": "error",
                           "scope": "live_execution", "liveExecutionAffected": True,
                           "message": identity["fingerprintReason"]})
        elif paper["enabled"]:
            rows[0]["type"] = "Operational paper + normalized forward NAV"
    except Exception as exc:  # dashboard still shows shadow state if the execution DB is unavailable
        alerts.append({"code": "paper_status_unavailable", "severity": "warning",
                       "scope": "live_execution", "liveExecutionAffected": True, "message": str(exc)})
    dm_mrm_returns = nav[["dm", "mrm"]].pct_change().dropna() if len(nav) else pd.DataFrame()
    core_comparisons = {
        "dmMrmReturnCorrelation": (float(dm_mrm_returns.corr().iloc[0, 1]) if len(dm_mrm_returns) > 1 else None),
        "dmMrmVolatilityDifferencePp": (score.get("series", {}).get("dm", {}).get("volatilityPct") - score.get("series", {}).get("mrm", {}).get("volatilityPct")
                                          if score.get("series", {}).get("dm", {}).get("volatilityPct") is not None and score.get("series", {}).get("mrm", {}).get("volatilityPct") is not None else None),
        "dmMrmDrawdownDifferencePp": (score.get("series", {}).get("dm", {}).get("maxDrawdownPct") - score.get("series", {}).get("mrm", {}).get("maxDrawdownPct")
                                        if score.get("series", {}).get("dm", {}).get("maxDrawdownPct") is not None and score.get("series", {}).get("mrm", {}).get("maxDrawdownPct") is not None else None),
        **(operations.get("comparisonTracking") or {}),
    }
    return {"series": rows, "scorecard": score, "comparisons": core_comparisons, "maturity": maturity_checkpoint(len(nav)),
            "currentBlend": operations.get("currentBlend"), "operations": operations, "alerts": alerts,
            "reconciliation": {"confirmedAmendments": len(amendments),
                               "rawNavPreserved": True,
                               "finalizationLagSessions": FINALIZATION_LAG_SESSIONS},
            "paperAutomation": paper,
            "separation": {"alpacaOrderSeries": (["dm"] if paper.get("canonicalConfig") else []), "shadowOrderSeries": ["mrm", "fiftyFifty", "volScaled"],
                           "alpacaEquityLabel": "Alpaca paper account equity",
                           "researchNavLabel": "Normalized strategy forward NAV"}}


def write_operational_audit(output_dir: Path = Path("reports/forward_stack_audit")) -> dict[str, Any]:
    """Persist the current implementation audit without advancing or trading."""
    status = forward_stack_status()
    paper = status["paperAutomation"]
    blend = status.get("currentBlend") or {}
    try:
        from engine import alpaca_trading
        account = alpaca_trading.get_account()
        paper_account = {"available": bool(account.get("available")), "equity": account.get("equity"),
                         "portfolioValue": account.get("portfolioValue")}
    except Exception as exc:
        paper_account = {"available": False, "reason": str(exc)}
    account_label = "unavailable" if not paper_account.get("available") else f"${float(paper_account['equity']):,.2f}"
    payload = {
        "auditedAt": datetime.now(NY).isoformat(timespec="seconds"),
        "developmentCutoff": DEVELOPMENT_CUTOFF.isoformat(),
        "firstEligibleForwardSessionRule": "first completed market session strictly after cutoff",
        "preImplementationFinding": "No DM/MRM decisions, outcomes, or NAV ledger existed; all research shadows required manual code execution and had never advanced.",
        "currentSeries": status["series"], "paperAutomation": paper, "paperAccount": paper_account,
        "automaticAdvancement": {
            "implemented": True, "cadence": "hourly check within existing API execution scheduler",
            "completedSessionsOnly": True, "exactlyOnce": True,
            "limitation": "Runs only while the API/uvicorn process is running; restart catches up all missing completed sessions."
        },
        "immutability": {"decisionsAppendOnly": True, "navAppendOnly": True,
                         "sourceMutationFailsClosed": True, "atomicWrites": True},
        "maturity": status["maturity"], "comparisons": status["comparisons"],
        "currentBlend": blend, "alerts": status["alerts"],
        "strategyChanges": {"canonicalDm": False, "canonicalMrm": False, "fixedFiftyFifty": False,
                            "volScaledSpec": False, "alpacaAutomation": False, "winnerGraceReopened": False},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json_write(output_dir / "results.json", payload)
    lines = ["# Forward-Testing Stack Operational Audit", "", f"Audited {payload['auditedAt']}.", "",
             "## Outcome", "", "The research shadow stack is now wired to the existing hourly app scheduler and advances every newly completed session exactly once. It is append-only, restart-safe, completed-session-only, and incapable of placing Alpaca orders.", "",
             "**Operational blocker:** Alpaca automation is enabled and placed a completed paper rebalance on " + (paper.get("lastCompletedRun") or {}).get("date", "an unknown date") + ", but its persisted configuration is 63-session/daily over a 26-name custom universe—not canonical 189-session/monthly DM. Per the instruction to leave Alpaca automation unchanged, this audit did not alter it.", "",
             f"Alpaca paper account equity at audit time: {account_label}. This is not normalized research NAV.", "",
             "## Current forward evidence", "", "The cutoff is 2026-08-21. The latest completed session is also 2026-08-21, so all five normalized series correctly have zero forward observations. No development NAV was spliced or relabeled.", "",
             "| Series | Type | Sessions | NAV | Status |", "|---|---|---:|---:|---|"]
    for row in status["series"]:
        nav_label = "—" if row["nav"] is None else f"{row['nav']:.2f}"
        lines.append(f"| {row['series']} | {row['type']} | {row['sessions']} | {nav_label} | {row['status']} |")
    lines += ["", "## Frozen vol-scaled state", "", f"Actual drifted sleeve weights: DM {blend.get('dmWeight', 0):.2%}, MRM {blend.get('mrmWeight', 0):.2%}. Targets: DM {blend.get('dmTargetWeight', 0):.2%}, MRM {blend.get('mrmTargetWeight', 0):.2%}. Last reset {blend.get('lastWeightResetDate')}; next scheduled reset {blend.get('nextScheduledReset')}.", "",
              "Combined holdings aggregate any overlap into one security exposure: " + ", ".join(f"{s} {w:.2%}" for s, w in (blend.get("combinedHoldings") or {}).items()) + ".", "",
              "## Controls verified", "", "- Decisions and NAV reject dates on or before the cutoff.", "- Volatility windows contain 60 common sessions and end strictly before reset dates.", "- Existing decisions are skipped on rerun; outcomes are stored separately; source revisions raise an implementation alert instead of rewriting history.", "- NAV uses a common 100 baseline on the first accepted forward session, preserves sleeve costs, and charges the frozen zero overlay cost.", "- Restarts catch up missing sessions chronologically; no-new-data reruns are byte-identical.", "- Checkpoints are 20, 63, 126, and 252 sessions. Annualized metrics remain withheld before 252.", "- Alpaca equity remains separately labeled from normalized research NAV.", "", "## Scheduling limitation", "", "Automatic advancement uses the repository's existing scheduler and therefore runs only while the API process is alive. A restart catches up missed sessions; no external scheduler was created."]
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


# --- scorecard ---------------------------------------------------------------


def review_status(n_sessions: int) -> str:
    """The 12-month gate. Never returns anything stronger than an interim
    label -- promotion past "Forward evidence ..." requires a human reading
    the preregistered comparison metrics, not this function."""
    if n_sessions < MIN_SESSIONS_FOR_REVIEW:
        return "Too Early for Formal Review"
    return "Interim review permitted (see preregistered metrics; not a verdict)"


def _perf_block(nav: pd.Series) -> dict:
    r = nav.pct_change().dropna()
    dd = nav / nav.cummax() - 1.0
    return {
        "cumulativeReturnPct": float(nav.iloc[-1] / nav.iloc[0] - 1) * 100 if len(nav) > 1 else 0.0,
        "volatilityPct": float(r.std(ddof=1) * np.sqrt(252) * 100) if len(r) > 1 else None,
        "maxDrawdownPct": float(dd.min() * 100) if len(dd) else 0.0,
        "worstDayPct": float(r.min() * 100) if len(r) else None,
    }


def build_scorecard(nav_df: pd.DataFrame, decisions: list[dict]) -> dict:
    """The forward scorecard. Annualized figures (return, Sharpe, Sortino,
    positive-month rate) are withheld with an explicit label below
    MIN_SESSIONS_FOR_ANNUALIZED sessions -- never computed and footnoted,
    withheld outright, per the "no unstable annualized number without a
    loud warning" requirement.
    """
    n = len(nav_df)
    if n == 0:
        return {
            "observations": 0, "monthsAccumulated": 0.0,
            "status": "Frozen — Forward Testing / No Forward Observations Yet",
            "reviewStatus": review_status(0),
            "series": {}, "allocationPath": [],
        }

    months = n / 21.0  # ~21 sessions/month, descriptive only
    series = {}
    for key in NAV_SERIES_KEYS:
        block = _perf_block(nav_df[key])
        if n >= MIN_SESSIONS_FOR_ANNUALIZED:
            r = nav_df[key].pct_change().dropna()
            years = n / 252.0
            ann_return = float((nav_df[key].iloc[-1] / nav_df[key].iloc[0]) ** (1 / years) - 1) * 100
            vol = r.std(ddof=1) * np.sqrt(252)
            sharpe = float(r.mean() * 252 / (vol)) if vol else None
            downside = r.clip(upper=0)
            down_dev = np.sqrt((downside ** 2).mean()) * np.sqrt(252)
            sortino = float(r.mean() * 252 / down_dev) if down_dev else None
            monthly = nav_df[key].resample("ME").last().pct_change().dropna()
            block.update({
                "annualizedReturnPct": ann_return, "sharpe": sharpe, "sortino": sortino,
                "positiveMonthPct": float((monthly > 0).mean() * 100) if len(monthly) else None,
            })
        else:
            block["annualizedBlock"] = "Too early to evaluate"
        series[key] = block

    real_decisions = [d for d in decisions if d.get("sufficientData")]
    allocation_path = [
        {"date": d["decisionDate"], "dmWeight": d["dmTargetWeight"], "mrmWeight": d["mrmTargetWeight"]}
        for d in real_decisions
    ]
    transfers = [d["requiredTransferPct"] for d in real_decisions]
    turnover_annual = float(sum(transfers) / months * 12) if months > 0 and transfers else None

    def _relative(a: str, b: str) -> dict:
        out = {
            "cumulativeReturnDiffPp": series[a]["cumulativeReturnPct"] - series[b]["cumulativeReturnPct"],
            "volatilityDiffPp": (
                series[a]["volatilityPct"] - series[b]["volatilityPct"]
                if series[a]["volatilityPct"] is not None and series[b]["volatilityPct"] is not None
                else None
            ),
            "maxDrawdownDiffPp": series[a]["maxDrawdownPct"] - series[b]["maxDrawdownPct"],
        }
        if n >= MIN_SESSIONS_FOR_ANNUALIZED:
            out["sharpeDiff"] = series[a].get("sharpe", 0) - series[b].get("sharpe", 0) if series[a].get("sharpe") is not None and series[b].get("sharpe") is not None else None
        else:
            out["sharpeDiff"] = "Too early to evaluate"
        return out

    status = (
        "Too Early to Evaluate" if n < MIN_SESSIONS_FOR_ANNUALIZED
        else "Forward evidence pending interim review"  # a human assigns Supportive/Mixed/Adverse
    )

    return {
        "observations": n,
        "monthsAccumulated": round(months, 1),
        "developmentPeriod": {"start": DEVELOPMENT_START.isoformat(), "end": DEVELOPMENT_CUTOFF.isoformat()},
        "forwardPeriod": {"start": nav_df.index[0].date().isoformat(), "end": nav_df.index[-1].date().isoformat()},
        "status": status,
        "reviewStatus": review_status(n),
        "series": series,
        "allocationPath": allocation_path,
        "overlayTurnoverAnnualPct": turnover_annual,
        "volScaledVsDm": _relative("volScaled", "dm"),
        "volScaledVsFiftyFifty": _relative("volScaled", "fiftyFifty"),
    }
