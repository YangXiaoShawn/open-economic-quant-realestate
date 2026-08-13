"""Census Building Permits Survey adapter (state, monthly).

File pattern: ``https://www2.census.gov/econ/bps/State/st{YYMM}{v}.txt`` where
``v`` is ``c`` (current / preliminary) or ``r`` (revised). Two header rows, then
one row per state.

Column blocks, in order, each ``Bldgs, Units, Value``:

    1-unit, 2-units, 3-4 units, 5+ units,
    1-unit rep, 2-units rep, 3-4 units rep, 5+ units rep

The first four blocks include **imputation** for non-responding permit-issuing
places; the ``rep`` blocks are **reported only**. We store both and default to the
imputed series.

**BPS measures permits AUTHORIZED, not housing starts and not completions.**
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import polars as pl

from lockin.adapters.base import SourceSpec, cache_path, download
from lockin.config import Config
from lockin.manifest import write_manifest

SPEC = SourceSpec(
    name="census_bps",
    source="U.S. Census Bureau Building Permits Survey",
    urls=(),
    license_terms="U.S. Government work; public domain. Cite the Census Bureau and vintage.",
    redistribution_status="public domain; cached not committed",
    geographic_level="state (MSA, county, and place files also published)",
    known_limitations=(
        "Measures permits AUTHORIZED, not starts and not completions. A permit may "
        "never be built, and the lag from permit to completion varies with cycle "
        "conditions.",
        "Only permit-issuing places are covered; construction in non-permit "
        "jurisdictions is missed.",
        "The headline series IMPUTES for non-responding permit places; the 'rep' "
        "columns are reported-only. The two differ materially in some states.",
        "The 'c' (current) monthly files are PRELIMINARY and are revised; the "
        "vintage fetched is recorded in the manifest.",
        "Universe of permit places and the imputation methodology have changed "
        "across benchmark revisions.",
    ),
    schema_version="census-bps-state-monthly-v1",
)

_BLOCKS = ("u1", "u2", "u34", "u5p", "u1_rep", "u2_rep", "u34_rep", "u5p_rep")
_METRICS = ("bldgs", "units", "value")
COLUMNS = ["date_raw", "state_fips", "region_code", "division_code", "state_name"] + [
    f"{b}_{m}" for b in _BLOCKS for m in _METRICS
]


def _url(year: int, month: int, vintage: str) -> str:
    return f"https://www2.census.gov/econ/bps/State/st{year % 100:02d}{month:02d}{vintage}.txt"


def _parse(text: str) -> pl.DataFrame:
    """Parse one monthly state file. Two header rows are skipped by hand."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    body = [ln for ln in lines if ln.split(",")[0].strip().isdigit()]
    if not body:
        raise ValueError("no data rows found in BPS file")
    raw = pl.read_csv(
        io.StringIO("\n".join(body)),
        has_header=False,
        infer_schema_length=0,
        truncate_ragged_lines=True,
    )
    ncols = min(raw.width, len(COLUMNS))
    raw = raw.select(raw.columns[:ncols])
    raw.columns = COLUMNS[:ncols]
    num_cols = [c for c in raw.columns if c not in ("date_raw", "state_name")]
    return raw.with_columns(
        [pl.col(c).str.strip_chars().cast(pl.Int64, strict=False).alias(c) for c in num_cols]
        + [pl.col("state_name").str.strip_chars().alias("state_name")]
    )


def fetch(
    cfg: Config,
    years: list[int] | None = None,
    max_age_days: int = 30,
    vintages_to_try: tuple[str, ...] = ("c",),
    max_workers: int = 6,
) -> tuple[str, int]:
    """Fetch every available month in ``years`` and write a combined parquet.

    ``vintages_to_try`` defaults to ``("c",)`` only. The revised (``r``) monthly
    files are not published for every month, and probing for them costs one
    timed-out request per missing month against a public government server. Pass
    ``("r", "c")`` to prefer revised where it exists; the vintage actually used is
    recorded per month in the manifest.
    """
    years = years or cfg.panel.permits_years
    frames: list[pl.DataFrame] = []
    vintages: dict[str, str] = {}

    def one(year: int, month: int) -> tuple[str, pl.DataFrame] | None:
        for vintage in vintages_to_try:
            target = cache_path(cfg, "census_bps", f"st{year % 100:02d}{month:02d}{vintage}.txt")
            try:
                path, _ = download(
                    cfg,
                    (_url(year, month, vintage),),
                    target,
                    max_age_days=max_age_days,
                    min_bytes=1000,
                    retries=1,
                    timeout=45,
                )
                df = _parse(path.read_text(errors="replace"))
            except Exception:
                continue
            return (
                vintage,
                df.with_columns(
                    pl.lit(date(year, month, 1)).alias("period"),
                    pl.lit(vintage).alias("vintage"),
                ),
            )
        return None

    jobs = [(y, m) for y in years for m in range(1, 13)]
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(one, y, m): (y, m) for y, m in jobs}
        for fut in as_completed(futures):
            y, m = futures[fut]
            res = fut.result()
            if res is not None:
                vintage, df = res
                vintages[f"{y}-{m:02d}"] = vintage
                frames.append(df)

    if not frames:
        raise RuntimeError(f"no BPS monthly state files could be fetched for years {years}")

    combined = pl.concat(frames, how="diagonal_relaxed").sort(["state_fips", "period"])
    out = cache_path(cfg, "census_bps", "bps_state_monthly.parquet")
    combined.write_parquet(out)
    coverage = f"{combined['period'].min()}..{combined['period'].max()}"
    write_manifest(
        out,
        name=SPEC.name,
        source=SPEC.source,
        source_url="https://www2.census.gov/econ/bps/State/",
        license_terms=SPEC.license_terms,
        redistribution_status=SPEC.redistribution_status,
        schema_version=SPEC.schema_version,
        row_count=combined.height,
        geographic_level="state",
        coverage_period=coverage,
        known_limitations=list(SPEC.known_limitations)
        + [
            f"Vintages fetched: {sorted(set(vintages.values()))}. "
            f"{len(vintages)} of {len(years) * 12} requested months were available."
        ],
        data_class="PUBLIC",
        extra={
            "vintage_by_month": vintages,
            "columns": combined.columns,
            "months_requested": len(years) * 12,
            "months_obtained": len(vintages),
        },
    )
    return (coverage, combined.height)


def load(cfg: Config) -> pl.DataFrame:
    """Load the cached monthly state permits panel with tidy outcome columns."""
    p = cache_path(cfg, "census_bps", "bps_state_monthly.parquet")
    if not p.exists():
        raise FileNotFoundError(f"Census BPS not cached at {p}. Run `make fetch-public-data`.")
    df = pl.read_parquet(p)
    return df.with_columns(
        (
            pl.col("u1_units").fill_null(0)
            + pl.col("u2_units").fill_null(0)
            + pl.col("u34_units").fill_null(0)
            + pl.col("u5p_units").fill_null(0)
        ).alias("permits_total_units"),
        pl.col("u1_units").alias("permits_1unit"),
        (pl.col("u2_units").fill_null(0) + pl.col("u34_units").fill_null(0)).alias(
            "permits_2to4unit"
        ),
        pl.col("u5p_units").alias("permits_5plus"),
        pl.col("state_fips").cast(pl.Int64).alias("state_fips_int"),
    ).sort(["state_fips", "period"])


# --- metropolitan geography ---------------------------------------------------
#
# The Census Bureau split its metro series at January 2024, into
# ``Metro (ending 2023)/ma{YYMM}{v}.txt`` and
# ``CBSA (beginning Jan 2024)/cbsa{YYMM}{v}.txt``. The split is not cosmetic: the 2024+
# files are delineated on the **2023 OMB bulletin** -- Chicago loses its Wisconsin county
# and Atlanta is renamed -- which is the same delineation change, in the same year, that
# HMDA makes (``DECISION_LOG`` D036, D037).
#
# **BPS reports the parent CBSA, not Metropolitan Divisions** -- the opposite of HMDA,
# which returns zero for a parent and only answers on divisions. Chicago appears here as
# 16980, New York as 35620, Los Angeles as 31080. Both sources are therefore keyed to the
# parent CBSA in this project so they join on a common unit; the difference is absorbed
# in the adapters rather than left for the panel builder to discover.

_METRO_BASE = "https://www2.census.gov/econ/bps/Metro%20(ending%202023)"
_CBSA_BASE = "https://www2.census.gov/econ/bps/CBSA%20(beginning%20Jan%202024)"

#: First year published under the CBSA (2023-delineation) directory.
CBSA_DIRECTORY_FIRST_YEAR: int = 2024

#: The metro files carry CSA and CBSA codes where the state file carries FIPS/region.
#: Field 4 is ``MONCOV`` in the pre-2024 files and ``HHEADER`` in the 2024+ files; it is
#: read positionally and kept unparsed, since neither is used here.
METRO_COLUMNS = ["date_raw", "csa_code", "cbsa_code", "_flag", "cbsa_name"] + [
    f"{b}_{m}" for b in _BLOCKS for m in _METRICS
]


def _metro_url(year: int, month: int, vintage: str) -> str:
    if year >= CBSA_DIRECTORY_FIRST_YEAR:
        return f"{_CBSA_BASE}/cbsa{year % 100:02d}{month:02d}{vintage}.txt"
    return f"{_METRO_BASE}/ma{year % 100:02d}{month:02d}{vintage}.txt"


def _parse_metro(text: str) -> pl.DataFrame:
    """Parse one monthly metro/CBSA file.

    Data rows are identified by a leading YYYYMM, which skips the two header rows and the
    blank line between them without depending on how many there happen to be.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    body = [ln for ln in lines if ln.split(",")[0].strip().isdigit()]
    if not body:
        raise ValueError("no data rows found in BPS metro file")
    raw = pl.read_csv(
        io.StringIO("\n".join(body)),
        has_header=False,
        infer_schema_length=0,
        truncate_ragged_lines=True,
    )
    ncols = min(raw.width, len(METRO_COLUMNS))
    raw = raw.select(raw.columns[:ncols])
    raw.columns = METRO_COLUMNS[:ncols]
    num_cols = [c for c in raw.columns if c not in ("date_raw", "cbsa_name", "cbsa_code", "_flag")]
    return raw.with_columns(
        [pl.col(c).str.strip_chars().cast(pl.Int64, strict=False).alias(c) for c in num_cols]
        + [
            pl.col("cbsa_name").str.strip_chars().alias("cbsa_name"),
            # Kept as text: a CBSA code is an identifier, and casting it to an integer
            # would drop the leading zero that some codes carry in other Census files.
            pl.col("cbsa_code").str.strip_chars().alias("cbsa_code"),
        ]
    )


METRO_FILENAME = "bps_metro_monthly.parquet"


def fetch_metro(
    cfg: Config,
    years: list[int] | None = None,
    max_age_days: int = 30,
    vintages_to_try: tuple[str, ...] = ("c",),
    max_workers: int = 6,
) -> tuple[str, int]:
    """Fetch the monthly metropolitan permit files and write a combined parquet."""
    years = years or cfg.panel.permits_years
    jobs = [(y, m) for y in years for m in range(1, 13)]
    frames: list[pl.DataFrame] = []
    misses: list[str] = []

    def one(y: int, m: int) -> pl.DataFrame | None:
        for v in vintages_to_try:
            target = cache_path(cfg, "census_bps", f"metro_{y}{m:02d}{v}.txt")
            try:
                path, _ = download(
                    cfg, (_metro_url(y, m, v),), target, max_age_days=max_age_days, min_bytes=512
                )
            except Exception:
                continue
            try:
                df = _parse_metro(path.read_text(errors="replace"))
            except ValueError:
                continue
            return df.with_columns(
                pl.lit(y).alias("year"), pl.lit(m).alias("month"), pl.lit(v).alias("vintage")
            )
        return None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(one, y, m): (y, m) for y, m in jobs}
        for fut in as_completed(futures):
            y, m = futures[fut]
            try:
                df = fut.result()
            except Exception as exc:
                misses.append(f"{y}-{m:02d}: {type(exc).__name__}")
                continue
            if df is None:
                misses.append(f"{y}-{m:02d}: unavailable")
            else:
                frames.append(df)

    if not frames:
        raise ValueError(f"no BPS metro months could be fetched. Misses: {misses[:6]}")

    df = (
        pl.concat(frames, how="diagonal_relaxed")
        .with_columns(pl.date(pl.col("year"), pl.col("month"), 1).alias("period"))
        .rename({"cbsa_code": "geography"})
        .sort(["geography", "period"])
    )
    out = cache_path(cfg, "census_bps", METRO_FILENAME)
    df.write_parquet(out)
    coverage = f"{df['period'].min()}..{df['period'].max()}"
    write_manifest(
        out,
        name=f"{SPEC.name}_metro",
        source=SPEC.source,
        source_url=f"{_METRO_BASE}/ ; {_CBSA_BASE}/",
        license_terms=SPEC.license_terms,
        redistribution_status=SPEC.redistribution_status,
        schema_version="census-bps-metro-monthly-v1",
        row_count=df.height,
        geographic_level="CBSA (parent, NOT Metropolitan Division)",
        coverage_period=coverage,
        known_limitations=[
            *SPEC.known_limitations,
            "The series is published in two directories split at January 2024, and the "
            "2024+ files are delineated on the 2023 OMB bulletin while earlier files are "
            "not. A CBSA's counties can therefore change mid-panel -- Chicago loses a "
            "Wisconsin county at that boundary. Use lockin.adapters.omb_cbsa stability "
            "verdicts to decide which codes are comparable across it.",
            "BPS reports the PARENT CBSA and never a Metropolitan Division, unlike HMDA "
            "which reports divisions and returns zero for the parent. Both are keyed to "
            "the parent CBSA in this project.",
            "Metro coverage is permit-issuing places within the CBSA, so a metro's total "
            "is not the sum of its counties' true construction.",
        ],
        data_class="PUBLIC",
        extra={
            "n_geographies": df["geography"].n_unique(),
            "n_months": df["period"].n_unique(),
            "months_missing": len(misses),
            "delineation_note": (
                "files from 2024 onward use the 2023 OMB delineation; earlier files do not"
            ),
        },
    )
    return (coverage, df.height)


def load_metro(cfg: Config) -> pl.DataFrame:
    """Load the cached metropolitan permit panel, named like the state one."""
    p = cache_path(cfg, "census_bps", METRO_FILENAME)
    if not p.exists():
        raise FileNotFoundError(f"BPS metro panel not cached at {p}. Run fetch_metro.")
    df = pl.read_parquet(p)
    return df.select(
        "geography",
        "period",
        "vintage",
        pl.col("u1_units").alias("permits_1unit"),
        pl.col("u2_units").alias("permits_2to4unit"),
        pl.col("u5p_units").alias("permits_5plus"),
        (
            pl.col("u1_units").fill_null(0)
            + pl.col("u2_units").fill_null(0)
            + pl.col("u34_units").fill_null(0)
            + pl.col("u5p_units").fill_null(0)
        ).alias("permits_total_units"),
    ).sort(["geography", "period"])
