# Research Design

**Mortgage Rate Lock-In, Housing Liquidity, and Local Market Dynamics**

---

## 1. Economic framework

A homeowner with an outstanding fixed-rate mortgage at note rate $r_0$ holds an
asset: the right to continue borrowing at $r_0$ for the remaining term. That
right is not portable across properties and, in the United States, is generally
not assumable on conventional conforming loans. When the market rate $r_t$ rises
above $r_0$, moving destroys that asset. The destroyed value is the **lock-in
cost**.

For a borrower with remaining balance $B_t$ and remaining term $n_t$ months, the
payment-equivalent lock-in cost is

$$
\Delta_t \;=\; \mathrm{PMT}(B_t, r_t, n_t) \;-\; \mathrm{PMT}(B_t, r_0, n_t),
$$

and the present-value financing gap over an expected holding horizon $H$ months,
discounted at monthly rate $\delta$, is

$$
\mathrm{PVGap}_t \;=\; \sum_{h=1}^{H} \frac{\Delta_t}{(1+\delta)^h}
\;=\; \Delta_t \cdot a_{H,\delta}, \qquad a_{H,\delta} = \frac{1-(1+\delta)^{-H}}{\delta}.
$$

Two mechanisms follow, and they push local transaction volume the same way but
local **prices** in opposite directions:

- **Listing (supply) channel.** A locked-in owner is less willing to sell,
  because selling forces refinancing the housing consumption at $r_t$. Fewer
  existing homes are listed. Holding demand fixed, this *raises* prices.
- **Repeat-buyer (demand) channel.** The same owner is also a *buyer* in the
  same market. An owner who does not sell also does not buy. Holding supply
  fixed, this *lowers* prices.

The net price effect is therefore **theoretically ambiguous** and depends on
which side is more inelastic, on the share of demand coming from first-time
buyers and investors (who are not locked in), and on the local supply elasticity
of new construction. **We do not assume a sign.** This is the central
identification-of-mechanism problem of the project and drives §5.

Quantities are less ambiguous: both channels reduce **transaction volume**, so
purchase-mortgage originations should fall with exposure. But a fall in
transactions is *not* proof of a supply-only mechanism — see
`reports/demand_supply_decomposition.md`.

Construction is a third margin. If lock-in raises the price of existing homes
relative to new homes, builders may respond by increasing permits (substitution
toward new construction). If lock-in instead suppresses repeat-buyer demand for
trade-up housing, permits may fall. Again: no assumed sign.

---

## 2. Data and populations

| Source | What it is | Population | Level | Frequency |
|---|---|---|---|---|
| Freddie Mac Single-Family Loan-Level Dataset | Origination + monthly performance for loans Freddie Mac acquired | **Selected**: conventional, conforming, single-family, 1999Q1– | Loan | Monthly |
| Freddie Mac PMMS | Survey-based national average offered rate, 30-yr and 15-yr FRM | Lenders surveyed | National | Weekly |
| FHFA HPI | Repeat-sales index | Transactions in the index concept | Nation / division / state / MSA | Monthly or quarterly |
| HMDA (CFPB Data Browser) | Mortgage **applications and originations** | Reporting institutions | State / MSA / tract | Annual |
| Census BPS | Building permits **authorized** | Permit-issuing places | State / MSA / county / place | Monthly, annual |

**Selection statement (must appear in every report):** the loan-level population
is a conforming conventional subset. It excludes FHA/VA, jumbo, non-QM, portfolio
loans, and all-cash purchases, and it contains no mortgage-free owners. Shares
computed on it are shares of *Freddie-acquired loans*.

---

## 3. Outcome definitions (loan level)

Constructed from the official Zero Balance Code priority table (highest priority
first), Freddie Mac user guide, "Zero Balance Codes":

| ZB | Official label | Our event class | Rationale |
|---|---|---|---|
| 15 | Whole Loan Sale | `admin_removal` → **censored** | Portfolio action, not borrower behaviour |
| 16 | RPL Securitization | `admin_removal` → **censored** | Portfolio action |
| 09 | REO Disposition | `credit_event` | Terminal credit outcome |
| 96 | Defect prior to other termination | `admin_removal` → **censored** | Repurchase/indemnification, not borrower behaviour |
| 03 | Short Sale or Charge Off | `credit_event` | Terminal credit outcome |
| 02 | Third Party Sale | `credit_event` | Foreclosure-auction sale to a third party |
| 01 | Prepaid or Matured (Voluntary Payoff) | `prepayment` | **Conflates refinance, sale-related payoff, and maturity** |
| — | Modification Flag `Y`/`P` | `modification` (state, and a competing exit in one variant) | Resets loan age per the guide |
| — | No ZB by performance cutoff | `censored` | Right censoring |

`prepayment` is further split for description only, using an arithmetic rule that
is **not** an event classification: a prepayment in a month where the loan's
remaining term is ≤ 3 is flagged `maturity_like`. This is a heuristic filter, not
a documented category, and is labeled as such.

**We never emit a `home_sale` or `household_move` event.** No available field
supports it.

---

## 4. Loan-level duration design

Unit: loan-month episode. Time origin: loan age (scheduled payments since first
payment date), as defined in the guide.

- **Left truncation.** Performance records begin at Freddie Mac *acquisition*,
  not origination. A loan first observed at age $a_0 > 1$ contributes risk only
  from $a_0$. The risk set at age $a$ excludes loans not yet observed at $a$.
- **Right censoring.** At the performance cutoff, or at an `admin_removal`.
- **Competing risks.** Cause-specific hazards for `prepayment` and
  `credit_event`; cumulative incidence functions rather than $1-\mathrm{KM}$ for
  each cause.
- **Time-varying covariates.** $r_t$ from PMMS aligned point-in-time (see §6),
  current UPB from the performance file, estimated current LTV from origination
  LTV scaled by the FHFA HPI path for the loan's state, local HPI growth,
  and local unemployment (optional).

Model ladder:

1. Kaplan–Meier survival by lock-in bucket; Aalen–Johansen / CIF by cause.
2. Discrete-time logit: $\mathrm{logit}\,h(a,t,X) = \alpha_a + f(\text{gap}_t) + X'\beta$,
   with $\alpha_a$ loan-age bins and $f$ a binned or spline rate-gap profile.
3. Complementary log-log (proportional-hazards interpretation in discrete time).
4. Cox PH on a sampled loan set, with Schoenfeld-residual PH diagnostics.
5. Cause-specific competing-risk versions of (2).
6. Gradient-boosted trees as a predictive benchmark, out-of-time.

Reported: hazard ratios / average marginal effects, baseline hazard by age,
nonlinear rate-gap profile, duration dependence, cohort effects, geographic and
borrower heterogeneity, PH diagnostics, out-of-time AUC and calibration.

**Tier: `hazard_association`.** These are conditional correlations. The rate gap
is a deterministic function of the note rate (a chosen loan characteristic) and
the national rate path (common across borrowers). Borrowers with low note rates
differ systematically from borrowers with high note rates. Nothing in the
loan-level design makes these causal.

---

## 5. Local-market design

Panel unit: **state × month** in the sample slice (state is the geography where
loan, HPI, HMDA, and permit coverage all overlap without a crosswalk vintage
problem). MSA is supported and is the target for the full-data run; county is
deferred pending a versioned crosswalk.

Outcomes:
- HMDA purchase-loan originations (count and volume), annual → interpolated to
  the panel frequency only for display, never for estimation.
- HMDA refinance originations.
- FHFA HPI growth (purchase-only, monthly, state), log difference.
- Census BPS authorized units, total / 1-unit / 5+-unit.

Exposure (predetermined; see `docs/IDENTIFICATION_STRATEGY.md`):

$$
E_g \;=\; \sum_{k} \omega_{gk}^{\,\text{pre}} \cdot \mathbf{1}\{\,\bar r - r_k > \tau\,\}
$$

where $\omega_{gk}^{\text{pre}}$ is geography $g$'s share of active loans in
coupon bin $k$ **as of a pre-shock date** (default 2021-12), $\bar r$ is the
post-shock national rate level, and $\tau$ is a threshold in basis points. The
UPB-weighted variant replaces loan counts with current UPB. A payment-gap variant
replaces the indicator with the average payment-equivalent cost.

Specifications:

1. **Descriptive event study** — outcome paths for high- vs low-exposure terciles.
2. **Continuous-treatment event study**
   $$y_{gt} = \alpha_g + \gamma_t + \sum_{k\neq k_0} \beta_k \left(E_g \times \mathbf{1}\{t = k\}\right) + X_{gt}'\theta + \varepsilon_{gt}$$
   with $k_0$ the reference period immediately before the shock.
3. **DiD-style two-period / pre-post** collapse for a single headline number.
4. **Shift-share / predetermined-exposure** specification with share diagnostics.
5. **IV interpretation only if** the exclusion restriction is written down and
   defended. Default: **no IV language.**

SEs clustered by geography; wild-cluster bootstrap when the geography count is
small. Report pre-trends (joint test on $\beta_k$ for $k<k_0$), dynamic effects,
exposure distribution, economic magnitudes, weighting sensitivity (unweighted,
population-weighted, UPB-weighted), and alternative exposure definitions.

**Tier: `quasi_experimental`**, conditional on the pre-trend and placebo evidence
actually being reported and passing. Where it fails, the result moves to
`descriptive` and is recorded in `reports/failed_hypotheses.md`.

---

## 6. Point-in-time rate alignment

PMMS is a weekly survey with an **observation (survey) week** and a
**publication** timing. To guarantee no look-ahead:

- For a monthly reporting period $m$, the market rate used is the **last PMMS
  observation whose survey date is on or before the first day of month $m$**, and
  whose implied publication date is also on or before that day.
- Methodology regimes are recorded: PMMS changed from a lender-survey design to a
  loan-application-based methodology in **November 2022**, and the fees/points
  series was discontinued at the same time. The 5/1 ARM series was discontinued
  in **November 2022**. `pmms.methodology_regime` labels each observation.
- The Freddie Mac accounting cycle changed in **May 2019** (from 16th-to-15th to
  calendar month). Loan-month timestamps before and after are not perfectly
  comparable; recorded as a known limitation.

Tested in `tests/test_rate_alignment.py::test_no_look_ahead`.

---

## 7. Robustness and falsification grid

| Axis | Variants |
|---|---|
| Market-rate series | PMMS 30-yr; PMMS 15-yr; FRED MORTGAGE30US cross-check |
| Rate-gap threshold | 100 / 200 / 300 / 400 bp |
| Exposure concept | Indicator share vs average payment gap vs PV gap |
| Pre-shock date | 2021-06, 2021-12, 2022-03 |
| Placebo shock date | 2018-01, 2019-06 (rate moves too small to bind) |
| Placebo outcome | Multifamily (5+) permits; commercial-adjacent series |
| Sample exclusions | Top-decile 2020–21 price-growth markets; top-decile refi-intensity markets |
| Controls | Origination-cohort composition; unemployment; pre-period price growth |
| HPI concept | Purchase-only monthly vs all-transactions vs expanded-data (never mixed) |
| Geography | State (default) vs MSA |
| Weights | Loan count vs UPB; unweighted vs population-weighted regressions |
| Panel | Balanced vs unbalanced |
| Sub-samples | FRM only (default); purchase-origination loans only; stable-servicer |
| Coverage | HMDA pre/post 2018 and 2020 threshold changes handled separately |

Every cell is written to `outputs/robustness/grid.parquet` with a verdict:
`consistent`, `attenuated`, `sign_flip`, `insignificant`, or `failed`. Sign flips
and failures are surfaced, not buried.

---

## 8. What this design cannot do

1. It cannot measure household mobility. Full stop.
2. It cannot separate refinance from sale-related payoff at the loan level.
3. It cannot speak to FHA/VA, jumbo, non-QM, portfolio, or all-cash segments.
4. It cannot identify the *level* effect of the national rate path — only
   differential effects across exposure, because time fixed effects absorb the
   common shock.
5. It cannot claim external validity for the counterfactual module; those are
   model-dependent projections under stated behavioural parameters.
