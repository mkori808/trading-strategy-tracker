"""Backtest loop for CrossSectionalStrategy -- rebalance-driven, not the
bar-by-bar entry/exit loop engine/backtest.py runs for single-symbol
Strategy instances. See strategies/cross_sectional.py and LESSONS.md for
why this is a separate engine rather than a variant of the existing one.

Rebalances on a fixed monthly schedule by default (first trading day seen
each calendar month across the universe; `rebalance_frequency="weekly"`
switches to the first trading day of each ISO week -- added for
strategies/swing/ensemble_voting.py, which wants weekly rebalancing; every
existing caller keeps the monthly default so its numbers don't shift), holds
target weights between rebalances, and marks equity to market daily using
each position's close. Positions can be fractional shares -- there's no
discrete stop/target bracket order to model here the way engine/backtest.py's
adapter does, so there's no realism cost to fractional sizing (and real
brokers, including Alpaca, support fractional shares).

Every rebalance decision uses bars strictly BEFORE the execution date and
fills at that date's open.  This one-bar information boundary is deliberate:
ranking on a day's close and also buying that close is not an executable
historical rule, even when the dataframe slice itself contains no future row.

Slippage/commission (`slippage_bps`/`commission_bps`) default to 0.0 --
byte-identical to this module's original behavior for every existing caller
(Dual Momentum). A caller that wants realistic costs (e.g. the ensemble
engine) passes them explicitly; they're charged only on the traded delta at
each rebalance, not on the whole position, since only the delta actually
transacts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable, Literal

import pandas as pd

from engine import data as data_module
from engine.metrics import TRADING_DAYS_PER_YEAR
from engine.portfolio import annualized_stats
from strategies.cross_sectional import CrossSectionalStrategy

class InsufficientHistory(RuntimeError):
    """A symbol cannot supply the warmup its strategy needs before `start`.

    Its own error type rather than ValueError so the API layer can map it to
    a 400 with the message intact -- the user needs to know WHICH symbol and
    by how much, since the fix is a judgement call (move the window, or drop
    the name on purpose) rather than something the engine should pick."""


DEFAULT_CASH = 10_000.0

RebalanceFrequency = Literal["monthly", "weekly", "daily", "semimonthly", "quarterly", "every_20_sessions"]


@dataclass
class CrossSectionalResult:
    strategy_name: str
    symbols: list[str]
    start: date
    end: date
    equity_curve: pd.Series
    rebalances: pd.DataFrame  # one row per rebalance date: {date, holdings}
    final_equity: float
    return_pct: float
    cagr_pct: float | None
    max_drawdown_pct: float
    sharpe: float | None
    sortino: float | None
    risk_free_rate: float
    total_costs: float = 0.0  # sum of slippage + commission paid across all rebalances
    total_traded_notional: float = 0.0  # absolute dollar delta actually charged a cost
    commission_bps: float = 0.0
    universe_key: str | None = None
    pit_membership_applied: bool = False
    incomplete_warmup: dict[str, int] = field(default_factory=dict)
    pit_diagnostics: dict = field(default_factory=dict)
    pit_analysis: dict = field(default_factory=dict)
    security_labels: dict[str, str] = field(default_factory=dict)
    validation_bars: dict[str, pd.DataFrame] | None = field(default=None, repr=False)
    membership_at_runtime: Callable[[date], set[str]] | None = field(default=None, repr=False)
    # logging_db row id, set by engine/runner.py after the run is logged, so
    # engine/validation.py's report can be attached to THIS run rather than
    # guessed at by strategy name + timestamp.
    run_id: int | None = None


def _rebalance_dates(
    calendar: pd.DatetimeIndex, frequency: RebalanceFrequency = "monthly"
) -> set[pd.Timestamp]:
    """First trading day present in the calendar for each period -- each
    (year, month) for 'monthly', each (year, ISO week) for 'weekly', every
    single trading day for 'daily' (added to test whether a strategy's
    drawdowns come from a rebalance cadence too slow to react -- see
    engine/compare_dual_momentum_robustness.py -- without having to
    special-case the main loop, which already just checks membership in
    this set), each (year, month, half) for 'semimonthly' (calendar day
    <=15 vs. >15 -- so "twice a month" means the 1st-half/2nd-half split,
    not a rolling 14-day cadence), and each (year, calendar quarter) for
    'quarterly'."""
    if frequency == "daily":
        return set(calendar)
    if frequency == "every_20_sessions":
        return set(calendar[::20])
    s = pd.Series(calendar, index=calendar)
    if frequency == "weekly":
        iso = calendar.isocalendar()
        return set(s.groupby([iso.year, iso.week]).first())
    if frequency == "semimonthly":
        half = pd.Series(calendar.day, index=calendar).le(15).map({True: 1, False: 2})
        return set(s.groupby([calendar.year, calendar.month, half]).first())
    if frequency == "quarterly":
        quarter = (calendar.month - 1) // 3
        return set(s.groupby([calendar.year, quarter]).first())
    return set(s.groupby([calendar.year, calendar.month]).first())


def run_cross_sectional_backtest(
    strategy_name: str,
    strategy: CrossSectionalStrategy,
    symbols: list[str],
    start: date,
    end: date,
    cash: float = DEFAULT_CASH,
    risk_free_rate: float = 0.0,
    rebalance_frequency: RebalanceFrequency = "monthly",
    slippage_bps: float = 0.0,
    commission_bps: float = 0.0,
    spread_by_symbol: dict[str, float] | None = None,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
    membership_at: Callable[[date], set[str]] | None = None,
    universe_key: str | None = None,
    allow_incomplete_warmup: bool = False,
) -> CrossSectionalResult:
    """`spread_by_symbol` maps symbol -> spread as a DECIMAL (0.0002 = 2bps),
    the shape engine/data.py:estimate_spread returns. When supplied it
    overrides the flat `slippage_bps` per symbol, matching what the
    per-symbol engine already does in engine/backtest.py -- a leaderboard
    where one engine estimates cost per name and another applies a single
    flat rate (or none) is not comparing like with like. A symbol missing
    from the map falls back to `slippage_bps`.

    History is fetched from BEFORE `start` when the strategy declares a
    lookback (see CrossSectionalStrategy.required_history_days), so the first
    rebalance can rank on day one instead of the portfolio sitting in cash
    until the traded window itself supplies enough bars. Only [start, end] is
    traded or reported -- the warmup prefix is ranking input and nothing else.

    Raises InsufficientHistory if any symbol cannot supply that warmup.
    Deliberately loud: the previous behaviour was a `continue` inside the
    strategy, which rendered "this symbol did not exist yet" identical to an
    ordinary all-cash day. It also catches a window extended backwards past
    what the data provider has -- e.g. a 2018 start over the July-2021 Dow
    roster, where DOW has no bars before 2019-03-20 because it was spun out
    of DowDuPont in 2019 and did not exist earlier."""
    warmup_days = strategy.required_history_days()
    # 252 trading days per 365.25 calendar -> 1.45, plus a week of slack.
    # Correct on average and wrong on any window with unusual holiday density
    # or an early data gap -- which is exactly why the resulting bar count is
    # ASSERTED below rather than trusted.
    fetch_start = start - timedelta(days=int(warmup_days * 1.45) + 7) if warmup_days else start

    # Validation replays many nearby parameter arms over the identical data.
    # Let it preload the widest required window once; normal callers keep the
    # original fetch behavior. Copies prevent this loop from mutating a shared
    # validation cache through later slicing/alignment operations.
    raw_bars = (
        {s: bars_by_symbol.get(s, pd.DataFrame()).copy() for s in symbols}
        if bars_by_symbol is not None
        else {s: data_module.get_bars(s, "1d", fetch_start, end) for s in symbols}
    )
    # A preloaded validation cache may extend beyond this arm's requested end.
    # Never let those later rows expand the trading calendar or membership
    # schedule; warmup is allowed only before start, never after end.
    raw_bars = {
        symbol: bars.loc[bars.index.date <= end]
        for symbol, bars in raw_bars.items()
        if not bars.empty
    }
    raw_bars = {s: b for s, b in raw_bars.items() if not b.empty}
    if not raw_bars:
        empty_curve = pd.Series([cash], index=[pd.Timestamp(start)])
        return CrossSectionalResult(
            strategy_name, symbols, start, end, empty_curve, pd.DataFrame(),
            cash, 0.0, None, 0.0, None, None, risk_free_rate, 0.0,
        )

    # Bars carry a tz-aware index (America/New_York); a daily bar for trading
    # day D is stamped D 20:00 there. `get_bars(start, ...)` therefore returns
    # `start 20:00` as its first stamp, so a `>= start 00:00` boundary selects
    # exactly the bars the un-warmed fetch used to return -- the traded window
    # is unchanged, only the ranking input grew. Timezone is taken FROM the
    # data rather than assumed, so synthetic tz-naive frames still work.
    sample_index = next(iter(raw_bars.values())).index
    window_open = pd.Timestamp(start)
    if getattr(sample_index, "tz", None) is not None:
        window_open = window_open.tz_localize(sample_index.tz)

    if warmup_days:
        starting_members = membership_at(start) if membership_at else set(raw_bars)
        short = {
            symbol: int((bars.index < window_open).sum())
            for symbol, bars in raw_bars.items()
            if symbol in starting_members
            if (bars.index < window_open).sum() < warmup_days
        }
        if short and not allow_incomplete_warmup:
            detail = ", ".join(f"{s} has {n}" for s, n in sorted(short.items()))
            raise InsufficientHistory(
                f"{strategy_name}: cannot start at {start} -- {len(short)} symbol(s) "
                f"lack the {warmup_days} trading days of warmup this strategy needs "
                f"before its first ranking ({detail}). Start the window later, or "
                "drop the symbol(s) deliberately. Proceeding would rank a partial "
                "universe while reporting metrics as though the full one was there."
            )

    # Warmup feeds the ranking only -- never traded, never in the equity curve.
    calendar = pd.DatetimeIndex(sorted(set().union(*(b.index for b in raw_bars.values()))))
    calendar = calendar[calendar >= window_open]
    if len(calendar) == 0:
        empty_curve = pd.Series([cash], index=[window_open])
        return CrossSectionalResult(
            strategy_name, symbols, start, end, empty_curve, pd.DataFrame(),
            cash, 0.0, None, 0.0, None, None, risk_free_rate, 0.0,
        )
    rebalance_dates = _rebalance_dates(calendar, rebalance_frequency)
    close_df = pd.DataFrame({s: b["Close"] for s, b in raw_bars.items()}).sort_index().ffill()
    open_df = pd.DataFrame({s: b["Open"] for s, b in raw_bars.items()}).sort_index()
    flat_slippage = slippage_bps / 10_000.0
    commission = commission_bps / 10_000.0

    def cost_rate(symbol: str) -> float:
        slip = flat_slippage if spread_by_symbol is None else spread_by_symbol.get(symbol, flat_slippage)
        return slip + commission

    shares: dict[str, float] = {}
    cash_balance = cash
    total_costs = 0.0
    total_traded_notional = 0.0
    equity_points: list[tuple[pd.Timestamp, float]] = []
    rebalance_log: list[dict] = []

    def _positions_value(day: pd.Timestamp) -> float:
        total = 0.0
        for symbol, qty in shares.items():
            px = close_df.loc[day, symbol]
            if pd.notna(px):
                total += qty * px
        return total

    # Uninvested cash earns the risk-free rate, applied here rather than
    # reconstructed downstream because this loop tracks the exact cash balance.
    # Without it the engine assumed idle cash earned nothing while the Sharpe
    # numerator still subtracted rf -- a double penalty that scaled inversely
    # with exposure and made the shortlist tier unreachable for any selective
    # strategy. See engine/backtest.py:accrue_idle_cash for the per-symbol
    # counterpart and tests/test_engine/test_metric_calibration.py.
    daily_rf = (1.0 + risk_free_rate) ** (1.0 / TRADING_DAYS_PER_YEAR) - 1.0 if risk_free_rate else 0.0

    for position, day in enumerate(calendar):
        if position and daily_rf:
            cash_balance *= 1.0 + daily_rf
        if day in rebalance_dates:
            # The target is computed from information available before this
            # session and executed at this session's open.  Never let today's
            # close leak into a fill that claims to occur today.
            eligible = membership_at(day.date()) if membership_at else set(raw_bars)
            history = {
                s: b.loc[b.index < day]
                for s, b in raw_bars.items()
                if s in eligible
            }
            target_weights = strategy.rebalance(history, as_of=day)
            rebalance_log.append({"date": day, "holdings": dict(target_weights)})

            portfolio_value = cash_balance
            for symbol, qty in shares.items():
                px = open_df.loc[day, symbol] if symbol in open_df.columns else float("nan")
                if pd.notna(px):
                    portfolio_value += qty * px

            # Liquidate anything no longer in the target set.
            for symbol in list(shares):
                if symbol not in target_weights:
                    px = open_df.loc[day, symbol] if symbol in open_df.columns else float("nan")
                    qty = shares.pop(symbol)
                    if pd.notna(px):
                        proceeds = qty * px
                        cost = abs(proceeds) * cost_rate(symbol)
                        cash_balance += proceeds - cost
                        total_costs += cost
                        total_traded_notional += abs(proceeds)

            # (Re)establish target positions at this rebalance's weights.
            for symbol, weight in target_weights.items():
                if symbol not in close_df.columns:
                    continue
                px = open_df.loc[day, symbol]
                if pd.isna(px) or px <= 0:
                    continue
                target_value = portfolio_value * weight
                current_value = shares.get(symbol, 0.0) * px
                delta_shares = (target_value - current_value) / px
                # Slippage/commission apply to the traded delta only -- an
                # unchanged holding from the prior rebalance doesn't re-pay
                # a cost it already paid to get established.
                traded_notional = abs(delta_shares * px)
                cost = traded_notional * cost_rate(symbol)
                shares[symbol] = shares.get(symbol, 0.0) + delta_shares
                cash_balance -= delta_shares * px + cost
                total_costs += cost
                total_traded_notional += traded_notional

        equity_points.append((day, cash_balance + _positions_value(day)))

    equity_curve = pd.Series(
        [v for _, v in equity_points], index=pd.DatetimeIndex([d for d, _ in equity_points])
    )
    final_equity = float(equity_curve.iloc[-1])
    return_pct = (final_equity / cash - 1) * 100
    running_max = equity_curve.cummax()
    max_dd = float(((equity_curve - running_max) / running_max).min() * 100)
    cagr, sharpe, sortino = annualized_stats(
        equity_curve, risk_free_rate, cash_accrued=True
    )

    output = CrossSectionalResult(
        strategy_name=strategy_name,
        symbols=symbols,
        start=start,
        end=end,
        equity_curve=equity_curve,
        rebalances=pd.DataFrame(rebalance_log),
        final_equity=final_equity,
        return_pct=return_pct,
        cagr_pct=cagr,
        max_drawdown_pct=abs(max_dd),
        sharpe=sharpe,
        sortino=sortino,
        risk_free_rate=risk_free_rate,
        total_costs=total_costs,
        total_traded_notional=total_traded_notional,
        commission_bps=commission_bps,
        universe_key=universe_key,
        pit_membership_applied=membership_at is not None,
        incomplete_warmup=short if warmup_days else {},
    )
    output.validation_bars = raw_bars
    output.membership_at_runtime = membership_at
    return output
