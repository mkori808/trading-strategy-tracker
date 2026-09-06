import { useState } from "react";
import { api, type PortfolioHistoryRow, type ValidationReport } from "../api";
import { EdgeValidationPanel } from "./EdgeValidationPanel";
import { ProposeForwardExperimentDialog } from "./ProposeForwardExperimentDialog";
import { ProvenanceBadge } from "./ProvenanceBadge";
import { StatusPill } from "./StatusPill";
import { PROVENANCE_GROUP_ORDER, PROVENANCE_GROUP_TITLE, SELECTED_AFTER_SEARCH_DISCLOSURE, allChecks, classifyProvenance, gateCounts, groupByProvenance } from "./researchPresentation";

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
const pct = (value: number | null) => value === null ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(1)}%`;
function power(report: ValidationReport | null | undefined, fallback: string | null | undefined): string {
  const check = allChecks(report ?? null).find((item) => item.key === "statistical_power");
  if (check?.status === "pass") return "Adequately powered";
  if (check?.status === "fail" || fallback?.toLowerCase().includes("underpowered")) return "Underpowered";
  if (check?.status === "warning" || check?.status === "unresolved") return "Power unresolved";
  if (check?.status === "not_applicable") return "Not applicable";
  return "MDA unavailable";
}

function ReturnFigure({ value, deEmphasize }: { value: number | null; deEmphasize: boolean }) {
  return <span className={deEmphasize ? "text-xs font-normal" : "font-semibold"} style={{ color: deEmphasize ? "var(--text-muted)" : "var(--text-primary)" }}>{pct(value)}</span>;
}

function PortfolioRunRow({ row, open, onToggle, onReplay, onPropose, automatable, promoting, message, onEnableDirect }: {
  row: PortfolioHistoryRow;
  open: boolean;
  onToggle: () => void;
  onReplay: (row: PortfolioHistoryRow) => void;
  onPropose: (row: PortfolioHistoryRow) => void;
  automatable: boolean;
  promoting: number | null;
  message: { id: number; text: string; error: boolean } | null;
  onEnableDirect: (row: PortfolioHistoryRow) => void;
}) {
  const gates = gateCounts(row.validation ?? null);
  const provenance = classifyProvenance(row);
  const deEmphasize = provenance.group === "exploratory" || provenance.group === "selected_after_search";
  const columns = "md:grid-cols-[100px_minmax(190px,1.5fr)_90px_110px_75px_150px_120px_70px]";
  return <div className="border-b last:border-b-0" style={{ borderColor: "var(--gridline)" }}>
    <div className={`grid grid-cols-2 gap-3 px-3 py-3 text-sm md:items-center md:gap-3 ${columns}`}>
      <div><strong>Run #{row.id}</strong><div className="text-[10px]" style={{ color: "var(--text-muted)" }}>{day(row.runAt)}</div></div>
      <div><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Window<br /></span>{row.startDate && row.endDate ? `${row.startDate} → ${row.endDate}` : "—"}</div>
      <div className="md:text-right"><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Return<br /></span><ReturnFigure value={row.returnPct} deEmphasize={deEmphasize} /></div>
      <div className="md:text-right" title={`Strategy return minus ${row.benchmarkName} return over the same test window.`}><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Benchmark gap<br /></span><ReturnFigure value={row.benchmarkGapPct} deEmphasize={deEmphasize} /></div>
      <div className="md:text-right"><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Sharpe<br /></span>{row.sharpe?.toFixed(2) ?? "—"}</div>
      <div className="flex flex-wrap items-center gap-1.5"><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Provenance<br /></span><ProvenanceBadge badge={provenance.badge} /></div>
      <div><span className="md:hidden text-[10px] uppercase" style={{ color: "var(--text-muted)" }}>Power<br /></span><StatusPill status={power(row.validation, row.edgeVerdict)} /></div>
      <button type="button" onClick={onToggle} className="text-left text-xs font-medium md:text-right" style={{ color: "var(--series-1)" }}>{open ? "Hide" : "Inspect"}</button>
    </div>
    {open && <div className="border-t px-3 py-4" style={{ borderColor: "var(--gridline)", background: "var(--surface-2, var(--surface-1))" }}>
      <div className="grid gap-2 text-xs sm:grid-cols-2" style={{ color: "var(--text-secondary)" }}><div><strong>Configuration:</strong> {Object.keys(row.params).length ? Object.entries(row.params).map(([key, value]) => `${key}=${value}`).join(", ") : "registered defaults"}</div><div><strong>Validation:</strong> {gates.label}</div><div><strong>Drawdown:</strong> {pct(row.maxDrawdownPct)}</div><div><strong>CAGR:</strong> {pct(row.cagrPct)}</div></div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {!row.isCanonical && <button type="button" onClick={() => onReplay(row)} className="rounded-md border px-2.5 py-1 text-xs" style={{ borderColor: "var(--border)" }}>Load configuration</button>}
        {automatable && <button type="button" disabled={!row.validation} onClick={() => onPropose(row)} className="rounded-md px-2.5 py-1 text-xs text-white disabled:opacity-50" style={{ background: "var(--series-1)" }}>Propose for forward experiment</button>}
        {message?.id === row.id && <span className="text-xs" style={{ color: message.error ? "var(--status-critical)" : "var(--status-good)" }}>{message.text}</span>}
      </div>
      {automatable && <details className="mt-3"><summary className="cursor-pointer text-xs" style={{ color: "var(--text-muted)" }}>Advanced: enable live paper execution directly</summary>
        <div className="mt-2 rounded-md border px-3 py-2 text-xs" style={{ borderColor: "var(--status-warning)", color: "var(--text-secondary)" }}>
          Skips the forward-experiment review above and immediately assigns this run's exact configuration to live paper order placement, replacing whichever strategy currently owns the brokerage slot. Prefer "Propose for forward experiment" unless you specifically intend to change live execution right now.
          <div className="mt-2"><button type="button" disabled={!row.validation || promoting === row.id} onClick={() => onEnableDirect(row)} className="rounded-md border px-2.5 py-1 text-xs font-medium disabled:opacity-50" style={{ borderColor: "var(--status-warning)", color: "var(--status-warning)" }}>{promoting === row.id ? "Enabling…" : "Enable live paper execution now"}</button></div>
        </div>
      </details>}
      {row.validation ? <div className="mt-4"><EdgeValidationPanel report={row.validation} showVerdict={false} /></div> : <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>Validation was not stored for this run; this is not evidence of failure.</p>}
    </div>}
  </div>;
}

export function PortfolioRunHistory({ rows, onReplay, strategyName, automatable, onProposed }: { rows: PortfolioHistoryRow[]; onReplay: (row: PortfolioHistoryRow) => void; strategyName: string; automatable: boolean; onProposed?: () => void }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [promoting, setPromoting] = useState<number | null>(null);
  const [message, setMessage] = useState<{ id: number; text: string; error: boolean } | null>(null);
  const [proposing, setProposing] = useState<PortfolioHistoryRow | null>(null);
  if (!rows.length) return <p className="text-sm" style={{ color: "var(--text-muted)" }}>No runs logged yet for this strategy.</p>;

  const enableDirect = async (row: PortfolioHistoryRow) => {
    const passed = Boolean(row.validation?.verdict.forwardTestWorthy);
    if (!window.confirm(`Enable automated PAPER execution for "${strategyName}" using Run #${row.id}?${passed ? "" : " This run did not pass every validation gate."}`)) return;
    const policy = window.prompt("Type ADOPT or FLATTEN for the paper-account inception policy:", "ADOPT")?.trim().toLowerCase();
    if (policy !== "adopt" && policy !== "flatten") return;
    let override: { reason: string } | undefined;
    if (!passed) {
      const reason = window.prompt("Enter the logged reason for overriding the failed or unresolved evidence gates:")?.trim();
      if (!reason) return;
      override = { reason };
    }
    setPromoting(row.id); setMessage(null);
    try {
      await api.setExecutionConfig(strategyName, true, row.params, row.id, policy, override);
      setMessage({ id: row.id, text: `Paper execution enabled with ${policy} inception.`, error: false });
    } catch (error) { setMessage({ id: row.id, text: String(error), error: true }); }
    finally { setPromoting(null); }
  };

  const groups = groupByProvenance(rows);
  const columns = "md:grid-cols-[100px_minmax(190px,1.5fr)_90px_110px_75px_150px_120px_70px]";
  return <div>
    <ProposeForwardExperimentDialog
      open={proposing !== null}
      onClose={() => setProposing(null)}
      strategyName={strategyName}
      row={proposing}
      onProposed={() => { if (proposing) setMessage({ id: proposing.id, text: "Forward experiment created.", error: false }); setProposing(null); onProposed?.(); }}
    />
    {PROVENANCE_GROUP_ORDER.map((group) => {
      const groupRows = groups.get(group);
      if (!groupRows?.length && group !== "selected_after_search") return null;
      return <div key={group} className="mb-4 last:mb-0">
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>{PROVENANCE_GROUP_TITLE[group]}</h3>
        {group === "selected_after_search" && !groupRows?.length
          ? <p className="mb-2 text-xs italic" style={{ color: "var(--text-muted)" }}>{SELECTED_AFTER_SEARCH_DISCLOSURE}</p>
          : <div className="rounded-lg border" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
              <div className={`hidden gap-3 border-b px-3 py-2 text-xs font-medium md:grid ${columns}`} style={{ borderColor: "var(--gridline)", color: "var(--text-muted)" }}><span>Run</span><span>Window</span><span className="text-right">Return</span><span className="text-right">Benchmark gap</span><span className="text-right">Sharpe</span><span>Provenance</span><span>Power</span><span /></div>
              {(groupRows ?? []).map((row) => <PortfolioRunRow
                key={row.id}
                row={row}
                open={expanded.has(row.id)}
                onToggle={() => setExpanded((previous) => { const next = new Set(previous); if (next.has(row.id)) next.delete(row.id); else next.add(row.id); return next; })}
                onReplay={onReplay}
                onPropose={setProposing}
                automatable={automatable}
                promoting={promoting}
                message={message}
                onEnableDirect={enableDirect}
              />)}
            </div>}
      </div>;
    })}
  </div>;
}
