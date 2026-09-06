import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  api,
  type CompiledRules,
  type CustomStrategy,
  type CustomStrategyList,
  type StrategyDraft,
} from "../api";

/** Describe a strategy in English, review the rules it compiled to, save it.
 *
 * The review step is the point of the two-phase flow: drafting stores
 * nothing, and what it renders is `describe_spec()` output computed from
 * the parsed spec on the backend -- the rules that will actually run, not
 * the author model's account of what it wrote. A user who reads the
 * compiled rules and disagrees discards the draft; nothing is persisted
 * until they press Save.
 *
 * Strategies created here are marked `custom` everywhere they appear (see
 * StrategyTable's badge). They have no tracker entry and no prior sample --
 * they run on the same engine, log to the same table, and are scored by the
 * same bar as everything else, which is exactly why the provenance has to
 * stay visible rather than blending into the registered catalogue. */

const EXAMPLES = [
  "Buy when RSI drops below 30 but the price is still above its 200-day average. Stop 2 ATRs below entry, target twice the risk.",
  "Short a stock that gaps up more than 3% on above-average volume and closes below its opening price.",
  "Enter when the 9-day EMA crosses above the 21-day EMA with the price above the 50-day average; exit on the reverse cross.",
];

function RuleList({ rules }: { rules: CompiledRules }) {
  return (
    <div className="space-y-3 text-xs">
      <p style={{ color: "var(--text-secondary)" }}>{rules.summary}</p>
      <div>
        <div className="mb-1 font-semibold" style={{ color: "var(--text-primary)" }}>
          Enter when ALL of these hold
        </div>
        <ul className="ml-4 list-disc space-y-0.5" style={{ color: "var(--text-secondary)" }}>
          {rules.entry.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </div>
      {rules.exit.length > 0 && (
        <div>
          <div className="mb-1 font-semibold" style={{ color: "var(--text-primary)" }}>
            Close the position when ALL of these hold
          </div>
          <ul className="ml-4 list-disc space-y-0.5" style={{ color: "var(--text-secondary)" }}>
            {rules.exit.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <div className="mb-1 font-semibold" style={{ color: "var(--text-primary)" }}>
            Stop
          </div>
          <div style={{ color: "var(--text-secondary)" }}>{rules.stop}</div>
        </div>
        <div>
          <div className="mb-1 font-semibold" style={{ color: "var(--text-primary)" }}>
            Target
          </div>
          <div style={{ color: "var(--text-secondary)" }}>{rules.target}</div>
        </div>
      </div>
      <div style={{ color: "var(--text-muted)" }}>
        Needs {rules.warmupBars} bars of history before the first possible entry.
      </div>
    </div>
  );
}

export function NewStrategyDialog({
  open,
  onClose,
  onSaved,
  onDeleted,
}: {
  open: boolean;
  onClose: () => void;
  /** Called with the saved strategy's name so the Strategies tab can
   * refresh its leaderboard and select the new row. */
  onSaved: (name: string) => void;
  /** Refresh only -- deliberately does NOT select anything, since the row
   * that just disappeared is exactly what must not stay selected. */
  onDeleted: () => void;
}) {
  const [description, setDescription] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [draft, setDraft] = useState<StrategyDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [stored, setStored] = useState<CustomStrategyList | null>(null);

  useEffect(() => {
    if (!open) return;
    api.listCustomStrategies().then(setStored).catch(() => setStored(null));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const reset = () => {
    setDescription("");
    setDraft(null);
    setError(null);
  };

  const handleDraft = async () => {
    setDrafting(true);
    setError(null);
    setDraft(null);
    try {
      setDraft(await api.draftStrategy(description));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setDrafting(false);
    }
  };

  const handleSave = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const saved = await api.saveCustomStrategy(draft.spec, description);
      reset();
      onSaved(saved.name);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (entry: CustomStrategy) => {
    try {
      await api.deleteCustomStrategy(entry.name);
      setStored(await api.listCustomStrategies());
      onDeleted();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const unavailable = stored && !stored.authoringAvailable;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-strategy-title"
        className="max-h-[88vh] w-full max-w-3xl overflow-auto rounded-xl border shadow-2xl"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <div
          className="sticky top-0 z-10 flex items-start justify-between gap-4 border-b px-5 py-4"
          style={{ borderColor: "var(--gridline)", background: "var(--surface-1)" }}
        >
          <div>
            <h2 id="new-strategy-title" className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
              Describe a strategy
            </h2>
            <p className="mt-0.5 max-w-xl text-xs" style={{ color: "var(--text-muted)" }}>
              Write the rule in plain English. It's compiled into an entry/stop/target
              spec you review before anything is saved — and then backtested by the same
              engine, and judged by the same bar, as every registered strategy.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md border px-2 py-1 text-xs"
            style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
          >
            Close
          </button>
        </div>

        <div className="space-y-4 px-5 py-4">
          {unavailable && (
            <p
              className="rounded-md border px-3 py-2 text-xs"
              style={{ borderColor: "var(--status-warning)", color: "var(--status-warning)" }}
            >
              {stored?.authoringUnavailableReason}
            </p>
          )}

          <div>
            <label
              htmlFor="strategy-description"
              className="mb-1 block text-xs font-medium"
              style={{ color: "var(--text-primary)" }}
            >
              Strategy description
            </label>
            <textarea
              id="strategy-description"
              rows={4}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="e.g. Buy when RSI drops below 30 but price is above its 200-day average…"
              className="w-full rounded-md border px-3 py-2 text-sm"
              style={{
                borderColor: "var(--border)",
                background: "var(--page)",
                color: "var(--text-primary)",
              }}
            />
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <button
                type="button"
                disabled={drafting || !description.trim() || Boolean(unavailable)}
                onClick={handleDraft}
                className="rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-50"
                style={{ background: "var(--series-1)", color: "var(--page)" }}
              >
                {drafting ? "Compiling rules…" : "Compile rules"}
              </button>
              {draft && (
                <button
                  type="button"
                  onClick={reset}
                  className="rounded-md border px-3 py-1.5 text-xs"
                  style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
                >
                  Start over
                </button>
              )}
              {drafting && (
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                  This can take up to a minute.
                </span>
              )}
            </div>
          </div>

          {!draft && !drafting && (
            <div className="space-y-1">
              <div className="text-xs font-medium" style={{ color: "var(--text-primary)" }}>
                Examples
              </div>
              {EXAMPLES.map((example) => (
                <button
                  key={example}
                  type="button"
                  onClick={() => setDescription(example)}
                  className="block w-full rounded-md border px-3 py-2 text-left text-xs"
                  style={{
                    borderColor: "var(--gridline)",
                    color: "var(--text-secondary)",
                    background: "var(--page)",
                  }}
                >
                  {example}
                </button>
              ))}
              <p className="pt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                Rules can use price, volume, moving averages, RSI, ATR, MACD, Bollinger
                bands, rolling highs/lows, gaps and VWAP. Anything needing fundamentals,
                news, or cross-symbol ranking is declined rather than approximated.
              </p>
            </div>
          )}

          {error && (
            <p
              className="rounded-md border px-3 py-2 text-xs"
              style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
            >
              {error}
            </p>
          )}

          {draft && (
            <div
              className="space-y-3 rounded-lg border p-4"
              style={{ borderColor: "var(--border)", background: "var(--page)" }}
            >
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <div>
                  <div className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
                    {draft.name}
                  </div>
                  <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                    {draft.description}
                  </div>
                </div>
                <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                  {draft.kind} · {draft.timeframe === "5m" ? "5-minute bars" : "daily bars"} ·{" "}
                  {draft.direction}
                </span>
              </div>

              <RuleList rules={draft.rules} />

              {draft.params.length > 0 && (
                <div className="text-xs">
                  <div className="mb-1 font-semibold" style={{ color: "var(--text-primary)" }}>
                    Tunable parameters
                  </div>
                  <ul className="ml-4 list-disc space-y-0.5" style={{ color: "var(--text-secondary)" }}>
                    {draft.params.map((p) => (
                      <li key={p.name}>
                        {p.label}: {p.default} (range {p.minimum}–{p.maximum})
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1" style={{ color: "var(--text-muted)" }}>
                    These become sliders in the run configuration panel, so the strategy
                    can be swept without editing anything.
                  </p>
                </div>
              )}

              {draft.notes && (
                <p
                  className="rounded-md border px-3 py-2 text-xs"
                  style={{ borderColor: "var(--status-warning)", color: "var(--status-warning)" }}
                >
                  <span className="font-semibold">Interpretation notes:</span> {draft.notes}
                </p>
              )}

              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                These are the rules that will run — read them, not the description you
                typed. If they don't match what you meant, rewrite the description and
                compile again. Nothing has been backtested yet: saving only registers
                the strategy so you can run it.
                {draft.attempts > 1 &&
                  ` (The first ${draft.attempts - 1} attempt(s) failed validation and were repaired.)`}
              </p>

              {draft.nameConflict && (
                <p
                  className="rounded-md border px-3 py-2 text-xs"
                  style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
                >
                  {draft.nameConflict} Rewrite the description with a different name.
                </p>
              )}

              <button
                type="button"
                disabled={saving || Boolean(draft.nameConflict)}
                onClick={handleSave}
                className="rounded-md px-3 py-1.5 text-xs font-medium disabled:opacity-50"
                style={{ background: "var(--series-1)", color: "var(--page)" }}
              >
                {saving ? "Saving…" : "Save strategy"}
              </button>
            </div>
          )}

          {stored && (stored.strategies.length > 0 || stored.loadErrors.length > 0) && (
            <div className="border-t pt-3" style={{ borderColor: "var(--gridline)" }}>
              <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-primary)" }}>
                Your custom strategies
              </div>
              <ul className="space-y-1">
                {stored.strategies.map((entry) => (
                  <li
                    key={entry.name}
                    className="flex items-start justify-between gap-3 rounded-md border px-3 py-2"
                    style={{ borderColor: "var(--gridline)" }}
                  >
                    <div className="min-w-0">
                      <div className="text-xs font-medium" style={{ color: "var(--text-primary)" }}>
                        {entry.name}
                      </div>
                      <div className="truncate text-xs" style={{ color: "var(--text-muted)" }}>
                        “{entry.prompt || entry.description}”
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => handleDelete(entry)}
                      className="text-xs whitespace-nowrap underline-offset-2 hover:underline"
                      style={{ color: "var(--status-critical)" }}
                    >
                      Delete
                    </button>
                  </li>
                ))}
                {stored.loadErrors.map((broken) => (
                  <li
                    key={broken.filename}
                    className="flex items-start justify-between gap-3 rounded-md border px-3 py-2"
                    style={{ borderColor: "var(--status-critical)" }}
                  >
                    <div className="min-w-0">
                      <div className="text-xs font-medium" style={{ color: "var(--status-critical)" }}>
                        {broken.filename} — won't load
                      </div>
                      <div className="text-xs" style={{ color: "var(--text-muted)" }}>
                        {broken.error}
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={async () => {
                        await api.deleteBrokenCustomStrategy(broken.filename);
                        setStored(await api.listCustomStrategies());
                      }}
                      className="text-xs whitespace-nowrap underline-offset-2 hover:underline"
                      style={{ color: "var(--status-critical)" }}
                    >
                      Delete
                    </button>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs" style={{ color: "var(--text-muted)" }}>
                Deleting a strategy removes its definition only — the runs it already
                logged stay in the run history.
              </p>
            </div>
          )}
        </div>
      </section>
    </div>,
    document.body,
  );
}
