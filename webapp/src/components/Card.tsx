import type { ReactNode } from "react";

/** Skeleton rows, so a loading card holds its own height instead of
 * collapsing and shoving every card below it up the page. */
export function CardSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-hidden="true">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-3 animate-pulse rounded"
          style={{ background: "var(--gridline)", width: `${100 - i * 12}%` }}
        />
      ))}
    </div>
  );
}

/** One tile on the dashboard: a compact summary that opens the full view in
 * a popup. The ENTIRE card is the button -- a small "expand" affordance in
 * the corner alone is a smaller target and gives no hint that the body is
 * clickable too. That constrains card bodies to read-only content (numbers,
 * short lists); anything interactive belongs in the popup, which is where
 * every control in this app now lives.
 *
 * `error` renders in place of the body rather than replacing the card: a
 * failed screener fetch shouldn't make the tile vanish and reflow the grid.
 * The popup stays reachable so the user can read the full error there. */
export function Card({
  title,
  meta,
  onOpen,
  openLabel,
  loading = false,
  error = null,
  skeletonRows,
  children,
}: {
  title: string;
  /** Right-aligned status text in the header -- a timestamp, a count. */
  meta?: ReactNode;
  onOpen: () => void;
  /** Accessible name for the action, e.g. "Open market state". Defaults to
   * the title, which is usually enough. */
  openLabel?: string;
  loading?: boolean;
  error?: string | null;
  skeletonRows?: number;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={openLabel ?? `Open ${title}`}
      className="group flex w-full flex-col rounded-xl border p-4 text-left transition-shadow hover:shadow-md focus-visible:outline-2 focus-visible:outline-offset-2"
      style={{
        borderColor: "var(--border)",
        background: "var(--surface-1)",
        outlineColor: "var(--series-1)",
      }}
    >
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <span
          className="text-[11px] font-semibold tracking-wide"
          style={{ color: "var(--text-muted)" }}
        >
          {title.toUpperCase()}
        </span>
        <span className="flex items-center gap-2">
          {meta && (
            <span className="text-[11px]" style={{ color: "var(--text-muted)" }}>
              {meta}
            </span>
          )}
          <span
            aria-hidden="true"
            className="text-xs opacity-40 transition-opacity group-hover:opacity-100"
            style={{ color: "var(--series-1)" }}
          >
            ↗
          </span>
        </span>
      </div>

      <div className="flex-1">
        {error ? (
          <div className="text-xs" style={{ color: "var(--status-critical)" }}>
            {error}
          </div>
        ) : loading ? (
          <CardSkeleton rows={skeletonRows} />
        ) : (
          children
        )}
      </div>
    </button>
  );
}

/** A label/value line inside a card body. */
export function CardRow({
  label,
  value,
  valueColor,
}: {
  label: ReactNode;
  value: ReactNode;
  valueColor?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 text-sm">
      <span className="truncate" style={{ color: "var(--text-secondary)" }}>
        {label}
      </span>
      <span
        className="shrink-0 tabular-nums"
        style={{ color: valueColor ?? "var(--text-primary)" }}
      >
        {value}
      </span>
    </div>
  );
}

/** The one big number a card leads with. */
export function CardHeadline({
  value,
  caption,
  valueColor,
}: {
  value: ReactNode;
  caption?: ReactNode;
  valueColor?: string;
}) {
  return (
    <div className="mb-2">
      <div
        className="text-2xl font-semibold tabular-nums"
        style={{ color: valueColor ?? "var(--text-primary)" }}
      >
        {value}
      </div>
      {caption && (
        <div className="mt-0.5 text-xs" style={{ color: "var(--text-muted)" }}>
          {caption}
        </div>
      )}
    </div>
  );
}

/** Placeholder for a card body with nothing to show yet. */
export function CardEmpty({ children }: { children: ReactNode }) {
  return (
    <div className="text-xs" style={{ color: "var(--text-muted)" }}>
      {children}
    </div>
  );
}
