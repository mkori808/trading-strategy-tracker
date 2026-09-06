"""Machinery for strategies/swing/ensemble_voting.py's Weighted Voting
Ensemble: a macro regime gate, per-symbol continuous conviction scores for
sub-strategies that only expose a boolean entry/exit rule, rolling
risk-adjusted sub-strategy weighting, and inverse-volatility position
sizing.

This is machinery (how to turn existing rule definitions into ensemble
inputs), not a rule definition itself -- kept in engine/, mirroring the
project's existing split (strategies/ define rules, engine/ runs them).
No new bar-by-bar backtest loop lives here: the ensemble plugs into
engine/cross_sectional.py's existing rebalance-driven engine via
strategies.cross_sectional.CrossSectionalStrategy, the same interface
Dual Momentum already uses -- see strategies/swing/ensemble_voting.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from engine.indicators import atr, sma
from strategies.base import Strategy

TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# 1. Macro regime filter (the master switch)
# ---------------------------------------------------------------------------

ACTIVE = "ACTIVE"
DEFENSIVE = "DEFENSIVE"

# Deliberately simpler than engine/regime.py's 3-state Bullish/Neutral/
# Bearish classifier (which also uses a 50-day SMA and requires 50>200 for
# Bullish). The ensemble spec calls for a binary switch on SPY's 200-day SMA
# alone. These are two independently disclosed, differently-scoped regime
# lenses coexisting on purpose (same pattern as Dividend Hybrid's
# TRIGGER_SPEC vs TRIGGER_INTRADAY_PROXY) -- conflating them would silently
# change one spec or the other.
REGIME_SMA_PERIOD = 200


def macro_regime(spy_bars: pd.DataFrame, as_of: pd.Timestamp, sma_period: int = REGIME_SMA_PERIOD) -> str:
    """ACTIVE if SPY's close as of `as_of` is at/above its own `sma_period`
    SMA, else DEFENSIVE. `spy_bars` must already be sliced to `.loc[:as_of]`
    by the caller (see strategies/swing/ensemble_voting.py) -- this function
    never looks past its input, but it also never truncates it, so a caller
    passing unsliced future bars would leak look-ahead into the SMA tail.
    Returns DEFENSIVE (not ACTIVE) when the SMA isn't warm yet -- an unknown
    regime must gate capital off, not on, same principle as
    engine/regime.py's NaN handling."""
    if spy_bars.empty:
        return DEFENSIVE
    close = spy_bars["Close"]
    trend = sma(close, sma_period)
    last_close = close.iloc[-1]
    last_trend = trend.iloc[-1]
    if pd.isna(last_trend):
        return DEFENSIVE
    return ACTIVE if last_close >= last_trend else DEFENSIVE


# ---------------------------------------------------------------------------
# 2. Signal standardization: continuous score S_i,j(t) in [-1, +1]
# ---------------------------------------------------------------------------

def boolean_strategy_position_series(strategy: Strategy, bars: pd.DataFrame) -> pd.Series:
    """Walk `bars` close-by-close, replaying `strategy`'s own entry/stop/
    target/exit rules to produce a same-index boolean series: True on every
    bar the strategy would be holding a position it opened via its own real
    entry_signal(). Long-only (every sub-strategy wired into the ensemble
    has direction="long"); short is left at False rather than guessed.

    Disclosed simplification vs. engine/backtest.py's adapter: stop/target
    are checked against each bar's CLOSE, not intrabar via backtesting.py's
    broker, since there's no bracket order here, only a signal used for
    ensemble scoring. This can miss a same-day stop/target touch that
    reverses by the close. Chosen over inventing an unrelated decay curve
    for "how confident is this signal right now" -- replaying the
    strategy's actual rule is more faithful than a fabricated shape, and
    the same simplification (close-based, not intrabar) is already
    disclosed elsewhere in this codebase (engine/overnight.py has no
    intrabar path either).

    O(bars) per call -- the caller (rebalance()) invokes this once per
    symbol per rebalance date over `bars` already sliced to `.loc[:as_of]`,
    so cost scales with history length, not universe size squared.
    """
    n = len(bars)
    in_position = np.zeros(n, dtype=bool)
    if n == 0:
        return pd.Series(in_position, index=bars.index)

    holding = False
    entry_price = 0.0
    stop = 0.0
    target: float | None = None

    for i in range(n):
        window = bars.iloc[: i + 1]
        close = float(window["Close"].iloc[-1])

        if holding:
            hit_stop = close <= stop
            hit_target = target is not None and close >= target
            hit_exit_signal = strategy.exit_signal(window)
            if hit_stop or hit_target or hit_exit_signal:
                holding = False
            else:
                in_position[i] = True
                continue

        if not holding and strategy.entry_signal(window):
            entry_price = close
            stop = strategy.stop_price(window, entry_price)
            target = strategy.target_price(window, entry_price)
            # A degenerate stop (>= entry) can't be walked forward
            # meaningfully -- skip opening rather than divide-by-zero risk
            # downstream; same guard engine/backtest.py's adapter applies.
            if stop < entry_price:
                holding = True
                in_position[i] = True

    return pd.Series(in_position, index=bars.index)


def boolean_strategy_score(strategy: Strategy, bars: pd.DataFrame) -> float:
    """Score as of the last bar of `bars`: +1.0 if the strategy's own
    entry/stop/target/exit rules would currently hold a position, else 0.0.
    Long-only sub-strategies never score negative (see class docstring)."""
    series = boolean_strategy_position_series(strategy, bars)
    if series.empty:
        return 0.0
    return 1.0 if bool(series.iloc[-1]) else 0.0


def dual_momentum_scores(target_weights: dict[str, float], top_n: int) -> dict[str, float]:
    """Rescale DualMomentum.rebalance()'s output (equal weight 1/top_n per
    held symbol, symbols absent = 0) into the ensemble's [0, 1] scale, where
    a symbol DualMomentum currently holds scores 1.0 and everything else
    scores 0.0 -- rescaling by top_n rather than inventing a magnitude from
    the momentum value itself, since DualMomentum's own rule is binary
    (qualifies for a slot, or doesn't), not graded."""
    if top_n <= 0:
        return {}
    return {symbol: min(1.0, weight * top_n) for symbol, weight in target_weights.items()}


# ---------------------------------------------------------------------------
# 3. Dynamic ensemble weighting: rolling 63-day Sharpe of each sub-strategy
# ---------------------------------------------------------------------------

def _sharpe_from_daily_returns(returns: pd.Series, risk_free_rate: float) -> float:
    """Annualized Sharpe from a short daily-return window. Same formula
    shape as engine/portfolio.py's annualized_stats (annualized mean over
    annualized vol against a real risk-free rate, not backtesting.py's
    implicit 0%) applied directly to a returns slice instead of an equity
    curve -- annualized_stats' CAGR-from-years math is a poor fit for a
    63-trading-day window, but the Sharpe *definition* stays identical so
    numbers are comparable across the codebase."""
    returns = returns.dropna()
    if len(returns) < 5 or returns.std(ddof=1) == 0:
        return 0.0
    gmean = np.exp(np.log1p(returns).sum() / len(returns)) - 1
    ann_return = (1 + gmean) ** TRADING_DAYS_PER_YEAR - 1
    ann_vol = returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
    if ann_vol == 0:
        return 0.0
    return (ann_return - risk_free_rate) / ann_vol


def sub_strategy_rolling_sharpe(
    score_by_symbol: dict[str, float],
    close_df: pd.DataFrame,
    as_of: pd.Timestamp,
    window_days: int = 63,
    risk_free_rate: float = 0.0,
) -> float:
    """A sub-strategy's own rolling Sharpe, defined as the trailing
    `window_days` Sharpe of an equal-weight daily-rebalanced basket of
    whatever it currently scores positively (score > 0) as of `as_of`.
    This is a real, computable return series (not an invented number): "if
    you'd equal-weighted whatever this sub-strategy currently likes, how has
    that basket performed over the last `window_days` trading days" -- the
    natural reading of "this sub-strategy's rolling Sharpe" when the
    sub-strategy itself has no equity curve of its own (only per-symbol
    signals). A sub-strategy liking nothing right now (empty basket) scores
    Sharpe 0.0, which the weighting step below then clips to a zero weight
    anyway."""
    held = [s for s, sc in score_by_symbol.items() if sc > 0 and s in close_df.columns]
    if not held:
        return 0.0
    window = close_df.loc[:as_of, held].tail(window_days + 1)
    if len(window) < 5:
        return 0.0
    daily_returns = window.pct_change().dropna(how="all")
    basket_returns = daily_returns.mean(axis=1)  # equal-weight, rebalanced daily
    return _sharpe_from_daily_returns(basket_returns, risk_free_rate)


def dynamic_weights(sub_strategy_sharpe: dict[str, float]) -> dict[str, float]:
    """W_i = max(0, Sharpe_i) / sum(max(0, Sharpe_k)). A sub-strategy with
    non-positive rolling Sharpe gets weight 0 (clipped, never negative) --
    it doesn't drag the ensemble down, it's just excluded from this
    rebalance's vote. If every sub-strategy clips to 0 (nothing has a
    positive rolling Sharpe right now), every weight is 0 and the composite
    score is 0 for every symbol, which floors out to an all-cash rebalance
    downstream -- not a division-by-zero crash."""
    clipped = {name: max(0.0, sharpe) for name, sharpe in sub_strategy_sharpe.items()}
    total = sum(clipped.values())
    if total <= 0:
        return {name: 0.0 for name in sub_strategy_sharpe}
    return {name: value / total for name, value in clipped.items()}


def composite_scores(
    sub_strategy_scores: dict[str, dict[str, float]],
    weights: dict[str, float],
) -> dict[str, float]:
    """Score_j(t) = sum_i(W_i * S_i,j(t)), summed across sub-strategies for
    every symbol any sub-strategy scored. Symbols with a zero-weighted
    sub-strategy still get contributions from the others -- only a
    sub-strategy's own weight is zeroed, never a symbol's."""
    composite: dict[str, float] = {}
    for sub_name, per_symbol in sub_strategy_scores.items():
        w = weights.get(sub_name, 0.0)
        if w == 0.0:
            continue
        for symbol, score in per_symbol.items():
            composite[symbol] = composite.get(symbol, 0.0) + w * score
    return composite


# ---------------------------------------------------------------------------
# 4. Portfolio construction: top-N filter + inverse-ATR risk-parity sizing
# ---------------------------------------------------------------------------

def top_n_by_score(scores: dict[str, float], n: int) -> list[str]:
    """Top `n` symbols with strictly positive composite score, highest
    first. A symbol at exactly 0.0 (no sub-strategy likes it) is excluded,
    not treated as a weak long -- 0.0 is the ensemble's neutral/cash value
    by construction (see module docstring's [-1, +1] scale)."""
    positive = {s: v for s, v in scores.items() if v > 0}
    ranked = sorted(positive, key=positive.get, reverse=True)
    return ranked[:n]


def inverse_atr_weights(
    atr_by_symbol: dict[str, float],
    symbols: list[str],
    max_weight: float = 0.20,
) -> dict[str, float]:
    """Target Weight_j = (1/ATR_j) / sum(1/ATR_k) -- lower-volatility names
    get more capital (risk parity), not equal-dollar weighting -- subject to
    a hard per-symbol cap of `max_weight`, filled by the standard iterative
    "water-filling" procedure: split the total 1.0 budget proportionally to
    1/ATR across every symbol not yet fixed at the cap; any symbol whose
    share would exceed the cap gets permanently fixed at exactly `max_weight`
    and removed from the pool; the remaining budget (1.0 minus what's now
    fixed) is re-split proportionally across whatever's left; repeat until a
    pass fixes nothing new. Fixing a symbol at the cap is permanent within
    one call -- redistributing overflow onto an already-capped symbol would
    push it back over the cap, which is exactly the bug this iteration
    exists to avoid (a single clip-then-redistribute pass can shove a
    previously-fine symbol over the cap when the redistributed overflow is
    large, as it is whenever one symbol's ATR is far smaller than the rest).

    If every symbol ends up fixed at the cap, `sum(weights) < 1.0` is the
    CORRECT output, not a bug -- e.g. 3 symbols at a 20% cap can reach at
    most 60% total; the unallocated remainder has nowhere to go without
    breaking the cap, and is implicitly left as cash (same "weights need not
    sum to 1.0" contract strategies.cross_sectional.CrossSectionalStrategy
    already documents).

    A symbol with zero/NaN/missing ATR is dropped (can't size risk parity
    against an undefined risk unit) rather than silently given some
    placeholder ATR that would misstate its relative risk."""
    usable = {
        s: atr_by_symbol[s]
        for s in symbols
        if s in atr_by_symbol and atr_by_symbol[s] is not None and atr_by_symbol[s] > 0
    }
    if not usable:
        return {}

    free = {s: 1.0 / v for s, v in usable.items()}  # inverse-ATR raw shares, still eligible
    fixed: dict[str, float] = {}  # symbols permanently pinned at max_weight

    for _ in range(len(usable) + 1):
        remaining_budget = 1.0 - sum(fixed.values())
        if remaining_budget <= 1e-12 or not free:
            break
        free_total = sum(free.values())
        proportional = {s: (v / free_total) * remaining_budget for s, v in free.items()}
        overflowing = [s for s, w in proportional.items() if w > max_weight + 1e-12]
        if not overflowing:
            fixed.update(proportional)
            free = {}
            break
        for s in overflowing:
            fixed[s] = max_weight
            del free[s]

    return fixed


def atr_as_of(bars: pd.DataFrame, as_of: pd.Timestamp, period: int = 14) -> float | None:
    """Latest ATR value as of `as_of`, or None if `bars` doesn't have
    enough history yet (fewer than `period` bars before `as_of`)."""
    window = bars.loc[:as_of]
    if len(window) < period + 1:
        return None
    value = atr(window, period).iloc[-1]
    return float(value) if pd.notna(value) else None
