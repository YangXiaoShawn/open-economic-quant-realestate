# Thirty-minute research presentation

Audience: an economics seminar or a research-group brown bag. 30 minutes of content,
questions throughout (assume you will be interrupted — that is the point of a
seminar). Sections are timed; the running total is 30.

---

## §1 · Motivation and the object of study — 3 min

**The shock.** PMMS 30-year fixed: ~3.1% in December 2021 → ~7.1% in October 2023.
Largest two-year increase in the series' 50-year history.

**The mechanism.** A homeowner with a fixed-rate mortgage at $r_0$ holds an asset:
the right to keep borrowing at $r_0$ for the remaining term. In the U.S. that right
is not portable across properties and, on conventional conforming loans, generally not
assumable. When $r_t > r_0$, moving destroys the asset.

**The cost, made concrete.** For remaining balance $B_t$ and remaining term $n_t$:

$$\Delta_t = \mathrm{PMT}(B_t, r_t, n_t) - \mathrm{PMT}(B_t, r_0, n_t),
\qquad
\mathrm{PVGap}_t = \Delta_t \cdot a_{H,\delta}$$

**Research question.** How does the gap between existing note rates and current
market rates affect mortgage exits, housing-market activity, local prices, and new
construction?

**Announce the data class now.** The current run uses labeled synthetic loan fixtures;
the Freddie Mac loan-level dataset is behind a registration wall this project did not
bypass. Public aggregates (PMMS, FHFA HPI, HMDA, Census BPS) are real. Say this before
any number appears.

## §2 · The theoretical ambiguity — 4 min

**This is the intellectual core. Do not skip it to get to results.**

Two channels, same household, opposite price implications:

- **Listing (supply) channel.** The locked-in owner doesn't sell. Fewer existing homes
  listed. Holding demand fixed, prices **rise**.
- **Repeat-buyer (demand) channel.** The same owner is also a buyer. An owner who
  doesn't sell also doesn't buy. Holding supply fixed, prices **fall**.

Therefore:

- **Transaction volume:** unambiguously falls. Both channels push the same way.
- **Prices:** sign depends on relative elasticities, on the share of demand from
  first-time buyers and investors (not locked in), and on how readily new construction
  substitutes for existing homes.
- **Construction:** also ambiguous. Scarce, expensive existing homes → substitution
  toward new building. Suppressed trade-up demand → fewer permits.

**The inferential warning to state explicitly:** a fall in transactions is **not**
evidence of a supply-only mechanism. The same aggregate decline is consistent with a
pure listing-side contraction, a pure repeat-buyer contraction, or any mixture. Most
public commentary on lock-in commits exactly this error.

*Slide: the flow diagram from `reports/demand_supply_decomposition.md`.*

## §3 · Data, and what each source is not — 3 min

| source | what it is | the "is not" |
|---|---|---|
| Freddie Mac Single-Family Loan-Level | origination + monthly performance | **not** the U.S. mortgage market: conforming conventional GSE acquisitions only |
| PMMS | weekly national average **offered** rate | **not** transaction-weighted, **not** local |
| FHFA HPI | repeat-sales index | **not** a property value; concepts (purchase-only / all-transactions / expanded) are different objects |
| HMDA | applications and **originations** | **not** a property-sales registry; all-cash invisible |
| Census BPS | permits **authorized** | **not** starts, **not** completions |

**The double selection, stated once and clearly.** The loan population excludes
FHA/VA (disproportionately first-time and lower-wealth buyers — *and assumable*, so
the lock-in mechanism differs there), jumbo (high-price metros), non-QM, portfolio,
and Fannie Mae. The mortgage market in turn excludes all-cash purchases and the
roughly one third of U.S. owner-occupied homes with **no mortgage at all** — households
structurally immune to lock-in. Any "share locked in" is a share of *Freddie-acquired
loans*.

## §4 · Outcome definitions — 3 min

**The slide that determines what the rest of the talk is allowed to say.**

Freddie Mac's own termination-event priority table (1 = highest priority):

| ZB | official label | priority | our treatment |
|---|---|---|---|
| 15 | Whole Loan Sale | 1 | **censored** — portfolio action |
| 16 | Reperforming loan securitizations | 2 | **censored** — portfolio action |
| 09 | REO Disposition | 3 | credit event |
| 96 | Defect prior to other termination | 4 | **censored** — R&W repurchase |
| 03 | Short Sale or Charge Off | 5 | credit event |
| 02 | Third Party Sale | 6 | credit event *(foreclosure auction — not a household move)* |
| 01 | Prepaid or Matured (Voluntary Payoff) | 7 | **prepayment** |

**Code 01 pools refinancing, sale-related payoff, and maturity.** There is no property
identifier; postal code is truncated to three digits + `00`; field 27 links only
Relief Refinance/HARP chains, not ordinary refis.

**Consequence:** the loan-level outcome is `prepayment`. Not sales. Not moves. Not
refinancing. Enforced in code — no sale event exists in the schema and a test fails if
one is added.

Anticipate the question: *"Couldn't you infer sales from the balance path?"* No.
A payoff looks identical whether the household refinanced in place or sold.

## §5 · Loan-level duration design and results — 5 min

**Design.** Loan-month episodes; time origin loan age.

- **Left truncation** — performance records begin at Freddie Mac *acquisition*. Risk
  sets exclude loans not yet observed at each age.
- **Right censoring** — at the cutoff and at administrative removals. Censoring ZB
  15/16/96 is *an assumption*: it requires removal to be uninformative about latent
  exit time. Tested by a robustness cell that instead counts them as prepayment.
- **Competing risks** — cause-specific hazards; Aalen–Johansen CIF, not 1−KM.
- **Time-varying covariates** — point-in-time PMMS, start-of-month balance, estimated
  current LTV, local price growth.

**The model ladder:** (1) Kaplan–Meier; (2) Aalen–Johansen CIF; (3) discrete-time
logit with loan-age dummies; (4) complementary log-log; (5) Cox with entry times and
Schoenfeld PH diagnostics; (6) gradient-boosted classifier as an out-of-time
predictive benchmark.

**Results.** The monthly prepayment hazard falls steeply and monotonically across
rate-gap buckets and survives the covariate set. Report the coefficient, the hazard
ratio, and the average marginal effect in monthly-probability units.

**The interpretation slide — do not skip it.**
> The rate gap is a deterministic function of the note rate the borrower chose and the
> national rate path. A borrower with a 2.8% coupon in 2023 differs from one with a
> 6.8% coupon in cohort, credit, equity, and tenure. Age dummies and covariates reduce
> but do not eliminate that. **Tier: `hazard_association`.**

**Point-in-time discipline.** The market rate attached to month $m$ is the last PMMS
observation available on or before the first day of $m$, enforced by a backward as-of
join and asserted in validation and in a unit test. Look-ahead here would mechanically
inflate the very coefficient the talk is about.

## §6 · Market-level identification — 5 min

$$y_{gt} = \alpha_g + \gamma_t + \sum_{k \neq k_0} \beta_k\bigl(E_g \times \mathbf 1\{t=k\}\bigr) + X_{gt}'\theta + \varepsilon_{gt}$$

**The treatment.** Freeze the pre-shock (Dec 2021) local coupon distribution and
evaluate it at the later national rate level:

$$E_g = \sum_k \omega_{gk}^{\text{pre}} \cdot \mathbf 1\{\bar R^{\text{post}} - r_k > \tau\}$$

All cross-sectional variation is in the frozen shares; the rate level is a national
scalar.

**Tell the story of the error here — it is the best pedagogy in the talk.**
> My first attempt measured exposure as the *contemporaneous* locked-in share at
> Dec 2021. It was exactly zero in every state — correctly, because the pre-shock rate
> was near its historic low and **nobody was locked in yet**. Lock-in is created by the
> subsequent rise acting on the pre-existing coupon distribution. I had measured a
> definition, not a phenomenon. The exposure-distribution diagnostic caught it, and it
> is now computed and inspected before any event study is interpreted.

**Assumptions, stated as assumptions.**

- **A1 parallel trends** → joint Wald test on pre-period interactions; a failure
  **auto-demotes** the artifact to descriptive, in code, with no override.
- **A2 no anticipation** → the 2022 rate speed was largely unforecast; exposure was
  built by the 2020–21 refi wave, motivated by the *low* rate then prevailing.
- **A3 predetermined** → asserted in code: no input row may postdate the as-of date.
- **A4 shock–share exogeneity** → **not credible, and we say so.** The 2021 coupon
  distribution reflects when a market last turned over, which correlates with pandemic
  in-migration, price growth, and construction. Balance table: exposure correlates
  ≈ −0.4 with 2019–21 price growth, ≈ −0.7 with the pre-shock coupon level
  (mechanical). **Hence no IV language anywhere.** This is a conditional DiD with a
  continuous predetermined treatment.
- **A5 spillovers** → a locked-in household who doesn't move also doesn't buy
  elsewhere. Biases estimates **toward zero**; not corrected.
- **A6 measurement** → exposure is measured on Freddie-acquired loans only; coverage
  varies by state and is carried as a variable.

**What is not identified, even in the best case.** The aggregate effect of the rate
increase. $R_t$ is common to all geographies and absorbed by $\gamma_t$. Only relative
effects survive. No headline of the form "lock-in cost N million home sales" can come
out of this design.

## §7 · Market-level results, robustness, falsification — 4 min

- **Purchase originations** (the headline; refi outcomes are mechanically contaminated
  by pipeline exhaustion in high-exposure markets): pre-trend passes (p = 0.34) and the
  estimate is **+0.001, s.e. 0.019, t = 0.05** — a precise-looking zero. Wild-cluster
  bootstrap reported alongside the asymptotic test.
- Say it plainly: **this is a null result.** With 26 state clusters, annual HMDA data,
  and time fixed effects absorbing the common national shock, the design has limited
  power to detect a cross-state differential — so a null is not a refutation of
  lock-in, but it is also not suggestive evidence for it.
- **A placebo outcome fails.** The mortgage denial rate moves with t = −1.9. A
  significant placebo counts against the design. It is reported in the grid and in
  `reports/failed_hypotheses.md`, not buried.
- **Prices and single-family permits:** pre-trends **fail** (p = 0.003 and p = 0.007),
  so both are auto-demoted to descriptive. No assumed sign.
- **Robustness grid:** exposure definitions, bp thresholds, count vs UPB weighting,
  control sets, exclusion of pandemic-boom and high-refi markets, HMDA coverage
  regimes, panel balance, placebo shock dates, placebo outcomes. Each cell gets a
  verdict — consistent / attenuated / sign-flip / insignificant / not-estimable — and
  failures are enumerated in `reports/failed_hypotheses.md`, not dropped.
- **Falsification logic:** for a placebo, insignificance is the *pass*. Multifamily
  (5+) permits are renter-demand driven and should not respond to owner lock-in; the
  denial rate proxies credit conditions.

## §8 · Counterfactuals and their honesty boundary — 3 min

Eight scenarios: −50/−100/−200 bp; partial portability; conditional assumability;
seller credit; buydown; elevated supply elasticity; targeted starter homes; and a
no-lock-in bound.

**The boundary, drawn explicitly:**

- **Estimated:** the hazard coefficients, the stock composition, the observed rate path.
- **Calibrated (no error bars):** price elasticity of demand, supply elasticity,
  holding period, discount rate, policy take-up shares.
- **Unidentified:** the share of prepayments that correspond to a property
  transaction. Reported across a **range**; no point value is preferred.

**Three substantive points worth the seminar's time:**

1. Portability and assumability **transfer** the below-market-coupon loss rather than
   eliminating it. Someone holds the cheap coupon. The scenarios do not model who pays.
2. Cost per *additional* transaction far exceeds cost per assisted borrower, because
   most recipients transact anyway.
3. Supply elasticity and lock-in policy are **complements**: the same modelled demand
   shift converts into mostly-quantity or mostly-price depending on a calibrated supply
   elasticity. A demand-side unlock in an inelastic market partly capitalises into
   prices.

**Label:** `simulation`. Not a forecast. The **ordering** is the output; magnitudes are
order-of-magnitude at best.

## §9 · Benchmarking against the literature — 2 min

Four benchmarks recorded with target estimand, original data, original identification,
our data, population differences, outcome-definition differences, and comparison type.

- **FHFA lock-in / transactions line of work:** `conceptual`. They observe a *sale* in
  linked mortgage-property records. We observe prepayment. Comparing a prepayment
  hazard to a sale hazard would be a category error.
- **Fed work on lock-in and mobility:** `conceptual`. They have credit-bureau address
  panels. We have no mobility measure at all.
- **Prepayment-modelling literature:** `approximate` — genuinely the same estimand on
  the same kind of data. The steep non-linear incentive response and the seasoning ramp
  are legitimately comparable, and reproducing that shape is a validation of the
  pipeline, not a new finding.
- **HMDA descriptive accounts:** `approximate`.

**Nothing is labeled `exact`, and the code refuses to label anything exact.** Where our
side is synthetic, the comparison is explicitly *blocked* rather than reported.

## §10 · Limitations and the path forward — 2 min

Ranked by how much each should change the audience's reading:

1. A prepayment is not a move, a sale, or a refinance. Caps everything the loan-level
   results can mean.
2. Only relative effects are identified at the market level.
3. Predetermined exposure is not exogenous.
4. Doubly selected population.
5. The demand/supply decomposition is framed, not achieved.
6. Low power: 26 clusters, narrow exposure spread.
7. Scenarios use an association as a response function plus calibrated elasticities.

**What would resolve what:**

| would resolve | needs |
|---|---|
| lock-in and mobility | linked mortgage-property records, or a credit-bureau address panel |
| refinance vs sale payoff | a property identifier, or a servicer panel |
| listing vs repeat-buyer channel | MLS listings data |
| power | MSA-level with a versioned OMB crosswalk; both Enterprises' loan-level files |
| exogenous exposure | a shifter of the local coupon distribution unrelated to local demand — **not found** |

**Closing.** The deliverable is not a coefficient. It is a system in which the evidence
tier is attached to every artifact, the report renderer refuses causal language that
has not been earned, a failed pre-trend demotes a result automatically, restricted data
cannot be committed, and five of my own design errors are documented with the
diagnostics that caught them.

---

## Anticipated questions, with answers

**"Why not just use the ELTV field for current LTV?"**
Freddie Mac's reported ELTV is populated only for a subset of loan-periods. We prefer
it where present and fall back to origination LTV scaled by amortisation and the state
HPI path, recording which source was used per row. The state-index proxy for one
property is noisy and we say so.

**"Isn't the 2020–21 refi wave a great instrument?"**
No. It is exactly the confound. Markets that refinanced most also had the biggest
pandemic price booms, and those markets mean-reverted in 2022–23 for reasons unrelated
to lock-in. We control for 2019–21 price growth, exclude top-decile boom markets as a
robustness cell, and refuse IV language.

**"Your loan-level effect is large and your market-level effect is zero. Doesn't that
undermine the story?"**
They are different objects. The loan-level result is a within-borrower association
between the gap and payoff, in a design with hundreds of thousands of loan-months. The
market-level result asks whether *cross-state differences* in exposure predict
*cross-state differences* in origination volume, with 26 clusters, after time fixed
effects absorb the common shock. Low power there is expected. It would be wrong to
read the loan-level coefficient as the market-level effect, which is precisely why the
two are reported in separate files with separate tiers.

**"Why state and not MSA?"**
State is the only level where loan, HPI, HMDA, and permit coverage align without an
MSA-definition-vintage problem. The Freddie MSA field is explicitly *not* updated for
changing OMB delineations, so MSA analysis needs a versioned crosswalk. MSA is
implemented and is the target for the registered-data run; county is deferred.

**"Aren't administrative removals informative censoring?"**
Possibly, and that is a stated limitation rather than a solved problem. Counting them
as prepayment would inflate the hazard; counting them as still-alive would be false.
Censoring is the least-wrong option, and a robustness cell instead counts them as
prepayment so the reader can see the sensitivity.

**"What would you do with another month?"**
Register for the loan-level data and rerun (no code changes needed). Then MSA-level
analysis with a versioned crosswalk, an unemployment and teleworkable-share control to
address the two unresolved threats, and HMDA-reported interest rates to build a
*local* market-rate series and reduce the measurement error in the gap.
