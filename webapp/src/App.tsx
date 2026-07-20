import { useEffect, useRef, useState } from "react";
import {
  api,
  type MarketResponse,
  type StrategySummary,
} from "./api";
import { Sidebar } from "./components/Sidebar";
import { StrategiesTab } from "./components/StrategiesTab";
import { SymbolsView } from "./components/SymbolsView";
import { MarketView } from "./components/MarketView";
import { ScreenerView } from "./components/ScreenerView";
import { MoversView } from "./components/MoversView";
import { LiveMonitorView } from "./components/LiveMonitorView";
import type { Tab } from "./tabs";

function App() {
  const [strategies, setStrategies] = useState<StrategySummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("strategies");

  // Fetched ONCE at this level and shared by Sidebar + MarketView -- a cold
  // /api/market call scans the full 94-symbol research universe (see
  // CLAUDE.md's "Research platform" section) and can take up to ~40s.
  // Fetching it per-tab-switch or per-component would multiply that cost;
  // this is the one place it's requested, on mount, for the whole session.
  const [marketData, setMarketData] = useState<MarketResponse | null>(null);
  const [marketLoading, setMarketLoading] = useState(false);
  const [marketLastUpdated, setMarketLastUpdated] = useState<Date | null>(null);
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
      .then((res) => {
        setMarketData(res);
        setMarketLastUpdated(new Date());
      })
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
    <div className="flex min-h-screen">
      <Sidebar
        marketData={marketData}
        marketLoading={marketLoading}
        lastUpdated={marketLastUpdated}
        activeTab={tab}
        onSelectTab={setTab}
      />

      <div className="mx-auto w-full max-w-6xl px-6 py-8">
        <header className="mb-6">
          <details>
            <summary className="cursor-pointer text-xs" style={{ color: "var(--text-muted)" }}>
              About this tool
            </summary>
            <p className="mt-1 max-w-3xl text-xs" style={{ color: "var(--text-muted)" }}>
              Backtests run against a pre-registered symbol universe by default. Strategies
              under 30 trades are flagged "sample too small" — treat those numbers as
              directional, not conclusive. Day-trading strategies use ~60 days of 5-min bars
              (yfinance's intraday history limit); swing strategies use 5 years of daily bars.
              The <strong>Strategies</strong> tab lets you test variations — custom symbols,
              date ranges, and rule parameters — against any strategy; those runs are tagged as
              experiments and never replace the strategy's canonical (registered-default)
              result shown in the leaderboard.
            </p>
          </details>
        </header>

        {loadError && (
          <div
            className="mb-6 rounded-lg border px-4 py-3 text-sm"
            style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
          >
            Failed to load strategies: {loadError}. Is the API running (uvicorn api.main:app)?
          </div>
        )}

        {tab === "symbols" && <SymbolsView />}
        {tab === "market" && (
          <MarketView
            data={marketData}
            loading={marketLoading}
            error={marketError}
            onRefresh={loadMarket}
          />
        )}
        {tab === "screener" && <ScreenerView />}
        {tab === "movers" && <MoversView />}
        {tab === "monitor" && <LiveMonitorView />}
        {tab === "strategies" && (
          <StrategiesTab strategies={strategies} onRunLogged={loadStrategies} />
        )}
      </div>
    </div>
  );
}

export default App;
