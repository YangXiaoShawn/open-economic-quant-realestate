# Decision Log

Append-only. Each entry: date, decision, alternatives considered, rationale, and
what would make us revisit it. Autonomous decisions taken without human input are
marked **[AUTO]**.

---

### 2026-08-10 · D001 · Repository initialised from empty directory **[AUTO]**
`/Users/shawn/Documents/RealEstate` was empty and not a git repository. Initialised
a professional research repository with `git init`. No pre-existing user work to
preserve.

### 2026-08-10 · D002 · Python 3.12 via `uv` **[AUTO]**
System Python is 3.9.6; no Homebrew, pyenv, or conda. `uv` 0.12.3 was present as a
pip-installed package at `~/Library/Python/3.9/bin/uv`, and a CPython 3.12.13
Apple-Silicon build was already in its toolchain cache. The `Makefile` resolves
`uv` from `PATH` and falls back to that absolute path.
*Revisit if:* the user installs Homebrew Python or wants 3.13.

### 2026-08-10 · D003 · Freddie Mac schema taken from the public official layout **[AUTO]**
`file_layout.xlsx` and `user_guide.pdf` are served **without** registration at
`freddiemac.com/fmac-resources/research/pdf/`. Both were downloaded and parsed. The
schema in `src/lockin/schemas/freddie.py` and
`data/reference/freddie_llds_layout.yaml` reflects the *verified* current layout:
32 origination fields, 32 monthly performance fields.
Two documented discrepancies recorded in the spec:
- Performance field 12 is "Current Deferred UPB" in `file_layout.xlsx` but
  "Current Non-Interest Bearing UPB" in `user_guide.pdf`. We keep the guide's
  semantic name with the layout's position.
- Performance field 17 is "Expenses" in the layout, "Total Expenses" in older
  guides. Position is what matters for parsing.
*Revisit if:* Freddie Mac publishes a new release; `lockin verify-schema` re-checks.

### 2026-08-10 · D004 · Zero Balance Code → event mapping **[AUTO]**
From the user guide's official termination-event priority table
(1 = highest priority): 15 Whole Loan Sale, 16 RPL Securitization,
09 REO Disposition, 96 Defect prior to Property Disposition, 03 Short Sale or
Charge Off, 02 Third Party Sale, 01 Prepaid or Matured (Voluntary Payoff).

Mapping chosen:
- `01` → **`prepayment`** (the guide's own label conflates voluntary payoff and
  maturity, and does not distinguish refinance from sale-related payoff).
- `02`, `03`, `09` → **`credit_event`**.
- `15`, `16`, `96` → **`admin_removal`, treated as right censoring.**

Rationale for censoring 15/16/96: these are Freddie Mac portfolio and
representation-and-warranty actions, not borrower decisions. Counting them as
prepayment would inflate the prepayment hazard; counting them as "still alive"
would be false. Censoring is the least-wrong option and its informativeness is a
stated limitation, tested by a robustness cell that instead counts them as
prepayment.

Alternative considered and rejected: assigning `02` (Third Party Sale) to a
"sale" event class. Rejected — a third-party sale at foreclosure auction is a
credit outcome, not a voluntary household move, and conflating the two is exactly
the error `AGENTS.md` §1 forbids.

*Revisit if:* Freddie Mac adds a code that distinguishes refinance payoff, or if
a linkage to the Relief-Refinance `Pre-HARP Loan Sequence Number` field is used to
identify *some* refinances (it identifies only Relief Refinance / HARP chains, not
ordinary refis — see D005).

### 2026-08-10 · D005 · No refinance-vs-sale split at the loan level **[AUTO]**
Considered using field 27 (`Pre-Relief-Refinance Loan Sequence Number`) to link a
payoff to a subsequent Freddie-acquired refinance. Rejected as a general solution:
that field is populated only for Relief Refinance / HARP loans, so it identifies a
policy-program subset, not ordinary refinancing. Building a general "refi chain"
would require matching on property, which the dataset does not permit (postal code
is truncated to 3 digits + "00", and there is no property identifier).
**Consequence:** the loan-level outcome is `prepayment`, full stop. Refinance
behaviour is characterised via the refinance *incentive* measure and via HMDA
refinance origination counts at the market level. This is written up in
`reports/loan_hazard_analysis.md` §"Refinancing versus mobility".

### 2026-08-10 · D006 · PMMS URL and methodology regimes **[AUTO]**
`freddiemac.com/pmms/docs/historicalweeklydata.csv` now 404s;
`freddiemac.com/pmms/docs/PMMS_history.csv` serves the weekly history
(1971-04-02 → present; 2,889 observations at fetch time). The adapter holds a
candidate URL list and fails loudly listing every URL tried.
Methodology regimes recorded: `lender_survey` through 2022-11-10 and
`application_based` from 2022-11-17, when Freddie Mac changed the PMMS methodology
and discontinued the fees/points and 5/1 ARM series. Confirmed empirically in the
fetched file: `pmms30p` and all `pmms51*` columns are blank from that date.

### 2026-08-10 · D007 · Default geography is **state**, not MSA or county **[AUTO]**
State is the only level where all four sources overlap cleanly with no crosswalk
vintage problem: Freddie `Property State`, FHFA HPI `level == "State"`, HMDA
`states=` filter, Census BPS state files. MSA is implemented and is the target for
the registered-full run (Freddie field 5 is an MSA/Metropolitan Division code, and
the guide warns that it is **not** updated for changing MSA definitions — a
versioned crosswalk is required). County is deferred.
*Revisit when:* a versioned OMB delineation crosswalk is added.

### 2026-08-10 · D008 · Pre-shock date 2021-12; shock window 2022-01 → 2023-12 **[AUTO]**
PMMS 30-yr went from ~3.1% (2021-12) to ~7.1% (2023-10) — the largest two-year
increase in the series' history. 2021-12 is late enough to capture the full
2020–21 refinance wave in the coupon distribution and early enough to precede the
increase. Alternatives 2021-06 and 2022-03 are robustness cells.

### 2026-08-10 · D009 · HPI concept: purchase-only, **quarterly**, state **[AUTO]**
`hpi_flavor == "purchase-only"`, `level == "State"`. Purchase-only excludes
refinance appraisals and is the standard choice for transaction-price research.
All-transactions and expanded-data are available as robustness cells and are
**never** mixed into the same series.

**Frequency corrected after inspecting the fetched master file.** FHFA publishes
purchase-only at **monthly** frequency only for the nation and census divisions; at
**State** and **MSA** level purchase-only is **quarterly**. The original config
default (monthly + State) silently matched zero rows. `load_series` now raises with
the full list of published `(flavor, frequency, level)` combinations rather than
returning an empty frame, and `PUBLISHED_COMBINATIONS` documents them.

Growth rates are computed at the index's **published** frequency. Where a monthly
index value is needed as an *input* (the estimated-current-LTV scaling), the
quarterly index is expanded by holding the level constant within the quarter, and
`index_basis` is suffixed `+held-constant-within-quarter` so the interpolation
travels with the data. An expanded series is **never** used as a regression outcome.

### 2026-08-10 · D010 · Estimated current LTV from origination LTV × HPI path **[AUTO]**
The performance file has an `Estimated Loan-to-Value (ELTV)` field (position 26),
but the guide notes it is populated only for a subset of periods/loans. We compute
our own: `cur_ltv = (cur_upb / orig_upb) * orig_ltv * (hpi_orig / hpi_t)`, using
the state purchase-only HPI. We prefer the reported ELTV where present and record
which source was used per row in `ltv_source`.
*Limitation:* a state index is a poor proxy for an individual property. Recorded.

### 2026-08-10 · D011 · Synthetic fixtures, not scraped samples **[AUTO]**
The Freddie Mac *sample* files also sit behind the registration wall, so they
cannot be fetched here. Rather than stall, `lockin.fixtures` generates a synthetic
cohort that is **schema-exact** (same 32+32 pipe-delimited fields, same sentinel
codes, same ZB semantics) so that every parser, join, event rule, and estimator is
genuinely exercised. Generated with a recorded seed. Every downstream artifact and
report is stamped `SYNTHETIC` and the report renderer refuses to omit the banner.
**No synthetic number is ever described as an empirical finding.**

### 2026-08-10 · D012 · Hazard estimation on an episode table with case-cohort sampling **[AUTO]**
For the full-data run the loan-month episode table would be billions of rows on a
16 GB machine. Design: keep **all** exit months and a configurable random sample
of non-exit months, with an offset/weight recorded in the artifact
(`sampling_design`). For the synthetic slice the full episode table is small
enough that `loan_sample_fraction = 1.0`.

### 2026-08-10 · D013 · No IV language **[AUTO]**
Predetermined exposure is not an instrument. The exclusion restriction — that the
2021 coupon distribution affects 2023 purchase originations *only* through
lock-in — is not defensible without ruling out that the same 2020–21 refinance
wave proxies a local demand boom. Recorded in
`docs/IDENTIFICATION_STRATEGY.md` §A4. The design is a conditional
difference-in-differences with continuous predetermined treatment.
*Revisit if:* a genuinely excludable shifter of the coupon distribution is found.

### 2026-08-10 · D014 · Refinance originations are a contaminated outcome **[AUTO]**
A market where everyone refinanced in 2020–21 has both extreme lock-in exposure
and an exhausted refi pipeline. Post-2022 refi counts therefore fall mechanically
in high-exposure markets, independent of any lock-in mechanism. Refi outcomes are
reported but labeled mechanically contaminated; the headline outcome is **purchase**
originations.

### 2026-08-10 · D015 · HMDA annual data are not interpolated for estimation **[AUTO]**
HMDA is annual. The local panel is built at monthly frequency for HPI and permits
and at annual frequency for HMDA. Event studies on HMDA outcomes run at **annual**
frequency with year fixed effects; monthly interpolation is used only for
dashboard display and is flagged in the chart annotation.

### 2026-08-10 · D016 · Exposure is the frozen coupon distribution × the LATER rate path **[AUTO]**
**A design error caught by a diagnostic.** The first implementation measured
exposure as the *contemporaneous* locked-in share at the pre-shock date (2021-12).
The exposure distribution collapsed to exactly zero in every state — correctly,
because in December 2021 the market rate was near its historic low and essentially
**nobody was locked in yet**. Lock-in is created by the *subsequent* rate increase
acting on the coupon distribution that already existed.

Corrected measure (`lockin.stock.predetermined_exposure`):

$$E_g = \sum_k \omega_{gk}^{\text{pre}} \cdot \mathbf 1\{\bar R^{\text{post}} - r_k > \tau\}$$

with $\omega_{gk}^{\text{pre}}$ the 2021-12 coupon shares and $\bar R^{\text{post}}$
the mean point-in-time PMMS rate over 2022-01…2024-12. All cross-sectional variation
comes from the frozen local shares; the rate level is a national scalar. Also emitted:
`coupon_share_below_{τ}` (a pure coupon-share measure with no rate assumption at all)
and `coupon_share_hhi` (a shift-share concentration diagnostic).

The resulting exposure has real variation (sd ≈ 0.03 on a mean of 0.85) and the
balance table shows it correlates −0.73 with the pre-shock note rate (mechanical)
and −0.38 with 2019–21 price growth (**a genuine confound**, exactly the threat in
`IDENTIFICATION_STRATEGY.md` §3.1).

*Lesson recorded:* a treatment variable with zero variance is a design failure, not
an estimation failure. The exposure-distribution artifact is computed and inspected
before any event study is interpreted.

### 2026-08-10 · D017 · HMDA API silently drops unrecognised filters **[AUTO]**
The CFPB Data Browser aggregations API takes **`loan_purposes`** (plural). Passing the
singular `loan_purpose` does **not** error — the API ignores it and returns the
**all-purpose** total. Our first fetch therefore produced identical "purchase" and
"refinance" counts (both were all-purpose totals) which would have become a
fabricated empirical finding.

Fixes: (a) use `loan_purposes`; (b) `_assert_filters_applied()` verifies the API
echoed every filter back in its `parameters` block and refuses to cache a response
that did not, on both fetch **and** cache read; (c) cache filenames carry a `v2`
prefix so the bad v1 cells can never be reused. Verified after the fix: AZ refinance
originations peak at 315k (2020) and fall to 27k (2023) while purchase originations
go 132k → 91k — the real refinance boom and bust.

*Lesson recorded:* when an API accepts a filter without echoing it, assume it was
ignored. Assert the echo.

### 2026-08-10 · D018 · Start-of-month balance for all lock-in measures **[AUTO]**
`Current Actual UPB` is the **end**-of-period balance and is 0 in a zero-balance
month. Using it set the payment gap to zero in exactly the months where an exit
occurs, corrupting the covariate for every event. Lock-in measures now use the
**start-of-month** balance: the prior month's reported UPB, falling back to the
scheduled amortised balance for a loan's first observed month. `upb_timing_source`
records which was used per row. This is also the economically correct timing — a
within-month decision is made with the balance you owe going in.

### 2026-08-10 · D019 · Annual panel spans OUTCOME years, not stock years **[AUTO]**
The loan performance window starts 2021-01, but HMDA, FHFA HPI, and Census BPS all
reach back to 2018. Building the annual panel only over stock years left **no
pre-shock periods**, making pre-trends untestable and forcing every result to be
demoted to descriptive. The panel now spans the union of outcome years with the
geography-level frozen exposure attached to pre-shock years too; stock aggregates
are null there and `has_stock_data` flags which rows have them. Partial permit
years are dropped rather than compared against full-year totals.
