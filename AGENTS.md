# AGENTS.md — persistent operating instructions for this repository

This file is the standing contract for any human or agent working in this repo.
Read it before touching code, data, or reports.

**Project:** Mortgage Rate Lock-In, Housing Liquidity, and Local Market Dynamics
**Research question:** How does the gap between homeowners' existing mortgage
rates and current market mortgage rates affect mortgage exits, housing-market
activity, local prices, and new construction?

---

## 1. Non-negotiable vocabulary rules

These are the most frequent way housing-finance work goes wrong. They are
enforced in code (`src/lockin/events.py`) and in review.

| Term | What it means here | What it does NOT mean |
|---|---|---|
| **Prepayment** | A loan balance went to zero via voluntary payoff or maturity (Freddie Mac Zero Balance Code `01`). | A home sale. A household move. A refinance. |
| **Refinance** | A *new* origination whose stated purpose is refinance (HMDA `loan_purpose` 31/32, Freddie `Loan Purpose` C/N/R). | Any prepayment. |
| **Home sale** | A property transaction observed in a deeds/transaction source. **We have no such source.** | A prepayment. |
| **Household move** | A change of residence observed in a mobility source (ACS/CPS/IRS migration). | A prepayment. |
| **Credit event** | ZB codes `02`, `03`, `09` (third-party sale, short sale/charge-off, REO disposition). | Prepayment. |
| **Administrative removal** | ZB codes `15`, `16`, `96` (whole-loan sale, RPL securitization, defect prior to other termination). **Treated as censoring, not borrower behavior.** | An exit caused by the borrower. |
| **Lock-in** | A *state*: market rate above the borrower's note rate. Measured 8 ways (see `src/lockin/lockin_measures.py`). | An *effect*. The effect must be estimated. |

**Zero Balance Code `01` conflates voluntary payoff and scheduled maturity, and
does not distinguish refinance from sale-related payoff.** This is documented in
the official user guide and is the single most important limitation of the
loan-level design. Never write "moves", "sales", or "listings" when the evidence
is ZB `01`.

## 2. Evidence tiers — every claim must be tagged

Each result artifact carries an `evidence_tier` field. Reports must print it.

1. `descriptive` — means, rates, distributions, survival curves. No causal content.
2. `hazard_association` — conditional correlations from duration models. Not causal.
3. `quasi_experimental` — event study / DiD / shift-share with a stated
   identification argument and reported pre-trends and placebos.
4. `simulation` — model-dependent counterfactuals. Never a forecast.

A sentence that mixes tiers is a defect.

## 3. Data provenance rules

- **Never** commit restricted or licensed loan-level data. `.gitignore` enforces
  `data/raw/`, `data/interim/`, `data/processed/`, `*.parquet`, `*.zip`, `*.gz`.
- Every dataset written to disk gets a manifest via
  `lockin.manifest.write_manifest()`. No manifest = the artifact is invalid.
- Every result artifact records `data_period`, `config_hash`, `source_versions`,
  `git_commit`, `run_timestamp` via `lockin.provenance.run_context()`.
- Synthetic data is labeled `SYNTHETIC` in the manifest, in the filename, and in
  every report that consumes it. **Never** describe a synthetic-fixture number as
  an empirical finding.
- Do not attempt to bypass registration, authentication, licence acceptance, or
  redistribution restrictions. If data need registration, write instructions in
  `data/DATA_ACCESS.md` and stop.

## 4. Population rules

- The Freddie Mac Single-Family Loan-Level Dataset is a **selected** population:
  conforming, conventional, single-family, acquired by Freddie Mac, excluding
  loans that were never delivered. It is **not** the U.S. housing market. It
  excludes FHA/VA, jumbo, non-QM, portfolio, all-cash purchases, and (largely)
  investor-heavy segments. See `docs/RESEARCH_DESIGN.md` §Selection.
- HMDA is **application and origination** data, not a property-sales registry.
  All-cash purchases are invisible. Reporting thresholds changed in 2018 and 2020.
- FHFA HPI is an **index**, not a property value. Purchase-only, all-transactions,
  and expanded-data are **different concepts** and must never be silently mixed.
- Census BPS measures **permits authorized**, not starts or completions.

## 5. Engineering rules

- Python 3.12, `uv`, Polars-lazy + DuckDB + PyArrow. Never materialise the full
  loan-by-month panel; stream by cohort and write partitioned Parquet.
- Core logic lives in `src/lockin/` and is tested. Notebooks are thin and
  disposable; they must not contain logic.
- `make test` must pass before any report is regenerated.
- Reports in `reports/` are **generated** (`make report`). Do not hand-edit files
  whose header says `GENERATED`. Hand-written docs live in `docs/` and `portfolio/`.
- Public-data fetches are cached under `data/cache/` and are idempotent. Network
  tests are marked `@pytest.mark.network` and excluded from `make test`.

## 6. Workflow

1. `make setup`
2. `make reproduce-sample` — full end-to-end sample run
3. `make test lint typecheck`
4. Update `STATUS.md` and `docs/DECISION_LOG.md` before ending a work session.

## 7. Where things live

```
configs/            run profiles (sample = synthetic loans + real public aggregates)
data/reference/     committed small reference tables + the official field layout spec
data/fixtures/      labeled SYNTHETIC loan fixtures (committed, tiny)
data/cache/         downloaded official public data (gitignored)
data/{raw,interim,processed}/  gitignored; raw holds registered loan-level files
src/lockin/         all logic
outputs/            result artifacts (JSON/Parquet/PNG) with provenance (gitignored)
reports/            GENERATED markdown reports
portfolio/          hand-written portfolio deliverables
docs/               hand-written design and decision documents
dashboard/          Streamlit app
```

## 8. Things that are explicitly out of scope until stated otherwise

- Any claim about household mobility from loan-level data alone.
- A full structural stock-flow housing model before the vertical slice passes.
- County-level analysis until a versioned county↔MSA crosswalk is in place.
- Instrumental-variable language until the exclusion restriction is written down
  and defended in `docs/IDENTIFICATION_STRATEGY.md`.
