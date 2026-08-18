import { Fragment, useEffect, useState } from "react";
import {
  api,
  ApiError,
  type ExecutionOrderRow,
  type ExecutionStrategyConfig,
  type ExecutionSummary,
  type ForwardTestStatus,
  type FillCalibration,
  type GovernedForwardExperiment,
  type KillSwitchStatus,
  type LiveAccountResponse,
  type ParamSchema,
  type ParamSpec,
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
  blocked_validation: { color: "var(--status-critical)", bg: "var(--status-critical-bg)" },
  blocked_inception_policy: { color: "var(--status-critical)", bg: "var(--status-critical-bg)" },
  inception_flattening: { color: "var(--status-warning)", bg: "var(--status-warning-bg)" },
  inception_flatten_partial_failure: { color: "var(--status-critical)", bg: "var(--status-critical-bg)" },
  running: { color: "var(--series-1)", bg: "var(--series-1-wash)" },
};

// engine/execution.py:execute_rebalance's full return-status vocabulary.
// "Rebalance now" is force=True (skips the is-due check but nothing else),
// so any of these can come back as a well-formed 200 response with no
// exception thrown -- there is nothing for the frontend's error handling to
// catch. Discarding that response silently is indistinguishable from a
// hang: the button visibly does nothing on every genuinely-blocked outcome
// (market closed, kill switch active, already ran today, ...), which is
// the normal case outside trading hours, not an edge case worth ignoring.
const REBALANCE_RESULT_LABEL: Record<string, string> = {
  completed: "Rebalance completed.",
  completed_with_daily_loss_halt: "Rebalance completed, but new entries were halted (daily loss limit hit).",
  partial_failure: "Rebalance completed with some order failures.",
  failed: "Rebalance failed.",
  not_due: "Not due — this strategy already had its rebalance for the current cycle.",
  blocked_not_enabled: "Blocked — this strategy isn't enabled for execution.",
  blocked_validation: "Blocked by validation gates.",
  blocked_kill_switch: "Blocked — the kill switch is active.",
  blocked_inception_policy: "Blocked — choose Adopt or Flatten for this forward test first.",
  blocked_market_closed: "Blocked — the market is closed.",
  alpaca_not_configured: "Blocked — Alpaca isn't configured.",
  already_running_or_done_today: "A rebalance for this strategy already ran or is running today.",
  inception_flattening: "Waiting for the broker to confirm the account is flat before entering positions.",
  inception_flatten_partial_failure: "Some liquidation orders failed during inception flattening — see order history.",
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

function LiveParamControl({ spec, value, onChange }: {
  spec: ParamSpec;
  value: number | boolean | string;
  onChange: (value: number | boolean | string) => void;
}) {
  if (spec.kind === "bool") {
    return <label className="flex items-center justify-between gap-2 text-xs"><span>{spec.label}</span><input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} /></label>;
  }
  if (spec.kind === "str" && spec.choices) {
    return <label className="flex items-center justify-between gap-2 text-xs"><span>{spec.label}</span><select value={String(value)} onChange={(e) => onChange(e.target.value)} className="rounded border px-1.5 py-1" style={{ borderColor: "var(--border)", background: "var(--page)", color: "var(--text-primary)" }}>{spec.choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}</select></label>;
  }
  return <label className="flex items-center justify-between gap-2 text-xs"><span>{spec.label}</span><input type={spec.kind === "str" ? "text" : "number"} value={String(value)} min={spec.minimum ?? undefined} max={spec.maximum ?? undefined} step={spec.step ?? undefined} onChange={(e) => onChange(spec.kind === "str" ? e.target.value : spec.kind === "int" ? Math.round(e.target.valueAsNumber) : e.target.valueAsNumber)} className="w-20 rounded border px-1.5 py-1 text-right" style={{ borderColor: "var(--border)", background: "var(--page)", color: "var(--text-primary)" }} /></label>;
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
  const [forwardTest, setForwardTest] = useState<ForwardTestStatus | null>(null);
  const [governedForward, setGovernedForward] = useState<GovernedForwardExperiment[]>([]);
  const [fillCalibration, setFillCalibration] = useState<FillCalibration | null>(null);
  const [paramSchemas, setParamSchemas] = useState<Record<string, ParamSchema>>({});
  const [availableStrategies, setAvailableStrategies] = useState<{ strategyName: string }[]>([]);
  const [editingStrategy, setEditingStrategy] = useState<string | null>(null);
  const [draftParams, setDraftParams] = useState<Record<string, number | boolean | string>>({});
  const [savingConfig, setSavingConfig] = useState(false);

  const refreshExecutionState = () =>
    Promise.all([
      api.executionConfig(),
      api.executionRuns(20),
      api.killSwitchStatus(),
      api.executionSummary(),
      api.forwardTestStatus(),
      api.executionCalibration(),
    ]).then(([config, runRows, kill, execSummary, forward, calibration]) => {
      setExecutionConfig(config);
      setRuns(runRows);
      setKillSwitch(kill);
      setSummary(execSummary);
      setForwardTest(forward);
      setFillCalibration(calibration);
      // The registered-default config each ENABLED strategy is actually
      // running -- automated execution never applies a Lab-tab override
      // (see engine/execution.py's module docstring), so this schema's
      // `default` values ARE the live config, not just a Lab-tab starting
      // point. Reuses the same endpoint the Lab tab's param sliders read.
      Promise.all(config.map((c) => api.paramSchema(c.strategyName))).then((schemas) => {
        setParamSchemas(Object.fromEntries(schemas.map((s) => [s.strategyName, s])));
      });
      Promise.all(config.map((c) => api.forwardExperiments(c.strategyName))).then((groups) => {
        setGovernedForward(groups.flat());
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

  useEffect(() => {
    api.executionStrategies().then(async (strategies) => {
      setAvailableStrategies(strategies);
      const schemas = await Promise.all(strategies.map((s) => api.paramSchema(s.strategyName)));
      setParamSchemas((current) => ({ ...current, ...Object.fromEntries(schemas.map((s) => [s.strategyName, s])) }));
    }).catch((e) => setLoadError(String(e)));
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
    const selectedConfig = executionConfig.find((c) => c.strategyName === strategyName);
    const params = selectedConfig?.params ?? {};
    const validationRunId = selectedConfig?.validationRunId ?? undefined;
    let inceptionPolicy: "adopt" | "flatten" | undefined;
    if (enabled) {
      if (selectedConfig?.inception.status === "initialized") {
        inceptionPolicy = selectedConfig.inception.policy ?? undefined;
      } else {
        const choice = window.prompt(
          "Forward-test inception policy:\n\n" +
            "ADOPT marks current positions to market before reconciliation.\n" +
            "FLATTEN liquidates them first and waits for a confirmed flat account.\n\n" +
            "Type ADOPT or FLATTEN:",
          selectedConfig?.inception.policy === "flatten" ? "FLATTEN" : "ADOPT",
        );
        if (choice === null) {
          setTogglingConfig(null);
          return;
        }
        const normalized = choice.trim().toLowerCase();
        if (normalized !== "adopt" && normalized !== "flatten") {
          setLoadError("Enable canceled: enter exactly ADOPT or FLATTEN.");
          setTogglingConfig(null);
          return;
        }
        inceptionPolicy = normalized;
      }
    }
    try {
      await api.setExecutionConfig(
        strategyName, enabled, params, validationRunId, inceptionPolicy,
      );
      await refreshExecutionState();
    } catch (e) {
      const message = e instanceof Error ? e.message : String(e);
      // Only offer the override when ENABLING and the backend rejected with
      // 409 (a gate/state block on this endpoint, not e.g. a network error
      // or a 400 param-validation failure) -- branching on the actual HTTP
      // status rather than string-matching the detail text, since the
      // backend has more than one 409 source (paper_execution_eligibility's
      // gate check AND forward_experiments.start's own independent
      // forward-test-worthy check) and their wording can diverge -- a text
      // match tied to only one of them silently dead-ends the other with no
      // way to invoke the override this flow exists to offer. This is an
      // explicit, logged bypass (paper capital only, never production; see
      // engine/forward_experiments.py:start's docstring), never a silent retry.
      if (enabled && e instanceof ApiError && e.status === 409) {
        const reason = window.prompt(
          `${message}\n\nPromote to paper testing anyway? This is a logged override -- ` +
          "paper capital only, never live/production. State why:",
        );
        if (reason && reason.trim()) {
          try {
            await api.setExecutionConfig(
              strategyName, enabled, params, validationRunId, inceptionPolicy,
              { reason: reason.trim() },
            );
            await refreshExecutionState();
          } catch (e2) {
            setLoadError(String(e2));
          }
        }
      } else {
        setLoadError(message);
      }
    } finally {
      setTogglingConfig(null);
    }
  };

  const openConfig = (strategyName: string, current: Record<string, number | boolean | string> = {}) => {
    const schema = paramSchemas[strategyName];
    if (!schema) return;
    setEditingStrategy(strategyName);
    setDraftParams(Object.fromEntries(schema.params.map((p) => [p.name, current[p.name] ?? p.default])));
  };

  const saveConfig = async () => {
    if (!editingStrategy) return;
    setSavingConfig(true);
    try {
      const existing = executionConfig.find((c) => c.strategyName === editingStrategy);
      await api.setExecutionConfig(editingStrategy, existing?.enabled ?? false, draftParams);
      setEditingStrategy(null);
      await refreshExecutionState();
    } catch (e) {
      setLoadError(String(e));
    } finally {
      setSavingConfig(false);
    }
  };

  const rebalanceNow = async (strategyName: string) => {
    setRebalancing(strategyName);
    setLoadError(null);
    try {
      const result = await api.rebalanceNow(strategyName);
      await refreshExecutionState();
      const label = result.status ? REBALANCE_RESULT_LABEL[result.status] ?? `Rebalance status: ${result.status}` : null;
      if (label && result.status !== "completed") {
        setLoadError(result.reason ? `${label} ${result.reason}` : label);
      }
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
      {loadError && (
        <div
          className="flex items-start justify-between gap-3 rounded-lg border px-4 py-3 text-sm"
          style={{ borderColor: "var(--status-critical)", background: "var(--status-critical-bg)", color: "var(--status-critical)" }}
        >
          <span>{loadError}</span>
          <button
            type="button"
            onClick={() => setLoadError(null)}
            className="shrink-0 text-xs font-medium underline"
          >
            Dismiss
          </button>
        </div>
      )}
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

      {forwardTest && (
        <div
          className="rounded-lg border p-4"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                Frozen forward test · Dual Momentum
              </h2>
              <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                Stop benchmark: {forwardTest.stopBenchmark}. SPY remains an allocation comparison and does not drive the stop.
              </p>
            </div>
            <RunStatusBadge status={forwardTest.status} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
            <div><span style={{ color: "var(--text-muted)" }}>Frozen</span><div>{forwardTest.freezeDate}</div></div>
            <div><span style={{ color: "var(--text-muted)" }}>Observations</span><div>{forwardTest.observationCount}</div></div>
            <div><span style={{ color: "var(--text-muted)" }}>Stop review</span><div>{forwardTest.stopHorizonMonths} months</div></div>
            <div><span style={{ color: "var(--text-muted)" }}>Stop threshold</span><div>trails by &gt; {forwardTest.stopShortfallPp.toFixed(0)}pp</div></div>
          </div>
          {forwardTest.latest ? (
            <div className="mt-3 grid grid-cols-1 gap-2 text-xs sm:grid-cols-3">
              <div>vs EW PIT Dow: <strong>{fmtPct(forwardTest.latest.vs_ew_pit_dow_pp)}</strong></div>
              <div>vs SPY: <strong>{fmtPct(forwardTest.latest.vs_spy_pp)}</strong></div>
              <div>vs random median: <strong>{fmtPct(forwardTest.latest.vs_random_pp)}</strong></div>
            </div>
          ) : (
            <p className="mt-3 text-xs" style={{ color: "var(--status-warning)" }}>
              Not started. No forward observation has been recorded; backtest evidence must not be presented as forward evidence.
            </p>
          )}
          <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
            {forwardTest.decision.reasoning}
          </p>
        </div>
      )}

      {governedForward.length > 0 && (
        <div className="rounded-lg border p-4" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
          <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Locked strategy forward experiments</h2>
          <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
            These configurations cannot inherit observations after an edit. A falsified experiment automatically disables paper automation and demotes its lifecycle.
          </p>
          <div className="mt-3 space-y-2">
            {governedForward.map((experiment) => (
              <div key={experiment.id} className="rounded-md border p-3 text-xs" style={{ borderColor: "var(--gridline)" }}>
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <strong>{experiment.strategyName} · experiment #{experiment.id}</strong>
                  <RunStatusBadge status={experiment.status} />
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-4">
                  <div><span style={{ color: "var(--text-muted)" }}>Locked</span><div>{experiment.locked ? "Yes" : "No"}</div></div>
                  <div><span style={{ color: "var(--text-muted)" }}>Observations</span><div>{experiment.observationCount}/{experiment.minObservations}</div></div>
                  <div><span style={{ color: "var(--text-muted)" }}>Minimum horizon</span><div>{experiment.minCalendarDays} days</div></div>
                  <div><span style={{ color: "var(--text-muted)" }}>Stop threshold</span><div>{experiment.maxShortfallPct.toFixed(1)}pp</div></div>
                </div>
                <p className="mt-2" style={{ color: "var(--text-muted)" }}>{experiment.conclusion ?? "Waiting for the first forward observation."}</p>
                <details className="mt-2"><summary className="cursor-pointer">Frozen manifest</summary><code className="mt-1 block break-all">{experiment.frozenManifestHash}</code></details>
              </div>
            ))}
          </div>
        </div>
      )}

      {fillCalibration && (
        <div className="rounded-lg border p-4" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>Observed execution calibration</h2>
              <p className="mt-1 text-xs" style={{ color: "var(--text-secondary)" }}>
                Reconciled paper fills calibrate future simulations after {fillCalibration.minimumFills} observations. Prior manifests remain unchanged.
              </p>
            </div>
            <RunStatusBadge status={fillCalibration.calibrated ? "calibrated" : "collecting"} />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
            <div><span style={{ color: "var(--text-muted)" }}>Fills</span><div>{fillCalibration.fills}</div></div>
            <div><span style={{ color: "var(--text-muted)" }}>Median adverse slippage</span><div>{fillCalibration.medianAdverseSlippageBps?.toFixed(1) ?? "—"} bps</div></div>
            <div><span style={{ color: "var(--text-muted)" }}>P95 adverse slippage</span><div>{fillCalibration.p95AdverseSlippageBps?.toFixed(1) ?? "—"} bps</div></div>
            <div><span style={{ color: "var(--text-muted)" }}>Partial-fill rate</span><div>{fillCalibration.partialFillRate === null ? "—" : `${(fillCalibration.partialFillRate * 100).toFixed(1)}%`}</div></div>
          </div>
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
              <div className="flex items-center justify-between gap-3">
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                  Add a strategy disabled first, configure it, then explicitly enable paper execution.
                </span>
                <button
                  type="button"
                  onClick={() => {
                    // Today Dual Momentum is the only safely automatable
                    // strategy. Once it has been added, keep this action
                    // useful by reopening its saved configuration instead
                    // of leaving an unexplained disabled button.
                    const candidate = availableStrategies.find((s) => !executionConfig.some((c) => c.strategyName === s.strategyName))
                      ?? availableStrategies[0];
                    if (!candidate) return;
                    const current = executionConfig.find((c) => c.strategyName === candidate.strategyName);
                    openConfig(candidate.strategyName, current?.params);
                  }}
                  disabled={availableStrategies.length === 0}
                  className="rounded-md px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50"
                  style={{ background: "var(--series-1)" }}
                >
                  {availableStrategies.some((s) => !executionConfig.some((c) => c.strategyName === s.strategyName))
                    ? "Add live strategy"
                    : "Configure live strategy"}
                </button>
              </div>
              {editingStrategy && paramSchemas[editingStrategy] && (
                <div className="rounded-md border p-3" style={{ borderColor: "var(--series-1)", background: "var(--surface-1)" }}>
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <strong className="text-sm" style={{ color: "var(--text-primary)" }}>{editingStrategy}</strong>
                    <button type="button" onClick={() => setEditingStrategy(null)} className="text-xs" style={{ color: "var(--text-muted)" }}>Cancel</button>
                  </div>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {paramSchemas[editingStrategy].params.map((spec) => (
                      <LiveParamControl key={spec.name} spec={spec} value={draftParams[spec.name] ?? spec.default} onChange={(value) => setDraftParams((current) => ({ ...current, [spec.name]: value }))} />
                    ))}
                  </div>
                  <div className="mt-3 flex items-center gap-3">
                    <button type="button" onClick={saveConfig} disabled={savingConfig} className="rounded-md px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50" style={{ background: "var(--series-1)" }}>
                      {savingConfig ? "Saving…" : "Save paper configuration"}
                    </button>
                    <span className="text-xs" style={{ color: "var(--text-muted)" }}>Saving does not enable automated orders.</span>
                  </div>
                </div>
              )}
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
                        {cfg.overrideUsed && (
                          <span
                            className="rounded-full px-2 py-0.5 text-xs font-medium"
                            style={{ color: "var(--status-warning)", background: "var(--status-warning-bg, transparent)" }}
                            title={`Promoted despite failing: ${cfg.overrideBlockers.join(", ") || "validation gates"}`}
                          >
                            Promoted via override
                          </span>
                        )}
                        <span
                          className="rounded-full px-2 py-0.5 text-xs font-medium"
                          style={{
                            color: cfg.inception.status === "initialized" ? "var(--status-good)" : "var(--status-warning)",
                            background: cfg.inception.status === "initialized" ? "var(--status-good-bg)" : "var(--status-warning-bg)",
                          }}
                        >
                          {cfg.inception.policy === "adopt"
                            ? "Adopt at inception"
                            : cfg.inception.policy === "flatten"
                              ? "Flatten at inception"
                              : "Inception choice required"}
                          {` · ${cfg.inception.status}`}
                        </span>
                      </div>
                      <button
                        type="button"
                        onClick={() => openConfig(cfg.strategyName, cfg.params)}
                        disabled={cfg.enabled}
                        className="rounded-md border px-2.5 py-1 text-xs font-medium"
                        style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
                        title={cfg.enabled ? "Backtest and promote a new run to change an active configuration" : undefined}
                      >
                        {cfg.enabled ? "Configuration locked" : "Edit parameters"}
                      </button>
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
                            <span style={{ color: "var(--text-secondary)" }}>{p.name}</span>={String(cfg.params[p.name] ?? p.default)}
                          </span>
                        ))}
                        {schema.params.length === 0 && "no tunable parameters"}
                      </p>
                    )}
                    <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                      Promoted run #{cfg.validationRunId ?? "—"}
                      {cfg.universeId ? ` · universe ${cfg.universeId}` : ""}
                      {cfg.symbols.length ? ` · ${cfg.symbols.length} symbols` : ""}
                    </p>
                    <p
                      className="mt-1.5 text-xs"
                      style={{ color: cfg.inception.status === "initialized" ? "var(--text-muted)" : "var(--status-warning)" }}
                    >
                      {cfg.inception.status === "initialized"
                        ? `Forward baseline ${fmtMoney(cfg.inception.equity)} at ${fmtTime(cfg.inception.inceptionAt)}; ` +
                          `${cfg.inception.inheritedPositions.length} inherited position${cfg.inception.inheritedPositions.length === 1 ? "" : "s"} recorded.`
                        : cfg.inception.status === "policy_required"
                          ? "Execution is blocked until Adopt or Flatten is explicitly selected."
                        : cfg.inception.policy === "flatten" && cfg.inception.status === "flattening"
                          ? "Liquidation orders are being reconciled. Target entries remain blocked until Alpaca confirms the account is flat."
                          : cfg.inception.policy === "flatten"
                            ? "Pending first market-open cycle: inherited positions will be liquidated before the forward baseline is recorded."
                            : "Pending first market-open cycle: current holdings will be marked to market as inherited inventory before reconciliation."}
                    </p>
                    {cfg.inception.status === "policy_required" && (
                      <button
                        type="button"
                        onClick={() => toggleStrategy(cfg.strategyName, true)}
                        disabled={togglingConfig === cfg.strategyName}
                        className="mt-2 rounded-md px-2.5 py-1 text-xs font-medium text-white disabled:opacity-50"
                        style={{ background: "var(--status-warning)" }}
                      >
                        Choose Adopt or Flatten
                      </button>
                    )}
                    {cfg.overrideUsed && (
                      <p className="mt-1.5 text-xs" style={{ color: "var(--status-warning)" }}>
                        Promoted despite failing: {cfg.overrideBlockers.join(", ") || "validation gates"}
                        {cfg.overrideReason && ` — reason given: "${cfg.overrideReason}"`}
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
