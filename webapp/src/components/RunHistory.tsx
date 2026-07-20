import { Fragment, useState } from "react";
import type { HistoryRow } from "../api";
import { StatusPill } from "./StatusPill";

function fmtWhen(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function fmtParamValue(v: number | boolean | string): string {
  return String(v);
}

export function RunHistory({ rows }: { rows: HistoryRow[] }) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  if (rows.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--text-muted)" }}>
        No prior runs logged.
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
    <div
      className="overflow-x-auto rounded-lg border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <table className="w-full min-w-[760px] border-collapse text-sm">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
            {["Run at", "Window", "Trades", "Win Rate", "Expectancy (R)", "Profit Factor", "Sharpe", "Alpha", "Status", "Config"].map(
              (h) => (
                <th
                  key={h}
                  className="px-3 py-2 text-left font-medium whitespace-nowrap"
                  style={{ color: "var(--text-muted)" }}
                >
                  {h}
                </th>
              ),
            )}
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
                    {fmtWhen(r.runAt)}
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                    {r.startDate} → {r.endDate}
                  </td>
                  <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {r.tradesTaken}
                  </td>
                  <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {(r.winRate * 100).toFixed(1)}%
                  </td>
                  <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {r.expectancyR.toFixed(3)}
                  </td>
                  <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {r.profitFactor === null ? "∞" : r.profitFactor.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {r.sharpe === null ? "—" : r.sharpe.toFixed(2)}
                  </td>
                  <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {r.alphaPct === null ? "—" : `${r.alphaPct >= 0 ? "+" : ""}${r.alphaPct.toFixed(1)}%`}
                  </td>
                  <td className="px-3 py-2">
                    <StatusPill status={r.status} />
                  </td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <button
                      type="button"
                      onClick={() => toggleExpanded(i)}
                      className="text-xs font-medium underline-offset-2 hover:underline"
                      style={{ color: "var(--series-1)" }}
                    >
                      {isExpanded ? "Hide" : hasParams ? "Show (custom)" : "Show"}
                    </button>
                  </td>
                </tr>
                {isExpanded && (
                  <tr style={{ borderBottom: "1px solid var(--gridline)", background: "var(--surface-2, var(--surface-1))" }}>
                    <td colSpan={10} className="px-3 py-2">
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
  );
}
