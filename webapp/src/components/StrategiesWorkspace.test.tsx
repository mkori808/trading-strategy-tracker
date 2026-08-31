import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { ExecutionAccountStatus, HistoryRow, ResearchStatusRow, StrategySummary, ValidationReport } from "../api";
import { EditableUniverseSummary, ExecutionSemantics, LoadedRunNotice, StructuralUniverseSummary } from "./RunConfigPanel";
import { RunEvidenceDetails, RunHistory } from "./RunHistory";
import { ConditionalStatusSummary, KeyEvidence, StrategiesTab, StrategyExecutionState } from "./StrategiesTab";
import { StrategyTable } from "./StrategyTable";
import { gateCounts, primaryBlocker, searchFamilyNote } from "./researchPresentation";

const validation: ValidationReport = { version: 5, generatedAt: "2026-08-13", research: { experimentId: 33, familySearchNumber: 1, familySearchCount: 10, isPreregistered: true, lifecycleStage: "holdout_passed", validationSpec: {}, manifest: {} }, dimensions: [{ key: "power", label: "Power", checks: [{ key: "statistical_power", label: "Can this design resolve the claimed edge?", status: "fail", summary: "MDA exceeds target", required: true, value: 11.8, details: { selectedMdaPct: 11.8, minimumTradableAlphaPct: 2 } }] }, { key: "performance", label: "Performance", checks: [{ key: "beats_cash", label: "Beats cash", status: "pass", summary: "Passed cash hurdle", required: true, value: .8, details: {} }, { key: "sample_coverage", label: "Coverage", status: "pass", summary: "Coverage valid", required: true, value: 40, details: {} }] }, { key: "benchmark", label: "Benchmark", checks: [{ key: "beats_spy", label: "Beats SPY", status: "fail", summary: "Did not beat SPY", required: true, value: -1.4, details: {} }, { key: "pit_membership", label: "PIT", status: "warning", summary: "PIT history partial", required: true, value: null, details: {} }] }], verdict: { identifiedEdge: false, headline: "Evidence unresolved", signalEdge: "Unresolved", universeSpecific: "Unresolved", beatsBuyAndHold: "No", forwardTestWorthy: false, productionCapitalWorthy: false, lifecycleStage: "holdout_passed", blockers: ["Can this design resolve the claimed edge?"] } };

const strategy: StrategySummary = { name: "Opening Range Breakout (ORB)", kind: "Day Trading", engine: "standard", tradesTaken: 40, winRate: .55, avgWinR: 1.2, avgLossR: -.8, expectancyR: .07, profitFactor: 1.14, cagrPct: null, returnPct: 3, maxDrawdownPct: -4, benchmarkReturnPct: null, benchmarkGapPct: -2, benchmarkName: "SPY", benchmarkWindowStart: "2026-05-01", benchmarkWindowEnd: "2026-08-01", status: "Backtested", lastRun: "2026-08-13T00:00:00Z", sharpe: .4, alphaPct: null, beta: null, symbols: ["AAPL", "MSFT"], startDate: "2026-05-01", endDate: "2026-08-01", params: { opening_range_minutes: 15 }, archived: false, archivedReason: null, custom: false, customPrompt: null, customRules: null, edgeVerdict: "Underpowered - MDA 11.80%/yr exceeds 2.00%/yr", lifecycleStage: "holdout_passed", validation, implementationStatus: "implemented", unavailableReason: null, requestedStartDate: "2024-01-01", requestedEndDate: "2026-08-01", measuredStartDate: "2026-05-01", measuredEndDate: "2026-08-01" };

describe("final Strategies workspace cleanup", () => {
  it("separates the execution owner, shadows, and data-blocked strategies", () => {
    const account = { available: true, brokerageLabel: "Alpaca Paper", accountId: "PAPER-1", environment: "paper", multiStrategyExecution: false, executionIsolation: "Single-strategy locked", actionRequired: false, integrityIssues: [], counts: { "BROKERAGE EXECUTION": 1, SHADOW: 4, "PROP SHADOW": 2 }, forwardStrategies: [], principle: "one owner", accountKey: "alpaca:paper:PAPER-1", owner: { strategyId: "dm_optimized_63d_daily", strategyName: "Dual Momentum", displayName: "Dual Momentum · Optimized 63D/Daily", strategyFingerprint: "locked", ownershipStartedAt: "2026-08-17", active: true, fingerprintMatches: true } } satisfies ExecutionAccountStatus;
    const owner = renderToStaticMarkup(<StrategyExecutionState strategyName="Dual Momentum" blocked={false} account={account} />);
    expect(owner).toContain("PAPER AUTOMATED"); expect(owner).toContain("Optimized 63D/Daily"); expect(owner).toContain("canonical 189D/Monthly configuration"); expect(owner).toContain("canonical DM remains a separate shadow"); expect(owner).toContain("Execution-observed");
    const shadow = renderToStaticMarkup(<StrategyExecutionState strategyName="Market-Residual Momentum" blocked={false} account={account} />);
    expect(shadow).toContain("SHADOW — NO ORDERS"); expect(shadow).toContain("Enable brokerage execution"); expect(shadow).toContain("separate account");
    const blocked = renderToStaticMarkup(<StrategyExecutionState strategyName="Earnings Momentum / Gap-Hold" blocked account={account} />);
    expect(blocked).toContain("DATA BLOCKED"); expect(blocked).not.toContain("Enable brokerage execution");
  });
  it("uses a closed switcher and concise lifecycle/power/benchmark story", () => {
    const html = renderToStaticMarkup(<StrategiesTab strategies={[strategy, { ...strategy, name: "Second strategy" }]} onRunLogged={vi.fn()} />);
    for (const text of ["Switch strategy ▾", "aria-expanded=\"false\"", "Strategy lifecycle", "Statistical power", "Benchmark evidence", "Insufficient statistical power", "Validation gates", "2 passed · 2 failed · 1 unresolved", "Browse all strategies →"]) expect(html).toContain(text);
    expect(html).not.toContain("Search strategies…");
    expect(html).not.toContain("Second strategy");
    expect(html).not.toContain("All Strategies catalog");
  });

  it("renders structural universes as locked and editable universes as an advanced disclosure", () => {
    const locked = renderToStaticMarkup(<StructuralUniverseSummary symbols={["AAPL", "MSFT"]} />);
    expect(locked).toContain("Structural universe · locked"); expect(locked).toContain("View constituents"); expect(locked).not.toContain("<select");
    const editable = renderToStaticMarkup(<EditableUniverseSummary label="Strategy default" count={29} />);
    expect(editable).toContain("Advanced universe settings"); expect(editable).toContain("29 securities"); expect(editable).not.toContain("locked");
  });

  it("shows polished execution semantics without duplicate raw metadata", () => {
    const timing = { informationAvailability: "AT_CLOSE" as const, execution: "NEXT_OPEN" as const, usesCurrentClose: true, engine: "standard", exceptionReason: null };
    const before = JSON.stringify(timing);
    const html = renderToStaticMarkup(<ExecutionSemantics timing={timing} interval="5m" />);
    for (const text of ["Signal observed", "5-minute bar close", "Earliest fill", "Next 5-minute bar open", "Lookahead-safe", "✓ Yes"]) expect(html).toContain(text);
    expect(html).not.toContain("AT_CLOSE evidence"); expect(html).not.toContain("current close used as evidence");
    expect(JSON.stringify(timing)).toBe(before);
  });

  it("humanizes the power blocker and summarizes required gates", () => {
    expect(primaryBlocker(strategy)).toEqual({ label: "Insufficient statistical power", detail: "Current history can resolve effects of roughly 11.8%/yr or larger; the research target is 2.0%/yr." });
    expect(gateCounts(validation).label).toBe("2 passed · 2 failed · 1 unresolved");
    const evidence = renderToStaticMarkup(<KeyEvidence strategy={strategy} />);
    for (const text of ["✕ Statistical power", "✓ Beats cash / risk-free", "✓ Valid trade and exposure coverage"]) expect(evidence).toContain(text);
  });

  it("keeps prior runs compact with stable IDs and evidence behind Inspect", () => {
    const row = { id: 33, runAt: "2026-08-13T00:00:00Z", startDate: "2026-05-01", endDate: "2026-08-01", requestedStartDate: "2024-08-13", requestedEndDate: "2026-08-13", measuredStartDate: "2026-06-15", measuredEndDate: "2026-08-12", interval: "5m", timing: { informationAvailability: "AT_CLOSE", execution: "NEXT_OPEN", usesCurrentClose: true, engine: "standard", exceptionReason: null }, slippageBps: 5, commissionBps: 0, searchFamily: "Opening Range Breakout (ORB):standard", familySearchNumber: 10, familySearchCount: 10, isPreregistered: true, selectedAfterResults: null, tradesTaken: 40, winRate: .55, expectancyR: .07, profitFactor: 1.14, maxDrawdownPct: -4, sharpe: .4, alphaPct: null, benchmarkGapPct: -2, benchmarkName: "SPY", benchmarkWindowStart: null, benchmarkWindowEnd: null, status: "Backtested", isCanonical: false, universeId: null, symbols: ["AAPL", "MSFT"], params: { opening_range_minutes: 30 }, edgeVerdict: "Underpowered", lifecycleStage: "preregistered", validation } satisfies HistoryRow;
    const html = renderToStaticMarkup(<RunHistory rows={[row]} onReplay={vi.fn()} />);
    for (const text of ["Run #33", "Trades", "Expectancy", "Profit factor", "Run provenance", "Power", "Inspect", "Search family: 10 recorded preregistered experiments"]) expect(html).toContain(text);
    expect(html).not.toContain("Can this design resolve the claimed edge?");
    expect(html).not.toContain("Configuration</");
    const evidence = renderToStaticMarkup(<RunEvidenceDetails row={row} registeredParams={{ opening_range_minutes: 15 }} currentParams={{ opening_range_minutes: 15 }} onReplay={vi.fn()} />);
    for (const text of ["Run identity", "Preregistered historical configuration", "Requested: 2024-08-13", "Usable: 2026-06-15", "slippage 5 bps", "experiment 10 of 10", "Changes vs registered defaults", "Opening Range Minutes: 15 → 30", "5-minute bar close", "View raw strategy performance", "5 checks"]) expect(evidence).toContain(text);
    expect(searchFamilyNote([{ ...row, familySearchCount: undefined, validation: null }])).toBeNull();
    const loaded = renderToStaticMarkup(<LoadedRunNotice runId={104} provenance="Preregistered historical configuration" />);
    expect(loaded).toContain("Loaded from Run #104"); expect(loaded).toContain("Preregistered historical configuration"); expect(loaded).toContain("does not create a new preregistration");
  });

  it("keeps lifecycle/power separate and classifies unavailable strategies as data blocked", () => {
    const blocked = { ...strategy, name: "Blocked research", implementationStatus: "unavailable" as const, unavailableReason: "PIT earnings ledger required", tradesTaken: 0, lastRun: null, edgeVerdict: null };
    const html = renderToStaticMarkup(<StrategyTable strategies={[strategy, blocked]} selected={null} onSelect={vi.fn()} />);
    for (const text of ["Research lifecycle", "Power", "Underpowered", "Data blocked", "PIT earnings ledger required", "Benchmark gap"]) expect(html).toContain(text);
  });

  it("renders the authoritative Conditional Edge state without opening workflow controls", () => {
    const row = { status: "Blocked - Insufficient Effective N", primaryBlocker: "Independent trade clusters remain below the freeze floor." } as ResearchStatusRow;
    const html = renderToStaticMarkup(<ConditionalStatusSummary row={row} />);
    expect(html).toContain("Blocked - Insufficient Effective N"); expect(html).toContain("Independent trade clusters"); expect(html).not.toContain("Run discovery");
    const never = renderToStaticMarkup(<ConditionalStatusSummary row={{ ...row, status: "Discovery Only", evidenceStage: null, lastCompletedAction: "No Conditional Edge Discovery session has been run for this strategy." }} />);
    expect(never).toContain("Not evaluated");
    const failed = renderToStaticMarkup(<ConditionalStatusSummary error="API offline" />);
    expect(failed).toContain("Unable to load research status");
  });
});
