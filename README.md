# Trading Strategy Lab

Trading Strategy Lab is a local research, backtesting, validation, market-monitoring, and Alpaca paper-trading application. It combines a Python/FastAPI backend with a React/TypeScript dashboard and keeps the strategy catalogue, historical experiments, validation evidence, forward tests, and broker activity separate enough that an exploratory result cannot silently become a live-trading rule.

The application is designed around one principle: a result is only as trustworthy as its data lineage, timing assumptions, statistical power, and execution controls. It therefore records failed tests, refuses invalid windows, labels survivorship-biased universes, keeps point-in-time (PIT) datasets fail-closed, and requires an explicit per-strategy opt-in before any paper order can be sent.

> This is research software, not investment advice. Backtests and shadow ledgers are simulations. The only order-routing path in this repository targets an Alpaca **paper** account and is restricted to an explicit allowlist.

## Contents

- [What the application does](#what-the-application-does)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [What starts with the API](#what-starts-with-the-api)
- [Dashboard and user interface](#dashboard-and-user-interface)
- [Strategy catalogue](#strategy-catalogue)
- [Backtest engines and methodology](#backtest-engines-and-methodology)
- [Data sources, caching, and timing](#data-sources-caching-and-timing)
- [Universe registry](#universe-registry)
- [Validation and research governance](#validation-and-research-governance)
- [Forward tests, shadow books, and paper execution](#forward-tests-shadow-books-and-paper-execution)
- [Market-research tools](#market-research-tools)
- [Persistence and generated artifacts](#persistence-and-generated-artifacts)
- [API reference](#api-reference)
- [Command-line and research utilities](#command-line-and-research-utilities)
- [Tests and verification](#tests-and-verification)
- [Repository map](#repository-map)
- [Known limitations and safety boundaries](#known-limitations-and-safety-boundaries)

## What the application does

At a high level, the app:

- Runs daily and intraday strategies over registered or explicitly overridden universes.
- Supports four different simulation shapes: independent per-symbol trades, a shared-capital portfolio replay, cross-sectional rebalancing, and two-leg pairs trading.
- Calculates trade, portfolio, benchmark, exposure, excursion, factor, stability, concentration, and statistical-power evidence.
- Maintains canonical runs separately from parameter/date/universe experiments.
- Imports the established strategy names from the project catalogue and also supports safe, schema-based custom strategies described in natural language.
- Stores run history and validation reports in SQLite rather than treating the latest browser session as the research record.
- Implements preregistration, search-family accounting, frozen hypotheses, one-time validation/holdout consumption, methodology audits, and governed forward-test proposals.
- Tracks multiple forward evidence modes: real Alpaca paper execution, prospective synthetic shadows, prop-account shadows, self-funded shadows, and observation-only regime studies.
- Displays the current SPY regime, market breadth, sector performance, trend-template results, movers, watchlist quotes, a multi-factor screener, and recent insider purchases.
- Fetches and validates SEC Form 4 purchases using the public filing timestamp—not the insider's earlier transaction date—as the first tradeable information date.
- Offers a backtest-scoped assistant that can inspect the current result's real trades and metrics, plus a natural-language strategy authoring workflow. Both are optional Anthropic-powered features.
- Contains research-only PIT reconstruction pipelines for the Dow and S&P 500. These remain disabled until their declared identity, membership, and price-coverage gates pass.

## Architecture

```text
React 19 + TypeScript + Vite (webapp/, port 5174)
                     |
                     | /api proxy
                     v
FastAPI application (api/main.py, port 8794)
       |             |                |
       v             v                v
strategy engines   research layer   live/paper layer
engine/*.py        SQLite ledgers    Alpaca clients
strategies/*.py    JSON artifacts    schedulers/guards
       |
       v
local immutable-ish caches and datasets (data/)
yfinance daily data, Alpaca intraday/IEX data, SEC EDGAR data
```

The frontend never imports pandas or strategy code. It calls the JSON API. `api/main.py` performs request validation and serialization; strategy rules live in `strategies/`; calculations, storage, data access, research controls, and broker integration live in `engine/`.

The established catalogue is anchored by `strategy_tracker.xlsx`. Tests ensure registered production/catalogue names continue to match the workbook, while explicitly named research-only and user-authored strategies remain outside that boundary.

## Quick start

### Prerequisites

- Python with `pip`
- Node.js and npm
- Optional Alpaca paper credentials for quotes, deep intraday history, scanning, and paper execution
- Optional Anthropic API key for result chat and natural-language strategy drafting

### Install

```powershell
python -m pip install -r requirements.txt
Set-Location webapp
npm install
Set-Location ..
Copy-Item .env.example .env
```

Leave optional values blank if those integrations are not needed. Never commit `.env`.

### Run both services

```powershell
python start.py
```

This starts FastAPI at `http://localhost:8794` and Vite at `http://localhost:5174`. Press `Ctrl+C` to stop both.

On Git Bash/Linux/macOS, use:

```bash
./start.sh
```

`start.sh` defaults to this workstation's `trading` conda interpreter; override it elsewhere:

```bash
PYTHON_BIN=/path/to/python ./start.sh
```

You can also run the services separately:

```powershell
python -m uvicorn api.main:app --host 0.0.0.0 --port 8794
Set-Location webapp
npm run dev
```

FastAPI's interactive schema is available at `http://localhost:8794/docs`.

> `start.ps1` is a legacy launcher that starts the API on port 8791, while the current Vite proxy targets port 8794. Prefer `start.py`, `start.sh`, or update both ports together before using that script.

### Environment variables

| Variable | Purpose |
|---|---|
| `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` | Preferred Alpaca credentials. The code is configured for paper trading. |
| `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_SECRET_KEY` | Paper-key fallback names. |
| `ALPACA_PAPER_API_SECRET` | Accepted compatibility alias. |
| `APCA_API_KEY_ID` / `APCA_API_SECRET_KEY` | Accepted Alpaca-standard aliases. |
| `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET` / `ALPACA_SECRET` | Additional compatibility aliases. |
| `ANTHROPIC_API_KEY` | Enables result-scoped chat and natural-language strategy authoring. Each use makes a paid API call. |
| `SEC_EDGAR_USER_AGENT` | Optional descriptive SEC user agent. The built-in fallback identifies this research application and a contact address. |

Restart the API after editing `.env`.

## What starts with the API

Starting FastAPI does more than expose HTTP routes. It creates three resilient background loops:

1. **Live signal scanner — every five minutes.** It checks the registered day-strategy signals against available 5-minute data and writes alerts to `logs/signals.db`. It returns without action when Alpaca is not configured or the market is closed. Signal alerts do not place orders.
2. **Execution and shadow scheduler — every hour.** It reconciles open paper orders, checks only explicitly enabled allowlisted strategies for a due rebalance, then independently advances the DM/MRM blend, optimized-DM regime labels, optimized-DM hourly shadow, and plain-versus-residual momentum shadow. A shadow failure cannot submit an order, and an execution failure does not prevent the observation-only ledgers from being attempted.
3. **Prop-account snapshot collector — every five minutes.** It records observation-only Alpaca account-equity snapshots and advances synthetic prop/self-funded ledgers. Closed-market or offline intervals remain blank; they are not backfilled.

The loops exist only while the API process is running. They are not a substitute for an always-on deployment or operating-system scheduler.

## Dashboard and user interface

The UI has three persistent destinations.

### Dashboard

The dashboard is the operational overview. Cards open full views in modals without discarding the overview. It includes:

- An operational-integrity strip combining API, Alpaca, ownership, strategy-fingerprint, scheduler-freshness, and shadow-reconciliation alerts.
- Current SPY market regime, breadth score, strongest/weakest sectors, and trend-template pass count.
- Alpaca paper-account equity, cash, buying power, positions, open/recent orders, active strategy, execution ownership, and recent rebalance runs.
- Day-by-day paper performance and real-versus-shadow strategy account comparisons.
- Prospective forward stack, live shadow marks, DM/MRM research shadows, optimized-DM hourly shadow, prop-account simulations, and capital-efficiency views.
- Movers, recent insider purchases, composite screener results, and a symbol/watchlist view with delayed quotes and price charts.
- A preview-only daily digest combining regime, breadth, movers, insider purchases, and a research disclaimer.

Expensive resources are intentionally on demand or cached. Market scanning is shared between the dashboard and modal; SEC refresh and digest generation remain behind explicit actions.

### Research

The Research page presents one row per tracked branch and distinguishes:

- historical development evidence;
- validation and final-holdout state;
- prospective synthetic evidence;
- real brokerage execution evidence;
- blocked datasets or methods;
- maturity, causal chain, current conclusion, next checkpoint, and primary blockers.

It also exposes conditional-edge status, data blockers, forward-test comparisons, and the distinction between an executable strategy and an observation-only research branch.

### Strategies

The Strategies workspace provides:

- Searchable strategy catalogue grouped by established, research-only, custom, unavailable, and archived state.
- Registered-universe selection or advanced custom-symbol selection, date overrides, and validated parameter overrides.
- Canonical-versus-exploratory disclosures: changing a parameter, date range, or universe creates a separate experiment and never overwrites canonical evidence.
- Engine-specific result views for standard, cross-sectional, and pairs runs.
- Metrics, equity curves, trade details, MFE/MAE and exit-efficiency diagnostics, per-symbol performance, shared-capital portfolio replay, benchmark comparisons, and run history.
- Asynchronous validation jobs with progress and reuse of a recently completed identical job.
- Conditional-edge discovery, frozen hypotheses, validation/holdout actions, and rejected-hypothesis history.
- Governed forward-experiment proposals tied to a specific validation run and frozen configuration.
- Optional result-scoped chat grounded in tool calls against the currently open result.
- Optional natural-language strategy drafting. The model emits a constrained JSON rule specification, never Python code; the server validates it and requires a separate review/save step.

## Strategy catalogue

### Day strategies

All day strategies use the standard per-symbol engine and intraday bars.

| Strategy | Declared timeframe | Status note |
|---|---:|---|
| Opening Range Breakout (ORB) | 5 min | Archived from default view; still runnable |
| VWAP Bounce / Reversion | 5 min | Archived; still runnable |
| Momentum / Gap and Go | 5 min | Active |
| Scalping (3–5 min) | 5 min | Archived; still runnable |
| Mean Reversion Scalp | 1 min | Archived; still runnable |
| News Fade | 5 min | Archived; still runnable |
| Range Trading | 15 min | Archived; still runnable |
| Pivot-Level ETF Reversal | 5 min | Active; ETF-oriented |

### Swing and event strategies

| Strategy | Engine | Status note |
|---|---|---|
| Pullback to 21 EMA | Standard daily | Active |
| Breakout from Consolidation | Standard daily | Active |
| 9/21 EMA Crossover | Standard daily | Active |
| Oversold Bounce (RSI<30) | Standard daily | Active |
| Fibonacci Retracement Entry | Standard daily | Archived; still runnable |
| Earnings Momentum / Gap-Hold | Standard daily | Active; earnings-aware |
| Connors Mean Reversion (RSI2) | Standard daily | Active |
| Internal Bar Strength (IBS) | Standard daily | Active |
| Gap Fade (daily) | Standard daily | Archived; still runnable |
| Turnaround Tuesday | Standard daily | Archived; still runnable |
| Sector Rotation Play | Standard, weekly concept | Structural sector-ETF universe; benchmark injected at construction |
| Post-Earnings Drift (PEAD) | Standard daily | Uses per-symbol positive earnings-surprise dates |
| Overnight Hold | Bespoke close-to-open | Uses a dedicated overnight engine path |
| Anchored VWAP Breakout | Bespoke daily | Uses earnings-gap anchors |
| Dual Momentum Pullback Swing | Standard daily | User-defined research variant, not workbook-established |

### Whole-universe and pairs strategies

| Strategy | Engine | Status note |
|---|---|---|
| Dual Momentum | Cross-sectional | Monthly ranking by default; paper-order allowlisted |
| 52-Week-High Momentum | Cross-sectional | Frozen research variant; paper-order allowlisted |
| Market-Residual Momentum | Cross-sectional | Frozen research shadow; no Alpaca order path |
| Pairs / Stat Arb | Pairs | Archived from default view; still runnable |

### Frozen event research

These hypotheses are explicit research-only strategies and are run under the frozen-event protocol:

- Negative Return + Volume Shock Reversal
- Volume-Shock Continuation (Long)
- Volume-Shock Continuation (Short)
- MAX Lottery-Return Reversal (Short)
- Volatility-Conditioned Pullback

### Registered but unavailable hypotheses

The UI keeps these visible and explains why they cannot be tested honestly:

- **Earnings Announcement Return Drift (EAR):** no complete PIT historical event ledger.
- **Sector-Relative Momentum:** no PIT historical sector-classification ledger.
- **Overnight Idiosyncratic Shock Reversal (Long/Short):** observing and filling the same daily open is not executable with daily bars.

### Other implemented research modules

`Insider Buying`, `Dividend Hybrid`, and the `Weighted Voting Ensemble` are implemented in strategy/engine modules and have dedicated comparison or demo runners, but they are not members of the primary `ALL_STRATEGY_NAMES` catalogue shown above. Their presence in code must not be read as automatic promotion into canonical evidence.

Archived strategies are hidden by default, not deleted. See [ARCHIVED_STRATEGIES.md](ARCHIVED_STRATEGIES.md) for the measured retirement record and un-archiving procedure.

## Backtest engines and methodology

### Standard per-symbol engine

`engine/backtest.py` adapts library-independent `Strategy` objects to `backtesting.py`.

- Each symbol is initially simulated in an independent `$10,000` cash account.
- Default risk is 1% of current equity per trade, capped by available cash; margin/leverage is not assumed.
- A minimum of 30 bars is required before entry logic runs.
- Entries and bracket validity account for the modeled spread.
- Stop/target and explicit exit logic are strategy-defined.
- The initial risk per share is captured at order time so R-multiples remain valid after a stop order closes.
- Trade excursions calculate MFE, MAE, realized R, exit efficiency, and loss-realization ratios.
- Matched-benchmark annotations compare each trade with SPY over the same holding interval.

### Shared-capital portfolio replay

`engine/portfolio.py` replays the standard engine's completed trades chronologically against one cash pool. This provides correlation-aware equity and drawdown, capital-constrained position sizing, and a concurrent-position cap.

It deliberately does not rebuild the bar-level fill engine. Open positions are valued at cost basis for sizing new entries rather than continuously marked at every event, and shorts reserve notional like longs instead of using a detailed margin model. These are conservative disclosed simplifications.

### Cross-sectional engine

`engine/cross_sectional.py` ranks the whole universe and holds target weights between rebalances.

- Monthly is the default; supported schedules also include weekly, daily, semimonthly, quarterly, and every 20 sessions.
- Every decision uses data strictly before the execution session and fills at that session's open.
- Fractional shares are supported.
- Costs apply only to traded weight deltas.
- Warmup is explicit. Missing required lookback raises or records `InsufficientHistory`; it is never silently treated as a valid full-universe result.
- Dynamic PIT membership and security labels can be attached when a compatible loader exists.

### Pairs engine

`engine/pairs.py` tests candidate pairs with Engle–Granger cointegration, selects the lowest qualifying p-value, and trades mean reversion in the spread with roughly market-neutral long/short exposure.

- The first half of the requested window is the training/selection period.
- Only the second half is used to measure trading performance.
- At least 60 overlapping bars are required and the default cointegration significance level is 5%.
- Only one pair is held at a time; current equity is split across the two legs.
- Borrow cost and transaction costs are tracked separately.

### Bespoke paths

PEAD receives real per-symbol earnings dates, Overnight Hold uses close-to-next-open semantics, Anchored VWAP uses per-symbol event anchors, sector rotation receives SPY bars at construction, and frozen-event hypotheses use their own pre-registered runner. These are intentionally not forced through an incompatible generic abstraction.

### Metrics

The standard engine reports trades, wins/losses, win rate, average win/loss in R, expectancy, profit factor, total return, CAGR, drawdown, Sharpe, Sortino, alpha/beta, exposure, costs, buy-and-hold return, and matched-SPY evidence. Portfolio engines report return/CAGR, drawdown, Sharpe/Sortino, costs, turnover/exposure, benchmark gap, and engine-appropriate diagnostics.

Important interpretation rules include:

- Fewer than 30 trades is labeled a small sample.
- Sharpe/Sortino are withheld below 30 invested days because sparse exposure can make them degenerate.
- The shortlist hurdle is not expectancy alone: risk-adjusted performance and benchmark evidence are required.
- Invalid windows, incomplete warmup, unavailable risk-adjusted metrics, superseded metric versions, and underpowered designs are unrankable rather than treated as poor measured returns.
- Risk-free calculations use cached `^IRX` (13-week Treasury bill yield) data.
- Minimum detectable alpha and effective independent bets are exposed so a positive estimate is not mistaken for an identified edge.

## Data sources, caching, and timing

### Prices

- **Daily OHLCV:** yfinance, normalized to exchange-session dates and cached as `data/{SYMBOL}_1d.parquet`.
- **Intraday OHLCV:** Alpaca market-data API for supported intervals (`1m`, `5m`, `15m`, `30m`, `60m`/`1h`) when configured. Regular-hours bars are normalized to America/New_York. Free-tier IEX data is treated as delayed, with a 16-minute recent-data cutoff.
- **Intraday fallback:** yfinance when Alpaca is unavailable, subject to its roughly 60-day limit; requests are clamped to 58 days.
- **Cache updates:** settled history is preserved while a short tail is refetched so late-finalizing recent sessions can be corrected.
- **Earnings:** yfinance earnings-date history, cached per symbol; positive surprises seed PEAD.
- **Fundamentals:** current yfinance snapshots cached as JSON. They are explicitly prohibited as historical PIT fundamentals and are used only where snapshot semantics are disclosed.
- **Quotes/account/orders:** Alpaca/IEX when credentials are present.
- **Insider filings:** SEC EDGAR Form 4 XML/Atom data cached in SQLite.

Backtest engines do not make arbitrary provider calls inside the simulation loop. Data access and cache behavior are centralized in `engine/data.py` and related provider modules.

### Default windows and symbol pools

- Daily default: trailing five years.
- Intraday default request: trailing 730 days; actual measured coverage is reported separately from the requested window.
- Main executable equity roster: 29 names from the July 2021 Dow roster. WBA is intentionally not replaced after becoming unavailable.
- Additional mechanical mid-cap and small-cap samples are disclosed as current-constituent approximations, not true PIT universes.
- Sector and broad ETF sets support ETF-oriented strategies and market views.
- `RESEARCH_UNIVERSE` is the deduplicated current display/scanning universe. It must not be reused as a historical backtest universe.

## Universe registry

Universe definitions live in `universes/*.json`. Each definition declares asset class, membership semantics, symbols or ledger path, per-symbol coverage, cost model, benchmarks, applicable evidence gates, and whether it is runnable/selectable.

| ID | Description | Availability |
|---|---|---|
| `dow_pit` | Existing 29-symbol executable Dow roster; partial reconstruction | Runnable/selectable |
| `dow_pit_extended_v1` | Fail-closed 30-member PIT Dow reconstruction targeted for 2000-01-01 through 2026-08-31 | **Blocked** pending complete identity/price audit and runner wiring |
| `sp500_current` | 503 current S&P 500 securities as of 2026-08-13 | Runnable; historical tests are survivorship-biased |
| `sp400_current` | 400 current S&P MidCap 400 securities | Runnable; historical tests are survivorship-biased |
| `sp600_current` | 602 current S&P SmallCap 600 securities | Runnable; historical tests are survivorship-biased |
| `sp500_pit` | Research-only PIT S&P 500 reconstruction | **Blocked** on delisted-price and reused-ticker identity audit |
| `sp500_pit_free_v1` | Free PIT reconstruction from independent public membership trackers plus yfinance | **Blocked**; no window cleared the preregistered 98%/95%-of-dates coverage rule and ledger is not wired into execution |
| `sp400_pit` | Mechanical 27-stock current-constituent sample | Runnable but not selectable; PIT unresolved |
| `sp600_pit` | Mechanical 26-stock current-constituent sample | Runnable but not selectable; PIT unresolved |
| `sp500_proxy` | SPY only | Runnable; tests timing, not constituent selection |
| `international_markets` | Eight US-listed country/region ETFs | Runnable/selectable |
| `futures_market_proxies` | Seven ETF proxies for equity indexes, gold, oil, Treasuries, and USD | Runnable/selectable; does not model futures contracts, rolls, or margin |
| `crypto_majors` | BTC, ETH, and SOL quoted in USD | Runnable/selectable for compatible daily strategies |
| `us_all_stocks_pit` | Dynamic permanent-ID US common-stock universe, 1996 onward | **Blocked** until licensed PIT security master and delisting-inclusive prices are installed |

Strategy-specific checks further restrict invalid combinations: US-session/event strategies are blocked on crypto, whole-universe engines require multiple instruments, pairs searches are capped away from huge quadratic universes, and frozen Dow research cannot be silently moved to a different universe.

### PIT dataset work

The repository contains diagnostic/build pipelines for:

- S&P 500 membership construction, independent source cross-checking, symbol maps, free-price fetching, coverage audits, missing-price triage, distress/removal concentration, and blocker concentration.
- Extended Dow membership intervals, identity-safe legacy price recovery, provenance metadata, and an unchanged fail-closed coverage audit.
- A licensed-data contract for a future all-US-stock PIT security master.

These tools produce evidence; they do not lower thresholds, patch identity by ticker resemblance, or make a blocked universe runnable. In particular, successor securities must not be mapped across equity-extinguishing reorganizations (for example old GM to current GM, old Kodak to KODK, or DWDP to DOW), and post-inception securities do not receive fabricated warmup history.

## Validation and research governance

Every backtest can be followed by a machine-enforced validation job. The report is dimensional—failed gates are not averaged into a flattering composite score—and can include:

- data coverage, measured versus requested window, stale/cache checks, and PIT membership validity;
- causality and event-timing contracts;
- trade count, invested time, warmup completeness, and statistical power;
- return, cash hurdle, SPY/buy-and-hold comparison, and matched-holding-period benchmark evidence;
- factor regression, alpha/beta, residual diagnostics, and regime dependence;
- parameter-neighbor stability, rolling-window stability, and historical replication;
- concentration by symbol/position/rebalance and effective independent bets;
- modeled costs, fill calibration, turnover, borrow cost, and execution feasibility;
- multiple-testing/search-family burden and lifecycle stage.

The research lifecycle supports:

1. exploratory run;
2. preregistered development/validation specification;
3. frozen candidate or hypothesis;
4. one-time validation slice;
5. permanently recorded final-holdout consumption;
6. governed forward experiment with immutable configuration hash;
7. prospective observations and conclusion.

Conditional Edge Discovery searches declared PIT-safe features, logs discovery sessions, freezes exact conditions, and keeps rejected hypotheses. It records corrected-versus-prior statistical methodology audits rather than rewriting history.

Primary research programs represented in `research/` include frozen Dual Momentum, the frozen DM/MRM volatility-scaled blend, conditional-edge batches, Dual Momentum concentration/rank-depth/replacement-quality/stock-characteristic/regime/underperformance studies, MRM attribution and drawdown studies, earnings-momentum robustness, momentum persistence/reversal, prop-versus-self-funded scaling, plain-versus-residual momentum forward comparison, PIT data requirements, and the blocked free S&P/extended Dow datasets.

See [FROZEN_DUAL_MOMENTUM.md](FROZEN_DUAL_MOMENTUM.md), [FROZEN_DM_MRM_VOL_SCALED.md](FROZEN_DM_MRM_VOL_SCALED.md), and [research/frozen_research_report.md](research/frozen_research_report.md) for the frozen protocols and their amendments. `LESSONS.md` is the detailed decision journal; it is intentionally append-oriented.

## Forward tests, shadow books, and paper execution

The app keeps evidence modes explicit:

| Mode | What it means | Can place orders? |
|---|---|---:|
| Brokerage execution | Observed Alpaca paper-account fills/equity | Yes, within controls |
| Research shadow | Prospective synthetic NAV from frozen rules | No |
| Prop shadow | Synthetic prop-account constraints applied to observed parent stream | No |
| Self-funded shadow | Synthetic self-funded account on the same observed stream | No |
| Historical study | Backfilled/development analysis | No |
| Closed/blocked | No active valid forward evidence | No |

Tracked forward configurations include optimized DM daily execution, an optimized-DM hourly shadow, canonical DM, canonical MRM, fixed 50/50 and volatility-scaled DM/MRM blends, optimized-DM 0.20x/0.25x prop shadows, corresponding self-funded shadows, a prospective optimized-DM regime ledger, and plain-versus-residual momentum. Earnings Momentum remains data-blocked for forward evidence.

### Paper-execution safeguards

- Only `Dual Momentum` and `52-Week-High Momentum` are in the low-level Alpaca paper-order allowlist.
- Automation is off by default and enabled per strategy, never globally.
- Enabling requires an explicit account-inception policy (`adopt` inherited positions or `flatten` first).
- Strategy configuration is fingerprinted; mismatches are surfaced and block unsafe continuation.
- The system records account ownership so one account is not silently attributed to multiple strategies.
- Validation/forward-test gates are checked. A paper-only bypass requires an explicit logged reason; there is no production-capital override path.
- Rebalances are idempotently claimed so retries do not create duplicate real attempts.
- Market-hours, buying-power, position, order, and live-risk checks run before submission.
- Open orders are reconciled and fill/slippage calibration is recorded.
- The kill switch can stop automation; optional flattening is a separate explicit choice.
- Manual “rebalance now” uses the same guarded execution path as the scheduler.
- Research shadows have no import/call path that can submit Alpaca orders.

## Market-research tools

### Market overview

Builds current SPY regime, sector performance, market breadth, and trend-template participation across the research universe.

### Screener

Combines valuation, quality, growth/momentum, and risk features into normalized component and composite scores. Current fundamentals are suitable for today's screen only; they are not used as historical PIT facts.

### Movers and watchlist

Shows gainers, losers, momentum streaks, delayed quotes, daily change, offline metadata, and local price history for tracked symbols.

### Insider purchases

The EDGAR pipeline stores non-derivative Form 4 transaction-code `P` purchases, deduplicates by filing accession, throttles below the SEC's 10 requests/second ceiling, and retries failures. Signal time is the SEC acceptance timestamp; after-close/weekend/holiday filings roll to the next trading session. Validation checks no-lookahead behavior, after-hours handling, transaction filters, universe coverage, and filing lags.

### Digest

Composes a plain-text/structured preview from regime, breadth, top movers, and recent insider buys. It has no email transport, SMTP credentials, or scheduler; `GET /api/digest/preview` is preview-only.

### Result chat and strategy authoring

Result chat is stateless and scoped to one browser-held backtest result. The assistant can request lists/details of trades, run metrics, and per-symbol summaries; it cannot inspect another run, live markets, or the internet.

Natural-language authoring converts a description into a constrained `StrategySpec`. Only whitelisted indicators, comparisons, stops, targets, offsets, and timeframes can be expressed. Invalid specs are retried against validator feedback and unsupported ideas are declined. Saved custom strategies remain exploratory and their aggregate run history survives definition deletion.

## Persistence and generated artifacts

| Location | Contents |
|---|---|
| `logs/runs.db` | Standard runs, portfolio runs, validation reports, research experiments/equity curves, conditional sessions/hypotheses/results/holdout consumption/audits, forward experiments/observations, universe sweeps, frozen-neighbor results, schema metadata |
| `logs/execution.db` | Automation configuration, rebalance runs, orders, brokerage accounts, execution-account ownership, integrity events |
| `logs/signals.db` | Live scanner alerts |
| `logs/prop_forward.db` | Raw account snapshots, prop shadow marks/events/state/outcomes, self-funded marks/state |
| `logs/dm_regime_forward_v1.db` | Prospective optimized-DM market-state observations |
| `logs/*.json` | Append-oriented or atomic shadow NAV, decisions, amendments, scheduler operations, and live research artifacts |
| `data/*_1d.parquet`, `data/*_5m.parquet`, etc. | Local OHLCV cache |
| `data/*_earnings.parquet` | Earnings-event cache |
| `data/*_fundamentals.json` | Current fundamental snapshots |
| `data/edgar_form4.db` | Parsed Form 4 purchases and fetched accessions |
| `data/custom_strategies/` | Validated user-authored strategy specifications and prompts |
| `data/sp500_pit_free/` | Free PIT membership, price, identity, provenance, and audit artifacts |
| `data/dow_pit_extended/` | Extended Dow membership, recovered legacy prices, provenance, and coverage reports |
| `reports/` | Human-readable research and audit outputs |
| `research/*.json` | Preregistrations, protocols, and frozen research contracts |

Most caches, logs, databases, credentials, and generated reports are intentionally ignored by Git. Do not delete or rewrite them casually: several are append-only evidence or live operational state.

## API reference

All routes are under `/api`. Request/response schemas are also available in FastAPI's `/docs` UI.

### Catalogue, authoring, and universes

- `GET /strategies` — catalogue plus latest canonical evidence and implementation state.
- `GET /params/{strategy_name}` — validated parameter schema and defaults.
- `GET /strategies/custom` — saved and broken custom-strategy entries.
- `POST /strategies/custom/draft` — draft a validated spec from natural language; does not save.
- `POST /strategies/custom` — revalidate and save a reviewed spec.
- `DELETE /strategies/custom/{strategy_name}` — delete a custom definition, preserving run history.
- `DELETE /strategies/custom-file/{filename}` — remove a stored definition that cannot load.
- `GET /universes` — full registered-universe metadata and availability.
- `GET /universe/pools` — large/mid/small pools used by advanced random-sample controls.

### Backtests, history, and validation

- `POST /backtest/{strategy_name}` — standard/bespoke strategy backtest with optional symbols, dates, parameters, or registered universe.
- `POST /backtest/cross-sectional/{strategy_name}` — whole-universe ranking/rebalance engine.
- `POST /backtest/pairs/{strategy_name}` — pair-selection and spread-trading engine.
- `GET /history/{strategy_name}` — standard-run history.
- `GET /history/portfolio/{strategy_name}` — cross-sectional/pairs portfolio history.
- `POST /validation/jobs/{engine}/{strategy_name}` — start or reuse an asynchronous validation job.
- `GET /validation/jobs/{job_id}` — progress/result for a validation job.
- `GET /research/spec/{strategy_name}` — canonical research specification.
- `GET /research/data-quality/{strategy_name}` — data-quality evidence for the strategy.
- `GET /research/experiments/{strategy_name}` — experiment and search-family history.

### Conditional research

- `GET /conditional/strategies`
- `GET /conditional/features`
- `POST /conditional/jobs/{strategy_name}`
- `GET /conditional/jobs/{job_id}`
- `POST /conditional/hypotheses/freeze`
- `POST /conditional/hypotheses/{row_id}/validate`
- `POST /conditional/hypotheses/{row_id}/holdout`
- `POST /conditional/hypotheses/{row_id}/conditioned`
- `GET /conditional/ledger`
- `GET /conditional/rejected`
- `GET /conditional/sessions/{strategy_name}`
- `GET /conditional/holdout-status/{strategy_name}`
- `GET /conditional/methodology-audits`

These routes discover candidates, freeze exact hypotheses, consume validation/final-holdout slices, compare conditioned versus original strategies, and preserve the complete accepted/rejected ledger.

### Research and forward evidence

- `GET /research/status` — cross-program research dashboard.
- `GET /research/conditional-status/{strategy_name}` — authoritative conditional-edge state.
- `GET /research/data-blockers` — installed/missing dataset contracts.
- `POST /research/forward/propose` — create a governed forward experiment from a selected historical validation run without enabling trading.
- `GET /research/forward/{strategy_name}` — forward experiment state.
- `POST /research/forward/{experiment_id}/observations` — append a prospective observation.
- `GET /research/forward-stack` — normalized frozen forward NAV and operational state.
- `GET /research/plain-vs-residual-forward` — prospective Plain Momentum versus MRM state.
- `GET /research/prop-shadows` — optimized-DM prop and self-funded shadow ledgers.
- `GET /research/optimized-dm-hourly-shadow` — hourly observation-only shadow.
- `GET /research/shadow-live-marks` — in-progress quote-based marks for shadow holdings.
- `GET /research/capital-efficiency` — frozen historical capital-efficiency comparison.
- `GET /research/dm-regime-forward` — prospective market-state labels for optimized DM.

### Live scanning and paper execution

- `GET /live/signals` — recent entry-signal alerts.
- `POST /live/scan` — run one scanner cycle; no order routing.
- `GET /live/account` — Alpaca paper account, positions, and recent orders.
- `GET /live/execution/account-ownership` — owner, attribution, and integrity state.
- `GET /live/execution/calibration` — observed fill/slippage calibration.
- `GET /live/execution/config` — per-strategy automation settings.
- `POST /live/execution/config` — guarded opt-in/opt-out and inception configuration.
- `GET /live/execution/strategies` — eligible strategies and evidence mode.
- `GET /live/execution/runs` — recent rebalance attempts.
- `GET /live/execution/summary` — starting baseline and real-cycle summary.
- `GET /live/execution/daily` — daily paper performance.
- `GET /live/execution/orders` — recorded execution orders.
- `POST /live/execution/rebalance-now` — guarded manual scheduler-equivalent trigger.
- `GET /live/execution/kill-switch` — kill-switch state.
- `POST /live/execution/kill-switch` — activate; optional flatten request is explicit.
- `POST /live/execution/kill-switch/deactivate` — deactivate.
- `GET /live/forward-test` — append-only frozen forward-test state.

### Market tools and assistant

- `GET /market` — regime, sector performance, breadth, and trend template.
- `GET /symbols` — tracked symbol metadata and delayed quote summary.
- `GET /symbols/{ticker}` — symbol details and price history.
- `GET /quotes?symbols=AAPL,MSFT` — delayed IEX quotes.
- `GET /screener` — current composite screen.
- `GET /movers` — gainers, losers, and streaks.
- `GET /insider/recent` — cached qualifying purchases.
- `GET /insider/status` — refresh/cache status.
- `POST /insider/refresh` — explicit 90-day EDGAR refresh.
- `GET /digest/preview` — build the unsent daily digest.
- `POST /chat` — ask about the supplied current backtest result.

## Command-line and research utilities

### General backtesting

```powershell
python -m engine.cli --strategy "Dual Momentum"
python -m engine.cli --strategy "Pullback to 21 EMA" --portfolio
python -m engine.cli --all
```

`--all` runs the registered catalogue, including research-only/unavailable entries where the registry provides an explicit result/refusal. Large sweeps can fetch substantial data and take a long time.

### Data and universe construction

- `build_universe_registry.py`, `sync_sp_constituents.py` — construct/synchronize universe declarations.
- `build_sp500_pit_ledger.py` — base S&P PIT membership work.
- `build_sp500_pit_free_membership.py`, `build_sp500_pit_free_symbol_map.py`, `build_sp500_pit_free_bundle.py` — reproducible free-PIT inputs and bundle.
- `fetch_sp500_pit_free_prices.py`, `audit_sp500_pit_free_coverage.py`, `triage_sp500_pit_free_missing_prices.py`, `audit_sp500_price_coverage.py` — free-provider price collection and diagnostic-only coverage/identity triage.
- `build_dow_pit_extended_membership.py`, `recover_dow_pit_extended_prices.py`, `audit_dow_pit_extended_coverage.py` — fail-closed extended-Dow membership, legacy-price recovery, and coverage audit.
- `pit_all_stocks.py` — validates the licensed all-stocks PIT dataset contract.
- `recover_daily_cache.py` — daily-cache recovery/repair utility.
- `data_edgar.py` — Form 4 fetch/validation CLI.

Run any module with `--help` before use; data builders and audits have dataset-specific arguments and may perform network requests.

### Validation, reconstruction, and diagnostics

- `backfill_metrics.py`, `backfill_validation.py`, `backfill_frozen_validation.py` — add current metrics/validation evidence to eligible stored runs.
- `rebuild_validated_history.py`, `rebuild_market_residual_v1.py`, `revalidate_frozen_v1.py` — controlled result reconstruction and frozen revalidation.
- `factor_regression.py`, `power_curve.py`, `exposure_diagnostic.py`, `trade_lifecycle_audit.py` — factor, power, exposure, and trade-timing diagnostics.
- `candidate_screen.py`, `conditional_cli.py`, and the `conditional_*` modules — conditional-edge discovery and governance.

### Strategy and research experiments

- `compare_universe.py`, `compare_filters.py`, `compare_timing_filters.py`, `compare_sector_rotation_exits.py`, `compare_dividend_hybrid.py`, `compare_insider_buy.py`, `compare_dual_momentum_robustness.py` — focused comparisons.
- `run_frozen_v1.py`, `run_frozen_neighbors.py`, `run_universe_sweep.py` — frozen protocol, parameter neighbors, and registered-universe replication.
- `run_ensemble.py`, `demo_ensemble.py`, `run_avwap_breakout.py` — non-primary strategy runners/demos.
- `dow_pit_power_test.py`, `earnings_momentum_robustness.py`, `momentum_persistence_reversal.py` — preregistered robustness/power work.
- `dm_portfolio_concentration_audit.py`, `dm_rank_depth_audit.py`, `dm_regime_dependence.py`, `dm_replacement_quality.py`, `dm_spy_underperformance.py`, `dm_stock_characteristics.py`, `dm_winner_grace_experiment.py`, `dm_forward_underperformance.py` — Dual Momentum diagnostics.
- `mrm_attribution_ladder.py`, `mrm_regime_drawdown_attribution.py` — Market-Residual Momentum diagnostics.
- `capital_efficiency_study.py`, `prop_scaling_survival_study.py` — prop and self-funded account studies.
- `kill_switch.py` — command-line access to the execution kill switch.

Many research utilities write append-only ledger rows or reports. Read their module docstrings and matching preregistration before running them; “rerun” is not always methodologically neutral.

## Tests and verification

The Python suite contains API, engine, data, governance, execution, PIT, and strategy-rule tests.

```powershell
python -m pytest
```

Run a focused file while developing:

```powershell
python -m pytest tests/test_engine/test_cross_sectional.py -q
```

Frontend checks:

```powershell
Set-Location webapp
npm test
npm run lint
npm run build
```

The build runs TypeScript project compilation before Vite bundling. The Vitest suite covers the dashboard, live monitor, daily performance, and strategy workspace presentation.

## Repository map

```text
api/
  main.py                         FastAPI routes, schemas, background loops
engine/
  backtest.py                     standard per-symbol simulation
  portfolio.py                    shared-capital replay
  cross_sectional.py              rank/rebalance simulation
  pairs.py                        train/test cointegration strategy engine
  runner.py                       strategy dispatch, defaults, persistence
  data.py                         OHLCV/earnings/risk-free cache and providers
  metrics.py, validation.py       metrics and evidence gates
  logging_db.py                   run/research SQLite schema
  execution*.py, alpaca_*.py      paper execution and broker access
  live_scanner.py, signals_db.py  signal-only live scanner
  conditional_*.py                conditional research lifecycle
  *_forward.py, prop_*.py         forward and synthetic ledgers
  pit_*.py, build_*.py, audit_*.py PIT construction and diagnostics
  market_*.py, screener.py,
  movers.py, data_edgar.py,
  digest.py, quotes.py             current-market research tools
  chat_assistant.py,
  strategy_authoring.py           optional Anthropic workflows
strategies/
  base.py, spec.py, params.py     strategy contracts and safe rule language
  registry.py                     canonical/research/archive/execution boundaries
  day/                            intraday strategies
  swing/                          daily, cross-sectional, event, and pairs rules
universes/*.json                  immutable universe definitions
research/                         preregistrations and dataset contracts
data/                             local caches, PIT bundles, custom specs
logs/                             SQLite ledgers and generated operational artifacts
reports/                          human-readable research/audit outputs
tests/                            Python API/engine/strategy tests
webapp/src/                       React dashboard, research, strategy workspace
strategy_tracker.xlsx             established external strategy catalogue
LESSONS.md                        detailed research/engineering decision journal
```

Supporting engine modules also cover indicators, filters, timing filters, regimes, trend templates, observations, event timing, AVWAP, overnight returns, insider logic, dividends, matched benchmarks, excursions, sanity checks, PIT features/market cap/analysis, execution ownership/calibration/risk, strategy identity, custom-strategy storage, factor regression, stability, advanced validation, and the named research studies listed above.

## Known limitations and safety boundaries

- **No claim of profitability.** A shortlist, passed historical gate, or unfalsified forward test is not proof of a durable live edge.
- **Paper trading only.** The broker client is instantiated for Alpaca paper use. Production-capital deployment is not implemented or authorized by the research labels.
- **Starting the API starts schedulers.** Paper orders remain impossible unless an allowlisted strategy was explicitly enabled earlier, but operators should still inspect `/api/live/execution/config`, ownership, and kill-switch state before leaving the API unattended.
- **Free PIT data is structurally incomplete.** Delisted/distressed removals are exactly where free data is weakest, creating potential survivorship bias. The 98% S&P threshold was not lowered and the dataset remains blocked.
- **The main Dow roster is not a perfect PIT history.** It is a fixed July-2021 roster with WBA omitted because no valid free series is available. Older windows can be invalid for membership or security-inception reasons.
- **Current S&P rosters are not historical PIT universes.** Their availability is useful for current screening and disclosed exploratory replication, not survivor-free historical inference.
- **Ticker equality is not identity.** Reused symbols, reorganizations, mergers, spinoffs, and equity-extinguishing bankruptcies require lineage evidence; fetchable bars under a similar ticker are not proof of identity.
- **Fundamentals are snapshots.** Today's yfinance fundamentals cannot be projected backward into a historical screen.
- **Intraday free data is delayed/limited.** Alpaca IEX is not the full consolidated feed, and yfinance fallback has a short lookback.
- **Portfolio replay is simplified.** It does not continuously mark open positions for entry sizing and does not model real short margin.
- **Pairs search has selection risk.** It uses a train/trade split but still searches many combinations and is intentionally prevented from running over enormous universes.
- **The API is a local development service.** There is no authentication layer, production deployment configuration, TLS termination, or multi-user isolation.
- **SQLite and JSON ledgers assume a single local application.** They are not a distributed transaction system.
- **No automatic digest delivery.** The digest is preview-only.
- **LLM features cost money and remain bounded.** They require Anthropic credentials; chat cannot see live or external data, and authoring cannot generate arbitrary code.
- **Operational gaps are not backfilled.** Shadow/account sampling leaves offline intervals blank so reconstructed data is never presented as observed.

When changing strategy rules, universe definitions, timing semantics, validation thresholds, or frozen research configurations, update the associated tests and preregistration/decision record. Do not edit a historical conclusion into looking as if the new rule had always been the rule.
