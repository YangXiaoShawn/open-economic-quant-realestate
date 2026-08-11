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
