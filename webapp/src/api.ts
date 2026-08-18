export interface StrategySummary {
  name: string;
  kind: "Day Trading" | "Swing Trading";
  // "standard" runs through /api/backtest and the Strategies tab's override UI.
  // "cross_sectional" (Dual Momentum) and "pairs" (Pairs / Stat Arb) run on
  // different engines with different result shapes -- see
  // /api/backtest/cross-sectional and /api/backtest/pairs below, and
  // engine/logging_db.py's separate portfolio_runs table for their run
  // history (win rate/avg win/avg loss/expectancy/profit factor/beta
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
  // cross_sectional/pairs rows, whose status verdict is judged against it.
  benchmarkReturnPct: number | null;
  benchmarkGapPct: number | null;
  benchmarkName: string;
  // The exact window benchmarkGapPct was computed over -- only set for
  // standard-engine rows. Can differ from startDate/endDate (data coverage
  // vs. requested window) and, for a canonical row, from an earlier run of
  // the same strategy (a default request's end date is "today," so a
  // later re-run's window silently extends). Render as a tooltip so a
  // moved Gap vs SPY figure is traceable instead of looking like drift.
  benchmarkWindowStart: string | null;
  benchmarkWindowEnd: string | null;
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
  edgeVerdict: string | null;
  lifecycleStage: string | null;
  validation: ValidationReport | null;
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
  benchmarkGapPct: number | null;
  benchmarkName: string;
  benchmarkWindowStart: string | null;
  benchmarkWindowEnd: string | null;
  beta: number | null;
  cagrPct: number | null;
  exposurePct: number | null;
  riskFreeRate: number | null;
  // What buying and holding the same symbol(s) over the same window alone
  // would have returned. benchmarkGapPct is the cumulative strategy-minus-
  // benchmark return difference; it is not factor-regression alpha.
  buyHoldReturnPct: number | null;
  totalReturnPct: number | null;
  averageGrossExposurePct: number | null;
  averageNetExposurePct: number | null;
  timeInMarketPct: number | null;
  turnoverPct: number | null;
  modeledCosts: number | null;
  matchedSpyReturnPct: number | null;
  matchedSpyExcessPct: number | null;
  annualizedMatchedExcessPct: number | null;
  matchedAlphaAnnualPct: number | null;
  matchedBeta: number | null;
  matchedBenchmarkTrades: number;
  missingBenchmarkTrades: number;
  status: string;
}

export type ValidationStatus = "pass" | "fail" | "warning" | "unresolved" | "not_applicable";

export interface ValidationCheck {
  key: string;
  label: string;
  status: ValidationStatus;
  summary: string;
  required: boolean;
  value: number | string | boolean | null;
  details: Record<string, unknown>;
}

export interface ValidationDimension {
  key: string;
  label: string;
  checks: ValidationCheck[];
}

export interface EdgeVerdict {
  identifiedEdge: boolean;
  headline: string;
  signalEdge: string;
  universeSpecific: string;
  beatsBuyAndHold: string;
  forwardTestWorthy: boolean;
  productionCapitalWorthy: boolean;
  lifecycleStage?: string;
  blockers: string[];
  blockingChecks?: Array<{
    key: string;
    label: string;
    status: ValidationStatus;
    summary: string;
  }>;
}

export interface ValidationResearch {
  experimentId: number | null;
  familySearchNumber: number;
  isPreregistered: boolean;
  lifecycleStage: string;
  validationSpec: Record<string, unknown>;
  manifest: Record<string, unknown>;
  dataQuality?: Record<string, unknown> | null;
  canonicalPortfolioMetrics?: Record<string, unknown> | null;
  familySearchCount?: number;
  multipleTestingBurden?: string;
}

export interface GovernedForwardExperiment {
  id: number;
  strategyName: string;
  validationRunId: number;
  startedAt: string;
  frozenManifestHash: string;
  frozenConfig: Record<string, unknown>;
  benchmark: string;
  primaryCriterion: string;
  minCalendarDays: number;
  minObservations: number;
  maxShortfallPct: number;
  status: string;
  conclusion: string | null;
  locked: boolean;
  observationCount: number;
  latest: Record<string, unknown> | null;
}

export interface FillCalibration {
  symbol: string | null;
  fills: number;
  minimumFills: number;
  calibrated: boolean;
  medianAdverseSlippageBps: number | null;
  p95AdverseSlippageBps: number | null;
  meanFillRatio: number | null;
  partialFillRate: number | null;
}

export interface ValidationReport {
  version: number;
  generatedAt: string;
  dimensions: ValidationDimension[];
  verdict: EdgeVerdict;
  research?: ValidationResearch;
}

export type ValidationJobStatus = "queued" | "running" | "completed" | "failed";

export interface ValidationJob<T = unknown> {
  jobId: string;
  status: ValidationJobStatus;
  stage: string;
  progressPct: number;
  createdAt: string;
  completedAt: string | null;
  error: string | null;
  result: T | null;
  reused: boolean;
  experimentId: number | null;
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
  tradeReturn: number | null;
  matchedSpyReturn: number | null;
  excessVsSpy: number | null;
  matchedSpyEntryTime: string | null;
  matchedSpyExitTime: string | null;
  modeledCost: number | null;
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
  validation: ValidationReport;
  matchedBenchmark: MatchedBenchmark;
  researchMetadata: Record<string, unknown>;
  // Optional at the transport boundary so a browser connected to an older
  // backend renders a fail-closed warning instead of crashing the whole Lab.
  timing?: TimingContract;
}

export interface MatchedBenchmark {
  benchmark: string;
  matchedReturnPct: number | null;
  matchedExcessPct: number | null;
  annualizedExcessPct: number | null;
  alphaAnnualPct: number | null;
  beta: number | null;
  matchedTrades: number;
  missingTrades: number;
  executionNote: string;
  error?: string;
}

export interface TimingContract {
  informationAvailability: "PRE_MARKET" | "INTRADAY" | "AT_CLOSE" | "POST_CLOSE";
  execution: "SAME_OPEN" | "SAME_CLOSE" | "NEXT_OPEN";
  usesCurrentClose: boolean;
  engine: string;
  exceptionReason: string | null;
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
  universeDefault: string | null;
  implementationStatus: "implemented" | "unavailable";
  unavailableReason: string | null;
  universes: RegisteredUniverse[];
  timing: TimingContract;
  params: ParamSpec[];
}

export interface RegisteredUniverse {
  id: string;
  label: string;
  category: string;
  description: string;
  assetClass: "equity" | "crypto" | "futures" | "single-instrument";
  symbols: string[];
  membershipMode: string;
  primaryBenchmark: string | null;
  equalWeightBenchmark: string | null;
  runnable: boolean;
  selectable: boolean;
  unavailableReason: string | null;
  coverageStart: string | null;
  coverageEnd: string | null;
  approximateSecurityCount: number | null;
  pitStatus: {
    ready: boolean;
    summary: string;
    bundlePath: string;
    missingArtifacts: string[];
    invalidReasons: string[];
    source: string | null;
    snapshotId: string | null;
    coverageStart: string | null;
    coverageEnd: string | null;
    securityCount: number | null;
    delistedCount: number | null;
    acquiredCount: number | null;
    tickerChangeCount: number | null;
    marketCapAvailable: boolean;
  } | null;
}

export interface BacktestOverrides {
  symbols?: string[];
  start?: string;
  end?: string;
  params?: Record<string, number | boolean | string>;
  universeId?: string;
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
  id: number;
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
  benchmarkGapPct: number | null;
  benchmarkName: string;
  benchmarkWindowStart: string | null;
  benchmarkWindowEnd: string | null;
  status: string;
  isCanonical: boolean;
  universeId: string | null;
  symbols: string[];
  params: Record<string, number | boolean | string>;
  // Persisted edge-validation outcome for THIS run. Null on rows logged before
  // validation was stored, or on runs made outside the API (e.g. the CLI),
  // which is honestly "not validated" rather than "failed".
  edgeVerdict?: string | null;
  lifecycleStage?: string | null;
  validation?: ValidationReport | null;
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
  // Alpaca's own prior-trading-session-close equity -- the baseline the
  // automated-execution daily-loss circuit breaker compares against.
  lastEquity?: number | null;
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

// Automated paper-order execution (engine/execution.py) -- distinct from
// the day-trading signal scanner above: this actually places orders, for
// cross-sectional strategies (currently only Dual Momentum) the user has
// explicitly enabled below.
export interface ExecutionStrategyConfig {
  strategyName: string;
  enabled: boolean;
  enabledAt: string | null;
  params: Record<string, number | boolean | string>;
  universeId: string | null;
  symbols: string[];
  validationRunId: number | null;
  // Set when this strategy was promoted to paper testing despite failing
  // the forward-test gate -- an explicit, logged bypass (see
  // engine/forward_experiments.py:start's docstring), not a silent one.
  // overrideBlockers is frozen at the moment the override was used and can
  // legitimately differ from the strategy's current, live-recomputed status.
  overrideUsed: boolean;
  overrideReason: string | null;
  overrideBlockers: string[];
  inception: ExecutionInception;
}

export interface ExecutionInception {
  policy: "adopt" | "flatten" | null;
  status: "policy_required" | "pending" | "flattening" | "initialized";
  validationRunId: number | null;
  inceptionAt: string | null;
  equity: number | null;
  inheritedPositions: { symbol: string; qty: number; marketValue: number | null }[];
  legacyDefault: boolean;
}

export interface RebalanceRunRow {
  id: number;
  strategyName: string;
  rebalanceDate: string;
  triggerSource: "scheduled" | "manual";
  triggeredAt: string;
  status: string;
  strategyParams: Record<string, number | boolean | string> | null;
  portfolioValueAtStart: number | null;
  targetWeights: Record<string, number> | null;
  dailyLossPctAtStart: number | null;
  errorMessage: string | null;
}

export interface ExecutionOrderRow {
  id: number;
  symbol: string;
  side: "buy" | "sell";
  orderKind: "notional" | "qty" | "close";
  qty: number | null;
  notional: number | null;
  stopPrice: number | null;
  targetPrice: number | null;
  clientOrderId: string;
  alpacaOrderId: string | null;
  status: string;
  submittedAt: string | null;
  filledAt: string | null;
  filledQty: number | null;
  filledAvgPrice: number | null;
  isPaper: boolean;
  errorMessage: string | null;
}

export interface KillSwitchStatus {
  active: boolean;
}

export interface ExecutionSummary {
  // Account equity right before the earliest completed rebalance --
  // the baseline "all-time P&L since automated trading started" is
  // measured against. null until at least one rebalance has actually
  // traded (not just been blocked).
  startingEquity: number | null;
  firstTradeAt: string | null;
  completedRebalances: number;
  inception: ExecutionInception | null;
}

export interface ForwardTestPoint {
  asof: string;
  months_elapsed: number;
  strategy_return_pct: number;
  ew_pit_dow_return_pct: number | null;
  spy_return_pct: number | null;
  random_median_return_pct: number | null;
  vs_ew_pit_dow_pp: number | null;
  vs_spy_pp: number | null;
  vs_random_pp: number | null;
}

export interface ForwardTestStatus {
  status: string;
  freezeDate: string;
  observationCount: number;
  latest: ForwardTestPoint | null;
  decision: { triggered: boolean; verdict: string; reasoning: string };
  stopHorizonMonths: number;
  continueHorizonMonths: number;
  stopShortfallPp: number;
  stopBenchmark: string;
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
  universeId: string | null;
  universeLabel: string;
  rebalanceFrequency: string;
  targetPositionCount: number;
  initialRankableCount: number;
  incompleteWarmupCount: number;
  pitDiagnostics: Record<string, unknown> | null;
  pitAnalysis: {
    strategyReturnPct: number | null;
    strategyCagrPct: number | null;
    spyReturnPct: number | null;
    spyCagrPct: number | null;
    cumulativeGapPct: number | null;
    annualizedBenchmarkRelativeReturnPct: number | null;
    annualizedVolatilityPct: number | null;
    calmarRatio: number | null;
    mda: Record<string, unknown>;
    annualReturns: Array<{ year: number; strategyPct: number; spyPct: number; excessPct: number }>;
    regimes: Array<{ label: string; strategyPct: number; spyPct: number; excessPct: number }>;
    rollingExcess: Record<string, { observations: number; fractionBeatingSpy: number | null; medianExcessPct: number | null; worstExcessPct: number | null; bestExcessPct: number | null }>;
    holdout: Record<string, unknown>;
    costStressReturnPct: Record<string, number>;
    pitIntegrity: Record<string, unknown>;
    equalWeightEligibleReturnPct: number | null;
    rankingContributionPct: number | null;
    randomControl: Record<string, unknown>;
    robustness: {
      primaryPreregisteredConfig: { lookback: number; topN: number; frequency: string };
      arms: Array<{ lookback: number; topN: number; frequency: string; primary: boolean; returnPct: number; beatsSpy: boolean; beatsPitEqualWeight: boolean }>;
      fractionBeatingPitEqualWeight: number | null;
      fractionBeatingSpy: number | null;
      interpretation: string;
    };
  } | null;
  equityCurve: EquityPoint[];
  rebalances: RebalanceRow[];
  finalEquity: number;
  returnPct: number;
  cagrPct: number | null;
  maxDrawdownPct: number;
  sharpe: number | null;
  sortino: number | null;
  riskFreeRate: number;
  turnoverPct: number;
  totalCosts: number;
  totalTradedNotional: number;
  validation: ValidationReport;
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
  validation: ValidationReport;
}

export interface PortfolioHistoryRow {
  id: number;
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
  universeId: string | null;
  symbols: string[];
  params: Record<string, number | boolean | string>;
  pairSymbolA: string | null;
  pairSymbolB: string | null;
  pairPValue: number | null;
  benchmarkReturnPct: number | null;
  benchmarkGapPct: number | null;
  benchmarkName: string;
  // Verdict from engine/metrics.py:portfolio_status(); null on rows logged
  // before it existed, or on runs with no meaningful verdict (e.g. a Pairs
  // run that found no cointegrated pair).
  status: string | null;
  // Persisted edge-validation outcome for THIS run. Null on rows logged before
  // validation was stored, or on runs made outside the API (e.g. the CLI),
  // which is honestly "not validated" rather than "failed".
  edgeVerdict?: string | null;
  lifecycleStage?: string | null;
  validation?: ValidationReport | null;
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

// Carries the HTTP status code alongside the message so callers can branch
// on the actual status (e.g. "offer an override retry on any 409") instead
// of string-matching the FastAPI detail text, which is fragile the moment a
// backend message wording changes and silently breaks a caller that grep'd
// for a specific phrase in it.
export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
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
    throw new ApiError(res.status, message || `${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

function hasOverrides(overrides?: BacktestOverrides): boolean {
  if (!overrides) return false;
  return Boolean(
    overrides.symbols?.length ||
      overrides.universeId ||
      overrides.start ||
      overrides.end ||
      (overrides.params && Object.keys(overrides.params).length > 0),
  );
}

async function runValidationSuite<T>(
  engine: StrategySummary["engine"],
  name: string,
  overrides?: BacktestOverrides,
  onProgress?: (job: ValidationJob<T>) => void,
): Promise<T> {
  let job = await request<ValidationJob<T>>(
    `/validation/jobs/${engine}/${encodeURIComponent(name)}`,
    {
      method: "POST",
      ...(hasOverrides(overrides)
        ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(overrides) }
        : {}),
    },
  );
  onProgress?.(job);
  while (job.status === "queued" || job.status === "running") {
    await new Promise((resolve) => window.setTimeout(resolve, 750));
    job = await request<ValidationJob<T>>(`/validation/jobs/${job.jobId}`);
    onProgress?.(job);
  }
  if (job.status === "failed") throw new Error(job.error || "Validation failed");
  if (job.result === null) throw new Error("Validation completed without a result");
  return job.result;
}

export const api = {
  // universeId filters each row to that strategy's latest run AGAINST that
  // specific registered universe (never triggers a new backtest -- see
  // api/main.py:list_strategies). Omit for the registered-default leaderboard.
  listStrategies: (universeId?: string) =>
    request<StrategySummary[]>(
      universeId ? `/strategies?universe_id=${encodeURIComponent(universeId)}` : "/strategies",
    ),
  listUniverses: () => request<RegisteredUniverse[]>("/universes"),
  paramSchema: (name: string) => request<ParamSchema>(`/params/${encodeURIComponent(name)}`),
  runBacktest: (
    name: string,
    overrides?: BacktestOverrides,
    onProgress?: (job: ValidationJob<BacktestResult>) => void,
  ) => runValidationSuite<BacktestResult>("standard", name, overrides, onProgress),
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
  runCrossSectional: (
    name: string,
    overrides?: BacktestOverrides,
    onProgress?: (job: ValidationJob<CrossSectionalResponse>) => void,
  ) => runValidationSuite<CrossSectionalResponse>("cross_sectional", name, overrides, onProgress),
  runPairs: (
    name: string,
    overrides?: BacktestOverrides,
    onProgress?: (job: ValidationJob<PairsResponse>) => void,
  ) => runValidationSuite<PairsResponse>("pairs", name, overrides, onProgress),
  liveAccount: () => request<LiveAccountResponse>("/live/account"),
  liveSignals: (limit = 100) => request<SignalAlert[]>(`/live/signals?limit=${limit}`),
  triggerScan: () =>
    request<{ newAlerts: unknown[] }>("/live/scan", { method: "POST" }),
  executionConfig: () => request<ExecutionStrategyConfig[]>("/live/execution/config"),
  executionStrategies: () => request<{ strategyName: string }[]>("/live/execution/strategies"),
  setExecutionConfig: (
    strategyName: string,
    enabled: boolean,
    params: Record<string, number | boolean | string> = {},
    validationRunId?: number,
    inceptionPolicy?: "adopt" | "flatten",
    // Explicit, per-call, LOGGED bypass of the forward-test gate (paper
    // capital only) -- see engine/forward_experiments.py:start's docstring.
    // Omitted/false reproduces the original strict behavior exactly.
    override?: { reason: string },
  ) =>
    request<{
      strategyName: string; enabled: boolean;
      params: Record<string, number | boolean | string>; validationRunId: number | null;
      universeId: string | null; symbols: string[];
      overrideUsed: boolean; overrideBlockers: string[];
    }>("/live/execution/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        strategyName, enabled, params, validationRunId,
        inceptionPolicy: inceptionPolicy ?? null,
        overridePassedGates: Boolean(override),
        overrideReason: override?.reason ?? null,
      }),
    }),
  executionRuns: (limit = 50) =>
    request<RebalanceRunRow[]>(`/live/execution/runs?limit=${limit}`),
  executionOrders: (runId: number) =>
    request<ExecutionOrderRow[]>(`/live/execution/orders?runId=${runId}`),
  rebalanceNow: (strategyName: string) =>
    request<{ status: string; runId?: number; reason?: string }>("/live/execution/rebalance-now", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ strategyName }),
    }),
  killSwitchStatus: () => request<KillSwitchStatus>("/live/execution/kill-switch"),
  executionSummary: () => request<ExecutionSummary>("/live/execution/summary"),
  forwardTestStatus: () => request<ForwardTestStatus>("/live/forward-test"),
  executionCalibration: (symbol?: string) =>
    request<FillCalibration>(`/live/execution/calibration${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`),
  forwardExperiments: (strategyName: string) =>
    request<GovernedForwardExperiment[]>(`/research/forward/${encodeURIComponent(strategyName)}`),
  activateKillSwitch: (flatten: boolean) =>
    request<{ flagSet: boolean; flattened: boolean; error: string | null }>(
      "/live/execution/kill-switch",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ flatten }) },
    ),
  deactivateKillSwitch: () =>
    request<KillSwitchStatus>("/live/execution/kill-switch/deactivate", { method: "POST" }),
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
