"""Causal next-open/close-exit engine for frozen event hypotheses."""

from __future__ import annotations

from datetime import date
from typing import Callable

import numpy as np
import pandas as pd

from engine import data as data_module
from engine.backtest import DEFAULT_CASH, StrategyBacktestResult, SymbolBacktestResult, aggregate_symbol_results
from engine.event_timing import ExecutionTiming, reaction_session, timing_contract_for, validate_timing_contract
from engine.execution_calibration import spread_for
from engine.indicators import atr, rsi, sma
from engine.matched_benchmark import annotate_trades
from engine.portfolio import annualized_stats
from strategies.swing.frozen_research import FrozenEventStrategy


def earnings_reaction_dates(symbol: str, bars: pd.DataFrame) -> tuple[set[date], str | None]:
    """Return reaction sessions, or a disclosed reason the exclusion is unknown."""
    try:
        events = data_module.earnings_dates(symbol)
    except Exception as exc:
        return set(), f"{type(exc).__name__}: {exc}"
    if events is None or events.empty:
        return set(), "no timestamped earnings events available"
    reactions: set[date] = set()
    for event in events.index:
        session = reaction_session(bars.index, event)
        if session is not None:
            reactions.add(pd.Timestamp(session).date())
    return reactions, None


def precompute_signal_features(
    bars: pd.DataFrame, market_bars: pd.DataFrame,
) -> pd.DataFrame:
    """Causal per-session features shared by every preregistered arm.

    Every column is computed from observations at or before its own index.
    This is a speed cache only: ``signals_from_features`` mirrors the frozen
    strategy ``signal`` methods and regression tests compare both paths.
    """
    features = pd.DataFrame(index=bars.index)
    close = pd.to_numeric(bars["Close"], errors="coerce")
    volume = pd.to_numeric(bars["Volume"], errors="coerce")
    features["Close"] = close
    features["position"] = np.arange(len(bars))
    features["return_1d"] = close.pct_change()
    prior_median = volume.shift(1).rolling(20).median()
    features["volume_ratio"] = volume / prior_median.replace(0, np.nan)
    features["sma200"] = sma(close, 200)
    span = pd.to_numeric(bars["High"], errors="coerce") - pd.to_numeric(
        bars["Low"], errors="coerce"
    )
    features["close_location"] = (
        (close - pd.to_numeric(bars["Low"], errors="coerce"))
        / span.replace(0, np.nan)
    ).fillna(0.5)
    features["rsi2"] = rsi(close, 2)
    features["sma5"] = sma(close, 5)
    atr14 = atr(bars, 14)
    features["atr14"] = atr14
    normalized_atr = (atr14 / close).replace([np.inf, -np.inf], np.nan)
    atr_percentile = pd.Series(np.nan, index=bars.index, dtype=float)
    clean_atr = normalized_atr.dropna()
    for stamp in clean_atr.index:
        history = clean_atr.loc[:stamp].iloc[-252:]
        if len(history) >= 60:
            atr_percentile.loc[stamp] = float(
                (history.iloc[:-1] <= history.iloc[-1]).mean()
            )
    features["atr_percentile"] = atr_percentile

    market_close = pd.to_numeric(market_bars.get("Close"), errors="coerce")
    market_sma = sma(market_close, 200)
    features["market_close"] = market_close.reindex(bars.index, method="ffill")
    features["market_sma200"] = market_sma.reindex(bars.index, method="ffill")
    market_count = pd.Series(
        np.arange(1, len(market_bars) + 1), index=market_bars.index, dtype=float
    )
    features["market_count"] = market_count.reindex(
        bars.index, method="ffill"
    ).fillna(0.0)

    returns = close.pct_change()
    max5 = pd.Series(np.nan, index=bars.index, dtype=float)
    max5_volume_ratio = pd.Series(np.nan, index=bars.index, dtype=float)
    for current in range(4, len(bars)):
        recent = returns.iloc[current - 4:current + 1]
        if recent.dropna().empty:
            continue
        event_time = recent.idxmax()
        event_position = bars.index.get_loc(event_time)
        max5.iloc[current] = float(recent.loc[event_time])
        if event_position >= 20:
            median = float(volume.iloc[event_position - 20:event_position].median())
            if median > 0:
                max5_volume_ratio.iloc[current] = float(
                    volume.iloc[event_position] / median
                )
    features["max5"] = max5
    features["max5_volume_ratio"] = max5_volume_ratio
    return features


def signals_from_features(
    strategy: FrozenEventStrategy, features: pd.DataFrame,
) -> dict[date, str]:
    """Return the same close-known signals as ``strategy.signal`` in O(n)."""
    position = features["position"]
    if strategy.name == "Negative Return + Volume Shock Reversal":
        mask = (
            (position >= 200)
            & (features["return_1d"] <= strategy.return_threshold)
            & (features["volume_ratio"] >= strategy.volume_ratio)
            & (
                (not strategy.trend_filter)
                | (features["Close"] > features["sma200"])
            )
        )
        direction = "long"
    elif strategy.name.startswith("Volume-Shock Continuation"):
        common = (position >= 21) & (
            features["volume_ratio"] >= strategy.volume_ratio
        )
        if strategy.direction == "long":
            mask = common & (features["return_1d"] > 0) & (
                features["close_location"] >= strategy.close_location_threshold
            )
            direction = "long"
        else:
            mask = common & (features["return_1d"] < 0) & (
                features["close_location"] <= 1.0 - strategy.close_location_threshold
            )
            direction = "short"
    elif strategy.name == "MAX Lottery-Return Reversal (Short)":
        mask = (
            (position >= 25)
            & (features["max5"] >= strategy.max_threshold)
            & (features["max5_volume_ratio"] >= strategy.volume_ratio)
        )
        direction = "short"
    elif strategy.name == "Volatility-Conditioned Pullback":
        mask = (
            (position >= 251)
            & (features["Close"] > features["sma200"])
            & (features["rsi2"] <= strategy.rsi_threshold)
            & (features["atr_percentile"] >= strategy.atr_percentile_low)
            & (features["atr_percentile"] <= strategy.atr_percentile_high)
            & (
                (not strategy.market_trend_filter)
                | (
                    (features["market_count"] >= 200)
                    & (features["market_close"] > features["market_sma200"])
                )
            )
        )
        direction = "long"
    else:
        raise ValueError(f"No cached feature rule for {strategy.name!r}")
    return {
        pd.Timestamp(stamp).date(): direction
        for stamp in features.index[mask.fillna(False)]
    }


def _symbol_stats(equity: pd.Series, trades: pd.DataFrame, bars: pd.DataFrame, risk_free_rate: float, exposure_days: int) -> pd.Series:
    cagr, sharpe, sortino = annualized_stats(equity, risk_free_rate, cash_accrued=True)
    ret = float((equity.iloc[-1] / equity.iloc[0] - 1.0) * 100.0) if len(equity) else 0.0
    drawdown = float((equity / equity.cummax() - 1.0).min() * 100.0) if len(equity) else 0.0
    buy_hold = float((bars["Close"].iloc[-1] / bars["Close"].iloc[0] - 1.0) * 100.0) if len(bars) > 1 else np.nan
    beta = np.nan
    aligned = pd.concat([equity.pct_change().rename("strategy"), bars["Close"].pct_change().rename("asset")], axis=1).dropna()
    if len(aligned) >= 2 and float(aligned["asset"].var()) > 0:
        beta = float(aligned.cov().loc["strategy", "asset"] / aligned["asset"].var())
    return pd.Series({
        "Sharpe Ratio": np.nan if sharpe is None else sharpe,
        "Sortino Ratio": np.nan if sortino is None else sortino,
        "Max. Drawdown [%]": drawdown,
        "Return [%]": ret,
        "CAGR [%]": np.nan if cagr is None else cagr,
        "Exposure Time [%]": exposure_days / len(bars) * 100.0 if len(bars) else np.nan,
        "Alpha [%]": ret - buy_hold,
        "Beta": beta,
        "Buy & Hold Return [%]": buy_hold,
    })


def run_event_symbol(
    strategy: FrozenEventStrategy,
    symbol: str,
    bars: pd.DataFrame,
    market_bars: pd.DataFrame,
    start: date,
    end: date,
    risk_free_rate: float,
    membership_at: Callable[[date], set[str]],
    earnings_sessions: set[date],
    earnings_unknown: str | None,
    signal_by_date: dict[date, str] | None = None,
    risk_by_date: dict[date, float] | None = None,
    signal_exit_dates: set[date] | None = None,
) -> SymbolBacktestResult:
    validate_timing_contract(timing_contract_for(strategy), actual_execution=ExecutionTiming.NEXT_OPEN)
    traded = bars.loc[(bars.index.date >= start) & (bars.index.date <= end)].copy()
    if traded.empty:
        return SymbolBacktestResult(symbol, None, pd.DataFrame(), None)
    spread = spread_for(symbol, start, end)
    daily_rf = (1.0 + risk_free_rate) ** (1.0 / 252.0) - 1.0 if risk_free_rate else 0.0
    equity = DEFAULT_CASH
    position: dict | None = None
    pending: dict | None = None
    rows: list[dict] = []
    equity_points: list[tuple[pd.Timestamp, float]] = []
    exposure_days = 0

    all_positions = {stamp: i for i, stamp in enumerate(bars.index)}
    for day in traded.index:
        absolute = all_positions[day]
        if position is None:
            equity *= 1.0 + daily_rf

        if pending is not None:
            raw_entry = float(bars.loc[day, "Open"])
            direction = pending["direction"]
            entry_price = raw_entry * (1.0 + spread if direction == "long" else 1.0 - spread)
            size = int(equity // entry_price) if entry_price > 0 else 0
            if size > 0:
                position = {
                    **pending, "entry_position": absolute, "entry_time": day,
                    "entry_price": entry_price, "raw_entry": raw_entry,
                    "size": size, "equity_at_entry": equity,
                }
            pending = None

        if position is not None:
            exposure_days += 1
            if signal_by_date is None:
                history = bars.iloc[: absolute + 1]
                exit_due = strategy.exit_due(
                    history, position["entry_position"], absolute
                )
            else:
                exit_due = (
                    absolute >= position["entry_position"] + strategy.holding_sessions - 1
                    or (
                        signal_exit_dates is not None
                        and pd.Timestamp(day).date() in signal_exit_dates
                    )
                )
            if exit_due:
                raw_exit = float(bars.loc[day, "Close"])
                direction_sign = 1 if position["direction"] == "long" else -1
                exit_price = raw_exit * (1.0 - spread if direction_sign > 0 else 1.0 + spread)
                pnl = direction_sign * (exit_price - position["entry_price"]) * position["size"]
                equity = position["equity_at_entry"] + pnl
                modeled_cost = position["size"] * (position["raw_entry"] + raw_exit) * spread
                rows.append({
                    "EntryTime": position["entry_time"], "ExitTime": day,
                    "Size": direction_sign * position["size"],
                    "EntryPrice": position["entry_price"], "ExitPrice": exit_price,
                    "SL": np.nan, "TP": np.nan, "PnL": pnl,
                    "ReturnPct": pnl / (position["entry_price"] * position["size"]),
                    "Tag": position["risk"], "ModeledCost": modeled_cost,
                })
                position = None
            else:
                direction_sign = 1 if position["direction"] == "long" else -1
                equity = position["equity_at_entry"] + direction_sign * (
                    float(bars.loc[day, "Close"]) - position["entry_price"]
                ) * position["size"]

        if position is None and pending is None and absolute + 1 < len(bars):
            signal_date = pd.Timestamp(day).date()
            if symbol in membership_at(signal_date):
                if signal_by_date is None:
                    history = bars.iloc[: absolute + 1]
                    market_history = market_bars.loc[market_bars.index <= day]
                    direction = strategy.signal(history, market_history)
                else:
                    direction = signal_by_date.get(signal_date)
                if direction is not None:
                    event_mode = strategy.earnings_mode()
                    # Unknown coverage can never satisfy either "excluded"
                    # or "earnings only".  The explicit included diagnostic
                    # does not need classification because it accepts both.
                    if event_mode != "included" and earnings_unknown is not None:
                        direction = None
                    elif event_mode == "excluded" and signal_date in earnings_sessions:
                        direction = None
                    elif event_mode == "earnings_only_diagnostic" and signal_date not in earnings_sessions:
                        direction = None
                if direction is not None:
                    risk = (
                        float(atr(history, 14).iloc[-1])
                        if risk_by_date is None
                        else float(risk_by_date.get(signal_date, np.nan))
                    )
                    if np.isfinite(risk) and risk > 0:
                        pending = {"direction": direction, "risk": risk}

        equity_points.append((day, equity))

    trades = pd.DataFrame(rows)
    curve = pd.Series([value for _, value in equity_points], index=pd.DatetimeIndex([stamp for stamp, _ in equity_points]))
    stats = _symbol_stats(curve, trades, traded, risk_free_rate, exposure_days)
    return SymbolBacktestResult(symbol, stats, trades, pd.DataFrame({"Equity": curve}))


def run_frozen_event_backtest(
    strategy_name: str,
    strategy: FrozenEventStrategy,
    symbols: list[str],
    bars_by_symbol: dict[str, pd.DataFrame],
    market_bars: pd.DataFrame,
    start: date,
    end: date,
    risk_free_rate: float,
    membership_at: Callable[[date], set[str]],
    earnings_cache: dict[str, tuple[set[date], str | None]] | None = None,
    signals_by_symbol: dict[str, dict[date, str]] | None = None,
    risk_by_symbol: dict[str, dict[date, float]] | None = None,
    signal_exits_by_symbol: dict[str, set[date]] | None = None,
) -> StrategyBacktestResult:
    per_symbol: dict[str, SymbolBacktestResult] = {}
    unknown: dict[str, str] = {}
    for symbol in symbols:
        bars = bars_by_symbol.get(symbol, pd.DataFrame())
        if bars.empty:
            continue
        earnings_sessions, reason = (
            earnings_cache[symbol]
            if earnings_cache is not None and symbol in earnings_cache
            else earnings_reaction_dates(symbol, bars)
        )
        if reason:
            unknown[symbol] = reason
        per_symbol[symbol] = run_event_symbol(
            strategy, symbol, bars, market_bars, start, end, risk_free_rate,
            membership_at, earnings_sessions, reason,
            None if signals_by_symbol is None else signals_by_symbol.get(symbol, {}),
            None if risk_by_symbol is None else risk_by_symbol.get(symbol, {}),
            None if signal_exits_by_symbol is None else signal_exits_by_symbol.get(symbol, set()),
        )
    for result in per_symbol.values():
        result.trades = annotate_trades(result.trades, market_bars)
    output = aggregate_symbol_results(
        strategy_name, symbols, per_symbol, start, end, risk_free_rate
    )
    output.research_metadata = {
        "frozenV1": True,
        "pointInTimeUniverse": "dow_jones_industrial_average",
        "earningsExclusionRequired": strategy.requires_earnings_exclusion,
        "earningsMode": strategy.earnings_mode(),
        "earningsCoverageUnknown": unknown,
        "borrowAssumption": strategy.borrow_assumption,
    }
    return output
