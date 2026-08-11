"""Loan-level sensitivity analyses.

These exist because three places in the codebase asserted that a sensitivity check
was performed when it was not (``events.py``, ``lockin_measures.py``, ``rates.py``).
A comment claiming a robustness cell is a promise; this module keeps it.

Three checks, each re-estimating the headline discrete-time prepayment hazard under
an alternative that the main pipeline deliberately does *not* use:

1. **Administrative removals counted as prepayment.** The baseline censors Zero
   Balance Codes 15/16/96 because they are Freddie Mac portfolio and
   representation-and-warranty actions, not borrower decisions. Censoring assumes the
   removal is uninformative about the borrower's latent exit time -- an assumption,
   not a fact. This cell instead counts them as prepayment, bounding the error.
2. **Fresh-term payment gap.** The baseline holds the remaining term fixed when
   computing the payment-equivalent lock-in cost. A mover typically takes a fresh
   30-year term, so this cell substitutes ``payment_gap_fresh_term``.
3. **Month-end rate alignment.** The baseline attaches the last market rate available
   on the *first* day of the month (conservative). This cell uses the month-end rule,
   which lets a borrower see rates published during the month they acted in -- less
   conservative, and a check on whether the timing convention drives the coefficient.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from lockin.adapters import pmms
from lockin.config import Config
from lockin.episodes import scan_episodes
from lockin.rates import monthly_market_rate
from lockin.survival.dataset import build_discrete_time
from lockin.survival.models import discrete_time_logit


def _coef(res: dict[str, Any], term: str = "rate_gap") -> dict[str, Any]:
    for c in res.get("coefficients", []):
        if c["term"] == term:
            return {
                "coef": c["coef"],
                "std_err": c["std_err"],
                "z": c["z"],
                "hazard_ratio": c["hazard_ratio"],
            }
    return {"coef": None, "std_err": None, "z": None, "hazard_ratio": None}


def _verdict(base: float | None, alt: float | None, se: float | None) -> str:
    """How far the alternative moves the baseline, in baseline standard errors."""
    if base is None or alt is None or not se:
        return "not_estimable"
    shift = abs(alt - base) / se
    if shift < 1.0:
        return "consistent"
    if shift < 2.0:
        return "moderate_shift"
    if base * alt < 0:
        return "sign_flip"
    return "large_shift"


def run_sensitivities(cfg: Config) -> dict[str, Any]:
    """Run all three loan-level sensitivity checks against the baseline hazard."""
    baseline_ds = build_discrete_time(cfg)
    baseline = discrete_time_logit(baseline_ds)
    base = _coef(baseline)
    base_se = base["std_err"]

    cells: list[dict[str, Any]] = [
        {
            "cell": "baseline",
            "description": "ZB 15/16/96 censored; payment gap on the remaining term; "
            "month-start rate alignment",
            **base,
            "n_obs": baseline["n_obs"],
            "n_events": baseline["n_events"],
            "verdict": "baseline",
        }
    ]

    # -- 1. administrative removals counted as prepayment --------------------
    cells.append(_admin_removal_as_prepayment(cfg, base, base_se))

    # -- 2. fresh-term payment gap -------------------------------------------
    cells.append(_fresh_term_gap(cfg, base, base_se))

    # -- 3. month-end rate alignment -----------------------------------------
    cells.append(_month_end_alignment(cfg, base, base_se))

    return {
        "baseline_rate_gap_coef": base["coef"],
        "baseline_std_err": base_se,
        "cells": cells,
        "verdict_definitions": {
            "consistent": "moves the baseline coefficient by less than 1 baseline s.e.",
            "moderate_shift": "1 to 2 baseline s.e.",
            "large_shift": "more than 2 baseline s.e., same sign",
            "sign_flip": "opposite sign",
            "not_estimable": "the alternative could not be fitted",
        },
        "why_these_three": (
            "Each corresponds to a modelling choice the codebase makes and documents "
            "as an assumption rather than a fact: censoring Freddie Mac portfolio "
            "actions, holding the remaining term fixed in the payment gap, and using "
            "the conservative month-start rate-availability rule."
        ),
    }


def _admin_removal_as_prepayment(
    cfg: Config, base: dict[str, Any], base_se: float | None
) -> dict[str, Any]:
    """Re-label ZB 15/16/96 exits as prepayment instead of censoring them.

    Rebuilds the outcome directly on the episode table: the loan's final observed
    month becomes an event if its loan-event record was censored for an
    administrative reason.
    """
    from lockin.events import load_loan_events

    try:
        events = load_loan_events(cfg)
        admin = events.filter(pl.col("zero_balance_code").is_in(["15", "16", "96"])).select(
            "loan_seq_no", pl.col("event_date").alias("_admin_date")
        )
        n_admin = admin.height

        if n_admin == 0:
            return {
                "cell": "admin_removals_as_prepayment",
                "description": "ZB 15/16/96 counted as prepayment rather than censored",
                "coef": None,
                "std_err": None,
                "z": None,
                "hazard_ratio": None,
                "n_reclassified": 0,
                "verdict": "not_estimable",
                "note": "no administrative removals in this sample",
            }

        ds = build_discrete_time(cfg)
        frame = ds.frame.join(admin.lazy().collect(), on="loan_seq_no", how="left")
        frame = frame.with_columns(
            pl.max_horizontal(
                pl.col("exit_prepayment"),
                (pl.col("period") == pl.col("_admin_date")).cast(pl.Int8).fill_null(0),
            ).alias("exit_prepayment")
        ).drop("_admin_date")
        ds.frame = frame
        res = discrete_time_logit(ds)
        c = _coef(res)
        return {
            "cell": "admin_removals_as_prepayment",
            "description": "ZB 15/16/96 counted as prepayment rather than censored",
            **c,
            "n_obs": res["n_obs"],
            "n_events": res["n_events"],
            "n_reclassified": n_admin,
            "verdict": _verdict(base["coef"], c["coef"], base_se),
            "interpretation": (
                "The baseline censors these because whole-loan sales, RPL "
                "securitizations, and defect repurchases are Freddie Mac portfolio "
                "actions. This cell bounds the error if that censoring is informative."
            ),
        }
    except Exception as exc:
        return {
            "cell": "admin_removals_as_prepayment",
            "verdict": "not_estimable",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _fresh_term_gap(cfg: Config, base: dict[str, Any], base_se: float | None) -> dict[str, Any]:
    """Substitute a fresh-30-year-term payment gap for the remaining-term one.

    The rate gap itself is unchanged, so this cell re-estimates with the *payment*
    gap as the lock-in regressor under both conventions -- that is where the two
    definitions actually differ.
    """
    from lockin.amortization import payment

    try:
        ds = build_discrete_time(cfg)
        df = ds.frame
        bal = df["upb_start_of_month"].cast(pl.Float64).to_numpy()
        note = df["note_rate"].cast(pl.Float64).to_numpy()
        mkt = df["market_rate"].cast(pl.Float64).to_numpy()
        rem = df["remaining_term"].cast(pl.Float64).to_numpy()

        same = payment(bal, mkt, rem) - payment(bal, note, rem)
        fresh = payment(bal, mkt, np.full_like(rem, 360.0)) - payment(bal, note, rem)

        out: dict[str, Any] = {
            "cell": "payment_gap_fresh_term",
            "description": "payment-equivalent lock-in cost computed against a fresh "
            "360-month term instead of the remaining term",
            "mean_payment_gap_remaining_term": float(np.nanmean(same)),
            "mean_payment_gap_fresh_term": float(np.nanmean(fresh)),
            "correlation": float(
                np.corrcoef(
                    same[np.isfinite(same) & np.isfinite(fresh)],
                    fresh[np.isfinite(same) & np.isfinite(fresh)],
                )[0, 1]
            ),
        }

        # Re-estimate with each payment-gap definition as the regressor.
        for label, vals in (("remaining_term", same), ("fresh_term", fresh)):
            ds2 = build_discrete_time(cfg)
            ds2.frame = ds2.frame.with_columns(pl.Series("payment_gap_reg", vals / 1000.0))
            ds2.covariates = [c for c in ds2.covariates if c != "rate_gap"] + ["payment_gap_reg"]
            res = discrete_time_logit(ds2)
            c = _coef(res, "payment_gap_reg")
            out[f"coef_{label}"] = c["coef"]
            out[f"std_err_{label}"] = c["std_err"]

        out["coef"] = out.get("coef_fresh_term")
        out["std_err"] = out.get("std_err_fresh_term")
        out["verdict"] = _verdict(
            out.get("coef_remaining_term"),
            out.get("coef_fresh_term"),
            out.get("std_err_remaining_term"),
        )
        out["units"] = "log-odds per $1,000/month of payment gap"
        out["interpretation"] = (
            "Re-extending to a fresh term lowers the measured payment shock, so the "
            "fresh-term definition is the more conservative measure of lock-in cost "
            "for a trade-up buyer and the less conservative one for a borrower late "
            "in their term."
        )
        _ = base
        return out
    except Exception as exc:
        return {
            "cell": "payment_gap_fresh_term",
            "verdict": "not_estimable",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _month_end_alignment(
    cfg: Config, base: dict[str, Any], base_se: float | None
) -> dict[str, Any]:
    """Re-attach market rates under the month-end availability rule.

    Still no look-ahead -- the month-end rule only lets a borrower see rates published
    within the month they acted in -- but it is less conservative than month-start.
    """
    try:
        alt = monthly_market_rate(pmms.load(cfg), series=cfg.rates.series, as_of="month_end")
        ds = build_discrete_time(cfg)
        joined = (
            ds.frame.join(
                alt.select(pl.col("period"), pl.col("market_rate").alias("_mkt_end")),
                on="period",
                how="left",
            )
            .with_columns((pl.col("_mkt_end") - pl.col("note_rate")).alias("rate_gap"))
            .drop_nulls("rate_gap")
        )
        ds.frame = joined.drop("_mkt_end")
        res = discrete_time_logit(ds)
        c = _coef(res)
        return {
            "cell": "month_end_rate_alignment",
            "description": "market rate is the last observation available on the LAST "
            "day of the month rather than the first",
            **c,
            "n_obs": res["n_obs"],
            "n_events": res["n_events"],
            "verdict": _verdict(base["coef"], c["coef"], base_se),
            "interpretation": (
                "Still no look-ahead: the borrower may only see rates published within "
                "the month they acted in. A large shift would mean the coefficient is "
                "sensitive to the within-month timing convention."
            ),
        }
    except Exception as exc:
        return {
            "cell": "month_end_rate_alignment",
            "verdict": "not_estimable",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _unused(x: pl.LazyFrame) -> None:  # pragma: no cover
    _ = scan_episodes
    return None
