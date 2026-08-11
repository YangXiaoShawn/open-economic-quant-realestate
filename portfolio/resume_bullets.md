# Résumé bullets

Six framings of the same project. Pick the one matching the role and drop the rest.

**Standing rule for every version below:** the loan-level results in the current run
were computed from labeled **synthetic fixtures**, because the Freddie Mac
Single-Family Loan-Level Dataset requires registration. Do **not** put a loan-level
number on a résumé until you have completed the registration and re-run. Every
bullet here is written so that it describes **what was built and what it measures**,
not a magnitude — which is both honest and, for a résumé, stronger.

---

## Housing economist

- Built a reproducible research system measuring how the gap between homeowners'
  existing mortgage rates and prevailing market rates relates to mortgage exits,
  local purchase-market activity, house prices, and residential construction,
  combining loan-level duration analysis with a state-month quasi-experimental panel.
- Constructed eight distinct point-in-time lock-in measures (raw and positive rate
  gap, refinance incentive, payment-equivalent monthly cost, present-value financing
  gap, and count- and UPB-weighted geographic exposure shares), each aligned so that
  no measure uses a market rate observable only after the date it describes.
- Designed a shift-share identification strategy using the **predetermined**
  pre-shock local mortgage coupon distribution evaluated at the subsequent national
  rate path, with geography and period fixed effects, clustered inference, joint
  pre-trend tests, placebo shock dates, and placebo outcomes.
- Developed a demand-versus-supply framework showing why lock-in unambiguously
  reduces transaction volume while leaving the **price** effect theoretically
  ambiguous — a locked-in owner withdraws from both the listing side and the
  repeat-buyer side simultaneously — and refused to assume a sign anywhere in the
  analysis.
- Documented that Freddie Mac Zero Balance Code 01 pools refinancing, sale-related
  payoff, and maturity, and therefore reported the loan-level outcome as
  **prepayment** rather than mobility, with mobility-adjacent questions approached
  only through independent market-level measures.

## Household finance

- Estimated a ladder of duration models for mortgage prepayment — Kaplan–Meier,
  Aalen–Johansen cumulative incidence, discrete-time logit and complementary
  log-log hazards with loan-age dummies, Cox with Schoenfeld proportional-hazards
  diagnostics, cause-specific competing risks, and a gradient-boosted out-of-time
  predictive benchmark — on a loan-month panel with explicit left truncation and
  right censoring.
- Quantified household lock-in in dollar terms, not just basis points: the
  payment-equivalent monthly cost of replacing the outstanding balance at the
  prevailing market rate, and its present value over a stated (calibrated) holding
  horizon.
- Estimated pre-specified heterogeneity in the prepayment–rate-gap relationship by
  initial note rate, loan age, current LTV, credit score, loan balance, occupancy,
  and loan purpose, keeping exploratory subgroups labeled separately.
- Identified and corrected a covariate-timing bug in which the reported end-of-period
  balance (zero in a payoff month) had zeroed the payment-gap covariate in exactly
  the months where exits occur; switched all measures to a start-of-month balance
  with per-row provenance.

## Federal Reserve / policy

- Delivered a policy-facing analysis of mortgage rate lock-in that separates four
  evidence tiers — descriptive, hazard-association, quasi-experimental, and
  simulation — and enforces the separation **in code**: an outcome whose pre-trend
  test fails is automatically demoted and loses causal language, with no manual
  override.
- Wrote an executive memo answering how lock-in is defined, which borrowers are most
  exposed, what the evidence does and does not establish about exits, purchase
  activity, prices, and construction, which policy scenarios rank highest under the
  model, and what the binding limitations are.
- Built a transparent counterfactual module covering rate declines, partial
  portability, conditional assumability, seller credits, rate buydowns, elevated
  supply elasticity, targeted starter-home policy, and a no-lock-in bound — labeling
  every estimated versus calibrated input and stating explicitly that the outputs are
  not forecasts.
- Made the cost-effectiveness point explicit: cost per *additional* transaction far
  exceeds cost per assisted borrower because most recipients would transact anyway,
  and portability or assumability **transfers** the below-market-coupon loss rather
  than eliminating it.
- Documented, in a dedicated report, why this design cannot identify the aggregate
  effect of the national rate increase (the common shock is absorbed by time fixed
  effects) and therefore refuses to produce a national magnitude.

## Mortgage finance / banking

- Implemented a verified parser for the Freddie Mac Single-Family Loan-Level Dataset
  against the official published file layout and user guide (32 origination and 32
  monthly performance fields), including all documented sentinel missing-value codes
  and the official Zero Balance Code termination-event priority table.
- Classified loan exits according to Freddie Mac's own documentation, treating whole
  loan sales, reperforming-loan securitizations, and defect repurchases (ZB 15/16/96)
  as **censoring** rather than borrower behaviour, since counting portfolio actions as
  prepayment would inflate measured speeds.
- Reconstructed the active mortgage stock by geography and month with weighted-average
  coupons, full coupon-distribution deciles, locked-in shares at 100/200/300/400 bp
  under both loan-count and UPB weighting, realised prepayment and credit-event rates,
  and origination-cohort composition.
- Reproduced the qualitative shape of the prepayment literature — a steeply
  non-linear response to the refinance incentive plus a seasoning ramp — and used it
  as a validation check on the pipeline rather than presenting it as a new finding.
- Handled the operational realities of the data: modification-driven loan-age resets,
  missing performance months, servicer-name suppression below 1% of quarterly UPB,
  the 2019 accounting-cycle change, and conflicting termination codes resolved by the
  official priority table.

## Applied scientist

- Built an end-to-end causal-inference and survival-analysis system in Python 3.12
  (Polars, DuckDB, PyArrow, statsmodels, lifelines, scikit-learn) with 107 unit tests,
  ruff, and mypy on core interfaces, reproducible from a single `make` target.
- Designed an evidence-tier type system in which every result artifact carries its
  tier, population, geography, weight, outcome definition, caveats, and full
  provenance (git commit, config digest, data period, per-source
  schema/retrieval/checksum), and in which the report renderer refuses to emit
  causal language for a tier that has not earned it.
- Benchmarked the interpretable discrete-time hazard specification against a
  gradient-boosted classifier on an out-of-time split with AUC, Brier score, and a
  calibration curve, and documented explicitly that better prediction is not better
  identification.
- Caught five substantive design and data errors through automated diagnostics rather
  than inspection — including a treatment variable with zero variance and a public
  API that silently ignored an unrecognised filter and returned unfiltered totals —
  and converted each one into a permanent pipeline assertion.
- Implemented a case-cohort sampling design (retain all event months, sample non-event
  months with recorded weights) so the same estimation code scales from a small sample
  to a multi-billion-row loan-month panel on a 16 GB machine.

## Data engineering

- Engineered a streaming ingestion pipeline for multi-gigabyte pipe-delimited mortgage
  performance files: chunked line streaming, in-archive reads without full extraction,
  column projection that drops the unused loss/expense block, type and sentinel
  normalisation, and Hive-partitioned Parquet output keyed by cohort and period year
  for predicate and projection pushdown.
- Kept peak memory bounded and independent of file size by never materialising the
  loan-by-month panel: Polars lazy plans collected in streaming mode, DuckDB for
  aggregation, and a configurable row budget that fails the run rather than swapping.
- Built five replaceable source adapters (Freddie Mac loan-level, PMMS, FHFA HPI,
  HMDA Data Browser API, Census Building Permits Survey) with on-disk caching,
  idempotent fetches, bounded-concurrency parallel retrieval, URL-candidate fallback
  that fails loudly listing every URL tried, and refusal to silently use a stale cache.
- Enforced data governance mechanically: restricted loan-level data can never be
  committed (gitignore plus a test that scans the git index for restricted paths,
  bulk-data extensions, and oversized files), every dataset carries a manifest with a
  SHA-256 checksum that validation re-verifies, and synthetic inputs propagate a
  mandatory report banner that no flag can disable.
- Added an API-contract assertion after discovering that a public aggregations endpoint
  silently dropped an unrecognised filter parameter and returned unfiltered totals;
  responses are now rejected unless the service echoes every requested filter back, on
  both fetch and cache read, with versioned cache keys so poisoned entries cannot be
  reused.
