import { useEffect, useMemo, useState } from "react";
import { api, type ScreenerResponse, type ScreenerRow } from "../api";

type SortKey =
  | "symbol"
  | "price"
  | "compositeScore"
  | "valuationScore"
  | "qualityScore"
  | "growthMomentumScore"
  | "riskScore"
  | "trailingPe"
  | "analystRating"
  | "analystTargetPrice"
  | "upsidePct";

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: "symbol", label: "Symbol" },
  { key: "price", label: "Price" },
  { key: "compositeScore", label: "Composite" },
  { key: "valuationScore", label: "Valuation" },
  { key: "qualityScore", label: "Quality" },
  { key: "growthMomentumScore", label: "Growth / Momentum" },
  { key: "riskScore", label: "Risk" },
  { key: "trailingPe", label: "P/E" },
  { key: "analystRating", label: "Analyst rating" },
  { key: "analystTargetPrice", label: "Analyst target" },
  { key: "upsidePct", label: "Upside" },
];

// 1 (Strong Buy) - 5 (Sell) on yfinance's recommendationMean scale -- lower
// is more bullish, the opposite direction of every score column above, so
// it's never treated as a "higher is better" score.
function fmtAnalystRating(v: number | null): string {
  return v === null ? "—" : v.toFixed(2);
}

function scoreColor(v: number | null, invert = false): string | undefined {
  if (v === null) return "var(--text-muted)";
  const good = invert ? v <= 40 : v >= 60;
  const bad = invert ? v >= 60 : v <= 40;
  if (good) return "var(--status-good)";
  if (bad) return "var(--status-critical)";
  return "var(--text-secondary)";
}

function ScoreCell({ v, invert = false }: { v: number | null; invert?: boolean }) {
  return (
    <span className="tabular-nums" style={{ color: scoreColor(v, invert) }}>
      {v === null ? "—" : v.toFixed(0)}
    </span>
  );
}

function fmtPrice(v: number | null): string {
  return v === null ? "—" : `$${v.toFixed(2)}`;
}

function fmtPe(v: number | null): string {
  return v === null || v <= 0 ? "—" : v.toFixed(1);
}

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`;
}

export function ScreenerView() {
  const [data, setData] = useState<ScreenerResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("compositeScore");
  const [sortAsc, setSortAsc] = useState(false);

  const load = () => {
    setLoadError(null);
    api
      .screener()
      .then(setData)
      .catch((e) => setLoadError(String(e)));
  };

  useEffect(load, []);

  const rows = useMemo(() => {
    if (!data) return [];
    // Nulls always sink to the bottom, in EITHER direction -- only the
    // comparison between two real values flips with sortAsc. Sorting the
    // whole array ascending-with-nulls-last and then reversing (the
    // earlier approach) flips nulls to the top on a descending sort, which
    // reads as "worst/missing data ranked first."
    return [...data.rows].sort((a, b) => {
      const av = a[sortKey];
      const bv = b[sortKey];
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      const cmp =
        typeof av === "string" || typeof bv === "string"
          ? String(av).localeCompare(String(bv))
          : (av as number) - (bv as number);
      return sortAsc ? cmp : -cmp;
    });
  }, [data, sortKey, sortAsc]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortAsc((v) => !v);
    } else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  if (loadError) {
    return (
      <div
        className="rounded-lg border px-4 py-3 text-sm"
        style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
      >
        Failed to load screener: {loadError}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="text-sm" style={{ color: "var(--text-muted)" }}>
        Scanning tracked symbols… this takes a little while the first time each field is fetched.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <h2 className="text-sm font-semibold" style={{ color: "var(--text-primary)" }}>
          Screener
        </h2>
        <button
          type="button"
          onClick={load}
          className="rounded-md border px-3 py-1.5 text-xs font-medium"
          style={{ borderColor: "var(--border)", color: "var(--text-secondary)" }}
        >
          Refresh
        </button>
      </div>

      <div
        className="rounded-lg border px-4 py-3 text-xs"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)", color: "var(--text-secondary)" }}
      >
        <strong style={{ color: "var(--text-primary)" }}>Descriptive statistics, not investment advice</strong> —
        this doesn't tell you what to buy or sell. {data.methodology} Scores are 0-100 cross-sectional
        percentile ranks among the {rows.length} symbols shown, recomputed live -- a missing value ("—")
        means that field is unavailable for this symbol, not a score of zero.
      </div>

      <div
        className="overflow-x-auto rounded-lg border"
        style={{ borderColor: "var(--border)", background: "var(--surface-1)" }}
      >
        <table className="w-full min-w-[980px] border-collapse text-sm">
          <thead>
            <tr style={{ borderBottom: "1px solid var(--gridline)" }}>
              {COLUMNS.map((c) => (
                <th
                  key={c.key}
                  onClick={() => toggleSort(c.key)}
                  className="cursor-pointer px-4 py-3 text-left font-medium whitespace-nowrap select-none"
                  style={{ color: sortKey === c.key ? "var(--text-primary)" : "var(--text-muted)" }}
                >
                  {c.label}
                  {sortKey === c.key ? (sortAsc ? " ▲" : " ▼") : ""}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r: ScreenerRow) => (
              <tr key={r.symbol} style={{ borderBottom: "1px solid var(--gridline)" }}>
                <td className="px-4 py-3 font-medium" style={{ color: "var(--text-primary)" }}>
                  {r.symbol}
                </td>
                <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                  {fmtPrice(r.price)}
                </td>
                <td className="px-4 py-3">
                  <ScoreCell v={r.compositeScore} />
                </td>
                <td className="px-4 py-3">
                  <ScoreCell v={r.valuationScore} />
                </td>
                <td className="px-4 py-3">
                  <ScoreCell v={r.qualityScore} />
                </td>
                <td className="px-4 py-3">
                  <ScoreCell v={r.growthMomentumScore} />
                </td>
                <td className="px-4 py-3">
                  <ScoreCell v={r.riskScore} invert />
                </td>
                <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                  {fmtPe(r.trailingPe)}
                </td>
                <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                  {fmtAnalystRating(r.analystRating)}
                </td>
                <td className="px-4 py-3 tabular-nums" style={{ color: "var(--text-secondary)" }}>
                  {fmtPrice(r.analystTargetPrice)}
                </td>
                <td
                  className="px-4 py-3 tabular-nums"
                  style={{
                    color:
                      r.upsidePct === null
                        ? "var(--text-muted)"
                        : r.upsidePct >= 0
                          ? "var(--status-good)"
                          : "var(--status-critical)",
                  }}
                >
                  {fmtPct(r.upsidePct)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
