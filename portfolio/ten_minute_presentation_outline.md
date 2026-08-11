# Ten-minute presentation outline

Audience: a mixed research/policy group. Ten slides, one minute each. Leave three
minutes of the slot for questions; do not pad to fill.

**Opening discipline:** state the data class in the first thirty seconds. If the run
is synthetic, say so before anyone sees a number, not after.

---

### Slide 1 — The setup (1 min)

- PMMS 30-year fixed: ~3.1% (Dec 2021) → ~7.1% (Oct 2023). The largest two-year
  increase in the 50-year history of the series.
- That creates a stock of homeowners holding coupons far below market.
- Moving means refinancing your housing consumption at the new rate. So don't move.
- **The research question:** how does the gap between existing note rates and market
  rates affect mortgage exits, purchase activity, prices, and construction?

*Visual:* the real PMMS path, coloured by methodology regime, with Dec 2021 marked.

### Slide 2 — What "lock-in" is, and what it isn't (1 min)

- Lock-in is a **state**: market rate above your note rate. Measurable.
- Its **effect** is a different object. It has to be estimated.
- Eight measures, not one: raw gap, positive gap, refinance incentive,
  payment-equivalent cost in dollars per month, PV financing gap, and three
  geography-level exposure shares (count- and UPB-weighted).
- All **point-in-time** — no measure uses a rate observable only after its own date.

*The line to deliver:* "Most commentary conflates the state with the effect. I don't."

### Slide 3 — The constraint that shapes everything (1 min)

**Spend a full minute here. It is the most important slide.**

- Freddie Mac Zero Balance Code 01 is officially *"Prepaid or Matured (Voluntary
  Payoff)"*.
- It pools: a refinance (household stays), a sale-related payoff, and a scheduled
  maturity (no decision at all).
- No property identifier. Postal code truncated to three digits + `00`.
- **Therefore: the loan-level outcome is `prepayment`. Not sales. Not moves.**
- Enforced in code — there is no sale event in the schema, and a test fails if one is
  added.

*Visual:* the official ZB priority table with the three event classes colour-coded.

### Slide 4 — Loan-level design (1 min)

- Unit: loan-month. Time origin: loan age.
- **Left truncation** — performance data start at Freddie Mac acquisition, not
  origination.
- **Right censoring** — at the cutoff, and at administrative removals.
- **Administrative removals are censored, not exits.** ZB 15/16/96 are whole-loan
  sales, RPL securitizations, and defect repurchases — Freddie Mac portfolio actions,
  not borrower decisions. Counting them as prepayment would inflate the hazard.
- **Competing risks** — cause-specific hazards; Aalen–Johansen cumulative incidence,
  not 1−KM.

### Slide 5 — The loan-level gradient (1 min)

- Monthly prepayment hazard by rate-gap bucket: steep and monotone, from strong
  refinance incentive down to deeply locked in.
- Survives loan age, credit score, DTI, LTV, balance, and local price growth.
- **Tier: `hazard_association`.** Not causal. The gap is a deterministic function of
  the note rate the borrower chose and the national rate path, and borrowers with
  different coupons differ in cohort, credit, equity, and tenure.

*Visual:* the bucket bar chart. One chart, no table.

### Slide 6 — Market-level identification (1.5 min)

- Freeze the **pre-shock** local coupon distribution (Dec 2021). Evaluate it at the
  **later** national rate path:

  $$E_g = \sum_k \omega_{gk}^{\text{pre}} \cdot \mathbf 1\{\bar R^{\text{post}} - r_k > \tau\}$$

- Continuous-treatment event study, geography and period fixed effects, clustered by
  state, wild-cluster bootstrap where clusters are few.
- **Only relative effects are identified.** The national rate path is common, so time
  fixed effects absorb it. No aggregate magnitude comes out of this design.
- **This is not an instrument.** Predetermined ≠ exogenous — exposure correlates
  ≈ −0.4 with 2019–21 price growth. Conditional DiD, and I say so.

*Visual:* the exposure distribution plus the balance table. Show the confounding.

### Slide 7 — Market-level results (1 min)

- Purchase originations: pre-trend passes, and the estimate is **essentially zero**
  (+0.001, s.e. 0.019, t = 0.05). A genuine null, not a negative-but-noisy result.
- Say the sentence: *"With 26 clusters and time fixed effects absorbing the common
  shock, this design has limited power — so this is a null result, not a refutation."*
- **One placebo outcome fails.** The denial rate moves with t = −1.9, which counts
  against the design rather than for it. Report it; don't bury it.
- Prices and single-family permits: **pre-trends fail**, so both are auto-demoted to
  descriptive and carry no causal language. **No assumed sign anywhere.**
- The tier is assigned by the code from the diagnostics, not by me. A failed pre-trend
  auto-demotes to descriptive with no override.

### Slide 8 — Why the price sign is ambiguous (1 min)

- A locked-in owner is on **both** sides: doesn't list (supply ↓, price ↑), doesn't buy
  a replacement (demand ↓, price ↓).
- Quantities: unambiguous. Prices: ambiguous.
- **A fall in transactions is not evidence of a supply-only mechanism.** The most
  common inferential error in public commentary on this topic.
- Policy consequence: if listing dominates, unlocking owners improves affordability; if
  repeat-buyer dominates, the same policy adds demand and could raise prices.
  **Opposite conclusions from the same intervention.**

*Visual:* the flow diagram.

### Slide 9 — Counterfactuals, honestly labeled (1 min)

- Eight scenarios: −50/−100/−200 bp, partial portability, conditional assumability,
  seller credit, buydown, elevated supply elasticity, targeted starter homes, and a
  no-lock-in bound.
- **Not forecasts.** They apply a hazard *association* as if it were a structural
  response function.
- The mapping into transactions rests on an **unidentified** parameter — the share of
  prepayments that are property transactions — reported across a range, never as a
  point value.
- Two policy points worth making out loud:
  - Portability and assumability **transfer** the below-market-coupon loss; they don't
    eliminate it.
  - Cost per *additional* transaction far exceeds cost per assisted borrower.

### Slide 10 — What I refused to claim (1 min)

**Close on this slide, not on results.**

- No claim about household **mobility** — no mobility measure exists in these data.
- No claim about **sales** or **listings**.
- No **aggregate** effect of the rate increase.
- No **decomposition** of the listing and repeat-buyer channels.
- No **IV** language.
- No **forecast**.
- Five design errors found by automated diagnostics and documented in
  `reports/failed_hypotheses.md` — including a treatment variable with zero variance
  and an API that silently dropped a filter.

*Closing line:* "The most useful output of this project is a system that makes it hard
to say something I can't support."

---

## Timing notes

- Slides 3, 6, and 10 are the ones that earn the room's confidence. Do not rush them
  to spend more time on results.
- If you are running long, cut slide 5 to thirty seconds — the chart speaks for itself.
- If asked "what's the headline number?", the correct answer is: *"the ordering, not
  the magnitude"* — then explain why, then offer the registered-data path.
