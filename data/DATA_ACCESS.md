# Data Access

Two tiers of data are used. **Tier P (public)** is downloaded automatically.
**Tier R (registered)** requires you to register and accept terms yourself; this
repository will not and cannot do that for you.

Run `make fetch-public-data` for Tier P. Everything lands in `data/cache/` with a
manifest, and nothing in `data/cache/` is committed.

---

## Tier P — public, fetched automatically

### P1. Freddie Mac Primary Mortgage Market Survey (PMMS) — market mortgage rates

- URL: `https://www.freddiemac.com/pmms/docs/PMMS_history.csv`
- Contents: weekly national average offered rates. Columns `date, pmms30,
  pmms30p, pmms15, pmms15p, pmms51, pmms51p, pmms51m, pmms51spread`.
  `*p` = points/fees, `pmms51*` = 5/1 ARM series.
- Coverage: 1971-04-02 onward (30-yr); 15-yr from 1991-08-30; 5/1 ARM 2005–2022.
- **Methodology regimes we record:**
  - `lender_survey_1971_2022` — the original survey of lenders.
  - `application_based_2022_11+` — from 2022-11-17 Freddie Mac moved PMMS to a
    methodology based on loan applications received from lenders; **the
    fees/points series and the 5/1 ARM series were discontinued at the same
    time.** Rates before and after are not produced the same way.
  - The 1-yr ARM series was discontinued in 2015 and is not in this file.
- Terms: Freddie Mac permits use with attribution; commercial redistribution is
  restricted. We cache locally and never commit the file.
- Adapter: `src/lockin/adapters/pmms.py`.

### P2. FHFA House Price Indexes

- URL: `https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv` (~17 MB)
- Contents: long-format panel. Columns `hpi_type, hpi_flavor, frequency, level,
  place_name, place_id, yr, period, index_nsa, index_sa, rstderr, note`.
- `hpi_flavor` distinguishes **index concepts** — `purchase-only`,
  `all-transactions`, `expanded-data`. **Never mix these.** Default for this
  project: `purchase-only`, monthly, seasonally adjusted where available.
- `level` distinguishes geography: `USA or Census Division`, `State`, `MSA`,
  `Puerto Rico`.
- Terms: U.S. Government work, public domain. Cite FHFA and the release date.
- Adapter: `src/lockin/adapters/fhfa_hpi.py`.

### P3. HMDA — CFPB Data Browser aggregations API

- Endpoint: `https://ffiec.cfpb.gov/v2/data-browser-api/view/aggregations`
- Example: `?years=2022&states=WV&loan_purpose=1&actions_taken=1`
  returns `{"aggregations":[{"count":…,"sum":…}]}` — loan **count** and dollar
  **sum**. One call per (year, state, purpose, action) cell; responses cached.
- `loan_purpose`: `1` = Home purchase, `31` = Refinancing, `32` = Cash-out
  refinancing, `2` = Home improvement, `4` = Other, `5` = Not applicable.
  (2018+ codes. Pre-2018 HMDA used `1`/`2`/`3`.)
- `actions_taken`: `1` = Loan originated, `2` = Approved not accepted,
  `3` = Denied, `4` = Withdrawn, `5` = File closed incomplete,
  `6` = Purchased loan, `7`/`8` = preapproval outcomes.
- **Coverage breaks:** the 2018 HMDA rule changed reported fields and
  institutional coverage; the closed-end reporting threshold moved from 25 to 100
  loans effective 2022 data (after litigation history in 2020–2022). Counts are
  therefore not comparable across 2017/2018 or across the threshold change.
  Adapter records `coverage_regime` per year.
- Privacy: the public LAR is released with privacy modifications (binned/rounded
  fields). The aggregations endpoint returns counts and sums only.
- Terms: public. Adapter: `src/lockin/adapters/hmda.py`.

### P4. Census Building Permits Survey

- Monthly state file: `https://www2.census.gov/econ/bps/State/st{YYMM}c.txt`
  (`c` = current/preliminary; `r` = revised). Annual and MSA/county files follow
  parallel paths under `https://www2.census.gov/econ/bps/`.
- Contents: buildings, units, and construction value by structure size
  (1-unit, 2-units, 3-4 units, 5+ units), plus "rep" (reported-only) columns.
  The non-`rep` columns include **imputation** for non-responding permit places;
  the `rep` columns do not. We store both and default to the imputed series,
  documented.
- **Preliminary vs final:** monthly `c` files are preliminary and are revised.
  We record which vintage was fetched.
- Measures **permits authorized**, not starts or completions.
- Terms: U.S. Government work, public domain.
- Adapter: `src/lockin/adapters/census_bps.py`.

### P5. Official Freddie Mac dataset documentation (public)

- `https://www.freddiemac.com/fmac-resources/research/pdf/user_guide.pdf`
- `https://www.freddiemac.com/fmac-resources/research/pdf/file_layout.xlsx`

These two are publicly downloadable without registration and are the source of
truth for the schema in `src/lockin/schemas/freddie.py`. The **data** files are
not. Re-run `lockin verify-schema` after any Freddie Mac release to confirm the
layout has not changed.

### P6. Optional cross-check: FRED

- `https://fred.stlouisfed.org/graph/fredgraph.csv?id=MORTGAGE30US`
- The St. Louis Fed's redistribution of PMMS 30-yr FRM. Used only as an
  independent cross-check on P1, not as a substitute.

---

## Tier R — registered; you must do this yourself

### R1. Freddie Mac Single-Family Loan-Level Dataset

**We will not bypass registration, authentication, or licence acceptance.**

1. Go to `https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset`. The page
   states: *"To access the Single-Family Loan-Level Dataset, register and sign-in to
   **Clarity Data Intelligence**."*
2. Register for a **Clarity Data Intelligence** account and accept the terms of use.
   Read them: they prohibit redistribution of the loan-level records.

   | | |
   |---|---|
   | Dataset landing page | `https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset` |
   | Download portal (SAML sign-in) | `https://claritydownload.fmapps.freddiemac.com/CRT/` |
   | Reporting/BI portal | `https://claritybi.freddiemac.com/MicroStrategyLibrary/` |

   ⚠️ **Many older guides still point at `freddiemac.embs.com`. That host now returns
   HTTP 403 and is not the current route** (verified 2026-08-11). If a tutorial sends
   you there, it is out of date.

   The **sample files are behind the same sign-in** as the full dataset — there is no
   unauthenticated sample download, which is why this repository ships synthetic
   fixtures instead.
3. Download either
   - the **official sample dataset** (`sample_YYYY.zip` files — a random 50,000-loan
     sample per origination year, with matching monthly performance), or
   - the **standard full dataset** (`historical_data_YYYYQn.zip` per quarterly cohort).
4. Place the archives, unmodified, in `data/raw/freddie/`.
5. **Preflight before you commit hours to it:**

   ```bash
   make check-registered-data
   ```

   This reads only the first few hundred lines of each member (seconds, not hours) and
   answers: are these the expected files, does each parse at the verified 32-field
   layout, are all Zero Balance Codes ones we document, do the origination and
   performance files pair up, and roughly how much disk and time will the real run
   need. It exits non-zero on a blocker. **A layout change or an undocumented Zero
   Balance Code is a blocker on purpose** -- an unknown code would otherwise be
   silently censored, discarding real exits.

6. Set `mortgage.mode: registered_sample` or `registered_full` in your config, set
   `mortgage.cohorts` to what you actually downloaded, and run `make reproduce-sample`.

Expected archive contents per cohort:
- `historical_data_YYYYQn.txt` — origination, pipe-delimited, 32 fields, no header.
- `historical_data_time_YYYYQn.txt` — monthly performance, pipe-delimited, 32
  fields, no header.

Sizes: the full dataset is tens of GB decompressed and billions of loan-months.
The ingestion is streaming and per-cohort; see `docs/PROJECT_PLAN.md` §4.4.

**Until you complete R1, the pipeline runs on labeled synthetic fixtures**
(`data/fixtures/`, generated by `make prepare-sample-data`). Every artifact and
report produced in that mode is stamped `SYNTHETIC` and must not be read as an
empirical finding.

### R2. Optional local economic controls

**BLS LAUS state unemployment — IMPLEMENTED, and it needs no registration.**
`src/lockin/adapters/bls_laus.py`. Note the route: the bulk flat files at
`https://download.bls.gov/pub/time.series/la/` return **HTTP 403** to a generic
client, because BLS asks automated downloaders to identify themselves with a contact
email. **This repository will not put your personal email into an outbound header
without being asked to**, so it uses the **public JSON API v2** instead
(`https://api.bls.gov/publicAPI/v2/timeseries/data/`), which serves the same series
without a registration key within published unregistered limits (~25 queries/day, 25
series and 20 years per query). Three queries cover all 51 states.

Series: `LASST{fips}0000000000003` — LAUS, State, Seasonally adjusted, measure `03` =
unemployment rate. Fetched by `make fetch-public-data`; the pipeline runs without it
and records its absence.

*If you want the bulk-file route instead* (higher volume, no rate limit), set a
`User-Agent` containing your own contact email — that is your decision to make, not
this repository's.

**Teleworkable employment share — IMPLEMENTED, no registration.**
`src/lockin/adapters/teleworkable.py`. Dingel & Neiman (2020), "How many jobs can be done
at home?", *J. Public Economics* 189:104235 — the authors' public replication outputs on
GitHub. State and CBSA levels, four published measures; the default is `teleworkable_emp`
(O*NET survey rule, employment-weighted), **not** the `manual` pair, which is the authors'
own subjective classification.

Addresses the remote-work-exposure threat in `docs/IDENTIFICATION_STRATEGY.md` §3.2. It
measures the **feasibility** of remote work, not its realisation — that distinction is
carried on every artifact. Because it is a single cross-section it enters the event study
**interacted with every period**, never as a level control; as a level it would be exactly
collinear with the geography fixed effects and would constrain nothing. See `DECISION_LOG`
D027, which also records the two specification bugs this exposed.

**OMB CBSA delineation crosswalk — IMPLEMENTED, no registration.**
`src/lockin/adapters/omb_cbsa.py`. Census Bureau List 1 delineation files for six OMB
vintages (2013, 2015, 2017, 2018, 2020, 2023), public domain.

Required for any MSA-level analysis, because Freddie Mac's MSA field is as-of-origination
and is **not** restated when OMB redraws a boundary. The adapter records, per code,
whether its county composition, title, or metro/micro status ever changed. Measured
result: of 1,054 codes only 609 are stable across all six vintages, and **215 metropolitan
CBSAs are both stable and usable** as panel units. See `DECISION_LOG` D028.

**Not yet implemented, and each leaves a documented gap:**

- **IRS SOI county-to-county migration** — public, but requires manual file selection.
- **Census ACS housing tenure / vacancy** — public API; a free key is needed for
  high-volume use.

The core project must run without any of these. They are `optional: true` in the
config.

---

## Reproducibility contract

Every fetch writes `data/cache/<source>/<name>.manifest.json` containing source
URL, retrieval timestamp, release date where discoverable, coverage period,
licence/terms, redistribution status, schema version, row count, SHA-256
checksum, geographic level, and known limitations. `make validate-data` fails if
a manifest is missing or its checksum does not match.
