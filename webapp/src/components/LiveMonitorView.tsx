import { useEffect, useState } from "react";
import { api, type LiveAccountResponse, type SignalAlert } from "../api";
import { StatTile } from "./StatTile";

const POLL_MS = 30_000;

function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function LiveMonitorView() {
  const [account, setAccount] = useState<LiveAccountResponse | null>(null);
  const [signals, setSignals] = useState<SignalAlert[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      Promise.all([api.liveAccount(), api.liveSignals(100)])
        .then(([acct, sig]) => {
          if (cancelled) return;
          setAccount(acct);
          setSignals(sig);
          setLoadError(null);
        })
        .catch((e) => {
          if (!cancelled) setLoadError(String(e));
        });
    };
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const runScanNow = async () => {
    setScanning(true);
    try {
      await api.triggerScan();
      const [acct, sig] = await Promise.all([api.liveAccount(), api.liveSignals(100)]);
      setAccount(acct);
      setSignals(sig);
    } catch (e) {
      setLoadError(String(e));
    } finally {
      setScanning(false);
    }
  };

  if (loadError && !account) {
    return (
      <div
        className="rounded-lg border px-4 py-3 text-sm"
        style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
      >
        Failed to load live monitor: {loadError}
      </div>
    );
  }

  if (!account) {
    return (
      <div className="text-sm" style={{ color: "var(--text-muted)" }}>
        Loading live monitor…
      </div>
    );
  }

  const { account: acct, positions, orders, clock } = account;

  return (
    <div className="space-y-6">
      <div
        className="rounded-lg border px-4 py-3 text-sm"
        style={{ borderColor: "var(--status-warning)", background: "var(--status-warning-bg)", color: "var(--text-primary)" }}
      >
        <strong>Paper trading only.</strong> Signals below are detected from delayed (~15min,
        free-tier) data and logged for monitoring — this app never places an order
        automatically. Account <code>{acct.accountNumber ?? "—"}</code> is Alpaca's paper
        environment, not real money.
      </div>

      {!acct.available && (
        <div
          className="rounded-lg border px-4 py-3 text-sm"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-secondary)" }}
        >
          Alpaca isn't configured: {acct.reason} Add paper keys to <code>.env</code> and restart
          the API to enable live monitoring.
        </div>
      )}

      {acct.available && (
        <>
          <div className="flex items-center justify-between">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                Paper account
              </h2>
              <span
                className="rounded-full px-2.5 py-1 text-xs font-medium"
                style={{
                  color: clock.isOpen ? "var(--status-good)" : "var(--text-muted)",
                  background: clock.isOpen ? "var(--status-good-bg)" : "var(--gridline)",
                }}
              >
                {clock.isOpen ? "Market open" : `Market closed — next open ${fmtTime(clock.nextOpen ?? null)}`}
              </span>
            </div>
            <button
              type="button"
              onClick={runScanNow}
              disabled={scanning}
              className="rounded-md px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
              style={{ background: "var(--series-1)" }}
            >
              {scanning ? "Scanning…" : "Scan now"}
            </button>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="Equity" value={fmtMoney(acct.equity)} />
            <StatTile label="Cash" value={fmtMoney(acct.cash)} />
            <StatTile label="Buying power" value={fmtMoney(acct.buyingPower)} />
            <StatTile
              label="Day trades (5-day)"
              value={acct.daytradeCount === null || acct.daytradeCount === undefined ? "—" : String(acct.daytradeCount)}
            />
          </div>

          <div>
            <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
              Open positions ({positions.length})
            </div>
            <div
              className="overflow-x-auto rounded-lg border"
              style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
            >
              <table className="w-full min-w-[640px] border-collapse text-sm">
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
                    {["Symbol", "Side", "Qty", "Avg entry", "Current", "Unrealized P/L"].map((h) => (
                      <th key={h} className="px-4 py-2 text-left font-medium whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p) => (
                    <tr key={p.symbol} style={{ borderBottom: "1px solid var(--gridline)" }}>
                      <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>{p.symbol}</td>
                      <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>{p.side}</td>
                      <td className="px-4 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>{p.qty}</td>
                      <td className="px-4 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmtMoney(p.avgEntryPrice)}</td>
                      <td className="px-4 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmtMoney(p.currentPrice)}</td>
                      <td
                        className="px-4 py-2 tabular-nums"
                        style={{ color: (p.unrealizedPlPct ?? 0) >= 0 ? "var(--status-good)" : "var(--status-critical)" }}
                      >
                        {fmtPct(p.unrealizedPlPct)}
                      </td>
                    </tr>
                  ))}
                  {positions.length === 0 && (
                    <tr>
                      <td className="px-4 py-3 text-sm" colSpan={6} style={{ color: "var(--text-muted)" }}>
                        No open positions.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          <div>
            <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
              Recent orders ({orders.length})
            </div>
            <div
              className="overflow-x-auto rounded-lg border"
              style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
            >
              <table className="w-full min-w-[720px] border-collapse text-sm">
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
                    {["Symbol", "Side", "Qty", "Type", "Status", "Submitted", "Filled avg"].map((h) => (
                      <th key={h} className="px-4 py-2 text-left font-medium whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {orders.map((o) => (
                    <tr key={o.id} style={{ borderBottom: "1px solid var(--gridline)" }}>
                      <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>{o.symbol}</td>
                      <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>{o.side}</td>
                      <td className="px-4 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>{o.qty ?? "—"}</td>
                      <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>{o.type}</td>
                      <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>{o.status}</td>
                      <td className="px-4 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>{fmtTime(o.submittedAt)}</td>
                      <td className="px-4 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmtMoney(o.filledAvgPrice)}</td>
                    </tr>
                  ))}
                  {orders.length === 0 && (
                    <tr>
                      <td className="px-4 py-3 text-sm" colSpan={7} style={{ color: "var(--text-muted)" }}>
                        No orders yet — this app doesn't place any automatically.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      <div>
        <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          Live signal alerts ({signals.length})
        </div>
        <div
          className="overflow-x-auto rounded-lg border"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
        >
          <table className="w-full min-w-[820px] border-collapse text-sm">
            <thead>
              <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
                {["Bar time", "Strategy", "Symbol", "Direction", "Price", "Regime", "Trend template"].map((h) => (
                  <th key={h} className="px-4 py-2 text-left font-medium whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {signals.map((s, i) => (
                <tr key={i} style={{ borderBottom: "1px solid var(--gridline)" }}>
                  <td className="px-4 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>{fmtTime(s.barTimestamp)}</td>
                  <td className="px-4 py-2" style={{ color: "var(--text-primary)" }}>{s.strategyName}</td>
                  <td className="px-4 py-2 font-medium" style={{ color: "var(--text-primary)" }}>{s.symbol}</td>
                  <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>{s.direction}</td>
                  <td className="px-4 py-2 tabular-nums" style={{ color: "var(--text-secondary)" }}>{fmtMoney(s.price)}</td>
                  <td className="px-4 py-2" style={{ color: "var(--text-secondary)" }}>{s.regimeState ?? "—"}</td>
                  <td
                    className="px-4 py-2"
                    style={{
                      color:
                        s.trendTemplatePass === null
                          ? "var(--text-muted)"
                          : s.trendTemplatePass
                            ? "var(--status-good)"
                            : "var(--status-critical)",
                    }}
                  >
                    {s.trendTemplatePass === null ? "—" : s.trendTemplatePass ? "Pass" : "Fail"}
                  </td>
                </tr>
              ))}
              {signals.length === 0 && (
                <tr>
                  <td className="px-4 py-3 text-sm" colSpan={7} style={{ color: "var(--text-muted)" }}>
                    No signals logged yet. The scanner runs automatically every 5 minutes during
                    market hours, or use "Scan now" above.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
