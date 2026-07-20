import { useState } from "react";
import type { MarketResponse } from "../api";
import { SectorPerformanceChart } from "./SectorPerformanceChart";
import { SectorHeatTiles } from "./SectorHeatTiles";
import { GaugeDial } from "./GaugeDial";
import { StatTile } from "./StatTile";

const REGIME_COLOR: Record<string, string> = {
  Bullish: "var(--status-good)",
  Neutral: "var(--status-warning)",
  Bearish: "var(--status-critical)",
};

// Mirrors engine/trend_template.py's CRITERIA list, in human-readable form.
const CRITERION_LABEL: Record<string, string> = {
  above_150_and_200: "Close > 150/200 SMA",
  sma150_above_sma200: "150 SMA > 200 SMA",
  sma200_rising: "200 SMA rising",
  sma50_above_150_and_200: "50 SMA > 150/200 SMA",
  above_sma50: "Close > 50 SMA",
  above_52w_low: "25%+ above 52w low",
  near_52w_high: "Within 25% of 52w high",
  rs_beats_benchmark: "RS beats SPY (12mo)",
};

function criterionLabel(name: string): string {
  return CRITERION_LABEL[name] ?? name;
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

/** Data is fetched ONCE at the App level and shared with Sidebar (see
 * App.tsx) -- a cold /api/market call scans the full 94-symbol research
 * universe and can take up to ~40s, so this view must not re-fetch it on
 * its own; "Refresh" re-triggers the shared App-level fetch instead. */
export function MarketView({
  data,
  loading,
  error,
  onRefresh,
}: {
  data: MarketResponse | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}) {
  const [showAllSymbols, setShowAllSymbols] = useState(false);

  if (error && !data) {
    return (
      <div
        className="rounded-lg border px-4 py-3 text-sm"
        style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
      >
        Failed to load market data: {error}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="text-sm" style={{ color: "var(--text-muted)" }}>
        Scanning market data… this takes up to ~40s the first time (94-symbol breadth scan).
      </div>
    );
  }

  const { regime, sectorPerformance, sectorRotation, trendTemplate, marketSignals } = data;
  const regimeColor = REGIME_COLOR[regime.current] ?? "var(--text-muted)";
  const dist = regime.distribution;
  const failingSymbols = trendTemplate.symbols.filter((s) => !s.passes);
  const visibleFailing = showAllSymbols ? failingSymbols : failingSymbols.slice(0, 8);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Market overview
        </h2>
        <button
          type="button"
          onClick={onRefresh}
          disabled={loading}
          className="rounded-md border px-3 py-1.5 text-xs font-medium disabled:opacity-50"
          style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
        >
          {loading ? "Scanning…" : "Refresh"}
        </button>
      </div>

      <div
        className="rounded-lg border p-4"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="flex items-baseline gap-2">
            <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
              SPY regime
            </span>
            <span className="text-xl font-semibold" style={{ color: regimeColor }}>
              {regime.current}
            </span>
          </div>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            as of {fmtDate(regime.asOf)}
          </span>
        </div>
        <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
          New long entries are gated to Bullish regimes only in the pre-trade filter layer
          (not applied to canonical backtests or live signals below — see Compare tab). A
          regime flip never force-closes an open position.
        </p>

        <div className="mt-3 flex h-3 overflow-hidden rounded-full" style={{ background: "var(--gridline)" }}>
          {(["Bullish", "Neutral", "Bearish"] as const).map((state) => (
            <div
              key={state}
              style={{
                width: `${(dist[state] ?? 0) * 100}%`,
                background: REGIME_COLOR[state],
              }}
              title={`${state}: ${((dist[state] ?? 0) * 100).toFixed(0)}%`}
            />
          ))}
        </div>
        <div className="mt-1.5 flex gap-4 text-xs" style={{ color: "var(--text-muted)" }}>
          {(["Bullish", "Neutral", "Bearish"] as const).map((state) => (
            <span key={state} className="flex items-center gap-1">
              <span
                aria-hidden="true"
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: 4,
                  background: REGIME_COLOR[state],
                  display: "inline-block",
                }}
              />
              {state} {((dist[state] ?? 0) * 100).toFixed(0)}%
            </span>
          ))}
        </div>
        <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
          Share of the last ~90 sessions spent in each regime — a distribution near 0%/100%
          for any one state means the gate isn't selective over this window.
        </p>
      </div>

      <div
        className="rounded-lg border p-4"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div>
            <div className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
              Market Signals — breadth score
            </div>
            <p className="mt-1 max-w-md text-xs" style={{ color: "var(--text-muted)" }}>
              {marketSignals.methodology}
            </p>
            <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
              {marketSignals.symbolsTracked} symbols tracked, as of {fmtDate(marketSignals.asOf)}
            </p>
          </div>
          <GaugeDial value={marketSignals.score} label="Breadth score" />
        </div>
        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            label="Above 50-day SMA"
            value={marketSignals.components.pctAboveSma50 === null ? "—" : `${marketSignals.components.pctAboveSma50.toFixed(0)}%`}
          />
          <StatTile
            label="Above 200-day SMA"
            value={marketSignals.components.pctAboveSma200 === null ? "—" : `${marketSignals.components.pctAboveSma200.toFixed(0)}%`}
          />
          <StatTile label="New 20d highs" value={String(marketSignals.newHighs20d)} />
          <StatTile label="New 20d lows" value={String(marketSignals.newLows20d)} />
        </div>
      </div>

      <SectorPerformanceChart rows={sectorPerformance} />

      <div>
        <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          Sector Rotation — relative strength vs. SPY ({sectorRotation.lookbackDays}d)
        </div>
        <p className="mb-3 text-xs" style={{ color: "var(--text-muted)" }}>
          RS = sector's trailing return ÷ SPY's trailing return over the same window. RS &gt; 1
          means the sector outperformed SPY; the arrow compares today's RS to ~1 week ago.
        </p>
        <SectorHeatTiles rows={sectorRotation.rows} />
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatTile label="Trend template pass rate" value={`${(trendTemplate.passRate * 100).toFixed(0)}%`} />
        <StatTile label="Symbols passing" value={String(trendTemplate.passCount)} />
        <StatTile label="Symbols failing" value={String(trendTemplate.failCount)} />
        <StatTile label="Scanned as of" value={fmtDate(trendTemplate.asOf)} />
      </div>

      <div>
        <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          Minervini Trend Template — symbols failing today ({failingSymbols.length})
        </div>
        <div
          className="overflow-x-auto rounded-lg border"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
        >
          <table className="w-full min-w-[560px] border-collapse text-sm">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
                {["Symbol", "Failed criteria"].map((h) => (
                  <th
                    key={h}
                    className="px-4 py-2 text-left font-medium whitespace-nowrap"
                    style={{ color: "var(--text-muted)" }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleFailing.map((s) => (
                <tr key={s.symbol} style={{ borderBottom: "1px solid var(--gridline)" }}>
                  <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>
                    {s.symbol}
                  </td>
                  <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>
                    {s.failedCriteria.map(criterionLabel).join(", ")}
                  </td>
                </tr>
              ))}
              {failingSymbols.length === 0 && (
                <tr>
                  <td className="px-4 py-3 text-sm" colSpan={2} style={{ color: "var(--text-muted)" }}>
                    Every tracked symbol passes today.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        {failingSymbols.length > visibleFailing.length && (
          <button
            type="button"
            onClick={() => setShowAllSymbols(true)}
            className="mt-2 text-xs font-medium"
            style={{ color: "var(--series-1)" }}
          >
            Show all {failingSymbols.length}
          </button>
        )}
      </div>
    </div>
  );
}
