"""Active mortgage stock: geography-month summaries of the outstanding loan book.

Acceptance criterion 9. From the loan-month episode table, reconstruct for each
geography and month the set of loans that were **active** (observed and not yet
exited) and summarise it, under **both** weighting schemes:

* number of active loans, aggregate current UPB
* weighted-average note rate (count- and UPB-weighted)
* note-rate distribution (deciles)
* share locked in above 100 / 200 / 300 / 400 bp (count- and UPB-weighted)
* median and mean payment-equivalent lock-in cost
* refinance-incentive share
* realised prepayment rate and credit-event rate that month
* origination-cohort composition
* loan-purpose composition
* estimated current LTV

**Coverage caveat that must travel with every number here:** the stock is the stock
of *Freddie-acquired conforming conventional* loans that we observe, not the stock
of U.S. mortgages and certainly not the stock of U.S. homeowners (about a third of
owner-occupied homes carry no mortgage at all). Attrition over the window is
reported explicitly in ``coverage``.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from lockin.config import Config
from lockin.episodes import scan_episodes
from lockin.manifest import write_manifest

DECILES = (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def _threshold_exprs(thresholds_bp: list[int]) -> list[pl.Expr]:
    out: list[pl.Expr] = []
    for t in thresholds_bp:
        cut = t / 100.0
        locked = pl.col("rate_gap") > cut
        out.append(locked.mean().alias(f"locked_share_count_{t}"))
        out.append(
            (
                pl.when(locked).then(pl.col("current_upb")).otherwise(0.0).sum()
                / pl.col("current_upb").sum()
            ).alias(f"locked_share_upb_{t}")
        )
    return out


def geography_expr(cfg: Config) -> pl.Expr:
    """The column that defines a panel unit, as an expression.

    At state level this is simply ``property_state``. At MSA level it is the Freddie Mac
    MSA field **normalised to its parent CBSA**, because that field holds an MSA *or a
    Metropolitan Division* and 4.66 million of the 20.2 million real loans carry a
    division code. Grouping on the raw field would split the largest metros away from
    the Census permit and LAUS series, both of which report parent CBSAs, and those
    metros would vanish from the panel rather than error.
    """
    if cfg.panel.geography == "state":
        return pl.col("property_state")
    return pl.col("_cbsa_parent")


def with_geography(cfg: Config, lf: pl.LazyFrame) -> pl.LazyFrame:
    """Attach the parent-CBSA column when the panel geography needs it.

    Codes that resolve to no CBSA -- retired ones from delineations older than any
    vintage loaded -- stay null and are dropped by the caller's ``drop_nulls``. They are
    never guessed at: an unmappable code is 4.5% of loans here, and assigning them to a
    neighbouring metro would be fabrication.
    """
    if cfg.panel.geography == "state":
        return lf
    from lockin.adapters import omb_cbsa

    observed = lf.select(pl.col("msa_code").drop_nulls().unique()).collect()["msa_code"].to_list()
    mapping = omb_cbsa.to_parent_cbsa(cfg, [str(c) for c in observed]).select(
        pl.col("area_code").alias("msa_code"), pl.col("cbsa_code").alias("_cbsa_parent")
    )
    return lf.join(mapping.lazy(), on="msa_code", how="left")


#: Populated by :func:`build_active_stock` so the manifest can record how many panel
#: units the loan-count threshold removed, and what that threshold means once the
#: sampling fraction is undone.
_THRESHOLD_NOTE: dict[str, object] = {}


def stock_filename(cfg: Config) -> str:
    """Filename for the active stock, keyed by geography.

    A state run and an MSA run produce different tables. Sharing one filename let the
    second silently overwrite the first, and a later panel build would then join
    MSA-level exposure onto whatever geography happened to be on disk. The level is in
    the name so the two cannot be confused.
    """
    return f"active_stock_{cfg.panel.geography}.parquet"


def build_active_stock(cfg: Config) -> tuple[pl.DataFrame, Path]:
    """Aggregate the episode table into a geography-month active-stock table."""
    geo = "property_state" if cfg.panel.geography == "state" else "_cbsa_parent"
    lf = with_geography(cfg, scan_episodes(cfg))

    if cfg.panel.states:
        lf = lf.filter(pl.col("property_state").is_in(cfg.panel.states))

    thr = cfg.lockin.thresholds_bp
    refi_cut = cfg.lockin.refi_incentive_threshold_bp / 100.0

    agg = (
        lf.filter(pl.col(geo).is_not_null() & pl.col("market_rate").is_not_null())
        .group_by([pl.col(geo).alias("geography"), "period"])
        .agg(
            pl.len().alias("n_active_loans"),
            pl.col("current_upb").sum().alias("total_upb"),
            pl.col("note_rate").mean().alias("wavg_note_rate_count"),
            (
                (pl.col("note_rate") * pl.col("current_upb")).sum() / pl.col("current_upb").sum()
            ).alias("wavg_note_rate_upb"),
            pl.col("market_rate").first().alias("market_rate"),
            *_threshold_exprs(thr),
            pl.col("lockin_gap").mean().alias("mean_lockin_gap"),
            pl.col("rate_gap").mean().alias("mean_rate_gap"),
            pl.col("payment_gap").median().alias("median_payment_gap"),
            pl.col("payment_gap").mean().alias("mean_payment_gap"),
            pl.col("pv_financing_gap").median().alias("median_pv_financing_gap"),
            (pl.col("refi_incentive") > refi_cut).mean().alias("refi_incentive_share"),
            pl.col("exit_prepayment").mean().alias("prepayment_rate_monthly"),
            pl.col("exit_credit_event").mean().alias("credit_event_rate_monthly"),
            pl.col("exit_prepayment").sum().alias("n_prepayments"),
            pl.col("exit_credit_event").sum().alias("n_credit_events"),
            pl.col("est_current_ltv").median().alias("median_est_current_ltv"),
            pl.col("credit_score").mean().alias("mean_credit_score"),
            pl.col("orig_cohort_year").mean().alias("mean_orig_cohort_year"),
            (pl.col("loan_purpose") == "P").mean().alias("share_purchase_loans"),
            pl.col("loan_purpose").is_in(["C", "N", "R"]).mean().alias("share_refi_loans"),
            (pl.col("occupancy_status") == "P").mean().alias("share_primary_residence"),
            (pl.col("occupancy_status") == "I").mean().alias("share_investment"),
            *[pl.col("note_rate").quantile(q).alias(f"note_rate_p{int(q * 100)}") for q in DECILES],
        )
        .sort(["geography", "period"])
        .collect(engine="streaming")
    )

    # Cohort composition as a separate long table (wide would be sparse).
    cohort_mix = (
        lf.filter(pl.col(geo).is_not_null())
        .group_by([pl.col(geo).alias("geography"), "period", "orig_cohort_year"])
        .agg(pl.len().alias("n"), pl.col("current_upb").sum().alias("upb"))
        .sort(["geography", "period", "orig_cohort_year"])
        .collect(engine="streaming")
    )

    if cfg.panel.min_loans_per_geography > 0:
        # The threshold counts SAMPLED loans, so on a sampled run it is far stricter than
        # it reads. At loan_sample_fraction=0.05 a nominal 100 means roughly 2,000 real
        # loans, and that is what thins an MSA panel from ~395 metros to ~138. Stated
        # rather than left for someone to rediscover from a surprising geography count.
        frac = float(getattr(cfg.survival, "loan_sample_fraction", 1.0)) or 1.0
        effective = int(cfg.panel.min_loans_per_geography / max(frac, 1e-9))
        keep = (
            agg.group_by("geography")
            .agg(pl.col("n_active_loans").median().alias("med"))
            .filter(pl.col("med") >= cfg.panel.min_loans_per_geography)["geography"]
        )
        dropped = agg["geography"].n_unique() - len(keep)
        agg = agg.filter(pl.col("geography").is_in(keep))
        cohort_mix = cohort_mix.filter(pl.col("geography").is_in(keep))
        _THRESHOLD_NOTE.clear()
        _THRESHOLD_NOTE.update(
            {
                "min_loans_per_geography": cfg.panel.min_loans_per_geography,
                "loan_sample_fraction": frac,
                "effective_unsampled_equivalent": effective,
                "n_geographies_dropped": dropped,
                "n_geographies_kept": len(keep),
            }
        )

    out = cfg.path("processed", stock_filename(cfg))
    out.parent.mkdir(parents=True, exist_ok=True)
    agg.write_parquet(out)
    cohort_mix.write_parquet(
        cfg.path("processed", f"active_stock_cohort_mix_{cfg.panel.geography}.parquet")
    )

    cov = coverage_summary(agg)
    write_manifest(
        out,
        name="active_mortgage_stock",
        source="derived from loan_episodes (loan-level) and PMMS",
        source_url="n/a (derived)",
        license_terms="Aggregate; derived from restricted or synthetic loan data.",
        redistribution_status="aggregate -- redistributable if inputs permit",
        schema_version="active-stock-v1",
        row_count=agg.height,
        geographic_level=cfg.panel.geography,
        coverage_period=f"{agg['period'].min()}..{agg['period'].max()}",
        known_limitations=[
            "The stock of FREDDIE-ACQUIRED conforming conventional loans that we "
            "observe -- not the stock of U.S. mortgages, and not the stock of U.S. "
            "homeowners (roughly a third of owner-occupied homes have no mortgage).",
            "Only the configured origination cohorts are in the stock, so the "
            "cohort mix is an artifact of the configuration, not of the market.",
            "The surviving stock is mechanically low-coupon-tilted: loans with the "
            "strongest refinance incentive exited earliest. Contemporaneous "
            "exposure is therefore endogenous; use PREDETERMINED exposure for "
            "causal work.",
            "Attrition over the window is reported in extra.coverage.",
        ],
        data_class=cfg.manifest_data_class,
        extra={
            "coverage": cov,
            "thresholds_bp": thr,
            "weights": ["loan_count", "current_upb"],
            "geography": cfg.panel.geography,
            "loan_count_threshold": dict(_THRESHOLD_NOTE),
        },
    )
    return (agg, out)


def coverage_summary(stock: pl.DataFrame) -> dict[str, object]:
    """Attrition and coverage-change documentation for the active stock."""
    by_period = (
        stock.group_by("period")
        .agg(
            pl.col("n_active_loans").sum().alias("n_active"),
            pl.col("total_upb").sum().alias("total_upb"),
            pl.col("geography").n_unique().alias("n_geographies"),
        )
        .sort("period")
    )
    if by_period.height == 0:
        return {"note": "empty stock"}
    first = by_period.row(0, named=True)
    last = by_period.row(by_period.height - 1, named=True)
    peak_row = by_period.sort("n_active", descending=True).row(0, named=True)
    peak_n = int(peak_row["n_active"])
    return {
        "first_period": str(first["period"]),
        "last_period": str(last["period"]),
        "n_active_first": int(first["n_active"]),
        "n_active_last": int(last["n_active"]),
        "peak_period": str(peak_row["period"]),
        "n_active_peak": peak_n,
        # Measured from the PEAK, because the observed stock first GROWS as later
        # origination cohorts phase into the performance window. A first-to-last
        # comparison would net entry against exit and can even be negative.
        "attrition_from_peak_pct": round(100.0 * (1.0 - last["n_active"] / max(peak_n, 1)), 2),
        "net_change_first_to_last_pct": round(
            100.0 * (last["n_active"] / max(int(first["n_active"]), 1) - 1.0), 2
        ),
        "upb_first": float(first["total_upb"]),
        "upb_last": float(last["total_upb"]),
        "upb_peak": float(peak_row["total_upb"]),
        "n_geographies": int(last["n_geographies"]),
        "interpretation": (
            "The observed stock is subject to BOTH entry and exit. Entry: loans from "
            "later origination cohorts phase in as the performance window advances, "
            "and loans left-truncated by the window appear at their first observed "
            "month. Exit: prepayment, credit events, and administrative removals. A "
            "first-to-last comparison nets the two and can be POSITIVE even while "
            "loans are exiting, which is why attrition is measured from the peak. "
            "Neither number is a market-wide change in mortgages outstanding -- no "
            "origination cohort outside the configured set ever enters."
        ),
    }


def load_active_stock(cfg: Config) -> pl.DataFrame:
    p = cfg.path("processed", stock_filename(cfg))
    if not p.exists():
        raise FileNotFoundError(
            f"{p} missing. Run `make build-lockin CONFIG=...` with "
            f"panel.geography={cfg.panel.geography!r}."
        )
    return pl.read_parquet(p)


def post_shock_rate_level(cfg: Config) -> tuple[float, dict[str, object]]:
    """The national post-shock market-rate level that exposure is evaluated at.

    This is the *shift* in the shift-share design. It is a single national scalar --
    the mean point-in-time PMMS rate over the post-shock window -- so it contributes
    no cross-sectional variation. All variation in exposure comes from the frozen
    local coupon **shares**.
    """
    from datetime import date

    from lockin.adapters import pmms
    from lockin.rates import monthly_market_rate

    rates = monthly_market_rate(pmms.load(cfg), series=cfg.rates.series)
    sy, sm = map(int, cfg.event_study.shock_date.split("-"))
    ey, em = map(int, cfg.mortgage.performance_end.split("-"))
    window = rates.filter(pl.col("period").is_between(date(sy, sm, 1), date(ey, em, 1))).drop_nulls(
        "market_rate"
    )
    if window.height == 0:
        raise ValueError("no PMMS observations in the post-shock window")
    level = float(window["market_rate"].mean())
    return (
        level,
        {
            "post_shock_rate_level_pct": level,
            "window": f"{cfg.event_study.shock_date}..{cfg.mortgage.performance_end}",
            "series": cfg.rates.series,
            "n_months": window.height,
            "peak": float(window["market_rate"].max()),
            "note": "A NATIONAL scalar. It is the 'shift' in the shift-share design "
            "and contributes no cross-sectional variation.",
        },
    )


def predetermined_exposure(
    cfg: Config, as_of: str | None = None, post_rate: float | None = None
) -> tuple[pl.DataFrame, dict[str, object]]:
    """Predetermined lock-in exposure: the pre-shock coupon distribution evaluated
    at the later national rate path.

    .. math::
        E_g = \\sum_k \\omega_{gk}^{\\text{pre}} \\cdot
              \\mathbf 1\\{\\bar R^{\\text{post}} - r_k > \\tau\\}

    where :math:`\\omega_{gk}^{\\text{pre}}` is geography *g*'s share of active loans
    in coupon bin *k* **as of the pre-shock date** and :math:`\\bar R^{\\text{post}}`
    is the national post-shock rate level.

    **Why not the contemporaneous locked share at the pre-shock date?** Because it is
    almost exactly zero everywhere: in 2021-12 the market rate was near its historic
    low, so essentially no borrower was locked in *yet*. Lock-in is created by the
    subsequent rate increase interacting with the coupon distribution that already
    existed. Measuring "locked share at the pre-shock date" would therefore be a
    measure of nothing. This was a real design error caught by the exposure
    distribution collapsing to zero variance; see ``docs/DECISION_LOG.md`` D016.

    Everything on the right-hand side is fixed at or before the pre-shock date except
    the national scalar, so the measure remains predetermined at the geography level.
    A hard assertion enforces that no loan-level input postdates ``as_of``.
    """
    from datetime import date

    from lockin.episodes import scan_episodes

    as_of = as_of or cfg.event_study.pre_shock_date
    y, m = map(int, as_of.split("-"))
    cut = date(y, m, 1)

    if post_rate is None:
        post_rate, rate_meta = post_shock_rate_level(cfg)
    else:
        rate_meta = {"post_shock_rate_level_pct": post_rate, "source": "caller-supplied"}

    geo = "property_state" if cfg.panel.geography == "state" else "_cbsa_parent"
    lf = with_geography(cfg, scan_episodes(cfg).filter(pl.col("period") == cut))

    pre_loans = (
        lf.select(
            pl.col(geo).alias("geography"),
            "period",
            "note_rate",
            "upb_start_of_month",
            "remaining_term",
            "loan_purpose",
            "est_current_ltv",
            "credit_score",
            "orig_cohort_year",
        )
        .drop_nulls(["geography", "note_rate", "upb_start_of_month"])
        .collect()
    )

    if pre_loans.height == 0:
        avail = (
            scan_episodes(cfg)
            .select(pl.col("period").min().alias("lo"), pl.col("period").max().alias("hi"))
            .collect()
            .row(0)
        )
        raise ValueError(
            f"no loan-month episodes at the pre-shock date {as_of}. Available: {avail}"
        )
    if pre_loans["period"].max() > cut:  # pragma: no cover - defensive
        raise AssertionError("predetermined exposure used a period after the as-of date")

    # Counterfactual gap: national post-shock rate minus each loan's frozen coupon.
    cf_gap = pl.lit(post_rate) - pl.col("note_rate")
    cf_pgap = _pmt(
        pl.col("upb_start_of_month"), pl.lit(post_rate), pl.col("remaining_term")
    ) - _pmt(pl.col("upb_start_of_month"), pl.col("note_rate"), pl.col("remaining_term"))

    thr_exprs: list[pl.Expr] = []
    for t in cfg.lockin.thresholds_bp:
        locked = cf_gap > t / 100.0
        thr_exprs.append(locked.mean().alias(f"locked_share_count_{t}"))
        thr_exprs.append(
            (
                pl.when(locked).then(pl.col("upb_start_of_month")).otherwise(0.0).sum()
                / pl.col("upb_start_of_month").sum()
            ).alias(f"locked_share_upb_{t}")
        )
        # Coupon-share form: share of loans with a note rate below a fixed cut.
        # Truly predetermined -- no rate assumption at all.
        thr_exprs.append(
            (pl.col("note_rate") < post_rate - t / 100.0).mean().alias(f"coupon_share_below_{t}")
        )

    exposure = (
        pre_loans.with_columns(cf_gap.alias("_cf_gap"), cf_pgap.alias("_cf_pgap"))
        .group_by("geography")
        .agg(
            pl.len().alias("n_active_loans"),
            pl.col("upb_start_of_month").sum().alias("total_upb"),
            pl.col("note_rate").mean().alias("wavg_note_rate_count"),
            (
                (pl.col("note_rate") * pl.col("upb_start_of_month")).sum()
                / pl.col("upb_start_of_month").sum()
            ).alias("wavg_note_rate_upb"),
            *thr_exprs,
            pl.col("_cf_gap").mean().alias("mean_rate_gap"),
            pl.col("_cf_gap").clip(0.0, None).mean().alias("mean_lockin_gap"),
            pl.col("_cf_pgap").mean().alias("mean_payment_gap"),
            pl.col("_cf_pgap").median().alias("median_payment_gap"),
            pl.col("est_current_ltv").median().alias("median_est_current_ltv"),
            pl.col("credit_score").mean().alias("mean_credit_score"),
            (pl.col("loan_purpose") == "P").mean().alias("share_purchase_loans"),
            pl.col("orig_cohort_year").mean().alias("mean_orig_cohort_year"),
            *[pl.col("note_rate").quantile(q).alias(f"note_rate_p{int(q * 100)}") for q in DECILES],
        )
        .sort("geography")
    )
    if cfg.panel.min_loans_per_geography > 0:
        exposure = exposure.filter(pl.col("n_active_loans") >= cfg.panel.min_loans_per_geography)
    exposure = exposure.with_columns(
        pl.lit(as_of).alias("exposure_as_of"), pl.lit(post_rate).alias("exposure_post_rate_pct")
    )

    # Herfindahl of the coupon-bin shares: a shift-share diagnostic. High
    # concentration means the design leans on a few coupon bins.
    bins = (
        pre_loans.with_columns((pl.col("note_rate") * 4).round() / 4)
        .group_by(["geography", "note_rate"])
        .agg(pl.len().alias("n"))
    )
    hhi = (
        bins.with_columns((pl.col("n") / pl.col("n").sum().over("geography")).alias("share"))
        .group_by("geography")
        .agg((pl.col("share") ** 2).sum().alias("coupon_share_hhi"))
    )
    exposure = exposure.join(hhi, on="geography", how="left")

    meta = {
        "as_of": as_of,
        "design": "shift-share: frozen pre-shock coupon shares x national post-shock rate level",
        "formula": "E_g = sum_k omega_gk^pre * 1{R_post - r_k > tau}",
        "n_geographies": exposure.height,
        "n_loans_in_pre_stock": pre_loans.height,
        "predetermined": True,
        "rate_shift": rate_meta,
        "exposure_measures": [
            c for c in exposure.columns if c.startswith(("locked_share_", "coupon_share_below_"))
        ]
        + ["mean_payment_gap", "median_payment_gap", "mean_lockin_gap"],
        "why_not_contemporaneous": (
            "The locked share measured AT the pre-shock date is ~0 everywhere, "
            "because the pre-shock market rate was near its historic low. Lock-in is "
            "created by the SUBSEQUENT rate increase acting on the coupon "
            "distribution that already existed."
        ),
        "warning": (
            "Predetermined is NOT exogenous. The 2021 coupon distribution is a "
            "function of when a market last turned over, which correlates with "
            "pandemic in-migration, price growth, and construction. No IV language. "
            "See docs/IDENTIFICATION_STRATEGY.md A4."
        ),
    }
    return (exposure, meta)


def _pmt(balance: pl.Expr, rate_pct: pl.Expr, n: pl.Expr) -> pl.Expr:
    """Level payment as a Polars expression (mirrors lockin.amortization.payment)."""
    i = rate_pct / 1200.0
    g = (1.0 + i).pow(n)
    return (
        pl.when(n <= 0)
        .then(None)
        .when(i.abs() < 1e-12)
        .then(balance / n)
        .otherwise(balance * i * g / (g - 1.0))
    )
