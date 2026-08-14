# Mortgage Rate Lock-In, Housing Liquidity, and Local Market Dynamics

A reproducible U.S. housing-finance research system measuring how the gap between
homeowners' existing mortgage rates and current market mortgage rates relates to
**mortgage exits**, **local housing-market activity**, **house prices**, and
**residential construction**.

> **Central question.** How does the gap between homeowners' existing mortgage
> rates and current market mortgage rates affect mortgage exits, housing-market
> activity, local prices, and new construction?

The project combines four layers, each with its own evidence tier:

| Layer | Method | Evidence tier |
|---|---|---|
| Loan-level duration analysis | Kaplan–Meier / cumulative incidence, discrete-time logit & cloglog hazards, Cox, competing risks | `descriptive`, `hazard_association` |
| Local-market panel | State-month panel of exposure, originations, prices, permits | `descriptive` |
| Quasi-experimental design | Continuous-treatment event study on **predetermined** pre-shock lock-in exposure | `quasi_experimental` |
| Counterfactual module | Hazard-aggregation policy scenarios | `simulation` (never a forecast) |

---

## ⚠️ Read this before quoting any number

Two things determine whether an output of this repository is evidence:

1. **Which loan data were used.** The Freddie Mac Single-Family Loan-Level Dataset
   requires registration and licence acceptance. This repository does **not**
   bypass that. Without it, the pipeline runs on **labeled synthetic fixtures**
   that are schema-exact to the official layout. Every artifact and report in that
   mode is stamped `SYNTHETIC` and carries a banner. **A synthetic number is a
   software test, not a finding.** The public aggregate series (PMMS, FHFA HPI,
   HMDA, Census BPS) are real in either mode.
2. **Which evidence tier the artifact carries.** `descriptive` ≠
   `hazard_association` ≠ `quasi_experimental` ≠ `simulation`. Every artifact
   records its tier, and `docs/IDENTIFICATION_STRATEGY.md` §6 gives the decision
   rule for when causal language is permitted.

**The loan-level outcome is `prepayment`, not "moves".** Freddie Mac Zero Balance
Code `01` is officially *"Prepaid or Matured (Voluntary Payoff)"* — it conflates
voluntary payoff with scheduled maturity and does not distinguish a refinance from
a sale-related payoff. No field in the dataset supports a home-sale or
household-move event, so this repository never constructs one. Mobility-adjacent
questions are approached only through independent local purchase-origination
measures. See `AGENTS.md` §1.

---

## Quickstart

Requires macOS/Linux and [`uv`](https://docs.astral.sh/uv/). Python 3.12 is
installed by `make setup`.

```bash
make setup
```

```bash
make fetch-public-data
```

```bash
make reproduce-sample
```

`make reproduce-sample` runs the whole vertical slice end to end:

```
prepare-sample-data → ingest-mortgages → build-loan-events → build-lockin
  → build-local-panel → validate-data → estimate-hazards
  → estimate-local-effects → benchmark → simulate-policy → report
```

Then:

```bash
make test
```

```bash
make dashboard
```

Every target is also a CLI subcommand: `uv run lockin --help`.

### What you get

* `outputs/` — result artifacts (JSON), each with `evidence_tier`, population
  statement, geography, weight, outcome definition, and full provenance (git
  commit, config digest, data period, source versions).
* `reports/` — generated Markdown reports. Files whose header says `GENERATED`
  are rebuilt by `make report`; do not hand-edit them.
* `data/cache/` — downloaded official public series with manifests (gitignored).
* `data/processed/` — the loan-event table, episode table, and panels
  (gitignored; loan granularity).

---

## Using real loan-level data

1. Register at
   <https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset> and accept
   the terms of use. **Do this yourself** — the terms prohibit redistribution, and
   this repository will not circumvent the wall.
2. Put the archives, unmodified, in `data/raw/freddie/`. Either the official
   sample files (`sample_YYYY.zip`) or full quarterly cohorts
   (`historical_data_YYYYQn.zip`) work.
3. Set `mortgage.mode: registered_sample` (or `registered_full`) in your config
   and re-run. The adapter discovers the files, the `SYNTHETIC` stamps disappear,
   and the reports lose their banner.

Full instructions, expected archive contents, and sizes: `data/DATA_ACCESS.md`.

---

## Data sources

| Source | What it is | Access |
|---|---|---|
| Freddie Mac Single-Family Loan-Level Dataset | Origination + monthly performance | **Registration required**; not redistributed |
| Freddie Mac PMMS | Weekly national average offered mortgage rate | Public CSV |
| FHFA House Price Index | Repeat-sales index, nation/division/state/MSA | Public CSV |
| HMDA (CFPB Data Browser) | Mortgage applications and originations | Public API (counts and sums only) |
| Census Building Permits Survey | Permits **authorized** | Public |

Terms, redistribution status, and per-source limitations:
`data/LICENSE_AND_REDISTRIBUTION.md`.

---

## Repository map

```
AGENTS.md               Standing operating contract. Read first.
STATUS.md               Current state, what works, what is blocked.
docs/
  PROJECT_PLAN.md       Milestones, dependencies, acceptance tests, risk register
  RESEARCH_DESIGN.md    Economic framework, outcome definitions, robustness grid
  IDENTIFICATION_STRATEGY.md  Where causal language is earned or refused
  DECISION_LOG.md       Every non-obvious choice, with rationale
data/
  DATA_ACCESS.md        How to obtain each source (public and registered)
  DATA_DICTIONARY.md    Every field in every table we build
  LICENSE_AND_REDISTRIBUTION.md
src/lockin/             All logic, tested
reports/                GENERATED reports
portfolio/              Hand-written portfolio deliverables
dashboard/app.py        Streamlit dashboard
```

## Key design decisions

* **Prepayment ≠ mobility.** Enforced in code and in tests.
* **ZB 15/16/96 are censoring, not exits.** Whole-loan sales, RPL securitizations,
  and defect repurchases are Freddie Mac portfolio actions, not borrower decisions.
* **Left truncation is real.** Performance records begin at Freddie Mac
  acquisition, not origination, so a substantial share of loans enter the risk set
  at loan age > 1.
* **Exposure is predetermined and never recomputed.** Contemporaneous lock-in
  exposure is endogenous to the outcome; the event-study treatment is fixed at
  2021-12.
* **No instrumental-variable language.** Predetermined is not exogenous.
  `docs/IDENTIFICATION_STRATEGY.md` §A4 explains why.
* **The price effect's sign is not assumed.** Lock-in withdraws both existing-home
  supply *and* repeat-buyer demand; the net price effect is theoretically
  ambiguous and is an empirical question.

## Licence

Code: MIT. Data: see `data/LICENSE_AND_REDISTRIBUTION.md` — the data policy is
stricter than the code licence and overrides it.

## Public release

- Project page: <https://yangxiaoshawn.github.io/projects/realestate/>
- GitHub source: <https://github.com/YangXiaoShawn/YangXiaoShawn.github.io/tree/main/realestate>
- Versioned research package: <https://huggingface.co/datasets/ShawnChamberlain/open-economic-quant-research-data/tree/main/RealEstate>
- Interactive explorer: <https://huggingface.co/spaces/ShawnChamberlain/open-economic-quant-research-observatory>

The public release contains no registered Freddie Mac loan-level records or
loan-granular derivatives. See `docs/PUBLICATION.md` for the destination map and
publication boundary.
