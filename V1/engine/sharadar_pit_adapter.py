"""Fail-closed normalization of Sharadar raw identity/action rows.

Sharadar supplies the raw ingredients, but this adapter refuses to invent
known-at timestamps or terminal delisting returns. It is diagnostic-only and
does not write a PIT bundle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable


@dataclass(frozen=True)
class NormalizedSecurity:
    security_id: str
    ticker: str
    effective_start: str | None
    effective_end: str | None
    known_at: str | None
    is_us_listed: bool | None
    is_common_stock: bool | None
    security_type: str | None
    exchange: str | None
    is_acquired: bool | None
    delisting_reason: str | None
    unresolved: tuple[str, ...]


def _date(value: Any) -> str | None:
    if value in (None, "", "N/A"):
        return None
    return str(value)[:10]


def _is_delist(action: str) -> bool:
    lowered = action.lower().replace("_", "")
    return "delist" in lowered or lowered in {"lasttradedate", "liquidation"}


def normalize_security(
    ticker_row: dict[str, Any],
    action_rows: Iterable[dict[str, Any]],
) -> NormalizedSecurity:
    """Normalize one ticker row without guessing unavailable PIT semantics."""
    ticker = str(ticker_row.get("ticker") or "").strip()
    permaticker = str(ticker_row.get("permaticker") or "").strip()
    security_id = permaticker or f"ticker:{ticker}"
    actions = list(action_rows)
    delist_rows = [row for row in actions if _is_delist(str(row.get("action") or ""))]
    delisting_reason = next(
        (str(row.get("value")).strip() for row in delist_rows if row.get("value") not in (None, "", "N/A")),
        None,
    )
    unresolved: list[str] = []
    if not permaticker:
        unresolved.append("security_id")
    # Sharadar action dates/effective dates are not public-information times.
    unresolved.append("known_at")
    if delist_rows and delisting_reason is None:
        unresolved.append("delisting_reason")
    if delist_rows:
        unresolved.append("DelistingReturn")
    else:
        unresolved.append("delisting_status")
    category = str(ticker_row.get("category") or "").strip() or None
    exchange = str(ticker_row.get("exchange") or "").strip() or None
    is_common = category.lower() in {"common stock", "common"} if category else None
    is_us = exchange.upper() in {"NYSE", "NASDAQ", "NYSEMKT", "AMEX", "OTC"} if exchange else None
    return NormalizedSecurity(
        security_id=security_id,
        ticker=ticker,
        effective_start=_date(ticker_row.get("firstpricedate")),
        effective_end=_date(ticker_row.get("lastpricedate")),
        known_at=None,
        is_us_listed=is_us,
        is_common_stock=is_common,
        security_type=category,
        exchange=exchange,
        is_acquired=any(str(row.get("action") or "").lower() in {"acquisition", "acquired"} for row in actions),
        delisting_reason=delisting_reason,
        unresolved=tuple(sorted(set(unresolved))),
    )


def publishable(security: NormalizedSecurity) -> bool:
    """Return whether the normalized row can satisfy the strict PIT identity contract."""
    return not security.unresolved and all(
        value is not None
        for value in (
            security.effective_start,
            security.effective_end,
            security.known_at,
            security.is_us_listed,
            security.is_common_stock,
            security.security_type,
            security.exchange,
            security.is_acquired,
        )
    )

