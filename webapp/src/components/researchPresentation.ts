import type { HistoryRow, StrategySummary, ValidationCheck, ValidationReport } from "../api";

export function searchFamilyNote(rows: HistoryRow[]): string | null {
  const count = Math.max(0, ...rows.map((row) => row.familySearchCount ?? row.validation?.research?.familySearchCount ?? 0));
  return count > 0 ? `Search family: ${count} recorded preregistered experiments. No selection-after-results claim is recorded.` : null;
}

export interface PowerSummary {
  label: string;
  mdaPct: number | null;
  targetPct: number | null;
  detail: string | null;
}

export function allChecks(report: ValidationReport | null): ValidationCheck[] {
  return report?.dimensions.flatMap((dimension) => dimension.checks) ?? [];
}

export function powerSummary(strategy: StrategySummary): PowerSummary {
  const check = allChecks(strategy.validation).find((item) => item.key === "statistical_power");
  const details = check?.details ?? {};
  const numberFrom = (...keys: string[]) => {
    for (const key of keys) if (typeof details[key] === "number") return details[key] as number;
    return null;
  };
  const verdictMatch = strategy.edgeVerdict?.match(/MDA\s+([\d.]+)%.*?(?:exceeds|target)\s+([\d.]+)%/i);
  const mdaPct = numberFrom("selectedMdaPct", "mdaPct", "mda_pct") ?? (verdictMatch ? Number(verdictMatch[1]) : null);
  const targetPct = numberFrom("minimumTradableAlphaPct", "tradableAlphaPct", "tradable_alpha_pct") ?? (verdictMatch ? Number(verdictMatch[2]) : null);
  let label = "MDA unavailable";
  if (check?.status === "pass") label = "Adequately powered";
  else if (check?.status === "fail" || strategy.edgeVerdict?.toLowerCase().includes("underpowered")) label = "Underpowered";
  else if (check?.status === "warning" || check?.status === "unresolved") label = "Power unresolved";
  else if (check?.status === "not_applicable") label = "Not applicable";
  const detail = mdaPct === null ? null : `MDA ${mdaPct.toFixed(2)}%/yr${targetPct === null ? "" : ` · target ${targetPct.toFixed(2)}%/yr`}`;
  return { label, mdaPct, targetPct, detail };
}

export function gateCounts(report: ValidationReport | null): { passed: number; failed: number; unresolved: number; label: string } {
  const checks = allChecks(report).filter((check) => check.required);
  const passed = checks.filter((check) => check.status === "pass" || check.status === "not_applicable").length;
  const failed = checks.filter((check) => check.status === "fail").length;
  const unresolved = checks.filter((check) => check.status === "warning" || check.status === "unresolved").length;
  return { passed, failed, unresolved, label: checks.length ? `${passed} passed · ${failed} failed · ${unresolved} unresolved` : "Validation not recorded" };
}

export function primaryBlocker(strategy: StrategySummary): { label: string; detail: string | null } {
  if (strategy.implementationStatus === "unavailable") return { label: "Required data unavailable", detail: strategy.unavailableReason ?? null };
  const checks = allChecks(strategy.validation);
  const power = powerSummary(strategy);
  if (checks.some((check) => check.key === "statistical_power" && check.status === "fail") || power.label === "Underpowered") {
    return {
      label: "Insufficient statistical power",
      detail: power.mdaPct === null ? "The current design cannot resolve the registered target edge." : `Current history can resolve effects of roughly ${power.mdaPct.toFixed(1)}%/yr or larger${power.targetPct === null ? "." : `; the research target is ${power.targetPct.toFixed(1)}%/yr.`}`,
    };
  }
  if (checks.some((check) => check.key === "beats_spy" && check.status === "fail")) return { label: "Benchmark superiority not established", detail: `The strategy did not establish superiority over ${strategy.benchmarkName}.` };
  const pit = checks.find((check) => check.key === "pit_membership" && check.status !== "pass");
  if (pit) return { label: "Point-in-time universe limitation", detail: pit.summary };
  const blocker = strategy.validation?.verdict.blockers[0];
  return { label: blocker ? "Validation requirements not met" : "No required blocker recorded", detail: blocker ?? null };
}

export function benchmarkEvidence(strategy: StrategySummary): string {
  if (strategy.benchmarkGapPct === null) return "Not recorded";
  const gap = `${strategy.benchmarkGapPct >= 0 ? "+" : "−"}${Math.abs(strategy.benchmarkGapPct).toFixed(1)} pp vs ${strategy.benchmarkName}`;
  const beats = allChecks(strategy.validation).find((check) => check.key === "beats_spy");
  return `${gap}${beats?.status === "pass" ? " · superiority supported" : " · superiority not established"}`;
}
