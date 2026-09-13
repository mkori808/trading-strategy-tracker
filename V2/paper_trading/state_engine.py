"""Local virtual-fill state tracking, fully decoupled from the real Alpaca
paper account and from V1/paper_trading's ledger/holdings_*.json files.

Why this exists: the real Alpaca paper account already holds 5 unrelated
legacy positions (CBRL, CXW, DFIN, EAT, HZO) from an earlier, unrelated
strategy test, and V1's ledger/holdings_*.json files were pre-populated
with a full target portfolio before any order was ever submitted --
neither reflects a clean starting point. Per instruction, this module
ignores both entirely: it tracks its own {cash, current_holdings,
nav_history} per track in state/<track>_state.json, starting from
notional cash and zero holdings, and never calls Alpaca's order-submission
endpoints -- only market-data endpoints (to price Monday's virtual fills).

Fill scheme (as specified):
  - Friday: --record computes the target portfolio and its Friday close
    prices, and saves it as a `pending` block in the state file. Does NOT
    touch cash/current_holdings yet.
  - Monday (after 10am): --fill fetches each pending ticker's first
    1-minute bar after 9:30 ET open from Alpaca market data and fills at
    that price. If Monday data is unavailable for a ticker (halt/gap/
    delisting), falls back to the recorded Friday close and flags the fill
    DATA_UNAVAILABLE -- never crashes, never silently skips.
  - Fill quality and modeled transaction cost are recorded as two SEPARATE
    line items, not blended into one number:
      realized_slippage_bps = (monday_open / friday_close - 1) * 10000
      modeled_cost_bps      = 10  (constant, by assumption -- config.json's
                                    round-trip cost model, applied here
                                    per-trade as the user's spec directs)
    The fill price itself (not a separate slippage adjustment) is what
    hits cash; modeled_cost is an additional, explicit drag on top of that.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone, time as clock_time
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd

import rebalance_engine as engine

STATE_DIR = Path(__file__).resolve().parent / "state"
TRACKS = ["ibs_standalone", "composite_v2", "composite_v3_1", "composite_v3_2"]
ET = ZoneInfo("America/New_York")
MODELED_COST_BPS = 10.0  # applied per trade (buy or sell), per the user's explicit formula


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=str)
    os.replace(tmp, path)  # atomic on both POSIX and Windows


def state_path(track: str) -> Path:
    return STATE_DIR / f"{track}_state.json"


def init_fresh_state(track: str, notional: float, today: pd.Timestamp) -> dict:
    """Overwrites any existing state file with a clean slate. Only call
    this deliberately -- it discards whatever was there (legacy ledger
    state is a separate, untouched system; this only ever affects this
    module's own state files)."""
    track_cfg = engine.load_config().get("tracks", {}).get(track, {})
    state = {
        "inception_date": today.date().isoformat(),
        "specification_version": track_cfg.get("specification_version", track),
        "specification_sha256": track_cfg.get("specification_sha256"),
        "notional": notional,
        "current_holdings": {},
        "cash": notional,
        "nav_history": [],
        "fill_history": [],
        "implementation_failures": [],
    }
    _atomic_write_json(state_path(track), state)
    return state


def load_state(track: str) -> dict:
    return json.loads(state_path(track).read_text())


def save_state(track: str, state: dict) -> None:
    _atomic_write_json(state_path(track), state)


def record_target(track: str, cfg: dict, as_of: pd.Timestamp) -> dict:
    """Computes this Friday's target portfolio and stashes it as `pending`
    in the track's state file for Monday's apply_fill(). Does not touch
    cash/current_holdings -- those only change once a fill is applied."""
    result = engine.build_universe_and_score(track, cfg, as_of)
    held = engine.construct_portfolio(result, cfg["portfolio"]["notional_usd"])
    C = result["panel"]["C"]

    friday_close = {}
    for t in held.index:
        px = C.loc[as_of, t] if (as_of in C.index and t in C.columns) else None
        friday_close[t] = float(px) if pd.notna(px) else None
    missing_price = sorted(t for t, p in friday_close.items() if p is None)

    state = load_state(track)
    state["pending"] = {
        "as_of": as_of.date().isoformat(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "target_notional": {t: float(held.loc[t, "notional"]) for t in held.index},
        "friday_close": friday_close,
    }
    save_state(track, state)

    checks = engine.sanity_checks(result, held)
    return {
        "track": track, "as_of": as_of, "held": held, "friday_close": friday_close,
        "missing_price": missing_price, "checks": checks, "result": result,
    }


def _monday_open_price(client, ticker: str, monday: pd.Timestamp) -> tuple[float | None, str]:
    """First 1-minute bar's open at/after 9:30 ET on `monday`. Returns
    (price, 'OK') or (None, 'DATA_UNAVAILABLE') -- never raises for a
    single-ticker failure (halt, gap, delisting, feed hiccup)."""
    try:
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from alpaca.data.enums import DataFeed, Adjustment
        start = pd.Timestamp.combine(monday.date(), clock_time(9, 30)).tz_localize(ET)
        end = start + pd.Timedelta(minutes=10)
        req = StockBarsRequest(symbol_or_symbols=ticker, timeframe=TimeFrame.Minute, start=start, end=end, feed=DataFeed.IEX, adjustment=Adjustment.RAW)
        bars = client.get_stock_bars(req).data.get(ticker, [])
        bars = sorted(bars, key=lambda b: b.timestamp)
        bars = [b for b in bars if b.timestamp.astimezone(ET).time() >= clock_time(9, 30)]
        if bars:
            return float(bars[0].open), "OK"
    except Exception:
        pass
    return None, "DATA_UNAVAILABLE"


def apply_fill(track: str, market_data_client, monday: pd.Timestamp) -> dict:
    """Monday, after 10am. Reads the pending target, virtually fills every
    name at Monday's actual open (Friday close + DATA_UNAVAILABLE flag on
    missing data), updates cash/current_holdings/nav_history, and clears
    `pending`. Never calls any order-submission endpoint."""
    state = load_state(track)
    pending = state.get("pending")
    if not pending:
        raise RuntimeError(f"{track}: no pending target -- run --record first")

    target_notional = pending["target_notional"]
    friday_close = pending["friday_close"]
    current = dict(state["current_holdings"])
    all_tickers = sorted(set(target_notional) | set(current))

    fills = []
    fill_prices = {}
    for ticker in all_tickers:
        px, status = _monday_open_price(market_data_client, ticker, monday)
        fclose = friday_close.get(ticker)
        if px is None:
            px = fclose
            status = "DATA_UNAVAILABLE"
        fill_prices[ticker] = px

        target_usd = target_notional.get(ticker, 0.0)
        target_shares = (target_usd / px) if px else 0.0
        cur_shares = current.get(ticker, 0.0)
        delta_shares = target_shares - cur_shares
        if abs(delta_shares) < 1e-9:
            continue
        gross = abs(delta_shares) * (px or 0.0)
        realized_slippage_bps = ((px / fclose) - 1) * 10000 if (px and fclose and status == "OK") else None
        modeled_cost = gross * (MODELED_COST_BPS / 10_000)

        state["cash"] -= delta_shares * (px or 0.0)  # buy (delta>0) spends cash, sell (delta<0) raises it
        state["cash"] -= modeled_cost
        current[ticker] = target_shares if target_usd else 0.0

        fills.append({
            "ticker": ticker, "status": status, "fill_price": px, "friday_close": fclose,
            "delta_shares": delta_shares, "gross_notional": gross,
            "realized_slippage_bps": realized_slippage_bps, "modeled_cost_bps": MODELED_COST_BPS,
            "total_cost_bps": (realized_slippage_bps + MODELED_COST_BPS) if realized_slippage_bps is not None else None,
        })

    current = {t: s for t, s in current.items() if abs(s) > 1e-9}
    state["current_holdings"] = current
    holdings_value = sum(shares * (fill_prices.get(t) or 0.0) for t, shares in current.items())
    nav = state["cash"] + holdings_value
    modeled_cost_usd = float(sum(f["gross_notional"] * f["modeled_cost_bps"] / 10_000 for f in fills))
    slippage_values = [f["realized_slippage_bps"] for f in fills if f["realized_slippage_bps"] is not None]
    failures = [f["ticker"] for f in fills if f["status"] != "OK"]
    fill_summary = {
        "date": monday.date().isoformat(),
        "trade_count": len(fills),
        "turnover_notional": float(sum(f["gross_notional"] for f in fills)),
        "modeled_transaction_cost_usd": modeled_cost_usd,
        "estimated_slippage_bps_mean": (float(sum(slippage_values) / len(slippage_values)) if slippage_values else None),
        "implementation_failure_count": len(failures),
        "implementation_failure_tickers": failures,
    }
    state.setdefault("fill_history", []).append(fill_summary)
    if failures:
        state.setdefault("implementation_failures", []).append({
            "date": monday.date().isoformat(),
            "type": "DATA_UNAVAILABLE",
            "tickers": failures,
        })
    state["nav_history"].append({
        "date": monday.date().isoformat(), "nav": nav, "cash": state["cash"],
        "holdings_value": holdings_value, "modeled_transaction_cost_usd": modeled_cost_usd,
    })
    state["pending"] = None
    save_state(track, state)
    return {"track": track, "monday": monday, "fills": fills, "nav": nav, "cash": state["cash"]}
