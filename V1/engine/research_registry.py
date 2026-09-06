"""Thin metadata layer for the Research Status dashboard.

**This is not a second hypothesis ledger.** Hypothesis lifecycle verdicts
(validated, rejected, frozen) are computed live in `engine/research_status.py`
from the app's existing sources of truth -- `engine/conditional_ledger.py`,
`engine/dm_mrm_forward.py`, `engine/universe_registry.py`,
`engine/pit_all_stocks.py`. What lives here is the small amount of
INTERPRETIVE metadata that genuinely cannot be derived from a query: which
research question a strategy's Conditional Edge work was chasing, which
frozen markdown spec backs a hand-rolled forward test, and which downstream
research a currently-blocked dataset would unlock. It also maps final
full-pipeline calibration conclusions that the candidate/hypothesis ledger
has no row type capable of representing.

Ordinary note entries never change status. A
`CONDITIONAL_FINAL_INTERPRETATIONS` entry deliberately does: it is the
source-level mapping for a completed research-run conclusion, kept out of the
UI and applied only after a real persisted session exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StrategyModificationResearch:
    """A distinct research variant that must never shadow canonical strategy registration."""

    canonical_control: str
    preregistration_path: str
    result_ledger_path: str
    development_cutoff: str


REGISTERED_STRATEGY_MODIFICATIONS: dict[str, StrategyModificationResearch] = {
    "Dual Momentum - One-Rebalance Winner Grace v1": StrategyModificationResearch(
        canonical_control="Dual Momentum",
        preregistration_path="research/dm_winner_grace_v1_preregistration.json",
        result_ledger_path="research/dm_winner_grace_v1_ledger.json",
        development_cutoff="2026-08-22",
    ),
}


@dataclass(frozen=True)
class ConditionalResearchNote:
    """Per-strategy context for a Conditional Edge Discovery row that the
    ledger itself has no place to store (the ledger records WHAT was tested
    and WHAT happened; the original research QUESTION lives only in the
    conversation that preregistered it -- see CLAUDE.md and
    research/conditional_edge_batch2_preregistration.json)."""

    research_question: str


@dataclass(frozen=True)
class ConditionalFinalInterpretation:
    """Final, research-level interpretation not representable by the ledger.

    The ledger records candidates and freeze outcomes.  A full-pipeline null
    calibration compares the *adaptive search itself* with chance, so its
    conclusion belongs here as source metadata rather than being inferred
    from candidate effective N or hard-coded in the UI.
    """

    status: str
    primary_note: str
    secondary_note: str


CONDITIONAL_RESEARCH_NOTES: dict[str, ConditionalResearchNote] = {
    "Connors Mean Reversion (RSI2)": ConditionalResearchNote(
        "Is expectancy concentrated in a discoverable market/security state?",
    ),
    "Internal Bar Strength (IBS)": ConditionalResearchNote(
        "Is expectancy concentrated in a discoverable market/security state?",
    ),
    "Dual Momentum": ConditionalResearchNote(
        "Does market regime, breadth, or dispersion predict monthly portfolio performance?",
    ),
    "Market-Residual Momentum": ConditionalResearchNote(
        "Does market regime, breadth, or dispersion predict monthly portfolio performance?",
    ),
    "9/21 EMA Crossover": ConditionalResearchNote(
        "Is positive expectancy concentrated in particular trend/volatility/security-momentum "
        "states, or broadly distributed?",
    ),
    "Breakout from Consolidation": ConditionalResearchNote(
        "Do breakout outcomes depend materially on volatility, volume, prior trend, market "
        "regime, or relative strength in a way that survives OOS?",
    ),
    "Pullback to 21 EMA": ConditionalResearchNote(
        "Does the weak positive baseline expectancy contain a materially stronger conditional "
        "subset that survives validation?",
    ),
    "Oversold Bounce (RSI<30)": ConditionalResearchNote(
        "Is the stronger apparent expectancy stable enough to condition further, or too weak "
        "once effective-N and adaptive-search corrections are applied?",
    ),
}


# Final interpretation of the completed Batch-2 full-pipeline null
# calibration (150 cluster-preserving outcome shuffles; preregistered in
# research/conditional_edge_batch2_preregistration.json).  These strategies
# also failed the effective-N freeze gate, but that is now secondary context:
# the observed discovery output was itself unremarkable relative to the
# adaptive search pipeline's null behavior.
_BATCH2_NO_STRUCTURE = ConditionalFinalInterpretation(
    status="No Conditional Structure Detected",
    primary_note=(
        "The completed Batch-2 null calibration found no conditional discovery evidence "
        "distinguishable from the adaptive search process's null behavior."
    ),
    secondary_note="Candidate hypotheses also failed effective-N freeze requirements.",
)

CONDITIONAL_FINAL_INTERPRETATIONS: dict[str, ConditionalFinalInterpretation] = {
    strategy_name: _BATCH2_NO_STRUCTURE
    for strategy_name in (
        "9/21 EMA Crossover",
        "Breakout from Consolidation",
        "Pullback to 21 EMA",
        "Oversold Bounce (RSI<30)",
    )
}

#: Strategies whose Conditional Edge research is considered complete and
#: closed as of the 2026-08-22 dependence audit -- rejected/blocked
#: hypotheses here must never be re-surfaced as a fresh discovery.
COMPLETED_CONDITIONAL_RESEARCH: tuple[str, ...] = (
    "Connors Mean Reversion (RSI2)", "Internal Bar Strength (IBS)",
    "Dual Momentum", "Market-Residual Momentum",
    "9/21 EMA Crossover", "Breakout from Consolidation", "Pullback to 21 EMA",
    "Oversold Bounce (RSI<30)",
)

#: Preregistration path for the most recently completed batch. It remains as
#: provenance. `CURRENT_BATCH_STRATEGIES` is now empty; a future in-flight
#: batch can populate it and reuse `_batch_pending_row` without presenting
#: this completed batch as still running.
CURRENT_BATCH_PREREGISTRATION_PATH = "research/conditional_edge_batch2_preregistration.json"
CURRENT_BATCH_STRATEGIES: tuple[str, ...] = ()


@dataclass(frozen=True)
class DataBlockerNote:
    """Interpretive metadata for one data blocker that a registry/status
    query cannot answer on its own: what research it would unlock if
    installed, and how urgently that matters. Coverage/PIT-safety/
    missing-artifact facts are NOT duplicated here -- those come live from
    `engine/universe_registry.py` and `engine/pit_all_stocks.py`."""

    unlocks: tuple[str, ...]
    severity: str
    #: Research items (by name, matching a ResearchStatusRow's `name`) whose
    #: blocker this dataset IS -- the causal link section 6 of the dashboard
    #: brief asks for ("blocked because ... requires ... which is unavailable").
    blocks_research: tuple[str, ...] = field(default_factory=tuple)


DATA_BLOCKER_NOTES: dict[str, DataBlockerNote] = {
    "us_all_stocks_pit": DataBlockerNote(
        unlocks=(
            "Survivorship-free all-stocks strategy tests",
            "Longer-history conditional research (more independent rebalance/trade clusters)",
            "Robust delisting-aware momentum studies",
        ),
        severity="high",
        blocks_research=("Dual Momentum Conditional Edge", "Market-Residual Momentum Conditional Edge"),
    ),
    "sp500_pit": DataBlockerNote(
        unlocks=(
            "Long-history S&P 500 cross-sectional testing",
            "Stronger conditional research on broad-market cross-sectional strategies",
        ),
        severity="medium",
        blocks_research=("Dual Momentum Conditional Edge", "Market-Residual Momentum Conditional Edge"),
    ),
    "dow_pit": DataBlockerNote(
        unlocks=(),
        severity="low",
        blocks_research=(),
    ),
}
