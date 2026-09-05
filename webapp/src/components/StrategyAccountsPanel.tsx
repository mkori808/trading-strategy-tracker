import { useMemo, useState } from "react";
import {
  api,
  type DailyPerformance,
  type ExecutionAccountStatus,
  type ExecutionSummary,
  type ForwardStackStatus,
  type LiveAccountResponse,
  type OptimizedDmHourlyShadowStatus,
  type PropShadowStatus,
  type RebalanceRunRow,
  type ShadowLiveMark,
  type ShadowLiveMarks,
} from "../api";
import { changeColor, fmtMoney, fmtPct } from "../format";
import { KEYS } from "../resourceKeys";
import { useResource } from "../useResource";
import { DailyPerformancePanel } from "./DailyPerformancePanel";
import { Modal } from "./Modal";
import { StatTile } from "./StatTile";
import { StrategyComparisonPanel } from "./StrategyComparisonPanel";

type HistoryPoint = { date: string; equity: number };
export type DisplayPosition = {
  symbol: string;
  side: string;
  weight: number | null;
  marketValue: number | null;
  currentPrice: number | null;
};

export type PositionHistoryEvent = {
  date: string;
  holdings: Record<string, number>;
  source?: string;
};

export type StrategyAccount = {
  key: string;
  name: string;
  mode: "Paper" | "Shadow" | "Prop shadow";
  evidence: string;
  equity: number | null;
  startingEquity: number | null;
  cash: number | null;
  buyingPower: number | null;
  dayTrades: number | null;
  unrealizedPnl: number | null;
  sessions: number;
  since: string | null;
  history: HistoryPoint[];
  positions: DisplayPosition[];
  positionsAsOf: string | null;
  positionNote: string;
  positionHistory: PositionHistoryEvent[];
  note: string;
  // A live 'right now' mark on top of `equity`/`sessions` above -- see
  // engine/shadow_live_mark.py. Paper's own equity is ALREADY live (polled
  // straight from Alpaca every 30s), so it never gets one of these; every
  // shadow does, so every row in the list ticks the same way.
  liveMark: ShadowLiveMark | null;
};

function maxDrawdown(values: number[]): number {
  let peak = values[0] ?? 0;
  let worst = 0;
  for (const value of values) {
    peak = Math.max(peak, value);
    if (peak) worst = Math.min(worst, (value / peak - 1) * 100);
  }
  return worst;
}

function dailyFromHistory(
  history: HistoryPoint[],
  spy: Map<string, number>,
  liveMark?: ShadowLiveMark | null,
  spyLiveMark?: ShadowLiveMark | null,
): DailyPerformance {
  const rows = history.map((point, index) => {
    const prior = history[index - 1];
    const spyLevel = spy.get(point.date);
    const priorSpy = prior ? spy.get(prior.date) : undefined;
    return {
      date: point.date,
      equity: point.equity,
      profitLoss: prior ? point.equity - prior.equity : null,
      profitLossPct: prior && prior.equity ? (point.equity / prior.equity - 1) * 100 : null,
      benchmarkPct: spyLevel !== undefined && priorSpy ? (spyLevel / priorSpy - 1) * 100 : null,
    };
  });
  const aligned = history.filter((point) => spy.has(point.date));
  const first = aligned[0];
  const last = aligned.at(-1);
  const accountGrowth = first
    ? aligned.map((point) => (point.equity / first.equity) * 100)
    : [];
  const spyGrowth = first
    ? aligned.map((point) => (spy.get(point.date)! / spy.get(first.date)!) * 100)
    : [];
  const accountReturn = first && last ? (last.equity / first.equity - 1) * 100 : null;
  const benchmarkReturn = first && last
    ? (spy.get(last.date)! / spy.get(first.date)! - 1) * 100
    : null;
  const comparisonAvailable = aligned.length > 1;
  const lastSettledDate = history.at(-1)?.date ?? null;
  const todayDate = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  // Never show a reconstructed live "today" once the real settled row for
  // today already exists in history -- same rule as the paper account's
  // own today row: a derived figure never coexists with the broker-
  // confirmed one it was standing in for.
  const today = (liveMark?.available && liveMark.equity !== null && lastSettledDate !== todayDate) ? {
    date: todayDate,
    equity: liveMark.equity,
    profitLoss: liveMark.profitLoss,
    profitLossPct: liveMark.profitLossPct,
    benchmarkPct: spyLiveMark?.available ? spyLiveMark.profitLossPct : null,
    inProgress: true as const,
  } : null;
  return {
    available: true,
    reason: null,
    startDate: history[0]?.date ?? null,
    benchmarkSymbol: "SPY",
    rows,
    today,
    forwardBaselineEquity: history[0]?.equity ?? null,
    forwardReturnPct: accountReturn,
    forwardReturnSource: "settled synthetic equity ledger",
    maturity: { label: "Prospective synthetic", reached: [], next: null },
    benchmarkComparison: { available: comparisonAvailable, reason: comparisonAvailable ? null : "Not enough aligned sessions." },
    alignedBenchmarkComparison: comparisonAvailable ? {
      available: true,
      reason: null,
      baselineDate: first.date,
      startDate: first.date,
      endDate: last!.date,
      sessions: aligned.length,
      accountReturnPct: accountReturn ?? undefined,
      benchmarkReturnPct: benchmarkReturn ?? undefined,
      differencePctPoints: (accountReturn ?? 0) - (benchmarkReturn ?? 0),
      accountMaxDrawdownPct: maxDrawdown(accountGrowth),
      benchmarkMaxDrawdownPct: maxDrawdown(spyGrowth),
      growth: aligned.map((point, index) => ({
        date: point.date,
        account: accountGrowth[index],
        benchmark: spyGrowth[index],
        baseline: index === 0,
      })),
      note: "Settled prospective sessions only; shadow equity is synthetic and cannot be reconciled to broker cash.",
    } : { available: false, reason: "Not enough SPY-aligned settled sessions." },
  };
}

function signedMoney(value: number | null): string {
  if (value === null) return "—";
  return `${value >= 0 ? "+" : "−"}${fmtMoney(Math.abs(value))}`;
}

function formatPositionTimestamp(value: string | null): string {
  if (!value) return "No position timestamp";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return `As of ${value}`;
  return `As of ${new Date(value).toLocaleString()}`;
}

/** Prefer a live mark's equity over the last settled/computed figure when
 * one is available -- `scale` converts a normalized (~100-base) NAV mark
 * into the same dollar display every other field in this row already
 * uses (`dm`/`mrm`/`fiftyFifty`/`volScaled` are the only series on that
 * normalized scale; every other shadow's live mark is already in dollars,
 * matching its own `currentEquity`). Falls back to the settled equity,
 * with no `liveMark`, whenever the mark is unavailable -- never blocks
 * the row on a missing quote. */
function withLiveMark(
  settledEquity: number | null,
  mark: ShadowLiveMark | undefined,
  scale = 1,
): { equity: number | null; liveMark: ShadowLiveMark | null } {
  if (mark?.available && mark.equity !== null) {
    return { equity: mark.equity * scale, liveMark: mark };
  }
  return { equity: settledEquity, liveMark: mark ?? null };
}

function LiveDot({ title }: { title: string }) {
  return (
    <span title={title} className="relative inline-flex h-2 w-2 shrink-0" aria-label="Live">
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-75" style={{ background: "var(--status-good)" }} />
      <span className="relative inline-flex h-2 w-2 rounded-full" style={{ background: "var(--status-good)" }} />
    </span>
  );
}

function positionsFromWeights(
  holdings: Record<string, number> | undefined,
  equity: number | null,
): DisplayPosition[] {
  return Object.entries(holdings ?? {})
    .map(([symbol, weight]) => ({
      symbol,
      side: weight < 0 ? "short" : "long",
      weight,
      marketValue: equity === null ? null : equity * weight,
      currentPrice: null,
    }))
    .sort((a, b) => Math.abs(b.weight) - Math.abs(a.weight));
}

export function StrategyAccountsPanel({
  live,
  summary,
  ownership,
  forward,
  prop,
  hourly,
  runs,
  liveMarks,
}: {
  live: LiveAccountResponse;
  summary: ExecutionSummary | null;
  ownership: ExecutionAccountStatus | null;
  forward: ForwardStackStatus | null;
  prop: PropShadowStatus | null;
  hourly: OptimizedDmHourlyShadowStatus | null;
  runs: RebalanceRunRow[];
  liveMarks: ShadowLiveMarks;
}) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const paperDaily = useResource<DailyPerformance>(KEYS.executionDaily, () => api.executionDaily());
  const spy = useMemo(() => new Map(
    (forward?.series.find((row) => row.key === "spy")?.history ?? []).map((point) => [point.date, point.nav]),
  ), [forward]);
  const accounts = useMemo<StrategyAccount[]>(() => {
    const rows: StrategyAccount[] = [];
    const acct = live.account;
    const paperEquity = acct.equity ?? null;
    const paperPositionHistory = runs
      .filter((run) => run.strategyName === (ownership?.owner?.strategyName ?? "Dual Momentum") && run.status.startsWith("completed") && run.targetWeights)
      .map((run) => ({ date: run.rebalanceDate, holdings: run.targetWeights!, source: "completed_rebalance" }))
      .sort((a, b) => a.date.localeCompare(b.date));
    rows.push({
      key: ownership?.owner?.strategyId ?? "paper-account",
      name: ownership?.owner?.displayName ?? "Alpaca paper account",
      mode: "Paper",
      evidence: "Execution-observed",
      equity: paperEquity,
      startingEquity: summary?.startingEquity ?? null,
      cash: acct.cash ?? null,
      buyingPower: acct.buyingPower ?? null,
      dayTrades: acct.daytradeCount ?? null,
      unrealizedPnl: live.positions.reduce((total, position) => total + (position.unrealizedPl ?? 0), 0),
      sessions: paperDaily.data?.rows.length ?? 0,
      since: summary?.firstTradeAt?.slice(0, 10) ?? null,
      history: (paperDaily.data?.rows ?? []).map((row) => ({ date: row.date, equity: row.equity })),
      positions: live.positions.map((position) => ({
        symbol: position.symbol,
        side: position.side,
        weight: paperEquity && position.marketValue !== null ? position.marketValue / paperEquity : null,
        marketValue: position.marketValue,
        currentPrice: position.currentPrice,
      })),
      positionsAsOf: live.clock.timestamp ?? null,
      positionNote: "Actual open positions reported by Alpaca Paper.",
      positionHistory: paperPositionHistory,
      note: "Real Alpaca paper-account values. They are strategy-specific only while this remains the account's sole execution owner.",
      // Already truly live (polled straight from Alpaca every 30s) -- a
      // reconstructed live mark would be a strictly worse copy of a number
      // this row already has.
      liveMark: null,
    });
    for (const series of forward?.series ?? []) {
      if (series.key === "spy") continue;
      const history = (series.history ?? []).map((point) => ({ date: point.date, equity: point.nav * 1_000 }));
      const holdings = series.key === "dm"
        ? forward?.currentBlend?.dmHoldings
        : series.key === "mrm"
          ? forward?.currentBlend?.mrmHoldings
          : series.key === "fiftyFifty"
            ? forward?.currentBlend?.fixedCombinedHoldings
            : forward?.currentBlend?.combinedHoldings;
      const syntheticEquity = series.nav === null ? null : series.nav * 1_000;
      const { equity: liveEquity, liveMark } = withLiveMark(syntheticEquity, liveMarks[series.key], 1_000);
      rows.push({
        key: series.key,
        name: series.series,
        mode: "Shadow",
        evidence: "Prospective synthetic",
        equity: liveEquity,
        liveMark,
        startingEquity: history[0]?.equity ?? 100_000,
        cash: null,
        buyingPower: null,
        dayTrades: null,
        unrealizedPnl: null,
        sessions: series.sessions,
        since: history[0]?.date ?? null,
        history,
        positions: positionsFromWeights(holdings, syntheticEquity),
        positionsAsOf: forward?.operations?.lastSourceSession ?? series.lastUpdate,
        positionNote: series.key === "dm" || series.key === "mrm"
          ? "Simulated holdings from the latest completed canonical sleeve replay."
          : "Simulated combined sleeve holdings; overlapping symbols are aggregated into one weight.",
        positionHistory: series.positionHistory ?? [],
        note: "Normalized research NAV scaled to a hypothetical $100,000 starting account. No broker cash, buying power, positions, or orders exist.",
      });
    }
    if (hourly?.available && hourly.strategy) {
      const equity = hourly.currentEquity ?? null;
      const { equity: liveEquity, liveMark } = withLiveMark(equity, liveMarks[hourly.key]);
      rows.push({
        key: hourly.key,
        name: hourly.strategy,
        mode: "Shadow",
        evidence: "Blind pre-activation backfill + prospective synthetic",
        equity: liveEquity,
        liveMark,
        startingEquity: hourly.startingEquity ?? 100_000,
        cash: null,
        buyingPower: null,
        dayTrades: null,
        unrealizedPnl: null,
        sessions: hourly.dailyHistory?.length ?? 0,
        since: hourly.backfillStart ?? null,
        history: (hourly.dailyHistory ?? []).map((point) => ({ date: point.date, equity: point.equity })),
        positions: positionsFromWeights(hourly.currentHoldings, equity),
        positionsAsOf: hourly.lastUpdate ?? null,
        positionNote: "Hourly simulated holdings selected from the locked Optimized DM universe; no Alpaca orders are placed.",
        positionHistory: hourly.positionHistory ?? [],
        note: `${hourly.backfillSessions ?? 0} aligned session(s) are a blind pre-activation backfill and ${hourly.prospectiveSessions ?? 0} session(s) are true prospective evidence. The rule was specified before the backfill results were inspected; the segments remain separately counted.`,
      });
    }
    for (const shadow of prop?.shadows ?? []) {
      const positions = (prop?.currentParentPositions ?? []).map((position) => {
        const parentWeight = position.weight ?? (
          live.account.equity && position.marketValue !== null
            ? position.marketValue / live.account.equity
            : null
        );
        const weight = parentWeight === null ? null : parentWeight * shadow.scale;
        return {
          symbol: position.symbol,
          side: position.side,
          weight,
          marketValue: weight === null ? null : 100_000 * weight,
          currentPrice: position.currentPrice,
        };
      });
      const { equity: liveEquity, liveMark } = withLiveMark(shadow.currentEquity, liveMarks[shadow.key]);
      rows.push({
        key: shadow.key,
        name: `Optimized DM ${shadow.scale.toFixed(2)}x · ${shadow.label}`,
        mode: "Prop shadow",
        evidence: "Synthetic prop accounting on observed parent stream",
        equity: liveEquity,
        liveMark,
        startingEquity: 100_000,
        cash: null,
        buyingPower: null,
        dayTrades: null,
        unrealizedPnl: null,
        sessions: shadow.sessions,
        since: shadow.history?.[0]?.date ?? null,
        history: shadow.history ?? [],
        positions,
        positionsAsOf: prop?.lastSnapshot ?? null,
        positionNote: `Synthetic ${shadow.scale.toFixed(2)}x copy of the observed Optimized DM parent positions. Values are modeled against a $100,000 prop account.`,
        positionHistory: paperPositionHistory.map((event) => ({
          date: event.date,
          holdings: Object.fromEntries(Object.entries(event.holdings).map(([symbol, weight]) => [symbol, weight * shadow.scale])),
          source: "completed_parent_rebalance",
        })),
        note: `Synthetic prop-rule ledger; ${fmtMoney(shadow.fees)} in modeled fees and ${signedMoney(shadow.netPayout)} net payout. It cannot place orders.`,
      });
    }
    for (const shadow of prop?.selfFundedShadows ?? []) {
      const positions = (prop?.currentParentPositions ?? []).map((position) => {
        const parentWeight = position.weight ?? (
          live.account.equity && position.marketValue !== null
            ? position.marketValue / live.account.equity
            : null
        );
        const marketValue = parentWeight === null ? null : parentWeight * shadow.fixedExposure;
        return {
          symbol: position.symbol,
          side: position.side,
          weight: marketValue === null || !shadow.currentEquity ? null : marketValue / shadow.currentEquity,
          marketValue,
          currentPrice: position.currentPrice,
        };
      });
      const { equity: liveEquity, liveMark } = withLiveMark(shadow.currentEquity, liveMarks[shadow.key]);
      rows.push({
        key: shadow.key,
        name: shadow.label,
        mode: "Shadow",
        evidence: "Self-funded synthetic on observed parent stream",
        equity: liveEquity,
        liveMark,
        startingEquity: shadow.startingCapital,
        cash: null,
        buyingPower: null,
        dayTrades: null,
        unrealizedPnl: null,
        sessions: shadow.sessions,
        since: shadow.history?.[0]?.date ?? null,
        history: shadow.history ?? [],
        positions,
        positionsAsOf: prop?.lastSnapshot ?? null,
        positionNote: `Synthetic copy of the observed Optimized DM positions at ${fmtMoney(shadow.fixedExposure)} fixed exposure.`,
        positionHistory: paperPositionHistory,
        note: `Synthetic self-funded comparator with ${fmtMoney(shadow.fixedExposure)} fixed exposure. It has no broker account or order authority.`,
      });
    }
    return rows;
  }, [forward, hourly, live, liveMarks, ownership, paperDaily.data, prop, runs, summary]);
  const selected = accounts.find((account) => account.key === selectedKey) ?? null;
  const allTimePnl = selected?.equity !== null && selected?.startingEquity !== null && selected
    ? selected.equity! - selected.startingEquity!
    : null;
  const allTimeReturn = allTimePnl !== null && selected?.startingEquity
    ? (allTimePnl / selected.startingEquity) * 100
    : null;
  const daily = selected?.mode === "Paper" ? paperDaily.data : selected ? dailyFromHistory(selected.history, spy, selected.liveMark, liveMarks.spy) : null;

  return (
    <div className="rounded-lg border p-4" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Strategy accounts</h2>
          <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
            Paper and active shadow strategies in one list. Select any strategy for its isolated performance view.
          </p>
        </div>
        <StrategyComparisonPanel accounts={accounts} />
      </div>
      <div className="mt-3 overflow-hidden rounded-lg border" style={{ borderColor: "var(--border)" }}>
        <div
          className="hidden grid-cols-[minmax(0,1fr)_110px_100px_90px] gap-3 border-b px-3 py-2 text-[11px] font-medium uppercase tracking-wide sm:grid"
          style={{ borderColor: "var(--gridline)", color: "var(--text-muted)" }}
        >
          <span>Strategy</span>
          <span className="text-right">Equity</span>
          <span className="text-right">Return</span>
          <span className="text-right">Sessions</span>
        </div>
        <div className="divide-y" style={{ borderColor: "var(--gridline)" }}>
        {accounts.map((account) => {
          const pnl = account.equity !== null && account.startingEquity !== null ? account.equity - account.startingEquity : null;
          const returnPct = pnl !== null && account.startingEquity ? (pnl / account.startingEquity) * 100 : null;
          return (
            <button
              type="button"
              key={account.key}
              onClick={() => setSelectedKey(account.key)}
              className="grid w-full grid-cols-[1fr_auto] gap-3 px-3 py-3 text-left transition-colors hover:bg-black/5 sm:grid-cols-[minmax(0,1fr)_110px_100px_90px]"
              style={{ borderColor: "var(--gridline)" }}
            >
              <span className="min-w-0"><span className="flex items-center gap-1.5 truncate text-sm font-medium">{account.name}{(account.mode === "Paper" || account.liveMark?.available) && <LiveDot title={account.mode === "Paper" ? "Live from Alpaca" : "Live-quote marked since last settled session"} />}</span><span className="text-xs" style={{ color: "var(--text-muted)" }}>{account.mode} · {account.evidence}</span></span>
              <span className="hidden text-right text-sm tabular-nums sm:block">{fmtMoney(account.equity)}</span>
              <span className="text-right text-sm tabular-nums" style={{ color: changeColor(returnPct) }}>{fmtPct(returnPct)}</span>
              <span className="text-right text-xs tabular-nums sm:text-sm" style={{ color: "var(--text-muted)" }}>{account.sessions}<span className="sm:hidden"> sessions</span></span>
            </button>
          );
        })}
        </div>
      </div>

      <Modal open={Boolean(selected)} onClose={() => setSelectedKey(null)} title={selected?.name ?? "Strategy"} subtitle={selected ? `${selected.mode} · ${selected.evidence}` : undefined} size="xl">
        {selected && (
          <div className="space-y-5">
            <div className="rounded-md border px-3 py-2 text-xs" style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}>{selected.note}</div>
            {selected.mode !== "Paper" && (
              selected.liveMark?.available ? (
                <div className="flex items-center gap-1.5 text-xs" style={{ color: "var(--status-good)" }}>
                  <LiveDot title="Live" /> Live-quote marked as of {new Date(selected.liveMark.asOf).toLocaleTimeString()}
                  {selected.liveMark.missingSymbols.length > 0 && <span style={{ color: "var(--text-muted)" }}> · no quote for {selected.liveMark.missingSymbols.join(", ")}</span>}
                </div>
              ) : (
                <div className="text-xs" style={{ color: "var(--text-muted)" }}>Not live-marked: {selected.liveMark?.reason ?? "unavailable"}</div>
              )
            )}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile label="Equity" value={fmtMoney(selected.equity)} />
              <StatTile label="Cash" value={fmtMoney(selected.cash)} />
              <StatTile label="Buying power" value={fmtMoney(selected.buyingPower)} />
              <StatTile label="Day trades (5-day)" value={selected.dayTrades === null ? "—" : String(selected.dayTrades)} />
            </div>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile label="Unrealized P&L (open positions)" value={signedMoney(selected.unrealizedPnl)} valueColor={changeColor(selected.unrealizedPnl)} />
              <StatTile label="All-time P&L" value={signedMoney(allTimePnl)} valueColor={changeColor(allTimePnl)} />
              <StatTile label="All-time return" value={fmtPct(allTimeReturn)} valueColor={changeColor(allTimeReturn)} />
              <StatTile label="Trading since" value={selected.since ?? "—"} />
            </div>
            <p className="text-xs" style={{ color: "var(--text-muted)" }}>
              {selected.sessions} settled session{selected.sessions === 1 ? "" : "s"}. {selected.mode === "Paper" ? "Broker values and automated execution controls apply only to this paper account." : "Dash values are calculated only from this strategy's append-only synthetic ledger."}
            </p>
            <div className="space-y-2">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-sm font-semibold">
                  {selected.mode === "Paper" ? "Open positions" : "Open simulated positions"}
                </h3>
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                  {formatPositionTimestamp(selected.positionsAsOf)}
                </span>
              </div>
              <p className="text-xs" style={{ color: "var(--text-secondary)" }}>{selected.positionNote}</p>
              {selected.positions.length ? (
                <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--border)" }}>
                  <table className="w-full min-w-[520px] text-sm">
                    <thead>
                      <tr className="border-b" style={{ borderColor: "var(--gridline)", color: "var(--text-muted)" }}>
                        <th className="px-3 py-2 text-left font-medium">Symbol</th>
                        <th className="px-3 py-2 text-left font-medium">Side</th>
                        <th className="px-3 py-2 text-right font-medium">Portfolio weight</th>
                        <th className="px-3 py-2 text-right font-medium">{selected.mode === "Paper" ? "Market value" : "Simulated value"}</th>
                        <th className="px-3 py-2 text-right font-medium">Reference price</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.positions.map((position) => (
                        <tr key={position.symbol} className="border-b last:border-0" style={{ borderColor: "var(--gridline)" }}>
                          <td className="px-3 py-2 font-semibold">{position.symbol}</td>
                          <td className="px-3 py-2 capitalize">{position.side}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{fmtPct(position.weight === null ? null : position.weight * 100)}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{fmtMoney(position.marketValue)}</td>
                          <td className="px-3 py-2 text-right tabular-nums">{fmtMoney(position.currentPrice)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="rounded-md border px-3 py-3 text-xs" style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>
                  No open simulated positions are recorded for this strategy yet.
                </div>
              )}
            </div>
            <details className="rounded-lg border p-3" style={{ borderColor: "var(--border)" }}>
              <summary className="cursor-pointer text-sm font-semibold">Rebalance and position history</summary>
              <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
                Frozen decisions and completed parent rebalances only. A current replay snapshot is labeled separately and is not presented as a historical trade.
              </p>
              {selected.positionHistory.length ? (
                <div className="mt-3 overflow-x-auto">
                  <table className="w-full min-w-[820px] text-xs">
                    <thead><tr className="border-b" style={{ borderColor: "var(--gridline)", color: "var(--text-muted)" }}><th className="px-3 py-2 text-left font-medium">Date</th><th className="px-3 py-2 text-left font-medium">Record</th><th className="px-3 py-2 text-left font-medium">Entered</th><th className="px-3 py-2 text-left font-medium">Exited</th><th className="px-3 py-2 text-left font-medium">Holdings after record</th></tr></thead>
                    <tbody>
                      {selected.positionHistory.map((event, index) => {
                        const prior = selected.positionHistory[index - 1]?.holdings ?? {};
                        const entered = Object.keys(event.holdings).filter((symbol) => !(symbol in prior));
                        const exited = Object.keys(prior).filter((symbol) => !(symbol in event.holdings));
                        const source = event.source === "current_snapshot" ? "Current replay snapshot" : event.source === "frozen_decision" ? "Frozen shadow decision" : event.source === "completed_parent_rebalance" ? "Completed parent rebalance" : event.source === "blind_pre_activation_backfill" ? "Blind pre-activation backfill" : event.source === "prospective_shadow" ? "Prospective hourly decision" : "Completed paper rebalance";
                        return <tr key={`${event.date}-${index}`} className="border-b last:border-0 align-top" style={{ borderColor: "var(--gridline)" }}><td className="px-3 py-2 whitespace-nowrap">{event.date}</td><td className="px-3 py-2 whitespace-nowrap">{source}</td><td className="px-3 py-2" style={{ color: entered.length ? "var(--status-good)" : "var(--text-muted)" }}>{index === 0 ? `Initial: ${entered.join(", ") || "—"}` : entered.join(", ") || "—"}</td><td className="px-3 py-2" style={{ color: exited.length ? "var(--status-critical)" : "var(--text-muted)" }}>{exited.join(", ") || "—"}</td><td className="px-3 py-2">{Object.entries(event.holdings).sort((a, b) => Math.abs(b[1]) - Math.abs(a[1])).map(([symbol, weight]) => `${symbol} ${(weight * 100).toFixed(1)}%`).join(" · ") || "—"}</td></tr>;
                      })}
                    </tbody>
                  </table>
                </div>
              ) : <p className="mt-3 text-xs" style={{ color: "var(--text-muted)" }}>No immutable rebalance or position snapshots have been recorded yet.</p>}
            </details>
            {daily ? <DailyPerformancePanel suppliedData={daily} accountLabel={selected.mode === "Paper" ? "Paper account" : selected.name} scopeNote={selected.mode === "Paper" ? "Account-level and attributable to this strategy while it is the sole execution owner." : "Strategy-level synthetic ledger; no Alpaca account values are included."} /> : <p className="text-xs" style={{ color: "var(--text-muted)" }}>Loading daily performance…</p>}
          </div>
        )}
      </Modal>
    </div>
  );
}
