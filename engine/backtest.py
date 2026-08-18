"""Adapter that runs our Strategy objects through backtesting.py.

This is the only module that imports backtesting.py -- strategies stay
library-agnostic (see strategies/base.py), and swapping the underlying
engine later only touches this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from backtesting import Backtest
from backtesting import Strategy as BTStrategy
from backtesting._stats import compute_stats as _compute_stats

from engine import data as data_module
from engine.excursion import compute_trade_excursions
from engine.execution_calibration import spread_for
from engine.event_timing import ExecutionTiming, timing_contract_for, validate_timing_contract
from engine.metrics import TRADING_DAYS_PER_YEAR, BacktestMetrics, compute_metrics
from engine.matched_benchmark import annotate_trades, summarize_matches
from strategies.base import Strategy

DEFAULT_CASH = 10_000.0
DEFAULT_RISK_PCT = 0.01  # fraction of equity risked per trade
MIN_BARS_TO_TRADE = 30


def _make_adapter(strategy: Strategy, risk_pct: float, spread: float) -> type[BTStrategy]:
    class Adapter(BTStrategy):
        def init(self):
            pass

        def next(self):
            bars = self.data.df
            if len(bars) < MIN_BARS_TO_TRADE:
                return

            if self.position:
                if strategy.exit_signal(bars):
                    self.position.close()
                return

            if not strategy.entry_signal(bars):
                return

            entry_price = float(bars["Close"].iloc[-1])
            direction = strategy.entry_direction(bars)
            stop = strategy.stop_price(bars, entry_price)
            target = strategy.target_price(bars, entry_price)

            risk_per_share = abs(entry_price - stop)
            if risk_per_share <= 0:
                return

            # The broker fills at entry_price adjusted for spread, not the raw
            # close -- validate the bracket against that same adjusted price so
            # a too-thin edge is skipped here instead of raising inside the
            # broker (e.g. VWAP Bounce's mean-reversion target sitting inside
            # the spread of the touch bar's close).
            adjusted = entry_price * (1 + spread) if direction == "long" else entry_price * (1 - spread)
            if direction == "long":
                if stop >= adjusted or (target is not None and target <= adjusted):
                    return
            else:
                if stop <= adjusted or (target is not None and target >= adjusted):
                    return

            # Cash account (margin=1.0, no leverage): a tight stop can imply a
            # risk-sized share count whose notional exceeds account equity,
            # which the broker would silently cancel outright (no fill, no
            # warning -- see backtesting.py's Broker._process_orders). Cap by
            # buying power so the order is never larger than equity can cover.
            size_by_risk = int((self.equity * risk_pct) // risk_per_share)
            size_by_equity = int(self.equity // adjusted)
            size = min(size_by_risk, size_by_equity)
            if size < 1:
                return

            if direction == "long":
                self.buy(size=size, sl=stop, tp=target, tag=risk_per_share)
            else:
                self.sell(size=size, sl=stop, tp=target, tag=risk_per_share)

    return Adapter


@dataclass
class SymbolBacktestResult:
    symbol: str
    stats: pd.Series | None
    trades: pd.DataFrame
    equity_curve: pd.DataFrame | None
    # MFE/MAE and exit-quality diagnostics -- see engine/excursion.py. Only
    # populated by run_symbol_backtest (the standard backtesting.py-backed
    # engine, which has EntryBar/ExitBar positional indices to work with);
    # left as None by other engines producing this same result shape (e.g.
    # engine/overnight.py's close->open engine has no intrabar path to walk).
    excursions: pd.DataFrame | None = None


@dataclass
class StrategyBacktestResult:
    strategy_name: str
    symbols: list[str]
    start: date
    end: date
    per_symbol: dict[str, SymbolBacktestResult]
    metrics: BacktestMetrics
    excursions: pd.DataFrame = field(default_factory=pd.DataFrame)
    # logging_db row id, set by engine/runner.py after logging, so a validation
    # report can be attached to THIS run rather than matched by name+timestamp.
    run_id: int | None = None
    # Exact-interval benchmark evidence. Kept separately from the raw full-
    # window buy-and-hold gap so sparse strategies cannot confuse the two.
    matched_benchmark: dict = field(default_factory=dict)
    research_metadata: dict = field(default_factory=dict)


def run_symbol_backtest(
    strategy: Strategy,
    symbol: str,
    interval: str,
    start: date,
    end: date,
    cash: float = DEFAULT_CASH,
    risk_pct: float = DEFAULT_RISK_PCT,
    spread: float | None = None,
    risk_free_rate: float = 0.0,
) -> SymbolBacktestResult:
    validate_timing_contract(
        timing_contract_for(strategy), actual_execution=ExecutionTiming.NEXT_OPEN
    )
    bars = data_module.get_bars(symbol, interval, start, end)
    if bars.empty or len(bars) < MIN_BARS_TO_TRADE:
        return SymbolBacktestResult(symbol, None, pd.DataFrame(), None)

    # A flat spread across every symbol either overstates cost for liquid
    # names or understates it for thin ones -- estimate per symbol from real
    # dollar volume unless a caller explicitly pins one (e.g. a sensitivity
    # sweep). See engine/data.py:estimate_spread and LESSONS.md.
    resolved_spread = spread if spread is not None else spread_for(symbol, start, end)

    adapter_cls = _make_adapter(strategy, risk_pct, resolved_spread)
    bt = Backtest(bars, adapter_cls, cash=cash, spread=resolved_spread, margin=1.0)
    stats = bt.run()

    # backtesting.py 0.6.5 hardcodes risk_free_rate=0.0 inside Backtest.run()
    # and doesn't expose it as a parameter -- every Sharpe/Sortino/Alpha it
    # produces otherwise silently assumes cash earns nothing. Recompute with
    # the real rate using the same trades/equity/data it just derived.
    if risk_free_rate:
        stats = _compute_stats(
            trades=stats["_trades"],
            # Idle cash earns rf before any stat is derived, so every downstream
            # figure (Sharpe, Sortino, CAGR, drawdown) describes the account a
            # real trader would have held rather than one whose uninvested
            # balance sat in a mattress while still being charged rf.
            equity=accrue_idle_cash(
                stats["_equity_curve"]["Equity"], stats["_trades"], bars["Close"], risk_free_rate
            ).to_numpy(),
            ohlc_data=bars,
            strategy_instance=None,
            risk_free_rate=risk_free_rate,
        )

    # Alpha on the same excess-over-cash basis as the return column, replacing
    # backtesting.py's Jensen CAPM alpha (`Return - rf - beta*(B&H - rf)`).
    #
    # Netting BOTH sides of rf, the rate cancels exactly:
    #     (R_strategy - rf) - (R_benchmark - rf)  ==  R_strategy - R_benchmark
    # which is worth stating because it removes a whole error class rather than
    # merely avoiding it: there is no way for the benchmark's risk-free series,
    # window, or compounding convention to disagree with the strategy's, since
    # neither appears in the result.
    #
    # Jensen alpha is wrong here for a specific, measured reason. It scales the
    # benchmark leg by beta, and a mostly-cash strategy has beta near zero, so
    # alpha collapses to the strategy's own return -- which, once idle cash
    # earns rf, is mostly accrued interest. Measured: crediting cash moved
    # alpha by +18.8 to +19.5pp on four strategies against +19.6pp of
    # cumulative interest, a ~1:1 match, flipping all four from "hold" to
    # "shortlist" without a single trade changing. backtesting.py's own source
    # comment flags the same instability from the other direction.
    #
    # It is also incoherent for exactly the strategies in question: a
    # never-trading strategy has no tracking error and scores Jensen alpha 0 --
    # reading as "neutral" when the truth is that it gave up the benchmark's
    # entire excess return to sit in T-bills. On this basis it scores
    # -(benchmark excess), which is the honest number.
    if "Return [%]" in stats.index and "Buy & Hold Return [%]" in stats.index:
        stats["Alpha [%]"] = stats["Return [%]"] - stats["Buy & Hold Return [%]"]

    trades = stats["_trades"].copy()
    # backtesting.py applies `spread` in fill prices but does not expose a
    # modeled-cost column. Record a conservative two-way notional estimate so
    # turnover-heavy hypotheses cannot hide their cost burden.
    trades["ModeledCost"] = (
        trades["Size"].abs()
        * (trades["EntryPrice"].abs() + trades["ExitPrice"].abs())
        * resolved_spread
    )
    excursions = compute_trade_excursions(bars, trades) if not trades.empty else None
    return SymbolBacktestResult(symbol, stats, trades, stats["_equity_curve"], excursions)


def accrue_idle_cash(
    equity: pd.Series, trades: pd.DataFrame, closes: pd.Series, risk_free_rate: float
) -> pd.Series:
    """Credit the uninvested portion of the account with the risk-free rate.

    Without this the simulation assumes cash earns NOTHING while the Sharpe
    numerator still subtracts rf, charging a strategy for a drag no real
    account holding the same cash would experience. The distortion scales
    inversely with exposure, so it hit the most selective strategies hardest:
    Earnings Momentum (1.6% exposure, +0.10% CAGR, profit factor 2.65) scored
    -8.09 and Anchored VWAP -12.13, both of which are ~ -rf divided by a
    near-zero volatility rather than any statement about trade quality. The
    per-symbol maximum across the whole project was -0.16 against a 0.5
    shortlist threshold, so the tier was unreachable by construction.

    Invested notional is reconstructed per bar from each trade's
    EntryBar/ExitBar span and Size -- backtesting.py's equity curve exposes
    only Equity, not position value, but the trade ledger carries enough to
    rebuild it exactly. Cash is the remainder, so a strategy risking a small
    fraction of equity still earns interest on the rest, rather than being
    treated as fully invested whenever it holds any position at all.

    Interest is applied as an addition to each day's RETURN and then
    recompounded, so earned interest itself earns interest, matching a real
    settled cash balance.
    """
    if not risk_free_rate or equity.empty:
        return equity

    invested = np.zeros(len(equity), dtype=float)
    if not trades.empty:
        close_values = closes.to_numpy(dtype=float)
        for entry_bar, exit_bar, size in zip(
            trades["EntryBar"].to_numpy(dtype=int),
            trades["ExitBar"].fillna(len(equity) - 1).to_numpy(dtype=int),
            trades["Size"].to_numpy(dtype=float),
        ):
            lo = max(0, entry_bar)
            hi = min(len(equity) - 1, exit_bar)
            if hi >= lo:
                invested[lo : hi + 1] += abs(size) * close_values[lo : hi + 1]

    values = equity.to_numpy(dtype=float)
    # Clipped at 0: a leveraged bar has no idle cash to pay interest on, and a
    # negative "cash" balance must not become a negative interest charge here
    # -- margin interest is a separate cost this project does not model.
    cash_fraction = np.clip(1.0 - np.divide(invested, values, out=np.zeros_like(values), where=values > 0), 0.0, 1.0)

    # Per-BAR rate, derived from the series' own sampling frequency. Assuming a
    # daily bar here was wrong by the ratio of bar frequencies: day-trading
    # strategies run on 5-minute bars (~19,841 per year, not 252), so a daily
    # rate applied once per bar over-credits interest ~79x. Measured on ORB:
    # 3,042 bars at ~40% idle compounded to a spurious 1.21x in under two
    # months, producing Sharpe 7.24 and alpha +36.7% -- numbers implausible
    # enough to catch by eye, which is the only reason this was caught.
    #
    # Same failure class as annualizing calendar-day returns with a 252-day
    # constant (see engine/portfolio.py:annualized_stats): a per-period rate
    # paired with the wrong period count. Deriving periods-per-year from the
    # index makes total accrued interest equal rf compounded over the elapsed
    # window regardless of bar size, which also correctly spreads overnight
    # interest across intraday bars rather than dropping it.
    elapsed_years = (equity.index[-1] - equity.index[0]).days / 365.25
    periods_per_year = (
        len(equity) / elapsed_years if elapsed_years > 0 else TRADING_DAYS_PER_YEAR
    )
    daily_rf = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    base_returns = pd.Series(values, index=equity.index).pct_change().fillna(0.0).to_numpy()
    # Yesterday's cash earns today's interest -- shifting avoids crediting a
    # position opened today with a full day of interest it never held.
    accrued = base_returns + np.concatenate(([0.0], cash_fraction[:-1] * daily_rf))
    return pd.Series(values[0] * np.cumprod(1.0 + accrued), index=equity.index)


def run_strategy_backtest(
    strategy_name: str,
    strategy: Strategy,
    symbols: list[str],
    interval: str,
    start: date,
    end: date,
    risk_free_rate: float = 0.0,
    **kwargs,
) -> StrategyBacktestResult:
    """Run one strategy instance across every symbol and pool the results."""
    return run_strategy_backtest_seeded(
        strategy_name, lambda _symbol: strategy, symbols, interval, start, end,
        risk_free_rate=risk_free_rate, **kwargs,
    )


def run_strategy_backtest_seeded(
    strategy_name: str,
    strategy_for: "callable[[str], Strategy]",
    symbols: list[str],
    interval: str,
    start: date,
    end: date,
    risk_free_rate: float = 0.0,
    **kwargs,
) -> StrategyBacktestResult:
    """Like run_strategy_backtest, but builds a fresh strategy per symbol via
    `strategy_for(symbol)`. Needed when a strategy depends on symbol-specific
    external data the OHLCV bars don't carry -- e.g. PEAD seeding each name
    with its own real earnings dates (the per-symbol engine otherwise passes
    one shared instance with no symbol identity)."""
    per_symbol: dict[str, SymbolBacktestResult] = {
        symbol: run_symbol_backtest(
            strategy_for(symbol), symbol, interval, start, end,
            risk_free_rate=risk_free_rate, **kwargs,
        )
        for symbol in symbols
    }
    try:
        benchmark_bars = data_module.get_bars("SPY", interval, start, end)
        benchmark_error = None
    except Exception as exc:  # missing benchmark withholds evidence, not the strategy result
        benchmark_bars = pd.DataFrame()
        benchmark_error = f"{type(exc).__name__}: {exc}"
    for result in per_symbol.values():
        result.trades = annotate_trades(result.trades, benchmark_bars)
    aggregated = aggregate_symbol_results(
        strategy_name, symbols, per_symbol, start, end, risk_free_rate
    )
    if benchmark_error:
        aggregated.matched_benchmark["error"] = benchmark_error
    return aggregated


def portfolio_equity_curve(
    per_symbol: dict[str, "SymbolBacktestResult"],
) -> pd.Series | None:
    """One equal-weight equity curve across every symbol that produced data.

    Replaces averaging per-symbol Sharpes. A MEAN OF RATIOS is not the ratio of
    means, and the two can disagree in SIGN: measured on Turnaround Tuesday, the
    mean per-symbol Sharpe was +2.26 while the mean per-symbol excess CAGR was
    -0.11%, which is arithmetically impossible from any single curve. Gap Fade
    showed the same contradiction (+0.69 against -0.20%).

    It is also silently sensitive to HOW MANY symbols traded -- the same
    denominator problem as the exposure/Sharpe mismatch, in a different costume:
    a strategy trading 3 of 29 names had its Sharpe decided by those 3, while a
    real account holding all 29 sleeves would have experienced the diversified
    curve.

    Summing the per-symbol curves models exactly that account: each symbol runs
    its own equal-sized sleeve, so the sum is an equal-weight portfolio of them.
    That is also the definition engine/cross_sectional.py has always used, so
    both engines finally compute Sharpe the same way.

    Curves are outer-joined and forward-filled: symbols have different bar
    counts (an intraday name may list mid-window), and a missing sleeve must
    hold its last value rather than drop the whole date, which would silently
    shorten the portfolio's window to the shortest symbol's.
    """
    curves = [
        r.equity_curve["Equity"]
        for r in per_symbol.values()
        if r.equity_curve is not None and len(r.equity_curve)
    ]
    if not curves:
        return None
    frame = pd.concat(curves, axis=1).sort_index().ffill()
    # A sleeve that has not started yet holds its opening cash rather than NaN,
    # so early dates reflect uninvested capital instead of a smaller portfolio.
    frame = frame.fillna(DEFAULT_CASH)
    return frame.sum(axis=1)


def aggregate_symbol_results(
    strategy_name: str,
    symbols: list[str],
    per_symbol: dict[str, SymbolBacktestResult],
    start: date,
    end: date,
    risk_free_rate: float = 0.0,
) -> StrategyBacktestResult:
    """Pool per-symbol results into one StrategyBacktestResult: concat all
    trades for the R-multiple metrics, average the per-symbol risk stats.
    Shared by the per-symbol engine and any engine that produces the same
    SymbolBacktestResult shape (e.g. engine/overnight.py)."""
    all_trades = []
    all_excursions = []
    drawdowns, sharpes, sortinos, alphas, betas, cagrs, exposures, buy_holds = (
        [], [], [], [], [], [], [], []
    )
    for symbol, result in per_symbol.items():
        if not result.trades.empty:
            tagged = result.trades.copy()
            tagged["Symbol"] = symbol
            all_trades.append(tagged)
        if result.excursions is not None and not result.excursions.empty:
            tagged = result.excursions.copy()
            tagged["Symbol"] = symbol
            all_excursions.append(tagged)
    # SAME symbol set for every column, decided once -- not each column
    # dropping NaN independently. Previously a symbol that never traded
    # contributed Exposure Time = 0.0 (kept, since notna(0.0) is True) but
    # Sharpe = NaN (dropped), so a strategy trading 9 of 29 names averaged
    # exposure over 29 and Sharpe over 9: different denominators in the same
    # row, which understated exposure for exactly the selective strategies
    # whose Sharpe was already most distorted. Same-denominator BY
    # CONSTRUCTION, so adding a column later can't silently reintroduce this.
    _AGGREGATED = (
        (drawdowns, "Max. Drawdown [%]"),
        (sharpes, "Sharpe Ratio"),
        (sortinos, "Sortino Ratio"),
        (alphas, "Alpha [%]"),
        (betas, "Beta"),
        (cagrs, "CAGR [%]"),
        (exposures, "Exposure Time [%]"),
        (buy_holds, "Buy & Hold Return [%]"),
    )
    contributing = [
        result
        for result in per_symbol.values()
        if result.stats is not None
        and all(pd.notna(result.stats.get(key)) for _bucket, key in _AGGREGATED)
    ]
    for result in contributing:
        # Every one of these is a *mean of independent per-symbol runs*, not a
        # portfolio metric -- it ignores cross-symbol correlation, so it
        # understates true portfolio drawdown/risk. See LESSONS.md.
        for bucket, key in _AGGREGATED:
            value = result.stats.get(key)
            bucket.append(abs(value) if key == "Max. Drawdown [%]" else value)

    def _mean(values: list[float]) -> float | None:
        return (sum(values) / len(values)) if values else None

    pooled_trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    pooled_excursions = pd.concat(all_excursions, ignore_index=True) if all_excursions else pd.DataFrame()
    # The window actually covered by data, across every symbol that produced
    # any. Diverges sharply from the requested window for intraday strategies,
    # where the free tier serves ~50 days of 5-minute bars regardless of what
    # was asked for -- see BacktestMetrics.measured_start.
    covered = [
        r.equity_curve.index for r in per_symbol.values()
        if r.equity_curve is not None and len(r.equity_curve)
    ]
    measured_start = min(idx[0] for idx in covered).date() if covered else None
    measured_end = max(idx[-1] for idx in covered).date() if covered else None

    # Risk-adjusted figures come from the POOLED portfolio curve, not from
    # averaging per-symbol ratios. Everything else (exposure, alpha, buy&hold)
    # stays a per-symbol mean, which is meaningful for those quantities.
    pooled = portfolio_equity_curve(per_symbol)
    pooled_cagr = pooled_sharpe = pooled_sortino = None
    pooled_total_return = pooled_drawdown = None
    if pooled is not None and len(pooled) > 2:
        from engine.portfolio import annualized_stats

        pooled_cagr, pooled_sharpe, pooled_sortino = annualized_stats(
            pooled, risk_free_rate, cash_accrued=True
        )
        pooled_total_return = float((pooled.iloc[-1] / pooled.iloc[0] - 1.0) * 100.0)
        pooled_drawdown = float(abs((pooled / pooled.cummax() - 1.0).min() * 100.0))

    capital_base = float(pooled.iloc[0]) if pooled is not None and len(pooled) else DEFAULT_CASH * max(1, len(symbols))
    turnover_pct = None
    modeled_costs = None
    if not pooled_trades.empty:
        turnover = (
            pooled_trades["Size"].abs()
            * (pooled_trades["EntryPrice"].abs() + pooled_trades["ExitPrice"].abs())
        ).sum()
        turnover_pct = float(turnover / capital_base * 100.0)
        if "ModeledCost" in pooled_trades:
            modeled_costs = float(pd.to_numeric(pooled_trades["ModeledCost"], errors="coerce").sum())

    # Net exposure keeps long and short sides visible instead of allowing a
    # market-neutral book to look fully uninvested. Gross remains the average
    # sleeve exposure; time-in-market is the fraction of represented bars on
    # which at least one sleeve is active.
    net_sleeve_exposures: list[float] = []
    for result in per_symbol.values():
        if result.stats is None or pd.isna(result.stats.get("Exposure Time [%]")):
            continue
        exposure = float(result.stats.get("Exposure Time [%]"))
        if result.trades.empty:
            net_sleeve_exposures.append(0.0)
            continue
        notionals = result.trades["Size"].abs() * result.trades["EntryPrice"].abs()
        denom = float(notionals.sum())
        direction = float((notionals * np.sign(result.trades["Size"])).sum() / denom) if denom else 0.0
        net_sleeve_exposures.append(exposure * direction)

    time_in_market_pct = None
    if pooled is not None and len(pooled):
        represented = pd.DatetimeIndex(pooled.index).sort_values()
        active = np.zeros(len(represented), dtype=bool)
        for _, trade in pooled_trades.iterrows():
            active |= (represented >= pd.Timestamp(trade["EntryTime"])) & (represented <= pd.Timestamp(trade["ExitTime"]))
        time_in_market_pct = float(active.mean() * 100.0)

    matched = summarize_matches(
        pooled_trades,
        capital_base=capital_base,
        measured_start=measured_start,
        measured_end=measured_end,
    )

    metrics = compute_metrics(
        strategy_name=strategy_name,
        symbol="ALL",
        trades=pooled_trades,
        start=start,
        end=end,
        max_drawdown_pct=pooled_drawdown if pooled_drawdown is not None else (max(drawdowns) if drawdowns else None),
        sharpe=pooled_sharpe,
        sortino=pooled_sortino,
        alpha_pct=_mean(alphas),
        beta=_mean(betas),
        cagr_pct=pooled_cagr if pooled_cagr is not None else _mean(cagrs),
        exposure_pct=_mean(exposures),
        risk_free_rate=risk_free_rate,
        buy_hold_return_pct=_mean(buy_holds),
        measured_start=measured_start,
        measured_end=measured_end,
    )
    metrics.total_return_pct = pooled_total_return
    metrics.average_gross_exposure_pct = _mean(exposures)
    metrics.average_net_exposure_pct = _mean(net_sleeve_exposures)
    metrics.time_in_market_pct = time_in_market_pct
    metrics.turnover_pct = turnover_pct
    metrics.modeled_costs = modeled_costs
    metrics.matched_spy_return_pct = matched.matched_return_pct
    metrics.matched_spy_excess_pct = matched.matched_excess_pct
    metrics.annualized_matched_excess_pct = matched.annualized_excess_pct
    metrics.matched_alpha_annual_pct = matched.alpha_annual_pct
    metrics.matched_beta = matched.beta
    metrics.matched_benchmark_trades = matched.matched_trades
    metrics.missing_benchmark_trades = matched.missing_trades
    output = StrategyBacktestResult(
        strategy_name, symbols, start, end, per_symbol, metrics, pooled_excursions
    )
    output.matched_benchmark = matched.to_dict()
    return output
