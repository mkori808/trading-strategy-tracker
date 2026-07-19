import type { PortfolioResult } from "../api";
import { StatTile } from "./StatTile";
import { EquityChart } from "./EquityChart";

export function PortfolioPanel({ portfolio }: { portfolio: PortfolioResult }) {
  return (
    <div className="space-y-3">
      <div>
        <div className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          Portfolio simulation (shared capital, real concurrent positions)
        </div>
        <p className="mt-1 max-w-3xl text-xs" style={{ color: "var(--text-muted)" }}>
          Every symbol above ran in its own isolated $10K account -- the numbers here instead
          replay all of it against ONE shared pool, capped at {portfolio.maxConcurrentPositions}{" "}
          concurrent positions, so drawdown reflects real simultaneous exposure across correlated
          names instead of the max of independent per-symbol runs.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
        <StatTile label="Trades Taken" value={String(portfolio.tradesTaken)} />
        <StatTile
          label="Skipped (no capacity)"
          value={String(portfolio.skippedForCapacity)}
          valueColor={portfolio.skippedForCapacity > 0 ? "var(--status-warning)" : undefined}
        />
        <StatTile
          label="Return"
          value={`${portfolio.returnPct >= 0 ? "+" : ""}${portfolio.returnPct.toFixed(1)}%`}
          valueColor={portfolio.returnPct >= 0 ? "var(--status-good)" : "var(--status-critical)"}
        />
        <StatTile
          label="Max Drawdown"
          value={`${portfolio.maxDrawdownPct.toFixed(1)}%`}
        />
        <StatTile
          label="Sharpe (portfolio)"
          value={portfolio.sharpe !== null ? portfolio.sharpe.toFixed(2) : "—"}
          valueColor={
            portfolio.sharpe !== null
              ? portfolio.sharpe > 0
                ? "var(--status-good)"
                : "var(--status-critical)"
              : undefined
          }
        />
      </div>

      <EquityChart data={portfolio.equityCurve} symbol="portfolio" />
    </div>
  );
}
