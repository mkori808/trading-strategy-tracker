import type { PairsResponse } from "../api";
import { StatTile } from "./StatTile";
import { EquityChart } from "./EquityChart";

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

const money = (v: number) => `$${v.toFixed(2)}`;

export function PairsResultView({ result }: { result: PairsResponse }) {
  if (!result.pair) {
    return (
      <div
        className="rounded-lg border px-4 py-6 text-center text-sm"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-muted)" }}
      >
        No cointegrated pair cleared the significance threshold in the training half of the
        window ({fmtDate(result.trainingWindow[0])} – {fmtDate(result.trainingWindow[1])}). This
        run held 100% cash for the entire trading window -- not an error, just no qualifying pair
        this time.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div
        className="rounded-lg border px-4 py-3 text-sm"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-secondary)" }}
      >
        Selected pair <strong style={{ color: "var(--text-primary)" }}>{result.pair.symbolA} / {result.pair.symbolB}</strong>{" "}
        (cointegration p-value {result.pair.pValue.toFixed(4)}), chosen using only the training
        window <strong>{fmtDate(result.trainingWindow[0])} – {fmtDate(result.trainingWindow[1])}</strong>,
        then traded over <strong>{fmtDate(result.tradingWindow[0])} – {fmtDate(result.tradingWindow[1])}</strong>.
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

      <EquityChart data={result.equityCurve} symbol={`${result.pair.symbolA}/${result.pair.symbolB}`} />

      <div>
        <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          Trades ({result.trades.length})
        </div>
        {result.trades.length === 0 ? (
          <div
            className="rounded-lg border px-4 py-6 text-center text-sm"
            style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-muted)" }}
          >
            The spread never reached the entry z-score over the trading window.
          </div>
        ) : (
          <div
            className="max-h-96 overflow-auto rounded-lg border"
            style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
          >
            <table className="w-full min-w-[640px] border-collapse text-sm">
              <thead>
                <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
                  {["Entry", "Exit", "Position", "P&L", "Reason"].map((h) => (
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
                {result.trades.map((t, i) => (
                  <tr key={i} style={{ borderBottom: "1px solid var(--gridline)" }}>
                    <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                      {fmtDate(t.entryTime)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                      {fmtDate(t.exitTime)}
                    </td>
                    <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>
                      {t.position === "long_spread" ? "Long spread" : "Short spread"}
                    </td>
                    <td
                      className="px-3 py-2 tabular-nums font-medium"
                      style={{ color: t.pnl >= 0 ? "var(--status-good)" : "var(--status-critical)" }}
                    >
                      {t.pnl >= 0 ? "+" : ""}
                      {money(t.pnl)}
                    </td>
                    <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>
                      {t.reason === "cointegration_break_stop" ? "Cointegration break (stop)" : "Reverted to mean"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
