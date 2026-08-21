import { useCallback, useState } from "react";
import {
  api,
  type ExecutionStrategyConfig,
  type LiveAccountResponse,
  type MarketResponse,
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
  | "digest";

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
  // Stable identity: Modal guards against a changing handler internally, but
  // there is no reason to hand it a new function on every poll-driven render.
  const close = useCallback(() => setPopup(null), []);

  const account = useResource<LiveAccountResponse>(KEYS.liveAccount, () => api.liveAccount());
  const execConfig = useResource<ExecutionStrategyConfig[]>(KEYS.executionConfig, () =>
    api.executionConfig(),
  );
  const movers = useMovers();
  const insider = useInsider();
  const screener = useScreener();
  const symbols = useSymbols();

  const acct = account.data?.account;
  const positions = account.data?.positions ?? [];
  const openOrders = account.data?.orders ?? [];
  const automated = (execConfig.data ?? []).filter((c) => c.enabled);

  const unrealized = positions.reduce((sum, p) => sum + (p.unrealizedPl ?? 0), 0);

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
      <StatusStrip marketData={marketData} marketLoading={marketLoading} />

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card
          title="Market state"
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
          title="Paper account"
          meta={automated.length > 0 ? `${automated.length} automated` : "automation off"}
          onOpen={() => setPopup("trading")}
          loading={account.loading && !account.data}
          error={account.error}
        >
          {acct?.available ? (
            <>
              <CardHeadline
                value={fmtMoney(acct.equity)}
                caption={`${positions.length} position${positions.length === 1 ? "" : "s"} · ${openOrders.length} open order${openOrders.length === 1 ? "" : "s"}`}
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
              <CardRow label="Buying power" value={fmtMoney(acct.buyingPower)} />
              <CardRow
                label="Automation"
                value={automated.length > 0 ? automated.map((c) => c.strategyName).join(", ") : "Off"}
                valueColor={automated.length > 0 ? "var(--status-good)" : "var(--text-muted)"}
              />
            </>
          ) : (
            <CardEmpty>
              Alpaca isn't configured{acct?.reason ? `: ${acct.reason}` : "."} Open to see how.
            </CardEmpty>
          )}
        </Card>

        <Card
          title="Movers"
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

        <Card title="Daily digest" onOpen={() => setPopup("digest")}>
          <CardEmpty>
            Compose today's regime, movers and insider buys into one summary. Preview only —
            nothing is scheduled or emailed.
          </CardEmpty>
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
    </>
  );
}
