"""Versioned OMB CBSA delineation crosswalk.

This exists because of one sentence in the Freddie Mac documentation, recorded in
``lockin.schemas.freddie`` and ``lockin.adapters.freddie_llds``:

    MSA/Metropolitan Division codes are **not** updated for changing OMB delineations.

A loan originated in 2012 carries the metro code that was in force in 2012. A loan
originated in 2021 carries the 2018-delineation code. Grouping both by "MSA code" and
calling the result a market silently pools two different geographies whenever OMB
redrew the boundary -- and OMB redrew a lot of them in 2013 and again in 2023.

This adapter does **not** fix that; nothing can, because the underlying county is not in
the loan file. What it does is make the problem *visible and auditable*:

* it downloads several published delineation vintages, not just the current one;
* it records, for every code, which vintages it exists in and whether its county
  composition, its title, or its metro/micro status ever changed;
* it exposes :func:`resolve` so that any MSA-level run can partition its loans into
  codes that mean the same thing throughout the sample and codes that do not.

The second group is not silently dropped. It is reported, and the MSA-level analysis
runs both with and without it -- see ``lockin.panel.robustness``.

**Two code systems share one field.** Freddie Mac's origination field 5 is
*"Metropolitan Statistical Area (MSA) **Or Metropolitan Division**"*. Metropolitan
Division codes are also five digits and live in the same numeric range, so a code cannot
be classified by its shape alone. Both are loaded here as first-class entries with a
``code_kind`` discriminator; treating a Metropolitan Division as a CBSA would merge, for
example, one division of a large metro with the whole of a small one.

Source: U.S. Census Bureau, Population Division, "Core Based Statistical Areas ... and
Combined Statistical Areas" delineation files, prepared from the OMB bulletin of the
stated date. Public domain, no registration.
"""

from __future__ import annotations

from typing import Final, Literal

import pandas as pd
import polars as pl

from lockin.adapters.base import AdapterError, SourceSpec, cache_path, download
from lockin.config import Config
from lockin.manifest import write_manifest

_BASE: Final[str] = (
    "https://www2.census.gov/programs-surveys/metro-micro/geographies/reference-files"
)

#: Published delineation vintages, newest first. The key is the OMB bulletin year that
#: the file is built from; it is what gets recorded on every artifact, so it must never
#: be inferred from a filename at read time.
#:
#: Vintages matter because they bracket the origination span of the loan cohorts: a
#: project analysing 2013-2022 originations spans the 2013, 2015, 2017, 2018 and 2020
#: delineations, and is then *analysed* under 2023 boundaries.
VINTAGES: Final[dict[str, str]] = {
    "2023": f"{_BASE}/2023/delineation-files/list1_2023.xlsx",
    "2020": f"{_BASE}/2020/delineation-files/list1_2020.xls",
    "2018": f"{_BASE}/2018/delineation-files/list1_Sep_2018.xls",
    "2017": f"{_BASE}/2017/delineation-files/list1.xls",
    "2015": f"{_BASE}/2015/delineation-files/list1.xls",
    "2013": f"{_BASE}/2013/delineation-files/list1.xls",
}

#: The vintage whose boundaries define the analysis geography. Everything else is used
#: only to test whether a code's meaning is stable.
DEFAULT_ANALYSIS_VINTAGE: Final[str] = "2023"

CodeKind = Literal["cbsa", "metdiv"]

SPEC = SourceSpec(
    name="omb_cbsa",
    source=(
        "U.S. Census Bureau, Population Division, Core Based Statistical Area "
        "delineation files (List 1), prepared from the Office of Management and Budget "
        "delineation bulletins."
    ),
    urls=tuple(VINTAGES.values()),
    license_terms="U.S. Government work, public domain. Cite the Census Bureau and the OMB bulletin date.",
    redistribution_status="public domain; cached copies not committed",
    geographic_level="CBSA / Metropolitan Division / county",
    known_limitations=(
        "DELINEATIONS ARE NOT A TIME SERIES. Each vintage is a snapshot. A code that "
        "appears in two vintages with different counties is the same label for two "
        "different places, not a place that grew.",
        "CODE REUSE AND RETIREMENT. OMB retires codes and occasionally assigns a new "
        "area to a code, so 'present in both vintages' is necessary but not sufficient "
        "for comparability -- the county-set comparison is what settles it.",
        "MSA VS METROPOLITAN DIVISION. Freddie Mac reports either in one field. This "
        "table carries both with a code_kind discriminator; a consumer that ignores it "
        "will silently mis-scope the eleven metros that have divisions.",
        "COUNTY-LEVEL ONLY. New England is delineated by county here, but OMB also "
        "publishes NECTAs on a town basis; NECTAs are NOT loaded and NECTA codes will "
        "not resolve.",
        "NO ZIP CROSSWALK. Freddie Mac gives a 3-digit postal prefix, which does not "
        "nest inside counties, so a loan whose MSA field is null cannot be assigned to "
        "a CBSA from this file. Those loans are excluded from MSA analysis, not imputed.",
        "PUERTO RICO CBSAs are present and are dropped downstream because FHFA HPI, "
        "HMDA and the Census BPS coverage in this project is the 50 states plus DC.",
    ),
    schema_version="census-list1-v1",
)

CROSSWALK_FILENAME = "cbsa_crosswalk.parquet"
STABILITY_FILENAME = "cbsa_stability.parquet"

_REQUIRED_COLUMNS: Final[tuple[str, ...]] = (
    "CBSA Code",
    "CBSA Title",
    "Metropolitan/Micropolitan Statistical Area",
    "FIPS State Code",
    "FIPS County Code",
)


def _find_header_row(path: str, engine: str) -> int:
    """Locate the header row by looking for ``CBSA Code``.

    The published files carry one or two banner rows above the header, and the count is
    not constant across vintages. Hard-coding ``header=2`` works for 2018 and 2023 and
    is exactly the kind of assumption that breaks silently on a vintage nobody re-checked,
    so the row is found rather than assumed.
    """
    probe = pd.read_excel(path, header=None, nrows=12, dtype=str, engine=engine)
    for i in range(len(probe)):
        row = {str(v).strip() for v in probe.iloc[i].tolist()}
        if "CBSA Code" in row:
            return i
    raise AdapterError(
        f"{path}: no header row containing 'CBSA Code' in the first 12 rows. The "
        "published layout changed -- re-verify before using."
    )


def _read_vintage(path: str, vintage: str) -> pl.DataFrame:
    """Parse one delineation file into the long county-level table."""
    engine = "openpyxl" if path.lower().endswith("x") else "xlrd"
    header = _find_header_row(path, engine)
    raw = pd.read_excel(path, header=header, dtype=str, engine=engine)

    missing = [c for c in _REQUIRED_COLUMNS if c not in raw.columns]
    if missing:
        raise AdapterError(
            f"{path}: delineation file is missing column(s) {missing}. Columns present: "
            f"{list(raw.columns)}. Do not guess a mapping -- re-verify the layout."
        )

    df = pl.from_pandas(raw.astype(object).where(pd.notna(raw), None))

    # The files end with free-text notes ("Note: ...", "Source: ...") that land in the
    # first column. Keeping only rows whose CBSA code is five digits drops them without
    # depending on how many note lines a given vintage happens to have.
    df = (
        df.select(
            pl.col("CBSA Code").cast(pl.Utf8).str.strip_chars().alias("cbsa_code"),
            pl.col("CBSA Title").cast(pl.Utf8).str.strip_chars().alias("cbsa_title"),
            pl.col("Metropolitan/Micropolitan Statistical Area")
            .cast(pl.Utf8)
            .str.strip_chars()
            .alias("area_type_raw"),
            pl.col("Metropolitan Division Code")
            .cast(pl.Utf8)
            .str.strip_chars()
            .alias("metdiv_code")
            if "Metropolitan Division Code" in df.columns
            else pl.lit(None, dtype=pl.Utf8).alias("metdiv_code"),
            pl.col("Metropolitan Division Title")
            .cast(pl.Utf8)
            .str.strip_chars()
            .alias("metdiv_title")
            if "Metropolitan Division Title" in df.columns
            else pl.lit(None, dtype=pl.Utf8).alias("metdiv_title"),
            pl.col("CSA Code").cast(pl.Utf8).str.strip_chars().alias("csa_code")
            if "CSA Code" in df.columns
            else pl.lit(None, dtype=pl.Utf8).alias("csa_code"),
            pl.col("FIPS State Code").cast(pl.Utf8).str.strip_chars().alias("state_fips"),
            pl.col("FIPS County Code").cast(pl.Utf8).str.strip_chars().alias("county_fips"),
            pl.col("Central/Outlying County")
            .cast(pl.Utf8)
            .str.strip_chars()
            .alias("central_outlying")
            if "Central/Outlying County" in df.columns
            else pl.lit(None, dtype=pl.Utf8).alias("central_outlying"),
        )
        .filter(pl.col("cbsa_code").str.contains(r"^\d{5}$"))
        .with_columns(
            pl.when(pl.col("area_type_raw").str.contains("(?i)^metro"))
            .then(pl.lit("metro"))
            .when(pl.col("area_type_raw").str.contains("(?i)^micro"))
            .then(pl.lit("micro"))
            .otherwise(pl.lit("unknown"))
            .alias("cbsa_type"),
            (pl.col("state_fips") + pl.col("county_fips")).alias("county_geoid"),
            pl.lit(vintage).alias("vintage"),
        )
    )
    if df.height == 0:
        raise AdapterError(f"{path}: parsed to zero county rows")
    return df


def _long_table(df: pl.DataFrame) -> pl.DataFrame:
    """Stack CBSAs and Metropolitan Divisions into one code table.

    Both kinds are emitted because Freddie Mac's single MSA field can hold either.
    """
    # A vintage in which no CBSA has divisions -- or any caller building this frame
    # directly -- yields an all-null metdiv column, which Polars types as Null rather
    # than Utf8. The string predicate below would then raise on dtype instead of simply
    # matching nothing, so the column is normalised first.
    df = df.with_columns(
        pl.col(c).cast(pl.Utf8) for c in ("metdiv_code", "metdiv_title", "csa_code")
    )
    cbsa = df.select(
        "vintage",
        pl.col("cbsa_code").alias("area_code"),
        pl.lit("cbsa").alias("code_kind"),
        pl.col("cbsa_title").alias("area_title"),
        "cbsa_type",
        "csa_code",
        "state_fips",
        "county_fips",
        "county_geoid",
        "central_outlying",
    )
    div = df.filter(
        pl.col("metdiv_code").is_not_null() & pl.col("metdiv_code").str.contains(r"^\d{5}$")
    ).select(
        "vintage",
        pl.col("metdiv_code").alias("area_code"),
        pl.lit("metdiv").alias("code_kind"),
        pl.col("metdiv_title").alias("area_title"),
        pl.lit("metro").alias("cbsa_type"),
        "csa_code",
        "state_fips",
        "county_fips",
        "county_geoid",
        "central_outlying",
    )
    return pl.concat([cbsa, div], how="vertical").unique(
        subset=["vintage", "area_code", "code_kind", "county_geoid"]
    )


def _stability(long: pl.DataFrame) -> pl.DataFrame:
    """For each (area_code, code_kind), does the code mean the same thing throughout?

    Composition is compared on the **sorted county GEOID set**, which is the only
    definition of "same place" available here. Title changes are tracked separately
    because OMB renames areas (adding a third principal city, say) without moving a
    single county -- a rename is cosmetic, a recomposition is not.
    """
    per_vintage = (
        long.group_by(["area_code", "code_kind", "vintage"])
        .agg(
            pl.col("county_geoid").sort().str.join(",").alias("county_set"),
            pl.col("area_title").first().alias("title"),
            pl.col("cbsa_type").first().alias("cbsa_type"),
            pl.len().alias("n_counties"),
        )
        .sort(["area_code", "code_kind", "vintage"])
    )
    agg = per_vintage.group_by(["area_code", "code_kind"]).agg(
        pl.col("vintage").sort().alias("vintages"),
        pl.col("vintage").n_unique().alias("n_vintages"),
        pl.col("county_set").n_unique().alias("n_distinct_county_sets"),
        pl.col("title").n_unique().alias("n_distinct_titles"),
        pl.col("cbsa_type").n_unique().alias("n_distinct_types"),
        pl.col("n_counties").max().alias("max_counties"),
        pl.col("n_counties").min().alias("min_counties"),
    )
    n_all = long["vintage"].n_unique()
    return (
        agg.with_columns(
            pl.col("vintages").list.join("|").alias("vintages_present"),
            (pl.col("n_vintages") == n_all).alias("in_every_vintage"),
            (pl.col("n_distinct_county_sets") == 1).alias("composition_stable"),
            (pl.col("n_distinct_titles") == 1).alias("title_stable"),
            (pl.col("n_distinct_types") == 1).alias("type_stable"),
        )
        .with_columns(
            pl.when(~pl.col("in_every_vintage"))
            .then(pl.lit("absent_in_some_vintage"))
            .when(~pl.col("composition_stable"))
            .then(pl.lit("composition_changed"))
            .when(~pl.col("type_stable"))
            .then(pl.lit("type_changed"))
            .when(~pl.col("title_stable"))
            .then(pl.lit("renamed_only"))
            .otherwise(pl.lit("stable"))
            .alias("verdict")
        )
        .drop("vintages")
        .sort(["code_kind", "area_code"])
    )


def fetch(cfg: Config, vintages: list[str] | None = None) -> dict[str, int]:
    """Download every delineation vintage and build the crosswalk plus stability table."""
    wanted = vintages or list(VINTAGES)
    unknown = [v for v in wanted if v not in VINTAGES]
    if unknown:
        raise AdapterError(f"unknown delineation vintage(s) {unknown}; published: {list(VINTAGES)}")

    frames: list[pl.DataFrame] = []
    used_urls: dict[str, str] = {}
    for v in wanted:
        url = VINTAGES[v]
        suffix = ".xlsx" if url.lower().endswith(".xlsx") else ".xls"
        target = cache_path(cfg, "omb_cbsa", f"list1_{v}{suffix}")
        # Delineations are revised only when OMB issues a bulletin, so a long max_age
        # is correct; a 7-day refresh would re-download six files every week for nothing.
        path, used = download(
            cfg, (url,), target, max_age_days=3650, expect_text=False, min_bytes=10_000
        )
        used_urls[v] = used
        frames.append(_read_vintage(str(path), v))

    long = _long_table(pl.concat(frames, how="vertical"))
    stab = _stability(long)

    cw_target = cache_path(cfg, "omb_cbsa", CROSSWALK_FILENAME)
    st_target = cache_path(cfg, "omb_cbsa", STABILITY_FILENAME)
    long.sort(["vintage", "code_kind", "area_code", "county_geoid"]).write_parquet(cw_target)
    stab.write_parquet(st_target)

    verdicts = {
        str(r["verdict"]): int(r["n"])
        for r in stab.group_by("verdict").agg(pl.len().alias("n")).iter_rows(named=True)
    }

    for target, name, rows, level in (
        (cw_target, "omb_cbsa_crosswalk", long.height, "CBSA/metdiv x county x vintage"),
        (st_target, "omb_cbsa_stability", stab.height, "CBSA/metdiv"),
    ):
        write_manifest(
            target,
            name=name,
            source=SPEC.source,
            source_url="; ".join(f"{v}={u}" for v, u in used_urls.items()),
            license_terms=SPEC.license_terms,
            redistribution_status=SPEC.redistribution_status,
            schema_version=SPEC.schema_version,
            row_count=rows,
            geographic_level=level,
            coverage_period=f"OMB delineation vintages {min(wanted)}..{max(wanted)}",
            known_limitations=list(SPEC.known_limitations),
            data_class="PUBLIC",
            extra={
                "vintages": sorted(wanted),
                "analysis_vintage": DEFAULT_ANALYSIS_VINTAGE,
                "n_codes": stab.height,
                "stability_verdicts": verdicts,
                "note": (
                    "Freddie Mac MSA codes are as-of-origination and are NOT restated "
                    "for redelineation. Any code whose verdict is not 'stable' means "
                    "different geography in different cohorts."
                ),
            },
        )

    return {"crosswalk_rows": long.height, "codes": stab.height, **verdicts}


def load_crosswalk(cfg: Config) -> pl.DataFrame:
    p = cache_path(cfg, "omb_cbsa", CROSSWALK_FILENAME)
    if not p.exists():
        raise FileNotFoundError(
            f"CBSA crosswalk not cached at {p}. Run `make fetch-public-data`. "
            "MSA-level analysis REQUIRES it -- there is no fallback, because grouping "
            "unversioned MSA codes is the error this table exists to prevent."
        )
    return pl.read_parquet(p)


def load_stability(cfg: Config) -> pl.DataFrame:
    p = cache_path(cfg, "omb_cbsa", STABILITY_FILENAME)
    if not p.exists():
        raise FileNotFoundError(f"CBSA stability table not cached at {p}")
    return pl.read_parquet(p)


def try_load_stability(cfg: Config) -> pl.DataFrame | None:
    try:
        return load_stability(cfg)
    except (FileNotFoundError, OSError):
        return None


def area_states(cfg: Config, vintage: str = DEFAULT_ANALYSIS_VINTAGE) -> pl.DataFrame:
    """One row per code in ``vintage``: title, type, and the states it spans.

    ``n_states > 1`` matters for this project because every outcome series except the
    FHFA MSA index is published by state. A multi-state CBSA cannot be matched to a
    single state's HMDA or permit total without an allocation assumption.
    """
    cw = load_crosswalk(cfg).filter(pl.col("vintage") == vintage)
    if cw.height == 0:
        raise AdapterError(f"vintage {vintage!r} not present in the cached crosswalk")
    return (
        cw.group_by(["area_code", "code_kind"])
        .agg(
            pl.col("area_title").first().alias("area_title"),
            pl.col("cbsa_type").first().alias("cbsa_type"),
            pl.col("state_fips").unique().sort().alias("state_fips_list"),
            pl.col("state_fips").n_unique().alias("n_states"),
            pl.len().alias("n_counties"),
        )
        .with_columns(pl.col("state_fips_list").list.join(",").alias("state_fips_csv"))
        .drop("state_fips_list")
        .sort(["code_kind", "area_code"])
    )


def resolve(
    cfg: Config,
    codes: list[str],
    vintage: str = DEFAULT_ANALYSIS_VINTAGE,
) -> pl.DataFrame:
    """Classify observed MSA codes against the delineation and the stability table.

    Returns one row per input code with ``code_kind``, ``area_title``, the stability
    ``verdict``, and ``usable_for_panel`` -- true for a metropolitan CBSA that is present
    in the analysis vintage and whose **county composition** never moved.

    Note the criterion is composition, not the verdict string: ``renamed_only`` counts as
    usable, because a title change with an identical county set is the same geography
    under a new name (OMB adds and drops principal cities from titles without moving a
    boundary). ``composition_changed`` does not, even when the county *count* is
    unchanged -- the 2023 Atlanta CBSA swapped one county for another at constant size.

    Micropolitan areas are resolvable but not usable: FHFA does not publish an MSA index
    for them, so they would enter the panel with a missing outcome.

    ``absent_in_some_vintage`` is excluded by default but recorded, because whether it
    matters depends on the cohort span -- a code absent only from the 2013 delineation is
    harmless for a 2015+ sample. ``lockin.panel.robustness`` re-runs with it included.
    """
    stab = load_stability(cfg)
    present = (
        load_crosswalk(cfg)
        .filter(pl.col("vintage") == vintage)
        .group_by(["area_code", "code_kind"])
        .agg(
            pl.col("area_title").first().alias("area_title"),
            pl.col("cbsa_type").first().alias("cbsa_type"),
        )
    )
    obs = pl.DataFrame({"area_code": [str(c).strip() for c in codes]}).unique()
    out = (
        obs.join(present, on="area_code", how="left")
        .join(
            stab.select("area_code", "code_kind", "verdict"),
            on=["area_code", "code_kind"],
            how="left",
        )
        .with_columns(
            pl.col("code_kind").is_null().alias("unknown_code"),
            pl.col("verdict").fill_null("not_in_any_vintage"),
        )
        .with_columns(
            (
                ~pl.col("unknown_code")
                & (pl.col("cbsa_type") == "metro")
                & pl.col("verdict").is_in(["stable", "renamed_only"])
            ).alias("usable_for_panel")
        )
        .sort("area_code")
    )
    return out


#: Which delineation vintage HMDA reports under, by data year.
#:
#: **Established empirically, not from an effective-date rule.** OMB bulletins take
#: effect in HMDA with a lag that is not documented anywhere this project could find, and
#: guessing it produces silent zeros rather than errors (``DECISION_LOG`` D036). Each
#: boundary below was located by querying the CFPB aggregations API for codes that exist
#: in only one vintage and seeing which years return a real count:
#:
#: ===========  =========  ==========================================================
#: HMDA year    vintage    evidence
#: ===========  =========  ==========================================================
#: 2018         2017       Chicago division is 16974 (2015/2017 vintages only);
#:                         16984 returns 0
#: 2019-2023    2018       Chicago division is 16984; 16974 returns 0 from 2019 on
#: 2024+        2023       Atlanta division 12054 and Arlington division 11694 appear;
#:                         Atlanta CBSA 12060 and Washington division 47894 drop to 0
#: ===========  =========  ==========================================================
#:
#: The 2020 bulletin changed nothing that separates it from 2018 for these codes, so
#: 2018 is used for the whole 2019-2023 span.
_HMDA_YEAR_VINTAGE: Final[tuple[tuple[int, str], ...]] = (
    (2024, "2023"),
    (2019, "2018"),
    (2018, "2017"),
)

#: Below this year the mapping has not been checked against the API. HMDA's public
#: aggregations start at 2018 anyway.
HMDA_FIRST_VERIFIED_YEAR: Final[int] = 2018


def vintage_for_hmda_year(year: int) -> str:
    """Delineation vintage that HMDA reports under for ``year``.

    Raises for years the mapping was never verified against, rather than extrapolating:
    an unverified guess here yields zero counts that look like real observations.
    """
    if year < HMDA_FIRST_VERIFIED_YEAR:
        raise AdapterError(
            f"HMDA year {year} is before {HMDA_FIRST_VERIFIED_YEAR}, the earliest year "
            "whose delineation vintage this project verified against the API. Do not "
            "extrapolate -- an unverified vintage returns silent zeros, not errors."
        )
    for first_year, vintage in _HMDA_YEAR_VINTAGE:
        if year >= first_year:
            return vintage
    raise AdapterError(f"no delineation vintage mapped for HMDA year {year}")


def hmda_geographies(cfg: Config, year: int) -> pl.DataFrame:
    """The codes HMDA actually reports for ``year``, one row per reporting area.

    HMDA reports a **Metropolitan Division wherever one exists** and the CBSA otherwise;
    querying a divided metro by its parent CBSA code returns zero without error, which
    would silently empty out New York, Los Angeles, Chicago, Dallas and every other large
    metro. This resolves each metropolitan CBSA to the code HMDA will answer on.

    Returns ``report_code`` (what to send to the API), ``cbsa_code`` (the parent, which is
    the stable analysis unit), ``code_kind`` and ``area_title``.
    """
    vintage = vintage_for_hmda_year(year)
    cw = load_crosswalk(cfg).filter(pl.col("vintage") == vintage)
    if cw.height == 0:
        raise AdapterError(
            f"delineation vintage {vintage!r} (needed for HMDA {year}) is not in the "
            f"cached crosswalk. Fetch it: omb_cbsa.fetch(cfg, vintages=['{vintage}'])."
        )
    metro = cw.filter((pl.col("code_kind") == "cbsa") & (pl.col("cbsa_type") == "metro"))
    divisions = cw.filter(pl.col("code_kind") == "metdiv")

    # A division's parent is the CBSA sharing its counties. Join on county rather than
    # trusting a code prefix -- division codes are not derived from their parent's.
    parent = (
        divisions.join(
            metro.select("area_code", "county_geoid").rename({"area_code": "cbsa_code"}),
            on="county_geoid",
            how="inner",
        )
        .group_by(["area_code", "cbsa_code"])
        .agg(pl.col("area_title").first())
        .rename({"area_code": "report_code"})
        .with_columns(pl.lit("metdiv").alias("code_kind"))
    )
    divided = set(parent["cbsa_code"].to_list())
    undivided = (
        metro.filter(~pl.col("area_code").is_in(list(divided)))
        .group_by("area_code")
        .agg(pl.col("area_title").first())
        .select(
            pl.col("area_code").alias("report_code"),
            pl.col("area_code").alias("cbsa_code"),
            "area_title",
            pl.lit("cbsa").alias("code_kind"),
        )
    )
    return (
        pl.concat([parent.select(undivided.columns), undivided], how="vertical")
        .unique(subset=["report_code"])
        .sort("report_code")
    )


def to_parent_cbsa(
    cfg: Config, codes: list[str], vintage: str = DEFAULT_ANALYSIS_VINTAGE
) -> pl.DataFrame:
    """Map observed codes to the **parent CBSA**, which is the analysis unit.

    Freddie Mac's field 5 holds an MSA *or a Metropolitan Division*, and in the real
    Standard dataset 4,657,458 loans of 20,199,214 carry a division code -- the largest
    metros in the country. Left unmapped they match nothing in the Census permit series
    or in LAUS, both of which report parent CBSAs, and the panel silently loses New York,
    Los Angeles, Chicago, Dallas, Washington and the rest.

    A division's parent is found by shared counties rather than by any relation between
    the codes, because there is none: Chicago's division is 16984 under parent 16980, but
    Boston's is 14454 under 14460.

    Returns ``area_code`` -> ``cbsa_code`` plus ``code_kind`` and ``area_title``. Codes
    that resolve to nothing keep a null ``cbsa_code`` and are the caller's to drop or
    report -- never to guess at.
    """
    cw = load_crosswalk(cfg).filter(pl.col("vintage") == vintage)
    if cw.height == 0:
        raise AdapterError(f"vintage {vintage!r} is not in the cached crosswalk")

    metro_counties = cw.filter(pl.col("code_kind") == "cbsa").select(
        pl.col("area_code").alias("cbsa_code"), "county_geoid"
    )
    div_parent = (
        cw.filter(pl.col("code_kind") == "metdiv")
        .join(metro_counties, on="county_geoid", how="inner")
        .group_by("area_code")
        .agg(pl.col("cbsa_code").mode().first())
    )
    self_parent = (
        cw.filter(pl.col("code_kind") == "cbsa")
        .select("area_code")
        .unique()
        .with_columns(pl.col("area_code").alias("cbsa_code"))
    )
    mapping = pl.concat([div_parent, self_parent], how="vertical").unique(subset=["area_code"])

    attrs = cw.group_by("area_code").agg(
        pl.col("area_title").first(), pl.col("code_kind").first(), pl.col("cbsa_type").first()
    )
    obs = pl.DataFrame({"area_code": [str(c).strip() for c in codes]}).unique()
    return (
        obs.join(mapping, on="area_code", how="left")
        .join(attrs, on="area_code", how="left")
        .sort("area_code")
    )
