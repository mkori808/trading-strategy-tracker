import type { PerSymbolRow } from "../api";

function Sparkline({ values }: { values: number[] }) {
  if (values.length < 2) {
    return <span style={{ color: "var(--text-muted)" }}>—</span>;
  }
  const w = 88;
  const h = 22;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values
    .map((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / span) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const up = values[values.length - 1] >= values[0];
  const color = up ? "var(--status-good)" : "var(--status-critical)";
  return (
    <svg width={w} height={h} style={{ display: "block" }} aria-hidden="true">
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} />
    </svg>
  );
}

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${(v * 100).toFixed(1)}%`;
}

function fmtR(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(3)}`;
}

function fmtMoney(v: number): string {
  const sign = v >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtPF(v: number | null): string {
  if (v === null) return "—";
  return v > 1000 ? "∞" : v.toFixed(2);
}

export function PerSymbolTable({ rows }: { rows: PerSymbolRow[] }) {
  const traded = rows.filter((r) => r.tradesTaken > 0).length;
  return (
    <div>
      <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        Per-symbol breakdown — {traded} of {rows.length} symbols traded. Pooled metrics hide
        that a rule can win on a few names and lose on the rest; this is where you see it.
      </div>
      <div
        className="overflow-x-auto rounded-lg border"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <table className="w-full min-w-[720px] border-collapse text-sm">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
              {["Symbol", "Trades", "Win Rate", "Expectancy (R)", "Profit Factor", "Net P&L", "Sharpe", "Equity"].map(
                (h) => (
                  <th
                    key={h}
                    className="px-4 py-3 text-left font-medium whitespace-nowrap"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} style={{ borderBottom: "1px solid var(--gridline)" }}>
                <td className="px-4 py-3 font-medium" style={{ color: "var(--text-primary)" }}>
                  {r.symbol}
                </td>
                <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                  {r.tradesTaken}
                </td>
                <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                  {fmtPct(r.winRate)}
                </td>
                <td
                  className="px-4 py-3 tabular-nums"
                  style={{
                    color:
                      r.expectancyR === null
                        ? "var(--text-muted)"
                        : r.expectancyR >= 0
                          ? "var(--status-good)"
                          : "var(--status-critical)",
                  }}
                >
                  {fmtR(r.expectancyR)}
                </td>
                <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                  {fmtPF(r.profitFactor)}
                </td>
                <td
                  className="px-4 py-3 tabular-nums"
                  style={{
                    color: r.tradesTaken
                      ? r.pnl >= 0
                        ? "var(--status-good)"
                        : "var(--status-critical)"
                      : "var(--text-muted)",
                  }}
                >
                  {r.tradesTaken ? fmtMoney(r.pnl) : "—"}
                </td>
                <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                  {r.sharpe === null ? "—" : r.sharpe.toFixed(2)}
                </td>
                <td className="px-4 py-3">
                  <Sparkline values={r.sparkline} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
