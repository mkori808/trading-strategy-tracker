import { useState } from "react";
import type { BacktestOverrides, HistoryRow, ValidationReport } from "../api";
import { EdgeValidationPanel } from "./EdgeValidationPanel";
import { ExecutionSemantics } from "./RunConfigPanel";
import { StatusPill } from "./StatusPill";
import { allChecks, gateCounts, searchFamilyNote } from "./researchPresentation";

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
function power(report: ValidationReport | null | undefined, fallback: string | null | undefined): string {
  const check = allChecks(report ?? null).find((item) => item.key === "statistical_power");
  if (check?.status === "pass") return "Adequately powered";
  if (check?.status === "fail" || fallback?.toLowerCase().includes("underpowered")) return "Underpowered";
  if (check?.status === "warning" || check?.status === "unresolved") return "Power unresolved";
  return check?.status === "not_applicable" ? "Not applicable" : "MDA unavailable";
}

function paramLabel(key: string): string { return key.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function provenance(row: HistoryRow): string { return row.isPreregistered === true ? "Preregistered historical configuration" : row.isPreregistered === false ? "Exploratory historical configuration" : row.lifecycleStage?.replace(/_/g, " ") ?? (row.isCanonical ? "Registered-default historical run" : "Historical provenance not recorded"); }

export function RunEvidenceDetails({ row, currentOverrides, currentParams, registeredParams, onReplay }: { row: HistoryRow; currentOverrides?: BacktestOverrides; currentParams?: Record<string, number | boolean | string>; registeredParams?: Record<string, number | boolean | string>; onReplay: (row: HistoryRow) => void }) {
  const gates = gateCounts(row.validation ?? null);
  const defaults = registeredParams ?? {};
  const runParams = { ...defaults, ...row.params };
  const defaultChanges = Object.entries(runParams).filter(([key, value]) => key in defaults && defaults[key] !== value);
  const currentChanges = currentParams ? Object.entries(runParams).filter(([key, value]) => currentParams[key] !== value) : [];
  return <div className="border-t px-3 py-4" style={{ borderColor: "var(--gridline)", background: "var(--surface-2, var(--surface-1))" }}>
    <div className="grid gap-4 text-xs lg:grid-cols-3" style={{ color: "var(--text-secondary)" }}>
      <div><h4 className="mb-1 font-semibold" style={{ color: "var(--text-primary)" }}>Run identity</h4><div>Run #{row.id} · {new Date(row.runAt).toLocaleString()}</div><div>{provenance(row)}</div></div>
      <div><h4 className="mb-1 font-semibold" style={{ color: "var(--text-primary)" }}>Configuration</h4><div>{Object.keys(runParams).length ? Object.entries(runParams).map(([key, value]) => `${paramLabel(key)}: ${value}`).join(" · ") : "Parameter values were not persisted."}</div><div>Universe: {row.universeId ?? (row.symbols.length ? `${row.symbols.length} securities` : "registered default")}</div><div>Requested: {row.requestedStartDate ?? row.startDate} → {row.requestedEndDate ?? row.endDate}</div><div>Usable: {row.measuredStartDate ?? "not recorded"} → {row.measuredEndDate ?? "not recorded"}</div><div>Costs: slippage {row.slippageBps ?? "not recorded"} bps · commission {row.commissionBps ?? "not recorded"} bps</div></div>
      <div><h4 className="mb-1 font-semibold" style={{ color: "var(--text-primary)" }}>Research provenance</h4><div>{row.searchFamily ? `${row.searchFamily} · experiment ${row.familySearchNumber ?? "?"} of ${row.familySearchCount ?? "?"}` : "Search-family identifier not recorded"}</div><div>{row.isCanonical ? "Matched registered defaults when recorded" : "Did not carry canonical/default identity"}</div>{row.selectedAfterResults === true && <div style={{ color: "var(--status-warning)" }}>Historically selected · requires OOS confirmation</div>}</div>
    </div>
    {row.timing && <div className="mt-4"><ExecutionSemantics timing={row.timing} interval={row.interval} /></div>}
    <div className="mt-3 grid gap-3 sm:grid-cols-2"><div className="rounded-md border p-2 text-xs" style={{ borderColor: "var(--border)" }}><strong>Changes vs registered defaults</strong>{defaultChanges.length ? defaultChanges.map(([key, value]) => <div key={key}>{paramLabel(key)}: {String(defaults[key])} → {String(value)}</div>) : <div>Matches registered parameter defaults</div>}<div>Universe: registered default → {row.universeId ?? (row.symbols.length ? `${row.symbols.length} symbols` : "unchanged")}</div></div><div className="rounded-md border p-2 text-xs" style={{ borderColor: "var(--border)" }}><strong>Changes vs current configuration</strong>{currentParams ? currentChanges.length ? currentChanges.map(([key, value]) => <div key={key}>{paramLabel(key)}: {String(currentParams[key])} → {String(value)}</div>) : <div>Matches current parameters</div> : <div>Current parameter values unavailable</div>}<div>Universe: {currentOverrides?.universeId ?? (currentOverrides?.symbols ? `${currentOverrides.symbols.length} custom symbols` : "registered default")} → {row.universeId ?? (row.symbols.length ? `${row.symbols.length} symbols` : "registered default")}</div></div></div>
    <div className="mt-3 text-xs"><strong>Validation gates:</strong> {gates.label} · <strong>Benchmark gap:</strong> {row.benchmarkGapPct === null ? "—" : `${row.benchmarkGapPct.toFixed(1)}%`} · <strong>Sharpe:</strong> {row.sharpe?.toFixed(2) ?? "—"}</div>
    {!row.isCanonical && <button type="button" onClick={() => onReplay(row)} className="mt-3 rounded-md border px-2.5 py-1 text-xs" style={{ borderColor: "var(--border)" }}>Load configuration</button>}{row.validation && <div className="mt-4"><EdgeValidationPanel report={row.validation} showVerdict={false} /></div>}
  </div>;
}

export function RunHistory({ rows, onReplay, currentOverrides, currentParams, registeredParams }: { rows: HistoryRow[]; onReplay: (row: HistoryRow) => void; currentOverrides?: BacktestOverrides; currentParams?: Record<string, number | boolean | string>; registeredParams?: Record<string, number | boolean | string> }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  if (!rows.length) return <p className="text-sm" style={{ color: "var(--text-muted)" }}>No prior runs logged.</p>;
  const familyNote = searchFamilyNote(rows);
  const columns = "md:grid-cols-[100px_minmax(190px,1.5fr)_70px_95px_90px_120px_120px_70px]";
  return <div>
    {familyNote && <p className="mb-2 text-xs" style={{ color: "var(--text-muted)" }}>{familyNote}</p>}
    <div className="rounded-lg border" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
      <div className={`hidden gap-3 border-b px-3 py-2 text-xs font-medium md:grid ${columns}`} style={{ borderColor: "var(--gridline)", color: "var(--text-muted)" }}><span>Run</span><span>Window</span><span className="text-right">Trades</span><span className="text-right">Expectancy</span><span className="text-right">Profit factor</span><span title="Provenance of this individual historical run, independent of the strategy's current lifecycle.">Run provenance</span><span>Power</span><span /></div>
      {rows.map((row) => { const open = expanded.has(row.id); return <div key={row.id} className="border-b last:border-b-0" style={{ borderColor: "var(--gridline)" }}>
        <div className={`grid grid-cols-2 gap-3 px-3 py-3 text-sm md:items-center md:gap-3 ${columns}`}>
          <div><strong>Run #{row.id}</strong><div className="text-[10px]" style={{ color: "var(--text-muted)" }}>{day(row.runAt)}</div></div>
          <div><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Window<br /></span>{row.startDate} → {row.endDate}</div>
          <div className="md:text-right"><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Trades<br /></span>{row.tradesTaken}</div>
          <div className="md:text-right"><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Expectancy<br /></span>{row.expectancyR === null ? "—" : `${row.expectancyR.toFixed(3)} R`}</div>
          <div className="md:text-right"><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Profit factor<br /></span>{row.profitFactor === null ? "—" : row.profitFactor.toFixed(2)}</div>
          <div><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Run provenance<br /></span><StatusPill status={row.lifecycleStage?.replace(/_/g, " ") ?? (row.isCanonical ? "Preregistered" : "Exploratory")} /></div>
          <div><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Power<br /></span><StatusPill status={power(row.validation, row.edgeVerdict)} /></div>
          <button type="button" onClick={() => setExpanded((previous) => { const next = new Set(previous); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); return next; })} className="text-left text-xs font-medium md:text-right" style={{ color: "var(--series-1)" }}>{open ? "Hide" : "Inspect"}</button>
        </div>
        {open && <RunEvidenceDetails row={row} currentOverrides={currentOverrides} currentParams={currentParams} registeredParams={registeredParams} onReplay={onReplay} />}
      </div>; })}
    </div>
  </div>;
}
