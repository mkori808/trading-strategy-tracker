import { useEffect, useRef, useState } from "react";
import { api, type DataBlockerRow, type ForwardStackStatus, type ResearchStatusDashboard, type ResearchStatusRow } from "../api";
import { useResource } from "../useResource";
import { KEYS } from "../resourceKeys";
import { StatTile } from "./StatTile";

const CARD: React.CSSProperties = { borderColor: "var(--border)", background: "var(--surface-1)" };
const MUTED = { color: "var(--text-muted)" };
const SECONDARY = { color: "var(--text-secondary)" };

/** Status -> color, grouped by what the label MEANS rather than its exact
 * text, so a future added label degrades to a sensible bucket instead of
 * falling through to "no color at all." */
function statusColor(status: string): string {
  if (status === "Validated") return "var(--status-good)";
  if (status === "Methodology Complete") return "var(--status-good)";
  if (status.startsWith("Forward Testing") || status === "Too Early to Evaluate") return "var(--series-1)";
  if (status === "Research Run In Progress") return "var(--series-1)";
  if (status === "No Conditional Structure Detected") return "var(--text-muted)";
  if (status === "Discovery Only" || status === "Historical Candidate" || status === "Audit Passed" || status === "Frozen") {
    return "var(--status-warning)";
  }
  if (status.startsWith("Blocked") || status === "Data Required") return "var(--status-warning)";
  if (status === "Validation Failed" || status === "Rejected") return "var(--status-critical)";
  return "var(--text-muted)";
}

function StatusPill({ status }: { status: string }) {
  const color = statusColor(status);
  return (
    <span
      className="inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold"
      style={{ color, background: `${color}22` }}
    >
      {status}
    </span>
  );
}

function severityColor(severity: string): string {
  if (severity === "high") return "var(--status-critical)";
  if (severity === "medium") return "var(--status-warning)";
  return "var(--text-muted)";
}

/** One research-branch row, expandable for notes/causal chain -- kept as a
 * single wide row rather than a card grid, since the whole point is to
 * compare status/blocker/next-action across many branches at a glance. */
function ResearchRow({ row, focused = false, forward }: { row: ResearchStatusRow; focused?: boolean; forward?: ForwardStackStatus | null }) {
  const [expanded, setExpanded] = useState(focused);
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "start" });
  }, [focused]);
  return (
    <div ref={ref} className="border-t" style={{ borderColor: focused ? "var(--series-1)" : "var(--border)", background: focused ? "var(--series-1-wash)" : undefined }}>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full flex-col gap-1 p-3 text-left text-xs hover:bg-black/[0.02]"
      >
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="font-medium" style={{ color: "var(--text-primary)" }}>{row.name}</span>
            <span style={MUTED}>{row.researchType}</span>
          </div>
          <StatusPill status={row.status} />
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-1" style={SECONDARY}>
          {row.evidenceStage && <span>Stage: <strong>{row.evidenceStage}</strong></span>}
          <span>Last action: {row.lastCompletedAction}</span>
          {row.developmentPeriod && <span>Development: {row.developmentPeriod}</span>}
          {row.forwardTestStart && <span>Forward start: {row.forwardTestStart}</span>}
          {row.holdoutStatus && <span>Holdout: {row.holdoutStatus}</span>}
        </div>
        {row.methodologySuperseded && (
          <div className="rounded px-2 py-1 text-[11px]" style={{ background: "var(--status-warning-bg, transparent)", color: "var(--status-warning)" }}>
            ⚠ Superseded — computed under an earlier methodology; see notes for the corrected evidence.
          </div>
        )}
        <div className="mt-1 flex items-start gap-2">
          <span style={MUTED}>Next action:</span>
          <span style={{ color: "var(--text-primary)" }}>{row.nextAction}</span>
        </div>
      </button>
      {expanded && (
        <div className="space-y-2 border-t px-3 py-2 text-xs" style={{ borderColor: "var(--border)", background: "var(--page)" }}>
          {(() => {
            const key = row.name.startsWith("Canonical Dual") ? "dm" : row.name === "Market-Residual Momentum" ? "mrm" : row.name.startsWith("Fixed 50/50") ? "fiftyFifty" : row.name.startsWith("DM/MRM Volatility") ? "volScaled" : null;
            const series = forward?.series.find((item) => item.key === key);
            return series ? (
              <div className="rounded border p-2" style={{ borderColor: "var(--gridline)" }}>
                <strong>Normalized forward evidence:</strong> {series.sessions} session{series.sessions === 1 ? "" : "s"} · NAV {series.nav?.toFixed(2) ?? "—"} · return {series.returnPct == null ? "—" : `${series.returnPct.toFixed(2)}%`}.
                <div style={MUTED}>Research shadow only; no Alpaca orders. SPY is the normalized benchmark series.</div>
              </div>
            ) : null;
          })()}
          {row.name.startsWith("DM/MRM Volatility") && forward?.currentBlend && (
            <div className="rounded border p-2" style={{ borderColor: "var(--gridline)" }}>
              <strong>Current frozen target:</strong> DM {(forward.currentBlend.dmTargetWeight * 100).toFixed(1)}% / MRM {(forward.currentBlend.mrmTargetWeight * 100).toFixed(1)}%
              <div style={MUTED}>Last reset {forward.currentBlend.lastWeightResetDate ?? "—"}; next reset {forward.currentBlend.nextScheduledReset ?? "—"}.</div>
            </div>
          )}
          {row.primaryBlocker && (
            <div><span style={MUTED}>Primary blocker: </span>{row.primaryBlocker}</div>
          )}
          {row.causalChain.length > 0 && (
            <div>
              <div style={MUTED}>Why this is blocked:</div>
              <ol className="ml-4 list-decimal">
                {row.causalChain.map((step, i) => <li key={i}>{step}</li>)}
              </ol>
            </div>
          )}
          {row.methodologyVersion && (
            <div><span style={MUTED}>Methodology version: </span><code>{row.methodologyVersion}</code></div>
          )}
          {row.validationStatus && (
            <div><span style={MUTED}>Validation status: </span>{row.validationStatus}</div>
          )}
          {row.notes.length > 0 && (
            <div>
              <div style={MUTED}>Notes:</div>
              <ul className="ml-4 list-disc">
                {row.notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DataBlockerCard({ blocker }: { blocker: DataBlockerRow }) {
  return (
    <div className="rounded-lg border p-3 text-xs" style={CARD}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium" style={{ color: "var(--text-primary)" }}>{blocker.dataset}</span>
        <span
          className="rounded-full px-2 py-0.5 text-[11px] font-semibold"
          style={{ color: severityColor(blocker.severity), background: `${severityColor(blocker.severity)}22` }}
        >
          {blocker.severity} priority
        </span>
      </div>
      <div className="mt-1" style={SECONDARY}>{blocker.status}</div>
      {blocker.intendedCoverage && (
        <div className="mt-1">
          <span style={MUTED}>Declared coverage: </span>{blocker.intendedCoverage}
        </div>
      )}
      <div className="mt-1">
        <span style={MUTED}>Actual: </span>{blocker.actualAvailability}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4">
        <span>PIT-safe: <strong>{blocker.pitSafe === null ? "—" : blocker.pitSafe ? "Yes" : "No"}</strong></span>
        <span>Survivorship-free: <strong>{blocker.survivorshipFree === null ? "—" : blocker.survivorshipFree ? "Yes" : "No"}</strong></span>
      </div>
      {blocker.missingArtifacts.length > 0 && (
        <div className="mt-1">
          <span style={MUTED}>Missing artifacts: </span>{blocker.missingArtifacts.join(", ")}
        </div>
      )}
      {blocker.unlocks.length > 0 && (
        <div className="mt-2">
          <div style={MUTED}>Unlocks if installed:</div>
          <ul className="ml-4 list-disc">
            {blocker.unlocks.map((u, i) => <li key={i}>{u}</li>)}
          </ul>
        </div>
      )}
      {blocker.blocksResearch.length > 0 && (
        <div className="mt-1" style={MUTED}>
          Blocks: {blocker.blocksResearch.join(", ")}
        </div>
      )}
    </div>
  );
}

/** Research Status + Data Blocker dashboard. Organizational only -- every
 * number here is a fast read of already-persisted state (the conditional-
 * edge ledger, the frozen DM/MRM forward-test protocol, universe/PIT
 * status). Never triggers a backtest or changes a verdict. */
export function ResearchStatusView({ focusName }: { focusName?: string | null } = {}) {
  const status = useResource<ResearchStatusDashboard>(KEYS.researchStatus, () => api.researchStatus());
  const blockers = useResource<{ blockers: DataBlockerRow[] }>(KEYS.researchDataBlockers, () =>
    api.researchDataBlockers(),
  );
  const forward = useResource<ForwardStackStatus>(KEYS.researchForwardStack, () => api.researchForwardStack());

  const rows = status.data?.rows ?? [];
  const summary = status.data?.summary;

  return (
    <div className="space-y-5">
      {status.error && (
        <div className="rounded-md border p-2 text-xs" style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}>
          {status.error}
        </div>
      )}

      {summary && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
          <StatTile label="Active research runs" value={String(summary.activeResearchRuns)} />
          <StatTile label="Frozen forward-test candidates" value={String(summary.frozenForwardTestCandidates)} />
          <StatTile label="Validated strategies" value={String(summary.validatedStrategies)} />
          <StatTile label="Rejected hypotheses" value={String(summary.rejectedHypotheses)} />
          <StatTile label="Data-blocked research items" value={String(summary.dataBlockedResearchItems)} />
          <StatTile label="Methodology-complete systems" value={String(summary.methodologyCompleteSystems)} />
        </div>
      )}

      <section>
        <h3 className="mb-2 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Forward testing
        </h3>
        <div className="overflow-x-auto rounded-lg border" style={CARD}>
          <table className="w-full text-left text-xs">
            <thead style={MUTED}>
              <tr><th className="p-2">Series</th><th className="p-2">Type</th><th className="p-2">Status</th><th className="p-2 text-right">Sessions</th><th className="p-2 text-right">NAV</th><th className="p-2 text-right">Return</th><th className="p-2 text-right">Drawdown</th><th className="p-2">Last update</th></tr>
            </thead>
            <tbody>
              {(forward.data?.series ?? []).map((row) => (
                <tr key={row.key} className="border-t" style={{ borderColor: "var(--border)" }}>
                  <td className="p-2 font-medium">{row.series}</td><td className="p-2" style={MUTED}>{row.type}</td><td className="p-2"><StatusPill status={row.status} /></td>
                  <td className="p-2 text-right">{row.sessions}</td><td className="p-2 text-right">{row.nav == null ? "—" : row.nav.toFixed(2)}</td>
                  <td className="p-2 text-right">{row.returnPct == null ? "—" : `${row.returnPct.toFixed(2)}%`}</td><td className="p-2 text-right">{row.drawdownPct == null ? "—" : `${row.drawdownPct.toFixed(2)}%`}</td><td className="p-2">{row.lastUpdate ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {forward.loading && <div className="p-3 text-xs" style={MUTED}>Loading forward state…</div>}
        </div>
        {forward.error && <div className="mt-1 text-xs" style={{ color: "var(--status-critical)" }}>{forward.error}</div>}
        {forward.data?.currentBlend && (
          <div className="mt-2 rounded-lg border p-3 text-xs" style={CARD}>
            <div><strong>Frozen vol-scaled state:</strong> DM {(forward.data.currentBlend.dmWeight * 100).toFixed(1)}% / MRM {(forward.data.currentBlend.mrmWeight * 100).toFixed(1)}%</div>
            <div style={MUTED}>Last reset {forward.data.currentBlend.lastWeightResetDate ?? "—"}; next scheduled reset {forward.data.currentBlend.nextScheduledReset ?? "—"}</div>
            <div className="mt-1" style={SECONDARY}>Combined holdings: {Object.entries(forward.data.currentBlend.combinedHoldings).map(([s, w]) => `${s} ${(w * 100).toFixed(1)}%`).join(", ") || "—"}</div>
          </div>
        )}
        <div className="mt-2 text-[11px]" style={MUTED}>Alpaca paper account equity and normalized strategy forward NAV are separate measures. {forward.data?.maturity.label}</div>
        {(forward.data?.alerts ?? []).map((alert) => <div key={alert.code} className="mt-1 text-xs" style={{ color: "var(--status-critical)" }}>{alert.message}</div>)}
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Research branches
        </h3>
        <div className="rounded-lg border" style={CARD}>
          {status.loading && !rows.length ? (
            <div className="p-3 text-xs" style={MUTED}>Loading research status…</div>
          ) : rows.length ? (
            rows.map((row) => <ResearchRow key={row.name} row={row} focused={row.name === focusName} forward={forward.data} />)
          ) : (
            <div className="p-3 text-xs" style={MUTED}>No tracked research branches.</div>
          )}
        </div>
      </section>

      <section>
        <h3 className="mb-2 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Data blockers
        </h3>
        {blockers.error && (
          <div className="text-xs" style={{ color: "var(--status-critical)" }}>{blockers.error}</div>
        )}
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
          {(blockers.data?.blockers ?? []).map((b) => (
            <DataBlockerCard key={b.dataset} blocker={b} />
          ))}
        </div>
      </section>
    </div>
  );
}
