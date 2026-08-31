import { useCallback, useEffect, useState } from "react";
import {
  api,
  type DailyPerformance,
  type ExecutionAccountStatus,
  type ExecutionStrategyConfig,
  type ForwardStackStatus,
  type LiveAccountResponse,
  type MarketResponse,
  type ResearchStatusDashboard,
} from "../api";
import { useResource } from "../useResource";
import { KEYS } from "../resourceKeys";
import {
  changeColor,
  fmtCompactMoney,
  fmtMoney,
  fmtPct,
  fmtRelative,
  REGIME_COLOR,
} from "../format";
import { sectorName } from "../sectorNames";
import { Card, CardEmpty, CardHeadline, CardRow } from "./Card";
import { Modal } from "./Modal";
import { MarketView } from "./MarketView";
import { ScreenerRefresh, ScreenerView } from "./ScreenerView";
import { SymbolsView } from "./SymbolsView";
import { LiveMonitorView } from "./LiveMonitorView";
import { DigestPanel, InsiderPanel, MoversPanel, MoversRefresh } from "./ResearchPanels";
import { useInsider, useMovers, useScreener, useSymbols } from "../dataHooks";
import { StatusStrip } from "./StatusStrip";
import { ResearchStatusView } from "./ResearchStatusView";

const FORWARD_STATUS_POLL_MS = 60_000;
const fetchForwardStackStatus = () => api.researchForwardStack();

function StatusBadge({ mode, children }: { mode: "paper" | "shadow" | "frozen" | "closed"; children: string }) {
  const palette = {
    paper: { color: "var(--status-good)", background: "var(--status-good-bg)" },
    shadow: { color: "var(--series-1)", background: "var(--series-1-wash)" },
    frozen: { color: "var(--status-warning)", background: "var(--status-warning-bg)" },
    closed: { color: "var(--text-muted)", background: "var(--gridline)" },
  }[mode];
  return <span className="rounded-full px-2 py-0.5 text-[10px] font-semibold whitespace-nowrap" style={palette}>{children}</span>;
}

function fmtDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(`${value.slice(0, 10)}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function fmtTimestamp(value: string | null | undefined): string {
  if (!value) return "not recorded";
  return new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function maturityText(sessions: number, maturity: DailyPerformance["maturity"] | ForwardStackStatus["maturity"] | undefined): string {
  if (sessions === 0) return "0 · Not started";
  if (sessions >= 252) return `${sessions} sessions · Annual review eligible`;
  const latest = maturity?.reached.at(-1);
  return `${sessions} sessions · ${latest ?? "Too early"}`;
}

/** Which popup is open. `null` is the dashboard itself -- the popup is
 * always ON TOP of the overview, never instead of it, which is the whole
 * point of the rebuild: you never lose the page you were reading. */
type Popup =
  | null
  | "market"
  | "trading"
  | "movers"
  | "insider"
  | "screener"
  | "watchlist"
  | "digest"
  | "researchStatus";

/** The single home surface. Every former tab is a card here, summarized to
 * the two or three numbers that answer "do I need to look closer?", and the
 * full former tab opens in a popup on click.
 *
 * Data discipline carried over from the tabbed version, and the reason this
 * page isn't slow: /api/market is fetched ONCE in App.tsx (a cold call
 * scans 94 symbols, ~40s) and passed in; everything else goes through the
 * shared cache in useResource.ts, so a card and the popup it opens are one
 * request, not two. The genuinely expensive on-demand actions -- the digest
 * (~1 min) and the insider EDGAR refresh (minutes) -- stay behind explicit
 * buttons inside their popups and never fire from a card rendering. */
export function DashboardView({
  marketData,
  marketLoading,
  marketError,
  onRefreshMarket,
}: {
  marketData: MarketResponse | null;
  marketLoading: boolean;
  marketError: string | null;
  onRefreshMarket: () => void;
}) {
  const [popup, setPopup] = useState<Popup>(null);
  const [focusedResearchName, setFocusedResearchName] = useState<string | null>(null);
  // Stable identity: Modal guards against a changing handler internally, but
  // there is no reason to hand it a new function on every poll-driven render.
  const close = useCallback(() => setPopup(null), []);

  const account = useResource<LiveAccountResponse>(KEYS.liveAccount, () => api.liveAccount());
  const execConfig = useResource<ExecutionStrategyConfig[]>(KEYS.executionConfig, () =>
    api.executionConfig(),
  );
  const executionAccount = useResource<ExecutionAccountStatus>(KEYS.executionAccountOwnership, () => api.executionAccountOwnership());
  const movers = useMovers();
  const insider = useInsider();
  const screener = useScreener();
  const symbols = useSymbols();
  const researchStatus = useResource<ResearchStatusDashboard>(KEYS.researchStatus, () =>
    api.researchStatus(),
  );
  const forwardStack = useResource<ForwardStackStatus>(KEYS.researchForwardStack, fetchForwardStackStatus);
  const daily = useResource<DailyPerformance>(KEYS.executionDaily, () => api.executionDaily());
  const refreshForwardStack = forwardStack.refresh;

  // Unlike the expensive market/screener resources, this endpoint is a
  // persisted-state read.  Its scheduler heartbeat must stay current while
  // Dashboard remains mounted; otherwise a healthy hourly scheduler is
  // eventually compared against a page-load timestamp and shown as stale.
  useEffect(() => {
    void refreshForwardStack();
    const timer = window.setInterval(() => void refreshForwardStack(), FORWARD_STATUS_POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshForwardStack]);

  const acct = account.data?.account;
  const positions = account.data?.positions ?? [];
  const automated = (execConfig.data ?? []).filter((c) => c.enabled);
  const activeStrategy = automated[0] ?? null;
  const orderSummary = account.data?.orderSummary;

  const unrealized = positions.reduce((sum, p) => sum + (p.unrealizedPl ?? 0), 0);
  const schedulerAttempt = forwardStack.data?.operations?.lastAttemptAt;
  const schedulerAgeMs = schedulerAttempt ? Date.now() - new Date(schedulerAttempt).getTime() : null;
  const schedulerStale = Boolean(forwardStack.data && (schedulerAgeMs === null || schedulerAgeMs > 2.5 * 60 * 60 * 1000));
  const integrityIssues = [
    marketError ? { severity: "error", message: `Market API: ${marketError}` } : null,
    account.error ? { severity: "error", message: `Alpaca API: ${account.error}` } : null,
    executionAccount.error ? { severity: "error", message: `Execution ownership: ${executionAccount.error}` } : null,
    researchStatus.error ? { severity: "error", message: `Research API: ${researchStatus.error}` } : null,
    forwardStack.error ? { severity: "error", message: `Forward stack: ${forwardStack.error}` } : null,
    acct && !acct.available ? { severity: "error", message: `Alpaca unavailable: ${acct.reason ?? "connection failed"}` } : null,
    activeStrategy && !activeStrategy.identity.fingerprintMatches
      ? { severity: "error", message: activeStrategy.identity.fingerprintReason ?? "Strategy fingerprint mismatch" }
      : null,
    executionAccount.data?.actionRequired
      ? { severity: "error", message: "Brokerage execution ownership or account attribution requires action." }
      : null,
    daily.data && !daily.data.available ? { severity: "warning", message: daily.data.reason ?? "Daily paper history unavailable" } : null,
    schedulerStale ? { severity: "warning", message: "Shadow-forward scheduler has not checked within the expected interval." } : null,
    ...(forwardStack.data?.alerts ?? []).map((alert) => ({
      severity: alert.scope === "research_shadow" ? "warning" : alert.severity,
      message: `${alert.scope === "research_shadow" ? "Research shadow reconciliation: " : ""}${alert.message}`,
    })),
  ].filter((issue): issue is { severity: string; message: string } => issue !== null);
  const integrityState = integrityIssues.some((issue) => issue.severity === "error")
    ? "Action required"
    : integrityIssues.length > 0 ? "Warning" : "Healthy";
  const integrityColor = integrityState === "Healthy" ? "var(--status-good)" : integrityState === "Warning" ? "var(--status-warning)" : "var(--status-critical)";
  const integrityFreshness = [
    `Research ledger finalized through ${fmtDate(forwardStack.data?.operations?.latestFinalizedSession ?? forwardStack.data?.operations?.latestCompletedSession)}`,
    `Scheduler checked ${fmtTimestamp(schedulerAttempt)}`,
    `Alpaca ${acct?.available ? "connected" : "unavailable"}`,
    `Market data ${marketData?.regime.asOf ? `through ${fmtDate(marketData.regime.asOf)}` : "freshness unknown"}`,
  ].join(" · ");

  const researchRows = researchStatus.data?.rows ?? [];
  const majorResearchRows = [
    researchRows.find((row) => row.researchType === "Operational paper strategy"),
    researchRows.find((row) => row.name === "Canonical Dual Momentum · 189D/Monthly"),
    researchRows.find((row) => row.name === "Market-Residual Momentum"),
    researchRows.find((row) => row.name === "Fixed 50/50 DM/MRM"),
    researchRows.find((row) => row.name === "DM/MRM Volatility-Scaled Portfolio"),
    researchRows.find((row) => row.name.includes("Winner Grace")),
  ].filter((row): row is NonNullable<typeof row> => row !== undefined);

  const regime = marketData?.regime.current ?? null;
  const breadth = marketData?.marketSignals.score ?? null;
  const sectors = (marketData?.sectorPerformance ?? []).filter((r) => r.symbol !== "SPY");
  const rankedSectors = [...sectors].sort((a, b) => (b.changePct ?? 0) - (a.changePct ?? 0));
  const bestSector = rankedSectors[0];
  const worstSector = rankedSectors[rankedSectors.length - 1];

  const topScreener = [...(screener.data?.rows ?? [])]
    .filter((r) => r.compositeScore !== null)
    .sort((a, b) => (b.compositeScore ?? 0) - (a.compositeScore ?? 0))
    .slice(0, 4);

  const topInsider = (insider.data?.rows ?? []).slice(0, 4);

  const watchlistMovers = [...(symbols.data?.symbols ?? [])]
    .filter((s) => s.changePct !== null)
    .sort((a, b) => Math.abs(b.changePct ?? 0) - Math.abs(a.changePct ?? 0))
    .slice(0, 4);

  return (
    <>
      <div
        data-testid="operational-integrity"
        className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border px-4 py-2.5 text-xs"
        style={{
          borderColor: integrityColor,
          background: integrityState === "Healthy" ? "var(--status-good-bg)" : integrityState === "Warning" ? "var(--status-warning-bg)" : "var(--status-critical-bg)",
        }}
      >
        <span className="font-semibold tracking-wide" style={{ color: "var(--text-muted)" }}>OPERATIONAL INTEGRITY</span>
        <strong style={{ color: integrityColor }}>{integrityState}</strong>
        <span className="min-w-0 truncate" style={{ color: "var(--text-secondary)" }}>
          {integrityIssues.length === 0 ? integrityFreshness : `${integrityIssues.map((issue) => issue.message).join(" · ")} · ${integrityFreshness}`}
        </span>
      </div>
      <StatusStrip marketData={marketData} marketLoading={marketLoading} />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card
          title="Market state"
          className="order-2"
          meta={marketData?.regime.asOf ? `as of ${marketData.regime.asOf}` : undefined}
          onOpen={() => setPopup("market")}
          loading={marketLoading && !marketData}
          error={marketError}
          skeletonRows={4}
        >
          {marketData ? (
            <>
              <CardHeadline
                value={regime ?? "—"}
                valueColor={regime ? REGIME_COLOR[regime] : undefined}
                caption={
                  breadth === null
                    ? "Breadth unavailable"
                    : `Breadth ${breadth.toFixed(0)}/100 · ${marketData.marketSignals.symbolsTracked} symbols`
                }
              />
              {bestSector && (
                <CardRow
                  label={`Best · ${sectorName(bestSector.symbol)}`}
                  value={fmtPct(bestSector.changePct)}
                  valueColor={changeColor(bestSector.changePct)}
                />
              )}
              {worstSector && worstSector !== bestSector && (
                <CardRow
                  label={`Worst · ${sectorName(worstSector.symbol)}`}
                  value={fmtPct(worstSector.changePct)}
                  valueColor={changeColor(worstSector.changePct)}
                />
              )}
              <CardRow
                label="Trend template passing"
                value={`${marketData.trendTemplate.symbols.filter((s) => s.passes).length}/${marketData.trendTemplate.symbols.length}`}
              />
            </>
          ) : (
            <CardEmpty>Scanning the research universe…</CardEmpty>
          )}
        </Card>

        <Card
          title="Paper strategy"
          className="order-1 md:col-span-2"
          meta={automated.length > 0 ? <StatusBadge mode="paper">PAPER AUTOMATED</StatusBadge> : "automation off"}
          onOpen={() => setPopup("trading")}
          loading={account.loading && !account.data}
          error={account.error}
        >
          {acct?.available ? (
            <>
              <CardHeadline
                value={activeStrategy?.identity.displayName ?? "Paper automation off"}
                caption={`${positions.length} position${positions.length === 1 ? "" : "s"} · ${orderSummary?.openPending ?? 0} open/pending order${orderSummary?.openPending === 1 ? "" : "s"}`}
              />
              <CardRow
                label="Unrealized P&L"
                value={
                  positions.length === 0
                    ? "—"
                    : `${unrealized >= 0 ? "+" : "−"}${fmtMoney(Math.abs(unrealized))}`
                }
                valueColor={positions.length === 0 ? undefined : changeColor(unrealized)}
              />
              {activeStrategy && (
                <div className="mt-2 text-xs" style={{ color: "var(--text-muted)" }} title={activeStrategy.identity.selectionBiasNote}>
                  {activeStrategy.identity.actualConfig.lookback_trading_days}d · Top {activeStrategy.identity.actualConfig.top_n} · {String(activeStrategy.identity.actualConfig.rebalance_frequency)} · {activeStrategy.symbols.length} symbols
                  <br />{activeStrategy.identity.provenance} · <span className="underline decoration-dotted">{activeStrategy.identity.selectionContext}</span>
                </div>
              )}
              <div className="mt-2 grid grid-cols-2 gap-x-4 text-xs">
                <span style={{ color: "var(--text-muted)" }}>Forward start <strong style={{ color: "var(--text-primary)" }}>{fmtDate(daily.data?.startDate)}</strong></span>
                <span style={{ color: "var(--text-muted)" }}>Sessions <strong style={{ color: "var(--text-primary)" }}>{daily.data?.rows.length ?? "—"}</strong></span>
              </div>
              <div className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
                Forward return: <strong style={{ color: changeColor(daily.data?.forwardReturnPct ?? null) }}>{fmtPct(daily.data?.forwardReturnPct ?? null)}</strong> <span title={daily.data?.forwardReturnSource ?? undefined}>(Alpaca inception baseline)</span>
                {daily.data?.alignedBenchmarkComparison?.available ? (
                  <>
                    <br />Aligned full sessions: <strong style={{ color: changeColor(daily.data.alignedBenchmarkComparison.accountReturnPct ?? null) }}>{fmtPct(daily.data.alignedBenchmarkComparison.accountReturnPct ?? null)}</strong>
                    {" vs SPY "}<strong style={{ color: changeColor(daily.data.alignedBenchmarkComparison.benchmarkReturnPct ?? null) }}>{fmtPct(daily.data.alignedBenchmarkComparison.benchmarkReturnPct ?? null)}</strong>
                    {" · gap "}<strong>{(daily.data.alignedBenchmarkComparison.differencePctPoints ?? 0) >= 0 ? "+" : ""}{(daily.data.alignedBenchmarkComparison.differencePctPoints ?? 0).toFixed(2)}pp</strong>
                    <br />Max drawdown: paper <strong>{fmtPct(daily.data.alignedBenchmarkComparison.accountMaxDrawdownPct ?? null)}</strong>
                    {" vs SPY "}<strong>{fmtPct(daily.data.alignedBenchmarkComparison.benchmarkMaxDrawdownPct ?? null)}</strong>
                    <br /><span title={daily.data.benchmarkComparison.reason ?? undefined}>Settled through {fmtDate(daily.data.alignedBenchmarkComparison.endDate)} · {daily.data.alignedBenchmarkComparison.sessions} aligned sessions · the Aug 17 intraday inception and today are excluded.</span>
                  </>
                ) : (
                  <><br />vs SPY: unavailable — {daily.data?.alignedBenchmarkComparison.reason ?? daily.data?.benchmarkComparison.reason ?? "unaligned inception baseline"}</>
                )}
              </div>
            </>
          ) : (
            <CardEmpty>
              Alpaca isn't configured{acct?.reason ? `: ${acct.reason}` : "."} Open to see how.
            </CardEmpty>
          )}
        </Card>

        <div className="order-4 col-span-full mt-2">
          <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Opportunity Monitor</h2>
          <p className="text-xs" style={{ color: "var(--text-muted)" }}>Idea generation and market context — separate from paper execution and formal research.</p>
        </div>

        <Card
          title="Movers"
          className="order-4"
          meta={movers.data ? fmtRelative(movers.data.asOf) : undefined}
          onOpen={() => setPopup("movers")}
          loading={movers.loading && !movers.data}
          error={movers.error}
        >
          {movers.data ? (
            <>
              {movers.data.gainers.slice(0, 2).map((r) => (
                <CardRow
                  key={r.symbol}
                  label={r.symbol}
                  value={fmtPct(r.changePct)}
                  valueColor={changeColor(r.changePct)}
                />
              ))}
              {movers.data.losers.slice(0, 2).map((r) => (
                <CardRow
                  key={r.symbol}
                  label={r.symbol}
                  value={fmtPct(r.changePct)}
                  valueColor={changeColor(r.changePct)}
                />
              ))}
              {movers.data.streaks.length > 0 && (
                <div className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
                  {movers.data.streaks.length} symbol
                  {movers.data.streaks.length === 1 ? "" : "s"} on a 2+ day streak
                </div>
              )}
            </>
          ) : (
            <CardEmpty>No movers loaded.</CardEmpty>
          )}
        </Card>

        <Card
          title="Insider buying"
          className="order-4"
          meta={
            insider.data?.lastCompletedAt
              ? fmtRelative(insider.data.lastCompletedAt)
              : undefined
          }
          onOpen={() => setPopup("insider")}
          loading={insider.loading && !insider.data}
          error={insider.error}
        >
          {topInsider.length > 0 ? (
            topInsider.map((r, i) => (
              <CardRow
                key={`${r.issuerTicker}-${r.filedAt}-${i}`}
                label={`${r.issuerTicker} · ${r.filerName}`}
                value={fmtCompactMoney(r.transactionValue)}
                valueColor="var(--status-good)"
              />
            ))
          ) : (
            <CardEmpty>
              No open-market Form 4 purchases cached. Open to refresh from SEC EDGAR.
            </CardEmpty>
          )}
        </Card>

        <Card
          title="Screener"
          className="order-4"
          meta={screener.data ? `${screener.data.rows.length} symbols` : undefined}
          onOpen={() => setPopup("screener")}
          loading={screener.loading && !screener.data}
          error={screener.error}
        >
          {topScreener.length > 0 ? (
            <>
              <div className="mb-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                Highest composite score
              </div>
              {topScreener.map((r) => (
                <CardRow
                  key={r.symbol}
                  label={r.symbol}
                  value={`${(r.compositeScore ?? 0).toFixed(0)}/100`}
                />
              ))}
            </>
          ) : (
            <CardEmpty>Scanning tracked symbols…</CardEmpty>
          )}
        </Card>

        <Card
          title="Watchlist"
          className="order-4"
          meta={symbols.data ? `${symbols.data.symbols.length} symbols` : undefined}
          onOpen={() => setPopup("watchlist")}
          loading={symbols.loading && !symbols.data}
          error={symbols.error}
        >
          {watchlistMovers.length > 0 ? (
            <>
              <div className="mb-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                Biggest moves today
              </div>
              {watchlistMovers.map((s) => (
                <CardRow
                  key={s.symbol}
                  label={s.symbol}
                  value={fmtPct(s.changePct)}
                  valueColor={changeColor(s.changePct)}
                />
              ))}
            </>
          ) : (
            <CardEmpty>No cached daily bars yet.</CardEmpty>
          )}
        </Card>

        <Card title="Daily digest" className="order-4" onOpen={() => setPopup("digest")}>
          <CardEmpty>
            Compose today's regime, movers and insider buys into one summary. Preview only —
            nothing is scheduled or emailed.
          </CardEmpty>
        </Card>

        <section className="order-3 rounded-xl border p-4 md:col-span-2 xl:col-span-3" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
          <button type="button" onClick={() => { setFocusedResearchName(null); setPopup("researchStatus"); }} className="mb-3 flex w-full items-center justify-between text-left">
            <span className="text-[11px] font-semibold tracking-wide" style={{ color: "var(--text-muted)" }}>RESEARCH STATUS</span>
            <span className="text-xs" style={{ color: "var(--series-1)" }}>Open all research ↗</span>
          </button>
          {researchStatus.error ? (
            <div className="text-xs" style={{ color: "var(--status-critical)" }}>{researchStatus.error}</div>
          ) : researchStatus.data ? (
            <div className="space-y-1">
              <div className="hidden grid-cols-[minmax(0,1fr)_auto_minmax(11rem,auto)_auto] gap-x-4 text-[10px] font-semibold tracking-wide sm:grid" style={{ color: "var(--text-muted)" }}>
                <span>STRATEGY</span><span>MODE</span><span>MATURITY</span><span>FORWARD RETURN</span>
              </div>
              {majorResearchRows.map((row) => {
                const isPaper = row.researchType === "Operational paper strategy";
                const seriesKey = row.name.startsWith("Canonical Dual") ? "dm" : row.name === "Market-Residual Momentum" ? "mrm" : row.name.startsWith("Fixed 50/50") ? "fiftyFifty" : row.name.startsWith("DM/MRM Volatility") ? "volScaled" : null;
                const series = forwardStack.data?.series.find((item) => item.key === seriesKey);
                const sessions = isPaper ? daily.data?.rows.length ?? 0 : series?.sessions ?? 0;
                const mode = isPaper ? "paper" : row.status.includes("Closed") ? "closed" : row.status.includes("Frozen") || row.name.startsWith("DM/MRM Volatility") ? "frozen" : "shadow";
                const badge = mode === "paper" ? "PAPER AUTOMATED" : mode === "shadow" ? "SHADOW · NO ORDERS" : mode === "frozen" ? "FROZEN SHADOW" : "CLOSED";
                const maturity = mode === "closed" ? "Closed result" : maturityText(sessions, isPaper ? daily.data?.maturity : forwardStack.data?.maturity);
                const returnPct = isPaper ? daily.data?.forwardReturnPct ?? null : series?.returnPct ?? null;
                return (
                  <button
                    type="button"
                    key={row.name}
                    onClick={() => {
                      if (isPaper) setPopup("trading");
                      else { setFocusedResearchName(row.name); setPopup("researchStatus"); }
                    }}
                    className="grid w-full grid-cols-1 items-center gap-1 border-t py-2 text-left text-xs hover:bg-black/[0.02] sm:grid-cols-[minmax(0,1fr)_auto_minmax(11rem,auto)_auto] sm:gap-x-4"
                    style={{ borderColor: "var(--gridline)" }}
                  >
                    <div className="min-w-0">
                      <div className="font-medium" style={{ color: "var(--text-primary)" }}>{row.name}</div>
                      <div className="truncate" style={{ color: "var(--text-muted)" }}>{row.status}</div>
                    </div>
                    <div><StatusBadge mode={mode}>{badge}</StatusBadge></div>
                    <span style={{ color: "var(--text-secondary)" }}>{maturity}</span>
                    <span className="tabular-nums" style={{ color: changeColor(returnPct) }}>{fmtPct(returnPct)}</span>
                  </button>
                );
              })}
              {forwardStack.data?.currentBlend && (
                <button type="button" onClick={() => { setFocusedResearchName("DM/MRM Volatility-Scaled Portfolio"); setPopup("researchStatus"); }} className="w-full border-t pt-2 text-left text-xs" style={{ borderColor: "var(--gridline)", color: "var(--text-secondary)" }}>
                  Frozen vol-scaled target: <strong>DM {(forwardStack.data.currentBlend.dmTargetWeight * 100).toFixed(1)}%</strong> / <strong>MRM {(forwardStack.data.currentBlend.mrmTargetWeight * 100).toFixed(1)}%</strong>
                  <span style={{ color: "var(--text-muted)" }}> · reset {fmtDate(forwardStack.data.currentBlend.lastWeightResetDate)} · next {fmtDate(forwardStack.data.currentBlend.nextScheduledReset)} · inspect ↗</span>
                </button>
              )}
              <div className="text-[11px]" style={{ color: "var(--text-muted)" }}>
                SPY is a normalized benchmark, not a strategy. No annualized metrics are shown before the maturity gate.
              </div>
            </div>
          ) : <CardEmpty>Loading tracked research branches…</CardEmpty>}
        </section>
      </div>

      <Modal
        open={popup === "market"}
        onClose={close}
        title="Market state"
        subtitle="Regime, sector performance, rotation, trend template and breadth"
        size="xl"
      >
        <MarketView
          data={marketData}
          loading={marketLoading}
          error={marketError}
          onRefresh={onRefreshMarket}
        />
      </Modal>

      <Modal
        open={popup === "trading"}
        onClose={close}
        title="Paper trading"
        subtitle="Account, automated execution, kill switch, rebalance history and live signals"
        size="xl"
      >
        <LiveMonitorView />
      </Modal>

      <Modal
        open={popup === "movers"}
        onClose={close}
        title="Trending movers"
        subtitle="Gainers, losers and momentum streaks across the research universe"
        size="xl"
        headerAction={<MoversRefresh />}
      >
        <MoversPanel />
      </Modal>

      <Modal
        open={popup === "insider"}
        onClose={close}
        title="Insider buying"
        subtitle="Open-market purchases from SEC EDGAR Form 4 filings"
        size="xl"
      >
        <InsiderPanel />
      </Modal>

      <Modal
        open={popup === "screener"}
        onClose={close}
        title="Screener"
        subtitle="Cross-sectional factor ranks across every tracked symbol"
        size="xl"
        headerAction={<ScreenerRefresh />}
      >
        <ScreenerView />
      </Modal>

      <Modal
        open={popup === "watchlist"}
        onClose={close}
        title="Watchlist"
        subtitle="Tracked symbols, live quotes and per-symbol detail"
        size="xl"
      >
        <SymbolsView />
      </Modal>

      <Modal
        open={popup === "digest"}
        onClose={close}
        title="Daily digest preview"
        subtitle="Preview only — no scheduler, no email"
        size="lg"
      >
        <DigestPanel />
      </Modal>

      <Modal
        open={popup === "researchStatus"}
        onClose={close}
        title="Research status"
        subtitle="What each research branch has actually shown, and what's blocking the rest — organizational only, never a verdict of its own"
        size="xl"
      >
        <ResearchStatusView focusName={focusedResearchName} />
      </Modal>
    </>
  );
}
