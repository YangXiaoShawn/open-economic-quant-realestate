# Interview story

How to talk about this project. The structure is deliberate: the strongest thing to
say about a housing-finance project is not a number, it is **what you refused to
claim and why**.

---

## The 60-second version

> "In 2022–23 U.S. mortgage rates went from about 3% to over 7% in under two years.
> That created a large stock of homeowners holding mortgages far below market — and a
> reason not to move, because moving means refinancing your housing at the new rate.
> I built a system to measure that.
>
> There are two halves. At the loan level I estimate discrete-time prepayment hazards
> on a loan-month panel with proper left truncation, right censoring, and competing
> risks, using eight different point-in-time lock-in measures rather than one rate
> difference. At the market level I build a state-month panel and run a
> continuous-treatment event study on **predetermined** exposure — the pre-shock local
> coupon distribution evaluated at the later national rate path — against HMDA
> purchase originations, FHFA price growth, and Census permits.
>
> The thing I'd want you to know is the constraint I hit. The loan-level data
> identify *prepayment*, and Freddie Mac's own documentation says code 01 is 'Prepaid
> or Matured (Voluntary Payoff)' — it pools refinancing, sale-related payoff, and
> maturity. So I cannot say anything about mobility, and the whole project is built so
> that I *can't accidentally* say it. There's no sale event in the schema, and a test
> fails if anyone adds one."

## The follow-up they will ask: "so what did you find?"

The honest answer, and it is a better answer than a number:

> "Three things, at three different levels of confidence, and I keep them separate on
> purpose.
>
> **Descriptively and with high confidence**, the monthly prepayment hazard falls
> steeply and monotonically as the rate gap widens — that gradient is the whole
> mechanism, and it's the least controversial thing in the project.
>
> **As a conditional association**, that gradient survives controls for loan age,
> credit score, DTI, LTV, balance, and local price growth. But it's an association,
> not an elasticity: the rate gap is a deterministic function of the note rate the
> borrower chose and the national rate path, and a borrower with a 2.8% coupon in
> 2023 differs from one with a 6.8% coupon in cohort, credit, equity, and tenure.
>
> **At the market level I find essentially nothing.** The estimate on log purchase
> originations is +0.001 with a standard error of 0.019 — a t-statistic of 0.05. Not
> negative-but-noisy. Zero. And one of my placebo outcomes, the mortgage denial rate,
> moves with a t of −1.9, which counts *against* the design rather than for it.
>
> I want to be precise about what that does and doesn't mean. With 26 state clusters,
> annual HMDA data, and time fixed effects absorbing the common national shock, this
> design has limited power to detect a cross-state differential. So it is a genuine
> null, not a refutation of lock-in — but I'm not going to dress it up as suggestive
> evidence either.
>
> And one caveat I'd flag before you ask: the current run uses synthetic fixtures,
> because the Freddie Mac loan-level data are behind a registration wall I didn't
> bypass. The public aggregates — PMMS, FHFA, HMDA, Census — are real. Every synthetic
> number is stamped and banner-ed, and the mode switch to registered data needs no
> code changes."

## The question that separates candidates: "does lock-in raise or lower prices?"

This is where most people answer too fast. The correct answer is that it is
**theoretically ambiguous**, and being able to say why is the point:

> "A locked-in owner is on *both* sides of the market. They don't list — that removes
> existing-home supply and pushes prices up. But the same household also doesn't buy a
> replacement — that removes repeat-buyer demand and pushes prices down. Which
> dominates depends on the relative elasticities and on how much of local demand comes
> from first-time buyers and investors, who aren't locked in at all.
>
> Quantities are unambiguous — both channels cut transaction volume. Prices are not.
> So I never assume a sign, and I flag the most common inferential error in public
> commentary on this topic: a fall in transactions is **not** evidence of a
> supply-only mechanism. The same aggregate decline is consistent with a pure
> listing-side contraction, a pure demand-side contraction, or any mixture.
>
> And this matters for policy, not just for precision. If the listing channel
> dominates, unlocking owners improves affordability by adding supply. If the
> repeat-buyer channel dominates, the same policy adds demand and could raise prices
> while raising volume. Opposite affordability consequences from the same
> intervention. That's why my scenario module reports quantity and price responses
> separately and never nets them into a welfare claim."

## The bugs — lead with these, don't hide them

Interviewers rate candidates who find their own errors far above candidates whose
work has no visible errors. Five, with the diagnostic that caught each:

**1. A treatment variable with zero variance.**
> "My first exposure measure was the contemporaneous locked-in share at the pre-shock
> date, December 2021. It came out as exactly zero in every state — and that was
> *correct*, because in December 2021 the market rate was near its historic low, so
> essentially nobody was locked in yet. Lock-in is created by the *subsequent* rate
> rise acting on the coupon distribution that already existed. I'd measured a
> definition, not a phenomenon. The fix was the actual shift-share design: freeze the
> 2021 coupon shares, evaluate them at the later national rate path. The diagnostic
> that caught it — an exposure-distribution artifact computed and inspected before any
> event study is interpreted — is now a permanent step."

**2. An API that silently ignored a filter — and it changed my headline result.**
> "The CFPB HMDA Data Browser takes `loan_purposes`, plural. I passed the singular.
> It didn't error — it dropped the parameter and returned **all-purpose** totals. So
> my 'purchase originations' and 'refinance originations' were byte-identical, both
> being all-purpose totals. That would have been a fabricated finding. The fix is a
> contract assertion: reject any response where the service doesn't echo back every
> filter I asked for, on both fetch and cache read, with versioned cache keys so the
> poisoned entries couldn't be reused. The general lesson: if an API accepts a filter
> without echoing it, assume it was ignored."

**3. Covariates corrupted in exactly the event months.**
> "`Current Actual UPB` is the *end*-of-period balance, and it's zero in a payoff
> month. So the payment-gap covariate was zero precisely in the months where exits
> occur — corrupting the covariate for every single event. A validation check found it:
> ~2,900 rows where the payment gap and the rate gap disagreed in sign. Everything now
> uses a start-of-month balance, which is also the economically right timing: a
> within-month decision is made with the balance you owe going in."

**4. No pre-periods, so nothing was testable.**
> "I'd built the annual panel only over years where I had an active mortgage stock —
> 2021 onward. But HMDA, FHFA, and Census all reach back to 2018. With no pre-shock
> years, pre-trends were untestable, so my own tier logic auto-demoted every single
> outcome to descriptive. The pipeline was telling me the design was underpowered, and
> it was right."

**5. Asking a data source for something it doesn't publish.**
> "I configured monthly purchase-only FHFA HPI at state level. FHFA publishes
> purchase-only monthly only for the nation and census divisions; at state level it's
> quarterly. The filter matched zero rows and silently degraded my LTV proxy. Now
> `load_series` raises with the full list of published combinations rather than
> returning an empty frame."

## If they push on identification

> "I don't call the exposure an instrument, and I'd push back on anyone who did. It's
> predetermined, which is not the same as exogenous. The 2021 coupon distribution is a
> function of when a market last turned over, which correlates with pandemic
> in-migration, price growth, and construction — all of which independently predict
> 2023 outcomes. My balance table shows exposure correlating about −0.4 with 2019–21
> price growth. So the design is a conditional difference-in-differences with a
> continuous predetermined treatment, and I say so.
>
> What's genuinely not identified even in the best case is the *aggregate* effect of
> the rate increase. The national rate path is common to every geography, so it's
> absorbed by the time fixed effects. Only relative effects across exposure survive.
> Any headline of the form 'lock-in cost the economy N million home sales' cannot come
> out of this design, and I don't produce one."

## If they ask about engineering

> "The binding constraint is that the full loan-level dataset is billions of
> loan-months and I have 16 GB. So the panel is never materialised: chunked line
> streaming out of the zip archives without full extraction, column projection that
> drops the loss/expense block, Hive-partitioned Parquet keyed on cohort and period
> year so scans prune, Polars lazy plans collected in streaming mode, and a
> configurable row budget that fails the run rather than swapping. For the full data
> there's a case-cohort design: keep every event month, sample non-event months, carry
> the weight in the artifact so no coefficient is ever reported without its sampling
> design.
>
> The part I'm most pleased with is the governance being mechanical rather than
> aspirational. Restricted data can't be committed — gitignore plus a test that scans
> the git index for restricted paths, bulk extensions, and oversized files. Synthetic
> inputs propagate a mandatory report banner with no flag to disable it. And the
> vocabulary rule is a test: a regex scan fails the build if any file asserts that a
> prepayment is a sale or a move."

## Questions to ask them

- Do you have linked mortgage-and-property records, or a credit-bureau address panel?
  That's the single thing that would turn my prepayment analysis into a mobility
  analysis.
- How do you handle the Enterprise-only coverage problem? FHA and VA loans are
  *assumable*, so the excluded segment is exactly where the policy counterfactual
  already partly exists.
- When you run MSA-level analysis, how do you version OMB delineation changes? The
  Freddie MSA field isn't updated for redefinitions, which is why I deferred MSA.
- Where does your team draw the line between a hazard association and a policy
  response function? My scenario module crosses that line deliberately and labels it;
  I'd want to know your convention.

## What not to say

- Don't say "moves", "sales", or "listings" about the loan-level results. Ever.
- Don't quote a loan-level magnitude from the synthetic run as a finding.
- Don't say the exposure measure is an instrument.
- Don't present a scenario as a forecast.
- Don't claim to have replicated the FHFA or Fed lock-in papers. They use linked
  mortgage-property records and credit-bureau mobility panels; the honest comparison
  is conceptual, and my benchmark report labels it that way.
