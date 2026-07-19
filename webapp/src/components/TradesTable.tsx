import type { Trade } from "../api";

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

const money = (v: number) => `$${v.toFixed(2)}`;

// Exit efficiency only means something for winners (realized/MFE) and loss
// realization ratio only for losers (|realized|/MAE) -- see
// engine/excursion.py. One combined column shows whichever applies rather
// than two columns that are each empty for half the rows.
function exitQuality(t: Trade): { label: string; color: string } | null {
  if (t.pnl > 0 && t.exitEfficiencyPct !== null) {
    return {
      label: `${t.exitEfficiencyPct.toFixed(0)}% captured`,
      color: t.exitEfficiencyPct >= 60 ? "var(--status-good)" : "var(--status-critical)",
    };
  }
  if (t.pnl < 0 && t.lossRealizationRatioPct !== null) {
    return {
      label: `${t.lossRealizationRatioPct.toFixed(0)}% of MAE`,
      color: t.lossRealizationRatioPct <= 80 ? "var(--status-good)" : "var(--status-critical)",
    };
  }
  return null;
}

export function TradesTable({ trades }: { trades: Trade[] }) {
  if (trades.length === 0) {
    return (
      <div
        className="rounded-lg border px-4 py-6 text-center text-sm"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-muted)" }}
      >
        No trades were taken over the backtest window.
      </div>
    );
  }

  return (
    <div
      className="max-h-96 overflow-auto rounded-lg border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <table className="w-full min-w-[980px] border-collapse text-sm">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
            {[
              "Symbol", "Entry", "Exit", "Size", "Entry $", "Exit $", "P&L", "Return",
              "MFE (R)", "MAE (R)", "Exit Quality",
            ].map((h) => (
              <th
                key={h}
                className="sticky top-0 px-3 py-2 text-left font-medium whitespace-nowrap"
                style={{ color: "var(--text-muted)", background: "var(--surface-1)" }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i} style={{ borderBottom: "1px solid var(--gridline)" }}>
              <td className="px-3 py-2 font-medium" style={{ color: "var(--text-primary)" }}>
                {t.symbol}
              </td>
              <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                {fmtTime(t.entryTime)}
              </td>
              <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                {fmtTime(t.exitTime)}
              </td>
              <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                {t.size}
              </td>
              <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                {money(t.entryPrice)}
              </td>
              <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                {money(t.exitPrice)}
              </td>
              <td
                className="px-3 py-2 tabular-nums font-medium"
                style={{ color: t.pnl >= 0 ? "var(--status-good)" : "var(--status-critical)" }}
              >
                {t.pnl >= 0 ? "+" : ""}
                {money(t.pnl)}
              </td>
              <td
                className="px-3 py-2 tabular-nums font-medium"
                style={{ color: t.returnPct >= 0 ? "var(--status-good)" : "var(--status-critical)" }}
              >
                {t.returnPct >= 0 ? "+" : ""}
                {(t.returnPct * 100).toFixed(2)}%
              </td>
              <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                {t.mfeR !== null ? t.mfeR.toFixed(2) : "—"}
              </td>
              <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                {t.maeR !== null ? t.maeR.toFixed(2) : "—"}
              </td>
              <td className="px-3 py-2 whitespace-nowrap" style={{ color: exitQuality(t)?.color ?? "var(--text-muted)" }}>
                {exitQuality(t)?.label ?? "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
