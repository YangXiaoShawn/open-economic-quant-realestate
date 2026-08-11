# Project Plan

**Mortgage Rate Lock-In, Housing Liquidity, and Local Market Dynamics**

Status tracking lives in `STATUS.md`. Decisions live in `docs/DECISION_LOG.md`.
This document defines milestones, dependencies, acceptance tests, and the risk
register (licence, selection, identification, computation).

---

## 0. Scope statement

We measure how the **rate gap** — the difference between the prevailing market
mortgage rate and a homeowner's existing note rate — relates to:

- **Loan-level:** mortgage exits (prepayment; credit events; modification), with
  survival methods that respect left truncation, right censoring, and competing risks.
- **Local-market-level:** purchase-mortgage originations, refinance originations,
  house price growth, and residential building permits, via a predetermined
  exposure design around the 2021→2023 national mortgage-rate increase.
- **Counterfactual:** model-dependent policy scenarios (rate declines, portability,
  assumability, buydowns, supply elasticity) built on estimated hazards.

We do **not** measure household mobility. See `AGENTS.md` §1.

---

## 1. Milestones

### M0 — Repository foundation ✅
Professional research repo: `pyproject.toml` (Python 3.12 / uv), Makefile with the
full target set, `AGENTS.md`, docs, data governance files, `.gitignore` that blocks
restricted data.

**Acceptance:** `make setup && make test` succeeds on a clean clone.

### M1 — Verified schemas and data adapters ✅
- Freddie Mac Single-Family Loan-Level Dataset field layout encoded from the
  **official public** `file_layout.xlsx` and `user_guide.pdf` (32 origination
  fields, 32 monthly performance fields), with enumerations and sentinel
  missing-value codes.
- Adapters, each with disk cache + manifest:
  - `pmms` — Freddie Mac Primary Mortgage Market Survey history (official CSV).
  - `fhfa_hpi` — FHFA HPI master file (all index concepts × geographies).
  - `hmda` — CFPB HMDA Data Browser aggregations API.
  - `census_bps` — Census Building Permits Survey.
  - `freddie_llds` — registered local loan-level files (sample or full mode).

**Acceptance:** `make fetch-public-data` populates `data/cache/` with manifests;
`pytest -m network` verifies each live endpoint; offline tests pass with fixtures.

### M2 — Ingestion ✅
Streaming/chunked pipe-delimited parsers → partitioned Parquet
(`origination/cohort=YYYYQn/`, `performance/cohort=YYYYQn/period_year=YYYY/`),
type normalisation, sentinel → null normalisation, duplicate-loan detection,
performance-date monotonicity checks, incremental cohort processing.

**Acceptance:** `make ingest-mortgages && make validate-data` on the synthetic
fixture cohort produces zero hard validation failures; peak RSS < 2 GB.

### M3 — Loan-event table ✅
One row per loan: entry, observation start (**left truncation at Freddie Mac
acquisition, not origination**), observation end, censoring status, exit type from
the official ZB code priority table, plus a compact loan-month episode table with
time-varying UPB, estimated LTV, market rate, and lock-in measures.

**Acceptance:** `tests/test_events.py` — no loan has an exit before its
observation start; no loan has two exits; ZB `15/16/96` map to censoring; the sum
of exits + censored = number of loans.

### M4 — Lock-in measures ✅
Eight measures (raw gap, positive gap, refi incentive, payment-equivalent cost,
PV financing gap, local locked-in share, UPB-weighted exposure, count-weighted
exposure), all point-in-time.

**Acceptance:** `tests/test_amortization.py` and `tests/test_lockin_measures.py`
— closed-form payment identities, zero-rate limit, PV identity, no-look-ahead
assertion on market-rate alignment.

### M5 — Active mortgage stock + exposure panel ✅
Geography-month table: active loan count, aggregate UPB, weighted-average note
rate, note-rate distribution, locked-in shares at 100/200/300/400 bp, median
payment-equivalent lock-in cost, refi-incentive share, prepayment rate, credit-event
rate, cohort composition, purpose composition, estimated current LTV.

**Acceptance:** `make build-local-panel` writes both count-weighted and
UPB-weighted variants; attrition is documented in `outputs/coverage/`.

### M6 — Duration analysis ✅
Ladder: (1) Kaplan–Meier / cumulative incidence; (2) discrete-time logit hazard
with loan-age dummies; (3) complementary log-log; (4) Cox where feasible;
(5) cause-specific competing-risk hazards; (6) gradient-boosted classifier as a
predictive benchmark. Out-of-time split with calibration.

**Acceptance:** `make estimate-hazards` writes coefficient tables, baseline
hazard, spline/bin rate-gap profile, out-of-time AUC and calibration, all tagged
`hazard_association`.

### M7 — Local-market panel + event study ✅
State-month panel joining exposure, HMDA purchase/refi originations, FHFA HPI
growth, BPS permits. Continuous-treatment event study with geography and period
fixed effects, clustered SEs, pre-trends, placebo shock dates, placebo outcomes.

**Acceptance:** `make estimate-local-effects` writes dynamic coefficients, a
joint pre-trend test, ≥2 placebo specifications, and an exposure-distribution table.

### M8 — Demand/supply decomposition ✅
Conceptual flow diagram + empirical partition of the reduced form into
listing-side and repeat-buyer-side channels using purpose composition, permits,
and first-time-buyer proxies.

**Acceptance:** `reports/demand_supply_decomposition.md` generated with a sign
table that does **not** presume the price effect's sign.

### M9 — Robustness and falsification ✅
The full grid in `AGENTS.md`-adjacent `docs/RESEARCH_DESIGN.md` §Robustness.
Failed specifications recorded in `reports/failed_hypotheses.md`.

**Acceptance:** the robustness runner writes one row per specification with a
pass/fail/fragile verdict; failures are not silently dropped.

### M10 — Counterfactual policy module ✅
Hazard-aggregation simulator: −50/−100/−200 bp market-rate paths, partial
portability, conditional assumability, seller credit, buydown, elevated supply
elasticity, no-lock-in counterfactual. Reports behavioural parameters, which
inputs are estimated vs calibrated, modelled additional exits, demand/supply/price
responses, fiscal cost, distribution, and uncertainty.

**Acceptance:** every scenario artifact carries `evidence_tier: simulation` and
the string "not a forecast".

### M11 — Replication benchmark ✅
`reports/replication_protocol.md` + `reports/benchmark_comparison.md` against
published FHFA / Federal Reserve / academic lock-in estimates, each labelled
exact / approximate / conceptual.

**Acceptance:** no benchmark is labelled "exact" where the original used
proprietary linked mortgage-property records.

### M12 — Dashboard + portfolio ✅
Streamlit app; every chart annotated with population, geography, period, weight,
outcome definition, source, and model/descriptive status. Portfolio deliverables.

**Acceptance:** `make dashboard` runs; each panel renders its annotation block.

### M13 — Full registered-data run (BLOCKED)
Same pipeline over the registered Freddie Mac dataset.

**Blocker:** requires the user to register and accept Freddie Mac's terms. See
`data/DATA_ACCESS.md`. **We will not bypass this.**

---

## 2. Dependency graph

```
M0 ──> M1 ──> M2 ──> M3 ──> M4 ──> M5 ──> M6 ──┬──> M9 ──> M10 ──> M11 ──> M12
                                                │
                     (public aggregates) ───────┴──> M7 ──> M8
                                                            M13 (blocked on registration)
```

- M4 depends on M1 (`pmms`) for point-in-time market rates.
- M7 depends on M5 (exposure) **and** on M1 (`hmda`, `fhfa_hpi`, `census_bps`).
- M10 depends on M6 (hazard coefficients are the behavioural inputs).
- M8 depends on M7 and, for interpretation only, on M6.

---

## 3. Acceptance tests (repo-level, maps to the 22 stated criteria)

| # | Criterion | Where enforced |
|---|---|---|
| 1 | Sample end-to-end run from README | `make reproduce-sample`; `tests/test_pipeline_smoke.py` |
| 2 | Works without redistributing restricted data | `.gitignore`; `tests/test_governance.py` |
| 3 | Sample files / labeled fixtures support tests | `data/fixtures/`; `lockin.fixtures` |
| 4 | Origination↔performance joined reproducibly | `tests/test_ingest.py::test_join_is_deterministic` |
| 5 | Censoring and exits explicitly modelled | `tests/test_events.py` |
| 6 | Prepayment not mislabeled as mobility | `tests/test_governance.py::test_no_mobility_language` |
| 7 | Payment and rate-gap math unit-tested | `tests/test_amortization.py`, `tests/test_lockin_measures.py` |
| 8 | Market rates aligned point-in-time | `tests/test_rate_alignment.py::test_no_look_ahead` |
| 9 | Active mortgage stock reconstructable | `tests/test_stock.py` |
| 10 | ≥1 descriptive survival curve | `outputs/hazards/km_*.json` |
| 11 | ≥1 discrete-time hazard model | `outputs/hazards/dt_logit_*.json` |
| 12 | Competing exits addressed | `outputs/hazards/competing_*.json` |
| 13 | Geography-time lock-in exposure panel | `outputs/panel/exposure_panel.parquet` |
| 14 | Originations/prices/permits linked defensibly | `outputs/panel/local_market_panel.parquet` |
| 15 | Local event study estimated | `outputs/eventstudy/*.json` |
| 16 | Pre-trends and placebos reported | same, `pretrend_test` + `placebo_*` keys |
| 17 | Conforming selection discussed | `reports/methodology_and_limitations.md` |
| 18 | Loan-level vs local causal claims separated | `evidence_tier` on every artifact |
| 19 | ≥1 policy scenario, labeled model-dependent | `outputs/scenarios/*.json` |
| 20 | Reports generated from code | `make report`; `GENERATED` header check |
| 21 | Provenance on every result | `lockin.provenance.run_context()` |
| 22 | Failed/fragile specs documented | `reports/failed_hypotheses.md` |

---

## 4. Risk register

### 4.1 Data licence and access risks

| Risk | Severity | Mitigation |
|---|---|---|
| Freddie Mac loan-level data require registration and acceptance of terms; redistribution prohibited | **High** — blocks M13 | Adapter reads local registered files only; `data/DATA_ACCESS.md` gives instructions; synthetic fixtures for engineering; nothing loan-level is ever committed |
| PMMS terms restrict commercial redistribution | Medium | Cache locally, never commit; store retrieval metadata; cite Freddie Mac |
| HMDA public LAR carries privacy modifications; bulk files are large | Medium | Use the CFPB aggregations API (counts/sums only), cache responses |
| FHFA HPI / Census BPS are public-domain U.S. government works | Low | Cache + manifest; cite release |
| Upstream URLs change (PMMS URL already moved once) | Medium | Adapters hold a URL candidate list and fail loudly with the tried URLs |

### 4.2 Selection issues

1. **Freddie Mac population.** Conforming conventional only. Excludes FHA/VA
   (disproportionately first-time and lower-income buyers), jumbo (high-price
   metros), non-QM, bank portfolio, and all-cash. Any "share locked in" computed
   from this population is a share **of Freddie-acquired loans**, not of U.S.
   homeowners. Roughly a third of owner-occupied homes have no mortgage at all;
   those households cannot be locked in and are entirely absent.
2. **Survivorship in the active stock.** The active stock at date *t* is the set
   of loans that have not yet exited. Loans with the strongest refinance
   incentive exited earliest, so the surviving stock is mechanically
   low-coupon-tilted. Exposure measures must therefore be built from a
   **predetermined** date and pushed forward, not recomputed contemporaneously.
3. **Administrative removals.** ZB `15`/`16` (whole-loan sale, RPL
   securitization) remove loans for reasons unrelated to borrower behaviour.
   Treating them as prepayment would inflate hazards; treating them as
   informative censoring is itself an assumption. We censor and test sensitivity.
4. **HARP / Relief Refinance loans** have suppressed DTI/LTV disclosures and
   were created by a policy program, not ordinary refinancing.
5. **HMDA coverage changes.** The 2018 reporting threshold change and the 2020
   closed-end threshold change (25 → 100 loans) break comparability of counts
   across those boundaries.
6. **Seller/servicer name suppression** below 1% of quarterly UPB collapses the
   tail into "Other", limiting stable-servicer robustness checks.

### 4.3 Identification threats

Detailed in `docs/IDENTIFICATION_STRATEGY.md`. Summary:

| Threat | Why it matters | Planned response |
|---|---|---|
| Pandemic demand reallocation (2020–21) | Correlated with both refi intensity and later price/permit paths | Control for 2019–21 price growth; exclude top-decile boom markets; remote-work exposure control |
| Remote-work exposure | Drives migration and construction independent of lock-in | Teleworkable-share control (optional adapter); heterogeneity split |
| Differential refinancing booms | High-refi markets have both low coupons *and* unusual 2020–21 demand | Instrument-free: control for pre-period refi intensity; exclude top-decile |
| Local labour shocks | Move both exits and originations | Unemployment control; region-by-period FE |
| Housing-supply constraints | Determine how a demand shift maps into prices vs quantities | Predetermined supply-elasticity proxy; interaction, not control-only |
| Composition change in observed mortgages | Exposure measured on a shrinking, selected stock | Predetermined exposure; fixed pre-shock cohort |
| National monetary-policy endogeneity | The national rate path is common, so it is absorbed by time FE — but the *interaction* with exposure is the estimand | Time FE + continuous treatment; state clearly that only relative effects are identified |
| Geography-specific rate dispersion | PMMS is national; local rates differ | Acknowledge measurement error; robustness with HMDA-reported local rates where available |
| Shift-share exogeneity | Predetermined ≠ exogenous | Shock-orthogonality discussion; Goldsmith-Pinto-Sorkin style share diagnostics; no IV language without a written exclusion restriction |

### 4.4 Computational constraints

- Target: Apple Silicon, ~16 GB RAM, limited disk, no GPU.
- The full loan-level dataset is ~50+ GB decompressed with billions of
  loan-months. **Never** materialise the loan-by-month panel.
- Strategy: per-cohort streaming parse → partitioned Parquet → Polars lazy scans
  with projection/predicate pushdown → DuckDB for aggregation → compact episode
  table (one row per loan-month **only for the estimation window and a sampled
  set of loans**, with sampling weights recorded).
- Hazard estimation uses a stratified sample of loans (all exits + a sampled
  fraction of non-exits with offsets) when the full episode table exceeds a
  configurable row budget. The sampling design is recorded in the artifact.
- Budgets enforced in config: `max_episode_rows`, `loan_sample_fraction`,
  `chunk_rows`.

---

## 5. Deliberately deferred

- County-level analysis (needs a versioned county↔MSA crosswalk with vintage handling).
- Full structural stock-flow housing model with search frictions.
- ARM population (the dataset's ARM coverage is separate and lock-in logic differs).
- Any linkage to deeds/transaction data (not available to this project).
