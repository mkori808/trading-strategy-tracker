import { useEffect, useState } from "react";
import { api, type MarketClock, type MarketResponse } from "../api";
import { GaugeDial } from "./GaugeDial";
import { sectorName } from "../sectorNames";
import { TAB_GROUPS, TABS, type Tab } from "../tabs";

function fmtRelative(date: Date | null): string {
  if (!date) return "never";
  const mins = Math.round((Date.now() - date.getTime()) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  return `${Math.round(mins / 60)}h ago`;
}

function ChangeValue({ v }: { v: number | null }) {
  if (v === null) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  return (
    <span
      className="tabular-nums"
      style={{ color: v >= 0 ? "var(--status-good)" : "var(--status-critical)" }}
    >
      {v >= 0 ? "+" : ""}
      {v.toFixed(2)}%
    </span>
  );
}

/** Persistent left sidebar -- benchmark/sector snapshot + a mini breadth
 * gauge, following the pre-existing degrade-gracefully convention (blank/
 * "—" states, never an error, when /api/market or /api/live/account aren't
 * ready yet or Alpaca isn't configured). `marketData` is fetched ONCE at
 * the App level and shared with MarketView -- see App.tsx -- since a cold
 * /api/market call scans the full 94-symbol research universe and can take
 * up to ~40s; this sidebar must never trigger that scan itself. */
/** DOM id of the sidebar slot that hosts the Strategies tab config panel. */
export const STRATEGY_CONFIG_SLOT = "strategy-config-slot";

export function Sidebar({
  marketData,
  marketLoading,
  lastUpdated,
  activeTab,
  onSelectTab,
}: {
  marketData: MarketResponse | null;
  marketLoading: boolean;
  lastUpdated: Date | null;
  activeTab: Tab;
  onSelectTab: (tab: Tab) => void;
}) {
  const [clock, setClock] = useState<MarketClock | null>(null);

  useEffect(() => {
    api
      .liveAccount()
      .then((res) => setClock(res.clock))
      .catch(() => {});
  }, []);

  const spyRow = marketData?.sectorPerformance.find((r) => r.symbol === "SPY");
  const sectorRows = marketData?.sectorPerformance.filter((r) => r.symbol !== "SPY") ?? [];

  return (
    <aside
      className="hidden w-60 shrink-0 flex-col gap-6 border-r px-4 py-6 lg:flex"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div>
        <div className="text-sm font-bold" style={{ color: "var(--text-primary)" }}>
          Trading Strategy Lab
        </div>
        <div className="mt-1.5 flex items-center gap-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
          <span
            aria-hidden="true"
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: clock?.isOpen ? "var(--status-good)" : "var(--text-muted)" }}
          />
          {clock === null ? "Market status unknown" : clock.isOpen ? "Markets open" : "Markets closed"}
        </div>
      </div>

      <nav className="flex flex-col gap-3">
        {TAB_GROUPS.map((group) => (
          <div key={group}>
            <div
              className="mb-1 px-3 text-[10px] font-semibold tracking-wide"
              style={{ color: "var(--text-muted)" }}
            >
              {group.toUpperCase()}
            </div>
            <div className="flex flex-col gap-0.5">
              {TABS.filter((t) => t.group === group).map((t) => (
                <button
                  key={t.key}
                  type="button"
                  onClick={() => onSelectTab(t.key)}
                  className="rounded-md px-3 py-2 text-left text-sm font-medium transition-colors"
                  style={{
                    background: activeTab === t.key ? "var(--pill-bg-active)" : "transparent",
                    color: activeTab === t.key ? "#ffffff" : "var(--text-secondary)",
                  }}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div>
        <div className="mb-2 text-xs font-semibold tracking-wide" style={{ color: "var(--text-muted)" }}>
          BENCHMARK
        </div>
        {spyRow ? (
          <div className="flex items-center justify-between text-sm">
            <span style={{ color: "var(--text-primary)" }}>SPY</span>
            <ChangeValue v={spyRow.changePct} />
          </div>
        ) : (
          <div className="text-xs" style={{ color: "var(--text-muted)" }}>
            {marketLoading ? "Loading…" : "—"}
          </div>
        )}
      </div>

      <div>
        <div className="mb-2 text-xs font-semibold tracking-wide" style={{ color: "var(--text-muted)" }}>
          SECTORS
        </div>
        <div className="space-y-1.5">
          {sectorRows.length === 0 && (
            <div className="text-xs" style={{ color: "var(--text-muted)" }}>
              {marketLoading ? "Loading…" : "—"}
            </div>
          )}
          {sectorRows.map((r) => (
            <div key={r.symbol} className="flex items-center justify-between text-xs">
              <span style={{ color: "var(--text-secondary)" }}>{sectorName(r.symbol)}</span>
              <ChangeValue v={r.changePct} />
            </div>
          ))}
        </div>
      </div>

      <div>
        <div className="mb-1 text-xs font-semibold tracking-wide" style={{ color: "var(--text-muted)" }}>
          MARKET BREADTH
        </div>
        <GaugeDial
          value={marketData?.marketSignals.score ?? null}
          label="Breadth score"
          size="mini"
        />
      </div>

      {/* Slot for the Strategies tab's run configuration. Filled by a PORTAL
        * from StrategiesTab rather than by lifting its state up here: the
        * config owns selected strategy, override draft, running/error state and
        * the run handler, all of which are meaningless to the sidebar. A portal
        * moves only the rendered output, so ownership stays where the logic is.
        * Empty (and zero-height) on every other tab. */}
      <div id={STRATEGY_CONFIG_SLOT} className="empty:hidden" />

      <div className="mt-auto text-xs" style={{ color: "var(--text-muted)" }}>
        {marketLoading ? "Scanning research universe…" : `Updated ${fmtRelative(lastUpdated)}`}
      </div>
    </aside>
  );
}
