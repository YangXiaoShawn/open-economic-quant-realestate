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

### 2026-08-11 · D020 · The HMDA filter bug changed the headline result **[AUTO]**
Recorded separately from D017 because the *consequence* matters independently of the
cause. Before the `loan_purposes` fix, the pre/post DiD on "log purchase originations"
was **−0.026** (s.e. 0.038). After the fix — with genuine purchase-only counts rather
than all-purpose totals compared against themselves — it is **+0.001** (s.e. 0.019,
t = 0.05).

So the corrected market-level finding is a **null**, not a negative-but-noisy estimate.
Had the bug survived, this project would have reported a negative coefficient that was
an artifact of a silently dropped query parameter.

Three consequences applied:
1. `reports/` are generated, so they picked up the corrected numbers automatically.
   The hand-written `portfolio/` documents did **not**, and were corrected by hand.
2. The null is reported as a null. With 26 state clusters, annual HMDA data, and time
   fixed effects absorbing the common national shock, this design has limited power to
   detect a cross-state differential — so a null is not a refutation of lock-in, but it
   is also not suggestive evidence for it.
3. **A placebo outcome fails.** The purchase denial rate moves with t = −1.90. A
   significant placebo counts *against* the design, and the robustness grid flags it as
   `placebo_FAIL`. It is surfaced in `reports/failed_hypotheses.md` rather than buried.

Also recorded: `es_hpi_growth` (pre-trend p = 0.003) and `es_log_permits_1unit`
(p = 0.007) **fail** their pre-trend tests and are auto-demoted to `descriptive` by the
tier logic, with no manual override available.

### 2026-08-11 · D021 · Empty loan-age bins broke the cluster-robust covariance **[AUTO]**
Three loan-age dummies (`age_60_84`, `age_84_120`, `age_120_360`) had no observations
in the estimation window, leaving the design matrix rank 14 of 17 columns. A plain GLM
fit tolerates that via a pseudo-inverse, but the cluster-robust sandwich inverts X'X
exactly and failed with a singular matrix — silently downgrading the standard errors to
conventional ones, which are badly understated when loan-months are serially correlated
within a loan. `design_matrix` now drops zero-variance columns and records them in
`dropped_design_columns`. Standard errors are now clustered by loan across 10,320
clusters.

Related: `lifelines` cannot compute Schoenfeld residuals for a left-truncated fit, so
the proportional-hazards diagnostic runs on a **separate** Cox fit without entry times,
labeled as such in the artifact. That diagnostic reports a large PH violation for
`rate_gap_at_entry` (p < 0.0001) — expected, since the entry gap becomes a worse proxy
for the current gap as the loan ages, and precisely why the discrete-time rungs with a
time-varying gap are the preferred specifications.

### 2026-08-11 · D022 · Generated fixtures are not committed **[AUTO]**
The synthetic performance files are ~32 MB and are deterministic from
`mortgage.synthetic_seed`, so they are regenerable with `make prepare-sample-data` and
are gitignored. Their **manifest is committed**, so the exact fixture set behind any
result stays auditable. A governance test enforces a 5 MB ceiling on tracked files.

### 2026-08-11 · D023 · mypy: strict core, relaxed analysis modules **[AUTO]**
The seven core-interface modules (`amortization`, `lockin_measures`, `config`,
`provenance`, `manifest`, `schemas.*`, `adapters.base`) typecheck strictly and cleanly.
The analysis modules disable `arg-type`, `operator`, `str-bytes-safe`, `assignment`, and
`index`, because Polars declares accessors such as `Series.max()` and `DataFrame.item()`
as wide unions whose runtime type depends on a column dtype mypy cannot see. Every
flagged site was a `float()`/`int()` on a column we constructed as numeric. Narrowing
each with a cast would add dozens of casts asserting what the schema already guarantees
and would hide genuine errors in the noise. Two *genuine* typing defects were found and
fixed rather than suppressed: a dict typed `dict[str, dict]` that also held a string,
and `write_loan_events` returning `tuple[object, ...]` which erased structure at every
call site.

### 2026-08-11 · D024 · Closed three documentation-vs-implementation gaps **[AUTO]**
An audit against the original specification found three places where a document
promised something the code did not do. All are now real:

1. **`data/reference/freddie_llds_layout.yaml` did not exist**, despite being cited by
   D003 and by `LICENSE_AND_REDISTRIBUTION.md` §1. It is now **generated from**
   `lockin.schemas.freddie` by `make emit-layout`, and
   `tests/test_ingest.py::TestLayoutYaml` fails if the two ever disagree. Committing it
   makes the field map reviewable without reading Python; generating it means the two
   cannot drift.
2. **`make reproduce-sample` omitted `robustness`**, so a fresh clone produced an empty
   robustness section in `reports/failed_hypotheses.md`. Added.
3. **Three comments claimed "tested by a robustness cell" for checks that did not
   exist** (`events.py` on ZB 15/16/96, `lockin_measures.py` on the fresh-term payment
   gap, `rates.py` on month-end alignment). `lockin.survival.sensitivity` now runs all
   three and each comment points at the resulting artifact.

The sensitivity results are substantive rather than reassuring:

| cell | coefficient | vs baseline (−0.5248) |
|---|---|---|
| admin removals counted as prepayment | −0.4353 | `large_shift` |
| fresh-term payment gap | −1.4727 vs −3.2629 remaining-term | `large_shift` |
| month-end rate alignment | −0.4896 | `moderate_shift` |

So the censoring choice, the payment-gap term convention, and the within-month rate
timing each move the coefficient by more than one standard error. These are
**fragilities that must be stated whenever a magnitude is quoted**, and
`reports/failed_hypotheses.md` §3 now does so.

*Lesson recorded:* a comment claiming a robustness check is a promise. Prefer citing an
artifact path over describing a check in prose.

### 2026-08-11 · D025 · BLS LAUS unemployment adapter, via the API not the bulk files **[AUTO]**
Section F of the specification asked for optional local economic control adapters; none
existed, which left the local-labour-shock threat (`IDENTIFICATION_STRATEGY` §3.4)
merely documented rather than addressed.

`lockin.adapters.bls_laus` now supplies the seasonally adjusted state unemployment rate
(series `LASST{fips}0000000000003`), 3,672 state-months over 2018-01…2023-12.

**Route chosen and why.** The BLS bulk flat files at `download.bls.gov` return HTTP 403
to a generic client; BLS asks automated downloaders to identify themselves with a
contact email. **We do not put the user's personal email address into an outbound
header without being asked**, so that route is not used. The public JSON API v2 serves
the same series without a registration key within published unregistered limits (~25
queries/day, 25 series and 20 years per query); three queries cover all 51 states.

Wiring: the panel joins it when present and **records its absence when not**; the event
study adds `unemployment_rate` to the control set only if the column exists; nothing
raises. Every event-study artifact now carries an `identification_threats` map stating,
per threat, whether this run controlled for it — so a reader can see which threats were
addressed and which were merely noted, without opening the strategy document.

Effect on the headline: none. The purchase-originations DiD moves from +0.0010 to
+0.0007 (t = 0.03). The null is not an artifact of omitting labour-market conditions.

### 2026-08-11 · D026 · Robustness grid extended from 34 to 42 cells **[AUTO]**
Section K listed ~18 robustness axes; the grid covered 11. Added four axes in which the
treatment is **genuinely rebuilt** rather than relabelled:

- **Alternative pre-shock dates** (2021-06, 2022-03): exposure re-frozen from scratch.
- **Alternative market-rate series** (`pmms15`): exposure re-evaluated at the 15-year
  post-shock national level, which changes the gap implied by the same coupon
  distribution.
- **Alternative HPI concepts** (all-transactions, expanded-data): the 2019–21
  pandemic-boom control is rebuilt from each concept separately. Concepts are still
  never mixed *within* a series.
- **Loan sub-samples** (purchase originations only, primary residence only, excluding
  manufactured housing): the frozen coupon shares are recomputed on the restricted loan
  population.

All eight new cells are **insignificant**, consistent with the baseline null. The null
is therefore robust to the pre-shock date, the rate series, the price-index concept, and
the loan population — which is worth more than the same finding from a single
specification.

Still not covered, and recorded as such: alternative **geography** (MSA needs a
versioned OMB crosswalk) and **stable-servicer samples** (servicer names below 1% of
quarterly UPB are collapsed to "Other", so the sample is not constructible as intended).

### 2026-08-13 · D027 · Teleworkable share added, and it exposed two specification bugs **[AUTO]**
`lockin.adapters.teleworkable` fetches the Dingel & Neiman (2020) teleworkable
employment share (public replication outputs, no registration) at state and CBSA level.
This closes the last threat that was recorded as `UNCONTROLLED` on every event-study
artifact: remote-work reallocation of housing demand.

**Which of the four published measures.** The default is `teleworkable_emp`, not
`teleworkable_manual_emp`. The `manual` pair is the authors' own subjective
classification (`Teleworkable_BNJDopinion.csv` in their package); the unprefixed pair
applies a fixed rule to O*NET survey responses. A control a sceptical reader can rebuild
beats one that encodes expert judgement. The first draft of this adapter had it
backwards and asserted in a docstring that `manual` was the paper's headline; the claim
was checked against the authors' README and corrected. All four measures are retained
so the choice is a robustness axis rather than an assertion.

**Bug 1 — a control that was never doing anything.** The measure is a single
cross-section, so as a level control it is exactly collinear with the geography fixed
effects. Nothing raises: the pseudo-inverse simply splits the coefficient arbitrarily.
Adding the diagnostic for this revealed that **`pre_hpi_growth_2019_2021` had the same
problem and had been a level control since D006** — so the "pandemic demand
reallocation: CONTROLLED" claim on every prior artifact was hollow. Both now enter as
**trend controls**, interacted with every non-reference period, and
`_demote_degenerate_controls` records any such move under `degenerate_controls`.

**Bug 2 — the two halves of an artifact disagreed.** The demotion was first added only
to `event_study`, so `did_two_period` in the *same artifact* kept the collinear level
control. The headline DiD and its own dynamic path were estimating different
specifications. The demotion is now shared by both and a test asserts they agree.

**Effect on the headline.** Decomposed rather than reported as one jump:

| specification | DiD | s.e. | t |
|---|---|---|---|
| as previously published (pre-HPI as level) | +0.0007 | 0.0197 | +0.03 |
| pre-HPI moved to trend (bug fix alone) | −0.0100 | 0.0230 | −0.43 |
| + teleworkable trend (this decision) | **+0.0237** | 0.0281 | +0.84 |
| teleworkable trend only | +0.0347 | 0.0258 | +1.34 |
| no controls at all | +0.0010 | 0.0190 | +0.05 |

**The null survives all five.** No specification reaches |t| > 1.4. But the point
estimate moves across roughly two standard errors, which is the honest characterisation
of a 26-cluster design: the sign is not pinned down, and the null is a statement about
power as much as about lock-in.

Two other outcomes moved. The **denial-rate placebo now passes** (t = −1.00, was
−1.90) — the previously reported placebo failure was an artifact of the mis-specified
control, and `reports/failed_hypotheses.md` is updated to say so. `log_permits_1unit`
still fails its pre-trend (p = 0.066) and stays `descriptive`.

**A guard against the fix creating a new problem.** With `pre_hpi_growth_2019_2021` as a
trend control, the `hpi_growth` pre-trend test jumped from p = 0.003 (fail) to p = 0.848
(pass) — because the control is a lagged dependent variable interacted with time and
absorbs the very pre-trend the test looks for. A test that cannot fail is not evidence.
`_circular_trend_controls` detects a trend control drawn from the same published series
as the outcome and **blocks promotion to `quasi_experimental`** regardless of the
p-value. `hpi_growth` is therefore `descriptive` in this run, for a stated reason.

### 2026-08-13 · D028 · Versioned OMB CBSA crosswalk, and how unstable the codes are **[AUTO]**
`lockin.adapters.omb_cbsa` downloads six published delineation vintages (2013, 2015,
2017, 2018, 2020, 2023) and builds two tables: a long CBSA/Metropolitan-Division ×
county × vintage crosswalk, and a per-code stability verdict.

Freddie Mac documents that its MSA field is **not** restated for redelineation, so a
loan carries the code in force at origination. Grouping by that field across cohorts
pools different geographies wherever OMB moved a boundary. The crosswalk cannot fix
this — the county is not in the loan file — but it makes it auditable.

**How bad it is, measured rather than assumed.** Of 1,054 codes:

| verdict | codes |
|---|---|
| stable across all six vintages | 609 |
| composition changed | 187 |
| absent in some vintage | 188 |
| renamed only (county set identical) | 59 |
| metro ↔ micro reclassification | 11 |

**Roughly 42% of codes do not mean the same place throughout.** Restricting to
metropolitan CBSAs that are composition-stable leaves **215 candidate panel units** —
still an eight-fold gain in clusters over the 26 states, and the reason this was the
highest-value power fix.

Three details that a naive crosswalk gets wrong, each pinned by a test:

- **Composition is compared on the county *set*, not the count.** The 2023 Atlanta CBSA
  swapped Lamar County out for Lumpkin County at a constant 29 counties. A count
  comparison calls that stable; it is not.
- **A rename is not a redefinition.** OMB adds and drops principal cities from titles
  without moving a boundary, so `renamed_only` counts as usable. The first draft
  excluded it, contradicting its own docstring.
- **Metropolitan Divisions share the field with CBSAs.** Freddie's field 5 is "MSA *or
  Metropolitan Division*", both five digits. They are loaded as separate `code_kind`
  entries; folding divisions into their parent would silently rescope eleven large
  metros.

`absent_in_some_vintage` is excluded from the default panel but recorded, because
whether it matters depends on the cohort span — a code missing only from the 2013
delineation is harmless for a 2015+ sample.

Dependencies: Census publishes 2023 as `.xlsx` and every earlier vintage as legacy
`.xls`, so `openpyxl` and `xlrd>=2` are both required. The header row is *found* by
searching for `CBSA Code` rather than hard-coded, because the number of banner rows is
not constant across vintages.

### 2026-08-13 · D029 · Optional sources are now actually fetched **[AUTO]**
`bls_laus` was only ever `try_load`ed — no command fetched it — while
`data/DATA_ACCESS.md` §R2 stated it was fetched by `make fetch-public-data`. A fresh
clone would therefore have run with the local-labour-shock threat `UNCONTROLLED` while
the documentation said otherwise, and nothing would have complained. `fetch-public-data`
now fetches all three optional sources, reports them in a separate `optional` tier, and
prints an explicit warning naming the threat left uncontrolled when one fails.

### 2026-08-13 · D030 · MSA-level geography: guarded, not yet runnable **[AUTO]**
With the crosswalk (D028) in place, the fixtures now carry **real** composition-stable
metropolitan CBSA codes drawn from the 2023 delineation, with ~15% left null to keep the
non-metro path exercised. Fixtures must not invent five-digit codes: a made-up code
would either fail to resolve (making the crosswalk look broken) or collide with a real
metro (making a synthetic result look attributable to a real place).

**The first MSA run did not fail — and that was the problem.** It produced a
plausible-looking 182-row panel in which exposure was frozen for 102 MSAs, outcomes came
from 26 states, and the teleworkable control matched 0 of 182 rows. Nothing raised,
because a left join on disjoint keys is a legal operation. Three defects were behind it:

1. The FHFA HPI loader was hard-coded to `level="State"` regardless of the config.
2. `active_stock.parquet` had a single path for both geographies, so a state run and an
   MSA run silently overwrote each other. It is now `active_stock_{geography}.parquet`.
3. Nothing checked that a source's keys and the panel's keys were the same *kind* of
   thing.

`_assert_geography_compatible` now runs before every join and raises
`GeographyMismatchError` on disjoint keys. It deliberately does **not** subclass
`ValueError`, because the per-source handlers catch `ValueError` and downgrade it to
"source unavailable" — correct for a missing file, wrong for a mis-specified run.
Optional sources (LAUS, teleworkable) are dropped with a note naming the threat left
uncontrolled rather than raising.

Result: `configs/msa.yaml` now stops with an actionable message naming the offending
source and both key shapes. Remaining work is three adapters (HMDA `msamds`, Census BPS
MSA files, BLS LAUS `LAUMT` series) plus a rule for the **47 metro CBSAs that span more
than one state**. Recorded in `STATUS.md` §8.

### 2026-08-13 · D031 · The shipped files do not match the published layout **[AUTO]**
The registered download (`full_set_standard_historical_data.zip`, 40 GB, 110 quarterly
cohorts 1999Q1–2026Q1) carries **31 origination and 35 performance fields**. Both
official documents — `file_layout.xlsx` (Last-Modified 2024-04-08) and `user_guide.pdf`
— describe **32 and 32**. The documentation is behind the data.

Guessing a mapping from field names is exactly what `AGENTS.md` §3 forbids, so the
layout was **established against the data** and every inference labelled by its support:

- **Anchored.** Origination 1–24 and performance 1–32 confirmed by a cross-file join on
  2021Q4: origination Original Interest Rate equals performance Current Interest Rate at
  loan age 0 for **1,218 of 1,218** records, zero mismatches.
- **Inferred (strong).** `Servicer Name` moved origination 25 → performance 34 (observed
  values are servicer names). `MI Cancellation Indicator` moved origination 32 →
  performance 33 (observed domain `{7, N, Y}` is the documented domain exactly).
- **Undocumented.** Origination 31 (constant `9999`) and performance 35 (blank or
  `0.00`) appear in no official document. Parsed as `undocumented_position_N`, never
  interpreted, never renamed.

The arithmetic closes on both files, which is what makes the account credible rather
than merely possible: 32 − 2 + 1 = 31 and 32 + 2 + 1 = 35.

**No research variable depends on an inferred or undocumented field** — all of them sit
in the anchored range, and a test asserts that no research variable changes position
between variants. `lockin.schemas.variants` selects the variant from the observed field
count (modal over a sample, not `lines[0]`, so one truncated row cannot mis-select), and
an unknown count is still a hard blocker.

### 2026-08-13 · D032 · Three scaling defects the first real run exposed **[AUTO]**
The 522-million-loan-month ingest surfaced problems that no synthetic run could.

**1. Nested archives.** The full set is a zip of zips of zips
(`full_set.zip → historical_data_YYYY.zip → historical_data_YYYYQn.zip → orig_/perf_*.txt`),
and it uses the `orig_`/`perf_` naming rather than the documented
`historical_data_`/`historical_data_time_`. `discover()` now recurses (depth-capped) and
opens intermediate members **in place** as file objects. Nothing is extracted: the
archive is 40 GB and the machine had 33 GB free. Reading is a seek, not a decompression,
because the inner zips are stored uncompressed. Discovery of all 220 members takes 0.09 s.

**2. A global sort that nearly filled the disk.** `build_loan_events` sorted all 522 M
rows by `loan_seq_no`. It spilled ~20 GB of Polars scratch and took the disk from 26 GB
free to **5.8 GB** before being stopped. The collapse is per-loan, loan sequence numbers
encode their own cohort, and the interim tables are already hive-partitioned by cohort —
so it is now done **one cohort at a time** and concatenated. Identical result, bounded
scratch, and the step went from "spilling 20 GB after 9 minutes" to **61 seconds**.

**3. `data/interim` is shared across run profiles.** Running a `sample` command after a
`full` ingest reads the full dataset while stamping artifacts with the sample profile's
digest and its `SYNTHETIC` data class — a licence problem as well as a provenance one.
This is not hypothetical: it happened here, and triggered the same 522 M-row sort a
second time. `lockin.dataset_stamp` now records the writing profile and readers refuse a
mismatch. Unstamped directories pass, since pre-existing data is not evidence of a
mismatch.

Also fixed: `cfg.data_class` returns `REGISTERED`, which is not in the manifest
vocabulary `{PUBLIC, RESTRICTED, SYNTHETIC, DERIVED}`, so **every manifest write in a
registered run raised**. Never caught before because no registered run had ever reached
that line. `cfg.manifest_data_class` maps it to `RESTRICTED` — the same fact, stated as
redistribution status, which is what a manifest reader needs.

**First real numbers.** 40 cohorts (2013Q1–2022Q4), performance filtered to
2021-01…2024-12: **521,991,736 loan-months, 14,987,949 loans** — 4,529,704 prepayments
(30.2%), 7,665 credit events (0.1%), 10,450,580 censored (69.7%). 56.6% of loans are
left-truncated, and the diagnostic separates the two causes: 9.27 M enter in the first
month of the performance window (a window artifact) rather than through Freddie Mac's
acquisition lag. Ingest 8 minutes at ~2.05 M lines/s; 4.9 GB of Parquet.
