/** Formatters shared by the dashboard shell (status strip + cards).
 *
 * The pre-existing views each carry their own local copies of these; those
 * are deliberately left alone rather than swept into this module, since
 * several differ in ways that matter (the insider table's money formatter
 * rounds BEFORE choosing a unit, so 999,600 reads "$1.0M" rather than a
 * "$1000K" that looks like it skipped a unit -- preserved in
 * fmtCompactMoney below). This file is for the new shell, not a
 * migration. */

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function fmtMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

/** Compact dollars for dense lists: $1.2M, $840K. Rounds before picking the
 * unit, per the note above. */
export function fmtCompactMoney(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  if (v >= 999_500) return `$${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

export function fmtRelative(at: Date | number | string | null | undefined): string {
  if (at === null || at === undefined) return "never";
  const ms = Date.now() - new Date(at).getTime();
  if (Number.isNaN(ms)) return "—";
  const mins = Math.round(ms / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** Green for up, red for down, muted for unknown -- the one place the
 * direction-to-color mapping is decided for the shell. */
export function changeColor(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "var(--text-muted)";
  return v >= 0 ? "var(--status-good)" : "var(--status-critical)";
}

export const REGIME_COLOR: Record<string, string> = {
  Bullish: "var(--status-good)",
  Neutral: "var(--status-warning)",
  Bearish: "var(--status-critical)",
};
