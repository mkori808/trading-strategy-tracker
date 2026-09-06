# Cost screen across the hypothesis backlog (v2 -- dual estimator, holding-period model)

## Estimator validation (mega-cap sanity check)

Real spread on AAPL/MSFT is well-known to be ~0.5-2bps. Corwin-Schultz and Abdi-Ranaldo (2017, an independently-derived close/high/low estimator) are compared against this and against each other for every candidate below.

| Ticker | n | Corwin-Schultz median (bps) | Abdi-Ranaldo median (bps) |
|---|---:|---:|---:|
| AAPL | 254 | 18.48 | 0.00 |
| MSFT | 254 | 0.00 | 0.00 |

Corwin-Schultz materially overstates spread on higher-volatility names (confirmed on AAPL: 18.5bps vs. Abdi-Ranaldo's 0.0bps over the same year) -- both are reported per candidate below, never just one.

## Backlog table

| Candidate | Breadth verdict | MDA %/yr | n | CS spread (bps) | AR spread (bps) | Holding period | Annual cost CS/AR %/yr | Verdict |
|---|---|---:|---:|---:|---:|---|---|---|
| Index deletion / forced selling | SCREENED_OUT | 11.45 | 44 | 35.8 | 14.9 | 0.06yr | 6.02/2.51 | MOOT -- already dead on breadth |
| Index addition / forced buying | MARGINAL | 5.87 | 44 | 26.9 | 17.2 | 0.06yr | 4.53/2.89 | COST_OK -- stays under MDA under BOTH estimators |
| Spinoff shares dumped by ineligible holders | MARGINAL | 6.50 | 29 | 42.0 | 20.0 | 0.16yr | 2.65/1.26 | COST_OK -- stays under MDA under BOTH estimators |
| Distressed / post-reorg equity | VIABLE_WITH_CAVEAT | 2.82 | 45 | 118.7 | 17.7 | 0.71yr | 1.66/0.25 | COST_OK -- stays under MDA under BOTH estimators |
| Small merger arb below institutional minimum deal size | SCREENED_OUT | 4.10 | 41 | 72.0 | 42.1 | 0.52yr | 1.40/0.82 | MOOT -- already dead on breadth |
| Sub-$5 stocks excluded by fund charters | VIABLE_WEAK_EVIDENCE | 1.59 | 44 | 120.4 | 102.7 | 0.17yr | 7.22/6.16 | COST_DOMINATES -- exceeds MDA under BOTH estimators |
| Tax-loss selling reversal (December) | DEAD | 8.15 | 45 | 24.8 | 0.0 | 1.00yr | 0.25/0.00 | MOOT -- already dead on breadth |
| Sub-$300M cap cross-sectional anomalies (momentum) | PAUSED_BEFORE_PHASE_2 | 3.04 | 45 | 131.6 | 116.9 | 0.17yr | 7.89/7.01 | COST_DOMINATES -- exceeds MDA under BOTH estimators |
| S&P 500+400+600 additions/deletions/migrations (500-only available; 400/600 DATA_BLOCKED) | MARGINAL | 4.16 | 45 | 22.3 | 0.0 | 0.06yr | 3.74/0.00 | COST_OK -- stays under MDA under BOTH estimators |
| Odd-lot tender provisions | DATA_BLOCKED | — | 0 | — | — | — | — | NOT_MEASURED |

## Index deletion / forced selling

- Holding period model: event-driven, ASSUMED holding period 0.060yr (15-trading-day post-effective-date reversal, per the hypothesis's own short-horizon design) -- NOT the raw events/year figure used for breadth
- Sample tickers (n=45): NOV, PRGO, UNM, HBI, LEG, WU, IPGP, UAA, PENN, PVH, FBIN, LUMN, DISH, LNC, NWL, OGN, ALK, SEDG, SEE, WHR, ZION, CMA, ILMN, RHI, AAL, BIO, ETSY, AMTM, QRVO, BWA, CE, FMC, TFX, CZR, ENPH, MKTX, LKQ, MHK, SOLS, LW, MOH, MTCH, PAYC, CPB, POOL
- Corwin-Schultz median: 35.81942838606432 bps
- Abdi-Ranaldo median: 14.937170058714624 bps
- Annual cost by estimator: {'corwinSchultz': 6.017663968858805, 'abdiRanaldo': 2.509444569864057}
- Verdict: **MOOT -- already dead on breadth**

## Index addition / forced buying

- Holding period model: event-driven, ASSUMED holding period 0.060yr (mirrors deletion's short-horizon reversal design) -- NOT the raw events/year figure used for breadth
- Sample tickers (n=45): BRO, DAY, MTCH, FDS, SBNY, SEDG, KDP, ON, CSGP, INVH, FSLR, FICO, PANW, ABNB, BX, HUBB, BLDR, JBL, UBER, DECK, SMCI, CRWD, GDDY, KKR, DELL, ERIE, PLTR, APO, WDAY, DASH, EXE, TKO, WSM, APP, EME, HOOD, CRH, CVNA, FIX, COHR, ECHO, LITE, VRT, FLEX, MRVL
- Corwin-Schultz median: 26.940789989123388 bps
- Abdi-Ranaldo median: 17.197098388123486 bps
- Annual cost by estimator: {'corwinSchultz': 4.526052718172729, 'abdiRanaldo': 2.8891125292047457}
- Verdict: **COST_OK -- stays under MDA under BOTH estimators**

## Spinoff shares dumped by ineligible holders

- Holding period model: event-driven, ASSUMED holding period 0.159yr (~2 months for forced-selling pressure (multiple holder types, not one reversal) to resolve) -- NOT the raw events/year figure used for breadth
- Sample tickers (n=45): AUDAQ, CNR, DLPH, LEN.B, LILA, BRSP, RDVT, RFL, NVT, CHX, EDRY, CPLG, WH, PRSP, SMTA, VNE, RVIC, AMTB, KLXE, FTDR, GTX, REZI, ACA, ETRN, SER, NMRK, ILPT, ARLO, CVET, WAB, LTHM, FOX, FOXA, DSSI, CYCN, DOW, ALC, KTB, CTVA, IAA, DISH, PNTG, CRNC, AINC, BXRXQ
- Corwin-Schultz median: 41.995415170136724 bps
- Abdi-Ranaldo median: 19.993452910440688 bps
- Annual cost by estimator: {'corwinSchultz': 2.6457111557186135, 'abdiRanaldo': 1.2595875333577633}
- Verdict: **COST_OK -- stays under MDA under BOTH estimators**

## Distressed / post-reorg equity

- Holding period model: event-driven, ASSUMED holding period 0.714yr (~9 months for a slower re-rating as mandate-constrained holders become willing/eligible to buy) -- NOT the raw events/year figure used for breadth
- Sample tickers (n=45): MMATQ, SLAM, NUVOQ, CLOE, BIGGQ, WKME, NNAG, AAMCF, EGIOQ, TUPBQ, BGXXQ, SMFL, BFICQ, HHGC, EVVAQ, VTNRQ, ADRT, GHSI, GENE, GRTSQ, NTBLQ, VEVMQ, SBXC, ONYX, LILMF, GAQ, PXDT, SAVEQ, VINOQ, BLEU, CMAXQ, GHIX, ENGC, MONDQ, PFTA, TCSGQ, TFFP, AKTSQ, LEVGQ, BSFC, CYTOF, FREHY, MTEM, DNMRQ, AILEQ
- Corwin-Schultz median: 118.66441392577252 bps
- Abdi-Ranaldo median: 17.71141835678185 bps
- Annual cost by estimator: {'corwinSchultz': 1.6613017949608153, 'abdiRanaldo': 0.24795985699494585}
- Verdict: **COST_OK -- stays under MDA under BOTH estimators**

## Small merger arb below institutional minimum deal size

- Holding period model: event-driven, ASSUMED holding period 0.516yr (~6 months announcement-to-close, standard small-cap M&A timeline) -- NOT the raw events/year figure used for breadth
- Sample tickers (n=45): CSTR, CCLP, DSKE, KNTE, NGM, SCTL, SMMF, ZFOX, FATH, SCX, LABP, VIA2, ADTH, TGAN, VERY, CVLY, TSRI, TSRI, FNCB, AKLI, DPSI, DPSI, ASTR, ASTR, SDPI, CALB, FREE, ASXC, ALLGF, MRDB, MRDB, ALIM, GTHX, DOMA, SGE, AUGX, AAN, HMNF, LLAP, GRDI, GVP, ITI, ARC, LUMO, MNTX
- Corwin-Schultz median: 71.98799159866734 bps
- Abdi-Ranaldo median: 42.05571201801728 bps
- Annual cost by estimator: {'corwinSchultz': 1.3954595294510899, 'abdiRanaldo': 0.8152338021954119}
- Verdict: **MOOT -- already dead on breadth**

## Sub-$5 stocks excluded by fund charters

- Holding period model: cross-sectional, 12 rebalances/yr @ 50% turnover (holding period ~= 0.17yr)
- Sample tickers (n=45): ABEV, CYCN, HCTI, NCTY, SNWV, AIRI, DBGI, IHRT, MGNX, SEER, ABOS, ASPI, BTQ, CSAI, EVLV, GROW, INEO, LPSN, NEOV, OVID, RDGT, SLMT, UROY, ZVIA, CNTN, JEM, NABL, SNSC, XCH, ALAR, AYTU, CATO, CNDT, DETX, ENVB, GDHG, HPAI, IVF, LXEH, MWYN, ORIO, RCON, SJ, TELO, VHUB
- Corwin-Schultz median: 120.37967492283985 bps
- Abdi-Ranaldo median: 102.7176101453368 bps
- Annual cost by estimator: {'corwinSchultz': 7.222780495370391, 'abdiRanaldo': 6.163056608720208}
- Verdict: **COST_DOMINATES -- exceeds MDA under BOTH estimators**

## Tax-loss selling reversal (December)

- Holding period model: cross-sectional, 1 rebalances/yr @ 100% turnover (holding period ~= 1.00yr)
- Sample tickers (n=45): A, ADSK, ALL, AON, AVGO, BDX, BR, CB, CHRW, CMS, CPRT, CTVA, DELL, DPZ, EFX, ES, F, FIS, GD, GNRC, HCA, HSIC, IFF, ITW, KEYS, LDOS, LRCX, MCHP, MNST, MSFT, NI, NWS, OXY, PH, PPL, RDDT, RSG, SNDK, STZ, TECH, TRGP, TXT, USB, VRTX, WELL
- Corwin-Schultz median: 24.80878921446234 bps
- Abdi-Ranaldo median: 0.0 bps
- Annual cost by estimator: {'corwinSchultz': 0.2480878921446234, 'abdiRanaldo': 0.0}
- Verdict: **MOOT -- already dead on breadth**

## Sub-$300M cap cross-sectional anomalies (momentum)

- Holding period model: cross-sectional, 12 rebalances/yr @ 50% turnover (holding period ~= 0.17yr)
- Sample tickers (n=45): QH, FRGT, GIPR, STKH, EHGO, PW, CANF, DKI, WCT, INM, GRI, YCBD, WAFU, BGL, ARBB, SNGX, GFAI, OST, INUV, UPLD, MIMI, BRN, UCL, WETH, MGIH, ENLV, LHAI, RVP, BCG, PRZO, USEA, FKWL, AVAT, SLND, NXXT, CRVO, PETS, PAAI, BRIA, VHC, DOMH, IMMP, VSME, SORA, COPR
- Corwin-Schultz median: 131.579413678895 bps
- Abdi-Ranaldo median: 116.86193346924576 bps
- Annual cost by estimator: {'corwinSchultz': 7.894764820733701, 'abdiRanaldo': 7.011716008154746}
- Verdict: **COST_DOMINATES -- exceeds MDA under BOTH estimators**

## S&P 500+400+600 additions/deletions/migrations (500-only available; 400/600 DATA_BLOCKED)

- Holding period model: event-driven, ASSUMED holding period 0.060yr (same short-horizon reversal assumption as deletion/addition alone) -- NOT the raw events/year figure used for breadth
- Sample tickers (n=45): VST, CRWD, GDDY, KKR, SW, DELL, ERIE, PLTR, AMTM, TPL, APO, LII, WDAY, DASH, EXE, TKO, WSM, COIN, DDOG, TTD, XYZ, IBKR, APP, EME, HOOD, SOLS, Q, SNDK, ARES, CRH, CVNA, FIX, CIEN, COHR, ECHO, LITE, VRT, CASY, VEEV, FDXF, FLEX, MRVL, HONA, FERG, RDDT
- Corwin-Schultz median: 22.253372720172464 bps
- Abdi-Ranaldo median: 0.0 bps
- Annual cost by estimator: {'corwinSchultz': 3.738566616988974, 'abdiRanaldo': 0.0}
- Verdict: **COST_OK -- stays under MDA under BOTH estimators**

## Odd-lot tender provisions

- Holding period model: N/A -- DATA_BLOCKED, no event population identified
- Sample tickers (n=0): none
- Corwin-Schultz median: None bps
- Abdi-Ranaldo median: None bps
- Annual cost by estimator: {}
- Verdict: **NOT_MEASURED**
