"""Continuous-treatment event study and DiD-style panel specifications.

Estimand and assumptions: ``docs/IDENTIFICATION_STRATEGY.md``.

.. math::
    y_{gt} = \\alpha_g + \\gamma_t
             + \\sum_{k \\neq k_0} \\beta_k (E_g \\times \\mathbf 1\\{t=k\\})
             + X_{gt}'\\theta + \\varepsilon_{gt}

* ``E_g`` is **predetermined** exposure, frozen at the pre-shock date.
* ``alpha_g`` and ``gamma_t`` are geography and period fixed effects. The national
  rate path is common across geographies and is therefore *absorbed* by
  ``gamma_t`` -- only **relative** effects are identified. This is stated in every
  artifact.
* Standard errors are clustered by geography, with the cluster count reported. When
  the cluster count is small the wild-cluster-bootstrap p-value is reported too.
* Pre-trends: a joint Wald test on the pre-period coefficients. A failure demotes
  the artifact from ``quasi_experimental`` to ``descriptive``, automatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import statsmodels.api as sm

from lockin.artifacts import write_artifact
from lockin.config import Config
from lockin.panel.build import load_panel
from lockin.provenance import collect_source_versions, run_context

POPULATION_STATEMENT = (
    "U.S. states present in both the Freddie Mac loan sample and the public "
    "aggregate series. Lock-in exposure is measured on the FREDDIE-ACQUIRED "
    "conforming conventional population only; outcomes are measured on HMDA "
    "reporters (originations), the FHFA purchase-only index (prices), and "
    "permit-issuing places (construction)."
)

PRETREND_ALPHA = 0.10


def _standardise(x: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Standardise exposure so coefficients read 'per standard deviation'."""
    mu, sd = float(np.nanmean(x)), float(np.nanstd(x, ddof=1))
    if not np.isfinite(sd) or sd == 0:
        return (x - mu, mu, 1.0)
    return ((x - mu) / sd, mu, sd)


def _cluster_ols(
    y: np.ndarray, X: np.ndarray, names: list[str], clusters: np.ndarray
) -> dict[str, Any]:
    model = sm.OLS(y, X)
    n_clusters = len(np.unique(clusters))
    try:
        res = model.fit(cov_type="cluster", cov_kwds={"groups": clusters})
        se_kind = f"clustered by geography ({n_clusters} clusters)"
    except Exception:
        res = model.fit()
        se_kind = "conventional (clustering failed)"
    return {
        "params": np.asarray(res.params, dtype=float),
        "bse": np.asarray(res.bse, dtype=float),
        "names": names,
        "n_obs": int(res.nobs),
        "n_clusters": n_clusters,
        "r2": float(res.rsquared),
        "se_kind": se_kind,
        "result": res,
    }


def _wild_cluster_bootstrap_p(
    y: np.ndarray,
    X: np.ndarray,
    clusters: np.ndarray,
    test_idx: list[int],
    n_boot: int = 999,
    seed: int = 20260810,
) -> float | None:
    """Rademacher wild cluster bootstrap p-value for a joint restriction.

    Imposes the null (drops the tested columns), resamples cluster-level signs on
    the restricted residuals, and compares the Wald statistic. Reported when the
    cluster count is small enough that the asymptotic cluster-robust variance is
    unreliable.
    """
    if not test_idx:
        return None
    keep = [j for j in range(X.shape[1]) if j not in test_idx]
    if not keep:
        return None
    rng = np.random.default_rng(seed)

    def wald(yv: np.ndarray) -> float:
        try:
            r = sm.OLS(yv, X).fit(cov_type="cluster", cov_kwds={"groups": clusters})
            R = np.zeros((len(test_idx), X.shape[1]))
            for i, j in enumerate(test_idx):
                R[i, j] = 1.0
            b = np.asarray(r.params)
            V = np.asarray(r.cov_params())
            mid = R @ V @ R.T
            return float((R @ b).T @ np.linalg.pinv(mid) @ (R @ b))
        except Exception:
            return np.nan

    w_obs = wald(y)
    if not np.isfinite(w_obs):
        return None
    res_r = sm.OLS(y, X[:, keep]).fit()
    fitted, resid = res_r.fittedvalues, res_r.resid
    uniq = np.unique(clusters)
    count = 0
    valid = 0
    for _ in range(n_boot):
        signs = rng.choice([-1.0, 1.0], size=len(uniq))
        smap = dict(zip(uniq, signs, strict=True))
        mult = np.array([smap[c] for c in clusters])
        w_b = wald(fitted + resid * mult)
        if np.isfinite(w_b):
            valid += 1
            if w_b >= w_obs:
                count += 1
    return (count + 1) / (valid + 1) if valid else None


#: Which published series each panel column is derived from. Two columns sharing a
#: source cannot be treated as independent information about each other.
_SERIES_SOURCE: dict[str, str] = {
    "hpi_growth": "fhfa_hpi",
    "hpi_growth_12m": "fhfa_hpi",
    "pre_hpi_growth_2019_2021": "fhfa_hpi",
    "log_purchase_originations": "hmda",
    "log_refi_originations": "hmda",
    "denial_rate": "hmda",
    "pre_refi_count_2020_2021": "hmda",
    "log_permits_1unit": "census_bps",
    "log_permits_5plus": "census_bps",
    "permits_1unit": "census_bps",
    "permits_5plus": "census_bps",
    "unemployment_rate": "bls_laus",
    "teleworkable_share": "dingel_neiman",
}


def _circular_trend_controls(outcome: str, trend_controls: list[str]) -> list[str]:
    """Trend controls drawn from the same published series as the outcome.

    Interacting a pre-period value of the outcome's own series with every period makes
    the pre-trend test unfalsifiable: the control spans the pre-period variation the
    test looks for. Returning a non-empty list means the test result must be discarded,
    not celebrated.
    """
    src = _SERIES_SOURCE.get(outcome)
    if src is None:
        return []
    return [c for c in trend_controls if _SERIES_SOURCE.get(c) == src]


def _demote_degenerate_controls(
    df: pl.DataFrame, controls: list[str], trend_controls: list[str]
) -> list[str]:
    """Move level controls with no within-geography variation to the trend set.

    Such a control is exactly collinear with the geography fixed effects. Nothing
    raises -- the pseudo-inverse just splits the coefficient arbitrarily -- so without
    this the artifact would list a control that constrains no estimate.

    Shared by :func:`event_study` and :func:`did_two_period` so the two halves of one
    artifact cannot end up estimating different specifications. Mutates both lists in
    place and returns the names that moved.
    """
    degenerate: list[str] = []
    for c in list(controls):
        sds = df.group_by("geography").agg(pl.col(c).cast(pl.Float64).std().alias("sd"))["sd"]
        within_sd = sds.fill_null(0.0).max()
        if within_sd is None or float(within_sd) < 1e-12:
            degenerate.append(c)
            controls.remove(c)
            if c not in trend_controls:
                trend_controls.append(c)
    return degenerate


def event_study(
    panel: pl.DataFrame,
    outcome: str,
    exposure: str,
    time_col: str,
    reference_time: int | float,
    controls: list[str] | None = None,
    seed: int = 20260810,
    trend_controls: list[str] | None = None,
) -> dict[str, Any]:
    """Estimate the continuous-treatment event study for one outcome.

    ``controls`` enter as ordinary level regressors. That is only meaningful for a
    *time-varying* covariate: a geography attribute that never changes is **exactly
    collinear with the geography fixed effects**. Nothing raises when that happens --
    the pseudo-inverse simply splits the coefficient arbitrarily between the control and
    the fixed effects -- so the artifact would claim a control that constrains nothing.
    Such controls are detected here, excluded, and reported under
    ``degenerate_controls`` rather than left to fail silently.

    ``trend_controls`` are for exactly that case. Each is interacted with every period
    indicator, parallel to the exposure itself, so a predetermined characteristic is
    allowed its own arbitrary time path and the exposure coefficient is identified off
    variation orthogonal to it. This is how the teleworkable share addresses the
    remote-work threat -- see ``lockin.adapters.teleworkable``.
    """
    controls = [c for c in (controls or []) if c in panel.columns]
    trend_controls = [c for c in (trend_controls or []) if c in panel.columns]
    needed = [outcome, exposure, time_col, "geography", *controls, *trend_controls]
    df = panel.select(needed).drop_nulls([outcome, exposure, time_col])
    if df.height == 0:
        return {"status": "skipped", "reason": f"no non-null rows for {outcome} x {exposure}"}

    times = sorted(df[time_col].unique().to_list())
    if reference_time not in times:
        return {
            "status": "skipped",
            "reason": f"reference period {reference_time} absent; available {times}",
        }
    if len(times) < 3:
        return {"status": "skipped", "reason": f"only {len(times)} periods available"}

    geos = sorted(df["geography"].unique().to_list())
    if len(geos) < 5:
        return {"status": "skipped", "reason": f"only {len(geos)} geographies"}

    degenerate = _demote_degenerate_controls(df, controls, trend_controls)

    y = df[outcome].cast(pl.Float64).to_numpy()
    e_raw = df[exposure].cast(pl.Float64).to_numpy()
    e, e_mu, e_sd = _standardise(e_raw)
    t = df[time_col].to_numpy()
    g = df["geography"].to_numpy()

    cols: list[np.ndarray] = [np.ones(df.height)]
    names: list[str] = ["intercept"]
    for gg in geos[1:]:
        cols.append((g == gg).astype(float))
        names.append(f"geo[{gg}]")
    for tt in times[1:]:
        cols.append((t == tt).astype(float))
        names.append(f"time[{tt}]")
    interact_idx: dict[Any, int] = {}
    for tt in times:
        if tt == reference_time:
            continue
        cols.append(e * (t == tt).astype(float))
        interact_idx[tt] = len(names)
        names.append(f"exposure_x_time[{tt}]")
    for c in controls:
        cv = df[c].cast(pl.Float64).to_numpy()
        cv = np.nan_to_num(cv, nan=float(np.nanmean(cv)) if np.isfinite(np.nanmean(cv)) else 0.0)
        cols.append(cv)
        names.append(f"control[{c}]")
    # Trend controls: X_g interacted with every non-reference period. The reference
    # period is omitted for the same reason it is omitted for the exposure -- with a
    # full set of period interactions alongside period fixed effects the design is
    # singular. Standardising keeps the interaction columns on the same scale as the
    # exposure ones so the rank check is not tripped by units alone.
    for c in trend_controls:
        cv = df[c].cast(pl.Float64).to_numpy()
        cv = np.nan_to_num(cv, nan=float(np.nanmean(cv)) if np.isfinite(np.nanmean(cv)) else 0.0)
        cv, _, _ = _standardise(cv)
        for tt in times:
            if tt == reference_time:
                continue
            cols.append(cv * (t == tt).astype(float))
            names.append(f"trend[{c}]_x_time[{tt}]")

    X = np.column_stack(cols)
    fit = _cluster_ols(y, X, names, g)
    res = fit["result"]
    params, bse = fit["params"], fit["bse"]

    dynamic = []
    for tt in times:
        if tt == reference_time:
            dynamic.append(
                {
                    "time": tt,
                    "coef": 0.0,
                    "std_err": 0.0,
                    "ci_low": 0.0,
                    "ci_high": 0.0,
                    "is_reference": True,
                    "is_pre": True,
                }
            )
            continue
        j = interact_idx[tt]
        dynamic.append(
            {
                "time": tt,
                "coef": float(params[j]),
                "std_err": float(bse[j]),
                "ci_low": float(params[j] - 1.96 * bse[j]),
                "ci_high": float(params[j] + 1.96 * bse[j]),
                "is_reference": False,
                "is_pre": bool(tt < reference_time),
            }
        )

    pre_idx = [interact_idx[tt] for tt in times if tt < reference_time and tt in interact_idx]
    post_idx = [interact_idx[tt] for tt in times if tt > reference_time]

    pretrend: dict[str, Any] = {"n_pre_coefficients": len(pre_idx)}
    if pre_idx:
        R = np.zeros((len(pre_idx), X.shape[1]))
        for i, j in enumerate(pre_idx):
            R[i, j] = 1.0
        try:
            wt = res.wald_test(R, scalar=True)
            pretrend.update(
                {
                    "statistic": float(np.asarray(wt.statistic).ravel()[0]),
                    "pvalue": float(np.asarray(wt.pvalue).ravel()[0]),
                    "test": "joint Wald that all pre-period exposure interactions are zero",
                }
            )
        except Exception as exc:
            pretrend["error"] = f"{type(exc).__name__}: {exc}"
        if fit["n_clusters"] < 40:
            pretrend["wild_cluster_bootstrap_pvalue"] = _wild_cluster_bootstrap_p(
                y, X, g, pre_idx, seed=seed
            )
    else:
        pretrend["test"] = "not testable: no pre-period other than the reference"

    passes = bool(pretrend.get("pvalue", 0.0) >= PRETREND_ALPHA) if "pvalue" in pretrend else False
    pretrend["passes_at_alpha_0.10"] = passes

    post_mean = float(np.mean([params[j] for j in post_idx])) if post_idx else None

    return {
        "status": "ok",
        "specification": ("y_gt = a_g + g_t + sum_k beta_k (E_g x 1{t=k}) + X_gt'theta + e_gt"),
        "outcome": outcome,
        "exposure": exposure,
        "exposure_standardised": True,
        "exposure_mean": e_mu,
        "exposure_sd": e_sd,
        "coefficient_units": (
            f"change in {outcome} per ONE STANDARD DEVIATION of {exposure} "
            f"(sd = {e_sd:.4f}), relative to {time_col} = {reference_time}"
        ),
        "time_col": time_col,
        "reference_time": reference_time,
        "controls": controls,
        "trend_controls": trend_controls,
        "trend_control_note": (
            "Trend controls enter interacted with every non-reference period, so a "
            "predetermined geography characteristic is allowed its own time path. A "
            "level control could not do this: with geography fixed effects it would be "
            "collinear and would constrain nothing."
        )
        if trend_controls
        else None,
        "degenerate_controls": degenerate,
        "degenerate_control_note": (
            f"{degenerate} had no within-geography variation, so as level controls they "
            "were collinear with the geography fixed effects. They were moved to trend "
            "controls rather than silently absorbed."
        )
        if degenerate
        else None,
        "fixed_effects": ["geography", time_col],
        "n_obs": fit["n_obs"],
        "n_geographies": len(geos),
        "n_periods": len(times),
        "n_clusters": fit["n_clusters"],
        "standard_errors": fit["se_kind"],
        "r2": fit["r2"],
        "dynamic_effects": dynamic,
        "mean_post_effect": post_mean,
        "pretrend_test": pretrend,
        "identification_note": (
            "The national mortgage-rate path is common across geographies and is "
            "absorbed by the period fixed effects. Only RELATIVE effects across "
            "exposure are identified; the level effect of the rate increase is not."
        ),
    }


def did_two_period(
    panel: pl.DataFrame,
    outcome: str,
    exposure: str,
    time_col: str,
    shock_time: int | float,
    controls: list[str] | None = None,
    trend_controls: list[str] | None = None,
) -> dict[str, Any]:
    """Collapsed pre/post DiD -- one headline number per outcome.

    ``trend_controls`` enter as ``X_g x post``, the two-period analogue of the full set
    of period interactions used in :func:`event_study`. Without it a predetermined
    characteristic cannot be controlled for at all here, because it is absorbed by the
    geography fixed effects.
    """
    controls = [c for c in (controls or []) if c in panel.columns]
    trend_controls = [c for c in (trend_controls or []) if c in panel.columns]
    df = panel.select(
        [outcome, exposure, time_col, "geography", *controls, *trend_controls]
    ).drop_nulls([outcome, exposure, time_col])
    if df.height == 0:
        return {"status": "skipped", "reason": "no rows"}
    df = df.with_columns((pl.col(time_col) >= shock_time).cast(pl.Float64).alias("post"))
    if df["post"].n_unique() < 2:
        return {"status": "skipped", "reason": "no pre or no post periods"}

    y = df[outcome].cast(pl.Float64).to_numpy()
    e, e_mu, e_sd = _standardise(df[exposure].cast(pl.Float64).to_numpy())
    post = df["post"].to_numpy()
    g = df["geography"].to_numpy()
    times = sorted(df[time_col].unique().to_list())
    geos = sorted(df["geography"].unique().to_list())

    # Same demotion as event_study, so both halves of an artifact estimate the same
    # specification. Without this the DiD would keep a collinear level control that the
    # dynamic estimate had already moved to the trend set.
    degenerate = _demote_degenerate_controls(df, controls, trend_controls)

    cols = [np.ones(df.height)]
    names = ["intercept"]
    for gg in geos[1:]:
        cols.append((g == gg).astype(float))
        names.append(f"geo[{gg}]")
    for tt in times[1:]:
        cols.append((df[time_col].to_numpy() == tt).astype(float))
        names.append(f"time[{tt}]")
    cols.append(e * post)
    names.append("exposure_x_post")
    for c in controls:
        cv = df[c].cast(pl.Float64).to_numpy()
        cv = np.nan_to_num(cv, nan=float(np.nanmean(cv)) if np.isfinite(np.nanmean(cv)) else 0.0)
        cols.append(cv)
        names.append(f"control[{c}]")
    for c in trend_controls:
        cv = df[c].cast(pl.Float64).to_numpy()
        cv = np.nan_to_num(cv, nan=float(np.nanmean(cv)) if np.isfinite(np.nanmean(cv)) else 0.0)
        cv, _, _ = _standardise(cv)
        cols.append(cv * post)
        names.append(f"trend[{c}]_x_post")

    X = np.column_stack(cols)
    if not np.all(np.isfinite(y)) or not np.all(np.isfinite(X)):
        return {
            "status": "skipped",
            "reason": (
                "non-finite values in the design or outcome (usually log of a zero "
                "count). A fit on these returns null coefficients while still reporting "
                "success, so it is refused instead."
            ),
            "outcome": outcome,
            "exposure": exposure,
        }
    fit = _cluster_ols(y, X, names, g)
    j = names.index("exposure_x_post")
    b, se = float(fit["params"][j]), float(fit["bse"][j])
    if not (np.isfinite(b) and np.isfinite(se)):
        return {
            "status": "skipped",
            "reason": (
                f"the fitted exposure coefficient is not finite (coef={b}, se={se}), "
                "which means a singular or degenerate design. Reported as a failure "
                "rather than as an 'ok' result carrying nulls."
            ),
            "outcome": outcome,
            "exposure": exposure,
            "n_obs": fit["n_obs"],
        }
    return {
        "status": "ok",
        "specification": "y_gt = a_g + g_t + beta (E_g x post_t) + X'theta + e_gt",
        "outcome": outcome,
        "exposure": exposure,
        "shock_time": shock_time,
        "coef": b,
        "std_err": se,
        "t": b / se if se else None,
        "ci_low": b - 1.96 * se,
        "ci_high": b + 1.96 * se,
        "coefficient_units": f"change in {outcome} per one SD of {exposure} (sd={e_sd:.4f}) after the shock",
        "exposure_mean": e_mu,
        "exposure_sd": e_sd,
        "n_obs": fit["n_obs"],
        "n_clusters": fit["n_clusters"],
        "standard_errors": fit["se_kind"],
        "controls": controls,
        "trend_controls": trend_controls,
        "degenerate_controls": degenerate,
    }


def exposure_distribution(panel: pl.DataFrame, exposure: str) -> dict[str, Any]:
    """Distribution of exposure across geographies, plus a balance table."""
    if exposure not in panel.columns:
        return {"status": "skipped", "reason": f"{exposure} not in panel"}
    per_geo = panel.group_by("geography").agg(pl.col(exposure).first().alias("exposure"))
    per_geo = per_geo.drop_nulls("exposure").sort("exposure", descending=True)
    if per_geo.height == 0:
        return {"status": "skipped", "reason": "no non-null exposure"}

    balance_vars = [
        c
        for c in (
            "pre_hpi_growth_2019_2021",
            "pre_refi_count_2020_2021",
            "pre_wavg_note_rate_upb",
            "pre_n_active_loans",
            "median_est_current_ltv",
        )
        if c in panel.columns
    ]
    balance = []
    for v in balance_vars:
        sub = (
            panel.group_by("geography")
            .agg(pl.col(exposure).first().alias("e"), pl.col(v).mean().alias("v"))
            .drop_nulls()
        )
        if sub.height < 4:
            continue
        e = sub["e"].to_numpy()
        vv = sub["v"].to_numpy()
        if np.nanstd(e) == 0 or np.nanstd(vv) == 0:
            continue
        balance.append(
            {"variable": v, "correlation_with_exposure": float(np.corrcoef(e, vv)[0, 1])}
        )

    q = per_geo["exposure"]
    return {
        "status": "ok",
        "exposure": exposure,
        "n_geographies": per_geo.height,
        "mean": float(q.mean()),
        "sd": float(q.std()),
        "min": float(q.min()),
        "p25": float(q.quantile(0.25)),
        "median": float(q.median()),
        "p75": float(q.quantile(0.75)),
        "max": float(q.max()),
        "top_5": per_geo.head(5).to_dicts(),
        "bottom_5": per_geo.tail(5).to_dicts(),
        "balance_table": balance,
        "balance_interpretation": (
            "Non-zero correlations mean exposure is NOT randomly assigned across "
            "states. Predetermined is not exogenous; see "
            "docs/IDENTIFICATION_STRATEGY.md A4."
        ),
    }


def run_event_studies(cfg: Config) -> dict[str, Path]:
    """Run every event-study specification and write artifacts."""
    ctx = run_context(cfg, source_versions=collect_source_versions(cfg))
    annual = load_panel(cfg, "annual")
    monthly = load_panel(cfg, "monthly")
    written: dict[str, Path] = {}

    shock_year = int(cfg.event_study.shock_date.split("-")[0])
    ref_year = shock_year - 1
    exposure = f"pre_{cfg.event_study.exposure_measure}"
    # `unemployment_rate` is time-varying and enters as a contemporaneous control;
    # `pre_hpi_growth_2019_2021` is a fixed pre-period characteristic. Including the
    # unemployment control is what addresses the local-labour-shock threat; when the
    # optional LAUS source is absent the column is simply missing and the threat stays
    # uncontrolled, which the artifact records.
    controls = [c for c in ("pre_hpi_growth_2019_2021", "unemployment_rate") if c in annual.columns]
    # `teleworkable_share` is a single cross-section. It CANNOT go in `controls`: with
    # geography fixed effects it is exactly collinear and would constrain nothing while
    # still appearing in the artifact's control list. It enters interacted with every
    # period instead, which is what actually addresses the remote-work threat.
    trend_controls = [c for c in ("teleworkable_share",) if c in annual.columns]

    # --- exposure distribution + balance ------------------------------------
    written["exposure_distribution"] = write_artifact(
        cfg,
        ctx,
        group="eventstudy",
        name="exposure_distribution",
        evidence_tier="descriptive",
        population=POPULATION_STATEMENT,
        geography=cfg.panel.geography,
        outcome_definition="n/a (treatment variable description)",
        weight="geography (unweighted)",
        result={
            "primary": exposure_distribution(annual, exposure),
            "alternatives": {
                f"pre_{alt}": exposure_distribution(annual, f"pre_{alt}")
                for alt in cfg.event_study.alternative_exposures
            },
            "pre_shock_date": cfg.event_study.pre_shock_date,
        },
        caveats=["Exposure is predetermined at the pre-shock date and never recomputed."],
    )

    # --- annual outcomes (HMDA + annualised HPI/permits) --------------------
    annual_outcomes = [o for o in cfg.event_study.outcomes if o in annual.columns]
    missing = [o for o in cfg.event_study.outcomes if o not in annual.columns]

    for outcome in annual_outcomes:
        es = event_study(
            annual,
            outcome,
            exposure,
            "year",
            ref_year,
            controls=controls,
            trend_controls=trend_controls,
            seed=cfg.survival.seed,
        )
        did = did_two_period(
            annual,
            outcome,
            exposure,
            "year",
            shock_year,
            controls=controls,
            trend_controls=trend_controls,
        )

        # Tier is data-driven: a pre-trend failure demotes the artifact.
        tier = "quasi_experimental"
        caveats = [
            "Only RELATIVE effects across exposure are identified; the common "
            "national rate shock is absorbed by period fixed effects.",
            "Predetermined is not exogenous. No IV interpretation is claimed.",
        ]
        if es.get("status") != "ok":
            tier = "descriptive"
            caveats.append(f"Specification not estimable: {es.get('reason')}")
        elif not es["pretrend_test"].get("passes_at_alpha_0.10", False):
            tier = "descriptive"
            caveats.insert(
                0,
                "PRE-TREND TEST FAILED (or was not testable). This result is DEMOTED "
                "to descriptive and must not carry causal language. Recorded in "
                "reports/failed_hypotheses.md.",
            )
        if outcome == "log_refi_originations":
            caveats.append(
                "Refinance originations are MECHANICALLY CONTAMINATED: high-exposure "
                "markets refinanced heavily in 2020-21 and their pipeline is "
                "exhausted regardless of any lock-in mechanism (DECISION_LOG D014)."
            )

        # A trend control built from the SAME published series as the outcome is a
        # lagged dependent variable interacted with time. It absorbs the very pre-trend
        # the test is meant to detect, so a "pass" here carries no information and must
        # not be allowed to promote the artifact. See DECISION_LOG D027.
        circular = _circular_trend_controls(outcome, es.get("trend_controls") or [])
        if circular and tier == "quasi_experimental":
            tier = "descriptive"
            caveats.insert(
                0,
                "PRE-TREND TEST IS UNINFORMATIVE FOR THIS OUTCOME: the trend control(s) "
                f"{circular} are built from the same published series as {outcome!r}, so "
                "they are a lagged dependent variable interacted with time and absorb "
                "the pre-trend by construction. The test cannot fail here, so it is not "
                "evidence that it passed. DEMOTED to descriptive.",
            )

        written[f"es_{outcome}"] = write_artifact(
            cfg,
            ctx,
            group="eventstudy",
            name=f"es_{outcome}",
            evidence_tier=tier,
            population=POPULATION_STATEMENT,
            geography=cfg.panel.geography,
            outcome_definition=_outcome_definition(outcome),
            weight="geography-year (unweighted OLS)",
            result={
                "event_study": es,
                "did_two_period": did,
                "frequency": "annual",
                "reference_year": ref_year,
                "identification_threats": _threat_status(annual),
            },
            caveats=caveats,
        )

    # --- monthly outcomes (HPI growth, permits) ----------------------------
    monthly_outcomes = [
        c for c in ("hpi_growth_12m", "permits_1unit", "permits_5plus") if c in monthly.columns
    ]
    mp = monthly.with_columns(
        (pl.col("period").dt.year() * 12 + pl.col("period").dt.month()).alias("t_index")
    )
    for c in ("permits_1unit", "permits_5plus"):
        if c in mp.columns:
            mp = mp.with_columns(pl.col(c).cast(pl.Float64).clip(1, None).log().alias(f"log_{c}_m"))
    sy, sm_ = map(int, cfg.event_study.shock_date.split("-"))
    shock_idx = sy * 12 + sm_

    for outcome in monthly_outcomes:
        col = f"log_{outcome}_m" if f"log_{outcome}_m" in mp.columns else outcome
        # Annual aggregation of the monthly event study: collapse to 12-month bins
        # relative to the shock so the coefficient path is readable.
        binned = mp.with_columns(
            ((pl.col("t_index") - shock_idx) / 12).floor().cast(pl.Int64).alias("event_year")
        )
        collapsed = (
            binned.group_by(["geography", "event_year"])
            .agg(
                pl.col(col).mean().alias(col),
                pl.col(exposure).first().alias(exposure),
                *[pl.col(c).first().alias(c) for c in controls],
            )
            .filter(pl.col("event_year").is_between(-4, 3))
        )
        es = event_study(
            collapsed,
            col,
            exposure,
            "event_year",
            -1,
            controls=controls,
            trend_controls=trend_controls,
            seed=cfg.survival.seed,
        )
        tier = "quasi_experimental"
        caveats = [
            "Monthly series collapsed into 12-month bins relative to the shock date "
            "so the dynamic path is readable and serial correlation within year is "
            "not double-counted.",
            "Only RELATIVE effects are identified.",
        ]
        if es.get("status") != "ok" or not es.get("pretrend_test", {}).get(
            "passes_at_alpha_0.10", False
        ):
            tier = "descriptive"
            caveats.insert(0, "PRE-TREND TEST FAILED or not estimable -- DEMOTED to descriptive.")
        written[f"es_monthly_{outcome}"] = write_artifact(
            cfg,
            ctx,
            group="eventstudy",
            name=f"es_monthly_{outcome}",
            evidence_tier=tier,
            population=POPULATION_STATEMENT,
            geography=cfg.panel.geography,
            outcome_definition=_outcome_definition(outcome),
            weight="geography-event-year (unweighted OLS)",
            result={
                "event_study": es,
                "frequency": "monthly collapsed to event-year",
                "shock_date": cfg.event_study.shock_date,
                "pre_shock_date": cfg.event_study.pre_shock_date,
                "reference_event_year": -1,
            },
            caveats=caveats,
        )

    # --- placebo shock dates -----------------------------------------------
    placebos: dict[str, Any] = {}
    headline = (
        "log_purchase_originations"
        if "log_purchase_originations" in annual.columns
        else (annual_outcomes[0] if annual_outcomes else None)
    )
    if headline:
        for pdate in cfg.event_study.placebo_shock_dates:
            py2 = int(pdate.split("-")[0])
            placebos[pdate] = {
                "shock_year": py2,
                "did": did_two_period(
                    annual.filter(pl.col("year") < shock_year),
                    headline,
                    exposure,
                    "year",
                    py2,
                    controls=controls,
                    trend_controls=trend_controls,
                ),
            }
    # --- placebo outcome: multifamily permits ------------------------------
    placebo_outcomes: dict[str, Any] = {}
    for po in ("log_permits_5plus", "denial_rate"):
        if po in annual.columns:
            placebo_outcomes[po] = did_two_period(
                annual,
                po,
                exposure,
                "year",
                shock_year,
                controls=controls,
                trend_controls=trend_controls,
            )

    written["placebos"] = write_artifact(
        cfg,
        ctx,
        group="eventstudy",
        name="placebos",
        evidence_tier="quasi_experimental",
        population=POPULATION_STATEMENT,
        geography=cfg.panel.geography,
        outcome_definition=(
            "Placebo SHOCK DATES: the same DiD applied to a pre-period date where the "
            "national rate move was too small to bind. Placebo OUTCOMES: multifamily "
            "(5+ unit) permits, which are renter-demand driven and should not respond "
            "to owner lock-in; and the purchase denial rate, which proxies credit "
            "conditions."
        ),
        weight="geography-year (unweighted OLS)",
        result={
            "headline_outcome": headline,
            "placebo_shock_dates": placebos,
            "placebo_outcomes": placebo_outcomes,
            "interpretation": (
                "A significant placebo effect of the same sign as the main estimate "
                "is evidence AGAINST the lock-in interpretation and is reported in "
                "reports/failed_hypotheses.md."
            ),
        },
        caveats=["Placebo tests can only falsify, never confirm."],
    )

    if missing:
        written["outcomes_unavailable"] = write_artifact(
            cfg,
            ctx,
            group="eventstudy",
            name="outcomes_unavailable",
            evidence_tier="descriptive",
            population=POPULATION_STATEMENT,
            geography=cfg.panel.geography,
            outcome_definition="n/a (coverage report)",
            weight="n/a",
            result={
                "requested_but_unavailable": missing,
                "available_annual": annual_outcomes,
                "available_monthly": monthly_outcomes,
                "reason": "the corresponding public source failed to fetch or the "
                "column is absent from the panel",
            },
            caveats=["Unavailable outcomes are NOT reported as null results."],
        )
    return written


def _threat_status(panel: pl.DataFrame) -> dict[str, str]:
    """Which documented identification threats this run actually controls for.

    Recorded on every event-study artifact so a reader can see, without opening the
    strategy document, which threats were addressed and which were merely noted.
    """
    return {
        "pandemic_demand_reallocation": (
            "CONTROLLED via pre_hpi_growth_2019_2021"
            if "pre_hpi_growth_2019_2021" in panel.columns
            else "UNCONTROLLED"
        ),
        "local_labour_shocks": (
            "CONTROLLED via contemporaneous state unemployment rate (BLS LAUS)"
            if "unemployment_rate" in panel.columns
            else "UNCONTROLLED -- optional BLS LAUS source unavailable"
        ),
        "differential_refinancing_booms": (
            "PARTIALLY: refi outcomes labeled mechanically contaminated; "
            "pre_refi_count_2020_2021 available as a robustness control"
            if "pre_refi_count_2020_2021" in panel.columns
            else "UNCONTROLLED"
        ),
        "remote_work_exposure": (
            "CONTROLLED via the predetermined teleworkable employment share "
            "(Dingel & Neiman 2020), entered as a TREND control -- interacted with "
            "every period, because the measure is a single cross-section. Note this "
            "controls for the FEASIBILITY of remote work, not its realisation."
            if "teleworkable_share" in panel.columns
            else "UNCONTROLLED -- optional teleworkable-share source unavailable"
        ),
        "national_monetary_policy_endogeneity": (
            "ABSORBED by period fixed effects; only relative effects are identified"
        ),
        "geography_specific_rate_dispersion": (
            "UNCONTROLLED -- PMMS is national; the gap carries measurement error"
        ),
        "spillovers": "UNCORRECTED -- biases estimates toward zero",
    }


def _outcome_definition(outcome: str) -> str:
    return {
        "log_purchase_originations": "log HMDA home-purchase loans ORIGINATED "
        "(action taken = 1, loan purpose = 1). Applications and originations, not "
        "property sales; all-cash purchases are absent.",
        "log_refi_originations": "log HMDA refinance loans originated (loan purpose "
        "31 or 32). MECHANICALLY CONTAMINATED by pipeline exhaustion.",
        "hpi_growth": "annual sum of monthly log changes in the FHFA purchase-only "
        "state house price index. An INDEX, not a property value.",
        "hpi_growth_12m": "12-month log change in the FHFA purchase-only state HPI.",
        "log_permits_1unit": "log Census BPS single-family units AUTHORIZED (not "
        "starts, not completions).",
        "log_permits_5plus": "log Census BPS 5+-unit units authorized.",
        "permits_1unit": "Census BPS single-family units authorized.",
        "permits_5plus": "Census BPS 5+-unit units authorized.",
        "denial_rate": "HMDA purchase-loan denials divided by purchase-loan "
        "applications. A credit-conditions proxy, used as a placebo outcome.",
    }.get(outcome, outcome)
