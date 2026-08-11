"""The duration model ladder.

Six rungs, in increasing structure:

1. :func:`kaplan_meier` -- descriptive survival, respecting **left truncation**
   via an explicit at-risk count that excludes not-yet-entered loans.
2. :func:`cumulative_incidence` -- Aalen-Johansen CIF by cause. Used instead of
   ``1 - KM`` because competing risks make ``1 - KM`` an overstatement.
3. :func:`discrete_time_logit` -- the workhorse. Loan-age dummies plus covariates.
4. :func:`discrete_time_cloglog` -- the discrete-time proportional-hazards
   analogue; coefficients are log hazard ratios.
5. :func:`cox_ph` -- continuous-time Cox with entry times, plus Schoenfeld PH
   diagnostics.
6. :func:`gradient_boosted_benchmark` -- a flexible predictive benchmark, reported
   for out-of-time discrimination and calibration only. Not a causal object.

Nothing in this module produces a causal estimate. Every artifact built from it is
tagged ``hazard_association``.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl
import statsmodels.api as sm

from lockin.survival.dataset import SurvivalDataset, design_matrix, last_dropped_columns

# ---------------------------------------------------------------------------
# 1-2. Non-parametric description
# ---------------------------------------------------------------------------


def kaplan_meier(
    loans: pl.DataFrame,
    entry_col: str = "entry_age",
    exit_col: str = "exit_age",
    event_col: str = "event_code",
    event_of_interest: int = 1,
) -> dict[str, Any]:
    """Kaplan-Meier survival with left truncation and competing risks as censoring.

    Note the interpretation: treating the competing cause as censoring gives the
    survival function for the *cause-specific hazard*, which is **not** the
    probability of avoiding prepayment in a world where credit events exist. For
    that, use :func:`cumulative_incidence`.
    """
    entry = loans[entry_col].cast(pl.Float64).to_numpy()
    exit_ = loans[exit_col].cast(pl.Float64).to_numpy()
    code = loans[event_col].cast(pl.Int64).to_numpy()

    out_t: list[float] = []
    out_n: list[int] = []
    out_d: list[int] = []
    curve: list[float] = []
    surv = 1.0
    for t in np.sort(np.unique(exit_[code == event_of_interest])):
        # Risk set at t: entered strictly before t (left truncation) and not yet exited.
        n_risk = int(np.sum((entry < t) & (exit_ >= t)))
        if n_risk <= 0:
            continue
        d = int(np.sum((exit_ == t) & (code == event_of_interest)))
        surv *= 1.0 - d / n_risk
        out_t.append(float(t))
        out_n.append(n_risk)
        out_d.append(d)
        curve.append(surv)

    return {
        "method": "Kaplan-Meier with left truncation (risk set excludes loans not "
        "yet observed at each age)",
        "event_of_interest": int(event_of_interest),
        "competing_risks_treatment": "treated as censoring -- gives the CAUSE-SPECIFIC "
        "survival function, NOT the probability of "
        "avoiding the event in the presence of competing "
        "causes",
        "time_unit": "loan age in months",
        "times": out_t,
        "n_at_risk": out_n,
        "n_events": out_d,
        "survival": curve,
        "n_loans": int(loans.height),
        "n_left_truncated": int(np.sum(entry > 1)),
    }


def kaplan_meier_by_group(
    loans: pl.DataFrame, group_col: str, min_group_size: int = 100, **kwargs: Any
) -> dict[str, Any]:
    """KM curves stratified by a grouping column (e.g. rate-gap bucket)."""
    out: dict[str, Any] = {"group_col": group_col, "groups": {}}
    for (g,), part in loans.group_by([group_col], maintain_order=True):
        if part.height < min_group_size:
            continue
        out["groups"][str(g)] = kaplan_meier(part, **kwargs)
    return out


def cumulative_incidence(
    loans: pl.DataFrame,
    entry_col: str = "entry_age",
    exit_col: str = "exit_age",
    event_col: str = "event_code",
    causes: tuple[int, ...] = (1, 2),
) -> dict[str, Any]:
    """Aalen-Johansen cumulative incidence functions for competing causes.

    CIF_k(t) = sum_{s<=t} S(s-) * h_k(s), where S is the all-cause survival and
    h_k the cause-specific hazard. Unlike ``1 - KM``, the CIFs are guaranteed to
    sum to at most 1.
    """
    entry = loans[entry_col].cast(pl.Float64).to_numpy()
    exit_ = loans[exit_col].cast(pl.Float64).to_numpy()
    code = loans[event_col].cast(pl.Int64).to_numpy()

    times = np.sort(np.unique(exit_[code > 0]))
    s_prev = 1.0
    cif = dict.fromkeys(causes, 0.0)
    rows: list[dict[str, Any]] = []
    for t in times:
        n_risk = int(np.sum((entry < t) & (exit_ >= t)))
        if n_risk <= 0:
            continue
        d_all = int(np.sum((exit_ == t) & (code > 0)))
        for k in causes:
            d_k = int(np.sum((exit_ == t) & (code == k)))
            cif[k] += s_prev * d_k / n_risk
        rows.append(
            {
                "age": float(t),
                "n_at_risk": n_risk,
                "n_events_all": d_all,
                **{f"cif_cause_{k}": cif[k] for k in causes},
            }
        )
        s_prev *= 1.0 - d_all / n_risk

    return {
        "method": "Aalen-Johansen cumulative incidence (competing risks)",
        "cause_labels": {"1": "prepayment (ZB 01)", "2": "credit event (ZB 02/03/09)"},
        "why_not_one_minus_km": "1 - KM overstates the probability of the event when a "
        "competing cause removes loans from the risk set.",
        "rows": rows,
        "final_cif": {f"cause_{k}": cif[k] for k in causes},
        "n_loans": int(loans.height),
    }


# ---------------------------------------------------------------------------
# 3-4. Discrete-time hazards
# ---------------------------------------------------------------------------


def _glm_hazard(
    ds: SurvivalDataset,
    outcome: str,
    link: str,
    covariates: list[str] | None = None,
    include_gap_bins: bool = False,
    use_weights: bool = True,
    cluster_col: str | None = "loan_seq_no",
) -> dict[str, Any]:
    X, names, kept = design_matrix(
        ds, covariates=covariates, include_age_dummies=True, include_gap_bins=include_gap_bins
    )
    dropped_cols = last_dropped_columns()
    y = kept[outcome].cast(pl.Float64).to_numpy()
    w = kept["sampling_weight"].cast(pl.Float64).to_numpy() if use_weights else None
    # statsmodels does not support cluster-robust covariance together with
    # freq_weights. When every weight is 1 (the no-sampling case) the weights are
    # a no-op, so we drop them and keep the clustered standard errors, which matter
    # far more: loan-months are serially correlated within a loan.
    weights_are_trivial = w is not None and bool(np.allclose(w, 1.0))
    if weights_are_trivial:
        w = None

    family = sm.families.Binomial(
        link=sm.families.links.CLogLog() if link == "cloglog" else sm.families.links.Logit()
    )
    model = sm.GLM(y, X, family=family, freq_weights=w)
    if cluster_col and cluster_col in kept.columns:
        # statsmodels needs integer group codes, not the raw string loan ids.
        codes = kept[cluster_col].cast(pl.Categorical).to_physical().to_numpy()
        groups = np.asarray(codes, dtype=np.int64)
        try:
            res = model.fit(cov_type="cluster", cov_kwds={"groups": groups})
            se_kind = f"clustered by {cluster_col} ({int(np.unique(groups).size):,} clusters)"
        except Exception as exc:
            res = model.fit()
            se_kind = f"conventional (clustering failed: {type(exc).__name__}: {exc})"
    else:
        res = model.fit()
        se_kind = "conventional"

    params = np.asarray(res.params, dtype=float)
    bse = np.asarray(res.bse, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        z = params / bse
    coefs = []
    for i, nm in enumerate(names):
        coefs.append(
            {
                "term": nm,
                "coef": float(params[i]),
                "std_err": float(bse[i]),
                "z": float(z[i]) if np.isfinite(z[i]) else None,
                "hazard_ratio": float(np.exp(params[i])) if abs(params[i]) < 50 else None,
                "ci_low": float(params[i] - 1.96 * bse[i]),
                "ci_high": float(params[i] + 1.96 * bse[i]),
            }
        )

    # Average marginal effect of the rate gap, in percentage points of monthly
    # hazard per 1 pp of gap -- the interpretable quantity.
    ame = None
    if "rate_gap" in names:
        j = names.index("rate_gap")
        eta = X @ params
        if link == "cloglog":
            dmu = np.exp(eta - np.exp(eta))
        else:
            p = 1.0 / (1.0 + np.exp(-eta))
            dmu = p * (1.0 - p)
        ame = float(np.mean(dmu) * params[j])

    return {
        "model": f"discrete-time {link} hazard",
        "outcome": outcome,
        "link": link,
        "n_obs": int(kept.height),
        "n_dropped_missing_covariates": int(ds.frame.height - kept.height),
        "dropped_design_columns": dropped_cols,
        "dropped_design_columns_note": (
            "zero-variance columns (usually loan-age bins with no observations in the "
            "estimation window) removed so the cluster-robust covariance is not singular"
        ),
        "n_events": int(y.sum()),
        "n_loans": int(kept["loan_seq_no"].n_unique()),
        "standard_errors": se_kind,
        "frequency_weights_applied": not weights_are_trivial and use_weights,
        "weights_note": (
            "sampling weights were all 1 (no case-cohort sampling), so they were "
            "dropped in favour of cluster-robust standard errors, which statsmodels "
            "cannot combine with freq_weights"
            if weights_are_trivial
            else "sampling weights applied; standard errors are therefore NOT "
            "cluster-robust (statsmodels limitation) and are understated"
        ),
        "log_likelihood": float(res.llf),
        "aic": float(res.aic),
        "coefficients": coefs,
        "rate_gap_average_marginal_effect_monthly": ame,
        "rate_gap_ame_interpretation": (
            "change in the MONTHLY probability of the outcome per 1 percentage point "
            "increase in the rate gap (market minus note), averaged over the sample"
        ),
        "interpretation_warning": (
            "ASSOCIATION, not causation. The rate gap is a deterministic function of "
            "the borrower's chosen note rate and the national rate path; borrowers "
            "with different note rates differ in cohort, credit, equity, and tenure."
        ),
        "sampling_design": ds.sampling_design,
    }


def discrete_time_logit(
    ds: SurvivalDataset, outcome: str = "exit_prepayment", **kw: Any
) -> dict[str, Any]:
    """Rung 3 -- discrete-time logit hazard with loan-age dummies."""
    return _glm_hazard(ds, outcome, "logit", **kw)


def discrete_time_cloglog(
    ds: SurvivalDataset, outcome: str = "exit_prepayment", **kw: Any
) -> dict[str, Any]:
    """Rung 4 -- complementary log-log; coefficients are log hazard ratios."""
    return _glm_hazard(ds, outcome, "cloglog", **kw)


def baseline_hazard_by_age(ds: SurvivalDataset, outcome: str = "exit_prepayment") -> dict[str, Any]:
    """Empirical (unmodelled) hazard by loan-age bin -- the duration-dependence picture."""
    g = (
        ds.frame.group_by("age_bin")
        .agg(
            pl.len().alias("n_at_risk"),
            pl.col(outcome).sum().alias("n_events"),
            pl.col(outcome).mean().alias("hazard"),
            pl.col("loan_age").mean().alias("mean_age"),
        )
        .sort("mean_age")
    )
    return {
        "description": "empirical monthly hazard by loan-age bin (no covariates)",
        "outcome": outcome,
        "rows": g.to_dicts(),
    }


def nonlinear_gap_profile(ds: SurvivalDataset, outcome: str = "exit_prepayment") -> dict[str, Any]:
    """Empirical monthly hazard by rate-gap bucket, plus the modelled bin profile."""
    from lockin.lockin_measures import GAP_BUCKET_LABELS

    emp = (
        ds.frame.group_by("gap_bucket")
        .agg(
            pl.len().alias("n_at_risk"),
            pl.col(outcome).sum().alias("n_events"),
            pl.col(outcome).mean().alias("hazard"),
            pl.col("rate_gap").mean().alias("mean_rate_gap"),
        )
        .sort("gap_bucket")
        .with_columns(
            pl.col("gap_bucket")
            .map_elements(
                lambda i: GAP_BUCKET_LABELS[int(i)] if i is not None else None,
                return_dtype=pl.Utf8,
            )
            .alias("label")
        )
    )
    modelled = _glm_hazard(
        ds,
        outcome,
        "logit",
        covariates=[c for c in ds.covariates if c != "rate_gap"],
        include_gap_bins=True,
    )
    return {
        "description": "nonlinear rate-gap effect: empirical hazard by bucket and a "
        "binned-gap logit specification (reference bucket: 0 to +100bp)",
        "outcome": outcome,
        "empirical": emp.to_dicts(),
        "modelled_bins": [c for c in modelled["coefficients"] if c["term"].startswith("gapbin[")],
        "model_n_obs": modelled["n_obs"],
    }


# ---------------------------------------------------------------------------
# 5. Cox
# ---------------------------------------------------------------------------


def cox_ph(
    loans: pl.DataFrame,
    covariates: list[str],
    max_rows: int = 200_000,
    seed: int = 20260810,
) -> dict[str, Any]:
    """Rung 5 -- Cox PH with entry times (left truncation) and PH diagnostics.

    Uses ``rate_gap_at_entry``: a loan-level Cox cannot carry the time-varying gap.
    This is why the discrete-time rungs are preferred for rate-gap inference; the
    Cox rung exists to check that the ordering of covariate effects is not an
    artifact of the discrete-time functional form.
    """
    try:
        from lifelines import CoxPHFitter
        from lifelines.statistics import proportional_hazard_test
    except ImportError:  # pragma: no cover
        return {"status": "skipped", "reason": "lifelines not installed"}

    cols = [c for c in covariates if c in loans.columns]
    df = loans.select(["entry_age", "exit_age", "event_code", *cols]).drop_nulls().to_pandas()
    df = df[df["exit_age"] > df["entry_age"]]
    if df.empty:
        return {"status": "skipped", "reason": "no rows with exit_age > entry_age"}
    if len(df) > max_rows:
        df = df.sample(max_rows, random_state=seed)

    df["event"] = (df["event_code"] == 1).astype(int)
    fitter = CoxPHFitter()
    try:
        fitter.fit(
            df[["entry_age", "exit_age", "event", *cols]],
            duration_col="exit_age",
            event_col="event",
            entry_col="entry_age",
        )
    except Exception as exc:
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}

    summ = fitter.summary
    coefs = [
        {
            "term": str(idx),
            "coef": float(row["coef"]),
            "std_err": float(row["se(coef)"]),
            "hazard_ratio": float(row["exp(coef)"]),
            "p": float(row["p"]),
        }
        for idx, row in summ.iterrows()
    ]
    # Schoenfeld residuals are not implemented for models fitted with entry times, so
    # the PH diagnostic is run on a SEPARATE fit that drops the entry times. That fit
    # ignores left truncation and is therefore NOT the model whose coefficients we
    # report -- it exists only to test the proportionality assumption.
    ph: dict[str, Any] = {"status": "not_run"}
    try:
        ph_frame = df[["exit_age", "event", *cols]]
        ph_fitter = CoxPHFitter()
        ph_fitter.fit(ph_frame, duration_col="exit_age", event_col="event")
        # The test frame must be exactly the training frame; extra columns misalign it.
        test = proportional_hazard_test(ph_fitter, ph_frame, time_transform="rank")
        ph = {
            "status": "run",
            "fitted_on": "a SEPARATE Cox fit WITHOUT entry times: Schoenfeld residuals "
            "are not implemented for left-truncated fits. This diagnostic "
            "therefore ignores left truncation and is not the model whose "
            "coefficients are reported above.",
            "time_transform": "rank",
            "per_covariate": {
                str(k): {"test_statistic": float(v[0]), "p": float(v[1])}
                for k, v in zip(
                    test.summary.index,
                    test.summary[["test_statistic", "p"]].to_numpy(),
                    strict=False,
                )
            },
            "interpretation": "small p-values indicate the proportional-hazards "
            "assumption is violated for that covariate",
        }
    except Exception as exc:
        ph = {"status": "failed", "reason": str(exc)}

    return {
        "model": "Cox proportional hazards with entry times (left truncation)",
        "outcome": "prepayment (competing credit events treated as censoring)",
        "n_obs": len(df),
        "n_events": int(df["event"].sum()),
        "covariate_note": "rate_gap_at_entry is FIXED at entry; a loan-level Cox "
        "cannot carry the time-varying gap. Prefer the "
        "discrete-time rungs for rate-gap inference.",
        "concordance": float(fitter.concordance_index_),
        "log_likelihood": float(fitter.log_likelihood_),
        "coefficients": coefs,
        "proportional_hazards_test": ph,
        "sampled": bool(len(df) >= max_rows),
    }


# ---------------------------------------------------------------------------
# 6. Predictive benchmark
# ---------------------------------------------------------------------------


def gradient_boosted_benchmark(
    train: SurvivalDataset,
    test: SurvivalDataset,
    outcome: str = "exit_prepayment",
    max_rows: int = 400_000,
    seed: int = 20260810,
) -> dict[str, Any]:
    """Rung 6 -- flexible predictive benchmark with out-of-time discrimination.

    Reported to answer "is the linear-in-gap discrete-time specification leaving
    predictive signal on the table?", not to make any causal claim. A model that
    predicts better is not a model that identifies better.
    """
    try:
        from sklearn.calibration import calibration_curve
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.metrics import brier_score_loss, roc_auc_score
    except ImportError:  # pragma: no cover
        return {"status": "skipped", "reason": "scikit-learn not installed"}

    feats = [*train.covariates, "loan_age", "payment_gap", "period_index"]
    feats = [f for f in feats if f in train.frame.columns and f in test.frame.columns]

    tr = train.frame.select([*feats, outcome]).drop_nulls()
    te = test.frame.select([*feats, outcome]).drop_nulls()
    if tr.height == 0 or te.height == 0:
        return {"status": "skipped", "reason": "empty train or test set"}
    if tr.height > max_rows:
        tr = tr.sample(max_rows, seed=seed)

    xtr = tr.select(feats).to_numpy()
    ytr = tr[outcome].to_numpy()
    xte = te.select(feats).to_numpy()
    yte = te[outcome].to_numpy()
    if ytr.sum() == 0 or yte.sum() == 0:
        return {"status": "skipped", "reason": "no events in train or test set"}

    clf = HistGradientBoostingClassifier(
        max_iter=200, learning_rate=0.08, max_depth=5, random_state=seed
    )
    clf.fit(xtr, ytr)
    p_te = clf.predict_proba(xte)[:, 1]

    # Compare against the discrete-time logit on the same out-of-time rows.
    logit = discrete_time_logit(train, outcome=outcome, cluster_col=None)
    X_te, names, kept_te = design_matrix(test, include_age_dummies=True)
    coef_map = {c["term"]: c["coef"] for c in logit["coefficients"]}
    beta = np.array([coef_map.get(n, 0.0) for n in names])
    p_logit = 1.0 / (1.0 + np.exp(-(X_te @ beta)))
    y_logit = kept_te[outcome].to_numpy()

    frac_pos, mean_pred = calibration_curve(yte, p_te, n_bins=10, strategy="quantile")

    return {
        "model": "HistGradientBoostingClassifier (predictive benchmark only)",
        "outcome": outcome,
        "features": feats,
        "train_period": f"{train.period_min}..{train.period_max}",
        "test_period": f"{test.period_min}..{test.period_max}",
        "n_train": int(tr.height),
        "n_test": int(te.height),
        "out_of_time_auc_gbm": float(roc_auc_score(yte, p_te)),
        "out_of_time_auc_discrete_time_logit": float(roc_auc_score(y_logit, p_logit)),
        "out_of_time_brier_gbm": float(brier_score_loss(yte, p_te)),
        "out_of_time_brier_logit": float(brier_score_loss(y_logit, p_logit)),
        "base_rate_test": float(yte.mean()),
        "calibration_curve": {
            "mean_predicted": [float(x) for x in mean_pred],
            "fraction_positive": [float(x) for x in frac_pos],
        },
        "interpretation_warning": (
            "Predictive performance is NOT evidence of causal identification. This "
            "rung exists only to show whether the discrete-time specification is "
            "leaving predictive signal unexploited."
        ),
    }
