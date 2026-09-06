import { useEffect, useState } from "react";
import { api, type Quote, type SymbolDetail as SymbolDetailData } from "../api";
import { StatTile } from "./StatTile";
import { PriceChart } from "./PriceChart";

function fmtDollarVol(v: number | null): string {
  if (v === null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toFixed(0)}`;
}

export function SymbolDetail({
  ticker,
  quote,
  onBack,
}: {
  ticker: string;
  quote: Quote | undefined;
  onBack: () => void;
}) {
  const [detail, setDetail] = useState<SymbolDetailData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDetail(null);
    setError(null);
    api
      .symbolDetail(ticker)
      .then(setDetail)
      .catch((e) => setError(String(e)));
  }, [ticker]);

  const livePrice =
    quote && quote.source !== "unavailable" && quote.price !== undefined ? quote.price : null;

  return (
    <div className="space-y-6">
      <button
        type="button"
        onClick={onBack}
        className="text-sm font-medium"
        style={{ color: "var(--series-1)" }}
      >
        ← All symbols
      </button>

      <div className="flex flex-wrap items-baseline gap-3">
        <h2 className="text-2xl font-semibold" style={{ color: "var(--text-primary)" }}>
          {ticker}
        </h2>
        {detail?.universes.map((u) => (
          <span
            key={u}
            className="rounded px-2 py-0.5 text-xs"
            style={{ background: "var(--series-1-wash)", color: "var(--series-1)" }}
          >
            {u}
          </span>
        ))}
      </div>

      {error && (
        <div
          className="rounded-lg border px-4 py-3 text-sm"
          style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
        >
          {error}
        </div>
      )}

      {detail && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile
              label="Live price (delayed)"
              value={livePrice === null ? "—" : `$${livePrice.toFixed(2)}`}
            />
            <StatTile
              label={`Last close${detail.closeAsOf ? ` (${detail.closeAsOf})` : ""}`}
              value={detail.lastClose === null ? "—" : `$${detail.lastClose.toFixed(2)}`}
            />
            <StatTile
              label="Day change"
              value={detail.changePct === null ? "—" : `${detail.changePct >= 0 ? "+" : ""}${detail.changePct.toFixed(2)}%`}
              valueColor={
                detail.changePct === null
                  ? undefined
                  : detail.changePct >= 0
                    ? "var(--status-good)"
                    : "var(--status-critical)"
              }
            />
            <StatTile label="Avg $ volume (60d)" value={fmtDollarVol(detail.avgDollarVolume)} />
          </div>

          <PriceChart data={detail.history} />

          <p className="text-xs" style={{ color: "var(--text-muted)" }}>
            Liquidity tier: {detail.liquidityTier}. Price history is the same cached daily data
            backtests run against; the live price is a delayed IEX print and isn't used in any
            backtest.
          </p>
        </>
      )}
    </div>
  );
}
