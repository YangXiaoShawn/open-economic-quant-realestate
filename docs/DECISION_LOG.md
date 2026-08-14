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

### 2026-08-13 · D033 · First fully empirical run, and the sampling that made it fit **[AUTO]**
The whole pipeline now runs on the registered Freddie Mac Standard dataset. **36 of 37
artifacts are stamped `REGISTERED`**; the loan-level numbers below are the first in this
repository that are evidence rather than software verification.

**Loan-level prepayment hazard — the headline empirical result.**
Discrete-time logit, 3,808,045 loan-months, 187,401 prepayment events, standard errors
clustered across **240,757 loans**:

| | coef | s.e. | z | hazard ratio |
|---|---|---|---|---|
| `rate_gap` | **−0.2020** | 0.0018 | −112.2 | **0.8171** |

Each additional percentage point of rate gap multiplies the monthly prepayment hazard by
about **0.82** — roughly an 18% lower monthly exit hazard per point of lock-in. The
complementary log-log link gives −0.1990 on the same data, and a 15% loan sample gave
−0.1993 on 11.4M loan-months, so the estimate is stable across link function and sample
size.

Tier is `hazard_association`, not causal, and the vocabulary rules in `AGENTS.md` §1
still bind: Zero Balance Code 01 pools voluntary payoff, sale-related payoff and
maturity, so **this is an effect on mortgage exits, not on moves or home sales**.

**Market-level — the pre-trends fail, and the tier system demoted them.**
51 clusters now (real data covers every state plus DC), against 26 on fixtures:

| outcome | DiD | s.e. | t | pre-trend p | tier |
|---|---|---|---|---|---|
| log purchase originations | −0.0238 | 0.0109 | −2.18 | **0.0000 FAIL** | `descriptive` |
| log refinance originations | −0.0789 | 0.0269 | −2.93 | **0.0000 FAIL** | `descriptive` |
| house price growth | −0.0040 | 0.0021 | −1.96 | 0.509 | `descriptive` (circularity guard) |
| log single-family permits | −0.0264 | 0.0230 | −1.15 | 0.974 | `quasi_experimental` |
| log 5+-unit permits *(placebo)* | +0.0302 | 0.0552 | +0.55 | **0.072 FAIL** | `descriptive` |
| purchase denial rate *(placebo)* | −0.0019 | 0.0012 | −1.57 | **0.004 FAIL** | `descriptive` |

The purchase-originations coefficient is now "significant" at t = −2.18 — **and must not
be read causally**, because its pre-trend test fails at p < 0.0001. High- and low-exposure
states were already diverging before the shock. The automatic demotion did exactly the
job it was built for: the one outcome that passes its pre-trend, single-family permits,
is also the one that is insignificant.

**Why the run is sampled, and by how much.** Sized by **disk, not RAM**. Peak RSS across
the ten hazard stages stayed under 7 GB, but Polars streaming scratch accumulates between
stages: at a 15% loan sample it walked the disk from 76 GB free to 18 GB and falling
before being stopped. The unsampled episode table is 90.6M loan-months and its dense
design matrix is ~21.7 GB, which OOM-killed the estimator outright.

`survival.loan_sample_fraction` was **documented as the knob for exactly this and never
implemented** — its only appearance in the codebase was an error message advising the
user to lower it, which would have done nothing. It now works: a plain random sample of
loans, every month of each selected loan retained, applied at episode build. At 0.05 the
episode table is 4,519,525 rows and the whole pipeline completes in minutes.

Two draws now thin the data, so the inverse-probability weight is the reciprocal of the
**total** inclusion probability: `1/loan_frac` for an exit month and
`1/(loan_frac × non_event_frac)` otherwise. Weighting for only the case-cohort draw, as
the code did before, would have understated the population by a factor of 20.

**`reports/` is a shared path too.** A governance test asserted the synthetic banner by
loading `configs/sample.yaml` and checking the markdown on disk — which belonged to
whichever profile last rendered. `render_all` now stamps the reports directory, and the
test asks the directory what produced it instead of assuming.

### 2026-08-13 · D034 · `validate-data` did not scale to the real dataset **[AUTO, RESOLVED]**
Every other stage completes on the registered data in minutes. `validate-data` ran for
over thirty minutes without finishing and was stopped. Disk stayed flat at ~28 GB free
throughout, so it is not spilling — it is doing per-loan work that does not scale, most
likely one of the whole-table `.collect()` calls in `lockin.episodes.validate_episodes`
or `lockin.events.validate_events`.

**Resolved.** Timing each section individually rather than guessing found it at once:
everything except `performance.validate` finished in under two minutes, and that one
sorts the whole table by `loan_seq_no` twice — 522 million rows. Same defect and same fix
as D032: the checks are all per-loan (duplicate loan-months, more than one Zero Balance
Code, loan-age monotonicity, month gaps), loans never span cohorts, so it now iterates
cohort partitions and sums. **30+ minutes and unfinished → 149 seconds.** Full
`validate-data` now completes in about 4.5 minutes and all 37 artifacts are `REGISTERED`.

### 2026-08-13 · D035 · Two validation gates that were lying, in opposite directions **[AUTO]**
Once `validate-data` could finish, it reported **4 HARD** problems. Two were mine and two
were real, and they needed opposite treatment.

**Self-inflicted: the profile stamp broke the checksums it sat next to.** `sha256_dir`
excluded `*.manifest.json` from the directory digest but not the `.lockin_profile.json`
written by D032, so adding the stamp made both interim datasets report a checksum
mismatch — a HARD error about data that had not changed. Sidecars that *describe* a
dataset are now excluded as a named group, and the original checksums verify again.

**Real, and mis-graded: source-data noise was failing the run.** The registered files
genuinely contain 35 loans (of 20,199,214) with `orig_loan_term` of 481–544 months and
one loan with a `0.0` note rate. These are properties of Freddie Mac's data, not parsing
faults — and as unconditional HARD errors they would fail every real run forever, which
is how a gate stops being read.

Severity is now scaled by prevalence against `DOMAIN_VIOLATION_HARD_SHARE` (1e-4), and
**the observed values are reported either way**. The rule is principled rather than
convenient: a systematic layout shift or parse error corrupts a large fraction of a
column and still stops the run; 35 impossible records in 20 million is noise to be
recorded, not a reason to distrust the other 20,199,179. Lowering a severity to make a
run pass would be the wrong instinct, so the threshold is a documented constant with
tests on both sides of it.

### 2026-08-13 · D036 · MSA-level HMDA has two silent-zero traps **[AUTO, BLOCKING #3]**
Probing the CFPB aggregations API before writing the adapter — the discipline D017 was
opened for — found that `msamds` is accepted and echoed back, and still returns **zero**
in two distinct situations. Neither raises. A naive MSA-level fetch would have produced a
well-formed panel in which the largest metros in the country had no lending at all.

**Trap 1: HMDA reports Metropolitan Divisions, not their parent MSA.**

| code | area | 2022 purchase originations |
|---|---|---|
| 16984 | Chicago-Naperville-Evanston **MD** | 89,212 |
| 12060 | Atlanta (no divisions) | 94,726 |
| 16980 | Chicago **MSA** (the parent) | **0** |
| 35620 | New York MSA | **0** |
| 31080 | Los Angeles MSA | **0** |

Every divided metro — New York, Los Angeles, Chicago, Dallas, Miami, Washington, Boston,
Detroit, Philadelphia, San Francisco, Seattle, Minneapolis — returns zero under its CBSA
code. This is exactly why `lockin.adapters.omb_cbsa` loads divisions as first-class
entries with a `code_kind` discriminator (D028); a crosswalk that folded them into their
parents would have made this trap undetectable.

**Trap 2: HMDA year Y uses the OMB delineation in force in year Y.**
Two division codes returned zero despite being valid divisions: `11694`
(Arlington-Alexandria-Reston) and `12054` (Atlanta-Sandy Springs-Roswell). Both exist
**only in the 2023 vintage** of the crosswalk — they are new divisions, and 2022 HMDA
data predates them. `11244` (Anaheim) and `14454` (Boston), present since 2015, return
real counts.

**Consequence for #3.** MSA-level HMDA is not "pass `msamds` instead of `states`". It
requires resolving each geography **per year** against the delineation in force for that
year, and it requires an explicit **non-zero assertion**: a metropolitan area with zero
purchase originations in a year is not a real observation, it is a failed lookup. Both
belong in the adapter, alongside the existing `_assert_filters_applied` echo check.

Recorded now rather than discovered later, because the failure mode is a plausible number
rather than an error.

### 2026-08-13 · D037 · Year-versioned HMDA geography resolution **[AUTO]**
Implements what D036 established. `omb_cbsa.vintage_for_hmda_year` and
`omb_cbsa.hmda_geographies` resolve each analysis CBSA to the code HMDA will actually
answer on **for that year**, and `hmda.fetch_msa` uses them.

The year→vintage mapping is **empirical, not derived from an effective-date rule** — OMB
bulletins reach HMDA with a lag this project could not find documented, and a wrong guess
returns silent zeros:

| HMDA year | vintage | how it was located |
|---|---|---|
| 2018 | 2017 | Chicago answers on division `16974`, present only in the 2015/2017 vintages |
| 2019–2023 | 2018 | Chicago answers on `16984`; `16974` returns 0 from 2019 on |
| 2024+ | 2023 | Atlanta division `12054` and Arlington `11694` appear; Atlanta CBSA `12060` and Washington division `47894` drop to 0 |

Divisions are keyed back to their **parent CBSA** by shared counties — not by a code
prefix, since division codes are not derived from their parent's — so the panel unit
stays stable even though the queried code does not. Verified live: Boston `14454` →
parent `14460`, Chicago `16984` → `16980`, undivided Atlanta and Phoenix pass through,
all returning real counts.

`_assert_metros_are_not_empty` is the guard that makes the whole thing safe to trust. A
metropolitan area with zero purchase originations in a year is a failed lookup, not an
observation, so more than 5% empty in **any single year** raises. The check is per-year
on purpose: a wrong vintage boundary breaks exactly one span, and pooling across years
would dilute a total failure in 2024 below any sensible threshold.

### 2026-08-13 · D038 · Metropolitan permits, and the mirror image of the HMDA trap **[AUTO]**
`census_bps.fetch_metro` adds the Building Permits Survey at metropolitan geography.

Two things had to be checked rather than assumed, and both turned out to matter.

**The series is published in two directories, split at January 2024.**
`Metro (ending 2023)/ma{YYMM}{v}.txt` and `CBSA (beginning Jan 2024)/cbsa{YYMM}{v}.txt`.
The split is not cosmetic: the 2024+ files are delineated on the **2023 OMB bulletin** —
Chicago is "Chicago-Naperville-Elgin **IL-IN**" there against "IL-IN-**WI**" before, and
Atlanta is renamed. **That is the same delineation change, in the same year, that HMDA
makes** (D037). Two independent federal series moving together is a useful cross-check on
the empirically-derived HMDA year mapping, which was established from a different kind of
evidence entirely.

**BPS reports the parent CBSA and never a Metropolitan Division — the exact opposite of
HMDA.** Chicago is `16980` here, New York `35620`, Los Angeles `31080`: the very codes
that return a silent zero from the HMDA API. Had both adapters been written on the
assumption that "MSA code" means one thing, the panel would have joined divisions to
parents under a shared column name and nobody would have seen it.

Both adapters therefore key to the **parent CBSA**, and the difference is absorbed inside
each adapter rather than left for the panel builder to trip over. HMDA carries
`report_code` alongside so what was actually queried stays auditable.

Verified live on 2022: 384 metros, 12 months, and permit counts that behave the way the
places do — Atlanta 25,961 single-family units against Chicago's 8,020.

### 2026-08-13 · D039 · Metropolitan LAUS, and a self-inflicted silent-empty bug **[AUTO]**
`bls_laus.fetch_metro` completes the third and last MSA adapter. All 393 metropolitan
CBSAs resolve; 2021–22 rates behave as the places do (Los Angeles 6.40%, New York 6.01%,
Chicago 5.42%, Atlanta 3.46%).

**Metro LAUS is not seasonally adjusted, and the state series is.** `LASMT…` — the
adjusted counterpart of the `LASST…` series this project already uses — returns zero
observations for every metro tried; only `LAUMT…` exists. So the state panel carries an
*adjusted* rate and the metro panel an *unadjusted* one. These are different
measurements and must not be pooled. The annual panel averages twelve consecutive months,
which removes most of the seasonality and is a legitimate annual rate; the **monthly**
metro series is not comparable to the monthly state series and is labelled as such in
`METRO_SEASONALITY_NOTE` and in the manifest.

**The identifier carries a state FIPS, which is undefined for multi-state CBSAs** — 43 of
the 393. Rather than guess the rule (principal city? largest share? first alphabetically?)
every state the CBSA spans is tried and whichever answers is kept: 444 candidate series,
18 API queries, inside the unregistered daily allowance.

**And the first draft of it was wrong in exactly the way the adapter is designed to
avoid.** `metro_series_id` emitted 19 characters — five trailing zeros where the area code
needs six. BLS answers a malformed identifier with an **empty series, not an error**,
which is indistinguishable from a metro with no labour force; every one of the 393 would
have come back empty and the "try every state" logic would have reported that no state
worked. Caught by comparing against the identifier verified live before any code was
written. The function now asserts the 13-character area code, and a test pins both the
length and the two verified identifiers.

### 2026-08-13 · D040 · Metro sources wired into the panel; request volume constrained **[AUTO]**
`build_local_panel` now selects the metropolitan loader for each source when
`panel.geography == "msa"`: `census_bps.load_metro`, `bls_laus.try_load_metro`,
`hmda.load_msa`, and FHFA at `level="MSA"`. The state path is untouched — the metro
branch exits through a sentinel rather than restructuring the try blocks, so the default
geography keeps the exact shape every existing test exercises.

**A first MSA panel would cost ~12,350 HMDA requests against ~1,530 for the state
panel** — 393 metros × 6 years × 5 measures, against a free public service. `fetch_msa`
therefore takes `restrict_to`. A metro with no loans in the Freddie sample contributes a
null exposure and is dropped by the panel builder regardless, so fetching it buys
nothing. The adapter does not read the loan tables itself, so the caller supplies the
set; passing division codes or a wrong vintage raises rather than silently returning an
empty panel.

Still open before an MSA run can be believed: the **multi-state allocation rule**. 43 of
the 393 metropolitan CBSAs span more than one state, and while all four panel sources now
resolve at metro level, nothing yet decides how a state-published quantity is
apportioned when one is needed.

### 2026-08-13 · D041 · Freddie's MSA field is half Metropolitan Divisions **[AUTO]**
The multi-state allocation rule flagged as the last blocker for #3 turned out to be
**moot**: every panel source now resolves natively at CBSA level — FHFA at `level="MSA"`,
HMDA through the year-versioned division mapping, BPS and LAUS at parent CBSA,
teleworkable by CBSA. Nothing is published only by state any more, so nothing needs
apportioning. Recorded because it was on the plan and is now off it.

The real blocker was elsewhere, and larger. Resolving the 452 distinct MSA codes present
in the registered loan data against the crosswalk:

| what the code is | codes | loans |
|---|---|---|
| metropolitan CBSA, composition stable | 208 | 5,260,786 |
| **Metropolitan Division** | 37 | **4,657,458** |
| metropolitan CBSA, composition changed | 105 | 4,263,976 |
| metropolitan CBSA, renamed only | 47 | 2,933,059 |
| in no loaded vintage (retired) | 31 | 898,908 |

**4.66 million loans carry a division code**, because Freddie's field 5 is "MSA *or
Metropolitan Division*". Unmapped they match nothing in the Census permit series or LAUS,
both of which report parent CBSAs — so the panel would silently lose New York, Los
Angeles, Chicago, Dallas and Washington rather than fail.

`omb_cbsa.to_parent_cbsa` maps them by **shared counties**, since the codes bear no
relation to each other: Chicago's division is 16984 under parent 16980, Boston's is 14454
under 14460. Verified against those landmarks plus New York 35614→35620 and Los Angeles
31084→31080. **421 of 452 codes resolve, covering 17,225,055 of 18,124,095 loans (95%),
onto 395 parent CBSAs.** The 31 unresolved codes stay null and are dropped, never guessed
at — assigning a retired code to a neighbouring metro would be fabrication.

**The loan sample thins the panel, and the threshold hides it.** `min_loans_per_geography`
counts *sampled* loans, so at `loan_sample_fraction=0.05` a nominal 100 is really ~2,000
real loans. That is what takes the MSA panel from 395 metros to **138** — still 2.7× the
51 states, but the gap is the sampling and not the geography. The stock manifest now
records the nominal threshold, the fraction, the unsampled equivalent, and how many units
were dropped, so the number is visible rather than surprising.

**And a cache that answered the wrong question.** `bls_laus.fetch_metro` returned any
existing file without checking it covered the requested years, so a panel asking for
2018–2023 silently got a 2021–2022 file cached by an earlier probe. Both LAUS fetchers now
compare cached years against requested ones and refetch on a shortfall.

### 2026-08-13 · D042 · First MSA-level run: 8× the clusters, and it did not rescue identification **[AUTO]**
The MSA panel runs end to end on registered data: **138 metropolitan CBSAs** against 51
states. Compared with the state run on the same loans and the same shock:

| outcome | state (51 clusters) | MSA (138 clusters) | MSA pre-trend | tier |
|---|---|---|---|---|
| log purchase originations | −0.024 (t = −2.18) | **−0.042 (t = −4.93)** | **0.036 FAIL** | `descriptive` |
| log refinance originations | −0.079 (t = −2.93) | −0.124 (t = −4.68) | **0.000 FAIL** | `descriptive` |
| log single-family permits | −0.011 (t = −0.53) | −0.074 (t = −1.61) | 0.636 pass | `quasi_experimental` |
| log 5+-unit permits *(placebo)* | −0.037 (t = −0.65) | +0.029 (t = +0.37) | 0.940 pass | placebo passes |
| purchase denial rate *(placebo)* | −0.002 (t = −1.57) | −0.001 (t = −0.93) | 0.297 pass | placebo passes |

**The cluster gain bought precision, not identification.** The purchase-originations
t-statistic more than doubles and the magnitude nearly doubles — and the pre-trend test
still fails, so the artifact is still `descriptive` and the number still must not be read
causally. High- and low-exposure metros were already diverging before the shock, exactly
as high- and low-exposure states were. That the same failure appears at two very
different levels of aggregation is itself informative: it is not an artifact of pooling
states.

**Both placebos now pass**, where the denial rate failed at state level. More clusters
made the placebos better behaved, which is the expected direction and mild evidence that
the state-level placebo failure was a small-cluster artifact.

The one outcome that passes its pre-trend, single-family permits, remains insignificant.
**Nothing at market level supports a causal lock-in claim at either geography.**

Known gaps in this run, recorded rather than papered over: **FHFA publishes a
purchase-only quarterly index for only 65 of the 138 metros**, so the house-price row
rests on half the panel; **BLS refused further requests** ("daily threshold ... has been
reached") so metro LAUS covers only 2021–2022 and the cached-coverage guard correctly
reports the control as UNAVAILABLE rather than serving a short panel; and the monthly MSA
specifications are skipped for want of a monthly outcome at that geography.

### 2026-08-13 · D043 · An "ok" result carrying null coefficients **[AUTO]**
`log_permits_5plus` came back from the MSA run with `status: "ok"` and `coef: null`.
Thirty-four of 826 metro-years authorised no 5+-unit buildings at all, `log(0)` is `-inf`,
and the NaN propagated through OLS to a null coefficient that the estimator still called
a success.

Fixed at both ends. The log transform now maps non-positive counts to **null** rather
than `-inf`, so the observation is dropped honestly by the existing `drop_nulls`;
`log1p` was deliberately **not** used, because it would quietly redefine the outcome from
a log to a log-of-count-plus-one while keeping the name. And `did_two_period` now refuses
to return `status: "ok"` when the design or outcome contains non-finite values, or when
the fitted coefficient is not finite — a degenerate fit is reported as a failure with a
reason instead of as a result full of nulls. With both in place the cell estimates
normally: +0.029, t = +0.37, pre-trend 0.94.

### 2026-08-14 · D044 · Publish the research system, never the registered data **[AUTO]**
The public release is deliberately split across four surfaces: a versioned GitHub
source package for reviewable code, a permanent GitHub Pages narrative, a versioned
Hugging Face Dataset prefix, and a Dataset-backed Space. Canonical destinations and
catalog metadata live in `project.yaml`; the inclusion boundary lives in
`docs/PUBLICATION.md`.

The package is built from tracked files only. It includes source, tests, configuration,
synthetic fixtures, documentation, portfolio material, and aggregate reports. It
excludes `data/raw`, `data/interim`, `data/processed`, `data/cache`, and `outputs` in
their entirety. This is not only a size decision: the registered Freddie Mac archive and
every loan-granular derivative are non-redistributable. Aggregate coefficients and
reports remain publishable because they contain no loan records and retain the required
population, evidence-tier, and attribution language.

The website headline reports the registered-data hazard association and the failed
market-level identification checks together. Publishing only the strong loan-level
association would invite a causal or mobility reading the data do not support; the public
page therefore gives equal prominence to the pre-trend failures, the null permits result,
and the rule that prepayment is not a home sale or household move.
