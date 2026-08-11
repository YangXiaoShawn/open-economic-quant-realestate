# STATUS

**Last updated:** 2026-08-11 (second pass) · config `sample`

> ⚠️ **The loan-level results in this run are SYNTHETIC.** The Freddie Mac
> Single-Family Loan-Level Dataset requires registration, which this repository does
> not bypass. Loan-level numbers below come from labeled synthetic fixtures and are
> **software verification, not empirical findings**. The public aggregate series
> (PMMS, FHFA HPI, HMDA, Census BPS) **are real**.

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
| Tests | **110 passed** (109 offline + 1 network), 0 failed |
| `ruff check` | clean |
| `ruff format --check` | clean |
| `mypy` | clean (42 source files; core interfaces strict) |
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

## 4. What the market-level slice shows (REAL public data)

Exposure is the frozen 2021-12 coupon distribution evaluated at the later national rate
path, across 26 states: mean 0.845, s.d. 0.031. It is **not** randomly assigned —
correlation −0.38 with 2019–21 price growth and −0.73 with the pre-shock coupon level.

| outcome | DiD (per 1 s.d.) | s.e. | t | pre-trend p | tier |
|---|---|---|---|---|---|
| log purchase originations | **+0.0010** | 0.0191 | +0.05 | 0.342 | `quasi_experimental` |
| log refinance originations | −0.0579 | 0.0657 | −0.88 | 0.364 | `quasi_experimental` (contaminated) |
| house price growth | +0.0050 | 0.0041 | +1.23 | **0.003 FAIL** | `descriptive` (demoted) |
| log single-family permits | −0.0276 | 0.0169 | −1.63 | **0.007 FAIL** | `descriptive` (demoted) |
| log 5+-unit permits *(placebo)* | −0.0618 | 0.0452 | −1.37 | 0.353 | placebo passes |
| purchase denial rate *(placebo)* | −0.0014 | 0.0007 | −1.90 | 0.629 | **placebo FAILS** |

**The headline is a null.** The purchase-originations estimate is indistinguishable
from zero. With 26 clusters, annual HMDA data, and time fixed effects absorbing the
common national shock, this design has limited power to detect a cross-state
differential — so this is a null result, not a refutation of lock-in, and not
suggestive evidence for it either.

**A placebo outcome fails.** The denial rate moves at t = −1.90. That counts *against*
the design and is flagged `placebo_FAIL` in the robustness grid.

Robustness grid: **42 cells**. The eight added in the second pass — alternative
pre-shock dates (2021-06, 2022-03), alternative rate series (`pmms15`), alternative HPI
concepts (all-transactions, expanded-data), and three loan sub-samples — are all
insignificant. **The null is robust** to the pre-shock date, the rate series, the price
index concept, and the loan population.

Adding the unemployment control moved the headline from +0.0010 to +0.0007. The null is
not an artifact of omitting labour-market conditions.

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
| differential refinancing booms | PARTIAL (outcome labeled contaminated; control available) |
| national monetary-policy endogeneity | ABSORBED by period fixed effects |
| remote-work exposure | **UNCONTROLLED** — no teleworkable-share adapter |
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
2. **MSA-level analysis.** Needs a versioned OMB delineation crosswalk — Freddie's MSA
   field is explicitly *not* updated for redefinitions. This is the single highest-value
   fix for statistical power (26 clusters → several hundred).
3. **Local market-rate series** from HMDA-reported interest rates (2018+), to reduce the
   measurement error the national PMMS introduces into the loan-level gap.
4. **Teleworkable-share control**, the remaining unresolved threat (unemployment is
   now controlled via BLS LAUS). Also a **stable-servicer** robustness sample, which is
   currently not constructible because servicer names below 1% of quarterly UPB are
   collapsed to "Other".
5. **Stock-flow housing model** with search frictions — deliberately deferred until the
   vertical slice was stable, which it now is.
6. **County level**, after (2).

---

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
