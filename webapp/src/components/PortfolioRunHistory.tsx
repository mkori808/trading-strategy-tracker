import { Fragment, useState } from "react";
import { api, type PortfolioHistoryRow } from "../api";
import { EdgeValidationPanel } from "./EdgeValidationPanel";

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  });
}

function fmtParamValue(v: number | boolean | string): string {
  return String(v);
}

/** Past runs for Dual Momentum / Pairs / Stat Arb, read from
 * engine/logging_db.py's portfolio_runs table -- these two engines don't
 * produce a discrete-trade result, so this is their counterpart to the
 * standard ResultTabs "History" tab. Rendered even when there's no
 * in-memory result for the current session (e.g. after navigating away and
 * back), which is the actual fix for "my run doesn't seem to get saved". */
/** Compact validation state for the history table.
 *
 * Three distinct states, deliberately NOT collapsed into one blank: a run with
 * a stored verdict, a run predating persistence (validated live, result thrown
 * away), and a run that was never validated at all. An absent value in a table
 * gets read as a claim about the strategy, so each says why it is empty. */
/** One cell per EdgeVerdict field, rather than a single collapsed badge.
 *
 * An edge is identified only when EVERY required gate passes -- there is no
 * weighted or composite score (see engine/validation.py). Collapsing seven
 * independent gates into one label hides WHICH gate failed, which is the only
 * actionable part: "no edge" tells you nothing, "blocked on PIT membership
 * robustness" tells you what to go and do.
 *
 * A run with no stored report renders "not recorded" ACROSS the columns rather
 * than blanks. A blank in a verdict column reads as a claim about the strategy;
 * this project has hit that failure three separate times. */
function VerdictCells({ row }: { row: PortfolioHistoryRow }) {
  const v = row.validation?.verdict;
  const muted = { color: "var(--text-muted)" };
  if (!v) {
    const provenanceLabel = row.edgeVerdict ?? "Validation not recorded";
    return (
      <td className="px-3 py-2 text-xs whitespace-nowrap" colSpan={7} style={muted}
          title={row.edgeVerdict
            ? "This stored report uses an older validation contract and is not comparable with current evidence"
            : "No validation report was stored for this run -- this is not a failed validation"}>
        {provenanceLabel}
      </td>
    );
  }
  const yesNo = (b: boolean) => (
    <span
      className="rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap"
      style={{
        color: b ? "var(--status-good)" : "var(--status-warning)",
        background: b ? "var(--status-good-bg)" : "var(--status-warning-bg)",
      }}
    >
      {b ? "Yes" : "No"}
    </span>
  );
  const text = (value: string) => (
    <span className="text-xs whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
      {value || "—"}
    </span>
  );
  const lifecycle = v.lifecycleStage ?? row.lifecycleStage ?? "not recorded";
  const blockingText = v.blockingChecks?.length
    ? v.blockingChecks.map((check) => `${check.status === "fail" ? "Fail" : "Unresolved"}: ${check.label}`).join("; ")
    : v.blockers.join("; ");
  return (
    <>
      <td className="px-3 py-2 whitespace-nowrap capitalize" title={v.headline}>{text(lifecycle.replace(/_/g, " "))}</td>
      <td className="px-3 py-2">{text(v.signalEdge)}</td>
      <td className="px-3 py-2">{text(v.universeSpecific)}</td>
      <td className="px-3 py-2">{text(v.beatsBuyAndHold)}</td>
      <td className="px-3 py-2 whitespace-nowrap">{yesNo(v.forwardTestWorthy)}</td>
      <td className="px-3 py-2 whitespace-nowrap">{yesNo(v.productionCapitalWorthy)}</td>
      <td className="px-3 py-2">
        {blockingText ? (
          // Fixed width + horizontal scroll instead of wrapping. Blocking text
          // is several joined "Fail: ..." clauses -- left to wrap naturally it
          // stretched this one cell across many lines and inflated every row's
          // height to match, even for rows whose OTHER columns are one line.
          // Scrolling keeps the row single-line; title="" still exposes the
          // full text on hover for anyone who'd rather not scroll.
          <div
            className="max-w-[220px] overflow-x-auto whitespace-nowrap text-xs"
            style={{ color: "var(--status-warning)" }}
            title={blockingText}
          >
            {blockingText}
          </div>
        ) : (
          <span className="text-xs" style={muted}>none</span>
        )}
      </td>
    </>
  );
}

export function PortfolioRunHistory({
  rows,
  onReplay,
  strategyName,
  automatable,
}: {
  rows: PortfolioHistoryRow[];
  onReplay: (row: PortfolioHistoryRow) => void;
  /** Name of the strategy these rows belong to -- needed to target the
   * per-strategy paper-execution opt-in (POST /api/live/execution/config). */
  strategyName: string;
  /** Only strategies engine/execution.py knows how to run (see
   * strategies/registry.py:CROSS_SECTIONAL_STRATEGY_NAMES) can be promoted --
   * the API rejects anything else with a 400, so the button is hidden rather
   * than offered and failing. */
  automatable: boolean;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [promoting, setPromoting] = useState<number | null>(null);
  const [promoteMessage, setPromoteMessage] = useState<{ index: number; text: string; isError: boolean } | null>(null);

  // Canonical and exploratory rows are both promotable. The backend reloads
  // the selected row by id and persists its stored parameters/symbols rather
  // than trusting this browser payload or substituting registered defaults.
  const promote = async (i: number, row: PortfolioHistoryRow) => {
    const forwardTestWorthy = Boolean(row.validation?.verdict.forwardTestWorthy);
    const blockers = row.validation?.verdict.blockers ?? [];
    const confirmed = window.confirm(
      `Enable automated PAPER execution for "${strategyName}"?\n\n` +
        `This uses the exact ${row.isCanonical ? "registered-default" : "custom"} configuration from this run. It starts placing real ` +
        "orders in Alpaca's paper account (no real money) on the next scheduled or manual " +
        "rebalance, and replaces any currently enabled configuration for this strategy. " +
        (forwardTestWorthy
          ? "You can disable it any time from the Live tab."
          : "This run did NOT pass every validation gate. Continuing requires a logged override reason."),
    );
    if (!confirmed) return;

    const policyInput = window.prompt(
      "Choose how this forward test starts:\n\n" +
        "ADOPT — mark existing positions to market at the first real rebalance, then reconcile them to the frozen target.\n\n" +
        "FLATTEN — liquidate existing positions first; establish the frozen target only after Alpaca confirms the account is flat.\n\n" +
        "Type ADOPT or FLATTEN:",
      "ADOPT",
    );
    if (policyInput === null) return;
    const inceptionPolicy = policyInput.trim().toLowerCase();
    if (inceptionPolicy !== "adopt" && inceptionPolicy !== "flatten") {
      window.alert("Promotion canceled. Enter exactly ADOPT or FLATTEN.");
      return;
    }

    let override: { reason: string } | undefined;
    if (!forwardTestWorthy) {
      const reason = window.prompt(
        "Promote this run to active paper execution despite its failed or unresolved evidence gates?\n\n" +
          `Blockers: ${blockers.join("; ") || "validation evidence is incomplete"}\n\n` +
          "This does not mark the edge as identified or production-capital worthy. " +
          "Enter the reason for this override:",
      );
      if (!reason?.trim()) return;
      override = { reason: reason.trim() };
    }

    setPromoting(i);
    setPromoteMessage(null);
    try {
      await api.setExecutionConfig(
        strategyName, true, row.params, row.id, inceptionPolicy, override,
      );
      setPromoteMessage({
        index: i,
        text: override
          ? `Active via logged override with ${inceptionPolicy} inception -- see the Live tab.`
          : `Active with ${inceptionPolicy} inception -- see the Live tab.`,
        isError: false,
      });
    } catch (e) {
      setPromoteMessage({ index: i, text: String(e), isError: true });
    } finally {
      setPromoting(null);
    }
  };

  if (rows.length === 0) {
    return (
      <p className="text-sm" style={{ color: "var(--text-muted)" }}>
        No runs logged yet for this strategy.
      </p>
    );
  }

  const toggleExpanded = (i: number) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  };

  return (
    <div>
      <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        Recent runs
      </div>
      <div
        className="overflow-x-auto rounded-lg border"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <table className="w-full min-w-[1600px] border-collapse text-sm">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
              {["Run at", "Window", "Return", "SPY return", "Gap vs SPY", "CAGR", "Sharpe", "Pair",
                "Lifecycle", "Signal evidence", "Universe-specific", "Beats buy-and-hold",
                "Forward-test worthy", "Production-capital worthy", "Required gates not passed"].map((h) => (
                <th
                  key={h}
                  className="px-3 py-2 text-left font-medium whitespace-nowrap"
                  style={{ color: "var(--text-muted)" }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const isExpanded = expanded.has(i);
              const hasParams = Object.keys(r.params).length > 0;
              const replayable = !r.isCanonical;
              const paperEligible = Boolean(r.validation?.verdict.forwardTestWorthy);
              const hasCurrentValidation = Boolean(r.validation);
              return (
                <Fragment key={i}>
                  <tr
                    style={{ borderBottom: isExpanded ? "none" : "1px solid var(--gridline)" }}
                    className="cursor-pointer"
                    onClick={() => toggleExpanded(i)}
                    title={isExpanded ? "Hide run detail" : "Show run detail"}
                  >
                    <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                      {fmtDate(r.runAt)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                      {r.startDate && r.endDate ? `${r.startDate} → ${r.endDate}` : "—"}
                    </td>
                    <td
                      className="px-3 py-2 tabular-nums"
                      style={{
                        color:
                          r.returnPct === null
                            ? "var(--text-secondary)"
                            : r.returnPct >= 0
                              ? "var(--status-good)"
                              : "var(--status-critical)",
                      }}
                    >
                      {r.returnPct === null ? "—" : `${r.returnPct >= 0 ? "+" : ""}${r.returnPct.toFixed(1)}%`}
                    </td>
                    <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                      {r.benchmarkReturnPct == null
                        ? "—"
                        : `${r.benchmarkReturnPct >= 0 ? "+" : ""}${r.benchmarkReturnPct.toFixed(1)}%`}
                    </td>
                    <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                      {r.benchmarkGapPct == null
                        ? "—"
                        : `${r.benchmarkGapPct >= 0 ? "+" : ""}${r.benchmarkGapPct.toFixed(1)}%`}
                    </td>
                    <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                      {r.cagrPct === null ? "—" : `${r.cagrPct.toFixed(2)}%`}
                    </td>
                    <td className="px-3 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                      {r.sharpe === null ? "—" : r.sharpe.toFixed(2)}
                    </td>
                    <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>
                      {r.pairSymbolA ? `${r.pairSymbolA} / ${r.pairSymbolB}` : "—"}
                    </td>
                    <VerdictCells row={r} />
                  </tr>
                  {isExpanded && (
                    <tr style={{ borderBottom: "1px solid var(--gridline)", background: "var(--surface-2, var(--surface-1))" }}>
                      <td colSpan={15} className="px-3 py-2">
                        <div className="flex flex-col gap-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                          <div>
                            <span style={{ color: "var(--text-muted)" }}>
                              {r.isCanonical ? "Canonical run · " : "Experiment (custom configuration) · "}
                            </span>
                            <span style={{ color: "var(--text-muted)" }}>Symbols ({r.symbols.length}): </span>
                            {r.symbols.length ? r.symbols.join(", ") : "—"}
                          </div>
                          <div>
                            <span style={{ color: "var(--text-muted)" }}>Rule parameters: </span>
                            {hasParams
                              ? Object.entries(r.params)
                                  .map(([k, v]) => `${k}=${fmtParamValue(v)}`)
                                  .join(",  ")
                              : "registered defaults (no overrides)"}
                          </div>
                          {replayable && (
                            <div className="mt-1">
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  onReplay(r);
                                }}
                                className="rounded-md border px-2.5 py-1 text-xs font-medium"
                                style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
                              >
                                Load this configuration into the Lab
                              </button>
                            </div>
                          )}
                          {automatable && (
                            <div className="mt-1 flex items-center gap-2">
                              <button
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  promote(i, r);
                                }}
                                disabled={promoting === i || !hasCurrentValidation}
                                className="rounded-md px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50"
                                style={{ background: paperEligible ? "var(--series-1)" : "var(--status-warning)" }}
                              >
                                {promoting === i
                                  ? "Enabling…"
                                  : paperEligible
                                    ? "Promote to active (paper)"
                                    : hasCurrentValidation
                                      ? "Promote anyway (paper)"
                                      : "Evaluation required"}
                              </button>
                              {!paperEligible && (
                                <span className="text-xs" style={{ color: "var(--status-warning)" }}>
                                  {r.validation
                                    ? `Override available; failed gates remain visible and are not converted to passes: ${r.validation.verdict.blockers.join("; ") || r.validation.verdict.headline}`
                                    : "Run the evaluation once to create a reproducible run fingerprint; evidence gates may then be overridden."}
                                </span>
                              )}
                              {promoteMessage?.index === i && (
                                <span
                                  className="text-xs"
                                  style={{ color: promoteMessage.isError ? "var(--status-critical)" : "var(--status-good)" }}
                                >
                                  {promoteMessage.text}
                                </span>
                              )}
                            </div>
                          )}
                        </div>
                        {r.validation ? (
                          <div className="mt-3">
                            <EdgeValidationPanel report={r.validation} showVerdict={false} />
                          </div>
                        ) : (
                          <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>
                            No validation stored for this run. Reports are persisted from
                            2026-08-11 onward; earlier runs were validated live and the result
                            discarded, so this is "not recorded" rather than "did not pass".
                          </p>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
