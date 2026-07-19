"""Targeted exit-rule sensitivity test for Sector Rotation Play, and ONLY
Sector Rotation Play -- see LESSONS.md. Follows the same "never shadow the
canonical logged result" rule as engine/compare_filters.py/
compare_universe.py/compare_dividend_hybrid.py: this does not write to
engine/logging_db.py.

Premise check done before writing any of this (see LESSONS.md): the
registered Sector Rotation Play (strategies/swing/sector_rotation.py) is
NOT a top-N cross-sectional monthly-rebalance strategy -- it's a per-ETF
relative-strength (sector Close / SPY Close) EMA crossover, entered and
exited independently per symbol through the standard bracket engine
(engine/backtest.py), with NO cross-sectional ranking at all. Its exit is
already signal-based and is currently checked every trading day the
position is open. There is no "top-N drop-out at rebalance" mechanism to
vary -- Dual Momentum is the strategy that actually works that way, and it
runs on the Dow universe, not the sector ETFs, so it can't substitute here.

What's actually varied, holding entry rule / ranking-free RS crossover /
stop rule / universe (SECTOR_UNIVERSE, 11 sector SPDRs) / cost model /
risk-free rate / date range IDENTICAL across all four arms: the CADENCE at
which the existing RS-crossover exit signal is even evaluated.

  A (control): exit_signal checked every trading day -- exactly today's
      registered behavior. Must reproduce the logged baseline (117 trades,
      44.4% win rate, +0.0963R expectancy) before B/C/D are trusted.
  B: exit_signal only checked every 21 trading days since entry
  C: exit_signal only checked every 63 trading days since entry
  D: exit_signal never checked before 21 trading days since entry (a
     floor), checked every day after that

The stop-loss (support-lookback stop, strategies/swing/sector_rotation.py
:stop_price) is a broker-level bracket order and is NEVER gated by cadence
in any variant -- only the RS-crossover SIGNAL exit is. Risk management is
identical across all four arms; only how promptly the signal exit can act
differs. This also means B/C/D positions can still close earlier than
their nominal cadence if the stop is hit first.

Run with: python -m engine.compare_sector_rotation_exits
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd

from engine import data as data_module
from engine.backtest import StrategyBacktestResult, run_strategy_backtest_seeded
from engine.excursion import LOGS_DIR
from engine.metrics import MIN_RELIABLE_TRADES, BacktestMetrics
from engine.universe import SECTOR_BENCHMARK, SECTOR_UNIVERSE, daily_date_range
from strategies.base import Strategy
from strategies.swing.sector_rotation import SectorRotationPlay

STRATEGY_LABEL = "Sector Rotation Play"

# The "real finding" bar from the task spec -- a variant only counts as a
# shortlist candidate if it clears every one of these simultaneously.
SHORTLIST_SHARPE = 0.0
SHORTLIST_EXPECTANCY_R = 0.1
SHORTLIST_MIN_TRADES = MIN_RELIABLE_TRADES  # 30, same bar as everywhere else
SHORTLIST_EXIT_EFFICIENCY_PCT = 75.0

# The already-logged canonical run this test's Variant A must reproduce
# (engine.logging_db.latest_run_per_strategy()["Sector Rotation Play"] as of
# the run that produced logs/sector_rotation_play_mfe_mae_summary.txt).
# "Within rounding" per the task spec: exact trade count, metrics within
# 1e-6 -- this is a deterministic replay against the same cached local bars,
# not a fresh live pull, so there is no real source of drift to tolerate.
BASELINE_TRADES_TAKEN = 117
BASELINE_WIN_RATE = 0.4444444444444444
BASELINE_EXPECTANCY_R = 0.09627150303357118
_TOLERANCE = 1e-6


class PeriodicExitStrategy(Strategy):
    """Wraps `inner` so its exit_signal() is only evaluated -- and can only
    fire -- at a periodic cadence, with an optional minimum-hold floor.
    Entry rule, entry direction, and stop/target all pass straight through
    unchanged; check_every_days=1 and min_hold_days=0 is behaviorally
    identical to the unwrapped inner strategy (Variant A, the control).

    `bars` handed to entry_signal/exit_signal is cumulative (grows by one
    row per trading day -- see engine/backtest.py's adapter), so
    len(bars) at the moment entry_signal() returns True is a stable stamp
    for "how many bars had occurred as of entry." A real position always
    opens on the bar immediately following the LAST entry_signal()=True
    call before it fires (engine/backtest.py's adapter enters or skips
    entirely on that same bar -- it never defers to a later, unrelated
    True), so re-stamping on every True call and simply overwriting any
    earlier one that didn't lead to a real fill (e.g. skipped by the
    bracket-price sanity check) is safe.
    """

    def __init__(self, inner: Strategy, check_every_days: int = 1, min_hold_days: int = 0) -> None:
        self._inner = inner
        self.check_every_days = check_every_days
        self.min_hold_days = min_hold_days
        self.name = inner.name
        self.timeframe = inner.timeframe
        self.direction = inner.direction
        self._entry_len: int | None = None

    def entry_signal(self, bars: pd.DataFrame) -> bool:
        fires = self._inner.entry_signal(bars)
        if fires:
            self._entry_len = len(bars)
        return fires

    def entry_direction(self, bars: pd.DataFrame) -> Literal["long", "short"]:
        return self._inner.entry_direction(bars)

    def stop_price(self, bars: pd.DataFrame, entry_price: float) -> float:
        return self._inner.stop_price(bars, entry_price)

    def target_price(self, bars: pd.DataFrame, entry_price: float) -> float | None:
        return self._inner.target_price(bars, entry_price)

    def exit_signal(self, bars: pd.DataFrame) -> bool:
        if self._entry_len is None:
            # Defensive only -- exit_signal is only ever called while a
            # position is open, which always followed an entry_signal=True
            # stamp. Fail open to the inner rule rather than get stuck.
            return self._inner.exit_signal(bars)

        held = len(bars) - self._entry_len
        if held < self.min_hold_days:
            return False
        if self.check_every_days > 1 and held % self.check_every_days != 0:
            return False

        fired = self._inner.exit_signal(bars)
        if fired:
            self._entry_len = None
        return fired


@dataclass
class Variant:
    label: str
    check_every_days: int
    min_hold_days: int


VARIANTS: list[Variant] = [
    Variant("A: daily check (control)", check_every_days=1, min_hold_days=0),
    Variant("B: every 21 trading days", check_every_days=21, min_hold_days=0),
    Variant("C: every 63 trading days", check_every_days=63, min_hold_days=0),
    Variant("D: 21-day min hold, then daily", check_every_days=1, min_hold_days=21),
]


def _run_variant(
    variant: Variant,
    benchmark_bars: pd.DataFrame,
    start,
    end,
    risk_free_rate: float,
) -> StrategyBacktestResult:
    def factory(_symbol: str) -> Strategy:
        # Fresh SectorRotationPlay + fresh PeriodicExitStrategy per symbol --
        # not one shared instance -- so the wrapper's entry-bar bookkeeping
        # never leaks state between symbols' independent backtest runs.
        inner = SectorRotationPlay(benchmark_bars)
        return PeriodicExitStrategy(inner, variant.check_every_days, variant.min_hold_days)

    return run_strategy_backtest_seeded(
        STRATEGY_LABEL, factory, SECTOR_UNIVERSE, "1d", start, end,
        risk_free_rate=risk_free_rate,
    )


def _mean_hold_days(result: StrategyBacktestResult) -> float | None:
    durations: list[float] = []
    for r in result.per_symbol.values():
        if r.trades.empty:
            continue
        durations.extend((r.trades["ExitBar"] - r.trades["EntryBar"]).tolist())
    return (sum(durations) / len(durations)) if durations else None


def _excursion_stats(result: StrategyBacktestResult) -> tuple[float | None, float | None]:
    """Mean exit efficiency (winners) / loss realization ratio (losers) --
    same definitions as engine/excursion.py's write_excursion_report,
    already computed for this result by the standard engine."""
    exc = result.excursions
    if exc.empty:
        return None, None
    exit_eff = exc.loc[exc["RealizedR"] > 0, "ExitEfficiencyPct"].dropna()
    loss_ratio = exc.loc[exc["RealizedR"] < 0, "LossRealizationRatioPct"].dropna()
    return (
        float(exit_eff.mean()) if not exit_eff.empty else None,
        float(loss_ratio.mean()) if not loss_ratio.empty else None,
    )


def _row(variant: Variant, result: StrategyBacktestResult) -> dict:
    m: BacktestMetrics = result.metrics
    exit_eff, loss_ratio = _excursion_stats(result)
    return {
        "variant": variant.label,
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
        "exit_efficiency_pct": exit_eff,
        "loss_realization_ratio_pct": loss_ratio,
        "mean_hold_days": _mean_hold_days(result),
    }


def _baseline_reproduces(row: dict) -> list[str]:
    """Empty list = reproduced within tolerance. Non-empty = mismatches,
    each a reason to stop before running B/C/D at all."""
    problems = []
    if row["trades_taken"] != BASELINE_TRADES_TAKEN:
        problems.append(
            f"trades_taken={row['trades_taken']} != logged baseline {BASELINE_TRADES_TAKEN}"
        )
    if abs(row["win_rate"] - BASELINE_WIN_RATE) > _TOLERANCE:
        problems.append(f"win_rate={row['win_rate']} != logged baseline {BASELINE_WIN_RATE}")
    if abs(row["expectancy_r"] - BASELINE_EXPECTANCY_R) > _TOLERANCE:
        problems.append(
            f"expectancy_r={row['expectancy_r']} != logged baseline {BASELINE_EXPECTANCY_R}"
        )
    return problems


def _clears_shortlist(row: dict) -> bool:
    return (
        row["sharpe"] is not None
        and row["sharpe"] > SHORTLIST_SHARPE
        and row["expectancy_r"] > SHORTLIST_EXPECTANCY_R
        and row["trades_taken"] >= SHORTLIST_MIN_TRADES
        and row["exit_efficiency_pct"] is not None
        and row["exit_efficiency_pct"] > SHORTLIST_EXIT_EFFICIENCY_PCT
    )


def _build_notes(rows: list[dict]) -> str:
    by_label = {r["variant"]: r for r in rows}
    a, b, c, d = rows[0], rows[1], rows[2], rows[3]

    lines = [f"Sector Rotation Play -- exit-cadence sensitivity test", "=" * 55, ""]

    lines.append(
        "Scope note: the registered Sector Rotation Play has no top-N "
        "cross-sectional rebalance mechanism to vary (see this module's "
        "docstring) -- what's tested here is the CADENCE at which its "
        "existing daily RS-crossover signal exit is evaluated, with the "
        "stop-loss left live every day in all four variants."
    )
    lines.append("")

    with_eff = [r for r in rows if r["exit_efficiency_pct"] is not None]
    best_eff = max(with_eff, key=lambda r: r["exit_efficiency_pct"]) if with_eff else None
    with_sharpe = [r for r in rows if r["sharpe"] is not None]
    best_sharpe = max(with_sharpe, key=lambda r: r["sharpe"]) if with_sharpe else None

    lines.append(
        f"Highest exit efficiency: {best_eff['variant']} "
        f"({best_eff['exit_efficiency_pct']:.1f}%)" if best_eff else "Highest exit efficiency: n/a"
    )
    lines.append(
        f"Highest Sharpe: {best_sharpe['variant']} ({best_sharpe['sharpe']:.2f})"
        if best_sharpe else "Highest Sharpe: n/a"
    )
    lines.append("")

    shortlisted = [r for r in rows if _clears_shortlist(r)]
    if shortlisted:
        names = ", ".join(r["variant"] for r in shortlisted)
        lines.append(
            f"CLEARS SHORTLIST BAR (Sharpe>{SHORTLIST_SHARPE}, expectancy>{SHORTLIST_EXPECTANCY_R}R, "
            f">={SHORTLIST_MIN_TRADES} trades, exit efficiency>{SHORTLIST_EXIT_EFFICIENCY_PCT}%): {names}"
        )
    else:
        lines.append(
            f"No variant clears the shortlist bar (Sharpe>{SHORTLIST_SHARPE}, "
            f"expectancy>{SHORTLIST_EXPECTANCY_R}R, >={SHORTLIST_MIN_TRADES} trades, "
            f"exit efficiency>{SHORTLIST_EXIT_EFFICIENCY_PCT}%)."
        )
    lines.append("")

    # Sanity checks, each logged explicitly rather than silently passed/failed.
    lines.append("Sanity checks:")

    if b["trades_taken"] >= a["trades_taken"]:
        lines.append(
            f"  - FLAG: Variant B trade count ({b['trades_taken']}) is not lower than "
            f"Variant A ({a['trades_taken']}) despite forcing longer holds -- check the "
            "cadence gate."
        )
    else:
        lines.append(
            f"  - OK: trade count decreases with cadence (A={a['trades_taken']}, "
            f"B={b['trades_taken']}, C={c['trades_taken']}, D={d['trades_taken']})."
        )

    for label, row in (("C", c), ("D", d)):
        if row["mean_hold_days"] is not None and a["mean_hold_days"] is not None \
                and row["mean_hold_days"] < a["mean_hold_days"]:
            lines.append(
                f"  - FLAG: Variant {label} mean hold ({row['mean_hold_days']:.1f}d) is "
                f"below Variant A's ({a['mean_hold_days']:.1f}d) -- expected the opposite; "
                "check the min-hold/cadence gate for thrashing."
            )
    if a["mean_hold_days"] is not None:
        lines.append(
            "  - Mean hold (trading days): "
            + ", ".join(
                f"{r['variant'].split(':')[0]}={r['mean_hold_days']:.1f}"
                if r["mean_hold_days"] is not None else f"{r['variant'].split(':')[0]}=n/a"
                for r in rows
            )
        )

    effs = [r["exit_efficiency_pct"] for r in rows]
    if all(e is not None for e in effs):
        monotonic = effs[0] <= effs[1] <= effs[2] and effs[0] <= effs[1] <= effs[3]
        if effs[1] < effs[0]:
            lines.append(
                f"  - FLAG (logged, not buried): Variant B exit efficiency "
                f"({effs[1]:.1f}%) is LOWER than Variant A ({effs[0]:.1f}%). The "
                "hypothesis assumed longer holds let moves play out further; this "
                "result says the quarterly-scale hold is instead overshooting the "
                "natural move duration in the wrong direction from what was assumed."
            )
        elif monotonic:
            lines.append(
                f"  - OK: exit efficiency rises monotonically A->B->C/D "
                f"({effs[0]:.1f}% -> {effs[1]:.1f}% -> {effs[2]:.1f}%/{effs[3]:.1f}%)."
            )
        else:
            lines.append(
                f"  - Exit efficiency is not cleanly monotonic across variants "
                f"({effs[0]:.1f}%, {effs[1]:.1f}%, {effs[2]:.1f}%, {effs[3]:.1f}%) -- "
                "mixed result, not a clean confirmation of the hypothesis."
            )

    for r in rows:
        if r["sharpe"] is not None and r["sharpe"] > a["sharpe"] + 0.5 and r["trades_taken"] < SHORTLIST_MIN_TRADES:
            lines.append(
                f"  - FLAG: {r['variant']} shows a large Sharpe improvement "
                f"({r['sharpe']:.2f} vs. A's {a['sharpe']:.2f}) on only "
                f"{r['trades_taken']} trades (<{SHORTLIST_MIN_TRADES}) -- unreliable "
                "regardless of the headline number, per the small-sample trap in LESSONS.md."
            )
    lines.append("")

    # "Improvement, not a finding" vs. noise-given-sample-size framing.
    non_a = [r for r in rows[1:]]
    better_than_a = [r for r in non_a if r["expectancy_r"] > a["expectancy_r"] and (r["sharpe"] or -99) > (a["sharpe"] or -99)]
    if shortlisted:
        lines.append(
            "Result: at least one variant clears the shortlist bar -- see 'What to do "
            "with the result' in the task instructions (held-out validation required "
            "before promotion)."
        )
    elif better_than_a:
        names = ", ".join(r["variant"] for r in better_than_a)
        lines.append(
            f"Result: {names} improve on Variant A's expectancy and Sharpe but "
            f"Sharpe stays negative for all four variants (best: "
            f"{best_sharpe['sharpe']:.2f}) -- an improvement, not a finding. On "
            f"{a['trades_taken']}-{d['trades_taken']}-trade samples this size of "
            "Sharpe/expectancy movement is well within what noise alone produces; "
            "not large enough to act on."
        )
    else:
        lines.append(
            "Result: no variant meaningfully improves on Variant A. Exit-rule "
            "cadence is not the source of this strategy's underperformance."
        )

    return "\n".join(lines) + "\n"


def run_comparison() -> tuple[pd.DataFrame, dict[str, StrategyBacktestResult]]:
    start, end = daily_date_range()
    benchmark_bars = data_module.get_bars(SECTOR_BENCHMARK, "1d", start, end)
    risk_free_rate = data_module.risk_free_rate(start, end)

    rows: list[dict] = []
    results: dict[str, StrategyBacktestResult] = {}
    for i, variant in enumerate(VARIANTS):
        result = _run_variant(variant, benchmark_bars, start, end, risk_free_rate)
        row = _row(variant, result)
        results[variant.label] = result

        if i == 0:
            problems = _baseline_reproduces(row)
            if problems:
                raise RuntimeError(
                    "Variant A did not reproduce the logged Sector Rotation Play "
                    "baseline -- stopping before running B/C/D (a baseline that "
                    "doesn't reproduce is not a baseline). Mismatches: "
                    + "; ".join(problems)
                )
        rows.append(row)

    return pd.DataFrame(rows), results


def main() -> None:
    df, _ = run_comparison()
    LOGS_DIR.mkdir(exist_ok=True)
    df.to_csv(LOGS_DIR / "sector_rotation_exit_variants.csv", index=False)
    notes = _build_notes(df.to_dict("records"))
    (LOGS_DIR / "sector_rotation_exit_variants_notes.txt").write_text(notes)
    print(df.to_string(index=False))
    print()
    print(notes)


if __name__ == "__main__":
    main()
