# STATUS

**Last updated:** 2026-08-14 (public release pass) · config `full` — **REGISTERED data**

> ✅ **This run uses the REAL Freddie Mac Single-Family Loan-Level Dataset.**
> 36 of 37 artifacts are stamped `REGISTERED`. The loan-level estimates below are the
> first in this repository that are **evidence** rather than software verification.
>
> Two things still bind. **Tier:** loan-level results are `hazard_association`, not
> causal. **Vocabulary:** Zero Balance Code 01 pools voluntary payoff, sale-related
> payoff and maturity, so every result here is about **mortgage exits — not moves, not
> home sales.** No mobility measure exists in these data.
>
> The run is **sampled**: a 5% random sample of loans (all months of each retained),
> plus the case-cohort filter. Sized by disk, not RAM — see `DECISION_LOG` D033.
> Cohorts are 2013Q1–2022Q4, which is a documented population restriction that
> **overstates exposure** by dropping surviving high-coupon pre-2013 loans.

---

## 1. Where the project stands

The **first vertical slice is complete and passes end to end**: official sample-mode
ingestion → loan-event table → point-in-time rate gap → payment-gap calculation →
descriptive prepayment rates by lock-in bucket → discrete-time prepayment hazard →
state lock-in exposure → FHFA HPI → HMDA purchase-originations aggregate → local-market
panel → exposure event study → automatically generated research memo.

| check | state |
|---|---|
| Pipeline stages | **19 / 19 OK** (`uv run lockin status`) |
| Result artifacts | **37**, all with evidence tier + population + provenance |
| Tests | **181 passed**, 1 skipped, 0 failed |
| `ruff check` | clean |
| `ruff format --check` | clean |
| `mypy` | clean (47 source files; core interfaces strict) |
| `make validate-data` | **0 hard**, 4 soft, 13 informational (~4.5 min on the real data) |
| Generated reports | 10 |

Artifacts by tier: `descriptive` 15 · `hazard_association` 8 · `quasi_experimental` 3 ·
`simulation` 11. Data class: **all 37 REGISTERED**.

The tier mix moved sharply against the causal reading when real data arrived —
`quasi_experimental` fell from 6 to 3 — because real pre-trends fail where synthetic ones
did not. That is the system working.

Milestones M0–M13 are complete. **M13, the registered-data run, is DONE.**

**Public release is prepared.** The repository now carries canonical project metadata,
an MIT license file, and a publication map. Its compact public package contains the 101
publishable project files: source, tests, configuration, documentation, synthetic
fixtures, and aggregate reports. Registered Freddie Mac records, loan-granular
derivatives, caches, and generated output artifacts remain excluded by both the release
process and `.gitignore`. Permanent GitHub, GitHub Pages, Hugging Face Dataset, and Space
destinations are recorded in `project.yaml` and `docs/PUBLICATION.md`.

---

## 2. Data actually used

| source | real? | what was fetched |
|---|---|---|
| Freddie Mac loan-level | **YES — REGISTERED** | `full_set_standard_historical_data.zip` (40 GB, 110 cohorts 1999Q1–2026Q1), read **in place**, nothing extracted. Cohorts 2013Q1–2022Q4: **521,991,736 loan-months, 14,987,949 loans**; 4,529,704 prepayments, 7,665 credit events. Episodes sampled to 4,519,525 |
| Freddie Mac PMMS | **yes** | `PMMS_history.csv`, 2,889 weekly observations, 1971-04-02…2026-08-06 |
| FHFA HPI | **yes** | `hpi_master.csv`, 184,827 rows, 1975…2026; purchase-only, **quarterly**, State |
| HMDA (CFPB Data Browser) | **yes** | 1,530 state-year-cell aggregations, 2018…2023, 51 states × 5 measures |
| Census BPS | **yes** | 4,680 state-months, 2018-01…2023-12, `c` (preliminary) vintage |
| BLS LAUS (optional) | **yes** | 3,672 state-months, 2018-01…2023-12, seasonally adjusted unemployment rate via the public API |
| FRED `MORTGAGE30US` | **no — failed** | optional cross-check; read timeout. Non-fatal, recorded |

Loan-level schema: the shipped files are **31 / 35 fields**, while both official
documents describe **32 / 32**. The layout was established against the data, not the
documentation — see `DECISION_LOG` D031 and `lockin/schemas/variants.py`. Anchored by a
cross-file join (1,218 / 1,218 exact matches); two fields identified as *moved* by their
value domains; two positions left **undocumented and never interpreted**. No research
variable depends on an inferred or undocumented field, and a test enforces that.

**Data-quality findings in the source files**, surfaced by validation and reported rather
than silently dropped: 35 loans of 20,199,214 carry `orig_loan_term` of 481–544 months,
one loan carries a `0.0` note rate, and there are 68 within-loan month gaps in the
performance series. All are retained; none is frequent enough to indicate a parsing
fault, and severity is scaled by prevalence so a systematic break would still stop the
run (`DECISION_LOG` D035).

---

## 3. Loan-level: the headline empirical result

Discrete-time logit on the real dataset. **3,808,045 loan-months, 187,401 prepayment
events, standard errors clustered across 240,757 loans.**

| | coef | s.e. | z | hazard ratio |
|---|---|---|---|---|
| `rate_gap` | **−0.2020** | 0.0018 | −112.2 | **0.8171** |

**Each additional percentage point of rate gap multiplies the monthly prepayment hazard
by about 0.82** — roughly an 18% lower monthly exit hazard per point of lock-in.

Stable across specification and sample size: complementary log-log gives −0.1990 on the
same data, and a 15% loan sample gave −0.1993 on 11.4M loan-months.

**What this is not.** It is an association, tiered `hazard_association`. It is about
mortgage *exits*: ZB 01 does not distinguish a refinance from a sale-related payoff, so
nothing here licenses a statement about household mobility.

## 4. Market-level: real treatment, real outcomes — and the pre-trends fail

Exposure is now built from the **real** loan stock, across **51 clusters** (every state
plus DC), against 26 on fixtures.

| outcome | DiD (per 1 s.d.) | s.e. | t | pre-trend p | tier |
|---|---|---|---|---|---|
| log purchase originations | −0.0238 | 0.0109 | **−2.18** | **0.0000 FAIL** | `descriptive` |
| log refinance originations | −0.0789 | 0.0269 | **−2.93** | **0.0000 FAIL** | `descriptive` (contaminated) |
| house price growth | −0.0040 | 0.0021 | −1.96 | 0.509 | `descriptive` (circularity guard) |
| log single-family permits | −0.0264 | 0.0230 | −1.15 | 0.974 | `quasi_experimental` |
| log 5+-unit permits *(placebo)* | +0.0302 | 0.0552 | +0.55 | **0.072 FAIL** | `descriptive` |
| purchase denial rate *(placebo)* | −0.0019 | 0.0012 | −1.57 | **0.004 FAIL** | `descriptive` |

**The purchase-originations coefficient is "significant" at t = −2.18 and must not be
read causally.** Its pre-trend test fails at p < 0.0001: high- and low-exposure states
were already on diverging paths before the shock, so the parallel-trends assumption the
design rests on is violated. The tier system demoted it automatically, with no manual
override available.

Note the pattern, which is the honest summary of this layer: **the only outcome that
passes its pre-trend — single-family permits — is also the only one that is
insignificant.** Nothing at the market level supports a causal lock-in claim.

**Controls now actually bind.** `pre_hpi_growth_2019_2021` and `teleworkable_share` are
time-invariant, so as level controls they were exactly collinear with the geography fixed
effects and constrained nothing. Both now enter interacted with every period
(`DECISION_LOG` D027). House-price growth is demoted for a *stated* reason rather than a
failed test: its control is built from the same FHFA series as its outcome, so the
pre-trend test cannot fail and its result carries no information.

Robustness grid: **42 cells**. The eight added in the second pass — alternative
pre-shock dates (2021-06, 2022-03), alternative rate series (`pmms15`), alternative HPI
concepts (all-transactions, expanded-data), and three loan sub-samples — are all
insignificant. **The null is robust** to the pre-shock date, the rate series, the price
index concept, and the loan population.

**Loan-level sensitivity (new).** Three modelling choices each move the hazard
coefficient by more than one standard error, so they must be stated whenever a magnitude
is quoted:

| cell | coefficient | vs baseline (−0.5248) |
|---|---|---|
| ZB 15/16/96 counted as prepayment | −0.4353 | `large_shift` |
| fresh-term payment gap | −1.4727 (vs −3.2629) | `large_shift` |
| month-end rate alignment | −0.4896 | `moderate_shift` |

---

## 5. Design errors found and fixed this run

All five were caught by **automated diagnostics**, not inspection, and each is now a
permanent assertion. Full detail in `docs/DECISION_LOG.md` D016–D023 and
`reports/failed_hypotheses.md` §3.

1. **Zero-variance treatment** (D016). Exposure measured as the *contemporaneous*
   locked-in share at 2021-12 was exactly 0 in every state — correctly, since nobody
   was locked in yet. Replaced with the true shift-share form.
2. **HMDA API silently dropped a filter, and it changed the headline** (D017, D020).
   The parameter is `loan_purposes`, plural; the singular was ignored and returned
   all-purpose totals, making purchase and refi counts identical. The purchase
   coefficient moved from −0.026 to +0.001 once fixed. Now asserted on fetch *and*
   cache read, with versioned cache keys.
3. **Covariates corrupted in exactly the event months** (D018). `Current Actual UPB`
   is end-of-period and 0 in a payoff month, zeroing the payment gap for every event.
   All measures now use a start-of-month balance.
4. **No pre-periods** (D019). The annual panel spanned only stock years, making
   pre-trends untestable and auto-demoting everything. Now spans outcome years.
5. **Rank-deficient design matrix** (D021). Three empty loan-age bins made the
   cluster-robust sandwich singular, silently downgrading standard errors to
   conventional. Zero-variance columns are now dropped and recorded.

Plus: FHFA does not publish monthly purchase-only HPI at state level (D009), so the
original config matched zero rows; `load_series` now raises with the published
combinations.

**Second pass (D024–D026)** closed three documentation-vs-implementation gaps: the
committed layout YAML did not exist, `make reproduce-sample` omitted `robustness`, and
three comments claimed sensitivity checks that had never been run. All three are now
real, and the sensitivity results turned out to be fragilities worth reporting.

---

## 6. What is NOT established

1. **Anything about household mobility.** ZB 01 pools refinancing, sale-related payoff,
   and maturity. No mobility measure exists in these data.
2. **Anything about home sales or listings.** No sale indicator, no listings source.
3. **The aggregate effect of the rate increase.** Absorbed by time fixed effects; only
   relative effects across exposure are identified.
4. **A demand/supply decomposition.** Framed, not achieved — no listings data, no
   transaction records, no household panel.
5. **Any behaviour of FHA/VA, jumbo, non-QM, portfolio, or all-cash segments.**
6. **Any forecast.** The scenario module is explicitly not one.
7. **Exposure exogeneity.** Predetermined ≠ exogenous. No IV language is used.

Identification-threat status is now recorded **on every event-study artifact** under
`identification_threats`:

| threat | status |
|---|---|
| pandemic demand reallocation | CONTROLLED (2019–21 price growth) |
| local labour shocks | **CONTROLLED** (BLS LAUS unemployment rate) |
| pandemic demand reallocation | **CONTROLLED** — but only since D027; it was a *level* control before, i.e. collinear with geography FE and binding nothing |
| differential refinancing booms | PARTIAL (outcome labeled contaminated; control available) |
| national monetary-policy endogeneity | ABSORBED by period fixed effects |
| remote-work exposure | **CONTROLLED** — Dingel–Neiman teleworkable share, as a *trend* control (feasibility, not realisation) |
| geography-specific rate dispersion | **UNCONTROLLED** — PMMS is national |
| spillovers | UNCORRECTED — biases toward zero |

---

## 7. Done, and what it cost

**M13 — registered-data run: COMPLETE.** The archive is read in place from
`data/raw/full_set_standard_historical_data.zip`; nothing is extracted, because the
archive is 40 GB and the machine had 33 GB free at the start.

Three defects only a real run could expose, all fixed (`DECISION_LOG` D032, D033):
a global sort over 522M rows that spilled ~20 GB and nearly filled the disk; an episode
table whose dense design matrix was 21.7 GB against 17 GB of RAM; and `data/interim`
being shared across run profiles, so a `sample` command silently read `full` data and
stamped the output `SYNTHETIC`.

## 8. Where MSA-level analysis landed, and what is left

**MSA-level analysis now runs end to end on registered data: 138 metropolitan CBSAs
against 51 states.** Every source resolves natively at CBSA level, so the multi-state
allocation rule once flagged as the blocker is moot.

| outcome | state (51 clus.) | MSA (138 clus.) | MSA pre-trend | tier |
|---|---|---|---|---|
| log purchase originations | −0.024 (t −2.18) | **−0.042 (t −4.93)** | **0.036 FAIL** | `descriptive` |
| log refinance originations | −0.079 (t −2.93) | −0.124 (t −4.68) | **0.000 FAIL** | `descriptive` |
| log single-family permits | −0.011 (t −0.53) | −0.074 (t −1.61) | 0.636 pass | `quasi_experimental` |
| log 5+-unit permits *(placebo)* | −0.037 | +0.029 (t +0.37) | 0.940 pass | passes |
| purchase denial rate *(placebo)* | −0.002 (t −1.57) | −0.001 (t −0.93) | 0.297 pass | passes |

**Eight times the clusters bought precision, not identification.** The
purchase-originations t-statistic more than doubles and the pre-trend still fails, so the
estimate is still `descriptive`. That the same failure appears at two very different
levels of aggregation says it is not an artifact of pooling states. Both placebos pass at
metro level, which is the expected direction and mild evidence the state-level placebo
failure was a small-cluster artifact.

**What limits the MSA run today**

1. **Loan sampling, not geography.** `min_loans_per_geography` counts *sampled* loans, so
   a nominal 100 at `loan_sample_fraction=0.05` is ~2,000 real loans — which is what
   takes the panel from 395 resolvable metros to 138. Lifting the sampling lifts the
   count; the stock manifest records the arithmetic.
2. **FHFA covers 65 of 138 metros** with a purchase-only quarterly index, so the
   house-price row rests on half the panel.
3. **BLS daily request threshold** was reached, so metro LAUS holds only 2021–2022 and
   the cached-coverage guard reports the labour control UNAVAILABLE rather than serving
   a short panel. It clears on its own.
4. **No monthly MSA outcome**, so the monthly specifications skip.

**Still open beyond MSA**

- **Local market-rate series** from HMDA-reported interest rates (2018+), to reduce the
  measurement error the national PMMS introduces into the loan-level gap.
- **Stable-servicer robustness sample** — now *possible* where it was not: servicer name
  moved into the performance file in the shipped layout (D031), so it varies over the
  life of a loan and a stable-servicer subsample can be constructed. Servicers below 1%
  of quarterly UPB are still collapsed to "Other".
- **Stock-flow housing model** with search frictions.
- **County level**, which needs a ZIP-to-county path the loan file does not provide.

## 9. Reproducing this

```bash
make setup && make fetch-public-data && make reproduce-sample && make test
```

Every artifact records git commit, config digest, data period, and per-source
`schema@retrieved#checksum`. Every dataset on disk carries a manifest with a SHA-256
checksum that `make validate-data` re-verifies. See `reports/replication_protocol.md`
for the reviewer checklist and the list of fragile steps.

The one non-deterministic input is the **public data vintage** — PMMS, FHFA HPI, and
Census BPS are revised and HMDA is re-released, so a later rerun fetches newer data.
The manifests record which vintage produced any given number.
