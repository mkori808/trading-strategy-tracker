import { api, type PlainVsResidualForwardStatus, type PlainVsResidualSeriesBlock } from "../api";
import { useResource } from "../useResource";
import { KEYS } from "../resourceKeys";
import { changeColor, fmtDate, fmtPct } from "../format";
import { CardEmpty, StatusBadge } from "./Card";

const MUTED = { color: "var(--text-muted)" } as const;
const SECONDARY = { color: "var(--text-secondary)" } as const;

function fmtRatio(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : v.toFixed(2);
}

/** Prospective forward shadow for Plain Momentum (C, diagnostic control) vs
 * Market-Residual Momentum (D, canonical). Surfaces
 * engine/plain_vs_residual_momentum_forward.py's existing forward ledger
 * only -- this component never computes a return, a drawdown, or a
 * checkpoint date itself; every number here is exactly what the backend
 * scorecard returned.
 *
 * Deliberately visually and structurally separate from: the Alpaca paper
 * execution card (real orders; this places none), the canonical DM/MRM
 * forward-shadow stack in ResearchStatusView/LiveMonitorView (a different
 * pair, session-count checkpoints, a vol-scaled blend that doesn't exist
 * here), and the prop/self-funded simulation panels (synthetic account
 * accounting; this is a normalized 100-base NAV comparison, not an
 * account simulation). */
export function PlainVsResidualForwardView() {
  const forward = useResource<PlainVsResidualForwardStatus>(
    KEYS.researchPlainVsResidualForward,
    () => api.researchPlainVsResidualForward(),
  );

  if (forward.error) {
    return <div className="text-xs" style={{ color: "var(--status-critical)" }}>{forward.error}</div>;
  }
  if (!forward.data) {
    return <CardEmpty>Loading prospective shadow state…</CardEmpty>;
  }

  const { scorecard, alerts, reconciliation, operations } = forward.data;
  const { checkpoint } = scorecard;
  const c = scorecard.series.c;
  const d = scorecard.series.d;
  const spy = scorecard.series.spy;

  // Eligible for a real (still human-written) comparison only once the
  // 12-month checkpoint is reached, per the preregistration -- everything
  // before that, including the 6-month checkpoint, stays observational.
  const eligibleForComparison = checkpoint.reached.length >= 2;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3 rounded-lg border p-3" style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}>
        <div className="text-xs" style={SECONDARY}>
          <div><strong style={{ color: "var(--text-primary)" }}>C</strong> = Plain Momentum Control (diagnostic; not a registered/tradable strategy)</div>
          <div><strong style={{ color: "var(--text-primary)" }}>D</strong> = Market-Residual Momentum (canonical, frozen)</div>
          <div>Status = Prospective attribution / frozen</div>
        </div>
        <div className="flex flex-col items-end gap-1">
          <StatusBadge mode="shadow">SHADOW · NO ORDERS · FROZEN</StatusBadge>
          <span className="text-[11px]" style={MUTED}>{scorecard.observations} observation{scorecard.observations === 1 ? "" : "s"}</span>
        </div>
      </div>

      <div className="rounded-lg border px-3 py-2 text-xs font-semibold" style={{
        borderColor: eligibleForComparison ? "var(--series-1)" : "var(--status-warning)",
        background: eligibleForComparison ? "var(--series-1-wash)" : "var(--status-warning-bg)",
        color: "var(--text-primary)",
      }}>
        {eligibleForComparison
          ? checkpoint.interpretationStatus
          : "No inference — prospective evidence collection only."}
      </div>

      {alerts.length > 0 && (
        <div className="space-y-2">
          {alerts.map((alert) => (
            <div key={alert.code} className="rounded-md border px-3 py-2 text-xs" style={{
              borderColor: "var(--status-warning)", background: "var(--status-warning-bg)", color: "var(--text-primary)",
            }}>
              <strong>Reconciliation: </strong>{alert.message}
            </div>
          ))}
        </div>
      )}

      <div>
        <h3 className="mb-2 text-[11px] font-semibold tracking-wide" style={MUTED}>SIDE BY SIDE</h3>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead style={MUTED}>
              <tr>
                <th className="pb-2 pr-3 font-medium">Metric</th>
                <th className="pb-2 pr-3 text-right font-medium">C · Plain Momentum</th>
                <th className="pb-2 pr-3 text-right font-medium">D · Market-Residual</th>
                <th className="pb-2 text-right font-medium">SPY (benchmark)</th>
              </tr>
            </thead>
            <tbody>
              <SeriesRow label="Forward return" c={c} d={d} spy={spy} field="cumulativeReturnPct" fmt={fmtPct} colored />
              <SeriesRow label="Max drawdown" c={c} d={d} spy={spy} field="maxDrawdownPct" fmt={fmtPct} colored />
              <SeriesRow label="Sharpe" c={c} d={d} spy={spy} field="sharpe" fmt={fmtRatio} annualizedOnly />
              <SeriesRow label="Sortino" c={c} d={d} spy={spy} field="sortino" fmt={fmtRatio} annualizedOnly />
            </tbody>
          </table>
        </div>
        {c?.annualizedBlock && (
          <p className="mt-1 text-[11px]" style={MUTED}>
            Sharpe/Sortino withheld: {c.annualizedBlock.toLowerCase()} (fewer than 252 forward sessions).
          </p>
        )}
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-lg border p-3" style={{ borderColor: "var(--border)" }}>
          <h3 className="mb-2 text-[11px] font-semibold tracking-wide" style={MUTED}>RELATIVE (D minus C)</h3>
          <div className="space-y-1 text-sm">
            <Row label="Return difference" value={scorecard.pairedRelative ? `${scorecard.pairedRelative.dMinusCReturnDiffPp >= 0 ? "+" : ""}${scorecard.pairedRelative.dMinusCReturnDiffPp.toFixed(2)}pp` : "—"} valueColor={scorecard.pairedRelative ? changeColor(scorecard.pairedRelative.dMinusCReturnDiffPp) : undefined} />
            <Row label="Max drawdown difference" value={scorecard.pairedRelative ? `${scorecard.pairedRelative.dMinusCMaxDrawdownDiffPp >= 0 ? "+" : ""}${scorecard.pairedRelative.dMinusCMaxDrawdownDiffPp.toFixed(2)}pp` : "—"} valueColor={scorecard.pairedRelative ? changeColor(-scorecard.pairedRelative.dMinusCMaxDrawdownDiffPp) : undefined} />
          </div>
          <p className="mt-2 text-[11px]" style={MUTED}>
            The preregistered hypothesis predicts a negative return difference paired with a positive (less negative) drawdown difference — D gives up some raw return for better downside quality. This is not evaluated as supportive or contradicted before the 12-month checkpoint.
          </p>
        </div>

        <div className="rounded-lg border p-3" style={{ borderColor: "var(--border)" }}>
          <h3 className="mb-2 text-[11px] font-semibold tracking-wide" style={MUTED}>HIT RATE BY REBALANCE</h3>
          <div className="space-y-1 text-sm">
            <Row label="C positive periods" value={scorecard.hitRates.cHitRatePct == null ? "—" : `${scorecard.hitRates.cHitRatePct.toFixed(1)}%`} />
            <Row label="D positive periods" value={scorecard.hitRates.dHitRatePct == null ? "—" : `${scorecard.hitRates.dHitRatePct.toFixed(1)}%`} />
            <Row label="D beats C (paired)" value={scorecard.hitRates.pairedHitRatePct == null ? "—" : `${scorecard.hitRates.pairedHitRatePct.toFixed(1)}%`} />
            <Row label="Paired rebalance periods" value={String(scorecard.hitRates.pairedRebalancePeriods ?? 0)} />
          </div>
        </div>
      </div>

      <div>
        <h3 className="mb-2 text-[11px] font-semibold tracking-wide" style={MUTED}>REGIME BREAKDOWN</h3>
        {Object.keys(scorecard.regimeBreakdown).length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead style={MUTED}>
                <tr><th className="pb-1 pr-3 font-medium">Regime</th><th className="pb-1 pr-3 text-right font-medium">Periods</th>
                  <th className="pb-1 pr-3 text-right font-medium">C mean return</th><th className="pb-1 text-right font-medium">D mean return</th></tr>
              </thead>
              <tbody>
                {Object.entries(scorecard.regimeBreakdown).map(([regime, bucket]) => (
                  <tr key={regime} className="border-t" style={{ borderColor: "var(--gridline)" }}>
                    <td className="py-1 pr-3 font-medium" style={{ color: "var(--text-primary)" }}>{regime}</td>
                    <td className="py-1 pr-3 text-right">{bucket.periods}</td>
                    <td className="py-1 pr-3 text-right" style={{ color: changeColor(bucket.cMeanReturnPct) }}>{fmtPct(bucket.cMeanReturnPct)}</td>
                    <td className="py-1 text-right" style={{ color: changeColor(bucket.dMeanReturnPct) }}>{fmtPct(bucket.dMeanReturnPct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <CardEmpty>No closed rebalance periods yet.</CardEmpty>
        )}
      </div>

      <div>
        <h3 className="mb-2 text-[11px] font-semibold tracking-wide" style={MUTED}>PREREGISTERED CHECKPOINTS</h3>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          {(checkpoint.schedule ?? []).map((row) => (
            <div key={row.months} className="rounded-lg border p-2 text-xs" style={{
              borderColor: row.reached ? "var(--series-1)" : "var(--gridline)",
              background: row.reached ? "var(--series-1-wash)" : "transparent",
            }}>
              <div className="font-semibold" style={{ color: "var(--text-primary)" }}>
                {row.reached ? "✓ " : ""}{row.months}-month checkpoint
              </div>
              <div style={MUTED}>{row.label}</div>
              <div className="mt-1" style={SECONDARY}>{fmtDate(row.date)}</div>
            </div>
          ))}
        </div>
        {checkpoint.next && (
          <p className="mt-2 text-[11px]" style={MUTED}>
            Next checkpoint: {checkpoint.next.label} on {fmtDate(checkpoint.next.date)}.
          </p>
        )}
      </div>

      {reconciliation && reconciliation.confirmedAmendments > 0 && (
        <div>
          <h3 className="mb-2 text-[11px] font-semibold tracking-wide" style={MUTED}>DISCLOSED AMENDMENTS</h3>
          <div className="space-y-1 text-xs" style={SECONDARY}>
            {reconciliation.amendments.map((amendment) => (
              <div key={String(amendment.amendmentId)} className="rounded border p-2" style={{ borderColor: "var(--gridline)" }}>
                <strong>{String(amendment.reasonCode)}:</strong> {String(amendment.explanation)}
              </div>
            ))}
          </div>
          <p className="mt-1 text-[11px]" style={MUTED}>Raw NAV is never rewritten — amendments are disclosed corrections applied to the effective view only.</p>
        </div>
      )}

      <p className="text-[11px]" style={MUTED}>
        Development cutoff {fmtDate(scorecard.developmentCutoff)} · scheduler last checked {operations.lastAttemptAt ? fmtDate(operations.lastAttemptAt) : "not yet"} · preregistration: {forward.data.preregistration}
      </p>
    </div>
  );
}

function Row({ label, value, valueColor }: { label: string; value: string; valueColor?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <span style={SECONDARY}>{label}</span>
      <span className="tabular-nums" style={{ color: valueColor ?? "var(--text-primary)" }}>{value}</span>
    </div>
  );
}

function SeriesRow({
  label, c, d, spy, field, fmt, colored, annualizedOnly,
}: {
  label: string;
  c?: PlainVsResidualSeriesBlock;
  d?: PlainVsResidualSeriesBlock;
  spy?: PlainVsResidualSeriesBlock;
  field: keyof PlainVsResidualSeriesBlock;
  fmt: (v: number | null | undefined) => string;
  colored?: boolean;
  annualizedOnly?: boolean;
}) {
  if (annualizedOnly && c?.annualizedBlock) {
    return (
      <tr className="border-t" style={{ borderColor: "var(--gridline)" }}>
        <td className="py-2 pr-3" style={SECONDARY}>{label}</td>
        <td className="py-2 pr-3 text-right" colSpan={3} style={MUTED}>Too early to evaluate</td>
      </tr>
    );
  }
  const cv = c?.[field] as number | null | undefined;
  const dv = d?.[field] as number | null | undefined;
  const spyv = spy?.[field] as number | null | undefined;
  return (
    <tr className="border-t" style={{ borderColor: "var(--gridline)" }}>
      <td className="py-2 pr-3" style={SECONDARY}>{label}</td>
      <td className="py-2 pr-3 text-right tabular-nums" style={{ color: colored ? changeColor(cv) : "var(--text-primary)" }}>{fmt(cv)}</td>
      <td className="py-2 pr-3 text-right tabular-nums" style={{ color: colored ? changeColor(dv) : "var(--text-primary)" }}>{fmt(dv)}</td>
      <td className="py-2 text-right tabular-nums" style={{ color: colored ? changeColor(spyv) : "var(--text-primary)" }}>{fmt(spyv)}</td>
    </tr>
  );
}
