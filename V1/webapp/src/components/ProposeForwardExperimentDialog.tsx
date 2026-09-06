import { useState } from "react";
import { api, type PortfolioHistoryRow } from "../api";
import { Modal } from "./Modal";

// Replaces PortfolioRunHistory's old direct "Use for paper execution" button,
// which called the same POST /api/live/execution/config that flips on live
// paper trading -- one click on an arbitrary historical row silently
// promoted it into the account. This dialog instead calls
// api.proposeForwardExperiment, a genuinely separate, safer backend path
// (see api/main.py:propose_forward_experiment) that only ever creates a
// forward_experiments record: it never touches execution_db, so it cannot
// alter the strategy currently assigned to live/paper execution. Enabling
// live paper orders remains a deliberately separate action (see the
// "Advanced" section still in PortfolioRunHistory).
export function ProposeForwardExperimentDialog({
  open,
  onClose,
  strategyName,
  row,
  onProposed,
}: {
  open: boolean;
  onClose: () => void;
  strategyName: string;
  row: PortfolioHistoryRow | null;
  onProposed: () => void;
}) {
  const [acknowledged, setAcknowledged] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!open || !row) return null;
  const passed = Boolean(row.validation?.verdict.forwardTestWorthy);
  const blockers = row.validation?.verdict.blockers ?? [];

  const submit = async () => {
    if (!acknowledged) return;
    setSubmitting(true);
    setError(null);
    try {
      await api.proposeForwardExperiment(
        strategyName,
        row.id,
        true,
        passed ? undefined : { reason: overrideReason.trim() },
      );
      onProposed();
      setAcknowledged(false);
      setOverrideReason("");
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title="Propose for forward experiment" size="md">
      <div className="space-y-4 text-sm" style={{ color: "var(--text-secondary)" }}>
        <p>
          This freezes Run #{row.id}'s exact configuration (universe, dates, parameters) and starts a
          preregistered forward-shadow observation ledger against it. It does <strong>not</strong> enable live
          paper order placement, and does <strong>not</strong> change whichever strategy is currently assigned to
          execution — that remains a separate, explicit action.
        </p>

        <div className="rounded-md border p-3 text-xs" style={{ borderColor: "var(--border)" }}>
          <div><strong>Frozen configuration:</strong> {Object.keys(row.params).length ? Object.entries(row.params).map(([k, v]) => `${k}=${v}`).join(", ") : "registered defaults"}</div>
          <div className="mt-1"><strong>Universe:</strong> {row.universeId ?? (row.symbols.length ? `${row.symbols.length} custom symbols` : "registered default")}</div>
          <div className="mt-1"><strong>Minimum forward horizon:</strong> 180 calendar days and 20 observations before this experiment's status can mature past "running".</div>
        </div>

        {!passed && (
          <div className="rounded-md border px-3 py-2 text-xs" style={{ borderColor: "var(--status-warning)", background: "var(--status-warning-bg)", color: "var(--text-primary)" }}>
            <strong>This run did not pass every validation gate:</strong> {blockers.join("; ") || "see validation report"}.
            Proposing it anyway requires a logged reason below.
          </div>
        )}

        <label className="flex items-start gap-2 text-xs">
          <input type="checkbox" className="mt-0.5" checked={acknowledged} onChange={(e) => setAcknowledged(e.target.checked)} />
          <span>
            I acknowledge this configuration was selected from historical run results, and that a historical
            result — however this run ranks against others — is not by itself validation for production use. Only
            the forward experiment's own frozen, preregistered outcome after the minimum horizon can establish that.
          </span>
        </label>

        {!passed && (
          <label className="flex flex-col gap-1 text-xs">
            <span style={{ color: "var(--text-muted)" }}>Reason for proposing despite unresolved validation gates</span>
            <textarea
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              rows={2}
              className="rounded-md border px-2 py-1.5 text-sm"
              style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-primary)" }}
            />
          </label>
        )}

        {error && <div className="rounded-md border px-3 py-2 text-xs" style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}>{error}</div>}

        <div className="flex justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-md border px-3 py-1.5 text-xs font-medium" style={{ borderColor: "var(--border)" }}>Cancel</button>
          <button
            type="button"
            disabled={!acknowledged || submitting || (!passed && !overrideReason.trim())}
            onClick={submit}
            className="rounded-md px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
            style={{ background: "var(--series-1)" }}
          >
            {submitting ? "Proposing…" : "Propose forward experiment"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
