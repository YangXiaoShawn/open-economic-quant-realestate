# STATUS

**Last updated:** 2026-08-13 (third pass) · config `sample`

> ⚠️ **Every estimate in this run is SYNTHETIC, including the market-level ones.** The
> Freddie Mac Single-Family Loan-Level Dataset requires registration, which this
> repository does not bypass. The loan stock is the only source of a local coupon
> distribution, so **lock-in exposure — the treatment variable in every specification —
> is synthetic**, even where the outcome series are real. Nothing here is an empirical
> finding about U.S. housing. What is verified is the machinery: parsers, joins, event
> rules, estimators, pre-trend logic, and the automatic tier demotion.
>
> The public aggregate series themselves (PMMS, FHFA HPI, HMDA, Census BPS, BLS LAUS,
> OMB delineations, Dingel–Neiman) **are real** and are fetched live.

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
| Tests | **144 passed** (143 offline + 1 network), 0 failed |
| `ruff check` | clean |
| `ruff format --check` | clean |
| `mypy` | clean (45 source files; core interfaces strict) |
| `make validate-data` | **0 hard**, 0 soft, 11 informational |
| Generated reports | 10 |

Artifacts by tier: `descriptive` 12 · `hazard_association` 8 · `quasi_experimental` 6 ·
`simulation` 11.

Milestones M0–M12 are complete. **M13 (full registered-data run) is BLOCKED** on user
registration — see §7.

---

## 2. Data actually used

| source | real? | what was fetched |
|---|---|---|
| Freddie Mac loan-level | **no — SYNTHETIC** | 10,500 synthetic loans across cohorts 2019Q2 / 2020Q4 / 2021Q4; 368,341 loan-months over 2021-01…2024-12; seed `20260810` |
| Freddie Mac PMMS | **yes** | `PMMS_history.csv`, 2,889 weekly observations, 1971-04-02…2026-08-06 |
| FHFA HPI | **yes** | `hpi_master.csv`, 184,827 rows, 1975…2026; purchase-only, **quarterly**, State |
| HMDA (CFPB Data Browser) | **yes** | 1,530 state-year-cell aggregations, 2018…2023, 51 states × 5 measures |
| Census BPS | **yes** | 4,680 state-months, 2018-01…2023-12, `c` (preliminary) vintage |
| BLS LAUS (optional) | **yes** | 3,672 state-months, 2018-01…2023-12, seasonally adjusted unemployment rate via the public API |
| FRED `MORTGAGE30US` | **no — failed** | optional cross-check; read timeout. Non-fatal, recorded |

Loan-level schema **verified** against the public `file_layout.xlsx` and
`user_guide.pdf`: 32 origination + 32 monthly performance fields, and the official
Zero Balance Code termination-event priority table.

---

## 3. What the loan-level slice verifies (SYNTHETIC — not findings)

The synthetic DGP sets a **known** coefficient of `0.55` on the refinance incentive.
The discrete-time logit recovers **−0.5248** (s.e. 0.0198, clustered across 10,320
loans) on the rate gap — the same parameter with the opposite sign convention. **This
is a successful software test of the estimator, not a fact about U.S. mortgages.**

- Events: 2,381 prepayments · 104 credit events · 8,015 censored, of 10,500 loans.
- Left truncation: 8,551 loans (81.4%) enter at loan age > 1. Validation separates the
  two causes — Freddie Mac acquisition lag versus the configured performance window.
- Monotone hazard gradient across all eight rate-gap buckets (0.0377 → 0.0013 monthly),
  which is the qualitative shape the prepayment literature reports.
- Cox concordance 0.634. The PH diagnostic reports a large violation for
  `rate_gap_at_entry` (p < 0.0001) — expected, and the reason the discrete-time rungs
  with a *time-varying* gap are preferred.
- Out-of-time AUC: gradient boosting 0.599 vs discrete-time logit 0.613. The
  interpretable specification is not leaving predictive signal on the table.

## 4. The market-level slice — SYNTHETIC TREATMENT, real outcomes

> **Read this before any number below.** The *outcomes* (HMDA, FHFA HPI, Census BPS, BLS
> LAUS) are real. The **treatment is not**: lock-in exposure is computed from the
> synthetic loan fixtures, because the Freddie Mac stock is the only source of a local
> coupon distribution and it is behind registration. A difference-in-differences with a
> synthetic treatment variable is **not an empirical finding of any kind** — it is a test
> that the estimator, the joins, the pre-trend logic, and the tier demotion all work.
> Earlier versions of this file headed this section "REAL public data", which was
> misleading, and the artifacts themselves were always stamped `SYNTHETIC`.

**What this run does demonstrate, and it is worth stating.** Regenerating the fixtures
changed only the random assignment of loans to geographies. The purchase-originations
estimate moved from +0.024 to −0.003, and the **denial-rate placebo went from t = −1.00
to t = −2.78** — a "significant" placebo effect from a treatment that is, by
construction, arbitrary noise. With 26 clusters and annual data, this design produces
|t| > 2.5 on a placebo from reshuffling alone. That is the most useful thing the
synthetic market-level slice has produced: a direct measurement of how little the
cluster count supports.

Exposure is the frozen 2021-12 coupon distribution evaluated at the later national rate
path, across 25 states: mean 0.860, s.d. 0.027, range 0.787–0.917.

| outcome | DiD (per 1 s.d.) | s.e. | t | pre-trend p | tier |
|---|---|---|---|---|---|
| log purchase originations | **−0.0031** | 0.0185 | −0.17 | 0.547 | `quasi_experimental` |
| log refinance originations | +0.0228 | 0.0531 | +0.43 | 0.185 | `quasi_experimental` (contaminated) |
| house price growth | +0.0025 | 0.0041 | +0.62 | 0.150 | `descriptive` (circularity guard) |
| log single-family permits | −0.0108 | 0.0203 | −0.53 | 0.842 | `quasi_experimental` |
| log 5+-unit permits *(placebo)* | −0.0372 | 0.0572 | −0.65 | **0.057 FAIL** | `descriptive` |
| purchase denial rate *(placebo)* | −0.0023 | 0.0008 | **−2.78** | 0.840 | **placebo FAILS** |

**The headline is a null, and the placebo fails.** Neither is evidence about U.S.
housing, because the treatment is synthetic. Both are evidence about the *design*: a
26-cluster annual panel cannot distinguish a real effect from a reshuffle.

**Controls now actually bind.** Two predetermined characteristics —
`pre_hpi_growth_2019_2021` and the new `teleworkable_share` — are time-invariant, so as
level controls they were exactly collinear with the geography fixed effects and
constrained nothing. Both now enter interacted with every period. `DECISION_LOG` D027
decomposes the effect on the headline across five specifications; the null survived all
five in the pre-regeneration fixtures.

**House-price growth is demoted for a stated reason,** not a failed test. Its pre-trend
control is built from the same FHFA series as its outcome, so the test cannot fail;
`_circular_trend_controls` blocks promotion rather than accepting a p-value of 0.85 as
reassurance.

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

## 7. Blocked, and what unblocks it

**M13 — full registered-data run.** Requires the user to register at the Freddie Mac
Single-Family Loan-Level Dataset page and accept the terms of use. This repository
will not bypass a registration or licence wall, and the terms prohibit redistributing
the records.

To proceed: follow `data/DATA_ACCESS.md` §R1, drop the archives in `data/raw/freddie/`,
set `mortgage.mode: registered_sample` (or `registered_full`), and rerun.
**No code changes are needed** — the adapter discovers
`historical_data_YYYYQn.zip` / `sample_YYYY.zip` automatically, reads members without
full extraction, and the `SYNTHETIC` stamps and report banners disappear on their own.

---

## 8. Next milestones, in priority order

1. **Registered-data run** (blocked above). Everything downstream is already wired.
   This is the only step that turns any number in this repository into evidence.

2. **Finish MSA-level analysis.** The hard part is done: `lockin.adapters.omb_cbsa`
   builds a six-vintage crosswalk and a per-code stability verdict, the fixtures now
   carry real composition-stable CBSA codes, `configs/msa.yaml` exists, the active-stock
   path is geography-keyed, and FHFA HPI resolves at MSA level. **215 metropolitan CBSAs
   are stable and usable — an 8× gain over 26 state clusters.**

   What remains is three outcome adapters that are still state-only. The panel builder
   now **refuses** to join them at MSA level rather than silently producing a
   wrong-geography panel (`GeographyMismatchError`), which is how the first attempt at
   this failed: it built a plausible-looking 182-row panel with MSA exposure joined onto
   state outcomes and teleworkable matching 0 of 182 rows, without raising anything.

   | source | MSA support needed |
   |---|---|
   | HMDA | CFPB Data Browser accepts `msamds`; adapter passes `states` only |
   | Census BPS | MSA files exist under a different path and layout |
   | BLS LAUS | metro series exist (`LAUMT…`); adapter builds state IDs only |

   Two design questions to settle when doing it: **47 of the 430 metro CBSAs span more than one
   state**, so any state-published control needs an allocation rule; and Freddie's MSA
   field is null for ~15% of loans (non-metro), which must be dropped, never imputed —
   there is no ZIP→CBSA path in the loan file.

3. **Local market-rate series** from HMDA-reported interest rates (2018+), to reduce the
   measurement error the national PMMS introduces into the loan-level gap.

4. **Stable-servicer robustness sample** — still not constructible as intended, because
   servicer names below 1% of quarterly UPB are collapsed to "Other".

5. **Stock-flow housing model** with search frictions.

6. **County level**, after (2).

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
