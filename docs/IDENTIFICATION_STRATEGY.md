# Identification Strategy

This document is the place where causal language is earned or refused. Nothing in
`reports/` may carry tier `quasi_experimental` unless the corresponding argument
here is written, and the corresponding diagnostic is reported.

---

## 1. The estimand

Let $g$ index geographies and $t$ months. Let $E_g$ be predetermined exposure to
low-coupon mortgages, measured at a pre-shock date $t_0$. Let $R_t$ be the
national market mortgage rate. The target is the coefficient path

$$
y_{gt} \;=\; \alpha_g + \gamma_t + \sum_{k \neq k_0} \beta_k\,\bigl(E_g \times \mathbf 1\{t=k\}\bigr) + X_{gt}'\theta + \varepsilon_{gt}.
$$

$\beta_k$ is the **differential** response of outcome $y$ in a high-exposure
geography relative to a low-exposure geography, at date $k$, relative to
reference date $k_0$.

**What is not identified.** The level effect of the national rate increase. $R_t$
is common to all geographies and is absorbed by $\gamma_t$. Any statement of the
form "the rate increase reduced national transactions by X%" is *not* available
from this design. Only cross-sectional differences in the response are.

**Interpretation of $\beta_k$.** Under the assumptions in §2, $\beta_k$ is the
causal effect of an additional unit of predetermined lock-in exposure on the
outcome, at date $k$, holding common national shocks fixed. It is a *relative*
effect and it embeds general-equilibrium spillovers between geographies (a locked-in
household in one metro who does not move also does not buy in another metro).
Spillovers bias $\beta_k$ toward zero if high- and low-exposure markets are linked
by migration.

---

## 2. Assumptions required, stated explicitly

**A1 (Parallel trends in exposure).** Absent the national rate increase, outcomes
in high- and low-exposure geographies would have evolved in parallel, conditional
on $\alpha_g$, $\gamma_t$, and $X_{gt}$.

*Diagnostic:* joint test that $\beta_k = 0$ for all $k < k_0$. Reported, with the
F/Wald statistic and p-value, in `outputs/eventstudy/*.json` under `pretrend_test`.
A pre-trend failure demotes the result to `descriptive`.

**A2 (No anticipation).** Geographies did not adjust before $t_0$ in anticipation
of the rate increase. Plausible: the speed of the 2022 rate increase was widely
unforecast, and exposure was built up by the 2020–21 refinance wave, whose
motivation was the *low* rate then prevailing.

*Diagnostic:* leads in the event study; a placebo shock date in a low-rate-volatility
period (2018-01, 2019-06).

**A3 (Exposure is predetermined).** $E_g$ is computed only from information
available at $t_0$. Enforced in code: the exposure builder takes a `as_of` date
and asserts that no input row has a date after it.

**A4 (Shock–share exogeneity, for the shift-share variant).** Predetermined is
**not** exogenous. The shift-share form
$E_g = \sum_k \omega_{gk}^{\text{pre}} \cdot s_k$ requires either (i) the shares
$\omega_{gk}$ are conditionally uncorrelated with unobserved determinants of the
outcome trend, or (ii) the shocks $s_k$ are as-good-as-random across coupon bins.

*Neither is credible here without argument.* The coupon distribution at $t_0$ is
a function of when a geography's housing stock last turned over, which correlates
with pandemic in-migration, price growth, and construction. The exposure is
therefore **not** an instrument. We report:
- the concentration of exposure across coupon bins (Herfindahl of $\omega_{gk}$),
- the correlation of $E_g$ with pre-period covariates (a balance table),
- results with and without controls for pre-period price growth and refi intensity.

**We do not use IV language.** The design is a *conditional* difference-in-differences
with a continuous, predetermined treatment. If a future version wants IV, the
exclusion restriction must be stated here first: "$E_g$ affects post-2022 purchase
originations only through the lock-in channel", and the obvious violation —
that the same 2020–21 refinance wave also reflects a local demand boom that
independently predicts 2023 outcomes — must be defended, not asserted.

**A5 (SUTVA / limited spillovers).** Treated above. Direction of bias: toward zero.

**A6 (Measurement).** $E_g$ is measured on the Freddie-acquired population, not
all mortgages. If Freddie's share of a geography's mortgages varies systematically
with the outcome, $E_g$ is measured with non-classical error. We report Freddie
loan counts per geography as a coverage variable and test sensitivity to dropping
low-coverage geographies.

---

## 3. Threats, one by one

### 3.1 Pandemic housing-demand reallocation
2020–21 saw large, geographically uneven demand shifts. Markets with the biggest
price booms also had the most refinancing (equity + rate incentive), hence the
lowest coupons at $t_0$, hence the highest $E_g$. Those same markets then
mean-reverted in 2022–23 for reasons unrelated to lock-in.

*Response:* control for 2019-01→2021-12 log price growth; exclude top-decile
boom markets as a robustness cell; report both.

### 3.2 Remote-work exposure
Teleworkable employment share drives both migration and construction, and
correlates with the pandemic boom.

*Response:* optional adapter for a teleworkable-share control; heterogeneity split.
Documented as an *unresolved* threat if the control is unavailable in the slice.

### 3.3 Differential refinancing booms
A market where nearly everyone refinanced in 2020–21 has both extreme exposure
and an exhausted refinance pipeline, which mechanically depresses subsequent refi
counts regardless of lock-in.

*Response:* refi-origination outcomes are reported but treated as
**mechanically contaminated**; the headline outcome is *purchase* originations.
Exclude top-decile refi-intensity markets as a robustness cell.

### 3.4 Local labour-market shocks
*Response:* state unemployment control (optional adapter); region × period fixed
effects as a robustness cell.

### 3.5 Housing-supply constraints
Supply elasticity determines whether a demand shift shows up in prices or
quantities. This is not a nuisance — it is part of the mechanism.

*Response:* predetermined supply-constraint proxy (historical permits per housing
unit); interact with exposure rather than only controlling for it.

### 3.6 Composition change in the observed mortgage stock
The active stock shrinks and its composition drifts. Contemporaneous exposure is
endogenous to the outcome (markets with more transactions churn their stock faster).

*Response:* exposure is fixed at $t_0$ and never recomputed. Contemporaneous
exposure is reported *only* as a descriptive series.

### 3.7 National monetary-policy endogeneity
The Fed raised rates in response to macro conditions that also affect housing.
Because the rate path is national, this is absorbed by $\gamma_t$. The residual
concern is that the *interaction* of the national shock with exposure picks up
the interaction of macro conditions with whatever else exposure proxies for.

*Response:* this is exactly A1/A4. Handled by the balance table and controls, and
flagged as the deepest remaining threat.

### 3.8 Geography-specific mortgage-rate differences
PMMS is national. Local offered rates differ by tens of basis points.

*Response:* measurement error in the *level* of the gap, attenuating loan-level
coefficients. Robustness: HMDA-reported interest rates (available 2018+) to build
a local rate series; documented as future work if not in the slice.

### 3.9 Differential credit conditions
Tightening credit standards in 2022–23 varied locally and reduce originations
independent of lock-in.

*Response:* HMDA denial rates as a control/placebo outcome.

---

## 4. Falsification tests

| Test | Prediction if lock-in is the mechanism | Prediction if confounded |
|---|---|---|
| Placebo shock date 2018-01 or 2019-06 | $\beta_k \approx 0$ (rate move too small) | non-zero, similar sign |
| Multifamily (5+) permits as outcome | Weak — multifamily demand is renter-driven, not lock-in-driven | similar magnitude to single-family |
| HMDA denial rate as outcome | $\approx 0$ | non-zero |
| Exposure among *investor* loans only | Weaker (investors are less locked-in behaviourally, and second-home/investment loans are a small share) | similar |
| Reverse the sign of the shock (2019 rate *decline*) | Opposite-signed | same sign |

Each writes a row to `outputs/robustness/grid.parquet` and a paragraph to
`reports/failed_hypotheses.md` if it fails.

---

## 5. Loan-level vs local-level: the firewall

The loan-level hazard results and the local-market results answer different
questions and carry different tiers. They are reported in separate files
(`reports/loan_hazard_analysis.md` vs `reports/local_market_event_study.md`) and
the synthesis in `reports/technical_report.md` must state the tier of each
sentence it combines.

The loan-level rate-gap coefficient is **not** a causal elasticity of mobility. It
is the conditional association between a point-in-time rate gap and the
probability that a loan's balance goes to zero, in a selected population, where
the gap is mechanically a function of the note rate the borrower chose and the
national rate path. A borrower with a 2.8% note rate in 2023 is different from a
borrower with a 6.8% note rate in 2023 in cohort, credit, equity, and tenure. The
age dummies, cohort controls, and covariates reduce but do not eliminate that.

---

## 6. Decision rule for causal language

A report sentence may use causal language ("reduced", "caused", "led to") only if
**all** of the following hold for the underlying artifact:

1. `evidence_tier == "quasi_experimental"`.
2. `pretrend_test.pvalue >= 0.10` (or the failure is disclosed in the same paragraph).
3. At least one placebo specification is reported and does not itself produce a
   significant effect of the same sign.
4. Clustered standard errors are reported, with the cluster count.
5. The exposure definition and pre-shock date are stated in the sentence or its table.

Otherwise the sentence must read "is associated with" / "predicts" / "under the
model".
