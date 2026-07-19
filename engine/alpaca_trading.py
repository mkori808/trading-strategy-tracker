"""Read-only Alpaca PAPER trading client -- account, positions, recent
orders, and market-clock status for the live-monitor dashboard.

`paper=True` is hardcoded below and is never exposed as a toggle anywhere in
this module. That is deliberate defense-in-depth: even if a live key pair
ever ended up in .env under one of the accepted names, `paper=True` forces
alpaca-py's paper base URL, so live keys would simply fail auth rather than
silently trading real money (see CLAUDE.md's "Live trading safety
guardrails" -- default to paper, never a live default).

No order-submission code exists anywhere in this module, by design: this
pass is monitoring only. Automated paper bracket-order placement is
explicitly out of scope until the kill-switch/PDT/risk-cap guardrails
CLAUDE.md calls for are built alongside it, not bolted on afterward.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from engine.alpaca_client import first_env


def _credentials() -> tuple[str | None, str | None]:
    # Same accepted names as engine/alpaca_client.py, so any key pair the
    # user has already pasted into .env for market data also works here.
    api_key = first_env(
        "ALPACA_API_KEY", "ALPACA_PAPER_API_KEY", "APCA_API_KEY_ID", "ALPACA_API_KEY_ID"
    )
    secret_key = first_env(
        "ALPACA_SECRET_KEY", "ALPACA_API_SECRET", "ALPACA_PAPER_SECRET_KEY",
        "APCA_API_SECRET_KEY", "ALPACA_SECRET",
    )
    return api_key, secret_key


@lru_cache(maxsize=1)
def trading_client() -> tuple[Any | None, str]:
    """Lazily build a single paper TradingClient. Returns (client_or_None,
    reason). Never raises -- callers branch on the None to degrade
    gracefully, matching engine/alpaca_client.py:market_data_client."""
    api_key, secret_key = _credentials()
    if not api_key or not secret_key:
        missing = "key" if not api_key else "secret"
        return None, (
            f"Alpaca {missing} missing from .env. Need a key line "
            "(ALPACA_API_KEY=...) and a secret line (ALPACA_SECRET_KEY=...)."
        )
    try:
        from alpaca.trading.client import TradingClient
    except ImportError:
        return None, "alpaca-py is not installed (pip install alpaca-py)."
    try:
        return TradingClient(api_key, secret_key, paper=True), "ok"
    except Exception as exc:  # noqa: BLE001 -- surface any init failure to the UI
        return None, f"Failed to initialize Alpaca trading client: {exc}"


def available() -> tuple[bool, str]:
    client, reason = trading_client()
    return client is not None, reason


def get_account() -> dict[str, Any]:
    client, reason = trading_client()
    if client is None:
        return {"available": False, "reason": reason}
    acct = client.get_account()
    return {
        "available": True,
        "accountNumber": acct.account_number,
        "status": str(acct.status).rsplit(".", maxsplit=1)[-1],
        "equity": float(acct.equity),
        "cash": float(acct.cash),
        "buyingPower": float(acct.buying_power),
        "portfolioValue": float(acct.portfolio_value),
        "daytradeCount": None if acct.daytrade_count is None else int(acct.daytrade_count),
    }


def get_positions() -> list[dict[str, Any]]:
    client, reason = trading_client()
    if client is None:
        return []
    positions = client.get_all_positions()
    return [
        {
            "symbol": p.symbol,
            "side": str(p.side).rsplit(".", maxsplit=1)[-1].lower(),
            "qty": float(p.qty),
            "avgEntryPrice": float(p.avg_entry_price),
            "currentPrice": float(p.current_price) if p.current_price is not None else None,
            "marketValue": float(p.market_value) if p.market_value is not None else None,
            "unrealizedPl": float(p.unrealized_pl) if p.unrealized_pl is not None else None,
            "unrealizedPlPct": (
                float(p.unrealized_plpc) * 100 if p.unrealized_plpc is not None else None
            ),
        }
        for p in positions
    ]


def get_recent_orders(limit: int = 50) -> list[dict[str, Any]]:
    client, reason = trading_client()
    if client is None:
        return []
    from alpaca.trading.enums import QueryOrderStatus
    from alpaca.trading.requests import GetOrdersRequest

    req = GetOrdersRequest(status=QueryOrderStatus.ALL, limit=limit)
    orders = client.get_orders(req)
    return [
        {
            "id": str(o.id),
            "symbol": o.symbol,
            "side": str(o.side).rsplit(".", maxsplit=1)[-1].lower(),
            "qty": float(o.qty) if o.qty is not None else None,
            "type": str(o.type).rsplit(".", maxsplit=1)[-1].lower(),
            "status": str(o.status).rsplit(".", maxsplit=1)[-1].lower(),
            "submittedAt": o.submitted_at.isoformat() if o.submitted_at else None,
            "filledAt": o.filled_at.isoformat() if o.filled_at else None,
            "filledAvgPrice": float(o.filled_avg_price) if o.filled_avg_price else None,
        }
        for o in orders
    ]


def get_clock() -> dict[str, Any]:
    client, reason = trading_client()
    if client is None:
        return {"available": False, "reason": reason}
    clock = client.get_clock()
    return {
        "available": True,
        "isOpen": bool(clock.is_open),
        "nextOpen": clock.next_open.isoformat(),
        "nextClose": clock.next_close.isoformat(),
        "timestamp": clock.timestamp.isoformat(),
    }


def account_snapshot() -> dict[str, Any]:
    """Everything the Live Monitor tab needs in one call."""
    return {
        "account": get_account(),
        "positions": get_positions(),
        "orders": get_recent_orders(),
        "clock": get_clock(),
    }
