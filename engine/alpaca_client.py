"""Shared Alpaca market-data client + env-var resolution.

Both engine/data.py (historical intraday bars for backtests) and
engine/quotes.py (live watchlist quotes) need an Alpaca client. Putting the
builder here keeps them from importing each other (quotes.py already imports
data.py) and gives one place that knows the accepted key names.

Market data is the same API for paper and live accounts, so either key pair
works. On the free tier this is the IEX feed: real prices, but only
IEX-routed volume (~2-3% of consolidated). Prices for liquid names are
representative; volume is a partial sample -- see LESSONS.md.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()


def first_env(*names: str) -> str | None:
    """First non-empty environment variable among `names` (trimmed)."""
    for name in names:
        val = os.getenv(name)
        if val and val.strip():
            return val.strip()
    return None


def _credentials() -> tuple[str | None, str | None]:
    # Accept this project's names, the common ALPACA_API_SECRET variant, and
    # Alpaca's canonical APCA_* names, so a paste under any of them just works.
    api_key = first_env(
        "ALPACA_API_KEY", "ALPACA_PAPER_API_KEY", "APCA_API_KEY_ID", "ALPACA_API_KEY_ID"
    )
    secret_key = first_env(
        "ALPACA_SECRET_KEY", "ALPACA_API_SECRET", "ALPACA_PAPER_SECRET_KEY",
        "APCA_API_SECRET_KEY", "ALPACA_SECRET",
    )
    return api_key, secret_key


@lru_cache(maxsize=1)
def market_data_client() -> tuple[Any | None, str]:
    """Lazily build a single StockHistoricalDataClient. Returns
    (client_or_None, reason). Cached so env/imports aren't re-probed per call.
    Never raises -- callers branch on the None to degrade gracefully."""
    api_key, secret_key = _credentials()
    if not api_key or not secret_key:
        missing = "key" if not api_key else "secret"
        return None, (
            f"Alpaca {missing} missing from .env. Need a key line "
            "(ALPACA_API_KEY=...) and a secret line (ALPACA_SECRET_KEY=... "
            "or ALPACA_API_SECRET=...)."
        )
    try:
        from alpaca.data.historical import StockHistoricalDataClient
    except ImportError:
        return None, "alpaca-py is not installed (pip install alpaca-py)."
    try:
        return StockHistoricalDataClient(api_key, secret_key), "ok"
    except Exception as exc:  # noqa: BLE001 -- surface any init failure to the UI
        return None, f"Failed to initialize Alpaca client: {exc}"


def available() -> tuple[bool, str]:
    client, reason = market_data_client()
    return client is not None, reason
