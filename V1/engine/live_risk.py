"""Hard position/risk limits for automated paper-order execution
(engine/execution.py), enforced INDEPENDENT of any strategy's own logic --
CLAUDE.md's "Live trading safety guardrails": "max % of account risked per
trade, max number of concurrent open positions, and a daily loss
circuit-breaker."

These are a code-level backstop, not a substitute for a strategy's own
rules. A strategy's registered/tuned parameters (e.g. Dual Momentum's
top_n) can still ask for something these limits then clip -- that's by
design: a Lab-tab override that raises top_n past
RiskLimits.max_concurrent_positions means live execution silently trades
fewer names than that backtest validated, unless the limit itself is also
raised. Not a bug; a disclosed consequence of these being independent
checks.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    max_position_pct: float = 0.30
    max_concurrent_positions: int = 10
    daily_loss_halt_pct: float = 0.05


def clip_target_weights(target_weights: dict[str, float], limits: RiskLimits) -> dict[str, float]:
    """Cap each weight to max_position_pct, THEN truncate to the top
    max_concurrent_positions by (already-capped) weight. Excess from
    either step is dropped -- it becomes cash, never redistributed to
    other symbols, since redistribution would silently change what the
    strategy itself asked for rather than just capping it."""
    capped = {
        symbol: min(weight, limits.max_position_pct)
        for symbol, weight in target_weights.items()
    }
    if len(capped) <= limits.max_concurrent_positions:
        return capped
    top = sorted(capped.items(), key=lambda kv: kv[1], reverse=True)
    return dict(top[: limits.max_concurrent_positions])


def daily_loss_halted(equity: float, last_equity: float | None, limits: RiskLimits) -> bool:
    """last_equity is Alpaca's own prior-session-close equity
    (engine/alpaca_trading.py:get_account()'s lastEquity field) -- reuses
    what the broker already tracks rather than building a redundant
    'equity at midnight' snapshot mechanism. None/non-positive means no
    real baseline exists yet (e.g. a brand-new account) -- never halted
    on a baseline that isn't real."""
    if last_equity is None or last_equity <= 0:
        return False
    return (1 - equity / last_equity) >= limits.daily_loss_halt_pct
