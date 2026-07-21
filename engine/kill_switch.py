"""Kill switch for automated paper-order execution -- CLAUDE.md's "Live
trading safety guardrails": "a single command/button that immediately
stops all new order submission and optionally flattens open positions,
reachable even if the UI is unresponsive (e.g., a CLI script that hits
Alpaca's API directly)."

File-based flag, not a DB row: it needs to be checkable and settable via
a bare `python -m engine.kill_switch` from a terminal even if the FastAPI
server (and therefore engine/execution_db.py's usual access path) is
completely down. A DB write still works standalone too, but a flag file
is the simplest thing that provably can't depend on anything else in this
project being alive.

`engine/alpaca_trading.py:submit_market_order`/`close_symbol_position`
both check `is_active()` themselves, as defense-in-depth on the one place
that actually talks to the broker's order endpoint -- callers (like
engine/execution.py) also check it earlier to log a clean
'blocked_kill_switch' status rather than an exception, but the broker-facing
functions never trust that they were called correctly.
"""

from __future__ import annotations

import argparse
from pathlib import Path

FLAG_PATH = Path(__file__).resolve().parent.parent / "logs" / "kill_switch.flag"


def is_active() -> bool:
    return FLAG_PATH.exists()


def activate(flatten: bool = False) -> dict:
    """Sets the flag FIRST, unconditionally -- stopping new order
    submission must not depend on the Alpaca call below succeeding (e.g.
    if Alpaca is unreachable, the flag must still be set)."""
    FLAG_PATH.parent.mkdir(exist_ok=True)
    FLAG_PATH.touch()
    result: dict = {"flagSet": True, "flattened": False, "error": None}
    if flatten:
        from engine import alpaca_trading

        client, reason = alpaca_trading.trading_client()
        if client is None:
            result["error"] = reason
        else:
            try:
                client.close_all_positions(cancel_orders=True)
                result["flattened"] = True
            except Exception as exc:  # noqa: BLE001 -- surface any failure, don't crash the kill switch itself
                result["error"] = str(exc)
    return result


def deactivate() -> None:
    FLAG_PATH.unlink(missing_ok=True)


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Kill switch for automated paper-order execution. "
        "Works standalone even if the app's server isn't running."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--activate", action="store_true", help="Block all new order submission.")
    group.add_argument("--deactivate", action="store_true", help="Clear the kill switch.")
    group.add_argument("--status", action="store_true", help="Report whether it's currently active.")
    parser.add_argument(
        "--flatten", action="store_true",
        help="With --activate: also cancel open orders and close all positions immediately.",
    )
    args = parser.parse_args()

    if args.status:
        print("ACTIVE" if is_active() else "inactive")
    elif args.activate:
        result = activate(flatten=args.flatten)
        print(f"Kill switch ACTIVATED. {result}")
    elif args.deactivate:
        deactivate()
        print("Kill switch deactivated.")


if __name__ == "__main__":
    _main()
