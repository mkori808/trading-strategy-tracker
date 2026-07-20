# Trading Strategy Lab — Project Guide for Claude Code

## What this project is

A local tool for backtesting and paper-testing day-trading and swing-trading
strategies against historical and live market data, tracking results, and
surfacing which strategies have a real statistical edge before any real money
follows them. The companion file `strategy_tracker.xlsx` is the source list of
candidate strategies (rules, market conditions, timeframes) — read it before
building anything so the UI reflects the same strategy definitions.

**Automated execution happens only through Alpaca — paper by default, live
only with explicit per-strategy opt-in (see "Live trading safety
guardrails"). Fidelity is never automated.**

## Goals

1. Let the user define a strategy once (entry rule, stop rule, target,
   timeframe, instrument) and run it against historical data to get win rate,
   expectancy, profit factor, max drawdown, and equity curve — matching the
   metrics already in `strategy_tracker.xlsx`.
2. Provide a simple local UI (dashboard) to compare strategies side by side.
3. Support **paper trading / signal alerts** against live/delayed data so the
   user can forward-test a strategy in real time without automating real
   orders.
4. Log every backtest and paper-trade run so results feed back into the
   tracker (win rate, sample size, expectancy) instead of living only in
   someone's head.

## Non-goals (do not build these)

- **Do not build automated live order placement into Fidelity.** Fidelity has
  no public retail API for algorithmic trading. The only way to automate real
  orders into Fidelity would be unofficial browser automation/screen-scraping
  against their web UI, which likely violates their Terms of Service and
  account agreements — this project will not implement that. Fidelity remains
  the user's core/manual brokerage account and is untouched by this codebase.
- Do not give investment advice framed as a recommendation to buy/sell a
  specific security. This tool surfaces statistics about rules the user
  defines; it doesn't tell the user what to trade today.
- Do not fabricate or backfill backtest results — every number in the UI must
  trace to an actual computed run against real historical data.
- Do not place a live (non-paper) order under any circumstance without the
  user having explicitly enabled live mode (see "Live trading safety
  guardrails" below) — default every new environment to paper trading.

## Broker: Alpaca (chosen for live/paper automated execution)

The user trades US equities/ETFs day and swing strategies, which is exactly
Alpaca's strength: API-first, commission-free stocks/ETFs, and a paper-trading
environment that mirrors live trading exactly. Alpaca is a **separate account
from Fidelity** — Fidelity stays untouched; only capital the user explicitly
moves into Alpaca is ever traded by this codebase.

**Setup**
- Account: user needs to sign up at alpaca.markets and generate API keys
  separately for Paper and Live environments (they are different key pairs).
- SDK: `pip install alpaca-py` (the current official Python SDK; do not use
  the deprecated `alpaca-trade-api` package).
- Auth: load `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` (and
  `ALPACA_PAPER_API_KEY` / `ALPACA_PAPER_SECRET_KEY`) from environment
  variables or a local `.env` file — **never hardcode keys in source, never
  commit `.env`** (add it to `.gitignore` immediately in v0).

**Endpoints**
- Paper trading base URL: `https://paper-api.alpaca.markets`
- Live trading base URL: `https://api.alpaca.markets`
- Market data: Alpaca's Market Data API (REST + WebSocket) for historical
  bars and streaming quotes/trades — use this instead of `yfinance` once the
  project moves from pure backtesting into paper/live signal generation, so
  backtest data and live data come from the same source and stay consistent.

**Rate limits (design around these, don't discover them in production)**
- Free tier: 200 API calls/minute. Funded accounts: 1,000/minute.
- WebSocket streaming connections are effectively unlimited — prefer
  streaming over polling for live price/quote updates.

**Order handling**
- Use bracket orders (entry + stop-loss + take-profit as one order) wherever
  a strategy's rules define both a stop and a target — this keeps risk
  management enforced at the broker level, not just in application logic.
- Every order the code places must log: strategy name, rule parameters,
  timestamp, symbol, side, quantity, stop price, target price, and whether it
  was paper or live — this feeds back into the `strategy_tracker.xlsx`
  Trades Taken / Wins / Avg Win / Avg Loss columns.

## Live trading safety guardrails

These apply once the project reaches v3 (real automated orders) — build them
in from the start, not as an afterthought:

- **Default to paper.** Every new strategy starts in paper mode for a minimum
  of 30 trades (matching the tracker's "sample too small" threshold) before
  it's eligible to be flipped to live.
- **Explicit opt-in per strategy.** Live mode should be a per-strategy flag
  the user sets deliberately (e.g., in a config file), never a global switch,
  and never the default for a newly added strategy.
- **Hard position/risk limits enforced in code**, independent of the
  strategy's own logic: max % of account risked per trade, max number of
  concurrent open positions, and a daily loss circuit-breaker that halts all
  new entries for the session if hit.
- **Kill switch**: a single command/button that immediately stops all new
  order submission and optionally flattens open positions, reachable even if
  the UI is unresponsive (e.g., a CLI script that hits Alpaca's API directly
  to cancel open orders and close positions).
- **Alert on every live fill** (not just paper) via desktop notification or
  email so the user is never surprised by what the bot did in their account.

## Suggested architecture

```
/data          -- cached historical OHLCV data (parquet/csv), gitignored
/strategies    -- one file per strategy, defining entry/stop/target rules as code
/engine        -- backtest engine: feeds bars to a strategy, tracks trades, computes metrics
               -- also the pre-trade filter layer (regime.py, trend_template.py,
                  filters.py) that gates entries before entry_signal() runs
               -- also the fundamentals feed (fundamentals.py) and the
                  Dividend Hybrid engine (dividend_hybrid.py) -- see below
               -- also the live research platform: screener.py, movers.py,
                  market_signals.py, digest.py, and data_edgar.py's insider-
                  buying read path -- see "Research platform" below
/api           -- thin FastAPI layer exposing engine/strategies as JSON for the frontend
               -- also validates and applies Lab-tab overrides (RunRequest)
/webapp        -- React + TypeScript (Vite, Tailwind) dashboard; talks only to /api
               -- Lab tab: test strategy variations (symbols/dates/params);
                  Compare tab: canonical-only leaderboard; Symbols tab: watchlist;
                  Screener/Movers tabs: live research platform (see below)
/logs          -- backtest run outputs + paper-trading signal logs (csv/sqlite)
strategy_tracker.xlsx   -- source of truth for the strategy list (imported, not duplicated)
```

**Suggested stack for v1** (favor simple/local over heavy infra):
- **Data**: `yfinance` is fine for early daily-bar backtesting since it needs
  no account. Move to **Alpaca's Market Data API** as soon as intraday
  strategies (ORB, VWAP bounce, scalping) need reliable minute bars, since
  it's free with an Alpaca account and keeps backtest data and live paper
  data consistent (same source, same adjustments).
- **Backtesting**: `backtrader` or `backtesting.py` (both free/maintained,
  event-driven, easy to reason about stop/target fills). Note: `vectorbt`'s
  free version is largely unmaintained now (active development moved to the
  paid `vectorbt.pro`) — don't default to it. Pick one free option and be
  consistent; don't build a third custom engine unless it falls short for a
  specific strategy's rules.
- **UI**: originally `streamlit`; replaced 2026-07-16 at the user's request
  with a **React + TypeScript frontend (`/webapp`, Vite + Tailwind) backed by
  a thin FastAPI layer (`/api`)** for a more polished look. `/api` only
  serializes `engine`/`strategies` output to JSON — it holds no backtest
  logic of its own. Run both together: `uvicorn api.main:app --port 8791`
  and, in `/webapp`, `npm run dev` (proxies `/api` to 8791). Don't reintroduce
  Streamlit; extend the existing React app instead.
- **Storage**: SQLite for run history and paper-trading signal logs; nothing
  fancier needed at this scale.

## Strategy definitions

Import strategy names, entry rules, stop rules, and timeframes directly from
`strategy_tracker.xlsx` (`Day Trading` and `Swing Trading` tabs) rather than
re-typing them — keep this file as the single source of truth for what a
strategy "is." Writing results back into the xlsx is out of scope for the
code; the user updates the tracker manually from what the UI reports.

**A strategy that clears a large-enough sample with decisively negative
results is archived from the default dashboard view, not deleted.**
`strategies/registry.py:ARCHIVED_STRATEGY_NAMES` is purely additive — it
never removes anything from the tracker, the registry dicts, or
`ALL_STRATEGY_NAMES` (all three must still match 1:1, enforced by
`tests/test_engine/test_registry.py`) — it only tells `/api/strategies`
and the webapp (`StrategyTable`, `StrategyPicker`) which rows to hide
behind a "Show archived" toggle by default. An archived strategy still
runs, still logs, still has full history. See `ARCHIVED_STRATEGIES.md` for
the current list and rationale, and never delete a row from that file when
a verdict changes — update it in place so "what we tried" stays intact.

Current strategies to support first (already defined in the tracker):
- **Day trading**: Opening Range Breakout, VWAP Bounce/Reversion, Momentum/Gap
  and Go, Scalping (3-5 min), Mean Reversion Scalp, News Fade, Range Trading.
- **Swing trading**: Pullback to 21 EMA, Breakout from Consolidation, 9/21 EMA
  Crossover, Oversold Bounce (RSI<30), Fibonacci Retracement Entry, Earnings
  Momentum/Gap-Hold, Sector Rotation Play.

Each strategy module should expose a consistent interface, e.g.:

```python
class Strategy:
    name: str
    timeframe: str          # "1min", "5min", "1d", etc.
    direction: str           # "long", "short", or "both" — several strategies
                             # (News Fade, Mean Reversion Scalp) trade both sides
    def entry_signal(bars) -> bool: ...
    def stop_price(bars, entry_price) -> float: ...
    def target_price(bars, entry_price) -> float | None: ...   # None if using exit_signal
    def exit_signal(bars) -> bool: ...   # optional: for signal-based exits
                                          # (e.g. EMA crossover) with no fixed target
```

so the backtest engine and UI can treat every strategy identically.

**Every strategy is a `@dataclass`; every tunable rule number is a
`param_field()`, not a bare module constant.** This is what lets the
webapp's Lab tab (see below) tune a strategy's parameters without editing
Python:

```python
from dataclasses import dataclass
from strategies.params import param_field

@dataclass
class PullbackTo21Ema(Strategy):
    name = "Pullback to 21 EMA"       # plain class attrs stay plain --
    timeframe = "1d"                  # @dataclass only turns ANNOTATED
    direction = "long"                # attributes into fields

    pullback_atr_tolerance: float = param_field(
        0.5, label="Pullback tolerance (x ATR)", minimum=0.1, maximum=2.0,
        step=0.1, help="...",
    )
```

`strategies/params.py:describe_params(cls)` reads `dataclasses.fields()` and
surfaces only fields declared via `param_field()` (i.e. carrying a `label`
in their metadata) — a field the ENGINE injects at construction time
(`benchmark_bars` on Sector Rotation Play, `positive_earnings` on PEAD,
`risk_free_rate` on Dual Momentum) is declared as a plain field with no
`param_field()` and is excluded automatically, by the same rule, not by a
separate list that can drift out of sync. New numeric/bool/str rule
parameters follow this pattern; don't reintroduce a bare module constant a
strategy reads by name.

**Timezone**: all timestamps in `America/New_York`; regular market hours are
9:30–16:00 ET. Intraday strategies (ORB especially) must not be built against
UTC-naive timestamps — this is a common silent-bug source.

**Pattern Day Trader (PDT) rule**: a margin account under $25K is limited to
3 day trades per rolling 5 business days. If Alpaca live trading uses a
margin account under that threshold, day-trading strategies must track and
respect this limit (or run on a cash account with settlement-date awareness
instead).

## Pre-trade filters (run before `entry_signal`)

Two gating layers sit in front of every strategy's entry rule. They are
**pre-filters, not entry signals** — they decide whether conditions are right
to *consider* a trade at all. Order: regime first, then trend template, then
the strategy's own `entry_signal()`.

- **Market regime** (`engine/regime.py`) — classifies each SPY daily bar
  Bullish / Neutral / Bearish from its 50- and 200-day SMAs. New **long**
  entries are allowed only in Bullish. Neutral and Bearish block new entries;
  neither ever force-closes an open position — a regime flip is not an exit
  signal. Bearish bars with live exposure are logged as a warning only.
- **Minervini Trend Template** (`engine/trend_template.py`) — an 8-point
  stock-selection check per symbol per scan date, computable from daily OHLCV
  plus SPY. All 8 criteria must pass simultaneously; any single failure
  disqualifies the symbol for that date. Computed vectorized over the whole
  history in one pass, then looked up per date — not recomputed per bar.
- **Wiring** (`engine/filters.py`) — `FilteredStrategy` wraps a strategy and
  re-implements the same `strategies.base.Strategy` interface, so it drops
  into the existing `run_strategy_backtest_seeded` with no engine changes.
  Extend the filters by wrapping, not by adding filter parameters to
  `engine/backtest.py`. Stop/target/exit always delegate through untouched.

**Filters gate long entries only.** Short and both-sided strategies pass
through ungated on their short side.

**Check thesis compatibility before applying a filter to a strategy.** The
trend template is a trend/momentum selector; a mean-reversion entry
(RSI<30, IBS, Connors RSI2, Gap Fade) asks for roughly the opposite
conditions. Stacking them does not produce a stricter mean-reversion
strategy, it produces zero trades — Oversold Bounce went 210 trades to
exactly 0, with *zero* overlapping bars measured across sample symbols. Two
filters with opposing theses annihilate rather than compose. See LESSONS.md.

**Always log selectivity.** `regime_distribution` (share of bars per state)
and `scan_summary` (per-date pass/fail counts across the universe, plus the
most common rejection reason) exist so "the filter is working" can be told
apart from "the filter is a no-op" — both otherwise look like a changed
trade count. A filter passing ~95% or ~0% of the universe is not filtering.

**Look-ahead is the main risk in this layer.** Every filter value for bar *i*
must depend only on bars ≤ *i*: right-aligned rolling SMAs (never
`center=True`), trailing regressions, `rolling(252)` 52-week extremes (never
a full-series `.max()`/`.min()`), forward-filled benchmark alignment (never
backward), and warmup history prepended *before* the window start. Because
the filters are daily and day-trading strategies run on 5-minute bars,
**intraday lookups must resolve to the prior session** — today's daily close
does not exist at 10:00am. Assert this with a causality test that recomputes
on truncated data, not by inspection.

**Comparison runs** (`engine/compare_filters.py`) must hold universe, date
range, interval, cost model and risk-free rate identical across both arms so
the filters are the only variable, and must not write to
`engine/logging_db.py` — that schema has no "which filters were active"
field, so a filtered run would silently shadow the unfiltered dashboard
result. Same reasoning as `engine/compare_universe.py`.

### Timing gates: FOMC-day exclusion and SPY volatility regime (`engine/timing_filters.py`)

A second gate layer, separate from regime/trend-template above because both
conditions are **direction-agnostic** (event risk and market volatility
apply the same to a long or a short) rather than long-only by thesis. Same
wrapping pattern as `engine/filters.py`: `EntryGate` re-implements
`strategies.base.Strategy` and drops into the existing engine unchanged.
Overnight Hold can't take the `Strategy` wrapper (it runs on the close→open
engine, `engine/overnight.py`), so that engine instead takes an optional
`entry_allowed(timestamp) -> bool` predicate — `None` (every pre-existing
caller) is byte-identical to before.

- **FOMC-day gate** — blocks new entries on both days of each two-day FOMC
  meeting, from a hardcoded 2021–2026 calendar of the Fed's published
  meeting dates (`FOMC_DECISION_DATES`). Known in advance, so no
  prior-session shift applies even intraday; extend the tuple when the Fed
  publishes a new year, don't approximate. `fomc_blocked_dates()` raises
  past the last covered year rather than silently gating nothing.
- **Volatility-regime gate** — SPY's 21-day realized vol ranked against its
  own trailing 252-day history (right-aligned rolling std, then a trailing
  rolling percentile — causality asserted with a truncation test, same
  requirement as the look-ahead paragraph above). "calm"/"storm" modes
  partition the same threshold rather than two overlapping windows.
  Intraday lookups resolve to the prior session via the same `_asof`
  convention `engine/filters.py` uses; daily lookups use the bar's own day.
- **Comparison** (`engine/compare_timing_filters.py`) — same
  universe/window/cost-model/risk-free-rate-held-identical and
  no-`logging_db`-write discipline as `compare_filters.py`. Tested against
  every strategy with positive canonical expectancy plus Overnight Hold
  (see LESSONS.md): the FOMC gate is a near no-op (~6% of days, ≤0.02R
  expectancy movement, never changes a status); the vol gate's "storm"
  arm (entries only in the top 30% of realized vol) improves per-trade
  expectancy on every mean-reversion strategy but not the trend strategies
  — thesis-consistent, and worth remembering, but it does **not** flip any
  strategy's status: alpha stays negative and Sharpe gets mechanically
  worse everywhere (exposure collapses 70-80%). Report a gate's trade-count
  and exposure impact alongside any expectancy change, same as the
  filters-comparison rule already states.

## Fundamentals data (`engine/fundamentals.py`)

The rest of the pipeline is OHLCV-only. This module adds dividend and
fundamental fields, and it is split into two tiers that every caller and
every reader of a result must keep straight:

- **Point-in-time (real history)** — trailing dividend yield, dividend
  growth YoY, 5-year dividend CAGR, and dividend-cut detection, all computed
  from `yfinance.Ticker.dividends` (genuine payment-date history, decades
  deep for large caps). Safe to evaluate on a historical scan date.
- **Snapshot (today's value, NOT point-in-time)** — market cap, payout
  ratio, EPS/revenue growth, trailing P/E, analyst rating, analyst price
  target, from `yfinance.Ticker.info`. yfinance carries no history for any of
  these. Applying one to a 2021 scan date asserts 2026 fundamentals were
  knowable in 2021.

**Screening on snapshot fields is not merely imprecise, it is biased toward
the answer.** It selects companies healthy *today*, which systematically
excludes the names whose fundamentals deteriorated — exactly the cases a
"the dividend is a floor" thesis needs to be tested against. Measured
directly on Dividend Hybrid: the snapshot screen eliminated 100% of the
symbols that ever passed the point-in-time screen, including both names that
later cut their dividend. Any strategy screening on both tiers must run both
ways (point-in-time-only vs. full) and report the gap, not just disclose it
in a warning string — see `engine/compare_dividend_hybrid.py`.

**Prices for a yield calculation must be unadjusted.** `engine/data.py`'s
bars use `auto_adjust=True` (correct for computing returns, wrong here) —
back-adjusting for dividends deflates historical closes and inflates
historical yield computed against them. `engine/fundamentals.py` keeps its
own unadjusted-close cache used only as the yield denominator; backtest
fills still use the adjusted bars everything else uses.

**Bar timestamps and dividend timestamps do not share a stamping
convention** — bars are stamped 20:00 the *previous* calendar day
(`engine/data.py`'s UTC→NY localization), dividends are stamped 09:30 on the
payment date. Never join them on a derived date key; use a calendar-window
sum over real timestamps (`_window_sum` in `engine/fundamentals.py`). A
naive date match silently drops nearly every payment — measured result: a
serial dividend *raiser* (VZ) reported as cutting on 379 bars.

Print `NOT_POINT_IN_TIME_WARNING` at the top of any output using snapshot
fields, and `SURVIVORSHIP_WARNING` at the top of any output whose screen
includes them.

## Dividend Hybrid (`strategies/swing/dividend_hybrid.py`, `engine/dividend_hybrid.py`)

A fundamental dividend screen gating a technical entry, run as two exit
versions (A: no stop, take profit at the entry yield %; B: same target, 8%
hard stop) so the strategy's central claim — a stop is unnecessary because
the dividend is a floor — is tested rather than assumed. Not a
`strategies.base.Strategy`: 10%-of-equity sizing (not risk-based), Version
A's indefinite hold, and its required outputs (max unrealized drawdown
during a hold, still-held count, dividend cuts during the hold) don't fit
the bracket-engine trade row. Same shape as Overnight Hold: a dedicated
engine emitting the project's standard metrics.

**An approximated intraday rule must be checked for scale, not just
translated by name.** The spec's daily proxy for "pullback to the 5-min
EMA20" was "close within 0.5% of the daily SMA20" — but a 5-minute EMA20
tracks price within tenths of a percent while a 20-*day* average sits a
median 3.55% away on gap-up days. The literal proxy produced zero trades
over five years; it wasn't selective, it was self-contradictory. Both the
literal rule and a corrected same-scale proxy are implemented and reported
separately (`TRIGGER_SPEC` / `TRIGGER_INTRADAY_PROXY`) rather than silently
swapped.

**Version A's win rate is 100% by construction whenever it has no losers
closed out** — with no stop, the only way a trade closes is by hitting
target. Always mark still-held positions to market in the headline metrics;
report the closed-only view separately, never as the default.

**A result from an underpowered or unstressed sample is not a verdict.**
Dividend Hybrid's only runnable configuration produced 16–24 trades, and
Version A's worst unrealized drawdown never breached 20% — the strategy's
whole thesis is about what happens beyond that. Report a small or
unstressed sample as inconclusive, not as support for whichever version has
the better headline number.

## Research platform (Screener / Movers / Market Signals / Insider Buying / Digest)

Added 2026-07-19 at the user's request to turn the app into "a full service
platform for monitoring trades and information about market," modeled on a
UK stock-research product (screener, market signals, trending movers,
analyst consensus, regulatory-news scoring, a daily email digest). Every
piece below is a **live, descriptive** feature, not a backtest input — it
describes today's state only, is never cached across days, and never feeds
a strategy or a backtest. This is a different tier of feature from
everything else in this file, so it gets its own non-goals reminder:
**every score/composite here is presented as "Score: NN/100," never as a
buy/sell signal** — the same non-goals rule the rest of the app follows.

- **Universe** (`engine/universe.py:RESEARCH_UNIVERSE`): the dedup union of
  `EQUITY_UNIVERSE`/`MIDCAP_UNIVERSE`/`SMALL_CAP_UNIVERSE`/`ETF_UNIVERSE`
  (94 symbols). Deliberately **not** given the point-in-time-membership
  disclosures those lists carry for backtesting — that bias doesn't apply
  here, since "which symbols do we track today" has no survivorship-bias
  risk the way a fixed backtest sample does. Never use `RESEARCH_UNIVERSE`
  as a backtest universe, and never backfill point-in-time rigor onto it;
  it exists for a different purpose than the lists above it.
- **Screener** (`engine/screener.py`, `GET /api/screener`, Screener tab):
  valuation/quality/growth-momentum/risk composite scores, each a
  cross-sectional 0–100 percentile rank computed live across whichever
  symbol set is requested (a filtered subset ranks against itself, not the
  full universe). The composite is a **plain average of the four factor
  scores, disclosed as such** — not a validated multi-factor model. A
  missing field excludes that symbol from that one ranking rather than
  defaulting to a misleading middle score. Analyst-consensus columns
  (rating, target, upside %) come straight from `engine/fundamentals.py`'s
  existing snapshot fields — appropriate here specifically because this is
  a *live* display, not a backtest scan date, so the point-in-time caveat
  that governs `fundamentals.py` elsewhere doesn't apply.
- **Movers** (`engine/movers.py`, `GET /api/movers`, Movers tab): gainers/
  losers reuse `engine/quotes.py`'s already-computed day change-pct — no new
  fetch logic. Momentum streaks are consecutive up/down days from cached
  closes.
- **Market Signals** (`engine/market_signals.py`, folded into
  `engine/market_overview.py`'s response so `/api/market` stays the single
  source rather than adding a second, inconsistent endpoint): a breadth
  score built from disclosed, computable inputs (% above 50/200-day SMA,
  net new-20-day-highs-vs-lows, SPY's own regime state) — **not** a claim to
  replicate any licensed index (e.g. CNN's Fear & Greed). Scanning the full
  94-symbol universe with a 400-day warmup (matching
  `engine/regime.py:REGIME_WARMUP_DAYS`'s calendar-vs-trading-day
  conversion) is genuinely slow on a cold cache — `/api/market` now takes
  up to ~40s. This is a real, accepted latency cost of breadth-scanning the
  whole universe, not a bug; the UI's loading state should say so rather
  than look frozen.
- **Insider Buying** (`engine/data_edgar.py:recent_purchases`,
  `GET /api/insider/recent`, `POST /api/insider/refresh`, Movers tab): the
  US analog to "RNS regulatory-news scoring" — there's no US RNS
  equivalent, but the project already had a rigorous, look-ahead-safe SEC
  EDGAR Form 4 pipeline (see `data_edgar.py`'s own module docstring). Real,
  structured, quantifiable open-market purchase data, ranked by transaction
  value — a strictly better analog than scraped headlines would have been.
  `recent_purchases` is read-only and uses its own lightweight connection
  (not the shared `connect()` helper, which always re-runs
  `executescript(_SCHEMA)`) plus a `PRAGMA busy_timeout`, so a read doesn't
  collide with a multi-minute `fetch_form4_for_universe` write triggered by
  the refresh button.
- **Daily digest** (`engine/digest.py`, `GET /api/digest/preview`, Movers
  tab): composes regime + movers + insider buys + the market-signals score
  into one payload plus a plain-text rendering. **Preview only, by
  deliberate scope decision** — no scheduler, no SMTP, nothing is ever sent.
  Wiring up a real send is a separate decision requiring new `.env` SMTP
  credentials the user hasn't set up; don't half-build it by adding a
  scheduler without sending, or sending without the user's explicit
  credentials.

**A fundamentals cache written before a new field existed must be treated
as a cache miss for that field, not as "no data."** `engine/fundamentals.py`
gained three quality fields (`profit_margins_pct`, `return_on_equity_pct`,
`debt_to_equity`) this session; every symbol's pre-existing cached JSON
predated them, so a naive load would have silently shown "quality score:
unavailable" for every tracked symbol forever. `snapshot()` now checks for
the new keys and refetches once if they're missing, rather than trusting
any cache that predates a schema change. The same append-only,
default-`None` field ordering used for `PairsResult.symbols` earlier this
session applies here too — never insert a new field in the middle of an
existing dataclass whose cache/construction sites aren't all keyword-based.

## The Lab tab: testing strategy variations (`engine/runner.py:RunRequest`)

The webapp's **Lab** tab lets a user override a strategy's symbol universe,
date range, and/or rule parameters (see `param_field()` above) for one run,
without touching its registered defaults — the "test variations" one-stop-
shop. Three things make this safe rather than a quiet backdoor around
CLAUDE.md's own survivorship-bias rule:

- **`RunRequest(symbols=None, start=None, end=None, params=None)`** is the
  only override surface. `run_backtest(name)` with no request is
  byte-identical to the strategy's original zero-argument behavior — same
  universe, same dates, same params — and is what every existing caller
  (`engine/cli.py`, the API's default call) still does.
- **`apply_params()` validates before it constructs anything** — an unknown
  field name or an out-of-bounds value raises `ValueError` (→ HTTP 400) via
  the same `ParamSpec` bounds the UI renders sliders from. Never silently
  clamp or drop a bad value.
- **Canonical vs. experiment is tracked in the DB, not by convention.**
  `engine/logging_db.py`'s `runs` table has an `is_canonical` column;
  `latest_run_per_strategy()` (what `/api/strategies` and the Compare tab's
  leaderboard read) filters to canonical rows only, so a one-off parameter
  sweep can never silently replace what a strategy's registered
  configuration shows. This is the same principle already applied to
  `compare_universe.py`/`compare_filters.py`/`compare_dividend_hybrid.py`
  (never let a comparison run shadow the canonical result) — enforced here
  at the schema level since the Lab tab makes running an override trivial
  and frequent, unlike a one-off comparison script.

**The UI must disclose when a config is custom.** Overriding symbols or the
date range reintroduces exactly the survivorship-bias risk CLAUDE.md warns
about elsewhere ("picking symbols after seeing which ones moved is
survivorship bias") — the Lab tab shows a persistent "Custom configuration
— exploratory, not a replacement for the canonical backtest" banner the
moment anything differs from the registered defaults. Don't remove this
banner or make custom results visually indistinguishable from canonical
ones anywhere in the UI.

Sector Rotation Play's universe (sector ETFs ranked against SPY
specifically) can't be overridden via the Lab tab — same reasoning
`engine/compare_universe.py` already documents for excluding it from
universe-swap comparisons; a symbols override for it is rejected with a 400
at the API layer (`SYMBOL_OVERRIDE_DISALLOWED_NAMES`).

## Chat assistant (`engine/chat_assistant.py`, the "Chat" tab in `ResultTabs`)

Added 2026-07-20 so a user can ask "why did this trade fail while all
others succeeded?" and get an answer grounded in the same computed numbers
the Overview/Trades/Per-Symbol tabs already show — this is the app's first
LLM integration and its only feature with a real per-use cost (an Anthropic
API call per message; `ANTHROPIC_API_KEY` in `.env`, degrades gracefully to
a clear "not configured" reply like every other optional integration here).

**Scoped to exactly one result, on purpose.** The assistant only ever sees
whichever backtest result is currently on screen — the frontend sends the
full result JSON (the same payload `/api/backtest/{name}` already returned)
with every message; nothing is re-fetched, re-run, or read from a database.
This isn't a shortcut: individual trades aren't persisted anywhere long-term
(`engine/logging_db.py` only logs aggregate metrics per run, never
per-trade rows), so there is no durable per-trade store to query across
runs even if the assistant wanted to reach further. Making it reach across
historical runs would first need a real schema change (a per-trade table),
a deliberately separate, bigger piece of work — not something to bolt on
by widening this feature's scope quietly.

**Grounded via tool use, not context-stuffing.** Rather than pasting the
whole trades list into every prompt (expensive, and stops scaling once a
strategy has more than a few dozen trades), the model gets four tools
(`list_trades`, `get_trade_detail`, `get_run_metrics`,
`get_per_symbol_breakdown`) that query the result dict on demand — the
same "give the model real computed data, don't make it invent context"
principle already applied to the market-signals/screener composites
elsewhere in this file. Every number the assistant can cite is something
`engine/excursion.py` or `engine/metrics.py` already computed for that
trade or run; the system prompt (`SYSTEM_PROMPT` in `chat_assistant.py`)
states this explicitly and forbids inventing a reason a trade behaved a
certain way when the tool output doesn't explain it.

**Same investment-advice non-goal as the rest of the app, restated for an
LLM specifically.** A model that can reason fluently about historical
trade data can also drift into "so you should buy X next" without being
told not to — the system prompt explicitly instructs it to explain
historical backtest behavior only, never forward-looking advice about real
money. This is worth restating per-feature, not assumed inherited, because
an LLM's default conversational instinct is to be helpfully prescriptive.

**Stateless backend, frontend owns history.** `POST /api/chat` takes the
result plus the full conversation so far and returns one reply; tool-call
round-trips happen entirely inside that one request and are never sent
back to the frontend, which only ever stores finalized `{role, content}`
text turns. `ChatPanel.tsx` is keyed on a signature of the run (strategy +
window + applied symbols/params) so switching to a genuinely different
result resets the conversation, while switching tabs back and forth on the
same result does not.

## Metrics to compute (match the spreadsheet's definitions exactly)

- **Win Rate** = Wins / Trades Taken
- **Expectancy (R)** = (Win Rate × Avg Win R) − (Loss Rate × Avg Loss R)
- **Profit Factor** = Gross Wins / Gross Losses
- Also compute: max drawdown, Sharpe/Sortino (optional, nice-to-have), number
  of trades, and date range tested.
- Flag any strategy with fewer than 30 trades as "sample too small" in the UI
  — do not present its win rate/expectancy as reliable.
- Test against a fixed, pre-registered symbol list (e.g., decided before
  running the backtest), not today's top gainers/most-active list — picking
  symbols after seeing which ones moved is survivorship bias.
- **Always show buy-and-hold's own return next to alpha, not just the
  difference.** `backtesting.py` computes "Buy & Hold Return [%]" for every
  per-symbol run internally; `engine/metrics.py`'s `buy_hold_return_pct`
  (averaged the same way alpha/beta already are in
  `engine/backtest.py:aggregate_symbol_results`) surfaces it directly in
  the Overview/Compare tabs and the Per-Symbol table, rather than leaving
  the user to infer it from alpha alone. A strategy can show a small
  positive alpha while buy-and-hold itself returned triple digits on a
  runaway symbol — the per-symbol table is where that dispersion is
  visible (see PerSymbolTable's "Buy & Hold" column).
- **When R-expectancy and profit factor disagree in direction, believe
  profit factor about the dollars.** R-multiples are normalized by each
  trade's initial risk, so tight stops on winners and wide stops on losers
  can post positive expectancy while profit factor stays below 1.0 (gross
  losses exceeding gross wins). Report both; never headline expectancy alone.
- **Decompose risk-adjusted ratios before narrating them.** Anything that
  cuts trade count sharply (a pre-filter, a capacity cap) drives exposure
  toward zero, and Sharpe/Sortino against a non-zero risk-free rate will blow
  up as a mechanical consequence — numerator pinned near −rf, denominator
  collapsing with volatility. Check exposure % and CAGR before reading a
  dramatic Sharpe as a statement about trade quality.
- **A win rate of 100% (or 0%) is a construction artifact, not a result,
  whenever a version of a strategy has no way to close a losing trade** (no
  stop, held to window end). Mark unrealized positions to market in the
  headline metrics before reporting a win rate.
- **A leaderboard status string must be recomputed from a row's stored
  numbers, never trusted as logged.** `/api/strategies` calls
  `engine/metrics.py:derive_status()` fresh on every row's
  trades/expectancy/Sharpe/alpha rather than reading the `status` column
  `logging_db.runs` stored at run time — the status *logic* itself has
  changed over this project's life (e.g. the Sharpe/alpha gate added
  2026-07-16), so an old row's stored string can read "shortlist" under
  today's rules even when its own numbers no longer qualify. Measured
  directly: Overnight Hold's best-Sharpe canonical row was logged hours
  before that gate shipped and won the ranking by a rounding-error margin
  over its own corrected re-run — the leaderboard showed a stale
  "shortlist" for a strategy that, by current logic, does not clear the
  bar. `/api/history/{name}` still shows the logged string as-is — that's
  the honest historical record of what the app said that day; only the
  leaderboard's "what's true right now" view recomputes.
- **The cross-sectional/pairs engines (Dual Momentum, Pairs / Stat Arb)
  get the same shortlist bar, phrased in return terms.**
  `engine/metrics.py:portfolio_status()` applies Sharpe > 0.5 and
  beats-benchmark (SPY's own buy-and-hold over the identical window,
  stored as `benchmark_return_pct` in `logging_db.portfolio_runs`) exactly
  like `derive_status()` does for R-multiple strategies. Before this,
  `/api/strategies` hardcoded these two rows to a bare "Backtested" label
  regardless of outcome — which was silently presenting a losing strategy
  (Pairs / Stat Arb: -13.2% return vs. SPY's +60.7% over the same window)
  with the same neutral label as a winning one.

## Development conventions

- Python 3.11+, type hints on all public functions.
- Every strategy's rules should be testable in isolation with unit tests
  (feed a small synthetic OHLCV series, assert the expected entry/exit).
- No network calls inside the backtest engine itself — fetch and cache data
  separately, then run backtests against local data so results are
  reproducible.
- Never hardcode a stop/target as a probability of profit; every number must
  come from an actual computed backtest.
- Log the exact rule parameters used for every run (so "ORB with 15-min range"
  and "ORB with 30-min range" don't get silently conflated).

## Milestones

1. **v0**: Load historical data for one symbol, implement 1-2 strategies,
   run a backtest, print metrics to console.
2. **v1**: React dashboard (backed by the FastAPI layer) listing all
   strategies with computed metrics; ability to pick a symbol/date range and
   re-run.
3. **v2**: Connect to Alpaca's **paper** environment. Stream live/delayed data
   during market hours via Alpaca's Market Data API, log signals when a
   strategy's entry condition fires, place paper bracket orders automatically,
   and track fills for forward-testing accuracy against the backtest. This is
   real automated trading — just with Alpaca's paper money, not real money.
4. **v3 (only after 30+ paper trades per strategy with positive expectancy
   overall — not 30 winners — and explicit user opt-in)**: Flip individual
   strategies to Alpaca **live** trading, with
   every safety guardrail above already in place. Fidelity is never touched
   by any of this.

## Reminders for every session

- This tool automates execution only in Alpaca (paper by default); it never
  gives personalized investment advice about what to trade.
- Keep backtests honest: no look-ahead bias (a strategy can't use a bar's
  close to decide whether to enter during that same bar), no survivorship
  bias in symbol selection, and model realistic slippage even though Alpaca
  is commission-free — spread and fill slippage still erode edge.
- If a request implies scraping or automating Fidelity's website to place
  orders, stop and flag the ToS/legal concern instead of writing that code.
- A filter or engine change that makes a strategy trade far less has not
  automatically improved it. Report trade count, exposure, and sample size
  alongside every "improved" metric, and flag anything that drops back under
  30 trades — a filtered result can be *less* informative than the
  unfiltered one it replaced.
- Read LESSONS.md before repeating an experiment. Universe changes,
  capacity sweeps, and pre-filters have all already been run; none produced
  a strategy clearing the shortlist bar (Sharpe > 0.5 and alpha > 0).
- When a strategy needs fundamental data yfinance only carries as today's
  snapshot (`Ticker.info`, no history), do not screen a historical backtest
  on it without also running a point-in-time-only version and reporting the
  gap — see "Fundamentals data" above. The bias this introduces is not
  generic noise, it targets exactly the cases (dividend cuts, earnings
  misses) that would refute an optimistic thesis.
