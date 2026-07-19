"""Portfolio engine for Dividend Hybrid, running both exit versions.

Why this isn't engine/backtest.py: position size is a flat 10% of account
equity (not risk-based), Version A places no stop and holds losers to the end
of the window, and the required outputs (max unrealized drawdown during a
hold, still-held-at-end count, dividend cuts during the hold) have nowhere to
live on a bracket-engine trade row. Same situation Overnight Hold was in --
so, same resolution: a dedicated engine that emits the project's standard
metrics shape. Existing engine code is untouched.

Capital is shared across the universe, chronologically, because "10% of
account equity per trade" and "warn above 3 concurrent positions" are both
statements about one account, not about N independent ones (see LESSONS.md
on why N independent single-symbol backtests are not a portfolio).

The comparison this exists to produce:
    Version A -- take profit at the entry-date trailing yield %, NO stop,
                 hold indefinitely if it goes against you.
    Version B -- identical take profit, plus a hard 8% stop.

Version A's closed trades are 100% winners BY CONSTRUCTION: with no stop,
the only way a trade closes is by hitting its target. Reading its win rate
without the still-held count next to it is the exact illusion the original
thesis rests on, so every still-held position is marked to market at the
final close and included in the headline metrics, with the closed-only view
reported separately rather than as the default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from engine import data as data_module
from engine import fundamentals as fundamentals_module
from engine.backtest import DEFAULT_CASH
from engine.metrics import BacktestMetrics, compute_metrics
from engine.portfolio import annualized_stats
from strategies.swing.dividend_hybrid import (
    DRAWDOWN_BUCKETS_PCT,
    MANUAL_CHECKPOINTS,
    NOMINAL_RISK_PCT,
    THESIS_BREAKDOWN_PCT,
    DividendHybrid,
    entry_trigger,
    point_in_time_fundamental_screen,
    snapshot_fundamental_screen,
    technical_screen,
)

VERSION_A = "A"  # no stop -- the original strategy
VERSION_B = "B"  # 8% hard stop -- the test variant

SCREEN_POINT_IN_TIME = "point_in_time"
SCREEN_FULL = "full"

MAX_CONCURRENT_WARN = 3
MIN_BARS = 220  # need a 200-day SMA before the technical screen means anything


@dataclass
class DividendHybridResult:
    version: str
    screen_mode: str
    symbols: list[str]
    start: date
    end: date
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    metrics: BacktestMetrics
    final_equity: float
    return_pct: float
    max_drawdown_pct: float
    sharpe: float | None
    sortino: float | None
    cagr_pct: float | None
    risk_free_rate: float
    still_held: int
    closed_trades: int
    dividend_cuts_during_hold: int
    drawdown_bucket_counts: dict[float, int]
    thesis_breakdown_trades: int
    max_concurrent_positions: int
    screen_log: pd.DataFrame
    warnings: list[str] = field(default_factory=list)

    def closed_only_metrics(self) -> BacktestMetrics:
        """Metrics over closed trades alone -- Version A's flattering view,
        reported beside the marked-to-market headline, never instead of it."""
        if self.trades.empty or "ExitReason" not in self.trades.columns:
            return compute_metrics(self.metrics.strategy_name, "ALL", pd.DataFrame(),
                                   self.start, self.end)
        closed = self.trades[self.trades["ExitReason"] != "still_held"]
        return compute_metrics(
            strategy_name=f"{self.metrics.strategy_name} (closed only)",
            symbol="ALL", trades=closed, start=self.start, end=self.end,
        )


def _symbol_frames(
    symbol: str, config: DividendHybrid, start: date, end: date, screen_mode: str
) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """Bars plus a per-bar frame of screen columns, trigger columns, the
    entry-date trailing yield (which sets the take-profit) and the
    dividend-cut flag. None if the symbol has too little history."""
    bars = data_module.get_bars(symbol, "1d", start, end)
    if bars.empty or len(bars) < MIN_BARS:
        return None

    fundamentals = fundamentals_module.fundamentals_frame(symbol, bars.index)
    technical = technical_screen(bars, config)
    pit = point_in_time_fundamental_screen(fundamentals, config)

    screen = pd.concat([technical, pit], axis=1)
    if screen_mode == SCREEN_FULL:
        snapshot = fundamentals_module.snapshot(symbol)
        screen = pd.concat(
            [screen, snapshot_fundamental_screen(snapshot, bars["Close"], config)], axis=1
        )

    trigger = entry_trigger(bars, config)
    combined = pd.DataFrame(index=bars.index)
    combined["screen_pass"] = screen.all(axis=1)
    combined["yield_pass"] = pit["yield_ok"]
    combined["trigger_pass"] = trigger.all(axis=1)
    combined["signal"] = combined["screen_pass"] & combined["trigger_pass"]
    combined["signal_screen_only"] = combined["screen_pass"]
    combined["entry_yield_pct"] = fundamentals["trailing_dividend_yield_pct"]
    combined["dividend_cut"] = fundamentals["dividend_cut"]
    return bars, combined


@dataclass
class _OpenPosition:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    size: int
    target: float
    stop: float | None
    worst_price: float
    dividend_cut_seen: bool


def run_dividend_hybrid(
    strategy_name: str,
    config: DividendHybrid,
    symbols: list[str],
    start: date,
    end: date,
    version: str,
    screen_mode: str = SCREEN_POINT_IN_TIME,
    cash: float = DEFAULT_CASH,
    risk_free_rate: float = 0.0,
    require_trigger: bool = True,
) -> DividendHybridResult:
    """Run one (version, screen_mode) combination over a shared capital pool.

    `require_trigger=False` enters on the SCREEN alone, ignoring the gap-up
    entry trigger. That is not the strategy as specified -- it is the only way
    to get a testable sample of the thing the strategy is actually arguing
    about. The daily rendering of the intraday trigger fires on 11 of the 169
    screen-passing bars in this universe and never alongside the volume and
    pullback legs, so the strategy as specified produces zero trades over five
    years and its central claim ("no stop is safe, the dividend is a floor")
    cannot be evaluated at all. Screen-only entry holds the exit rules,
    sizing, and capital model fixed and varies only the entry, so the A-vs-B
    comparison it produces is a genuine test of the stop question even though
    it is not a backtest of the full strategy. Reported as its own row, never
    merged with the trigger-required results.
    """
    signal_column = "signal" if require_trigger else "signal_screen_only"
    per_symbol: dict[str, tuple[pd.DataFrame, pd.DataFrame]] = {}
    for symbol in symbols:
        frames = _symbol_frames(symbol, config, start, end, screen_mode)
        if frames is not None:
            per_symbol[symbol] = frames

    if not per_symbol:
        return _empty_result(strategy_name, version, screen_mode, symbols, start, end,
                             cash, risk_free_rate)

    calendar = sorted(set().union(*(bars.index for bars, _ in per_symbol.values())))
    equity_cash = cash
    open_positions: dict[str, _OpenPosition] = {}
    closed_rows: list[dict] = []
    equity_times, equity_values = [], []
    screen_rows: list[dict] = []
    warnings: list[str] = []
    max_concurrent = 0

    for i, today in enumerate(calendar):
        # 1. Entries from signals that fired on the PREVIOUS bar, filled at
        #    today's open (no look-ahead -- see the strategy module docstring).
        if i > 0:
            previous = calendar[i - 1]
            for symbol, (bars, signals) in per_symbol.items():
                if symbol in open_positions:
                    continue
                if previous not in signals.index or today not in bars.index:
                    continue
                if not bool(signals.loc[previous, signal_column]):
                    continue
                entry_yield = float(signals.loc[previous, "entry_yield_pct"])
                if not np.isfinite(entry_yield) or entry_yield <= 0:
                    continue
                entry_price = float(bars.loc[today, "Open"])
                if not entry_price > 0:
                    continue
                equity_now = _mark_to_market(equity_cash, open_positions, per_symbol, today)
                size = int((equity_now * config.position_pct_of_equity / 100) // entry_price)
                if size < 1 or size * entry_price > equity_cash:
                    continue
                equity_cash -= entry_price * size
                open_positions[symbol] = _OpenPosition(
                    symbol=symbol,
                    entry_time=today,
                    entry_price=entry_price,
                    size=size,
                    target=entry_price * (1 + entry_yield / 100),
                    stop=(entry_price * (1 - config.stop_pct / 100)
                          if version == VERSION_B else None),
                    worst_price=entry_price,
                    dividend_cut_seen=False,
                )

        if len(open_positions) > max_concurrent:
            max_concurrent = len(open_positions)

        # 2. Manage every open position against today's bar -- INCLUDING one
        #    entered at today's open, whose stop and target are live for the
        #    rest of the session. Checking exits before entries would let a
        #    position enter at the open and ignore a 20% intraday collapse on
        #    the same bar, which would understate Version B's stop-outs.
        for symbol in list(open_positions):
            position = open_positions[symbol]
            bars, signals = per_symbol[symbol]
            if today not in bars.index:
                continue
            bar = bars.loc[today]
            position.worst_price = min(position.worst_price, float(bar["Low"]))
            if bool(signals.loc[today, "dividend_cut"]):
                position.dividend_cut_seen = True

            exit_price, reason = None, None
            # Stop checked before target: when a bar's range spans both, a
            # daily bar can't say which came first, so assume the adverse one.
            if position.stop is not None and float(bar["Low"]) <= position.stop:
                exit_price, reason = position.stop, "stop"
            elif float(bar["High"]) >= position.target:
                exit_price, reason = position.target, "target"

            if exit_price is not None:
                equity_cash += exit_price * position.size
                closed_rows.append(_trade_row(position, today, exit_price, reason))
                del open_positions[symbol]

        # 3. Screening diagnostic + equity mark.
        yield_passers = sum(
            1 for _, signals in per_symbol.values()
            if today in signals.index and bool(signals.loc[today, "yield_pass"])
        )
        evaluated = sum(1 for _, signals in per_symbol.values() if today in signals.index)
        screen_passers = sum(
            1 for _, signals in per_symbol.values()
            if today in signals.index and bool(signals.loc[today, "screen_pass"])
        )
        screen_rows.append({
            "date": today, "evaluated": evaluated,
            "yield_pass": yield_passers, "screen_pass": screen_passers,
            "yield_pass_rate": yield_passers / evaluated if evaluated else np.nan,
        })

        equity_times.append(today)
        equity_values.append(_mark_to_market(equity_cash, open_positions, per_symbol, today))

    # 4. Anything still open at the window end is marked to market and kept in
    #    the trade table. Version A's whole risk profile lives in these rows.
    final_time = calendar[-1]
    for symbol, position in list(open_positions.items()):
        bars, _ = per_symbol[symbol]
        last_close = float(bars["Close"].loc[:final_time].iloc[-1])
        closed_rows.append(_trade_row(position, final_time, last_close, "still_held"))
        equity_cash += last_close * position.size

    trades = pd.DataFrame(closed_rows)
    equity_curve = pd.DataFrame(
        {"Equity": equity_values}, index=pd.DatetimeIndex(equity_times)
    )

    if max_concurrent > MAX_CONCURRENT_WARN:
        pct = max_concurrent * config.position_pct_of_equity
        warnings.append(
            f"WARNING: {max_concurrent} positions open simultaneously "
            f"({pct:.0f}% of account committed)"
            + (" with undefined downside -- Version A has no stop."
               if version == VERSION_A else ".")
        )

    return _build_result(
        strategy_name, config, version, screen_mode, symbols, start, end,
        trades, equity_curve, cash, risk_free_rate,
        pd.DataFrame(screen_rows), warnings, max_concurrent,
    )


def _mark_to_market(
    cash_balance: float,
    open_positions: dict[str, _OpenPosition],
    per_symbol: dict[str, tuple[pd.DataFrame, pd.DataFrame]],
    today: pd.Timestamp,
) -> float:
    total = cash_balance
    for symbol, position in open_positions.items():
        bars, _ = per_symbol[symbol]
        window = bars["Close"].loc[:today]
        if not window.empty:
            total += float(window.iloc[-1]) * position.size
    return total


def _trade_row(
    position: _OpenPosition, exit_time: pd.Timestamp, exit_price: float, reason: str
) -> dict:
    pnl = (exit_price - position.entry_price) * position.size
    worst_pct = (position.worst_price / position.entry_price - 1) * 100
    # Nominal risk unit, not a real stop for Version A -- see the strategy
    # module. Keeps R-multiples comparable across both versions.
    nominal_risk = position.entry_price * NOMINAL_RISK_PCT / 100
    return {
        "Symbol": position.symbol,
        "EntryTime": position.entry_time,
        "ExitTime": exit_time,
        "EntryPrice": position.entry_price,
        "ExitPrice": exit_price,
        "Size": position.size,
        "SL": position.stop if position.stop is not None else np.nan,
        "TP": position.target,
        "PnL": pnl,
        "ReturnPct": exit_price / position.entry_price - 1,
        "Tag": nominal_risk,
        "ExitReason": reason,
        "MaxUnrealizedDrawdownPct": worst_pct,
        "DividendCutDuringHold": position.dividend_cut_seen,
    }


def _build_result(
    strategy_name, config, version, screen_mode, symbols, start, end,
    trades, equity_curve, cash, risk_free_rate, screen_log, warnings, max_concurrent,
) -> DividendHybridResult:
    equity = equity_curve["Equity"]
    cagr, sharpe, sortino = annualized_stats(equity, risk_free_rate)
    drawdown = float((equity / equity.cummax() - 1).min() * 100) if len(equity) else 0.0
    final_equity = float(equity.iloc[-1]) if len(equity) else cash
    return_pct = (final_equity / cash - 1) * 100

    metrics = compute_metrics(
        strategy_name=f"{strategy_name} (Version {version}, {screen_mode})",
        symbol="ALL", trades=trades, start=start, end=end,
        max_drawdown_pct=abs(drawdown), sharpe=sharpe, sortino=sortino,
        cagr_pct=cagr, risk_free_rate=risk_free_rate,
    )

    still_held = int((trades["ExitReason"] == "still_held").sum()) if not trades.empty else 0
    cuts = int(trades["DividendCutDuringHold"].sum()) if not trades.empty else 0
    buckets = {
        level: (int((trades["MaxUnrealizedDrawdownPct"] <= -level).sum())
                if not trades.empty else 0)
        for level in DRAWDOWN_BUCKETS_PCT
    }
    breakdown = buckets.get(THESIS_BREAKDOWN_PCT, 0)
    if breakdown:
        warnings.append(
            f"WARNING: {breakdown} trade(s) hit an unrealized loss worse than "
            f"{THESIS_BREAKDOWN_PCT:.0f}% -- the dividend-floor thesis does not "
            "survive at that level; no yield compensates it."
        )
    if cuts:
        warnings.append(
            f"WARNING: {cuts} trade(s) held a position through a dividend CUT "
            "-- the floor the no-stop rule relies on was removed mid-hold."
        )

    return DividendHybridResult(
        version=version, screen_mode=screen_mode, symbols=symbols, start=start, end=end,
        trades=trades, equity_curve=equity_curve, metrics=metrics,
        final_equity=final_equity, return_pct=return_pct,
        max_drawdown_pct=abs(drawdown), sharpe=sharpe, sortino=sortino, cagr_pct=cagr,
        risk_free_rate=risk_free_rate, still_held=still_held,
        closed_trades=(len(trades) - still_held),
        dividend_cuts_during_hold=cuts, drawdown_bucket_counts=buckets,
        thesis_breakdown_trades=breakdown, max_concurrent_positions=max_concurrent,
        screen_log=screen_log, warnings=warnings,
    )


def _empty_result(strategy_name, version, screen_mode, symbols, start, end, cash, rf):
    return DividendHybridResult(
        version=version, screen_mode=screen_mode, symbols=symbols, start=start, end=end,
        trades=pd.DataFrame(), equity_curve=pd.DataFrame({"Equity": [cash]}),
        metrics=compute_metrics(strategy_name, "ALL", pd.DataFrame(), start, end),
        final_equity=cash, return_pct=0.0, max_drawdown_pct=0.0, sharpe=None,
        sortino=None, cagr_pct=None, risk_free_rate=rf, still_held=0, closed_trades=0,
        dividend_cuts_during_hold=0,
        drawdown_bucket_counts={level: 0 for level in DRAWDOWN_BUCKETS_PCT},
        thesis_breakdown_trades=0, max_concurrent_positions=0,
        screen_log=pd.DataFrame(), warnings=["No symbol had enough history to screen."],
    )


def manual_checkpoint_notice() -> str:
    """The qualitative gates that were NOT applied, restated in full for every
    run's output so a backtest is never mistaken for the complete strategy."""
    lines = [
        "MANUAL CHECKPOINTS -- NOT APPLIED IN THIS BACKTEST.",
        "Live trading would use these as a final human gate before entry:",
    ]
    lines += [f"  [ ] {item}" for item in MANUAL_CHECKPOINTS]
    return "\n".join(lines)
