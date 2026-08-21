"""Orchestrates one automated paper-order rebalance for a cross-sectional
strategy (currently only Dual Momentum) -- the piece that turns
`CrossSectionalStrategy.rebalance()`'s target weights into real Alpaca
paper orders, gated by every guardrail in `engine/live_risk.py` and
`engine/kill_switch.py`, logged via `engine/execution_db.py`.

Scope: uses only the explicitly saved paper-execution configuration for an
enabled strategy. Its values are validated with the same parameter schema as
the backtest UI before saving, then stored and logged per rebalance; a Lab-tab
experiment can never silently alter automated execution.

Order-sizing note: BUY orders and PARTIAL-sell orders (a target weight
that shrank but didn't zero out) both use Alpaca's `notional=` sizing
(exact fractional qty resolved by the broker from a live price at
submission time), not a hand-computed share count against a possibly-
stale snapshot -- this sidesteps the overselling/drift risk entirely for
anything short of a full exit. Only a FULL liquidation (a symbol dropped
from target_weights entirely) uses `close_symbol_position`'s exact-qty
close, since notional can't guarantee zeroing out a fractional remainder.

Backtest-vs-live divergence, disclosed: `engine/cross_sectional.py`'s
backtest computes rankings using data through day D's close AND executes
at day D's close, same day -- a look-ahead convenience only defensible in
backtesting. Live execution cannot replicate that: rankings here are
computed from data through the PRIOR trading day's close, then orders
execute intraday on the rebalance day itself at whatever the live market
price is. This is the one place "paper forward-test accuracy vs. the
backtest" (CLAUDE.md's v2 milestone wording) has a real, structural gap,
not a bug to fix.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from engine import (
    alpaca_trading,
    data as data_module,
    execution_db,
    kill_switch,
    live_risk,
    logging_db,
)
from engine.cross_sectional import _rebalance_dates
from engine.runner import run_config
from engine.universe import TIMEZONE
from strategies.cross_sectional import CrossSectionalStrategy
from strategies.params import apply_params, describe_params
from strategies.registry import build_cross_sectional_strategy

RISK_LIMITS = live_risk.RiskLimits()

# Generous enough for even the widest Lab-tab-declared bound on
# lookback_trading_days (378 trading days =~ 550 calendar days), even
# though live execution never actually applies a Lab-tab override (see
# module docstring) -- sized for the parameter's declared range, not just
# today's registered default, so raising the default later doesn't
# silently starve the strategy of history.
HISTORY_LOOKBACK_DAYS = 600

# Alpaca's own practical floor for a notional order -- skip a delta this
# small rather than submit an order that would just get rejected.
MIN_ORDER_NOTIONAL = 1.0

# CROSS_SECTIONAL_STRATEGY_NAMES import deferred to call sites (not module
# level) to avoid a needless import-time dependency for callers that only
# want e.g. execute_rebalance for a single, already-known strategy name.


def is_rebalance_due(strategy: CrossSectionalStrategy, today: date, client: Any) -> bool:
    """Reuses engine.cross_sectional._rebalance_dates rather than
    reimplementing monthly/weekly/semimonthly/quarterly/daily logic a
    second time -- same reasoning engine/timing_filters.py already
    applies reusing engine/filters.py's _asof. Calendar sourced from
    Alpaca's own get_calendar(), not calendar-day arithmetic, so weekends/
    holidays are handled the same way the broker itself handles them."""
    from alpaca.trading.requests import GetCalendarRequest

    frequency = getattr(strategy, "rebalance_frequency", "monthly")
    # 100 calendar days safely reaches back past the start of the current
    # month/quarter for any frequency this strategy supports -- extra
    # earlier days in the window are harmless, _rebalance_dates groups by
    # (year, period) so they just form their own separate, unused group.
    sessions = client.get_calendar(
        GetCalendarRequest(start=today - timedelta(days=100), end=today)
    )
    calendar = pd.DatetimeIndex([s.date for s in sessions])
    if calendar.empty:
        return False
    return pd.Timestamp(today) in _rebalance_dates(calendar, frequency)


def _prior_trading_day(client: Any, today: date) -> date | None:
    from alpaca.trading.requests import GetCalendarRequest

    sessions = client.get_calendar(
        GetCalendarRequest(start=today - timedelta(days=14), end=today)
    )
    prior = [s.date for s in sessions if s.date < today]
    return prior[-1] if prior else None


def _client_order_id(run_id: int, symbol: str) -> str:
    return f"exec-{run_id}-{symbol}"


def _plan_orders(
    current_positions: list[dict[str, Any]],
    target_weights: dict[str, float],
    portfolio_value: float,
    buys_halted: bool,
) -> list[dict[str, Any]]:
    """Sell/close orders first, then buys -- matches the backtest's own
    liquidate-then-establish ordering (engine/cross_sectional.py) and
    frees buying power before it's needed."""
    by_symbol = {p["symbol"]: p for p in current_positions}
    sells: list[dict[str, Any]] = []
    buys: list[dict[str, Any]] = []

    for symbol in by_symbol:
        if symbol not in target_weights:
            sells.append({
                "symbol": symbol, "side": "sell", "order_kind": "close",
                "qty": float(by_symbol[symbol].get("qty") or 0.0),
                "reference_price": by_symbol[symbol].get("currentPrice"),
            })

    for symbol, weight in target_weights.items():
        pos = by_symbol.get(symbol)
        current_price = pos.get("currentPrice") if pos else None
        current_qty = float(pos["qty"]) if pos else 0.0
        current_value = current_qty * current_price if current_price else 0.0
        delta_value = portfolio_value * weight - current_value

        if abs(delta_value) < MIN_ORDER_NOTIONAL:
            continue
        if delta_value > 0:
            if buys_halted:
                continue
            buys.append({
                "symbol": symbol, "side": "buy", "order_kind": "notional",
                "notional": round(delta_value, 2),
                "reference_price": current_price,
            })
        else:
            sells.append({
                "symbol": symbol, "side": "sell", "order_kind": "notional",
                "notional": round(abs(delta_value), 2),
                "reference_price": current_price,
            })

    return sells + buys


def _submit_orders(
    run_id: int,
    planned: list[dict[str, Any]],
    raw_bars: dict[str, pd.DataFrame] | None = None,
) -> int:
    """Log-before-submit one order batch and return its rejection count."""
    failures = 0
    raw_bars = raw_bars or {}
    for order in planned:
        reference_price = order.get("reference_price")
        if not reference_price:
            symbol_bars = raw_bars.get(order["symbol"], pd.DataFrame())
            if not symbol_bars.empty:
                reference_price = float(symbol_bars["Close"].iloc[-1])
        expected_qty = order.get("qty")
        if expected_qty is None and reference_price and order.get("notional"):
            expected_qty = float(order["notional"]) / float(reference_price)
        client_order_id = _client_order_id(run_id, order["symbol"])
        order_id = execution_db.log_order(
            run_id,
            symbol=order["symbol"], side=order["side"], order_kind=order["order_kind"],
            qty=order.get("qty"), notional=order.get("notional"),
            stop_price=None, target_price=None,
            client_order_id=client_order_id, status="pending", is_paper=1,
            reference_price=reference_price, expected_qty=expected_qty,
        )
        try:
            if order["order_kind"] == "close":
                result = alpaca_trading.close_symbol_position(order["symbol"], client_order_id)
            else:
                result = alpaca_trading.submit_market_order(
                    order["symbol"], order["side"], notional=order.get("notional"),
                    client_order_id=client_order_id,
                )
            execution_db.update_order(
                order_id, status="submitted", alpaca_order_id=result["id"],
                submitted_at=result.get("submittedAt"),
            )
        except Exception as exc:  # noqa: BLE001 -- one bad symbol must not kill the batch
            failures += 1
            execution_db.update_order(order_id, status="rejected", error_message=str(exc))
    return failures


def reconcile_open_orders() -> None:
    """Refresh every non-terminal locally-logged order against Alpaca's
    own order status. A row with no alpaca_order_id at all (a crash
    between submitting and recording the response) is left as-is --
    surfaced to the UI as unresolved rather than silently retried."""
    for row in execution_db.open_orders():
        if not row["alpaca_order_id"]:
            continue
        result = alpaca_trading.get_order_status(row["alpaca_order_id"])
        if not result.get("available"):
            continue
        execution_db.update_order(
            row["id"],
            status=result["status"],
            filled_at=result.get("filledAt"),
            filled_qty=result.get("filledQty"),
            filled_avg_price=result.get("filledAvgPrice"),
        )


def execute_rebalance(
    strategy_name: str, trigger_source: str, force: bool = False, today: date | None = None,
) -> dict[str, Any]:
    """`force=True` (the manual "rebalance now" trigger) skips the
    is_rebalance_due check but NOT claim_run's uniqueness guard -- a
    manual smoke-test can run on an off-day, but two real attempts
    (a manual click racing the hourly scheduler) still can't both
    proceed same-day. `today` defaults to the real date.today() -- an
    explicit override exists only so tests can pin a fixed date instead
    of depending on whatever day it happens to be when the suite runs."""
    now = datetime.now().isoformat()
    today = today if today is not None else date.today()
    rebalance_date = today.isoformat()

    if not execution_db.is_enabled(strategy_name):
        return {"status": "blocked_not_enabled"}

    # Nothing further can happen for this strategy today once the day's one
    # real-attempt slot is taken, so short-circuit here rather than letting
    # a later branch log. The market-hours check below calls write_blocked,
    # which is deliberately exempt from claim_run's partial unique index --
    # without this, every hourly tick after the close appended a fresh
    # 'blocked_market_closed' row to a day whose rebalance already
    # completed, reading like a repeated execution attempt. Same status
    # claim_run itself would return, including for force=True: a manual
    # trigger skips is_rebalance_due, never the one-attempt-per-day guard.
    if execution_db.has_real_run_for_date(strategy_name, rebalance_date):
        return {"status": "already_running_or_done_today"}

    # Re-check the persisted report on every scheduled/manual attempt. An old
    # enabled flag must never outlive the evidence that authorized it (or a
    # deleted/corrupt validation row) and continue submitting orders.
    validation_run_id = execution_db.validation_run_id_for(strategy_name)
    eligible, validation_reason, _ = logging_db.paper_execution_eligibility(
        strategy_name, validation_run_id
    )
    if not eligible:
        execution_db.write_blocked(
            strategy_name, rebalance_date, trigger_source,
            "blocked_validation", now, error_message=validation_reason,
        )
        return {"status": "blocked_validation", "reason": validation_reason}

    if kill_switch.is_active():
        execution_db.write_blocked(
            strategy_name, rebalance_date, trigger_source, "blocked_kill_switch", now
        )
        return {"status": "blocked_kill_switch"}

    inception = execution_db.inception_for(strategy_name)
    if inception["status"] == "policy_required" or inception["policy"] is None:
        return {
            "status": "blocked_inception_policy",
            "reason": "Choose Adopt or Flatten for this forward test before execution.",
        }

    client, reason = alpaca_trading.trading_client()
    if client is None:
        # Not a guardrail block -- a setup/credentials problem. No DB row:
        # this can recur every scheduler tick until fixed, and isn't a
        # safety-relevant event worth an audit-trail entry the way a real
        # kill-switch/market-closed block is.
        return {"status": "alpaca_not_configured", "reason": reason}

    interval, default_symbols, _default_start, _default_end = run_config(strategy_name)
    # A promoted exploratory run may use a different universe. The exact
    # persisted symbol list is authoritative; falling back to registered
    # defaults is only for legacy automation rows created before this field
    # existed.
    symbols = execution_db.selected_symbols_for(strategy_name) or default_symbols
    rf_window_start = today - timedelta(days=90)
    risk_free_rate = data_module.risk_free_rate(rf_window_start, today)
    strategy = build_cross_sectional_strategy(strategy_name, risk_free_rate=risk_free_rate)
    configured_params = execution_db.params_for(strategy_name)
    if configured_params:
        strategy = apply_params(strategy, json.loads(configured_params))

    if not force and not is_rebalance_due(strategy, today, client):
        return {"status": "not_due"}

    try:
        clock = client.get_clock()
    except Exception as exc:  # noqa: BLE001 -- network hiccup, treat as closed for this tick
        execution_db.write_blocked(
            strategy_name, rebalance_date, trigger_source, "blocked_market_closed", now,
            error_message=str(exc),
        )
        return {"status": "blocked_market_closed", "reason": str(exc)}

    if not clock.is_open:
        execution_db.write_blocked(
            strategy_name, rebalance_date, trigger_source, "blocked_market_closed", now
        )
        return {"status": "blocked_market_closed"}

    cached_acct_positions: dict[str, Any] | None = None
    if inception["policy"] == "flatten" and inception["status"] == "flattening":
        # Never race entry orders against liquidation fills. Wait until the
        # broker confirms the account is flat, then freeze the clean baseline.
        cached_acct_positions = alpaca_trading.account_and_positions()
        flatten_account = cached_acct_positions["account"]
        if not flatten_account.get("available"):
            return {"status": "failed", "reason": flatten_account.get("reason")}
        if cached_acct_positions["positions"]:
            if execution_db.has_open_orders_for_strategy(strategy_name):
                return {"status": "inception_flattening"}
            # Every close order is terminal but inventory remains (rejection,
            # partial fill, or broker cancellation). A later eligible attempt
            # may safely retry only the remaining inventory.
            execution_db.set_inception_status(strategy_name, "pending", now)
            inception["status"] = "pending"
        else:
            execution_db.record_inception(
                strategy_name, flatten_account["equity"], [], now,
            )
            inception["status"] = "initialized"

    run_id = execution_db.claim_run(strategy_name, rebalance_date, trigger_source, now)
    if run_id is None:
        return {"status": "already_running_or_done_today"}

    try:
        acct_positions = cached_acct_positions or alpaca_trading.account_and_positions()
        account = acct_positions["account"]
        if not account.get("available"):
            execution_db.update_run(run_id, status="failed", error_message=account.get("reason"))
            return {"status": "failed", "runId": run_id}

        equity = account["equity"]
        last_equity = account.get("lastEquity")
        halted = live_risk.daily_loss_halted(equity, last_equity, RISK_LIMITS)
        daily_loss_pct = None if not last_equity else round(1 - equity / last_equity, 6)

        params_json = json.dumps(
            {spec.name: getattr(strategy, spec.name) for spec in describe_params(type(strategy))}
        )
        execution_db.update_run(
            run_id, strategy_params=params_json, portfolio_value_at_start=equity,
            daily_loss_pct_at_start=daily_loss_pct,
        )

        if inception["status"] == "pending":
            if inception["policy"] == "adopt":
                # This mark-to-market checkpoint, not Alpaca's historical cost
                # basis, is time zero for the forward experiment.
                execution_db.record_inception(
                    strategy_name, equity, acct_positions["positions"], now,
                )
                inception["status"] = "initialized"
            elif acct_positions["positions"]:
                # Flatten is deliberately a liquidation-only phase. Entry
                # orders are deferred until a later scheduler pass confirms
                # the broker account is actually flat.
                flatten_orders = _plan_orders(
                    acct_positions["positions"], {}, equity, buys_halted=True,
                )
                failures = _submit_orders(run_id, flatten_orders)
                if failures == len(flatten_orders):
                    status = "failed"
                elif failures:
                    status = "inception_flatten_partial_failure"
                else:
                    status = "inception_flattening"
                    execution_db.set_inception_status(strategy_name, "flattening", now)
                execution_db.update_run(run_id, status=status)
                return {"status": status, "runId": run_id}
            else:
                execution_db.record_inception(strategy_name, equity, [], now)
                inception["status"] = "initialized"

        prior_day = _prior_trading_day(client, today)
        if prior_day is None:
            execution_db.update_run(run_id, status="failed", error_message="No prior trading day found")
            return {"status": "failed", "runId": run_id}

        start = prior_day - timedelta(days=HISTORY_LOOKBACK_DAYS)
        raw_bars = {s: data_module.get_bars(s, "1d", start, prior_day) for s in symbols}
        # Timezone-aware to match get_bars' own tz-localized index
        # (CLAUDE.md: all timestamps in America/New_York) -- a naive
        # Timestamp here raises inside bars.loc[:as_of] ("Cannot compare
        # tz-naive and tz-aware datetime-like objects"), a real bug this
        # unit test suite's fake strategy (which never touches its bars/
        # as_of arguments) couldn't have caught.
        target_weights = strategy.rebalance(raw_bars, as_of=pd.Timestamp(prior_day, tz=TIMEZONE))
        clipped = live_risk.clip_target_weights(target_weights, RISK_LIMITS)
        execution_db.update_run(run_id, target_weights=json.dumps(clipped))

        planned = _plan_orders(acct_positions["positions"], clipped, equity, halted)

        failures = _submit_orders(run_id, planned, raw_bars)

        if not planned:
            status = "completed"
        elif failures == len(planned):
            status = "failed"
        elif failures:
            status = "partial_failure"
        elif halted:
            status = "completed_with_daily_loss_halt"
        else:
            status = "completed"
        execution_db.update_run(run_id, status=status)
        return {"status": status, "runId": run_id}
    except Exception as exc:
        execution_db.update_run(run_id, status="failed", error_message=str(exc))
        raise
