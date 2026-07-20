import { Fragment, useState } from "react";
import type { PortfolioHistoryRow } from "../api";
import { StatusPill } from "./StatusPill";

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function fmtParamValue(v: number | boolean | string): string {
  return String(v);
}

/** Past runs for Dual Momentum / Pairs / Stat Arb, read from
 * engine/logging_db.py's portfolio_runs table -- these two engines don't
 * produce a discrete-trade result, so this is their counterpart to the
 * standard ResultTabs "History" tab. Rendered even when there's no
 * in-memory result for the current session (e.g. after navigating away and
 * back), which is the actual fix for "my run doesn't seem to get saved". */
export function PortfolioRunHistory({ rows }: { rows: PortfolioHistoryRow[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  if (rows.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--text-muted)" }}>
        No runs logged yet for this strategy.
      </p>
    );
  }

  const toggleExpanded = (i: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  return (
    <div>
      <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        Recent runs
      </div>
      <div
        className="overflow-x-auto rounded-lg border"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <table className="w-full min-w-[700px] border-collapse text-sm">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
              {["Run at", "Window", "Return", "vs SPY", "CAGR", "Sharpe", "Pair", "Status", "Config"].map((h) => (
                <th
                  key={h}
                  className="px-3 py-2 text-left font-medium whitespace-nowrap"
                  style={{ color: "var(--text-muted)" }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const isExpanded = expanded.has(i);
              const hasParams = Object.keys(r.params).length > 0;
              return (
                <Fragment key={i}>
                  <tr style={{ borderBottom: isExpanded ? "none" : "1px solid var(--gridline)" }}>
                    <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                      {fmtDate(r.runAt)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                      {r.startDate && r.endDate ? `${r.startDate} → ${r.endDate}` : "—"}
                    </td>
                    <td
                      className="px-3 py-2 tabular-nums"
                      style={{
                        color:
                          r.returnPct === null
                            ? "var(--text-secondary)"
                            : r.returnPct >= 0
                              ? "var(--status-good)"
                              : "var(--status-critical)",
                      }}
                    >
                      {r.returnPct === null ? "—" : `${r.returnPct >= 0 ? "+" : ""}${r.returnPct.toFixed(1)}%`}
                    </td>
                    <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                      {r.benchmarkReturnPct == null
                        ? "—"
                        : `${r.benchmarkReturnPct >= 0 ? "+" : ""}${r.benchmarkReturnPct.toFixed(1)}%`}
                    </td>
                    <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                      {r.cagrPct === null ? "—" : `${r.cagrPct.toFixed(2)}%`}
                    </td>
                    <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                      {r.sharpe === null ? "—" : r.sharpe.toFixed(2)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                      {r.pairSymbolA ? `${r.pairSymbolA} / ${r.pairSymbolB}` : "—"}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      {r.status ? (
                        <StatusPill status={r.status} />
                      ) : (
                        <span className="text-xs" style={{ color: "var(--text-muted)" }}>—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap">
                      <button
                        type="button"
                        onClick={() => toggleExpanded(i)}
                        className="text-xs font-medium underline-offset-2 hover:underline"
                        style={{ color: "var(--series-1)" }}
                      >
                        {isExpanded ? "Hide" : r.isCanonical ? "Show" : "Show (custom)"}
                      </button>
                    </td>
                  </tr>
                  {isExpanded && (
                    <tr style={{ borderBottom: "1px solid var(--gridline)", background: "var(--surface-2, var(--surface-1))" }}>
                      <td colSpan={9} className="px-3 py-2">
                        <div className="flex flex-col gap-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                          <div>
                            <span style={{ color: "var(--text-muted)" }}>
                              {r.isCanonical ? "Canonical run · " : "Experiment (Lab tab override) · "}
                            </span>
                            <span style={{ color: "var(--text-muted)" }}>Symbols ({r.symbols.length}): </span>
                            {r.symbols.length ? r.symbols.join(", ") : "—"}
                          </div>
                          <div>
                            <span style={{ color: "var(--text-muted)" }}>Rule parameters: </span>
                            {hasParams
                              ? Object.entries(r.params)
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
