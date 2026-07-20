"""Chat assistant for the "why did this trade fail" question -- the Chat tab
next to a backtest's results (Overview/Trades/Per-Symbol/Portfolio/History).

Scope, deliberately narrow: this assistant only ever sees ONE backtest
result -- whichever one is currently open in the Lab/Compare tab, passed in
whole by the frontend on every call. It is not a general market chatbot and
has no access to any other run, symbol, or live data. Individual trades
aren't persisted anywhere long-term (engine/logging_db.py only logs
aggregate metrics per run), so there is nothing durable to query across
runs -- the frontend already holds the one result this needs, in exactly
the shape /api/backtest/{name} returns, and sends it with every message.

Grounded via tool use, not context-stuffing: rather than pasting the whole
trades list into every prompt (expensive, and doesn't scale past a few dozen
trades), the model gets a small set of tools (list_trades, get_trade_detail,
get_run_metrics, get_per_symbol_breakdown) that query the result dict
on demand, the same pattern engine/live_scanner.py's design note on
"context, not a gate" mirrors for a different feature: give the model real
data to reason from, computed once by the engine, not fabricated by the
model itself. Every number the assistant cites is something
engine/excursion.py or engine/metrics.py already computed for that trade or
run -- the assistant explains the number, it does not invent one.

This is the app's only feature with a real per-use cost (an Anthropic API
call per message) -- everything else is free (yfinance) or already-paid-for
(Alpaca). Degrades gracefully like engine/alpaca_client.py's
market_data_client()/engine/alpaca_trading.py's trading_client(): a missing
ANTHROPIC_API_KEY returns a clear "not configured" reply instead of raising,
so the rest of the app is unaffected.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from engine.alpaca_client import first_env

MODEL = "claude-sonnet-5"
MAX_TOKENS = 1024

# A tool-call round-trip (model asks for data, gets it, asks again) -- capped
# so a confused model can't loop indefinitely on one message.
MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """You are a backtesting analysis assistant embedded in the Trading Strategy Lab app, in the "Chat" tab next to one specific backtest result's Trades/Overview/Per-Symbol tables.

Scope: you can only see the ONE backtest result the user currently has open, via the tools provided (list_trades, get_trade_detail, get_run_metrics, get_per_symbol_breakdown). You have no access to any other run, symbol, live market data, or the internet.

Ground rules, non-negotiable:
- Every claim you make must come from a tool call's actual output. Never invent a number, a date, or a reason a trade behaved a certain way -- if the data doesn't explain something, say so plainly rather than speculating as if it were fact.
- When explaining why a trade won or lost, use the excursion diagnostics already computed for it: realizedR, mfeR (best unrealized excursion in R), maeR (worst unrealized excursion in R), exitEfficiencyPct (winners: how much of the favorable move was captured), lossRealizationRatioPct (losers: how much of the adverse move was realized vs. recovered from). Compare the trade of interest against a few contrasting trades (list_trades with outcome filters) rather than describing it in isolation.
- You are explaining HISTORICAL backtest behavior, never giving forward-looking investment advice. Do not say what the user should buy, sell, or do next with real money -- describe what this specific backtest's data shows, nothing more.
- Be concise. Lead with the answer, then the one or two numbers that support it.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_trades",
        "description": (
            "List trades from the currently loaded backtest result, optionally "
            "filtered by symbol and/or outcome. Returns compact rows (index, "
            "symbol, entry/exit time, PnL, return%, realized R, MFE R, MAE R, "
            "exit efficiency %, loss realization ratio %). Use get_trade_detail "
            "for a specific trade's full fields (stop/target price, size, "
            "entry slippage)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Filter to one symbol, e.g. 'AAPL'. Omit for all symbols.",
                },
                "outcome": {
                    "type": "string",
                    "enum": ["win", "loss", "all"],
                    "description": "Filter by outcome (win = PnL > 0). Default 'all'.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max rows to return. Default 50.",
                },
            },
        },
    },
    {
        "name": "get_trade_detail",
        "description": (
            "Full detail for one trade by its 0-based index in the trades list "
            "(as returned by list_trades) -- entry/exit time and price, size, "
            "stop and target, PnL, return%, realized R, MFE R, MAE R, exit "
            "efficiency %, loss realization ratio %, entry slippage %."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "trade_index": {"type": "integer", "description": "0-based index into the trades list."},
            },
            "required": ["trade_index"],
        },
    },
    {
        "name": "get_run_metrics",
        "description": (
            "This run's aggregate metrics (trades taken, wins, losses, win rate, "
            "avg win/loss R, expectancy R, profit factor, max drawdown, Sharpe, "
            "Sortino, alpha vs. buy-and-hold, beta, CAGR, exposure %, status) and "
            "excursion summary (mean/median exit efficiency for winners, "
            "mean/median loss realization ratio for losers), plus the strategy "
            "name, symbol universe, and date range this run covers."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_per_symbol_breakdown",
        "description": (
            "Per-symbol trade counts, win rate, expectancy R, profit factor, and "
            "PnL for this run -- use this to see whether a trade's outcome is "
            "part of a symbol-specific pattern (e.g. this symbol loses on every "
            "trade) or an isolated case within an otherwise-healthy symbol."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _credentials() -> str | None:
    return first_env("ANTHROPIC_API_KEY")


@lru_cache(maxsize=1)
def chat_client() -> tuple[Any | None, str]:
    """Lazily build a single Anthropic client. Returns (client_or_None,
    reason). Never raises -- callers branch on the None to degrade
    gracefully, matching engine/alpaca_client.py:market_data_client."""
    api_key = _credentials()
    if not api_key:
        return None, "ANTHROPIC_API_KEY missing from .env. Add a key line and restart the API."
    try:
        from anthropic import Anthropic
    except ImportError:
        return None, "anthropic is not installed (pip install anthropic)."
    try:
        return Anthropic(api_key=api_key), "ok"
    except Exception as exc:  # noqa: BLE001 -- surface any init failure to the UI
        return None, f"Failed to initialize Anthropic client: {exc}"


def available() -> tuple[bool, str]:
    client, reason = chat_client()
    return client is not None, reason


def _list_trades(result: dict, tool_input: dict) -> dict:
    trades = result.get("trades", [])
    symbol = tool_input.get("symbol")
    outcome = tool_input.get("outcome", "all")
    limit = tool_input.get("limit", 50)

    rows = []
    for i, t in enumerate(trades):
        if symbol and t["symbol"].upper() != symbol.upper():
            continue
        is_win = t["pnl"] > 0
        if outcome == "win" and not is_win:
            continue
        if outcome == "loss" and is_win:
            continue
        rows.append({
            "index": i,
            "symbol": t["symbol"],
            "entryTime": t["entryTime"],
            "exitTime": t["exitTime"],
            "pnl": t["pnl"],
            "returnPct": t["returnPct"],
            "realizedR": t["realizedR"],
            "mfeR": t["mfeR"],
            "maeR": t["maeR"],
            "exitEfficiencyPct": t["exitEfficiencyPct"],
            "lossRealizationRatioPct": t["lossRealizationRatioPct"],
        })
    return {"totalMatching": len(rows), "rows": rows[:limit]}


def _get_trade_detail(result: dict, tool_input: dict) -> dict:
    trades = result.get("trades", [])
    idx = tool_input.get("trade_index")
    if not isinstance(idx, int) or not (0 <= idx < len(trades)):
        return {"error": f"trade_index {idx!r} out of range (0..{len(trades) - 1})"}
    return {"index": idx, **trades[idx]}


def _get_run_metrics(result: dict) -> dict:
    return {
        "strategyName": result.get("strategyName"),
        "symbols": result.get("symbols"),
        "start": result.get("start"),
        "end": result.get("end"),
        "metrics": result.get("metrics", {}),
        "excursionSummary": result.get("excursionSummary", {}),
    }


def _execute_tool(name: str, tool_input: dict, result: dict) -> dict:
    if name == "list_trades":
        return _list_trades(result, tool_input)
    if name == "get_trade_detail":
        return _get_trade_detail(result, tool_input)
    if name == "get_run_metrics":
        return _get_run_metrics(result)
    if name == "get_per_symbol_breakdown":
        return {"rows": result.get("perSymbol", [])}
    return {"error": f"Unknown tool {name!r}"}


def chat(result: dict, messages: list[dict[str, str]]) -> str:
    """One assistant turn: run the tool-use loop against `result` (the exact
    JSON /api/backtest/{name} returned for whatever's on screen) and
    `messages` (the conversation so far, plain {role, content} pairs from
    the frontend). Returns the assistant's final text reply -- tool-call
    round-trips happen entirely within this call and are never sent back to
    the frontend, which only ever stores finalized text turns."""
    client, reason = chat_client()
    if client is None:
        return f"Chat assistant isn't configured: {reason}"

    conversation: list[dict[str, Any]] = [dict(m) for m in messages]
    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=conversation,
            )
        except Exception as exc:  # noqa: BLE001 -- surface the failure as a chat reply, not a 500
            return f"Chat request failed: {exc}"

        if response.stop_reason != "tool_use":
            text = "".join(block.text for block in response.content if block.type == "text")
            return text or "(no response)"

        conversation.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                output = _execute_tool(block.name, block.input, result)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(output),
                })
        conversation.append({"role": "user", "content": tool_results})

    return "I wasn't able to reach a final answer within the tool-call limit -- try a narrower question."
