import {
  api,
  type KillSwitchStatus,
  type LiveAccountResponse,
  type MarketResponse,
} from "../api";
import { useResource } from "../useResource";
import { KEYS } from "../resourceKeys";
import { changeColor, fmtMoney, fmtPct, REGIME_COLOR } from "../format";

function Stat({
  label,
  value,
  color,
  title,
}: {
  label: string;
  value: string;
  color?: string;
  title?: string;
}) {
  return (
    <div className="min-w-0" title={title}>
      <div
        className="text-[10px] font-semibold tracking-wide"
        style={{ color: "var(--text-muted)" }}
      >
        {label}
      </div>
      <div
        className="truncate text-sm font-semibold tabular-nums"
        style={{ color: color ?? "var(--text-primary)" }}
      >
        {value}
      </div>
    </div>
  );
}

/** The one row that is true everywhere and never behind a click: market
 * open/closed, the benchmark, the regime gating long entries, breadth, and
 * the paper account's own state including whether automation is armed.
 *
 * The kill switch's state is here on purpose. It is the app's emergency
 * stop, and "is it currently on?" must be answerable without opening
 * anything -- the guardrails section of CLAUDE.md requires the switch to be
 * reachable even when the UI is misbehaving, and a state you have to hunt
 * for fails the same test for a different reason. The control that flips it
 * still lives in the trading popup; only the readout is promoted. */
export function StatusStrip({
  marketData,
  marketLoading,
}: {
  marketData: MarketResponse | null;
  marketLoading: boolean;
}) {
  const account = useResource<LiveAccountResponse>(KEYS.liveAccount, () => api.liveAccount());
  const kill = useResource<KillSwitchStatus>(KEYS.killSwitch, () => api.killSwitchStatus());

  const clock = account.data?.clock ?? null;
  const acct = account.data?.account;
  const spy = marketData?.sectorPerformance.find((r) => r.symbol === "SPY");
  const regime = marketData?.regime.current ?? null;
  const breadth = marketData?.marketSignals.score ?? null;

  // Alpaca's own prior-session-close equity is the same baseline the
  // daily-loss circuit breaker compares against -- deriving day P&L from
  // anything else here would show a number the breaker disagrees with.
  const dayPnl =
    acct?.equity != null && acct?.lastEquity != null ? acct.equity - acct.lastEquity : null;
  const dayPnlPct =
    dayPnl !== null && acct?.lastEquity ? (dayPnl / acct.lastEquity) * 100 : null;

  return (
    <div
      className="mb-6 flex flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border px-4 py-3"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div className="flex items-center gap-2">
        <span
          aria-hidden="true"
          className="inline-block h-2 w-2 rounded-full"
          style={{ background: clock?.isOpen ? "var(--status-good)" : "var(--text-muted)" }}
        />
        <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
          {clock === null ? "Market status unknown" : clock.isOpen ? "Markets open" : "Markets closed"}
        </span>
      </div>

      <div className="h-8 w-px" style={{ background: "var(--gridline)" }} aria-hidden="true" />

      <Stat
        label="SPY"
        value={marketLoading && !spy ? "…" : fmtPct(spy?.changePct ?? null)}
        color={changeColor(spy?.changePct ?? null)}
      />
      <Stat
        label="REGIME"
        value={marketLoading && !regime ? "…" : (regime ?? "—")}
        color={regime ? REGIME_COLOR[regime] : "var(--text-muted)"}
        title="SPY's 50/200-day SMA state. New long entries are gated to Bullish in the pre-trade filter layer."
      />
      <Stat
        label="BREADTH"
        value={breadth === null ? (marketLoading ? "…" : "—") : `${breadth.toFixed(0)}/100`}
        title="Composite of % above 50/200-day SMA, net new 20-day highs vs lows, and SPY's regime. Not a buy/sell signal."
      />

      <div className="h-8 w-px" style={{ background: "var(--gridline)" }} aria-hidden="true" />

      <Stat
        label="PAPER EQUITY"
        value={acct?.available ? fmtMoney(acct.equity) : "—"}
        title={acct?.available ? undefined : acct?.reason}
      />
      <Stat
        label="DAY P&L"
        value={
          dayPnl === null
            ? "—"
            : `${dayPnl >= 0 ? "+" : "−"}${fmtMoney(Math.abs(dayPnl))}${
                dayPnlPct === null ? "" : ` (${fmtPct(dayPnlPct)})`
              }`
        }
        color={changeColor(dayPnl)}
      />

      {kill.data?.active && (
        <span
          className="rounded-full px-2.5 py-1 text-xs font-semibold"
          style={{ color: "var(--status-critical)", background: "var(--status-critical-bg)" }}
        >
          KILL SWITCH ACTIVE — no new orders
        </span>
      )}

      <span className="ml-auto text-xs" style={{ color: "var(--text-muted)" }}>
        Alpaca paper — not real money
      </span>
    </div>
  );
}
