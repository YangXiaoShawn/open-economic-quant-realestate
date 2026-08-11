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

Z10 = 1.645


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
