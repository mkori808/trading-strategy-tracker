"""Maximum Favorable/Adverse Excursion (MFE/MAE) and exit-quality diagnostics.

The realized-R metrics in engine/metrics.py answer "did the trade win or
lose"; they throw away everything that happened between entry and exit. MFE
is the best price reached during the trade, MAE is the worst -- together
they diagnose *how* a strategy is winning or losing (exits too early? stops
too tight? no real edge at all?), which a bare win rate can't. Both are
expressed in R (the same unit as realized_r -- see
engine/metrics.py:r_multiples) so they're directly comparable.

This module is purely additive: it reads the same `bars` and `trades`
frame engine/backtest.py already has in scope and never changes what
compute_metrics/BacktestMetrics/logging_db produce.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from engine.metrics import r_multiples

logger = logging.getLogger(__name__)

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

# Thresholds from the diagnostic itself, not from any single backtest run --
# used only to flag a summary as worth a second look, never to change a
# computed number.
LOW_EXIT_EFFICIENCY_PCT = 60.0
HIGH_EXIT_EFFICIENCY_SUSPECT_PCT = 95.0
HIGH_LOSS_REALIZATION_PCT = 80.0
LOW_LOSS_REALIZATION_SUSPECT_PCT = 30.0

_EXCURSION_COLUMNS = [
    "EntryTime", "ExitTime", "Direction", "EntryPrice", "ExitPrice",
    "RealizedR", "MFE_R", "MAE_R", "ExitEfficiencyPct",
    "LossRealizationRatioPct", "EntrySlippagePct",
]


def slugify(strategy_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", strategy_name.lower()).strip("_")
    return slug or "strategy"


def compute_trade_excursions(bars: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """Per-trade MFE/MAE/exit-efficiency/loss-realization/entry-slippage.

    `bars` is the OHLC frame the trade was run against (High/Low used for
    the intrabar excursion, Close used for the entry-slippage baseline);
    `trades` is backtesting.py's trades frame (EntryBar/ExitBar are
    positional indices into `bars` -- see backtesting/_stats.py).

    MFE must be >= realized_r for a winner and MAE must be >= |realized_r|
    for a loser -- you cannot realize more than the best (or survive less
    than the worst) price actually reached. A trade that violates this
    points at a bug in the bar window or the risk_per_share lookup, not a
    real result, so it's logged and dropped rather than written out.
    """
    if trades.empty:
        return pd.DataFrame(columns=_EXCURSION_COLUMNS)

    fallback_risk = (trades["EntryPrice"] - trades["SL"]).abs()
    if "Tag" in trades.columns:
        risk_per_share = pd.to_numeric(trades["Tag"], errors="coerce").fillna(fallback_risk)
    else:
        risk_per_share = fallback_risk

    realized_r = r_multiples(trades)
    highs = bars["High"].to_numpy()
    lows = bars["Low"].to_numpy()
    closes = bars["Close"].to_numpy()

    rows = []
    for pos, (idx, trade) in enumerate(trades.iterrows()):
        entry_bar = int(trade["EntryBar"])
        exit_bar = int(trade["ExitBar"])
        entry_price = float(trade["EntryPrice"])
        rps = float(risk_per_share.iloc[pos])
        r = realized_r.iloc[pos]
        is_long = float(trade["Size"]) > 0

        if rps <= 0 or not np.isfinite(rps):
            logger.error(
                "Skipping trade at EntryBar=%s: non-positive risk_per_share (%s)",
                entry_bar, rps,
            )
            continue

        window_high = highs[entry_bar:exit_bar + 1].max()
        window_low = lows[entry_bar:exit_bar + 1].min()

        if is_long:
            mfe_r = max(window_high - entry_price, 0.0) / rps
            mae_r = max(entry_price - window_low, 0.0) / rps
        else:
            mfe_r = max(entry_price - window_low, 0.0) / rps
            mae_r = max(window_high - entry_price, 0.0) / rps

        if pd.notna(r) and r > 0 and mfe_r < r - 1e-9:
            logger.error(
                "Skipping trade at EntryBar=%s: MFE_R=%.4f < realized_r=%.4f "
                "(should be impossible -- bar window or risk lookup is wrong)",
                entry_bar, mfe_r, r,
            )
            continue
        if pd.notna(r) and r < 0 and mae_r < abs(r) - 1e-9:
            logger.error(
                "Skipping trade at EntryBar=%s: MAE_R=%.4f < |realized_r|=%.4f "
                "(should be impossible -- bar window or risk lookup is wrong)",
                entry_bar, mae_r, abs(r),
            )
            continue

        exit_efficiency = np.nan
        if pd.notna(r) and r > 0 and mfe_r > 0:
            exit_efficiency = r / mfe_r * 100

        loss_realization = np.nan
        if pd.notna(r) and r < 0 and mae_r > 0:
            loss_realization = abs(r) / mae_r * 100

        # Signal bar is the one whose close triggered entry_signal(); the
        # engine fills the order at the *next* bar (see
        # engine/backtest.py:_make_adapter), which is EntryBar itself.
        entry_slippage = np.nan
        if entry_bar > 0:
            signal_close = closes[entry_bar - 1]
            signal_range = highs[entry_bar - 1] - lows[entry_bar - 1]
            if signal_range > 0:
                entry_slippage = (entry_price - signal_close) / signal_range * 100

        rows.append({
            "EntryTime": trade.get("EntryTime"),
            "ExitTime": trade.get("ExitTime"),
            "Direction": "long" if is_long else "short",
            "EntryPrice": entry_price,
            "ExitPrice": float(trade["ExitPrice"]),
            "RealizedR": float(r) if pd.notna(r) else np.nan,
            "MFE_R": mfe_r,
            "MAE_R": mae_r,
            "ExitEfficiencyPct": exit_efficiency,
            "LossRealizationRatioPct": loss_realization,
            "EntrySlippagePct": entry_slippage,
        })

    return pd.DataFrame(rows, columns=_EXCURSION_COLUMNS)


def _scatter_block(label: str, x: pd.Series, y: pd.Series) -> str:
    lines = [label, "x,y"]
    lines += [f"{xv:.4f},{yv:.4f}" for xv, yv in zip(x, y)]
    return "\n".join(lines)


def write_excursion_report(strategy_name: str, excursions: pd.DataFrame) -> None:
    """Write logs/{slug}_mfe_mae.csv (one row per trade) and
    logs/{slug}_mfe_mae_summary.txt (aggregate stats + scatter coordinates).
    No-op if there are no valid trades to report."""
    if excursions.empty:
        return

    LOGS_DIR.mkdir(exist_ok=True)
    slug = slugify(strategy_name)

    out = excursions.reset_index(drop=True).copy()
    out.insert(0, "trade_id", out.index + 1)
    out["entry_date"] = pd.to_datetime(out["EntryTime"]).dt.date
    out["exit_date"] = pd.to_datetime(out["ExitTime"]).dt.date

    csv_columns = {
        "trade_id": "trade_id",
        "Symbol": "symbol",
        "entry_date": "entry_date",
        "exit_date": "exit_date",
        "Direction": "direction",
        "EntryPrice": "entry_price",
        "ExitPrice": "exit_price",
        "RealizedR": "realized_r",
        "MFE_R": "mfe_r",
        "MAE_R": "mae_r",
        "ExitEfficiencyPct": "exit_efficiency_pct",
        "LossRealizationRatioPct": "loss_realization_ratio_pct",
        "EntrySlippagePct": "entry_slippage_pct",
    }
    if "Symbol" not in out.columns:
        out["Symbol"] = ""
    csv_df = out[list(csv_columns)].rename(columns=csv_columns)
    csv_df.to_csv(LOGS_DIR / f"{slug}_mfe_mae.csv", index=False)

    winners = out[out["RealizedR"] > 0]
    losers = out[out["RealizedR"] < 0]
    exit_eff = winners["ExitEfficiencyPct"].dropna()
    loss_ratio = losers["LossRealizationRatioPct"].dropna()

    lines = [f"MFE/MAE summary: {strategy_name}", "=" * (18 + len(strategy_name))]
    lines.append(f"Trades: {len(out)}  (winners: {len(winners)}, losers: {len(losers)})")
    lines.append("")
    if not exit_eff.empty:
        lines.append(f"Exit efficiency (winners), mean:   {exit_eff.mean():.1f}%")
        lines.append(f"Exit efficiency (winners), median: {exit_eff.median():.1f}%")
        pct_low = (exit_eff < LOW_EXIT_EFFICIENCY_PCT).mean() * 100
        lines.append(f"Winners with exit efficiency < {LOW_EXIT_EFFICIENCY_PCT:.0f}%: {pct_low:.1f}%")
    else:
        lines.append("Exit efficiency (winners): no winning trades")
    lines.append("")
    if not loss_ratio.empty:
        lines.append(f"Loss realization ratio (losers), mean:   {loss_ratio.mean():.1f}%")
        lines.append(f"Loss realization ratio (losers), median: {loss_ratio.median():.1f}%")
        pct_high = (loss_ratio > HIGH_LOSS_REALIZATION_PCT).mean() * 100
        lines.append(f"Losers with loss realization ratio > {HIGH_LOSS_REALIZATION_PCT:.0f}%: {pct_high:.1f}%")
    else:
        lines.append("Loss realization ratio (losers): no losing trades")
    lines.append("")
    slippage = out["EntrySlippagePct"].dropna()
    if not slippage.empty:
        lines.append(f"Entry slippage distance, mean: {slippage.mean():.1f}% of signal bar range")
    lines.append("")

    flags = []
    if not exit_eff.empty and exit_eff.mean() > HIGH_EXIT_EFFICIENCY_SUSPECT_PCT:
        flags.append(
            f"Mean exit efficiency ({exit_eff.mean():.1f}%) is above "
            f"{HIGH_EXIT_EFFICIENCY_SUSPECT_PCT:.0f}% -- MFE may be computed from "
            "exit price instead of intrabar high/low. Check the bar window."
        )
    if not loss_ratio.empty and loss_ratio.mean() < LOW_LOSS_REALIZATION_SUSPECT_PCT:
        flags.append(
            f"Mean loss realization ratio ({loss_ratio.mean():.1f}%) is below "
            f"{LOW_LOSS_REALIZATION_SUSPECT_PCT:.0f}% -- MAE may be using the wrong bars."
        )
    if flags:
        lines.append("FLAGGED:")
        lines.extend(f"  - {f}" for f in flags)
        lines.append("")

    lines.append(_scatter_block(
        "Scatter 1 -- Winners: MFE_R (x) vs Realized R (y)",
        winners["MFE_R"], winners["RealizedR"],
    ))
    lines.append("")
    lines.append(_scatter_block(
        "Scatter 2 -- Losers: MAE_R (x) vs abs(Realized R) (y)",
        losers["MAE_R"], losers["RealizedR"].abs(),
    ))

    (LOGS_DIR / f"{slug}_mfe_mae_summary.txt").write_text("\n".join(lines) + "\n")
