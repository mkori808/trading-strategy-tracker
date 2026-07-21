import { Fragment, useEffect, useState } from "react";
import {
  api,
  type ExecutionOrderRow,
  type ExecutionStrategyConfig,
  type ExecutionSummary,
  type KillSwitchStatus,
  type LiveAccountResponse,
  type ParamSchema,
  type RebalanceRunRow,
  type SignalAlert,
} from "../api";
import { StatTile } from "./StatTile";

const POLL_MS = 30_000;

const RUN_STATUS_STYLE: Record<string, { color: string; bg: string }> = {
  completed: { color: "var(--status-good)", bg: "var(--status-good-bg)" },
  completed_with_daily_loss_halt: { color: "var(--status-warning)", bg: "var(--status-warning-bg)" },
  partial_failure: { color: "var(--status-warning)", bg: "var(--status-warning-bg)" },
  failed: { color: "var(--status-critical)", bg: "var(--status-critical-bg)" },
  blocked_kill_switch: { color: "var(--status-critical)", bg: "var(--status-critical-bg)" },
  blocked_not_enabled: { color: "var(--text-muted)", bg: "var(--gridline)" },
  blocked_market_closed: { color: "var(--text-muted)", bg: "var(--gridline)" },
  running: { color: "var(--series-1)", bg: "var(--series-1-wash)" },
};

function RunStatusBadge({ status }: { status: string }) {
  const style = RUN_STATUS_STYLE[status] ?? { color: "var(--text-muted)", bg: "var(--gridline)" };
  return (
    <span
      className="rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap"
      style={{ color: style.color, background: style.bg }}
    >
      {status.replace(/_/g, " ")}
    </span>
  );
}

function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

function fmtSignedMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

// Neutral "Long/Short signal" wording, never "BUY"/"SELL" -- this is a
// detected entry-condition alert, not a recommendation (CLAUDE.md's
// investment-advice non-goal applies here same as the Screener banner).
function DirectionBadge({ direction }: { direction: string }) {
  const isLong = direction.toLowerCase() === "long";
  const color = isLong ? "var(--status-good)" : "var(--status-critical)";
  const bg = isLong ? "var(--status-good-bg)" : "var(--status-critical-bg)";
  return (
    <span
      className="rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap"
      style={{ color, background: bg }}
    >
      {isLong ? "Long signal" : "Short signal"}
    </span>
  );
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

  const [executionConfig, setExecutionConfig] = useState<ExecutionStrategyConfig[]>([]);
  const [runs, setRuns] = useState<RebalanceRunRow[]>([]);
  const [killSwitch, setKillSwitch] = useState<KillSwitchStatus | null>(null);
  const [rebalancing, setRebalancing] = useState<string | null>(null);
  const [togglingConfig, setTogglingConfig] = useState<string | null>(null);
  const [killSwitchBusy, setKillSwitchBusy] = useState(false);
  const [flattenOnKill, setFlattenOnKill] = useState(false);
  const [expandedRunId, setExpandedRunId] = useState<number | null>(null);
  const [runOrders, setRunOrders] = useState<ExecutionOrderRow[]>([]);
  const [summary, setSummary] = useState<ExecutionSummary | null>(null);
  const [paramSchemas, setParamSchemas] = useState<Record<string, ParamSchema>>({});

  const refreshExecutionState = () =>
    Promise.all([
      api.executionConfig(),
      api.executionRuns(20),
      api.killSwitchStatus(),
      api.executionSummary(),
    ]).then(([config, runRows, kill, execSummary]) => {
      setExecutionConfig(config);
      setRuns(runRows);
      setKillSwitch(kill);
      setSummary(execSummary);
      // The registered-default config each ENABLED strategy is actually
      // running -- automated execution never applies a Lab-tab override
      // (see engine/execution.py's module docstring), so this schema's
      // `default` values ARE the live config, not just a Lab-tab starting
      // point. Reuses the same endpoint the Lab tab's param sliders read.
      Promise.all(config.map((c) => api.paramSchema(c.strategyName))).then((schemas) => {
        setParamSchemas(Object.fromEntries(schemas.map((s) => [s.strategyName, s])));
      });
    });

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      Promise.all([api.liveAccount(), api.liveSignals(100), refreshExecutionState()])
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

  const toggleStrategy = async (strategyName: string, enabled: boolean) => {
    setTogglingConfig(strategyName);
    try {
      await api.setExecutionConfig(strategyName, enabled);
      await refreshExecutionState();
    } catch (e) {
      setLoadError(String(e));
    } finally {
      setTogglingConfig(null);
    }
  };

  const rebalanceNow = async (strategyName: string) => {
    setRebalancing(strategyName);
    try {
      await api.rebalanceNow(strategyName);
      await refreshExecutionState();
    } catch (e) {
      setLoadError(String(e));
    } finally {
      setRebalancing(null);
    }
  };

  const toggleKillSwitch = async () => {
    if (killSwitch?.active) {
      setKillSwitchBusy(true);
      try {
        await api.deactivateKillSwitch();
        await refreshExecutionState();
      } finally {
        setKillSwitchBusy(false);
      }
      return;
    }
    const confirmed = window.confirm(
      flattenOnKill
        ? "Activate the kill switch and immediately close all open positions? This stops all new order submission."
        : "Activate the kill switch? This stops all new order submission (existing positions are left open).",
    );
    if (!confirmed) return;
    setKillSwitchBusy(true);
    try {
      await api.activateKillSwitch(flattenOnKill);
      await refreshExecutionState();
    } finally {
      setKillSwitchBusy(false);
    }
  };

  const toggleRunExpanded = async (runId: number) => {
    if (expandedRunId === runId) {
      setExpandedRunId(null);
      return;
    }
    setExpandedRunId(runId);
    const orders = await api.executionOrders(runId);
    setRunOrders(orders);
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
  const enabledCount = executionConfig.filter((c) => c.enabled).length;

  // Sum of Alpaca's own per-position unrealized P&L -- "money made on the
  // current set of open stocks," mark-to-market as of the last poll.
  const unrealizedPnl = positions.reduce((sum, p) => sum + (p.unrealizedPl ?? 0), 0);

  // "All time" = since this account's first REAL (non-blocked) rebalance
  // -- account.equity now vs. the equity captured right before that first
  // trade (engine/execution_db.py:earliest_run_with_baseline). Deliberately
  // account-level: see /api/live/execution/summary's docstring for why
  // that's the same thing as "the strategy" while only one strategy trades
  // in this account.
  const allTimePnl =
    summary?.startingEquity != null && acct.equity !== undefined
      ? acct.equity - summary.startingEquity
      : null;
  const allTimeReturnPct =
    allTimePnl !== null && summary?.startingEquity ? (allTimePnl / summary.startingEquity) * 100 : null;
  const daysSinceFirstTrade = summary?.firstTradeAt
    ? Math.max(0, Math.floor((Date.now() - new Date(summary.firstTradeAt).getTime()) / 86_400_000))
    : null;

  return (
    <div className="space-y-6">
      <div
        className="rounded-lg border px-4 py-3 text-sm"
        style={{ borderColor: "var(--status-warning)", background: "var(--status-warning-bg)", color: "var(--text-primary)" }}
      >
        <strong>Paper trading only.</strong> Signals from day-trading strategies below are
        detected from delayed (~15min, free-tier) data and logged for monitoring only — never
        traded automatically.{" "}
        {enabledCount > 0 ? (
          <>
            <strong>
              {enabledCount} strateg{enabledCount === 1 ? "y is" : "ies are"} enabled for automated
              paper rebalancing
            </strong>{" "}
            below — real market orders, no stop/target, gated by the guardrails in the
            "Automated execution" panel. Kill switch:{" "}
            <strong style={{ color: killSwitch?.active ? "var(--status-critical)" : "var(--status-good)" }}>
              {killSwitch?.active ? "ACTIVE (blocking new orders)" : "off"}
            </strong>
            .
          </>
        ) : (
          "No strategy is currently enabled for automated execution."
        )}{" "}
        Account <code>{acct.accountNumber ?? "—"}</code> is Alpaca's paper environment, not real
        money.
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

          <div
            className="space-y-4 rounded-lg border p-4"
            style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                Automated execution
              </h2>
              <div className="flex flex-wrap items-center gap-3">
                <label className="flex items-center gap-1.5 text-xs" style={{ color: "var(--text-secondary)" }}>
                  <input
                    type="checkbox"
                    checked={flattenOnKill}
                    onChange={(e) => setFlattenOnKill(e.target.checked)}
                    disabled={Boolean(killSwitch?.active)}
                  />
                  Also flatten positions
                </label>
                <button
                  type="button"
                  onClick={toggleKillSwitch}
                  disabled={killSwitchBusy}
                  className="rounded-md px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                  style={{
                    background: killSwitch?.active ? "var(--status-good)" : "var(--status-critical)",
                  }}
                >
                  {killSwitchBusy
                    ? "Working…"
                    : killSwitch?.active
                      ? "Deactivate kill switch"
                      : "Activate kill switch"}
                </button>
              </div>
            </div>

            {summary && summary.startingEquity != null ? (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <StatTile
                  label="Unrealized P&L (open positions)"
                  value={fmtSignedMoney(unrealizedPnl)}
                  valueColor={unrealizedPnl >= 0 ? "var(--status-good)" : "var(--status-critical)"}
                />
                <StatTile
                  label="All-time P&L"
                  value={fmtSignedMoney(allTimePnl)}
                  valueColor={
                    allTimePnl === null ? undefined : allTimePnl >= 0 ? "var(--status-good)" : "var(--status-critical)"
                  }
                />
                <StatTile
                  label="All-time return"
                  value={fmtPct(allTimeReturnPct)}
                  valueColor={
                    allTimeReturnPct === null
                      ? undefined
                      : allTimeReturnPct >= 0
                        ? "var(--status-good)"
                        : "var(--status-critical)"
                  }
                />
                <StatTile
                  label="Trading since"
                  value={
                    daysSinceFirstTrade === null
                      ? "—"
                      : daysSinceFirstTrade === 0
                        ? "Today"
                        : `${daysSinceFirstTrade} day${daysSinceFirstTrade === 1 ? "" : "s"} ago`
                  }
                />
              </div>
            ) : (
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                No completed rebalance yet — all-time P&L will appear here after the first real trade.
              </p>
            )}
            {summary && summary.completedRebalances > 0 && (
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                {summary.completedRebalances} completed rebalance{summary.completedRebalances === 1 ? "" : "s"} all
                time. All-time figures are account-level (this Alpaca paper account, not a single strategy in
                isolation) — accurate as long as only automated strategies trade in it.
              </p>
            )}

            <div className="space-y-2">
              {executionConfig.map((cfg) => {
                const schema = paramSchemas[cfg.strategyName];
                return (
                  <div
                    key={cfg.strategyName}
                    className="rounded-md border px-3 py-2"
                    style={{ borderColor: "var(--gridline)" }}
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-3">
                        <label className="flex items-center gap-2 text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                          <input
                            type="checkbox"
                            checked={cfg.enabled}
                            disabled={togglingConfig === cfg.strategyName}
                            onChange={(e) => toggleStrategy(cfg.strategyName, e.target.checked)}
                          />
                          {cfg.strategyName}
                        </label>
                        <span
                          className="rounded-full px-2 py-0.5 text-xs font-medium"
                          style={{
                            color: cfg.enabled ? "var(--status-good)" : "var(--text-muted)",
                            background: cfg.enabled ? "var(--status-good-bg)" : "var(--gridline)",
                          }}
                        >
                          {cfg.enabled ? "Automated" : "Off"}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => rebalanceNow(cfg.strategyName)}
                        disabled={rebalancing === cfg.strategyName}
                        className="rounded-md border px-2.5 py-1 text-xs font-medium disabled:opacity-50"
                        style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
                      >
                        {rebalancing === cfg.strategyName ? "Running…" : "Rebalance now"}
                      </button>
                    </div>
                    {schema && (
                      <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                        Running config:{" "}
                        {schema.params.map((p, i) => (
                          <span key={p.name}>
                            {i > 0 && ", "}
                            <span style={{ color: "var(--text-secondary)" }}>{p.name}</span>={String(p.default)}
                          </span>
                        ))}
                        {schema.params.length === 0 && "no tunable parameters"}
                      </p>
                    )}
                  </div>
                );
              })}
              {executionConfig.length === 0 && (
                <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                  No automatable strategies registered.
                </p>
              )}
            </div>

            <div>
              <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
                Recent rebalance runs ({runs.length})
              </div>
              <div className="overflow-x-auto rounded-lg border" style={{ borderColor: "var(--gridline)" }}>
                <table className="w-full min-w-[720px] border-collapse text-sm">
                  <thead>
                    <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
                      {["Triggered", "Strategy", "Date", "Source", "Status", ""].map((h) => (
                        <th key={h} className="px-3 py-2 text-left font-medium whitespace-nowrap" style={{ color: "var(--text-muted)" }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {runs.map((run) => (
                      <Fragment key={run.id}>
                        <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
                          <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>{fmtTime(run.triggeredAt)}</td>
                          <td className="px-3 py-2" style={{ color: "var(--text-primary)" }}>{run.strategyName}</td>
                          <td className="px-3 py-2 whitespace-nowrap" style={{ color: "var(--text-secondary)" }}>{run.rebalanceDate}</td>
                          <td className="px-3 py-2" style={{ color: "var(--text-secondary)" }}>{run.triggerSource}</td>
                          <td className="px-3 py-2"><RunStatusBadge status={run.status} /></td>
                          <td className="px-3 py-2 whitespace-nowrap">
                            <button
                              type="button"
                              onClick={() => toggleRunExpanded(run.id)}
                              className="text-xs font-medium underline-offset-2 hover:underline"
                              style={{ color: "var(--series-1)" }}
                            >
                              {expandedRunId === run.id ? "Hide orders" : "Show orders"}
                            </button>
                          </td>
                        </tr>
                        {expandedRunId === run.id && (
                          <tr style={{ borderBottom: "1px solid var(--gridline)", background: "var(--surface-2, var(--surface-1))" }}>
                            <td colSpan={6} className="px-3 py-2">
                              {run.errorMessage && (
                                <p className="mb-2 text-xs" style={{ color: "var(--status-critical)" }}>
                                  {run.errorMessage}
                                </p>
                              )}
                              {runOrders.length === 0 ? (
                                <p className="text-xs" style={{ color: "var(--text-muted)" }}>No orders for this run.</p>
                              ) : (
                                <div className="flex flex-col gap-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                                  {runOrders.map((o) => (
                                    <div key={o.id}>
                                      <span style={{ color: "var(--text-primary)", fontWeight: 500 }}>{o.symbol}</span>{" "}
                                      {o.side} · {o.orderKind}
                                      {o.notional !== null ? ` $${o.notional.toFixed(2)}` : o.qty !== null ? ` ${o.qty} sh` : ""}
                                      {" · "}
                                      <span style={{ color: "var(--text-muted)" }}>{o.status}</span>
                                      {o.filledAvgPrice !== null && ` · filled @ ${fmtMoney(o.filledAvgPrice)}`}
                                      {o.errorMessage && (
                                        <span style={{ color: "var(--status-critical)" }}> · {o.errorMessage}</span>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                    {runs.length === 0 && (
                      <tr>
                        <td className="px-3 py-3 text-sm" colSpan={6} style={{ color: "var(--text-muted)" }}>
                          No rebalance runs logged yet.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
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
                        No orders yet. Orders placed by the "Automated execution" panel above
                        (for strategies you've enabled) will appear here.
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
                  <td className="px-4 py-2">
                    <DirectionBadge direction={s.direction} />
                  </td>
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
