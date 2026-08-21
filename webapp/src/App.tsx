import { useEffect, useRef, useState } from "react";
import { api, type MarketResponse, type StrategySummary } from "./api";
import { DashboardView } from "./components/DashboardView";
import { StrategiesTab } from "./components/StrategiesTab";
import { StrategySidebar } from "./components/StrategySidebar";
import { TopBar } from "./components/TopBar";
import type { Tab } from "./tabs";

function App() {
  const [strategies, setStrategies] = useState<StrategySummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("dashboard");

  // Fetched ONCE at this level and shared by the dashboard's status strip,
  // its market card, and the market popup -- a cold /api/market call scans
  // the full 94-symbol research universe (see CLAUDE.md's "Research
  // platform" section) and can take up to ~40s. Fetching it per-component
  // would multiply that cost; this is the one place it's requested, on
  // mount, for the whole session. Every OTHER endpoint shares through the
  // cache in useResource.ts instead; this one predates it and stays here
  // because it must not be re-triggered by a popup opening.
  const [marketData, setMarketData] = useState<MarketResponse | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketError, setMarketError] = useState<string | null>(null);

  const loadStrategies = () => {
    api
      .listStrategies()
      .then((rows) => {
        setStrategies(rows);
        setLoadError(null);
      })
      .catch((e) => setLoadError(String(e)));
  };

  const loadMarket = () => {
    setMarketLoading(true);
    setMarketError(null);
    api
      .market()
      .then(setMarketData)
      .catch((e) => setMarketError(String(e)))
      .finally(() => setMarketLoading(false));
  };

  // StrictMode double-invokes mount effects in dev (React's intentional
  // "flush out non-idempotent effects" check) -- harmless for the cheap
  // loadStrategies() call, but /api/market is a real ~40s 94-symbol scan,
  // so a bare useEffect(loadMarket, []) would fire it TWICE on every real
  // `npm run dev` session (this project's actual day-to-day workflow, not
  // just a build step -- see CLAUDE.md). Guard with a ref so the second
  // StrictMode invocation is a no-op instead of a second concurrent scan.
  const marketFetchedRef = useRef(false);
  useEffect(loadStrategies, []);
  useEffect(() => {
    if (marketFetchedRef.current) return;
    marketFetchedRef.current = true;
    loadMarket();
  }, []);

  return (
    <div className="min-h-screen">
      <TopBar activeTab={tab} onSelectTab={setTab} />

      <div className="flex">
        {/* Rendered only on Strategies, and in the same commit as the page
          * itself, so StrategiesTab's portal finds the slot on mount. */}
        {tab === "strategies" && <StrategySidebar />}

        {/* min-w-0 + flex-1: without them a `w-full` flex child refuses to
          * shrink below its content, and sidebar + main together overflowed
          * the viewport -- the whole PAGE scrolled sideways instead of the
          * one wide table inside it. */}
        <main className="mx-auto w-full min-w-0 max-w-7xl flex-1 px-6 py-6">
          {loadError && (
            <div
              className="mb-6 rounded-lg border px-4 py-3 text-sm"
              style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
            >
              Failed to load strategies: {loadError}. Is the API running (uvicorn api.main:app)?
            </div>
          )}

          {tab === "dashboard" && (
            <DashboardView
              marketData={marketData}
              marketLoading={marketLoading}
              marketError={marketError}
              onRefreshMarket={loadMarket}
            />
          )}

          {tab === "strategies" && (
            <>
              <details className="mb-5">
                <summary className="cursor-pointer text-xs" style={{ color: "var(--text-muted)" }}>
                  About backtests here
                </summary>
                <p className="mt-1 max-w-3xl text-xs" style={{ color: "var(--text-muted)" }}>
                  Backtests run against a pre-registered symbol universe by default. Strategies
                  under 30 trades are flagged "sample too small" — treat those numbers as
                  directional, not conclusive. Day-trading strategies use 5-min bars (limited to
                  the window the data provider serves); swing strategies use 5 years of daily
                  bars. You can test variations — custom symbols, date ranges, and rule
                  parameters — against any strategy; those runs are tagged as experiments and
                  never replace the strategy's canonical (registered-default) result shown in
                  the leaderboard.
                </p>
              </details>
              <StrategiesTab strategies={strategies} onRunLogged={loadStrategies} />
            </>
          )}
        </main>
      </div>
    </div>
  );
}

export default App;
