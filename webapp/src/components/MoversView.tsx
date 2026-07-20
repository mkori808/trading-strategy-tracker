import { useEffect, useRef, useState } from "react";
import {
  api,
  type DigestPreview,
  type InsiderPurchase,
  type MoversResponse,
  type SymbolMeta,
} from "../api";

const INSIDER_POLL_MS = 3000;

function fmtChange(v: number | null): string {
  if (v === null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtMoney(v: number): string {
  // Threshold checked against the ROUNDED value, not the raw one -- a raw
  // 999,600 is < 1e6 but rounds to "1000K" at 0 decimals, which should read
  // as "$1.0M" instead of a value that looks like it skipped a unit.
  if (v >= 999_500) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function fmtRelative(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  const mins = Math.round(ms / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  return `${hours}h ago`;
}

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
                  {fmtChange(r.changePct)}
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
                {fmtMoney(r.transactionValue)}
              </td>
              <td className="px-4 py-2" style={{ color: "var(--text-muted)" }}>
                {r.signalDate}
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="px-4 py-3 text-sm" colSpan={6} style={{ color: "var(--text-muted)" }}>
                No qualifying open-market purchases cached yet -- click "Refresh insider data".
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export function MoversView() {
  const [movers, setMovers] = useState<MoversResponse | null>(null);
  const [moversError, setMoversError] = useState<string | null>(null);
  const [insiderRows, setInsiderRows] = useState<InsiderPurchase[]>([]);
  const [insiderMeta, setInsiderMeta] = useState<{ running: boolean; lastCompletedAt: string | null } | null>(null);
  const [digest, setDigest] = useState<DigestPreview | null>(null);
  const [digestLoading, setDigestLoading] = useState(false);
  const [digestError, setDigestError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadMovers = () => {
    setMoversError(null);
    api.movers().then(setMovers).catch((e) => setMoversError(String(e)));
  };

  const loadInsider = () => {
    api.insiderRecent().then((res) => {
      setInsiderRows(res.rows);
      setInsiderMeta({ running: res.running, lastCompletedAt: res.lastCompletedAt });
    });
  };

  useEffect(() => {
    loadMovers();
    loadInsider();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const refreshInsider = async () => {
    await api.insiderRefresh();
    loadInsider();
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const status = await api.insiderStatus();
      if (!status.running) {
        if (pollRef.current) clearInterval(pollRef.current);
        loadInsider();
      } else {
        setInsiderMeta({ running: true, lastCompletedAt: status.lastCompletedAt });
      }
    }, INSIDER_POLL_MS);
  };

  const generateDigest = async () => {
    setDigestLoading(true);
    setDigestError(null);
    try {
      setDigest(await api.digestPreview());
    } catch (e) {
      setDigestError(String(e));
    } finally {
      setDigestLoading(false);
    }
  };

  return (
    <div className="space-y-8">
      <section className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            Trending movers
          </h2>
          <button
            type="button"
            onClick={loadMovers}
            className="rounded-md border px-3 py-1.5 text-xs font-medium"
            style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
          >
            Refresh
          </button>
        </div>
        {moversError && (
          <div
            className="rounded-lg border px-4 py-3 text-sm"
            style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
          >
            Failed to load movers: {moversError}
          </div>
        )}
        {!movers && !moversError && (
          <div className="text-sm" style={{ color: "var(--text-muted)" }}>
            Loading movers…
          </div>
        )}
        {movers && (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <MoversTable title="Top gainers" rows={movers.gainers} />
            <MoversTable title="Top losers" rows={movers.losers} />
          </div>
        )}
        {movers && movers.streaks.length > 0 && (
          <div>
            <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
              Momentum streaks (2+ consecutive days)
            </div>
            <div className="flex flex-wrap gap-2">
              {movers.streaks.map((s) => (
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
      </section>

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            Insider buying
          </h2>
          <div className="flex items-center gap-3">
            <span className="text-xs" style={{ color: "var(--text-muted)" }}>
              Last refreshed: {fmtRelative(insiderMeta?.lastCompletedAt ?? null)}
            </span>
            <button
              type="button"
              onClick={refreshInsider}
              disabled={insiderMeta?.running}
              className="rounded-md px-3 py-1.5 text-xs font-medium text-white transition-opacity disabled:opacity-50"
              style={{ background: "var(--series-1)" }}
            >
              {insiderMeta?.running ? "Refreshing…" : "Refresh insider data"}
            </button>
          </div>
        </div>
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Open-market (code "P") purchases from SEC EDGAR Form 4 filings, largest transaction first.
          Real, structured regulatory data -- not a claim about what happens to the stock next.
        </p>
        <InsiderTable rows={insiderRows} />
      </section>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
            Daily digest preview
          </h2>
          <button
            type="button"
            onClick={generateDigest}
            disabled={digestLoading}
            className="rounded-md border px-3 py-1.5 text-xs font-medium disabled:opacity-50"
            style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
          >
            {digestLoading ? "Generating… (~1 min, scans all tracked symbols)" : "Generate today's digest preview"}
          </button>
        </div>
        <p className="text-xs" style={{ color: "var(--text-muted)" }}>
          Preview only -- nothing is scheduled or emailed. This composes the same regime/movers/insider
          data above into the shape a future daily digest would send.
        </p>
        {digestError && (
          <div
            className="rounded-lg border px-4 py-3 text-sm"
            style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
          >
            Failed to generate digest: {digestError}
          </div>
        )}
        {digest && (
          <pre
            className="overflow-x-auto rounded-lg border p-4 text-xs whitespace-pre-wrap"
            style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-secondary)" }}
          >
            {digest.text}
          </pre>
        )}
      </section>
    </div>
  );
}
