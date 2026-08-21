import { useState } from "react";
import { api, type DailyPerformance, type DailyPerformanceRow } from "../api";
import { useResource } from "../useResource";
import { KEYS } from "../resourceKeys";
import { changeColor, fmtMoney, fmtPct } from "../format";
import { StatTile } from "./StatTile";

/** How many settled days the table shows before the "Show all" toggle. */
const COLLAPSED_ROWS = 10;

function fmtDay(iso: string): string {
  // Parsed as UTC midnight deliberately: the API already resolved these to
  // NY calendar dates, so `new Date("2026-08-20")` must not be re-shifted by
  // the viewer's own zone into the 19th.
  const d = new Date(`${iso}T00:00:00Z`);
  return d.toLocaleDateString(undefined, {
    timeZone: "UTC",
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

function fmtSigned(v: number | null): string {
  if (v === null) return "—";
  return `${v >= 0 ? "+" : "−"}${fmtMoney(Math.abs(v))}`;
}

/** A proportional bar next to the day's percent, scaled against the biggest
 * absolute move in the window so the worst day fills the track. Pure CSS --
 * a chart library for one column would be more machinery than the column is
 * worth, and this stays readable at table density.
 *
 * The TRACK is a fixed width and the fill varies inside it. A bar that
 * itself resized pushed each row's percentage to a different horizontal
 * position, which defeats the point of tabular numbers -- the eye can no
 * longer scan the column. */
function MoveBar({ pct, scale }: { pct: number | null; scale: number }) {
  const filled = pct === null || scale <= 0 ? 0 : Math.min(100, (Math.abs(pct) / scale) * 100);
  return (
    <span
      aria-hidden="true"
      className="relative inline-block h-1.5 w-16 shrink-0 overflow-hidden rounded-full"
      style={{ background: "var(--gridline)" }}
    >
      <span
        className="absolute inset-y-0 left-0 rounded-full"
        style={{
          width: `${filled}%`,
          background:
            pct === null
              ? "transparent"
              : pct >= 0
                ? "var(--status-good)"
                : "var(--status-critical)",
        }}
      />
    </span>
  );
}

function DayRow({
  row,
  scale,
  benchmarkSymbol,
  today = false,
}: {
  row: DailyPerformanceRow;
  scale: number;
  benchmarkSymbol: string;
  today?: boolean;
}) {
  return (
    <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
      <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-primary)" }}>
        {fmtDay(row.date)}
        {today && (
          <span
            className="ml-2 rounded-full px-2 py-0.5 text-[10px] font-medium"
            style={{ color: "var(--status-warning)", background: "var(--status-warning-bg)" }}
          >
            in progress
          </span>
        )}
      </td>
      <td
        className="px-3 py-2 text-right tabular-nums whitespace-nowrap"
        style={{ color: changeColor(row.profitLoss) }}
      >
        {fmtSigned(row.profitLoss)}
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        <span className="flex items-center justify-end gap-2">
          <span className="tabular-nums" style={{ color: changeColor(row.profitLossPct) }}>
            {fmtPct(row.profitLossPct)}
          </span>
          <MoveBar pct={row.profitLossPct} scale={scale} />
        </span>
      </td>
      <td
        className="px-3 py-2 text-right tabular-nums whitespace-nowrap"
        style={{ color: changeColor(row.benchmarkPct) }}
        title={
          row.benchmarkPct === null
            ? `${benchmarkSymbol}'s move for this session isn't available yet`
            : today
              ? `${benchmarkSymbol} so far today, from a delayed quote — intraday-to-now, matching the in-progress account figure beside it`
              : `${benchmarkSymbol}'s own return for this settled session`
        }
      >
        {fmtPct(row.benchmarkPct)}
      </td>
      <td
        className="px-3 py-2 text-right tabular-nums whitespace-nowrap"
        style={{ color: "var(--text-secondary)" }}
      >
        {fmtMoney(row.equity)}
      </td>
    </tr>
  );
}

/** Day-by-day performance of the paper account since automated trading
 * started, alongside the same session's benchmark move.
 *
 * Sits next to the all-time tiles because a cumulative number, on its own,
 * hides every day inside it -- "up 1.2% since inception" reads the same
 * whether that was a steady drift or one good day inside four bad ones.
 *
 * Today is rendered as its own row above the settled ones and badged "in
 * progress": it comes from the live account (equity vs. Alpaca's
 * prior-session close), not from a broker-confirmed daily mark, which does
 * not exist until the session closes. It is deliberately not summed into
 * the best/worst/up-days statistics below, which describe settled sessions
 * only. */
export function DailyPerformancePanel() {
  const { data, error, loading } = useResource<DailyPerformance>(KEYS.executionDaily, () =>
    api.executionDaily(),
  );
  const [showAll, setShowAll] = useState(false);

  if (error) {
    return (
      <p className="text-xs" style={{ color: "var(--status-critical)" }}>
        Couldn't load daily performance: {error}
      </p>
    );
  }
  if (!data) {
    return (
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
        {loading ? "Loading daily performance…" : "—"}
      </p>
    );
  }
  if (!data.available) {
    return (
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
        Daily performance unavailable: {data.reason}
      </p>
    );
  }
  if (data.rows.length === 0 && !data.today) {
    return (
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
        No trading days yet — the daily breakdown starts after the first rebalance trades.
      </p>
    );
  }

  // Newest first: the question this panel answers is "what happened today,
  // and lately", not "walk me through the history from the start".
  const settled = [...data.rows].reverse();
  const visible = showAll ? settled : settled.slice(0, COLLAPSED_ROWS);

  const pcts = settled.map((r) => r.profitLossPct).filter((v): v is number => v !== null);
  const scale = Math.max(...pcts.map(Math.abs), Math.abs(data.today?.profitLossPct ?? 0), 0.01);
  const upDays = pcts.filter((v) => v > 0).length;
  const downDays = pcts.filter((v) => v < 0).length;
  const best = pcts.length ? Math.max(...pcts) : null;
  const worst = pcts.length ? Math.min(...pcts) : null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Day by day
        </h3>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          Since {data.startDate ?? "inception"} · {settled.length} settled session
          {settled.length === 1 ? "" : "s"}
        </span>
      </div>

      {data.today && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <StatTile
            label="Today (in progress)"
            value={fmtSigned(data.today.profitLoss)}
            valueColor={changeColor(data.today.profitLoss)}
          />
          <StatTile
            label="Today %"
            value={fmtPct(data.today.profitLossPct)}
            valueColor={changeColor(data.today.profitLossPct)}
          />
          <StatTile
            label="Best settled day"
            value={fmtPct(best)}
            valueColor={changeColor(best)}
          />
          <StatTile
            label="Worst settled day"
            value={fmtPct(worst)}
            valueColor={changeColor(worst)}
          />
        </div>
      )}

      {pcts.length > 0 && (
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          {upDays} up / {downDays} down across settled sessions. Account-level, like the all-time
          figures above — accurate as long as only automated strategies trade in this account. The{" "}
          {data.benchmarkSymbol} column is that session's own return for context, not a
          risk-adjusted comparison; on the in-progress row it is intraday-to-now from a delayed
          quote, so it spans the same part-day the account figure does.
        </p>
      )}

      <div
        className="overflow-x-auto rounded-lg border"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <table className="w-full min-w-[520px] border-collapse text-sm">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
              {["Session", "P&L", "Change", data.benchmarkSymbol, "Closing equity"].map(
                (h, i) => (
                  <th
                    key={h}
                    className={`px-3 py-2 font-medium whitespace-nowrap ${i === 0 ? "text-left" : "text-right"}`}
                    style={{ color: "var(--text-muted)" }}
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {data.today && (
              <DayRow
                row={data.today}
                scale={scale}
                benchmarkSymbol={data.benchmarkSymbol}
                today
              />
            )}
            {visible.map((row) => (
              <DayRow
                key={row.date}
                row={row}
                scale={scale}
                benchmarkSymbol={data.benchmarkSymbol}
              />
            ))}
          </tbody>
        </table>
      </div>

      {settled.length > COLLAPSED_ROWS && (
        <button
          type="button"
          onClick={() => setShowAll((v) => !v)}
          className="text-xs font-medium"
          style={{ color: "var(--series-1)" }}
        >
          {showAll ? "Show fewer" : `Show all ${settled.length} sessions`}
        </button>
      )}
    </div>
  );
}
