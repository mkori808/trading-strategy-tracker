import type { SectorPerformanceRow } from "../api";

function fmtPct(v: number | null): string {
  return v === null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function SectorPerformanceChart({ rows }: { rows: SectorPerformanceRow[] }) {
  const known = rows.filter((r) => r.changePct !== null);
  const maxAbs = Math.max(1e-6, ...known.map((r) => Math.abs(r.changePct as number)));

  return (
    <div
      className="rounded-lg border p-4"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <div className="mb-3 text-xs font-medium" style={{ color: "var(--text-muted)" }}>
        Sector day change (SPDR ETFs + SPY)
      </div>
      <div className="space-y-1.5">
        {rows.map((r) => {
          const pct = r.changePct;
          const isKnown = pct !== null;
          const positive = isKnown && (pct as number) >= 0;
          const widthPct = isKnown ? (Math.abs(pct as number) / maxAbs) * 50 : 0;
          return (
            <div key={r.symbol} className="flex items-center gap-2 text-xs">
              <div
                className="w-12 shrink-0 text-right font-medium tabular-nums"
                style={{ color: "var(--text-secondary)" }}
              >
                {r.symbol}
              </div>
              <div className="relative h-4 flex-1" style={{ background: "transparent" }}>
                {/* Zero baseline, centered */}
                <div
                  className="absolute top-0 bottom-0 left-1/2 w-px"
                  style={{ background: "var(--baseline)" }}
                />
                {isKnown && (
                  <div
                    className="absolute top-0.5 bottom-0.5 rounded-sm"
                    style={{
                      left: positive ? "50%" : `${50 - widthPct}%`,
                      width: `${widthPct}%`,
                      background: positive ? "var(--status-good)" : "var(--status-critical)",
                    }}
                  />
                )}
              </div>
              <div
                className="w-16 shrink-0 tabular-nums"
                style={{
                  color: !isKnown
                    ? "var(--text-muted)"
                    : positive
                      ? "var(--status-good)"
                      : "var(--status-critical)",
                }}
              >
                {fmtPct(pct)}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
