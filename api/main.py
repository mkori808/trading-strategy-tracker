"""FastAPI backend exposing the existing backtest engine over JSON.

The React frontend (webapp/) talks only to this API -- it never touches
pandas/backtesting.py directly. This is a thin serialization layer; all
strategy/backtest/metrics logic stays in engine/ and strategies/ unchanged.

Run with: uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine import (
    alpaca_trading,
    chat_assistant,
    data_edgar,
    digest as digest_module,
    live_scanner,
    market_overview,
    movers as movers_module,
    screener as screener_module,
    signals_db,
)
from engine import quotes as quotes_module
from engine.logging_db import (
    best_portfolio_run_per_strategy,
    best_run_per_strategy,
    portfolio_run_history,
    run_history,
)
from engine.metrics import compute_metrics, derive_status
from engine.portfolio import run_portfolio_backtest
from engine.runner import (
    SYMBOL_OVERRIDE_DISALLOWED_NAMES,
    RunRequest,
    is_cross_sectional,
    is_pairs,
    run_backtest,
    run_config,
    run_cross_sectional,
    run_pairs,
    strategy_class,
)
from engine.universe import RESEARCH_UNIVERSE
from strategies.params import describe_params
from strategies.registry import ALL_STRATEGY_NAMES, DAY_TRADING_STRATEGIES

MAX_CUSTOM_SYMBOLS = 60

# Matches the day-trading strategies' own 5-min bar timeframe -- see
# engine/live_scanner.py's module docstring on why polling faster than that
# buys no new information against the free-tier IEX feed's ~16min delay.
SCAN_INTERVAL_SECONDS = 5 * 60

logger = logging.getLogger("uvicorn.error")

# asyncio only holds a WEAK reference to a task -- if nothing else references
# it, the event loop is free to garbage-collect it mid-run. This module-level
# reference is what keeps the background scanner alive for the process's
# lifetime instead of vanishing silently after an unpredictable delay.
_scan_task: asyncio.Task | None = None

# Same GC-safety reasoning as _scan_task above, for the one-shot insider
# Form-4 refresh job -- plus a small status dict (not persisted) so the
# Movers tab can show "last refreshed"/"refreshing now" without polling a
# task object directly.
_insider_refresh_task: asyncio.Task | None = None
_insider_refresh_state: dict[str, Any] = {
    "running": False, "lastCompletedAt": None, "lastError": None,
}

# How far back a manual insider-refresh looks -- generous enough to catch
# anything recent without re-scanning years of filing history on every click
# (fetch_form4_for_universe is idempotent/cached regardless, but a narrower
# window means fewer EDGAR pages requested per refresh).
INSIDER_REFRESH_LOOKBACK_DAYS = 90

app = FastAPI(title="Trading Strategy Lab API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _start_live_scanner() -> None:
    """Background loop calling engine/live_scanner.py:scan_once() on a fixed
    interval. scan_once() itself no-ops (returns immediately) when Alpaca
    isn't configured or the market is closed, so this is a harmless idle
    loop outside trading hours or with no keys set."""

    async def _loop() -> None:
        while True:
            try:
                await asyncio.to_thread(live_scanner.scan_once)
            except Exception:  # noqa: BLE001 -- a scan failure must not kill the server
                logger.exception("live_scanner.scan_once() failed")
            await asyncio.sleep(SCAN_INTERVAL_SECONDS)

    global _scan_task
    _scan_task = asyncio.create_task(_loop())


def _clean(value: Any) -> Any:
    """Replace NaN/inf with None -- Python's json module emits the literal
    (non-standard) tokens NaN/Infinity for these, which JS's JSON.parse
    rejects outright."""
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _strategy_kind(name: str) -> str:
    return "Day Trading" if name in DAY_TRADING_STRATEGIES else "Swing Trading"


def _run_config_fields(row: Any) -> dict:
    """symbols/date-range/params for the run a leaderboard row came from --
    same `runs`/`portfolio_runs` columns /api/history/{name} already
    serializes, pulled into the leaderboard row too so the Compare tab can
    show what configuration actually produced the displayed score without
    needing to open the run-history table first."""
    if row is None:
        return {"symbols": [], "startDate": None, "endDate": None, "params": {}}
    return {
        "symbols": json.loads(row["symbols"]) if row["symbols"] else [],
        "startDate": row["start_date"],
        "endDate": row["end_date"],
        "params": json.loads(row["params"]) if row["params"] else {},
    }


def _strategy_engine(name: str) -> str:
    """Which backtest engine `name` runs on -- the standard per-symbol one
    (bracket-order trades, R-multiples), or one of the two whole-universe/
    two-leg engines that use a different result shape entirely and are only
    reachable through /api/backtest/cross-sectional or /api/backtest/pairs
    (see engine/cross_sectional.py, engine/pairs.py)."""
    if is_cross_sectional(name):
        return "cross_sectional"
    if is_pairs(name):
        return "pairs"
    return "standard"


class BacktestOverrides(BaseModel):
    """Optional body for POST /api/backtest/{name} -- the Lab tab's "test a
    variation" request. Every field is optional; an absent/empty body is
    exactly today's canonical run (see engine/runner.py:RunRequest)."""

    symbols: list[str] | None = None
    start: str | None = None
    end: str | None = None
    params: dict[str, Any] | None = None


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    """Body for POST /api/chat -- `result` is the exact JSON a prior
    POST /api/backtest/{name} call returned (the frontend already has it in
    state for whichever result is on screen), `messages` is the conversation
    so far. See engine/chat_assistant.py: this endpoint is stateless -- the
    frontend, not the backend, is the source of truth for chat history."""

    result: dict[str, Any]
    messages: list[ChatMessage]


def _validate_symbols(strategy_name: str, symbols: list[str] | None) -> list[str] | None:
    if symbols is None:
        return None
    if strategy_name in SYMBOL_OVERRIDE_DISALLOWED_NAMES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{strategy_name!r}'s universe is structural (sector ETFs "
                "ranked against SPY) and can't be overridden."
            ),
        )
    cleaned = sorted({s.strip().upper() for s in symbols if s.strip()})
    if not cleaned:
        raise HTTPException(status_code=400, detail="symbols override must be non-empty")
    if len(cleaned) > MAX_CUSTOM_SYMBOLS:
        raise HTTPException(
            status_code=400,
            detail=f"symbols override is capped at {MAX_CUSTOM_SYMBOLS} tickers, got {len(cleaned)}",
        )
    return cleaned


def _validate_dates(start: str | None, end: str | None) -> tuple[date | None, date | None]:
    try:
        parsed_start = date.fromisoformat(start) if start else None
        parsed_end = date.fromisoformat(end) if end else None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid date: {e}") from e
    if parsed_start and parsed_end and parsed_start >= parsed_end:
        raise HTTPException(status_code=400, detail="start must be before end")
    if parsed_end and parsed_end > date.today():
        raise HTTPException(status_code=400, detail="end cannot be in the future")
    return parsed_start, parsed_end


def _sparkline(equity_curve: pd.DataFrame | None, points: int = 40) -> list[float]:
    """Downsample an equity curve to a short list of values for an inline
    sparkline (the detail chart carries the full-resolution curve)."""
    if equity_curve is None or equity_curve.empty:
        return []
    series = equity_curve["Equity"]
    if len(series) <= points:
        return [float(v) for v in series.values]
    step = len(series) / points
    return [float(series.iloc[min(int(i * step), len(series) - 1)]) for i in range(points)]


def _per_symbol_rows(result: Any) -> list[dict]:
    """One row per universe symbol, so the UI can show the dispersion the
    pooled metrics hide -- a strategy can be strong on a few names and awful
    on the rest. Symbols that never traded are still listed (trades == 0)."""
    rows = []
    for symbol, r in result.per_symbol.items():
        stats = r.stats
        m = compute_metrics(
            strategy_name=result.strategy_name,
            symbol=symbol,
            trades=r.trades,
            start=result.start,
            end=result.end,
            sharpe=None if stats is None else stats.get("Sharpe Ratio"),
            max_drawdown_pct=None if stats is None else abs(stats.get("Max. Drawdown [%]", float("nan"))),
        )
        pnl = float(r.trades["PnL"].sum()) if not r.trades.empty else 0.0
        rows.append({
            "symbol": symbol,
            "tradesTaken": m.trades_taken,
            "winRate": m.win_rate if m.trades_taken else None,
            "expectancyR": m.expectancy_r if m.trades_taken else None,
            "profitFactor": m.profit_factor if m.trades_taken else None,
            "pnl": pnl,
            "returnPct": None if stats is None else float(stats.get("Return [%]", float("nan"))),
            "buyHoldReturnPct": (
                None if stats is None else float(stats.get("Buy & Hold Return [%]", float("nan")))
            ),
            "sharpe": m.sharpe,
            "sparkline": _sparkline(r.equity_curve),
        })
    # Best expectancy first; symbols that never traded sink to the bottom.
    rows.sort(key=lambda r: (r["tradesTaken"] > 0, r["expectancyR"] or float("-inf")), reverse=True)
    return rows


def _excursion_lookup(excursions: pd.DataFrame) -> dict[tuple[str, Any, Any], pd.Series]:
    """(symbol, EntryTime, ExitTime) -> that trade's excursion row, so the
    trades list (built from each symbol's raw backtesting.py trades frame)
    can attach MFE/MAE/exit-quality fields without re-deriving them -- see
    engine/excursion.py. A trade with no match (e.g. its excursion row was
    dropped for failing the MFE>=realized_r sanity check) just renders
    without these fields rather than erroring."""
    if excursions.empty:
        return {}
    return {
        (row["Symbol"], row["EntryTime"], row["ExitTime"]): row
        for _, row in excursions.iterrows()
    }


def _exc_field(exc: pd.Series | None, key: str) -> float | None:
    return None if exc is None else float(exc[key])


def _excursion_summary(excursions: pd.DataFrame) -> dict:
    """Headline MFE/MAE diagnostics for the whole run -- see
    engine/excursion.py's write_excursion_report, which computes the same
    numbers for the on-disk report. Exit efficiency only makes sense for
    winners, loss realization ratio only for losers (see engine/excursion.py)."""
    if excursions.empty:
        return {
            "tradesWithData": 0,
            "meanExitEfficiencyPct": None,
            "medianExitEfficiencyPct": None,
            "meanLossRealizationRatioPct": None,
            "medianLossRealizationRatioPct": None,
        }
    exit_eff = excursions.loc[excursions["RealizedR"] > 0, "ExitEfficiencyPct"].dropna()
    loss_ratio = excursions.loc[excursions["RealizedR"] < 0, "LossRealizationRatioPct"].dropna()
    return {
        "tradesWithData": len(excursions),
        "meanExitEfficiencyPct": float(exit_eff.mean()) if not exit_eff.empty else None,
        "medianExitEfficiencyPct": float(exit_eff.median()) if not exit_eff.empty else None,
        "meanLossRealizationRatioPct": float(loss_ratio.mean()) if not loss_ratio.empty else None,
        "medianLossRealizationRatioPct": float(loss_ratio.median()) if not loss_ratio.empty else None,
    }


def _portfolio_strategy_row(name: str, row: Any) -> dict:
    """Row shape for cross-sectional (Dual Momentum) / pairs (Pairs / Stat
    Arb) strategies -- these never had a discrete-trade result, so the
    R-multiple fields (win rate, avg win/loss R, expectancy, profit factor,
    alpha, beta) are structurally not applicable, always null. Was
    previously always the `row is None` branch below for these two names,
    since engine/runner.py's run_cross_sectional/run_pairs never logged
    anywhere -- "most recent run" could never update no matter how many
    times you ran them. See engine/logging_db.py's portfolio_runs table."""
    if row is None:
        return {
            "name": name,
            "kind": _strategy_kind(name),
            "engine": _strategy_engine(name),
            # None, not 0: "no discrete-trade concept," which is different
            # from "traded zero times." The UI renders it as "--".
            "tradesTaken": None,
            "winRate": None,
            "avgWinR": None,
            "avgLossR": None,
            "expectancyR": None,
            "profitFactor": None,
            "status": "Not yet tested",
            "lastRun": None,
            "sharpe": None,
            "alphaPct": None,
            "beta": None,
            "cagrPct": None,
            "returnPct": None,
            "maxDrawdownPct": None,
            "benchmarkReturnPct": None,
            **_run_config_fields(None),
        }
    return {
        "name": name,
        "kind": _strategy_kind(name),
        "engine": _strategy_engine(name),
        "tradesTaken": None,
        "winRate": None,
        "avgWinR": None,
        "avgLossR": None,
        "expectancyR": None,
        "profitFactor": None,
        # Runs logged since 2026-07-20 carry a real verdict from
        # engine/metrics.py:portfolio_status(); older rows (and runs with no
        # meaningful verdict, e.g. Pairs finding no cointegrated pair) keep
        # the pre-verdict "Backtested" label.
        "status": row["status"] or "Backtested",
        "lastRun": row["run_at"],
        "sharpe": row["sharpe"],
        "alphaPct": None,
        "beta": None,
        "cagrPct": row["cagr_pct"],
        "returnPct": row["return_pct"],
        "maxDrawdownPct": row["max_drawdown_pct"],
        "benchmarkReturnPct": row["benchmark_return_pct"],
        **_run_config_fields(row),
    }


@app.get("/api/strategies")
def list_strategies() -> list[dict]:
    # Compare tab leaderboard: best-Sharpe CANONICAL run per strategy, not
    # merely the most recent one -- see engine/logging_db.py's
    # best_run_per_strategy() docstring for why (still canonical-only; a
    # Lab-tab experiment can never surface here).
    latest = best_run_per_strategy()
    latest_portfolio = best_portfolio_run_per_strategy()
    rows = []
    for name in ALL_STRATEGY_NAMES:
        # Cross-sectional (Dual Momentum) and pairs (Pairs / Stat Arb)
        # strategies run on a different engine with a different result shape
        # -- reachable through /api/backtest/cross-sectional and
        # /api/backtest/pairs respectively, not /api/params or the standard
        # /api/backtest -- and log to a separate table (see
        # engine/logging_db.py's portfolio_runs). Surfacing which engine
        # here lets the UI pick the right endpoint/result view instead of
        # guessing from the name.
        if _strategy_engine(name) != "standard":
            rows.append(_portfolio_strategy_row(name, latest_portfolio.get(name)))
            continue

        row = latest.get(name)
        if row is None:
            rows.append({
                "name": name,
                "kind": _strategy_kind(name),
                "engine": "standard",
                "tradesTaken": 0,
                "winRate": None,
                "avgWinR": None,
                "avgLossR": None,
                "expectancyR": None,
                "profitFactor": None,
                "status": "Not yet tested",
                "lastRun": None,
                "sharpe": None,
                "alphaPct": None,
                "beta": None,
                "cagrPct": None,
                "returnPct": None,
                "maxDrawdownPct": None,
                "benchmarkReturnPct": None,
                **_run_config_fields(None),
            })
        else:
            rows.append({
                "name": name,
                "kind": _strategy_kind(name),
                "engine": "standard",
                "tradesTaken": row["trades_taken"],
                "winRate": row["win_rate"],
                "avgWinR": row["avg_win_r"],
                "avgLossR": row["avg_loss_r"],
                "expectancyR": row["expectancy_r"],
                "profitFactor": row["profit_factor"],
                # Recomputed from the row's stored NUMBERS with current
                # logic, not the status string logged at run time -- see
                # engine/metrics.py:derive_status()'s docstring (a stale
                # pre-Sharpe-gate 'shortlist' string was surfacing here).
                "status": derive_status(
                    row["trades_taken"], row["expectancy_r"],
                    row["sharpe"], row["alpha_pct"],
                ),
                "lastRun": row["run_at"],
                "sharpe": row["sharpe"],
                "alphaPct": row["alpha_pct"],
                "beta": row["beta"],
                "cagrPct": row["cagr_pct"],
                "returnPct": None,
                "maxDrawdownPct": row["max_drawdown_pct"],
                "benchmarkReturnPct": None,
                **_run_config_fields(row),
            })
    return _clean(rows)


@app.get("/api/params/{strategy_name:path}")
def strategy_params(strategy_name: str) -> dict:
    """Schema for the Lab tab's run-configuration form: the default
    universe/date range this strategy runs with, whether that universe can
    be overridden, and every tunable rule parameter (see
    strategies/params.py). Cross-sectional (Dual Momentum) and pairs
    (Pairs / Stat Arb) strategies get a real schema here too -- their
    dataclasses already declare param_field() tunables, describe_params()
    doesn't care which engine runs the result, and engine/runner.py's
    run_cross_sectional/run_pairs now accept a RunRequest the same as
    run_backtest -- see /api/backtest/cross-sectional and /api/backtest/
    pairs below for where an override submitted from this schema goes."""
    if strategy_name not in ALL_STRATEGY_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown strategy {strategy_name!r}")

    interval, symbols, start, end = run_config(strategy_name)
    specs = describe_params(strategy_class(strategy_name))
    return _clean({
        "strategyName": strategy_name,
        "interval": interval,
        "symbolsDefault": symbols,
        "startDefault": start.isoformat(),
        "endDefault": end.isoformat(),
        "symbolOverrideAllowed": strategy_name not in SYMBOL_OVERRIDE_DISALLOWED_NAMES,
        "params": [
            {
                "name": s.name,
                "label": s.label,
                "kind": s.kind,
                "default": s.default,
                "minimum": s.minimum,
                "maximum": s.maximum,
                "step": s.step,
                "help": s.help,
            }
            for s in specs
        ],
    })


@app.post("/api/backtest/cross-sectional/{strategy_name:path}")
def run_cross_sectional_endpoint(strategy_name: str, overrides: BacktestOverrides | None = None) -> dict:
    """Dual Momentum et al -- accepts the same optional overrides body as
    the standard /api/backtest/{name}, now that engine/runner.py:
    run_cross_sectional takes a RunRequest.

    Registered BEFORE the generic /api/backtest/{strategy_name:path} route
    below -- FastAPI matches path routes in registration order, and that
    route's own `:path` converter would otherwise greedily swallow
    'cross-sectional/Dual Momentum' as a single (wrong) strategy_name."""
    if not is_cross_sectional(strategy_name):
        raise HTTPException(
            status_code=404, detail=f"{strategy_name!r} isn't a cross-sectional strategy"
        )
    request: RunRequest | None = None
    if overrides is not None and (
        overrides.symbols or overrides.start or overrides.end or overrides.params
    ):
        symbols = _validate_symbols(strategy_name, overrides.symbols)
        start, end = _validate_dates(overrides.start, overrides.end)
        request = RunRequest(symbols=symbols, start=start, end=end, params=overrides.params)

    try:
        result = run_cross_sectional(strategy_name, request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _clean({
        "strategyName": result.strategy_name,
        "symbols": result.symbols,
        "start": result.start.isoformat(),
        "end": result.end.isoformat(),
        "appliedSymbols": result.symbols,
        "appliedParams": request.params if request else None,
        "equityCurve": [
            {"time": ts.isoformat(), "equity": float(v)}
            for ts, v in result.equity_curve.items()
        ],
        "rebalances": [
            {
                "date": row["date"].isoformat(),
                "holdings": {k: float(v) for k, v in row["holdings"].items()},
            }
            for _, row in result.rebalances.iterrows()
        ],
        "finalEquity": result.final_equity,
        "returnPct": result.return_pct,
        "cagrPct": result.cagr_pct,
        "maxDrawdownPct": result.max_drawdown_pct,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "riskFreeRate": result.risk_free_rate,
    })


@app.post("/api/backtest/pairs/{strategy_name:path}")
def run_pairs_endpoint(strategy_name: str, overrides: BacktestOverrides | None = None) -> dict:
    """Pairs / Stat Arb -- accepts the same optional overrides body as the
    cross-sectional endpoint above, now that engine/runner.py:run_pairs
    takes a RunRequest. `pair` is None when no cointegrated pair cleared the
    significance threshold in the training half of the window; the frontend
    must handle that as a real empty state, not an error. Also registered
    before the generic route below, for the same route-ordering reason."""
    if not is_pairs(strategy_name):
        raise HTTPException(status_code=404, detail=f"{strategy_name!r} isn't a pairs strategy")
    request: RunRequest | None = None
    if overrides is not None and (
        overrides.symbols or overrides.start or overrides.end or overrides.params
    ):
        symbols = _validate_symbols(strategy_name, overrides.symbols)
        start, end = _validate_dates(overrides.start, overrides.end)
        request = RunRequest(symbols=symbols, start=start, end=end, params=overrides.params)

    try:
        result = run_pairs(strategy_name, request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _clean({
        "strategyName": result.strategy_name,
        "pair": None if result.pair is None else {
            "symbolA": result.pair.symbol_a,
            "symbolB": result.pair.symbol_b,
            "pValue": result.pair.p_value,
        },
        "symbols": result.symbols,
        "appliedSymbols": result.symbols,
        "appliedParams": request.params if request else None,
        "trainingWindow": [result.training_window[0].isoformat(), result.training_window[1].isoformat()],
        "tradingWindow": [result.trading_window[0].isoformat(), result.trading_window[1].isoformat()],
        "equityCurve": [
            {"time": ts.isoformat(), "equity": float(v)}
            for ts, v in result.equity_curve.items()
        ],
        "trades": [
            {
                "entryTime": row["EntryTime"].isoformat(),
                "exitTime": row["ExitTime"].isoformat(),
                "pair": row["Pair"],
                "position": row["Position"],
                "pnl": float(row["PnL"]),
                "reason": row["Reason"],
            }
            for _, row in result.trades.iterrows()
        ],
        "finalEquity": result.final_equity,
        "returnPct": result.return_pct,
        "cagrPct": result.cagr_pct,
        "maxDrawdownPct": result.max_drawdown_pct,
        "sharpe": result.sharpe,
        "sortino": result.sortino,
        "riskFreeRate": result.risk_free_rate,
    })


@app.post("/api/backtest/{strategy_name:path}")
def run(strategy_name: str, overrides: BacktestOverrides | None = None) -> dict:
    if strategy_name not in ALL_STRATEGY_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown strategy {strategy_name!r}")
    if is_cross_sectional(strategy_name):
        raise HTTPException(
            status_code=501,
            detail=(
                f"{strategy_name!r} runs on the cross-sectional engine, a different result "
                "shape than this endpoint returns. Use POST /api/backtest/cross-sectional/"
                f"{strategy_name!r} instead."
            ),
        )
    if is_pairs(strategy_name):
        raise HTTPException(
            status_code=501,
            detail=(
                f"{strategy_name!r} runs on the pairs engine, a different result shape than "
                f"this endpoint returns. Use POST /api/backtest/pairs/{strategy_name!r} instead."
            ),
        )

    request: RunRequest | None = None
    if overrides is not None and (
        overrides.symbols or overrides.start or overrides.end or overrides.params
    ):
        symbols = _validate_symbols(strategy_name, overrides.symbols)
        start, end = _validate_dates(overrides.start, overrides.end)
        request = RunRequest(symbols=symbols, start=start, end=end, params=overrides.params)

    try:
        result = run_backtest(strategy_name, request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    m = result.metrics

    equity_symbol = next(
        (s for s, r in result.per_symbol.items() if r.equity_curve is not None), None
    )
    equity_curve = []
    if equity_symbol is not None:
        eq = result.per_symbol[equity_symbol].equity_curve["Equity"]
        equity_curve = [
            {"time": ts.isoformat(), "equity": float(v)} for ts, v in eq.items()
        ]

    excursion_lookup = _excursion_lookup(result.excursions)
    trades = []
    for symbol, r in result.per_symbol.items():
        if r.trades.empty:
            continue
        for _, t in r.trades.iterrows():
            exc = excursion_lookup.get((symbol, t["EntryTime"], t["ExitTime"]))
            trades.append({
                "symbol": symbol,
                "entryTime": t["EntryTime"].isoformat(),
                "exitTime": t["ExitTime"].isoformat(),
                "size": float(t["Size"]),
                "entryPrice": float(t["EntryPrice"]),
                "exitPrice": float(t["ExitPrice"]),
                "sl": None if pd.isna(t["SL"]) else float(t["SL"]),
                "tp": None if pd.isna(t["TP"]) else float(t["TP"]),
                "pnl": float(t["PnL"]),
                "returnPct": float(t["ReturnPct"]),
                "realizedR": _exc_field(exc, "RealizedR"),
                "mfeR": _exc_field(exc, "MFE_R"),
                "maeR": _exc_field(exc, "MAE_R"),
                "exitEfficiencyPct": _exc_field(exc, "ExitEfficiencyPct"),
                "lossRealizationRatioPct": _exc_field(exc, "LossRealizationRatioPct"),
                "entrySlippagePct": _exc_field(exc, "EntrySlippagePct"),
            })
    trades.sort(key=lambda t: t["exitTime"])

    portfolio = run_portfolio_backtest(result, risk_free_rate=m.risk_free_rate or 0.0)
    portfolio_payload = {
        "maxConcurrentPositions": portfolio.max_concurrent_positions,
        "tradesTaken": len(portfolio.trades),
        "skippedForCapacity": portfolio.skipped_for_capacity,
        "finalEquity": portfolio.final_equity,
        "returnPct": portfolio.return_pct,
        "cagrPct": portfolio.cagr_pct,
        "maxDrawdownPct": portfolio.max_drawdown_pct,
        "sharpe": portfolio.sharpe,
        "sortino": portfolio.sortino,
        "equityCurve": [
            {"time": ts.isoformat(), "equity": float(v)}
            for ts, v in portfolio.equity_curve.items()
        ],
    }

    payload = {
        "strategyName": result.strategy_name,
        "symbols": result.symbols,
        "start": result.start.isoformat(),
        "end": result.end.isoformat(),
        "metrics": {
            "tradesTaken": m.trades_taken,
            "wins": m.wins,
            "losses": m.losses,
            "winRate": m.win_rate,
            "avgWinR": m.avg_win_r,
            "avgLossR": m.avg_loss_r,
            "expectancyR": m.expectancy_r,
            "profitFactor": m.profit_factor,
            "maxDrawdownPct": m.max_drawdown_pct,
            "sharpe": m.sharpe,
            "sortino": m.sortino,
            "alphaPct": m.alpha_pct,
            "beta": m.beta,
            "cagrPct": m.cagr_pct,
            "exposurePct": m.exposure_pct,
            "riskFreeRate": m.risk_free_rate,
            "buyHoldReturnPct": m.buy_hold_return_pct,
            "status": m.status,
        },
        "isCanonical": request is None or request.is_default(),
        "appliedSymbols": result.symbols,
        "appliedParams": request.params if request else None,
        "equitySymbol": equity_symbol,
        "equityCurve": equity_curve,
        "trades": trades,
        "perSymbol": _per_symbol_rows(result),
        "portfolio": portfolio_payload,
        "excursionSummary": _excursion_summary(result.excursions),
    }
    return _clean(payload)


@app.get("/api/symbols")
def list_symbols() -> dict:
    """Every symbol in the pre-registered universes, with offline metadata
    (last cached close, day change, liquidity tier). No network -- reads only
    what engine/data.py already cached, so this renders instantly and works
    with no Alpaca keys. Live prices come separately from /api/quotes."""
    available, reason = quotes_module.quotes_available()
    return _clean({
        "symbols": quotes_module.all_symbol_metadata(),
        "quotesAvailable": available,
        "quotesReason": reason,
    })


@app.get("/api/symbols/{ticker}")
def symbol_detail(ticker: str) -> dict:
    ticker = ticker.upper()
    meta = quotes_module.symbol_metadata(ticker)
    if not meta["universes"]:
        raise HTTPException(status_code=404, detail=f"{ticker!r} is not in any tracked universe")
    meta["history"] = quotes_module.daily_history(ticker)
    return _clean(meta)


@app.get("/api/quotes")
def quotes(symbols: str) -> dict:
    """Latest delayed (IEX) trade price for a comma-separated symbol list.
    Degrades to per-symbol {source: 'unavailable', reason} when Alpaca keys
    are absent -- the caller renders the reason rather than failing."""
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not requested:
        return {}
    return _clean(quotes_module.get_quotes(requested))


@app.get("/api/history/portfolio/{strategy_name:path}")
def portfolio_history(strategy_name: str) -> list[dict]:
    """Counterpart to /api/history/{name} for cross-sectional (Dual
    Momentum) / pairs (Pairs / Stat Arb) strategies -- see
    engine/logging_db.py's portfolio_runs table. Registered BEFORE the
    generic /api/history/{strategy_name:path} route below -- same
    route-ordering reason as /api/backtest/cross-sectional and
    /api/backtest/pairs: that route's own `:path` converter would
    otherwise greedily swallow 'portfolio/Dual Momentum' as a single
    (wrong) strategy_name."""
    if strategy_name not in ALL_STRATEGY_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown strategy {strategy_name!r}")
    rows = [
        {
            "runAt": row["run_at"],
            "startDate": row["start_date"],
            "endDate": row["end_date"],
            "finalEquity": row["final_equity"],
            "returnPct": row["return_pct"],
            "cagrPct": row["cagr_pct"],
            "maxDrawdownPct": row["max_drawdown_pct"],
            "sharpe": row["sharpe"],
            "sortino": row["sortino"],
            "isCanonical": bool(row["is_canonical"]),
            "symbols": json.loads(row["symbols"]) if row["symbols"] else [],
            "params": json.loads(row["params"]) if row["params"] else {},
            "pairSymbolA": row["pair_symbol_a"],
            "pairSymbolB": row["pair_symbol_b"],
            "pairPValue": row["pair_p_value"],
            "benchmarkReturnPct": row["benchmark_return_pct"],
            "status": row["status"],
        }
        for row in portfolio_run_history(strategy_name)
    ]
    return _clean(rows)


@app.get("/api/history/{strategy_name:path}")
def history(strategy_name: str) -> list[dict]:
    if strategy_name not in ALL_STRATEGY_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown strategy {strategy_name!r}")
    rows = [
        {
            "runAt": row["run_at"],
            "startDate": row["start_date"],
            "endDate": row["end_date"],
            "tradesTaken": row["trades_taken"],
            "winRate": row["win_rate"],
            "expectancyR": row["expectancy_r"],
            "profitFactor": row["profit_factor"],
            "maxDrawdownPct": row["max_drawdown_pct"],
            "sharpe": row["sharpe"],
            "alphaPct": row["alpha_pct"],
            "status": row["status"],
            "isCanonical": bool(row["is_canonical"]),
            "symbols": json.loads(row["symbols"]) if row["symbols"] else [],
            "params": json.loads(row["params"]) if row["params"] else {},
        }
        for row in run_history(strategy_name)
    ]
    return _clean(rows)


@app.get("/api/universe/pools")
def universe_pools() -> dict:
    """The three market-cap tiers the Lab tab's symbol filter draws a random
    sample from -- see engine/universe.py:CAP_TIER_POOLS for the disclosed
    methodology and known limitations behind each list."""
    from engine.universe import CAP_TIER_POOLS

    return _clean(CAP_TIER_POOLS)


@app.get("/api/market")
def market() -> dict:
    """SPY regime, sector performance, and trend-template pass-rate -- all
    read from data engine/regime.py, engine/trend_template.py, and
    engine/quotes.py already compute; see engine/market_overview.py."""
    return _clean(market_overview.market_overview())


@app.get("/api/live/account")
def live_account() -> dict:
    """Real Alpaca PAPER account snapshot: equity/cash/positions/recent
    orders/market clock. `available: false` (with a reason) if Alpaca isn't
    configured, rather than erroring -- same degrade-gracefully convention
    as /api/quotes."""
    return _clean(alpaca_trading.account_snapshot())


@app.get("/api/live/signals")
def live_signals(limit: int = 100) -> list[dict]:
    """Recent live entry-signal alerts logged by engine/live_scanner.py,
    newest bar first."""
    rows = [
        {
            "detectedAt": row["detected_at"],
            "barTimestamp": row["bar_timestamp"],
            "strategyName": row["strategy_name"],
            "symbol": row["symbol"],
            "direction": row["direction"],
            "price": row["price"],
            "timeframe": row["timeframe"],
            "regimeState": row["regime_state"],
            "trendTemplatePass": (
                None if row["trend_template_pass"] is None else bool(row["trend_template_pass"])
            ),
        }
        for row in signals_db.recent_signals(limit)
    ]
    return _clean(rows)


@app.post("/api/live/scan")
def trigger_scan() -> dict:
    """Manually run one scan cycle -- lets the live monitor be smoke-tested
    without waiting for the background loop or for market hours."""
    new_alerts = live_scanner.scan_once()
    return _clean({"newAlerts": new_alerts})


@app.get("/api/screener")
def screener(symbols: str | None = None) -> dict:
    """Live valuation/quality/growth-momentum/risk composite scores plus
    analyst-consensus columns -- see engine/screener.py for the disclosed
    methodology. `symbols` (optional, comma-separated) re-ranks that subset
    against itself rather than the full RESEARCH_UNIVERSE."""
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    return _clean(screener_module.build_screener(requested))


@app.get("/api/movers")
def movers(symbols: str | None = None, topN: int = 10) -> dict:
    """Today's gainers/losers + momentum streaks -- see engine/movers.py."""
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
    return _clean(movers_module.build_movers(requested, top_n=topN))


def _insider_row(row: dict) -> dict:
    """engine/data_edgar.py:recent_purchases returns raw sqlite3.Row dicts
    (the table's own snake_case column names) -- map to this API's camelCase
    convention here, at the JSON boundary, rather than downstream."""
    return {
        "issuerTicker": row["issuer_ticker"],
        "issuerName": row["issuer_name"],
        "filerName": row["filer_name"],
        "filedAt": row["filed_at"],
        "signalDate": row["signal_date"],
        "transactionDate": row["transaction_date"],
        "sharesTransacted": row["shares_transacted"],
        "pricePerShare": row["price_per_share"],
        "transactionValue": row["transaction_value"],
        "pctChangeHoldings": row["pct_change_holdings"],
        "ownershipNature": row["ownership_nature"],
        "formUrl": row["form_url"],
    }


@app.get("/api/insider/recent")
def insider_recent(limit: int = 50) -> dict:
    """Cached SEC EDGAR Form 4 open-market purchases for RESEARCH_UNIVERSE,
    largest transaction first -- see engine/data_edgar.py:recent_purchases.
    Read-only; never fetches. Empty `rows` (not an error) if nothing has
    been fetched yet -- the UI should point at the refresh button."""
    rows = data_edgar.recent_purchases(tickers=RESEARCH_UNIVERSE, limit=limit)
    return _clean({"rows": [_insider_row(r) for r in rows], **_insider_refresh_state})


@app.get("/api/insider/status")
def insider_status() -> dict:
    return _clean(_insider_refresh_state)


def _run_insider_refresh() -> None:
    """Runs on a worker thread (asyncio.to_thread) -- fetch_form4_for_universe
    does blocking, rate-limited HTTP calls to SEC EDGAR, and must not block
    the event loop the rest of the API is serving requests on."""
    global _insider_refresh_state
    try:
        end = date.today()
        start = end - timedelta(days=INSIDER_REFRESH_LOOKBACK_DAYS)
        data_edgar.fetch_form4_for_universe(RESEARCH_UNIVERSE, start, end, progress=False)
        _insider_refresh_state = {
            "running": False,
            "lastCompletedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "lastError": None,
        }
    except Exception as exc:  # noqa: BLE001 -- a refresh failure must not kill the server
        logger.exception("insider refresh failed")
        _insider_refresh_state = {
            "running": False,
            "lastCompletedAt": _insider_refresh_state.get("lastCompletedAt"),
            "lastError": str(exc),
        }


@app.post("/api/insider/refresh")
async def insider_refresh() -> dict:
    """Manually trigger a Form 4 fetch for RESEARCH_UNIVERSE over the last
    INSIDER_REFRESH_LOOKBACK_DAYS -- mirrors POST /api/live/scan's "run once
    now" pattern. Runs in the background (asyncio.to_thread + a module-level
    task reference, same GC-safety pattern as _scan_task) since a first-time
    fetch across ~94 tickers, rate-limited at 8 req/s, is not instant."""
    global _insider_refresh_task, _insider_refresh_state
    if _insider_refresh_state["running"]:
        return _clean({"started": False, "reason": "A refresh is already running.", **_insider_refresh_state})

    _insider_refresh_state = {
        "running": True,
        "lastCompletedAt": _insider_refresh_state.get("lastCompletedAt"),
        "lastError": None,
    }

    async def _job() -> None:
        await asyncio.to_thread(_run_insider_refresh)

    _insider_refresh_task = asyncio.create_task(_job())
    return _clean({"started": True, **_insider_refresh_state})


@app.get("/api/digest/preview")
def digest_preview() -> dict:
    """Composed daily digest (regime, movers, insider buys, market-signals
    score) + a plain-text rendering -- see engine/digest.py. Preview only:
    no scheduler, no SMTP, nothing is sent anywhere."""
    result = digest_module.build_digest()
    result["insiderPurchases"] = [_insider_row(r) for r in result["insiderPurchases"]]
    return _clean(result)


@app.post("/api/chat")
def chat(req: ChatRequest) -> dict:
    """The Chat tab next to a backtest result -- see engine/chat_assistant.py.
    Scoped to exactly the one result the frontend already has in state
    (passed in whole, not re-fetched or re-run); never touches any other
    run, symbol, or live data. Never 500s on a missing API key -- returns a
    reply string explaining it isn't configured, same degrade-gracefully
    convention as /api/live/account and /api/quotes."""
    reply = chat_assistant.chat(req.result, [m.model_dump() for m in req.messages])
    return {"reply": reply}
