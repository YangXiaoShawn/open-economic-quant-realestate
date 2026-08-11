"""Run the full hazard ladder and write result artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl

from lockin.artifacts import write_artifact
from lockin.config import Config
from lockin.lockin_measures import GAP_BUCKET_LABELS
from lockin.provenance import collect_source_versions, run_context
from lockin.survival.dataset import build_discrete_time, build_loan_level
from lockin.survival.models import (
    baseline_hazard_by_age,
    cox_ph,
    cumulative_incidence,
    discrete_time_cloglog,
    discrete_time_logit,
    gradient_boosted_benchmark,
    kaplan_meier,
    kaplan_meier_by_group,
    nonlinear_gap_profile,
)

POPULATION = (
    "Loans in the ingested Freddie Mac Single-Family cohorts: conventional, "
    "conforming, single-family, fixed-rate, acquired by Freddie Mac. A SELECTED "
    "mortgage population -- excludes FHA/VA, jumbo, non-QM, portfolio loans, "
    "all-cash purchases, and all mortgage-free owners."
)
OUTCOME_PREPAY = (
    "prepayment = Freddie Mac Zero Balance Code 01 'Prepaid or Matured (Voluntary "
    "Payoff)'. Conflates voluntary payoff with scheduled maturity and does NOT "
    "distinguish refinance from sale-related payoff. NOT a home sale, NOT a move."
)
OUTCOME_CREDIT = "credit event = Zero Balance Code 02, 03, or 09."


def run_hazard_ladder(cfg: Config) -> dict[str, Path]:
    """Estimate every rung and write one artifact per rung."""
    ctx = run_context(cfg, source_versions=collect_source_versions(cfg))
    written: dict[str, Path] = {}

    def emit(
        name: str, tier: str, outcome: str, weight: str, result: dict, caveats: list[str]
    ) -> None:
        written[name] = write_artifact(
            cfg,
            ctx,
            group="hazards",
            name=name,
            evidence_tier=tier,
            population=POPULATION,
            geography=cfg.panel.geography,
            outcome_definition=outcome,
            weight=weight,
            result=result,
            caveats=caveats,
        )

    loans = build_loan_level(cfg)
    train = build_discrete_time(cfg, out_of_time=False)
    test = build_discrete_time(cfg, out_of_time=True)

    # -- rung 1: Kaplan-Meier ------------------------------------------------
    km = kaplan_meier(loans)
    loans_bucketed = loans.with_columns(
        pl.when(pl.col("rate_gap_at_entry") <= -1.0)
        .then(pl.lit("entry gap <= -100bp (refi incentive)"))
        .when(pl.col("rate_gap_at_entry") <= 0.0)
        .then(pl.lit("entry gap -100 to 0bp"))
        .when(pl.col("rate_gap_at_entry") <= 1.0)
        .then(pl.lit("entry gap 0 to +100bp"))
        .when(pl.col("rate_gap_at_entry") <= 2.0)
        .then(pl.lit("entry gap +100 to +200bp"))
        .otherwise(pl.lit("entry gap > +200bp (locked in)"))
        .alias("entry_gap_bucket")
    )
    km_by = kaplan_meier_by_group(loans_bucketed, "entry_gap_bucket")
    emit(
        "km_prepayment",
        "descriptive",
        OUTCOME_PREPAY,
        "loan count (unweighted)",
        {
            "overall": km,
            "by_entry_rate_gap_bucket": km_by,
            "n_loans": loans.height,
            "n_left_truncated": int(loans["left_truncated"].sum()),
            "bucketing_note": "buckets use the rate gap at the loan's FIRST observed "
            "month, not a time-varying gap; the discrete-time models "
            "use the point-in-time gap.",
        },
        [
            "Stratifying by the entry gap does not control for anything. Loans with a "
            "large entry gap were originated in different years, to different "
            "borrowers, at different LTVs.",
            "Competing credit events are treated as censoring here; see the "
            "cumulative-incidence artifact for the competing-risk version.",
        ],
    )

    # -- rung 2: cumulative incidence ---------------------------------------
    cif = cumulative_incidence(loans)
    emit(
        "cif_competing_risks",
        "descriptive",
        f"{OUTCOME_PREPAY} || {OUTCOME_CREDIT}",
        "loan count (unweighted)",
        cif,
        [
            "Cumulative incidence, not 1-KM: the two differ whenever a competing cause "
            "removes loans from the risk set."
        ],
    )

    # -- rung 3-4: discrete-time hazards ------------------------------------
    logit = discrete_time_logit(train)
    emit(
        "dt_logit_prepayment",
        "hazard_association",
        OUTCOME_PREPAY,
        "loan-month, sampling weight applied",
        logit,
        [
            "ASSOCIATION only. No identification argument is made at the loan level.",
            "The rate gap uses the national PMMS rate; local offered rates differ, "
            "so the gap carries measurement error that attenuates the coefficient.",
            "Loan-age dummies absorb duration dependence but calendar time is only "
            "partly controlled; cohort effects and the national rate path are "
            "collinear with the gap by construction.",
        ],
    )
    cloglog = discrete_time_cloglog(train)
    emit(
        "dt_cloglog_prepayment",
        "hazard_association",
        OUTCOME_PREPAY,
        "loan-month, sampling weight applied",
        cloglog,
        [
            "Complementary log-log: coefficients are log hazard ratios under a "
            "discrete-time proportional-hazards interpretation."
        ],
    )

    # competing-risk cause-specific hazard
    credit = discrete_time_logit(train, outcome="exit_credit_event")
    emit(
        "dt_logit_credit_event",
        "hazard_association",
        OUTCOME_CREDIT,
        "loan-month, sampling weight applied",
        credit,
        [
            "Cause-specific hazard for the competing cause. Cause-specific "
            "coefficients do not translate directly into effects on cumulative "
            "incidence, because a covariate can raise one cause-specific hazard and "
            "lower the CIF of the other."
        ],
    )

    emit(
        "baseline_hazard",
        "descriptive",
        OUTCOME_PREPAY,
        "loan-month (unweighted)",
        {
            "prepayment": baseline_hazard_by_age(train, "exit_prepayment"),
            "credit_event": baseline_hazard_by_age(train, "exit_credit_event"),
        },
        [
            "Empirical hazards with no covariates; the age profile mixes duration "
            "dependence with cohort and calendar-time composition."
        ],
    )

    emit(
        "gap_profile_nonlinear",
        "hazard_association",
        OUTCOME_PREPAY,
        "loan-month, sampling weight applied",
        {
            "prepayment": nonlinear_gap_profile(train, "exit_prepayment"),
            "gap_bucket_labels": list(GAP_BUCKET_LABELS),
        },
        [
            "Binned-gap specification; the reference bucket is 0 to +100bp so "
            "coefficients read as 'relative to a barely locked-in loan'."
        ],
    )

    # -- heterogeneity ------------------------------------------------------
    emit(
        "heterogeneity",
        "hazard_association",
        OUTCOME_PREPAY,
        "loan-month, sampling weight applied",
        _heterogeneity(cfg, train),
        [
            "PRE-SPECIFIED subgroups: note rate, loan age, current LTV, credit "
            "score, loan balance, occupancy, loan purpose, region. Any subgroup not "
            "in that list is EXPLORATORY and labeled as such in the result.",
            "No multiplicity correction is applied; treat individual subgroup "
            "p-values accordingly.",
        ],
    )

    # -- rung 5: Cox --------------------------------------------------------
    cox = cox_ph(
        loans,
        covariates=["rate_gap_at_entry", "credit_score", "orig_ltv", "orig_dti"],
        seed=cfg.survival.seed,
    )
    emit(
        "cox_ph_prepayment",
        "hazard_association",
        OUTCOME_PREPAY,
        "loan (unweighted)",
        cox,
        [
            "Loan-level Cox cannot carry the time-varying rate gap; it uses the gap at "
            "entry. Included as a functional-form check on the discrete-time rungs."
        ],
    )

    # -- rung 6: predictive benchmark ---------------------------------------
    gbm = gradient_boosted_benchmark(train, test, seed=cfg.survival.seed)
    emit(
        "predictive_benchmark",
        "hazard_association",
        OUTCOME_PREPAY,
        "loan-month (unweighted)",
        gbm,
        [
            "Predictive performance is not identification. Reported for out-of-time "
            "discrimination and calibration only."
        ],
    )

    # -- loan-level sensitivity cells ---------------------------------------
    from lockin.survival.sensitivity import run_sensitivities

    emit(
        "sensitivity_cells",
        "hazard_association",
        OUTCOME_PREPAY,
        "loan-month",
        run_sensitivities(cfg),
        [
            "These re-estimate the headline hazard under alternatives the baseline "
            "deliberately does not use. They exist because three places in the code "
            "documented a sensitivity check as if it had been run.",
            "Censoring ZB 15/16/96 is an ASSUMPTION (that the removal is uninformative "
            "about the borrower's latent exit time), not a fact. The first cell bounds "
            "the error if it is wrong.",
        ],
    )

    # -- dataset description (for the methodology report) -------------------
    emit(
        "survival_dataset",
        "descriptive",
        OUTCOME_PREPAY,
        "n/a",
        {
            "estimation_sample": train.describe(),
            "out_of_time_sample": test.describe(),
            "out_of_time_split": cfg.survival.out_of_time_split,
            "loan_level": {
                "n_loans": loans.height,
                "n_left_truncated": int(loans["left_truncated"].sum()),
                "median_entry_age": float(loans["entry_age"].median() or 0),
                "n_prepayments": int((loans["event_code"] == 1).sum()),
                "n_credit_events": int((loans["event_code"] == 2).sum()),
                "n_censored": int((loans["event_code"] == 0).sum()),
            },
        },
        ["Left truncation at Freddie Mac acquisition is respected in the risk sets."],
    )
    return written


def _heterogeneity(cfg: Config, train) -> dict:
    """Pre-specified subgroup hazards. Exploratory groups are labeled."""
    prespecified = {
        "note_rate_tercile": pl.col("note_rate").qcut(3, labels=["low", "mid", "high"]),
        "loan_age_group": pl.when(pl.col("loan_age") < 24)
        .then(pl.lit("<24m"))
        .when(pl.col("loan_age") < 48)
        .then(pl.lit("24-47m"))
        .otherwise(pl.lit("48m+")),
        "est_ltv_group": pl.when(pl.col("est_current_ltv") < 60)
        .then(pl.lit("<60"))
        .when(pl.col("est_current_ltv") < 80)
        .then(pl.lit("60-80"))
        .otherwise(pl.lit("80+")),
        "credit_score_group": pl.when(pl.col("credit_score") < 700)
        .then(pl.lit("<700"))
        .when(pl.col("credit_score") < 760)
        .then(pl.lit("700-759"))
        .otherwise(pl.lit("760+")),
        "balance_group": pl.when(pl.col("orig_upb") < 200_000)
        .then(pl.lit("<200k"))
        .when(pl.col("orig_upb") < 400_000)
        .then(pl.lit("200-400k"))
        .otherwise(pl.lit("400k+")),
        "occupancy_status": pl.col("occupancy_status"),
        "loan_purpose": pl.col("loan_purpose"),
    }
    exploratory = {
        "first_time_homebuyer_flag": pl.col("first_time_homebuyer_flag"),
        "property_type": pl.col("property_type"),
    }

    # Holds nested subgroup tables plus a top-level "note" string.
    out: dict[str, Any] = {"prespecified": {}, "exploratory": {}}
    for label, groups in (("prespecified", prespecified), ("exploratory", exploratory)):
        for name, expr in groups.items():
            try:
                g = (
                    train.frame.with_columns(expr.alias("_g"))
                    .group_by("_g")
                    .agg(
                        pl.len().alias("n_loan_months"),
                        pl.col("loan_seq_no").n_unique().alias("n_loans"),
                        pl.col("exit_prepayment").sum().alias("n_prepayments"),
                        pl.col("exit_prepayment").mean().alias("monthly_prepay_hazard"),
                        pl.col("rate_gap").mean().alias("mean_rate_gap"),
                        pl.col("payment_gap").mean().alias("mean_payment_gap"),
                    )
                    .sort("_g")
                )
                out[label][name] = g.rename({"_g": "group"}).to_dicts()
            except Exception as exc:
                out[label][name] = [{"error": f"{type(exc).__name__}: {exc}"}]

    out["note"] = (
        "Subgroup hazards are DESCRIPTIVE within subgroup: they do not hold the "
        "other covariates fixed. A high-note-rate group differs from a low-note-rate "
        "group in origination year, credit, and equity as well as in the rate gap."
    )
    _ = cfg
    return out
