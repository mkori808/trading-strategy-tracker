import { useEffect, useState } from "react";
import {
  api,
  type BacktestOverrides,
  type BacktestResult,
  type CrossSectionalResponse,
  type HistoryRow,
  type PairsResponse,
  type PortfolioHistoryRow,
  type StrategySummary,
} from "../api";
import { CrossSectionalResultView } from "./CrossSectionalResultView";
import { MetricsHistoryChart } from "./MetricsHistoryChart";
import { PairsResultView } from "./PairsResultView";
import { PortfolioRunHistory } from "./PortfolioRunHistory";
import { ResultTabs } from "./ResultTabs";
import { RunConfigPanel } from "./RunConfigPanel";
import { RunHistory } from "./RunHistory";
import { StrategyTable } from "./StrategyTable";

const DAY_TRADING_CAPTION = "Day-trading strategy: backtests the last ~60 days of 5-min bars.";
const SWING_TRADING_CAPTION = "Swing-trading strategy: backtests the last 5 years of daily bars.";

function EmptyResultPlaceholder() {
  return (
    <div
      className="flex h-64 items-center justify-center rounded-lg border text-sm"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-muted)" }}
    >
      Run a backtest to see results here.
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
  const [runError, setRunError] = useState<string | null>(null);
  const [replay, setReplay] = useState<{ token: number; overrides: BacktestOverrides } | null>(null);

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

  const handleReplay = (row: HistoryRow | PortfolioHistoryRow) => {
    setReplay({
      token: Date.now(),
      overrides: {
        symbols: row.symbols.length ? row.symbols : undefined,
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
        <h2 className="mb-3 text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          All strategies
        </h2>
        <StrategyTable strategies={strategies} selected={selected} onSelect={setSelected} />
      </section>

      {selected && (
        <section>
          <div className="mb-3 flex items-baseline gap-3">
            <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              {selected}
            </h2>
            {selectedEngine === "standard" ? (
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                {kindCaption}
              </span>
            ) : (
              <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                {selectedEngine === "cross_sectional"
                  ? "Cross-sectional rebalancing portfolio."
                  : "Pairs / stat-arb spread."}
              </span>
            )}
          </div>

          <div className="grid grid-cols-1 gap-6 lg:grid-cols-[360px_1fr]">
            <RunConfigPanel
              key={`${selected}-${replay?.token ?? "default"}`}
              strategyName={selected}
              running={running}
              runError={runError}
              onRun={runBacktest}
              initialOverrides={replay?.overrides}
            />
            <div>
              {selectedEngine === "standard" && <ResultTabs result={result} />}
              {selectedEngine === "cross_sectional" &&
                (csResult ? <CrossSectionalResultView result={csResult} /> : <EmptyResultPlaceholder />)}
              {selectedEngine === "pairs" &&
                (pairsResult ? <PairsResultView result={pairsResult} /> : <EmptyResultPlaceholder />)}
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
              <PortfolioRunHistory rows={portfolioHistory} onReplay={handleReplay} />
            )}
          </div>
        </section>
      )}
    </>
  );
}
