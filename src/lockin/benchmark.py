"""Replication benchmark against published mortgage lock-in research.

The benchmarks below are recorded from the published literature. For each one we
record the target estimand, the original data and identification, what *we* can do,
the differences in population and outcome definition, and whether our comparison is
**exact**, **approximate**, or **conceptual**.

No benchmark here is labeled ``exact``. The leading lock-in papers use data this
project does not have -- proprietary linked mortgage-and-property records
(e.g. servicer panels matched to deeds), the full Enterprise loan universe, or
credit-bureau panels with address changes. A project with public aggregates plus one
GSE's loan-level file cannot reproduce those estimands exactly, and claiming
otherwise would be the exact failure mode the operating instructions warn about.

**Numeric benchmark values are quoted from published work as reference points.**
Where a figure is stated below, it is a recollection of the published magnitude and
should be verified against the cited source before being used in any external
document; `verification_status` records this explicitly for each entry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lockin.artifacts import try_read_artifact, write_artifact
from lockin.config import Config
from lockin.provenance import collect_source_versions, run_context

BENCHMARKS: list[dict[str, Any]] = [
    {
        "id": "fhfa_lockin_transactions",
        "citation": "Federal Housing Finance Agency working-paper line of research on "
        "mortgage rate lock-in and housing-market transactions (Batzer, "
        "Coste, Doerner, Seiler and related FHFA working papers).",
        "target_estimand": "The reduction in the probability that a mortgaged owner "
        "sells, per unit of rate lock-in, and the implied aggregate "
        "reduction in home sales.",
        "original_data": "Enterprise (Fannie Mae and Freddie Mac) loan-level records "
        "linked to property transactions, with the ability to observe "
        "a SALE rather than only a payoff.",
        "original_identification": "Variation in the individual gap between the "
        "borrower's note rate and the prevailing market "
        "rate, with borrower and market controls.",
        "our_available_data": "One GSE's public loan-level file (or synthetic fixtures), "
        "PMMS, FHFA HPI, HMDA aggregates, Census BPS.",
        "population_differences": "We observe only Freddie Mac acquisitions, not both "
        "Enterprises, and no FHA/VA, jumbo, non-QM, "
        "portfolio, or all-cash segment.",
        "outcome_definition_differences": "DECISIVE. The original can identify a sale. "
        "We can only identify PREPAYMENT (ZB 01), "
        "which pools refinance payoff, sale-related "
        "payoff, and maturity. Our loan-level "
        "coefficient therefore answers a different "
        "question.",
        "published_magnitude_reference": "This literature reports a large negative "
        "effect of lock-in on mortgaged-owner sale "
        "probability and an aggregate shortfall of "
        "home sales in the millions over 2022-2023.",
        "comparison_type": "conceptual",
        "why_not_exact": "The original outcome is a property sale observed in linked "
        "records. We have no property linkage and no sale indicator. "
        "Any numeric comparison would be comparing a prepayment "
        "hazard to a sale hazard.",
        "verification_status": "magnitude quoted from memory of the published work -- "
        "VERIFY against the source before external use",
        "our_comparable_quantity": ("hazards/dt_logit_prepayment", "rate_gap"),
    },
    {
        "id": "fed_lockin_mobility",
        "citation": "Federal Reserve Board / Federal Reserve Bank research on mortgage "
        "lock-in and household mobility (e.g. Fonseca and Liu; "
        "Fonseca, Liu and Mabille lines of work).",
        "target_estimand": "The causal effect of mortgage lock-in on household MOVING "
        "rates, and its general-equilibrium consequences.",
        "original_data": "Credit-bureau panels with address changes, or "
        "survey/administrative mobility measures, linked to mortgage "
        "characteristics.",
        "original_identification": "Within-borrower variation in the rate gap, plus "
        "structural models of housing and mortgage choice.",
        "our_available_data": "No mobility measure whatsoever at the individual level.",
        "population_differences": "Credit-bureau panels cover all mortgage types and "
        "renters; we cover conforming conventional GSE loans.",
        "outcome_definition_differences": "DECISIVE. Mobility is a change of residence. "
        "We do not observe residence. We refuse to "
        "proxy it with prepayment.",
        "published_magnitude_reference": "This literature finds economically meaningful "
        "reductions in mobility attributable to "
        "lock-in, with effects concentrated among "
        "borrowers holding the largest rate gaps.",
        "comparison_type": "conceptual",
        "why_not_exact": "We have no mobility outcome. The only honest comparison is of "
        "the SIGN and the qualitative gradient in the rate gap.",
        "verification_status": "magnitude quoted from memory of the published work -- "
        "VERIFY against the source before external use",
        "our_comparable_quantity": ("hazards/gap_profile_nonlinear", "gradient in gap buckets"),
    },
    {
        "id": "prepayment_burnout_literature",
        "citation": "The mortgage-prepayment modelling literature (Schwartz-Torous "
        "onward, and industry prepayment models).",
        "target_estimand": "The response of the monthly prepayment hazard to the "
        "refinance incentive, plus seasoning, burnout, and "
        "turnover components.",
        "original_data": "Pool-level or loan-level agency prepayment data -- the SAME "
        "kind of data as ours.",
        "original_identification": "Predictive, not causal. This literature is explicit "
        "that it models an association for pricing purposes.",
        "our_available_data": "Directly comparable: the same outcome (prepayment) on "
        "the same kind of population.",
        "population_differences": "Cohort and vintage coverage differ with our config.",
        "outcome_definition_differences": "NONE material -- both use agency prepayment.",
        "published_magnitude_reference": "Prepayment speeds rise steeply and non-linearly "
        "once the refinance incentive exceeds roughly "
        "50-100 bp, with a seasoning ramp over the "
        "first 2-3 years and burnout thereafter.",
        "comparison_type": "approximate",
        "why_not_exact": "Our cohort set, performance window, and covariate set are "
        "ours, not theirs. But the estimand is genuinely the same, so "
        "the SHAPE of the incentive response and the seasoning ramp "
        "are legitimately comparable.",
        "verification_status": "qualitative shape only; no specific published number is "
        "being matched",
        "our_comparable_quantity": ("hazards/gap_profile_nonlinear", "empirical hazard by bucket"),
    },
    {
        "id": "hmda_purchase_activity",
        "citation": "Descriptive HMDA-based accounts of the 2022-2023 collapse in "
        "purchase and refinance origination volumes (CFPB HMDA data point "
        "reports and related FHFA/Fed commentary).",
        "target_estimand": "The change in purchase and refinance origination counts and "
        "volumes as rates rose.",
        "original_data": "The full HMDA LAR.",
        "original_identification": "Descriptive. No causal claim.",
        "our_available_data": "HMDA aggregations API -- the same underlying data, aggregated.",
        "population_differences": "None material at the state-year aggregate level.",
        "outcome_definition_differences": "None material.",
        "published_magnitude_reference": "Refinance originations fell by roughly an "
        "order of magnitude from the 2020-21 peak to "
        "2023; purchase originations fell far less, "
        "roughly by a third to a half.",
        "comparison_type": "approximate",
        "why_not_exact": "We aggregate a subset of state-years and cells rather than "
        "the full LAR, and reporting-threshold changes affect the "
        "2022+ counts.",
        "verification_status": "our own fetched HMDA aggregates can be checked directly "
        "against the published data point reports",
        "our_comparable_quantity": ("eventstudy/es_log_refi_originations", "descriptive levels"),
    },
]


def run_benchmark(cfg: Config) -> Path:
    """Attach our own comparable quantities to each benchmark and write the artifact."""
    ctx = run_context(cfg, source_versions=collect_source_versions(cfg))

    enriched: list[dict[str, Any]] = []
    for b in BENCHMARKS:
        entry = dict(b)
        group_name, what = b["our_comparable_quantity"]
        group, _, name = group_name.partition("/")
        art = try_read_artifact(cfg, group, name)
        if art is None:
            entry["our_estimate"] = {
                "status": "unavailable",
                "reason": f"artifact {group_name} has not been produced; run the "
                f"corresponding make target",
            }
        else:
            entry["our_estimate"] = _extract_our_estimate(art, what)
            entry["our_estimate"]["evidence_tier"] = art["evidence_tier"]
            entry["our_estimate"]["data_class"] = art["provenance"]["data_class"]
            if art["provenance"]["data_class"] == "SYNTHETIC":
                entry["our_estimate"]["comparison_blocked"] = (
                    "Our side of this comparison was computed from SYNTHETIC fixtures. "
                    "It recovers the parameters of our own data-generating process, so "
                    "it CANNOT be compared to a published empirical estimate. The "
                    "comparison becomes meaningful only after a registered-data run."
                )
                entry["comparison_type"] = "blocked_by_synthetic_data"
        enriched.append(entry)

    result = {
        "benchmarks": enriched,
        "comparison_type_definitions": {
            "exact": "same estimand, same data, same identification -- we claim this for NOTHING",
            "approximate": "same estimand and comparable data, different sample or specification",
            "conceptual": "different outcome definition or population; only sign and "
            "qualitative pattern are comparable",
            "blocked_by_synthetic_data": "our side of the comparison is not empirical",
        },
        "standing_rule": (
            "No benchmark in this project may be labeled 'exact'. The leading lock-in "
            "papers use proprietary linked mortgage-and-property records or "
            "credit-bureau mobility panels. We have neither."
        ),
        "verification_note": (
            "Published magnitudes recorded here are reference points recalled from the "
            "literature, not extracted programmatically from the sources. Each entry "
            "carries a verification_status field. Verify against the cited source "
            "before using any of these numbers in an external document."
        ),
    }
    return write_artifact(
        cfg,
        ctx,
        group="benchmark",
        name="benchmark_comparison",
        evidence_tier="descriptive",
        population="n/a (comparison of estimands across studies)",
        geography="varies by benchmark",
        outcome_definition="varies by benchmark -- the differences ARE the content",
        weight="n/a",
        result=result,
        caveats=[
            "Nothing here is an exact replication.",
            "Where our side is synthetic, the comparison is explicitly blocked rather "
            "than reported.",
        ],
    )


def _extract_our_estimate(art: dict[str, Any], what: str) -> dict[str, Any]:
    res = art["result"]
    if what == "rate_gap":
        for c in res.get("coefficients", []):
            if c["term"] == "rate_gap":
                return {
                    "quantity": "discrete-time logit coefficient on the point-in-time "
                    "rate gap, prepayment outcome",
                    "coef": c["coef"],
                    "std_err": c["std_err"],
                    "hazard_ratio_per_1pp": c["hazard_ratio"],
                    "average_marginal_effect_monthly": res.get(
                        "rate_gap_average_marginal_effect_monthly"
                    ),
                    "interpretation": "association between the rate gap and monthly "
                    "PREPAYMENT, not sale and not mobility",
                }
        return {"status": "rate_gap coefficient not found in the artifact"}
    if "gap" in what and "prepayment" in res:
        return {
            "quantity": "empirical monthly prepayment hazard by rate-gap bucket",
            "rows": res["prepayment"].get("empirical", []),
            "interpretation": "the SHAPE of this profile is what is comparable",
        }
    if "descriptive levels" in what:
        es = res.get("event_study", {})
        return {
            "quantity": "event-study dynamic path for log refinance originations",
            "dynamic_effects": es.get("dynamic_effects"),
            "status": es.get("status"),
        }
    return {"status": f"no extractor for {what!r}", "available_keys": sorted(res)[:20]}
