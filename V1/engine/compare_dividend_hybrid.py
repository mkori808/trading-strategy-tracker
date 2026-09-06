"""Dividend Hybrid: Version A (no stop) vs Version B (8% stop), side by side.

This is the strategy's central question. The original thesis is that a stop
is unnecessary because the dividend is a floor -- if the trade goes against
you, you simply become a long-term dividend holder. That claim is testable
and this module tests it directly.

Every run is produced along three axes, because two of them are limitations
of the data/spec rather than of the strategy, and collapsing them would hide
which is which:

  version      A (no stop) vs B (8% stop)          -- the actual question
  screen_mode  point_in_time vs full               -- how much survivorship
                                                      bias the snapshot
                                                      fundamentals inject
  trigger_mode spec vs intraday_proxy              -- whether the daily
                                                      rendering of the
                                                      intraday entry can fire

Writes logs/dividend_hybrid_comparison.csv. Deliberately does NOT write to
engine/logging_db.py, matching engine/compare_universe.py and
engine/compare_filters.py: that schema is keyed on strategy name with no
version/screen/trigger field, so logging these would silently shadow one
another and the dashboard's single "latest run" row would be meaningless.

Run with:
    python -m engine.compare_dividend_hybrid
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from engine import data as data_module
from engine.dividend_hybrid import (
    SCREEN_FULL,
    SCREEN_POINT_IN_TIME,
    VERSION_A,
    VERSION_B,
    DividendHybridResult,
    manual_checkpoint_notice,
    run_dividend_hybrid,
)
from engine.fundamentals import NOT_POINT_IN_TIME_WARNING, SURVIVORSHIP_WARNING
from engine.universe import EQUITY_UNIVERSE, daily_date_range
from strategies.swing.dividend_hybrid import (
    DRAWDOWN_BUCKETS_PCT,
    TRIGGER_INTRADAY_PROXY,
    TRIGGER_SPEC,
    DividendHybrid,
)

STRATEGY_NAME = "Dividend Hybrid"

# Entry on the screen alone, with the gap-up trigger disabled. Not the
# specified strategy; the only configuration that yields a testable sample.
ENTRY_SCREEN_ONLY = "screen_only_no_trigger"
OUTPUT_CSV = Path(__file__).resolve().parent.parent / "logs" / "dividend_hybrid_comparison.csv"

# Expected share of a large-cap universe passing a >4% trailing yield screen.
# Outside this band the yield feed is suspect, not the strategy -- a 50%+ pass
# rate on the Dow would mean stale or mis-scaled dividend data.
YIELD_PASS_SANITY_BAND = (0.05, 0.35)


def _row(result: DividendHybridResult, trigger_mode: str) -> dict:
    m = result.metrics
    closed = result.closed_only_metrics()
    worst = (
        float(result.trades["MaxUnrealizedDrawdownPct"].min())
        if not result.trades.empty else 0.0
    )
    row = {
        "version": result.version,
        "screen_mode": result.screen_mode,
        "entry_mode": trigger_mode,
        "trades_taken": m.trades_taken,
        "closed_trades": result.closed_trades,
        "still_held_at_end": result.still_held,
        "win_rate": m.win_rate,
        "win_rate_closed_only": closed.win_rate,
        "avg_win_r": m.avg_win_r,
        "avg_loss_r": m.avg_loss_r,
        "expectancy_r": m.expectancy_r,
        "profit_factor": m.profit_factor,
        "max_unrealized_drawdown_pct": worst,
        "dividend_cuts_during_hold": result.dividend_cuts_during_hold,
        "total_return_pct": result.return_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "sharpe": result.sharpe,
        "max_concurrent_positions": result.max_concurrent_positions,
    }
    for level in DRAWDOWN_BUCKETS_PCT:
        row[f"trades_unrealized_loss_over_{int(level)}pct"] = result.drawdown_bucket_counts[level]
    return row


def _print_header(start, end) -> None:
    bar = "=" * 74
    print(bar)
    print(NOT_POINT_IN_TIME_WARNING)
    print()
    print(SURVIVORSHIP_WARNING)
    print(bar)
    print(f"{STRATEGY_NAME}: Version A (no stop) vs Version B (8% stop)")
    print(f"Window: {start} to {end}   Universe: {len(EQUITY_UNIVERSE)} names (Dow, 2021 roster)")
    print(bar)
    print(manual_checkpoint_notice())
    print(bar)


def _print_result(result: DividendHybridResult, trigger_mode: str) -> None:
    m = result.metrics
    label = f"Version {result.version} | screen={result.screen_mode} | entry={trigger_mode}"
    print(f"\n{label}")
    print("-" * len(label))
    if m.trades_taken == 0:
        print("  No trades. Screen and trigger never fired together.")
        return
    closed = result.closed_only_metrics()
    print(f"  Trades Taken:          {m.trades_taken}"
          f"  (closed {result.closed_trades}, still held {result.still_held})")
    print(f"  Win Rate:              {m.win_rate:.1%}  "
          f"[closed trades only: {closed.win_rate:.1%}]")
    print(f"  Avg Win / Avg Loss R:  {m.avg_win_r:.2f} / {m.avg_loss_r:.2f}")
    print(f"  Expectancy (R):        {m.expectancy_r:+.3f}")
    pf = "inf" if m.profit_factor == float("inf") else f"{m.profit_factor:.2f}"
    print(f"  Profit Factor:         {pf}")
    print(f"  Total Return:          {result.return_pct:+.1f}%")
    print(f"  Max Drawdown:          {result.max_drawdown_pct:.1f}%")
    if result.sharpe is not None:
        print(f"  Sharpe:                {result.sharpe:.2f} (rf={result.risk_free_rate:.1%})")
    worst = float(result.trades["MaxUnrealizedDrawdownPct"].min())
    print(f"  Worst unrealized DD:   {worst:.1f}% during a hold")
    print("  Unrealized loss buckets: " + ", ".join(
        f">{int(level)}%: {result.drawdown_bucket_counts[level]}"
        for level in DRAWDOWN_BUCKETS_PCT
    ))
    print(f"  Dividend cuts in hold: {result.dividend_cuts_during_hold}")
    print(f"  Max concurrent:        {result.max_concurrent_positions} positions")
    print(f"  Status:                {m.status}")
    for warning in result.warnings:
        print(f"  {warning}")


def _print_screen_sanity(result: DividendHybridResult) -> None:
    """CLAUDE.md's sanity check: what share of the universe clears >4% yield
    on a typical scan date? Far outside the expected band means the dividend
    data is wrong, and no downstream number should be trusted."""
    log = result.screen_log
    if log.empty:
        return
    rate = float(log["yield_pass_rate"].mean())
    low, high = YIELD_PASS_SANITY_BAND
    verdict = "OK" if low <= rate <= high else "SUSPECT -- check the dividend feed"
    print(f"\nYield-screen sanity: {rate:.1%} of the universe clears >4% "
          f"trailing yield on an average scan date  [{verdict}]")
    print(f"Full screen passes on {float(log['screen_pass'].mean()):.2f} names "
          f"per date (of {int(log['evaluated'].mean())} evaluated)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default=str(OUTPUT_CSV), help="Comparison table output path")
    args = parser.parse_args()

    start, end = daily_date_range()
    rf = data_module.risk_free_rate(start, end)
    _print_header(start, end)

    # (entry label, trigger_mode, require_trigger). SCREEN_ONLY is not the
    # strategy as specified -- see run_dividend_hybrid's docstring for why it
    # is the only row that can answer the stop question at all.
    entry_modes = [
        (TRIGGER_SPEC, TRIGGER_SPEC, True),
        (TRIGGER_INTRADAY_PROXY, TRIGGER_INTRADAY_PROXY, True),
        (ENTRY_SCREEN_ONLY, TRIGGER_SPEC, False),
    ]

    rows = []
    sanity_printed = False
    for entry_label, trigger_mode, require_trigger in entry_modes:
        config = DividendHybrid(trigger_mode=trigger_mode)
        for screen_mode in (SCREEN_POINT_IN_TIME, SCREEN_FULL):
            for version in (VERSION_A, VERSION_B):
                result = run_dividend_hybrid(
                    STRATEGY_NAME, config, EQUITY_UNIVERSE, start, end,
                    version=version, screen_mode=screen_mode, risk_free_rate=rf,
                    require_trigger=require_trigger,
                )
                _print_result(result, entry_label)
                rows.append(_row(result, entry_label))
                if not sanity_printed and screen_mode == SCREEN_POINT_IN_TIME:
                    _print_screen_sanity(result)
                    sanity_printed = True

    table = pd.DataFrame(rows)
    Path(args.csv).parent.mkdir(exist_ok=True)
    table.to_csv(args.csv, index=False)
    print(f"\nWrote {len(table)} rows to {args.csv}")
    print("\n" + NOT_POINT_IN_TIME_WARNING)


if __name__ == "__main__":
    main()
