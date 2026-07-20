"""Insider Buying -- dedicated engine for Variant A (Significant Single Buy)
and Variant B (Cluster Buy), built on engine/data_edgar.py's Form 4 purchase
feed and strategies/swing/insider_buy.py's signal generation.

Doesn't fit strategies.base.Strategy / the per-symbol bracket engine, for
two independent reasons documented at length elsewhere in this project:
  - The entry signal comes from an external per-symbol event feed (Form 4
    filings), not from `entry_signal(bars)` computed off OHLCV alone -- same
    situation PEAD was in (see strategies/swing/pead.py, LESSONS.md).
  - The exit is a genuinely FIXED N-day hold. `exit_signal(bars)` can't see
    how long a position has been open -- the same wall Turnaround Tuesday
    and PEAD hit, which forced both into signal-based approximations. Here
    the spec is explicit about wanting the real fixed hold (plus a hard
    stop), so this gets its own engine instead, following Overnight Hold's
    precedent: a dedicated engine emitting the project's standard result
    shape (BacktestMetrics) rather than bending the rule to fit the
    interface.
Sizing IS risk-based (1% of equity / 8% stop distance), which fits this
project's normal convention -- unlike Dividend Hybrid's flat 10%-of-equity
sizing, there's no reason to depart from risk-based sizing here.

Capital is shared across the whole universe, processed chronologically by
ENTRY date (entries lag signals by one trading day -- see
strategies/swing/insider_buy.py's signal_date/entry_date split), the same
"one account, not N independent ones" principle Dividend Hybrid documents.

SECTOR FILTER STATUS: strategies/swing/insider_buy.py's
PRE_REGISTERED_SECTORS is None (see that module's docstring for why -- the
Reddit post it should come from was never supplied). Every report this
module produces prints SECTOR_FILTER_WARNING at the top so a run is never
mistaken for the fully-specified strategy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from engine import data as data_module
from engine import regime as regime_module
from engine.backtest import DEFAULT_CASH
from engine.data_edgar import connect as edgar_connect
from engine.excursion import compute_trade_excursions
from engine.metrics import BacktestMetrics, compute_metrics
from engine.portfolio import annualized_stats
from engine.universe import TIMEZONE
from strategies.swing.insider_buy import (
    InsiderBuy,
    filing_level_purchases,
    passes_sector_filter,
    variant_a_signals,
    variant_b_signals,
)

VARIANT_A = "A"  # Significant Single Buy
VARIANT_B = "B"  # Cluster Buy

EXIT_STOP = "stop"
EXIT_TIME = "time"

LIQUIDITY_LOOKBACK_DAYS = 20

SECTOR_FILTER_WARNING = (
    "WARNING: no sector filter is applied -- PRE_REGISTERED_SECTORS is None "
    "(the Reddit post's sector list was never supplied). This run is "
    "sector-UNFILTERED, not the fully-specified strategy. See "
    "strategies/swing/insider_buy.py's module docstring."
)


@dataclass
class InsiderBuyResult:
    variant: str
    symbols: list[str]
    start: date
    end: date
    trades: pd.DataFrame
    equity_curve: pd.DataFrame
    metrics: BacktestMetrics
    n_signals: int
    n_entries: int
    n_blocked_by_regime: int
    n_blocked_by_liquidity: int
    n_blocked_by_sector: int
    mean_filing_lag_days: float | None
    pct_exit_time_stop: float | None
    pct_exit_hard_stop: float | None
    beta: float | None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _OpenPosition:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    size: int
    stop: float
    hold_days: int
    days_held: int = 0
    accession_no: str = ""
    filed_at: str = ""
    signal_date: str = ""
    transaction_date: str = ""


def _passes_liquidity(bars: pd.DataFrame, as_of: pd.Timestamp, config: InsiderBuy) -> bool:
    """Point-in-time: trailing LIQUIDITY_LOOKBACK_DAYS sessions ending at or
    before `as_of`, never bars after it."""
    window = bars.loc[:as_of].tail(LIQUIDITY_LOOKBACK_DAYS)
    if window.empty:
        return False
    last_price = float(window["Close"].iloc[-1])
    avg_dollar_volume = float((window["Close"] * window["Volume"]).mean())
    return last_price > config.min_price and avg_dollar_volume > config.min_dollar_volume


def _entry_date(bars: pd.DataFrame, signal_date: pd.Timestamp) -> pd.Timestamp | None:
    """First trading day strictly after `signal_date` in this symbol's own
    bars -- the actual fill calendar, not a synthetic one, so a symbol
    missing a session (e.g. late IPO, thin history edge) never manufactures
    a fake entry date."""
    later = bars.index[bars.index > signal_date]
    return later[0] if len(later) else None


def _build_signals(
    conn, config: InsiderBuy, symbols: list[str], start: date, end: date, variant: str
) -> pd.DataFrame:
    filings = filing_level_purchases(conn, symbols, start, end)
    if variant == VARIANT_A:
        sig = variant_a_signals(filings, config)
        if sig.empty:
            return sig
        sig = sig.rename(columns={"accession_no": "completing_accession_no"})
        sig["cluster_size"] = 1
        return sig
    if variant == VARIANT_B:
        return variant_b_signals(filings, config)
    raise ValueError(f"Unknown variant {variant!r}")


def run_insider_buy_backtest(
    config: InsiderBuy,
    symbols: list[str],
    start: date,
    end: date,
    variant: str,
    cash: float = DEFAULT_CASH,
    risk_free_rate: float = 0.0,
) -> InsiderBuyResult:
    conn = edgar_connect()
    try:
        signals = _build_signals(conn, config, symbols, start, end, variant)
    finally:
        conn.close()

    warnings: list[str] = [SECTOR_FILTER_WARNING]
    n_signals = len(signals)
    if signals.empty:
        return _empty_result(variant, symbols, start, end, cash, risk_free_rate, warnings)

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        b = data_module.get_bars(symbol, "1d", start - timedelta(days=60), end)
        if not b.empty:
            bars_by_symbol[symbol] = b

    spy_bars = regime_module.load_spy_bars(start, end)
    regime_labels = regime_module.regime_series(spy_bars)

    # Resolve every signal to an (entry_date, blocked_reason) pair up front,
    # so signals-vs-entries counts are exact regardless of loop order.
    n_blocked_regime = n_blocked_liquidity = n_blocked_sector = 0
    resolved: list[dict] = []
    for _, sig in signals.iterrows():
        symbol = sig["issuer_ticker"]
        bars = bars_by_symbol.get(symbol)
        if bars is None or bars.empty:
            continue
        # Bars are NY-localized throughout this project (engine/data.py) --
        # a naive Timestamp here would fail to compare against them at all.
        signal_ts = pd.Timestamp(sig["signal_date"]).tz_localize(TIMEZONE)

        if not passes_sector_filter(symbol):
            n_blocked_sector += 1
            continue
        if not _passes_liquidity(bars, signal_ts, config):
            n_blocked_liquidity += 1
            continue

        entry_ts = _entry_date(bars, signal_ts)
        if entry_ts is None:
            continue

        # Regime as of the most recent COMPLETE session before entry (the
        # signal date's own close) -- no look-ahead into the entry bar.
        regime_asof = regime_labels.loc[:signal_ts]
        regime_state = regime_asof.iloc[-1] if not regime_asof.empty else regime_module.NEUTRAL
        if regime_state != regime_module.BULLISH:
            n_blocked_regime += 1
            continue

        resolved.append({
            "symbol": symbol,
            "entry_date": entry_ts,
            "signal_date": signal_ts,
            "accession_no": sig["completing_accession_no"],
            "filed_at": sig.get("filed_at", ""),
            "transaction_date": sig.get("earliest_transaction_date", ""),
        })

    if not resolved:
        warnings.append("No signal survived the sector/liquidity/regime gates -- 0 entries.")
        return _empty_result(
            variant, symbols, start, end, cash, risk_free_rate, warnings,
            n_signals=n_signals, n_blocked_regime=n_blocked_regime,
            n_blocked_liquidity=n_blocked_liquidity, n_blocked_sector=n_blocked_sector,
        )

    entries_by_date: dict[pd.Timestamp, list[dict]] = {}
    for r in resolved:
        entries_by_date.setdefault(r["entry_date"], []).append(r)

    calendar = sorted(set().union(*(b.index for b in bars_by_symbol.values())))
    calendar = [c for c in calendar if start <= c.date() <= end]

    equity_cash = cash
    open_positions: dict[str, _OpenPosition] = {}
    closed_rows: list[dict] = []
    equity_times, equity_values = [], []
    # Tracks positions actually opened vs. signals that cleared sector/
    # liquidity/regime but still didn't become a trade (another position
    # already open in the same symbol, or sizing/cash rounded to zero
    # shares) -- the sector/liquidity/regime counters above only cover the
    # gates checked before this loop, so without this a signal can vanish
    # here with no accounting for where it went.
    n_actually_entered = 0
    n_blocked_concurrent_or_sizing = 0

    def _mark_to_market(today: pd.Timestamp) -> float:
        total = equity_cash
        for pos in open_positions.values():
            bars = bars_by_symbol[pos.symbol]
            window = bars["Close"].loc[:today]
            if not window.empty:
                total += float(window.iloc[-1]) * pos.size
        return total

    for today in calendar:
        # 1. New entries scheduled for today.
        for r in entries_by_date.get(today, []):
            symbol = r["symbol"]
            if symbol in open_positions:
                n_blocked_concurrent_or_sizing += 1
                continue  # one open position per symbol at a time
            bars = bars_by_symbol[symbol]
            if today not in bars.index:
                n_blocked_concurrent_or_sizing += 1
                continue
            entry_price = float(bars.loc[today, "Open"])
            if not entry_price > 0:
                n_blocked_concurrent_or_sizing += 1
                continue
            stop_price = entry_price * (1 - config.stop_pct / 100)
            risk_per_share = entry_price - stop_price
            if risk_per_share <= 0:
                n_blocked_concurrent_or_sizing += 1
                continue
            equity_now = _mark_to_market(today)
            size = int((equity_now * config.risk_pct_per_trade / 100) // risk_per_share)
            size = min(size, int(equity_cash // entry_price))
            if size < 1:
                n_blocked_concurrent_or_sizing += 1
                continue
            equity_cash -= entry_price * size
            open_positions[symbol] = _OpenPosition(
                symbol=symbol, entry_time=today, entry_price=entry_price, size=size,
                stop=stop_price, hold_days=config.hold_days,
                accession_no=r["accession_no"], filed_at=str(r["filed_at"]),
                signal_date=str(r["signal_date"].date()),
                transaction_date=str(r["transaction_date"]),
            )
            n_actually_entered += 1

        # 2. Manage open positions -- stop checked before the time exit, and
        #    a position entered today is evaluated against today's own bar
        #    too (a same-day collapse must be visible, matching Dividend
        #    Hybrid's fix for the identical bug).
        for symbol in list(open_positions):
            pos = open_positions[symbol]
            bars = bars_by_symbol[symbol]
            if today not in bars.index:
                continue
            bar = bars.loc[today]
            if today > pos.entry_time:
                pos.days_held += 1

            exit_price, reason = None, None
            if float(bar["Low"]) <= pos.stop:
                exit_price, reason = pos.stop, EXIT_STOP
            elif pos.days_held >= pos.hold_days:
                exit_price, reason = float(bar["Close"]), EXIT_TIME

            if exit_price is not None:
                equity_cash += exit_price * pos.size
                closed_rows.append(_trade_row(pos, today, exit_price, reason, bars))
                del open_positions[symbol]

        equity_times.append(today)
        equity_values.append(_mark_to_market(today))

    # Anything still open at window end: mark to market, record as "still_held".
    if calendar:
        final_time = calendar[-1]
        for symbol, pos in list(open_positions.items()):
            bars = bars_by_symbol[symbol]
            last_close = float(bars["Close"].loc[:final_time].iloc[-1])
            closed_rows.append(_trade_row(pos, final_time, last_close, "still_held", bars))
            equity_cash += last_close * pos.size

    trades = pd.DataFrame(closed_rows)
    equity_curve = pd.DataFrame({"Equity": equity_values}, index=pd.DatetimeIndex(equity_times))

    if n_blocked_concurrent_or_sizing:
        warnings.append(
            f"{n_blocked_concurrent_or_sizing} signal(s) cleared sector/liquidity/regime "
            "but did not become a trade (another position already open in the same "
            "symbol, or sizing rounded to zero shares)."
        )
    return _build_result(
        variant, symbols, start, end, trades, equity_curve, cash, risk_free_rate,
        warnings, n_signals, n_actually_entered, n_blocked_regime, n_blocked_liquidity,
        n_blocked_sector, spy_bars,
    )


def _trade_row(pos: _OpenPosition, exit_time: pd.Timestamp, exit_price: float, reason: str, bars: pd.DataFrame) -> dict:
    pnl = (exit_price - pos.entry_price) * pos.size
    risk_per_share = pos.entry_price - pos.stop
    entry_bar = bars.index.get_loc(pos.entry_time)
    exit_bar = bars.index.get_loc(exit_time)
    return {
        "Symbol": pos.symbol,
        "EntryTime": pos.entry_time,
        "ExitTime": exit_time,
        "EntryPrice": pos.entry_price,
        "ExitPrice": exit_price,
        "Size": pos.size,
        "SL": pos.stop,
        "TP": np.nan,
        "PnL": pnl,
        "ReturnPct": exit_price / pos.entry_price - 1,
        "Tag": risk_per_share,
        "ExitReason": reason,
        "AccessionNo": pos.accession_no,
        "FiledAt": pos.filed_at,
        "SignalDate": pos.signal_date,
        "TransactionDate": pos.transaction_date,
        "EntryBar": entry_bar,
        "ExitBar": exit_bar,
    }


def _alpha_beta(equity_curve: pd.DataFrame, spy_bars: pd.DataFrame, risk_free_rate: float) -> tuple[float | None, float | None]:
    """Portfolio alpha (annualized, %) and beta vs. SPY buy-and-hold, from
    daily-resampled returns -- standard single-factor CAPM regression
    (beta = Cov(port, bench) / Var(bench)), computed by hand rather than via
    a benchmark-aware library because the project's other alpha/beta figures
    (engine/backtest.py's aggregate_symbol_results) come from backtesting.py
    internals this hand-rolled chronological engine doesn't have access to
    -- same reason Overnight Hold/Dividend Hybrid leave both as None, except
    here the task explicitly wants them computed, so unlike those two this
    engine does the regression itself instead of disclosing an omission."""
    if equity_curve.empty or spy_bars.empty:
        return None, None
    port_daily = equity_curve["Equity"].resample("D").last().ffill().dropna()
    bench_daily = spy_bars["Close"].resample("D").last().ffill().dropna()
    idx = port_daily.index.intersection(bench_daily.index)
    if len(idx) < 3:
        return None, None
    port_ret = port_daily.loc[idx].pct_change().dropna()
    bench_ret = bench_daily.loc[idx].pct_change().dropna()
    idx2 = port_ret.index.intersection(bench_ret.index)
    port_ret, bench_ret = port_ret.loc[idx2], bench_ret.loc[idx2]
    if len(idx2) < 3 or bench_ret.var() == 0:
        return None, None

    beta = float(np.cov(port_ret, bench_ret)[0, 1] / bench_ret.var())
    port_cagr, _, _ = annualized_stats(port_daily, risk_free_rate)
    bench_cagr, _, _ = annualized_stats(bench_daily, risk_free_rate)
    if port_cagr is None or bench_cagr is None:
        return None, beta
    alpha_pct = (port_cagr / 100 - risk_free_rate) - beta * (bench_cagr / 100 - risk_free_rate)
    return alpha_pct * 100, beta


def _build_result(
    variant, symbols, start, end, trades, equity_curve, cash, risk_free_rate,
    warnings, n_signals, n_entries, n_blocked_regime, n_blocked_liquidity, n_blocked_sector,
    spy_bars,
) -> InsiderBuyResult:
    equity = equity_curve["Equity"] if not equity_curve.empty else pd.Series([cash])
    cagr, sharpe, sortino = annualized_stats(equity, risk_free_rate) if not equity_curve.empty else (None, None, None)
    drawdown = float((equity / equity.cummax() - 1).min() * 100) if len(equity) else 0.0
    alpha_pct, beta = _alpha_beta(equity_curve, spy_bars, risk_free_rate)

    # MFE/MAE-based exit efficiency / loss realization -- reuse the standard
    # diagnostic (engine/excursion.py) per symbol, using the EntryBar/ExitBar
    # positional indices already recorded on each trade row.
    excursions = []
    if not trades.empty:
        for symbol, group in trades.groupby("Symbol"):
            bars = data_module.get_bars(symbol, "1d", start - timedelta(days=60), end)
            if bars.empty:
                continue
            group_reset = group.reset_index(drop=True)
            exc = compute_trade_excursions(bars, group_reset)
            if not exc.empty:
                excursions.append(exc)
    excursions_df = pd.concat(excursions, ignore_index=True) if excursions else pd.DataFrame()

    metrics = compute_metrics(
        strategy_name=f"Insider Buying (Variant {variant})",
        symbol="ALL", trades=trades, start=start, end=end,
        max_drawdown_pct=abs(drawdown) if trades is not None else None,
        sharpe=sharpe, sortino=sortino, alpha_pct=alpha_pct, beta=beta,
        cagr_pct=cagr, risk_free_rate=risk_free_rate,
    )

    mean_lag = None
    pct_time, pct_stop = None, None
    if not trades.empty:
        lags = []
        for _, row in trades.iterrows():
            filed_raw, txn_raw = row["FiledAt"], row["TransactionDate"]
            if not filed_raw or not txn_raw:
                continue
            filed = pd.Timestamp(filed_raw)
            txn = pd.Timestamp(txn_raw)
            if pd.isna(filed) or pd.isna(txn):
                continue
            # Same definition as engine/data_edgar.py's Check 5 (filed date
            # minus transaction date), restricted to the signals that
            # actually became trades here -- NOT filed_at minus signal_date,
            # which is frequently negative/zero by construction whenever the
            # signal date rolled forward past filed_at's own calendar date
            # (after-hours filing) and would misreport as a "lag" artifact.
            lags.append((filed.date() - txn.date()).days)
        if lags:
            mean_lag = float(np.mean(lags))
        closed = trades[trades["ExitReason"] != "still_held"]
        if len(closed):
            pct_time = float((closed["ExitReason"] == EXIT_TIME).mean() * 100)
            pct_stop = float((closed["ExitReason"] == EXIT_STOP).mean() * 100)

    return InsiderBuyResult(
        variant=variant, symbols=symbols, start=start, end=end,
        trades=trades, equity_curve=equity_curve, metrics=metrics,
        n_signals=n_signals, n_entries=n_entries,
        n_blocked_by_regime=n_blocked_regime, n_blocked_by_liquidity=n_blocked_liquidity,
        n_blocked_by_sector=n_blocked_sector,
        mean_filing_lag_days=mean_lag, pct_exit_time_stop=pct_time, pct_exit_hard_stop=pct_stop,
        beta=beta, warnings=warnings,
    )


def _empty_result(
    variant, symbols, start, end, cash, risk_free_rate, warnings,
    n_signals=0, n_blocked_regime=0, n_blocked_liquidity=0, n_blocked_sector=0,
) -> InsiderBuyResult:
    return InsiderBuyResult(
        variant=variant, symbols=symbols, start=start, end=end,
        trades=pd.DataFrame(), equity_curve=pd.DataFrame({"Equity": [cash]}),
        metrics=compute_metrics(f"Insider Buying (Variant {variant})", "ALL", pd.DataFrame(), start, end),
        n_signals=n_signals, n_entries=0,
        n_blocked_by_regime=n_blocked_regime, n_blocked_by_liquidity=n_blocked_liquidity,
        n_blocked_by_sector=n_blocked_sector,
        mean_filing_lag_days=None, pct_exit_time_stop=None, pct_exit_hard_stop=None,
        beta=None, warnings=warnings,
    )
