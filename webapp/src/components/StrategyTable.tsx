import { Fragment, useState } from "react";
import type { StrategySummary } from "../api";
import { StatusPill } from "./StatusPill";

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function fmtNum(v: number | null, digits = 2): string {
  return v === null ? "—" : v.toFixed(digits);
}

function fmtPF(v: number | null): string {
  if (v === null) return "—";
  return v > 1000 ? "∞" : v.toFixed(2);
}

function fmtSignedPct(v: number): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

function fmtWindow(start: string | null, end: string | null): string {
  if (!start || !end) return "—";
  return `${start} → ${end}`;
}

function fmtParamValue(v: number | boolean | string): string {
  return typeof v === "number" ? String(v) : String(v);
}

export function StrategyTable({
  strategies,
  selected,
  onSelect,
}: {
  strategies: StrategySummary[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [showArchived, setShowArchived] = useState(false);

  const toggleExpanded = (name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const archivedCount = strategies.filter((s) => s.archived).length;
  const visible = showArchived ? strategies : strategies.filter((s) => !s.archived);

  return (
    <div>
      {archivedCount > 0 && (
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            {showArchived
              ? `Showing ${archivedCount} archived strategies alongside the active ones -- retired after a large-enough sample showed decisively negative results. Still fully runnable; see ARCHIVED_STRATEGIES.md.`
              : `${archivedCount} strategies archived (large-sample, decisively negative) -- hidden by default.`}
          </span>
          <button
            type="button"
            onClick={() => setShowArchived((v) => !v)}
            className="text-xs font-medium whitespace-nowrap underline-offset-2 hover:underline"
            style={{ color: "var(--series-1)" }}
          >
            {showArchived ? "Hide archived" : `Show archived (${archivedCount})`}
          </button>
        </div>
      )}
      <p className="mb-2 text-xs" style={{ color: "var(--text-muted)" }}>
        Scannable columns only -- click a row or "Details" for win rate, R-multiples,
        window, and full validation evidence.
      </p>
      <div
        className="overflow-x-auto rounded-lg border"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <table className="w-full min-w-[720px] border-collapse text-sm">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
              {[
                "Strategy",
                "Type",
                "Trades",
                "Sharpe (vs rf)",
                "Gap vs SPY",
                "Edge verdict",
                "",
              ].map((h) => (
                <th
                  key={h}
                  className="px-4 py-3 text-left font-medium whitespace-nowrap"
                  style={{ color: "var(--text-muted)" }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((s) => {
            const hasConfig = s.symbols.length > 0 || Object.keys(s.params).length > 0;
            const hasDetails = hasConfig || s.archived || (s.tradesTaken ?? 0) > 0 || s.engine !== "standard";
            const isExpanded = expanded.has(s.name);
            return (
              <Fragment key={s.name}>
                <tr
                  onClick={() => onSelect(s.name)}
                  className="cursor-pointer transition-colors"
                  style={{
                    borderBottom: isExpanded ? "none" : "1px solid var(--gridline)",
                    background: selected === s.name ? "var(--series-1-wash)" : undefined,
                    opacity: s.archived ? 0.6 : 1,
                  }}
                >
                  <td className="px-4 py-3 font-medium" style={{ color: "var(--text-primary)" }}>
                    {s.name}
                    {s.archived && (
                      <span
                        className="ml-2 rounded px-1.5 py-0.5 text-[10px] font-medium whitespace-nowrap"
                        style={{ color: "var(--text-muted)", background: "var(--surface-2, var(--surface-1))" }}
                      >
                        archived
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-3" style={{ color: "var(--text-secondary)" }}>
                    {s.kind}
                  </td>
                  <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {s.tradesTaken ?? "—"}
                  </td>
                  <td
                    className="px-4 py-3 tabular-nums"
                    style={{
                      color:
                        s.sharpe !== null && s.sharpe <= 0
                          ? "var(--status-critical)"
                          : "var(--text-secondary)",
                    }}
                  >
                    {fmtNum(s.sharpe)}
                  </td>
                  <td
                    className="px-4 py-3 tabular-nums"
                    style={{
                      color:
                        s.benchmarkGapPct !== null && s.benchmarkGapPct <= 0
                          ? "var(--status-critical)"
                          : "var(--text-secondary)",
                    }}
                  >
                    <span
                      title={
                        s.benchmarkWindowStart && s.benchmarkWindowEnd
                          ? `${s.benchmarkName} measured ${s.benchmarkWindowStart} to ${s.benchmarkWindowEnd} -- can move between runs, since a canonical run's end date defaults to "today"`
                          : `Cumulative return gap vs ${s.benchmarkName} over identical dates`
                      }
                    >
                      {s.benchmarkGapPct === null ? "—" : `${s.benchmarkGapPct >= 0 ? "+" : ""}${s.benchmarkGapPct.toFixed(1)}%`}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <StatusPill status={s.edgeVerdict ?? "Validation not recorded"} />
                    {s.lifecycleStage && (
                      <div className="mt-1 text-[10px] capitalize" style={{ color: "var(--text-muted)" }}>
                        {s.lifecycleStage.replace(/_/g, " ")}
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    {hasDetails && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleExpanded(s.name);
                        }}
                        className="text-xs font-medium underline-offset-2 hover:underline"
                        style={{ color: "var(--series-1)" }}
                      >
                        {isExpanded ? "Hide details" : "Details"}
                      </button>
                    )}
                  </td>
                </tr>
                {isExpanded && (
                  <tr
                    key={`${s.name}-config`}
                    style={{
                      borderBottom: "1px solid var(--gridline)",
                      background: "var(--surface-2, var(--surface-1))",
                    }}
                  >
                    <td colSpan={7} className="px-4 py-3">
                      <div className="flex flex-col gap-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                        {s.archived && (
                          <div style={{ color: "var(--status-warning)" }}>
                            Archived: {s.archivedReason}
                          </div>
                        )}
                        <div>
                          <span style={{ color: "var(--text-muted)" }}>Window: </span>
                          {fmtWindow(s.startDate, s.endDate)}
                        </div>
                        {s.engine === "standard" && (
                          <div>
                            <span style={{ color: "var(--text-muted)" }}>Win rate: </span>
                            {fmtPct(s.winRate)}
                            <span style={{ color: "var(--text-muted)" }}> · Avg win (R): </span>
                            {fmtNum(s.avgWinR)}
                            <span style={{ color: "var(--text-muted)" }}> · Avg loss (R): </span>
                            {fmtNum(s.avgLossR)}
                            <span style={{ color: "var(--text-muted)" }}> · Expectancy (R): </span>
                            {fmtNum(s.expectancyR, 3)}
                            <span style={{ color: "var(--text-muted)" }}> · Profit factor: </span>
                            {fmtPF(s.profitFactor)}
                          </div>
                        )}
                        {s.validation && (
                          <div>
                            <span style={{ color: "var(--text-muted)" }}>Validation: </span>
                            Signal evidence: {s.validation.verdict.signalEdge} · {s.validation.verdict.forwardTestWorthy
                              ? "eligible for paper execution"
                              : `paper execution blocked (${s.validation.verdict.blockers.join(" · ") || "required gates did not pass"})`}
                          </div>
                        )}
                        <div>
                          <span style={{ color: "var(--text-muted)" }}>Comparison basis: </span>
                          cumulative strategy return minus {s.benchmarkName} return over identical dates
                        </div>
                        {s.returnPct != null && (
                          <div>
                            <span style={{ color: "var(--text-muted)" }}>
                              {s.engine === "standard" ? "Shared-capital result: " : "Portfolio result: "}
                            </span>
                            {/* `!= null` (not `!==`): a stale API without these
                                fields sends undefined, which must render as
                                missing, not crash on .toFixed. */}
                            {s.returnPct != null ? `Return ${fmtSignedPct(s.returnPct)}` : "Return —"}
                            {s.benchmarkReturnPct != null
                              ? ` (SPY buy & hold same window: ${fmtSignedPct(s.benchmarkReturnPct)})`
                              : ""}
                            {s.cagrPct != null ? ` · CAGR ${s.cagrPct.toFixed(2)}%` : ""}
                            {s.maxDrawdownPct != null ? ` · Max DD ${s.maxDrawdownPct.toFixed(1)}%` : ""}
                            {s.engine !== "standard"
                              ? " · rebalancing portfolio engine — no discrete R-multiple trades"
                              : " · aggregate of simultaneous signals and cash"}
                          </div>
                        )}
                        <div>
                          <span style={{ color: "var(--text-muted)" }}>Symbols ({s.symbols.length}): </span>
                          {s.symbols.length ? s.symbols.join(", ") : "—"}
                        </div>
                        <div>
                          <span style={{ color: "var(--text-muted)" }}>Rule parameters: </span>
                          {Object.keys(s.params).length
                            ? Object.entries(s.params)
                                .map(([k, v]) => `${k}=${fmtParamValue(v)}`)
                                .join(",  ")
                            : "registered defaults (no overrides)"}
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
