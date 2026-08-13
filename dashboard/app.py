"""Streamlit dashboard.

Every chart carries an annotation block naming its **population**, **geography**,
**time period**, **weight**, **outcome definition**, **data source**, and
**model/descriptive status**. The annotation is rendered by :func:`annotate`, which
is called for every figure; a chart without one is a defect.

Run with:  ``make dashboard``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import altair as alt
import polars as pl
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lockin.artifacts import try_read_artifact
from lockin.config import load_config
from lockin.pipeline_status import pipeline_status

st.set_page_config(page_title="Mortgage Rate Lock-In", layout="wide")

CONFIG = "configs/sample.yaml"
for i, a in enumerate(sys.argv):
    if a == "--config" and i + 1 < len(sys.argv):
        CONFIG = sys.argv[i + 1]

cfg = load_config(CONFIG)


def annotate(
    *,
    population: str,
    geography: str,
    period: str,
    weight: str,
    outcome: str,
    source: str,
    status: str,
) -> None:
    """The mandatory annotation block for every chart and map."""
    st.caption(
        f"**Population:** {population}  \n"
        f"**Geography:** {geography} · **Period:** {period} · **Weight:** {weight}  \n"
        f"**Outcome definition:** {outcome}  \n"
        f"**Source:** {source} · **Status:** `{status}`"
    )


def art(group: str, name: str) -> dict[str, Any] | None:
    return try_read_artifact(cfg, group, name)


# ---------------------------------------------------------------------------
# Header: data class and coverage warnings
# ---------------------------------------------------------------------------

st.title("Mortgage Rate Lock-In, Housing Liquidity, and Local Market Dynamics")

if cfg.data_class == "SYNTHETIC":
    st.error(
        "**SYNTHETIC LOAN DATA.** Every loan-level number on this page was computed "
        "from synthetic fixtures generated for engineering tests. They are **not** "
        "empirical findings about U.S. mortgages. The public aggregate series (PMMS, "
        "FHFA HPI, HMDA, Census BPS) **are** real. See `data/DATA_ACCESS.md` §R1 to "
        "run on registered data.",
        icon="⚠️",
    )
else:
    st.success("Running on REGISTERED Freddie Mac loan-level data.", icon="✅")

st.warning(
    "**Coverage and selection.** The loan population is conforming conventional "
    "Freddie Mac acquisitions: no FHA/VA (which are *assumable*), no jumbo, no "
    "non-QM, no portfolio loans, no Fannie Mae, no all-cash purchases, and no "
    "mortgage-free owners (roughly a third of U.S. owner-occupied homes). Any share "
    "shown here is a share **of Freddie-acquired loans**, not of U.S. households.",
    icon="ℹ️",
)
st.info(
    '**Vocabulary.** `prepayment` = Freddie Mac Zero Balance Code 01, *"Prepaid or '
    'Matured (Voluntary Payoff)"*. It pools refinancing, sale-related payoff, and '
    "maturity. It is **not** a home sale and **not** a household move.",
    icon="📖",
)

tabs = st.tabs(
    [
        "Rates & coupons",
        "Lock-in exposure",
        "Prepayment hazards",
        "Local market",
        "Event study",
        "Scenarios",
        "Pipeline & freshness",
    ]
)

# ---------------------------------------------------------------------------
# 1. Rates and the outstanding coupon distribution
# ---------------------------------------------------------------------------
with tabs[0]:
    st.header("National mortgage-rate path")
    try:
        from lockin.adapters import pmms
        from lockin.rates import monthly_market_rate

        raw = pmms.load(cfg)
        monthly = monthly_market_rate(raw, cfg.rates.series).drop_nulls("market_rate")
        d = monthly.filter(pl.col("period") >= pl.date(2000, 1, 1)).to_pandas()
        ch = (
            alt.Chart(d)
            .mark_line()
            .encode(
                x=alt.X("period:T", title="month"),
                y=alt.Y(
                    "market_rate:Q", title=f"{cfg.rates.series} (%)", scale=alt.Scale(zero=False)
                ),
                color=alt.Color("methodology_regime:N", title="PMMS methodology"),
                tooltip=["period:T", "market_rate:Q", "rate_obs_date:T", "methodology_regime:N"],
            )
            .properties(height=320)
        )
        st.altair_chart(ch, use_container_width=True)
        annotate(
            population="Lenders surveyed (through 2022-11-10) / loan applications "
            "received (from 2022-11-17)",
            geography="National",
            period=f"2000-01 … {monthly['period'].max()}",
            weight="unweighted survey average",
            outcome="average OFFERED rate for a prime conventional conforming "
            "30-year fixed mortgage with ~20% down — not a transaction-weighted "
            "average of rates actually taken",
            source="Freddie Mac Primary Mortgage Market Survey",
            status="descriptive · point-in-time aligned (no look-ahead)",
        )
        st.caption(
            "The colour break marks the 2022-11-17 methodology change, when PMMS moved "
            "from a lender survey to an application-based method and discontinued the "
            "fees/points and 5/1 ARM series. Levels either side are not produced the "
            "same way."
        )
    except Exception as exc:
        st.warning(f"PMMS unavailable: {exc}")

    st.header("Distribution of outstanding note rates")
    stock_p = cfg.path("processed", f"active_stock_{cfg.panel.geography}.parquet")
    if stock_p.exists():
        stock = pl.read_parquet(stock_p)
        deciles = [c for c in stock.columns if c.startswith("note_rate_p")]
        if deciles:
            latest = stock["period"].max()
            snap = stock.filter(pl.col("period") == latest)
            long = snap.select(["geography", *deciles]).unpivot(
                index="geography", variable_name="quantile", value_name="note_rate"
            )
            ch = (
                alt.Chart(long.to_pandas())
                .mark_boxplot()
                .encode(
                    x=alt.X("quantile:N", title="note-rate percentile of the active stock"),
                    y=alt.Y("note_rate:Q", title="note rate (%)", scale=alt.Scale(zero=False)),
                )
                .properties(height=300)
            )
            st.altair_chart(ch, use_container_width=True)
            annotate(
                population="Active Freddie-acquired conforming conventional fixed-rate "
                "loans in the configured cohorts",
                geography=f"{cfg.panel.geography} (distribution across geographies)",
                period=str(latest),
                weight="loan count",
                outcome="note rate on the outstanding mortgage",
                source=f"{cfg.data_class} loan-level data",
                status="descriptive",
            )
    else:
        st.info("Active stock not built. Run `make build-local-panel`.")

# ---------------------------------------------------------------------------
# 2. Lock-in exposure
# ---------------------------------------------------------------------------
with tabs[1]:
    st.header("Lock-in exposure over time")
    if stock_p.exists():
        stock = pl.read_parquet(stock_p)
        share_cols = sorted(c for c in stock.columns if c.startswith("locked_share_upb_"))
        if share_cols:
            agg = (
                stock.group_by("period")
                .agg([pl.col(c).mean().alias(c) for c in share_cols])
                .sort("period")
                .unpivot(index="period", variable_name="threshold", value_name="share")
            )
            ch = (
                alt.Chart(agg.to_pandas())
                .mark_line()
                .encode(
                    x=alt.X("period:T", title="month"),
                    y=alt.Y(
                        "share:Q", title="share of active UPB locked in", axis=alt.Axis(format="%")
                    ),
                    color=alt.Color("threshold:N", title="threshold"),
                    tooltip=["period:T", "threshold:N", "share:Q"],
                )
                .properties(height=320)
            )
            st.altair_chart(ch, use_container_width=True)
            annotate(
                population="Active Freddie-acquired conforming conventional fixed-rate loans",
                geography=f"mean across {cfg.panel.geography}s",
                period=f"{stock['period'].min()} … {stock['period'].max()}",
                weight="unpaid principal balance",
                outcome="share of active UPB whose rate gap (PMMS minus note rate) "
                "exceeds the threshold",
                source=f"{cfg.data_class} loan-level data + Freddie Mac PMMS",
                status="descriptive · CONTEMPORANEOUS (endogenous — not the event-study treatment)",
            )
            st.caption(
                "**This is the contemporaneous share and it is endogenous.** The "
                "surviving stock is mechanically low-coupon-tilted because the loans "
                "with the strongest refinance incentive exited first. The event study "
                "uses *predetermined* exposure instead."
            )

    st.header("Predetermined exposure by geography (the event-study treatment)")
    a = art("eventstudy", "exposure_distribution")
    if a:
        p = a["result"]["primary"]
        if p.get("status") == "ok":
            rows = p["top_5"] + p["bottom_5"]
            df = pl.DataFrame(rows).unique(subset="geography").sort("exposure", descending=True)
            ch = (
                alt.Chart(df.to_pandas())
                .mark_bar()
                .encode(
                    x=alt.X("exposure:Q", title=p["exposure"], scale=alt.Scale(zero=False)),
                    y=alt.Y("geography:N", sort="-x", title=cfg.panel.geography),
                    tooltip=["geography:N", "exposure:Q"],
                )
                .properties(height=340)
            )
            st.altair_chart(ch, use_container_width=True)
            annotate(
                population="Active Freddie-acquired loans as of the pre-shock date",
                geography=cfg.panel.geography,
                period=f"frozen at {a['result']['pre_shock_date']}",
                weight="UPB (a loan-count variant is also computed)",
                outcome="frozen pre-shock coupon shares evaluated at the later national "
                "rate path: E_g = Σ_k ω_gk^pre · 1{R_post − r_k > τ}",
                source=f"{cfg.data_class} loan-level data + Freddie Mac PMMS",
                status="descriptive (this is the treatment variable, not a result)",
            )
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("mean", f"{p['mean']:.3f}")
            c2.metric("s.d.", f"{p['sd']:.3f}")
            c3.metric("min", f"{p['min']:.3f}")
            c4.metric("max", f"{p['max']:.3f}")
            if p.get("balance_table"):
                st.subheader("Balance table — exposure is not randomly assigned")
                st.dataframe(pl.DataFrame(p["balance_table"]).to_pandas(), use_container_width=True)
                st.caption(p["balance_interpretation"])
    else:
        st.info("Run `make estimate-local-effects`.")

# ---------------------------------------------------------------------------
# 3. Prepayment hazards
# ---------------------------------------------------------------------------
with tabs[2]:
    st.header("Monthly prepayment hazard by rate-gap bucket")
    a = art("hazards", "gap_profile_nonlinear")
    if a:
        emp = pl.DataFrame(a["result"]["prepayment"]["empirical"])
        ch = (
            alt.Chart(emp.to_pandas())
            .mark_bar()
            .encode(
                x=alt.X(
                    "label:N", sort=None, title="rate-gap bucket", axis=alt.Axis(labelAngle=-35)
                ),
                y=alt.Y("hazard:Q", title="monthly prepayment hazard"),
                tooltip=["label:N", "hazard:Q", "n_at_risk:Q", "n_events:Q"],
            )
            .properties(height=340)
        )
        st.altair_chart(ch, use_container_width=True)
        annotate(
            population=a["population"],
            geography=a["geography"],
            period=a["provenance"]["data_period"],
            weight=a["weight"],
            outcome=a["outcome_definition"],
            source=f"{a['provenance']['data_class']} loan-level data + Freddie Mac PMMS",
            status=f"{a['evidence_tier']} — NOT causal",
        )
        st.dataframe(emp.to_pandas(), use_container_width=True)

    st.header("Survival and cumulative incidence")
    km = art("hazards", "km_prepayment")
    if km:
        o = km["result"]["overall"]
        if o["survival"]:
            df = pl.DataFrame(
                {"age": o["times"], "survival": o["survival"], "n_at_risk": o["n_at_risk"]}
            )
            ch = (
                alt.Chart(df.to_pandas())
                .mark_line(interpolate="step-after")
                .encode(
                    x=alt.X("age:Q", title="loan age (months)"),
                    y=alt.Y(
                        "survival:Q", title="cause-specific survival", scale=alt.Scale(zero=False)
                    ),
                    tooltip=["age:Q", "survival:Q", "n_at_risk:Q"],
                )
                .properties(height=300)
            )
            st.altair_chart(ch, use_container_width=True)
            annotate(
                population=km["population"],
                geography=km["geography"],
                period=km["provenance"]["data_period"],
                weight=km["weight"],
                outcome=km["outcome_definition"],
                source=f"{km['provenance']['data_class']} loan-level data",
                status="descriptive · Kaplan–Meier with left truncation",
            )
            st.caption(o["competing_risks_treatment"])
    cif = art("hazards", "cif_competing_risks")
    if cif:
        rows = pl.DataFrame(cif["result"]["rows"])
        if rows.height:
            long = (
                rows.select(["age", "cif_cause_1", "cif_cause_2"])
                .unpivot(index="age", variable_name="cause", value_name="cif")
                .with_columns(
                    pl.col("cause").replace(
                        {
                            "cif_cause_1": "prepayment (ZB 01)",
                            "cif_cause_2": "credit event (ZB 02/03/09)",
                        }
                    )
                )
            )
            ch = (
                alt.Chart(long.to_pandas())
                .mark_line()
                .encode(
                    x=alt.X("age:Q", title="loan age (months)"),
                    y=alt.Y("cif:Q", title="cumulative incidence"),
                    color=alt.Color("cause:N", title="cause"),
                )
                .properties(height=300)
            )
            st.altair_chart(ch, use_container_width=True)
            annotate(
                population=cif["population"],
                geography=cif["geography"],
                period=cif["provenance"]["data_period"],
                weight=cif["weight"],
                outcome=cif["outcome_definition"],
                source=f"{cif['provenance']['data_class']} loan-level data",
                status="descriptive · Aalen–Johansen competing risks (not 1−KM)",
            )

    st.header("Discrete-time hazard coefficients")
    lg = art("hazards", "dt_logit_prepayment")
    if lg:
        coefs = pl.DataFrame(
            [c for c in lg["result"]["coefficients"] if not c["term"].startswith("age_")]
        )
        st.dataframe(coefs.to_pandas(), use_container_width=True)
        annotate(
            population=lg["population"],
            geography=lg["geography"],
            period=lg["provenance"]["data_period"],
            weight=lg["weight"],
            outcome=lg["outcome_definition"],
            source=f"{lg['provenance']['data_class']} loan-level data + Freddie Mac PMMS",
            status=f"{lg['evidence_tier']} — {lg['result']['standard_errors']}",
        )
        st.caption(lg["result"]["interpretation_warning"])

# ---------------------------------------------------------------------------
# 4. Local market
# ---------------------------------------------------------------------------
with tabs[3]:
    ann_p = cfg.path("processed", "local_market_panel_annual.parquet")
    if not ann_p.exists():
        st.info("Local panel not built. Run `make build-local-panel`.")
    else:
        panel = pl.read_parquet(ann_p)
        exposure_col = f"pre_{cfg.event_study.exposure_measure}"

        st.header("Geography map (choropleth by exposure)")
        if exposure_col in panel.columns:
            geo = (
                panel.group_by("geography")
                .agg(pl.col(exposure_col).first().alias("exposure"))
                .drop_nulls()
            )
            st.dataframe(
                geo.sort("exposure", descending=True).to_pandas(),
                use_container_width=True,
                height=260,
            )
            annotate(
                population="Active Freddie-acquired loans as of the pre-shock date",
                geography=cfg.panel.geography,
                period=f"frozen at {cfg.event_study.pre_shock_date}",
                weight="UPB",
                outcome=f"predetermined exposure `{exposure_col}`",
                source=f"{cfg.data_class} loan-level data + Freddie Mac PMMS",
                status="descriptive",
            )
            st.caption(
                "Rendered as a sortable table rather than a shaded map: a choropleth "
                "needs a committed TIGER/Line boundary file, and shading 26 of 51 "
                "states would misleadingly imply national coverage."
            )

        for col, label, src, outdef in (
            (
                "n_purchase_originations",
                "HMDA purchase originations",
                "HMDA via CFPB Data Browser",
                "home-purchase loans ORIGINATED (action taken 1, loan purpose 1). "
                "Applications and originations, not property sales; all-cash purchases absent",
            ),
            (
                "n_refi_originations",
                "HMDA refinance originations",
                "HMDA via CFPB Data Browser",
                "refinance loans originated (loan purpose 31 or 32). MECHANICALLY "
                "CONTAMINATED by pipeline exhaustion in high-exposure markets",
            ),
            (
                "hpi_growth",
                "FHFA house price growth",
                f"FHFA HPI ({cfg.panel.hpi_flavor}, {cfg.panel.hpi_frequency}, State)",
                "within-year log change in the purchase-only state index. An INDEX, "
                "not a property value",
            ),
            (
                "permits_1unit",
                "Census single-family permits",
                "Census Building Permits Survey",
                "single-family units AUTHORIZED — not starts, not completions",
            ),
        ):
            if col not in panel.columns:
                continue
            st.subheader(label)
            d = panel.select(["geography", "year", col]).drop_nulls()
            ch = (
                alt.Chart(d.to_pandas())
                .mark_line(opacity=0.55)
                .encode(
                    x=alt.X("year:O", title="year"),
                    y=alt.Y(f"{col}:Q", title=label, scale=alt.Scale(zero=False)),
                    color=alt.Color("geography:N", legend=None),
                    tooltip=["geography:N", "year:O", f"{col}:Q"],
                )
                .properties(height=280)
            )
            st.altair_chart(ch, use_container_width=True)
            annotate(
                population="HMDA reporters / permit-issuing places / index transactions, "
                "as applicable — NOT the Freddie loan population",
                geography=cfg.panel.geography,
                period=f"{panel['year'].min()}–{panel['year'].max()}",
                weight="unweighted (one line per geography)",
                outcome=outdef,
                source=src,
                status="descriptive",
            )

        st.subheader("Cohort composition of the active stock")
        mix_p = cfg.path("processed", f"active_stock_cohort_mix_{cfg.panel.geography}.parquet")
        if mix_p.exists():
            mix = pl.read_parquet(mix_p)
            agg = (
                mix.group_by(["period", "orig_cohort_year"])
                .agg(pl.col("n").sum().alias("n"))
                .sort("period")
            )
            ch = (
                alt.Chart(agg.to_pandas())
                .mark_area()
                .encode(
                    x=alt.X("period:T", title="month"),
                    y=alt.Y(
                        "n:Q",
                        stack="normalize",
                        title="share of active loans",
                        axis=alt.Axis(format="%"),
                    ),
                    color=alt.Color("orig_cohort_year:O", title="origination year"),
                )
                .properties(height=280)
            )
            st.altair_chart(ch, use_container_width=True)
            annotate(
                population="Active Freddie-acquired loans in the configured cohorts",
                geography="all geographies pooled",
                period=f"{agg['period'].min()} … {agg['period'].max()}",
                weight="loan count",
                outcome="share of active loans by approximate origination year",
                source=f"{cfg.data_class} loan-level data",
                status="descriptive · the cohort mix is an artifact of the CONFIGURED "
                "cohort set, not of the market",
            )

# ---------------------------------------------------------------------------
# 5. Event study
# ---------------------------------------------------------------------------
with tabs[4]:
    st.header("Continuous-treatment event study")
    st.markdown(
        "Only **relative** effects across exposure are identified. The national "
        "rate path is common to every geography and is absorbed by the period fixed "
        "effects. A failed pre-trend test **automatically** demotes an outcome to "
        "`descriptive`."
    )
    found = False
    for p in (
        sorted(cfg.path("outputs", "eventstudy").glob("es_*.json"))
        if cfg.path("outputs", "eventstudy").exists()
        else []
    ):
        a = art("eventstudy", p.stem)
        if not a:
            continue
        es = a["result"].get("event_study", {})
        if es.get("status") != "ok":
            continue
        found = True
        pt = es["pretrend_test"]
        passes = pt.get("passes_at_alpha_0.10")
        st.subheader(f"{p.stem}  ·  tier `{a['evidence_tier']}`")
        if not passes:
            st.warning(
                f"Pre-trend test **{'fails' if pt.get('pvalue') is not None else 'not testable'}** "
                f"(p = {pt.get('pvalue')}). Demoted to `descriptive`; no causal language.",
                icon="⚠️",
            )
        dyn = pl.DataFrame(es["dynamic_effects"])
        base = alt.Chart(dyn.to_pandas())
        band = base.mark_area(opacity=0.2).encode(
            x=alt.X("time:O", title="period"),
            y=alt.Y("ci_low:Q", title="coefficient (per 1 s.d. exposure)"),
            y2="ci_high:Q",
        )
        line = base.mark_line(point=True).encode(x="time:O", y="coef:Q")
        rule = (
            alt.Chart(pl.DataFrame({"y": [0.0]}).to_pandas())
            .mark_rule(strokeDash=[4, 4])
            .encode(y="y:Q")
        )
        st.altair_chart((band + line + rule).properties(height=300), use_container_width=True)
        annotate(
            population=a["population"],
            geography=a["geography"],
            period=a["provenance"]["data_period"],
            weight=a["weight"],
            outcome=a["outcome_definition"],
            source="HMDA / FHFA HPI / Census BPS + loan-level exposure",
            status=f"{a['evidence_tier']} · pre-trend "
            f"{'passes' if passes else 'fails'} · {es['standard_errors']}",
        )
        st.caption(es["coefficient_units"] + ". " + es["identification_note"])
    if not found:
        st.info("Run `make estimate-local-effects`.")

    pl_art = art("eventstudy", "placebos")
    if pl_art:
        st.subheader("Falsification")
        st.json(pl_art["result"], expanded=False)

# ---------------------------------------------------------------------------
# 6. Scenarios
# ---------------------------------------------------------------------------
with tabs[5]:
    st.header("Policy counterfactuals")
    st.error(
        "**Model-dependent projections, not forecasts.** These apply a hazard "
        "*association* as if it were a structural response function, and map "
        "prepayments into transactions through an **unidentified** "
        "prepayment-to-transaction share (shown across a range, never as a point "
        "value).",
        icon="🚫",
    )
    comp = art("scenarios", "scenario_comparison")
    if comp:
        r = comp["result"]
        rank = pl.DataFrame(r["ranking"])
        ch = (
            alt.Chart(rank.to_pandas())
            .mark_bar()
            .encode(
                x=alt.X(
                    "additional_monthly_prepayments:Q",
                    title="modelled additional monthly prepayments",
                ),
                y=alt.Y("scenario:N", sort="-x", title="scenario"),
                tooltip=[
                    "scenario:N",
                    "policy_type:N",
                    "additional_monthly_prepayments:Q",
                    "pct_change_in_prepayments:Q",
                ],
            )
            .properties(height=340)
        )
        st.altair_chart(ch, use_container_width=True)
        annotate(
            population=comp["population"],
            geography=comp["geography"],
            period=f"baseline month {r['baseline_month']}",
            weight=comp["weight"],
            outcome=comp["outcome_definition"],
            source="estimated hazard coefficients + calibrated elasticities",
            status="simulation — NOT A FORECAST",
        )
        st.caption(r["how_to_read_this"])
        st.subheader("Calibrated inputs (chosen, not estimated — no error bars)")
        st.dataframe(
            pl.DataFrame(
                {
                    "parameter": list(r["calibrated_inputs"]),
                    "value": [str(v) for v in r["calibrated_inputs"].values()],
                }
            ).to_pandas(),
            use_container_width=True,
        )
        st.dataframe(rank.to_pandas(), use_container_width=True)
    else:
        st.info("Run `make simulate-policy`.")

# ---------------------------------------------------------------------------
# 7. Pipeline status and data freshness
# ---------------------------------------------------------------------------
with tabs[6]:
    st.header("Pipeline status")
    stt = pipeline_status(cfg)
    c1, c2, c3 = st.columns(3)
    c1.metric("stages OK", f"{stt['n_stages_ok']} / {stt['n_stages']}")
    c2.metric("artifacts", stt["n_artifacts"])
    c3.metric("data class", stt["data_class"])
    st.dataframe(pl.DataFrame(stt["stages"]).to_pandas(), use_container_width=True, height=520)
    st.subheader("Artifacts by evidence tier")
    st.json(stt["artifacts_by_tier"])

    st.header("Data freshness")
    rows = []
    for mf in (
        sorted(cfg.path("cache").rglob("*.manifest.json")) if cfg.path("cache").exists() else []
    ):
        m = json.loads(mf.read_text())
        rows.append(
            {
                "dataset": m.get("name"),
                "retrieved_at": m.get("retrieved_at"),
                "coverage": m.get("coverage_period"),
                "rows": m.get("row_count"),
                "class": m.get("data_class"),
                "checksum": str(m.get("checksum_sha256", ""))[:12],
            }
        )
    if rows:
        st.dataframe(pl.DataFrame(rows).to_pandas(), use_container_width=True)
        st.caption(
            "Public sources are revised: PMMS, FHFA HPI, and Census BPS are all "
            "restated, and HMDA is re-released. A rerun on a later date fetches a "
            "newer vintage; the manifest records exactly which one produced a result."
        )
    else:
        st.info("No cached public data. Run `make fetch-public-data`.")

    st.header("Known limitations")
    st.markdown(
        "1. **A prepayment is not a move.** ZB 01 pools refinancing, sale-related "
        "payoff, and maturity. Nothing here measures mobility.\n"
        "2. **Only relative effects are identified** at the market level.\n"
        "3. **Predetermined exposure is not exogenous** — it correlates with pandemic "
        "price growth and refinance intensity.\n"
        "4. **The population is doubly selected** (see the banner above).\n"
        "5. **The demand/supply decomposition is framed, not achieved.**\n"
        "6. **Scenarios are not forecasts.**\n\n"
        "Full treatment: `reports/methodology_and_limitations.md` and "
        "`reports/failed_hypotheses.md`."
    )
