"""Alpaca PAPER trading client -- account, positions, orders, and
market-clock status for the live-monitor dashboard, plus the ONLY order-
submission code in the project (`submit_market_order`,
`close_symbol_position`), used exclusively by `engine/execution.py`'s
automated rebalancer for strategies the user has explicitly opted in
(`engine/execution_db.py:strategy_automation`).

`paper=True` is hardcoded below and is never exposed as a toggle anywhere in
this module. That is deliberate defense-in-depth: even if a live key pair
ever ended up in .env under one of the accepted names, `paper=True` forces
alpaca-py's paper base URL, so live keys would simply fail auth rather than
silently trading real money (see CLAUDE.md's "Live trading safety
guardrails" -- default to paper, never a live default).

Order submission shipped together with, not before, the guardrails
CLAUDE.md calls for: `engine/kill_switch.py` (checked here too, as
defense-in-depth, not just by callers), `engine/live_risk.py` (max
position %, max concurrent positions, daily-loss halt -- enforced by the
caller before it ever reaches these functions), and
`engine/execution_db.py` (every order logged before submission, so a
mid-batch crash can't leave a real Alpaca order with zero local record).
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
        # Alpaca's own prior-trading-session-close equity -- the baseline
        # engine/live_risk.py:daily_loss_halted() compares today's equity
        # against, so the daily-loss circuit breaker reuses what the
        # broker already tracks instead of a redundant snapshot mechanism.
        "lastEquity": None if acct.last_equity is None else float(acct.last_equity),
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


def account_and_positions() -> dict[str, Any]:
    """Account + positions only -- engine/execution.py:execute_rebalance's
    hot path needs these two and nothing else; account_snapshot() also
    fetches recent orders and the clock (the clock is checked separately,
    first, by the caller before spending calls on this), which would be
    wasted work on every rebalance attempt."""
    return {"account": get_account(), "positions": get_positions()}


def submit_market_order(
    symbol: str,
    side: str,
    *,
    qty: float | None = None,
    notional: float | None = None,
    client_order_id: str,
) -> dict[str, Any]:
    """The first order-submission code in this project. Exactly one of
    qty/notional -- BUY orders should pass notional (Alpaca computes the
    exact fractional qty at fill time rather than trusting a share count
    derived from a possibly-stale quote); SELL-delta orders (not a full
    exit -- use close_symbol_position for that) should pass qty, re-derived
    from a FRESH get_positions() call by the caller immediately before
    sizing, never an earlier snapshot, to avoid overselling on drift.

    Refuses if the kill switch is active -- checked here too, not just by
    engine/execution.py, as defense-in-depth on the one function that
    actually talks to the broker's order endpoint."""
    from engine import kill_switch

    if kill_switch.is_active():
        raise RuntimeError("Kill switch is active; refusing to submit orders.")
    if (qty is None) == (notional is None):
        raise ValueError("submit_market_order needs exactly one of qty or notional")

    client, reason = trading_client()
    if client is None:
        raise RuntimeError(reason)

    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    request = MarketOrderRequest(
        symbol=symbol,
        side=OrderSide[side.upper()],
        time_in_force=TimeInForce.DAY,
        qty=qty,
        notional=notional,
        client_order_id=client_order_id,
    )
    order = client.submit_order(request)
    return _order_dict(order)


def close_symbol_position(symbol: str, client_order_id: str) -> dict[str, Any]:
    """A full exit via Alpaca's own close_position(symbol) -- exact qty,
    avoids the float-precision drift a hand-computed qty could accumulate.
    Same kill-switch defense-in-depth as submit_market_order."""
    from engine import kill_switch

    if kill_switch.is_active():
        raise RuntimeError("Kill switch is active; refusing to submit orders.")

    client, reason = trading_client()
    if client is None:
        raise RuntimeError(reason)

    # close_options=None (the default) means "close the entire position" --
    # ClosePositionRequest itself requires qty or percentage and would
    # reject an empty call, so it's deliberately not constructed here.
    order = client.close_position(symbol)
    return _order_dict(order)


def get_order_status(alpaca_order_id: str) -> dict[str, Any]:
    """For engine/execution.py:reconcile_open_orders() to refresh a
    previously-submitted order's fill status."""
    client, reason = trading_client()
    if client is None:
        return {"available": False, "reason": reason}
    order = client.get_order_by_id(alpaca_order_id)
    return {"available": True, **_order_dict(order)}


def _order_dict(order: Any) -> dict[str, Any]:
    """Same camelCase mapping convention get_recent_orders() already uses."""
    return {
        "id": str(order.id),
        "clientOrderId": order.client_order_id,
        "symbol": order.symbol,
        "side": str(order.side).rsplit(".", maxsplit=1)[-1].lower(),
        "qty": float(order.qty) if order.qty is not None else None,
        "notional": float(order.notional) if order.notional is not None else None,
        "status": str(order.status).rsplit(".", maxsplit=1)[-1].lower(),
        "submittedAt": order.submitted_at.isoformat() if order.submitted_at else None,
        "filledAt": order.filled_at.isoformat() if order.filled_at else None,
        "filledQty": float(order.filled_qty) if order.filled_qty is not None else None,
        "filledAvgPrice": float(order.filled_avg_price) if order.filled_avg_price else None,
    }
