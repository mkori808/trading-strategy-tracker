import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("../useResource", () => ({
  useResource: () => ({
    error: null,
    loading: false,
    data: {
      available: true,
      reason: null,
      startDate: "2026-08-17",
      benchmarkSymbol: "SPY",
      rows: [
        { date: "2026-08-17", equity: 101000, profitLoss: 1000, profitLossPct: 1, benchmarkPct: -0.5 },
        { date: "2026-08-18", equity: 99000, profitLoss: -2000, profitLossPct: -1.98, benchmarkPct: -1 },
        { date: "2026-08-19", equity: 102000, profitLoss: 3000, profitLossPct: 3.03, benchmarkPct: 2 },
      ],
      today: null,
      forwardBaselineEquity: 100500,
      forwardReturnPct: 1.49,
      forwardReturnSource: "locked inception",
      maturity: { label: "Too Early to Evaluate", reached: [], next: { sessions: 20, label: "Operational sanity only" } },
      benchmarkComparison: { available: false, reason: "Intraday inception baseline." },
      alignedBenchmarkComparison: {
        available: true,
        reason: null,
        baselineDate: "2026-08-17",
        startDate: "2026-08-18",
        endDate: "2026-08-19",
        sessions: 2,
        accountReturnPct: 0.99,
        benchmarkReturnPct: 0.98,
        differencePctPoints: 0.01,
        accountMaxDrawdownPct: -1.98,
        benchmarkMaxDrawdownPct: -1,
        growth: [
          { date: "2026-08-17", account: 100, benchmark: 100, baseline: true },
          { date: "2026-08-18", account: 98.02, benchmark: 99, baseline: false },
          { date: "2026-08-19", account: 100.99, benchmark: 100.98, baseline: false },
        ],
        note: "Settled full sessions only; intraday inception and today are excluded.",
      },
    },
  }),
}));

import { DailyPerformancePanel } from "./DailyPerformancePanel";

describe("DailyPerformancePanel aligned benchmark comparison", () => {
  it("shows account, SPY, gap, session count, and normalized chart framing", () => {
    const html = renderToStaticMarkup(<DailyPerformancePanel />);
    expect(html).toContain("Paper account · aligned");
    expect(html).toContain("SPY · aligned");
    expect(html).toContain("+0.01pp");
    expect(html).toContain("Max drawdown · paper / SPY");
    expect(html).toContain("-1.98% / -1.00%");
    expect(html).toContain("Settled through 2026-08-19 · 2 aligned sessions");
    expect(html).toContain("Normalized growth · settled sessions");
    expect(html).toContain("Both series start at 100 after the intraday inception session");
  });
});
