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

  const toggleExpanded = (name: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  return (
    <div
      className="overflow-x-auto rounded-lg border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <table className="w-full min-w-[980px] border-collapse text-sm">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
            {[
              "Strategy",
              "Type",
              "Window",
              "Trades",
              "Win Rate",
              "Avg Win (R)",
              "Avg Loss (R)",
              "Expectancy (R)",
              "Profit Factor",
              "Sharpe (vs rf)",
              "Alpha",
              "Status",
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
          {strategies.map((s) => {
            const hasConfig = s.symbols.length > 0 || Object.keys(s.params).length > 0;
            const isExpanded = expanded.has(s.name);
            return (
              <Fragment key={s.name}>
                <tr
                  onClick={() => onSelect(s.name)}
                  className="cursor-pointer transition-colors"
                  style={{
                    borderBottom: isExpanded ? "none" : "1px solid var(--gridline)",
                    background: selected === s.name ? "var(--series-1-wash)" : undefined,
                  }}
                >
                  <td className="px-4 py-3 font-medium" style={{ color: "var(--text-primary)" }}>
                    {s.name}
                  </td>
                  <td className="px-4 py-3" style={{ color: "var(--text-secondary)" }}>
                    {s.kind}
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                    {fmtWindow(s.startDate, s.endDate)}
                  </td>
                  <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {s.tradesTaken ?? "—"}
                  </td>
                  <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {fmtPct(s.winRate)}
                  </td>
                  <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {fmtNum(s.avgWinR)}
                  </td>
                  <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {fmtNum(s.avgLossR)}
                  </td>
                  <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {fmtNum(s.expectancyR, 3)}
                  </td>
                  <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                    {fmtPF(s.profitFactor)}
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
                        s.alphaPct !== null && s.alphaPct <= 0
                          ? "var(--status-critical)"
                          : "var(--text-secondary)",
                    }}
                  >
                    {s.alphaPct === null ? "—" : `${s.alphaPct >= 0 ? "+" : ""}${s.alphaPct.toFixed(1)}%`}
                  </td>
                  <td className="px-4 py-3">
                    <StatusPill status={s.status} />
                  </td>
                  <td className="px-4 py-3 whitespace-nowrap">
                    {hasConfig && (
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          toggleExpanded(s.name);
                        }}
                        className="text-xs font-medium underline-offset-2 hover:underline"
                        style={{ color: "var(--series-1)" }}
                      >
                        {isExpanded ? "Hide config" : "Show config"}
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
                    <td colSpan={13} className="px-4 py-3">
                      <div className="flex flex-col gap-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                        {s.engine !== "standard" && (
                          <div>
                            <span style={{ color: "var(--text-muted)" }}>Portfolio result: </span>
                            {/* `!= null` (not `!==`): a stale API without these
                                fields sends undefined, which must render as
                                missing, not crash on .toFixed. */}
                            {s.returnPct != null ? `Return ${fmtSignedPct(s.returnPct)}` : "Return —"}
                            {s.benchmarkReturnPct != null
                              ? ` (SPY buy & hold same window: ${fmtSignedPct(s.benchmarkReturnPct)})`
                              : ""}
                            {s.cagrPct != null ? ` · CAGR ${s.cagrPct.toFixed(2)}%` : ""}
                            {s.maxDrawdownPct != null ? ` · Max DD ${s.maxDrawdownPct.toFixed(1)}%` : ""}
                            {" · rebalancing portfolio engine — no discrete R-multiple trades"}
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
  );
}
