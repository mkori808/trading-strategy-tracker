export interface StrategySummary {
  name: string;
  kind: "Day Trading" | "Swing Trading";
  // "standard" runs through /api/backtest and the Lab tab's override UI.
  // "cross_sectional" (Dual Momentum) and "pairs" (Pairs / Stat Arb) run on
  // different engines with different result shapes -- see
  // /api/backtest/cross-sectional and /api/backtest/pairs below. No
  // overrides exist for either yet, so they're Compare-tab only.
  engine: "standard" | "cross_sectional" | "pairs";
  tradesTaken: number;
  winRate: number | null;
  avgWinR: number | null;
  avgLossR: number | null;
  expectancyR: number | null;
  profitFactor: number | null;
  status: string;
  lastRun: string | null;
  sharpe: number | null;
  alphaPct: number | null;
  beta: number | null;
}

export interface Metrics {
  tradesTaken: number;
  wins: number;
  losses: number;
  winRate: number;
  avgWinR: number;
  avgLossR: number;
  expectancyR: number;
  profitFactor: number | null;
  maxDrawdownPct: number | null;
  sharpe: number | null;
  sortino: number | null;
  alphaPct: number | null;
  beta: number | null;
  cagrPct: number | null;
  exposurePct: number | null;
  riskFreeRate: number | null;
  status: string;
}

export interface EquityPoint {
  time: string;
  equity: number;
}

export interface Trade {
  symbol: string;
  entryTime: string;
  exitTime: string;
  size: number;
  entryPrice: number;
  exitPrice: number;
  sl: number | null;
  tp: number | null;
  pnl: number;
  returnPct: number;
  // MFE/MAE and exit-quality diagnostics -- see engine/excursion.py. null
  // when the trade has no matching excursion row (e.g. dropped by the
  // MFE>=realized_r sanity check, or the strategy's engine doesn't compute
  // these at all, like Overnight Hold).
  realizedR: number | null;
  mfeR: number | null;
  maeR: number | null;
  exitEfficiencyPct: number | null;
  lossRealizationRatioPct: number | null;
  entrySlippagePct: number | null;
}

export interface ExcursionSummary {
  tradesWithData: number;
  meanExitEfficiencyPct: number | null;
  medianExitEfficiencyPct: number | null;
  meanLossRealizationRatioPct: number | null;
  medianLossRealizationRatioPct: number | null;
}

export interface PortfolioResult {
  maxConcurrentPositions: number;
  tradesTaken: number;
  skippedForCapacity: number;
  finalEquity: number;
  returnPct: number;
  cagrPct: number | null;
  maxDrawdownPct: number;
  sharpe: number | null;
  sortino: number | null;
  equityCurve: EquityPoint[];
}

export interface PerSymbolRow {
  symbol: string;
  tradesTaken: number;
  winRate: number | null;
  expectancyR: number | null;
  profitFactor: number | null;
  pnl: number;
  returnPct: number | null;
  sharpe: number | null;
  sparkline: number[];
}

export interface BacktestResult {
  strategyName: string;
  symbols: string[];
  start: string;
  end: string;
  metrics: Metrics;
  isCanonical: boolean;
  appliedSymbols: string[];
  appliedParams: Record<string, number | boolean | string> | null;
  equitySymbol: string | null;
  equityCurve: EquityPoint[];
  trades: Trade[];
  perSymbol: PerSymbolRow[];
  portfolio: PortfolioResult;
  excursionSummary: ExcursionSummary;
}

export type ParamKind = "int" | "float" | "bool" | "str";

export interface ParamSpec {
  name: string;
  label: string;
  kind: ParamKind;
  default: number | boolean | string;
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  help: string | null;
}

export interface ParamSchema {
  strategyName: string;
  interval: string;
  symbolsDefault: string[];
  startDefault: string;
  endDefault: string;
  symbolOverrideAllowed: boolean;
  params: ParamSpec[];
}

export interface BacktestOverrides {
  symbols?: string[];
  start?: string;
  end?: string;
  params?: Record<string, number | boolean | string>;
}

export interface SymbolMeta {
  symbol: string;
  universes: string[];
  lastClose: number | null;
  prevClose: number | null;
  changePct: number | null;
  closeAsOf: string | null;
  avgDollarVolume: number | null;
  liquidityTier: string;
  hasCache: boolean;
}

export interface SymbolsResponse {
  symbols: SymbolMeta[];
  quotesAvailable: boolean;
  quotesReason: string;
}

export interface OhlcBar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface SymbolDetail extends SymbolMeta {
  history: OhlcBar[];
}

export interface Quote {
  symbol: string;
  price?: number;
  asOf?: string;
  source: string;
  reason?: string;
}

export interface HistoryRow {
  runAt: string;
  startDate: string;
  endDate: string;
  tradesTaken: number;
  winRate: number;
  expectancyR: number;
  profitFactor: number | null;
  maxDrawdownPct: number | null;
  sharpe: number | null;
  alphaPct: number | null;
  status: string;
  isCanonical: boolean;
  symbols: string[];
  params: Record<string, number | boolean | string>;
}

export interface RegimeLogEntry {
  date: string;
  regime: "Bullish" | "Neutral" | "Bearish";
  changed: boolean;
}

export interface RegimeInfo {
  current: "Bullish" | "Neutral" | "Bearish";
  asOf: string | null;
  distribution: Record<string, number>;
  recentLog: RegimeLogEntry[];
}

export interface SectorPerformanceRow {
  symbol: string;
  universes: string[];
  lastClose: number | null;
  prevClose: number | null;
  changePct: number | null;
  closeAsOf: string | null;
}

export interface TrendTemplateSymbolRow {
  symbol: string;
  passes: boolean;
  failedCriteria: string[];
}

export interface TrendTemplateScan {
  asOf: string;
  passCount: number;
  failCount: number;
  passRate: number;
  symbols: TrendTemplateSymbolRow[];
}

export interface MarketResponse {
  regime: RegimeInfo;
  sectorPerformance: SectorPerformanceRow[];
  trendTemplate: TrendTemplateScan;
}

export interface LiveAccount {
  available: boolean;
  reason?: string;
  accountNumber?: string;
  status?: string;
  equity?: number;
  cash?: number;
  buyingPower?: number;
  portfolioValue?: number;
  daytradeCount?: number | null;
}

export interface LivePosition {
  symbol: string;
  side: string;
  qty: number;
  avgEntryPrice: number;
  currentPrice: number | null;
  marketValue: number | null;
  unrealizedPl: number | null;
  unrealizedPlPct: number | null;
}

export interface LiveOrder {
  id: string;
  symbol: string;
  side: string;
  qty: number | null;
  type: string;
  status: string;
  submittedAt: string | null;
  filledAt: string | null;
  filledAvgPrice: number | null;
}

export interface MarketClock {
  available: boolean;
  reason?: string;
  isOpen?: boolean;
  nextOpen?: string;
  nextClose?: string;
  timestamp?: string;
}

export interface LiveAccountResponse {
  account: LiveAccount;
  positions: LivePosition[];
  orders: LiveOrder[];
  clock: MarketClock;
}

export interface SignalAlert {
  detectedAt: string;
  barTimestamp: string;
  strategyName: string;
  symbol: string;
  direction: string;
  price: number | null;
  timeframe: string | null;
  regimeState: string | null;
  trendTemplatePass: boolean | null;
}

export interface CapTierPools {
  small: string[];
  mid: string[];
  large: string[];
}

export interface RebalanceRow {
  date: string;
  holdings: Record<string, number>;
}

export interface CrossSectionalResponse {
  strategyName: string;
  symbols: string[];
  start: string;
  end: string;
  appliedSymbols: string[];
  appliedParams: Record<string, number | boolean | string> | null;
  equityCurve: EquityPoint[];
  rebalances: RebalanceRow[];
  finalEquity: number;
  returnPct: number;
  cagrPct: number | null;
  maxDrawdownPct: number;
  sharpe: number | null;
  sortino: number | null;
  riskFreeRate: number;
}

export interface PairSelection {
  symbolA: string;
  symbolB: string;
  pValue: number;
}

export interface PairTrade {
  entryTime: string;
  exitTime: string;
  pair: string;
  position: string;
  pnl: number;
  reason: string;
}

export interface PairsResponse {
  strategyName: string;
  pair: PairSelection | null;
  symbols: string[];
  appliedSymbols: string[];
  appliedParams: Record<string, number | boolean | string> | null;
  trainingWindow: [string, string];
  tradingWindow: [string, string];
  equityCurve: EquityPoint[];
  trades: PairTrade[];
  finalEquity: number;
  returnPct: number;
  cagrPct: number | null;
  maxDrawdownPct: number;
  sharpe: number | null;
  sortino: number | null;
  riskFreeRate: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${body}`);
  }
  return res.json() as Promise<T>;
}

function hasOverrides(overrides?: BacktestOverrides): boolean {
  if (!overrides) return false;
  return Boolean(
    overrides.symbols?.length ||
      overrides.start ||
      overrides.end ||
      (overrides.params && Object.keys(overrides.params).length > 0),
  );
}

export const api = {
  listStrategies: () => request<StrategySummary[]>("/strategies"),
  paramSchema: (name: string) => request<ParamSchema>(`/params/${encodeURIComponent(name)}`),
  runBacktest: (name: string, overrides?: BacktestOverrides) =>
    request<BacktestResult>(`/backtest/${encodeURIComponent(name)}`, {
      method: "POST",
      // An untouched config sends no body at all -- byte-identical to the
      // original canonical-only call, so it logs as canonical server-side.
      ...(hasOverrides(overrides)
        ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(overrides) }
        : {}),
    }),
  history: (name: string) => request<HistoryRow[]>(`/history/${encodeURIComponent(name)}`),
  listSymbols: () => request<SymbolsResponse>("/symbols"),
  symbolDetail: (ticker: string) =>
    request<SymbolDetail>(`/symbols/${encodeURIComponent(ticker)}`),
  quotes: (symbols: string[]) =>
    request<Record<string, Quote>>(`/quotes?symbols=${encodeURIComponent(symbols.join(","))}`),
  market: () => request<MarketResponse>("/market"),
  universePools: () => request<CapTierPools>("/universe/pools"),
  runCrossSectional: (name: string, overrides?: BacktestOverrides) =>
    request<CrossSectionalResponse>(`/backtest/cross-sectional/${encodeURIComponent(name)}`, {
      method: "POST",
      ...(hasOverrides(overrides)
        ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(overrides) }
        : {}),
    }),
  runPairs: (name: string, overrides?: BacktestOverrides) =>
    request<PairsResponse>(`/backtest/pairs/${encodeURIComponent(name)}`, {
      method: "POST",
      ...(hasOverrides(overrides)
        ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(overrides) }
        : {}),
    }),
  liveAccount: () => request<LiveAccountResponse>("/live/account"),
  liveSignals: (limit = 100) => request<SignalAlert[]>(`/live/signals?limit=${limit}`),
  triggerScan: () =>
    request<{ newAlerts: unknown[] }>("/live/scan", { method: "POST" }),
};
