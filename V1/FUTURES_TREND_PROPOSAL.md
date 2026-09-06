# Futures trend-following — question zero and the power gate

**Written 2026-08-11, BEFORE any data was sourced or code written.**

Steps 1 and 2 of the plan. Nothing is built until this file says the design can
answer its own question.

---

## Question zero: claimed effect size and mechanism

Stated first, because a minimum detectable alpha cannot be computed without
positing an alpha. The power question is downstream of the mechanism question.

**Claimed effect:** 2–5%/yr, time-series trend following, before fees.

**Mechanism — two, both with a named counterparty:**

1. **Slow information diffusion.** Prices adjust to fundamental news over weeks
   to months rather than instantly, so recent direction carries information
   about near-future direction.
2. **Systematic risk transfer from hedgers.** Commercial producers and
   consumers hold price risk they want to shed and are willing to pay to shed
   it. Speculators taking the other side are compensated. This is a *structural*
   payment for bearing risk, not an inefficiency.

Mechanism (2) is the load-bearing one, and it is why this is worth testing at
all after the equity momentum programme failed. **It names who is paying and
why.** An airline hedging jet fuel is not trying to win; it is buying certainty
and accepting a worse expected price for it. That payment does not require
anyone to be wrong, so it has no reason to arbitrage away — unlike a
cross-sectional ranking effect whose only story is that other participants have
not noticed.

**Falsifying condition:** if hedger positioning (COT data) shows no persistent
directional imbalance, mechanism (2) is absent and the claimed effect reduces to
(1) alone, which is far more crowded and far more likely already priced.

---

## Step 2: minimum detectable alpha, computed before building

`MDA = 2 * sigma_res / sqrt(T)`

| σ_res | 10y | 20y | **35y** | detects 2%/yr? | detects 5%/yr? |
|---|---|---|---|---|---|
| 8% | 5.06% | 3.58% | **2.70%** | no | **YES** |
| 10% | 6.32% | 4.47% | **3.38%** | no | **YES** |
| 12% | 7.59% | 5.37% | **4.06%** | no | **YES** |
| 15% | 9.49% | 6.71% | **5.07%** | no | no |

Years required:

| σ_res | for 2%/yr | for 3%/yr | for 5%/yr |
|---|---|---|---|
| 8% | 64y | 28y | **10y** |
| 10% | 100y | 44y | **16y** |
| 12% | 144y | 64y | **23y** |
| 15% | 225y | 100y | 36y |

**Breadth** (cross-asset correlation is far below equity mega-caps):

| design | independent bets/yr |
|---|---|
| 60 contracts, monthly, ρ=0.15 | **229** |
| 60 contracts, monthly, ρ=0.25 | **181** |
| 60 contracts, monthly, ρ=0.50 | 130 |
| *Dual Momentum, for scale* | *34.6* |

### VERDICT: conditionally proceed

**With 35 years of history and σ_res ≤ 12%, MDA is 2.7–4.1%/yr — inside the
claimed 2–5%/yr range.** This is the first design examined in this project whose
MDA falls within its own claimed effect. Breadth is 4–7× Dual Momentum's, and
the reason is diversification across genuinely different asset classes rather
than more names inside one correlated universe.

**But the test is informative only in the UPPER half of the claimed range:**

- effect ~5%/yr → **detectable**
- effect ~3%/yr → **marginal** (detectable at σ_res 8%, not at 12%)
- effect ~2%/yr → **not detectable**, needing 64–144 years

**Pre-commitment:** a null result rules out a ~5%/yr effect. It does **not** rule
out 2%/yr, and must not be reported as though it did. That is the error made in
the equity programme and corrected afterwards; it is written down here first.

**Hard dependency:** the verdict rests entirely on obtaining ≥ 30 years of
history. At 10 years MDA is 5.1–9.5%/yr and the design cannot resolve any part
of the claimed range. **If the data source cannot deliver 1990-or-earlier depth
across the sector spread, stop at step 3.**

---

## Problem found in step 6 (tradable replicators)

**DBMF launched May 2019; KMLM December 2020.** A replicator regression can
therefore cover at most ~6 years, not 35.

At 6 years and σ_res 10%, MDA is ~8.2%/yr — **worse than the equity programme's
12%/yr only in degree.** Step 6 will be structurally incapable of establishing
whether the strategy is distinguishable from a purchasable managed-futures
product.

This is the same wall as before, and it splits the two questions:

- **"Is it real?"** — answerable on 35 years of futures data. Proceed.
- **"Is it mine, or already purchasable?"** — **not** answerable statistically at
  ETF-history length. It must be argued by CONSTRUCTION: compare holdings,
  turnover, sector weights and fee load against the ETF's published
  methodology, and treat the return correlation as descriptive rather than as a
  significance test.

Recording this now so a t-stat of 1.2 against DBMF in five years' time is not
mistaken for evidence of independence.

---

## Remaining steps, unchanged

3. Source continuous back-adjusted data (Norgate/CSI). **Verify roll methodology
   and confirm ≥30y depth before proceeding** — this is the gating dependency.
4. Model costs explicitly: roll cost, margin, contract-level slippage. Do **not**
   reuse `estimate_spread()`, which is an equity dollar-volume heuristic and has
   no meaning for futures.
5. Frozen-analogue rules only. No parameter search. Volatility-scale positions to
   equal risk contribution.
6. Replicator comparison — by construction, per the limitation above.

---

## GATE RESULT 2026-08-11: **STOP BEFORE LICENSING**

Three pre-licensing items resolved. The gate fails on item 1, and item 3 is why.

### Item 3 — disaggregated COT weakens mechanism (2) materially

The legacy report's "commercial" category bundles producers with swap dealers.
Split apart, the hedging story does not hold where it matters most:

| contract | producer/merchant net | swap dealer net | supports mechanism (2)? |
|---|---|---|---|
| **CRUDE OIL** | **+4.7%** (net LONG) | −15.1% | **NO** |
| **GOLD** | −8.2% | **−15.5%** | mostly swap dealer |
| **NATURAL GAS** | **+7.9%** (net LONG) | −8.1% | **NO** |
| SILVER | **−15.9%** | −2.5% | **yes** |
| COPPER | **−19.4%** | +17.9% | **yes** |
| CORN | **−12.1%** | +8.3% | **yes** |
| SOYBEANS | **−10.7%** | +11.4% | **yes** |

**Crude oil producers are net LONG.** For the single largest energy contract,
the −12.9% "commercial net short" that passed the first falsification test was
driven by swap dealers intermediating OTC books, not by producers shedding
physical risk. Gold is mostly the same. Natural gas likewise.

Swap-dealer imbalance is a different phenomenon with a weaker reason to persist:
an intermediary hedging its own book is not a counterparty paying to shed
fundamental price risk, and its positioning can reverse with client flow.

**Mechanism (2) survives in silver, copper, corn and soybeans -- agricultural
and industrial physicals -- and fails in the largest, most liquid energy and
precious-metal contracts.** That is close to the opposite of what a
breadth-hungry design needs.

### Item 1 — MDA under the mechanism-backed universe

Cutting from ~60 contracts to ~18 (metals/energy/FX) inflates MDA ~1.4x:

| σ_res | MDA 35y, full universe | MDA 35y, mechanism-backed | ≤5%/yr? |
|---|---|---|---|
| 10% | 3.38% | 4.56% | yes |
| **12%** | 4.06% | **5.48%** | **NO** |
| 13% | 4.39% | 5.93% | **NO** |
| 15% | 5.07% | 6.85% | **NO** |

At a realistic trend-program residual volatility of 12–15%, mechanism-backed MDA
is **5.5–6.9%/yr, above the 5%/yr ceiling** the gate was pre-committed to.

And item 3 makes this **optimistic**: with crude, gold and natural gas removed,
the mechanism-backed set is closer to 8–12 contracts than 18, inflating MDA
further and concentrating it in ags and industrial metals.

### Item 2 — the rates decision, now moot but recorded

Rates were to be included under mechanism (1) and labelled the crowded claim, or
excluded as mechanism-inconsistent. **10-Year Treasury commercials are net LONG
in 0.0% of weeks net short** — decisively inconsistent with mechanism (2).
Recorded for completeness; the gate fails before the choice matters.

### Verdict

**Do not license data.** The pre-committed condition was: *"If MDA exceeds 5%/yr
there, the gate fails regardless of vendor depth."* It does, at any realistic
residual volatility, and the disaggregated data shrinks the qualifying universe
further rather than expanding it.

This cost roughly one hour and no vendor fees. The equity programme cost weeks
and reached the same class of conclusion — that the design could not resolve its
own claim — only after the work was complete.

**What would reopen it:** a mechanism-backed universe large enough to sustain
the breadth assumption. That likely means ags and industrial metals breadth
(many contracts, deep history, genuine producer hedging) rather than the
liquid-macro spread trend programs normally run. That is a *different* strategy
with a different literature, and it would need its own question zero.
