import { useEffect, useRef, useState } from "react";
import { api, type Quote, type SymbolsResponse } from "../api";
import { SymbolsTable } from "./SymbolsTable";
import { SymbolDetail } from "./SymbolDetail";

const QUOTE_REFRESH_MS = 30_000;

export function SymbolsView() {
  const [data, setData] = useState<SymbolsResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [quotes, setQuotes] = useState<Record<string, Quote>>({});
  const [selected, setSelected] = useState<string | null>(null);
  const tickers = useRef<string[]>([]);

  useEffect(() => {
    api
      .listSymbols()
      .then((res) => {
        setData(res);
        setLoadError(null);
        tickers.current = res.symbols.map((s) => s.symbol);
      })
      .catch((e) => setLoadError(String(e)));
  }, []);

  useEffect(() => {
    if (!data?.quotesAvailable || tickers.current.length === 0) return;
    let cancelled = false;
    const poll = () => {
      api
        .quotes(tickers.current)
        .then((q) => {
          if (!cancelled) setQuotes(q);
        })
        .catch(() => {});
    };
    poll();
    const id = setInterval(poll, QUOTE_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [data]);

  if (loadError) {
    return (
      <div
        className="rounded-lg border px-4 py-3 text-sm"
        style={{ borderColor: "var(--status-critical)", color: "var(--status-critical)" }}
      >
        Failed to load symbols: {loadError}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="text-sm" style={{ color: "var(--text-muted)" }}>
        Loading symbols…
      </div>
    );
  }

  if (selected) {
    return (
      <SymbolDetail ticker={selected} quote={quotes[selected]} onBack={() => setSelected(null)} />
    );
  }

  return (
    <div className="space-y-4">
      <div
        className="rounded-lg border px-4 py-3 text-sm"
        style={{
          borderColor: "var(--border)",
          background: "var(--surface-1)",
          color: "var(--text-secondary)",
        }}
      >
        {data.quotesAvailable ? (
          <>
            Live prices are <strong>delayed IEX</strong> quotes from Alpaca (free tier), refreshing
            every 30s. Last close and day change come from cached daily bars.
          </>
        ) : (
          <>
            Live quotes are off: {data.quotesReason} Add keys to <code>.env</code> and restart the
            API to enable them. Everything below (last close, day change, charts) works now from
            cached data.
          </>
        )}
      </div>

      <SymbolsTable symbols={data.symbols} quotes={quotes} onSelect={setSelected} />
    </div>
  );
}
