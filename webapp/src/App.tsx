import { useEffect, useRef, useState } from "react";
import {
  api,
  type BacktestOverrides,
  type BacktestResult,
  type CrossSectionalResponse,
  type HistoryRow,
  type MarketResponse,
  type PairsResponse,
  type PortfolioHistoryRow,
  type StrategySummary,
} from "./api";
import { Sidebar } from "./components/Sidebar";
import { CrossSectionalResultView } from "./components/CrossSectionalResultView";
import { PairsResultView } from "./components/PairsResultView";
import { PortfolioRunHistory } from "./components/PortfolioRunHistory";
import { StrategyTable } from "./components/StrategyTable";
import { StrategyPicker } from "./components/StrategyPicker";
import { RunConfigPanel } from "./components/RunConfigPanel";
import { ResultTabs } from "./components/ResultTabs";
import { StatTile } from "./components/StatTile";
import { StatusPill } from "./components/StatusPill";
import { EquityChart } from "./components/EquityChart";
import { TradesTable } from "./components/TradesTable";
import { RunHistory } from "./components/RunHistory";
import { MetricsHistoryChart } from "./components/MetricsHistoryChart";
import { PortfolioPanel } from "./components/PortfolioPanel";
import { PerSymbolTable } from "./components/PerSymbolTable";
import { SymbolsView } from "./components/SymbolsView";
import { MarketView } from "./components/MarketView";
import { ScreenerView } from "./components/ScreenerView";
import { MoversView } from "./components/MoversView";
import { LiveMonitorView } from "./components/LiveMonitorView";
import type { Tab } from "./tabs";

const DAY_TRADING_CAPTION = "Day-trading strategy: backtests the last ~60 days of 5-min bars.";
const SWING_TRADING_CAPTION = "Swing-trading strategy: backtests the last 5 years of daily bars.";

function pct(v: number, digits = 1): string {
  return `${(v * 100).toFixed(digits)}%`;
}

function App() {
  const [strategies, setStrategies] = useState<StrategySummary[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("lab");

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
              The <strong>Lab</strong> tab lets you test variations — custom symbols, date
              ranges, and rule parameters — against any strategy; those runs are tagged as
              experiments and never replace the strategy's canonical (registered-default)
              result shown in <strong>Compare</strong>.
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
        {tab === "lab" && <LabTab strategies={strategies} onRunLogged={loadStrategies} />}
        {tab === "compare" && <CompareTab strategies={strategies} onRunLogged={loadStrategies} />}
      </div>
    </div>
  );
}

function LabTab({
  strategies,
  onRunLogged,
}: {
  strategies: StrategySummary[];
  onRunLogged: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [csResult, setCsResult] = useState<CrossSectionalResponse | null>(null);
  const [pairsResult, setPairsResult] = useState<PairsResponse | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [portfolioHistory, setPortfolioHistory] = useState<PortfolioHistoryRow[]>([]);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [replay, setReplay] = useState<{ token: number; overrides: BacktestOverrides } | null>(null);

  useEffect(() => {
    if (!selected && strategies.length > 0) setSelected(strategies[0].name);
  }, [strategies, selected]);

  const selectedEngine = strategies.find((s) => s.name === selected)?.engine ?? "standard";

  useEffect(() => {
    if (!selected) return;
    setResult(null);
    setCsResult(null);
    setPairsResult(null);
    setRunError(null);
    if (selectedEngine === "standard") {
      api.history(selected).then(setHistory).catch(() => setHistory([]));
    } else {
      api.portfolioHistory(selected).then(setPortfolioHistory).catch(() => setPortfolioHistory([]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, selectedEngine]);

  const runBacktest = async (overrides: BacktestOverrides) => {
    if (!selected) return;
    setRunning(true);
    setRunError(null);
    try {
      if (selectedEngine === "cross_sectional") {
        setCsResult(await api.runCrossSectional(selected, overrides));
        setPortfolioHistory(await api.portfolioHistory(selected));
        onRunLogged();
      } else if (selectedEngine === "pairs") {
        setPairsResult(await api.runPairs(selected, overrides));
        setPortfolioHistory(await api.portfolioHistory(selected));
        onRunLogged();
      } else {
        const res = await api.runBacktest(selected, overrides);
        setResult(res);
        const hist = await api.history(selected);
        setHistory(hist);
        onRunLogged();
      }
    } catch (e) {
      setRunError(String(e));
    } finally {
      setRunning(false);
    }
  };

  const handleReplay = (row: HistoryRow) => {
    setReplay({
      token: Date.now(),
      overrides: {
        symbols: row.symbols.length ? row.symbols : undefined,
        start: row.startDate,
        end: row.endDate,
        params: Object.keys(row.params).length ? row.params : undefined,
      },
    });
  };

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
      <div className="space-y-4">
        <StrategyPicker strategies={strategies} selected={selected} onSelect={setSelected} />
        {selected && (
          <RunConfigPanel
            key={`${selected}-${replay?.token ?? "default"}`}
            strategyName={selected}
            running={running}
            runError={runError}
            onRun={runBacktest}
            initialOverrides={replay?.overrides}
          />
        )}
      </div>
      <div className="space-y-6">
        {selectedEngine === "cross_sectional" ? (
          <>
            {csResult ? (
              <CrossSectionalResultView result={csResult} />
            ) : (
              <div
                className="flex h-64 items-center justify-center rounded-lg border text-sm"
                style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-muted)" }}
              >
                Run a backtest to see results here.
              </div>
            )}
            <PortfolioRunHistory rows={portfolioHistory} />
          </>
        ) : selectedEngine === "pairs" ? (
          <>
            {pairsResult ? (
              <PairsResultView result={pairsResult} />
            ) : (
              <div
                className="flex h-64 items-center justify-center rounded-lg border text-sm"
                style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-muted)" }}
              >
                Run a backtest to see results here.
              </div>
            )}
            <PortfolioRunHistory rows={portfolioHistory} />
          </>
        ) : (
          <ResultTabs result={result} history={history} onReplay={handleReplay} />
        )}
      </div>
    </div>
  );
}

function CompareTab({
  strategies,
  onRunLogged,
}: {
  strategies: StrategySummary[];
  onRunLogged: () => void;
}) {
  const [selected, setSelected] = useState<string | null>(null);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [portfolioHistory, setPortfolioHistory] = useState<PortfolioHistoryRow[]>([]);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [csResult, setCsResult] = useState<CrossSectionalResponse | null>(null);
  const [pairsResult, setPairsResult] = useState<PairsResponse | null>(null);

  useEffect(() => {
    if (selected === null && strategies.length > 0) setSelected(strategies[0].name);
  }, [strategies, selected]);

  const selectedMeta = strategies.find((s) => s.name === selected);

  useEffect(() => {
    if (!selected) return;
    setResult(null);
    setCsResult(null);
    setPairsResult(null);
    setRunError(null);
    if (selectedMeta?.engine === "standard" || !selectedMeta) {
      api.history(selected).then(setHistory).catch(() => setHistory([]));
    } else {
      api.portfolioHistory(selected).then(setPortfolioHistory).catch(() => setPortfolioHistory([]));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, selectedMeta?.engine]);

  const runBacktest = async () => {
    if (!selected || !selectedMeta) return;
    setRunning(true);
    setRunError(null);
    try {
      if (selectedMeta.engine === "cross_sectional") {
        setCsResult(await api.runCrossSectional(selected));
        setPortfolioHistory(await api.portfolioHistory(selected));
        onRunLogged();
      } else if (selectedMeta.engine === "pairs") {
        setPairsResult(await api.runPairs(selected));
        setPortfolioHistory(await api.portfolioHistory(selected));
        onRunLogged();
      } else {
        const res = await api.runBacktest(selected);
        setResult(res);
        const hist = await api.history(selected);
        setHistory(hist);
        onRunLogged();
      }
    } catch (e) {
      setRunError(String(e));
    } finally {
      setRunning(false);
    }
  };

  const kindCaption =
    selectedMeta?.kind === "Day Trading" ? DAY_TRADING_CAPTION : SWING_TRADING_CAPTION;
  const m = result?.metrics;

  return (
    <>
      <section className="mb-8">
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          All strategies
        </h2>
        <StrategyTable strategies={strategies} selected={selected} onSelect={setSelected} />
      </section>

      <section>
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Run a backtest
        </h2>

        <div className="mb-4 flex flex-wrap items-center gap-3">
          <select
            value={selected ?? ""}
            onChange={(e) => setSelected(e.target.value)}
            className="rounded-md border px-3 py-2 text-sm"
            style={{
              borderColor: "var(--border)",
              background: "var(--surface-1)",
              color: "var(--text-primary)",
            }}
          >
            {strategies.map((s) => (
              <option key={s.name} value={s.name}>
                {s.name}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={runBacktest}
            disabled={running || !selected}
            className="rounded-md px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-50"
            style={{ background: "var(--series-1)" }}
          >
            {running ? "Running…" : "Run Backtest"}
          </button>
          {selectedMeta && selectedMeta.engine === "standard" && (
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              {kindCaption}
            </span>
          )}
          {selectedMeta && selectedMeta.engine !== "standard" && (
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              {selectedMeta.engine === "cross_sectional"
                ? "Cross-sectional rebalancing portfolio -- this runs the registered default; use the Lab tab to override symbols/dates/params."
                : "Pairs / stat-arb spread -- this runs the registered default; use the Lab tab to override symbols/dates/params."}
            </span>
          )}
        </div>

        {runError && (
          <div
            className="mb-4 rounded-lg border px-4 py-3 text-sm"
            style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
          >
            {runError}
          </div>
        )}

        {result && m && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <StatTile label="Trades Taken" value={String(m.tradesTaken)} />
              <StatTile label="Win Rate" value={m.tradesTaken ? pct(m.winRate) : "—"} />
              <StatTile
                label="Expectancy (R)"
                value={m.tradesTaken ? `${m.expectancyR >= 0 ? "+" : ""}${m.expectancyR.toFixed(3)}` : "—"}
                valueColor={
                  m.tradesTaken
                    ? m.expectancyR >= 0
                      ? "var(--status-good)"
                      : "var(--status-critical)"
                    : undefined
                }
              />
              <StatTile
                label="Profit Factor"
                value={m.tradesTaken ? (m.profitFactor === null ? "∞" : m.profitFactor.toFixed(2)) : "—"}
              />
              <StatTile
                label="Max Drawdown"
                value={m.maxDrawdownPct !== null ? `${m.maxDrawdownPct.toFixed(1)}%` : "—"}
              />
            </div>

            <div className="grid grid-cols-2 gap-3 sm:grid-cols-6">
              <StatTile
                label="Sharpe (vs. risk-free)"
                value={m.sharpe !== null ? m.sharpe.toFixed(2) : "—"}
                valueColor={m.sharpe !== null ? (m.sharpe > 0 ? "var(--status-good)" : "var(--status-critical)") : undefined}
              />
              <StatTile
                label="Buy & Hold Return"
                value={m.buyHoldReturnPct !== null ? `${m.buyHoldReturnPct >= 0 ? "+" : ""}${m.buyHoldReturnPct.toFixed(1)}%` : "—"}
              />
              <StatTile
                label="Alpha vs. buy & hold"
                value={m.alphaPct !== null ? `${m.alphaPct >= 0 ? "+" : ""}${m.alphaPct.toFixed(1)}%` : "—"}
                valueColor={m.alphaPct !== null ? (m.alphaPct > 0 ? "var(--status-good)" : "var(--status-critical)") : undefined}
              />
              <StatTile label="Beta" value={m.beta !== null ? m.beta.toFixed(3) : "—"} />
              <StatTile label="CAGR" value={m.cagrPct !== null ? `${m.cagrPct.toFixed(2)}%` : "—"} />
              <StatTile
                label="Exposure"
                value={m.exposurePct !== null ? `${m.exposurePct.toFixed(1)}% of time` : "—"}
              />
            </div>
            {m.riskFreeRate !== null && (
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                Sharpe/alpha measured against a {(m.riskFreeRate * 100).toFixed(1)}% risk-free
                rate (13-week T-bill, averaged over this run's window) and this strategy's own
                buy-and-hold return on the same symbols — not just R-multiples. See{" "}
                <code>LESSONS.md</code> for why that distinction matters.
              </p>
            )}

            <div>
              <StatusPill status={m.status} />
            </div>

            <EquityChart data={result.equityCurve} symbol={result.equitySymbol} />

            <PerSymbolTable rows={result.perSymbol} />

            <div>
              <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
                Trade log (pooled across the universe)
              </div>
              <TradesTable trades={result.trades} />
            </div>

            <PortfolioPanel portfolio={result.portfolio} />
          </div>
        )}

        {csResult && <CrossSectionalResultView result={csResult} />}
        {pairsResult && <PairsResultView result={pairsResult} />}
      </section>

      {selected && (
        <section className="mt-8">
          <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            Previous runs — {selected}
          </h2>
          {selectedMeta && selectedMeta.engine !== "standard" ? (
            <PortfolioRunHistory rows={portfolioHistory} />
          ) : (
            <div className="space-y-4">
              <MetricsHistoryChart rows={history} />
              <RunHistory rows={history} />
            </div>
          )}
        </section>
      )}
    </>
  );
}

export default App;
