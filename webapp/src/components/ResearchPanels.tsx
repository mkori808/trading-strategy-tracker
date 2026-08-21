import { useEffect, useRef, useState } from "react";
import { api, type DigestPreview, type InsiderPurchase, type SymbolMeta } from "../api";
import { useInsider, useMovers } from "../dataHooks";
import { fmtCompactMoney, fmtPct, fmtRelative } from "../format";

const INSIDER_POLL_MS = 3000;

function MoversTable({ title, rows }: { title: string; rows: SymbolMeta[] }) {
  return (
    <div>
      <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        {title}
      </div>
      <div
        className="overflow-x-auto rounded-lg border"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <table className="w-full min-w-[280px] border-collapse text-sm">
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} style={{ borderBottom: "1px solid var(--gridline)" }}>
                <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>
                  {r.symbol}
                </td>
                <td className="px-4 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                  {r.lastClose === null ? "—" : `$${r.lastClose.toFixed(2)}`}
                </td>
                <td
                  className="px-4 py-2 text-right tabular-nums"
                  style={{ color: (r.changePct ?? 0) >= 0 ? "var(--status-good)" : "var(--status-critical)" }}
                >
                  {fmtPct(r.changePct)}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td className="px-4 py-3 text-sm" style={{ color: "var(--text-muted)" }}>
                  No data.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function InsiderTable({ rows }: { rows: InsiderPurchase[] }) {
  return (
    <div
      className="overflow-x-auto rounded-lg border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <table className="w-full min-w-[720px] border-collapse text-sm">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
            {["Symbol", "Filer", "Shares", "Price", "Value", "Filed"].map((h) => (
              <th
                key={h}
                className="px-4 py-2 text-left font-medium whitespace-nowrap"
                style={{ color: "var(--text-muted)" }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.issuerTicker}-${r.filedAt}-${i}`} style={{ borderBottom: "1px solid var(--gridline)" }}>
              <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>
                {r.issuerTicker}
              </td>
              <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>
                {r.filerName}
              </td>
              <td className="px-4 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                {r.sharesTransacted.toLocaleString()}
              </td>
              <td className="px-4 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                ${r.pricePerShare.toFixed(2)}
              </td>
              <td className="px-4 py-2 tabular-nums font-medium" style={{ color: "var(--status-good)" }}>
                {fmtCompactMoney(r.transactionValue)}
              </td>
              <td className="px-4 py-2" style={{ color: "var(--text-muted)" }}>
                {r.signalDate}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="px-4 py-3 text-sm" colSpan={6} style={{ color: "var(--text-muted)" }}>
                No qualifying open-market purchases cached yet — click "Refresh insider data".
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/** Gainers/losers/streaks. Split out of the former single Movers tab so the
 * dashboard can open this, insider buying, and the digest as three separate
 * popups -- they answer three different questions and were only adjacent
 * because they shared a tab. */
export function MoversPanel() {
  const movers = useMovers();

  return (
    <div className="space-y-4">
      {movers.error && (
        <div
          className="rounded-lg border px-4 py-3 text-sm"
          style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
        >
          Failed to load movers: {movers.error}
        </div>
      )}
      {!movers.data && !movers.error && (
        <div className="text-sm" style={{ color: "var(--text-muted)" }}>
          Loading movers…
        </div>
      )}
      {movers.data && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <MoversTable title="Top gainers" rows={movers.data.gainers} />
          <MoversTable title="Top losers" rows={movers.data.losers} />
        </div>
      )}
      {movers.data && movers.data.streaks.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
            Momentum streaks (2+ consecutive days)
          </div>
          <div className="flex flex-wrap gap-2">
            {movers.data.streaks.map((s) => (
              <span
                key={s.symbol}
                className="rounded-md border px-2.5 py-1 text-xs"
                style={{
                  borderColor: "var(--border)",
                  color: s.direction === "up" ? "var(--status-good)" : "var(--status-critical)",
                }}
              >
                {s.symbol} {s.direction === "up" ? "▲" : "▼"} {s.days}d
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Refresh button for the movers popup header. */
export function MoversRefresh() {
  const movers = useMovers(false);
  return (
    <button
      type="button"
      onClick={() => void movers.refresh()}
      disabled={movers.loading}
      className="rounded-md border px-3 py-1.5 text-xs font-medium disabled:opacity-50"
      style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
    >
      {movers.loading ? "Refreshing…" : "Refresh"}
    </button>
  );
}

export function InsiderPanel() {
  const insider = useInsider();
  // Local override for the window between clicking Refresh and the first
  // poll landing -- the cached response still says running:false.
  const [refreshing, setRefreshing] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const running = refreshing || Boolean(insider.data?.running);

  const refreshInsider = async () => {
    setRefreshing(true);
    await api.insiderRefresh();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const status = await api.insiderStatus();
      if (!status.running) {
        if (pollRef.current) clearInterval(pollRef.current);
        setRefreshing(false);
        void insider.refresh();
      }
    }, INSIDER_POLL_MS);
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="max-w-2xl text-xs" style={{ color: "var(--text-muted)" }}>
          Open-market (code "P") purchases from SEC EDGAR Form 4 filings, largest transaction
          first. Real, structured regulatory data — not a claim about what happens to the stock
          next.
        </p>
        <div className="flex shrink-0 items-center gap-3">
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>
            Last refreshed: {fmtRelative(insider.data?.lastCompletedAt ?? null)}
          </span>
          <button
            type="button"
            onClick={refreshInsider}
            disabled={running}
            className="rounded-md px-3 py-1.5 text-xs font-medium text-white transition-opacity disabled:opacity-50"
            style={{ background: "var(--series-1)" }}
          >
            {running ? "Refreshing…" : "Refresh insider data"}
          </button>
        </div>
      </div>
      <InsiderTable rows={insider.data?.rows ?? []} />
    </div>
  );
}

/** Preview only -- nothing is scheduled or emailed, by deliberate scope
 * decision (see CLAUDE.md's Research platform section). The button is the
 * only trigger because generating it scans every tracked symbol (~1 min);
 * it must never fire just because a popup opened. */
export function DigestPanel() {
  const [digest, setDigest] = useState<DigestPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    setLoading(true);
    setError(null);
    try {
      setDigest(await api.digestPreview());
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="max-w-2xl text-xs" style={{ color: "var(--text-muted)" }}>
          Preview only — nothing is scheduled or emailed. This composes the same regime, movers
          and insider data into the shape a future daily digest would send.
        </p>
        <button
          type="button"
          onClick={generate}
          disabled={loading}
          className="shrink-0 rounded-md border px-3 py-1.5 text-xs font-medium disabled:opacity-50"
          style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
        >
          {loading ? "Generating… (~1 min)" : "Generate today's digest preview"}
        </button>
      </div>
      {error && (
        <div
          className="rounded-lg border px-4 py-3 text-sm"
          style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
        >
          Failed to generate digest: {error}
        </div>
      )}
      {digest && (
        <pre
          className="overflow-x-auto rounded-lg border p-4 text-xs whitespace-pre-wrap"
          style={{
            borderColor: "var(--border)",
            background: "var(--surface-1)",
            color: "var(--text-secondary)",
          }}
        >
          {digest.text}
        </pre>
      )}
      {!digest && !loading && !error && (
        <div className="text-xs" style={{ color: "var(--text-muted)" }}>
          Nothing generated yet.
        </div>
      )}
    </div>
  );
}
