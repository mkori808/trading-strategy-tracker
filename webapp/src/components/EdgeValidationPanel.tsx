import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { ValidationCheck, ValidationDimension, ValidationReport, ValidationStatus } from "../api";

const STATUS_META: Record<ValidationStatus, { label: string; color: string; background: string; mark: string }> = {
  pass: { label: "Pass", color: "var(--status-good)", background: "var(--status-good-bg)", mark: "✓" },
  fail: { label: "Fail", color: "var(--status-critical)", background: "var(--status-critical-bg)", mark: "×" },
  warning: { label: "Weak", color: "var(--status-warning)", background: "var(--status-warning-bg)", mark: "!" },
  unresolved: { label: "Unresolved", color: "var(--status-warning)", background: "var(--status-warning-bg)", mark: "?" },
  not_applicable: { label: "N/A", color: "var(--text-muted)", background: "var(--pill-bg)", mark: "–" },
};

function StatusBadge({ status }: { status: ValidationStatus }) {
  const meta = STATUS_META[status];
  return (
    <span
      className="inline-flex min-w-[88px] items-center justify-center gap-1 rounded-full px-2 py-1 text-xs font-semibold"
      style={{ color: meta.color, background: meta.background }}
    >
      <span aria-hidden="true">{meta.mark}</span> {meta.label}
    </span>
  );
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function fmtPct(value: unknown, digits = 1): string {
  const number = asNumber(value);
  return number === null ? "—" : `${number >= 0 ? "+" : ""}${number.toFixed(digits)}%`;
}

function EvidenceDetails({ check }: { check: ValidationCheck }) {
  const details = check.details;

  if (check.key === "statistical_power") {
    const selectedMda = asNumber(details.selectedMdaPct);
    const requiredAlpha = asNumber(details.minimumTradableAlphaPct);
    const margin = asNumber(details.detectabilityMarginPct);
    return (
      <div className="mt-3 grid grid-cols-2 gap-3 rounded-md p-3 text-xs sm:grid-cols-4" style={{ background: "var(--page)" }}>
        <span>Minimum detectable alpha<br /><strong>{fmtPct(selectedMda, 2)}</strong></span>
        <span>Actionable-alpha threshold<br /><strong>{fmtPct(requiredAlpha, 2)}</strong></span>
        <span>Detectability margin<br /><strong>{fmtPct(margin, 2)}</strong></span>
        <span>Evidence basis<br /><strong>{String(details.selectedBasis ?? "total volatility")}</strong></span>
        <span>Residual alpha<br /><strong>{fmtPct(details.observedResidualAlphaPct, 2)}</strong></span>
        <span>Residual 95% CI<br /><strong>{fmtPct(details.residualAlphaCi95LowPct, 2)} / {fmtPct(details.residualAlphaCi95HighPct, 2)}</strong></span>
        <span>Years observed<br /><strong>{asNumber(details.yearsObserved)?.toFixed(1) ?? "—"}</strong></span>
        <span>Effective independent bets<br /><strong>{asNumber(details.effectiveIndependentBets)?.toFixed(1) ?? "—"}</strong></span>
      </div>
    );
  }

  if (check.key === "beats_random") {
    const percentile = asNumber(details.percentile);
    const values = [
      ["Random mean", fmtPct(details.meanPct)],
      ["Random median", fmtPct(details.medianPct)],
      ["Random p05 / p95", `${fmtPct(details.p05Pct)} / ${fmtPct(details.p95Pct)}`],
      ["Random maximum", fmtPct(details.maxPct)],
      ["Empirical p", asNumber(details.empiricalP)?.toFixed(4) ?? "—"],
      ["Simulations", String(details.simulations ?? "—")],
    ];
    return (
      <div className="mt-3 rounded-md p-3" style={{ background: "var(--page)" }}>
        <div className="mb-1 flex justify-between text-xs" style={{ color: "var(--text-secondary)" }}>
          <span>Strategy percentile in random distribution</span>
          <strong>{percentile === null ? "—" : `${percentile.toFixed(1)}th`}</strong>
        </div>
        <div className="h-2 overflow-hidden rounded-full" style={{ background: "var(--gridline)" }}>
          <div
            className="h-full rounded-full"
            style={{ width: `${Math.max(0, Math.min(100, percentile ?? 0))}%`, background: "var(--series-1)" }}
          />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-x-5 gap-y-1 text-xs sm:grid-cols-3">
          {values.map(([label, value]) => (
            <div key={label} className="flex justify-between gap-2">
              <span style={{ color: "var(--text-muted)" }}>{label}</span>
              <span className="tabular-nums" style={{ color: "var(--text-secondary)" }}>{value}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (check.key === "parameter_ridge" && Array.isArray(details.arms)) {
    const arms = details.arms as Array<Record<string, unknown>>;
    const genericParameters = arms.some((arm) => "parameter" in arm);
    return (
      <div className="mt-3 max-h-64 overflow-auto rounded-md" style={{ background: "var(--page)" }}>
        <table className="w-full text-xs">
          <thead className="sticky top-0" style={{ background: "var(--page)", color: "var(--text-muted)" }}>
            <tr>{(genericParameters
              ? ["Parameter", "Value", "Return", "Benchmark", "Contribution"]
              : ["Lookback", "Top N", "Cadence", "Return", "vs EW", "vs SPY"]
            ).map((h) => <th key={h} className="px-2 py-1.5 text-left">{h}</th>)}</tr>
          </thead>
          <tbody>
            {genericParameters ? arms.map((arm, index) => (
              <tr key={index} style={{ borderTop: "1px solid var(--gridline)" }}>
                <td className="px-2 py-1.5">{String(arm.parameter ?? "—")}</td>
                <td className="px-2 py-1.5">{String(arm.value ?? "—")}</td>
                <td className="px-2 py-1.5 tabular-nums">{fmtPct(arm.returnPct)}</td>
                <td className="px-2 py-1.5 tabular-nums">{fmtPct(arm.benchmarkReturnPct)}</td>
                <td className="px-2 py-1.5 tabular-nums" style={{ color: (asNumber(arm.contributionPct) ?? 0) > 0 ? "var(--status-good)" : "var(--status-critical)" }}>{fmtPct(arm.contributionPct)}</td>
              </tr>
            )) : arms.map((arm, index) => (
              <tr key={index} style={{ borderTop: "1px solid var(--gridline)" }}>
                <td className="px-2 py-1.5">{String(arm.lookback ?? "—")}</td>
                <td className="px-2 py-1.5">{String(arm.topN ?? "—")}</td>
                <td className="px-2 py-1.5">{String(arm.frequency ?? "—")}</td>
                <td className="px-2 py-1.5 tabular-nums">{fmtPct(arm.returnPct)}</td>
                <td className="px-2 py-1.5 tabular-nums" style={{ color: (asNumber(arm.contributionPct) ?? 0) > 0 ? "var(--status-good)" : "var(--status-critical)" }}>{fmtPct(arm.contributionPct)}</td>
                <td className="px-2 py-1.5">{arm.beatsSpy ? "Pass" : "Fail"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (check.key === "chronological_oos" && Array.isArray(details.walkForwardWindows)) {
    const windows = details.walkForwardWindows as Array<Record<string, unknown>>;
    return (
      <div className="mt-3 rounded-md p-3" style={{ background: "var(--page)" }}>
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <span>Holdout starts: <strong>{String(details.holdoutStart ?? "—")}</strong></span>
          <span>Holdout strategy: <strong>{fmtPct(details.holdoutStrategyReturnPct)}</strong></span>
          <span>Holdout benchmark: <strong>{fmtPct(details.holdoutBenchmarkReturnPct)}</strong></span>
          <span>Contribution: <strong>{fmtPct(details.holdoutContributionPct)}</strong></span>
        </div>
        <div className="mt-3 max-h-52 overflow-auto text-xs">
          {windows.map((window, index) => <div key={index} className="grid grid-cols-4 border-t py-1" style={{ borderColor: "var(--gridline)" }}>
            <span>{String(window.start).slice(0, 10)}</span>
            <span>{fmtPct(window.strategyReturnPct)}</span>
            <span>{fmtPct(window.benchmarkReturnPct)}</span>
            <strong style={{ color: (asNumber(window.contributionPct) ?? 0) > 0 ? "var(--status-good)" : "var(--status-critical)" }}>{fmtPct(window.contributionPct)}</strong>
          </div>)}
        </div>
      </div>
    );
  }

  if (check.key === "bootstrap_confidence") {
    return (
      <div className="mt-3 grid grid-cols-2 gap-2 rounded-md p-3 text-xs sm:grid-cols-4" style={{ background: "var(--page)" }}>
        <span>Profit probability<br /><strong>{asNumber(details.probabilityProfit) === null ? "—" : `${(asNumber(details.probabilityProfit)! * 100).toFixed(1)}%`}</strong></span>
        <span>Outperform probability<br /><strong>{asNumber(details.probabilityOutperform) === null ? "—" : `${(asNumber(details.probabilityOutperform)! * 100).toFixed(1)}%`}</strong></span>
        <span>Contribution p05<br /><strong>{fmtPct(details.contributionP05Pct)}</strong></span>
        <span>Drawdown p95<br /><strong>{fmtPct(details.maxDrawdownP95Pct)}</strong></span>
      </div>
    );
  }

  if (check.key === "reproducible_manifest") {
    return (
      <div className="mt-3 space-y-1 rounded-md p-3 text-xs" style={{ background: "var(--page)", color: "var(--text-secondary)" }}>
        {["runFingerprint", "codeHashSha256", "dataHashSha256", "resultHashSha256"].map((key) => (
          <div key={key} className="grid grid-cols-[130px_1fr] gap-2">
            <span style={{ color: "var(--text-muted)" }}>{key}</span>
            <code className="break-all">{String(details[key] ?? "—")}</code>
          </div>
        ))}
        <details className="pt-2"><summary className="cursor-pointer">Full manifest</summary><pre className="mt-2 max-h-64 overflow-auto">{JSON.stringify(details, null, 2)}</pre></details>
      </div>
    );
  }

  if (check.key === "historical_stability" && Array.isArray(details.windows)) {
    const windows = details.windows as Array<Record<string, unknown>>;
    return (
      <div className="mt-3 rounded-md p-3" style={{ background: "var(--page)" }}>
        <div className="mb-2 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <span>Positive: <strong>{asNumber(details.fractionPositive) === null ? "—" : `${(asNumber(details.fractionPositive)! * 100).toFixed(0)}%`}</strong></span>
          <span>Median: <strong>{fmtPct(details.medianContributionPct)}</strong></span>
          <span>Worst: <strong>{fmtPct(details.worstContributionPct)}</strong></span>
          <span>Ex-best mean: <strong>{fmtPct(details.meanExcludingBestPct)}</strong></span>
        </div>
        <div className="grid max-h-52 grid-cols-1 gap-x-5 overflow-auto text-xs sm:grid-cols-2">
          {windows.map((window, index) => {
            const contribution = asNumber(window.contributionPct ?? window.meanTradeExcessPct);
            return (
              <div key={index} className="flex justify-between border-t py-1" style={{ borderColor: "var(--gridline)" }}>
                <span style={{ color: "var(--text-muted)" }}>
                  {String(window.start)} – {String(window.end)}
                  {window.matchedTrades == null ? "" : ` · ${String(window.matchedTrades)} trades`}
                </span>
                <strong className="tabular-nums" style={{ color: (contribution ?? 0) > 0 ? "var(--status-good)" : "var(--status-critical)" }}>{fmtPct(contribution)}</strong>
              </div>
            );
          })}
        </div>
      </div>
    );
  }

  if (check.key === "cross_universe_replication" && Array.isArray(details.universes)) {
    const universes = details.universes as Array<Record<string, unknown>>;
    return (
      <div className="mt-3 overflow-auto rounded-md" style={{ background: "var(--page)" }}>
        <table className="w-full text-xs">
          <thead style={{ color: "var(--text-muted)" }}><tr>{["Universe", "Strategy", "Equal weight", "Contribution", "Missing"].map((h) => <th key={h} className="px-2 py-1.5 text-left">{h}</th>)}</tr></thead>
          <tbody>{universes.map((universe, index) => <tr key={index} style={{ borderTop: "1px solid var(--gridline)" }}>
            <td className="px-2 py-1.5">{String(universe.universe ?? "—")}</td>
            <td className="px-2 py-1.5">{fmtPct(universe.returnPct)}</td>
            <td className="px-2 py-1.5">{fmtPct(universe.equalWeightReturnPct)}</td>
            <td className="px-2 py-1.5">{fmtPct(universe.contributionPct)}</td>
            <td className="px-2 py-1.5">{Array.isArray(universe.missingSymbols) ? universe.missingSymbols.join(", ") || "none" : String(universe.error ?? "none")}</td>
          </tr>)}</tbody>
        </table>
      </div>
    );
  }

  return (
    <pre
      className="mt-2 max-h-72 overflow-auto rounded-md p-3 text-[11px] leading-5"
      style={{ background: "var(--page)", color: "var(--text-secondary)" }}
    >
      {JSON.stringify(details, null, 2)}
    </pre>
  );
}

function CheckRow({ check }: { check: ValidationCheck }) {
  const hasDetails = Object.keys(check.details).length > 0;
  return (
    <div className="border-t px-4 py-3 first:border-t-0" style={{ borderColor: "var(--gridline)" }}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-sm font-medium" style={{ color: "var(--text-primary)" }}>
            {check.label}
            {check.required && (
              <span className="text-[10px] font-semibold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
                Required gate
              </span>
            )}
          </div>
          <p className="mt-0.5 text-xs" style={{ color: "var(--text-secondary)" }}>{check.summary}</p>
        </div>
        <StatusBadge status={check.status} />
      </div>
      {hasDetails && (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs" style={{ color: "var(--text-muted)" }}>
            Evidence details
          </summary>
          <EvidenceDetails check={check} />
        </details>
      )}
    </div>
  );
}

function YesNo({ value }: { value: boolean }) {
  return <span style={{ color: value ? "var(--status-good)" : "var(--status-critical)" }}>{value ? "Yes" : "No"}</span>;
}

function lifecycleLabel(value: string | undefined): string {
  return value ? value.replace(/_/g, " ") : "not recorded";
}

function PerformanceModal({
  dimensions,
  metrics,
  onClose,
}: {
  dimensions: ValidationDimension[];
  metrics?: Record<string, unknown> | null;
  onClose: () => void;
}) {
  const metricRows: Array<[string, string]> = metrics ? [
    ["Shared-capital return", fmtPct(metrics.returnPct, 2)],
    ["CAGR", fmtPct(metrics.cagrPct, 2)],
    ["Sharpe", asNumber(metrics.sharpe)?.toFixed(2) ?? "â€”"],
    ["Sortino", asNumber(metrics.sortino)?.toFixed(2) ?? "â€”"],
    ["Max drawdown", fmtPct(metrics.maxDrawdownPct, 2)],
    ["Trades / rebalances", String(metrics.trades ?? metrics.rebalances ?? "â€”")],
    ["Win rate", fmtPct(metrics.winRatePct, 1)],
    ["Expectancy", asNumber(metrics.expectancyR) === null ? "â€”" : `${asNumber(metrics.expectancyR)!.toFixed(3)}R`],
    ["Profit factor", asNumber(metrics.profitFactor)?.toFixed(2) ?? "â€”"],
    ["Average gross exposure", fmtPct(metrics.averageGrossExposurePct, 1)],
    ["Time in market", fmtPct(metrics.timeInMarketPct, 1)],
    ["Turnover", fmtPct(metrics.turnoverPct, 1)],
    ["Modeled costs", asNumber(metrics.modeledCosts) === null ? "â€”" : `$${asNumber(metrics.modeledCosts)!.toFixed(2)}`],
    ["Matched-SPY excess", fmtPct(metrics.matchedSpyExcessPct, 2)],
    ["Matched alpha / year", fmtPct(metrics.matchedAlphaAnnualPct, 2)],
    ["PIT equal-weight excess", fmtPct(metrics.pitEqualWeightExcessPct, 2)],
  ] : [];
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="raw-performance-title"
        className="max-h-[85vh] w-full max-w-2xl overflow-auto rounded-xl border shadow-2xl"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <div
          className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b px-4 py-3"
          style={{ borderColor: "var(--gridline)", background: "var(--surface-1)" }}
        >
          <div>
            <h2 id="raw-performance-title" className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              Raw strategy performance
            </h2>
            <p className="mt-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
              Complete validation evidence for this run, including benchmarks, robustness, stability, integrity, and replication.
            </p>
          </div>
          <button
            type="button"
            autoFocus
            onClick={onClose}
            className="rounded-md border px-3 py-1.5 text-xs font-medium"
            style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
          >
            Close
          </button>
        </div>
        <div className="space-y-3 p-4">
          {metricRows.length > 0 && (
            <div className="rounded-lg border p-4" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
              <div className="mb-3 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
                Canonical portfolio metrics
              </div>
              <div className="grid grid-cols-1 gap-x-8 gap-y-2 text-xs sm:grid-cols-2">
                {metricRows.map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-4">
                    <span style={{ color: "var(--text-muted)" }}>{label}</span>
                    <strong className="tabular-nums" style={{ color: "var(--text-primary)" }}>{value}</strong>
                  </div>
                ))}
              </div>
            </div>
          )}
          {dimensions.map((dimension) => (
            <div
              key={dimension.key}
              className="overflow-hidden rounded-lg border"
              style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
            >
              <div className="px-4 py-2 text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>
                {dimension.label}
              </div>
              {dimension.checks.map((check) => <CheckRow key={check.key} check={check} />)}
            </div>
          ))}
        </div>
      </section>
    </div>,
    document.body,
  );
}

export function EdgeValidationPanel({
  report,
  showVerdict = true,
}: {
  report: ValidationReport;
  showVerdict?: boolean;
}) {
  const [showPerformance, setShowPerformance] = useState(false);
  const verdict = report.verdict;
  const accent = verdict.identifiedEdge ? "var(--status-good)" : "var(--status-warning)";
  const checkCount = report.dimensions.reduce((total, dimension) => total + dimension.checks.length, 0);

  useEffect(() => {
    if (!showPerformance) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setShowPerformance(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [showPerformance]);

  return (
    <section className="space-y-4">
      {showVerdict && (
        <div className="px-1 py-1">
          <div className="text-xs font-semibold uppercase tracking-wide" style={{ color: accent }}>
            Edge verdict
          </div>
          <div className="mt-1 text-lg font-semibold" style={{ color: "var(--text-primary)" }}>
            {verdict.headline}
          </div>
          <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
            An edge is identified only when every required evidence gate passes. No weighted or composite score is used.
          </p>

          <div className="mt-4 grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
            <div className="flex justify-between gap-4"><span>Research lifecycle</span><strong className="capitalize">{lifecycleLabel(verdict.lifecycleStage)}</strong></div>
            <div className="flex justify-between gap-4"><span>Signal evidence</span><strong>{verdict.signalEdge}</strong></div>
            <div className="flex justify-between gap-4"><span>Universe-specific</span><strong>{verdict.universeSpecific}</strong></div>
            <div className="flex justify-between gap-4"><span>Beats buy-and-hold</span><strong>{verdict.beatsBuyAndHold}</strong></div>
            <div className="flex justify-between gap-4"><span>Forward-test worthy</span><YesNo value={verdict.forwardTestWorthy} /></div>
            <div className="flex justify-between gap-4"><span>Production-capital worthy</span><YesNo value={verdict.productionCapitalWorthy} /></div>
          </div>

          {verdict.blockers.length > 0 && (
            <div className="mt-4 text-xs" style={{ color: "var(--text-secondary)" }}>
              <strong style={{ color: "var(--text-primary)" }}>Required gates not passed:</strong>
              <div className="mt-2 flex flex-wrap gap-2">
                {(verdict.blockingChecks?.length
                  ? verdict.blockingChecks
                  : verdict.blockers.map((label) => ({ key: label, label, status: "unresolved" as const, summary: "" }))
                ).map((check) => (
                  <span
                    key={check.key}
                    className="rounded-full px-2 py-1"
                    title={check.summary}
                    style={{
                      color: check.status === "fail" ? "var(--status-critical)" : "var(--status-warning)",
                      background: check.status === "fail" ? "var(--status-critical-bg)" : "var(--status-warning-bg)",
                    }}
                  >
                    {check.status === "fail" ? "Fail" : "Unresolved"}: {check.label}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {report.dimensions.length > 0 && (
        <button
          type="button"
          onClick={() => setShowPerformance(true)}
          className="flex w-full items-center justify-between rounded-lg border px-4 py-3 text-left text-sm font-medium transition-colors hover:bg-black/5"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-primary)" }}
        >
          <span>View raw strategy performance</span>
          <span className="text-xs font-normal" style={{ color: "var(--text-muted)" }}>
            {checkCount} checks · Opens popup
          </span>
        </button>
      )}

      {showPerformance && report.dimensions.length > 0 && (
        <PerformanceModal
          dimensions={report.dimensions}
          metrics={report.research?.canonicalPortfolioMetrics}
          onClose={() => setShowPerformance(false)}
        />
      )}
    </section>
  );
}
