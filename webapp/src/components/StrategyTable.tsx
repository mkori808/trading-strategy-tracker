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

export function StrategyTable({
  strategies,
  selected,
  onSelect,
}: {
  strategies: StrategySummary[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  return (
    <div
      className="overflow-x-auto rounded-lg border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <table className="w-full min-w-[860px] border-collapse text-sm">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
            {[
              "Strategy",
              "Type",
              "Trades",
              "Win Rate",
              "Avg Win (R)",
              "Avg Loss (R)",
              "Expectancy (R)",
              "Profit Factor",
              "Sharpe (vs rf)",
              "Alpha",
              "Status",
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
          {strategies.map((s) => (
            <tr
              key={s.name}
              onClick={() => onSelect(s.name)}
              className="cursor-pointer transition-colors"
              style={{
                borderBottom: "1px solid var(--gridline)",
                background: selected === s.name ? "var(--series-1-wash)" : undefined,
              }}
            >
              <td className="px-4 py-3 font-medium" style={{ color: "var(--text-primary)" }}>
                {s.name}
              </td>
              <td className="px-4 py-3" style={{ color: "var(--text-secondary)" }}>
                {s.kind}
              </td>
              <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                {s.tradesTaken}
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
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
