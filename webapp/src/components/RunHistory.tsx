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

export function RunHistory({ rows }: { rows: HistoryRow[] }) {
  if (rows.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--text-muted)" }}>
        No prior runs logged.
      </p>
    );
  }

  return (
    <div
      className="overflow-x-auto rounded-lg border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
            {["Run at", "Window", "Trades", "Win Rate", "Expectancy (R)", "Profit Factor", "Sharpe", "Alpha", "Status"].map((h) => (
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
          {rows.map((r, i) => (
            <tr key={i} style={{ borderBottom: "1px solid var(--gridline)" }}>
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
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
