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
  // Authored from a natural-language description rather than defined in
  // strategy_tracker.xlsx (see engine/custom_strategies.py). Runs on the
  // same engine and is scored by the same bar -- the flag exists so the UI
  // can disclose that its RULES were generated, the same way the Lab tab
  // discloses a custom run CONFIGURATION.
  custom: boolean;
  customPrompt: string | null;
  customRules: CompiledRules | null;
  edgeVerdict: string | null;
  lifecycleStage: string | null;
  validation: ValidationReport | null;
  implementationStatus?: "implemented" | "unavailable";
  unavailableReason?: string | null;
  requestedStartDate?: string | null;
  requestedEndDate?: string | null;
  measuredStartDate?: string | null;
  measuredEndDate?: string | null;
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
  interval?: string;
  timing?: TimingContract;
  requestedStartDate?: string | null;
  requestedEndDate?: string | null;
  measuredStartDate?: string | null;
  measuredEndDate?: string | null;
  slippageBps?: number | null;
  commissionBps?: number | null;
  searchFamily?: string | null;
  familySearchNumber?: number | null;
  familySearchCount?: number | null;
  isPreregistered?: boolean | null;
  selectedAfterResults?: boolean | null;
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
  orderSummary: {
    openPending: number;
    filled: number;
    canceled: number;
    rejected: number;
    recentHistory: number;
  };
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
  identity: StrategyIdentity;
}

export interface StrategyIdentity {
  key: string;
  displayName: string;
  shortName: string;
  variant: "canonical" | "optimized" | "registered";
  provenance: string;
  selectionContext?: string;
  selectionBiasNote?: string;
  expectedConfig: Record<string, number | boolean | string>;
  actualConfig: Record<string, number | boolean | string>;
  expectedSymbolCount?: number | null;
  actualSymbolCount?: number;
  fingerprintMatches: boolean;
  fingerprintReason: string | null;
}

export interface ForwardTestPromotionCandidate {
  strategyName: string;
  testMode: "automated_paper" | "research_shadow";
  canPlaceOrders: boolean;
  status: string;
}

export interface ForwardArchitectureRow {
  key: string;
  strategy: string;
  mode: "BROKERAGE EXECUTION" | "SHADOW" | "PROP SHADOW" | "CLOSED / BLOCKED";
  brokerage: string | null;
  observations: number;
  evidenceQuality: "Execution-observed" | "Prospective synthetic" | "Prop synthetic on observed parent stream" | "No active forward evidence";
  parentStrategyId?: string | null;
  parentFingerprint?: string | null;
  positionsLabel: string;
  backfillObservations?: number;
}

export interface ExecutionAccountStatus {
  available: boolean;
  reason?: string;
  accountKey: string;
  brokerageLabel: string;
  accountId: string;
  environment: "paper" | "live";
  multiStrategyExecution: boolean;
  executionIsolation: string;
  owner: null | {
    strategyId: string;
    strategyName: string;
    displayName: string;
    strategyFingerprint: string;
    ownershipStartedAt: string;
    active: boolean;
    fingerprintMatches: boolean;
  };
  actionRequired: boolean;
  integrityIssues: { type: string; severity: string; detectedAt: string; symbol?: string | null; brokerOrderId?: string | null }[];
  counts: Record<"BROKERAGE EXECUTION" | "SHADOW" | "PROP SHADOW", number>;
  forwardStrategies: ForwardArchitectureRow[];
  principle: string;
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

export interface DailyPerformanceRow {
  /** NY calendar date of the session. */
  date: string;
  equity: number;
  profitLoss: number | null;
  profitLossPct: number | null;
  /** The benchmark's OWN return that session -- descriptive, not alpha.
   * null when that session's benchmark bar isn't available (notably the
   * in-progress day, whose daily bar doesn't exist until the close). */
  benchmarkPct: number | null;
}

export interface DailyPerformanceToday extends DailyPerformanceRow {
  /** Always true. Today is derived from the live account rather than a
   * settled close, and is reported separately from `rows` so an unsettled
   * figure is never mistaken for a broker-confirmed one. */
  inProgress: boolean;
}

export interface AlignedGrowthPoint {
  date: string;
  account: number;
  benchmark: number;
  baseline: boolean;
}

export interface AlignedBenchmarkComparison {
  available: boolean;
  reason: string | null;
  baselineDate?: string;
  startDate?: string;
  endDate?: string;
  sessions?: number;
  accountReturnPct?: number;
  benchmarkReturnPct?: number;
  differencePctPoints?: number;
  accountMaxDrawdownPct?: number;
  benchmarkMaxDrawdownPct?: number;
  growth?: AlignedGrowthPoint[];
  note?: string;
}

export interface DailyPerformance {
  available: boolean;
  reason: string | null;
  /** Inception of automated trading -- the same anchor
   * /live/execution/summary uses, so the daily series and the all-time
   * headline always describe one window. */
  startDate: string | null;
  benchmarkSymbol: string;
  rows: DailyPerformanceRow[];
  today: DailyPerformanceToday | null;
  forwardBaselineEquity: number | null;
  forwardReturnPct: number | null;
  forwardReturnSource: string | null;
  maturity: { label: string; reached: string[]; next: { sessions: number; label: string } | null };
  benchmarkComparison: { available: boolean; reason: string | null };
  alignedBenchmarkComparison: AlignedBenchmarkComparison;
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
  interval?: string;
  timing?: TimingContract;
  requestedStartDate?: string | null;
  requestedEndDate?: string | null;
  measuredStartDate?: string | null;
  measuredEndDate?: string | null;
  slippageBps?: number | null;
  commissionBps?: number | null;
  searchFamily?: string | null;
  familySearchNumber?: number | null;
  familySearchCount?: number | null;
  isPreregistered?: boolean | null;
  selectedAfterResults?: boolean | null;
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

// ---------------------------------------------------------------------------
// Conditional Edge Discovery -- see engine/conditional_edge.py for the
// workflow this mirrors: discovery (in-sample, on the strategy's discovery
// slice only) -> freeze -> validate -> (optional) final holdout ->
// conditioned strategy + Prop Account Analysis. Every number returned from
// `runConditionalDiscovery` alone is IN-SAMPLE; only a hypothesis that has
// been through freeze + validate carries any out-of-sample evidence.
// ---------------------------------------------------------------------------

export type ConditionalJobStatus = "queued" | "running" | "completed" | "failed";

export interface ConditionalJob<T = unknown> {
  jobId: string;
  status: ConditionalJobStatus;
  stage: string;
  progressPct: number;
  createdAt: string;
  completedAt: string | null;
  error: string | null;
  result: T | null;
}

export interface ConditionalStrategies {
  initial: string[];
  available: string[];
  engineFor: Record<string, "standard" | "cross_sectional">;
}

export interface FeatureDefinition {
  key: string;
  label: string;
  group: string;
  kind: "continuous" | "categorical" | "boolean";
  source: string;
  lookbackBars: number;
  description: string;
  pitSafe: boolean;
  pitNote: string;
  missingPolicy: string;
  discoveryEligible: boolean;
  available: boolean;
  unavailableReason: string | null;
  engines: string[];
  categories: string[];
}

export interface FeatureAvailability {
  engine: string;
  groups: Record<string, string>;
  features: FeatureDefinition[];
  availableCount: number;
  unavailableCount: number;
  discoveryEligibleCount: number;
  warmupTradingDays: number;
}

export interface ConditionalDiscoveryRequest {
  permutations?: number;
  seed?: number;
  includeModels?: boolean;
  full?: boolean;
  freezeTop?: number;
  consumeHoldout?: boolean;
  holdoutReason?: string;
  includeConditioned?: boolean;
  includeProp?: boolean;
}

export interface ConditionBucket {
  label: string;
  n: number;
  mean: number;
  median: number;
  winRate: number;
  profitFactor: number | null;
  ciLow: number;
  ciHigh: number;
  lowEdge: number | null;
  highEdge: number | null;
}

export interface UnivariateResult {
  feature: string;
  label: string;
  group: string;
  kind: string;
  pitSafe: boolean;
  discoveryEligible: boolean;
  buckets: ConditionBucket[];
  nUsed: number;
  nMissing: number;
  baselineMean: number;
  bestLabel: string;
  bestMean: number;
  worstLabel: string;
  worstMean: number;
  spread: number;
  effectSize: number;
  permutationP: number | null;
  welchP: number | null;
  monotonic: { spearman: number | null; strictlyMonotonic: boolean; direction: string | null };
  tier: "insufficient" | "exploratory" | "thin" | "adequate";
  warnings: string[];
  qValue: number | null;
  fdrSignificant: boolean;
  /** Independent clusters (rebalance dates, or overlap-based trade blocks)
   * contributing to the BEST bucket -- vs. `nUsed`'s raw row count across all
   * buckets. `null` only for a session computed before the 2026-08-22
   * dependence audit (methodology v1). See ClusterStructure below. */
  effectiveN: number | null;
  inferenceMethod: string;
  methodologyVersion: string;
}

/** How one discovery session's rows were grouped for dependence-aware
 * inference -- see engine/conditional_dependence.py. `effectiveN` is what
 * the freeze gate and every p-value/CI in the session are computed against;
 * `nRaw` is the plain row count and must never be read as though it were an
 * independent-observation count. */
export interface ClusterStructure {
  method: "iid" | "rebalance_cluster" | "overlap_block";
  nRaw: number;
  nClusters: number;
  effectiveN: number;
  clusterSizeMean: number;
  clusterSizeMedian: number;
  clusterSizeMin: number;
  clusterSizeMax: number;
  blockLength: number | null;
  dependencyChains: { components: number; sizeMean: number; sizeMedian: number; sizeMax: number; note: string } | null;
  notes: string[];
}

export interface InteractionCell {
  labels: string[];
  n: number;
  mean: number;
  winRate: number;
  ciLow: number;
  ciHigh: number;
  tier: string;
}

export interface InteractionResult {
  features: string[];
  labels: string[];
  groups: string[];
  cells: InteractionCell[];
  axisLabels: string[][];
  baselineMean: number;
  best: InteractionCell | null;
  worst: InteractionCell | null;
  permutationP: number | null;
  effectSize: number;
  nUsed: number;
  cellsSuppressed: number;
  order: number;
  warnings: string[];
  qValue: number | null;
  fdrSignificant: boolean;
  effectiveN: number | null;
  inferenceMethod: string;
  methodologyVersion: string;
}

export interface Condition {
  feature: string;
  op: string;
  value: number | boolean | string;
  rawEdge: number | null;
  source: string;
  description: string;
}

export interface CandidateHypothesis {
  hypothesisId: string;
  strategyName: string;
  engine: string;
  outcomeColumn: string;
  conditions: Condition[];
  origin: string;
  nConditioned: number;
  nTotal: number;
  retentionPct: number;
  baselineMean: number;
  conditionalMean: number;
  improvement: number;
  ciLow: number;
  ciHigh: number;
  winRate: number;
  baselineWinRate: number;
  profitFactor: number | null;
  effectSize: number;
  permutationP: number | null;
  qValue: number | null;
  fdrSignificant: boolean;
  tier: string;
  groups: string[];
  discoveryStart: string;
  discoveryEnd: string;
  warnings: string[];
  status: string;
  inSample: boolean;
  description: string;
  rawObservations: number | null;
  effectiveN: number | null;
  inferenceMethod: string;
  methodologyVersion: string;
}

export interface DiscoverySession {
  sessionId: string;
  strategyName: string;
  engine: string;
  outcomeColumn: string;
  outcomeLabel: string;
  observations: number;
  discoveryStart: string;
  discoveryEnd: string;
  univariate: UnivariateResult[];
  interactions: InteractionResult[];
  candidates: CandidateHypothesis[];
  accounting: {
    sessionId: string;
    univariateTested: number;
    pairwiseTested: number;
    threeWayTested: number;
    suppressedForSample: number;
    totalExamined: number;
    notes: string[];
  };
  fdr: {
    alpha: number;
    hypothesesTested: number;
    testableHypotheses: number;
    rawSignificant: number;
    fdrSignificant: number;
    method: string;
  };
  redundancy: {
    pairs: { a: string; b: string; spearman: number }[];
    clusters: string[][];
    threshold: number;
    method?: string;
  };
  coverage: {
    key: string;
    label: string;
    group: string;
    present: number;
    total: number;
    coveragePct: number;
    pitSafe: boolean;
    discoveryEligible: boolean;
    missingPolicy: string;
  }[];
  baseline: {
    n: number;
    mean: number;
    median: number;
    std: number;
    standardError: number;
    winRate: number;
    profitFactor: number | null;
    ciLow: number;
    ciHigh: number;
    ciMethod: string;
  };
  concurrency: {
    observations: number;
    averageConcurrent: number;
    maxConcurrent: number;
    capitalUtilizationPct: number;
    distinctEntryDays: number;
    maxEntriesPerDay: number;
    clusteringRatio: number | null;
  };
  warnings: string[];
  seed: number;
  createdAt: string;
  /** `null` only for a session computed with dependence_aware=False (the
   * null-experiment calibration replaying the original pre-audit
   * procedure) -- every ordinary discovery run has this set. */
  clusterStructure: ClusterStructure | null;
  methodologyVersion: string;
}

export interface ResearchSplitSummary {
  strategyName: string;
  boundaries: Record<string, unknown>;
  discoveryObservations: number;
  validationObservations: number;
  finalHoldoutObservations: number;
  finalHoldoutRevealed: boolean;
  finalHoldoutSealedNote: string;
  embargoDays: number;
}

export interface HoldoutStatus {
  strategyName: string;
  consumed: boolean;
  consumptionCount: number;
  firstConsumedAt: string | null;
  lastConsumedAt: string | null;
  entries: { consumedAt: string; reason: string; hypothesisKey: string | null }[];
}

export interface ModelDiagnostic {
  model: string;
  target: string;
  features: string[];
  observations: number;
  coefficients: Record<string, number>;
  coefficientStability: Record<string, number>;
  permutationImportance: Record<string, number>;
  foldScores: number[];
  meanScore: number | null;
  scoreName: string;
  notes: string[];
}

export interface ConditionalStudy {
  strategyName: string;
  engine: string;
  totalObservations: number;
  split: ResearchSplitSummary;
  session: DiscoverySession;
  featureAvailability: FeatureAvailability;
  models: ModelDiagnostic[];
  priorResults: Record<string, PreviouslyTested>;
  warnings: string[];
  holdoutConsumption: HoldoutStatus;
  disclosure: { inSample: string; multipleTesting: string; sampleSize: string };
}

export interface PreviouslyTested {
  hypothesisKey: string;
  versions: number;
  latestRowId: number;
  latestVersion: number;
  status: string;
  reason: string | null;
  frozenAt: string;
  previouslyRejected: boolean;
}

export interface FrozenHypothesis {
  rowId: number | null;
  hypothesisKey: string;
  version: number;
  frozenAt: string;
  strategyName: string;
  engine: string;
  outcomeColumn: string;
  conditions: Condition[];
  featureContract: Record<string, FeatureDefinition>;
  discoverySessionId: string | null;
  discoveryStart: string | null;
  discoveryEnd: string | null;
  discoveryObservations: number | null;
  discoveryMean: number | null;
  discoveryBaselineMean: number | null;
  expectedDirection: "higher" | "lower";
  primaryMetric: string;
  minimumEffect: number;
  minimumObservations: number;
  validationStart: string | null;
  validationEnd: string | null;
  holdoutStart: string | null;
  holdoutEnd: string | null;
  status: string;
  reason: string | null;
  supersedes: number | null;
  supersededBy: number | null;
  contractHash: string;
  description: string;
}

export interface ConditionalEvaluation {
  label: string;
  observations: number;
  totalObservations?: number;
  retentionPct?: number;
  baselineMean: number | null;
  conditionalMean: number | null;
  improvement: number | null;
  ciLow: number | null;
  ciHigh: number | null;
  ciMethod?: string | null;
  winRate: number | null;
  baselineWinRate?: number | null;
  profitFactor: number | null;
  medianOutcome?: number | null;
  effectSize: number | null;
  permutationP: number | null;
  permutationMethod?: string | null;
  minimumEffect?: number;
  minimumObservations?: number;
  expectedDirection?: string;
  directionMatched?: boolean;
  sampleTier?: string;
  sampleWarning?: string | null;
  passed: boolean;
  conclusion: string;
  windowStart?: string;
  windowEnd?: string;
  walkForward?: {
    foldCount: number;
    usableFolds: number;
    positiveFolds: number;
    positiveFoldPct: number | null;
    meanImprovement: number | null;
    medianImprovement: number | null;
    worstFoldImprovement: number | null;
    bestFoldImprovement: number | null;
    improvementStd: number | null;
    retentionMeanPct: number | null;
    retentionStdPct: number | null;
    directionStable: boolean;
    embargoDays: number;
    method: string;
    folds: Record<string, unknown>[];
  };
  integrity?: {
    contractHash: string;
    recomputedHash: string;
    intact: boolean;
    featureDrift: string[];
    featureDriftDetected: boolean;
  };
  decomposition?: FilterDecomposition;
  consumptionReason?: string;
}

export interface FilterDecomposition {
  available: boolean;
  reason?: string;
  mechanisms?: {
    badTradeAvoidance: { lossRateChange: number; removedLosers: number; removedLoserMean: number | null; share: number };
    winnerConcentration: { meanWinChange: number; removedWinners: number; removedWinnerMean: number | null; keptWinRate: number };
    riskReduction: { p05Baseline: number; p05Conditioned: number; p05Change: number; worstBaseline: number; worstConditioned: number; stdBaseline: number; stdConditioned: number | null };
  };
  primaryMechanism?: string;
  note?: string;
}

export interface ConditionedComparisonMetrics {
  trades: number | null;
  tradesPerYear: number | null;
  expectancyR: number | null;
  winRate: number | null;
  profitFactor: number | null;
  cagrPct: number | null;
  sharpe: number | null;
  sortino: number | null;
  maxDrawdownPct: number | null;
  exposurePct: number | null;
  alphaPct: number | null;
  buyHoldReturnPct: number | null;
  p95DrawdownPct: number | null;
  worstDayPct: number | null;
  worst5dPct: number | null;
  annualVolPct: number | null;
  returnOverP95Dd: number | null;
}

export interface ConditionedComparison {
  available: boolean;
  reason?: string;
  comparison?: {
    original: ConditionedComparisonMetrics;
    conditioned: ConditionedComparisonMetrics;
    delta: Partial<Record<keyof ConditionedComparisonMetrics, number | null>>;
    retention: { originalSignals: number; conditionedSignals: number; retentionPct: number | null; note: string };
    concurrency: Record<string, unknown>;
    sampleWarnings: string[];
    costNote: string;
  };
  decomposition?: FilterDecomposition;
  prop?: {
    scenario: string;
    accountRules: { name: string; accountSize: number; maxTotalLossPct: number; dailyLossLimitPct: number; riskBudget: number };
    original: PropArmResult;
    conditioned: PropArmResult;
    delta: Record<string, number | null> | null;
    note: string;
  };
}

export interface PropArmResult {
  label: string;
  available: boolean;
  reason?: string;
  observations?: number;
  sessionCoverage?: number;
  headline?: {
    safeRiskMultiplier: number | null;
    safeFailureProb: number | null;
    conservativeMultiplier: number | null;
    expectedNetPayout: number | null;
    survival12m: number | null;
    dailyLimitFailureProb: number | null;
    totalLossFailureProb: number | null;
    p95DrawdownPct: number | null;
    worstDayPct: number | null;
  };
}

export interface ConditionalVerdict {
  verdict: string;
  headline: string;
  reasons: string[];
  blockers: string[];
  gates: Record<string, unknown>;
  hypothesesExamined?: number;
  fdrSignificant?: number;
  rawSignificant?: number;
  discoveryObservations?: number;
  validationObservations?: number;
}

export interface ConditionalWorkflowResult {
  study: ConditionalStudy;
  notFrozen: { hypothesisId: string; description: string; retentionPct: number; projectedValidationObservations: number; minimumObservations: number; reason: string }[];
  hypotheses: {
    candidate: CandidateHypothesis;
    previouslyTested?: PreviouslyTested;
    frozen?: FrozenHypothesis;
    validation?: ConditionalEvaluation;
    holdout?: ConditionalEvaluation | null;
    conditioned?: ConditionedComparison | null;
    verdict: ConditionalVerdict;
  }[];
  verdict: ConditionalVerdict;
}

export interface LedgerEntry extends FrozenHypothesis {
  results: (ConditionalEvaluation & { stage: string; evaluatedAt: string; passed: number | null })[];
  integrity: { contractHash: string; recomputedHash: string; intact: boolean; featureDrift: string[]; featureDriftDetected: boolean };
  /** Dependence-audit records (see MethodologyAudit) that recomputed THIS
   * hypothesis's evidence under the corrected statistics -- empty for a
   * hypothesis frozen after methodology v2 became the default, or never
   * re-scored. A non-empty list here means the row above may show evidence
   * that was later found to be overstated; read the audit before trusting
   * the original p/q value alone. */
  methodologyAudits: MethodologyAudit[];
}

/** One "prior evidence -> corrected evidence" record from the 2026-08-22
 * dependence audit -- see engine/conditional_ledger.py:record_methodology_audit.
 * Permanent and append-only: never a replacement for the session/hypothesis
 * row it is about. */
export interface MethodologyAudit {
  id: number;
  auditedAt: string;
  strategyName: string;
  sessionId: string | null;
  hypothesisRowId: number | null;
  targetType: "session" | "candidate" | "univariate" | "interaction";
  targetKey: string;
  targetDescription: string | null;
  priorMethodologyVersion: string | null;
  priorInferenceMethod: string | null;
  priorRawN: number | null;
  priorEffectiveN: number | null;
  priorPValue: number | null;
  priorQValue: number | null;
  priorCiLow: number | null;
  priorCiHigh: number | null;
  correctedMethodologyVersion: string;
  correctedInferenceMethod: string;
  correctedRawN: number | null;
  correctedEffectiveN: number | null;
  correctedPValue: number | null;
  correctedQValue: number | null;
  correctedCiLow: number | null;
  correctedCiHigh: number | null;
  materiallyWeakened: number;
  reason: string;
}

async function runConditionalJob<T>(
  strategyName: string,
  body: ConditionalDiscoveryRequest,
  onProgress?: (job: ConditionalJob<T>) => void,
): Promise<T> {
  let job = await request<ConditionalJob<T>>(`/conditional/jobs/${encodeURIComponent(strategyName)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  onProgress?.(job);
  while (job.status === "queued" || job.status === "running") {
    await new Promise((resolve) => window.setTimeout(resolve, 750));
    job = await request<ConditionalJob<T>>(`/conditional/jobs/${job.jobId}`);
    onProgress?.(job);
  }
  if (job.status === "failed") throw new Error(job.error || "Conditional discovery failed");
  if (job.result === null) throw new Error("Conditional discovery completed without a result");
  return job.result;
}


/** A compiled spec rendered back to English by
 * strategies/spec.py:describe_spec. This -- never the model's own summary
 * of what it wrote -- is what the review step displays, so the user
 * approves the rules that will actually run. */
export interface CompiledRules {
  summary: string;
  entry: string[];
  exit: string[];
  stop: string;
  target: string;
  warmupBars: number;
}

export interface SpecParam {
  name: string;
  label: string;
  default: number;
  minimum: number;
  maximum: number;
  step: number | null;
  kind: "int" | "float";
  help: string | null;
}

/** The draft step's response: a validated spec plus everything needed to
 * review it before saving. Nothing is stored until a separate save call. */
export interface StrategyDraft {
  name: string;
  kind: "Day Trading" | "Swing Trading";
  timeframe: string;
  direction: "long" | "short";
  description: string;
  /** Judgment calls the author model made -- approximations, numbers it
   * chose, a side it picked. Worth reading before saving. */
  notes: string;
  /** How many validation round-trips the spec needed. >1 means the first
   * attempt was rejected by the parser and repaired. */
  attempts: number;
  spec: Record<string, unknown>;
  rules: CompiledRules;
  params: SpecParam[];
  /** Non-null when the proposed name is already taken -- saving would 409. */
  nameConflict: string | null;
}

export interface CustomStrategy {
  name: string;
  kind: "Day Trading" | "Swing Trading";
  timeframe: string;
  direction: "long" | "short";
  description: string;
  createdAt: string;
  /** The description this strategy was written from, kept as provenance. */
  prompt: string;
  spec: Record<string, unknown>;
  rules: CompiledRules;
}

export interface CustomStrategyList {
  strategies: CustomStrategy[];
  /** Stored files that no longer parse -- surfaced so a broken definition
   * is visible and deletable rather than silently missing. */
  loadErrors: { filename: string; error: string }[];
  authoringAvailable: boolean;
  authoringUnavailableReason: string | null;
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
  listCustomStrategies: () => request<CustomStrategyList>("/strategies/custom"),
  // One Anthropic API call; can take 30s+ on a complex description. Saves
  // nothing -- the returned draft is reviewed, then saved separately.
  draftStrategy: (description: string) =>
    request<StrategyDraft>("/strategies/custom/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description }),
    }),
  saveCustomStrategy: (spec: Record<string, unknown>, prompt: string) =>
    request<CustomStrategy>("/strategies/custom", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec, prompt }),
    }),
  deleteCustomStrategy: (name: string) =>
    request<{ deleted: string }>(`/strategies/custom/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  deleteBrokenCustomStrategy: (filename: string) =>
    request<{ deleted: string }>(`/strategies/custom-file/${encodeURIComponent(filename)}`, {
      method: "DELETE",
    }),
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
  executionAccountOwnership: () => request<ExecutionAccountStatus>("/live/execution/account-ownership"),
  executionStrategies: () => request<ForwardTestPromotionCandidate[]>("/live/execution/strategies"),
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
  executionDaily: () => request<DailyPerformance>("/live/execution/daily"),
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

  // -- Conditional Edge Discovery -- engine/conditional_edge.py -----------
  conditionalStrategies: () => request<ConditionalStrategies>("/conditional/strategies"),
  conditionalFeatures: (engine: "standard" | "cross_sectional" = "standard") =>
    request<FeatureAvailability>(`/conditional/features?engine=${engine}`),
  // `full=false` (discovery only) is fast enough (~10-30s) to poll every
  // 750ms like a validation job; `full=true` re-runs the backtest twice more
  // (conditioned arm + prop sweep) and can take noticeably longer -- same
  // job/poll shape either way so the caller doesn't need to branch.
  runConditionalDiscovery: (
    strategyName: string,
    request_: ConditionalDiscoveryRequest = {},
    onProgress?: (job: ConditionalJob<ConditionalStudy>) => void,
  ) => runConditionalJob<ConditionalStudy>(strategyName, request_, onProgress),
  runConditionalWorkflow: (
    strategyName: string,
    request_: ConditionalDiscoveryRequest = {},
    onProgress?: (job: ConditionalJob<ConditionalWorkflowResult>) => void,
  ) => runConditionalJob<ConditionalWorkflowResult>(strategyName, { ...request_, full: true }, onProgress),
  freezeConditionalHypothesis: (body: {
    sessionId: string;
    hypothesisId: string;
    minimumEffect?: number;
    minimumObservations?: number;
  }) =>
    request<FrozenHypothesis>("/conditional/hypotheses/freeze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  validateConditionalHypothesis: (rowId: number, body: { sessionId: string; permutations?: number }) =>
    request<ConditionalEvaluation>(`/conditional/hypotheses/${rowId}/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  consumeConditionalHoldout: (
    rowId: number,
    body: { sessionId: string; reason: string; permutations?: number },
  ) =>
    request<ConditionalEvaluation>(`/conditional/hypotheses/${rowId}/holdout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  conditionalConditionedComparison: (
    rowId: number,
    body: { propScenario?: string; propPaths?: number; includeProp?: boolean } = {},
  ) =>
    request<ConditionedComparison>(`/conditional/hypotheses/${rowId}/conditioned`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  conditionalLedger: (strategyName?: string) =>
    request<{ hypotheses: LedgerEntry[] }>(
      `/conditional/ledger${strategyName ? `?strategy=${encodeURIComponent(strategyName)}` : ""}`,
    ),
  conditionalRejected: (strategyName?: string) =>
    request<{ rejected: LedgerEntry[] }>(
      `/conditional/rejected${strategyName ? `?strategy=${encodeURIComponent(strategyName)}` : ""}`,
    ),
  conditionalHoldoutStatus: (strategyName: string) =>
    request<HoldoutStatus>(`/conditional/holdout-status/${encodeURIComponent(strategyName)}`),
  conditionalMethodologyAudits: (strategyName?: string) =>
    request<{ audits: MethodologyAudit[] }>(
      `/conditional/methodology-audits${strategyName ? `?strategy=${encodeURIComponent(strategyName)}` : ""}`,
    ),

  // -- Research Status + Data Blocker dashboard -- engine/research_status.py
  researchStatus: () => request<ResearchStatusDashboard>("/research/status"),
  researchConditionalStatus: (strategyName: string) =>
    request<ResearchStatusRow>(`/research/conditional-status/${encodeURIComponent(strategyName)}`),
  researchForwardStack: () => request<ForwardStackStatus>("/research/forward-stack"),
  researchPropShadows: () => request<PropShadowStatus>("/research/prop-shadows"),
  researchOptimizedDmHourlyShadow: () => request<OptimizedDmHourlyShadowStatus>("/research/optimized-dm-hourly-shadow"),
  researchCapitalEfficiency: () => request<CapitalEfficiencyStatus>("/research/capital-efficiency"),
  researchDataBlockers: () => request<{ blockers: DataBlockerRow[] }>("/research/data-blockers"),
};

/** One tracked research branch's current state -- see
 * engine/research_status.py:ResearchStatusRow. Every field is read live from
 * an existing source of truth (the conditional-edge ledger, the frozen
 * DM/MRM forward-test protocol, universe/PIT status); nothing here is a
 * second authoritative verdict store. */
export interface ResearchStatusRow {
  name: string;
  researchType: string;
  status: string;
  evidenceStage: string | null;
  lastCompletedAction: string;
  developmentPeriod: string | null;
  forwardTestStart: string | null;
  validationStatus: string | null;
  holdoutStatus: string | null;
  primaryBlocker: string | null;
  methodologyVersion: string | null;
  methodologySuperseded: boolean;
  notes: string[];
  causalChain: string[];
  nextAction: string;
}

export interface ResearchStatusSummary {
  activeResearchRuns: number;
  frozenForwardTestCandidates: number;
  validatedStrategies: number;
  rejectedHypotheses: number;
  dataBlockedResearchItems: number;
  methodologyCompleteSystems: number;
}

export interface ResearchStatusDashboard {
  rows: ResearchStatusRow[];
  summary: ResearchStatusSummary;
  statusLabels: string[];
}

export interface ForwardStackSeries {
  key: string;
  series: string;
  type: string;
  status: string;
  sessions: number;
  nav: number | null;
  returnPct: number | null;
  drawdownPct: number | null;
  lastUpdate: string | null;
  history?: { date: string; nav: number }[];
  positionHistory?: { date: string; holdings: Record<string, number>; source?: string }[];
}

export interface ForwardBlendState {
  dmWeight: number;
  mrmWeight: number;
  dmTargetWeight: number;
  mrmTargetWeight: number;
  lastWeightResetDate: string | null;
  nextScheduledReset: string | null;
  combinedHoldings: Record<string, number>;
  fixedCombinedHoldings?: Record<string, number>;
  dmHoldings?: Record<string, number>;
  mrmHoldings?: Record<string, number>;
}

export interface ForwardStackStatus {
  series: ForwardStackSeries[];
  currentBlend: ForwardBlendState | null;
  maturity: { label: string; reached: string[]; next: { sessions: number; label: string } | null };
  alerts: { code: string; severity: string; message: string; scope?: "research_shadow" | "live_execution"; liveExecutionAffected?: boolean }[];
  paperAutomation: {
    enabled: boolean;
    canonicalConfig: boolean | null;
    configuredParams?: Record<string, number | boolean | string>;
    configuredSymbols?: string[];
    validationRunId?: number | null;
    lastCompletedRun?: {
      date: string;
      triggeredAt: string;
      targetWeights: Record<string, number>;
    } | null;
    identity?: StrategyIdentity;
  };
  operations?: {
    lastAttemptAt?: string | null;
    lastSuccessAt?: string | null;
    lastError?: string | null;
    latestCompletedSession?: string | null;
    latestFinalizedSession?: string | null;
    lastSourceSession?: string | null;
    finalizationLagSessions?: number;
    advanceResult?: { status?: string; appendedSessions?: number };
  };
  reconciliation?: { confirmedAmendments: number; rawNavPreserved: boolean; finalizationLagSessions: number };
  separation: { alpacaEquityLabel: string; researchNavLabel: string };
}

export interface PropShadowRow {
  key: string;
  label: string;
  scale: number;
  drawdownRule: "static" | "trailing_to_breakeven";
  sessions: number;
  state: string;
  currentEquity: number;
  netPayout: number;
  grossPayout: number;
  fees: number;
  breaches: number;
  attempts: number;
  failures: number;
  drawdownUtilization: number;
  maximumObservedDrawdown: number;
  dailyLimitUtilization: number;
  annualizedBreachFrequency: number | null;
  distanceToDailyBreach: number | null;
  distanceToDrawdownBreach: number | null;
  history?: { date: string; equity: number }[];
}

export interface SelfFundedShadowRow {
  key: string;
  label: string;
  startingCapital: number;
  fixedExposure: number;
  observations: number;
  sessions: number;
  currentEquity: number;
  netProfit: number;
  returnOnCommittedCapital: number;
  maximumObservedDrawdown: number;
  drawdownPctCommitted: number;
  worstIntradayLoss: number;
  stopoutRule: null;
  propFees: 0;
  payoutSplit: 1;
  history?: { date: string; equity: number }[];
}

export interface PropShadowStatus {
  program: string;
  evidenceClass: "prospective_prop_shadow";
  historicalEvidenceKeptSeparate: boolean;
  samplingIntervalSeconds: number;
  lastSnapshot: string | null;
  samplingAgeSeconds: number | null;
  completedSessions: number;
  maturity: string;
  warnings: string[];
  shadows: PropShadowRow[];
  selfFundedShadows: SelfFundedShadowRow[];
  controlsPreserved: string[];
  fingerprintLocked: boolean;
  selfFundedFingerprintLocked: boolean;
  currentParentPositions?: (LivePosition & { weight?: number })[];
}

export interface OptimizedDmHourlyShadowStatus {
  available: boolean;
  reason?: string;
  key: string;
  strategy?: string;
  activatedAt?: string;
  backfillStart?: string;
  backfillIntent?: string;
  startingEquity?: number;
  currentEquity?: number;
  returnPct?: number;
  lastUpdate?: string | null;
  currentHoldings?: Record<string, number>;
  dailyHistory?: { date: string; equity: number; evidenceClass: "blind_pre_activation_backfill" | "prospective_shadow" }[];
  inProgress?: { date: string; equity: number; evidenceClass: "blind_pre_activation_backfill" | "prospective_shadow"; timestamp: string } | null;
  positionHistory?: { date: string; holdings: Record<string, number>; source: "blind_pre_activation_backfill" | "prospective_shadow" }[];
  backfillMarks?: number;
  prospectiveMarks?: number;
  backfillSessions?: number;
  prospectiveSessions?: number;
  totalTurnoverPct?: number;
  lastAttemptAt?: string | null;
  lastSuccessAt?: string | null;
  lastError?: string | null;
  methodology?: Record<string, string>;
}

export interface CapitalEfficiencyStatus {
  available: boolean;
  reason?: string;
  classification?: {
    result: "depends_on_available_capital" | "prop_clearly_superior" | "self_funded_clearly_superior" | "inconclusive";
    historicalEvidenceGrade: string;
    reason: string;
    selfFundedComparatorB: string;
  };
  historicalClassification?: string;
  prospectiveClassification?: string;
  operatingPoints?: Record<string, {
    key: string; scale: number; drawdownRule: string; effectiveExposure: number;
    prop: {
      netProfit: { expected: number; median: number; p05: number; p01: number };
      fees: { expected: number; median: number; p05: number; p01: number };
      breachProbability12m: number;
    };
    selfFundedA: {
      netProfit: { expected: number; median: number; p05: number; p01: number };
      probabilityAnnualLoss: number;
    };
    breakEven: {
      minimumOwnCapitalToMatchExposure: number;
      ownCapitalForExpectedSelfFundedProfitToEqualProp: number | null;
      ownCapitalForExpectedSelfFundedProfitToReach35000: number | null;
      expectedSelfFundedReturnPerDollar: number;
    };
  }>;
  frontier?: Array<{
    operatingPoint: string; structure: "prop" | "self_funded_a"; accounts: number;
    aggregateEffectiveExposure: number; expectedCapitalCommitted: number;
    expectedNetProfit: number; medianNetProfit: number; probabilityTarget35000: number;
  }>;
  reproductionChecks?: Array<{ operatingPoint: string; maxAbsoluteError: number; passed: boolean }>;
}

/** One declared dataset's actual availability -- see
 * engine/research_status.py:DataBlockerRow. Coverage/PIT-safety facts come
 * straight from engine/universe_registry.py; only `unlocks`/`severity` are
 * this feature's own interpretive metadata. */
export interface DataBlockerRow {
  dataset: string;
  status: string;
  intendedCoverage: string | null;
  actualAvailability: string;
  pitSafe: boolean | null;
  survivorshipFree: boolean | null;
  missingArtifacts: string[];
  unlocks: string[];
  blocksResearch: string[];
  severity: string;
}
