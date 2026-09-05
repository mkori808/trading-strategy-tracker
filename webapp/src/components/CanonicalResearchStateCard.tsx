import type { GovernedForwardExperiment, StrategySummary } from "../api";
import { ProvenanceBadge } from "./ProvenanceBadge";
import { StatusPill } from "./StatusPill";
import { allChecks, powerSummary, primaryBlocker, type PowerSummary } from "./researchPresentation";

function Field({ label, children, title }: { label: string; children: React.ReactNode; title?: string }) {
  return (
    <div title={title}>
      <span className="block text-[11px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>{label}</span>
      <div className="mt-0.5 text-sm font-medium" style={{ color: "var(--text-primary)" }}>{children}</div>
    </div>
  );
}

function holdoutStatus(strategy: StrategySummary): { label: string; passed: boolean | null } {
  const check = allChecks(strategy.validation).find((item) => item.key === "chronological_oos");
  if (!check) return { label: "Not yet run", passed: null };
  if (check.status === "pass") return { label: "Passed", passed: true };
  if (check.status === "fail") return { label: "Did not pass", passed: false };
  if (check.status === "not_applicable") return { label: "Not applicable", passed: null };
  return { label: "Unresolved", passed: null };
}

function forwardTestStatus(strategy: StrategySummary, experiments: GovernedForwardExperiment[]): string {
  const active = experiments.find((experiment) => experiment.status === "running" || experiment.status === "forward_validated");
  if (active) {
    const days = active.status === "running" ? ` · ${active.observationCount} observation${active.observationCount === 1 ? "" : "s"} logged` : "";
    return `${active.status === "forward_validated" ? "Forward-validated" : "Running"}${days}`;
  }
  const falsified = experiments.find((experiment) => experiment.status === "falsified");
  if (falsified) return "Falsified — suspended";
  if (strategy.validation?.verdict.forwardTestWorthy) return "Eligible, not yet proposed";
  return "Not eligible";
}

function conclusion(strategy: StrategySummary, power: PowerSummary, experiments: GovernedForwardExperiment[]): { text: string; tone: "neutral" | "warning" | "positive" } {
  const verdict = strategy.validation?.verdict;
  if (verdict?.productionCapitalWorthy) return { text: "Forward-validated at the frozen minimum horizon. Still paper-only — this is not authorization to deploy real capital.", tone: "positive" };
  const falsified = experiments.some((experiment) => experiment.status === "falsified");
  if (falsified) return { text: "The forward experiment was falsified against its own preregistered criteria; paper automation was automatically suspended.", tone: "warning" };
  if (strategy.implementationStatus === "unavailable") return { text: "No production conclusion yet — required data is unavailable for this strategy.", tone: "warning" };
  if (power.label === "Underpowered") return { text: "No production conclusion yet — statistical confidence is insufficient to resolve the registered target edge.", tone: "warning" };
  if (experiments.some((experiment) => experiment.status === "running")) return { text: "No production conclusion yet — a forward experiment is running against frozen criteria.", tone: "neutral" };
  return { text: "No production conclusion yet.", tone: "neutral" };
}

const TONE_COLOR: Record<"neutral" | "warning" | "positive", string> = {
  neutral: "var(--border)",
  warning: "var(--status-warning)",
  positive: "var(--status-good)",
};

export function CanonicalResearchStateCard({ strategy, lifecycle, forwardExperiments }: { strategy: StrategySummary; lifecycle: string; forwardExperiments: GovernedForwardExperiment[] }) {
  const power = powerSummary(strategy);
  const blocker = primaryBlocker(strategy);
  const holdout = holdoutStatus(strategy);
  const gap = strategy.benchmarkGapPct;
  const configSummary = strategy.symbols.length
    ? `${strategy.symbols.length} securities · ${strategy.startDate ?? "?"} → ${strategy.endDate ?? "?"}`
    : `Registered default · ${strategy.startDate ?? "?"} → ${strategy.endDate ?? "?"}`;
  const verdict = conclusion(strategy, power, forwardExperiments);

  return (
    <section className="rounded-lg border p-4" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>Canonical Research State</h2>
        <div className="flex flex-wrap gap-2"><ProvenanceBadge badge="CANONICAL" title="This card describes the strategy's registered configuration and current evidence, independent of any sandbox edits below." /><StatusPill status={lifecycle} /></div>
      </div>

      <div className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
        <Field label="Canonical configuration">{configSummary}</Field>
        <Field label="Historical benchmark result" title={`Strategy return minus ${strategy.benchmarkName} return over the tested window. Purely descriptive -- statistical confidence is a separate field below.`}>
          {gap === null ? "Not recorded" : `${gap >= 0 ? "+" : "−"}${Math.abs(gap).toFixed(1)} pp vs ${strategy.benchmarkName}`}
        </Field>
        <Field label="Statistical confidence" title={power.detail ?? undefined}>
          {power.label === "Adequately powered" ? "Sufficient" : power.label === "Underpowered" ? "Insufficient" : power.label}
        </Field>
        <Field label="Holdout status">{holdout.label}</Field>
        <Field label="Forward-test status">{forwardTestStatus(strategy, forwardExperiments)}</Field>
        <Field label="Primary blocker(s)">{blocker.label}</Field>
      </div>

      {blocker.detail && <p className="mt-3 text-xs" style={{ color: "var(--text-secondary)" }}><strong style={{ color: "var(--text-primary)" }}>Why:</strong> {blocker.detail}</p>}

      <div className="mt-4 rounded-md border-l-4 px-3 py-2 text-sm" style={{ borderColor: TONE_COLOR[verdict.tone], background: "var(--page)", color: "var(--text-primary)" }}>
        {verdict.text}
      </div>
    </section>
  );
}
