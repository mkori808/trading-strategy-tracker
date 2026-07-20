export interface StrategySummary {
  name: string;
  kind: "Day Trading" | "Swing Trading";
  // "standard" runs through /api/backtest and the Strategies tab's override UI.
  // "cross_sectional" (Dual Momentum) and "pairs" (Pairs / Stat Arb) run on
  // different engines with different result shapes -- see
  // /api/backtest/cross-sectional and /api/backtest/pairs below, and
  // engine/logging_db.py's separate portfolio_runs table for their run
  // history (win rate/avg win/avg loss/expectancy/profit factor/alpha/beta
  // are structurally not applicable to these two -- always null; cagrPct/
  // returnPct are the closest equivalents).
  engine: "standard" | "cross_sectional" | "pairs";
  // null for cross_sectional/pairs rows: "no discrete-trade concept" is
  // different from "traded zero times" -- render as "--", not 0.
  tradesTaken: number | null;
  winRate: number | null;
  avgWinR: number | null;
  avgLossR: number | null;
  expectancyR: number | null;
  profitFactor: number | null;
  cagrPct: number | null;
  returnPct: number | null;
  maxDrawdownPct: number | null;
  // SPY's buy-and-hold return over the same window -- only set for
  // cross_sectional/pairs rows, whose status verdict is judged against it
  // (standard rows carry alphaPct instead).
  benchmarkReturnPct: number | null;
  status: string;
  lastRun: string | null;
  sharpe: number | null;
  alphaPct: number | null;
  beta: number | null;
  // The exact configuration behind the run this row's scores came from --
  // same fields /api/history/{name} rows carry, surfaced here too so the
  // leaderboard doesn't require opening run history to answer "what
  // symbols/date range/params produced this number."
  symbols: string[];
  startDate: string | null;
  endDate: string | null;
  params: Record<string, number | boolean | string>;
  // Retired from the default dashboard view after a large-enough sample
  // showed decisively negative expectancy/return -- see
  // strategies/registry.py:ARCHIVED_STRATEGY_NAMES and
  // ARCHIVED_STRATEGIES.md. Still fully runnable/queryable; this only
  // controls default visibility (see StrategyTable's "Show archived" toggle).
  archived: boolean;
  archivedReason: string | null;
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
  // What buying and holding the same symbol(s) over the same window alone
  // would have returned -- alphaPct is the strategy's excess return over
  // this, shown alongside it rather than only the difference.
  buyHoldReturnPct: number | null;
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
  buyHoldReturnPct: number | null;
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
  // Fixed set of valid values for a "str" kind field -- renders as a
  // dropdown instead of free text. null means free text (no current
  // strategy uses that combination).
  choices: string[] | null;
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
  // Genuinely nullable in practice (a handful of early-logged rows predate
  // some metric computations existing at all) despite trades_taken > 0 on
  // those same rows -- render null-safely, don't assume "has trades" implies
  // "has these fields".
  winRate: number | null;
  expectancyR: number | null;
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

export interface MarketSignals {
  asOf: string;
  score: number | null;
  methodology: string;
  components: {
    pctAboveSma50: number | null;
    pctAboveSma200: number | null;
    netNewHighsLowsPct: number | null;
    spyRegime: "Bullish" | "Neutral" | "Bearish";
    spyRegimeScore: number | null;
  };
  symbolsTracked: number;
  newHighs20d: number;
  newLows20d: number;
}

export interface SectorRotationRow {
  symbol: string;
  relativeStrength: number | null;
  rising: boolean | null;
}

export interface SectorRotation {
  asOf: string;
  lookbackDays: number;
  rows: SectorRotationRow[];
}

export interface MarketResponse {
  regime: RegimeInfo;
  sectorPerformance: SectorPerformanceRow[];
  sectorRotation: SectorRotation;
  trendTemplate: TrendTemplateScan;
  marketSignals: MarketSignals;
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

export interface PortfolioHistoryRow {
  runAt: string;
  startDate: string | null;
  endDate: string | null;
  finalEquity: number | null;
  returnPct: number | null;
  cagrPct: number | null;
  maxDrawdownPct: number | null;
  sharpe: number | null;
  sortino: number | null;
  isCanonical: boolean;
  symbols: string[];
  params: Record<string, number | boolean | string>;
  pairSymbolA: string | null;
  pairSymbolB: string | null;
  pairPValue: number | null;
  benchmarkReturnPct: number | null;
  // Verdict from engine/metrics.py:portfolio_status(); null on rows logged
  // before it existed, or on runs with no meaningful verdict (e.g. a Pairs
  // run that found no cointegrated pair).
  status: string | null;
}

export interface ScreenerRow {
  symbol: string;
  price: number | null;
  compositeScore: number | null;
  valuationScore: number | null;
  qualityScore: number | null;
  growthMomentumScore: number | null;
  riskScore: number | null;
  trailingPe: number | null;
  profitMarginsPct: number | null;
  returnOnEquityPct: number | null;
  debtToEquity: number | null;
  momentum6mPct: number | null;
  volatilityPct: number | null;
  maxDrawdownPct: number | null;
  analystRating: number | null;
  analystTargetPrice: number | null;
  upsidePct: number | null;
  marketCap: number | null;
}

export interface ScreenerResponse {
  asOf: string;
  methodology: string;
  rows: ScreenerRow[];
}

export interface StreakRow {
  symbol: string;
  direction: "up" | "down" | null;
  days: number;
}

export interface MoversResponse {
  asOf: string;
  gainers: SymbolMeta[];
  losers: SymbolMeta[];
  streaks: StreakRow[];
}

export interface InsiderPurchase {
  issuerTicker: string;
  issuerName: string;
  filerName: string;
  filedAt: string;
  signalDate: string;
  transactionDate: string;
  sharesTransacted: number;
  pricePerShare: number;
  transactionValue: number;
  pctChangeHoldings: number | null;
  ownershipNature: string | null;
  formUrl: string;
}

export interface InsiderStatus {
  running: boolean;
  lastCompletedAt: string | null;
  lastError: string | null;
}

export interface InsiderRecentResponse extends InsiderStatus {
  rows: InsiderPurchase[];
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatResponse {
  reply: string;
}

export interface DigestPreview {
  asOf: string;
  regime: RegimeInfo;
  marketSignals: MarketSignals;
  movers: MoversResponse;
  insiderPurchases: InsiderPurchase[];
  disclaimer: string;
  text: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, init);
  if (!res.ok) {
    const body = await res.text();
    // FastAPI error bodies are {"detail": "..."} -- surface just that
    // clean message rather than the raw status/JSON, which otherwise
    // leaks straight into user-facing error text (e.g. RunConfigPanel's
    // "Couldn't load configuration" banner).
    let message = body;
    try {
      const parsed = JSON.parse(body) as { detail?: string };
      if (parsed?.detail) message = parsed.detail;
    } catch {
      // not JSON -- fall back to the raw body
    }
    throw new Error(message || `${res.status} ${res.statusText}`);
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
  portfolioHistory: (name: string) =>
    request<PortfolioHistoryRow[]>(`/history/portfolio/${encodeURIComponent(name)}`),
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
  screener: (symbols?: string[]) =>
    request<ScreenerResponse>(
      `/screener${symbols?.length ? `?symbols=${encodeURIComponent(symbols.join(","))}` : ""}`,
    ),
  movers: (symbols?: string[], topN = 10) =>
    request<MoversResponse>(
      `/movers?topN=${topN}${symbols?.length ? `&symbols=${encodeURIComponent(symbols.join(","))}` : ""}`,
    ),
  insiderRecent: (limit = 50) => request<InsiderRecentResponse>(`/insider/recent?limit=${limit}`),
  insiderStatus: () => request<InsiderStatus>("/insider/status"),
  insiderRefresh: () =>
    request<{ started: boolean; reason?: string } & InsiderStatus>("/insider/refresh", {
      method: "POST",
    }),
  digestPreview: () => request<DigestPreview>("/digest/preview"),
  chat: (result: BacktestResult, messages: ChatMessage[]) =>
    request<ChatResponse>("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ result, messages }),
    }),
};
