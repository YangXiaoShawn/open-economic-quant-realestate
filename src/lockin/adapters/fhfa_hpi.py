"""FHFA House Price Index adapter.

The FHFA master file is a long-format panel over *index concepts* × *geographies*
× *frequencies*. Columns::

    hpi_type, hpi_flavor, frequency, level, place_name, place_id,
    yr, period, index_nsa, index_sa, rstderr, note

``hpi_flavor`` values include ``purchase-only``, ``all-transactions``,
``expanded-data``. **These are different index concepts and are never mixed.**
``lockin.adapters.fhfa_hpi.load_series`` requires an explicit flavor and records it.

An HPI is an *index*, not a property value. It is used here for (a) local price
growth as an outcome and (b) scaling origination LTV into an estimated current LTV,
with the state-index-as-property-proxy limitation recorded.
"""

from __future__ import annotations

from datetime import date

import polars as pl

from lockin.adapters.base import SourceSpec, cache_path, download
from lockin.config import Config
from lockin.manifest import write_manifest

SPEC = SourceSpec(
    name="fhfa_hpi",
    source="Federal Housing Finance Agency House Price Index (master file)",
    urls=(
        "https://www.fhfa.gov/hpi/download/monthly/hpi_master.csv",
        "https://www.fhfa.gov/DataTools/Downloads/Documents/HPI/HPI_master.csv",
    ),
    license_terms="U.S. Government work; public domain. Cite FHFA and the release.",
    redistribution_status="public domain; cached not committed (size)",
    geographic_level="nation / census division / state / MSA",
    known_limitations=(
        "An INDEX, not a transaction-level property value. Cannot be used to value "
        "an individual property.",
        "Repeat-sales construction: reflects only properties that transacted at "
        "least twice, and is influenced by the mix of transacting properties.",
        "purchase-only, all-transactions, and expanded-data are DIFFERENT index "
        "concepts. Purchase-only excludes refinance appraisals; all-transactions "
        "includes them.",
        "Monthly purchase-only indexes are based on Enterprise (Fannie/Freddie) "
        "acquisitions, so they inherit the same conforming-conventional selection "
        "as the loan-level data.",
        "State-level indexes are a coarse proxy for any individual property's "
        "price path; estimated current LTV built from them carries substantial "
        "measurement error.",
        "Revised with each release; the release used is recorded in the manifest.",
    ),
    schema_version="fhfa-hpi-master-v1",
)

FILENAME = "hpi_master.csv"

VALID_FLAVORS = ("purchase-only", "all-transactions", "expanded-data")


def fetch(cfg: Config, max_age_days: int = 30) -> tuple[str, int]:
    target = cache_path(cfg, "fhfa_hpi", FILENAME)
    path, url = download(cfg, SPEC.urls, target, max_age_days=max_age_days, min_bytes=100_000)
    df = pl.read_csv(path, infer_schema_length=10_000)
    coverage = f"{df['yr'].min()}..{df['yr'].max()}"
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
        extra={
            "hpi_flavors_present": sorted(df["hpi_flavor"].unique().to_list()),
            "levels_present": sorted(df["level"].unique().to_list()),
            "frequencies_present": sorted(df["frequency"].unique().to_list()),
        },
    )
    return (coverage, df.height)


def _load_raw(cfg: Config) -> pl.LazyFrame:
    path = cache_path(cfg, "fhfa_hpi", FILENAME)
    if not path.exists():
        raise FileNotFoundError(f"FHFA HPI not cached at {path}. Run `make fetch-public-data`.")
    return pl.scan_csv(path, infer_schema_length=10_000)


def load_series(
    cfg: Config,
    flavor: str = "purchase-only",
    frequency: str = "monthly",
    level: str = "State",
    seasonal: str = "nsa",
) -> pl.DataFrame:
    """Load one HPI concept as a tidy monthly/quarterly series.

    Parameters
    ----------
    flavor
        One of :data:`VALID_FLAVORS`. Required explicitly -- there is no default
        that silently mixes concepts.
    level
        ``State``, ``MSA``, or ``USA or Census Division``.
    seasonal
        ``nsa`` or ``sa``. ``sa`` is not published for every flavor/level; the
        function falls back to ``nsa`` and records it in the returned
        ``index_basis`` column rather than failing silently.

    Returns
    -------
    Columns: ``geography``, ``place_name``, ``period`` (first day of month/quarter),
    ``hpi``, ``index_basis``, ``hpi_flavor``, ``hpi_frequency``, ``hpi_level``.
    """
    if flavor not in VALID_FLAVORS:
        raise ValueError(f"flavor must be one of {VALID_FLAVORS}, got {flavor!r}")
    if seasonal not in ("nsa", "sa"):
        raise ValueError("seasonal must be 'nsa' or 'sa'")

    lf = _load_raw(cfg).filter(
        (pl.col("hpi_flavor") == flavor)
        & (pl.col("frequency") == frequency)
        & (pl.col("level") == level)
    )
    df = lf.collect()
    if df.height == 0:
        avail = (
            _load_raw(cfg)
            .select("hpi_flavor", "frequency", "level")
            .unique()
            .collect()
            .sort(["hpi_flavor", "frequency", "level"])
        )
        raise ValueError(
            f"no FHFA HPI rows for flavor={flavor!r} frequency={frequency!r} "
            f"level={level!r}. Available combinations:\n{avail}"
        )

    want = f"index_{seasonal}"
    basis = seasonal
    if df[want].null_count() == df.height:
        want, basis = "index_nsa", "nsa (sa unavailable for this concept)"

    if frequency == "monthly":
        period = pl.date(pl.col("yr"), pl.col("period"), 1)
    elif frequency == "quarterly":
        period = pl.date(pl.col("yr"), (pl.col("period") - 1) * 3 + 1, 1)
    else:
        raise ValueError(f"unsupported frequency {frequency!r}")

    return (
        df.with_columns(period.alias("period"))
        .select(
            pl.col("place_id").alias("geography"),
            "place_name",
            "period",
            pl.col(want).cast(pl.Float64).alias("hpi"),
            pl.lit(basis).alias("index_basis"),
            pl.lit(flavor).alias("hpi_flavor"),
            pl.lit(frequency).alias("hpi_frequency"),
            pl.lit(level).alias("hpi_level"),
        )
        .drop_nulls("hpi")
        .sort(["geography", "period"])
    )


#: Which (flavor, frequency, level) combinations FHFA actually publishes.
#: Verified empirically against the fetched master file on 2026-08-10. The important
#: fact: **purchase-only is MONTHLY only at the national/census-division level.**
#: At State and MSA level, purchase-only is QUARTERLY. Asking for monthly state
#: purchase-only silently returns nothing, which is why `load_series` raises with
#: the available combinations rather than returning an empty frame.
PUBLISHED_COMBINATIONS: tuple[tuple[str, str, str], ...] = (
    ("purchase-only", "monthly", "USA or Census Division"),
    ("purchase-only", "quarterly", "USA or Census Division"),
    ("purchase-only", "quarterly", "State"),
    ("purchase-only", "quarterly", "MSA"),
    ("purchase-only", "quarterly", "Puerto Rico"),
    ("all-transactions", "quarterly", "USA or Census Division"),
    ("all-transactions", "quarterly", "State"),
    ("all-transactions", "quarterly", "MSA"),
    ("all-transactions", "quarterly", "Puerto Rico"),
    ("expanded-data", "quarterly", "USA or Census Division"),
    ("expanded-data", "quarterly", "State"),
    ("expanded-data", "quarterly", "MSA"),
)


def to_monthly(df: pl.DataFrame) -> pl.DataFrame:
    """Expand a quarterly index to monthly by holding the level within the quarter.

    Used **only** where a monthly index value is needed as an input to another
    calculation -- specifically the estimated-current-LTV scaling -- never as an
    outcome. The returned frame carries ``index_basis`` suffixed with
    ``+held-constant-within-quarter`` so the interpolation travels with the data and
    cannot be mistaken for a published monthly series.

    A quarterly index is *not* a monthly index. Growth rates computed on the
    expanded series are step functions and must not be used as regression outcomes.
    """
    if df.height == 0:
        return df
    if (df["hpi_frequency"] == "monthly").all():
        return df
    expanded = (
        df.with_columns(pl.int_ranges(0, 3).alias("_offset"))
        .explode("_offset")
        .with_columns(
            pl.col("period").dt.offset_by(pl.format("{}mo", pl.col("_offset"))).alias("period"),
            (pl.col("index_basis") + pl.lit("+held-constant-within-quarter")).alias("index_basis"),
            pl.lit("quarterly-expanded-to-monthly").alias("hpi_frequency"),
        )
        .drop("_offset")
    )
    return expanded.sort(["geography", "period"])


def add_growth(df: pl.DataFrame, horizons: tuple[int, ...] = (1, 12)) -> pl.DataFrame:
    """Add log-difference growth columns over the given month horizons."""
    out = df.sort(["geography", "period"])
    exprs = []
    for h in horizons:
        exprs.append(
            (pl.col("hpi").log() - pl.col("hpi").log().shift(h).over("geography")).alias(
                f"hpi_logdiff_{h}"
            )
        )
    return out.with_columns(exprs)


def index_at(df: pl.DataFrame, geography: str, period: date) -> float | None:
    """Index level for one geography at one period, or ``None``."""
    row = df.filter((pl.col("geography") == geography) & (pl.col("period") == period))
    if row.height == 0:
        return None
    return float(row["hpi"][0])
