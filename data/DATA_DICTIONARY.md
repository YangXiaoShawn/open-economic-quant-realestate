# Data Dictionary

Every table this project builds, and every field in it. Source-file field
definitions are transcribed from official documentation; derived fields state their
formula and their limitation.

Vocabulary rules that govern this whole document: `AGENTS.md` §1.

---

## 1. `data/interim/origination/` — parsed origination records

Partitioned `cohort=YYYYQn/part-*.parquet`. One row per loan. Field positions,
names, types, and sentinel "Not Available" codes are transcribed from the **public**
`file_layout.xlsx` and `user_guide.pdf` (32 fields; see
`src/lockin/schemas/freddie.py`).

| # | field | type | sentinel → null | notes |
|---|---|---|---|---|
| 1 | `credit_score` | Int64 | `9999` | 301–850 valid |
| 2 | `first_payment_date` | Date | | `YYYYMM` → first of month |
| 3 | `first_time_homebuyer_flag` | Utf8 | `9` | `Y`/`N`. **Not populated for refinance loans** |
| 4 | `maturity_date` | Date | | |
| 5 | `msa_code` | Utf8 | blank, `00000` | **NOT updated for changing OMB delineations** — MSA analysis needs a versioned crosswalk |
| 6 | `mi_percent` | Int64 | `999` | 1–55 valid; `0` = no MI |
| 7 | `num_units` | Int64 | `99` | 1–4 |
| 8 | `occupancy_status` | Utf8 | `9` | `P` primary, `I` investment, `S` second home |
| 9 | `orig_cltv` | Int64 | `999` | set to NA when CLTV < LTV; ranges differ pre/post 2018Q2 |
| 10 | `orig_dti` | Int64 | `999` | **>65% and all HARP loans are reported as Not Available** |
| 11 | `orig_upb` | Float64 | | rounded to the nearest $1,000 |
| 12 | `orig_ltv` | Int64 | `999` | 6–105 (≤2018Q1) / 1–998 (≥2018Q2) |
| 13 | `orig_interest_rate` | Float64 | | percent, e.g. `6.875` |
| 14 | `channel` | Utf8 | `9` | `R` retail, `B` broker, `C` correspondent, `T` TPO unspecified |
| 15 | `ppm_flag` | Utf8 | | prepayment-penalty mortgage |
| 16 | `amortization_type` | Utf8 | | `FRM` / `ARM` |
| 17 | `property_state` | Utf8 | | two-letter abbreviation — **the default geography key** |
| 18 | `property_type` | Utf8 | `99` | `SF`, `CO`, `PU`, `MH`, `CP` |
| 19 | `postal_code` | Utf8 | blank, `00000` | **first three ZIP digits + `00`.** Not a full ZIP; cannot identify a property |
| 20 | `loan_seq_no` | Utf8 | | `PYYQnXXXXXXX`. The join key |
| 21 | `loan_purpose` | Utf8 | `9` | `P` purchase, `C` cash-out refi, `N` no-cash-out refi, `R` refi unspecified |
| 22 | `orig_loan_term` | Int64 | | (maturity − first payment) + 1, months |
| 23 | `num_borrowers` | Int64 | `99` | `02` semantics differ pre/post 2018Q2 |
| 24 | `seller_name` | Utf8 | | **collapsed to `Other sellers`** below 1% of quarterly original UPB |
| 25 | `servicer_name` | Utf8 | | **collapsed to `Other servicers`** below 1% of quarterly original UPB |
| 26 | `super_conforming_flag` | Utf8 | blank | `Y` = exceeds conforming limits |
| 27 | `pre_relief_refi_loan_seq_no` | Utf8 | blank | **Relief Refinance / HARP chains only** — not ordinary refinancing (D005) |
| 28 | `special_eligibility_program` | Utf8 | `9` | `H` Home Possible, `F` HFA Advantage, `R` Refi Possible |
| 29 | `relief_refi_indicator` | Utf8 | blank | `Y`; with orig LTV > 80 these are HARP loans |
| 30 | `property_valuation_method` | Int64 | `7` | `1` appraisal waiver, `2` appraisal, `3` other, `4` ACE+PDR |
| 31 | `interest_only_indicator` | Utf8 | | |
| 32 | `mi_cancellation_indicator` | Utf8 | `7`, blank | |

Derived on ingest:

| field | formula | limitation |
|---|---|---|
| `cohort` | from the source filename | |
| `seq_product`, `seq_orig_year`, `seq_orig_quarter` | parsed from `loan_seq_no` | kept so a mismatch with the file cohort is visible rather than silent |
| `approx_origination_date` | `first_payment_date − 2 months` | **an approximation.** Origination date is not a field |
| `geography_state` | alias of `property_state` | |

---

## 2. `data/interim/performance/` — parsed monthly performance

Partitioned `cohort=YYYYQn/period_year=YYYY/part-*.parquet`. One row per observed
loan-month. **The loss/expense block (official fields 14–23, 27, 28, 31) is
projected away at parse time** — loss-severity analysis is out of scope and dropping
it roughly halves the footprint.

| # | field | type | notes |
|---|---|---|---|
| 1 | `loan_seq_no` | Utf8 | join key |
| 2 | `monthly_reporting_period` | Date | Combines the **current** month's accounting cycle for performing loans with the **previous** calendar month's default reporting for non-performing loans. Accounting cycle was 16th-to-15th through 2019-04, calendar month from 2019-05 |
| 3 | `current_upb` | Float64 | **END-of-period balance; 0 in a zero-balance month.** Do not use it for a within-month decision — see `upb_start_of_month` (D018) |
| 4 | `delinquency_status` | Utf8 | `0` current, `1` 30–59d, `2` 60–89d, …, `RA` REO acquisition |
| 5 | `loan_age` | Int64 | scheduled payments since origination — **resets on MODIFICATION** (not on a payment deferral) |
| 6 | `remaining_months_to_maturity` | Int64 | uses the modified maturity date for modified loans |
| 7 | `defect_settlement_date` | Date | representation-and-warranty resolution |
| 8 | `modification_flag` | Utf8 | `Y` current period, `P` prior period |
| 9 | `zero_balance_code` | Utf8 | see §3. **Set at most once per loan** |
| 10 | `zero_balance_effective_date` | Date | the period of the triggering event |
| 11 | `current_interest_rate` | Float64 | reflects modifications |
| 12 | `current_deferred_upb` | Float64 | the guide calls this "Current Non-Interest Bearing UPB" |
| 13 | `ddlpi` | Date | due date of last paid installment |
| 24 | `step_modification_flag` | Utf8 | |
| 25 | `deferred_payment_plan` | Utf8 | |
| 26 | `reported_eltv` | Int64 | **Freddie Mac's own** estimated LTV; populated only for a subset of loan-periods |
| 29 | `delinquency_due_to_disaster` | Utf8 | |
| 30 | `borrower_assistance_status` | Utf8 | `F` forbearance, `R` repayment, `T` trial |
| 32 | `interest_bearing_upb` | Float64 | |

---

## 3. Zero Balance Codes — the official termination-event priority table

From `user_guide.pdf`, "Zero Balance Codes". Priority 1 is highest: when two
termination events fall in the same reporting period, the higher-ranking one is
reported.

| ZB | official label | priority | our event class | censored? | why |
|---|---|---|---|---|---|
| `15` | Whole Loan Sale | 1 | `admin_removal` | **yes** | Freddie Mac portfolio action, not a borrower decision |
| `16` | Reperforming loan securitizations | 2 | `admin_removal` | **yes** | portfolio action |
| `09` | REO Disposition | 3 | `credit_event` | no | terminal credit outcome |
| `96` | Defect prior to other termination event | 4 | `admin_removal` | **yes** | repurchase / indemnification / make-whole |
| `03` | Short Sale or Charge Off | 5 | `credit_event` | no | terminal credit outcome |
| `02` | Third Party Sale | 6 | `credit_event` | no | **foreclosure-auction sale — a CREDIT outcome, not a household move** |
| `01` | Prepaid or Matured (Voluntary Payoff) | 7 | `prepayment` | no | **CONFLATES voluntary payoff with scheduled maturity, and does NOT distinguish refinance from sale-related payoff** |

An unset code means the loan was active at the performance cutoff → right
censoring. An **undocumented** code is censored, never guessed at.

---

## 4. `data/processed/loan_events.parquet` — one row per loan

| field | type | definition |
|---|---|---|
| `loan_seq_no` | Utf8 | |
| `entry_date` | Date | `first_payment_date` — the time origin for loan age |
| `observation_start` | Date | first observed performance month. Begins at Freddie Mac **acquisition**, not origination, and is further truncated by the configured performance window |
| `observation_end` | Date | exit month, or the last observed month (right censoring) |
| `start_age`, `end_age` | Int64 | loan age at the observation bounds. `start_age > 1` ⇒ **left truncated** |
| `event_type` | Utf8 | `prepayment` \| `credit_event` \| `censored`. **No `home_sale`, no `household_move`, ever** |
| `event_date` | Date | `zero_balance_effective_date`, falling back to the reporting period |
| `censored` | Boolean | |
| `censoring_reason` | Utf8 | `active_at_performance_cutoff`, `admin_removal_zb15_…`, `undocumented_zb_code` |
| `zero_balance_code`, `zero_balance_label` | Utf8 | |
| `n_months_observed` | UInt32 | |
| `n_month_gaps` | Int64 | span minus observed months. Gap months contribute **no risk time** |
| `ever_modified` | Boolean | `modification_flag` in `{Y, P}` at any point |
| `modification_reset` | Boolean | loan age decreased — expected only across a modification |
| `conflicting_zb_codes` | Boolean | more than one ZB code; resolved by the priority table |
| `reappeared_after_exit` | Boolean | performance months after the exit; truncated at the exit |
| `home_sale_observed` | Boolean | **always `False`.** A validator makes it a hard error if it is ever `True` |
| `ever_30d_plus` | Boolean | |
| *(origination fields)* | | carried through from §1 |

---

## 5. `data/processed/loan_episodes/` — one row per observed loan-month

The estimation dataset. Partitioned by `period_year`.

| field | type | definition / formula | limitation |
|---|---|---|---|
| `period` | Date | monthly reporting period | |
| `loan_age` | Int64 | duration-model time index | resets on modification |
| `at_risk` | Int8 | always 1 for an observed month | |
| `exit_prepayment` | Int8 | 1 **only** in the exit month, if `event_type == prepayment` | |
| `exit_credit_event` | Int8 | 1 only in the exit month, if `credit_event` | |
| `exit_any`, `censored_this_month` | Int8 | | |
| `current_upb` | Float64 | as reported (end of period) | 0 in a zero-balance month |
| `upb_start_of_month` | Float64 | prior month's reported UPB → same-month UPB if positive → scheduled amortised balance | **the balance used by every lock-in measure** (D018) |
| `upb_timing_source` | Utf8 | which of the three above was used | |
| `remaining_term` | Float64 | reported remaining months, else `orig_loan_term − loan_age`, floored at 1 | |
| `note_rate` | Float64 | `orig_interest_rate` | |
| `market_rate` | Float64 | PMMS, **point-in-time**: last observation on or before the 1st of `period` | national only — local offered rates differ by tens of bp |
| `rate_series`, `methodology_regime` | Utf8 | which PMMS series and methodology regime | not spliced silently |
| `rate_gap` | Float64 | `market_rate − note_rate`. **Positive ⇒ locked in** | |
| `lockin_gap` | Float64 | `max(rate_gap, 0)` | |
| `refi_incentive` | Float64 | `note_rate − market_rate`. Positive ⇒ refinancing pays | ignores closing costs, eligibility, option value |
| `payment_gap` | Float64 | `PMT(upb_start, market, rem) − PMT(upb_start, note, rem)`, $/month | holds the remaining term fixed |
| `pv_financing_gap` | Float64 | `payment_gap × a(min(H, rem), δ)` | **H and δ are CALIBRATED**, not estimated |
| `gap_bucket` | Int64 | 0–7, edges at −200/−100/0/+100/+200/+300/+400 bp | |
| `est_current_ltv` | Float64 | reported ELTV → `orig_ltv × (upb/orig_upb) × (hpi_orig/hpi_t)` → balance-only | **a STATE index is a poor proxy for one property** |
| `ltv_source` | Utf8 | which of the three above was used | |
| `hpi_growth_12m` | Float64 | 12-month log change in the state purchase-only index | quarterly index expanded to monthly |
| *(loan characteristics)* | | state, MSA, purpose, occupancy, property type, FICO, DTI, LTV, UPB, term, FTHB flag, cohort year | |

---

## 6. `data/processed/active_stock.parquet` — geography-month

One row per geography-month. **Both weighting schemes are always present.**

| field | definition |
|---|---|
| `geography`, `period` | key |
| `n_active_loans`, `total_upb` | size of the observed stock |
| `wavg_note_rate_count`, `wavg_note_rate_upb` | weighted-average coupon, both weightings |
| `market_rate` | point-in-time PMMS for the month |
| `locked_share_count_{100,200,300,400}` | loan-count-weighted share above the bp threshold |
| `locked_share_upb_{100,200,300,400}` | UPB-weighted share |
| `mean_rate_gap`, `mean_lockin_gap` | |
| `median_payment_gap`, `mean_payment_gap`, `median_pv_financing_gap` | $/month and $ |
| `refi_incentive_share` | share with refi incentive above the configured bp cut |
| `prepayment_rate_monthly`, `credit_event_rate_monthly` | realised monthly rates |
| `n_prepayments`, `n_credit_events` | counts |
| `median_est_current_ltv`, `mean_credit_score`, `mean_orig_cohort_year` | composition |
| `share_purchase_loans`, `share_refi_loans` | loan-purpose composition |
| `share_primary_residence`, `share_investment` | occupancy composition |
| `note_rate_p10 … note_rate_p90` | coupon distribution deciles |

**Coverage caveat carried in the manifest.** The stock has both entry (later cohorts
phasing into the performance window) and exit, so a first-to-last change nets the
two and can be positive. Attrition is therefore measured **from the peak**.

`active_stock_cohort_mix.parquet` gives geography × period × origination-year counts
and UPB.

---

## 7. Predetermined exposure (the event-study treatment)

Computed by `lockin.stock.predetermined_exposure`, frozen at
`event_study.pre_shock_date` and **never recomputed**. Prefixed `pre_` in the panel.

$$E_g = \sum_k \omega_{gk}^{\text{pre}} \cdot \mathbf 1\{\bar R^{\text{post}} - r_k > \tau\}$$

| field | definition |
|---|---|
| `pre_locked_share_count_{τ}` | share of the frozen stock whose counterfactual gap at the post-shock rate exceeds τ bp, loan-count-weighted |
| `pre_locked_share_upb_{τ}` | the same, UPB-weighted (**the default treatment**) |
| `pre_coupon_share_below_{τ}` | share of loans with a note rate below `R_post − τ/100`. A pure coupon-share measure with no payment assumption |
| `pre_mean_payment_gap`, `pre_median_payment_gap` | counterfactual payment gap at the post-shock rate, $/month |
| `pre_mean_rate_gap`, `pre_mean_lockin_gap` | pp |
| `pre_wavg_note_rate_upb`, `pre_wavg_note_rate_count` | pre-shock coupon level |
| `pre_note_rate_p10 … p90` | the frozen coupon distribution itself |
| `pre_coupon_share_hhi` | Herfindahl of coupon-bin shares — a shift-share concentration diagnostic |
| `pre_n_active_loans`, `pre_total_upb` | **coverage variables**, not market size |
| `exposure_as_of`, `exposure_post_rate_pct` | provenance of the measure |

**Why not the contemporaneous locked share at the pre-shock date?** It is ≈ 0
everywhere: the pre-shock market rate was near its historic low, so nobody was
locked in *yet*. See `docs/DECISION_LOG.md` D016.

---

## 8. `data/processed/local_market_panel_{monthly,annual}.parquet`

| field | source | definition | limitation |
|---|---|---|---|
| `geography`, `period` / `year` | | key | |
| `has_stock_data` | derived | whether loan-level aggregates exist for this row | false in pre-shock years |
| `hpi`, `hpi_year_start`, `hpi_year_end` | FHFA | purchase-only state index level | **an INDEX, not a property value** |
| `hpi_growth_period`, `hpi_growth_12m` | FHFA | log differences at the **published** frequency | quarterly at state level |
| `hpi_growth` | FHFA | `log(hpi_year_end) − log(hpi_year_start)` | computed from published levels, not from expanded monthly steps |
| `permits_total_units`, `permits_1unit`, `permits_2to4unit`, `permits_5plus` | Census BPS | units **AUTHORIZED** | not starts, not completions; `c` vintage is preliminary; partial years dropped |
| `n_purchase_originations`, `n_refi_originations` | HMDA | loans **originated** (action taken 1) | applications and originations, **not a property-sales registry**; all-cash absent |
| `n_purchase_applications`, `n_purchase_denials`, `n_refi_applications` | HMDA | | |
| `usd_*` | HMDA | dollar sums | |
| `denial_rate` | derived | `n_purchase_denials / n_purchase_applications` | a credit-conditions proxy; used as a **placebo** outcome |
| `log_*` | derived | natural logs of the count outcomes | |
| `coverage_regime` | HMDA | `post_2018_rule` \| `threshold_100_closed_end` | **counts are NOT comparable across regimes** |
| `pre_hpi_growth_2019_2021` | FHFA | cumulative log price growth 2019-01…2021-12 | the pandemic-boom control |
| `pre_refi_count_2020_2021` | HMDA | 2020+2021 refinance originations | the refi-intensity control |
| `pre_*` | derived | frozen exposure (§7) | |
| *(stock aggregates)* | | annual means/sums of §6 | null before the performance window |

---

## 9. Result artifacts — `outputs/<group>/<name>.json`

Fixed envelope on every artifact:

| field | meaning |
|---|---|
| `artifact`, `group` | identity |
| `evidence_tier` | `descriptive` \| `hazard_association` \| `quasi_experimental` \| `simulation`. **Determines the verb a report may use** |
| `population` | mandatory statement of who is in the sample |
| `geography`, `weight`, `outcome_definition` | mandatory |
| `caveats` | list of strings, rendered into reports |
| `provenance` | `run_timestamp`, `git_commit`, `config_name`, `config_digest`, `data_class`, `data_period`, `source_versions` (`schema@retrieved#checksum` per dataset), `python_version`, `platform_str` |
| `result` | the payload |

## 10. Manifests — `<dataset>.manifest.json`

| field | meaning |
|---|---|
| `name`, `source`, `source_url` | provenance |
| `retrieved_at`, `release_date`, `coverage_period` | vintage |
| `license_terms`, `redistribution_status` | governance |
| `schema_version`, `row_count`, `checksum_sha256`, `checksum_scope` | integrity |
| `geographic_level` | |
| `known_limitations` | list — travels with the data |
| `data_class` | `PUBLIC` \| `RESTRICTED` \| `SYNTHETIC` \| `DERIVED`. **`SYNTHETIC` forces the report banner** |
