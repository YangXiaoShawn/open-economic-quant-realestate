"""Policy counterfactual module.

**These are model-dependent projections, not forecasts.** Every artifact carries
``evidence_tier: simulation`` and the literal string "not a forecast".

Mechanism. The simulator takes the estimated discrete-time prepayment hazard,
perturbs the *rate gap* (or the effective gap under a policy), and re-predicts the
monthly hazard for every loan in the active stock at a chosen baseline date. The
difference in predicted exits is the modelled behavioural response. Aggregating to
the geography level gives a modelled change in transaction-relevant flows, which is
then mapped into price and construction responses through **calibrated** elasticities.

The honesty boundary, stated in every scenario:

* **Estimated** inputs: the hazard coefficients (from ``outputs/hazards/``), the
  active stock composition, the observed rate path.
* **Calibrated** inputs: price elasticity of demand, supply elasticity, holding
  period, the share of prepayments that reflect a move rather than a refinance
  (which is *not identified* -- see below), policy take-up shares, fiscal unit costs.

The single largest source of model uncertainty is that **a prepayment is not a
move**. Converting modelled additional prepayments into modelled additional
*transactions* requires an assumption about what fraction of prepayments correspond
to a property transaction. We do not know that fraction. We therefore report every
transaction-denominated quantity across a **range** of that share and never pick a
point value.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from lockin.artifacts import read_artifact, write_artifact
from lockin.config import Config
from lockin.episodes import scan_episodes
from lockin.provenance import collect_source_versions, run_context
from lockin.survival.dataset import BASE_COVARIATES

#: The unidentified conversion from prepayment to property transaction. Reported as
#: a range, never a point estimate.
MOVE_SHARE_RANGE: tuple[float, ...] = (0.15, 0.30, 0.50)

POPULATION = (
    "Active Freddie-acquired conforming conventional fixed-rate loans in the "
    "simulation baseline month. NOT all U.S. mortgages and NOT all homeowners."
)


def _hazard_coefficients(cfg: Config) -> tuple[dict[str, float], dict[str, Any]]:
    art = read_artifact(cfg, "hazards", "dt_logit_prepayment")
    res = art["result"]
    coefs = {c["term"]: float(c["coef"]) for c in res["coefficients"]}
    meta = {
        "source_artifact": "hazards/dt_logit_prepayment",
        "model": res["model"],
        "n_obs": res["n_obs"],
        "n_events": res["n_events"],
        "standard_errors": res["standard_errors"],
        "evidence_tier_of_input": art["evidence_tier"],
        "input_caveats": art["caveats"],
    }
    return (coefs, meta)


def _baseline_stock(cfg: Config, as_of: date) -> pl.DataFrame:
    lf = scan_episodes(cfg).filter(pl.col("period") == as_of)
    df = lf.select(
        "loan_seq_no",
        "property_state",
        "loan_age",
        "note_rate",
        "market_rate",
        "rate_gap",
        "payment_gap",
        "upb_start_of_month",
        "remaining_term",
        "est_current_ltv",
        "credit_score",
        "orig_dti",
        "orig_upb",
        "orig_ltv",
        "hpi_growth_12m",
        "loan_purpose",
        "occupancy_status",
    ).collect()
    return df.with_columns(pl.col("orig_upb").log().alias("log_orig_upb"))


def _age_bin_label(age: int, edges: list[int]) -> str:
    for i in range(len(edges) - 1):
        if edges[i] <= age < edges[i + 1]:
            return f"age_{edges[i]}_{edges[i + 1]}"
    return f"age_{edges[-2]}_{edges[-1]}"


def _predict_hazard(
    stock: pl.DataFrame,
    coefs: dict[str, float],
    cfg: Config,
    gap_override: np.ndarray | None = None,
) -> np.ndarray:
    """Predicted monthly prepayment probability for each loan in the stock."""
    eta = np.full(stock.height, coefs.get("intercept", 0.0))
    gap = (
        gap_override if gap_override is not None else stock["rate_gap"].cast(pl.Float64).to_numpy()
    )
    eta = eta + coefs.get("rate_gap", 0.0) * np.nan_to_num(gap)
    for cov in BASE_COVARIATES:
        if cov == "rate_gap" or cov not in stock.columns or cov not in coefs:
            continue
        v = stock[cov].cast(pl.Float64).to_numpy()
        v = np.nan_to_num(v, nan=float(np.nanmean(v)) if np.isfinite(np.nanmean(v)) else 0.0)
        eta = eta + coefs[cov] * v
    if "hpi_growth_12m" in stock.columns and "hpi_growth_12m" in coefs:
        v = np.nan_to_num(stock["hpi_growth_12m"].cast(pl.Float64).to_numpy())
        eta = eta + coefs["hpi_growth_12m"] * v
    ages = stock["loan_age"].cast(pl.Int64).to_numpy()
    edges = cfg.survival.age_bin_edges
    for i, a in enumerate(ages):
        eta[i] += coefs.get(_age_bin_label(int(a), edges), 0.0)
    return 1.0 / (1.0 + np.exp(-eta))


def _pmt(bal: np.ndarray, rate: np.ndarray | float, n: np.ndarray) -> np.ndarray:
    from lockin.amortization import payment

    return payment(bal, rate, n)


def _aggregate(
    stock: pl.DataFrame, h0: np.ndarray, h1: np.ndarray, cfg: Config, label: str
) -> dict[str, Any]:
    """Aggregate a per-loan hazard change into modelled flows and elasticity effects."""
    n = stock.height
    base_exits = float(h0.sum())
    new_exits = float(h1.sum())
    delta = new_exits - base_exits

    upb = stock["upb_start_of_month"].cast(pl.Float64).to_numpy()
    upb_weighted_delta = float(np.nansum((h1 - h0) * upb))

    by_state = (
        stock.with_columns(
            pl.Series("h0", h0),
            pl.Series("h1", h1),
        )
        .group_by("property_state")
        .agg(
            pl.len().alias("n_loans"),
            pl.col("h0").sum().alias("baseline_expected_exits"),
            pl.col("h1").sum().alias("scenario_expected_exits"),
            (pl.col("h1") - pl.col("h0")).sum().alias("additional_expected_exits"),
        )
        .with_columns(
            (pl.col("additional_expected_exits") / pl.col("baseline_expected_exits")).alias(
                "pct_change_in_exits"
            )
        )
        .sort("additional_expected_exits", descending=True)
    )

    # Transaction / price / construction mapping. Everything below the "modelled
    # additional prepayments" line depends on CALIBRATED inputs.
    pct_exit_change = delta / base_exits if base_exits > 0 else float("nan")
    transactions = {}
    for share in MOVE_SHARE_RANGE:
        add_txn = delta * share
        # A demand-side quantity shift mapped into price via a constant-elasticity
        # inverse demand curve with an offsetting supply response.
        eps_d = cfg.simulation.price_elasticity_of_demand
        eps_s = cfg.simulation.supply_elasticity
        # dlnP = dlnQ / (eps_s - eps_d) under simultaneous shift of both sides by the
        # same locked-in households (they add BOTH a listing and a purchase).
        q_shift = pct_exit_change * share
        dln_p = q_shift / (eps_s - eps_d) if (eps_s - eps_d) != 0 else float("nan")
        transactions[f"move_share_{share:g}"] = {
            "assumed_share_of_prepayments_that_are_property_transactions": share,
            "modelled_additional_transactions_per_month": add_txn,
            "modelled_pct_change_in_transaction_flow": q_shift,
            "modelled_log_price_change": dln_p,
            "modelled_pct_change_in_permits": q_shift * cfg.simulation.supply_elasticity,
        }

    return {
        "scenario": label,
        "n_loans_in_baseline_stock": n,
        "baseline_expected_monthly_prepayments": base_exits,
        "scenario_expected_monthly_prepayments": new_exits,
        "modelled_additional_monthly_prepayments": delta,
        "modelled_pct_change_in_prepayments": pct_exit_change,
        "modelled_additional_monthly_prepaid_upb": upb_weighted_delta,
        "by_geography": by_state.to_dicts(),
        "transaction_price_construction_mapping": transactions,
        "mapping_warning": (
            "Everything under transaction_price_construction_mapping rests on an "
            "UNIDENTIFIED parameter: the share of prepayments that correspond to a "
            "property transaction. Zero Balance Code 01 does not distinguish a "
            "refinance from a sale-related payoff, so this share is not estimable "
            "from our data. It is therefore reported across a RANGE and no point "
            "value is preferred."
        ),
    }


def run_scenarios(cfg: Config) -> dict[str, Path]:
    """Run every configured scenario and write one artifact each."""
    ctx = run_context(cfg, source_versions=collect_source_versions(cfg))
    coefs, hazard_meta = _hazard_coefficients(cfg)

    ey, em = map(int, cfg.mortgage.performance_end.split("-"))
    as_of = date(ey, em, 1)
    stock = _baseline_stock(cfg, as_of)
    if stock.height == 0:
        # Fall back to the latest month that has data.
        latest = scan_episodes(cfg).select(pl.col("period").max()).collect().item()
        as_of = latest
        stock = _baseline_stock(cfg, as_of)
    if stock.height == 0:
        raise RuntimeError("no active loans available for the simulation baseline")

    h0 = _predict_hazard(stock, coefs, cfg)
    gap0 = stock["rate_gap"].cast(pl.Float64).to_numpy()
    bal = stock["upb_start_of_month"].cast(pl.Float64).to_numpy()
    rem = stock["remaining_term"].cast(pl.Float64).to_numpy()
    note = stock["note_rate"].cast(pl.Float64).to_numpy()
    mkt = stock["market_rate"].cast(pl.Float64).to_numpy()

    calibrated = {
        "price_elasticity_of_demand": cfg.simulation.price_elasticity_of_demand,
        "supply_elasticity": cfg.simulation.supply_elasticity,
        "holding_period_months": cfg.lockin.holding_period_months,
        "discount_rate_pct": cfg.lockin.discount_rate_pct,
        "move_share_range": list(MOVE_SHARE_RANGE),
        "portability_share": cfg.simulation.portability_share,
        "assumability_share": cfg.simulation.assumability_share,
        "seller_credit_dollars": cfg.simulation.seller_credit_dollars,
        "buydown_bp": cfg.simulation.buydown_bp,
        "supply_elasticity_multiplier": cfg.simulation.supply_elasticity_multiplier,
    }
    common = {
        "baseline_month": as_of.isoformat(),
        "estimated_inputs": hazard_meta,
        "calibrated_inputs": calibrated,
        "not_a_forecast": (
            "This is a model-dependent projection, NOT a forecast. It holds the "
            "loan population, the composition of the housing stock, credit "
            "conditions, income, and migration fixed, and it applies a hazard "
            "relationship estimated on historical association as if it were a "
            "structural response function. It is not."
        ),
        "uncertainty": (
            "No confidence interval is attached to the scenario quantities. The "
            "hazard coefficients have sampling error, the calibrated elasticities "
            "have no error bars at all, and the prepayment-to-transaction share is "
            "unidentified. Treat the ORDERING of scenarios as more informative than "
            "any single magnitude."
        ),
    }

    written: dict[str, Path] = {}

    def emit(name: str, result: dict[str, Any], extra_caveats: list[str]) -> None:
        written[name] = write_artifact(
            cfg,
            ctx,
            group="scenarios",
            name=name,
            evidence_tier="simulation",
            population=POPULATION,
            geography=cfg.panel.geography,
            outcome_definition=(
                "modelled monthly PREPAYMENTS (Zero Balance Code 01 equivalent), and "
                "downstream transaction/price/permit quantities under calibrated "
                "elasticities. Prepayments are NOT moves."
            ),
            weight="loan count, with a UPB-weighted variant reported",
            result=result | common,
            caveats=[
                "MODEL-DEPENDENT. Not a forecast.",
                "The behavioural input is a hazard ASSOCIATION, not a causal "
                "elasticity. Using it as a policy response function is the central "
                "assumption of this module and it is not defended by the "
                "identification strategy.",
                *extra_caveats,
            ],
        )

    # -- 1. market rate declines --------------------------------------------
    for bp in cfg.simulation.rate_shocks_bp:
        shift = bp / 100.0
        gap1 = gap0 + shift
        h1 = _predict_hazard(stock, coefs, cfg, gap_override=gap1)
        res = _aggregate(stock, h0, h1, cfg, f"market_rate_{bp:+d}bp")
        res["policy"] = {
            "type": "market mortgage rate declines",
            "shock_bp": bp,
            "implementation": "the point-in-time rate gap shifts by the shock for every "
            "loan; note rates are unchanged",
            "mean_gap_before": float(np.nanmean(gap0)),
            "mean_gap_after": float(np.nanmean(gap1)),
            "mean_payment_gap_before": float(
                np.nanmean(_pmt(bal, mkt, rem) - _pmt(bal, note, rem))
            ),
            "mean_payment_gap_after": float(
                np.nanmean(_pmt(bal, mkt + shift, rem) - _pmt(bal, note, rem))
            ),
        }
        emit(
            f"scenario_rate_{abs(bp)}bp_decline",
            res,
            [
                "A rate decline also stimulates first-time-buyer demand and refinancing "
                "for reasons outside this model; the modelled prepayment response is "
                "only the locked-in-owner channel.",
            ],
        )

    # -- 2. partial portability ---------------------------------------------
    # A portable mortgage lets the borrower carry the note rate to a new property, so
    # the effective gap for the take-up share is zero.
    share = cfg.simulation.portability_share
    gap1 = gap0 * (1.0 - share)
    h1 = _predict_hazard(stock, coefs, cfg, gap_override=gap1)
    res = _aggregate(stock, h0, h1, cfg, "partial_portability")
    res["policy"] = {
        "type": "existing mortgages become partially portable",
        "take_up_share": share,
        "implementation": "the effective rate gap is scaled by (1 - take_up_share), "
        "representing a share of borrowers who can carry their note "
        "rate to a new property",
        "who_bears_the_cost": "the investor or guarantor holding the below-market "
        "coupon; portability transfers that loss rather than "
        "eliminating it. Not modelled here.",
    }
    emit(
        "scenario_partial_portability",
        res,
        [
            "Portability does not destroy the below-market-coupon loss, it moves it. The "
            "fiscal/investor cost is NOT modelled.",
            "The take-up share is a pure assumption with no empirical basis in this project.",
        ],
    )

    # -- 3. conditional assumability ----------------------------------------
    share = cfg.simulation.assumability_share
    gap1 = gap0 * (1.0 - share)
    h1 = _predict_hazard(stock, coefs, cfg, gap_override=gap1)
    res = _aggregate(stock, h0, h1, cfg, "conditional_assumability")
    res["policy"] = {
        "type": "existing mortgages become assumable under conditions",
        "take_up_share": share,
        "implementation": "same functional form as portability but a lower take-up "
        "share, reflecting that an assumption requires a buyer who "
        "qualifies AND can fund the equity gap in cash or a second lien",
        "binding_constraint": "the buyer must cover the difference between the price "
        "and the assumed balance. In a market where prices have "
        "risen since origination, that gap is large -- which is "
        "precisely why assumability take-up would be low.",
    }
    emit(
        "scenario_conditional_assumability",
        res,
        [
            "The equity-gap funding constraint is the reason assumability take-up is "
            "likely small; the model represents it only through a lower take-up share.",
        ],
    )

    # -- 4. seller credit ---------------------------------------------------
    # Convert a lump-sum credit into an equivalent monthly payment reduction over the
    # calibrated holding period, then into a rate-gap equivalent.
    from lockin.amortization import annuity_factor

    ann = annuity_factor(
        np.minimum(np.full_like(rem, float(cfg.lockin.holding_period_months)), rem),
        cfg.lockin.discount_rate_pct,
    )
    monthly_equiv = cfg.simulation.seller_credit_dollars / np.maximum(ann, 1.0)
    base_pgap = _pmt(bal, mkt, rem) - _pmt(bal, note, rem)
    target_pgap = base_pgap - monthly_equiv
    # Invert numerically: find the rate that produces target_pgap.
    gap1 = gap0.copy()
    for _ in range(40):
        pg = _pmt(bal, note + gap1, rem) - _pmt(bal, note, rem)
        d = _pmt(bal, note + gap1 + 0.01, rem) - _pmt(bal, note + gap1, rem)
        with np.errstate(divide="ignore", invalid="ignore"):
            step = np.where(np.abs(d) > 1e-12, (pg - target_pgap) / (d / 0.01), 0.0)
        gap1 = gap1 - np.nan_to_num(step)
    h1 = _predict_hazard(stock, coefs, cfg, gap_override=gap1)
    res = _aggregate(stock, h0, h1, cfg, "temporary_seller_credit")
    res["policy"] = {
        "type": "temporary seller credit",
        "credit_dollars": cfg.simulation.seller_credit_dollars,
        "implementation": "the lump sum is annuitised over the calibrated holding "
        "period and converted to the rate-gap change that produces "
        "the same monthly payment relief",
        "mean_monthly_equivalent_dollars": float(np.nanmean(monthly_equiv)),
        "mean_gap_equivalent_pp": float(np.nanmean(gap1 - gap0)),
        "fiscal_or_private_cost_per_transaction": cfg.simulation.seller_credit_dollars,
    }
    emit(
        "scenario_seller_credit",
        res,
        [
            "A seller credit is paid by the seller, not the government, unless it is "
            "subsidised. If subsidised, cost per ADDITIONAL transaction is far above the "
            "headline credit because most recipients would have transacted anyway.",
        ],
    )

    # -- 5. rate buydown ----------------------------------------------------
    bd = cfg.simulation.buydown_bp / 100.0
    gap1 = gap0 - bd
    h1 = _predict_hazard(stock, coefs, cfg, gap_override=gap1)
    res = _aggregate(stock, h0, h1, cfg, "mortgage_rate_buydown")
    # Fiscal cost: PV of the buydown on the average new loan balance.
    pv_cost_per_loan = float(
        np.nanmean(
            (_pmt(bal, mkt, rem) - _pmt(bal, mkt - bd, rem))
            * annuity_factor(
                np.minimum(np.full_like(rem, float(cfg.lockin.holding_period_months)), rem),
                cfg.lockin.discount_rate_pct,
            )
        )
    )
    res["policy"] = {
        "type": "mortgage-rate buydown",
        "buydown_bp": cfg.simulation.buydown_bp,
        "implementation": "the effective market rate faced by a mover falls by the "
        "buydown, reducing the rate gap one-for-one",
        "modelled_pv_fiscal_cost_per_assisted_loan": pv_cost_per_loan,
        "cost_note": "This is the cost per ASSISTED loan. Cost per ADDITIONAL "
        "transaction is much higher, because the great majority of "
        "assisted borrowers would have transacted regardless.",
    }
    if res["modelled_additional_monthly_prepayments"] > 0:
        res["policy"]["modelled_cost_per_additional_prepayment"] = (
            pv_cost_per_loan
            * res["scenario_expected_monthly_prepayments"]
            / res["modelled_additional_monthly_prepayments"]
        )
    emit(
        "scenario_rate_buydown",
        res,
        [
            "Cost per additional transaction is the policy-relevant number and it is "
            "dominated by inframarginal recipients.",
            "A demand-side subsidy in an inelastic-supply market is partly capitalised "
            "into prices; that capitalisation is not modelled.",
        ],
    )

    # -- 6. elevated construction responsiveness ----------------------------
    # This scenario does not change the hazard; it changes the mapping from a demand
    # shift into prices vs quantities.
    res = _aggregate(stock, h0, h0, cfg, "elevated_supply_elasticity")
    mult = cfg.simulation.supply_elasticity_multiplier
    eps_s0, eps_d = cfg.simulation.supply_elasticity, cfg.simulation.price_elasticity_of_demand
    eps_s1 = eps_s0 * mult
    res["policy"] = {
        "type": "increased residential construction responsiveness",
        "supply_elasticity_baseline": eps_s0,
        "supply_elasticity_scenario": eps_s1,
        "implementation": "the borrower-level hazard is UNCHANGED; only the mapping "
        "from a transaction-flow shift into prices versus quantities "
        "changes",
        "price_pass_through_baseline": 1.0 / (eps_s0 - eps_d),
        "price_pass_through_scenario": 1.0 / (eps_s1 - eps_d),
        "interpretation": "a more elastic supply side converts a given demand shift "
        "into more units and less price, which is why supply policy "
        "and lock-in policy are complements rather than substitutes",
    }
    emit(
        "scenario_supply_elasticity",
        res,
        [
            "This scenario contains NO estimated behavioural response at all; it is pure "
            "calibration arithmetic on the elasticity mapping.",
        ],
    )

    # -- 7. targeted starter-home policy -----------------------------------
    # Apply the buydown only to loans in the bottom balance tercile.
    thresh = float(np.nanquantile(bal, 1 / 3))
    targeted = bal <= thresh
    gap1 = gap0 - bd * targeted
    h1 = _predict_hazard(stock, coefs, cfg, gap_override=gap1)
    res = _aggregate(stock, h0, h1, cfg, "targeted_starter_home_buydown")
    res["policy"] = {
        "type": "targeted policy for starter homes",
        "targeting_rule": "loans in the bottom tercile of current balance",
        "balance_threshold": thresh,
        "n_targeted": int(targeted.sum()),
        "buydown_bp": cfg.simulation.buydown_bp,
        "distributional_note": "targeting by loan balance is a proxy for house price, "
        "not for borrower income or wealth. Low balance can mean "
        "a modest home OR a large down payment.",
    }
    emit(
        "scenario_targeted_starter_homes",
        res,
        [
            "Balance-based targeting is a crude proxy for the intended beneficiary and "
            "will misclassify high-wealth borrowers with large down payments.",
        ],
    )

    # -- 8. no-lock-in counterfactual ---------------------------------------
    gap1 = np.zeros_like(gap0)
    h1 = _predict_hazard(stock, coefs, cfg, gap_override=gap1)
    res = _aggregate(stock, h0, h1, cfg, "no_lock_in")
    res["policy"] = {
        "type": "no-lock-in counterfactual (bounding exercise)",
        "implementation": "the rate gap is set to zero for every loan, i.e. every "
        "borrower could refinance the same balance at their own note "
        "rate. This is the upper bound of the modelled lock-in "
        "channel, not a policy.",
        "mean_gap_removed_pp": float(np.nanmean(gap0)),
    }
    emit(
        "scenario_no_lock_in",
        res,
        [
            "A BOUNDING exercise, not a policy. It removes the rate gap entirely, which no "
            "instrument can do.",
            "The bound is an extrapolation far outside the support of the estimation "
            "sample for many loans, so the linear-in-gap functional form is doing most of "
            "the work.",
        ],
    )

    # -- comparison table ---------------------------------------------------
    comparison = []
    for name, path in written.items():
        art = read_artifact(cfg, "scenarios", path.stem)
        r = art["result"]
        comparison.append(
            {
                "scenario": r.get("scenario"),
                "artifact": name,
                "policy_type": r.get("policy", {}).get("type"),
                "additional_monthly_prepayments": r.get("modelled_additional_monthly_prepayments"),
                "pct_change_in_prepayments": r.get("modelled_pct_change_in_prepayments"),
                "additional_monthly_prepaid_upb": r.get("modelled_additional_monthly_prepaid_upb"),
            }
        )
    comparison.sort(key=lambda x: abs(x["additional_monthly_prepayments"] or 0.0), reverse=True)
    written["scenario_comparison"] = write_artifact(
        cfg,
        ctx,
        group="scenarios",
        name="scenario_comparison",
        evidence_tier="simulation",
        population=POPULATION,
        geography=cfg.panel.geography,
        outcome_definition="modelled additional monthly prepayments, ranked",
        weight="loan count and UPB",
        result={
            "ranking": comparison,
            "baseline_month": as_of.isoformat(),
            "n_loans_in_baseline_stock": stock.height,
            "calibrated_inputs": calibrated,
            "estimated_inputs": hazard_meta,
            "how_to_read_this": (
                "The ORDERING is the useful output. Magnitudes inherit the sampling "
                "error of the hazard coefficients, the arbitrariness of the calibrated "
                "elasticities, and an unidentified prepayment-to-transaction share."
            ),
            "not_a_forecast": common["not_a_forecast"],
        },
        caveats=[
            "MODEL-DEPENDENT. Not a forecast.",
            "Scenarios are not mutually exclusive and their effects are not additive.",
        ],
    )
    return written
