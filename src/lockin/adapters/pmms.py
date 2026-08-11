"""Freddie Mac Primary Mortgage Market Survey (PMMS) adapter.

Official weekly history CSV. Columns as published:

    date, pmms30, pmms30p, pmms15, pmms15p, pmms51, pmms51p, pmms51m, pmms51spread

``*p`` = fees and points; ``pmms51*`` = the 5/1 ARM series (discontinued 2022-11).
The ``date`` column is the survey/release date (a Thursday).

See ``docs/DECISION_LOG.md`` D006 for the URL history and the methodology regimes.
"""

from __future__ import annotations

import polars as pl

from lockin.adapters.base import SourceSpec, cache_path, download
from lockin.config import Config
from lockin.manifest import write_manifest
from lockin.rates import methodology_regime

SPEC = SourceSpec(
    name="pmms",
    source="Freddie Mac Primary Mortgage Market Survey",
    urls=(
        "https://www.freddiemac.com/pmms/docs/PMMS_history.csv",
        "https://www.freddiemac.com/pmms/docs/historicalweeklydata.csv",
    ),
    license_terms=(
        "Freddie Mac permits use with attribution; commercial redistribution of "
        "the series is restricted. Cached locally, never committed."
    ),
    redistribution_status="not redistributed by this repository",
    geographic_level="national",
    known_limitations=(
        "Survey/application-based average OFFERED rate for prime conventional "
        "conforming loans with ~20% down -- not a transaction-weighted average of "
        "rates actually taken, and not a local rate.",
        "Methodology changed 2022-11-17 from a lender survey to an "
        "application-based method; fees/points and the 5/1 ARM series were "
        "discontinued at the same time.",
        "Weekly frequency: within-month timing of a borrower's decision is not observed.",
        "No geographic variation: local offered rates differ by tens of basis "
        "points, so the loan-level rate gap carries measurement error.",
    ),
    schema_version="pmms-history-v1",
)

FILENAME = "PMMS_history.csv"

SERIES_LABELS = {
    "pmms30": "30-year fixed-rate mortgage, average offered rate (%)",
    "pmms30p": "30-year FRM fees and points (% of loan) -- DISCONTINUED 2022-11",
    "pmms15": "15-year fixed-rate mortgage, average offered rate (%)",
    "pmms15p": "15-year FRM fees and points (%) -- DISCONTINUED 2022-11",
    "pmms51": "5/1 hybrid ARM initial rate (%) -- DISCONTINUED 2022-11",
    "pmms51p": "5/1 ARM fees and points (%) -- DISCONTINUED 2022-11",
    "pmms51m": "5/1 ARM margin (%) -- DISCONTINUED 2022-11",
    "pmms51spread": "5/1 ARM spread to 30-yr FRM (pp) -- DISCONTINUED 2022-11",
}


def fetch(cfg: Config, max_age_days: int = 7) -> tuple[str, int]:
    """Download the PMMS history into the cache and write its manifest."""
    target = cache_path(cfg, "pmms", FILENAME)
    path, url = download(cfg, SPEC.urls, target, max_age_days=max_age_days)

    df = _read_raw(path)
    coverage = f"{df['date'].min()}..{df['date'].max()}"
    write_manifest(
        path,
        name=SPEC.name,
        source=SPEC.source,
        source_url=url,
        license_terms=SPEC.license_terms,
        redistribution_status=SPEC.redistribution_status,
        schema_version=SPEC.schema_version,
        row_count=df.height,
        geographic_level=SPEC.geographic_level,
        coverage_period=coverage,
        known_limitations=list(SPEC.known_limitations),
        data_class="PUBLIC",
        extra={"series_labels": SERIES_LABELS, "columns": df.columns},
    )
    return (coverage, df.height)


def _read_raw(path) -> pl.DataFrame:
    df = pl.read_csv(path, try_parse_dates=False, infer_schema_length=0)
    df = df.rename({c: c.strip().lower() for c in df.columns})
    df = df.with_columns(
        pl.col("date").str.strip_chars().str.to_date(format="%m/%d/%Y", strict=False)
    )
    numeric = [c for c in df.columns if c != "date"]
    df = df.with_columns(
        [
            pl.col(c).str.strip_chars().replace("", None).cast(pl.Float64, strict=False).alias(c)
            for c in numeric
        ]
    )
    return df.drop_nulls("date").sort("date")


def load(cfg: Config) -> pl.DataFrame:
    """Load the cached PMMS history, with a ``methodology_regime`` column added."""
    path = cache_path(cfg, "pmms", FILENAME)
    if not path.exists():
        raise FileNotFoundError(f"PMMS not cached at {path}. Run `make fetch-public-data`.")
    df = _read_raw(path)
    return df.with_columns(
        pl.col("date")
        .map_elements(methodology_regime, return_dtype=pl.Utf8)
        .alias("methodology_regime")
    )


def fetch_fred_cross_check(cfg: Config, max_age_days: int = 7) -> pl.DataFrame:
    """Independent cross-check: FRED ``MORTGAGE30US`` (the Fed's PMMS redistribution).

    Not a substitute for the direct PMMS fetch. Used by ``make validate-data`` to
    confirm the two agree to within a small tolerance.
    """
    target = cache_path(cfg, "fred", "MORTGAGE30US.csv")
    path, url = download(
        cfg,
        ("https://fred.stlouisfed.org/graph/fredgraph.csv?id=MORTGAGE30US",),
        target,
        max_age_days=max_age_days,
        retries=2,
        timeout=45,
    )
    df = pl.read_csv(path, infer_schema_length=0)
    date_col, val_col = df.columns[0], df.columns[1]
    out = (
        df.select(
            pl.col(date_col).str.to_date(format="%Y-%m-%d", strict=False).alias("date"),
            pl.col(val_col).cast(pl.Float64, strict=False).alias("fred_mortgage30us"),
        )
        .drop_nulls("date")
        .sort("date")
    )
    write_manifest(
        path,
        name="fred_mortgage30us",
        source="Federal Reserve Bank of St. Louis (FRED), series MORTGAGE30US",
        source_url=url,
        license_terms=(
            "FRED redistributes under the original provider's terms; this series "
            "originates with Freddie Mac, so PMMS terms apply."
        ),
        redistribution_status="not redistributed by this repository",
        schema_version="fred-csv-v1",
        row_count=out.height,
        geographic_level="national",
        coverage_period=f"{out['date'].min()}..{out['date'].max()}",
        known_limitations=[
            "Identical underlying series to PMMS 30-year FRM; used only as a "
            "cross-check on our direct fetch, not as an independent measurement."
        ],
        data_class="PUBLIC",
    )
    return out
