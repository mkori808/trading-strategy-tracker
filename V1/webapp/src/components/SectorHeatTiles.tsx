import type { SectorRotationRow } from "../api";
import { sectorName } from "../sectorNames";

// Wash intensity is fixed (not scaled by magnitude) -- RS values cluster
// tightly around 1.0 (see engine/market_overview.py:sector_rotation), so a
// magnitude-scaled wash would barely differ tile to tile. Sign (RS above/
// below 1, i.e. outperforming/lagging SPY) is what the color encodes; the
// number itself carries the magnitude.
export function SectorHeatTiles({ rows }: { rows: SectorRotationRow[] }) {
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 md:grid-cols-4">
      {rows.map((r) => {
        const outperforming = r.relativeStrength !== null && r.relativeStrength >= 1;
        const color =
          r.relativeStrength === null
            ? "var(--text-muted)"
            : outperforming
              ? "var(--status-good)"
              : "var(--status-critical)";
        const bg =
          r.relativeStrength === null
            ? "var(--surface-1)"
            : outperforming
              ? "var(--status-good-bg)"
              : "var(--status-critical-bg)";
        return (
          <div
            key={r.symbol}
            className="rounded-lg border px-3 py-2.5"
            style={{ borderColor: "var(--border)", background: bg }}
          >
            <div className="flex items-center justify-between gap-1">
              <span className="text-sm font-medium" style={{ color: "var(--text-primary)" }}>
                {sectorName(r.symbol)}
              </span>
              {r.rising !== null && (
                <span style={{ color }} aria-hidden="true">
                  {r.rising ? "▲" : "▼"}
                </span>
              )}
            </div>
            <div className="mt-1 text-lg font-semibold tabular-nums" style={{ color }}>
              {r.relativeStrength === null ? "—" : r.relativeStrength.toFixed(2)}
              <span className="ml-1 text-xs font-normal" style={{ color: "var(--text-muted)" }}>
                RS
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
