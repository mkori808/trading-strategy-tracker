import { useEffect, useState } from "react";
import { api, type BacktestOverrides, type CapTierPools, type ParamSchema, type ParamSpec } from "../api";

const CHIP_STYLE = {
  borderColor: "var(--border)",
  background: "var(--page)",
  color: "var(--text-primary)",
};

const CAP_TIERS = [
  { key: "small", label: "Small cap" },
  { key: "mid", label: "Mid cap" },
  { key: "large", label: "Large cap" },
] as const;

type CapTier = (typeof CAP_TIERS)[number]["key"];

const DEFAULT_SAMPLE_SIZE = 15;

function randomSample(pool: string[], n: number): string[] {
  const shuffled = [...pool];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  return shuffled.slice(0, Math.min(n, shuffled.length));
}

function CapTierSampler({
  pools,
  onSample,
}: {
  pools: CapTierPools;
  onSample: (symbols: string[]) => void;
}) {
  const [tier, setTier] = useState<CapTier>("large");
  const [sampleSize, setSampleSize] = useState(DEFAULT_SAMPLE_SIZE);
  const poolSize = pools[tier].length;

  return (
    <div
      className="mb-2 space-y-2 rounded-lg border p-2.5"
      style={{ borderColor: "var(--border)", background: "var(--page)" }}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
          Random sample by market cap
        </span>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          pool: {poolSize}
        </span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex overflow-hidden rounded-md border" style={{ borderColor: "var(--border)" }}>
          {CAP_TIERS.map(({ key, label }) => (
            <button
              key={key}
              type="button"
              onClick={() => setTier(key)}
              className="px-2.5 py-1 text-xs font-medium"
              style={{
                background: tier === key ? "var(--series-1)" : "var(--surface-1)",
                color: tier === key ? "#fff" : "var(--text-secondary)",
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <input
          type="number"
          value={sampleSize}
          min={1}
          max={poolSize}
          onChange={(e) => {
            const n = e.target.valueAsNumber;
            if (!Number.isNaN(n)) setSampleSize(Math.max(1, Math.min(poolSize, Math.round(n))));
          }}
          className="w-14 rounded-md border px-2 py-1 text-right text-xs tabular-nums"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-primary)" }}
        />
        <button
          type="button"
          onClick={() => onSample(randomSample(pools[tier], sampleSize))}
          className="rounded-md px-2.5 py-1 text-xs font-medium text-white"
          style={{ background: "var(--series-1)" }}
        >
          Draw random sample
        </button>
      </div>
      <p className="text-xs" style={{ color: "var(--text-muted)" }}>
        Replaces the symbol list below with {sampleSize} random tickers from the {tier}-cap pool.
        Each draw is a fresh random subset -- this is a Lab experiment, not the canonical universe.
      </p>
    </div>
  );
}

function SymbolChips({
  symbols,
  editable,
  onChange,
}: {
  symbols: string[];
  editable: boolean;
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");

  const addFromDraft = () => {
    const ticker = draft.trim().toUpperCase();
    if (!ticker) return;
    if (!symbols.includes(ticker)) onChange([...symbols, ticker]);
    setDraft("");
  };

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {symbols.map((sym) => (
        <span
          key={sym}
          className="inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium"
          style={CHIP_STYLE}
        >
          {sym}
          {editable && (
            <button
              type="button"
              onClick={() => onChange(symbols.filter((s) => s !== sym))}
              aria-label={`Remove ${sym}`}
              className="opacity-60 transition-opacity hover:opacity-100"
            >
              ×
            </button>
          )}
        </span>
      ))}
      {editable && (
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value.toUpperCase())}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              addFromDraft();
            }
          }}
          onBlur={addFromDraft}
          placeholder="+ ticker"
          className="w-20 rounded-full border px-2.5 py-1 text-xs outline-none"
          style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-primary)" }}
        />
      )}
    </div>
  );
}

function ParamControl({
  spec,
  value,
  onChange,
}: {
  spec: ParamSpec;
  value: number | boolean | string;
  onChange: (next: number | boolean | string) => void;
}) {
  if (spec.kind === "bool") {
    return (
      <label className="flex items-center justify-between gap-3 text-sm">
        <span style={{ color: "var(--text-secondary)" }}>{spec.label}</span>
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
      </label>
    );
  }

  if (spec.kind === "str") {
    return (
      <label className="flex flex-col gap-1 text-sm">
        <span style={{ color: "var(--text-secondary)" }}>{spec.label}</span>
        <input
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border px-2 py-1 text-sm"
          style={{ borderColor: "var(--border)", background: "var(--page)", color: "var(--text-primary)" }}
        />
      </label>
    );
  }

  // int / float
  const numeric = Number(value);
  return (
    <div className="flex flex-col gap-1 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span style={{ color: "var(--text-secondary)" }} title={spec.help ?? undefined}>
          {spec.label}
        </span>
        <input
          type="number"
          value={numeric}
          min={spec.minimum ?? undefined}
          max={spec.maximum ?? undefined}
          step={spec.step ?? (spec.kind === "int" ? 1 : "any")}
          onChange={(e) => {
            const n = e.target.valueAsNumber;
            if (!Number.isNaN(n)) onChange(spec.kind === "int" ? Math.round(n) : n);
          }}
          className="w-20 rounded-md border px-2 py-1 text-right tabular-nums"
          style={{ borderColor: "var(--border)", background: "var(--page)", color: "var(--text-primary)" }}
        />
      </div>
      {spec.minimum !== null && spec.maximum !== null && (
        <input
          type="range"
          value={numeric}
          min={spec.minimum}
          max={spec.maximum}
          step={spec.step ?? (spec.kind === "int" ? 1 : 0.01)}
          onChange={(e) => {
            const n = Number(e.target.value);
            onChange(spec.kind === "int" ? Math.round(n) : n);
          }}
          className="w-full accent-current"
          style={{ color: "var(--series-1)" }}
        />
      )}
    </div>
  );
}

export function RunConfigPanel({
  strategyName,
  running,
  runError,
  onRun,
  initialOverrides,
}: {
  strategyName: string;
  running: boolean;
  runError: string | null;
  onRun: (overrides: BacktestOverrides) => void;
  /** Pre-fill from a past experiment's config (see ResultTabs' History tab
   * "click a row to reload"). Only read on mount -- pass a changing `key`
   * on this component from the parent to force a remount when replaying a
   * different experiment, the standard React reset-via-remount pattern. */
  initialOverrides?: BacktestOverrides;
}) {
  const [schema, setSchema] = useState<ParamSchema | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [symbols, setSymbols] = useState<string[]>([]);
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [params, setParams] = useState<Record<string, number | boolean | string>>({});
  const [pools, setPools] = useState<CapTierPools | null>(null);

  useEffect(() => {
    api.universePools().then(setPools).catch(() => {});
  }, []);

  const resetToDefaults = (s: ParamSchema) => {
    setSymbols(s.symbolsDefault);
    setStart(s.startDefault);
    setEnd(s.endDefault);
    setParams(Object.fromEntries(s.params.map((p) => [p.name, p.default])));
  };

  useEffect(() => {
    setSchema(null);
    setLoadError(null);
    api
      .paramSchema(strategyName)
      .then((s) => {
        setSchema(s);
        resetToDefaults(s);
        if (initialOverrides) {
          if (initialOverrides.symbols?.length) setSymbols(initialOverrides.symbols);
          if (initialOverrides.start) setStart(initialOverrides.start);
          if (initialOverrides.end) setEnd(initialOverrides.end);
          if (initialOverrides.params) {
            setParams((prev) => ({ ...prev, ...initialOverrides.params }));
          }
        }
      })
      .catch((e) => setLoadError(String(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyName]);

  if (loadError) {
    return (
      <div
        className="rounded-lg border px-4 py-3 text-sm"
        style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
      >
        Couldn't load configuration for this strategy: {loadError}
      </div>
    );
  }

  if (!schema) {
    return (
      <div className="text-sm" style={{ color: "var(--text-muted)" }}>
        Loading configuration…
      </div>
    );
  }

  const symbolsChanged = JSON.stringify([...symbols].sort()) !== JSON.stringify([...schema.symbolsDefault].sort());
  const datesChanged = start !== schema.startDefault || end !== schema.endDefault;
  const paramsChanged = schema.params.some((p) => params[p.name] !== p.default);
  const isCustom = symbolsChanged || datesChanged || paramsChanged;

  const handleRun = () => {
    const overrides: BacktestOverrides = {};
    if (symbolsChanged) overrides.symbols = symbols;
    if (start !== schema.startDefault) overrides.start = start;
    if (end !== schema.endDefault) overrides.end = end;
    if (paramsChanged) {
      overrides.params = Object.fromEntries(
        schema.params.filter((p) => params[p.name] !== p.default).map((p) => [p.name, params[p.name]]),
      );
    }
    onRun(overrides);
  };

  return (
    <div className="space-y-4">
      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
            Symbols
          </span>
          {isCustom && (
            <button
              type="button"
              onClick={() => resetToDefaults(schema)}
              className="text-xs underline"
              style={{ color: "var(--text-muted)" }}
            >
              Reset to defaults
            </button>
          )}
        </div>
        {schema.symbolOverrideAllowed ? (
          <>
            {pools && <CapTierSampler pools={pools} onSample={setSymbols} />}
            <SymbolChips symbols={symbols} editable onChange={setSymbols} />
          </>
        ) : (
          <>
            <SymbolChips symbols={symbols} editable={false} onChange={setSymbols} />
            <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
              This strategy's universe is structural (ranked against SPY) and can't be
              overridden.
            </p>
          </>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1 text-xs">
          <span style={{ color: "var(--text-muted)" }}>Start</span>
          <input
            type="date"
            value={start}
            max={end || undefined}
            onChange={(e) => setStart(e.target.value)}
            className="rounded-md border px-2 py-1.5 text-sm"
            style={{ borderColor: "var(--border)", background: "var(--page)", color: "var(--text-primary)" }}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs">
          <span style={{ color: "var(--text-muted)" }}>End</span>
          <input
            type="date"
            value={end}
            min={start || undefined}
            onChange={(e) => setEnd(e.target.value)}
            className="rounded-md border px-2 py-1.5 text-sm"
            style={{ borderColor: "var(--border)", background: "var(--page)", color: "var(--text-primary)" }}
          />
        </label>
      </div>

      {schema.params.length > 0 && (
        <div>
          <div className="mb-2 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
            Parameters
          </div>
          <div className="space-y-3 rounded-lg border p-3" style={{ borderColor: "var(--border)" }}>
            {schema.params.map((spec) => (
              <ParamControl
                key={spec.name}
                spec={spec}
                value={params[spec.name]}
                onChange={(next) => setParams((prev) => ({ ...prev, [spec.name]: next }))}
              />
            ))}
          </div>
        </div>
      )}

      {isCustom && (
        <div
          className="rounded-lg border px-3 py-2 text-xs"
          style={{
            borderColor: "var(--status-warning)",
            background: "var(--status-warning-bg)",
            color: "var(--text-primary)",
          }}
        >
          <strong>Custom configuration</strong> — not the pre-registered universe/window.
          Results here are exploratory, not a replacement for the canonical backtest.
        </div>
      )}

      <button
        type="button"
        onClick={handleRun}
        disabled={running}
        className="w-full rounded-md px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-50"
        style={{ background: "var(--series-1)" }}
      >
        {running ? "Running…" : isCustom ? "Run this variation" : "Run Backtest"}
      </button>

      {runError && (
        <div
          className="rounded-lg border px-3 py-2 text-xs"
          style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
        >
          {runError}
        </div>
      )}
    </div>
  );
}
