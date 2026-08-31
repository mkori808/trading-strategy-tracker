import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { MarketResponse } from "../api";
import { KEYS } from "../resourceKeys";

const resources: Record<string, unknown> = {
  [KEYS.liveAccount]: {
    account: { available: true, equity: 102_750, lastEquity: 102_000, buyingPower: 200_000 },
    positions: [{ symbol: "BLFS", side: "long", qty: 1, avgEntryPrice: 1, currentPrice: 2, marketValue: 2, unrealizedPl: 1, unrealizedPlPct: 100 }],
    orders: Array.from({ length: 35 }, (_, i) => ({ id: String(i), status: "filled" })),
    orderSummary: { openPending: 0, filled: 35, canceled: 0, rejected: 0, recentHistory: 35 },
    clock: { available: true, isOpen: false },
  },
  [KEYS.executionConfig]: [{
    strategyName: "Dual Momentum", enabled: true, enabledAt: "2026-08-14", params: {},
    universeId: null, symbols: Array.from({ length: 26 }, (_, i) => `S${i}`), validationRunId: 33,
    overrideUsed: true, overrideReason: "paper", overrideBlockers: [], inception: { policy: "adopt", status: "initialized", validationRunId: 33, inceptionAt: "2026-08-17", equity: 101_535, inheritedPositions: [], legacyDefault: false },
    identity: { key: "dm_optimized_63d_daily", displayName: "Dual Momentum · Optimized 63D/Daily", shortName: "DM Optimized 63D/Daily", variant: "optimized", provenance: "Historically optimized · requires OOS confirmation", selectionContext: "Selected from config search", selectionBiasNote: "Historical development evidence is not independent validation.", expectedConfig: { lookback_trading_days: 63, top_n: 5, rebalance_frequency: "daily" }, actualConfig: { lookback_trading_days: 63, top_n: 5, rebalance_frequency: "daily" }, expectedSymbolCount: 26, actualSymbolCount: 26, fingerprintMatches: true, fingerprintReason: null },
  }],
  [KEYS.executionDaily]: { available: true, reason: null, startDate: "2026-08-17", benchmarkSymbol: "SPY", rows: Array.from({ length: 5 }, (_, i) => ({ date: `2026-08-${17 + i}`, equity: 100_000, profitLoss: 0, profitLossPct: 0, benchmarkPct: 0 })), today: null, forwardBaselineEquity: 101_535, forwardReturnPct: 1.2, forwardReturnSource: "Alpaca paper equity / locked inception equity", maturity: { label: "Too Early to Evaluate", reached: [], next: { sessions: 20, label: "Operational sanity only" } }, benchmarkComparison: { available: false, reason: "The paper inception mark occurred intraday, without an aligned SPY baseline." }, alignedBenchmarkComparison: { available: true, reason: null, baselineDate: "2026-08-17", startDate: "2026-08-18", endDate: "2026-08-21", sessions: 4, accountReturnPct: 0.95, benchmarkReturnPct: -0.85, differencePctPoints: 1.8, accountMaxDrawdownPct: -1.98, benchmarkMaxDrawdownPct: -1.3, growth: [{ date: "2026-08-17", account: 100, benchmark: 100, baseline: true }, { date: "2026-08-21", account: 100.95, benchmark: 99.15, baseline: false }], note: "Settled full sessions only." } },
  [KEYS.killSwitch]: { active: false },
  [KEYS.researchStatus]: { summary: { activeResearchRuns: 0, frozenForwardTestCandidates: 1, validatedStrategies: 0, rejectedHypotheses: 2, dataBlockedResearchItems: 0, methodologyCompleteSystems: 1 }, statusLabels: [], rows: [
    { name: "Dual Momentum · Optimized 63D/Daily", researchType: "Operational paper strategy", status: "Paper Forward Test" },
    { name: "Canonical Dual Momentum · 189D/Monthly", researchType: "Research shadow strategy", status: "Shadow Forward Testing" },
    { name: "Market-Residual Momentum", researchType: "Research shadow strategy", status: "Shadow Forward Testing" },
    { name: "Fixed 50/50 DM/MRM", researchType: "Research shadow strategy", status: "Shadow Forward Testing" },
    { name: "DM/MRM Volatility-Scaled Portfolio", researchType: "Frozen forward-test overlay", status: "Frozen — Forward Testing" },
    { name: "Dual Momentum - One-Rebalance Winner Grace v1", researchType: "Strategy modification", status: "Modification Unsupported / Closed" },
  ] },
  [KEYS.researchForwardStack]: { alerts: [], operations: { lastAttemptAt: new Date().toISOString(), lastSuccessAt: new Date().toISOString(), latestCompletedSession: "2026-08-21" }, maturity: { label: "Too Early to Evaluate", reached: [], next: { sessions: 20, label: "Operational sanity only" } }, separation: { alpacaEquityLabel: "Alpaca paper account equity", researchNavLabel: "Normalized strategy forward NAV" }, paperAutomation: { enabled: true, canonicalConfig: false }, series: ["dm", "mrm", "fiftyFifty", "volScaled", "spy"].map((key) => ({ key, series: key, type: "shadow", status: "Too Early to Evaluate", sessions: 0, nav: null, returnPct: null, drawdownPct: null, lastUpdate: null })), currentBlend: { dmWeight: .38, mrmWeight: .62, dmTargetWeight: .38, mrmTargetWeight: .62, lastWeightResetDate: "2026-08-03", nextScheduledReset: "2026-09-01", combinedHoldings: {} } },
};

vi.mock("../useResource", () => ({
  useResource: (key: string) => ({ data: resources[key] ?? null, error: null, loading: false, fetchedAt: 1, refresh: vi.fn() }),
}));
vi.mock("../dataHooks", () => ({
  useMovers: () => ({ data: null, error: null, loading: false }), useInsider: () => ({ data: null, error: null, loading: false }),
  useScreener: () => ({ data: null, error: null, loading: false }), useSymbols: () => ({ data: null, error: null, loading: false }),
}));

import { DashboardView } from "./DashboardView";

const market = { regime: { current: "Bullish", asOf: "2026-08-21" }, marketSignals: { score: 55, symbolsTracked: 94 }, sectorPerformance: [{ symbol: "SPY", changePct: 1 }], trendTemplate: { symbols: [] } } as unknown as MarketResponse;

describe("DashboardView status hierarchy", () => {
  it("renders explicit paper/shadow identities, integrity, order semantics, and frozen blend", () => {
    const html = renderToStaticMarkup(<DashboardView marketData={market} marketLoading={false} marketError={null} onRefreshMarket={vi.fn()} />);
    expect(html).toContain("OPERATIONAL INTEGRITY");
    expect(html).toContain("Healthy");
    expect(html).toContain("DM Optimized 63D/Daily");
    expect(html).toContain("Historically optimized · requires OOS confirmation");
    expect(html).toContain("Selected from config search");
    expect(html).toContain("Canonical Dual Momentum · 189D/Monthly");
    expect(html).toContain("PAPER AUTOMATED");
    expect(html).toContain("SHADOW");
    expect(html).toContain("0 open/pending orders");
    expect(html).not.toContain("35 open orders");
    expect(html).toContain("5 sessions · Too early");
    expect(html).toContain("0 · Not started");
    expect(html).toContain("+1.20%");
    expect(html).toContain("Aligned full sessions");
    expect(html).toContain("+1.80pp");
    expect(html).toContain("Max drawdown: paper");
    expect(html).toContain("4 aligned sessions");
    expect(html).toContain("intraday inception and today are excluded");
    expect(html.match(/\$102,750/g)?.length).toBe(1);
    expect(html).toContain("Open all research");
    expect(html).toContain("Modification Unsupported / Closed");
    expect(html).toMatch(/<button[^>]*>[\s\S]*Dual Momentum - One-Rebalance Winner Grace v1[\s\S]*<\/button>/);
    expect(html).toContain("sm:grid-cols-[minmax(0,1fr)_auto_minmax(11rem,auto)_auto]");
    expect(html).toContain("Frozen vol-scaled target");
    expect(html).toContain("Opportunity Monitor");
  });

  it("warns when the shadow scheduler freshness exceeds its expected interval", () => {
    const stack = resources[KEYS.researchForwardStack] as { operations: { lastAttemptAt: string } };
    const current = stack.operations.lastAttemptAt;
    stack.operations.lastAttemptAt = "2026-08-01T00:00:00-04:00";
    const html = renderToStaticMarkup(<DashboardView marketData={market} marketLoading={false} marketError={null} onRefreshMarket={vi.fn()} />);
    expect(html).toContain("Shadow-forward scheduler has not checked within the expected interval");
    expect(html).toContain("Warning");
    stack.operations.lastAttemptAt = current;
  });

  it("surfaces a persisted stale-ledger alert without treating small samples as failures", () => {
    const stack = resources[KEYS.researchForwardStack] as { alerts: { code: string; severity: string; message: string }[] };
    stack.alerts.push({ code: "stale_forward_state", severity: "warning", message: "Forward state trails completed session 2026-08-21" });
    const html = renderToStaticMarkup(<DashboardView marketData={market} marketLoading={false} marketError={null} onRefreshMarket={vi.fn()} />);
    expect(html).toContain("Forward state trails completed session 2026-08-21");
    expect(html).not.toContain("small sample failure");
    stack.alerts.pop();
  });

  it("scopes research-shadow failures as warnings rather than live action-required failures", () => {
    const stack = resources[KEYS.researchForwardStack] as { alerts: { code: string; severity: string; message: string; scope?: "research_shadow" }[] };
    stack.alerts.push({ code: "research_shadow_failure", severity: "error", scope: "research_shadow", message: "Corporate-action reconciliation required" });
    const html = renderToStaticMarkup(<DashboardView marketData={market} marketLoading={false} marketError={null} onRefreshMarket={vi.fn()} />);
    expect(html).toContain("Research shadow reconciliation: Corporate-action reconciliation required");
    expect(html).toContain("Warning");
    expect(html).not.toContain("Action required");
    stack.alerts.pop();
  });
});
