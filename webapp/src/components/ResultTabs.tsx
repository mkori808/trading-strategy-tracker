import { useState } from "react";
import type { BacktestResult, HistoryRow, Metrics } from "../api";
import { EquityChart } from "./EquityChart";
import { PerSymbolTable } from "./PerSymbolTable";
import { PortfolioPanel } from "./PortfolioPanel";
import { StatTile } from "./StatTile";
import { StatusPill } from "./StatusPill";
import { TradesTable } from "./TradesTable";

type Tab = "overview" | "trades" | "perSymbol" | "portfolio" | "history";

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "trades", label: "Trades" },
  { key: "perSymbol", label: "Per-Symbol" },
  { key: "portfolio", label: "Portfolio" },
  { key: "history", label: "History" },
];

function pct(v: number, digits = 1): string {
  return `${(v * 100).toFixed(digits)}%`;
}

function OverviewTab({ result }: { result: BacktestResult }) {
  const m: Metrics = result.metrics;
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatTile label="Trades Taken" value={String(m.tradesTaken)} />
        <StatTile label="Win Rate" value={m.tradesTaken ? pct(m.winRate) : "—"} />
        <StatTile
          label="Expectancy (R)"
          value={m.tradesTaken ? `${m.expectancyR >= 0 ? "+" : ""}${m.expectancyR.toFixed(3)}` : "—"}
          valueColor={
            m.tradesTaken ? (m.expectancyR >= 0 ? "var(--status-good)" : "var(--status-critical)") : undefined
          }
        />
        <StatTile
          label="Profit Factor"
          value={m.tradesTaken ? (m.profitFactor === null ? "∞" : m.profitFactor.toFixed(2)) : "—"}
        />
        <StatTile
          label="Max Drawdown"
          value={m.maxDrawdownPct !== null ? `${m.maxDrawdownPct.toFixed(1)}%` : "—"}
        />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatTile
          label="Sharpe (vs. risk-free)"
          value={m.sharpe !== null ? m.sharpe.toFixed(2) : "—"}
          valueColor={m.sharpe !== null ? (m.sharpe > 0 ? "var(--status-good)" : "var(--status-critical)") : undefined}
        />
        <StatTile
          label="Alpha vs. buy & hold"
          value={m.alphaPct !== null ? `${m.alphaPct >= 0 ? "+" : ""}${m.alphaPct.toFixed(1)}%` : "—"}
          valueColor={m.alphaPct !== null ? (m.alphaPct > 0 ? "var(--status-good)" : "var(--status-critical)") : undefined}
        />
        <StatTile label="Beta" value={m.beta !== null ? m.beta.toFixed(3) : "—"} />
        <StatTile label="CAGR" value={m.cagrPct !== null ? `${m.cagrPct.toFixed(2)}%` : "—"} />
        <StatTile
          label="Exposure"
          value={m.exposurePct !== null ? `${m.exposurePct.toFixed(1)}% of time` : "—"}
        />
      </div>

      {result.excursionSummary.tradesWithData > 0 && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-2">
          <StatTile
            label="Exit Efficiency (winners, mean of realized/MFE)"
            value={
              result.excursionSummary.meanExitEfficiencyPct !== null
                ? `${result.excursionSummary.meanExitEfficiencyPct.toFixed(1)}%`
                : "—"
            }
            valueColor={
              result.excursionSummary.meanExitEfficiencyPct !== null
                ? result.excursionSummary.meanExitEfficiencyPct >= 60
                  ? "var(--status-good)"
                  : "var(--status-critical)"
                : undefined
            }
          />
          <StatTile
            label="Loss Realization Ratio (losers, mean of |realized|/MAE)"
            value={
              result.excursionSummary.meanLossRealizationRatioPct !== null
                ? `${result.excursionSummary.meanLossRealizationRatioPct.toFixed(1)}%`
                : "—"
            }
            valueColor={
              result.excursionSummary.meanLossRealizationRatioPct !== null
                ? result.excursionSummary.meanLossRealizationRatioPct <= 80
                  ? "var(--status-good)"
                  : "var(--status-critical)"
                : undefined
            }
          />
        </div>
      )}

      {m.riskFreeRate !== null && (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Sharpe/alpha measured against a {(m.riskFreeRate * 100).toFixed(1)}% risk-free rate
          (13-week T-bill, averaged over this run's window) and this strategy's own buy-and-hold
          return on the same symbols — not just R-multiples.
        </p>
      )}

      <StatusPill status={m.status} />

      <EquityChart data={result.equityCurve} symbol={result.equitySymbol} />
    </div>
  );
}

function HistoryTab({
  history,
  onReplay,
}: {
  history: HistoryRow[];
  onReplay: (row: HistoryRow) => void;
}) {
  const canonical = history.filter((r) => r.isCanonical);
  const experiments = history.filter((r) => !r.isCanonical);

  const row = (r: HistoryRow, replayable: boolean) => (
    <tr
      key={`${r.runAt}-${replayable}`}
      style={{ borderBottom: "1px solid var(--gridline)" }}
      className={replayable ? "cursor-pointer" : undefined}
      onClick={replayable ? () => onReplay(r) : undefined}
    >
      <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
        {new Date(r.runAt).toLocaleString(undefined, {
          month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
        })}
      </td>
      <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
        {r.startDate} → {r.endDate}
      </td>
      <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
        {r.tradesTaken}
      </td>
      <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
        {r.expectancyR.toFixed(3)}
      </td>
      <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>
        {replayable
          ? [
              r.symbols.length ? `${r.symbols.length} symbols` : null,
              Object.keys(r.params).length ? `${Object.keys(r.params).length} params changed` : null,
            ]
              .filter(Boolean)
              .join(", ") || "—"
          : "—"}
      </td>
      <td className="px-3 py-2">
        <StatusPill status={r.status} />
      </td>
    </tr>
  );

  const headers = ["Run at", "Window", "Trades", "Expectancy (R)", "Config", "Status"];

  const table = (rows: HistoryRow[], replayable: boolean) => (
    <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
            {headers.map((h) => (
              <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{rows.map((r) => row(r, replayable))}</tbody>
      </table>
    </div>
  );

  return (
    <div className="space-y-6">
      <div>
        <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          Canonical runs — this strategy's registered default configuration over time
        </div>
        {canonical.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>No canonical runs logged.</p>
        ) : (
          table(canonical, false)
        )}
      </div>
      <div>
        <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          Your experiments — custom symbols/dates/parameters. Click a row to reload that
          configuration.
        </div>
        {experiments.length === 0 ? (
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>
            No experiments yet — change a symbol, date, or parameter and run to start one.
          </p>
        ) : (
          table(experiments, true)
        )}
      </div>
    </div>
  );
}

export function ResultTabs({
  result,
  history,
  onReplay,
}: {
  result: BacktestResult | null;
  history: HistoryRow[];
  onReplay: (row: HistoryRow) => void;
}) {
  const [tab, setTab] = useState<Tab>("overview");

  return (
    <div>
      <nav className="mb-4 flex gap-1 border-b" style={{ borderColor: "var(--gridline)" }}>
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className="border-b-2 px-3 py-2 text-sm font-medium transition-colors"
            style={{
              borderColor: tab === key ? "var(--series-1)" : "transparent",
              color: tab === key ? "var(--text-primary)" : "var(--text-muted)",
            }}
          >
            {label}
          </button>
        ))}
      </nav>

      {tab === "history" ? (
        <HistoryTab history={history} onReplay={onReplay} />
      ) : !result ? (
        <div
          className="flex h-40 items-center justify-center rounded-lg border text-sm"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-muted)" }}
        >
          Run a backtest to see results here.
        </div>
      ) : (
        <>
          {tab === "overview" && <OverviewTab result={result} />}
          {tab === "trades" && <TradesTable trades={result.trades} />}
          {tab === "perSymbol" && <PerSymbolTable rows={result.perSymbol} />}
          {tab === "portfolio" && <PortfolioPanel portfolio={result.portfolio} />}
        </>
      )}
    </div>
  );
}
