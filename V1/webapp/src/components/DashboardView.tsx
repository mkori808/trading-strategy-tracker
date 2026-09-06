import { useCallback, useEffect, useState } from "react";
import {
  api,
  type DailyPerformance,
  type ExecutionAccountStatus,
  type ExecutionStrategyConfig,
  type ExecutionSummary,
  type ForwardStackStatus,
  type LiveAccountResponse,
  type MarketResponse,
  type OptimizedDmHourlyShadowStatus,
  type PlainVsResidualForwardStatus,
  type PropShadowStatus,
  type RebalanceRunRow,
  type ResearchStatusDashboard,
  type ShadowLiveMarks,
} from "../api";
import { useResource } from "../useResource";
import { KEYS } from "../resourceKeys";
import {
  changeColor,
  fmtCompactMoney,
  fmtDate,
  fmtMoney,
  fmtPct,
  fmtRelative,
  REGIME_COLOR,
} from "../format";
import { sectorName } from "../sectorNames";
import { Card, CardEmpty, CardHeadline, CardRow, StatusBadge } from "./Card";
import { Modal } from "./Modal";
import { MarketView } from "./MarketView";
import { ScreenerRefresh, ScreenerView } from "./ScreenerView";
import { SymbolsView } from "./SymbolsView";
import { LiveMonitorView, PropShadowRows } from "./LiveMonitorView";
import { StrategyAccountsPanel } from "./StrategyAccountsPanel";
import { DigestPanel, InsiderPanel, MoversPanel, MoversRefresh } from "./ResearchPanels";
import { useInsider, useMovers, useScreener, useSymbols } from "../dataHooks";
import { StatusStrip } from "./StatusStrip";
import { PlainVsResidualForwardView } from "./PlainVsResidualForwardView";

const FORWARD_STATUS_POLL_MS = 60_000;
const fetchForwardStackStatus = () => api.researchForwardStack();

/** One labeled section break in the dashboard grid -- extends the
 * "Opportunity Monitor" heading pattern this file already used (a bare
 * `col-span-full` heading + one-line description directly in the grid, no
 * bordered wrapper) to every section, so evidence types read as clearly
 * separated groups: what places real orders, what's prospective forward
 * evidence, what's completed historical research, and what's a synthetic
 * account simulation are never visually conflated with each other. */
function DashboardSection({ title, description, order }: { title: string; description: string; order: number }) {
  // Inline `style={{ order }}`, not a Tailwind `order-[N]` utility class:
  // Tailwind's build-time scanner only picks up class names that appear as
  // literal strings in source, so a template-literal-interpolated class
  // name here would silently never be generated. The `order` CSS property
  // set via inline style has no such restriction.
  return (
    <div className="col-span-full mt-2" style={{ order }}>
      <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>{title}</h2>
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>{description}</p>
    </div>
  );
}

function fmtTimestamp(value: string | null | undefined): string {
  if (!value) return "not recorded";
  return new Date(value).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
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
  | "plainVsResidual"
  | "simulations";

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
  onOpenResearch,
}: {
  marketData: MarketResponse | null;
  marketLoading: boolean;
  marketError: string | null;
  onRefreshMarket: () => void;
  /** Research Status used to be a Dashboard card+popup; it's now its own
   * tab (too much of its per-strategy detail duplicated the Strategy
   * accounts panel to justify a second, shallower copy on the home page).
   * This still lets a card send the user there with a specific strategy
   * pre-focused, the same deep-link the old popup supported. */
  onOpenResearch: (focusName?: string) => void;
}) {
  const [popup, setPopup] = useState<Popup>(null);
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
  const plainVsResidual = useResource<PlainVsResidualForwardStatus>(
    KEYS.researchPlainVsResidualForward,
    () => api.researchPlainVsResidualForward(),
  );
  const propShadows = useResource<PropShadowStatus>(KEYS.researchPropShadows, () => api.researchPropShadows());
  const summary = useResource<ExecutionSummary>(KEYS.executionSummary, () => api.executionSummary());
  const hourlyShadow = useResource<OptimizedDmHourlyShadowStatus>(KEYS.researchOptimizedDmHourlyShadow, () => api.researchOptimizedDmHourlyShadow());
  const executionRuns = useResource<RebalanceRunRow[]>(KEYS.executionRuns, () => api.executionRuns(20));
  const shadowLiveMarks = useResource<ShadowLiveMarks>(KEYS.researchShadowLiveMarks, () => api.researchShadowLiveMarks());
  const refreshForwardStack = forwardStack.refresh;
  const refreshAccount = account.refresh;
  const refreshSummary = summary.refresh;
  const refreshExecutionRuns = executionRuns.refresh;
  const refreshHourlyShadow = hourlyShadow.refresh;
  const refreshShadowLiveMarks = shadowLiveMarks.refresh;

  // Unlike the expensive market/screener resources, these are persisted-
  // state reads (or, for liveAccount/shadow-live-marks, a batched quote
  // lookup) -- cheap enough to keep current while Dashboard remains
  // mounted, the same way LiveMonitorView already polls them every 30s
  // while its own popup is open. Without this, the Strategy accounts panel
  // embedded directly on the dashboard (see below) would only ever refresh
  // once, on page load, unless the user separately opened the Paper
  // trading popup -- the forward-stack scheduler heartbeat check has the
  // same "must stay current" requirement this effect already served before
  // the other resources joined it.
  useEffect(() => {
    const refreshAll = () => {
      void refreshForwardStack();
      void refreshAccount();
      void refreshSummary();
      void refreshExecutionRuns();
      void refreshHourlyShadow();
      void refreshShadowLiveMarks();
    };
    refreshAll();
    const timer = window.setInterval(refreshAll, FORWARD_STATUS_POLL_MS);
    return () => window.clearInterval(timer);
  }, [refreshForwardStack, refreshAccount, refreshSummary, refreshExecutionRuns, refreshHourlyShadow, refreshShadowLiveMarks]);

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

  const plainVsResidualScore = plainVsResidual.data?.scorecard;
  // Mirrors PlainVsResidualForwardView's own gate: eligible for a real
  // (still human-written) comparison only once the 12-month checkpoint is
  // reached -- everything before that stays "no inference" on the card too.
  const plainVsResidualEligible = (plainVsResidualScore?.checkpoint.reached.length ?? 0) >= 2;

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
        <DashboardSection
          title="Live / Execution"
          description="Real Alpaca paper orders and account state — the only section that actually trades."
          order={1}
        />

        <Card
          title="Market state"
          className="order-[12]"
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
          className="order-[2] md:col-span-2 xl:col-span-3"
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

        <DashboardSection
          title="Opportunity Monitor"
          description="Idea generation and market context — separate from paper execution and formal research."
          order={11}
        />

        <Card
          title="Movers"
          className="order-[13]"
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
          className="order-[14]"
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
          className="order-[15]"
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
          className="order-[16]"
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

        <Card title="Daily digest" className="order-[17]" onOpen={() => setPopup("digest")}>
          <CardEmpty>
            Compose today's regime, movers and insider buys into one summary. Preview only —
            nothing is scheduled or emailed.
          </CardEmpty>
        </Card>

        <DashboardSection
          title="Prospective Research"
          description="Forward-only shadow evidence collected in real time from frozen strategies — no orders, no parameter changes, no early verdicts."
          order={3}
        />

        {acct?.available && (
          <div className="order-[5] md:col-span-2 xl:col-span-3">
            <StrategyAccountsPanel
              live={account.data!}
              summary={summary.data}
              ownership={executionAccount.data}
              forward={forwardStack.data}
              prop={propShadows.data}
              hourly={hourlyShadow.data}
              runs={executionRuns.data ?? []}
              liveMarks={shadowLiveMarks.data ?? {}}
            />
          </div>
        )}

        <Card
          title="Plain vs residual momentum"
          className="order-[8]"
          meta={<StatusBadge mode="shadow">SHADOW · FROZEN</StatusBadge>}
          onOpen={() => setPopup("plainVsResidual")}
          loading={plainVsResidual.loading && !plainVsResidual.data}
          error={plainVsResidual.error}
        >
          {plainVsResidual.data ? (
            <>
              <CardHeadline
                value={`${plainVsResidualScore?.observations ?? 0} obs.`}
                caption={plainVsResidualEligible ? plainVsResidualScore?.checkpoint.interpretationStatus : "No inference — prospective evidence collection only."}
              />
              <CardRow
                label="D minus C (return)"
                value={plainVsResidualScore?.pairedRelative ? `${plainVsResidualScore.pairedRelative.dMinusCReturnDiffPp >= 0 ? "+" : ""}${plainVsResidualScore.pairedRelative.dMinusCReturnDiffPp.toFixed(2)}pp` : "—"}
                valueColor={plainVsResidualScore?.pairedRelative ? changeColor(plainVsResidualScore.pairedRelative.dMinusCReturnDiffPp) : undefined}
              />
              <CardRow
                label="Next checkpoint"
                value={plainVsResidualScore?.checkpoint.next ? `${plainVsResidualScore.checkpoint.next.months}mo · ${fmtDate(plainVsResidualScore.checkpoint.next.date)}` : "—"}
              />
            </>
          ) : (
            <CardEmpty>Awaiting first forward session.</CardEmpty>
          )}
        </Card>

        <DashboardSection
          title="Historical Research"
          description="Completed backtest evidence and validation status — see the Strategies page for the full leaderboard."
          order={6}
        />

        <Card
          title="Research program"
          className="order-[9]"
          onOpen={() => onOpenResearch()}
        >
          {researchStatus.data ? (
            <>
              <CardHeadline value={researchStatus.data.summary.validatedStrategies} caption="Validated strategies" />
              <CardRow label="Methodology complete" value={researchStatus.data.summary.methodologyCompleteSystems} />
              <CardRow label="Rejected hypotheses" value={researchStatus.data.summary.rejectedHypotheses} />
              <CardRow label="Data-blocked" value={researchStatus.data.summary.dataBlockedResearchItems} />
            </>
          ) : (
            <CardEmpty>Loading research program summary…</CardEmpty>
          )}
        </Card>

        <DashboardSection
          title="Simulations"
          description="Synthetic prop and self-funded account accounting over the live paper equity stream — not a strategy result, not an order."
          order={7}
        />

        <Card
          title="Prop / self-funded shadows"
          className="order-[10]"
          meta={<StatusBadge mode="shadow">SIMULATED ACCOUNTING</StatusBadge>}
          onOpen={() => setPopup("simulations")}
          loading={propShadows.loading && !propShadows.data}
          error={propShadows.error}
        >
          {propShadows.data ? (
            <>
              <CardHeadline
                value={`${propShadows.data.shadows.length + propShadows.data.selfFundedShadows.length} shadow${propShadows.data.shadows.length + propShadows.data.selfFundedShadows.length === 1 ? "" : "s"}`}
                caption={propShadows.data.maturity}
              />
              {propShadows.data.warnings.length > 0 && (
                <CardRow label="Warnings" value={propShadows.data.warnings.length} valueColor="var(--status-warning)" />
              )}
              <CardRow label="Completed sessions" value={propShadows.data.completedSessions} />
            </>
          ) : (
            <CardEmpty>No prop/self-funded shadows running.</CardEmpty>
          )}
        </Card>
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
        open={popup === "plainVsResidual"}
        onClose={close}
        title="Plain Momentum vs Market-Residual Momentum"
        subtitle="Prospective forward shadow — frozen strategies, no orders, no early verdicts"
        size="xl"
      >
        <PlainVsResidualForwardView />
      </Modal>

      <Modal
        open={popup === "simulations"}
        onClose={close}
        title="Prop / self-funded shadows"
        subtitle="Synthetic account accounting over the live paper equity stream — not a strategy result, not an order"
        size="xl"
      >
        {propShadows.error ? (
          <div className="text-xs" style={{ color: "var(--status-critical)" }}>{propShadows.error}</div>
        ) : propShadows.data ? (
          <div className="space-y-3">
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              {propShadows.data.program} · {propShadows.data.completedSessions} completed sessions · {propShadows.data.maturity}
            </p>
            {propShadows.data.warnings.map((warning) => (
              <div key={warning} className="rounded-md border px-3 py-2 text-xs" style={{ borderColor: "var(--status-warning)", background: "var(--status-warning-bg)" }}>
                <strong>Operational integrity: </strong>{warning}
              </div>
            ))}
            <PropShadowRows data={propShadows.data} />
            <p className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              Historical Monte Carlo simulation results remain separate — see the Strategies page. This panel is prospective synthetic accounting only.
            </p>
          </div>
        ) : (
          <CardEmpty>Loading prop/self-funded shadow state…</CardEmpty>
        )}
      </Modal>
    </>
  );
}
