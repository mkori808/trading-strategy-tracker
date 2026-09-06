"""Explicit supplemental policies for the two Sharadar fields it cannot prove alone.

No timestamp or terminal value is inferred from an action date. Callers must
provide independently verified supplements; otherwise the result stays
unresolved and cannot enter a runnable PIT bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SupplementalEvidence:
    known_at: str | None = None
    terminal_value_per_share: float | None = None
    terminal_value_source: str | None = None


def delisting_return(last_trade_price: float, evidence: SupplementalEvidence) -> float | None:
    """Calculate terminal return only when an independently sourced value exists."""
    if last_trade_price <= 0 or evidence.terminal_value_per_share is None:
        return None
    if evidence.terminal_value_per_share < 0:
        raise ValueError("terminal value cannot be negative")
    if not evidence.terminal_value_source:
        return None
    return evidence.terminal_value_per_share / last_trade_price - 1.0


def policy_status(evidence: SupplementalEvidence) -> dict[str, Any]:
    missing: list[str] = []
    if not evidence.known_at:
        missing.append("known_at")
    if evidence.terminal_value_per_share is None or not evidence.terminal_value_source:
        missing.append("DelistingReturn")
    return {
        "publishable": not missing,
        "missing": missing,
        "knownAtPolicy": "external_public_timestamp_only",
        "delistingReturnPolicy": "terminal_value / last_trade_price - 1, with source required",
    }

