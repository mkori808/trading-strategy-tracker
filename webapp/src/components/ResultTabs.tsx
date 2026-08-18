import { useEffect, useState } from "react";
import type { BacktestResult, Metrics } from "../api";
import { ChatPanel } from "./ChatPanel";
import { EquityChart } from "./EquityChart";
import { EdgeValidationPanel } from "./EdgeValidationPanel";
import { PerSymbolTable } from "./PerSymbolTable";
import { PortfolioPanel } from "./PortfolioPanel";
import { StatTile } from "./StatTile";
import { StatusPill } from "./StatusPill";
import { TradesTable } from "./TradesTable";

type Tab = "validation" | "overview" | "trades" | "perSymbol" | "portfolio" | "chat";

const TABS: { key: Tab; label: string }[] = [
  { key: "validation", label: "Edge validation" },
  { key: "overview", label: "Overview" },
  { key: "trades", label: "Trades" },
  { key: "perSymbol", label: "Per-Symbol" },
  { key: "portfolio", label: "Portfolio" },
  { key: "chat", label: "Chat" },
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

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-6">
        <StatTile
          label="Sharpe (vs. risk-free)"
          value={m.sharpe !== null ? m.sharpe.toFixed(2) : "—"}
          valueColor={m.sharpe !== null ? (m.sharpe > 0 ? "var(--status-good)" : "var(--status-critical)") : undefined}
        />
        <StatTile
          label="Buy-and-hold gap (descriptive)"
          value={m.benchmarkGapPct !== null ? `${m.benchmarkGapPct >= 0 ? "+" : ""}${m.benchmarkGapPct.toFixed(1)}%` : "—"}
        />
        <StatTile
          label="Matched SPY excess"
          value={m.matchedSpyExcessPct !== null ? `${m.matchedSpyExcessPct >= 0 ? "+" : ""}${m.matchedSpyExcessPct.toFixed(1)}%` : "—"}
          valueColor={m.matchedSpyExcessPct !== null ? (m.matchedSpyExcessPct > 0 ? "var(--status-good)" : "var(--status-critical)") : undefined}
        />
        <StatTile label="Matched beta" value={m.matchedBeta !== null ? m.matchedBeta.toFixed(3) : "—"} />
        <StatTile label="CAGR" value={m.cagrPct !== null ? `${m.cagrPct.toFixed(2)}%` : "—"} />
        <StatTile
          label="Average gross exposure"
          value={m.averageGrossExposurePct !== null ? `${m.averageGrossExposurePct.toFixed(1)}%` : "—"}
        />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatTile label="Matched SPY return" value={m.matchedSpyReturnPct !== null ? `${m.matchedSpyReturnPct >= 0 ? "+" : ""}${m.matchedSpyReturnPct.toFixed(1)}%` : "—"} />
        <StatTile label="Matched alpha (annual)" value={m.matchedAlphaAnnualPct !== null ? `${m.matchedAlphaAnnualPct >= 0 ? "+" : ""}${m.matchedAlphaAnnualPct.toFixed(2)}%` : "Underpowered"} />
        <StatTile label="Time in market" value={m.timeInMarketPct !== null ? `${m.timeInMarketPct.toFixed(1)}%` : "—"} />
        <StatTile label="Turnover" value={m.turnoverPct !== null ? `${m.turnoverPct.toFixed(1)}%` : "—"} />
        <StatTile label="Modeled costs" value={m.modeledCosts !== null ? `$${m.modeledCosts.toFixed(2)}` : "—"} />
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
          Sharpe is measured against a {(m.riskFreeRate * 100).toFixed(1)}% risk-free rate
          (13-week T-bill, averaged over this run's window). The full-window buy-and-hold
          gap is descriptive. Matched SPY excess compares only intervals and notional where
          strategy capital was deployed.
        </p>
      )}

      <StatusPill status={m.status} />

      <EquityChart data={result.equityCurve} symbol={result.equitySymbol} />
    </div>
  );
}

export function ResultTabs({ result }: { result: BacktestResult | null }) {
  const [tab, setTab] = useState<Tab>("validation");

  // Every new run should lead with the verdict even if the user was viewing
  // trades or the portfolio on the previous result.
  useEffect(() => {
    if (result) setTab("validation");
  }, [result]);

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

      {!result ? (
        <div
          className="flex h-40 items-center justify-center rounded-lg border text-sm"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-muted)" }}
        >
          Run a backtest to see results here.
        </div>
      ) : (
        <>
          {tab === "validation" && <EdgeValidationPanel report={result.validation} />}
          {tab === "overview" && <OverviewTab result={result} />}
          {tab === "trades" && <TradesTable trades={result.trades} />}
          {tab === "perSymbol" && <PerSymbolTable rows={result.perSymbol} />}
          {tab === "portfolio" && <PortfolioPanel portfolio={result.portfolio} />}
          {tab === "chat" && (
            <ChatPanel
              // Reset the conversation when the underlying run changes (a
              // different strategy, window, symbols, or params) -- but NOT
              // on every re-render of the same result, e.g. switching tabs
              // back and forth.
              key={`${result.strategyName}-${result.start}-${result.end}-${result.appliedSymbols.join(",")}-${JSON.stringify(result.appliedParams)}`}
              result={result}
            />
          )}
        </>
      )}
    </div>
  );
}
