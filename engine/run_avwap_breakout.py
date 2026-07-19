"""Anchored VWAP Breakout: anchor-type resolution, per-symbol strategy
construction, the standard backtest run (through engine/filters.py's
regime + Trend Template gate -- see strategies/swing/avwap_breakout.py's
module docstring for why this strategy bakes that gate in), and the
AVWAP-specific diagnostic report engine/backtest.py's standard metrics
don't cover: anchor counts, exit-type breakdown, mean hold duration, and
mean entry-to-AVWAP distance.

Run with: python -m engine.run_avwap_breakout
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import pandas as pd

from engine import avwap
from engine import data as data_module
from engine.backtest import StrategyBacktestResult, run_strategy_backtest_seeded
from engine.excursion import LOGS_DIR
from engine.filters import FilterDiagnostics, build_filter_factory
from engine.metrics import MIN_RELIABLE_TRADES, BacktestMetrics
from engine.universe import EQUITY_UNIVERSE, daily_date_range
from strategies.swing.avwap_breakout import AvwapBreakout

STRATEGY_LABEL = "Anchored VWAP Breakout"

# Data-availability check, locked 2026-07-19 (see LESSONS.md): 0/29
# EQUITY_UNIVERSE symbols were missing earnings date coverage (yfinance
# get_earnings_dates via engine.data.earnings_dates), mean ~20 events/symbol
# over the 5-year daily window -- well under the 20%-missing threshold that
# would trigger the swing-low fallback. Locked as a constant rather than
# re-run on every backtest per the task's own "pre-registered, don't modify
# after seeing results" instruction -- data availability, unlike anchor
# thresholds, isn't expected to change run to run, but re-deciding it live
# would still violate the spirit of a locked decision.
ANCHOR_TYPE = "earnings_gap"

EARNINGS_COVERAGE_MAX_MISSING_PCT = 20.0

SHORTLIST_SHARPE = 0.0
SHORTLIST_EXPECTANCY_R = 0.1
SHORTLIST_MIN_TRADES = MIN_RELIABLE_TRADES
SHORTLIST_EXIT_EFFICIENCY_PCT = 65.0


def earnings_coverage(symbols: list[str], start: date, end: date) -> dict:
    """What engine/run_avwap_breakout.ANCHOR_TYPE was actually decided from
    -- callable standalone to re-verify the premise, though the run itself
    uses the locked constant above, not a fresh call to this."""
    missing = []
    counts: dict[str, int] = {}
    for symbol in symbols:
        df = data_module.earnings_dates(symbol)
        if df.empty:
            missing.append(symbol)
            counts[symbol] = 0
            continue
        idx = df.index.tz_localize(None) if df.index.tz is not None else df.index
        counts[symbol] = sum(1 for d in idx if start <= d.date() <= end)
    missing_pct = len(missing) / len(symbols) * 100 if symbols else 100.0
    return {
        "missing_symbols": missing,
        "missing_pct": missing_pct,
        "counts_per_symbol": counts,
        "mean_count": (sum(counts.values()) / len(counts)) if counts else 0.0,
        "recommended_anchor_type": (
            "swing_low" if missing_pct > EARNINGS_COVERAGE_MAX_MISSING_PCT else "earnings_gap"
        ),
    }


def _symbol_anchors(bars: pd.DataFrame, symbol: str, anchor_type: str) -> list[pd.Timestamp]:
    if anchor_type == "earnings_gap":
        earnings_df = data_module.earnings_dates(symbol)
        if earnings_df.empty:
            return []
        idx = earnings_df.index.tz_localize(None) if earnings_df.index.tz is not None else earnings_df.index
        raw_dates = [d.date() for d in idx]
        return avwap.earnings_gap_anchors(bars, raw_dates)
    return avwap.swing_low_anchors(bars)


def build_strategy_factory(
    symbols: list[str], start: date, end: date, anchor_type: str = ANCHOR_TYPE,
) -> tuple[Callable[[str], AvwapBreakout], dict[str, AvwapBreakout]]:
    """A `strategy_for(symbol)` factory (default params only -- see
    engine/run_avwap_breakout.py module docstring / engine/runner.py's
    AVWAP Breakout branch for how a Lab-tab param override layers on top
    without needing this factory to know about it) that also stashes each
    constructed instance in the returned dict, so its entry_log/exit_log
    can be read back after the backtest run completes -- the standard
    engine has no way to hand intermediate strategy instances back to a
    caller otherwise."""
    instances: dict[str, AvwapBreakout] = {}

    def factory(symbol: str) -> AvwapBreakout:
        bars = data_module.get_bars(symbol, "1d", start, end)
        anchors = _symbol_anchors(bars, symbol, anchor_type)
        anchor_dates = sorted({ts.date() for ts in anchors})
        strategy = AvwapBreakout(anchor_dates=anchor_dates, anchor_type=anchor_type)
        instances[symbol] = strategy
        return strategy

    return factory, instances


def run_avwap_breakout(
    symbols: list[str],
    start: date,
    end: date,
    risk_free_rate: float,
    anchor_type: str = ANCHOR_TYPE,
) -> tuple[StrategyBacktestResult, dict[str, AvwapBreakout], FilterDiagnostics]:
    factory, instances = build_strategy_factory(symbols, start, end, anchor_type)
    strategy_for, diagnostics = build_filter_factory(factory, symbols, start, end)
    result = run_strategy_backtest_seeded(
        STRATEGY_LABEL, strategy_for, symbols, "1d", start, end,
        risk_free_rate=risk_free_rate,
    )
    return result, instances, diagnostics


def _classify_exits(result: StrategyBacktestResult, instances: dict[str, AvwapBreakout]) -> pd.Series:
    """Every real trade closes via exactly one of: signal_exit / time_stop
    (both self-logged in AvwapBreakout.exit_log, keyed by entry_bar) or the
    broker-level hard stop (never visible to exit_signal -- see
    strategies/swing/avwap_breakout.py's docstring). A trade with no
    matching exit_log row is therefore, by construction, a hard stop --
    not a guess from price proximity to the stop level."""
    reasons = []
    for symbol, r in result.per_symbol.items():
        if r.trades.empty:
            continue
        strategy = instances.get(symbol)
        by_entry_bar = {e["entry_bar"]: e["reason"] for e in (strategy.exit_log if strategy else [])}
        for entry_bar in r.trades["EntryBar"]:
            reasons.append(by_entry_bar.get(int(entry_bar), "hard_stop"))
    return pd.Series(reasons, dtype="object")


def _mean_hold_days(result: StrategyBacktestResult) -> float | None:
    durations: list[float] = []
    for r in result.per_symbol.values():
        if r.trades.empty:
            continue
        durations.extend((r.trades["ExitBar"] - r.trades["EntryBar"]).tolist())
    return (sum(durations) / len(durations)) if durations else None


def _mean_entry_to_avwap_distance_pct(
    result: StrategyBacktestResult, instances: dict[str, AvwapBreakout]
) -> float | None:
    distances: list[float] = []
    for symbol, r in result.per_symbol.items():
        if r.trades.empty:
            continue
        strategy = instances.get(symbol)
        avwap_at_signal = {e["entry_bar"]: e["avwap_at_signal"] for e in (strategy.entry_log if strategy else [])}
        for _, trade in r.trades.iterrows():
            avwap_value = avwap_at_signal.get(int(trade["EntryBar"]))
            if avwap_value is None or avwap_value == 0:
                continue
            distances.append((float(trade["EntryPrice"]) - avwap_value) / avwap_value * 100)
    return (sum(distances) / len(distances)) if distances else None


def _mean_anchor_count(instances: dict[str, AvwapBreakout]) -> float:
    if not instances:
        return 0.0
    return sum(len(s.anchor_dates) for s in instances.values()) / len(instances)


def compute_diagnostics(
    result: StrategyBacktestResult, instances: dict[str, AvwapBreakout]
) -> dict:
    exit_reasons = _classify_exits(result, instances)
    total = len(exit_reasons)
    breakdown = (
        {reason: float((exit_reasons == reason).mean() * 100) for reason in exit_reasons.unique()}
        if total else {}
    )
    exc = result.excursions
    if exc.empty:
        exit_eff, loss_ratio = None, None
    else:
        eff = exc.loc[exc["RealizedR"] > 0, "ExitEfficiencyPct"].dropna()
        loss = exc.loc[exc["RealizedR"] < 0, "LossRealizationRatioPct"].dropna()
        exit_eff = float(eff.mean()) if not eff.empty else None
        loss_ratio = float(loss.mean()) if not loss.empty else None

    return {
        "mean_anchors_per_symbol": _mean_anchor_count(instances),
        "exit_type_pct_signal_exit": breakdown.get("signal_exit", 0.0),
        "exit_type_pct_hard_stop": breakdown.get("hard_stop", 0.0),
        "exit_type_pct_time_stop": breakdown.get("time_stop", 0.0),
        "mean_hold_days": _mean_hold_days(result),
        "mean_entry_to_avwap_distance_pct": _mean_entry_to_avwap_distance_pct(result, instances),
        "exit_efficiency_pct": exit_eff,
        "loss_realization_ratio_pct": loss_ratio,
    }


def _report_row(anchor_type: str, result: StrategyBacktestResult, diagnostics: dict) -> dict:
    m: BacktestMetrics = result.metrics
    return {
        "anchor_type": anchor_type,
        "trades_taken": m.trades_taken,
        "win_rate": m.win_rate,
        "avg_win_r": m.avg_win_r,
        "avg_loss_r": m.avg_loss_r,
        "expectancy_r": m.expectancy_r,
        "profit_factor": m.profit_factor,
        "max_drawdown_pct": m.max_drawdown_pct,
        "sharpe": m.sharpe,
        "alpha_pct": m.alpha_pct,
        "beta": m.beta,
        "cagr_pct": m.cagr_pct,
        "exposure_pct": m.exposure_pct,
        **diagnostics,
    }


def _clears_shortlist(row: dict) -> bool:
    return (
        row["sharpe"] is not None
        and row["sharpe"] > SHORTLIST_SHARPE
        and row["expectancy_r"] > SHORTLIST_EXPECTANCY_R
        and row["trades_taken"] >= SHORTLIST_MIN_TRADES
        and row["exit_efficiency_pct"] is not None
        and row["exit_efficiency_pct"] > SHORTLIST_EXIT_EFFICIENCY_PCT
    )


def write_report(anchor_type: str, row: dict) -> None:
    LOGS_DIR.mkdir(exist_ok=True)
    filename = f"avwap_breakout_{'earnings_anchor' if anchor_type == 'earnings_gap' else 'swinglow_anchor'}.csv"
    pd.DataFrame([row]).to_csv(LOGS_DIR / filename, index=False)


def run_sanity_checks(
    result: StrategyBacktestResult, instances: dict[str, AvwapBreakout], row: dict
) -> list[str]:
    """Each check from the task spec, logged as a finding rather than
    silently passed/failed."""
    findings = []

    anchor_counts = [len(s.anchor_dates) for s in instances.values()]
    if anchor_counts:
        if max(anchor_counts) >= 19:  # ~every earnings date in a 5yr/20-report window qualifying
            findings.append(
                f"Max anchors for one symbol is {max(anchor_counts)}, close to the ~20 possible "
                "earnings dates in this window -- check the 3% gap threshold isn't too loose."
            )
        if sum(anchor_counts) == 0:
            findings.append(
                "Zero qualifying anchors across the whole universe -- gap/volume threshold is "
                "too tight, or earnings data isn't actually available despite the coverage check."
            )
        findings.append(
            f"Mean anchors/symbol: {sum(anchor_counts) / len(anchor_counts):.1f} "
            f"(range {min(anchor_counts)}-{max(anchor_counts)}) across {len(anchor_counts)} symbols."
        )

    if row["beta"] is not None and row["beta"] < 0.05:
        findings.append(
            f"Beta ({row['beta']:.3f}) is below 0.05 -- unexpectedly low for a trend-following "
            "breakout entry; check position sizing/capital deployment."
        )

    if row["exit_efficiency_pct"] is not None and row["exit_efficiency_pct"] < 55.0:
        findings.append(
            f"Exit efficiency ({row['exit_efficiency_pct']:.1f}%) is below 55% -- the AVWAP "
            "cross-below exit may be firing too early. Logged per instructions, NOT adjusted "
            "mid-test."
        )

    if row["trades_taken"] > 0:
        time_stop_pct = row.get("exit_type_pct_time_stop", 0.0)
        if time_stop_pct > 40.0:
            findings.append(
                f"{time_stop_pct:.1f}% of exits were time stops (>40%) -- the 60-day window may "
                "be too short, or the AVWAP exit signal isn't firing cleanly."
            )

    return findings


def main() -> None:
    start, end = daily_date_range()
    symbols = EQUITY_UNIVERSE
    risk_free_rate = data_module.risk_free_rate(start, end)

    coverage = earnings_coverage(symbols, start, end)
    print(f"Earnings coverage check: {coverage['missing_pct']:.1f}% missing, "
          f"mean {coverage['mean_count']:.1f} events/symbol -> "
          f"recommended anchor type: {coverage['recommended_anchor_type']}")
    print(f"Locked anchor type (this run): {ANCHOR_TYPE}")
    print()

    result, instances, filter_diagnostics = run_avwap_breakout(
        symbols, start, end, risk_free_rate, ANCHOR_TYPE
    )
    diagnostics = compute_diagnostics(result, instances)
    row = _report_row(ANCHOR_TYPE, result, diagnostics)
    write_report(ANCHOR_TYPE, row)

    for k, v in row.items():
        print(f"  {k}: {v}")
    print()
    print(filter_diagnostics.summary())
    print()

    findings = run_sanity_checks(result, instances, row)
    if findings:
        print("Sanity check findings:")
        for f in findings:
            print(f"  - {f}")
    else:
        print("No sanity check findings.")
    print()

    if _clears_shortlist(row):
        print(f"CLEARS SHORTLIST BAR (Sharpe>{SHORTLIST_SHARPE}, expectancy>{SHORTLIST_EXPECTANCY_R}R, "
              f">={SHORTLIST_MIN_TRADES} trades, exit efficiency>{SHORTLIST_EXIT_EFFICIENCY_PCT}%) -- "
              "run held-out validation next, no further changes.")
    else:
        print("Does not clear the shortlist bar on in-sample data.")


if __name__ == "__main__":
    main()
