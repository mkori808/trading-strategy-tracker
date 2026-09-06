# Georgia Tech WRDS / CRSP Access and Integration Plan

Status: access-verification and integration-planning only  
Research date: 2026-08-22  
User fact treated as confirmed: currently enrolled Georgia Tech OMSA student  
Data acquisition status: **no account was created and no dataset was purchased, downloaded, ingested, scraped, or installed**

## Executive answer

Georgia Tech is a current WRDS subscribing institution: the public WRDS registration form currently lists **Georgia Institute of Technology** as a selectable subscriber. This is stronger and more current evidence than the historical Georgia Tech technology-fee report used in the provider-neutral study. The institutional relationship appears to be sponsored or administered through the Scheller College of Business, but the public registration form names the Institute rather than limiting the subscription to Scheller.

An enrolled OMSA student does **not yet have a publicly verifiable entitlement**. WRDS's general policy allows named master's accounts for full-time master's students at subscribing institutions, with web, SSH, and FTP access and no permanent WRDS disk. It does not exclude online students. It does, however, condition this account type on full-time status, requires local representative approval, and lets the institution impose additional restrictions. Public Georgia Tech pages do not say whether OMSA enrollment satisfies WRDS's full-time test, whether non-Scheller students are eligible, or whether independent research outside a course is within Georgia Tech's license.

Georgia Tech's exact current CRSP product entitlement is also not public. Recent Georgia Tech teaching/research evidence makes CRSP access plausible, but WRDS platform access is not proof of a CRSP subscription. Treat CRSP U.S. Stock, Stock & Indexes, S&P membership, and CRSP/Compustat Merged (CCM) as **unconfirmed until the user's approved account shows those products** or Georgia Tech's WRDS representative confirms them.

The practical answer is therefore:

> **Probably, but not yet proven.** The institutional WRDS route is real and CRSP is the right technical source. One short registration/login check plus a written license question can resolve the remaining OMSA, product, and local-retention gates. If approved for CRSP U.S. Stock with daily CIZ files and delisting data, the safest first extraction is a deliberately small, PERMNO-keyed event pilot—not the full market.

## 1. Fixed project requirements

`research/PIT_DATA_REQUIREMENTS.md` remains the provider-neutral source of truth and is not changed by this plan. The CRSP-relevant must-haves are:

- A permanent **security/share-class** identifier; ticker cannot be the key.
- Date-effective ticker, name, exchange, security-type, and common-stock eligibility history.
- Active and inactive/delisted securities, with listing and delisting continuity.
- Daily regular-session raw OHLCV plus a consistent return/adjustment representation.
- Explicit terminal economics: delisting return, cash value, successor security, or a flagged unresolved case.
- Corporate-action history sufficient to prevent dividend/split double counting.
- Historical index membership for Dow/S&P-specific research; no membership ledger is needed for Tier C.
- A local `security_history.parquet`, partitioned daily Parquet, and manifest, followed by strict identity, coverage, action, delisting, and PIT audits.

Current loader v1 specifically requires:

| Destination | Required content |
| --- | --- |
| `security_history.parquet` | `security_id`, `ticker`, `effective_start`, `effective_end`, `known_at`, `is_us_listed`, `is_common_stock`, `security_type`, `exchange`, `is_acquired`, `delisting_reason` |
| Daily Parquet | `security_id`, `date`, `Open`, `High`, `Low`, `Close`, `RawClose`, `Volume`, `DelistingReturn`; optional `MarketCap` |
| Manifest | `priceBasis = total_return_adjusted_ohlcv` and affirmative provenance/coverage flags |

The manifest booleans are assertions, not proof. No bundle becomes runnable until the audits in the provider-neutral contract pass.

## 2. Access findings and confidence

### Finding matrix

| Question | Finding | Confidence | Basis / unresolved point |
| --- | --- | --- | --- |
| Current Georgia Tech WRDS subscription | **Confirmed** | High | The current public [WRDS registration form](https://wrds-www.wharton.upenn.edu/register/du/) lists “Georgia Institute of Technology” as a subscriber. |
| Sponsoring/administering unit | **Likely: Scheller** | Medium-high | A Georgia Tech FY2017 technology-fee report identified Scheller-funded tools through the WRDS portal, and Scheller is the natural current support route. Current contract ownership is not public. |
| Institution-wide vs. Scheller-only eligibility | **Unclear** | Low | The WRDS subscriber label is Institute-wide; public Georgia Tech pages do not publish eligible department rules. |
| Named master's self-registration | **Likely, subject to approval** | Medium | WRDS allows full-time master's students at subscribing institutions and routes the request to the local WRDS representative. Georgia Tech may add restrictions. |
| Faculty sponsorship for a master's account | **No under general WRDS rules; Georgia Tech override unclear** | Medium-high | The registration form requires a sponsor for Research Assistant accounts, not ordinary master's accounts. Local approval still applies. |
| Faculty sponsorship for an RA account | **Yes** | High | Explicit in [WRDS account types](https://wrds-www.wharton.upenn.edu/pages/about/wrds-account-types/) and the registration form. |
| Course enrollment/class code required | **No for an approved named master's account; yes for class account** | High for general WRDS rule | Class accounts are course-bound and created by faculty. Georgia Tech might offer only a class route to some students. |
| Remote access | **Confirmed generally** | High | Named master's accounts support website, SSH, and FTP; WRDS also supports remote PostgreSQL/Python/R and web tools. |
| Campus network or Georgia Tech VPN required | **No under normal named-account WRDS access; local exception unclear** | Medium-high | WRDS uses direct login plus Duo MFA. IP-based Access Pass is a different, location-controlled route. No public Georgia Tech page says VPN is required for WRDS. |
| Degree-program/school affiliation controls access | **Unclear** | Low | WRDS permits institutions to impose extra restrictions. No public Georgia Tech policy resolves OMSA/non-Scheller eligibility. |
| Online status itself disqualifies a student | **No evidence of that; likely no** | Medium | WRDS account rules distinguish account/enrollment type, not online vs. on-campus. Georgia Tech's local approval remains decisive. |
| OMSA counts as “full-time master's” for WRDS | **Unclear** | Low | Current enrollment is confirmed, but course load/full-time classification and the local representative's interpretation are not public. |
| Independent noncommercial academic research | **Generally yes for a named account; Georgia Tech license unclear** | Medium | WRDS terms allow academic, noncommercial research. A class account is limited to coursework. |
| Current CRSP U.S. Stock entitlement | **Likely, not confirmed** | Medium | Georgia Tech has current WRDS use and recent CRSP-related instructional evidence, but public subscription inventory is absent. Verify in the authenticated product list. |
| Daily stock and delisting fields | **Unclear until login** | Low-medium | They are part of CRSP U.S. Stock/CIZ, but Georgia Tech's exact product/frequency package is not public. |
| CRSP Stock & Indexes / S&P membership | **Unclear** | Low | CRSP documents the membership files, but access is product-dependent. |
| CRSP/Compustat Merged (CCM) | **Unclear and not required for this phase** | Low | CCM is separately referenced by WRDS as a product requiring relevant subscriptions. |

### What is actually confirmed about Georgia Tech

The live public subscriber selector is the decisive current evidence that Georgia Tech has a WRDS institutional subscription. It does **not** reveal databases licensed within WRDS. The older Georgia Tech fee report remains useful only for identifying Scheller as the likely institutional relationship; it should not be used as proof of the present product package.

Georgia Tech's official 2026 course material also shows that datasets are currently delivered through WRDS in Scheller finance instruction, but it does not enumerate the Georgia Tech subscription or prove that an OMSA student receives a reusable personal account. The public [Georgia Tech course schedule](https://oscar.gatech.edu/bprod/bwckctlg.p_disp_listcrse?crse_in=8803&schd_in=%25&subj_in=MGT&term_in=202602) confirms that OMSA/MS Analytics students take Scheller-coded MGT coursework. This makes a Scheller support request appropriate, but it does not erase the full-time/account-type gate.

### Account implications for OMSA

WRDS says master's/undergraduate accounts are for **full-time** students, have a default two-year expiration, allow web/SSH/FTP, provide no permanent WRDS disk, and are disabled during extended semester breaks. Summer access is available to students enrolled in a summer course who are not working as interns. See [WRDS account types](https://wrds-www.wharton.upenn.edu/pages/about/wrds-account-types/).

That produces three plausible routes:

1. **Named Master's/Undergrad account** — best route for independent academic work; no sponsor field under general rules, but local representative approval is required.
2. **Research Assistant account** — fallback if the local representative requires a Georgia Tech faculty sponsor; it includes WRDS disk and is explicitly tied to research for faculty at the institution.
3. **Class account** — weakest route for this project because it is course-specific and time-limited. It should not be treated as permission for independent research or post-course retention.

Online status is not a stated WRDS exclusion. The potential blocker is whether Georgia Tech/WRDS classifies the user's current OMSA load as full-time and whether the institutional license is restricted by school or use.

## 3. Exact manual verification checklist

Do this without sharing credentials and without downloading data:

1. Open the official [WRDS registration form](https://wrds-www.wharton.upenn.edu/register/du/).
2. Select **Georgia Institute of Technology** as Subscriber.
3. Use the Georgia Tech institutional email address. Select the ordinary **Master's/Undergraduate** account type if offered; enter the real OMSA/Analytics department description. Do not select Research Assistant unless there is an actual Georgia Tech faculty sponsor.
4. Submit the account request. A normal non-class request should go to Georgia Tech's local WRDS representative for approval. Do not provide credentials to this project or any other person.
5. If approved, log in with WRDS MFA from an ordinary off-campus connection. A named account should not require Georgia Tech VPN unless the approval notice says otherwise.
6. On **Get Data**, inspect **My Subscriptions** or the visible database list. Record product names only; do not run a query yet.
7. Confirm whether these exact product families are visible:
   - CRSP U.S. Stock / CRSP Stock (annual or quarterly update)
   - CIZ `StkDlySecurityData` or `StkDlySecurityPrimaryData`
   - `StkSecurityInfoHist`
   - `StkDistributions`
   - `StkDelists`
   - `StkDlyCumulativeAdjFactor`
   - CRSP Stock & Indexes and `StkIndMembership`, if present
   - CRSP/Compustat Merged Database (optional, not a blocker)
8. Use WRDS's schema/table browser or `wrds.Connection().list_libraries()` and `list_tables()` only after approval. The [WRDS CIZ announcement](https://wrds-www.wharton.upenn.edu/pages/data-announcements/changes-to-crsp-data/) says current work must use CIZ table and variable names; do not build a new pipeline around legacy `dsf`/`dsedelist` names.
9. Before any extraction, ask the local representative in writing whether this independent, noncommercial project may store filtered raw extracts and transformed Parquet locally, whether OneDrive is permitted, and what must be deleted after graduation or account termination.

### Best escalation path

Use these in order:

1. **Georgia Tech local WRDS representative** — the person who receives the registration request is the only authoritative source for OMSA eligibility, CRSP product entitlement, and the Georgia Tech license.
2. **Georgia Tech Library eResource ticket** — submit the official [eResource issue form](https://www.library.gatech.edu/eresource-issue) and name WRDS/CRSP. The form accepts student affiliation, off-campus/VPN context, database name, and details. The Library says help tickets usually receive a response within one day; phone 404-894-4500 is also listed on [Ask Us](https://www.library.gatech.edu/help).
3. **Scheller IT Services** — `helpdesk@scheller.gatech.edu`, 404-385-5188, from the official [Scheller IT page](https://www.scheller.gatech.edu/directory/offices/itservices/index.html). Ask them to route the request to the WRDS institutional representative/data administrator, not merely troubleshoot a password.
4. **WRDS Support** — use the WRDS support route or `wrds-support@wharton.upenn.edu` for account mechanics or to identify the institutional representative. WRDS cannot override Georgia Tech's license.
5. **OMSA administration** — use only if the WRDS representative asks for confirmation of enrollment/full-time classification or program sponsorship.

### Concise message

> Subject: OMSA eligibility and CRSP entitlement through Georgia Tech WRDS
>
> I am a currently enrolled Georgia Tech OMSA student conducting independent, noncommercial academic quantitative-finance research. Am I eligible for a named WRDS master's account, and does Georgia Tech's current subscription include CRSP U.S. Stock daily CIZ data, security history, distributions, delisting returns, and Stock & Indexes/S&P membership? If eligible, may I retain filtered raw extracts and transformed Parquet locally during enrollment, and what must be deleted after graduation? Please route this to Georgia Tech's WRDS institutional representative if needed.

## 4. CRSP entitlement to verify

WRDS is the platform; CRSP products are separate entitlements. The authenticated check must distinguish:

| Product/capability | Needed? | Public Georgia Tech status | Manual proof |
| --- | --- | --- | --- |
| CRSP U.S. Stock Database | Yes | Likely, unconfirmed | Product visible and daily CIZ tables queryable |
| Daily stock files | Yes | Unconfirmed | `StkDlySecurityData`/Primary present with daily coverage |
| Security information history | Yes | Unconfirmed | `StkSecurityInfoHist` present |
| Events/distributions | Yes | Unconfirmed | `StkDistributions` present |
| Delisting history/return | Yes | Unconfirmed | `StkDelists` plus daily delisting fields present |
| Cumulative adjustment factors | Yes for v1 normalization | Unconfirmed | `StkDlyCumulativeAdjFactor` present |
| CRSP Stock & Indexes / membership | Needed for CRSP-supplied S&P membership | Unconfirmed | `StkIndMembership` and S&P family visible |
| CRSP/Compustat Merged (CCM) | Optional | Unconfirmed | CCM library/link table visible |

CCM does not supply the missing price/delisting foundation and is not needed to map PERMNO-based CRSP stock data. It becomes useful later for Compustat fundamentals or identifier linking.

## 5. Current CRSP CIZ schema mapping

CRSP and WRDS retired the legacy SIZ/FIZ update stream after December 2024. This plan uses current CIZ 2.0 names from the official [CRSP CIZ file guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf) and the [WRDS transition notice](https://wrds-www.wharton.upenn.edu/pages/data-announcements/changes-to-crsp-data/). The exact PostgreSQL library prefix must be discovered from the approved account rather than guessed.

### Identity and eligibility

| Project concept/destination | CRSP CIZ source | Transformation / caveat |
| --- | --- | --- |
| `security_id` | `StkSecurityInfoHist.PERMNO` | Store as string, optionally namespace as `crsp:<PERMNO>`. PERMNO is security-level and is the trading key. |
| company identifier | `PERMCO` | Preserve as auxiliary issuer key; never use it to collapse multiple share classes. |
| ticker | `Ticker` and/or `TradingSymbol` in `StkSecurityInfoHist` | Use date-effective history, not current header value. Verify which field matches desired display/exchange convention. |
| name | `SecurityNm`; issuer name from `StkIssuerInfoHist.IssuerNm` | Preserve both security/share-class name and issuer name. |
| interval | `SecInfoStartDt`, `SecInfoEndDt` | Feed `effective_start`, `effective_end`. Check inclusive boundary semantics in a pilot. |
| data coverage | `SecurityBegDt`, `SecurityEndDt` | Provenance/coverage checks, not a substitute for identity intervals. |
| exchange | `PrimaryExch`, `ExchangeTier` | Feed `exchange` and exchange audit fields. |
| common-stock eligibility | `SecurityType`, `SecuritySubType`, `ShareType`, `IssuerType`, `USIncFlg`, `ConditionalType` | Define and preregister the eligible code set. Do not blindly recreate legacy share-code 10/11 without checking CIZ flags. |
| active/listing status | `SecurityActiveFlg`, `TradingStatusFlg`, `ConditionalType` | Derive `is_us_listed` and status intervals; distinguish halt/suspension from delisting. |
| `known_at` | No equivalent public field identified | Keep the immutable CRSP snapshot/export timestamp in manifest provenance, **not** in this field. For an operationally observable ticker/listing/status interval, a proposed normalization is `known_at = effective_start` (or first usable session under a documented next-session rule). Validate this convention event-by-event; do not use it for index announcements or claim CRSP supplied announcement-time knowledge. |
| `is_acquired` / `delisting_reason` | `DelActionType`, `DelStatusType`, `DelReasonType`, `DelPaymentType` from `StkDelists` | Derive acquisition flag and retain the original four reason/payment fields. |

CRSP describes PERMNO/PERMCO as permanent identifiers that track securities through ticker/name changes and corporate events; see [CRSP permanent identifiers](https://www.crsp.org/research/permno/). This is the key advantage over the current ticker-keyed cache.

### Daily prices and returns

| Project field/concept | CRSP CIZ source | Transformation / caveat |
| --- | --- | --- |
| `date` | `DlyCalDt` | Normalize to an exchange session date. |
| raw close | `DlyClose`; fallback `DlyPrc` only with `DlyPrcFlg` retained | `DlyPrc` may be a bid/ask average if no trade. CIZ separates value and price flag; do not use legacy `abs(PRC)` logic on CIZ. |
| raw OHLC | `DlyOpen`, `DlyHigh`, `DlyLow`, `DlyClose` | Open availability is broadly suitable for the 1996+ project; missing/nontrading values must remain missing. |
| total return | `DlyRet` | Current CIZ return, including a delisting-return row where applicable. |
| price return | `DlyRetx` | Return excluding ordinary dividends; retain for reconciliation. |
| dividends in return | `DlyOrdDivAmt`, `DlyNonOrdDivAmt`, `DlyDistRetFlg`, `DlyFacPrc` | Audit `DlyRet`; do not apply again to a return-based series. |
| volume | `DlyVol` | Raw shares traded. Apply only the documented share/split convention needed by the v1 adjusted view. |
| shares outstanding | `StkDlyCumulativeAdjFactor.DlyShrOut` | Optional; CRSP documents it in thousands. |
| market cap | `DlyCap` plus `DlyCapFlg` | Optional `MarketCap`; preserve units and price-quality flag. |
| price adjustment factor | `DlyCumFacPr` | Use for factor reconciliation, not on top of `DlyRet`. |
| share/volume factor | `DlyCumFacShr` | Use for split-consistent share/volume normalization. |

The official guide states that `DlyOpen` is available for reporting securities after June 15, 1992, so the requested 1996-present research window is structurally supported. It also states that CIZ separates the old negative-price signal into `DlyPrc` plus `DlyPrcFlg`; negative-price handling is a **legacy-format concern**, not a reason to take the absolute value of every CIZ price.

### Corporate actions

| CRSP CIZ table/field | Role |
| --- | --- |
| `StkDistributions.PERMNO`, `DisExDt`, `DisSeqNbr` | Event key/date |
| `DisOrdinaryFlg`, `DisType`, `DisFreqType`, `DisPaymentType`, `DisDetailType`, `DisTaxType` | Distribution classification |
| `DisDivAmt` | Cash dividend/distribution amount |
| `DisFacPr`, `DisFacShr` | Price and share adjustment factors |
| `DisDeclareDt`, `DisRecordDt`, `DisPayDt` | Declaration, record, and payment dates |
| `DisPERMNO`, `DisPERMCO` | Security/issuer received or linked in a distribution, merger, exchange, or spin-off |

Keep this event table in immutable staging and use it to reconcile the v1 adjusted view. The current engine does not consume a separate action table.

### Delisting and terminal economics

| CRSP CIZ field | Role / destination |
| --- | --- |
| `PERMNO`, `DelistingDt` | Terminal event identity/date |
| `DelDtPrc`, `DelDtPrcFlg` | Delisting-date price and quality |
| `DelActionType`, `DelStatusType`, `DelReasonType`, `DelPaymentType` | `delisting_reason`, acquisition/status logic, audit provenance |
| `DelPERMNO`, `DelPERMCO` | Successor security/company link when present |
| `DelRet`, `DelRetMissType` | Explicit delisting return or reason it is missing |
| `DelNextDt`, `DelNextPrc`, `DelNextPrcFlg` | Post-delisting observed value/date |
| `DelAmtDt`, `DelDivAmt`, `DelDisType` | Cash/distribution amount and timing |
| `DelDlyDt` | Date used to place the daily delisting record |

CRSP defines delisting return as the return from the last trading price to a post-delisting price or total distributions, with zero used when the security becomes worthless and no trading opportunity exists; insufficient information produces a missing value. See the official [CRSP calculation guide](https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-calculations-index-methodologies-guide-flat-file-format-2-0/).

In CIZ, the daily time-series files include a subset of delisting fields and put the delisting return into `DlyRet` on a daily row marked `DlyDelFlg = Y`. WRDS's current [CIZ event-study code](https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/run-an-event-study-ciz-format-macro/) explicitly says the delisting return is already incorporated. Do not compound `DelRet` into that CIZ `DlyRet` a second time.

### Historical index membership

`StkIndMembership` contains `PERMNO`, `INDFAM`, `INDNO`, `MbrStartDt`, `MbrEndDt`, and `MbrFlg` in current CIZ documentation. CRSP also documents S&P 500-universe indexes and WRDS provides a “Downloading the S&P 500 Constituents” tutorial that requires a CRSP subscription. Therefore:

- **S&P 500:** CRSP Stock & Indexes can provide historical PERMNO membership if Georgia Tech's entitlement includes the required files. Verify the index family and date semantics in the account. The public schema does not establish announcement/known-at timestamps.
- **Dow:** no official CRSP research-data documentation reviewed here establishes a complete historical Dow 30 membership ledger in the CRSP stock product. Treat Dow membership as a separate input.
- **Product distinction:** the CRSP guide says membership data is available to subscribers of specified Stock & Index products. A stock-only entitlement cannot be assumed to include it.

## 6. Tier sufficiency

### Tier A — Dow / DM-MRM long history

**CRSP daily stock data + a verified external Dow membership ledger is sufficient for the core data problem**, provided the joins resolve to PERMNO and the project handles membership knowability and terminal events correctly.

Still needed:

- Complete Dow membership start/end dates for the requested window, including temporary constituents.
- Preferably announcement/known-at dates; otherwise adopt the provider-neutral effective-date-only rule and trade only after changes are effective.
- A carefully audited membership-to-PERMNO crosswalk.
- Risk-free/cash and benchmark inputs already used by DM/MRM.
- A v1-compatible terminal-value representation because the current loader does not itself apply `DelistingReturn`.

This remains the safest first production target because the member set and exceptional events are small enough to audit manually.

### Tier B — S&P 500

**Yes, if Georgia Tech has CRSP Stock & Indexes/S&P membership.** CRSP's PERMNO identity, daily history, distributions, and delisting economics address the 429 unfetchable, 637 incomplete-tenure, and 52 ambiguous/reused-ticker defects in the current local reconstruction. Historical membership must come either from entitled `StkIndMembership` or a separately licensed membership source.

Remaining gaps:

- Announcement/known-at membership dates may still be absent.
- The existing open ledger should be reconciliation evidence, not the authoritative identity source.
- Every membership record must resolve to PERMNO and pass tenure/warmup/terminal audits.

### Tier C — all eligible U.S. common stocks

**CRSP U.S. Stock is sufficient for the base identity/price/action/delisting bundle.** No index membership is required. It provides the right permanent key, daily prices and returns, event histories, inactive securities, and terminal economics.

Remaining gaps:

- Translate CIZ security/share classifications into the project's preregistered U.S. common-stock rule.
- Resolve missing delisting returns without silently using zero or -100%.
- Preserve bid/ask-price flags, suspensions, relistings, multiple share classes, and successor links.
- Build a v1 total-return-compatible OHLC view and audit it against `DlyRet`.
- Historical sectors, earnings events, and intraday execution data remain separate acquisitions.

## 7. Adjustment representation and delisting logic

### Safest representation for this backtester

The long-term canonical design should keep raw CRSP OHLCV, `DlyRet`/`DlyRetx`, distributions, cumulative adjustment factors, and delisting events separately. The current loader, however, requires total-return-adjusted OHLCV plus `RawClose`. To satisfy it without double counting:

1. Preserve raw CIZ rows and event tables unchanged in licensed staging.
2. Set `RawClose` from actual `DlyClose`; allow a flagged `DlyPrc` bid/ask fallback only under an explicit policy.
3. Build one versioned cumulative **total-return wealth scale** from `DlyRet`, including its CIZ delisting row exactly once.
4. Scale each session's raw O/H/L/C consistently to the wealth-series close for that session. Do not separately add dividends or `DelRet` after using `DlyRet`.
5. Adjust Volume only for split/share factors (`DlyCumFacShr`) according to one documented direction; never adjust volume for cash dividends.
6. Retain `DlyRetx`, distributions, `DlyCumFacPr`, and `DlyCumFacShr` for reconciliation, not a second adjustment pass.
7. Populate `DelistingReturn` from the explicit terminal event for audit, while ensuring terminal economics are already reflected exactly once in the adjusted Close/return stream.

Important execution caveat: a CIZ delisting row may represent a payment/value observation, not a tradable session. If there is no true regular-session OHLC, it must be a **non-executable valuation row**. Do not fabricate a fillable `Open`. The ingestion design must prove that the backtester can mark the holding through the terminal return without selling it at a nonexistent market open. If v1 cannot express that safely, the bundle must remain disabled until the later event-aware engine change; this task does not change the loader.

### Negative prices

Legacy CRSP formats used a negative price to denote a bid/ask average. Current CIZ stores the price as `DlyPrc` and the provenance in `DlyPrcFlg`. Do not apply legacy `abs(PRC)` transformations to a CIZ pipeline. Always retain and audit the flag.

### Formula and sequence for terminal economics

For a legacy or source representation with an ordinary final-period return `R` and a separate delisting return `DLRET`, the combined economic return is:

```text
R_combined = (1 + R) * (1 + DLRET) - 1
```

Cases:

- **Final trading return only, no separate terminal value:** use `R`; mark terminal economics unresolved if the security delisted and no authoritative resolution exists.
- **Separate delisting return only:** use `DLRET` from the last executable value to the post-delisting value.
- **Both:** compound with the formula above, never add them.
- **Current CIZ daily data:** compound the ordered `DlyRet` rows. The ordinary final-trading return appears first and the `DlyDelFlg=Y` delisting row carries the terminal return. Do not reapply `StkDelists.DelRet`.
- **Missing `DelRet`:** preserve `DelRetMissType`; do not coerce missing to 0 or -100%. Resolve from documented payment/next-price fields or quarantine. A preregistered imputation can be a sensitivity analysis, not hidden base-case data.
- **Cash acquisition:** value the holding at authoritative consideration on the economic/payment convention and preserve the cash timing.
- **Stock acquisition:** use `DelPERMNO`/distribution successor link and the authoritative exchange ratio where present; carry to the successor rather than inventing a sale.
- **Worthless/bankruptcy:** apply -100% only when the CRSP event/payment information establishes a zero value.
- **OTC continuation:** treat it as a listing transition, not automatic economic extinction.

Required reconciliation per security:

```text
product(1 + normalized_daily_return)
    == product(1 + CRSP DlyRet)
```

within a specified tolerance, with a separate assertion that each `DelRet` is included zero or one time as intended—never twice.

## 8. Practical extraction methods and limits

WRDS currently supports:

- Web query forms with downloadable output.
- SAS Studio, RStudio Server, and JupyterHub in a browser.
- WRDS Cloud via SSH, with SAS, Python, R, Stata, and MATLAB.
- Remote PostgreSQL from Python/R/Stata/MATLAB and ODBC/JDBC clients.
- SFTP/SCP for transferring Cloud-created result files.

See [WRDS delivery options](https://wrds-www.wharton.upenn.edu/pages/about/3-ways-use-wrds/) and [MFA/access methods](https://wrds-www.wharton.upenn.edu/pages/about/log-in-to-wrds-using-two-factor-authentication/). Named master's accounts have no permanent WRDS disk, although WRDS describes up to 500 GB of temporary Cloud workspace generally. Confirm what temporary workspace is available to the approved account.

No current public WRDS page reviewed gives a universal numeric row limit, web-query download cap, or per-query file-size maximum. Do not invent one. WRDS does document that Excel-oriented output has spreadsheet row limits and that plain text/CSV is more suitable for large extracts. For this project:

- Use the web form only for schema inspection and the small pilot.
- Use SQL/Python/SAS on WRDS Cloud for a larger extraction.
- Select only necessary fields and date/security predicates.
- Export by year (and optionally PERMNO bucket) directly into manageable files.
- Use incremental, resumable pulls with row counts and hashes rather than one monolithic all-market query.
- Never automate website login/query clicks; [WRDS Terms](https://wrds-www.wharton.upenn.edu/users/tou/) prohibit that. Automation on WRDS Cloud or authorized remote database connections is allowed.

CRSP's own CIZ cross-reference guide estimates the all-history complete `StkDlySecurityData` flat file at about 20 GB and the 12-column Primary file at about 7 GB. That confirms that a 20–30 year filtered extraction is practical, but it should be server-filtered and partitioned. CRSP's January 2026 annual release reports about 40,518 daily-file securities across the full history through 2025, not simultaneous eligible common stocks.

## 9. Extraction-size planning ranges

These are order-of-magnitude engineering estimates, not vendor limits or exact counts. CSV assumes selected fields, not all 32 CIZ daily columns.

| Tier | Ever securities (planning range) | Daily rows, 1996–2025 | Selected CSV/raw | Normalized Parquet | Complexity |
| --- | ---: | ---: | ---: | ---: | --- |
| A — Dow-ever | ~70–120 | ~0.4–0.9 million | ~0.1–0.3 GB | ~0.03–0.15 GB | Low data volume; high event/membership audit value |
| B — S&P-ever | ~1,000–1,500 | ~5–10 million | ~1–3 GB | ~0.3–1.5 GB | Moderate; membership and share-class joins dominate |
| C — U.S. common-stock-ever | ~15,000–25,000 | ~50–120 million | ~10–30 GB | ~4–15 GB | High; classification, gaps, and terminal exceptions require automation |

Working storage should be larger than final Parquet: roughly 0.5–2 GB for Tier A, 2–8 GB for Tier B, and 20–60 GB for Tier C when raw slices, action tables, audit reports, and republished normalized partitions coexist. The number of eligible common stocks is materially below CRSP's approximately 40,000 full-history securities because the latter spans 1925 onward and includes multiple security types.

## 10. Smallest useful pilot

Do not start with the Dow universe or the full market. Start with an **event-stratified PERMNO pilot**:

- 8–12 PERMNOs.
- Each selected PERMNO's full identity interval plus a narrow daily window, normally 18 months around the target event; add one quiet survivor with roughly five years for return/adjustment reconciliation.
- All relevant rows from security history, daily data, cumulative factors, distributions, and delists.
- No index-membership dependency in the first extraction.

Required cases:

| Case | What it proves |
| --- | --- |
| Ordinary surviving common stock | Baseline PERMNO mapping, daily OHLCV, shares/cap |
| Ticker/name change | Same PERMNO across historical identity intervals |
| Cash merger/acquisition | Delisting reason, consideration, payment date, terminal return |
| Stock merger/successor | `DelPERMNO`/distribution linkage and successor continuity |
| Bankruptcy/worthless delisting | Explicit loss and missing-code behavior |
| Reused ticker across issuers | Different PERMNOs never splice together |
| Split plus ordinary cash dividend | Factor direction and no split/dividend double count |
| Multiple share classes | PERMCO shared while PERMNO remains distinct |

Selection procedure after access is confirmed:

1. Query only event metadata to identify candidate PERMNOs; do not rely on remembered tickers.
2. Freeze the pilot PERMNOs and event windows before examining normalized performance.
3. Extract identity/history, daily primary/full fields, factors, distributions, and delists for those IDs.
4. Build a temporary non-runnable staging transformation outside `data/pit_us_all_stocks/`.
5. Run the identity, ticker-reuse, action, terminal-economics, and daily-master reconciliation audits from the PIT contract.
6. Require exact row counts and hashes, and manually inspect every pilot event.
7. Only after all cases reconcile should Tier A extraction be authorized.

Pass criteria:

- Every event date maps to one and only one PERMNO identity interval.
- Ticker reuse maps to different PERMNOs.
- Split-adjusted OHLC/volume and total returns reconcile to CRSP factors/`DlyRet`.
- Terminal economics reconcile exactly once.
- A missing delisting return remains explicitly unresolved.
- No delisting payment row is treated as an executable open.

## 11. Licensing and local storage

### General WRDS/CRSP rules that are public

- WRDS services and downloaded data are for academic, noncommercial research; commercial use is prohibited under the public academic terms.
- Users may download data to their computers through approved web, Cloud, or programmatic methods.
- Credentials cannot be shared, including with a collaborator.
- Data cannot be accessed outside subscribed products or by bypassing the front end.
- Public WRDS terms prohibit reproducing, distributing, creating derivative works from, or exploiting proprietary material except as the controlling subscription agreement permits.
- The institution may impose additional restrictions, and its subscription agreement controls in a conflict.
- Users must report affiliation changes; they cannot continue accessing WRDS services after leaving a subscribing institution.
- Publications/communications using WRDS must include WRDS's required acknowledgment.

These points come from the public [WRDS Terms of Use](https://wrds-www.wharton.upenn.edu/users/tou/) and [delivery documentation](https://wrds-www.wharton.upenn.edu/pages/about/what-wrds/).

### Not publicly resolved for Georgia Tech

The following need written confirmation from the local WRDS representative because neither the public WRDS terms nor a public Georgia Tech license answers them precisely:

| Use | Status pending Georgia Tech license |
| --- | --- |
| Export filtered research subsets | Generally supported by WRDS; confirm CRSP/GT scope |
| Save raw extracts locally during enrollment | Likely permitted for authorized academic research; confirm storage location/security |
| Store transformed Parquet locally | Unclear because it is derived from licensed source data |
| Store on OneDrive/cloud backup | Unclear; ask explicitly |
| Retain raw data after graduation/account termination | **Do not assume; likely restricted** |
| Retain transformed row-level data after access ends | **Do not assume; likely restricted** |
| Keep code, schemas, audit logs, aggregate results, and papers | Likely, but derived-data/result boundary must be confirmed |
| Independent academic research outside a course | Generally allowed for a named account; not necessarily for class access |
| Share raw or row-level derived data | Presume prohibited unless written license says otherwise |
| Publish raw extracts | Presume prohibited |
| Commercial use or live trading product use | Prohibited under academic WRDS terms; would require separate rights |

Repository policy if access is granted:

- Never commit CRSP raw or normalized row-level data to Git.
- Keep licensed data outside shared/public folders unless Georgia Tech approves the location.
- Commit only ingestion code, schemas, hashes, aggregate audit statistics, and non-reconstructive examples.
- Record the license/retention answer and a deletion date in the manifest/provenance record.
- Treat `PERMNO` itself and any identifier crosswalk according to the subscription agreement; do not publish a bulk crosswalk by default.

## 12. Complementary index-membership sources

### S&P 500

Preferred order:

1. Entitled CRSP `StkIndMembership` S&P family.
2. Another Georgia Tech-licensed S&P/Compustat/Capital IQ constituent-history product, if available in WRDS.
3. Official S&P constituent history under a separate institutional license.
4. Existing open ledger only as reconciliation evidence.

### Dow

CRSP should supply the security economics; use a separate Dow membership ledger. Candidate source categories, in preferred order:

1. Another Georgia Tech/WRDS licensed Dow Jones or S&P Global constituent-history table.
2. Georgia Tech's Bloomberg workstation for targeted historical membership research and cross-checking, subject to Bloomberg export/licensing limits. The official [Georgia Tech Bloomberg page](https://library.gatech.edu/bloomberg) says it is available to current students but only in the Library building.
3. Official S&P Dow Jones Indices history/license.
4. A narrow commercial point-in-time membership product only after institutional sources are exhausted.

Do not assume the Bloomberg workstation permits a bulk local membership export. Ask its librarian and retain source/license provenance. Do not buy Norgate while these institutional routes remain unresolved.

## 13. Decision tree

```text
Georgia Tech WRDS registration
  |
  +-- approved named account
  |     |
  |     +-- CRSP U.S. Stock daily CIZ + delists visible
  |     |     |
  |     |     +-- local export/retention permitted in writing
  |     |     |     -> run 8–12 PERMNO event pilot
  |     |     |     -> validate identity/actions/delisting exactly once
  |     |     |     -> source Dow membership
  |     |     |     -> build Tier A first
  |     |     |     -> decide separately on Tier B/C expansion
  |     |     |
  |     |     +-- storage/use terms not permitted or unclear
  |     |           -> stop extraction; resolve with GT WRDS representative
  |     |
  |     +-- WRDS visible but CRSP absent
  |           -> ask whether GT licenses Compustat/Capital IQ/Bloomberg or another
  |              source with permanent identity + explicit terminal economics
  |           -> if not, return to narrow commercial Tier A/B evaluation
  |
  +-- rejected because OMSA/full-time/school eligibility
  |     -> ask local WRDS representative for the exact rule
  |     -> request named-account exception only if policy permits
  |     -> if appropriate, seek a genuine faculty-sponsored RA account
  |     -> do not repurpose a class account for independent research
  |
  +-- Georgia Tech institutional access fails
        -> document denial/absence
        -> resume narrow commercial Tier A/B evaluation
        -> do not buy Norgate until this path is exhausted
```

## 14. Recommended next action

Submit the normal Georgia Tech WRDS master's-account request now, without attempting any data query. If the form/representative rejects the account because OMSA is online, non-Scheller, or not classified full-time, use the exact message above through the Georgia Tech Library eResource form and copy Scheller IT for routing.

Once approved, perform only the product-visibility checklist and obtain the local storage/retention answer. If CRSP daily CIZ, history, distributions, factors, and delisting data are all visible and the use is permitted, freeze an 8–12 PERMNO event pilot. Tier A comes only after that pilot passes; Tier B/C remain separate scope decisions.

No purchase, download, ingestion, loader change, or universe activation is authorized by this plan.

## Sources

Primary/current sources used for decisions:

- [WRDS registration form / current subscriber selector](https://wrds-www.wharton.upenn.edu/register/du/)
- [WRDS account types](https://wrds-www.wharton.upenn.edu/pages/about/wrds-account-types/)
- [WRDS Terms of Use](https://wrds-www.wharton.upenn.edu/users/tou/)
- [WRDS access/delivery methods](https://wrds-www.wharton.upenn.edu/pages/about/3-ways-use-wrds/)
- [WRDS MFA and remote programmatic access](https://wrds-www.wharton.upenn.edu/pages/about/log-in-to-wrds-using-two-factor-authentication/)
- [WRDS changes to CRSP/CIZ](https://wrds-www.wharton.upenn.edu/pages/data-announcements/changes-to-crsp-data/)
- [WRDS current CIZ event-study example](https://wrds-www.wharton.upenn.edu/pages/wrds-research/macros/run-an-event-study-ciz-format-macro/)
- [CRSP CIZ 2.0 file guide](https://www.crsp.org/wp-content/uploads/guides/CRSP_US_Stock_%26_Indexes_Database_Guide_Flat_File_Format_2.0.pdf)
- [CRSP SIZ-to-CIZ cross-reference](https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-siz-to-ciz-cross-reference-guide/)
- [CRSP calculations/index methodology guide](https://www.crsp.org/crsp_pdf/crsp-us-stock-indexes-databases-calculations-index-methodologies-guide-flat-file-format-2-0/)
- [CRSP January 2026 annual release notes](https://www.crsp.org/crsp_pdf/crsp-us-stock-database-release-notes-2025-12-annual/)
- [CRSP PERMNO/PERMCO](https://www.crsp.org/research/permno/)
- [Georgia Tech Library eResource issue form](https://www.library.gatech.edu/eresource-issue)
- [Georgia Tech Library help](https://www.library.gatech.edu/help)
- [Scheller IT Services](https://www.scheller.gatech.edu/directory/offices/itservices/index.html)
- [Georgia Tech Bloomberg workstation](https://library.gatech.edu/bloomberg)
