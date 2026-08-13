"""HMDA adapter -- CFPB Data Browser aggregations API.

We use the aggregations endpoint, which returns **counts** and dollar **sums**
only. We never download the loan-level LAR, never attempt re-identification, and
never try to reverse the CFPB's privacy modifications.

    GET https://ffiec.cfpb.gov/v2/data-browser-api/view/aggregations
        ?years=2022&states=WV&loan_purposes=1&actions_taken=1

    -> {"aggregations":[{"count": 33815, "sum": 6.394015E9, ...}], ...}

**HMDA is application and origination data, not a property-sales registry.**
All-cash purchases are invisible. Institutions below the reporting threshold are
invisible. See :data:`COVERAGE_REGIMES` for the threshold changes that break
comparability of raw counts across years.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Final

import polars as pl
import requests

from lockin.adapters.base import USER_AGENT, AdapterError, OfflineError, SourceSpec, cache_path
from lockin.config import Config
from lockin.manifest import write_manifest

API = "https://ffiec.cfpb.gov/v2/data-browser-api/view/aggregations"

SPEC = SourceSpec(
    name="hmda",
    source="Home Mortgage Disclosure Act data via the CFPB Data Browser aggregations API",
    urls=(API,),
    license_terms="Public. Released by the CFPB with privacy modifications applied.",
    redistribution_status="public; cached API responses not committed",
    geographic_level="state (MSA and county also supported by the API)",
    known_limitations=(
        "APPLICATION and ORIGINATION data, NOT a property-sales registry. "
        "All-cash purchases are entirely absent.",
        "Only institutions above the reporting threshold report; small lenders and "
        "many non-depositories in some years are absent.",
        "2018 HMDA rule changed reported fields and institutional coverage; counts "
        "are not comparable across the 2017/2018 boundary.",
        "The closed-end reporting threshold moved from 25 to 100 loans, which "
        "removes small reporters and lowers counts independently of market "
        "activity.",
        "The public release carries CFPB privacy modifications (binning, rounding, omission).",
        "Annual frequency only: within-year timing is not observed.",
        "A refinance origination count in a market that already refinanced heavily "
        "is mechanically depressed by pipeline exhaustion -- see DECISION_LOG D014.",
    ),
    schema_version="cfpb-data-browser-aggregations-v2",
)

#: Loan purpose codes (2018+ HMDA).
LOAN_PURPOSE: Final[dict[str, str]] = {
    "1": "Home purchase",
    "2": "Home improvement",
    "31": "Refinancing",
    "32": "Cash-out refinancing",
    "4": "Other purpose",
    "5": "Not applicable",
}

#: Action taken codes.
ACTION_TAKEN: Final[dict[str, str]] = {
    "1": "Loan originated",
    "2": "Application approved but not accepted",
    "3": "Application denied",
    "4": "Application withdrawn by applicant",
    "5": "File closed for incompleteness",
    "6": "Purchased loan",
    "7": "Preapproval request denied",
    "8": "Preapproval request approved but not accepted",
}

#: Reporting-coverage regimes. Raw counts are NOT comparable across regimes.
COVERAGE_REGIMES: Final[dict[str, tuple[int, int]]] = {
    "pre_2018_rule": (2007, 2017),
    "post_2018_rule": (2018, 2021),
    "threshold_100_closed_end": (2022, 2100),
}


def coverage_regime(year: int) -> str:
    for name, (lo, hi) in COVERAGE_REGIMES.items():
        if lo <= year <= hi:
            return name
    return "unknown"


#: The API filter parameter for loan purpose is **plural**. This matters more than it
#: looks: the Data Browser API silently IGNORES unrecognised query parameters and
#: returns the unfiltered aggregate rather than an error. Passing the singular
#: ``loan_purpose`` therefore yields all-purpose totals that look like a clean
#: purchase-only series. :func:`_assert_filters_applied` exists so that this class of
#: silent failure can never reach a result artifact again. See DECISION_LOG D017.
LOAN_PURPOSE_PARAM: Final[str] = "loan_purposes"


class FilterNotAppliedError(AdapterError):
    """The API did not echo back a filter we asked for."""


def _assert_filters_applied(payload: dict[str, Any], purpose: str, action: str) -> None:
    """Verify the API echoed our filters back, so a dropped filter cannot pass silently."""
    echoed = payload.get("parameters") or {}
    if LOAN_PURPOSE_PARAM not in echoed:
        raise FilterNotAppliedError(
            f"the HMDA API did not apply the {LOAN_PURPOSE_PARAM!r} filter "
            f"(requested {purpose!r}); it echoed {sorted(echoed)}. Unrecognised "
            "parameters are silently dropped and the response would be an "
            "ALL-PURPOSE total masquerading as a filtered one. Refusing to cache it."
        )
    if "actions_taken" not in echoed:
        raise FilterNotAppliedError(
            f"the HMDA API did not apply the 'actions_taken' filter (requested "
            f"{action!r}); it echoed {sorted(echoed)}."
        )


#: API parameter that selects the geography, by geography kind. Both are plural, and
#: both are silently ignored when misspelled -- the failure mode that D017 was opened for.
GEO_PARAM: Final[dict[str, str]] = {"state": "states", "msa": "msamds"}


def _cache_file(
    cfg: Config, year: int, geo: str, purpose: str, action: str, geo_kind: str = "state"
) -> Path:
    # v2 in the name: v1 cache files were fetched with the wrong (silently ignored)
    # filter parameter and must not be reused. The geography kind is in the key because
    # a five-digit MSA code and a two-letter state abbreviation can never collide, but
    # being explicit keeps the two caches independently invalidatable.
    tag = "" if geo_kind == "state" else f"{geo_kind}_"
    return cache_path(cfg, "hmda", f"agg_v2_{tag}{year}_{geo}_p{purpose}_a{action}.json")


def _one_cell(
    cfg: Config,
    year: int,
    state: str,
    purpose: str,
    action: str,
    sleep: float = 0.05,
    session: requests.Session | None = None,
    geo_kind: str = "state",
) -> dict[str, Any]:
    """Fetch (or read from cache) a single (year, geography, purpose, action) cell."""
    target = _cache_file(cfg, year, state, purpose, action, geo_kind)
    if target.exists():
        cached: dict[str, Any] = json.loads(target.read_text())
        _assert_filters_applied(cached, purpose, action)
        return cached
    if cfg.offline:
        raise OfflineError(f"offline=True and HMDA cell not cached: {target.name}")

    params = {
        "years": str(year),
        GEO_PARAM[geo_kind]: state,
        LOAN_PURPOSE_PARAM: purpose,
        "actions_taken": action,
    }
    get = (session or requests).get
    last_err = ""
    for attempt in range(3):
        try:
            resp = get(API, params=params, headers={"User-Agent": USER_AGENT}, timeout=90)
            if resp.status_code == 200:
                payload = resp.json()
                _assert_filters_applied(payload, purpose, action)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(payload, indent=1))
                if sleep:
                    time.sleep(sleep)
                return payload  # type: ignore[no-any-return]
            last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except (requests.RequestException, ValueError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
        time.sleep(1.5 * (attempt + 1))
    raise AdapterError(f"HMDA cell {year}/{state}/p{purpose}/a{action} failed: {last_err}")


def _extract(payload: dict[str, Any]) -> tuple[int, float]:
    aggs = payload.get("aggregations") or []
    if not aggs:
        return (0, 0.0)
    count = sum(int(a.get("count", 0) or 0) for a in aggs)
    total = sum(float(a.get("sum", 0) or 0) for a in aggs)
    return (count, total)


#: The (purpose, action) cells we pull. Kept deliberately small: each is one API call
#: per state-year, and the API is a public service.
DEFAULT_CELLS: Final[tuple[tuple[str, str, str], ...]] = (
    ("purchase_originations", "1", "1"),
    ("purchase_applications", "1", "1,2,3,4,5"),
    ("purchase_denials", "1", "3"),
    ("refi_originations", "31,32", "1"),
    ("refi_applications", "31,32", "1,2,3,4,5"),
)


def fetch(
    cfg: Config,
    years: list[int] | None = None,
    states: list[str] | None = None,
    cells: tuple[tuple[str, str, str], ...] = DEFAULT_CELLS,
    max_workers: int = 6,
) -> tuple[str, int]:
    """Populate the HMDA cache for the requested state-years and write a manifest."""
    years = years or cfg.panel.hmda_years
    states = states or (cfg.panel.states or DEFAULT_STATES)
    jobs = [
        (year, st, label, purpose, action)
        for year in years
        for st in states
        for label, purpose, action in cells
    ]
    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    # A bounded pool: the Data Browser is a public service, so we stay modest.
    with requests.Session() as session, ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_one_cell, cfg, year, st, purpose, action, 0.05, session): (
                year,
                st,
                label,
                purpose,
                action,
            )
            for year, st, label, purpose, action in jobs
        }
        for fut in as_completed(futures):
            year, st, label, purpose, action = futures[fut]
            try:
                count, total = _extract(fut.result())
            except Exception as exc:
                failures.append(f"{year}/{st}/{label}: {type(exc).__name__}")
                continue
            rows.append(
                {
                    "year": year,
                    "geography": st,
                    "measure": label,
                    "loan_purpose_codes": purpose,
                    "actions_taken_codes": action,
                    "count": count,
                    "amount_usd": total,
                    "coverage_regime": coverage_regime(year),
                }
            )

    if not rows:
        raise AdapterError(
            f"every HMDA cell failed ({len(failures)} attempts). First few: {failures[:5]}"
        )
    df = pl.DataFrame(rows).sort(["geography", "year", "measure"])
    out = cache_path(cfg, "hmda", "hmda_state_year_aggregates.parquet")
    df.write_parquet(out)
    coverage = f"{min(years)}..{max(years)}"
    write_manifest(
        out,
        name=SPEC.name,
        source=SPEC.source,
        source_url=API,
        license_terms=SPEC.license_terms,
        redistribution_status=SPEC.redistribution_status,
        schema_version=SPEC.schema_version,
        row_count=df.height,
        geographic_level="state",
        coverage_period=coverage,
        known_limitations=list(SPEC.known_limitations)
        + (
            [
                f"{len(failures)} of {len(jobs)} API cells failed and are ABSENT from the "
                "table; affected state-years have missing outcomes rather than zeros."
            ]
            if failures
            else []
        ),
        data_class="PUBLIC",
        extra={
            "n_cells_requested": len(jobs),
            "n_cells_failed": len(failures),
            "failed_cells": failures[:50],
            "cells": [
                {"measure": c[0], "loan_purpose": c[1], "actions_taken": c[2]} for c in cells
            ],
            "states": states,
            "years": years,
            "loan_purpose_labels": LOAN_PURPOSE,
            "action_taken_labels": ACTION_TAKEN,
        },
    )
    return (coverage, df.height)


def load(cfg: Config) -> pl.DataFrame:
    """Load the cached state-year aggregate table in wide form."""
    p = cache_path(cfg, "hmda", "hmda_state_year_aggregates.parquet")
    if not p.exists():
        raise FileNotFoundError(f"HMDA aggregates not cached at {p}. Run `make fetch-public-data`.")
    long = pl.read_parquet(p)
    # Explicit conditional aggregation rather than `DataFrame.pivot` with multiple
    # `values` columns: that form silently produced identical columns for different
    # measures, which made purchase and refinance originations the same number.
    measures = sorted(long["measure"].unique().to_list())
    wide = (
        long.group_by(["year", "geography", "coverage_regime"])
        .agg(
            [
                pl.col("count").filter(pl.col("measure") == m).first().alias(f"n_{m}")
                for m in measures
            ]
            + [
                pl.col("amount_usd").filter(pl.col("measure") == m).first().alias(f"usd_{m}")
                for m in measures
            ]
        )
        .sort(["geography", "year"])
    )
    return wide


DEFAULT_STATES: Final[list[str]] = [
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "DC",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
]


MSA_FILENAME = "hmda_msa_year_aggregates.parquet"

#: A metropolitan area with no purchase originations in a year is not an observation --
#: it is a failed lookup. If more than this share of areas come back empty, the geography
#: resolution is wrong and the run must stop rather than produce a panel full of zeros.
MAX_EMPTY_SHARE: Final[float] = 0.05


def fetch_msa(
    cfg: Config,
    years: list[int] | None = None,
    cells: tuple[tuple[str, str, str], ...] = DEFAULT_CELLS,
    max_workers: int = 6,
    restrict_to: set[str] | None = None,
) -> tuple[str, int]:
    """Populate the HMDA cache at **metropolitan** geography.

    Two things make this more than a parameter swap, both established by probing the API
    before writing any of it (``DECISION_LOG`` D036):

    * HMDA reports a **Metropolitan Division wherever one exists**, not the parent CBSA.
      Querying divided metros -- New York, Los Angeles, Chicago, Dallas and the rest of
      the largest in the country -- by their CBSA code returns ``count: 0`` with no error.
    * HMDA year Y reports under the **OMB delineation in force in year Y**, not the
      current one. A code introduced in a later bulletin also returns a silent zero.

    Both are resolved through :func:`lockin.adapters.omb_cbsa.hmda_geographies`, which
    maps each analysis CBSA to the code HMDA will actually answer on *for that year*.
    Results are keyed back to the parent ``cbsa_code`` so the panel unit is stable even
    though the queried code is not.

    Raises when too many areas come back empty, because that is what a broken geography
    resolution looks like: not an exception, a plausible panel of zeros.
    """
    from lockin.adapters import omb_cbsa

    years = years or cfg.panel.hmda_years
    resolved = {y: omb_cbsa.hmda_geographies(cfg, y) for y in years}

    # Fetching every published metro costs ~12,350 requests against a public service,
    # against ~1,530 for the state panel. A metro with no loans in the sample contributes
    # a null exposure and is dropped by the panel builder anyway, so restricting to the
    # geographies actually present in the loan stock buys nothing but goodwill -- and it
    # is the caller's job to say which those are, since this adapter does not read the
    # loan tables.
    if restrict_to is not None:
        keep = {str(g) for g in restrict_to}
        resolved = {
            y: df.filter(pl.col("cbsa_code").is_in(list(keep))) for y, df in resolved.items()
        }
        empty = [y for y, df in resolved.items() if df.height == 0]
        if empty:
            raise AdapterError(
                f"restrict_to matched no metropolitan areas for year(s) {empty}. The codes "
                "given are probably Metropolitan Divisions or a different delineation "
                "vintage -- pass PARENT CBSA codes."
            )

    jobs = [
        (year, row["report_code"], row["cbsa_code"], label, purpose, action)
        for year in years
        for row in resolved[year].iter_rows(named=True)
        for label, purpose, action in cells
    ]
    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    with requests.Session() as session, ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_one_cell, cfg, y, rc, purpose, action, 0.05, session, "msa"): (
                y,
                rc,
                cc,
                label,
                purpose,
                action,
            )
            for y, rc, cc, label, purpose, action in jobs
        }
        for fut in as_completed(futures):
            y, rc, cc, label, purpose, action = futures[fut]
            try:
                count, total = _extract(fut.result())
            except Exception as exc:
                failures.append(f"{y}/{rc}/{label}: {type(exc).__name__}")
                continue
            rows.append(
                {
                    "year": y,
                    "geography": cc,
                    "report_code": rc,
                    "measure": label,
                    "loan_purpose_codes": purpose,
                    "actions_taken_codes": action,
                    "count": count,
                    "amount_usd": total,
                    "coverage_regime": coverage_regime(y),
                }
            )

    if not rows:
        raise AdapterError(
            f"every HMDA MSA cell failed ({len(failures)} attempts). First: {failures[:5]}"
        )

    df = pl.DataFrame(rows).sort(["geography", "year", "measure"])
    _assert_metros_are_not_empty(df)

    out = cache_path(cfg, "hmda", MSA_FILENAME)
    df.write_parquet(out)
    coverage = f"{min(years)}..{max(years)}"
    write_manifest(
        out,
        name=f"{SPEC.name}_msa",
        source=SPEC.source,
        source_url=API,
        license_terms=SPEC.license_terms,
        redistribution_status=SPEC.redistribution_status,
        schema_version=SPEC.schema_version + "-msa",
        row_count=df.height,
        geographic_level="CBSA (queried as Metropolitan Division where one exists)",
        coverage_period=coverage,
        known_limitations=[
            *SPEC.known_limitations,
            "HMDA reports Metropolitan Divisions, not parent CBSAs, for divided metros. "
            "Rows are keyed to the PARENT cbsa_code; report_code records what was queried.",
            "The delineation vintage HMDA reports under changes by year (2018 -> 2017 "
            "vintage, 2019-2023 -> 2018, 2024+ -> 2023), so a metro's queried code is "
            "not constant across the panel even when the analysis unit is.",
            "Multi-state metros are single observations here, but every OTHER control in "
            "this project is published by state and cannot be attached without an "
            "allocation assumption.",
        ],
        data_class="PUBLIC",
        extra={
            "n_geographies": df["geography"].n_unique(),
            "n_failed_cells": len(failures),
            "delineation_vintage_by_year": {
                str(y): omb_cbsa.vintage_for_hmda_year(y) for y in years
            },
        },
    )
    return (coverage, df.height)


def _assert_metros_are_not_empty(df: pl.DataFrame) -> None:
    """Fail loudly when the geography resolution silently returned zeros.

    The API answers an unresolvable metro code with ``count: 0`` rather than an error, so
    without this a wrong vintage or a parent-instead-of-division lookup yields a
    well-formed panel in which the largest markets simply have no lending.
    """
    per_year = (
        df.filter(pl.col("measure") == "purchase_originations")
        .group_by("year")
        .agg(
            pl.len().alias("n_areas"),
            (pl.col("count") == 0).sum().alias("n_empty"),
        )
        .with_columns((pl.col("n_empty") / pl.col("n_areas")).alias("share"))
        .sort("year")
    )
    bad = per_year.filter(pl.col("share") > MAX_EMPTY_SHARE)
    if bad.height:
        detail = "; ".join(
            f"{r['year']}: {r['n_empty']}/{r['n_areas']} empty ({r['share']:.1%})"
            for r in bad.iter_rows(named=True)
        )
        raise AdapterError(
            "HMDA returned zero purchase originations for too many metropolitan areas -- "
            f"{detail}. A metro with no lending is not a real observation; this is what a "
            "wrong delineation vintage or a parent-CBSA-instead-of-division lookup looks "
            "like. Check lockin.adapters.omb_cbsa.vintage_for_hmda_year against the API "
            "before trusting any of it."
        )


def load_msa(cfg: Config) -> pl.DataFrame:
    """Load the cached metropolitan HMDA aggregates, wide by measure."""
    p = cache_path(cfg, "hmda", MSA_FILENAME)
    if not p.exists():
        raise FileNotFoundError(f"HMDA MSA aggregates not cached at {p}. Run fetch_msa.")
    long = pl.read_parquet(p)
    return (
        long.group_by(["year", "geography", "coverage_regime"])
        .agg(
            *[
                pl.col("count").filter(pl.col("measure") == m).sum().alias(f"n_{m}")
                for m in sorted(set(long["measure"].to_list()))
            ]
        )
        .sort(["geography", "year"])
    )
