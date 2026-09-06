import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ExecutionCalibrationSummary, PropShadowRows } from "./LiveMonitorView";

describe("ExecutionCalibrationSummary", () => {
  it("renders persisted execution quality after the calibration gate", () => {
    const html = renderToStaticMarkup(<ExecutionCalibrationSummary calibration={{ symbol: null, fills: 30, minimumFills: 5, calibrated: true, medianAdverseSlippageBps: 9.259, p95AdverseSlippageBps: 217.5, meanFillRatio: .996, partialFillRate: .3667 }} />);
    expect(html).toContain("30 fills");
    expect(html).toContain("9.3 bps");
    expect(html).toContain("partial fills 36.7%");
    expect(html).toContain("no realized-vs-assumed verdict");
  });

  it("labels a sub-threshold sample as immature", () => {
    const html = renderToStaticMarkup(<ExecutionCalibrationSummary calibration={{ symbol: null, fills: 3, minimumFills: 5, calibrated: false, medianAdverseSlippageBps: 2, p95AdverseSlippageBps: 4, meanFillRatio: 1, partialFillRate: 0 }} />);
    expect(html).toContain("Calibration immature — 3/5 required fills");
  });
});

describe("PropShadowRows", () => {
  it("keeps both frozen scales and prospective accounting visible", () => {
    const html = renderToStaticMarkup(<PropShadowRows data={{ program: "Optimized DM Prop Shadows", evidenceClass: "prospective_prop_shadow", historicalEvidenceKeptSeparate: true, samplingIntervalSeconds: 300, lastSnapshot: null, samplingAgeSeconds: null, completedSessions: 0, maturity: "Not started", warnings: [], controlsPreserved: ["Canonical DM"], fingerprintLocked: true, selfFundedFingerprintLocked: true, selfFundedShadows: [], shadows: [
      { key: "a", label: "Conservative", scale: .20, drawdownRule: "trailing_to_breakeven", sessions: 0, state: "Challenge", currentEquity: 100000, netPayout: -500, grossPayout: 0, fees: 500, breaches: 0, attempts: 1, failures: 0, drawdownUtilization: 0, maximumObservedDrawdown: 0, dailyLimitUtilization: 0, annualizedBreachFrequency: null, distanceToDailyBreach: 3000, distanceToDrawdownBreach: 6000 },
      { key: "b", label: "Static reference", scale: .25, drawdownRule: "static", sessions: 0, state: "Challenge", currentEquity: 100000, netPayout: -500, grossPayout: 0, fees: 500, breaches: 0, attempts: 1, failures: 0, drawdownUtilization: 0, maximumObservedDrawdown: 0, dailyLimitUtilization: 0, annualizedBreachFrequency: null, distanceToDailyBreach: 3000, distanceToDrawdownBreach: 6000 },
    ] }} />);
    expect(html).toContain("Conservative");
    expect(html).toContain("0.20x");
    expect(html).toContain("Static reference");
    expect(html).toContain("0.25x");
    expect(html).toContain("-$500");
  });
});
