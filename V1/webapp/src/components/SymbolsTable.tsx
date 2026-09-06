import type { Quote, SymbolMeta } from "../api";

function UniverseTags({ tags }: { tags: string[] }) {
  return (
    <span className="flex flex-wrap gap-1">
      {tags.map((t) => (
        <span
          key={t}
          className="rounded px-1.5 py-0.5 text-xs"
          style={{ background: "var(--series-1-wash)", color: "var(--series-1)" }}
        >
          {t}
        </span>
      ))}
    </span>
  );
}

function fmtDollarVol(v: number | null): string {
  if (v === null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(0)}`;
}

function ChangeCell({ v }: { v: number | null }) {
  if (v === null) return <span style={{ color: "var(--text-muted)" }}>—</span>;
  const color = v >= 0 ? "var(--status-good)" : "var(--status-critical)";
  return (
    <span className="tabular-nums" style={{ color }}>
      {v >= 0 ? "+" : ""}
      {v.toFixed(2)}%
    </span>
  );
}

function QuoteCell({ quote }: { quote: Quote | undefined }) {
  if (!quote) return <span style={{ color: "var(--text-muted)" }}>…</span>;
  if (quote.source === "unavailable" || quote.price === undefined) {
    return <span style={{ color: "var(--text-muted)" }}>—</span>;
  }
  return (
    <span className="tabular-nums" style={{ color: "var(--text-primary)" }}>
      ${quote.price.toFixed(2)}
    </span>
  );
}

export function SymbolsTable({
  symbols,
  quotes,
  onSelect,
}: {
  symbols: SymbolMeta[];
  quotes: Record<string, Quote>;
  onSelect: (ticker: string) => void;
}) {
  return (
    <div
      className="overflow-x-auto rounded-lg border"
      style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
    >
      <table className="w-full min-w-[820px] border-collapse text-sm">
        <thead>
          <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
            {["Symbol", "Universe", "Live (delayed)", "Last close", "Day change", "Avg $ vol (60d)", "Liquidity"].map(
              (h) => (
                <th
                  key={h}
                  className="px-4 py-3 text-left font-medium whitespace-nowrap"
                  style={{ color: "var(--text-muted)" }}
                >
                  {h}
                </th>
              ),
            )}
          </tr>
        </thead>
        <tbody>
          {symbols.map((s) => (
            <tr
              key={s.symbol}
              onClick={() => onSelect(s.symbol)}
              className="cursor-pointer transition-colors hover:opacity-90"
              style={{ borderBottom: "1px solid var(--gridline)" }}
            >
              <td className="px-4 py-3 font-medium" style={{ color: "var(--text-primary)" }}>
                {s.symbol}
              </td>
              <td className="px-4 py-3">
                <UniverseTags tags={s.universes} />
              </td>
              <td className="px-4 py-3">
                <QuoteCell quote={quotes[s.symbol]} />
              </td>
              <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                {s.lastClose === null ? "—" : `$${s.lastClose.toFixed(2)}`}
              </td>
              <td className="px-4 py-3">
                <ChangeCell v={s.changePct} />
              </td>
              <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                {fmtDollarVol(s.avgDollarVolume)}
              </td>
              <td className="px-4 py-3" style={{ color: "var(--text-secondary)" }}>
                {s.liquidityTier}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
