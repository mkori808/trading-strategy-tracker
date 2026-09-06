"""Weighted Voting Ensemble -- combines five existing sub-strategies'
signals under a macro regime gate, weighting each sub-strategy by its own
rolling risk-adjusted performance rather than a static blend, and sizes the
resulting top-N picks by inverse-ATR risk parity.

Sub-strategies (real registered instances, not reimplementations -- see
engine/ensemble.py for how each one's native signal shape gets turned into
a comparable [0, 1] score):
    - Dual Momentum (cross-sectional; already produces a rebalance weight)
    - Post-Earnings Drift / PEAD (per-symbol; seeded with real earnings
      dates the same way engine/runner.py's _run_pead does)
    - Breakout from Consolidation (per-symbol, 20-day-high trigger)
    - Earnings Momentum / Gap-Hold (per-symbol; "Earnings Gap-Hold" in the
      brief -- this is the one registered strategy matching that name; see
      strategies/swing/earnings_momentum.py for why it's a price/volume
      proxy rather than a real earnings-calendar trigger)
    - Internal Bar Strength / IBS (per-symbol mean reversion)

Architecture note, a deliberate deviation from a from-scratch build: this
implements strategies.cross_sectional.CrossSectionalStrategy (the same
interface strategies/swing/dual_momentum.py already uses) instead of a new
"BaseStrategy" class, and plugs into the existing engine/cross_sectional.py
rebalance-driven backtest loop instead of a new Backtester. CLAUDE.md is
explicit that this project doesn't build a second/third engine when an
existing one doesn't fall short -- and for a "pick target weights at each
rebalance" strategy, it doesn't. See LESSONS.md for the full rationale and
run instructions.

Not wired into strategies/registry.py or the webapp dashboard, matching the
precedent already set for Dividend Hybrid: a structurally novel strategy
(here: a meta-strategy over other strategies, with its own weighting/sizing
axes no other strategy has) stays as directly-runnable engine/demo code
until it's proven out, rather than silently entering the Compare tab's
leaderboard next to ordinary single-rule strategies.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from engine import data as data_module
from engine.ensemble import (
    ACTIVE,
    atr_as_of,
    boolean_strategy_score,
    composite_scores,
    dual_momentum_scores,
    dynamic_weights,
    inverse_atr_weights,
    macro_regime,
    sub_strategy_rolling_sharpe,
    top_n_by_score,
)
from strategies.cross_sectional import CrossSectionalStrategy
from strategies.params import param_field
from strategies.swing.breakout_consolidation import BreakoutFromConsolidation
from strategies.swing.dual_momentum import DualMomentum
from strategies.swing.earnings_momentum import EarningsMomentumGapHold
from strategies.swing.internal_bar_strength import InternalBarStrength
from strategies.swing.pead import PostEarningsDrift

REGIME_BENCHMARK = "SPY"


@dataclass
class EnsembleWeightedVoting(CrossSectionalStrategy):
    name = "Weighted Voting Ensemble"
    timeframe = "1d"

    # Structural: the run window's real rate (matches DualMomentum's own
    # risk_free_rate field), used both for DualMomentum's absolute filter
    # and every sub-strategy's rolling Sharpe. Not a param_field -- it's
    # data the caller supplies from engine.data.risk_free_rate(), not a
    # rule a user tunes.
    risk_free_rate: float = 0.0

    top_n: int = param_field(
        6, label="Positions held", minimum=1, maximum=15, step=1,
        help="Top N tickers by composite conviction score, per rebalance.",
    )
    sharpe_window_days: int = param_field(
        63, label="Sub-strategy Sharpe window (trading days)", minimum=21, maximum=126, step=21,
        help="Rolling window used to compute each sub-strategy's own risk-adjusted weight.",
    )
    max_position_weight: float = param_field(
        0.20, label="Max weight per position", minimum=0.05, maximum=1.0, step=0.05,
        help="Hard cap on any single symbol's target portfolio weight.",
    )
    atr_period: int = param_field(
        14, label="Sizing ATR period", minimum=5, maximum=30, step=1,
        help="ATR period used for inverse-volatility position sizing.",
    )
    regime_sma_period: int = param_field(
        200, label="Regime SMA period (days)", minimum=100, maximum=300, step=10,
        help="SPY must close at/above its own SMA of this length to allow new long exposure.",
    )

    def _sub_strategies(self) -> dict[str, object]:
        """Fresh instances per call, at their registered defaults -- rebuilt
        every rebalance rather than cached on self, since PostEarningsDrift
        needs symbol-specific earnings seeding that isn't known until
        rebalance() sees the actual universe (see _pead_for)."""
        return {
            "Dual Momentum": DualMomentum(risk_free_rate=self.risk_free_rate),
            "Breakout from Consolidation": BreakoutFromConsolidation(),
            "Earnings Momentum / Gap-Hold": EarningsMomentumGapHold(),
            "Internal Bar Strength (IBS)": InternalBarStrength(),
            # PEAD handled separately per symbol -- see _pead_for.
        }

    @staticmethod
    def _pead_for(symbol: str) -> PostEarningsDrift:
        return PostEarningsDrift(data_module.positive_earnings_dates(symbol))

    def rebalance(
        self, universe_bars: dict[str, pd.DataFrame], as_of: pd.Timestamp
    ) -> dict[str, float]:
        # --- 1. Macro regime filter (the master switch) ---
        # Requires SPY's own bars to be present in universe_bars -- the
        # caller must include "SPY" in the symbols list passed to
        # engine.cross_sectional.run_cross_sectional_backtest for this
        # strategy to evaluate its regime gate; if it's missing, fail
        # DEFENSIVE (unknown regime gates capital off, not on) rather than
        # silently skipping the check.
        spy_bars = universe_bars.get(REGIME_BENCHMARK)
        if spy_bars is None or macro_regime(spy_bars.loc[:as_of], as_of, self.regime_sma_period) != ACTIVE:
            return {}  # DEFENSIVE: fully in cash (CrossSectionalStrategy's own "empty = cash" contract)

        # Trading universe excludes the regime benchmark itself -- SPY is a
        # regime input here, not a tradable candidate for this strategy.
        tradable = {s: b for s, b in universe_bars.items() if s != REGIME_BENCHMARK}
        if not tradable:
            return {}

        history = {s: b.loc[:as_of] for s, b in tradable.items()}
        close_df = pd.DataFrame({s: b["Close"] for s, b in history.items()}).sort_index().ffill()

        # --- 2. Per-sub-strategy signal standardization: S_i,j(t) in [0, 1] ---
        # (Long-only sub-strategies here never score negative -- see
        # engine/ensemble.py's module docstring on the [-1, +1] scale.)
        sub_scores: dict[str, dict[str, float]] = {}
        subs = self._sub_strategies()

        dual_mom = subs.pop("Dual Momentum")
        dm_weights = dual_mom.rebalance(history, as_of)
        sub_scores["Dual Momentum"] = dual_momentum_scores(dm_weights, dual_mom.top_n)

        for sub_name, strategy in subs.items():
            sub_scores[sub_name] = {
                symbol: boolean_strategy_score(strategy, bars)
                for symbol, bars in history.items()
            }

        sub_scores["Post-Earnings Drift (PEAD)"] = {
            symbol: boolean_strategy_score(self._pead_for(symbol), bars)
            for symbol, bars in history.items()
        }

        # --- 3. Dynamic ensemble weighting: rolling risk-adjusted Sharpe ---
        # W_i = max(0, Sharpe_i_63d) / sum(max(0, Sharpe_k_63d)) -- computed
        # from each sub-strategy's own trailing basket of whatever it
        # currently likes (see sub_strategy_rolling_sharpe's docstring for
        # why that's the faithful reading of "a sub-strategy's Sharpe" when
        # the sub-strategy itself has no equity curve of its own).
        sub_sharpe = {
            sub_name: sub_strategy_rolling_sharpe(
                scores, close_df, as_of, self.sharpe_window_days, self.risk_free_rate
            )
            for sub_name, scores in sub_scores.items()
        }
        weights = dynamic_weights(sub_sharpe)

        # --- 4. Composite conviction score per ticker ---
        # Score_j(t) = sum_i(W_i * S_i,j(t))
        scores = composite_scores(sub_scores, weights)

        # --- 5. Portfolio construction: top-N filter ---
        picks = top_n_by_score(scores, self.top_n)
        if not picks:
            return {}

        # --- 6. Risk-parity sizing: inverse-ATR, capped at max_position_weight ---
        atr_by_symbol = {
            symbol: atr_as_of(tradable[symbol], as_of, self.atr_period) for symbol in picks
        }
        return inverse_atr_weights(atr_by_symbol, picks, self.max_position_weight)
