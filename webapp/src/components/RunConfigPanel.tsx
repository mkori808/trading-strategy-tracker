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
const UNIVERSE_CATEGORY_ORDER = ["US markets", "S&P indexes", "Crypto", "Futures", "International"];

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
          Experimental sampling source
        </span>
        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
          {CAP_TIERS.find((item) => item.key === tier)?.label} pool: {poolSize}
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

  if (spec.kind === "str" && spec.choices) {
    return (
      <label className="flex items-center justify-between gap-3 text-sm">
        <span style={{ color: "var(--text-secondary)" }} title={spec.help ?? undefined}>
          {spec.label}
        </span>
        <select
          value={String(value)}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border px-2 py-1 text-sm"
          style={{ borderColor: "var(--border)", background: "var(--page)", color: "var(--text-primary)" }}
        >
          {spec.choices.map((choice) => (
            <option key={choice} value={choice}>
              {choice}
            </option>
          ))}
        </select>
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
  const [universeId, setUniverseId] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [params, setParams] = useState<Record<string, number | boolean | string>>({});
  const [pools, setPools] = useState<CapTierPools | null>(null);

  useEffect(() => {
    api.universePools().then(setPools).catch(() => {});
  }, []);

  const resetToDefaults = (s: ParamSchema) => {
    setSymbols(s.symbolsDefault);
    setUniverseId(s.universeDefault ?? "");
    setStart(s.startDefault);
    setEnd(s.endDefault);
    setParams(Object.fromEntries(s.params.map((p) => [p.name, p.default])));
  };

  // Params-only reset, independent of the full reset above -- lets a user
  // revert a parameter sweep without losing a symbol/date override they
  // want to keep, and lives next to the Parameters section itself rather
  // than only under Symbols, where it's easy to miss if that's not what
  // was changed.
  const resetParamsToDefaults = (s: ParamSchema) => {
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
          if (initialOverrides.universeId) {
            setUniverseId(initialOverrides.universeId);
            const universe = s.universes.find((item) => item.id === initialOverrides.universeId);
            if (universe?.symbols.length) setSymbols(universe.symbols);
          }
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
  const universeChanged = universeId !== (schema.universeDefault ?? "");
  const isCustom = symbolsChanged || datesChanged || paramsChanged || universeChanged;
  const universeGroups = schema.universes.reduce<Record<string, typeof schema.universes>>(
    (groups, universe) => {
      (groups[universe.category] ??= []).push(universe);
      return groups;
    },
    {},
  );
  const selectedUniverse = schema.universes.find((item) => item.id === universeId);
  const universeRunBlocked = Boolean(selectedUniverse && !selectedUniverse.runnable);
  const visibleParams = schema.params.filter((spec) => (
    !spec.name.startsWith("pit_") || selectedUniverse?.membershipMode === "dynamic_pit_security_master"
  ));
  const pitDatesOutsideCoverage = Boolean(
    selectedUniverse?.membershipMode === "dynamic_pit_security_master"
    && ((selectedUniverse.coverageStart && start < selectedUniverse.coverageStart)
      || (selectedUniverse.coverageEnd && end > selectedUniverse.coverageEnd))
  );

  const handleRun = () => {
    const overrides: BacktestOverrides = {};
    if (universeId) overrides.universeId = universeId;
    else if (symbolsChanged) overrides.symbols = symbols;
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
            Market universe
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
        <select
          value={universeId}
          disabled={!schema.symbolOverrideAllowed}
          onChange={(event) => {
            const next = event.target.value;
            setUniverseId(next);
            if (!next) {
              setSymbols(schema.symbolsDefault);
              return;
            }
            const universe = schema.universes.find((item) => item.id === next);
            if (universe?.symbols.length) setSymbols(universe.symbols);
            else if (universe?.membershipMode === "dynamic_pit_security_master") setSymbols([]);
            if (universe?.membershipMode === "dynamic_pit_security_master") {
              if (universe.coverageStart) setStart(universe.coverageStart);
              if (universe.coverageEnd) setEnd(universe.coverageEnd);
            }
          }}
          className="mb-2 w-full rounded-md border px-2 py-1.5 text-sm"
          style={{ borderColor: "var(--border)", background: "var(--page)", color: "var(--text-primary)" }}
        >
          <option value="">Strategy default</option>
          {Object.entries(universeGroups)
            .sort(([a], [b]) => {
              const ai = UNIVERSE_CATEGORY_ORDER.indexOf(a);
              const bi = UNIVERSE_CATEGORY_ORDER.indexOf(b);
              return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi) || a.localeCompare(b);
            })
            .map(([category, universes]) => (
            <optgroup key={category} label={category}>
              {universes.map((universe) => (
                <option key={universe.id} value={universe.id}>
                  {universe.label}{universe.runnable ? "" : " — data required"}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {universeId && (() => {
          const universe = schema.universes.find((item) => item.id === universeId);
          return universe ? (
            <div className="mb-2 rounded-md border px-2.5 py-2 text-xs" style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}>
              <div>{universe.description}</div>
              <div className="mt-1" style={{ color: "var(--text-muted)" }}>
                {universe.assetClass} · benchmark {universe.primaryBenchmark ?? "N/A"} · {universe.symbols.length || universe.approximateSecurityCount || "dynamic"} instruments
              </div>
              {universe.unavailableReason && <div style={{ color: "var(--status-warning)" }}>{universe.unavailableReason}</div>}
              {universe.pitStatus && (
                <div className="mt-2 space-y-1 border-t pt-2" style={{ borderColor: "var(--border)" }}>
                  <div><strong>PIT integrity:</strong> {universe.pitStatus.ready ? "Validated" : "Blocked"}</div>
                  <div>Coverage: {universe.pitStatus.coverageStart ?? universe.coverageStart ?? "unknown"} to {universe.pitStatus.coverageEnd ?? universe.coverageEnd ?? "unknown"}</div>
                  <div>Delisted securities: {universe.pitStatus.delistedCount === null ? "not verified" : universe.pitStatus.delistedCount}</div>
                  <div>Security master: {universe.pitStatus.source ?? "not installed"}</div>
                  {universe.pitStatus.missingArtifacts.length > 0 && (
                    <div style={{ color: "var(--status-critical)" }}>
                      Missing: {universe.pitStatus.missingArtifacts.join(", ")}
                    </div>
                  )}
                  {universe.pitStatus.invalidReasons.length > 0 && (
                    <div style={{ color: "var(--status-critical)" }}>
                      Invalid: {universe.pitStatus.invalidReasons.join("; ")}
                    </div>
                  )}
                </div>
              )}
              {universe.membershipMode === "full_current_constituents_static_history" && (
                <div className="mt-2" style={{ color: "var(--status-critical)" }}>
                  Current constituents applied historically. Results are survivorship-biased and cannot validate an edge.
                </div>
              )}
            </div>
          ) : null;
        })()}
        <div className="mb-2 text-xs" style={{ color: "var(--text-secondary)" }}>
          Active universe: <strong>{selectedUniverse?.label || (symbolsChanged ? "Custom symbols" : "Strategy default")} — {selectedUniverse?.membershipMode === "dynamic_pit_security_master" ? (selectedUniverse.approximateSecurityCount ?? "dynamic") : symbols.length} securities</strong>
        </div>
        {schema.timing ? (
          <div className="mb-2 rounded-md border px-2.5 py-2 text-xs" style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}>
            <strong>Execution timing:</strong> {schema.timing.informationAvailability} evidence → {schema.timing.execution.replace(/_/g, " ")}
            <div className="mt-1" style={{ color: "var(--text-muted)" }}>
              Engine: {schema.timing.engine}; current close used as evidence: {schema.timing.usesCurrentClose ? "yes" : "no"}.
            </div>
            {schema.timing.exceptionReason && (
              <div className="mt-1" style={{ color: "var(--status-warning)" }}>
                Explicit timing exception: {schema.timing.exceptionReason}
              </div>
            )}
          </div>
        ) : (
          <div
            className="mb-2 rounded-md border px-2.5 py-2 text-xs"
            style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
          >
            <strong>Execution timing unavailable.</strong> Restart the API server before running this strategy; the UI will not infer a timing contract.
          </div>
        )}
        {schema.symbolOverrideAllowed ? (
          <>
            {!universeId && pools && <CapTierSampler pools={pools} onSample={setSymbols} />}
            {selectedUniverse?.membershipMode === "dynamic_pit_security_master" ? (
              <p className="text-xs" style={{ color: "var(--text-muted)" }}>
                Permanent security IDs and the eligible roster are loaded from the point-in-time security master at each rebalance; no present-day ticker list is substituted.
              </p>
            ) : (
              <SymbolChips
                symbols={universeId && symbols.length > 30 ? symbols.slice(0, 30) : symbols}
                editable={!universeId}
                onChange={setSymbols}
              />
            )}
            {universeId && symbols.length > 30 && (
              <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
                Showing the first 30 symbols; all {symbols.length} are included in the run.
              </p>
            )}
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
            min={selectedUniverse?.membershipMode === "dynamic_pit_security_master" ? selectedUniverse.coverageStart ?? undefined : undefined}
            max={selectedUniverse?.membershipMode === "dynamic_pit_security_master" ? (selectedUniverse.coverageEnd ?? end) || undefined : end || undefined}
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
            min={start || (selectedUniverse?.membershipMode === "dynamic_pit_security_master" ? selectedUniverse.coverageStart ?? undefined : undefined)}
            max={selectedUniverse?.membershipMode === "dynamic_pit_security_master" ? selectedUniverse.coverageEnd ?? undefined : undefined}
            onChange={(e) => setEnd(e.target.value)}
            className="rounded-md border px-2 py-1.5 text-sm"
            style={{ borderColor: "var(--border)", background: "var(--page)", color: "var(--text-primary)" }}
          />
        </label>
      </div>
      {pitDatesOutsideCoverage && (
        <div className="rounded-md border px-3 py-2 text-xs" style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}>
          Requested dates fall outside the validated point-in-time dataset coverage.
        </div>
      )}

      {visibleParams.length > 0 && (
        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>
              Parameters
            </span>
            {paramsChanged && (
              <button
                type="button"
                onClick={() => resetParamsToDefaults(schema)}
                className="text-xs underline"
                style={{ color: "var(--text-muted)" }}
              >
                Reset to defaults
              </button>
            )}
          </div>
          <div className="space-y-3 rounded-lg border p-3" style={{ borderColor: "var(--border)" }}>
            {visibleParams.map((spec) => (
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
          <strong>Run variation</strong> — the universe, dates, symbols, or parameters differ
          from this strategy's default. The selected universe is recorded with the run.
        </div>
      )}

      {schema.implementationStatus === "unavailable" && (
        <div className="rounded-lg border px-3 py-2 text-xs" style={{ borderColor: "var(--status-warning)", color: "var(--status-warning)" }}>
          <strong>Unavailable by research-integrity rule.</strong> {schema.unavailableReason}
        </div>
      )}

      <button
        type="button"
        onClick={handleRun}
        disabled={running || !schema.timing || universeRunBlocked || pitDatesOutsideCoverage || schema.implementationStatus === "unavailable"}
        className="w-full rounded-md px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-50"
        style={{ background: "var(--series-1)" }}
      >
        {running ? "Running validation suite…" : !schema.timing ? "Restart API server" : schema.implementationStatus === "unavailable" ? "Unavailable" : universeRunBlocked ? "PIT dataset required" : isCustom ? "Run this variation" : "Run Backtest"}
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
