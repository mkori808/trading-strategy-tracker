"""Causal timing contracts and event-to-session mapping.

Every engine must say when its signal was observable and where it executes.
This keeps a strategy from silently using a completed close and also filling
at that same close.  Event timestamp mapping lives here as the other half of
the same causality rule.
"""

from __future__ import annotations

from datetime import date, datetime
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd

from engine.universe import TIMEZONE


class InformationAvailability(str, Enum):
    PRE_MARKET = "PRE_MARKET"
    INTRADAY = "INTRADAY"
    AT_CLOSE = "AT_CLOSE"
    POST_CLOSE = "POST_CLOSE"


class ExecutionTiming(str, Enum):
    SAME_OPEN = "SAME_OPEN"
    SAME_CLOSE = "SAME_CLOSE"
    NEXT_OPEN = "NEXT_OPEN"


@dataclass(frozen=True)
class TimingContract:
    information_availability: InformationAvailability
    execution: ExecutionTiming
    uses_current_close: bool
    engine: str
    exception_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "informationAvailability": self.information_availability.value,
            "execution": self.execution.value,
            "usesCurrentClose": self.uses_current_close,
            "engine": self.engine,
            "exceptionReason": self.exception_reason,
        }


def default_timing_contract(*, engine: str = "standard") -> TimingContract:
    return TimingContract(
        information_availability=InformationAvailability.AT_CLOSE,
        execution=ExecutionTiming.NEXT_OPEN,
        uses_current_close=True,
        engine=engine,
    )


def timing_contract_for(strategy: object | type) -> TimingContract:
    provider = getattr(strategy, "timing_contract", None)
    if callable(provider):
        contract = provider()
        if isinstance(contract, TimingContract):
            return contract
    return default_timing_contract()


def validate_timing_contract(
    contract: TimingContract,
    *,
    actual_execution: ExecutionTiming | None = None,
) -> None:
    """Reject execution that occurs before its evidence is observable."""
    execution = actual_execution or contract.execution
    if actual_execution is not None and actual_execution != contract.execution:
        raise ValueError(
            f"Timing contract declares {contract.execution.value}, but the "
            f"engine executes {actual_execution.value}"
        )
    if execution == ExecutionTiming.SAME_CLOSE and contract.uses_current_close:
        raise ValueError("Same-close execution cannot use the current completed close")
    if execution == ExecutionTiming.SAME_CLOSE and contract.information_availability in {
        InformationAvailability.AT_CLOSE,
        InformationAvailability.POST_CLOSE,
    }:
        raise ValueError(
            f"{contract.information_availability.value} evidence is unavailable before a same-close fill"
        )
    if execution == ExecutionTiming.SAME_OPEN and contract.information_availability != InformationAvailability.PRE_MARKET:
        raise ValueError(
            f"{contract.information_availability.value} evidence is unavailable before a same-open fill"
        )


def _event_timestamp(value: date | datetime | pd.Timestamp, *, tz) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    if stamp.tz is None:
        # A date-only legacy value means "known before this session". Exact
        # provider timestamps remain exact and are never truncated here.
        stamp = stamp.tz_localize(tz or TIMEZONE)
    elif tz is not None:
        stamp = stamp.tz_convert(tz)
    if tz is None and stamp.tz is not None:
        stamp = stamp.tz_convert(TIMEZONE).tz_localize(None)
    return stamp


def reaction_session(
    index: pd.DatetimeIndex,
    event: date | datetime | pd.Timestamp,
) -> pd.Timestamp | None:
    """First represented equity session whose 16:00 close is after `event`.

    Thus an 08:00 BMO report maps to the same day's bar, while a 16:05 AMC
    report maps to the next represented trading session. Weekends and market
    holidays work naturally because only dates present in ``index`` are
    considered. Exactly-16:00 releases conservatively map to the next session.
    """
    if len(index) == 0:
        return None
    tz = index.tz
    event_stamp = _event_timestamp(event, tz=tz)
    session_dates = pd.DatetimeIndex(pd.to_datetime(index.date))
    closes = session_dates + pd.Timedelta(hours=16)
    if tz is not None:
        closes = closes.tz_localize(tz)
    positions = closes > event_stamp
    if not positions.any():
        return None
    return index[int(positions.argmax())]
