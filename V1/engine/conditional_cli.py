"""Console runner for Conditional Edge Discovery.

    python -m engine.conditional_cli                          # the four starting strategies
    python -m engine.conditional_cli --strategy "Internal Bar Strength (IBS)"
    python -m engine.conditional_cli --full --consume-holdout --reason "..."
    python -m engine.conditional_cli --ledger

Prints the same numbers the API serves, in the order the research brief asks
for them: the search size first, then the sample, then the result. That order
is deliberate -- a conditional expectancy read before its hypothesis count and
its N is read without the two facts that decide what it means.
"""

from __future__ import annotations

import argparse
import json
import warnings
from typing import Any, Sequence

from engine import conditional_edge, conditional_ledger, conditional_stats


def _fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:+.{digits}f}" if abs(value) < 1000 else f"{value:,.1f}"
    return str(value)


def print_study(study: conditional_edge.ConditionalStudy) -> None:
    session = study.session
    split = study.split
    print("=" * 78)
    print(f"{study.strategy_name}  [{study.engine} engine]")
    print("=" * 78)
    print(f"Observations              : {len(study.observations)}")
    print(f"  discovery / validation / final holdout : "
          f"{len(split.discovery)} / {len(split.validation)} / "
          f"{split.to_dict()['finalHoldoutObservations']} (sealed)")
    print(f"Purged at boundaries      : "
          f"{split.boundaries.get('purgedFromDiscovery')} + "
          f"{split.boundaries.get('purgedFromValidation')} "
          f"(embargo {split.boundaries.get('embargoDays')}d)")
    print(f"Primary outcome           : {study.observations.outcome_label}")
    baseline = session.baseline
    print(f"Unconditional             : n={baseline['n']} mean={_fmt(baseline['mean'])} "
          f"median={_fmt(baseline['median'])} win={baseline['winRate']:.1%} "
          f"PF={_fmt(baseline['profitFactor'], 2)}")
    print(f"  95% CI                  : [{_fmt(baseline['ciLow'])}, {_fmt(baseline['ciHigh'])}]")
    print()
    print(f"Hypotheses examined       : {session.fdr.get('hypothesesTested')}  "
          f"(univariate {session.accounting.univariate_tested}, "
          f"pairwise {session.accounting.pairwise_tested}, "
          f"three-way {session.accounting.three_way_tested}, "
          f"suppressed for sample {session.accounting.suppressed_for_sample})")
    print(f"Raw significant (p<=0.05) : {session.fdr.get('rawSignificant')}")
    print(f"FDR significant (q<={session.fdr.get('alpha')})  : {session.fdr.get('fdrSignificant')}")
    for note in session.accounting.notes:
        print(f"  note: {note}")
    print()

    ranked = sorted(
        (u for u in session.univariate if u.permutation_p is not None),
        key=lambda u: u.permutation_p,
    )[:10]
    if ranked:
        print("Top univariate relationships (IN SAMPLE):")
        print(f"  {'feature':<32}{'group':<16}{'spread':>9}{'effect':>8}{'p':>9}{'q':>8}  flags")
        for result in ranked:
            flags = []
            if not result.pit_safe:
                flags.append("NOT-PIT")
            if not result.discovery_eligible:
                flags.append("exploratory")
            if result.fdr_significant:
                flags.append("FDR-SIG")
            if result.monotonic.get("strictlyMonotonic"):
                flags.append("monotonic")
            print(f"  {result.feature:<32}{result.group:<16}{result.spread:>+9.3f}"
                  f"{result.effect_size:>+8.2f}{result.permutation_p:>9.4f}"
                  f"{(result.q_value if result.q_value is not None else float('nan')):>8.3f}"
                  f"  {','.join(flags)}")
        print()

    interactions = sorted(
        (i for i in session.interactions if i.permutation_p is not None),
        key=lambda i: i.permutation_p,
    )[:6]
    if interactions:
        print("Top interactions (IN SAMPLE):")
        for interaction in interactions:
            best = interaction.best
            print(f"  {' x '.join(interaction.features)}  "
                  f"best cell {best.labels} n={best.n} mean={_fmt(best.mean)} "
                  f"vs baseline {_fmt(interaction.baseline_mean)} "
                  f"p={interaction.permutation_p:.4f} q={_fmt(interaction.q_value, 3)}"
                  f"{'  FDR-SIG' if interaction.fdr_significant else ''}")
            if interaction.cells_suppressed:
                print(f"      {interaction.cells_suppressed} cell(s) suppressed below the "
                      "minimum-observation floor")
        print()

    if session.redundancy.get("clusters"):
        print("Redundant feature clusters (|Spearman| >= "
              f"{session.redundancy['threshold']}):")
        for cluster in session.redundancy["clusters"][:6]:
            print(f"  - {', '.join(cluster)}")
        print()

    print(f"Candidate hypotheses      : {len(session.candidates)}  "
          "(CANDIDATE - NOT VALIDATED)")
    for candidate in session.candidates:
        print(f"  [{candidate.tier}] n={candidate.n_conditioned} "
              f"retention={candidate.retention_pct:.1f}% "
              f"mean={_fmt(candidate.conditional_mean)} "
              f"(baseline {_fmt(candidate.baseline_mean)}, "
              f"delta {_fmt(candidate.improvement)}) "
              f"p={_fmt(candidate.permutation_p, 4)} q={_fmt(candidate.q_value, 3)}")
        for condition in candidate.conditions:
            print(f"      - {condition.describe()}"
                  + (f"   [raw edge {condition.raw_edge:.4g}]" if condition.raw_edge is not None else ""))
        for warning in candidate.warnings:
            print(f"      ! {warning}")
    print()

    unavailable = [
        f for f in study.feature_availability["features"] if not f["available"]
    ]
    if unavailable:
        print("Features declared but NOT computed:")
        for feature in unavailable:
            print(f"  - {feature['key']}: {feature['unavailableReason']}")
        print()

    non_pit = [
        f for f in study.feature_availability["features"]
        if f["available"] and not f["pitSafe"]
    ]
    if non_pit:
        print(f"Not point-in-time (exploratory only): "
              f"{', '.join(f['key'] for f in non_pit)}")
    for warning in study.warnings:
        print(f"WARNING: {warning}")
    print()


def print_workflow(payload: dict[str, Any]) -> None:
    for entry in payload.get("notFrozen", []):
        print(f"NOT FROZEN: {entry['reason']}")
    for entry in payload.get("hypotheses", []):
        verdict = entry["verdict"]
        print("-" * 78)
        print(f"VERDICT: {verdict['verdict']}")
        for reason in verdict.get("reasons", []):
            print(f"  + {reason}")
        for blocker in verdict.get("blockers", []):
            print(f"  - {blocker}")
        validation = entry.get("validation")
        if validation:
            print(f"  validation: {validation['conclusion']}")
            walk = validation.get("walkForward") or {}
            if walk.get("usableFolds"):
                print(f"  walk-forward: {walk['positiveFolds']}/{walk['usableFolds']} folds "
                      f"positive, mean {_fmt(walk.get('meanImprovement'))}, "
                      f"worst {_fmt(walk.get('worstFoldImprovement'))}")
        holdout = entry.get("holdout")
        if holdout:
            print(f"  final holdout: {holdout['conclusion']}")
        conditioned = entry.get("conditioned")
        if conditioned and conditioned.get("available"):
            comparison = conditioned["comparison"]
            print(f"  retention: {comparison['retention']['conditionedSignals']} / "
                  f"{comparison['retention']['originalSignals']} signals "
                  f"({_fmt(comparison['retention']['retentionPct'], 1)}%)")
            print(f"  {'metric':<18}{'original':>14}{'conditioned':>14}")
            for key in ("trades", "expectancyR", "winRate", "profitFactor", "cagrPct",
                        "sharpe", "sortino", "maxDrawdownPct", "p95DrawdownPct",
                        "worstDayPct", "exposurePct"):
                print(f"  {key:<18}{_fmt(comparison['original'].get(key), 3):>14}"
                      f"{_fmt(comparison['conditioned'].get(key), 3):>14}")
            prop = conditioned.get("prop") or {}
            if prop.get("delta"):
                print(f"  prop ({prop['scenario']}): safe size "
                      f"{_fmt(prop['original']['headline']['safeRiskMultiplier'], 2)} -> "
                      f"{_fmt(prop['conditioned']['headline']['safeRiskMultiplier'], 2)}, "
                      f"expected net payout "
                      f"{_fmt(prop['original']['headline']['expectedNetPayout'], 0)} -> "
                      f"{_fmt(prop['conditioned']['headline']['expectedNetPayout'], 0)}")
        elif conditioned:
            print(f"  conditioned comparison unavailable: {conditioned['reason']}")
    overall = payload.get("verdict") or {}
    print("=" * 78)
    print(f"STRATEGY-LEVEL CONDITIONAL VERDICT: {overall.get('verdict')}")
    print(f"  hypotheses examined: {overall.get('hypothesesExamined')}, "
          f"raw significant: {overall.get('rawSignificant')}, "
          f"FDR significant: {overall.get('fdrSignificant')}")
    for blocker in overall.get("blockers", []):
        print(f"  - {blocker}")
    print()


def print_ledger(strategy: str | None) -> None:
    rows = conditional_ledger.ledger(strategy)
    if not rows:
        print("Research ledger is empty.")
        return
    print(f"{'id':>4}  {'strategy':<32}{'v':>2}  {'status':<20}{'frozen':<12}results")
    for row in rows:
        print(f"{row['row_id']:>4}  {row['strategy_name']:<32}{row['version']:>2}  "
              f"{row['status']:<20}{row['frozen_at'][:10]:<12}{len(row['results'])}")
        for condition in row["conditions"]:
            print(f"        - {condition.get('description', condition)}")
        if row["reason"]:
            print(f"        reason: {row['reason']}")
        if not row["integrity"]["intact"]:
            print("        !! CONTRACT HASH MISMATCH")
        if row["integrity"]["featureDriftDetected"]:
            print(f"        !! feature drift: {row['integrity']['featureDrift'][:2]}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Conditional Edge Discovery")
    parser.add_argument("--strategy", action="append", default=None,
                        help="Strategy name; repeatable. Defaults to the four starting strategies.")
    parser.add_argument("--full", action="store_true",
                        help="Run the full workflow (freeze + validate + conditioned + prop).")
    parser.add_argument("--consume-holdout", action="store_true",
                        help="Open the final holdout for hypotheses that pass validation. "
                             "Permanently recorded in the ledger.")
    parser.add_argument("--reason", default="", help="Reason recorded when consuming the holdout.")
    parser.add_argument("--models", action="store_true", help="Also fit the diagnostic models.")
    parser.add_argument("--permutations", type=int, default=1_000)
    parser.add_argument("--seed", type=int, default=conditional_stats.DEFAULT_SEED)
    parser.add_argument("--freeze-top", type=int, default=1)
    parser.add_argument("--no-persist", action="store_true",
                        help="Do not write to the research ledger.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a report.")
    parser.add_argument("--ledger", action="store_true", help="Print the research ledger and exit.")
    args = parser.parse_args(argv)

    warnings.filterwarnings("ignore")

    if args.ledger:
        print_ledger(args.strategy[0] if args.strategy else None)
        return 0

    strategies = args.strategy or list(conditional_edge.INITIAL_STRATEGIES)
    payloads: dict[str, Any] = {}
    for name in strategies:
        try:
            if args.full:
                payload = conditional_edge.full_workflow(
                    name, seed=args.seed, permutations=args.permutations,
                    freeze_top=args.freeze_top, consume_holdout=args.consume_holdout,
                    holdout_reason=args.reason, include_models=args.models,
                    persist=not args.no_persist,
                )
                payloads[name] = payload
                if not args.json:
                    study = conditional_edge.analyze  # noqa: F841 - report printed below
                    print(json.dumps(payload["study"]["split"], indent=1)) if False else None
                    _print_full(payload)
            else:
                study = conditional_edge.analyze(
                    name, seed=args.seed, permutations=args.permutations,
                    include_models=args.models, persist=not args.no_persist,
                )
                payloads[name] = study.to_dict()
                if not args.json:
                    print_study(study)
        except Exception as exc:  # noqa: BLE001 -- one strategy failing must not stop the rest
            payloads[name] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"{name}: FAILED -- {type(exc).__name__}: {exc}")
    if args.json:
        print(json.dumps(payloads, indent=1, default=str))
    return 0


def _print_full(payload: dict[str, Any]) -> None:
    study = payload["study"]
    session = study["session"]
    print("=" * 78)
    print(f"{study['strategyName']}  [{study['engine']} engine]")
    print("=" * 78)
    print(f"Observations {study['totalObservations']} | discovery "
          f"{study['split']['discoveryObservations']} / validation "
          f"{study['split']['validationObservations']} / holdout "
          f"{study['split']['finalHoldoutObservations']}")
    print(f"Hypotheses examined {session['fdr'].get('hypothesesTested')}, "
          f"raw significant {session['fdr'].get('rawSignificant')}, "
          f"FDR significant {session['fdr'].get('fdrSignificant')}")
    print_workflow(payload)


if __name__ == "__main__":
    raise SystemExit(main())
