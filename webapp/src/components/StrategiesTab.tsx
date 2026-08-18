import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  api,
  type BacktestOverrides,
  type BacktestResult,
  type CrossSectionalResponse,
  type HistoryRow,
  type PairsResponse,
  type PortfolioHistoryRow,
  type RegisteredUniverse,
  type StrategySummary,
  type ValidationJob,
} from "../api";
import { CrossSectionalResultView } from "./CrossSectionalResultView";
import { MetricsHistoryChart } from "./MetricsHistoryChart";
import { PairsResultView } from "./PairsResultView";
import { PortfolioRunHistory } from "./PortfolioRunHistory";
import { ResultTabs } from "./ResultTabs";
import { RunConfigPanel } from "./RunConfigPanel";
import { RunHistory } from "./RunHistory";
import { STRATEGY_CONFIG_SLOT } from "./Sidebar";
import { StrategyTable } from "./StrategyTable";

// Deliberately does NOT state a day count. The free data tier serves ~50
// trading days of 5-minute bars, not the ~60 this claimed, and the figure
// moves whenever the provider changes -- so the window is read from the run
// itself (the MEASURED window, see api/main.py:_run_config_fields) rather
// than asserted here and left to drift.
const DAY_TRADING_CAPTION =
  "Day-trading strategy: 5-min bars, limited to the window the data provider serves (see Window column).";
const SWING_TRADING_CAPTION = "Swing-trading strategy: backtests the last 5 years of daily bars.";

function EmptyResultPlaceholder({
  running = false,
  progress,
}: {
  running?: boolean;
  progress?: Pick<ValidationJob, "stage" | "progressPct" | "status"> | null;
}) {
  return (
    <div
      className="flex h-64 items-center justify-center rounded-lg border px-8 text-center text-sm"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-muted)" }}
    >
      {running ? (
        <div>
          <div className="font-medium" style={{ color: "var(--text-primary)" }}>
            {progress?.stage || "Starting the validation suite…"}
          </div>
          <div
            className="mx-auto mt-3 h-2 max-w-md overflow-hidden rounded-full"
            style={{ background: "var(--gridline)" }}
          >
            <div
              className="h-full rounded-full transition-[width] duration-300"
              style={{ width: `${progress?.progressPct ?? 2}%`, background: "var(--series-1)" }}
            />
          </div>
          <div className="mt-1 text-xs tabular-nums">{progress?.progressPct ?? 0}%</div>
          <p className="mt-2 max-w-xl text-xs">
            The basic backtest is complete. The app is now replaying parameter neighbors,
            rolling history, random concentration controls, and alternative universes.
            Dual Momentum can take several minutes. Keep this page open.
          </p>
        </div>
      ) : "Run a backtest to see results here."}
    </div>
  );
}

/** The single "Strategies" tab: browse every strategy's scores in one
 * leaderboard, then select a row to drill into it -- run configuration,
 * result view, and run history all update together. Replaces the old
 * separate Lab (config + result, narrow picker) and Compare (leaderboard +
 * canonical-only run button, no override capability) tabs, which had
 * drifted into two inconsistent code paths for the same job. */
export function StrategiesTab({
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
  const [runProgress, setRunProgress] = useState<Pick<ValidationJob, "stage" | "progressPct" | "status"> | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [replay, setReplay] = useState<{ token: number; overrides: BacktestOverrides } | null>(null);

  // Universe filter: which registered universe's results the leaderboard is
  // showing. "" means the registered default (the `strategies` prop as-is).
  // Selecting a universe never triggers a new backtest -- it only re-reads
  // whichever row each strategy last logged AGAINST that universe (see
  // api/main.py:list_strategies), so most rows read "Not yet tested" until
  // someone runs that strategy against it from the Lab tab.
  const [universes, setUniverses] = useState<RegisteredUniverse[]>([]);
  const [selectedUniverseId, setSelectedUniverseId] = useState("");
  const [universeStrategies, setUniverseStrategies] = useState<StrategySummary[] | null>(null);
  const [universeLoading, setUniverseLoading] = useState(false);
  useEffect(() => {
    api.listUniverses().then(setUniverses).catch(() => {});
  }, []);
  useEffect(() => {
    if (!selectedUniverseId) {
      setUniverseStrategies(null);
      return;
    }
    let cancelled = false;
    setUniverseLoading(true);
    api.listStrategies(selectedUniverseId)
      .then((rows) => { if (!cancelled) setUniverseStrategies(rows); })
      .catch(() => { if (!cancelled) setUniverseStrategies(null); })
      .finally(() => { if (!cancelled) setUniverseLoading(false); });
    return () => { cancelled = true; };
  }, [selectedUniverseId]);
  const displayedStrategies = selectedUniverseId ? (universeStrategies ?? []) : strategies;
  const universesByCategory = new Map<string, RegisteredUniverse[]>();
  for (const u of universes) {
    if (!u.selectable || !u.runnable) continue;
    const list = universesByCategory.get(u.category) ?? [];
    list.push(u);
    universesByCategory.set(u.category, list);
  }

  // The sidebar slot is a sibling subtree, so it is absent on first render.
  // Resolved after mount and stored in state so the portal re-renders once it
  // exists rather than silently dropping the panel.
  const [configSlot, setConfigSlot] = useState<HTMLElement | null>(null);
  useEffect(() => {
    setConfigSlot(document.getElementById(STRATEGY_CONFIG_SLOT));
  }, []);

  useEffect(() => {
    if (!selected && strategies.length > 0) setSelected(strategies[0].name);
  }, [strategies, selected]);

  const selectedMeta = strategies.find((s) => s.name === selected);
  const selectedEngine = selectedMeta?.engine ?? "standard";

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
    setRunProgress(null);
    setRunError(null);
    try {
      if (selectedEngine === "cross_sectional") {
        setCsResult(await api.runCrossSectional(selected, overrides, setRunProgress));
        setPortfolioHistory(await api.portfolioHistory(selected));
        onRunLogged();
      } else if (selectedEngine === "pairs") {
        setPairsResult(await api.runPairs(selected, overrides, setRunProgress));
        setPortfolioHistory(await api.portfolioHistory(selected));
        onRunLogged();
      } else {
        const res = await api.runBacktest(selected, overrides, setRunProgress);
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

  const handleReplay = (row: HistoryRow | PortfolioHistoryRow) => {
    setReplay({
      token: Date.now(),
      overrides: {
        universeId: row.universeId ?? undefined,
        symbols: row.universeId ? undefined : row.symbols.length ? row.symbols : undefined,
        start: row.startDate ?? undefined,
        end: row.endDate ?? undefined,
        params: Object.keys(row.params).length ? row.params : undefined,
      },
    });
  };

  const kindCaption =
    selectedMeta?.kind === "Day Trading" ? DAY_TRADING_CAPTION : SWING_TRADING_CAPTION;

  return (
    <>
      <section className="mb-8">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            All strategies
          </h2>
          <div className="flex items-center gap-2">
            <label className="text-xs" style={{ color: "var(--text-muted)" }} htmlFor="universe-filter">
              Universe
            </label>
            <select
              id="universe-filter"
              value={selectedUniverseId}
              onChange={(event) => setSelectedUniverseId(event.target.value)}
              className="rounded-md border px-2 py-1 text-xs"
              style={{ borderColor: "var(--border)", background: "var(--page)", color: "var(--text-primary)" }}
            >
              <option value="">Registered defaults</option>
              {[...universesByCategory.entries()].map(([category, items]) => (
                <optgroup key={category} label={category}>
                  {items.map((u) => (
                    <option key={u.id} value={u.id}>{u.label}</option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>
        </div>
        {selectedUniverseId && (
          <p className="mb-2 text-xs" style={{ color: "var(--status-warning)" }}>
            Showing each strategy's latest logged run against{" "}
            {universes.find((u) => u.id === selectedUniverseId)?.label ?? selectedUniverseId}
            {" "}-- not the registered default, and not a new backtest. Most strategies
            read "Not yet tested" here until run against this universe from the Lab tab.
            {universeLoading && " Loading…"}
          </p>
        )}
        <StrategyTable strategies={displayedStrategies} selected={selected} onSelect={setSelected} />
      </section>
      {selected && (
        <section>
          {configSlot &&
            createPortal(
              <div className="border-t pt-4" style={{ borderColor: "var(--gridline)" }}>
                <div className="mb-3 flex flex-col gap-0.5">
                  <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                    {selected}
                  </h2>
                  <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                    {selectedEngine === "standard"
                      ? kindCaption
                      : selectedEngine === "cross_sectional"
                        ? "Cross-sectional ranking and rebalancing strategy. Run details reflect the selected universe and cadence."
                        : "Pairs / stat-arb spread."}
                  </span>
                </div>
                <RunConfigPanel
                  key={`${selected}-${replay?.token ?? "default"}`}
                  strategyName={selected}
                  running={running}
                  runError={runError}
                  onRun={runBacktest}
                  initialOverrides={replay?.overrides}
                />
              </div>,
              configSlot,
            )}

          <div className="grid grid-cols-1 gap-6">
            <div>
              {selectedEngine === "standard" &&
                (result ? <ResultTabs result={result} /> : <EmptyResultPlaceholder running={running} progress={runProgress} />)}
              {selectedEngine === "cross_sectional" &&
                (csResult ? <CrossSectionalResultView result={csResult} /> : <EmptyResultPlaceholder running={running} progress={runProgress} />)}
              {selectedEngine === "pairs" &&
                (pairsResult ? <PairsResultView result={pairsResult} /> : <EmptyResultPlaceholder running={running} progress={runProgress} />)}
            </div>
          </div>

          <div className="mt-8">
            <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              Previous runs — {selected}
            </h2>
            {selectedEngine === "standard" ? (
              <div className="space-y-4">
                <MetricsHistoryChart rows={history} />
                <RunHistory rows={history} onReplay={handleReplay} />
              </div>
            ) : (
              <PortfolioRunHistory
                rows={portfolioHistory}
                onReplay={handleReplay}
                strategyName={selected ?? ""}
                automatable={selectedEngine === "cross_sectional"}
              />
            )}
          </div>
        </section>
      )}
    </>
  );
}
