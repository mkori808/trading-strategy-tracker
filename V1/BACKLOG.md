# Backlog

Open items with enough context to resume cold. Each records what was found, why
it stopped, and what would unblock it — so a future reader does not repeat the
scoping.

Last updated: 2026-08-11

---

## 1. Point-in-time smallcap replication — BLOCKED on paid data

**Status:** scoped, not built. Blocked on two independent data barriers.

### Why it matters

Momentum literature predicts the effect should be **strongest** in small,
less-covered names. The frozen Dual Momentum config measured **−10.2pp** there —
the opposite direction. That is either evidence against the premise, or an
artifact of the data being unusable. Currently unresolved, and it is the single
test whose theoretical prediction runs *against* the observed result, which is
what makes it worth doing.

### Pre-registered interpretation (recorded before any clean data exists)

| outcome | reading |
|---|---|
| Negative with the frozen config | Strong evidence against the premise. Momentum fails where theory says it is strongest, on clean data — which makes the Dow result harder to explain *as momentum*. |
| Positive with the frozen config | Genuine replication. |
| Positive only after tuning | **Meaningless. Do not tune.** |

Honest prior: the current −10.2pp is already the low-survivorship-exposure case
(holding all 600 captures the full survival premium; picking a few captures it
only where the ranking selects survivors), so **the sign will probably hold**.
The test is worth running because the theoretical prediction runs the other way,
not because the current number is unreliable.

### Blocker 1 — the repo's smallcap universe is 26 names, not 600

`engine/universe.py:SMALL_CAP_UNIVERSE` is a **26-name sample of TODAY's** S&P
600 membership, taken from Wikipedia. `MIDCAP_UNIVERSE` is 27 of ~400. The
−10.2pp figure was measured on that, not on the index. `universe.py`'s own
comment already discloses the weaker rigor.

### Blocker 2 — clean point-in-time membership is a licensed product

The S&P 600 reconstitutes quarterly with dozens of changes a year plus M&A and
index migrations. Wikipedia's change log is nowhere near as complete as the
Dow's (which changes roughly once every two years and was reconstructible).

Paid sources: S&P DJI, CRSP, Compustat. **Norgate Data** is the affordable
retail option (~$50–100/mo) and does provide point-in-time index constituents.

### Blocker 3 — delisted PRICE data, which is worse than membership

Even with a perfect membership list, `yfinance` cannot fetch delisted tickers.
Measured on the one universe where point-in-time membership WAS reconstructed:

> **4 of 17 Dow ejections (24%) were unfetchable** — `KFT`, `DWDP`, `UTX`, `WBA`.

Small caps are acquired and delisted at far higher rates than Dow constituents,
so the unfetchable share over a decade would be considerably worse. **Paying for
membership alone does not fix this.** Both axes need paid data.

### To unblock

Point-in-time constituents **and** delisted price history. Norgate is the
cheapest realistic path. Until then this is genuinely not answerable, which is a
real answer rather than a gap.

---

## 2. S&P 500 point-in-time substitute — DECISION PENDING

Wikipedia's S&P 500 change coverage is materially better than the S&P 600's, so
this **is** buildable without paid data.

**But it tests a different question.** It would not falsify *"momentum is
strongest in small caps"* — it only asks whether the Dow result is an artifact of
universe *size*. Worth doing; must not be presented as the smallcap replication.

Same delisted-price barrier applies, though less severely than for small caps.

Under the frozen scaling rule: `top_n = round(0.172 × 500) = 86`.

**Needs a scope decision before building.**

---

## 3. Re-validate `ARCHIVED_STRATEGIES.md` against v2 numbers

The ten archived strategies were retired using **metrics_version 0** numbers —
i.e. the broken metric. The verdicts probably all hold, but at least two rest on
stated rationale the corrected data contradicts:

- **Scalping / VWAP Bounce** — archived for "large-enough sample" (21,108 and
  8,201 trades). Correct verdict, wrong reason: those trades span ~50 measured
  days, not years. They are droppable on **expectancy** (−0.184R, −0.143R),
  which trade count determines regardless of calendar span.
- **Turnaround Tuesday** — archived on negative expectancy, verdict holds. But
  its v0 Sharpe of **+2.26** (now **−0.52**) was pure degenerate-sleeve
  inflation and would have made anyone hesitate.
- **Fibonacci Retracement** — same shape, 0.50 → −0.32.

Per `CLAUDE.md`: edit rationales **in place**, never delete a row when a verdict
changes. Expect all ten confirmed with two or three rationales rewritten.

---

## 4. `SHARPE_THRESHOLD = 0.5` has never been calibrated

Set when nothing in the project could reach it (best achievable was −0.16).
Post-v2 it admits two strategies (Connors 0.60, IBS 0.55) whose excess CAGR is
+0.15% and +0.50% against SPY's +9.52%.

The gate is not obviously measuring the right quantity. `MIN_INVESTED_DAYS`
handles the degenerate-denominator case; whether 0.5 is the right bar for a
non-degenerate one is untested. **Do not tune it to fit current results** — that
is the same loop that produced the original false positive.

---

## 5. Coverage guard does not fire at 50 days

`coverage_is_measurable()` refuses a window only when even 100% exposure could
not reach `MIN_INVESTED_DAYS`. At 50 measured trading days that is 30/50 = 60%
exposure, so intraday strategies below 60% fail the per-row invested-days gate
individually rather than being refused as a class.

Working as designed. Open question: whether the **intraday half of the app is
measurable at all** on the free data tier — 8 strategies currently produce no
risk-adjusted verdict. That is arithmetic, not a threshold choice.

---

## 6. MTUM (or similar) comparison

If the belief is *"momentum is a risk premium,"* it is purchasable across
hundreds of names with capacity and diversification a 5-name book cannot have.
Comparing the frozen rule against a momentum ETF answers whether the DIY version
adds anything at all.

Cheap, and worth knowing **before** years of forward testing.

---

## 7. Point-in-time Dow only reconstructs from 2013

`KFT` (Kraft, removed 2012) is unfetchable, putting a hole in the roster before
the 2013-09-23 change. Everything earlier uses the fixed-roster approximation.

Not worth solving unless deeper history becomes necessary.

---

## 8. Forward test — not yet started

`FROZEN_DUAL_MOMENTUM.md` is committed (`2ba3107`) and
`engine/forward_tracking.py` is in place with the stop rule under test.

**Not started.** Requires: first observation recorded, and the paper sleeves
running (strategy + a passive SPY control in the same account structure, so live
fills are compared against live fills rather than a backtested index return).

Stop evaluates at **24 months**; continuation review at **48**.
