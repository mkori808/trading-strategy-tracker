"""DM/MRM Volatility-Scaled Portfolio -- FROZEN combined-strategy overlay.

Full specification: FROZEN_DM_MRM_VOL_SCALED.md. That file is the source of
truth for every constant here; this module reads them, it never decides them
-- same relationship engine/forward_tracking.py has to
FROZEN_DUAL_MOMENTUM.md.

This is a CAPITAL-ALLOCATION overlay on top of two frozen, unmodified
strategies (Dual Momentum, Market-Residual Momentum). It contains no signal
logic of its own and must never gain any: the only research variable this
module is allowed to touch is how much capital each sleeve gets, decided
from each sleeve's own trailing volatility and nothing else.

**The weighting rule** (frozen 2026-08-22, before any forward observation
existed):

    At each monthly reset date t:
        vol_DM(t)  = std(DM daily returns over the 60 sessions strictly
                     before t) * sqrt(252)
        vol_MRM(t) = std(MRM daily returns over the 60 sessions strictly
                     before t) * sqrt(252)
        w_DM(t)  = (1 / vol_DM(t)) / (1/vol_DM(t) + 1/vol_MRM(t))
        w_MRM(t) = 1 - w_DM(t)

No floor, no cap, no epsilon beyond IEEE754 (an exact-zero trailing vol is
never observed in practice and is treated as "insufficient data" -- see
`compute_weights`, which withholds a reset rather than dividing by zero).
No forecast, no trend filter, no discretionary bound. If a future version
wants any of those, it is a NEW version with a new ID, not an edit here.

**Reset schedule**: the union of both sleeves' own monthly rebalance dates,
same convention the preregistered blend experiment used -- both sleeves
already rebalance monthly, so this never asks either to trade off its own
schedule.

**Between resets**: buy-and-hold. Each sleeve's dollar value drifts with its
own daily return; the realized weight drifts away from the target until the
next reset re-establishes it. This is a REAL two-sleeve capital split, not a
daily-reweighted overlay -- turnover is countable and is exactly the
capital moved at each reset (see `incremental_turnover`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252

#: Frozen. Changing this number requires a new strategy version, not an edit.
VOL_LOOKBACK_SESSIONS = 60

#: The historical window this specification was frozen against. Every
#: session on or before this date is DEVELOPMENT data -- it was examined
#: while designing and auditing this overlay and can never serve as a
#: forward-test observation for it. See FROZEN_DM_MRM_VOL_SCALED.md.
DEVELOPMENT_CUTOFF = date(2026, 8, 21)

LEDGER_PATH = Path(__file__).resolve().parent.parent / "logs" / "dm_mrm_vol_scaled_forward.json"


def _reset_dates(dm_ret: pd.Series, mrm_ret: pd.Series) -> list[pd.Timestamp]:
    """First common session of each calendar month -- the union of both
    sleeves' own monthly cadence, never a cadence invented for the overlay."""
    idx = dm_ret.index.intersection(mrm_ret.index).sort_values()
    if len(idx) == 0:
        return []
    months = pd.date_range(idx[0], idx[-1], freq="MS")
    out = []
    for m in months:
        candidates = idx[idx >= m]
        if len(candidates):
            out.append(candidates[0])
    return sorted(set(out))


@dataclass(frozen=True)
class WeightDecision:
    """One reset's complete, independently-checkable arithmetic."""

    date: str
    dm_trailing_vol_pct: float | None
    mrm_trailing_vol_pct: float | None
    dm_weight: float | None
    mrm_weight: float | None
    prior_dm_weight: float | None
    prior_mrm_weight: float | None
    sufficient_data: bool
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def compute_weight_path(
    dm_ret: pd.Series, mrm_ret: pd.Series
) -> list[WeightDecision]:
    """The complete, auditable sequence of reset-date decisions.

    Every value used at reset `t` is drawn from sessions strictly BEFORE
    `t`: `vol_dm_all`/`vol_mrm_all` are computed with `.rolling(60).std()`
    and then indexed at position `i - 1` (yesterday's close), never `i`
    (today). This is the one invariant the look-ahead tests below exist to
    protect -- see test_dm_mrm_vol_scaled.py.

    Before `VOL_LOOKBACK_SESSIONS` sessions of common history exist, a
    reset is recorded with `sufficient_data=False` and no weight change --
    the portfolio holds whatever it already held (50/50 at inception,
    since there is nothing else to hold before the first real decision).
    """
    common_idx = dm_ret.index.intersection(mrm_ret.index).sort_values()
    dm_ret, mrm_ret = dm_ret.loc[common_idx], mrm_ret.loc[common_idx]
    vol_dm_all = dm_ret.rolling(VOL_LOOKBACK_SESSIONS).std() * np.sqrt(TRADING_DAYS_PER_YEAR)
    vol_mrm_all = mrm_ret.rolling(VOL_LOOKBACK_SESSIONS).std() * np.sqrt(TRADING_DAYS_PER_YEAR)
    resets = set(_reset_dates(dm_ret, mrm_ret))

    decisions: list[WeightDecision] = []
    w_dm, w_mrm = 0.5, 0.5
    for i, day in enumerate(common_idx):
        if day not in resets:
            continue
        prior_w_dm, prior_w_mrm = w_dm, w_mrm
        if i == 0:
            decisions.append(WeightDecision(
                str(day.date()), None, None, w_dm, w_mrm, prior_w_dm, prior_w_mrm,
                False, "First session in the common history; no trailing window exists yet.",
            ))
            continue
        v_dm, v_mrm = vol_dm_all.iloc[i - 1], vol_mrm_all.iloc[i - 1]
        if pd.isna(v_dm) or pd.isna(v_mrm) or v_dm <= 0 or v_mrm <= 0:
            decisions.append(WeightDecision(
                str(day.date()), None if pd.isna(v_dm) else float(v_dm * 100),
                None if pd.isna(v_mrm) else float(v_mrm * 100),
                w_dm, w_mrm, prior_w_dm, prior_w_mrm, False,
                f"Fewer than {VOL_LOOKBACK_SESSIONS} prior sessions of common "
                "history, or a zero trailing volatility. Weights held at their "
                "prior value.",
            ))
            continue
        w_dm = (1 / v_dm) / (1 / v_dm + 1 / v_mrm)
        w_mrm = 1 - w_dm
        decisions.append(WeightDecision(
            str(day.date()), float(v_dm * 100), float(v_mrm * 100),
            float(w_dm), float(w_mrm), prior_w_dm, prior_w_mrm, True,
            "Trailing vol computed through the prior session's close.",
        ))
    return decisions


def build_portfolio(dm_ret: pd.Series, mrm_ret: pd.Series) -> tuple[pd.Series, list[WeightDecision]]:
    """NAV-based reconstruction (real dollar drift between resets, not a
    daily-reweighted return sum) plus the decision log that produced it."""
    common_idx = dm_ret.index.intersection(mrm_ret.index).sort_values()
    dm_ret, mrm_ret = dm_ret.loc[common_idx], mrm_ret.loc[common_idx]
    decisions = compute_weight_path(dm_ret, mrm_ret)
    weight_by_date = {d.date: (d.dm_weight, d.mrm_weight) for d in decisions}

    nav = pd.Series(index=common_idx, dtype=float)
    value_dm, value_mrm = 0.5, 0.5
    nav.iloc[0] = 1.0
    for i in range(1, len(common_idx)):
        day = common_idx[i]
        value_dm *= 1 + dm_ret.iloc[i]
        value_mrm *= 1 + mrm_ret.iloc[i]
        key = str(day.date())
        if key in weight_by_date:
            w_dm, w_mrm = weight_by_date[key]
            total = value_dm + value_mrm
            value_dm, value_mrm = total * w_dm, total * w_mrm
        nav.iloc[i] = value_dm + value_mrm
    return nav, decisions


def incremental_turnover(decisions: list[WeightDecision]) -> dict:
    """Capital moved BETWEEN sleeves at each reset -- the overlay's own
    turnover, separate from and additive to each sleeve's internal trading.
    A reset that moves weight from 40% to 45% DM trades 5% of NAV out of
    MRM and into DM: one side's |delta|, not the sum of both (they are the
    same dollars moving one direction)."""
    real = [d for d in decisions if d.sufficient_data]
    if len(real) < 2:
        return {"annualTurnoverPct": None, "avgMonthlyTransferPct": None, "maxMonthlyTransferPct": None}
    deltas = [abs(real[i].dm_weight - real[i - 1].dm_weight) * 100 for i in range(1, len(real))]
    years = len(real) / 12.0
    return {
        "annualTurnoverPct": float(sum(deltas) / years) if years > 0 else None,
        "avgMonthlyTransferPct": float(np.mean(deltas)) if deltas else None,
        "maxMonthlyTransferPct": float(np.max(deltas)) if deltas else None,
        "resetsWithSufficientData": len(real),
    }


# --- append-only forward-test ledger ----------------------------------------


@dataclass(frozen=True)
class ForwardDecision:
    """One completed forward-test reset. Immutable once recorded."""

    decisionDate: str
    recordedAt: str
    dmTrailingVolPct: float | None
    mrmTrailingVolPct: float | None
    dmTargetWeight: float
    mrmTargetWeight: float
    dmHoldings: dict
    mrmHoldings: dict
    transactionCostBps: float
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def record_forward_decision(decision: ForwardDecision, path: Path = LEDGER_PATH) -> None:
    """Append one completed decision. Refuses to overwrite an existing date.

    Past forward decisions must never be recalculated using later data --
    this is the one invariant a real forward test cannot survive losing.
    Enforced structurally: appending is the only supported operation, and a
    duplicate `decisionDate` raises rather than silently replacing the
    original entry.
    """
    path.parent.mkdir(exist_ok=True)
    existing = json.loads(path.read_text()) if path.exists() else []
    if any(row["decisionDate"] == decision.decisionDate for row in existing):
        raise ValueError(
            f"A forward decision for {decision.decisionDate} is already recorded. "
            "The ledger is append-only -- past decisions cannot be revised."
        )
    if date.fromisoformat(decision.decisionDate) <= DEVELOPMENT_CUTOFF:
        raise ValueError(
            f"{decision.decisionDate} is on or before the development cutoff "
            f"({DEVELOPMENT_CUTOFF.isoformat()}); it was already examined while "
            "this strategy was designed and cannot count as a forward observation."
        )
    existing.append(decision.to_dict())
    path.write_text(json.dumps(existing, indent=2))


def load_forward_ledger(path: Path = LEDGER_PATH) -> list[dict]:
    return json.loads(path.read_text()) if path.exists() else []
