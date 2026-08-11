"""Robustness and falsification grid.

Every cell writes one row with an explicit verdict. **Failures are surfaced, not
dropped** -- `reports/failed_hypotheses.md` is generated from the failing rows.

Verdicts:

``consistent``     same sign as the baseline and significant at 10%.
``attenuated``     same sign, |coef| < half the baseline, or insignificant.
``sign_flip``      opposite sign and significant -- the specification contradicts the baseline.
``insignificant``  |t| < 1.645.
``not_estimable``  the specification could not be fit (too few clusters, no variation).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from lockin.artifacts import write_artifact
from lockin.config import Config
from lockin.panel.build import load_panel
from lockin.panel.eventstudy import POPULATION_STATEMENT, did_two_period, event_study
from lockin.provenance import collect_source_versions, run_context
from lockin.stock import predetermined_exposure

Z10 = 1.645

#: Alternative dates at which to freeze the coupon distribution (DECISION_LOG D008).
ALTERNATIVE_PRE_SHOCK_DATES: tuple[str, ...] = ("2021-06", "2022-03")

#: Alternative FHFA index concepts. NEVER mixed within a series -- each is used to
#: rebuild the pandemic-boom control on its own terms.
ALTERNATIVE_HPI_FLAVORS: tuple[str, ...] = ("all-transactions", "expanded-data")

#: Loan sub-samples on which exposure is rebuilt from scratch. Each restricts the loan
#: population entering the frozen coupon distribution.
LOAN_SUBSAMPLES: tuple[tuple[str, pl.Expr, str], ...] = (
    (
        "purchase-origination loans only",
        pl.col("loan_purpose") == "P",
        "refinance-originated loans have a different seasoning and equity profile; "
        "restricting to purchase originations removes that composition",
    ),
    (
        "primary residence only",
        pl.col("occupancy_status") == "P",
        "investor and second-home borrowers face different mobility and financing "
        "constraints, so lock-in should operate differently for them",
    ),
    (
        "excluding manufactured housing",
        pl.col("property_type") != "MH",
        "manufactured housing has distinct financing and turnover dynamics",
    ),
)


def load_config_like(cfg: Config, rates_series: str) -> Config:
    """Shallow copy of the config with a different market-rate series.

    Used by the market-rate-series cell so that exposure is genuinely recomputed at the
    alternative series' post-shock level, rather than relabelled.
    """
    import copy

    alt = copy.deepcopy(cfg)
    alt.rates.series = rates_series
    return alt


def _pre_growth_under_flavor(cfg: Config, flavor: str) -> pl.DataFrame | None:
    """Rebuild the 2019-21 price-growth control from a different HPI concept."""
    from lockin.adapters import fhfa_hpi

    try:
        hpi = fhfa_hpi.load_series(
            cfg,
            flavor=flavor,
            frequency="quarterly",
            level="State",
            seasonal=cfg.panel.hpi_seasonal,
        )
    except (FileNotFoundError, ValueError):
        return None
    sub = hpi.filter(pl.col("period").is_between(pl.date(2019, 1, 1), pl.date(2021, 12, 1)))
    if sub.height == 0:
        return None
    return (
        sub.sort(["geography", "period"])
        .group_by("geography")
        .agg((pl.col("hpi").last().log() - pl.col("hpi").first().log()).alias("_alt_pre_growth"))
    )


def _exposure_on_subsample(cfg: Config, restrict: pl.Expr) -> pl.DataFrame | None:
    """Recompute predetermined exposure on a restricted loan population.

    Mirrors :func:`lockin.stock.predetermined_exposure` but applies ``restrict`` to the
    pre-shock loan set first, so the frozen coupon shares are those of the sub-sample.
    """
    from datetime import date

    from lockin.episodes import scan_episodes
    from lockin.stock import post_shock_rate_level

    as_of = cfg.event_study.pre_shock_date
    y, m = map(int, as_of.split("-"))
    cut = date(y, m, 1)
    post_rate, _ = post_shock_rate_level(cfg)
    geo = "property_state" if cfg.panel.geography == "state" else "msa_code"

    pre = (
        scan_episodes(cfg)
        .filter((pl.col("period") == cut) & restrict)
        .select(
            pl.col(geo).alias("geography"),
            "note_rate",
            "upb_start_of_month",
        )
        .drop_nulls()
        .collect()
    )
    if pre.height == 0:
        return None

    cf_gap = pl.lit(post_rate) - pl.col("note_rate")
    exprs: list[pl.Expr] = []
    for t in cfg.lockin.thresholds_bp:
        locked = cf_gap > t / 100.0
        exprs.append(locked.mean().alias(f"locked_share_count_{t}"))
        exprs.append(
            (
                pl.when(locked).then(pl.col("upb_start_of_month")).otherwise(0.0).sum()
                / pl.col("upb_start_of_month").sum()
            ).alias(f"locked_share_upb_{t}")
        )
    out = pre.group_by("geography").agg(pl.len().alias("n_loans"), *exprs)
    return out.filter(pl.col("n_loans") >= max(cfg.panel.min_loans_per_geography // 2, 10))


def _verdict(coef: float | None, se: float | None, baseline: float | None) -> str:
    if coef is None or se is None or se <= 0:
        return "not_estimable"
    t = coef / se
    if abs(t) < Z10:
        return "insignificant"
    if baseline is None or baseline == 0:
        return "consistent"
    if coef * baseline < 0:
        return "sign_flip"
    if abs(coef) < 0.5 * abs(baseline):
        return "attenuated"
    return "consistent"


def run_robustness_grid(cfg: Config) -> tuple[Path, int]:
    """Run the grid and write both a parquet table and an artifact."""
    annual = load_panel(cfg, "annual")
    shock_year = int(cfg.event_study.shock_date.split("-")[0])
    ref_year = shock_year - 1
    base_exposure = f"pre_{cfg.event_study.exposure_measure}"
    headline = "log_purchase_originations"
    if headline not in annual.columns:
        return (_write_empty(cfg, "headline outcome absent from the panel"), 0)

    controls = [c for c in ("pre_hpi_growth_2019_2021",) if c in annual.columns]
    rows: list[dict[str, Any]] = []

    baseline = did_two_period(annual, headline, base_exposure, "year", shock_year, controls)
    base_coef = baseline.get("coef")
    rows.append(
        _row(
            "baseline",
            "baseline",
            headline,
            base_exposure,
            baseline,
            base_coef,
            "the reference specification",
        )
    )

    # -- alternative exposure definitions ------------------------------------
    for alt in cfg.event_study.alternative_exposures:
        col = f"pre_{alt}"
        if col not in annual.columns:
            continue
        r = did_two_period(annual, headline, col, "year", shock_year, controls)
        rows.append(
            _row(
                "exposure_definition",
                alt,
                headline,
                col,
                r,
                base_coef,
                "alternative lock-in exposure measure",
            )
        )

    # -- alternative thresholds ---------------------------------------------
    for t in cfg.lockin.thresholds_bp:
        for stem in ("locked_share_upb", "locked_share_count", "coupon_share_below"):
            col = f"pre_{stem}_{t}"
            if col not in annual.columns or col == base_exposure:
                continue
            r = did_two_period(annual, headline, col, "year", shock_year, controls)
            rows.append(
                _row(
                    "threshold",
                    f"{stem}_{t}bp",
                    headline,
                    col,
                    r,
                    base_coef,
                    f"{stem} at a {t} bp threshold",
                )
            )

    # -- weighting: count vs UPB --------------------------------------------
    for col, label in (
        (f"pre_locked_share_count_{cfg.lockin.thresholds_bp[1]}", "loan-count weights"),
        (f"pre_locked_share_upb_{cfg.lockin.thresholds_bp[1]}", "UPB weights"),
    ):
        if col in annual.columns:
            r = did_two_period(annual, headline, col, "year", shock_year, controls)
            rows.append(
                _row(
                    "weighting",
                    label,
                    headline,
                    col,
                    r,
                    base_coef,
                    "loan-count vs UPB weighting of the exposure measure",
                )
            )

    # -- controls on/off ----------------------------------------------------
    r = did_two_period(annual, headline, base_exposure, "year", shock_year, [])
    rows.append(
        _row(
            "controls",
            "no pre-period controls",
            headline,
            base_exposure,
            r,
            base_coef,
            "drops the 2019-21 price-growth control -- if the estimate moves a "
            "lot, the pandemic-boom confound is doing real work",
        )
    )
    extra = [
        c
        for c in (
            "pre_hpi_growth_2019_2021",
            "pre_refi_count_2020_2021",
            "pre_wavg_note_rate_upb",
            "median_est_current_ltv",
        )
        if c in annual.columns
    ]
    r = did_two_period(annual, headline, base_exposure, "year", shock_year, extra)
    rows.append(
        _row(
            "controls",
            "all pre-period controls",
            headline,
            base_exposure,
            r,
            base_coef,
            "adds refi intensity, pre-shock coupon level, and equity",
        )
    )

    # -- alternative pre-shock dates ----------------------------------------
    # The exposure measure is rebuilt from scratch at each date, so this genuinely
    # varies the treatment rather than relabelling it.
    for alt_date in ALTERNATIVE_PRE_SHOCK_DATES:
        if alt_date == cfg.event_study.pre_shock_date:
            continue
        try:
            alt_exposure, _meta = predetermined_exposure(cfg, as_of=alt_date)
            col = cfg.event_study.exposure_measure
            if col not in alt_exposure.columns:
                continue
            merged = annual.drop([c for c in annual.columns if c == "_alt_exposure"]).join(
                alt_exposure.select("geography", pl.col(col).alias("_alt_exposure")),
                on="geography",
                how="left",
            )
            r = did_two_period(merged, headline, "_alt_exposure", "year", shock_year, controls)
            rows.append(
                _row(
                    "pre_shock_date",
                    f"exposure frozen at {alt_date}",
                    headline,
                    f"{col}@{alt_date}",
                    r,
                    base_coef,
                    "the coupon distribution is re-frozen at a different pre-shock date; "
                    "an estimate that depends on the date is an estimate of the date",
                )
            )
        except Exception as exc:
            rows.append(
                _row(
                    "pre_shock_date",
                    f"exposure frozen at {alt_date}",
                    headline,
                    "n/a",
                    {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"},
                    base_coef,
                    "alternative pre-shock date",
                )
            )

    # -- alternative market-rate series -------------------------------------
    # Exposure is the frozen coupon distribution evaluated at a national post-shock
    # rate LEVEL, so swapping PMMS 30-year for 15-year changes that level.
    for alt_series in cfg.rates.alternative_series:
        try:
            alt_cfg = load_config_like(cfg, rates_series=alt_series)
            alt_exposure, meta = predetermined_exposure(alt_cfg)
            col = cfg.event_study.exposure_measure
            if col not in alt_exposure.columns:
                continue
            merged = annual.join(
                alt_exposure.select("geography", pl.col(col).alias("_alt_rate_exposure")),
                on="geography",
                how="left",
            )
            r = did_two_period(merged, headline, "_alt_rate_exposure", "year", shock_year, controls)
            rows.append(
                _row(
                    "market_rate_series",
                    f"{alt_series} instead of {cfg.rates.series}",
                    headline,
                    f"{col}@{alt_series}",
                    r,
                    base_coef,
                    f"post-shock national rate level "
                    f"{meta['rate_shift']['post_shock_rate_level_pct']:.2f}% under "
                    f"{alt_series}; a 15-year series implies a different gap for the "
                    "same coupon distribution",
                )
            )
        except Exception as exc:
            rows.append(
                _row(
                    "market_rate_series",
                    f"{alt_series} instead of {cfg.rates.series}",
                    headline,
                    "n/a",
                    {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"},
                    base_coef,
                    "alternative market-rate series",
                )
            )

    # -- alternative HPI concepts -------------------------------------------
    # purchase-only / all-transactions / expanded-data are DIFFERENT index concepts.
    # They are never mixed into one series; each is used to rebuild the pandemic-boom
    # control separately.
    for flavor in ALTERNATIVE_HPI_FLAVORS:
        if flavor == cfg.panel.hpi_flavor:
            continue
        try:
            alt_ctrl = _pre_growth_under_flavor(cfg, flavor)
            if alt_ctrl is None:
                continue
            merged = annual.join(alt_ctrl, on="geography", how="left")
            r = did_two_period(
                merged, headline, base_exposure, "year", shock_year, ["_alt_pre_growth"]
            )
            rows.append(
                _row(
                    "hpi_concept",
                    f"pandemic control from {flavor} HPI",
                    headline,
                    base_exposure,
                    r,
                    base_coef,
                    f"the 2019-21 price-growth control is rebuilt from the {flavor} "
                    "index concept rather than purchase-only; concepts are never mixed "
                    "within a series",
                )
            )
        except Exception as exc:
            rows.append(
                _row(
                    "hpi_concept",
                    f"pandemic control from {flavor} HPI",
                    headline,
                    base_exposure,
                    {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"},
                    base_coef,
                    "alternative HPI concept",
                )
            )

    # -- loan sub-samples: exposure rebuilt on a restricted loan population ---
    for label, expr, why in LOAN_SUBSAMPLES:
        try:
            sub_exposure = _exposure_on_subsample(cfg, expr)
            col = cfg.event_study.exposure_measure
            if sub_exposure is None or col not in sub_exposure.columns:
                continue
            merged = annual.join(
                sub_exposure.select("geography", pl.col(col).alias("_sub_exposure")),
                on="geography",
                how="left",
            )
            r = did_two_period(merged, headline, "_sub_exposure", "year", shock_year, controls)
            rows.append(
                _row("loan_subsample", label, headline, f"{col}|{label}", r, base_coef, why)
            )
        except Exception as exc:
            rows.append(
                _row(
                    "loan_subsample",
                    label,
                    headline,
                    "n/a",
                    {"status": "skipped", "reason": f"{type(exc).__name__}: {exc}"},
                    base_coef,
                    why,
                )
            )

    # -- sample exclusions --------------------------------------------------
    if "pre_hpi_growth_2019_2021" in annual.columns:
        cut = annual.select(pl.col("pre_hpi_growth_2019_2021").quantile(0.9)).item()
        sub = annual.filter(pl.col("pre_hpi_growth_2019_2021") < cut)
        r = did_two_period(sub, headline, base_exposure, "year", shock_year, controls)
        rows.append(
            _row(
                "sample",
                "exclude top-decile 2019-21 price-boom markets",
                headline,
                base_exposure,
                r,
                base_coef,
                "removes the markets where the pandemic demand shift was largest",
            )
        )
    if "pre_refi_count_2020_2021" in annual.columns:
        cut = annual.select(pl.col("pre_refi_count_2020_2021").quantile(0.9)).item()
        sub = annual.filter(pl.col("pre_refi_count_2020_2021") < cut)
        r = did_two_period(sub, headline, base_exposure, "year", shock_year, controls)
        rows.append(
            _row(
                "sample",
                "exclude top-decile refinance-intensity markets",
                headline,
                base_exposure,
                r,
                base_coef,
                "removes markets whose refi pipeline was most exhausted",
            )
        )

    # -- HMDA coverage regimes ----------------------------------------------
    if "coverage_regime" in annual.columns:
        for regime in sorted(annual["coverage_regime"].drop_nulls().unique().to_list()):
            sub = annual.filter(pl.col("coverage_regime") == regime)
            r = did_two_period(sub, headline, base_exposure, "year", shock_year, controls)
            rows.append(
                _row(
                    "hmda_coverage",
                    f"regime={regime}",
                    headline,
                    base_exposure,
                    r,
                    base_coef,
                    "HMDA reporting-threshold regimes are not comparable; a "
                    "within-regime estimate avoids splicing across the break",
                )
            )

    # -- balanced panel -----------------------------------------------------
    complete = (
        annual.drop_nulls([headline, base_exposure]).group_by("geography").agg(pl.len().alias("n"))
    )
    if complete.height:
        full = int(complete["n"].max() or 0)
        keep = complete.filter(pl.col("n") == full)["geography"]
        sub = annual.filter(pl.col("geography").is_in(keep))
        r = did_two_period(sub, headline, base_exposure, "year", shock_year, controls)
        rows.append(
            _row(
                "panel_balance",
                f"balanced panel ({full} years)",
                headline,
                base_exposure,
                r,
                base_coef,
                "restricts to geographies observed in every year",
            )
        )

    # -- placebo shock dates ------------------------------------------------
    for pdate in cfg.event_study.placebo_shock_dates:
        py = int(pdate.split("-")[0])
        sub = annual.filter(pl.col("year") < shock_year)
        r = did_two_period(sub, headline, base_exposure, "year", py, controls)
        v = _verdict(r.get("coef"), r.get("std_err"), base_coef)
        # For a placebo, "insignificant" is the PASS. Relabel accordingly.
        rows.append(
            _row(
                "placebo_date",
                f"placebo shock at {pdate}",
                headline,
                base_exposure,
                r,
                base_coef,
                "a placebo PASSES when it is insignificant; a significant effect of "
                "the same sign is evidence against the lock-in interpretation",
                verdict_override="placebo_pass" if v == "insignificant" else "placebo_FAIL",
            )
        )

    # -- placebo outcomes ---------------------------------------------------
    for po, why in (
        (
            "log_permits_5plus",
            "multifamily permits are renter-demand driven and should not respond to owner lock-in",
        ),
        ("denial_rate", "the denial rate proxies credit conditions, not lock-in"),
    ):
        if po not in annual.columns:
            continue
        r = did_two_period(annual, po, base_exposure, "year", shock_year, controls)
        v = _verdict(r.get("coef"), r.get("std_err"), None)
        rows.append(
            _row(
                "placebo_outcome",
                po,
                po,
                base_exposure,
                r,
                None,
                why,
                verdict_override="placebo_pass" if v == "insignificant" else "placebo_FAIL",
            )
        )

    # -- other real outcomes at the baseline spec ---------------------------
    for outcome in cfg.event_study.outcomes:
        if outcome == headline or outcome not in annual.columns:
            continue
        r = did_two_period(annual, outcome, base_exposure, "year", shock_year, controls)
        es = event_study(
            annual, outcome, base_exposure, "year", ref_year, controls, seed=cfg.survival.seed
        )
        pt = es.get("pretrend_test", {}) if es.get("status") == "ok" else {}
        rows.append(
            _row(
                "other_outcome",
                outcome,
                outcome,
                base_exposure,
                r,
                None,
                "the same specification applied to another outcome",
            )
            | {
                "pretrend_pvalue": pt.get("pvalue"),
                "pretrend_passes": pt.get("passes_at_alpha_0.10"),
            }
        )

    grid = pl.DataFrame(rows, infer_schema_length=None)
    out = cfg.path("outputs", "robustness")
    out.mkdir(parents=True, exist_ok=True)
    path = out / "grid.parquet"
    grid.write_parquet(path)

    failing = grid.filter(pl.col("verdict").is_in(["sign_flip", "not_estimable", "placebo_FAIL"]))
    ctx = run_context(cfg, source_versions=collect_source_versions(cfg))
    write_artifact(
        cfg,
        ctx,
        group="robustness",
        name="robustness_grid",
        evidence_tier="quasi_experimental",
        population=POPULATION_STATEMENT,
        geography=cfg.panel.geography,
        outcome_definition=f"baseline outcome: {headline} (log HMDA purchase originations)",
        weight="geography-year (unweighted OLS), clustered by geography",
        result={
            "baseline_coefficient": base_coef,
            "baseline_std_err": baseline.get("std_err"),
            "n_cells": grid.height,
            "verdict_counts": grid.group_by("verdict")
            .agg(pl.len().alias("n"))
            .sort("n", descending=True)
            .to_dicts(),
            "cells": grid.to_dicts(),
            "n_flagged": failing.height,
            "flagged_cells": failing.to_dicts(),
            "verdict_definitions": {
                "consistent": "same sign as baseline, |t| >= 1.645",
                "attenuated": "same sign but less than half the baseline magnitude, or insignificant",
                "sign_flip": "opposite sign and significant -- contradicts the baseline",
                "insignificant": "|t| < 1.645",
                "not_estimable": "could not be fit (too few clusters or no variation)",
                "placebo_pass": "placebo is insignificant, as it should be",
                "placebo_FAIL": "placebo is SIGNIFICANT -- evidence against the interpretation",
            },
        },
        caveats=[
            "Cells are not independent; this is a fragility map, not a set of "
            "hypothesis tests, and no multiplicity correction is meaningful here.",
            "A specification that fails is reported in reports/failed_hypotheses.md "
            "and is not dropped.",
        ],
    )
    return (path, failing.height)


def _row(
    axis: str,
    variant: str,
    outcome: str,
    exposure: str,
    res: dict[str, Any],
    baseline: float | None,
    rationale: str,
    verdict_override: str | None = None,
) -> dict[str, Any]:
    coef, se = res.get("coef"), res.get("std_err")
    return {
        "axis": axis,
        "variant": variant,
        "outcome": outcome,
        "exposure": exposure,
        "status": res.get("status", "ok"),
        "reason": res.get("reason"),
        "coef": coef,
        "std_err": se,
        "t": (coef / se) if (coef is not None and se) else None,
        "n_obs": res.get("n_obs"),
        "n_clusters": res.get("n_clusters"),
        "verdict": verdict_override or _verdict(coef, se, baseline),
        "rationale": rationale,
        "pretrend_pvalue": None,
        "pretrend_passes": None,
    }


def _write_empty(cfg: Config, reason: str) -> Path:
    ctx = run_context(cfg, source_versions=collect_source_versions(cfg))
    return write_artifact(
        cfg,
        ctx,
        group="robustness",
        name="robustness_grid",
        evidence_tier="descriptive",
        population=POPULATION_STATEMENT,
        geography=cfg.panel.geography,
        outcome_definition="n/a",
        weight="n/a",
        result={"status": "skipped", "reason": reason, "cells": []},
        caveats=["The robustness grid could not run."],
    )


def load_grid(cfg: Config) -> pl.DataFrame | None:
    p = cfg.path("outputs", "robustness", "grid.parquet")
    return pl.read_parquet(p) if p.exists() else None
