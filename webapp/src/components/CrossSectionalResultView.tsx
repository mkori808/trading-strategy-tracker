import type { CrossSectionalResponse } from "../api";
import { StatTile } from "./StatTile";
import { EquityChart } from "./EquityChart";
import { EdgeValidationPanel } from "./EdgeValidationPanel";

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function fmtHoldings(holdings: Record<string, number>): string {
  const entries = Object.entries(holdings);
  if (entries.length === 0) return "All cash";
  return entries.map(([sym, w]) => `${sym} ${(w * 100).toFixed(0)}%`).join(", ");
}

function cadenceLabel(value: string): string {
  if (value === "semimonthly") return "twice-monthly";
  return value;
}

function metric(value: unknown, digits = 2, suffix = "%"): string {
  return typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(digits)}${suffix}` : "—";
}

export function CrossSectionalResultView({ result }: { result: CrossSectionalResponse }) {
  const universeLabel = result.universeLabel || "Strategy default";
  const rebalanceFrequency = result.rebalanceFrequency || "monthly";
  const targetPositionCount = result.targetPositionCount || Math.max(
    0,
    ...result.rebalances.map((row) => Object.keys(row.holdings).length),
  );
  const incompleteWarmupCount = result.incompleteWarmupCount || 0;
  const initialRankableCount = result.initialRankableCount ?? (
    result.symbols.length - incompleteWarmupCount
  );
  const pit = result.pitAnalysis;
  const pitIntegrity = (result.pitDiagnostics ?? {}) as Record<string, unknown>;
  return (
    <div className="space-y-6">
      <EdgeValidationPanel report={result.validation} />
      <div>
        <div className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          {universeLabel} · cross-sectional portfolio
        </div>
        <p className="mt-1 max-w-3xl text-xs" style={{ color: "var(--text-muted)" }}>
          The selected universe contains {result.symbols.length} instruments; {initialRankableCount}
          {" "}had the required lookback at the start of this run. Ranks available instruments by
          relative momentum, selects up to{" "}
          {targetPositionCount} that also pass the absolute-momentum filter, and rebalances{" "}
          {cadenceLabel(rebalanceFrequency)}. Target weights are held until the next
          rebalance. Results are portfolio-level because this engine records allocation changes,
          not independent per-symbol trades.
        </p>
        {incompleteWarmupCount > 0 && (
          <p className="mt-1 max-w-3xl text-xs" style={{ color: "var(--status-warning)" }}>
            {incompleteWarmupCount} constituent(s) entered the ranking only after enough history
            accumulated. The warmup-validity gate remains blocked for this historical run.
          </p>
        )}
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

      {pit && (
        <div className="space-y-4 rounded-lg border p-4" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
          <div>
            <div className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Point-in-time all-stocks evidence</div>
            <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
              SPY, holdout, rolling-window, and MDA calculations use the exact measured strategy dates. Permanent security IDs preserve ticker changes and historical exits.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="SPY return" value={metric(pit.spyReturnPct, 1)} />
            <StatTile label="SPY CAGR" value={metric(pit.spyCagrPct)} />
            <StatTile label="Annualized gap" value={metric(pit.annualizedBenchmarkRelativeReturnPct)} />
            <StatTile label="HAC MDA / year" value={metric(pit.mda.mdaPct)} />
            <StatTile label="PIT equal-weight" value={metric(pit.equalWeightEligibleReturnPct, 1)} />
            <StatTile label="Ranking contribution" value={metric(pit.rankingContributionPct, 1)} />
            <StatTile label="Annualized volatility" value={metric(pit.annualizedVolatilityPct)} />
            <StatTile label="Calmar" value={metric(pit.calmarRatio, 2, "")} />
            <StatTile label="Average eligible" value={metric(pitIntegrity.averageEligibleSecurities, 0, "")} />
            <StatTile label="PIT coverage complete" value={metric(pitIntegrity.completePitCoveragePct)} />
            <StatTile label="Turnover" value={metric(result.turnoverPct, 0)} />
            <StatTile label="Estimated costs" value={`$${result.totalCosts.toLocaleString(undefined, { maximumFractionDigits: 2 })}`} />
          </div>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            {String(pit.mda.explanation ?? "MDA is computed from the HAC-adjusted benchmark-relative return series.")}
          </p>
          {pit.robustness && (
            <div className="rounded-md border p-3 text-xs" style={{ borderColor: "var(--border)" }}>
              <div className="font-medium" style={{ color: "var(--text-primary)" }}>Preregistered robustness family</div>
              <div className="mt-1">
                Primary: {pit.robustness.primaryPreregisteredConfig.lookback} days · top {pit.robustness.primaryPreregisteredConfig.topN} · {pit.robustness.primaryPreregisteredConfig.frequency}
              </div>
              <div>
                {pit.robustness.arms.length} labeled arms · {metric((pit.robustness.fractionBeatingPitEqualWeight ?? 0) * 100)} beat PIT equal weight · {metric((pit.robustness.fractionBeatingSpy ?? 0) * 100)} beat SPY
              </div>
              <div style={{ color: "var(--text-muted)" }}>{pit.robustness.interpretation}</div>
            </div>
          )}
          <div className="grid gap-3 text-xs sm:grid-cols-2">
            <div className="rounded-md border p-3" style={{ borderColor: "var(--border)" }}>
              <div className="mb-2 font-medium" style={{ color: "var(--text-primary)" }}>PIT integrity</div>
              <div>Currently active used: {String(pitIntegrity.currentlyActiveSecuritiesUsed ?? "—")}</div>
              <div>Historically delisted used: {String(pitIntegrity.historicallyDelistedSecuritiesUsed ?? "—")}</div>
              <div>Acquired securities used: {String(pitIntegrity.acquiredSecuritiesUsed ?? "—")}</div>
              <div>Ticker changes resolved: {String(pitIntegrity.tickerChangesResolved ?? "—")}</div>
              <div>Eligible range: {String(pitIntegrity.minimumEligibleSecurities ?? "—")}–{String(pitIntegrity.maximumEligibleSecurities ?? "—")}</div>
              <div>Periods below target count: {metric(pitIntegrity.periodsBelowTargetPositionsPct)}</div>
            </div>
            <div className="rounded-md border p-3" style={{ borderColor: "var(--border)" }}>
              <div className="mb-2 font-medium" style={{ color: "var(--text-primary)" }}>Chronological holdout</div>
              <div>{String(pit.holdout.splitPolicy ?? "First 80% development / final 20% holdout")}</div>
              <div className="mt-1">Development: {String(pit.holdout.developmentStart ?? "—")} to {String(pit.holdout.developmentEnd ?? "—")}</div>
              <div>Holdout: {String(pit.holdout.holdoutStart ?? "—")} to {String(pit.holdout.holdoutEnd ?? "—")}</div>
              <div>Holdout excess: {metric(pit.holdout.holdoutExcessPct)}</div>
              <div className="mt-1">Cost stress: 1× {metric(pit.costStressReturnPct["1x"], 1)}, 2× {metric(pit.costStressReturnPct["2x"], 1)}, 3× {metric(pit.costStressReturnPct["3x"], 1)}</div>
            </div>
          </div>
          <div className="grid gap-3 text-xs sm:grid-cols-2">
            <div className="rounded-md border p-3" style={{ borderColor: "var(--border)" }}>
              <div className="mb-2 font-medium" style={{ color: "var(--text-primary)" }}>Rolling excess return</div>
              {Object.entries(pit.rollingExcess).map(([window, row]) => (
                <div key={window} className="mb-2 last:mb-0">
                  <div className="font-medium">{window.replace("Year", "-year")}</div>
                  <div>Beats SPY: {row.fractionBeatingSpy === null ? "—" : metric(row.fractionBeatingSpy * 100)}</div>
                  <div>Median {metric(row.medianExcessPct)} · worst {metric(row.worstExcessPct)} · best {metric(row.bestExcessPct)}</div>
                </div>
              ))}
            </div>
            <div className="rounded-md border p-3" style={{ borderColor: "var(--border)" }}>
              <div className="mb-2 font-medium" style={{ color: "var(--text-primary)" }}>Historical regimes</div>
              {pit.regimes.map((row) => (
                <div key={row.label} className="grid grid-cols-[1fr_auto] gap-2">
                  <span>{row.label}</span><span>{metric(row.excessPct)} vs SPY</span>
                </div>
              ))}
            </div>
          </div>
          <div className="max-h-72 overflow-auto rounded-md border" style={{ borderColor: "var(--border)" }}>
            <table className="w-full border-collapse text-xs">
              <thead><tr style={{ borderBottom: "1px solid var(--gridline)" }}>
                {['Year', 'Strategy', 'SPY', 'Excess'].map((heading) => <th key={heading} className="px-3 py-2 text-right first:text-left">{heading}</th>)}
              </tr></thead>
              <tbody>{pit.annualReturns.map((row) => (
                <tr key={row.year} style={{ borderBottom: "1px solid var(--gridline)" }}>
                  <td className="px-3 py-1.5">{row.year}</td><td className="px-3 py-1.5 text-right">{metric(row.strategyPct)}</td>
                  <td className="px-3 py-1.5 text-right">{metric(row.spyPct)}</td><td className="px-3 py-1.5 text-right">{metric(row.excessPct)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}

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
