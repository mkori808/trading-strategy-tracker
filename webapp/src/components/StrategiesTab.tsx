import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type BacktestOverrides, type BacktestResult, type CrossSectionalResponse, type ExecutionAccountStatus, type GovernedForwardExperiment, type HistoryRow, type PairsResponse, type PortfolioHistoryRow, type RegisteredUniverse, type ResearchStatusRow, type StrategySummary, type ValidationJob } from "../api";
import { CanonicalResearchStateCard } from "./CanonicalResearchStateCard";
import { ConditionalEdgePanel } from "./ConditionalEdgePanel";
import { CrossSectionalResultView } from "./CrossSectionalResultView";
import { EdgeValidationPanel } from "./EdgeValidationPanel";
import { NewStrategyDialog } from "./NewStrategyDialog";
import { PairsResultView } from "./PairsResultView";
import { PortfolioRunHistory } from "./PortfolioRunHistory";
import { ResultTabs } from "./ResultTabs";
import { RunConfigPanel, type RunConfigState } from "./RunConfigPanel";
import { RunHistory } from "./RunHistory";
import { StatusPill } from "./StatusPill";
import { StrategyTable } from "./StrategyTable";
import { allChecks, benchmarkEvidence, gateCounts, powerSummary, primaryBlocker } from "./researchPresentation";

const dateLabel = (iso: string | null) => iso ? new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "Not run";
const field = (label: string, value: string | number | null, title?: string) => <div title={title}><span className="block text-[11px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>{label}</span><strong className="tabular-nums">{value ?? "—"}</strong></div>;

function EmptyResult({ running, progress }: { running: boolean; progress: Pick<ValidationJob, "stage" | "progressPct"> | null }) {
  return <div className="flex min-h-36 items-center justify-center rounded-lg border p-6 text-center text-sm" style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>{running ? <div><strong style={{ color: "var(--text-primary)" }}>{progress?.stage ?? "Starting validation suite…"}</strong><div className="mt-2">{progress?.progressPct ?? 0}% complete</div></div> : "Run a backtest to establish a latest result for this session."}</div>;
}

function LatestResult({ engine, standard, cross, pairs, running, progress }: { engine: StrategySummary["engine"]; standard: BacktestResult | null; cross: CrossSectionalResponse | null; pairs: PairsResponse | null; running: boolean; progress: Pick<ValidationJob, "stage" | "progressPct"> | null }) {
  const result = standard ?? cross ?? pairs;
  if (!result) return <EmptyResult running={running} progress={progress} />;
  const validation = result.validation;
  const portfolio = cross ?? pairs;
  const windowLabel = standard ? `${standard.start} → ${standard.end}` : cross ? `${cross.start} → ${cross.end}` : `${pairs!.tradingWindow[0]} → ${pairs!.tradingWindow[1]}`;
  return <div className="rounded-lg border p-4" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
    <div className="text-xs font-semibold uppercase tracking-wide" style={{ color: validation.verdict.forwardTestWorthy ? "var(--status-positive)" : "var(--status-warning)" }}>{validation.verdict.headline || validation.verdict.signalEdge} · {validation.research?.isPreregistered ? "PREREGISTERED" : "EXPLORATORY"}</div>
    <div className="mt-1 text-sm" style={{ color: "var(--text-secondary)" }}>{gateCounts(validation).label}</div>
    <div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4 lg:grid-cols-7">{field("Window", windowLabel)}{engine === "standard" ? <>{field("Trades", standard!.metrics.tradesTaken)}{field("Win rate", `${(standard!.metrics.winRate * 100).toFixed(1)}%`)}{field("Expectancy", `${standard!.metrics.expectancyR.toFixed(3)} R`)}{field("Profit factor", standard!.metrics.profitFactor?.toFixed(2) ?? "—")}</> : <>{field("Return", `${portfolio!.returnPct.toFixed(1)}%`)}{field("CAGR", portfolio!.cagrPct === null ? "—" : `${portfolio!.cagrPct.toFixed(1)}%`)}</>}{field("Sharpe", (engine === "standard" ? standard!.metrics.sharpe : portfolio!.sharpe)?.toFixed(2) ?? "—")}{field("Max drawdown", (engine === "standard" ? standard!.metrics.maxDrawdownPct : portfolio!.maxDrawdownPct) == null ? "—" : `${(engine === "standard" ? standard!.metrics.maxDrawdownPct! : portfolio!.maxDrawdownPct).toFixed(1)}%`)}</div>
  </div>;
}

function lifecycleFor(strategy: StrategySummary): string {
  if (strategy.implementationStatus === "unavailable") return "Data blocked";
  if (strategy.archived) return "Archived";
  return strategy.lifecycleStage?.replace(/_/g, " ") ?? (strategy.lastRun ? "Exploratory" : "Not yet tested");
}

export function ConditionalStatusSummary({ row, loading = false, error = null, archived = false }: { row?: ResearchStatusRow; loading?: boolean; error?: string | null; archived?: boolean }) {
  const neverEvaluated = Boolean(row && !row.evidenceStage && row.lastCompletedAction?.startsWith("No Conditional Edge Discovery session"));
  const status = error ? "Unable to load research status" : loading ? "Loading research status…" : neverEvaluated ? "Not evaluated" : row?.status ?? "Not evaluated";
  return <><strong>Conditional Edge</strong><span className="ml-2 text-sm" style={{ color: error ? "var(--status-warning)" : "var(--text-muted)" }}>{status}</span><span className="ml-2 text-xs" style={{ color: "var(--series-1)" }}>View conditional research →</span>{(row?.primaryBlocker || (neverEvaluated && archived)) && <span className="mt-2 block text-xs" style={{ color: "var(--text-secondary)" }}>{row?.primaryBlocker ?? "Strategy archived"}</span>}</>;
}

export function KeyEvidence({ strategy }: { strategy: StrategySummary }) {
  const checks = allChecks(strategy.validation);
  const priorities = ["statistical_power", "beats_cash", "sample_coverage", "beats_spy", "pit_membership"];
  const selected = priorities.map((key) => checks.find((check) => check.key === key)).filter((check): check is NonNullable<typeof check> => Boolean(check)).slice(0, 3);
  const icon = (status: string) => status === "pass" ? "✓" : status === "fail" ? "✕" : status === "not_applicable" ? "—" : "?";
  const color = (status: string) => status === "pass" ? "var(--status-good)" : status === "fail" ? "var(--status-warning)" : "var(--text-muted)";
  const label = (key: string, fallback: string) => ({ statistical_power: "Statistical power", beats_cash: "Beats cash / risk-free", sample_coverage: "Valid trade and exposure coverage", beats_spy: "Benchmark superiority", pit_membership: "PIT universe integrity" }[key] ?? fallback);
  return <div><div className="text-[11px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>Key evidence</div><div className="mt-1 flex flex-wrap gap-2">{selected.length ? selected.map((check) => <span key={check.key} className="rounded-full px-2 py-1 text-xs" title={`${check.status}: ${check.summary}`} style={{ background: "var(--pill-bg)", color: color(check.status) }}>{icon(check.status)} {label(check.key, check.label)}</span>) : <span className="text-xs" style={{ color: "var(--text-muted)" }}>Structured gate results not recorded</span>}</div></div>;
}

export function StrategyExecutionState({ strategyName, blocked, account }: { strategyName: string; blocked: boolean; account: ExecutionAccountStatus | null }) {
  const isOwner = account?.owner?.strategyName === strategyName;
  const mode = blocked ? "DATA BLOCKED" : isOwner ? `PAPER AUTOMATED — ${account?.owner?.displayName ?? strategyName}` : "SHADOW — NO ORDERS";
  const occupied = Boolean(account?.owner && !isOwner);
  return <section><h2 className="mb-3 text-base font-semibold">Forward execution state</h2><div className="rounded-lg border p-4 text-sm" style={{ borderColor: blocked || occupied ? "var(--status-warning)" : "var(--border)", background: "var(--surface-1)" }}><div className="flex flex-wrap items-center justify-between gap-3"><strong>{mode}</strong><span className="text-xs" style={{ color: "var(--text-muted)" }}>{isOwner ? "Execution-observed" : blocked ? "No active forward evidence" : "Prospective synthetic only"}</span></div><p className="mt-2 text-xs" style={{ color: "var(--text-secondary)" }}>{blocked ? "This strategy cannot enter forward testing until its data-quality blocker is resolved." : isOwner ? `${account?.brokerageLabel} is assigned only to the exact ${account?.owner?.displayName} fingerprint. This does not make the canonical 189D/Monthly configuration an executing strategy; canonical DM remains a separate shadow.` : occupied ? `The connected account is assigned to ${account?.owner?.displayName}. Brokerage execution is unavailable here; use a shadow forward test or a separate account.` : "No free brokerage account is assigned. Shadow testing does not place orders."}</p>{occupied && !blocked && <button type="button" disabled className="mt-3 rounded-md border px-2.5 py-1 text-xs font-medium opacity-60" title="Assign a separate free brokerage account before enabling execution">Enable brokerage execution</button>}</div></section>;
}

export function StrategiesTab({ strategies, onRunLogged }: { strategies: StrategySummary[]; onRunLogged: () => void }) {
  const [selected, setSelected] = useState<string | null>(strategies[0]?.name ?? null);
  const [view, setView] = useState<"workspace" | "catalog">("workspace");
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const [switchQuery, setSwitchQuery] = useState("");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [csResult, setCsResult] = useState<CrossSectionalResponse | null>(null);
  const [pairsResult, setPairsResult] = useState<PairsResponse | null>(null);
  const [history, setHistory] = useState<HistoryRow[]>([]);
  const [portfolioHistory, setPortfolioHistory] = useState<PortfolioHistoryRow[]>([]);
  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<Pick<ValidationJob, "stage" | "progressPct" | "status"> | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [replay, setReplay] = useState<{ token: number; runId: number; provenance: string; overrides: BacktestOverrides } | null>(null);
  const [config, setConfig] = useState<RunConfigState | null>(null);
  const [authoring, setAuthoring] = useState(false);
  const [conditionalOpen, setConditionalOpen] = useState(false);
  const [conditionalState, setConditionalState] = useState<{ row?: ResearchStatusRow; loading: boolean; error: string | null }>({ loading: false, error: null });
  const [universes, setUniverses] = useState<RegisteredUniverse[]>([]);
  const [catalogUniverse, setCatalogUniverse] = useState("");
  const [catalogRows, setCatalogRows] = useState<StrategySummary[] | null>(null);
  const [executionAccount, setExecutionAccount] = useState<ExecutionAccountStatus | null>(null);
  const [forwardExperiments, setForwardExperiments] = useState<GovernedForwardExperiment[]>([]);

  useEffect(() => { if (strategies.length && (!selected || !strategies.some((item) => item.name === selected))) setSelected(strategies[0].name); }, [strategies, selected]);
  useEffect(() => { api.listUniverses().then(setUniverses).catch(() => undefined); }, []);
  useEffect(() => { api.executionAccountOwnership().then(setExecutionAccount).catch(() => setExecutionAccount(null)); }, []);
  useEffect(() => { if (!catalogUniverse) { setCatalogRows(null); return; } api.listStrategies(catalogUniverse).then(setCatalogRows).catch(() => setCatalogRows([])); }, [catalogUniverse]);
  const meta = strategies.find((strategy) => strategy.name === selected);
  const engine = meta?.engine ?? "standard";
  useEffect(() => { if (!selected) return; setResult(null); setCsResult(null); setPairsResult(null); setRunError(null); setReplay(null); setConfig(null); setConditionalOpen(false); if (engine === "standard") api.history(selected).then(setHistory).catch(() => setHistory([])); else api.portfolioHistory(selected).then(setPortfolioHistory).catch(() => setPortfolioHistory([])); }, [selected, engine]);
  const refreshForwardExperiments = useCallback(() => { if (!selected) return; api.forwardExperiments(selected).then(setForwardExperiments).catch(() => setForwardExperiments([])); }, [selected]);
  useEffect(() => { refreshForwardExperiments(); }, [refreshForwardExperiments]);
  useEffect(() => { if (!selected || engine === "pairs") return; let cancelled = false; setConditionalState({ loading: true, error: null }); api.researchConditionalStatus(selected).then((row) => { if (!cancelled) setConditionalState({ row, loading: false, error: null }); }).catch((error) => { if (!cancelled) setConditionalState({ loading: false, error: String(error) }); }); return () => { cancelled = true; }; }, [selected, engine]);

  const onConfigState = useCallback((state: RunConfigState) => setConfig(state), []);
  const runBacktest = async (overrides: BacktestOverrides) => { if (!selected) return; setRunning(true); setProgress(null); setRunError(null); try { if (engine === "cross_sectional") { setCsResult(await api.runCrossSectional(selected, overrides, setProgress)); setPortfolioHistory(await api.portfolioHistory(selected)); } else if (engine === "pairs") { setPairsResult(await api.runPairs(selected, overrides, setProgress)); setPortfolioHistory(await api.portfolioHistory(selected)); } else { setResult(await api.runBacktest(selected, overrides, setProgress)); setHistory(await api.history(selected)); } onRunLogged(); } catch (error) { setRunError(String(error)); } finally { setRunning(false); } };
  const replayRun = (row: HistoryRow | PortfolioHistoryRow) => setReplay({ token: Date.now(), runId: row.id, provenance: row.isPreregistered === true ? "Preregistered historical configuration" : row.isPreregistered === false ? "Exploratory historical configuration" : row.lifecycleStage?.replace(/_/g, " ") ?? "Historical configuration", overrides: { universeId: row.universeId ?? undefined, symbols: row.universeId ? undefined : row.symbols.length ? row.symbols : undefined, start: row.startDate ?? undefined, end: row.endDate ?? undefined, params: Object.keys(row.params).length ? row.params : undefined } });
  const chooseStrategy = (name: string) => { setSelected(name); setView("workspace"); setSwitcherOpen(false); setSwitchQuery(""); window.scrollTo({ top: 0, behavior: "smooth" }); };

  const switchGroups = useMemo(() => { const groups = new Map<string, StrategySummary[]>(); strategies.filter((strategy) => strategy.name.toLowerCase().includes(switchQuery.toLowerCase())).forEach((strategy) => { const label = lifecycleFor(strategy); groups.set(label, [...(groups.get(label) ?? []), strategy]); }); return groups; }, [strategies, switchQuery]);

  if (view === "catalog") return <>
    <NewStrategyDialog open={authoring} onClose={() => setAuthoring(false)} onSaved={(name) => { chooseStrategy(name); onRunLogged(); }} onDeleted={onRunLogged} />
    <div className="mb-5 flex flex-wrap items-center justify-between gap-3"><div><h1 className="text-2xl font-semibold">Strategies</h1><p className="mt-1 text-sm" style={{ color: "var(--text-muted)" }}>Research lifecycle and statistical power are distinct states.</p></div><button type="button" onClick={() => setView("workspace")} className="text-sm font-medium" style={{ color: "var(--series-1)" }}>← Back to selected strategy</button></div>
    <div className="mb-3 flex flex-wrap justify-end gap-2"><select aria-label="Catalog universe" value={catalogUniverse} onChange={(event) => setCatalogUniverse(event.target.value)} className="rounded-md border px-2 py-1 text-xs" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}><option value="">Registered defaults</option>{universes.filter((universe) => universe.selectable).map((universe) => <option key={universe.id} value={universe.id}>{universe.label}{universe.runnable ? "" : " · DATA REQUIRED"}</option>)}</select><button type="button" onClick={() => setAuthoring(true)} className="rounded-md px-2.5 py-1 text-xs font-medium text-white" style={{ background: "var(--series-1)" }}>+ Describe a strategy</button></div>
    <StrategyTable strategies={catalogUniverse ? (catalogRows ?? []) : strategies} selected={selected} onSelect={chooseStrategy} />
  </>;

  if (!selected || !meta) return null;
  const lifecycle = lifecycleFor(meta);
  const power = powerSummary(meta);
  const blocker = primaryBlocker(meta);
  const gates = gateCounts(meta.validation);
  const execution = config?.timing?.execution.replace(/_/g, " ").toLowerCase() ?? "Loading";
  const conditional = conditionalState.row;
  const lastRow = engine === "standard" ? history[0] : portfolioHistory[0];
  const holdoutPowerTitle = lifecycle.toLowerCase().includes("holdout") && power.label === "Underpowered" ? "The preregistered holdout did not invalidate the strategy, but statistical power remains insufficient to establish the target edge." : undefined;

  return <>
    <NewStrategyDialog open={authoring} onClose={() => setAuthoring(false)} onSaved={(name) => { chooseStrategy(name); onRunLogged(); }} onDeleted={onRunLogged} />
    <article className="space-y-8">
      <header>
        <div className="flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-2xl font-semibold" style={{ color: "var(--text-primary)" }}>{selected}</h1><div className="mt-2 flex flex-wrap gap-2"><StatusPill status={meta.kind} /><span title={holdoutPowerTitle}><StatusPill status={lifecycle} /></span><StatusPill status={power.label} /></div></div><div className="relative"><button type="button" aria-expanded={switcherOpen} onClick={() => setSwitcherOpen((open) => !open)} className="rounded-md border px-3 py-2 text-sm font-medium" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>Switch strategy ▾</button>{switcherOpen && <div className="absolute right-0 z-20 mt-2 w-[min(22rem,calc(100vw-3rem))] rounded-lg border p-2 shadow-lg" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}><input autoFocus aria-label="Search strategies" value={switchQuery} onChange={(event) => setSwitchQuery(event.target.value)} placeholder="Search strategies…" className="mb-2 w-full rounded-md border px-3 py-2 text-sm" style={{ borderColor: "var(--border)", background: "var(--page)" }} /><div className="max-h-72 overflow-y-auto">{[...switchGroups.entries()].map(([label, rows]) => <div key={label} className="mb-2"><div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>{label}</div>{rows.map((row) => <button type="button" key={row.name} onClick={() => chooseStrategy(row.name)} className="block w-full rounded px-2 py-1.5 text-left text-sm" style={{ background: row.name === selected ? "var(--series-1-wash)" : undefined, color: "var(--text-primary)" }}>{row.name}</button>)}</div>)}{switchGroups.size === 0 && <p className="p-2 text-sm" style={{ color: "var(--text-muted)" }}>No matching strategies.</p>}</div><button type="button" onClick={() => setView("catalog")} className="mt-2 w-full border-t px-2 pt-2 text-left text-xs font-medium" style={{ borderColor: "var(--gridline)", color: "var(--series-1)" }}>Browse all strategies →</button></div>}</div></div>
        <div className="mt-4 grid grid-cols-2 gap-4 rounded-lg border p-3 text-sm sm:grid-cols-3 lg:grid-cols-6" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>{field("Strategy lifecycle", lifecycle, holdoutPowerTitle)}{field("Statistical power", power.detail ? `${power.label} · ${power.detail.replace(" · target", "; target")}` : power.label)}{field("Benchmark evidence", benchmarkEvidence(meta), `Strategy return minus ${meta.benchmarkName} return over the same test window.${meta.kind === "Day Trading" ? " This does not by itself measure capital efficiency because exposure may differ materially from buy-and-hold." : ""}`)}{field("Last run", dateLabel(meta.lastRun))}{field("Universe", config ? `${config.universeLabel} · ${config.securityCount}` : "Loading")}{field("Execution", execution)}</div>
        {meta.kind === "Day Trading" && <div className="mt-3 rounded-md border px-3 py-2 text-xs" title={meta.measuredStartDate ? `Provider coverage begins ${meta.measuredStartDate}; usable dates are measured from returned bars, not the requested window.` : "Usable dates come from the bars actually returned by the provider."} style={{ borderColor: "var(--status-warning)", color: "var(--text-secondary)" }}><strong>Intraday history is limited by provider coverage.</strong>{meta.requestedStartDate && meta.measuredStartDate && <span> Requested {meta.requestedStartDate} → {meta.requestedEndDate}; usable {meta.measuredStartDate} → {meta.measuredEndDate}. Provider coverage begins {meta.measuredStartDate}.</span>}</div>}
      </header>

      <CanonicalResearchStateCard strategy={meta} lifecycle={lifecycle} forwardExperiments={forwardExperiments} />

      <StrategyExecutionState strategyName={selected} blocked={meta.implementationStatus === "unavailable"} account={executionAccount} />

      <section>
        <h2 className="mb-1 text-base font-semibold">Experiment Sandbox</h2>
        <p className="mb-3 text-xs" style={{ color: "var(--text-secondary)" }}>Changing the universe, date range, or parameters below runs a new, separate experiment. It never edits or replaces the canonical evidence shown above — a run made here is recorded as exploratory (or as a search-family member, if preregistered) and only promotes into research evidence through the Research History section and, for automatable strategies, an explicit proposal review.</p>
        <div className="rounded-lg border p-4" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}><RunConfigPanel key={`${selected}-${replay?.token ?? "default"}`} strategyName={selected} running={running} runError={runError} onRun={runBacktest} initialOverrides={replay?.overrides} loadedRunId={replay?.runId} loadedRunProvenance={replay?.provenance} onStateChange={onConfigState} /></div>
      </section>

      <section><h2 className="mb-3 text-base font-semibold">Latest Result</h2><LatestResult engine={engine} standard={result} cross={csResult} pairs={pairsResult} running={running} progress={progress} />{!result && !csResult && !pairsResult && lastRow && <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>Last recorded experiment: Run #{lastRow.id} · {dateLabel(lastRow.runAt)} · see Research History</p>}{(result || csResult || pairsResult) && <details className="mt-3 rounded-lg border p-3" style={{ borderColor: "var(--border)" }}><summary className="cursor-pointer text-sm font-medium" style={{ color: "var(--series-1)" }}>Inspect evidence</summary><div className="mt-4">{result && <ResultTabs result={result} />}{csResult && <CrossSectionalResultView result={csResult} />}{pairsResult && <PairsResultView result={pairsResult} />}</div></details>}</section>

      <section><h2 className="mb-3 text-base font-semibold">Research Status</h2><div className="rounded-lg border p-4 text-sm" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}><div><div className="text-[11px] uppercase tracking-wide" style={{ color: "var(--text-muted)" }}>Primary blocker</div><strong>{blocker.label}</strong>{blocker.detail && <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>{blocker.detail}</p>}</div><div className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">{field("Strategy lifecycle", lifecycle, holdoutPowerTitle)}{field("Statistical power", power.detail ? `${power.label} · ${power.detail}` : power.label)}{field("Benchmark evidence", benchmarkEvidence(meta))}{field("Logged experiments", engine === "standard" ? history.length : portfolioHistory.length)}{field("Validation gates", gates.label)}</div><div className="mt-4"><KeyEvidence strategy={meta} /></div>{meta.validation && <details className="mt-4"><summary className="cursor-pointer text-xs font-medium" style={{ color: "var(--series-1)" }}>View all validation evidence</summary><div className="mt-4"><EdgeValidationPanel report={meta.validation} /></div></details>}</div></section>

      <section><h2 className="mb-3 text-base font-semibold">Research History</h2>{engine === "standard" ? <RunHistory rows={history} onReplay={replayRun} currentOverrides={config?.overrides} currentParams={config?.params} registeredParams={config?.registeredParams} /> : <PortfolioRunHistory rows={portfolioHistory} onReplay={replayRun} strategyName={selected} automatable={engine === "cross_sectional"} onProposed={refreshForwardExperiments} />}</section>

      {(engine === "standard" || engine === "cross_sectional") && <section><h2 className="mb-3 text-base font-semibold">Advanced Research</h2><details className="rounded-lg border p-4" onToggle={(event) => setConditionalOpen(event.currentTarget.open)} style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}><summary className="cursor-pointer"><ConditionalStatusSummary row={conditional} loading={conditionalState.loading} error={conditionalState.error} archived={meta.archived} /></summary>{conditionalOpen && !conditionalState.error && <div className="mt-4"><ConditionalEdgePanel strategyName={selected} engine={engine} /></div>}</details></section>}
      <button type="button" onClick={() => setView("catalog")} className="text-sm font-medium" style={{ color: "var(--series-1)" }}>Browse all strategies →</button>
    </article>
  </>;
}
