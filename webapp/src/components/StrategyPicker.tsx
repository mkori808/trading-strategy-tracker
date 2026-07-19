import type { StrategySummary } from "../api";
import { StatusPill } from "./StatusPill";

function fmtR(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(3)}`;
}

/** Slim sidebar list for the Lab tab -- name, status, expectancy, click to
 * configure. The full leaderboard (every metric, sortable by eye) lives in
 * the Compare tab via StrategyTable; this is deliberately narrower so it
 * fits beside the run-configuration panel. */
export function StrategyPicker({
  strategies,
  selected,
  onSelect,
}: {
  strategies: StrategySummary[];
  selected: string | null;
  onSelect: (name: string) => void;
}) {
  return (
    <div
      className="overflow-hidden rounded-lg border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <ul className="max-h-[70vh] divide-y overflow-y-auto" style={{ borderColor: "var(--gridline)" }}>
        {strategies.map((s) => {
          const isSelected = selected === s.name;
          return (
            <li key={s.name}>
              <button
                type="button"
                onClick={() => onSelect(s.name)}
                className="flex w-full flex-col gap-1 px-3 py-2.5 text-left transition-colors"
                style={{
                  background: isSelected ? "var(--series-1-wash)" : undefined,
                  borderLeft: isSelected
                    ? "2px solid var(--series-1)"
                    : "2px solid transparent",
                }}
              >
                <span
                  className="text-sm font-medium"
                  style={{ color: "var(--text-primary)" }}
                >
                  {s.name}
                </span>
                <span className="flex items-center justify-between gap-2">
                  <StatusPill status={s.status} />
                  <span
                    className="text-xs tabular-nums"
                    style={{
                      color:
                        s.expectancyR === null
                          ? "var(--text-muted)"
                          : s.expectancyR >= 0
                            ? "var(--status-good)"
                            : "var(--status-critical)",
                    }}
                  >
                    {fmtR(s.expectancyR)}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
