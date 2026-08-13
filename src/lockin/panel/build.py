"""Local housing-market panel.

Joins, at the geography level chosen in the config (default **state**):

* **lock-in exposure** from the active mortgage stock (monthly, and the frozen
  predetermined values),
* **HMDA** purchase and refinance originations, applications, denials (**annual**),
* **FHFA HPI** growth (monthly, purchase-only by default),
* **Census BPS** authorized units (monthly).

Frequency discipline: HMDA is annual and is **not** interpolated for estimation
(``DECISION_LOG`` D015). Two panels are therefore written:

* ``local_market_panel_monthly`` -- exposure, HPI growth, permits.
* ``local_market_panel_annual``  -- the monthly variables collapsed to annual
  means/sums, joined to HMDA. This is the panel the HMDA event studies use.

State is the default geography because it is the only level where all four sources
line up without an MSA-definition-vintage problem (``DECISION_LOG`` D007).
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from lockin.adapters import bls_laus, census_bps, fhfa_hpi, hmda, teleworkable
from lockin.config import Config
from lockin.manifest import write_manifest
from lockin.stock import load_active_stock, predetermined_exposure

STATE_FIPS_TO_ABBR: dict[int, str] = {
    1: "AL",
    2: "AK",
    4: "AZ",
    5: "AR",
    6: "CA",
    8: "CO",
    9: "CT",
    10: "DE",
    11: "DC",
    12: "FL",
    13: "GA",
    15: "HI",
    16: "ID",
    17: "IL",
    18: "IN",
    19: "IA",
    20: "KS",
    21: "KY",
    22: "LA",
    23: "ME",
    24: "MD",
    25: "MA",
    26: "MI",
    27: "MN",
    28: "MS",
    29: "MO",
    30: "MT",
    31: "NE",
    32: "NV",
    33: "NH",
    34: "NJ",
    35: "NM",
    36: "NY",
    37: "NC",
    38: "ND",
    39: "OH",
    40: "OK",
    41: "OR",
    42: "PA",
    44: "RI",
    45: "SC",
    46: "SD",
    47: "TN",
    48: "TX",
    49: "UT",
    50: "VT",
    51: "VA",
    53: "WA",
    54: "WV",
    55: "WI",
    56: "WY",
    72: "PR",
}


#: FHFA's own name for the geographic level, keyed by our panel geography.
_HPI_LEVEL: dict[str, str] = {"state": "State", "msa": "MSA"}


class _MetroLoaded(Exception):
    """Internal: the metro branch already populated this source.

    A sentinel rather than restructuring the whole try block, so the state path -- which
    is the default and is exercised by every existing test -- keeps its exact shape.
    """


class GeographyMismatchError(RuntimeError):
    """A source is keyed to a different geography than the panel.

    Deliberately **not** a ``ValueError``: the per-source ``except (FileNotFoundError,
    ValueError)`` handlers downgrade a failure to "source unavailable" and carry on,
    which is right for a missing file and wrong for this. A geography mismatch means the
    run is mis-specified, not under-supplied, and it must stop.

    Raised rather than joined. A left join between MSA-keyed exposure and state-keyed
    outcomes does not fail -- it silently produces all-null outcomes, or worse, matches
    nothing and leaves a panel that looks well-formed at the wrong level entirely. That
    is precisely how a run can report a plausible number for a geography it never
    measured.
    """


def _assert_geography_compatible(
    name: str, df: pl.DataFrame, expected: set[str], cfg: Config, notes: list[str]
) -> None:
    """Check a source's geography keys against the panel's before any join."""
    got = set(df["geography"].unique().to_list())
    overlap = expected & got
    if overlap:
        missing = len(expected - got)
        if missing:
            notes.append(
                f"{name}: {len(overlap):,} of {len(expected):,} panel geographies matched; "
                f"{missing:,} have no {name} coverage and are null, not imputed."
            )
        return
    raise GeographyMismatchError(
        f"{name} is keyed to a geography that shares NO values with the loan stock. "
        f"Panel geography is {cfg.panel.geography!r}; {name} keys look like "
        f"{sorted(got)[:3]} while the stock has {sorted(expected)[:3]}. This source is "
        f"published at a different level and CANNOT be joined -- extend the adapter to "
        f"the {cfg.panel.geography} level or drop {cfg.panel.geography} analysis for "
        "this outcome. Joining anyway would silently produce a panel at the wrong level."
    )


def build_local_panel(cfg: Config) -> tuple[pl.DataFrame, Path, list[str]]:
    """Build and persist the monthly and annual local-market panels."""
    notes: list[str] = []
    stock = load_active_stock(cfg)
    panel_geos = set(stock["geography"].unique().to_list())

    # --- HPI -----------------------------------------------------------------
    # Growth is computed at the index's OWN published frequency (quarterly at state
    # level for purchase-only), then expanded to monthly by holding within the
    # quarter. The expansion is labeled in index_basis and hpi_frequency so it can
    # never be mistaken for a published monthly series.
    try:
        hpi_q = fhfa_hpi.load_series(
            cfg,
            flavor=cfg.panel.hpi_flavor,
            frequency=cfg.panel.hpi_frequency,
            level=_HPI_LEVEL[cfg.panel.geography],
            seasonal=cfg.panel.hpi_seasonal,
        )
        per_year = 4 if cfg.panel.hpi_frequency == "quarterly" else 12
        hpi_growth = fhfa_hpi.add_growth(hpi_q, horizons=(1, per_year)).select(
            "geography",
            "period",
            "hpi",
            pl.col("hpi_logdiff_1").alias("hpi_growth_period"),
            pl.col(f"hpi_logdiff_{per_year}").alias("hpi_growth_12m"),
            "hpi_flavor",
            "index_basis",
            "hpi_frequency",
        )
        hpi = fhfa_hpi.to_monthly(hpi_growth)
        _assert_geography_compatible("FHFA HPI", hpi, panel_geos, cfg, notes)
        notes.append(
            f"FHFA HPI: {cfg.panel.hpi_flavor}, published {cfg.panel.hpi_frequency}, "
            f"{_HPI_LEVEL[cfg.panel.geography]}, basis={hpi_q['index_basis'][0]}, {hpi_q.height:,} published rows "
            f"-> {hpi.height:,} monthly rows (level held constant within period). "
            "Growth is computed at the PUBLISHED frequency."
        )
    except (FileNotFoundError, ValueError) as exc:
        hpi = None
        hpi_q = None
        per_year = 4
        notes.append(f"FHFA HPI UNAVAILABLE: {exc}")

    # --- permits -------------------------------------------------------------
    try:
        if cfg.panel.geography == "msa":
            # Already keyed by CBSA code; no FIPS translation, and no Metropolitan
            # Divisions -- BPS reports parent CBSAs (DECISION_LOG D038).
            bps = (
                census_bps.load_metro(cfg)
                .with_columns(pl.lit(None, dtype=pl.Int64).alias("permits_2to4unit_unused"))
                .drop("permits_2to4unit_unused")
            )
            _assert_geography_compatible("Census BPS (metro)", bps, panel_geos, cfg, notes)
            notes.append(
                f"Census BPS (metro): {bps.height:,} CBSA-months, vintages "
                f"{sorted(set(bps['vintage'].to_list()))}. Parent CBSAs, not divisions. "
                "Files from 2024 on use the 2023 OMB delineation; earlier ones do not."
            )
            raise _MetroLoaded
        bps = census_bps.load(cfg)
        bps = (
            bps.with_columns(
                pl.col("state_fips_int")
                .replace_strict(STATE_FIPS_TO_ABBR, default=None, return_dtype=pl.Utf8)
                .alias("geography")
            )
            .select(
                "geography",
                "period",
                "permits_total_units",
                "permits_1unit",
                "permits_2to4unit",
                "permits_5plus",
                "vintage",
            )
            .drop_nulls("geography")
        )
        _assert_geography_compatible("Census BPS", bps, panel_geos, cfg, notes)
        notes.append(
            f"Census BPS: {bps.height:,} state-months, vintages "
            f"{sorted(set(bps['vintage'].to_list()))}"
        )
    except _MetroLoaded:
        pass
    except FileNotFoundError as exc:
        bps = None
        notes.append(f"Census BPS UNAVAILABLE: {exc}")

    # --- unemployment (OPTIONAL) ---------------------------------------------
    # Addresses the local-labour-shock threat in IDENTIFICATION_STRATEGY 3.4. The
    # panel is built with or without it; absence is recorded, never silently ignored.
    laus = bls_laus.try_load_metro(cfg) if cfg.panel.geography == "msa" else bls_laus.try_load(cfg)
    if laus is not None and cfg.panel.geography == "msa":
        notes.append(
            "BLS LAUS at metro level is NOT seasonally adjusted, unlike the state "
            "series. The annual panel averages twelve months, which removes most of the "
            "seasonality; the MONTHLY metro rate is not comparable to the monthly state "
            "rate (DECISION_LOG D039)."
        )
    if laus is not None and not (set(laus["geography"].unique().to_list()) & panel_geos):
        notes.append(
            "BLS LAUS is published for STATES and shares no key with a "
            f"{cfg.panel.geography}-level panel. Dropped rather than joined; the "
            "local-labour-shock threat is UNCONTROLLED at this geography."
        )
        laus = None
    if laus is not None:
        laus = laus.select("geography", "period", "unemployment_rate")
        notes.append(
            f"BLS LAUS unemployment: {laus.height:,} state-months "
            f"({laus['period'].min()}..{laus['period'].max()}). Model-based estimates "
            "subject to annual revision; the rate conflates employment changes with "
            "labour-force participation changes."
        )
    else:
        notes.append(
            "BLS LAUS unemployment UNAVAILABLE -- the local-labour-shock threat in "
            "docs/IDENTIFICATION_STRATEGY.md 3.4 remains UNCONTROLLED in this run."
        )

    # --- teleworkable share (OPTIONAL) ---------------------------------------
    # Addresses the remote-work-exposure threat in IDENTIFICATION_STRATEGY 3.2. It is a
    # single cross-section, so it is joined WITHOUT a period key and must be used as a
    # trend control, never a level control -- see lockin.adapters.teleworkable.
    tele = teleworkable.try_load(cfg, cfg.panel.geography)
    if tele is not None and not (set(tele["geography"].unique().to_list()) & panel_geos):
        notes.append(
            "Teleworkable share shares no key with the panel geography. Dropped rather "
            "than joined; the remote-work threat is UNCONTROLLED at this geography."
        )
        tele = None
    if tele is not None:
        tele = tele.select("geography", "teleworkable_share")
        notes.append(
            f"Teleworkable share (Dingel & Neiman 2020): {tele.height:,} geographies, "
            f"mean {float(tele['teleworkable_share'].mean()):.4f}, "
            f"sd {float(tele['teleworkable_share'].std()):.4f}. Time-invariant, so it "
            "enters the event study interacted with period indicators, not as a level."
        )
    else:
        notes.append(
            "Teleworkable share UNAVAILABLE -- the remote-work-exposure threat in "
            "docs/IDENTIFICATION_STRATEGY.md 3.2 remains UNCONTROLLED in this run."
        )

    # --- monthly panel -------------------------------------------------------
    monthly = stock
    if hpi is not None:
        monthly = monthly.join(hpi, on=["geography", "period"], how="left")
    if bps is not None:
        monthly = monthly.join(bps, on=["geography", "period"], how="left")
    if laus is not None:
        monthly = monthly.join(laus, on=["geography", "period"], how="left")

    monthly = monthly.with_columns(
        pl.col("period").dt.year().alias("year"),
        pl.col("period").dt.month().alias("month"),
    ).sort(["geography", "period"])

    # --- HMDA (annual) -------------------------------------------------------
    try:
        h = hmda.load_msa(cfg) if cfg.panel.geography == "msa" else hmda.load(cfg)
        _assert_geography_compatible("HMDA", h, panel_geos, cfg, notes)
        notes.append(
            f"HMDA: {h.height:,} state-years, coverage regimes "
            f"{sorted(set(h['coverage_regime'].to_list()))}. Counts are NOT "
            "comparable across regimes."
        )
    except FileNotFoundError as exc:
        h = None
        notes.append(f"HMDA UNAVAILABLE: {exc}")

    # --- annual panel --------------------------------------------------------
    # The annual panel spans the OUTCOME years, not just the years for which we have
    # an active mortgage stock. This matters: the loan performance window starts in
    # 2021, but HMDA, HPI, and permits all reach back to 2018. Restricting the panel
    # to stock years would leave no pre-shock periods, making pre-trends untestable
    # and forcing every result to be demoted. Exposure is a geography-level constant
    # and is legitimately attached to pre-shock years; the stock aggregates are
    # simply null there, and `has_stock_data` flags which rows have them.
    outcome_years = sorted(
        set(cfg.panel.hmda_years)
        | set(cfg.panel.permits_years)
        | set(monthly["year"].unique().to_list())
    )
    geos = sorted(monthly["geography"].unique().to_list())
    skeleton = pl.DataFrame(
        {
            "geography": [g for g in geos for _ in outcome_years],
            "year": [y for _ in geos for y in outcome_years],
        }
    ).with_columns(pl.col("year").cast(pl.Int32))

    stock_annual = (
        monthly.group_by(["geography", "year"])
        .agg(
            pl.col("n_active_loans").mean().alias("mean_active_loans"),
            pl.col("total_upb").mean().alias("mean_total_upb"),
            pl.col("wavg_note_rate_upb").mean().alias("wavg_note_rate_upb"),
            pl.col("market_rate").mean().alias("mean_market_rate"),
            pl.col("mean_rate_gap").mean().alias("mean_rate_gap"),
            pl.col("mean_lockin_gap").mean().alias("mean_lockin_gap"),
            pl.col("mean_payment_gap").mean().alias("mean_payment_gap"),
            pl.col("median_payment_gap").mean().alias("median_payment_gap"),
            pl.col("prepayment_rate_monthly").mean().alias("mean_monthly_prepay_rate"),
            pl.col("n_prepayments").sum().alias("n_prepayments"),
            pl.col("n_credit_events").sum().alias("n_credit_events"),
            pl.col("refi_incentive_share").mean().alias("refi_incentive_share"),
            pl.col("median_est_current_ltv").mean().alias("median_est_current_ltv"),
            *[pl.col(c).mean().alias(c) for c in monthly.columns if c.startswith("locked_share_")],
            # HPI and permit aggregates are joined separately below, at their own
            # full year span; aggregating them here too would produce duplicate
            # columns whose non-suffixed version is null in every pre-stock year.
            pl.len().alias("n_months_in_year"),
        )
        .sort(["geography", "year"])
        .with_columns(pl.col("year").cast(pl.Int32))
    )
    if laus is not None:
        laus_annual = (
            laus.with_columns(pl.col("period").dt.year().cast(pl.Int32).alias("year"))
            .group_by(["geography", "year"])
            .agg(
                pl.col("unemployment_rate").mean().alias("unemployment_rate"),
                pl.len().alias("n_unemployment_months"),
            )
            .filter(pl.col("n_unemployment_months") == 12)
            .drop("n_unemployment_months")
        )
    else:
        laus_annual = None

    annual = skeleton.join(stock_annual, on=["geography", "year"], how="left").with_columns(
        pl.col("mean_active_loans").is_not_null().alias("has_stock_data")
    )

    # Outcome blocks are joined onto the skeleton directly, at their own full span.
    if hpi is not None:
        hpi_annual = (
            hpi.with_columns(pl.col("period").dt.year().cast(pl.Int32).alias("year"))
            .sort(["geography", "period"])
            .group_by(["geography", "year"])
            .agg(
                pl.col("hpi").last().alias("hpi_year_end"),
                pl.col("hpi").first().alias("hpi_year_start"),
                pl.col("hpi_growth_12m").last().alias("hpi_growth_12m_dec"),
            )
        )
        annual = annual.join(hpi_annual, on=["geography", "year"], how="left")
    if bps is not None:
        bps_annual = (
            bps.with_columns(pl.col("period").dt.year().cast(pl.Int32).alias("year"))
            .group_by(["geography", "year"])
            .agg(
                pl.col("permits_total_units").sum().alias("permits_total_units"),
                pl.col("permits_1unit").sum().alias("permits_1unit"),
                pl.col("permits_5plus").sum().alias("permits_5plus"),
                pl.len().alias("n_permit_months_in_year"),
            )
            # A partial year would understate annual permits; drop incomplete years
            # rather than compare a 4-month total against a 12-month total.
            .filter(pl.col("n_permit_months_in_year") == 12)
        )
        annual = annual.join(bps_annual, on=["geography", "year"], how="left")
    if laus_annual is not None:
        annual = annual.join(laus_annual, on=["geography", "year"], how="left")
    if h is not None:
        annual = annual.join(
            h.with_columns(pl.col("year").cast(pl.Int32)), on=["geography", "year"], how="left"
        )
    annual = annual.sort(["geography", "year"])
    notes.append(
        f"Annual panel spans outcome years {outcome_years[0]}..{outcome_years[-1]} for "
        f"{len(geos)} geographies. Stock aggregates are null before the loan "
        f"performance window; has_stock_data flags the rows that have them."
    )

    # Log outcomes for the event study, plus a denial rate.
    log_cols = [
        c
        for c in (
            "n_purchase_originations",
            "n_refi_originations",
            "permits_total_units",
            "permits_1unit",
            "permits_5plus",
        )
        if c in annual.columns
    ]
    # log(0) is -inf, which propagates NaN silently through OLS and yields a fitted
    # coefficient of null while the estimator still reports success. Zeros are real at
    # metro level -- 34 of 826 metro-years authorised no 5+-unit buildings at all -- so
    # they are mapped to NULL and dropped by the estimator's drop_nulls, which loses the
    # observation honestly rather than turning it into a silent NaN. log1p is deliberately
    # NOT used: it would silently change the outcome's units from a log to a
    # log-of-count-plus-one, which is a different quantity from the one named.
    annual = annual.with_columns(
        [
            pl.when(pl.col(c).cast(pl.Float64) > 0)
            .then(pl.col(c).cast(pl.Float64).log())
            .otherwise(None)
            .alias(f"log_{c}")
            for c in log_cols
        ]
    )
    if "n_purchase_denials" in annual.columns and "n_purchase_applications" in annual.columns:
        annual = annual.with_columns(
            (
                pl.col("n_purchase_denials").cast(pl.Float64)
                / pl.col("n_purchase_applications").cast(pl.Float64)
            ).alias("denial_rate")
        )
    if "log_n_purchase_originations" in annual.columns:
        annual = annual.rename({"log_n_purchase_originations": "log_purchase_originations"})
    if "log_n_refi_originations" in annual.columns:
        annual = annual.rename({"log_n_refi_originations": "log_refi_originations"})
    if "hpi_year_end" in annual.columns and "hpi_year_start" in annual.columns:
        # Within-year log change computed from the published index levels, not from a
        # sum of expanded monthly steps (which would double-count the expansion).
        annual = annual.with_columns(
            (pl.col("hpi_year_end").log() - pl.col("hpi_year_start").log()).alias("hpi_growth")
        )

    # --- predetermined exposure, merged onto both panels ---------------------
    exposure, exp_meta = predetermined_exposure(cfg)
    exp_cols = ["geography"] + [
        c
        for c in exposure.columns
        if c.startswith(("locked_share_", "coupon_share_below_", "note_rate_p"))
        or c
        in (
            "mean_payment_gap",
            "median_payment_gap",
            "mean_lockin_gap",
            "mean_rate_gap",
            "wavg_note_rate_upb",
            "wavg_note_rate_count",
            "n_active_loans",
            "total_upb",
            "coupon_share_hhi",
            "median_est_current_ltv",
            "mean_orig_cohort_year",
        )
    ]
    pre = exposure.select(exp_cols).rename({c: f"pre_{c}" for c in exp_cols if c != "geography"})
    monthly = monthly.join(pre, on="geography", how="left")
    annual = annual.join(pre, on="geography", how="left")
    notes.append(
        f"Predetermined exposure frozen at {exp_meta['as_of']} for "
        f"{exp_meta['n_geographies']} geographies; never recomputed."
    )

    # Pre-period controls: 2019-2021 price growth and refi intensity, used as
    # confound controls in the identification strategy.
    if hpi is not None:
        pre_growth = (
            hpi.filter(pl.col("period").is_between(pl.date(2019, 1, 1), pl.date(2021, 12, 1)))
            .sort(["geography", "period"])
            .group_by("geography")
            .agg(
                (pl.col("hpi").last().log() - pl.col("hpi").first().log()).alias(
                    "pre_hpi_growth_2019_2021"
                )
            )
        )
        monthly = monthly.join(pre_growth, on="geography", how="left")
        annual = annual.join(pre_growth, on="geography", how="left")
        notes.append("Pre-period control added: cumulative log HPI growth 2019-01..2021-12.")

    # Teleworkable share joins on geography only -- it has no time dimension. Joining it
    # here rather than in the monthly/annual aggregation keeps it out of any mean() that
    # would make it look time-varying.
    if tele is not None:
        monthly = monthly.join(tele, on="geography", how="left")
        annual = annual.join(tele, on="geography", how="left")
        matched = int(annual.select(pl.col("teleworkable_share").is_not_null().sum()).item())
        notes.append(
            f"Predetermined control added: teleworkable employment share, matched for "
            f"{matched:,} of {annual.height:,} geography-years."
        )

    if h is not None and "n_refi_originations" in h.columns:
        refi_intensity = (
            h.filter(pl.col("year").is_in([2020, 2021]))
            .group_by("geography")
            .agg(pl.col("n_refi_originations").sum().alias("pre_refi_count_2020_2021"))
        )
        monthly = monthly.join(refi_intensity, on="geography", how="left")
        annual = annual.join(refi_intensity, on="geography", how="left")
        notes.append("Pre-period control added: 2020-2021 HMDA refinance origination count.")

    out_m = cfg.path("processed", "local_market_panel_monthly.parquet")
    out_a = cfg.path("processed", "local_market_panel_annual.parquet")
    out_m.parent.mkdir(parents=True, exist_ok=True)
    monthly.write_parquet(out_m)
    annual.write_parquet(out_a)

    limitations = [
        "Geography is state (DECISION_LOG D007): the only level where loan, HPI, "
        "HMDA, and permit coverage align without an MSA-definition-vintage problem.",
        "HMDA is ANNUAL and is NOT interpolated for estimation (D015). HMDA "
        "outcomes are estimated on the annual panel only.",
        "HMDA counts are not comparable across the 2018 rule change or the "
        "closed-end threshold change; coverage_regime is carried through.",
        "FHFA HPI is an index, not a property value, and mixes index concepts only "
        "if you explicitly ask it to (we do not).",
        "Census BPS measures permits AUTHORIZED, not starts or completions, and the "
        "monthly 'c' vintage is preliminary.",
        "Lock-in exposure is measured on the Freddie-acquired population only; "
        "coverage varies across states and is carried as pre_n_active_loans.",
        "Refinance-origination outcomes are MECHANICALLY CONTAMINATED by pipeline "
        "exhaustion in high-exposure markets (D014); purchase originations are the "
        "headline outcome.",
    ]
    for target, name, nrows in (
        (out_m, "local_market_panel_monthly", monthly.height),
        (out_a, "local_market_panel_annual", annual.height),
    ):
        write_manifest(
            target,
            name=name,
            source="derived: active mortgage stock + FHFA HPI + HMDA + Census BPS",
            source_url="n/a (derived)",
            license_terms="Aggregate; inherits the terms of its inputs.",
            redistribution_status="aggregate",
            schema_version="local-panel-v1",
            row_count=nrows,
            geographic_level=cfg.panel.geography,
            coverage_period=(
                f"{monthly['period'].min()}..{monthly['period'].max()}"
                if name.endswith("monthly")
                else f"{annual['year'].min()}..{annual['year'].max()}"
            ),
            known_limitations=limitations,
            data_class=cfg.manifest_data_class,
            extra={
                "notes": notes,
                "exposure_meta": exp_meta,
                "columns": monthly.columns if name.endswith("monthly") else annual.columns,
            },
        )
    return (annual, out_a, notes)


def load_panel(cfg: Config, frequency: str = "annual") -> pl.DataFrame:
    p = cfg.path("processed", f"local_market_panel_{frequency}.parquet")
    if not p.exists():
        raise FileNotFoundError(f"{p} missing. Run `make build-local-panel`.")
    return pl.read_parquet(p)
