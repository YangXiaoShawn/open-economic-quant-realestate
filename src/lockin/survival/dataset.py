"""Survival estimation datasets built from the loan-month episode table.

Two shapes are produced:

* **Discrete-time person-period** -- one row per observed loan-month, an event
  indicator, loan-age bin dummies, and covariates. This is the workhorse for the
  logit and cloglog hazards and for the cause-specific competing-risk models.
* **Loan-level (start, stop, event)** -- one row per loan for Kaplan-Meier,
  cumulative incidence, and Cox, with ``entry`` set to the loan age at first
  observation so that **left truncation** is respected rather than ignored.

Case-cohort sampling: when the episode table is too large, all exit months are
retained and non-exit months are sampled at ``survival.non_event_sample_fraction``.
Each row carries ``sampling_weight``; the sampling design is recorded in the
artifact so no coefficient is ever reported without it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import polars as pl

from lockin.config import Config
from lockin.episodes import scan_episodes

#: Covariates used in the hazard specifications. Kept in one place so that the
#: model ladder is genuinely comparable across rungs.
BASE_COVARIATES: tuple[str, ...] = (
    "rate_gap",
    "est_current_ltv",
    "credit_score",
    "orig_dti",
    "log_orig_upb",
    "orig_ltv",
)
OPTIONAL_COVARIATES: tuple[str, ...] = ("hpi_growth_12m",)


@dataclass(slots=True)
class SurvivalDataset:
    frame: pl.DataFrame
    age_bins: list[str]
    covariates: list[str]
    sampling_design: dict[str, Any] = field(default_factory=dict)
    n_loans: int = 0
    n_episodes_total: int = 0
    period_min: str = ""
    period_max: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "n_rows": self.frame.height,
            "n_loans": self.n_loans,
            "n_episodes_before_sampling": self.n_episodes_total,
            "period": f"{self.period_min}..{self.period_max}",
            "age_bins": self.age_bins,
            "covariates": self.covariates,
            "sampling_design": self.sampling_design,
            "n_prepayments": int(self.frame["exit_prepayment"].sum()),
            "n_credit_events": int(self.frame["exit_credit_event"].sum()),
        }


def _age_bin_expr(edges: list[int]) -> tuple[pl.Expr, list[str]]:
    labels = [f"age_{edges[i]}_{edges[i + 1]}" for i in range(len(edges) - 1)]
    e = pl.when(pl.col("loan_age") < edges[1]).then(pl.lit(labels[0]))
    for i in range(1, len(labels)):
        lo, hi = edges[i], edges[i + 1]
        e = e.when((pl.col("loan_age") >= lo) & (pl.col("loan_age") < hi)).then(pl.lit(labels[i]))
    return (e.otherwise(pl.lit(labels[-1])).alias("age_bin"), labels)


def build_discrete_time(
    cfg: Config,
    out_of_time: bool = False,
    extra_filter: pl.Expr | None = None,
) -> SurvivalDataset:
    """Build the person-period dataset.

    Parameters
    ----------
    out_of_time
        ``False`` -> estimation sample (periods before ``survival.out_of_time_split``).
        ``True``  -> evaluation sample (periods on/after the split).
    extra_filter
        Optional additional restriction, used by robustness cells (e.g. purchase
        loans only, primary residence only).
    """
    split_y, split_m = map(int, cfg.survival.out_of_time_split.split("-"))
    split = date(split_y, split_m, 1)

    lf = scan_episodes(cfg).filter(
        pl.col("market_rate").is_not_null() & pl.col("rate_gap").is_not_null()
    )
    if extra_filter is not None:
        lf = lf.filter(extra_filter)
    lf = (
        lf.filter(pl.col("period") >= split) if out_of_time else lf.filter(pl.col("period") < split)
    )

    age_expr, labels = _age_bin_expr(cfg.survival.age_bin_edges)
    lf = lf.with_columns(
        age_expr,
        pl.col("orig_upb").log().alias("log_orig_upb"),
        pl.col("period").dt.year().alias("calendar_year"),
        (pl.col("period").dt.year() * 12 + pl.col("period").dt.month()).alias("period_index"),
    )

    total = lf.select(pl.len()).collect().item()
    frac = float(cfg.survival.non_event_sample_fraction)
    design: dict[str, Any] = {"scheme": "full", "non_event_sample_fraction": 1.0}

    already = bool(getattr(cfg.survival, "sample_at_episode_build", False))
    if already and 0.0 < frac < 1.0:
        # The identical predicate ran in lockin.episodes before the table was written,
        # so re-applying it here would sample the survivors a SECOND time and quietly
        # shrink the risk set to frac^2 of the population, while the weight below still
        # said 1/frac. The rows are already the right rows; only the design record and
        # the weight are still owed.
        design = {
            "scheme": "case-cohort applied AT EPISODE BUILD: all exit months retained, "
            "non-exit loans sampled by a stable hash of loan_seq_no",
            "non_event_sample_fraction": frac,
            "seed": cfg.survival.seed,
            "weight_column": "sampling_weight",
            "applied_at": "episode_build",
            "caveat": "Coefficients on time-varying covariates are consistent under "
            "this design with the offset applied; the estimated BASELINE hazard "
            "level is not directly interpretable without the weight.",
        }
    elif 0.0 < frac < 1.0:
        rng_seed = cfg.survival.seed
        lf = (
            lf.with_columns(
                pl.when(pl.col("exit_any") == 1)
                .then(pl.lit(True))
                .otherwise(
                    pl.col("loan_seq_no").hash(seed=rng_seed).mod(1_000_000) < int(frac * 1_000_000)
                )
                .alias("_keep")
            )
            .filter(pl.col("_keep"))
            .drop("_keep")
        )
        design = {
            "scheme": "case-cohort: all exit months retained, non-exit months "
            "sampled by a stable hash of loan_seq_no",
            "non_event_sample_fraction": frac,
            "seed": rng_seed,
            "weight_column": "sampling_weight",
            "caveat": "Coefficients on time-varying covariates are consistent under "
            "this design with the offset applied; the estimated BASELINE hazard "
            "level is not directly interpretable without the weight.",
        }

    # Inverse-probability weight. Two independent draws can have thinned the rows:
    # a plain loan sample (everything, cases included) and the case-cohort filter
    # (non-exiting loans only). A row's weight is the reciprocal of its total
    # inclusion probability, so both have to appear here -- weighting only for the
    # case-cohort draw would understate the population by 1/loan_frac.
    loan_frac = float(getattr(cfg.survival, "loan_sample_fraction", 1.0))
    if not (0.0 < loan_frac <= 1.0):
        loan_frac = 1.0
    base_w = 1.0 / loan_frac
    design["loan_sample_fraction"] = loan_frac
    design["weight_definition"] = (
        "1 / P(included) = (1/loan_sample_fraction) for an exit month, and "
        "(1/loan_sample_fraction) x (1/non_event_sample_fraction) otherwise"
    )
    df = lf.with_columns(
        pl.when(pl.col("exit_any") == 1)
        .then(base_w)
        .otherwise(base_w / max(frac, 1e-9))
        .alias("sampling_weight")
    ).collect(engine="streaming")

    covs = [c for c in BASE_COVARIATES if c in df.columns]
    for c in OPTIONAL_COVARIATES:
        if c in df.columns and df[c].null_count() < df.height:
            covs.append(c)

    return SurvivalDataset(
        frame=df,
        age_bins=labels,
        covariates=covs,
        sampling_design=design,
        n_loans=df["loan_seq_no"].n_unique(),
        n_episodes_total=total,
        period_min=str(df["period"].min()),
        period_max=str(df["period"].max()),
    )


def build_loan_level(cfg: Config) -> pl.DataFrame:
    """One row per loan: ``(entry_age, exit_age, event_code)`` with left truncation.

    ``event_code``: 0 = censored, 1 = prepayment, 2 = credit event. Used for
    Kaplan-Meier, Aalen-Johansen cumulative incidence, and Cox.

    The rate-gap covariate here is the value at **entry** (the first observed
    month), because a loan-level model cannot carry a time-varying covariate. That
    is a real limitation and the discrete-time models are the preferred rung of the
    ladder for anything involving the rate gap.
    """
    lf = scan_episodes(cfg)
    per_loan = (
        lf.sort(["loan_seq_no", "period"])
        .group_by("loan_seq_no")
        .agg(
            pl.col("loan_age").min().alias("entry_age"),
            pl.col("loan_age").max().alias("exit_age"),
            pl.col("exit_prepayment").max().alias("is_prepay"),
            pl.col("exit_credit_event").max().alias("is_credit"),
            pl.col("rate_gap").first().alias("rate_gap_at_entry"),
            pl.col("rate_gap").last().alias("rate_gap_at_exit"),
            pl.col("lockin_gap").mean().alias("mean_lockin_gap"),
            pl.col("payment_gap").last().alias("payment_gap_at_exit"),
            pl.col("note_rate").first().alias("note_rate"),
            pl.col("credit_score").first().alias("credit_score"),
            pl.col("orig_ltv").first().alias("orig_ltv"),
            pl.col("orig_dti").first().alias("orig_dti"),
            pl.col("orig_upb").first().alias("orig_upb"),
            pl.col("est_current_ltv").last().alias("est_current_ltv_at_exit"),
            pl.col("property_state").first().alias("property_state"),
            pl.col("loan_purpose").first().alias("loan_purpose"),
            pl.col("occupancy_status").first().alias("occupancy_status"),
            pl.col("orig_cohort_year").first().alias("orig_cohort_year"),
            pl.col("period").min().alias("first_period"),
            pl.col("period").max().alias("last_period"),
            pl.len().alias("n_months_observed"),
        )
        .collect(engine="streaming")
    )
    return per_loan.with_columns(
        pl.when(pl.col("is_prepay") == 1)
        .then(1)
        .when(pl.col("is_credit") == 1)
        .then(2)
        .otherwise(0)
        .alias("event_code"),
        (pl.col("exit_age") - pl.col("entry_age") + 1).alias("months_at_risk"),
        (pl.col("entry_age") > 1).alias("left_truncated"),
    ).sort("loan_seq_no")


def design_matrix(
    ds: SurvivalDataset,
    covariates: list[str] | None = None,
    include_age_dummies: bool = True,
    include_gap_bins: bool = False,
    drop_first_age: bool = True,
) -> tuple[np.ndarray, list[str], pl.DataFrame]:
    """Build a numeric design matrix, dropping rows with any missing covariate.

    Returns ``(X, column_names, retained_frame)``. An intercept column is included
    first. Missing-covariate rows are dropped listwise; the count is reported by the
    caller so attrition is visible.
    """
    covs = covariates if covariates is not None else list(ds.covariates)
    df = ds.frame
    needed = [c for c in covs if c in df.columns]
    df = df.drop_nulls(needed) if needed else df

    cols: list[np.ndarray] = [np.ones(df.height)]
    names: list[str] = ["intercept"]

    for c in needed:
        cols.append(df[c].cast(pl.Float64).to_numpy())
        names.append(c)

    if include_age_dummies:
        bins = ds.age_bins[1:] if drop_first_age else ds.age_bins
        for b in bins:
            cols.append((df["age_bin"] == b).cast(pl.Float64).to_numpy())
            names.append(b)

    if include_gap_bins:
        from lockin.lockin_measures import GAP_BUCKET_LABELS

        present = sorted(df["gap_bucket"].unique().drop_nulls().to_list())
        ref = 3 if 3 in present else present[0]  # reference: 0 to +100bp
        for b in present:
            if b == ref:
                continue
            cols.append((df["gap_bucket"] == b).cast(pl.Float64).to_numpy())
            names.append(f"gapbin[{GAP_BUCKET_LABELS[int(b)]}]")

    X = np.column_stack(cols)

    # Drop zero-variance columns (typically loan-age bins with no observations in the
    # estimation window). A plain GLM fit tolerates them via a pseudo-inverse, but the
    # cluster-robust sandwich inverts X'X exactly and fails with a singular matrix --
    # so silently keeping them costs the clustered standard errors that matter most
    # here, loan-months being serially correlated within a loan.
    keep_idx: list[int] = []
    dropped: list[str] = []
    for j, nm in enumerate(names):
        if nm == "intercept" or X[:, j].std() > 0:
            keep_idx.append(j)
        else:
            dropped.append(nm)
    if dropped:
        X = X[:, keep_idx]
        names = [names[j] for j in keep_idx]
        _LAST_DROPPED.clear()
        _LAST_DROPPED.extend(dropped)
    else:
        _LAST_DROPPED.clear()

    return (X, names, df)


#: Columns dropped by the most recent :func:`design_matrix` call, for the artifact.
_LAST_DROPPED: list[str] = []


def last_dropped_columns() -> list[str]:
    """Zero-variance design columns dropped by the last :func:`design_matrix` call."""
    return list(_LAST_DROPPED)
