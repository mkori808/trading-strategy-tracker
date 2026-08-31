"""engine/conditional_edge.py: engine classification and the freezability
gate that stops a candidate from being frozen when it cannot possibly pass
validation given its own retention rate."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from engine import conditional_edge as edge
from engine import conditional_stats as stats
from engine.conditional_analysis import CandidateHypothesis, Condition

pytestmark = pytest.mark.filterwarnings("ignore")


def test_engine_for_classifies_cross_sectional_strategies():
    assert edge.engine_for("Dual Momentum") == "cross_sectional"
    assert edge.engine_for("Market-Residual Momentum") == "cross_sectional"
    assert edge.engine_for("52-Week-High Momentum") == "cross_sectional"


def test_engine_for_defaults_to_standard():
    assert edge.engine_for("Connors Mean Reversion (RSI2)") == "standard"
    assert edge.engine_for("Internal Bar Strength (IBS)") == "standard"


def _candidate(hypothesis_id: str, *, retention_pct: float, q_value, n_conditioned: int, improvement: float = 0.1) -> CandidateHypothesis:
    return CandidateHypothesis(
        hypothesis_id=hypothesis_id, strategy_name="S", engine="standard", outcome_column="realized_r",
        conditions=[Condition(feature="rsi2", op="<", value=30.0)], origin="test",
        n_conditioned=n_conditioned, n_total=1000, retention_pct=retention_pct,
        baseline_mean=0.02, conditional_mean=0.02 + improvement, improvement=improvement,
        ci_low=0.0, ci_high=0.2, win_rate=0.55, baseline_win_rate=0.5, profit_factor=1.2,
        effect_size=0.3, permutation_p=0.02, q_value=q_value, fdr_significant=False,
        tier="adequate", groups=["mean_reversion"], discovery_start="2022-01-01", discovery_end="2023-01-01",
    )


def _flat_frame(n: int) -> pd.DataFrame:
    """A minimal per-symbol-engine observation frame: `n` non-overlapping
    trades (one per cluster), so `effective_n == raw_n` and the legacy
    (pre-dependence-audit, `effective_n=None`) candidate fallback path in
    `rank_freezable` can still be exercised against a real frame."""
    entries = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.DataFrame({
        "entry_time": entries, "exit_time": entries + pd.Timedelta(days=1), "symbol": "AAPL",
    })


def _fake_study(candidates, validation_n: int) -> SimpleNamespace:
    session = SimpleNamespace(candidates=candidates)
    validation = SimpleNamespace(frame=_flat_frame(validation_n))
    discovery = SimpleNamespace(frame=_flat_frame(validation_n * 3))
    split = SimpleNamespace(validation=validation, discovery=discovery)
    return SimpleNamespace(session=session, split=split, engine="standard")


def test_rank_freezable_blocks_candidates_that_cannot_reach_the_validation_floor():
    # 2% retention on a 200-observation validation window projects to ~4
    # observations -- structurally unable to clear a 30-observation floor.
    # effective_n=None (as every pre-dependence-audit candidate has) falls
    # back to the original raw-row projection.
    tiny = _candidate("tiny", retention_pct=2.0, q_value=0.01, n_conditioned=20)
    healthy = _candidate("healthy", retention_pct=40.0, q_value=0.02, n_conditioned=400)
    study = _fake_study([tiny, healthy], validation_n=200)

    freezable, blocked = edge.rank_freezable(study, minimum_observations=30)

    assert [c.hypothesis_id for c in freezable] == ["healthy"]
    assert len(blocked) == 1
    assert blocked[0]["hypothesisId"] == "tiny"
    assert "cannot pass validation" in blocked[0]["reason"]


def test_rank_freezable_blocks_on_effective_n_even_when_raw_retention_looks_fine():
    # 40 raw rows but only 8 independent clusters (5 rows/cluster) -- the
    # exact scenario section 12 of the dependence audit calls out. Raw
    # retention (40%) alone would look comfortably above a 30-row floor;
    # effective N must block it anyway.
    clustered = _candidate(
        "clustered", retention_pct=40.0, q_value=0.01, n_conditioned=40,
    )
    clustered.effective_n = 8
    study = _fake_study([clustered], validation_n=100)  # 100 rows / 1-row clusters = 100 clusters

    freezable, blocked = edge.rank_freezable(study, minimum_observations=30)

    assert freezable == []
    assert len(blocked) == 1
    assert blocked[0]["hypothesisId"] == "clustered"
    assert "EFFECTIVE observations" in blocked[0]["reason"]


def test_rank_freezable_orders_by_q_value_then_n_then_effect():
    worse_q = _candidate("worse_q", retention_pct=50.0, q_value=0.5, n_conditioned=500)
    better_q = _candidate("better_q", retention_pct=50.0, q_value=0.01, n_conditioned=500)
    same_q_smaller_n = _candidate("same_q_smaller_n", retention_pct=50.0, q_value=0.01, n_conditioned=300)
    study = _fake_study([worse_q, same_q_smaller_n, better_q], validation_n=100)

    freezable, blocked = edge.rank_freezable(study, minimum_observations=1)

    assert blocked == []
    ids = [c.hypothesis_id for c in freezable]
    # better_q ties same_q_smaller_n on q-value but wins on larger n.
    assert ids == ["better_q", "same_q_smaller_n", "worse_q"]


def test_rank_freezable_handles_no_candidates():
    study = _fake_study([], validation_n=100)
    freezable, blocked = edge.rank_freezable(study)
    assert freezable == [] and blocked == []


def test_study_verdict_picks_the_best_ranked_outcome_across_hypotheses():
    from engine import conditional_validation as cv

    verdicts = [
        {"verdict": {"verdict": cv.VERDICT_WEAK, "reasons": [], "blockers": []}},
        {"verdict": {"verdict": cv.VERDICT_VALIDATED, "reasons": [], "blockers": []}},
    ]
    session = SimpleNamespace(
        fdr={"hypothesesTested": 42, "fdrSignificant": 3, "rawSignificant": 10},
        accounting=SimpleNamespace(total_examined=42),
    )
    split = SimpleNamespace(discovery=[None] * 300, validation=[None] * 100)
    study = SimpleNamespace(session=session, split=split)

    result = edge._study_verdict(study, verdicts)
    assert result["verdict"] == cv.VERDICT_VALIDATED
    assert result["hypothesesExamined"] == 42
    assert result["discoveryObservations"] == 300
    assert result["validationObservations"] == 100


def test_study_verdict_with_no_hypotheses_evaluated_reports_none():
    session = SimpleNamespace(fdr={"hypothesesTested": 0}, accounting=SimpleNamespace(total_examined=0))
    split = SimpleNamespace(discovery=[], validation=[])
    study = SimpleNamespace(session=session, split=split)
    result = edge._study_verdict(study, [])
    assert result["blockers"] == ["No hypothesis was evaluated."]
