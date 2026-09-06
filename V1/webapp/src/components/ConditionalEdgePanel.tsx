import { useEffect, useMemo, useState } from "react";
import {
  api,
  ApiError,
  type CandidateHypothesis,
  type ConditionalEvaluation,
  type ConditionalJob,
  type ConditionalStudy,
  type ConditionalWorkflowResult,
  type ConditionedComparison,
  type FrozenHypothesis,
  type InteractionResult,
  type LedgerEntry,
  type UnivariateResult,
} from "../api";
import { StatTile } from "./StatTile";

const CARD: React.CSSProperties = { borderColor: "var(--border)", background: "var(--surface-1)" };
const MUTED = { color: "var(--text-muted)" };
const SECONDARY = { color: "var(--text-secondary)" };

function fmt(value: number | null | undefined, digits = 3): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}
function fmtPlain(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return value.toFixed(digits);
}
function fmtPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}%`;
}

function tierColor(tier: string | undefined): string {
  switch (tier) {
    case "adequate":
      return "var(--status-good)";
    case "thin":
      return "var(--status-warning)";
    case "exploratory":
      return "var(--status-warning)";
    default:
      return "var(--status-critical)";
  }
}

/** Always visible above every discovery result -- see engine/conditional_edge.py's
 * DISCLOSURE constant, which this text is transcribed from. Every number below this
 * banner is in-sample by construction until it has been through freeze + validate. */
function DisclosureBanner({ study }: { study: ConditionalStudy }) {
  return (
    <div className="rounded-lg border px-4 py-3 text-xs" style={{ borderColor: "var(--status-warning)", color: "var(--status-warning)" }}>
      <div className="font-semibold">Candidate hypothesis — not validated</div>
      <p className="mt-1" style={{ color: "var(--text-secondary)" }}>{study.disclosure.inSample}</p>
      <p className="mt-1" style={{ color: "var(--text-secondary)" }}>{study.disclosure.multipleTesting}</p>
    </div>
  );
}

function SplitSummary({ study }: { study: ConditionalStudy }) {
  const s = study.split;
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatTile label="Discovery sample" value={String(s.discoveryObservations)} />
      <StatTile label="Validation sample (unseen)" value={String(s.validationObservations)} />
      <StatTile label="Final holdout (sealed)" value={s.finalHoldoutRevealed ? String(s.finalHoldoutObservations) : `${s.finalHoldoutObservations} 🔒`} />
      <StatTile label="Purge / embargo" value={`${s.embargoDays}d`} />
    </div>
  );
}

function AccountingLine({ study }: { study: ConditionalStudy }) {
  const fdr = study.session.fdr;
  const acc = study.session.accounting;
  return (
    <div className="rounded-lg border p-3 text-xs" style={CARD}>
      <div className="flex flex-wrap gap-x-6 gap-y-1">
        <span><strong>{fdr.hypothesesTested}</strong> hypotheses examined</span>
        <span style={MUTED}>({acc.univariateTested} univariate, {acc.pairwiseTested} pairwise, {acc.threeWayTested} three-way, {acc.suppressedForSample} suppressed for sample)</span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-6 gap-y-1">
        <span>Raw significant (p≤0.05): <strong>{fdr.rawSignificant}</strong></span>
        <span>FDR significant (q≤{fdr.alpha}): <strong style={{ color: fdr.fdrSignificant > 0 ? "var(--status-good)" : "var(--status-critical)" }}>{fdr.fdrSignificant}</strong></span>
      </div>
      {acc.notes.map((note, i) => (
        <div key={i} className="mt-1" style={MUTED}>{note}</div>
      ))}
    </div>
  );
}

/** Raw rows vs. INDEPENDENT clusters -- see engine/conditional_dependence.py.
 * Every p-value/q-value/CI shown elsewhere in this session is computed
 * against the effective count here, never the raw row count. Rendered
 * prominently so `N=172` is never read as though it were 172 independent
 * market-regime observations. */
function DependenceCard({ study }: { study: ConditionalStudy }) {
  const cs = study.session.clusterStructure;
  if (!cs || cs.method === "iid") return null;
  const collapsed = cs.dependencyChains && cs.dependencyChains.components < cs.nClusters / 4;
  return (
    <div className="rounded-lg border p-3 text-xs" style={{ borderColor: "var(--status-warning)" }}>
      <div className="mb-1 font-semibold" style={{ color: "var(--text-primary)" }}>
        Dependence structure — {cs.method === "rebalance_cluster" ? "rebalance clusters" : "overlap-based trade blocks"}
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <span>Raw observations<br /><strong>{cs.nRaw}</strong></span>
        <span>Independent clusters<br /><strong style={{ color: "var(--status-warning)" }}>{cs.effectiveN}</strong></span>
        <span>Cluster size (mean / median)<br /><strong>{fmtPlain(cs.clusterSizeMean, 1)} / {cs.clusterSizeMedian}</strong></span>
        {cs.blockLength !== null && <span>Block length<br /><strong>{cs.blockLength} trades</strong></span>}
        <span>Inference method<br /><strong>{cs.method}</strong></span>
      </div>
      {cs.dependencyChains && (
        <div className="mt-1" style={MUTED}>
          Overlap-dependency graph: {cs.dependencyChains.components} connected component(s)
          {collapsed && " — positions overlap almost continuously; the block-cluster count above estimates dependence LENGTH, not a claim of that many independent episodes."}
        </div>
      )}
      {cs.notes.map((n, i) => <div key={i} className="mt-1" style={MUTED}>{n}</div>)}
    </div>
  );
}

function BaselineCard({ study }: { study: ConditionalStudy }) {
  const b = study.session.baseline;
  return (
    <div className="rounded-lg border p-3 text-xs" style={CARD}>
      <div className="mb-2 font-semibold" style={{ color: "var(--text-primary)" }}>
        Unconditional {study.session.outcomeLabel}
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
        <span>n<br /><strong>{b.n}</strong></span>
        <span>Mean<br /><strong>{fmt(b.mean, 4)}</strong></span>
        <span>Win rate<br /><strong>{fmtPct(b.winRate * 100)}</strong></span>
        <span>Profit factor<br /><strong>{fmtPlain(b.profitFactor)}</strong></span>
        <span>95% CI<br /><strong>[{fmt(b.ciLow, 3)}, {fmt(b.ciHigh, 3)}]</strong></span>
      </div>
    </div>
  );
}

function UnivariateTable({ results }: { results: UnivariateResult[] }) {
  const ranked = useMemo(
    () => [...results].filter((r) => r.permutationP !== null).sort((a, b) => (a.permutationP ?? 1) - (b.permutationP ?? 1)).slice(0, 15),
    [results],
  );
  const [expanded, setExpanded] = useState<string | null>(null);
  if (!ranked.length) return <p className="text-xs" style={MUTED}>No feature cleared the minimum sample size.</p>;
  return (
    <div className="overflow-x-auto rounded-lg border" style={CARD}>
      <table className="w-full text-xs">
        <thead>
          <tr style={{ color: "var(--text-muted)" }}>
            <th className="p-2 text-left">Feature</th>
            <th className="p-2 text-left">Group</th>
            <th className="p-2 text-right">Spread</th>
            <th className="p-2 text-right">Effect (g)</th>
            <th className="p-2 text-right">p</th>
            <th className="p-2 text-right">q</th>
            <th className="p-2 text-right" title="Independent clusters behind the best bucket, vs. its raw row count">Eff. N</th>
            <th className="p-2 text-center">Monotonic</th>
            <th className="p-2 text-center">Flags</th>
          </tr>
        </thead>
        <tbody>
          {ranked.map((r) => (
            <>
              <tr
                key={r.feature}
                className="cursor-pointer border-t"
                style={{ borderColor: "var(--border)" }}
                onClick={() => setExpanded(expanded === r.feature ? null : r.feature)}
              >
                <td className="p-2 font-medium" style={{ color: "var(--text-primary)" }}>{r.label}</td>
                <td className="p-2" style={MUTED}>{r.group}</td>
                <td className="p-2 text-right tabular-nums">{fmt(r.spread)}</td>
                <td className="p-2 text-right tabular-nums">{fmt(r.effectSize, 2)}</td>
                <td className="p-2 text-right tabular-nums">{fmtPlain(r.permutationP, 4)}</td>
                <td className="p-2 text-right tabular-nums">{r.qValue !== null ? fmtPlain(r.qValue, 3) : "—"}</td>
                <td className="p-2 text-right tabular-nums" title={r.inferenceMethod}>
                  {r.effectiveN !== null ? r.effectiveN : "—"}
                </td>
                <td className="p-2 text-center">{r.monotonic.strictlyMonotonic ? "✓" : "–"}</td>
                <td className="p-2 text-center">
                  {r.fdrSignificant && <span className="mr-1 rounded px-1" style={{ background: "var(--status-good-bg)", color: "var(--status-good)" }}>FDR</span>}
                  {!r.pitSafe && <span className="mr-1 rounded px-1" style={{ background: "var(--status-critical-bg)", color: "var(--status-critical)" }}>NOT-PIT</span>}
                  {!r.discoveryEligible && <span className="rounded px-1" style={{ background: "var(--status-warning-bg)", color: "var(--status-warning)" }}>exploratory</span>}
                </td>
              </tr>
              {expanded === r.feature && (
                <tr style={{ borderColor: "var(--border)" }} className="border-t">
                  <td colSpan={9} className="p-3" style={{ background: "var(--page)" }}>
                    <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
                      {r.buckets.map((bucket) => (
                        <div key={bucket.label} className="rounded border p-2" style={{ borderColor: tierColor(r.tier) }}>
                          <div className="font-medium">{bucket.label}</div>
                          <div style={MUTED}>n={bucket.n}</div>
                          <div className="tabular-nums" style={{ color: bucket.mean >= 0 ? "var(--status-good)" : "var(--status-critical)" }}>
                            {fmt(bucket.mean, 4)}
                          </div>
                          <div style={MUTED}>win {fmtPct(bucket.winRate * 100, 0)}</div>
                          {bucket.lowEdge !== null && bucket.highEdge !== null && (
                            <div style={MUTED} className="text-[10px]">
                              [{bucket.lowEdge.toFixed(2)}, {bucket.highEdge.toFixed(2)}]
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                    {r.warnings.map((w, i) => (
                      <div key={i} className="mt-2 text-[11px]" style={{ color: "var(--status-warning)" }}>⚠ {w}</div>
                    ))}
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function InteractionList({ interactions }: { interactions: InteractionResult[] }) {
  const ranked = useMemo(
    () => [...interactions].filter((i) => i.permutationP !== null).sort((a, b) => (a.permutationP ?? 1) - (b.permutationP ?? 1)).slice(0, 8),
    [interactions],
  );
  if (!ranked.length) return <p className="text-xs" style={MUTED}>No interaction cleared the minimum sample size.</p>;
  return (
    <div className="space-y-2">
      {ranked.map((interaction, i) => (
        <div key={i} className="rounded-lg border p-3 text-xs" style={CARD}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-medium" style={{ color: "var(--text-primary)" }}>
              {interaction.labels.join(" × ")}
              {interaction.order >= 3 && <span className="ml-2 rounded px-1" style={{ background: "var(--pill-bg)" }}>3-way</span>}
            </div>
            <div style={MUTED}>
              p={fmtPlain(interaction.permutationP, 4)} q={interaction.qValue !== null ? fmtPlain(interaction.qValue, 3) : "—"}
              {interaction.fdrSignificant && <span className="ml-2 rounded px-1" style={{ background: "var(--status-good-bg)", color: "var(--status-good)" }}>FDR</span>}
            </div>
          </div>
          {interaction.best && (
            <div className="mt-1">
              Best cell <strong>{interaction.best.labels.join(", ")}</strong>: n={interaction.best.n}, mean {fmt(interaction.best.mean, 4)}
              {" "}vs. baseline {fmt(interaction.baselineMean, 4)}
            </div>
          )}
          {interaction.cellsSuppressed > 0 && (
            <div style={MUTED}>{interaction.cellsSuppressed} cell(s) suppressed below the minimum-observation floor.</div>
          )}
          {interaction.warnings.map((w, wi) => (
            <div key={wi} className="mt-1" style={{ color: "var(--status-warning)" }}>⚠ {w}</div>
          ))}
        </div>
      ))}
    </div>
  );
}

function RedundancyClusters({ study }: { study: ConditionalStudy }) {
  const clusters = study.session.redundancy.clusters;
  if (!clusters.length) return null;
  return (
    <div className="rounded-lg border p-3 text-xs" style={CARD}>
      <div className="mb-1 font-medium" style={{ color: "var(--text-primary)" }}>
        Redundant feature clusters (|Spearman| ≥ {study.session.redundancy.threshold})
      </div>
      <p className="mb-2" style={MUTED}>These likely measure the same underlying concept — treat them as one piece of evidence, not several.</p>
      {clusters.map((cluster, i) => (
        <div key={i}>{cluster.join(", ")}</div>
      ))}
    </div>
  );
}

/** One candidate hypothesis, with the freeze → validate → holdout → conditioned
 * ladder driven inline. Every mutating step writes to the research ledger. */
function CandidateCard({
  sessionId,
  candidate,
  onLedgerChanged,
}: {
  sessionId: string;
  candidate: CandidateHypothesis;
  onLedgerChanged: () => void;
}) {
  const [frozen, setFrozen] = useState<FrozenHypothesis | null>(null);
  const [validation, setValidation] = useState<ConditionalEvaluation | null>(null);
  const [holdout, setHoldout] = useState<ConditionalEvaluation | null>(null);
  const [conditioned, setConditioned] = useState<ConditionedComparison | null>(null);
  const [holdoutReason, setHoldoutReason] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    fn()
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)))
      .finally(() => setBusy(null));
  };

  const doFreeze = () =>
    run("freeze", async () => {
      const result = await api.freezeConditionalHypothesis({ sessionId, hypothesisId: candidate.hypothesisId });
      setFrozen(result);
      onLedgerChanged();
    });
  const doValidate = () =>
    run("validate", async () => {
      if (!frozen?.rowId) return;
      const result = await api.validateConditionalHypothesis(frozen.rowId, { sessionId });
      setValidation(result);
      onLedgerChanged();
    });
  const doHoldout = () =>
    run("holdout", async () => {
      if (!frozen?.rowId) return;
      const result = await api.consumeConditionalHoldout(frozen.rowId, { sessionId, reason: holdoutReason || "Validation passed; final confirmation." });
      setHoldout(result);
      onLedgerChanged();
    });
  const doConditioned = () =>
    run("conditioned", async () => {
      if (!frozen?.rowId) return;
      const result = await api.conditionalConditionedComparison(frozen.rowId, {});
      setConditioned(result);
    });

  return (
    <div className="rounded-lg border p-3 text-xs" style={CARD}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="font-semibold" style={{ color: "var(--text-primary)" }}>{candidate.origin}</div>
          <ul className="mt-1 ml-4 list-disc">
            {candidate.conditions.map((c, i) => (
              <li key={i}>
                {c.description}
                {c.rawEdge !== null && <span style={MUTED}> (raw quantile edge {c.rawEdge.toFixed(3)})</span>}
              </li>
            ))}
          </ul>
        </div>
        <span
          className="shrink-0 rounded-full px-2 py-1 text-[10px] font-semibold"
          style={{ background: tierColor(candidate.tier) + "22", color: tierColor(candidate.tier) }}
        >
          {candidate.tier}
        </span>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-6">
        <span>Raw n / Eff. N<br />
          <strong>{candidate.rawObservations ?? candidate.nConditioned}</strong>
          {candidate.effectiveN !== null && (
            <span style={{ color: candidate.effectiveN < 30 ? "var(--status-warning)" : undefined }}> / {candidate.effectiveN}</span>
          )}
        </span>
        <span>Retention<br /><strong>{fmtPct(candidate.retentionPct)}</strong></span>
        <span>Discovery mean<br /><strong style={{ color: candidate.conditionalMean >= 0 ? "var(--status-good)" : "var(--status-critical)" }}>{fmt(candidate.conditionalMean, 4)}</strong></span>
        <span>vs. baseline<br /><strong>{fmt(candidate.baselineMean, 4)}</strong></span>
        <span>p / q<br /><strong>{fmtPlain(candidate.permutationP, 4)} / {candidate.qValue !== null ? fmtPlain(candidate.qValue, 3) : "—"}</strong></span>
        <span title={candidate.inferenceMethod}>Inference<br /><strong>{candidate.inferenceMethod}</strong></span>
      </div>
      {candidate.warnings.map((w, i) => (
        <div key={i} className="mt-1" style={{ color: "var(--status-warning)" }}>⚠ {w}</div>
      ))}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-2" style={{ borderColor: "var(--border)" }}>
        {!frozen && (
          <button
            className="rounded-md border px-2 py-1 font-medium"
            style={{ borderColor: "var(--series-1)", color: "var(--series-1)" }}
            disabled={busy === "freeze"}
            onClick={doFreeze}
          >
            {busy === "freeze" ? "Freezing…" : "Freeze hypothesis"}
          </button>
        )}
        {frozen && !validation && (
          <button
            className="rounded-md border px-2 py-1 font-medium"
            style={{ borderColor: "var(--series-1)", color: "var(--series-1)" }}
            disabled={busy === "validate"}
            onClick={doValidate}
          >
            {busy === "validate" ? "Validating…" : "Validate on unseen data"}
          </button>
        )}
        {validation?.passed && !holdout && (
          <>
            <input
              className="rounded border px-2 py-1"
              style={{ borderColor: "var(--border)", background: "var(--page)" }}
              placeholder="Reason for opening the final holdout…"
              value={holdoutReason}
              onChange={(e) => setHoldoutReason(e.target.value)}
            />
            <button
              className="rounded-md border px-2 py-1 font-medium"
              style={{ borderColor: "var(--status-warning)", color: "var(--status-warning)" }}
              disabled={busy === "holdout" || !holdoutReason.trim()}
              onClick={doHoldout}
            >
              {busy === "holdout" ? "Opening…" : "Reveal final holdout (permanent)"}
            </button>
          </>
        )}
        {validation?.passed && (
          <button
            className="rounded-md border px-2 py-1 font-medium"
            style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
            disabled={busy === "conditioned"}
            onClick={doConditioned}
          >
            {busy === "conditioned" ? "Running…" : "Build conditioned strategy + Prop comparison"}
          </button>
        )}
        {error && <span style={{ color: "var(--status-critical)" }}>{error}</span>}
      </div>

      {validation && <EvaluationBlock title="Validation (unseen)" evaluation={validation} />}
      {holdout && <EvaluationBlock title="Final holdout (sealed until now)" evaluation={holdout} />}
      {conditioned && <ConditionedBlock comparison={conditioned} />}
    </div>
  );
}

function EvaluationBlock({ title, evaluation }: { title: string; evaluation: ConditionalEvaluation }) {
  return (
    <div className="mt-3 rounded-md border p-2" style={{ borderColor: evaluation.passed ? "var(--status-good)" : "var(--status-critical)" }}>
      <div className="flex items-center justify-between">
        <span className="font-medium">{title}</span>
        <span style={{ color: evaluation.passed ? "var(--status-good)" : "var(--status-critical)" }}>
          {evaluation.passed ? "PASSED" : "FAILED"}
        </span>
      </div>
      <p className="mt-1" style={SECONDARY}>{evaluation.conclusion}</p>
      <div className="mt-1 grid grid-cols-2 gap-2 sm:grid-cols-5">
        <span>n<br /><strong>{evaluation.observations}</strong></span>
        <span>Conditional<br /><strong>{fmt(evaluation.conditionalMean, 4)}</strong></span>
        <span>Baseline<br /><strong>{fmt(evaluation.baselineMean, 4)}</strong></span>
        <span>Improvement<br /><strong>{fmt(evaluation.improvement, 4)}</strong></span>
        <span>95% CI<br /><strong>[{fmt(evaluation.ciLow, 3)}, {fmt(evaluation.ciHigh, 3)}]</strong></span>
      </div>
      {evaluation.walkForward && evaluation.walkForward.usableFolds > 0 && (
        <div className="mt-2" style={MUTED}>
          Walk-forward: <strong>{evaluation.walkForward.positiveFolds}/{evaluation.walkForward.usableFolds}</strong> folds
          positive, mean {fmt(evaluation.walkForward.meanImprovement, 4)}, worst {fmt(evaluation.walkForward.worstFoldImprovement, 4)}
          {evaluation.walkForward.directionStable ? " — direction stable" : " — direction NOT stable"}
        </div>
      )}
      {evaluation.decomposition?.available && evaluation.decomposition.primaryMechanism && (
        <div className="mt-2" style={MUTED}>
          Primary mechanism: <strong>{evaluation.decomposition.primaryMechanism}</strong> — {evaluation.decomposition.note}
        </div>
      )}
      {evaluation.integrity && !evaluation.integrity.intact && (
        <div className="mt-2" style={{ color: "var(--status-critical)" }}>Contract hash mismatch — this rule is no longer the rule that was frozen.</div>
      )}
    </div>
  );
}

function ConditionedBlock({ comparison }: { comparison: ConditionedComparison }) {
  if (!comparison.available || !comparison.comparison) {
    return <div className="mt-3 text-[11px]" style={MUTED}>{comparison.reason}</div>;
  }
  const { original, conditioned, retention } = comparison.comparison;
  const rows: [string, keyof typeof original][] = [
    ["Trades", "trades"], ["Expectancy R", "expectancyR"], ["Win rate", "winRate"],
    ["Profit factor", "profitFactor"], ["CAGR %", "cagrPct"], ["Sharpe", "sharpe"],
    ["Sortino", "sortino"], ["Max DD %", "maxDrawdownPct"], ["95% DD %", "p95DrawdownPct"],
    ["Worst day %", "worstDayPct"], ["Exposure %", "exposurePct"],
  ];
  return (
    <div className="mt-3 rounded-md border p-2" style={{ borderColor: "var(--border)" }}>
      <div className="mb-1 font-medium">
        Signal retention: {retention.conditionedSignals} / {retention.originalSignals} ({fmtPct(retention.retentionPct ?? 0)})
      </div>
      <table className="w-full text-[11px]">
        <thead><tr style={MUTED}><th className="text-left">Metric</th><th className="text-right">Original</th><th className="text-right">Conditioned</th></tr></thead>
        <tbody>
          {rows.map(([label, key]) => (
            <tr key={key} className="border-t" style={{ borderColor: "var(--border)" }}>
              <td>{label}</td>
              <td className="text-right tabular-nums">{fmtPlain(original[key] as number | null, 3)}</td>
              <td className="text-right tabular-nums">{fmtPlain(conditioned[key] as number | null, 3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {comparison.prop?.original.available && comparison.prop.conditioned.available && (
        <div className="mt-2">
          <div className="mb-1 font-medium">Prop Account Analysis ({comparison.prop.scenario})</div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <span>Safe size<br /><strong>{fmtPlain(comparison.prop.original.headline?.safeRiskMultiplier ?? null, 2)}x → {fmtPlain(comparison.prop.conditioned.headline?.safeRiskMultiplier ?? null, 2)}x</strong></span>
            <span>12m survival<br /><strong>{fmtPct((comparison.prop.original.headline?.survival12m ?? 0) * 100)} → {fmtPct((comparison.prop.conditioned.headline?.survival12m ?? 0) * 100)}</strong></span>
            <span>Expected net payout<br /><strong>${fmtPlain(comparison.prop.original.headline?.expectedNetPayout ?? null, 0)} → ${fmtPlain(comparison.prop.conditioned.headline?.expectedNetPayout ?? null, 0)}</strong></span>
            <span>95% DD<br /><strong>{fmtPct(comparison.prop.original.headline?.p95DrawdownPct ?? 0)} → {fmtPct(comparison.prop.conditioned.headline?.p95DrawdownPct ?? 0)}</strong></span>
          </div>
        </div>
      )}
      {comparison.decomposition?.available && (
        <div className="mt-2" style={MUTED}>Primary mechanism: <strong>{comparison.decomposition.primaryMechanism}</strong></div>
      )}
    </div>
  );
}

function LedgerPanel({ entries }: { entries: LedgerEntry[] }) {
  if (!entries.length) return <p className="text-xs" style={MUTED}>No frozen hypotheses yet for this strategy.</p>;
  return (
    <div className="space-y-2">
      {entries.map((row) => (
        <div key={`${row.hypothesisKey}-${row.version}`} className="rounded-lg border p-3 text-xs" style={CARD}>
          <div className="flex items-center justify-between">
            <span className="font-medium">v{row.version} — {row.status}</span>
            <span style={MUTED}>frozen {row.frozenAt.slice(0, 10)}</span>
          </div>
          <ul className="mt-1 ml-4 list-disc">
            {row.conditions.map((c, i) => <li key={i}>{c.description}</li>)}
          </ul>
          {row.reason && <p className="mt-1" style={SECONDARY}>{row.reason}</p>}
          {!row.integrity.intact && <p style={{ color: "var(--status-critical)" }}>Contract hash mismatch.</p>}
        </div>
      ))}
    </div>
  );
}

/** Conditional Edge Discovery — the workspace panel for one selected strategy.
 * Discovery (in-sample) → candidate hypotheses → freeze → validate → optional
 * final holdout → conditioned strategy + Prop Account Analysis. Mirrors
 * engine/conditional_edge.py's workflow one step at a time so every mutating
 * action (freeze / validate / consume-holdout) is a deliberate click, not a
 * side effect of loading the page. */
export function ConditionalEdgePanel({ strategyName, engine }: { strategyName: string; engine: "standard" | "cross_sectional" | "pairs" }) {
  const [study, setStudy] = useState<ConditionalStudy | null>(null);
  const [workflow, setWorkflow] = useState<ConditionalWorkflowResult | null>(null);
  const [ledger, setLedger] = useState<LedgerEntry[]>([]);
  const [progress, setProgress] = useState<ConditionalJob<unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"explore" | "full">("explore");
  const [includeModels, setIncludeModels] = useState(false);
  const [consumeHoldout, setConsumeHoldout] = useState(false);
  const [holdoutReason, setHoldoutReason] = useState("");

  const refreshLedger = () => {
    api.conditionalLedger(strategyName).then((res) => setLedger(res.hypotheses)).catch(() => {});
  };

  useEffect(() => {
    setStudy(null);
    setWorkflow(null);
    setProgress(null);
    setError(null);
    refreshLedger();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyName]);

  const runExplore = () => {
    setError(null);
    setStudy(null);
    setWorkflow(null);
    setProgress(null);
    api
      .runConditionalDiscovery(strategyName, { permutations: 1000, includeModels }, setProgress)
      .then((result) => {
        setStudy(result);
        refreshLedger();
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)));
  };

  const runFull = () => {
    setError(null);
    setStudy(null);
    setWorkflow(null);
    setProgress(null);
    api
      .runConditionalWorkflow(strategyName, {
        permutations: 1000, includeModels, freezeTop: 1,
        consumeHoldout, holdoutReason: holdoutReason || undefined,
      }, setProgress)
      .then((result) => {
        setWorkflow(result);
        setStudy(result.study);
        refreshLedger();
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : String(err)));
  };

  if (engine === "pairs") {
    return (
      <p className="text-xs" style={MUTED}>
        Conditional Edge Discovery does not yet support the pairs/stat-arb engine.
      </p>
    );
  }

  const running = progress && (progress.status === "queued" || progress.status === "running");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        <div className="inline-flex rounded-md border p-0.5" style={{ borderColor: "var(--border)" }}>
          {(["explore", "full"] as const).map((m) => (
            <button
              key={m}
              className="rounded px-3 py-1 text-xs font-medium"
              style={mode === m ? { background: "var(--series-1)", color: "white" } : { color: "var(--text-secondary)" }}
              onClick={() => setMode(m)}
            >
              {m === "explore" ? "Explore (discovery only)" : "Full workflow"}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-xs" style={SECONDARY}>
          <input type="checkbox" checked={includeModels} onChange={(e) => setIncludeModels(e.target.checked)} />
          Fit diagnostic models
        </label>
        {mode === "full" && (
          <>
            <label className="flex items-center gap-1 text-xs" style={SECONDARY}>
              <input type="checkbox" checked={consumeHoldout} onChange={(e) => setConsumeHoldout(e.target.checked)} />
              Also reveal final holdout if validation passes
            </label>
            {consumeHoldout && (
              <input
                className="rounded border px-2 py-1 text-xs"
                style={{ borderColor: "var(--border)", background: "var(--page)" }}
                placeholder="Reason for opening the holdout…"
                value={holdoutReason}
                onChange={(e) => setHoldoutReason(e.target.value)}
              />
            )}
          </>
        )}
        <button
          className="rounded-md px-3 py-1.5 text-xs font-semibold text-white"
          style={{ background: "var(--series-1)" }}
          disabled={Boolean(running)}
          onClick={mode === "explore" ? runExplore : runFull}
        >
          {running ? "Running…" : mode === "explore" ? "Run discovery" : "Run full workflow"}
        </button>
      </div>

      {running && (
        <div className="rounded-lg border p-3 text-xs" style={CARD}>
          <div>{progress?.stage}</div>
          <div className="mt-1 h-2 overflow-hidden rounded-full" style={{ background: "var(--gridline)" }}>
            <div className="h-full rounded-full transition-[width]" style={{ width: `${progress?.progressPct ?? 2}%`, background: "var(--series-1)" }} />
          </div>
        </div>
      )}
      {error && <div className="rounded-md border p-2 text-xs" style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}>{error}</div>}

      {study && (
        <>
          <DisclosureBanner study={study} />
          <SplitSummary study={study} />
          <BaselineCard study={study} />
          <DependenceCard study={study} />
          <AccountingLine study={study} />
          {study.warnings.map((w, i) => (
            <div key={i} className="text-xs" style={{ color: "var(--status-warning)" }}>⚠ {w}</div>
          ))}

          {workflow ? (
            <div className="space-y-3">
              <div
                className="rounded-lg border p-3 text-sm font-semibold"
                style={{ borderColor: "var(--border)", color: workflow.verdict.verdict.includes("Validated") ? "var(--status-good)" : workflow.verdict.verdict.includes("No") ? "var(--status-critical)" : "var(--status-warning)" }}
              >
                {workflow.verdict.verdict}
              </div>
              {workflow.notFrozen.map((n, i) => (
                <div key={i} className="text-xs" style={MUTED}>Not frozen: {n.reason}</div>
              ))}
              {workflow.hypotheses.map((h, i) => (
                <div key={i} className="rounded-lg border p-3" style={CARD}>
                  <div className="mb-2 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{h.verdict.verdict}</div>
                  {h.verdict.reasons.map((r, ri) => <div key={ri} className="text-xs" style={{ color: "var(--status-good)" }}>+ {r}</div>)}
                  {h.verdict.blockers.map((b, bi) => <div key={bi} className="text-xs" style={{ color: "var(--status-critical)" }}>- {b}</div>)}
                  {h.validation && <EvaluationBlock title="Validation (unseen)" evaluation={h.validation} />}
                  {h.holdout && <EvaluationBlock title="Final holdout" evaluation={h.holdout} />}
                  {h.conditioned && <ConditionedBlock comparison={h.conditioned} />}
                </div>
              ))}
            </div>
          ) : (
            <>
              <section>
                <h3 className="mb-2 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Top univariate relationships (in-sample)</h3>
                <UnivariateTable results={study.session.univariate} />
              </section>
              <section>
                <h3 className="mb-2 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Interactions (in-sample)</h3>
                <InteractionList interactions={study.session.interactions} />
              </section>
              <RedundancyClusters study={study} />
              <section>
                <h3 className="mb-2 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                  Candidate hypotheses ({study.session.candidates.length}) — not validated
                </h3>
                <div className="space-y-3">
                  {study.session.candidates.map((c) => (
                    <CandidateCard
                      key={c.hypothesisId}
                      sessionId={study.session.sessionId}
                      candidate={c}
                      onLedgerChanged={refreshLedger}
                    />
                  ))}
                  {!study.session.candidates.length && (
                    <p className="text-xs" style={MUTED}>
                      No interpretable rule cleared the sample-size and interpretability rules. This is a valid
                      finding: {strategyName} shows no discoverable conditional structure in this sample.
                    </p>
                  )}
                </div>
              </section>
              {study.models.length > 0 && (
                <section>
                  <h3 className="mb-2 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Diagnostic models</h3>
                  <div className="space-y-2">
                    {study.models.map((m, i) => (
                      <div key={i} className="rounded-lg border p-3 text-xs" style={CARD}>
                        <div className="font-medium">{m.model} — {m.scoreName} {fmtPlain(m.meanScore, 3)}</div>
                        <div style={MUTED}>Permutation importance (higher = more useful out-of-fold):</div>
                        {Object.entries(m.permutationImportance).sort((a, b) => b[1] - a[1]).slice(0, 6).map(([k, v]) => (
                          <div key={k}>{k}: {fmt(v, 4)}</div>
                        ))}
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </>
          )}
        </>
      )}

      <section>
        <h3 className="mb-2 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Research ledger — {strategyName}</h3>
        <LedgerPanel entries={ledger} />
      </section>
    </div>
  );
}
