import type { CrossSectionalResponse } from "../api";
import { StatTile } from "./StatTile";
import { EquityChart } from "./EquityChart";

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function fmtHoldings(holdings: Record<string, number>): string {
  const entries = Object.entries(holdings);
  if (entries.length === 0) return "All cash";
  return entries.map(([sym, w]) => `${sym} ${(w * 100).toFixed(0)}%`).join(", ");
}

export function CrossSectionalResultView({ result }: { result: CrossSectionalResponse }) {
  return (
    <div className="space-y-6">
      <div>
        <div className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          Cross-sectional rebalancing portfolio
        </div>
        <p className="mt-1 max-w-3xl text-xs" style={{ color: "var(--text-muted)" }}>
          Ranks the whole universe ({result.symbols.length} symbols) against itself on a fixed
          monthly schedule and holds target weights between rebalances -- there's no per-symbol
          trade log the way the standard per-symbol engine produces, so these are portfolio-level
          numbers only.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatTile
          label="Return"
          value={`${result.returnPct >= 0 ? "+" : ""}${result.returnPct.toFixed(1)}%`}
          valueColor={result.returnPct >= 0 ? "var(--status-good)" : "var(--status-critical)"}
        />
        <StatTile
          label="CAGR"
          value={result.cagrPct !== null ? `${result.cagrPct.toFixed(2)}%` : "—"}
        />
        <StatTile label="Max Drawdown" value={`${result.maxDrawdownPct.toFixed(1)}%`} />
        <StatTile
          label="Sharpe"
          value={result.sharpe !== null ? result.sharpe.toFixed(2) : "—"}
          valueColor={result.sharpe !== null ? (result.sharpe > 0 ? "var(--status-good)" : "var(--status-critical)") : undefined}
        />
        <StatTile label="Sortino" value={result.sortino !== null ? result.sortino.toFixed(2) : "—"} />
      </div>
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
        Sharpe/Sortino measured against a {(result.riskFreeRate * 100).toFixed(1)}% risk-free rate
        (13-week T-bill, averaged over {fmtDate(result.start)} to {fmtDate(result.end)}).
      </p>

      <EquityChart data={result.equityCurve} symbol={result.strategyName} />

      <div>
        <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          Rebalances ({result.rebalances.length})
        </div>
        <div
          className="max-h-96 overflow-auto rounded-lg border"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
        >
          <table className="w-full min-w-[560px] border-collapse text-sm">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
                {["Date", "Holdings"].map((h) => (
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
              {result.rebalances.map((r, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--gridline)" }}>
                  <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                    {fmtDate(r.date)}
                  </td>
                  <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>
                    {fmtHoldings(r.holdings)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
